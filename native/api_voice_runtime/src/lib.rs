//! Curie's native API and speech coordination boundary.
//!
//! Rust owns bounded request admission, priority ordering, cancellation,
//! idempotent response replay, live-voice session fencing, artifact expiry,
//! speech-worker plans, trained-voice leases, and worker-output validation.
//! Python remains the thin ASGI adapter and hosts model/provider SDKs.

use fs2::FileExt;
use pyo3::exceptions::{PyKeyError, PyPermissionError, PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use regex::Regex;
use serde_json::{json, Map, Value};
use std::cmp::Reverse;
use std::collections::{BinaryHeap, HashMap, HashSet, VecDeque};
use std::fs::{self, File, OpenOptions};
use std::io::ErrorKind;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use std::thread;
use std::time::{Duration, Instant};
use uuid::Uuid;

const VERSION: &str = "rust-api-voice-runtime-v1";
const MAX_RESPONSE_BYTES: usize = 2 * 1024 * 1024;
const MAX_HISTORY_USER_CHARS: usize = 2_000;
const MAX_HISTORY_ASSISTANT_CHARS: usize = 3_000;

fn value_error(message: impl Into<String>) -> PyErr {
    PyValueError::new_err(message.into())
}

fn runtime_error(message: impl Into<String>) -> PyErr {
    PyRuntimeError::new_err(message.into())
}

fn permission_error(message: impl Into<String>) -> PyErr {
    PyPermissionError::new_err(message.into())
}

fn parse_json(raw: &str, label: &str) -> PyResult<Value> {
    serde_json::from_str(raw).map_err(|_| value_error(format!("{label} is not valid JSON")))
}

fn json_string(value: &Value) -> PyResult<String> {
    serde_json::to_string(value).map_err(|_| runtime_error("native serialization failed"))
}

fn bounded_string(value: &str, maximum: usize) -> String {
    value.chars().take(maximum).collect()
}

fn uuid_regex() -> &'static Regex {
    static UUID: OnceLock<Regex> = OnceLock::new();
    UUID.get_or_init(|| {
        Regex::new(r"(?i)^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
            .expect("static UUID regex")
    })
}

fn artifact_regex() -> &'static Regex {
    static ARTIFACT: OnceLock<Regex> = OnceLock::new();
    ARTIFACT.get_or_init(|| {
        Regex::new(r"^(?:voice_[0-9a-fA-F-]+|curie_reply_[0-9A-Za-z_-]+)\.(?:ogg|wav|mp3)$")
            .expect("static artifact regex")
    })
}

#[pyfunction]
fn runtime_version() -> &'static str {
    VERSION
}

#[pyfunction]
#[pyo3(signature = (user_id, message, idempotency_key=None, max_user_chars=256, max_message_chars=10000))]
fn validate_api_message(
    user_id: &str,
    message: &str,
    idempotency_key: Option<&str>,
    max_user_chars: usize,
    max_message_chars: usize,
) -> PyResult<String> {
    if user_id.is_empty()
        || user_id.trim() != user_id
        || user_id.chars().count() > max_user_chars.clamp(1, 4_096)
    {
        return Err(value_error("api_user_id_invalid"));
    }
    if message.is_empty()
        || message.trim() != message
        || message.chars().count() > max_message_chars.clamp(1, 100_000)
        || message.contains('\0')
    {
        return Err(value_error("api_message_invalid"));
    }
    let request_id = match idempotency_key {
        Some(value) if uuid_regex().is_match(value) => value.to_ascii_lowercase(),
        Some(_) => return Err(value_error("api_idempotency_key_invalid")),
        None => Uuid::new_v4().to_string(),
    };
    json_string(&json!({
        "request_id": request_id,
        "user_id": user_id,
        "message": message,
    }))
}

fn audio_suffix(path: &Path) -> Option<String> {
    path.extension()
        .and_then(|item| item.to_str())
        .map(|item| format!(".{}", item.to_ascii_lowercase()))
}

fn supported_audio_suffix(suffix: &str) -> bool {
    matches!(
        suffix,
        ".wav" | ".ogg" | ".m4a" | ".mp3" | ".webm" | ".opus" | ".flac"
    )
}

#[pyfunction]
fn validate_audio_file(path: &str, max_bytes: u64) -> PyResult<String> {
    let source = Path::new(path);
    let metadata = fs::symlink_metadata(source).map_err(|_| value_error("speech_audio_missing"))?;
    if !metadata.file_type().is_file() || metadata.file_type().is_symlink() {
        return Err(value_error("speech_audio_not_regular"));
    }
    let size = metadata.len();
    if size == 0 || size > max_bytes.clamp(1, 1024 * 1024 * 1024) {
        return Err(value_error("speech_audio_size_invalid"));
    }
    let suffix = audio_suffix(source).ok_or_else(|| value_error("speech_audio_format_invalid"))?;
    if !supported_audio_suffix(&suffix) {
        return Err(value_error("speech_audio_format_invalid"));
    }
    json_string(&json!({"path": path, "suffix": suffix, "size_bytes": size}))
}

#[pyfunction]
fn validate_audio_artifact_name(name: &str) -> PyResult<String> {
    if !artifact_regex().is_match(name)
        || Path::new(name).file_name().and_then(|v| v.to_str()) != Some(name)
    {
        return Err(value_error("voice_artifact_name_invalid"));
    }
    Ok(name.to_owned())
}

#[derive(Clone)]
struct ActiveRequest {
    owner_id: String,
    kind: String,
    lease_token: u64,
    deadline_ms: i64,
}

#[derive(Clone)]
struct CachedResponse {
    response: Value,
    expires_at_ms: i64,
}

#[derive(Default)]
struct ApiMetrics {
    admitted: u64,
    completed: u64,
    failed: u64,
    cancelled: u64,
    overloaded: u64,
    owner_busy: u64,
    replays: u64,
    expired: u64,
}

