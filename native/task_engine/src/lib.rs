//! Curie's durable task, lease, and idempotency state machine.
//!
//! Natural-language planning, capability policy, approvals, tool execution,
//! evidence verification, compensation, and response wording stay in Python.
//! This crate owns deterministic graph checks, stable hashes, atomic SQLite
//! create-or-replay, lease fencing, retry scheduling, cancellation, and task
//! state reconciliation.

use chrono::{DateTime, Datelike, Timelike, Utc};
use pyo3::exceptions::{PyKeyError, PyPermissionError, PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use rusqlite::{params, Connection, OptionalExtension, Transaction, TransactionBehavior};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::time::Duration;

const ENGINE_VERSION: &str = "rust-task-engine-v2";
const TERMINAL: [&str; 4] = ["completed", "failed", "cancelled", "expired"];

fn value_error(message: impl Into<String>) -> PyErr {
    PyValueError::new_err(message.into())
}

fn runtime_error(message: impl Into<String>) -> PyErr {
    PyRuntimeError::new_err(message.into())
}

fn sqlite_error(error: rusqlite::Error) -> PyErr {
    runtime_error(format!("durable task store failed: {error}"))
}

fn parse_json(raw: &str, label: &str) -> PyResult<Value> {
    serde_json::from_str(raw).map_err(|_| value_error(format!("{label} is not valid JSON")))
}

fn object_mut<'a>(value: &'a mut Value, label: &str) -> PyResult<&'a mut Map<String, Value>> {
    value
        .as_object_mut()
        .ok_or_else(|| value_error(format!("{label} must be a JSON object")))
}

fn required_string<'a>(value: &'a Value, key: &str) -> PyResult<&'a str> {
    value
        .get(key)
        .and_then(Value::as_str)
        .filter(|item| !item.is_empty())
        .ok_or_else(|| value_error(format!("task field {key} is required")))
}

fn hash_text(value: &str) -> String {
    format!("{:x}", Sha256::digest(value.as_bytes()))
}

fn open_store(path: &str) -> PyResult<Connection> {
    let connection = Connection::open(path).map_err(sqlite_error)?;
    connection
        .busy_timeout(Duration::from_secs(10))
        .map_err(sqlite_error)?;
    connection
        .execute_batch(
            "PRAGMA foreign_keys=ON;
             CREATE TABLE IF NOT EXISTS durable_task_leases (
               task_id TEXT NOT NULL,
               step_id TEXT NOT NULL,
               lease_token INTEGER NOT NULL,
               worker_id TEXT NOT NULL,
               lease_expires_at_ms INTEGER NOT NULL,
               active INTEGER NOT NULL DEFAULT 1,
               updated_at TEXT NOT NULL,
               PRIMARY KEY(task_id, step_id)
             );
             CREATE INDEX IF NOT EXISTS idx_durable_task_leases_active
               ON durable_task_leases(active, lease_expires_at_ms);
             CREATE TABLE IF NOT EXISTS scheduled_work (
               id TEXT PRIMARY KEY,
               owner_id TEXT NOT NULL,
               kind TEXT NOT NULL,
               schedule_type TEXT NOT NULL,
               schedule TEXT,
               due_at_ms INTEGER NOT NULL,
               payload_json TEXT NOT NULL,
               enabled INTEGER NOT NULL DEFAULT 1,
               status TEXT NOT NULL DEFAULT 'pending',
               lease_token INTEGER NOT NULL DEFAULT 0,
               worker_id TEXT,
               lease_expires_at_ms INTEGER,
               attempts INTEGER NOT NULL DEFAULT 0,
               max_attempts INTEGER NOT NULL DEFAULT 5,
               last_error TEXT,
               updated_at_ms INTEGER NOT NULL
             );
             CREATE INDEX IF NOT EXISTS idx_scheduled_work_due
               ON scheduled_work(enabled,status,due_at_ms,lease_expires_at_ms);",
        )
        .map_err(sqlite_error)?;
    Ok(connection)
}

fn transaction(connection: &mut Connection) -> PyResult<Transaction<'_>> {
    connection
        .transaction_with_behavior(TransactionBehavior::Immediate)
        .map_err(sqlite_error)
}

fn load_owned(tx: &Transaction<'_>, owner_id: &str, task_id: &str) -> PyResult<Value> {
    let raw: Option<String> = tx
        .query_row(
            "SELECT document_json FROM durable_tasks WHERE id=?1 AND internal_id=?2",
            params![task_id, owner_id],
            |row| row.get(0),
        )
        .optional()
        .map_err(sqlite_error)?;
    let raw =
        raw.ok_or_else(|| PyKeyError::new_err("Task does not exist or belongs to another user"))?;
    parse_json(&raw, "stored task")
}

fn bump_revision(document: &mut Value, now_iso: &str) -> PyResult<()> {
    let object = object_mut(document, "task")?;
    let revision = object.get("revision").and_then(Value::as_u64).unwrap_or(0);
    object.insert("revision".to_owned(), json!(revision.saturating_add(1)));
    object.insert("updated_at".to_owned(), json!(now_iso));
    Ok(())
}

fn persist(tx: &Transaction<'_>, document: &Value, now_iso: &str) -> PyResult<()> {
    let id = required_string(document, "id")?;
    let owner = required_string(document, "owner_id")?;
    let status = required_string(document, "status")?;
    let raw =
        serde_json::to_string(document).map_err(|_| runtime_error("task serialization failed"))?;
    let changed = tx
        .execute(
            "UPDATE durable_tasks SET status=?1, document_json=?2, updated_at=?3
             WHERE id=?4 AND internal_id=?5",
            params![status, raw, now_iso, id, owner],
        )
        .map_err(sqlite_error)?;
    if changed != 1 {
        return Err(runtime_error("durable task update lost ownership"));
    }
    Ok(())
}

fn document_steps_mut(document: &mut Value) -> PyResult<&mut Vec<Value>> {
    document
        .get_mut("steps")
        .and_then(Value::as_array_mut)
        .ok_or_else(|| value_error("task steps must be an array"))
}

fn step_index(document: &Value, step_id: &str) -> PyResult<usize> {
    document
        .get("steps")
        .and_then(Value::as_array)
        .and_then(|steps| {
            steps
                .iter()
                .position(|step| step.get("id").and_then(Value::as_str) == Some(step_id))
        })
        .ok_or_else(|| PyKeyError::new_err("Task step does not exist"))
}

fn task_status(document: &Value) -> &str {
    document
        .get("status")
        .and_then(Value::as_str)
        .unwrap_or("pending")
}

fn is_terminal(status: &str) -> bool {
    TERMINAL.contains(&status)
}

fn deadline_expired(document: &Value, now_ms: i64) -> bool {
    if let Some(epoch) = document.get("deadline_epoch_ms").and_then(Value::as_i64) {
        return epoch <= now_ms;
    }
    document
        .get("deadline")
        .and_then(Value::as_str)
        .and_then(|raw| DateTime::parse_from_rfc3339(raw).ok())
        .is_some_and(|deadline| deadline.timestamp_millis() <= now_ms)
}

