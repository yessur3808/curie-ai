//! Curie's coarse-grained native runtime boundary.
//!
//! Rust owns deterministic, bounded mechanisms: a process-resident SQLite
//! connection, append-only events, ingress admission, text normalization,
//! model residency bookkeeping, redaction/telemetry batching, and safe archive
//! extraction. Python retains policy, model inference, personality, and SDKs.

use pyo3::exceptions::{PyKeyError, PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use regex::{Captures, Regex};
use rusqlite::types::{Value as SqlValue, ValueRef};
use rusqlite::{params, params_from_iter, Connection, OpenFlags};
use serde_json::{json, Map, Value};
use std::collections::{HashMap, VecDeque};
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Seek, Write};
use std::path::{Component, Path};
use std::sync::{Mutex, OnceLock};
use std::time::Duration;
use unicode_normalization::UnicodeNormalization;

const VERSION: &str = "rust-runtime-kernel-v1";

fn runtime_error(message: impl Into<String>) -> PyErr {
    PyRuntimeError::new_err(message.into())
}

fn value_error(message: impl Into<String>) -> PyErr {
    PyValueError::new_err(message.into())
}

fn sqlite_error(error: rusqlite::Error) -> PyErr {
    runtime_error(format!("runtime persistence failed: {error}"))
}

fn parse_json(raw: &str, label: &str) -> PyResult<Value> {
    serde_json::from_str(raw).map_err(|_| value_error(format!("{label} is not valid JSON")))
}

fn to_sql(value: &Value) -> PyResult<SqlValue> {
    match value {
        Value::Null => Ok(SqlValue::Null),
        Value::Bool(item) => Ok(SqlValue::Integer(i64::from(*item))),
        Value::Number(item) => {
            if let Some(value) = item.as_i64() {
                Ok(SqlValue::Integer(value))
            } else if let Some(value) = item.as_f64() {
                Ok(SqlValue::Real(value))
            } else {
                Err(value_error("SQLite numeric parameter is out of range"))
            }
        }
        Value::String(item) => Ok(SqlValue::Text(item.clone())),
        Value::Array(items)
            if items
                .iter()
                .all(|item| item.as_u64().is_some_and(|v| v <= 255)) =>
        {
            Ok(SqlValue::Blob(
                items
                    .iter()
                    .filter_map(Value::as_u64)
                    .map(|v| v as u8)
                    .collect(),
            ))
        }
        _ => Ok(SqlValue::Text(
            serde_json::to_string(value)
                .map_err(|_| value_error("parameter serialization failed"))?,
        )),
    }
}

fn from_sql(value: ValueRef<'_>) -> Value {
    match value {
        ValueRef::Null => Value::Null,
        ValueRef::Integer(item) => json!(item),
        ValueRef::Real(item) => json!(item),
        ValueRef::Text(item) => json!(String::from_utf8_lossy(item)),
        ValueRef::Blob(item) => json!(item),
    }
}

fn open_connection(path: &str, read_only: bool) -> PyResult<Connection> {
    let flags = if read_only {
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX
    } else {
        OpenFlags::SQLITE_OPEN_READ_WRITE
            | OpenFlags::SQLITE_OPEN_CREATE
            | OpenFlags::SQLITE_OPEN_NO_MUTEX
    };
    let connection = Connection::open_with_flags(path, flags).map_err(sqlite_error)?;
    connection
        .busy_timeout(Duration::from_secs(10))
        .map_err(sqlite_error)?;
    if !read_only {
        connection
            .execute_batch(
                "PRAGMA journal_mode=WAL;
                 PRAGMA foreign_keys=ON;
                 PRAGMA synchronous=NORMAL;
                 CREATE TABLE IF NOT EXISTS runtime_events (
                   sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                   stream TEXT NOT NULL,
                   event_type TEXT NOT NULL,
                   owner_id TEXT,
                   payload_json TEXT NOT NULL,
                   created_at_ms INTEGER NOT NULL,
                   dedupe_key TEXT
                 );
                 CREATE UNIQUE INDEX IF NOT EXISTS idx_runtime_events_dedupe
                   ON runtime_events(stream, dedupe_key) WHERE dedupe_key IS NOT NULL;
                 CREATE INDEX IF NOT EXISTS idx_runtime_events_stream_sequence
                   ON runtime_events(stream, sequence);
                 CREATE TABLE IF NOT EXISTS connector_ingress (
                   dedupe_key TEXT PRIMARY KEY,
                   connector TEXT NOT NULL,
                   received_at_ms INTEGER NOT NULL,
                   expires_at_ms INTEGER NOT NULL,
                   state TEXT NOT NULL,
                   response TEXT
                 );
                 CREATE INDEX IF NOT EXISTS idx_connector_ingress_expiry
                   ON connector_ingress(expires_at_ms);",
            )
            .map_err(sqlite_error)?;
    }
    Ok(connection)
}