#[derive(Default)]
struct ApiState {
    sequence: u64,
    active: HashMap<String, ActiveRequest>,
    owner_counts: HashMap<String, usize>,
    responses: HashMap<String, CachedResponse>,
    response_order: VecDeque<String>,
    metrics: ApiMetrics,
}

#[pyclass]
struct ApiRequestCoordinator {
    capacity: usize,
    per_owner: usize,
    response_capacity: usize,
    state: Mutex<ApiState>,
}

impl ApiRequestCoordinator {
    fn prune(state: &mut ApiState, now_ms: i64, response_capacity: usize) {
        let expired_active: Vec<String> = state
            .active
            .iter()
            .filter(|(_, value)| value.deadline_ms <= now_ms)
            .map(|(key, _)| key.clone())
            .collect();
        for key in expired_active {
            if let Some(request) = state.active.remove(&key) {
                if let Some(count) = state.owner_counts.get_mut(&request.owner_id) {
                    *count = count.saturating_sub(1);
                    if *count == 0 {
                        state.owner_counts.remove(&request.owner_id);
                    }
                }
                state.metrics.expired = state.metrics.expired.saturating_add(1);
            }
        }
        state
            .responses
            .retain(|_, response| response.expires_at_ms > now_ms);
        let live_keys: HashSet<String> = state.responses.keys().cloned().collect();
        state.response_order.retain(|key| live_keys.contains(key));
        while state.responses.len() > response_capacity {
            let Some(key) = state.response_order.pop_front() else {
                break;
            };
            state.responses.remove(&key);
        }
    }

    fn remove_active(state: &mut ApiState, request_id: &str) -> Option<ActiveRequest> {
        let request = state.active.remove(request_id)?;
        if let Some(count) = state.owner_counts.get_mut(&request.owner_id) {
            *count = count.saturating_sub(1);
            if *count == 0 {
                state.owner_counts.remove(&request.owner_id);
            }
        }
        Some(request)
    }
}

#[pymethods]
impl ApiRequestCoordinator {
    #[new]
    #[pyo3(signature = (capacity=64, per_owner=2, response_capacity=2048))]
    fn new(capacity: usize, per_owner: usize, response_capacity: usize) -> Self {
        Self {
            capacity: capacity.clamp(1, 100_000),
            per_owner: per_owner.clamp(1, 1_000),
            response_capacity: response_capacity.clamp(1, 100_000),
            state: Mutex::new(ApiState::default()),
        }
    }

    #[pyo3(signature = (request_id, owner_id, kind, now_ms, lease_ms=120000))]
    fn begin(
        &self,
        request_id: &str,
        owner_id: &str,
        kind: &str,
        now_ms: i64,
        lease_ms: i64,
    ) -> PyResult<String> {
        if request_id.trim().is_empty() || owner_id.trim().is_empty() || kind.trim().is_empty() {
            return Err(value_error("api_request_identity_invalid"));
        }
        let mut state = self
            .state
            .lock()
            .map_err(|_| runtime_error("api coordinator lock poisoned"))?;
        Self::prune(&mut state, now_ms, self.response_capacity);
        if let Some(cached) = state.responses.get(request_id).cloned() {
            state.metrics.replays = state.metrics.replays.saturating_add(1);
            return json_string(&json!({"outcome": "replay", "response": cached.response}));
        }
        if state.active.contains_key(request_id) {
            return json_string(&json!({"outcome": "in_progress"}));
        }
        if state.active.len() >= self.capacity {
            state.metrics.overloaded = state.metrics.overloaded.saturating_add(1);
            return json_string(&json!({"outcome": "overloaded"}));
        }
        if state.owner_counts.get(owner_id).copied().unwrap_or(0) >= self.per_owner {
            state.metrics.owner_busy = state.metrics.owner_busy.saturating_add(1);
            return json_string(&json!({"outcome": "owner_busy"}));
        }
        state.sequence = state.sequence.saturating_add(1).max(1);
        let token = state.sequence;
        state.active.insert(
            request_id.to_owned(),
            ActiveRequest {
                owner_id: owner_id.to_owned(),
                kind: kind.to_owned(),
                lease_token: token,
                deadline_ms: now_ms.saturating_add(lease_ms.clamp(1_000, 900_000)),
            },
        );
        *state.owner_counts.entry(owner_id.to_owned()).or_default() += 1;
        state.metrics.admitted = state.metrics.admitted.saturating_add(1);
        json_string(&json!({"outcome": "admitted", "lease_token": token}))
    }

    #[pyo3(signature = (request_id, lease_token, response_json, now_ms, cache_ttl_ms=600000))]
    fn finish(
        &self,
        request_id: &str,
        lease_token: u64,
        response_json: &str,
        now_ms: i64,
        cache_ttl_ms: i64,
    ) -> PyResult<bool> {
        if response_json.len() > MAX_RESPONSE_BYTES {
            return Err(value_error("api_response_too_large"));
        }
        let response = parse_json(response_json, "API response")?;
        let mut state = self
            .state
            .lock()
            .map_err(|_| runtime_error("api coordinator lock poisoned"))?;
        let Some(active) = state.active.get(request_id) else {
            return Ok(false);
        };
        if active.lease_token != lease_token {
            return Err(permission_error("api_request_lease_stale"));
        }
        Self::remove_active(&mut state, request_id);
        state.responses.insert(
            request_id.to_owned(),
            CachedResponse {
                response,
                expires_at_ms: now_ms.saturating_add(cache_ttl_ms.clamp(1_000, 86_400_000)),
            },
        );
        state.response_order.push_back(request_id.to_owned());
        Self::prune(&mut state, now_ms, self.response_capacity);
        state.metrics.completed = state.metrics.completed.saturating_add(1);
        Ok(true)
    }