fn append_audit(document: &mut Value, event: Value) -> PyResult<()> {
    let object = object_mut(document, "task")?;
    let audit = object
        .entry("audit")
        .or_insert_with(|| Value::Array(Vec::new()))
        .as_array_mut()
        .ok_or_else(|| value_error("task audit must be an array"))?;
    audit.push(event);
    Ok(())
}

fn set_task_field(document: &mut Value, key: &str, value: Value) -> PyResult<()> {
    object_mut(document, "task")?.insert(key.to_owned(), value);
    Ok(())
}

fn completed_ids(document: &Value) -> HashSet<String> {
    document
        .get("steps")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter(|step| step.get("status").and_then(Value::as_str) == Some("completed"))
        .filter_map(|step| step.get("id").and_then(Value::as_str).map(str::to_owned))
        .collect()
}

fn step_dependencies_ready(step: &Value, completed: &HashSet<String>) -> bool {
    step.get("depends_on")
        .and_then(Value::as_array)
        .into_iter()
        .flatten()
        .filter_map(Value::as_str)
        .all(|dependency| completed.contains(dependency))
}

fn reconcile_in_transaction(
    tx: &Transaction<'_>,
    mut document: Value,
    now_ms: i64,
    now_iso: &str,
) -> PyResult<Value> {
    let status = task_status(&document).to_owned();
    if is_terminal(&status) {
        return Ok(json!({"outcome": "terminal", "task": document}));
    }
    if deadline_expired(&document, now_ms) {
        set_task_field(&mut document, "status", json!("expired"))?;
        bump_revision(&mut document, now_iso)?;
        persist(tx, &document, now_iso)?;
        return Ok(json!({"outcome": "terminal", "task": document}));
    }
    if document
        .get("cancel_requested")
        .and_then(Value::as_bool)
        .unwrap_or(false)
    {
        set_task_field(&mut document, "status", json!("cancelled"))?;
        bump_revision(&mut document, now_iso)?;
        persist(tx, &document, now_iso)?;
        return Ok(json!({"outcome": "terminal", "task": document}));
    }
    if status == "waiting_approval" {
        return Ok(json!({"outcome": "waiting_approval", "task": document}));
    }
    let steps = document
        .get("steps")
        .and_then(Value::as_array)
        .ok_or_else(|| value_error("task steps must be an array"))?;
    if steps
        .iter()
        .any(|step| step.get("status").and_then(Value::as_str) == Some("failed"))
    {
        set_task_field(&mut document, "status", json!("failed"))?;
        bump_revision(&mut document, now_iso)?;
        persist(tx, &document, now_iso)?;
        return Ok(json!({"outcome": "terminal", "task": document}));
    }
    if !steps.is_empty()
        && steps
            .iter()
            .all(|step| step.get("status").and_then(Value::as_str) == Some("completed"))
    {
        return Ok(json!({"outcome": "ready_to_finalize", "task": document}));
    }
    let completed = completed_ids(&document);
    let mut ready = Vec::new();
    let mut deferred_ms: Option<i64> = None;
    for step in steps {
        if step.get("status").and_then(Value::as_str) != Some("pending")
            || !step_dependencies_ready(step, &completed)
        {
            continue;
        }
        let retry_at = step
            .get("next_attempt_at_ms")
            .and_then(Value::as_i64)
            .unwrap_or(0);
        if retry_at > now_ms {
            let remaining = retry_at.saturating_sub(now_ms);
            deferred_ms = Some(deferred_ms.map_or(remaining, |value| value.min(remaining)));
        } else if let Some(id) = step.get("id").and_then(Value::as_str) {
            ready.push(id.to_owned());
        }
    }
    if status != "running" {
        set_task_field(&mut document, "status", json!("running"))?;
        bump_revision(&mut document, now_iso)?;
        persist(tx, &document, now_iso)?;
    }
    Ok(json!({
        "outcome": if ready.is_empty() && deferred_ms.is_some() { "deferred" } else { "running" },
        "ready_step_ids": ready,
        "deferred_ms": deferred_ms,
        "task": document,
    }))
}

#[pyfunction]
fn engine_version() -> &'static str {
    ENGINE_VERSION
}

#[pyfunction]
fn hash_canonical_json(canonical_json: &str) -> String {
    hash_text(canonical_json)
}

#[pyfunction]
fn validate_graph(graph_json: &str) -> PyResult<Vec<String>> {
    let graph = parse_json(graph_json, "task graph")?;
    let nodes = graph
        .as_array()
        .ok_or_else(|| value_error("task graph must be an array"))?;
    if nodes.is_empty() {
        return Err(value_error("A durable task requires at least one step"));
    }
    let mut dependencies: HashMap<String, Vec<String>> = HashMap::new();
    let mut order = Vec::with_capacity(nodes.len());
    for node in nodes {
        let id = node
            .get("id")
            .and_then(Value::as_str)
            .filter(|value| !value.is_empty())
            .ok_or_else(|| value_error("Task step IDs must be non-empty and unique"))?;
        if dependencies.contains_key(id) {
            return Err(value_error("Task step IDs must be non-empty and unique"));
        }
        let deps = node
            .get("depends_on")
            .and_then(Value::as_array)
            .into_iter()
            .flatten()
            .map(|item| {
                item.as_str()
                    .map(str::to_owned)
                    .ok_or_else(|| value_error("Task dependencies must be strings"))
            })
            .collect::<PyResult<Vec<_>>>()?;
        dependencies.insert(id.to_owned(), deps);
        order.push(id.to_owned());
    }
    for (id, deps) in &dependencies {
        if deps.iter().any(|dependency| dependency == id)
            || deps
                .iter()
                .any(|dependency| !dependencies.contains_key(dependency))
        {
            return Err(value_error(format!(
                "Task step {id} has invalid dependencies"
            )));
        }
    }
    fn visit(
        node: &str,
        graph: &HashMap<String, Vec<String>>,
        visiting: &mut HashSet<String>,
        visited: &mut HashSet<String>,
        result: &mut Vec<String>,
    ) -> Result<(), ()> {
        if visiting.contains(node) {
            return Err(());
        }
        if visited.contains(node) {
            return Ok(());
        }
        visiting.insert(node.to_owned());
        for dependency in &graph[node] {
            visit(dependency, graph, visiting, visited, result)?;
        }
        visiting.remove(node);
        visited.insert(node.to_owned());
        result.push(node.to_owned());
        Ok(())
    }
    let mut visiting = HashSet::new();
    let mut visited = HashSet::new();
    let mut topological = Vec::with_capacity(order.len());
    for id in order {
        if visit(
            &id,
            &dependencies,
            &mut visiting,
            &mut visited,
            &mut topological,
        )
        .is_err()
        {
            return Err(value_error("Task dependency graph contains a cycle"));
        }
    }
    Ok(topological)
}