#[pyclass]
struct PersistenceStore {
    path: String,
    connection: Mutex<Connection>,
}

#[pymethods]
impl PersistenceStore {
    #[new]
    #[pyo3(signature = (path, read_only=false))]
    fn new(path: &str, read_only: bool) -> PyResult<Self> {
        if path.trim().is_empty() {
            return Err(value_error("database path must not be empty"));
        }
        Ok(Self {
            path: path.to_owned(),
            connection: Mutex::new(open_connection(path, read_only)?),
        })
    }

    fn begin(&self) -> PyResult<()> {
        self.connection
            .lock()
            .map_err(|_| runtime_error("persistence lock poisoned"))?
            .execute_batch("BEGIN IMMEDIATE")
            .map_err(sqlite_error)
    }

    fn commit(&self) -> PyResult<()> {
        self.connection
            .lock()
            .map_err(|_| runtime_error("persistence lock poisoned"))?
            .execute_batch("COMMIT")
            .map_err(sqlite_error)
    }

    fn rollback(&self) -> PyResult<()> {
        self.connection
            .lock()
            .map_err(|_| runtime_error("persistence lock poisoned"))?
            .execute_batch("ROLLBACK")
            .map_err(sqlite_error)
    }

    fn execute(&self, sql: &str, params_json: &str) -> PyResult<String> {
        let values = parse_json(params_json, "SQL parameters")?;
        let items = values
            .as_array()
            .ok_or_else(|| value_error("SQL parameters must be a JSON array"))?;
        let parameters = items.iter().map(to_sql).collect::<PyResult<Vec<_>>>()?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| runtime_error("persistence lock poisoned"))?;
        let mut statement = connection.prepare(sql).map_err(sqlite_error)?;
        let column_count = statement.column_count();
        if column_count == 0 {
            let changed = statement
                .execute(params_from_iter(parameters.iter()))
                .map_err(sqlite_error)?;
            return Ok(json!({"rows": [], "rowcount": changed}).to_string());
        }
        let names: Vec<String> = statement
            .column_names()
            .iter()
            .map(|item| (*item).to_owned())
            .collect();
        let mut query = statement
            .query(params_from_iter(parameters.iter()))
            .map_err(sqlite_error)?;
        let mut rows = Vec::new();
        while let Some(row) = query.next().map_err(sqlite_error)? {
            let mut object = Map::new();
            for (index, name) in names.iter().enumerate() {
                object.insert(
                    name.clone(),
                    from_sql(row.get_ref(index).map_err(sqlite_error)?),
                );
            }
            rows.push(Value::Object(object));
        }
        Ok(json!({"rowcount": rows.len(), "rows": rows}).to_string())
    }

    #[pyo3(signature = (stream, event_type, owner_id, payload_json, created_at_ms, dedupe_key=None))]
    fn append_event(
        &self,
        stream: &str,
        event_type: &str,
        owner_id: Option<&str>,
        payload_json: &str,
        created_at_ms: i64,
        dedupe_key: Option<&str>,
    ) -> PyResult<i64> {
        let payload = parse_json(payload_json, "event payload")?;
        if stream.trim().is_empty() || event_type.trim().is_empty() {
            return Err(value_error("event stream and type are required"));
        }
        let payload = serde_json::to_string(&payload)
            .map_err(|_| value_error("event payload serialization failed"))?;
        let connection = self
            .connection
            .lock()
            .map_err(|_| runtime_error("persistence lock poisoned"))?;
        let changed = connection
            .execute(
                "INSERT OR IGNORE INTO runtime_events(stream,event_type,owner_id,payload_json,created_at_ms,dedupe_key)
                 VALUES(?1,?2,?3,?4,?5,?6)",
                params![stream, event_type, owner_id, payload, created_at_ms, dedupe_key],
            )
            .map_err(sqlite_error)?;
        if changed == 0 {
            return connection
                .query_row(
                    "SELECT sequence FROM runtime_events WHERE stream=?1 AND dedupe_key=?2",
                    params![stream, dedupe_key],
                    |row| row.get(0),
                )
                .map_err(sqlite_error);
        }
        Ok(connection.last_insert_rowid())
    }

    #[pyo3(signature = (stream, after_sequence=0, limit=100))]
    fn read_events(&self, stream: &str, after_sequence: i64, limit: usize) -> PyResult<String> {
        let connection = self
            .connection
            .lock()
            .map_err(|_| runtime_error("persistence lock poisoned"))?;
        let mut statement = connection
            .prepare(
                "SELECT sequence,event_type,owner_id,payload_json,created_at_ms,dedupe_key
                 FROM runtime_events WHERE stream=?1 AND sequence>?2 ORDER BY sequence LIMIT ?3",
            )
            .map_err(sqlite_error)?;
        let mapped = statement
            .query_map(params![stream, after_sequence, limit.clamp(1, 10_000)], |row| {
                Ok(json!({
                    "sequence": row.get::<_, i64>(0)?,
                    "event_type": row.get::<_, String>(1)?,
                    "owner_id": row.get::<_, Option<String>>(2)?,
                    "payload": serde_json::from_str::<Value>(&row.get::<_, String>(3)?).unwrap_or(Value::Null),
                    "created_at_ms": row.get::<_, i64>(4)?,
                    "dedupe_key": row.get::<_, Option<String>>(5)?,
                }))
            })
            .map_err(sqlite_error)?;
        let rows = mapped
            .collect::<Result<Vec<_>, _>>()
            .map_err(sqlite_error)?;
        Ok(Value::Array(rows).to_string())
    }

    fn quick_check(&self) -> PyResult<String> {
        self.connection
            .lock()
            .map_err(|_| runtime_error("persistence lock poisoned"))?
            .query_row("PRAGMA quick_check", [], |row| row.get(0))
            .map_err(sqlite_error)
    }

    fn snapshot(&self) -> PyResult<String> {
        let connection = self
            .connection
            .lock()
            .map_err(|_| runtime_error("persistence lock poisoned"))?;
        let events: i64 = connection
            .query_row("SELECT COUNT(*) FROM runtime_events", [], |row| row.get(0))
            .map_err(sqlite_error)?;
        let ingress: i64 = connection
            .query_row("SELECT COUNT(*) FROM connector_ingress", [], |row| {
                row.get(0)
            })
            .map_err(sqlite_error)?;
        Ok(json!({"path": self.path, "events": events, "ingress": ingress}).to_string())
    }
}