    fn fail(&self, request_id: &str, lease_token: u64) -> PyResult<bool> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| runtime_error("api coordinator lock poisoned"))?;
        let Some(active) = state.active.get(request_id) else {
            return Ok(false);
        };
        if active.lease_token != lease_token {
            return Err(permission_error("api_request_lease_stale"));
        }
        Self::remove_active(&mut state, request_id);
        state.metrics.failed = state.metrics.failed.saturating_add(1);
        Ok(true)
    }

    fn cancel(&self, request_id: &str) -> PyResult<bool> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| runtime_error("api coordinator lock poisoned"))?;
        let removed = Self::remove_active(&mut state, request_id).is_some();
        if removed {
            state.metrics.cancelled = state.metrics.cancelled.saturating_add(1);
        }
        Ok(removed)
    }

    fn snapshot(&self, now_ms: i64) -> PyResult<String> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| runtime_error("api coordinator lock poisoned"))?;
        Self::prune(&mut state, now_ms, self.response_capacity);
        let kinds = state
            .active
            .values()
            .fold(HashMap::new(), |mut result, request| {
                *result.entry(request.kind.clone()).or_insert(0_usize) += 1;
                result
            });
        json_string(&json!({
            "backend": "rust",
            "active_requests": state.active.len(),
            "capacity": self.capacity,
            "per_owner": self.per_owner,
            "cached_responses": state.responses.len(),
            "active_kinds": kinds,
            "metrics": {
                "admitted": state.metrics.admitted,
                "completed": state.metrics.completed,
                "failed": state.metrics.failed,
                "cancelled": state.metrics.cancelled,
                "overloaded": state.metrics.overloaded,
                "owner_busy": state.metrics.owner_busy,
                "replays": state.metrics.replays,
                "expired": state.metrics.expired,
            }
        }))
    }
}

#[derive(Clone, Eq, PartialEq, Ord, PartialOrd)]
struct QueueKey {
    priority: i32,
    sequence: u64,
    request_id: String,
}

#[derive(Clone, Copy, Eq, PartialEq)]
enum JobStatus {
    Queued,
    Running,
    Cancelled,
}

#[derive(Clone)]
struct NativeJob {
    owner_id: String,
    priority: i32,
    sequence: u64,
    created_ms: i64,
    status: JobStatus,
}

#[derive(Default)]
struct InferenceMetrics {
    submitted: u64,
    completed: u64,
    failed: u64,
    cancelled: u64,
    overloaded: u64,
    model_reloads: u64,
}

#[derive(Default)]
struct InferenceState {
    sequence: u64,
    queue: BinaryHeap<Reverse<QueueKey>>,
    jobs: HashMap<String, NativeJob>,
    metrics: InferenceMetrics,
}

#[pyclass]
struct InferenceCoordinator {
    capacity: usize,
    workers: usize,
    state: Mutex<InferenceState>,
}

fn priority_value(priority: &str) -> PyResult<i32> {
    match priority {
        "active" | "interactive" => Ok(0),
        "background" => Ok(10),
        _ => Err(value_error("inference_priority_invalid")),
    }
}

#[pymethods]
impl InferenceCoordinator {
    #[new]
    #[pyo3(signature = (capacity=16, workers=1))]
    fn new(capacity: usize, workers: usize) -> Self {
        Self {
            capacity: capacity.clamp(1, 100_000),
            workers: workers.clamp(1, 256),
            state: Mutex::new(InferenceState::default()),
        }
    }

    fn submit(
        &self,
        request_id: &str,
        owner_id: &str,
        priority: &str,
        created_ms: i64,
    ) -> PyResult<String> {
        if request_id.trim().is_empty() || owner_id.trim().is_empty() {
            return Err(value_error("inference_request_identity_invalid"));
        }
        let value = priority_value(priority)?;
        let mut state = self
            .state
            .lock()
            .map_err(|_| runtime_error("inference coordinator lock poisoned"))?;
        if state.jobs.contains_key(request_id) {
            return Err(value_error("inference_request_duplicate"));
        }
        let queued = state
            .jobs
            .values()
            .filter(|job| job.status == JobStatus::Queued)
            .count();
        if queued >= self.capacity {
            state.metrics.overloaded = state.metrics.overloaded.saturating_add(1);
            return Err(runtime_error("inference_queue_full"));
        }
        state.sequence = state.sequence.saturating_add(1).max(1);
        let sequence = state.sequence;
        state.queue.push(Reverse(QueueKey {
            priority: value,
            sequence,
            request_id: request_id.to_owned(),
        }));
        state.jobs.insert(
            request_id.to_owned(),
            NativeJob {
                owner_id: owner_id.to_owned(),
                priority: value,
                sequence,
                created_ms,
                status: JobStatus::Queued,
            },
        );
        state.metrics.submitted = state.metrics.submitted.saturating_add(1);
        json_string(&json!({"request_id": request_id, "sequence": sequence}))
    }

