//! Curie's bounded connector queue and delivery-state hot path.
//!
//! Python retains provider SDK calls and connector presentation. This module
//! owns admission, FIFO/priority ordering, concurrency limits, deadlines,
//! cancellation, live idempotency keys, counters, and retry-delay policy.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use std::collections::{HashMap, HashSet};
use std::sync::Mutex;

const GATEWAY_VERSION: &str = "rust-connector-gateway-v1";

#[derive(Debug)]
struct Job {
    id: u64,
    sequence: u64,
    priority: i16,
    deadline_ms: Option<u64>,
    idempotency_key: Option<String>,
}

#[derive(Debug)]
struct GatewayState {
    capacity: usize,
    concurrency: usize,
    next_id: u64,
    pending: Vec<Job>,
    active: HashSet<u64>,
    live_keys: HashMap<String, u64>,
    rejected: u64,
    expired: u64,
    cancelled: u64,
    completed: u64,
    duplicates: u64,
}

impl GatewayState {
    fn new(capacity: usize, concurrency: usize) -> Result<Self, &'static str> {
        if capacity == 0 {
            return Err("capacity must be positive");
        }
        if concurrency == 0 || concurrency > capacity {
            return Err("concurrency must be between one and capacity");
        }
        Ok(Self {
            capacity,
            concurrency,
            next_id: 1,
            pending: Vec::with_capacity(capacity),
            active: HashSet::with_capacity(concurrency),
            live_keys: HashMap::with_capacity(capacity),
            rejected: 0,
            expired: 0,
            cancelled: 0,
            completed: 0,
            duplicates: 0,
        })
    }

    fn remove_key(&mut self, job: &Job) {
        if let Some(key) = &job.idempotency_key {
            self.live_keys.remove(key);
        }
    }

    fn best_pending_index(&self) -> Option<usize> {
        self.pending
            .iter()
            .enumerate()
            .min_by(|(_, left), (_, right)| {
                right
                    .priority
                    .cmp(&left.priority)
                    .then_with(|| left.sequence.cmp(&right.sequence))
            })
            .map(|(index, _)| index)
    }

    fn enqueue(
        &mut self,
        idempotency_key: Option<String>,
        priority: i16,
        deadline_ms: Option<u64>,
    ) -> (String, u64) {
        let normalized_key = idempotency_key.and_then(|value| {
            let trimmed = value.trim();
            (!trimmed.is_empty()).then(|| trimmed.to_owned())
        });
        if let Some(key) = &normalized_key {
            if let Some(existing) = self.live_keys.get(key) {
                self.duplicates = self.duplicates.saturating_add(1);
                return ("duplicate".to_owned(), *existing);
            }
        }
        if self.pending.len() + self.active.len() >= self.capacity {
            self.rejected = self.rejected.saturating_add(1);
            return ("overloaded".to_owned(), 0);
        }
        let id = self.next_id;
        self.next_id = self.next_id.saturating_add(1).max(1);
        let job = Job {
            id,
            sequence: id,
            priority,
            deadline_ms,
            idempotency_key: normalized_key.clone(),
        };
        if let Some(key) = normalized_key {
            self.live_keys.insert(key, id);
        }
        self.pending.push(job);
        ("accepted".to_owned(), id)
    }

    fn try_claim(&mut self, id: u64, now_ms: u64) -> String {
        if self.active.contains(&id) {
            return "claimed".to_owned();
        }
        let Some(position) = self.pending.iter().position(|job| job.id == id) else {
            return "unknown".to_owned();
        };
        if self.pending[position]
            .deadline_ms
            .is_some_and(|deadline| now_ms >= deadline)
        {
            let job = self.pending.remove(position);
            self.remove_key(&job);
            self.expired = self.expired.saturating_add(1);
            return "expired".to_owned();
        }
        if self.active.len() >= self.concurrency {
            return "waiting".to_owned();
        }
        if self.best_pending_index() != Some(position) {
            return "waiting".to_owned();
        }
        let job = self.pending.remove(position);
        self.active.insert(job.id);
        "claimed".to_owned()
    }

    fn finish(&mut self, id: u64, cancelled: bool) -> bool {
        if self.active.remove(&id) {
            if let Some(key) = self
                .live_keys
                .iter()
                .find_map(|(key, value)| (*value == id).then(|| key.clone()))
            {
                self.live_keys.remove(&key);
            }
            if cancelled {
                self.cancelled = self.cancelled.saturating_add(1);
            } else {
                self.completed = self.completed.saturating_add(1);
            }
            return true;
        }
        if let Some(position) = self.pending.iter().position(|job| job.id == id) {
            let job = self.pending.remove(position);
            self.remove_key(&job);
            if cancelled {
                self.cancelled = self.cancelled.saturating_add(1);
            } else {
                self.completed = self.completed.saturating_add(1);
            }
            return true;
        }
        false
    }
}