#[pyfunction]
#[pyo3(signature = (attempt, max_attempts, read_only, idempotency_mode, error_kind, explicit_retryable=false, retry_after_ms=0, cancellation_requested=false))]
#[allow(clippy::too_many_arguments)]
fn decide_retry(
    attempt: u32,
    max_attempts: u32,
    read_only: bool,
    idempotency_mode: &str,
    error_kind: &str,
    explicit_retryable: bool,
    retry_after_ms: i64,
    cancellation_requested: bool,
) -> (bool, String, i64) {
    if cancellation_requested {
        return (false, "user_cancelled".to_owned(), 0);
    }
    if attempt >= max_attempts {
        return (false, "attempt_limit".to_owned(), 0);
    }
    if matches!(error_kind, "value" | "permission" | "lookup") {
        return (false, "non_retryable_input_or_policy".to_owned(), 0);
    }
    if !explicit_retryable && !matches!(error_kind, "timeout" | "connection") {
        return (false, "error_not_transient".to_owned(), 0);
    }
    if !read_only && !matches!(idempotency_mode, "state_reconciled" | "provider_key") {
        return (false, "mutation_not_safely_idempotent".to_owned(), 0);
    }
    (
        true,
        "transient_safe_retry".to_owned(),
        retry_after_ms.clamp(0, 5_000),
    )
}

#[pyfunction]
fn create_or_get(db_path: &str, document_json: &str, now_iso: &str) -> PyResult<(String, bool)> {
    let mut document = parse_json(document_json, "task")?;
    let id = required_string(&document, "id")?.to_owned();
    let owner = required_string(&document, "owner_id")?.to_owned();
    let key = required_string(&document, "idempotency_key")?.to_owned();
    let graph_hash = required_string(&document, "graph_hash")?.to_owned();
    if key.len() > 128 {
        return Err(value_error("Idempotency key exceeds 128 characters"));
    }
    object_mut(&mut document, "task")?
        .entry("revision")
        .or_insert(json!(1));
    let raw =
        serde_json::to_string(&document).map_err(|_| runtime_error("task serialization failed"))?;
    let mut connection = open_store(db_path)?;
    let tx = transaction(&mut connection)?;
    let existing: Option<String> = tx
        .query_row(
            "SELECT document_json FROM durable_tasks WHERE internal_id=?1 AND idempotency_key=?2",
            params![owner, key],
            |row| row.get(0),
        )
        .optional()
        .map_err(sqlite_error)?;
    if let Some(existing_raw) = existing {
        let existing_doc = parse_json(&existing_raw, "stored task")?;
        if existing_doc.get("graph_hash").and_then(Value::as_str) != Some(&graph_hash) {
            return Err(value_error(
                "Idempotency key is already bound to a different task graph",
            ));
        }
        tx.commit().map_err(sqlite_error)?;
        return Ok((existing_raw, true));
    }
    tx.execute(
        "INSERT INTO durable_tasks(id,internal_id,idempotency_key,status,document_json,updated_at)
         VALUES(?1,?2,?3,?4,?5,?6)",
        params![id, owner, key, task_status(&document), raw, now_iso],
    )
    .map_err(sqlite_error)?;
    tx.commit().map_err(sqlite_error)?;
    Ok((serde_json::to_string(&document).unwrap_or_default(), false))
}

#[pyfunction]
fn load_task(db_path: &str, owner_id: &str, task_id: &str) -> PyResult<String> {
    let mut connection = open_store(db_path)?;
    let tx = transaction(&mut connection)?;
    let document = load_owned(&tx, owner_id, task_id)?;
    tx.commit().map_err(sqlite_error)?;
    serde_json::to_string(&document).map_err(|_| runtime_error("task serialization failed"))
}

#[pyfunction]
fn replace_task_snapshot(db_path: &str, document_json: &str, now_iso: &str) -> PyResult<String> {
    let mut document = parse_json(document_json, "task")?;
    let task_id = required_string(&document, "id")?.to_owned();
    let owner_id = required_string(&document, "owner_id")?.to_owned();
    let idempotency_key = required_string(&document, "idempotency_key")?.to_owned();
    let graph_hash = required_string(&document, "graph_hash")?.to_owned();
    let mut connection = open_store(db_path)?;
    let tx = transaction(&mut connection)?;
    let existing = load_owned(&tx, &owner_id, &task_id)?;
    if existing.get("idempotency_key").and_then(Value::as_str) != Some(&idempotency_key)
        || existing.get("graph_hash").and_then(Value::as_str) != Some(&graph_hash)
    {
        return Err(value_error(
            "Task identity, idempotency key, and graph are immutable",
        ));
    }
    let existing_revision = existing
        .get("revision")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    let incoming_revision = document
        .get("revision")
        .and_then(Value::as_u64)
        .unwrap_or(existing_revision);
    if incoming_revision < existing_revision {
        return Err(value_error("Task snapshot revision is stale"));
    }
    object_mut(&mut document, "task")?.insert("updated_at".to_owned(), json!(now_iso));
    persist(&tx, &document, now_iso)?;
    tx.commit().map_err(sqlite_error)?;
    serde_json::to_string(&document).map_err(|_| runtime_error("task serialization failed"))
}

#[pyfunction]
#[pyo3(signature = (db_path, owner_id, status=None, limit=100))]
fn list_tasks(
    db_path: &str,
    owner_id: &str,
    status: Option<&str>,
    limit: usize,
) -> PyResult<Vec<String>> {
    let connection = open_store(db_path)?;
    let bounded = limit.clamp(1, 1000) as i64;
    let mut documents = Vec::new();
    if let Some(status) = status {
        let mut statement = connection
            .prepare(
                "SELECT document_json FROM durable_tasks
                 WHERE internal_id=?1 AND status=?2 ORDER BY updated_at DESC LIMIT ?3",
            )
            .map_err(sqlite_error)?;
        let rows = statement
            .query_map(params![owner_id, status, bounded], |row| row.get(0))
            .map_err(sqlite_error)?;
        for row in rows {
            documents.push(row.map_err(sqlite_error)?);
        }
    } else {
        let mut statement = connection
            .prepare(
                "SELECT document_json FROM durable_tasks
                 WHERE internal_id=?1 ORDER BY updated_at DESC LIMIT ?2",
            )
            .map_err(sqlite_error)?;
        let rows = statement
            .query_map(params![owner_id, bounded], |row| row.get(0))
            .map_err(sqlite_error)?;
        for row in rows {
            documents.push(row.map_err(sqlite_error)?);
        }
    }
    Ok(documents)
}

#[pyfunction]
fn reconcile_task(
    db_path: &str,
    owner_id: &str,
    task_id: &str,
    now_ms: i64,
    now_iso: &str,
) -> PyResult<String> {
    let mut connection = open_store(db_path)?;
    let tx = transaction(&mut connection)?;
    let document = load_owned(&tx, owner_id, task_id)?;
    let result = reconcile_in_transaction(&tx, document, now_ms, now_iso)?;
    tx.commit().map_err(sqlite_error)?;
    serde_json::to_string(&result).map_err(|_| runtime_error("task serialization failed"))
}