    fn pop(&self, started_ms: i64) -> PyResult<Option<String>> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| runtime_error("inference coordinator lock poisoned"))?;
        while let Some(Reverse(key)) = state.queue.pop() {
            let Some(job) = state.jobs.get_mut(&key.request_id) else {
                continue;
            };
            if job.status != JobStatus::Queued || job.sequence != key.sequence {
                continue;
            }
            job.status = JobStatus::Running;
            return Ok(Some(json_string(&json!({
                "request_id": key.request_id,
                "owner_id": job.owner_id,
                "priority": job.priority,
                "created_ms": job.created_ms,
                "started_ms": started_ms,
                "queue_ms": started_ms.saturating_sub(job.created_ms),
            }))?));
        }
        Ok(None)
    }

    fn cancel(&self, request_id: &str) -> PyResult<String> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| runtime_error("inference coordinator lock poisoned"))?;
        let Some(job) = state.jobs.get_mut(request_id) else {
            return json_string(&json!({"found": false}));
        };
        let was_running = job.status == JobStatus::Running;
        job.status = JobStatus::Cancelled;
        if !was_running {
            state.jobs.remove(request_id);
        }
        state.metrics.cancelled = state.metrics.cancelled.saturating_add(1);
        json_string(&json!({"found": true, "was_running": was_running}))
    }

    fn cancel_owner(&self, owner_id: &str) -> PyResult<Vec<String>> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| runtime_error("inference coordinator lock poisoned"))?;
        let identifiers: Vec<String> = state
            .jobs
            .iter()
            .filter(|(_, job)| job.owner_id == owner_id)
            .map(|(identifier, _)| identifier.clone())
            .collect();
        for identifier in &identifiers {
            let was_running = state
                .jobs
                .get(identifier)
                .is_some_and(|job| job.status == JobStatus::Running);
            if was_running {
                if let Some(job) = state.jobs.get_mut(identifier) {
                    job.status = JobStatus::Cancelled;
                }
            } else {
                state.jobs.remove(identifier);
            }
            state.metrics.cancelled = state.metrics.cancelled.saturating_add(1);
        }
        Ok(identifiers)
    }

    fn is_cancelled(&self, request_id: &str) -> PyResult<bool> {
        let state = self
            .state
            .lock()
            .map_err(|_| runtime_error("inference coordinator lock poisoned"))?;
        Ok(state
            .jobs
            .get(request_id)
            .is_some_and(|job| job.status == JobStatus::Cancelled))
    }

    fn finish(&self, request_id: &str, outcome: &str) -> PyResult<bool> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| runtime_error("inference coordinator lock poisoned"))?;
        let Some(job) = state.jobs.remove(request_id) else {
            return Ok(false);
        };
        if job.status != JobStatus::Cancelled {
            match outcome {
                "completed" => state.metrics.completed = state.metrics.completed.saturating_add(1),
                "failed" => state.metrics.failed = state.metrics.failed.saturating_add(1),
                "cancelled" => state.metrics.cancelled = state.metrics.cancelled.saturating_add(1),
                _ => return Err(value_error("inference_outcome_invalid")),
            }
        }
        Ok(true)
    }

    fn note_model_reload(&self) -> PyResult<()> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| runtime_error("inference coordinator lock poisoned"))?;
        state.metrics.model_reloads = state.metrics.model_reloads.saturating_add(1);
        Ok(())
    }

    fn snapshot(&self) -> PyResult<String> {
        let state = self
            .state
            .lock()
            .map_err(|_| runtime_error("inference coordinator lock poisoned"))?;
        let queued = state
            .jobs
            .values()
            .filter(|job| job.status == JobStatus::Queued)
            .count();
        let running = state
            .jobs
            .values()
            .filter(|job| job.status == JobStatus::Running)
            .count();
        json_string(&json!({
            "backend": "rust",
            "submitted": state.metrics.submitted,
            "completed": state.metrics.completed,
            "failed": state.metrics.failed,
            "cancelled": state.metrics.cancelled,
            "overloaded": state.metrics.overloaded,
            "model_reloads": state.metrics.model_reloads,
            "queue_depth": queued,
            "queue_capacity": self.capacity,
            "queue_utilization_percent": (queued as f64 / self.capacity as f64 * 1000.0).round() / 10.0,
            "saturated": queued >= self.capacity,
            "active_requests": state.jobs.len(),
            "running_requests": running,
            "workers": self.workers,
        }))
    }
}

#[derive(Clone)]
struct VoiceSession {
    owner_id: String,
    kind: String,
    state: String,
    created_at_ms: i64,
    last_seen_ms: i64,
    expires_at_ms: i64,
    turn_sequence: u64,
    active_token: Option<u64>,
    history: VecDeque<Value>,
}

#[derive(Default)]
struct VoiceMetrics {
    opened: u64,
    closed: u64,
    expired: u64,
    operations: u64,
    cancelled: u64,
    busy_rejections: u64,
    artifacts_registered: u64,
    artifacts_expired: u64,
}

#[derive(Default)]
struct VoiceState {
    sessions: HashMap<String, VoiceSession>,
    owner_sessions: HashMap<String, String>,
    artifacts: HashMap<String, i64>,
    metrics: VoiceMetrics,
}

#[pyclass]
struct VoiceSessionManager {
    capacity: usize,
    history_turns: usize,
    state: Mutex<VoiceState>,
}

impl VoiceSessionManager {
    fn prune(state: &mut VoiceState, now_ms: i64) {
        let expired: Vec<String> = state
            .sessions
            .iter()
            .filter(|(_, session)| session.expires_at_ms <= now_ms)
            .map(|(identifier, _)| identifier.clone())
            .collect();
        for identifier in expired {
            if let Some(session) = state.sessions.remove(&identifier) {
                let owner_key = format!("{}:{}", session.kind, session.owner_id);
                if state.owner_sessions.get(&owner_key) == Some(&identifier) {
                    state.owner_sessions.remove(&owner_key);
                }
                state.metrics.expired = state.metrics.expired.saturating_add(1);
            }
        }
    }

    fn session_json(identifier: &str, session: &VoiceSession) -> Value {
        json!({
            "session_id": identifier,
            "owner_id": session.owner_id,
            "kind": session.kind,
            "state": session.state,
            "created_at_ms": session.created_at_ms,
            "last_seen_ms": session.last_seen_ms,
            "expires_at_ms": session.expires_at_ms,
            "active": session.active_token.is_some(),
            "active_token": session.active_token,
            "turn_sequence": session.turn_sequence,
            "history_turns": session.history.len(),
        })
    }
}

fn operation_state(operation: &str) -> PyResult<&'static str> {
    match operation {
        "listening" => Ok("listening"),
        "transcription" => Ok("transcribing"),
        "thinking" | "chat" => Ok("thinking"),
        "synthesis" => Ok("synthesizing"),
        "playback" => Ok("speaking"),
        _ => Err(value_error("voice_operation_invalid")),
    }
}

fn valid_transition(current: &str, next: &str) -> bool {
    current == next
        || matches!(
            (current, next),
            ("listening", "transcribing")
                | ("transcribing", "thinking")
                | ("thinking", "synthesizing")
                | ("thinking", "speaking")
                | ("synthesizing", "speaking")
                | ("speaking", "listening")
                | (_, "idle")
                | (_, "closing")
        )
}

#[pymethods]
impl VoiceSessionManager {
    #[new]
    #[pyo3(signature = (capacity=128, history_turns=4))]
    fn new(capacity: usize, history_turns: usize) -> Self {
        Self {
            capacity: capacity.clamp(1, 100_000),
            history_turns: history_turns.clamp(1, 100),
            state: Mutex::new(VoiceState::default()),
        }
    }