fn normalize_text_inner(value: &str) -> PyResult<String> {
    if value.contains('\0') {
        return Err(value_error("text_contains_nul"));
    }
    let normalized: String = value.nfc().collect();
    let mut output = String::with_capacity(normalized.len());
    for character in normalized.chars() {
        match character {
            '\r' => output.push('\n'),
            '\u{200b}' | '\u{200c}' | '\u{200d}' | '\u{feff}' => {}
            '\u{2018}' | '\u{2019}' => output.push('\''),
            '\u{201c}' | '\u{201d}' => output.push('"'),
            '\u{2013}' | '\u{2014}' => output.push('-'),
            '\u{2026}' => output.push_str("..."),
            '\u{00a0}' => output.push(' '),
            item => output.push(item),
        }
    }
    let mut lines = Vec::new();
    let mut blank_count = 0;
    for line in output.replace("\r\n", "\n").lines() {
        let clean = line.split_whitespace().collect::<Vec<_>>().join(" ");
        if clean.is_empty() {
            blank_count += 1;
            if blank_count <= 2 {
                lines.push(String::new());
            }
        } else {
            blank_count = 0;
            lines.push(clean);
        }
    }
    Ok(lines.join("\n").trim().to_owned())
}

#[pyfunction]
fn preprocess_text(value: &str) -> PyResult<String> {
    let normalized = normalize_text_inner(value)?;
    let lower = normalized.to_lowercase();
    let tokens: Vec<&str> = lower
        .split(|character: char| !character.is_alphanumeric() && character != '_')
        .filter(|item| !item.is_empty())
        .collect();
    let action = tokens.iter().find(|item| {
        matches!(
            **item,
            "turn" | "switch" | "set" | "open" | "close" | "start" | "stop" | "remind" | "send"
        )
    });
    let state = if tokens
        .iter()
        .any(|item| matches!(*item, "off" | "disable" | "stop"))
    {
        Some("off")
    } else if tokens
        .iter()
        .any(|item| matches!(*item, "on" | "enable" | "start"))
    {
        Some("on")
    } else {
        None
    };
    let quantifier = tokens
        .iter()
        .find(|item| matches!(**item, "all" | "both" | "every" | "each"))
        .copied();
    static URL: OnceLock<Regex> = OnceLock::new();
    static EMAIL: OnceLock<Regex> = OnceLock::new();
    let url = URL
        .get_or_init(|| Regex::new(r"(?i)\bhttps?://[^\s<]+").expect("static URL regex"))
        .is_match(&normalized);
    let email = EMAIL
        .get_or_init(|| {
            Regex::new(r"(?i)\b[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}\b")
                .expect("static email regex")
        })
        .is_match(&normalized);
    Ok(json!({
        "normalized": normalized,
        "tokens": tokens,
        "features": {
            "action": action,
            "state": state,
            "quantifier": quantifier,
            "has_url": url,
            "has_email": email,
            "is_question": value.trim_end().ends_with('?'),
            "negated": tokens.iter().any(|item| matches!(*item, "not" | "never" | "don't" | "dont")),
        }
    }).to_string())
}

