//! Curie's durable task, lease, and idempotency state machine.
//!
//! Natural-language planning, capability policy, approvals, tool execution,
//! evidence verification, compensation, and response wording stay in Python.
//! This crate owns deterministic graph checks, stable hashes, atomic SQLite
//! create-or-replay, lease fencing, retry scheduling, cancellation, and task
//! state reconciliation.

use chrono::DateTime;
use pyo3::exceptions::{PyKeyError, PyPermissionError, PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use rusqlite::{params, Connection, OptionalExtension, Transaction, TransactionBehavior};
use serde_json::{json, Map, Value};
use sha2::{Digest, Sha256};
use std::collections::{HashMap, HashSet};
use std::time::Duration;

const ENGINE_VERSION: &str = "rust-task-engine-v1";
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
               ON durable_task_leases(active, lease_expires_at_ms);",
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

#[pymodule]
fn _curie_task_engine(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(engine_version, module)?)?;
    module.add_function(wrap_pyfunction!(hash_canonical_json, module)?)?;
    module.add_function(wrap_pyfunction!(validate_graph, module)?)?;
    module.add_function(wrap_pyfunction!(decide_retry, module)?)?;
    module.add_function(wrap_pyfunction!(create_or_get, module)?)?;
    module.add_function(wrap_pyfunction!(load_task, module)?)?;
    module.add_function(wrap_pyfunction!(list_tasks, module)?)?;
    module.add_function(wrap_pyfunction!(reconcile_task, module)?)?;
    module.add_function(wrap_pyfunction!(claim_step, module)?)?;
    module.add_function(wrap_pyfunction!(heartbeat_step, module)?)?;
    module.add_function(wrap_pyfunction!(complete_step, module)?)?;
    module.add_function(wrap_pyfunction!(fail_step, module)?)?;
    module.add_function(wrap_pyfunction!(set_waiting_approval, module)?)?;
    module.add_function(wrap_pyfunction!(request_cancel, module)?)?;
    module.add_function(wrap_pyfunction!(finalize_task, module)?)?;
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