    #[pyo3(signature = (owner_id, kind, now_ms, ttl_ms=3600000, requested_id=None, reuse_owner=true))]
    fn open(
        &self,
        owner_id: &str,
        kind: &str,
        now_ms: i64,
        ttl_ms: i64,
        requested_id: Option<&str>,
        reuse_owner: bool,
    ) -> PyResult<String> {
        if owner_id.trim().is_empty() || kind.trim().is_empty() {
            return Err(value_error("voice_session_identity_invalid"));
        }
        let mut state = self
            .state
            .lock()
            .map_err(|_| runtime_error("voice session lock poisoned"))?;
        Self::prune(&mut state, now_ms);
        let owner_key = format!("{kind}:{owner_id}");
        let existing_id = requested_id.map(str::to_owned).or_else(|| {
            reuse_owner
                .then(|| state.owner_sessions.get(&owner_key).cloned())
                .flatten()
        });
        if let Some(identifier) = existing_id {
            let session = state
                .sessions
                .get_mut(&identifier)
                .ok_or_else(|| PyKeyError::new_err("voice_session_missing"))?;
            if session.owner_id != owner_id || session.kind != kind {
                return Err(permission_error("voice_session_owner_mismatch"));
            }
            session.last_seen_ms = now_ms;
            session.expires_at_ms = now_ms.saturating_add(ttl_ms.clamp(5_000, 86_400_000));
            return json_string(&Self::session_json(&identifier, session));
        }
        if state.sessions.len() >= self.capacity {
            return Err(runtime_error("voice_session_capacity_reached"));
        }
        let identifier = Uuid::new_v4().to_string();
        let session = VoiceSession {
            owner_id: owner_id.to_owned(),
            kind: kind.to_owned(),
            state: "idle".to_owned(),
            created_at_ms: now_ms,
            last_seen_ms: now_ms,
            expires_at_ms: now_ms.saturating_add(ttl_ms.clamp(5_000, 86_400_000)),
            turn_sequence: 0,
            active_token: None,
            history: VecDeque::new(),
        };
        let result = Self::session_json(&identifier, &session);
        state.sessions.insert(identifier.clone(), session);
        state.owner_sessions.insert(owner_key, identifier);
        state.metrics.opened = state.metrics.opened.saturating_add(1);
        json_string(&result)
    }

    #[pyo3(signature = (session_id, operation, now_ms, ttl_ms=3600000))]
    fn start_operation(
        &self,
        session_id: &str,
        operation: &str,
        now_ms: i64,
        ttl_ms: i64,
    ) -> PyResult<String> {
        let next_state = operation_state(operation)?;
        let mut state = self
            .state
            .lock()
            .map_err(|_| runtime_error("voice session lock poisoned"))?;
        Self::prune(&mut state, now_ms);
        let Some(session) = state.sessions.get_mut(session_id) else {
            return Err(PyKeyError::new_err("voice_session_missing"));
        };
        if session.active_token.is_some() {
            let current_state = session.state.clone();
            let _ = session;
            state.metrics.busy_rejections = state.metrics.busy_rejections.saturating_add(1);
            return json_string(&json!({"outcome": "busy", "state": current_state}));
        }
        session.turn_sequence = session.turn_sequence.saturating_add(1).max(1);
        let token = session.turn_sequence;
        session.active_token = Some(token);
        session.state = next_state.to_owned();
        session.last_seen_ms = now_ms;
        session.expires_at_ms = now_ms.saturating_add(ttl_ms.clamp(5_000, 86_400_000));
        state.metrics.operations = state.metrics.operations.saturating_add(1);
        json_string(&json!({"outcome": "started", "token": token, "state": next_state}))
    }