#[pyclass]
struct IngressGateway {
    store: PersistenceStore,
    ttl_ms: i64,
    max_entries: usize,
}

#[pymethods]
impl IngressGateway {
    #[new]
    #[pyo3(signature = (path, ttl_ms=600_000, max_entries=50_000))]
    fn new(path: &str, ttl_ms: i64, max_entries: usize) -> PyResult<Self> {
        Ok(Self {
            store: PersistenceStore::new(path, false)?,
            ttl_ms: ttl_ms.clamp(1_000, 86_400_000),
            max_entries: max_entries.clamp(100, 1_000_000),
        })
    }

    fn admit(&self, connector: &str, dedupe_key: &str, now_ms: i64) -> PyResult<bool> {
        if connector.trim().is_empty() || dedupe_key.trim().is_empty() {
            return Err(value_error("connector and dedupe key are required"));
        }
        let connection = self
            .store
            .connection
            .lock()
            .map_err(|_| runtime_error("ingress lock poisoned"))?;
        connection
            .execute(
                "DELETE FROM connector_ingress WHERE expires_at_ms<=?1",
                params![now_ms],
            )
            .map_err(sqlite_error)?;
        let changed = connection
            .execute(
                "INSERT OR IGNORE INTO connector_ingress(dedupe_key,connector,received_at_ms,expires_at_ms,state)
                 VALUES(?1,?2,?3,?4,'accepted')",
                params![dedupe_key, connector, now_ms, now_ms.saturating_add(self.ttl_ms)],
            )
            .map_err(sqlite_error)?;
        if changed == 1 {
            let count: i64 = connection
                .query_row("SELECT COUNT(*) FROM connector_ingress", [], |row| {
                    row.get(0)
                })
                .map_err(sqlite_error)?;
            if count > self.max_entries as i64 {
                connection
                    .execute(
                        "DELETE FROM connector_ingress WHERE dedupe_key IN (
                           SELECT dedupe_key FROM connector_ingress ORDER BY received_at_ms
                           LIMIT ?1
                         )",
                        params![count - self.max_entries as i64],
                    )
                    .map_err(sqlite_error)?;
            }
        }
        Ok(changed == 1)
    }

    fn store_response(&self, dedupe_key: &str, response: &str) -> PyResult<bool> {
        let changed = self
            .store
            .connection
            .lock()
            .map_err(|_| runtime_error("ingress lock poisoned"))?
            .execute(
                "UPDATE connector_ingress SET state='completed',response=?1 WHERE dedupe_key=?2",
                params![response, dedupe_key],
            )
            .map_err(sqlite_error)?;
        Ok(changed == 1)
    }

    fn response(&self, dedupe_key: &str, now_ms: i64) -> PyResult<Option<String>> {
        let connection = self
            .store
            .connection
            .lock()
            .map_err(|_| runtime_error("ingress lock poisoned"))?;
        let mut statement = connection
            .prepare(
                "SELECT response FROM connector_ingress
                 WHERE dedupe_key=?1 AND expires_at_ms>?2 AND state='completed'",
            )
            .map_err(sqlite_error)?;
        let mut rows = statement
            .query(params![dedupe_key, now_ms])
            .map_err(sqlite_error)?;
        Ok(rows
            .next()
            .map_err(sqlite_error)?
            .and_then(|row| row.get::<_, Option<String>>(0).ok().flatten()))
    }
}