#[pyclass(skip_from_py_object)]
struct DeliveryGateway {
    state: Mutex<GatewayState>,
}

#[pymethods]
impl DeliveryGateway {
    #[new]
    #[pyo3(signature = (capacity=64, concurrency=4))]
    fn new(capacity: usize, concurrency: usize) -> PyResult<Self> {
        Ok(Self {
            state: Mutex::new(
                GatewayState::new(capacity, concurrency).map_err(PyValueError::new_err)?,
            ),
        })
    }

    #[pyo3(signature = (*, idempotency_key=None, priority=0, deadline_ms=None))]
    fn enqueue(
        &self,
        idempotency_key: Option<String>,
        priority: i16,
        deadline_ms: Option<u64>,
    ) -> PyResult<(String, u64)> {
        if idempotency_key
            .as_ref()
            .is_some_and(|value| value.len() > 128)
        {
            return Err(PyValueError::new_err(
                "idempotency key exceeds 128 characters",
            ));
        }
        Ok(self
            .state
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .enqueue(idempotency_key, priority, deadline_ms))
    }

    fn try_claim(&self, job_id: u64, now_ms: u64) -> String {
        self.state
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .try_claim(job_id, now_ms)
    }

    fn complete(&self, job_id: u64) -> bool {
        self.state
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .finish(job_id, false)
    }

    fn cancel(&self, job_id: u64) -> bool {
        self.state
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .finish(job_id, true)
    }

    #[allow(clippy::type_complexity)]
    fn snapshot(&self) -> (usize, usize, usize, usize, bool, u64, u64, u64, u64, u64) {
        let state = self
            .state
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        (
            state.pending.len(),
            state.active.len(),
            state.capacity,
            state.concurrency,
            state.pending.len() + state.active.len() >= state.capacity,
            state.rejected,
            state.expired,
            state.cancelled,
            state.completed,
            state.duplicates,
        )
    }
}

#[pyfunction]
#[pyo3(signature = (attempt, *, base_ms=250, cap_ms=30_000, retry_after_ms=None, jitter_seed=0))]
fn retry_delay_ms(
    attempt: u32,
    base_ms: u64,
    cap_ms: u64,
    retry_after_ms: Option<u64>,
    jitter_seed: u64,
) -> u64 {
    if let Some(provider_delay) = retry_after_ms {
        return provider_delay.min(cap_ms.max(1));
    }
    let exponent = attempt.saturating_sub(1).min(20);
    let ceiling = base_ms
        .max(1)
        .saturating_mul(1_u64 << exponent)
        .min(cap_ms.max(1));
    let mut value = jitter_seed ^ (u64::from(attempt).wrapping_mul(0x9E37_79B9_7F4A_7C15));
    value ^= value >> 12;
    value ^= value << 25;
    value ^= value >> 27;
    value.wrapping_mul(0x2545_F491_4F6C_DD1D) % ceiling.saturating_add(1)
}

#[pyfunction]
fn gateway_version() -> &'static str {
    GATEWAY_VERSION
}

#[pymodule]
fn _curie_connector_gateway(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<DeliveryGateway>()?;
    module.add_function(wrap_pyfunction!(retry_delay_ms, module)?)?;
    module.add_function(wrap_pyfunction!(gateway_version, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn preserves_fifo_and_priority_with_bounded_concurrency() {
        let mut gateway = GatewayState::new(4, 1).expect("valid gateway");
        let (_, first) = gateway.enqueue(None, 0, None);
        let (_, second) = gateway.enqueue(None, 0, None);
        let (_, urgent) = gateway.enqueue(None, 5, None);
        assert_eq!(gateway.try_claim(first, 0), "waiting");
        assert_eq!(gateway.try_claim(urgent, 0), "claimed");
        assert_eq!(gateway.try_claim(second, 0), "waiting");
        assert!(gateway.finish(urgent, false));
        assert_eq!(gateway.try_claim(first, 0), "claimed");
    }

    #[test]
    fn bounds_deduplicates_and_expires_jobs() {
        let mut gateway = GatewayState::new(1, 1).expect("valid gateway");
        let (status, id) = gateway.enqueue(Some("same".to_owned()), 0, Some(10));
        assert_eq!(status, "accepted");
        assert_eq!(
            gateway.enqueue(Some("same".to_owned()), 0, None).0,
            "duplicate"
        );
        assert_eq!(gateway.enqueue(None, 0, None).0, "overloaded");
        assert_eq!(gateway.try_claim(id, 10), "expired");
        assert_eq!(gateway.expired, 1);
    }

    #[test]
    fn retry_delay_is_bounded_and_deterministic() {
        assert_eq!(retry_delay_ms(3, 250, 30_000, Some(9_000), 1), 9_000);
        let first = retry_delay_ms(4, 250, 30_000, None, 42);
        assert_eq!(first, retry_delay_ms(4, 250, 30_000, None, 42));
        assert!(first <= 2_000);
    }
}