    fn transition(
        &self,
        session_id: &str,
        token: u64,
        next_state: &str,
        now_ms: i64,
    ) -> PyResult<String> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| runtime_error("voice session lock poisoned"))?;
        let session = state
            .sessions
            .get_mut(session_id)
            .ok_or_else(|| PyKeyError::new_err("voice_session_missing"))?;
        if session.active_token != Some(token) {
            return Err(permission_error("voice_session_token_stale"));
        }
        if !valid_transition(&session.state, next_state) {
            return Err(value_error("voice_session_transition_invalid"));
        }
        session.state = next_state.to_owned();
        session.last_seen_ms = now_ms;
        json_string(&Self::session_json(session_id, session))
    }

    fn finish_operation(&self, session_id: &str, token: u64, now_ms: i64) -> PyResult<bool> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| runtime_error("voice session lock poisoned"))?;
        let session = state
            .sessions
            .get_mut(session_id)
            .ok_or_else(|| PyKeyError::new_err("voice_session_missing"))?;
        if session.active_token != Some(token) {
            return Err(permission_error("voice_session_token_stale"));
        }
        session.active_token = None;
        session.state = "idle".to_owned();
        session.last_seen_ms = now_ms;
        Ok(true)
    }

    fn cancel_active(&self, session_id: &str, now_ms: i64) -> PyResult<bool> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| runtime_error("voice session lock poisoned"))?;
        let session = state
            .sessions
            .get_mut(session_id)
            .ok_or_else(|| PyKeyError::new_err("voice_session_missing"))?;
        let cancelled = session.active_token.take().is_some();
        session.turn_sequence = session.turn_sequence.saturating_add(1);
        session.state = "idle".to_owned();
        session.last_seen_ms = now_ms;
        if cancelled {
            state.metrics.cancelled = state.metrics.cancelled.saturating_add(1);
        }
        Ok(cancelled)
    }

    fn token_active(&self, session_id: &str, token: u64) -> PyResult<bool> {
        let state = self
            .state
            .lock()
            .map_err(|_| runtime_error("voice session lock poisoned"))?;
        Ok(state
            .sessions
            .get(session_id)
            .is_some_and(|session| session.active_token == Some(token)))
    }

    fn close(&self, session_id: &str) -> PyResult<bool> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| runtime_error("voice session lock poisoned"))?;
        let Some(session) = state.sessions.remove(session_id) else {
            return Ok(false);
        };
        let owner_key = format!("{}:{}", session.kind, session.owner_id);
        if state.owner_sessions.get(&owner_key) == Some(&session_id.to_owned()) {
            state.owner_sessions.remove(&owner_key);
        }
        state.metrics.closed = state.metrics.closed.saturating_add(1);
        Ok(true)
    }

    fn inspect(&self, session_id: &str, now_ms: i64) -> PyResult<String> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| runtime_error("voice session lock poisoned"))?;
        Self::prune(&mut state, now_ms);
        let session = state
            .sessions
            .get(session_id)
            .ok_or_else(|| PyKeyError::new_err("voice_session_missing"))?;
        json_string(&Self::session_json(session_id, session))
    }

    fn history(&self, session_id: &str) -> PyResult<String> {
        let state = self
            .state
            .lock()
            .map_err(|_| runtime_error("voice session lock poisoned"))?;
        let session = state
            .sessions
            .get(session_id)
            .ok_or_else(|| PyKeyError::new_err("voice_session_missing"))?;
        json_string(&Value::Array(session.history.iter().cloned().collect()))
    }

    fn append_history(&self, session_id: &str, user: &str, assistant: &str) -> PyResult<()> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| runtime_error("voice session lock poisoned"))?;
        let session = state
            .sessions
            .get_mut(session_id)
            .ok_or_else(|| PyKeyError::new_err("voice_session_missing"))?;
        session.history.push_back(json!({
            "user": bounded_string(user, MAX_HISTORY_USER_CHARS),
            "assistant": bounded_string(assistant, MAX_HISTORY_ASSISTANT_CHARS),
        }));
        while session.history.len() > self.history_turns {
            session.history.pop_front();
        }
        Ok(())
    }

    fn register_artifact(&self, name: &str, now_ms: i64, ttl_ms: i64) -> PyResult<()> {
        validate_audio_artifact_name(name)?;
        let mut state = self
            .state
            .lock()
            .map_err(|_| runtime_error("voice session lock poisoned"))?;
        let artifact_capacity = self.capacity.saturating_mul(64).max(64);
        if !state.artifacts.contains_key(name) && state.artifacts.len() >= artifact_capacity {
            if let Some(oldest) = state
                .artifacts
                .iter()
                .min_by_key(|(_, expires_at)| **expires_at)
                .map(|(artifact, _)| artifact.clone())
            {
                state.artifacts.remove(&oldest);
            }
        }
        state.artifacts.insert(
            name.to_owned(),
            now_ms.saturating_add(ttl_ms.clamp(1_000, 86_400_000)),
        );
        state.metrics.artifacts_registered = state.metrics.artifacts_registered.saturating_add(1);
        Ok(())
    }

    fn expired_artifacts(&self, now_ms: i64, limit: usize) -> PyResult<Vec<String>> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| runtime_error("voice session lock poisoned"))?;
        let mut expired: Vec<String> = state
            .artifacts
            .iter()
            .filter(|(_, expires_at)| **expires_at <= now_ms)
            .map(|(name, _)| name.clone())
            .take(limit.clamp(1, 10_000))
            .collect();
        expired.sort();
        for name in &expired {
            state.artifacts.remove(name);
        }
        state.metrics.artifacts_expired = state
            .metrics
            .artifacts_expired
            .saturating_add(expired.len() as u64);
        Ok(expired)
    }

    fn snapshot(&self, now_ms: i64) -> PyResult<String> {
        let mut state = self
            .state
            .lock()
            .map_err(|_| runtime_error("voice session lock poisoned"))?;
        Self::prune(&mut state, now_ms);
        let busy = state
            .sessions
            .values()
            .filter(|session| session.active_token.is_some())
            .count();
        json_string(&json!({
            "backend": "rust",
            "sessions": state.sessions.len(),
            "busy_sessions": busy,
            "capacity": self.capacity,
            "artifacts": state.artifacts.len(),
            "metrics": {
                "opened": state.metrics.opened,
                "closed": state.metrics.closed,
                "expired": state.metrics.expired,
                "operations": state.metrics.operations,
                "cancelled": state.metrics.cancelled,
                "busy_rejections": state.metrics.busy_rejections,
                "artifacts_registered": state.metrics.artifacts_registered,
                "artifacts_expired": state.metrics.artifacts_expired,
            }
        }))
    }
}

fn accent_language(accent: &str) -> Option<&'static str> {
    match accent.to_ascii_lowercase().as_str() {
        "american" => Some("en"),
        "british" | "australian" | "indian" | "canadian" | "irish" | "scottish" => Some("en"),
        "french" => Some("fr"),
        "german" => Some("de"),
        "spanish" | "mexican" => Some("es"),
        "italian" => Some("it"),
        "portuguese" | "brazilian" => Some("pt"),
        "russian" => Some("ru"),
        "japanese" => Some("ja"),
        "chinese" => Some("zh"),
        "korean" => Some("ko"),
        "arabic" => Some("ar"),
        "hindi" => Some("hi"),
        _ => None,
    }
}

fn normalize_language(language: &str, accent: Option<&str>, auto_detect: bool) -> Option<String> {
    if let Some(value) = accent.and_then(accent_language) {
        return Some(value.to_owned());
    }
    if auto_detect {
        return None;
    }
    let clean = language.trim().to_ascii_lowercase();
    if clean.is_empty()
        || clean.len() > 12
        || !clean.chars().all(|c| c.is_ascii_alphabetic() || c == '-')
    {
        None
    } else {
        Some(clean.split('-').next().unwrap_or("en").to_owned())
    }
}