#[pyfunction]
#[pyo3(signature = (db_path, owner_id, task_id, step_id, worker_id, risk, approved, now_ms, now_iso, lease_ms=30000))]
#[allow(clippy::too_many_arguments)]
fn claim_step(
    db_path: &str,
    owner_id: &str,
    task_id: &str,
    step_id: &str,
    worker_id: &str,
    risk: &str,
    approved: bool,
    now_ms: i64,
    now_iso: &str,
    lease_ms: i64,
) -> PyResult<String> {
    if worker_id.trim().is_empty() {
        return Err(value_error("Worker ID is required"));
    }
    let bounded_lease = lease_ms.clamp(1_000, 300_000);
    let mut connection = open_store(db_path)?;
    let tx = transaction(&mut connection)?;
    let mut document = load_owned(&tx, owner_id, task_id)?;
    if approved
        && (task_status(&document) != "waiting_approval"
            || document.get("waiting_step_id").and_then(Value::as_str) != Some(step_id))
    {
        return Err(PyPermissionError::new_err(
            "Task step does not have a matching consumed approval",
        ));
    }
    let reconciled = reconcile_in_transaction(&tx, document.clone(), now_ms, now_iso)?;
    let outcome = reconciled
        .get("outcome")
        .and_then(Value::as_str)
        .unwrap_or("");
    document = reconciled.get("task").cloned().unwrap_or(document);
    if matches!(outcome, "terminal" | "waiting_approval") && !approved {
        tx.commit().map_err(sqlite_error)?;
        return serde_json::to_string(&json!({"outcome": outcome, "task": document}))
            .map_err(|_| runtime_error("task serialization failed"));
    }
    if is_terminal(task_status(&document)) {
        tx.commit().map_err(sqlite_error)?;
        return Ok(json!({"outcome": "terminal", "task": document}).to_string());
    }
    let index = step_index(&document, step_id)?;
    let completed = completed_ids(&document);
    let step = &document["steps"][index];
    if !step_dependencies_ready(step, &completed) {
        tx.commit().map_err(sqlite_error)?;
        return Ok(json!({"outcome": "not_ready", "task": document}).to_string());
    }
    let status = step
        .get("status")
        .and_then(Value::as_str)
        .unwrap_or("pending");
    if matches!(status, "completed" | "failed") {
        tx.commit().map_err(sqlite_error)?;
        return Ok(json!({"outcome": "settled", "task": document}).to_string());
    }
    let retry_at = step
        .get("next_attempt_at_ms")
        .and_then(Value::as_i64)
        .unwrap_or(0);
    if status == "pending" && retry_at > now_ms {
        tx.commit().map_err(sqlite_error)?;
        return Ok(json!({
            "outcome": "deferred",
            "deferred_ms": retry_at.saturating_sub(now_ms),
            "task": document,
        })
        .to_string());
    }
    let lease: Option<(i64, String, i64, bool)> = tx
        .query_row(
            "SELECT lease_token,worker_id,lease_expires_at_ms,active
             FROM durable_task_leases WHERE task_id=?1 AND step_id=?2",
            params![task_id, step_id],
            |row| {
                Ok((
                    row.get(0)?,
                    row.get(1)?,
                    row.get(2)?,
                    row.get::<_, i64>(3)? != 0,
                ))
            },
        )
        .optional()
        .map_err(sqlite_error)?;
    if status == "running" {
        if let Some((_, _, expires_at, true)) = &lease {
            if *expires_at > now_ms {
                tx.commit().map_err(sqlite_error)?;
                return Ok(json!({"outcome": "busy", "task": document}).to_string());
            }
        }
    }
    let interrupted = status == "running";
    if risk == "mutating" && !approved && !interrupted {
        tx.commit().map_err(sqlite_error)?;
        return Ok(json!({"outcome": "approval_required", "task": document}).to_string());
    }
    let action = if interrupted && risk == "mutating" {
        "claimed_reconcile"
    } else {
        "claimed_execute"
    };
    let old_token = lease.map(|item| item.0).unwrap_or(0);
    let token = old_token.saturating_add(1).max(1);
    let expires_at = now_ms.saturating_add(bounded_lease);
    tx.execute(
        "INSERT INTO durable_task_leases(task_id,step_id,lease_token,worker_id,lease_expires_at_ms,active,updated_at)
         VALUES(?1,?2,?3,?4,?5,1,?6)
         ON CONFLICT(task_id,step_id) DO UPDATE SET
           lease_token=excluded.lease_token,worker_id=excluded.worker_id,
           lease_expires_at_ms=excluded.lease_expires_at_ms,active=1,updated_at=excluded.updated_at",
        params![task_id, step_id, token, worker_id, expires_at, now_iso],
    )
    .map_err(sqlite_error)?;
    {
        let step = object_mut(&mut document_steps_mut(&mut document)?[index], "task step")?;
        step.insert("status".to_owned(), json!("running"));
        step.remove("next_attempt_at_ms");
        if action == "claimed_execute" {
            let attempts = step.get("attempts").and_then(Value::as_u64).unwrap_or(0);
            let maximum = step
                .get("max_attempts")
                .and_then(Value::as_u64)
                .unwrap_or(1);
            if attempts >= maximum {
                step.insert("status".to_owned(), json!("failed"));
                step.insert("error".to_owned(), json!("Attempt limit reached"));
                tx.execute(
                    "UPDATE durable_task_leases SET active=0,updated_at=?1
                     WHERE task_id=?2 AND step_id=?3",
                    params![now_iso, task_id, step_id],
                )
                .map_err(sqlite_error)?;
                set_task_field(&mut document, "status", json!("failed"))?;
                bump_revision(&mut document, now_iso)?;
                persist(&tx, &document, now_iso)?;
                tx.commit().map_err(sqlite_error)?;
                return Ok(json!({"outcome": "attempt_limit", "task": document}).to_string());
            }
            step.insert("attempts".to_owned(), json!(attempts.saturating_add(1)));
        }
    }
    set_task_field(&mut document, "status", json!("running"))?;
    if approved {
        set_task_field(&mut document, "waiting_step_id", Value::Null)?;
        set_task_field(&mut document, "approval_token", Value::Null)?;
    }
    bump_revision(&mut document, now_iso)?;
    persist(&tx, &document, now_iso)?;
    let step = document["steps"][index].clone();
    tx.commit().map_err(sqlite_error)?;
    Ok(json!({
        "outcome": action,
        "lease_token": token,
        "lease_expires_at_ms": expires_at,
        "step": step,
        "task": document,
    })
    .to_string())
}