#[derive(Clone)]
struct ModelRecord {
    state: String,
    bytes: u64,
    leases: u64,
    last_used_ms: i64,
    loads: u64,
}

#[pyclass]
struct ModelSupervisor {
    models: Mutex<HashMap<String, ModelRecord>>,
    max_models: usize,
    max_bytes: u64,
}

#[pymethods]
impl ModelSupervisor {
    #[new]
    #[pyo3(signature = (max_models=2, max_bytes=0))]
    fn new(max_models: usize, max_bytes: u64) -> Self {
        Self {
            models: Mutex::new(HashMap::new()),
            max_models: max_models.clamp(1, 64),
            max_bytes,
        }
    }

    fn request_load(&self, name: &str, bytes: u64, now_ms: i64) -> PyResult<String> {
        let mut models = self
            .models
            .lock()
            .map_err(|_| runtime_error("model lock poisoned"))?;
        if let Some(record) = models.get_mut(name) {
            record.last_used_ms = now_ms;
            return Ok(json!({"admitted": true, "already_resident": record.state == "resident", "evict": []}).to_string());
        }
        let mut evict = Vec::new();
        let mut resident_bytes: u64 = models.values().map(|item| item.bytes).sum();
        while models.len() >= self.max_models
            || (self.max_bytes > 0 && resident_bytes.saturating_add(bytes) > self.max_bytes)
        {
            let candidate = models
                .iter()
                .filter(|(_, record)| record.leases == 0)
                .min_by_key(|(_, record)| record.last_used_ms)
                .map(|(key, _)| key.clone());
            let Some(candidate) = candidate else {
                return Ok(json!({"admitted": false, "reason": "all_resident_models_busy", "evict": evict}).to_string());
            };
            if let Some(record) = models.remove(&candidate) {
                resident_bytes = resident_bytes.saturating_sub(record.bytes);
                evict.push(candidate);
            }
        }
        models.insert(
            name.to_owned(),
            ModelRecord {
                state: "loading".to_owned(),
                bytes,
                leases: 0,
                last_used_ms: now_ms,
                loads: 0,
            },
        );
        Ok(json!({"admitted": true, "already_resident": false, "evict": evict}).to_string())
    }

    fn loaded(&self, name: &str, actual_bytes: u64, now_ms: i64) -> PyResult<()> {
        let mut models = self
            .models
            .lock()
            .map_err(|_| runtime_error("model lock poisoned"))?;
        let record = models
            .get_mut(name)
            .ok_or_else(|| PyKeyError::new_err("model was not admitted"))?;
        record.state = "resident".to_owned();
        record.bytes = actual_bytes;
        record.last_used_ms = now_ms;
        record.loads = record.loads.saturating_add(1);
        Ok(())
    }

    fn load_failed(&self, name: &str) -> PyResult<()> {
        self.models
            .lock()
            .map_err(|_| runtime_error("model lock poisoned"))?
            .remove(name);
        Ok(())
    }

    fn acquire(&self, name: &str, now_ms: i64) -> PyResult<bool> {
        let mut models = self
            .models
            .lock()
            .map_err(|_| runtime_error("model lock poisoned"))?;
        let Some(record) = models.get_mut(name) else {
            return Ok(false);
        };
        if record.state != "resident" {
            return Ok(false);
        }
        record.leases = record.leases.saturating_add(1);
        record.last_used_ms = now_ms;
        Ok(true)
    }

    fn release(&self, name: &str, now_ms: i64) -> PyResult<bool> {
        let mut models = self
            .models
            .lock()
            .map_err(|_| runtime_error("model lock poisoned"))?;
        let Some(record) = models.get_mut(name) else {
            return Ok(false);
        };
        record.leases = record.leases.saturating_sub(1);
        record.last_used_ms = now_ms;
        Ok(true)
    }

    fn remove(&self, name: &str, force: bool) -> PyResult<bool> {
        let mut models = self
            .models
            .lock()
            .map_err(|_| runtime_error("model lock poisoned"))?;
        if !force && models.get(name).is_some_and(|item| item.leases > 0) {
            return Ok(false);
        }
        Ok(models.remove(name).is_some())
    }