#[pyfunction]
#[pyo3(signature = (root, python, audio_path, language="en", accent=None, auto_detect=true, max_bytes=26214400))]
fn transcription_plan(
    root: &str,
    python: &str,
    audio_path: &str,
    language: &str,
    accent: Option<&str>,
    auto_detect: bool,
    max_bytes: u64,
) -> PyResult<String> {
    validate_audio_file(audio_path, max_bytes)?;
    let interpreter = Path::new(python);
    if !interpreter.is_file() {
        return Err(runtime_error("speech_worker_unavailable"));
    }
    let root_path = Path::new(root);
    if !root_path.is_dir() {
        return Err(value_error("speech_root_invalid"));
    }
    let language = normalize_language(language, accent, auto_detect);
    let mut command = vec![
        python.to_owned(),
        "-m".to_owned(),
        "services.trained_voice.transcribe".to_owned(),
        audio_path.to_owned(),
    ];
    if let Some(value) = &language {
        command.push(value.clone());
    }
    json_string(&json!({
        "backend": "faster-whisper-isolated",
        "command": command,
        "cwd": root,
        "language_hint": language,
    }))
}

#[pyfunction]
#[pyo3(signature = (stdout, max_chars=10000))]
fn parse_transcription(stdout: &[u8], max_chars: usize) -> PyResult<String> {
    if stdout.len() > 1024 * 1024 {
        return Err(value_error("speech_worker_output_too_large"));
    }
    let raw =
        std::str::from_utf8(stdout).map_err(|_| value_error("speech_worker_output_invalid"))?;
    for line in raw.lines().rev() {
        let Ok(value) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        let Some(text) = value.get("text").and_then(Value::as_str) else {
            continue;
        };
        let clean = text.split_whitespace().collect::<Vec<_>>().join(" ");
        if clean.is_empty() {
            return Err(value_error("speech_transcript_empty"));
        }
        if clean.chars().count() > max_chars.clamp(1, 100_000) {
            return Err(value_error("speech_transcript_too_large"));
        }
        let language = value
            .get("language")
            .and_then(Value::as_str)
            .filter(|item| item.len() <= 16)
            .unwrap_or("unknown");
        return json_string(&json!({"text": clean, "language": language}));
    }
    Err(value_error("speech_worker_output_invalid"))
}

fn read_voice_config(home: &Path) -> Option<Map<String, Value>> {
    let raw = fs::read_to_string(home.join("config.json")).ok()?;
    serde_json::from_str::<Value>(&raw)
        .ok()?
        .as_object()
        .cloned()
}

fn config_string<'a>(config: &'a Map<String, Value>, key: &str) -> Option<&'a str> {
    config.get(key).and_then(Value::as_str)
}

fn trained_ready(home: &Path, speech_python: &Path, config: &Map<String, Value>) -> bool {
    if config.get("enabled").and_then(Value::as_bool) != Some(true)
        || config_string(config, "engine") != Some("chatterbox-nano")
        || !speech_python.is_file()
    {
        return false;
    }
    let Some(profile) = config_string(config, "profile") else {
        return false;
    };
    let Some(model) = config_string(config, "model") else {
        return false;
    };
    if !home.join(profile).is_file() {
        return false;
    }
    if let Some(adapter) = config_string(config, "adapter") {
        if !adapter.is_empty() && !home.join(adapter).is_file() {
            return false;
        }
    }
    [
        "t3_nano_v1.safetensors",
        "s3gen_meanflow.safetensors",
        "ve.safetensors",
        "tokenizer_config.json",
    ]
    .iter()
    .all(|name| home.join(model).join(name).is_file())
}

#[pyfunction]
fn trained_voice_health(home: &str, speech_python: &str) -> PyResult<String> {
    let home = Path::new(home);
    let config = read_voice_config(home).unwrap_or_default();
    let ready = trained_ready(home, Path::new(speech_python), &config);
    json_string(&json!({
        "ready": ready,
        "backend": if ready { Some("chatterbox-nano-trained") } else { None },
        "engine": config.get("engine"),
        "trained": config.get("adapter").and_then(Value::as_str).is_some_and(|v| !v.is_empty()),
        "method": config.get("method"),
        "revision": config.get("revision"),
        "worker_isolated": true,
        "threads": config.get("threads").and_then(Value::as_i64).unwrap_or(0),
        "coordinator": "rust",
    }))
}

#[pyfunction]
#[pyo3(signature = (root, home, speech_python, output, delivery_json, stream_dir=None))]
fn synthesis_plan(
    root: &str,
    home: &str,
    speech_python: &str,
    output: &str,
    delivery_json: &str,
    stream_dir: Option<&str>,
) -> PyResult<String> {
    let config = read_voice_config(Path::new(home)).unwrap_or_default();
    if !trained_ready(Path::new(home), Path::new(speech_python), &config) {
        return Err(runtime_error("trained_voice_unavailable"));
    }
    let delivery = parse_json(delivery_json, "trained voice delivery")?;
    if !delivery.is_object() || delivery_json.len() > 16 * 1024 {
        return Err(value_error("trained_voice_delivery_invalid"));
    }
    if !Path::new(root).is_dir() || output.trim().is_empty() {
        return Err(value_error("trained_voice_path_invalid"));
    }
    let mut command = vec![
        speech_python.to_owned(),
        "-m".to_owned(),
        "services.trained_voice.reference_voice".to_owned(),
        "--config".to_owned(),
        Path::new(home)
            .join("config.json")
            .to_string_lossy()
            .to_string(),
        "--output".to_owned(),
        output.to_owned(),
        "--delivery".to_owned(),
        serde_json::to_string(&delivery)
            .map_err(|_| value_error("trained_voice_delivery_invalid"))?,
    ];
    if let Some(directory) = stream_dir {
        if directory.trim().is_empty() {
            return Err(value_error("trained_voice_stream_path_invalid"));
        }
        command.extend(["--stream-dir".to_owned(), directory.to_owned()]);
    }
    json_string(&json!({"command": command, "cwd": root}))
}

#[pyfunction]
fn validate_synthesis_text(text: &str, maximum: usize) -> PyResult<()> {
    if text.trim().is_empty()
        || text.chars().count() > maximum.clamp(1, 100_000)
        || text.contains('\0')
    {
        return Err(value_error("trained_voice_invalid_text"));
    }
    Ok(())
}