fn verify_lease(
    tx: &Transaction<'_>,
    task_id: &str,
    step_id: &str,
    worker_id: &str,
    lease_token: i64,
    now_ms: i64,
) -> PyResult<()> {
    let valid: Option<i64> = tx
        .query_row(
            "SELECT 1 FROM durable_task_leases
             WHERE task_id=?1 AND step_id=?2 AND worker_id=?3
               AND lease_token=?4 AND lease_expires_at_ms>?5 AND active=1",
            params![task_id, step_id, worker_id, lease_token, now_ms],
            |row| row.get(0),
        )
        .optional()
        .map_err(sqlite_error)?;
    if valid.is_none() {
        return Err(PyPermissionError::new_err(
            "Task step lease is stale, expired, inactive, or owned by another worker",
        ));
    }
    Ok(())
}

#[pyfunction]
#[pyo3(signature = (db_path, owner_id, task_id, step_id, worker_id, lease_token, now_ms, now_iso, lease_ms=30000))]
#[allow(clippy::too_many_arguments)]
fn heartbeat_step(
    db_path: &str,
    owner_id: &str,
    task_id: &str,
    step_id: &str,
    worker_id: &str,
    lease_token: i64,
    now_ms: i64,
    now_iso: &str,
    lease_ms: i64,
) -> PyResult<i64> {
    let mut connection = open_store(db_path)?;
    let tx = transaction(&mut connection)?;
    let _ = load_owned(&tx, owner_id, task_id)?;
    verify_lease(&tx, task_id, step_id, worker_id, lease_token, now_ms)?;
    let expires = now_ms.saturating_add(lease_ms.clamp(1_000, 300_000));
    tx.execute(
        "UPDATE durable_task_leases SET lease_expires_at_ms=?1,updated_at=?2
         WHERE task_id=?3 AND step_id=?4 AND lease_token=?5 AND worker_id=?6 AND active=1",
        params![expires, now_iso, task_id, step_id, lease_token, worker_id],
    )
    .map_err(sqlite_error)?;
    tx.commit().map_err(sqlite_error)?;
    Ok(expires)
}

#[pyfunction]
#[pyo3(signature = (db_path, owner_id, task_id, step_id, worker_id, lease_token, result_text, evidence_json, now_ms, now_iso, recovered=false))]
#[allow(clippy::too_many_arguments)]
fn complete_step(
    db_path: &str,
    owner_id: &str,
    task_id: &str,
    step_id: &str,
    worker_id: &str,
    lease_token: i64,
    result_text: &str,
    evidence_json: &str,
    now_ms: i64,
    now_iso: &str,
    recovered: bool,
) -> PyResult<String> {
    let evidence = parse_json(evidence_json, "task evidence")?;
    if !evidence.is_array() {
        return Err(value_error("Task evidence must be an array"));
    }
    let mut connection = open_store(db_path)?;
    let tx = transaction(&mut connection)?;
    let mut document = load_owned(&tx, owner_id, task_id)?;
    verify_lease(&tx, task_id, step_id, worker_id, lease_token, now_ms)?;
    let index = step_index(&document, step_id)?;
    let attempt = {
        let step = object_mut(&mut document_steps_mut(&mut document)?[index], "task step")?;
        if step.get("status").and_then(Value::as_str) != Some("running") {
            return Err(value_error("Task step is not running"));
        }
        step.insert("status".to_owned(), json!("completed"));
        step.insert("result".to_owned(), json!(result_text));
        step.insert("evidence".to_owned(), evidence.clone());
        step.insert("error".to_owned(), Value::Null);
        step.get("attempts").and_then(Value::as_u64).unwrap_or(0)
    };
    append_audit(
        &mut document,
        json!({
            "step_id": step_id,
            "status": if recovered { "recovered_by_verification" } else { "completed" },
            "attempt": attempt,
            "evidence": evidence,
            "at": now_iso,
            "lease_token": lease_token,
        }),
    )?;
    tx.execute(
        "UPDATE durable_task_leases SET active=0,updated_at=?1
         WHERE task_id=?2 AND step_id=?3 AND lease_token=?4",
        params![now_iso, task_id, step_id, lease_token],
    )
    .map_err(sqlite_error)?;
    bump_revision(&mut document, now_iso)?;
    persist(&tx, &document, now_iso)?;
    tx.commit().map_err(sqlite_error)?;
    serde_json::to_string(&document).map_err(|_| runtime_error("task serialization failed"))
}

#[pyfunction]
#[pyo3(signature = (db_path, owner_id, task_id, step_id, worker_id, lease_token, error, retry_allowed, retry_delay_ms, now_ms, now_iso))]
#[allow(clippy::too_many_arguments)]
fn fail_step(
    db_path: &str,
    owner_id: &str,
    task_id: &str,
    step_id: &str,
    worker_id: &str,
    lease_token: i64,
    error: &str,
    retry_allowed: bool,
    retry_delay_ms: i64,
    now_ms: i64,
    now_iso: &str,
) -> PyResult<String> {
    let mut connection = open_store(db_path)?;
    let tx = transaction(&mut connection)?;
    let mut document = load_owned(&tx, owner_id, task_id)?;
    verify_lease(&tx, task_id, step_id, worker_id, lease_token, now_ms)?;
    let index = step_index(&document, step_id)?;
    let (attempts, maximum, retry) = {
        let step = object_mut(&mut document_steps_mut(&mut document)?[index], "task step")?;
        let attempts = step.get("attempts").and_then(Value::as_u64).unwrap_or(0);
        let maximum = step
            .get("max_attempts")
            .and_then(Value::as_u64)
            .unwrap_or(1);
        let retry = retry_allowed && attempts < maximum;
        step.insert(
            "error".to_owned(),
            json!(error.chars().take(500).collect::<String>()),
        );
        if retry {
            step.insert("status".to_owned(), json!("pending"));
            step.insert(
                "next_attempt_at_ms".to_owned(),
                json!(now_ms.saturating_add(retry_delay_ms.clamp(0, 300_000))),
            );
        } else {
            step.insert("status".to_owned(), json!("failed"));
            step.remove("next_attempt_at_ms");
        }
        (attempts, maximum, retry)
    };
    append_audit(
        &mut document,
        json!({
            "step_id": step_id,
            "status": if retry { "retry_scheduled" } else { "failed" },
            "attempt": attempts,
            "max_attempts": maximum,
            "error": error.chars().take(500).collect::<String>(),
            "retry_delay_ms": if retry { retry_delay_ms.clamp(0, 300_000) } else { 0 },
            "at": now_iso,
            "lease_token": lease_token,
        }),
    )?;
    if !retry {
        set_task_field(&mut document, "status", json!("failed"))?;
    }
    tx.execute(
        "UPDATE durable_task_leases SET active=0,updated_at=?1
         WHERE task_id=?2 AND step_id=?3 AND lease_token=?4",
        params![now_iso, task_id, step_id, lease_token],
    )
    .map_err(sqlite_error)?;
    bump_revision(&mut document, now_iso)?;
    persist(&tx, &document, now_iso)?;
    tx.commit().map_err(sqlite_error)?;
    serde_json::to_string(&document).map_err(|_| runtime_error("task serialization failed"))
}