    fn snapshot(&self) -> PyResult<String> {
        let models = self
            .models
            .lock()
            .map_err(|_| runtime_error("model lock poisoned"))?;
        let values: Map<String, Value> = models
            .iter()
            .map(|(name, record)| {
                (
                    name.clone(),
                    json!({"state": record.state, "bytes": record.bytes, "leases": record.leases,
                   "last_used_ms": record.last_used_ms, "loads": record.loads}),
                )
            })
            .collect();
        Ok(json!({
            "resident_count": models.values().filter(|item| item.state == "resident").count(),
            "resident_bytes": models.values().map(|item| item.bytes).sum::<u64>(),
            "max_models": self.max_models, "max_bytes": self.max_bytes, "models": values
        })
        .to_string())
    }
}

fn sensitive_patterns() -> &'static [Regex] {
    static PATTERNS: OnceLock<Vec<Regex>> = OnceLock::new();
    PATTERNS.get_or_init(|| vec![
        Regex::new(r"(?i)(bearer\s+)[a-z0-9._~+/-]{12,}").expect("bearer regex"),
        Regex::new(r"(?i)((?:api[_-]?key|token|secret|password)\s*[:=]\s*)[^\s,;]+")
            .expect("secret regex"),
        Regex::new(r"\b(?:sk|ghp|github_pat)_[A-Za-z0-9_\-]{12,}\b").expect("token regex"),
        Regex::new(r"\b\d{6,14}:[A-Za-z0-9_-]{20,}\b").expect("telegram regex"),
        Regex::new(r"(?i)\b((?:https?|postgres(?:ql)?|mysql|mongodb(?:\+srv)?|redis)://)[^/\s@]+@")
            .expect("URL credential regex"),
        Regex::new(r"(?i)([?&](?:access_token|refresh_token|token|code|state|client_secret|code_verifier|key|api_key|password|signature|sig)=)[^&#\s]+")
            .expect("query secret regex"),
    ])
}

fn redact_string(value: &str) -> String {
    let mut clean = value.to_owned();
    for (index, pattern) in sensitive_patterns().iter().enumerate() {
        clean = if !(2..=4).contains(&index) {
            pattern
                .replace_all(&clean, |captures: &Captures<'_>| {
                    format!(
                        "{}[REDACTED]",
                        captures.get(1).map_or("", |item| item.as_str())
                    )
                })
                .into_owned()
        } else {
            pattern.replace_all(&clean, "[REDACTED]").into_owned()
        };
    }
    clean
}

fn redact_value(value: Value) -> Value {
    match value {
        Value::Object(items) => Value::Object(items.into_iter().map(|(key, value)| {
            let lower = key.to_lowercase();
            let telemetry = ["first_token", "first_token_ms", "output_tokens", "output_tokens_estimate",
                "prompt_tokens", "prompt_tokens_estimate", "token_counts", "tokens_per_second",
                "tokens_per_second_estimate"];
            static SENSITIVE_FIELD: OnceLock<Regex> = OnceLock::new();
            let sensitive = SENSITIVE_FIELD.get_or_init(|| Regex::new(r"(?i)(?:^|[_-])(?:token|secret|password|passwd|passcode|credential|authorization|cookie|code_verifier|private_key|database_url)$|(?:^|[_-])(?:api|private)[_-]?key$")
                .expect("sensitive field regex"));
            let value = if !telemetry.contains(&lower.as_str()) && sensitive.is_match(&lower) {
                Value::String("[REDACTED]".to_owned())
            } else { redact_value(value) };
            (key, value)
        }).collect()),
        Value::Array(items) => Value::Array(items.into_iter().map(redact_value).collect()),
        Value::String(item) => Value::String(redact_string(&item)),
        item => item,
    }
}

#[pyfunction]
fn redact_json(raw: &str) -> PyResult<String> {
    Ok(redact_value(parse_json(raw, "redaction value")?).to_string())
}

#[pyclass]
struct TelemetryBuffer {
    items: Mutex<VecDeque<Value>>,
    capacity: usize,
    dropped: Mutex<u64>,
}

#[pymethods]
impl TelemetryBuffer {
    #[new]
    #[pyo3(signature = (capacity=4096))]
    fn new(capacity: usize) -> Self {
        Self {
            items: Mutex::new(VecDeque::new()),
            capacity: capacity.clamp(1, 100_000),
            dropped: Mutex::new(0),
        }
    }