#[pyfunction]
fn parse_voice_metrics(stdout: &[u8]) -> PyResult<String> {
    if stdout.len() > 1024 * 1024 {
        return Err(value_error("trained_voice_metrics_too_large"));
    }
    let allowed = [
        "engine",
        "method",
        "revision",
        "deliveryMode",
        "seconds",
        "generationSeconds",
        "peakMiB",
        "chunks",
        "retries",
    ];
    let raw = String::from_utf8_lossy(stdout);
    for line in raw.lines().rev() {
        let Ok(Value::Object(value)) = serde_json::from_str::<Value>(line) else {
            continue;
        };
        let filtered: Map<String, Value> = allowed
            .iter()
            .filter_map(|key| {
                value
                    .get(*key)
                    .cloned()
                    .map(|item| ((*key).to_owned(), item))
            })
            .collect();
        if !filtered.is_empty() {
            return json_string(&Value::Object(filtered));
        }
    }
    Ok("{}".to_owned())
}

#[pyfunction]
fn parse_voice_event(line: &[u8]) -> PyResult<Option<String>> {
    if line.len() > 64 * 1024 {
        return Err(value_error("trained_voice_event_too_large"));
    }
    let value: Value = match serde_json::from_slice(line) {
        Ok(value) => value,
        Err(_) => return Ok(None),
    };
    if value.get("type").and_then(Value::as_str) != Some("audio") {
        return Ok(None);
    }
    if let Some(url) = value.get("url").and_then(Value::as_str) {
        let Some(name) = url.strip_prefix("/audio/") else {
            return Err(value_error("trained_voice_event_url_invalid"));
        };
        validate_audio_artifact_name(name)?;
    }
    Ok(Some(json_string(&value)?))
}

#[pyclass]
struct VoiceLease {
    file: Option<File>,
    path: PathBuf,
}

#[pymethods]
impl VoiceLease {
    fn release(&mut self) -> PyResult<bool> {
        let Some(file) = self.file.take() else {
            return Ok(false);
        };
        FileExt::unlock(&file).map_err(|_| runtime_error("trained_voice_unlock_failed"))?;
        Ok(true)
    }

    fn path(&self) -> String {
        self.path.to_string_lossy().to_string()
    }
}

impl Drop for VoiceLease {
    fn drop(&mut self) {
        if let Some(file) = self.file.take() {
            let _ = FileExt::unlock(&file);
        }
    }
}

#[pyfunction]
#[pyo3(signature = (path, wait_ms=3000, poll_ms=50))]
fn acquire_voice_lease(
    py: Python<'_>,
    path: &str,
    wait_ms: u64,
    poll_ms: u64,
) -> PyResult<VoiceLease> {
    let target = PathBuf::from(path);
    if let Some(parent) = target.parent() {
        fs::create_dir_all(parent).map_err(|_| runtime_error("trained_voice_lock_path_failed"))?;
    }
    let wait = wait_ms.min(30_000);
    let poll = poll_ms.clamp(10, 250);
    let file = py.detach(|| -> Result<File, String> {
        let file = OpenOptions::new()
            .read(true)
            .write(true)
            .create(true)
            .truncate(false)
            .open(&target)
            .map_err(|_| "trained_voice_lock_open_failed".to_owned())?;
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            let _ = fs::set_permissions(&target, fs::Permissions::from_mode(0o600));
        }
        let started = Instant::now();
        loop {
            match FileExt::try_lock_exclusive(&file) {
                Ok(()) => return Ok(file),
                Err(error) if error.kind() == ErrorKind::WouldBlock => {
                    if started.elapsed() >= Duration::from_millis(wait) {
                        return Err("trained_voice_busy".to_owned());
                    }
                    thread::sleep(Duration::from_millis(poll));
                }
                Err(_) => return Err("trained_voice_lock_failed".to_owned()),
            }
        }
    });
    match file {
        Ok(file) => Ok(VoiceLease {
            file: Some(file),
            path: target,
        }),
        Err(code) if code == "trained_voice_busy" => Err(runtime_error(code)),
        Err(code) => Err(runtime_error(code)),
    }
}

#[pymodule]
fn _curie_api_voice_runtime(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(runtime_version, module)?)?;
    module.add_function(wrap_pyfunction!(validate_api_message, module)?)?;
    module.add_function(wrap_pyfunction!(validate_audio_file, module)?)?;
    module.add_function(wrap_pyfunction!(validate_audio_artifact_name, module)?)?;
    module.add_function(wrap_pyfunction!(transcription_plan, module)?)?;
    module.add_function(wrap_pyfunction!(parse_transcription, module)?)?;
    module.add_function(wrap_pyfunction!(trained_voice_health, module)?)?;
    module.add_function(wrap_pyfunction!(synthesis_plan, module)?)?;
    module.add_function(wrap_pyfunction!(validate_synthesis_text, module)?)?;
    module.add_function(wrap_pyfunction!(parse_voice_metrics, module)?)?;
    module.add_function(wrap_pyfunction!(parse_voice_event, module)?)?;
    module.add_function(wrap_pyfunction!(acquire_voice_lease, module)?)?;
    module.add_class::<ApiRequestCoordinator>()?;
    module.add_class::<InferenceCoordinator>()?;
    module.add_class::<VoiceSessionManager>()?;
    module.add_class::<VoiceLease>()?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn language_and_transition_rules_are_deterministic() {
        assert_eq!(
            normalize_language("en-US", None, false).as_deref(),
            Some("en")
        );
        assert_eq!(
            normalize_language("en", Some("french"), true).as_deref(),
            Some("fr")
        );
        assert!(valid_transition("thinking", "synthesizing"));
        assert!(!valid_transition("listening", "speaking"));
    }

    #[test]
    fn filenames_are_bounded() {
        assert!(artifact_regex().is_match("voice_550e8400-e29b-41d4-a716-446655440000.ogg"));
        assert!(!artifact_regex().is_match("../voice_bad.ogg"));
    }
}