#[pyfunction]
fn set_waiting_approval(
    db_path: &str,
    owner_id: &str,
    task_id: &str,
    step_id: &str,
    approval_token: &str,
    now_iso: &str,
) -> PyResult<String> {
    let mut connection = open_store(db_path)?;
    let tx = transaction(&mut connection)?;
    let mut document = load_owned(&tx, owner_id, task_id)?;
    if is_terminal(task_status(&document)) {
        tx.commit().map_err(sqlite_error)?;
        return serde_json::to_string(&document)
            .map_err(|_| runtime_error("task serialization failed"));
    }
    if task_status(&document) == "waiting_approval" {
        tx.commit().map_err(sqlite_error)?;
        return serde_json::to_string(&document)
            .map_err(|_| runtime_error("task serialization failed"));
    }
    let index = step_index(&document, step_id)?;
    if document["steps"][index]
        .get("status")
        .and_then(Value::as_str)
        != Some("pending")
    {
        return Err(value_error(
            "Approval can only be requested for a pending step",
        ));
    }
    set_task_field(&mut document, "status", json!("waiting_approval"))?;
    set_task_field(&mut document, "waiting_step_id", json!(step_id))?;
    set_task_field(&mut document, "approval_token", json!(approval_token))?;
    bump_revision(&mut document, now_iso)?;
    persist(&tx, &document, now_iso)?;
    tx.commit().map_err(sqlite_error)?;
    serde_json::to_string(&document).map_err(|_| runtime_error("task serialization failed"))
}

#[pyfunction]
fn request_cancel(db_path: &str, owner_id: &str, task_id: &str, now_iso: &str) -> PyResult<String> {
    let mut connection = open_store(db_path)?;
    let tx = transaction(&mut connection)?;
    let mut document = load_owned(&tx, owner_id, task_id)?;
    if !is_terminal(task_status(&document)) {
        set_task_field(&mut document, "cancel_requested", json!(true))?;
        set_task_field(&mut document, "status", json!("cancelled"))?;
        bump_revision(&mut document, now_iso)?;
        persist(&tx, &document, now_iso)?;
        tx.execute(
            "UPDATE durable_task_leases SET active=0,updated_at=?1 WHERE task_id=?2",
            params![now_iso, task_id],
        )
        .map_err(sqlite_error)?;
    }
    tx.commit().map_err(sqlite_error)?;
    serde_json::to_string(&document).map_err(|_| runtime_error("task serialization failed"))
}

#[pyfunction]
fn finalize_task(
    db_path: &str,
    owner_id: &str,
    task_id: &str,
    success: bool,
    error: Option<&str>,
    now_iso: &str,
) -> PyResult<String> {
    let mut connection = open_store(db_path)?;
    let tx = transaction(&mut connection)?;
    let mut document = load_owned(&tx, owner_id, task_id)?;
    if is_terminal(task_status(&document)) {
        tx.commit().map_err(sqlite_error)?;
        return serde_json::to_string(&document)
            .map_err(|_| runtime_error("task serialization failed"));
    }
    let all_completed = document
        .get("steps")
        .and_then(Value::as_array)
        .is_some_and(|steps| {
            !steps.is_empty()
                && steps
                    .iter()
                    .all(|step| step.get("status").and_then(Value::as_str) == Some("completed"))
        });
    if !all_completed {
        return Err(value_error(
            "Task cannot be finalized before every step completes",
        ));
    }
    if success {
        set_task_field(&mut document, "status", json!("completed"))?;
        set_task_field(&mut document, "completed_at", json!(now_iso))?;
        set_task_field(&mut document, "error", Value::Null)?;
    } else {
        set_task_field(&mut document, "status", json!("failed"))?;
        set_task_field(
            &mut document,
            "error",
            json!(error.unwrap_or("Task verification failed")),
        )?;
    }
    bump_revision(&mut document, now_iso)?;
    persist(&tx, &document, now_iso)?;
    tx.commit().map_err(sqlite_error)?;
    serde_json::to_string(&document).map_err(|_| runtime_error("task serialization failed"))
}

fn expand_cron(schedule: &str) -> PyResult<String> {
    let clean = schedule.trim().to_lowercase();
    let named = match clean.as_str() {
        "@hourly" => Some("0 * * * *"),
        "@daily" | "@midnight" => Some("0 0 * * *"),
        "@weekly" => Some("0 0 * * 0"),
        "@monthly" => Some("0 0 1 * *"),
        "@yearly" | "@annually" => Some("0 0 1 1 *"),
        _ => None,
    };
    if let Some(value) = named {
        return Ok(value.to_owned());
    }
    if let Some(interval) = clean.strip_prefix("@every_") {
        let split = interval
            .find(|character: char| !character.is_ascii_digit())
            .ok_or_else(|| value_error("invalid interval schedule"))?;
        let count: u32 = interval[..split]
            .parse()
            .map_err(|_| value_error("invalid interval schedule"))?;
        let unit = &interval[split..];
        return match unit.chars().next() {
            Some('m') if (1..=59).contains(&count) => Ok(format!("*/{count} * * * *")),
            Some('h') if (1..=23).contains(&count) => Ok(format!("0 */{count} * * *")),
            Some('d') if count >= 1 => Ok(format!("0 0 */{count} * *")),
            _ => Err(value_error("invalid interval schedule")),
        };
    }
    if clean.split_whitespace().count() != 5 {
        return Err(value_error("cron schedule must have five fields"));
    }
    Ok(clean)
}

fn parse_cron_field(field: &str, minimum: u32, maximum: u32) -> PyResult<HashSet<u32>> {
    let mut result = HashSet::new();
    if field == "*" {
        result.extend(minimum..=maximum);
        return Ok(result);
    }
    for part in field.split(',') {
        let (range, step) = if let Some((range, step)) = part.split_once('/') {
            let step: u32 = step.parse().map_err(|_| value_error("invalid cron step"))?;
            if step == 0 {
                return Err(value_error("cron step must be positive"));
            }
            (range, step)
        } else {
            (part, 1)
        };
        let (start, end) = if range == "*" {
            (minimum, maximum)
        } else if let Some((start, end)) = range.split_once('-') {
            (
                start
                    .parse()
                    .map_err(|_| value_error("invalid cron range"))?,
                end.parse().map_err(|_| value_error("invalid cron range"))?,
            )
        } else {
            let value = range
                .parse()
                .map_err(|_| value_error("invalid cron value"))?;
            (value, value)
        };
        if start < minimum || end > maximum || start > end {
            return Err(value_error("cron field is out of range"));
        }
        result.extend((start..=end).step_by(step as usize));
    }
    Ok(result)
}