    fn push(&self, raw: &str) -> PyResult<()> {
        let value = redact_value(parse_json(raw, "telemetry event")?);
        let mut items = self
            .items
            .lock()
            .map_err(|_| runtime_error("telemetry lock poisoned"))?;
        if items.len() == self.capacity {
            items.pop_front();
            *self
                .dropped
                .lock()
                .map_err(|_| runtime_error("telemetry counter poisoned"))? += 1;
        }
        items.push_back(value);
        Ok(())
    }

    #[pyo3(signature = (limit=256))]
    fn drain(&self, limit: usize) -> PyResult<String> {
        let mut items = self
            .items
            .lock()
            .map_err(|_| runtime_error("telemetry lock poisoned"))?;
        let count = limit.clamp(1, 10_000).min(items.len());
        let values: Vec<Value> = items.drain(..count).collect();
        Ok(Value::Array(values).to_string())
    }

    fn snapshot(&self) -> PyResult<String> {
        let queued = self
            .items
            .lock()
            .map_err(|_| runtime_error("telemetry lock poisoned"))?
            .len();
        let dropped = *self
            .dropped
            .lock()
            .map_err(|_| runtime_error("telemetry counter poisoned"))?;
        Ok(json!({"queued": queued, "capacity": self.capacity, "dropped": dropped}).to_string())
    }

    #[pyo3(signature = (path, max_bytes, limit=256))]
    fn flush_jsonl(&self, path: &str, max_bytes: u64, limit: usize) -> PyResult<usize> {
        let target = Path::new(path);
        if target.as_os_str().is_empty() {
            return Err(value_error("telemetry path must not be empty"));
        }
        if let Some(parent) = target.parent() {
            fs::create_dir_all(parent)
                .map_err(|_| runtime_error("could not create telemetry directory"))?;
        }
        if target
            .metadata()
            .map(|item| item.len() >= max_bytes.max(64_000))
            .unwrap_or(false)
        {
            let rotated = target.with_extension(target.extension().map_or_else(
                || "1".into(),
                |item| {
                    let mut value = item.to_os_string();
                    value.push(".1");
                    value
                },
            ));
            fs::rename(target, rotated)
                .map_err(|_| runtime_error("could not rotate telemetry file"))?;
        }
        let mut items = self
            .items
            .lock()
            .map_err(|_| runtime_error("telemetry lock poisoned"))?;
        let count = limit.clamp(1, 10_000).min(items.len());
        if count == 0 {
            return Ok(0);
        }
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(target)
            .map_err(|_| runtime_error("could not open telemetry file"))?;
        for value in items.drain(..count) {
            serde_json::to_writer(&mut file, &value)
                .map_err(|_| runtime_error("could not encode telemetry event"))?;
            file.write_all(b"\n")
                .map_err(|_| runtime_error("could not write telemetry event"))?;
        }
        file.flush()
            .map_err(|_| runtime_error("could not flush telemetry file"))?;
        Ok(count)
    }
}

fn validate_member_name(name: &str) -> PyResult<()> {
    let path = Path::new(name);
    if path.is_absolute()
        || path
            .components()
            .any(|item| matches!(item, Component::ParentDir | Component::Prefix(_)))
    {
        return Err(value_error("archive contains an unsafe member path"));
    }
    Ok(())
}

fn strip_xml(raw: &str) -> String {
    static PARAGRAPH: OnceLock<Regex> = OnceLock::new();
    static TAB: OnceLock<Regex> = OnceLock::new();
    static TAGS: OnceLock<Regex> = OnceLock::new();
    let paragraph = PARAGRAPH.get_or_init(|| {
        Regex::new(r"(?i)</(?:w:p|text:p|table:table-row)>").expect("paragraph regex")
    });
    let tab = TAB.get_or_init(|| Regex::new(r"(?i)<w:tab[^>]*/>").expect("tab regex"));
    let tags = TAGS.get_or_init(|| Regex::new(r"<[^>]+>").expect("tag regex"));
    let text = paragraph.replace_all(raw, "\n");
    let text = tab.replace_all(&text, "\t");
    tags.replace_all(&text, "")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", "\"")
        .replace("&apos;", "'")
}