fn cron_matches_datetime(schedule: &str, date: DateTime<Utc>) -> PyResult<bool> {
    if schedule.trim().eq_ignore_ascii_case("@reboot") {
        return Ok(false);
    }
    let expanded = expand_cron(schedule)?;
    let parts: Vec<&str> = expanded.split_whitespace().collect();
    let minutes = parse_cron_field(parts[0], 0, 59)?;
    let hours = parse_cron_field(parts[1], 0, 23)?;
    let days = parse_cron_field(parts[2], 1, 31)?;
    let months = parse_cron_field(parts[3], 1, 12)?;
    let weekdays = parse_cron_field(parts[4], 0, 6)?;
    let cron_weekday = date.weekday().num_days_from_sunday();
    let day_match = if parts[2] != "*" && parts[4] != "*" {
        days.contains(&date.day()) || weekdays.contains(&cron_weekday)
    } else {
        days.contains(&date.day()) && weekdays.contains(&cron_weekday)
    };
    Ok(minutes.contains(&date.minute())
        && hours.contains(&date.hour())
        && months.contains(&date.month())
        && day_match)
}

fn next_cron_due(schedule: &str, after_ms: i64) -> PyResult<i64> {
    let minute = 60_000_i64;
    let start = after_ms
        .saturating_div(minute)
        .saturating_add(1)
        .saturating_mul(minute);
    for offset in 0..=527_040_i64 {
        let candidate = start.saturating_add(offset.saturating_mul(minute));
        let date = DateTime::<Utc>::from_timestamp_millis(candidate)
            .ok_or_else(|| value_error("schedule timestamp is out of range"))?;
        if cron_matches_datetime(schedule, date)? {
            return Ok(candidate);
        }
    }
    Err(value_error("schedule has no occurrence within one year"))
}

#[pyfunction]
fn cron_matches(schedule: &str, epoch_ms: i64) -> PyResult<bool> {
    let date = DateTime::<Utc>::from_timestamp_millis(epoch_ms)
        .ok_or_else(|| value_error("schedule timestamp is out of range"))?;
    cron_matches_datetime(schedule, date)
}

#[pyfunction]
fn next_scheduled_at(schedule: &str, after_ms: i64) -> PyResult<i64> {
    next_cron_due(schedule, after_ms)
}

#[pyfunction]
fn upsert_scheduled_work(db_path: &str, document_json: &str, now_ms: i64) -> PyResult<String> {
    let document = parse_json(document_json, "scheduled work")?;
    let id = required_string(&document, "id")?;
    let owner_id = required_string(&document, "owner_id")?;
    let kind = required_string(&document, "kind")?;
    let schedule_type = required_string(&document, "schedule_type")?;
    if !matches!(schedule_type, "once" | "cron") {
        return Err(value_error("schedule_type must be once or cron"));
    }
    let schedule = document.get("schedule").and_then(Value::as_str);
    if schedule_type == "cron" {
        expand_cron(schedule.ok_or_else(|| value_error("cron schedule is required"))?)?;
    }
    let due_at_ms = document
        .get("due_at_ms")
        .and_then(Value::as_i64)
        .or_else(|| schedule.and_then(|value| next_cron_due(value, now_ms).ok()))
        .ok_or_else(|| value_error("due_at_ms is required"))?;
    let payload = document
        .get("payload")
        .cloned()
        .unwrap_or_else(|| json!({}));
    let payload = serde_json::to_string(&payload)
        .map_err(|_| value_error("scheduled payload serialization failed"))?;
    let enabled = document
        .get("enabled")
        .and_then(Value::as_bool)
        .unwrap_or(true);
    let max_attempts = document
        .get("max_attempts")
        .and_then(Value::as_i64)
        .unwrap_or(5)
        .clamp(1, 100);
    let connection = open_store(db_path)?;
    connection
        .execute(
            "INSERT INTO scheduled_work(id,owner_id,kind,schedule_type,schedule,due_at_ms,payload_json,enabled,status,max_attempts,updated_at_ms)
             VALUES(?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11)
             ON CONFLICT(id) DO UPDATE SET owner_id=excluded.owner_id,kind=excluded.kind,
               schedule_type=excluded.schedule_type,schedule=excluded.schedule,
               due_at_ms=CASE
                 WHEN scheduled_work.schedule_type=excluded.schedule_type
                  AND COALESCE(scheduled_work.schedule,'')=COALESCE(excluded.schedule,'')
                  AND scheduled_work.status IN ('pending','retry','leased')
                 THEN scheduled_work.due_at_ms ELSE excluded.due_at_ms END,
               payload_json=excluded.payload_json,enabled=excluded.enabled,
               status=CASE WHEN scheduled_work.status='leased' THEN scheduled_work.status ELSE excluded.status END,
               max_attempts=excluded.max_attempts,updated_at_ms=excluded.updated_at_ms",
            params![id, owner_id, kind, schedule_type, schedule, due_at_ms, payload,
                    i64::from(enabled), if enabled { "pending" } else { "cancelled" }, max_attempts, now_ms],
        )
        .map_err(sqlite_error)?;
    Ok(json!({"id": id, "owner_id": owner_id, "kind": kind, "schedule_type": schedule_type,
        "schedule": schedule, "due_at_ms": due_at_ms, "payload": document.get("payload").cloned().unwrap_or_else(|| json!({})),
        "enabled": enabled, "status": if enabled { "pending" } else { "cancelled" }, "max_attempts": max_attempts}).to_string())
}

#[pyfunction]
fn claim_scheduled_work(
    db_path: &str,
    worker_id: &str,
    now_ms: i64,
    lease_ms: i64,
    limit: usize,
    kind: Option<&str>,
) -> PyResult<Vec<String>> {
    let mut connection = open_store(db_path)?;
    let tx = transaction(&mut connection)?;
    let mut query = String::from(
        "SELECT id FROM scheduled_work WHERE enabled=1 AND due_at_ms<=?1
         AND (status IN ('pending','retry') OR (status='leased' AND lease_expires_at_ms<=?1))",
    );
    if kind.is_some() {
        query.push_str(" AND kind=?2");
    }
    query.push_str(" ORDER BY due_at_ms,id LIMIT ?3");
    let ids = {
        let mut statement = tx.prepare(&query).map_err(sqlite_error)?;
        let mapped = statement
            .query_map(params![now_ms, kind, limit.clamp(1, 1_000)], |row| {
                row.get::<_, String>(0)
            })
            .map_err(sqlite_error)?;
        mapped
            .collect::<Result<Vec<_>, _>>()
            .map_err(sqlite_error)?
    };
    let mut claimed = Vec::new();
    for id in ids {
        let changed = tx
            .execute(
                "UPDATE scheduled_work SET status='leased',worker_id=?1,
                   lease_token=lease_token+1,lease_expires_at_ms=?2,attempts=attempts+1,updated_at_ms=?3
                 WHERE id=?4 AND enabled=1 AND (status IN ('pending','retry') OR
                   (status='leased' AND lease_expires_at_ms<=?3))",
                params![worker_id, now_ms.saturating_add(lease_ms.clamp(1_000, 3_600_000)), now_ms, id],
            )
            .map_err(sqlite_error)?;
        if changed == 0 {
            continue;
        }
        let raw: String = tx
            .query_row(
                "SELECT json_object('id',id,'owner_id',owner_id,'kind',kind,
                   'schedule_type',schedule_type,'schedule',schedule,'due_at_ms',due_at_ms,
                   'payload',json(payload_json),'lease_token',lease_token,'attempts',attempts,
                   'max_attempts',max_attempts,'lease_expires_at_ms',lease_expires_at_ms)
                 FROM scheduled_work WHERE id=?1",
                params![id],
                |row| row.get(0),
            )
            .map_err(sqlite_error)?;
        claimed.push(raw);
    }
    tx.commit().map_err(sqlite_error)?;
    Ok(claimed)
}

#[pyfunction]
#[pyo3(signature = (db_path, work_id, worker_id, lease_token, success, now_ms, retry_delay_ms=30_000, error=None))]
#[allow(clippy::too_many_arguments)]
fn complete_scheduled_work(
    db_path: &str,
    work_id: &str,
    worker_id: &str,
    lease_token: i64,
    success: bool,
    now_ms: i64,
    retry_delay_ms: i64,
    error: Option<&str>,
) -> PyResult<String> {
    let mut connection = open_store(db_path)?;
    let tx = transaction(&mut connection)?;
    let row: Option<(String, Option<String>, i64, i64)> = tx
        .query_row(
            "SELECT schedule_type,schedule,attempts,max_attempts FROM scheduled_work
             WHERE id=?1 AND worker_id=?2 AND lease_token=?3 AND status='leased'",
            params![work_id, worker_id, lease_token],
            |row| Ok((row.get(0)?, row.get(1)?, row.get(2)?, row.get(3)?)),
        )
        .optional()
        .map_err(sqlite_error)?;
    let (schedule_type, schedule, attempts, max_attempts) =
        row.ok_or_else(|| PyPermissionError::new_err("scheduled-work lease is stale"))?;
    let (status, due_at_ms) = if success && schedule_type == "cron" {
        (
            "pending",
            next_cron_due(schedule.as_deref().unwrap_or(""), now_ms)?,
        )
    } else if success {
        ("completed", now_ms)
    } else if attempts < max_attempts {
        (
            "retry",
            now_ms.saturating_add(retry_delay_ms.clamp(1_000, 3_600_000)),
        )
    } else {
        ("failed", now_ms)
    };
    tx.execute(
        "UPDATE scheduled_work SET status=?1,due_at_ms=?2,worker_id=NULL,
           lease_expires_at_ms=NULL,last_error=?3,updated_at_ms=?4 WHERE id=?5",
        params![
            status,
            due_at_ms,
            error.map(|item| item.chars().take(500).collect::<String>()),
            now_ms,
            work_id
        ],
    )
    .map_err(sqlite_error)?;
    tx.commit().map_err(sqlite_error)?;
    Ok(
        json!({"id": work_id, "status": status, "due_at_ms": due_at_ms,
        "attempts": attempts, "max_attempts": max_attempts})
        .to_string(),
    )
}

#[pyfunction]
fn cancel_scheduled_work(
    db_path: &str,
    owner_id: &str,
    work_id: &str,
    now_ms: i64,
) -> PyResult<bool> {
    let connection = open_store(db_path)?;
    let changed = connection
        .execute(
            "UPDATE scheduled_work SET enabled=0,status='cancelled',worker_id=NULL,
             lease_expires_at_ms=NULL,updated_at_ms=?1 WHERE id=?2 AND owner_id=?3",
            params![now_ms, work_id, owner_id],
        )
        .map_err(sqlite_error)?;
    Ok(changed == 1)
}

#[pyfunction]
fn next_due_work(db_path: &str, kind: Option<&str>) -> PyResult<Option<i64>> {
    let connection = open_store(db_path)?;
    let mut query = String::from(
        "SELECT MIN(due_at_ms) FROM scheduled_work WHERE enabled=1 AND status IN ('pending','retry')",
    );
    if kind.is_some() {
        query.push_str(" AND kind=?1");
    }
    if let Some(kind) = kind {
        connection
            .query_row(&query, params![kind], |row| row.get(0))
            .map_err(sqlite_error)
    } else {
        connection
            .query_row(&query, [], |row| row.get(0))
            .map_err(sqlite_error)
    }
}

#[pymodule]
fn _curie_task_engine(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(engine_version, module)?)?;
    module.add_function(wrap_pyfunction!(hash_canonical_json, module)?)?;
    module.add_function(wrap_pyfunction!(validate_graph, module)?)?;
    module.add_function(wrap_pyfunction!(decide_retry, module)?)?;
    module.add_function(wrap_pyfunction!(create_or_get, module)?)?;
    module.add_function(wrap_pyfunction!(load_task, module)?)?;
    module.add_function(wrap_pyfunction!(replace_task_snapshot, module)?)?;
    module.add_function(wrap_pyfunction!(list_tasks, module)?)?;
    module.add_function(wrap_pyfunction!(reconcile_task, module)?)?;
    module.add_function(wrap_pyfunction!(claim_step, module)?)?;
    module.add_function(wrap_pyfunction!(heartbeat_step, module)?)?;
    module.add_function(wrap_pyfunction!(complete_step, module)?)?;
    module.add_function(wrap_pyfunction!(fail_step, module)?)?;
    module.add_function(wrap_pyfunction!(set_waiting_approval, module)?)?;
    module.add_function(wrap_pyfunction!(request_cancel, module)?)?;
    module.add_function(wrap_pyfunction!(finalize_task, module)?)?;
    module.add_function(wrap_pyfunction!(cron_matches, module)?)?;
    module.add_function(wrap_pyfunction!(next_scheduled_at, module)?)?;
    module.add_function(wrap_pyfunction!(upsert_scheduled_work, module)?)?;
    module.add_function(wrap_pyfunction!(claim_scheduled_work, module)?)?;
    module.add_function(wrap_pyfunction!(complete_scheduled_work, module)?)?;
    module.add_function(wrap_pyfunction!(cancel_scheduled_work, module)?)?;
    module.add_function(wrap_pyfunction!(next_due_work, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn canonical_hash_is_stable() {
        assert_eq!(hash_text("{\"a\":1}"), hash_text("{\"a\":1}"));
        assert_ne!(hash_text("{\"a\":1}"), hash_text("{\"a\":2}"));
    }

    #[test]
    fn retry_policy_fences_unsafe_mutations() {
        assert_eq!(
            decide_retry(1, 2, false, "none", "timeout", false, 0, false).1,
            "mutation_not_safely_idempotent"
        );
        assert!(decide_retry(1, 2, true, "read_only", "timeout", false, 9000, false).0);
        assert_eq!(
            decide_retry(1, 2, true, "read_only", "timeout", false, 9000, false).2,
            5000
        );
    }
}