fn bounded_text(mut text: String, max_chars: usize) -> PyResult<String> {
    static BLANKS: OnceLock<Regex> = OnceLock::new();
    let clean = BLANKS
        .get_or_init(|| Regex::new(r"\n{4,}").expect("blank regex"))
        .replace_all(text.trim(), "\n\n\n")
        .into_owned();
    if clean.is_empty() {
        return Err(value_error("No readable text was found in this document"));
    }
    if clean.chars().count() <= max_chars {
        return Ok(clean);
    }
    text = clean.chars().take(max_chars).collect();
    text.push_str("\n[Attachment truncated at safety limit]");
    Ok(text)
}

fn read_archive_member<R: Read + Seek>(
    reader: R,
    member: &str,
    max_expanded: u64,
    max_members: usize,
) -> PyResult<String> {
    let mut archive =
        zip::ZipArchive::new(reader).map_err(|_| value_error("invalid document archive"))?;
    if archive.len() > max_members {
        return Err(value_error("archive has too many members"));
    }
    let mut total = 0_u64;
    for index in 0..archive.len() {
        let file = archive
            .by_index_raw(index)
            .map_err(|_| value_error("invalid archive member"))?;
        validate_member_name(file.name())?;
        total = total.saturating_add(file.size());
        if total > max_expanded {
            return Err(value_error(
                "expanded document exceeds the configured size limit",
            ));
        }
    }
    let file = archive
        .by_name(member)
        .map_err(|_| value_error("document content member is missing"))?;
    if file.size() > max_expanded {
        return Err(value_error(
            "expanded document exceeds the configured size limit",
        ));
    }
    let mut bytes = Vec::with_capacity(file.size().min(max_expanded) as usize);
    file.take(max_expanded.saturating_add(1))
        .read_to_end(&mut bytes)
        .map_err(|_| value_error("could not read document content"))?;
    if bytes.len() as u64 > max_expanded {
        return Err(value_error(
            "expanded document exceeds the configured size limit",
        ));
    }
    Ok(String::from_utf8_lossy(&bytes).into_owned())
}

#[pyfunction]
#[pyo3(signature = (path, suffix, max_source_bytes, max_expanded_bytes, max_chars, max_members=2048))]
fn extract_document(
    path: &str,
    suffix: &str,
    max_source_bytes: u64,
    max_expanded_bytes: u64,
    max_chars: usize,
    max_members: usize,
) -> PyResult<String> {
    let metadata = std::fs::metadata(path).map_err(|_| value_error("document does not exist"))?;
    if !metadata.is_file() || metadata.len() > max_source_bytes {
        return Err(value_error("Attachment exceeds the configured size limit"));
    }
    let suffix = suffix.to_lowercase();
    let raw = match suffix.as_str() {
        ".txt" | ".md" | ".csv" | ".json" | ".log" | ".py" | ".js" | ".ts" | ".html" | ".xml" => {
            let mut bytes = Vec::with_capacity(metadata.len() as usize);
            File::open(path)
                .and_then(|mut file| file.read_to_end(&mut bytes))
                .map_err(|_| value_error("could not read document"))?;
            String::from_utf8_lossy(&bytes).into_owned()
        }
        ".docx" => strip_xml(&read_archive_member(
            File::open(path).map_err(|_| value_error("could not open document"))?,
            "word/document.xml",
            max_expanded_bytes,
            max_members,
        )?),
        ".odt" => strip_xml(&read_archive_member(
            File::open(path).map_err(|_| value_error("could not open document"))?,
            "content.xml",
            max_expanded_bytes,
            max_members,
        )?),
        _ => return Err(value_error("unsupported native document type")),
    };
    bounded_text(raw, max_chars.clamp(1, 10_000_000))
}

#[pyfunction]
fn runtime_version() -> &'static str {
    VERSION
}

#[pymodule]
fn _curie_runtime_kernel(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(runtime_version, module)?)?;
    module.add_function(wrap_pyfunction!(preprocess_text, module)?)?;
    module.add_function(wrap_pyfunction!(redact_json, module)?)?;
    module.add_function(wrap_pyfunction!(extract_document, module)?)?;
    module.add_class::<PersistenceStore>()?;
    module.add_class::<IngressGateway>()?;
    module.add_class::<ModelSupervisor>()?;
    module.add_class::<TelemetryBuffer>()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn redacts_nested_secrets() {
        let value = redact_value(
            json!({"authorization": "Bearer abcdefghijklmnop", "nested": "token=abcdefghijklm"}),
        );
        assert_eq!(value["authorization"], "[REDACTED]");
        assert!(!value["nested"].as_str().unwrap().contains("abcdefghijklm"));
    }
}
