//! Curie's deterministic memory retrieval and ranking hot path.
//!
//! Python retains storage, owner policy, learning, and prompt construction.
//! This module receives an already bounded typed projection, applies the same
//! relevance and packing policy as the Python rollback implementation, and
//! returns only indices plus explainability metadata.

use blake2::digest::{Update, VariableOutput};
use blake2::Blake2bVar;
use lru::LruCache;
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyDict;
use std::collections::{HashMap, HashSet};
use std::num::NonZeroUsize;
use std::sync::{Arc, Mutex, OnceLock};

const DIMENSIONS: usize = 256;
const KERNEL_VERSION: &str = "rust-memory-kernel-v1";
type SelectedMetadata = (usize, f64, String, String);
type PythonRankResult = (Vec<SelectedMetadata>, usize, usize, usize, usize);

const STOP_WORDS: &[&str] = &[
    "a",
    "about",
    "again",
    "all",
    "am",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "been",
    "before",
    "by",
    "can",
    "could",
    "did",
    "do",
    "does",
    "for",
    "from",
    "had",
    "has",
    "have",
    "hello",
    "hey",
    "hi",
    "how",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "me",
    "my",
    "of",
    "on",
    "or",
    "our",
    "please",
    "say",
    "something",
    "tell",
    "that",
    "the",
    "their",
    "them",
    "then",
    "there",
    "these",
    "they",
    "this",
    "to",
    "up",
    "us",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "who",
    "why",
    "will",
    "with",
    "would",
    "you",
    "your",
];

const ALIASES: &[&[&str]] = &[
    &["prefer", "preference", "favorite", "like", "love", "enjoy"],
    &[
        "food",
        "meal",
        "dish",
        "cuisine",
        "eat",
        "diet",
        "dietary",
        "vegan",
        "vegetarian",
        "allergy",
    ],
    &["job", "work", "career", "occupation", "profession"],
    &["home", "house", "location", "live", "city", "country"],
    &["project", "build", "app", "application", "system"],
    &["name", "called", "nickname"],
    &["remember", "recall", "memory", "discussed", "mentioned"],
];

const RELATION_TOKENS: &[&str] = &[
    "prefer",
    "preference",
    "favorite",
    "like",
    "love",
    "enjoy",
    "remember",
    "recall",
    "memory",
    "discuss",
    "mention",
];

#[derive(Debug)]
struct PreparedMemory {
    document_tokens: HashSet<String>,
    key_tokens: HashSet<String>,
    text_normal: String,
    key_normal: String,
    vector: OnceLock<Vec<f64>>,
}

#[derive(Debug)]
struct PreparedCache {
    capacity: usize,
    values: Option<LruCache<[u8; 16], Arc<PreparedMemory>>>,
}

impl PreparedCache {
    fn new() -> Self {
        Self {
            capacity: 0,
            values: None,
        }
    }

    fn configure(&mut self, capacity: usize) {
        if self.capacity == capacity {
            return;
        }
        self.capacity = capacity;
        self.values = NonZeroUsize::new(capacity).map(LruCache::new);
    }

    fn len(&self) -> usize {
        self.values.as_ref().map_or(0, LruCache::len)
    }
}

static PREPARED_CACHE: OnceLock<Mutex<PreparedCache>> = OnceLock::new();

fn prepared_cache() -> &'static Mutex<PreparedCache> {
    PREPARED_CACHE.get_or_init(|| Mutex::new(PreparedCache::new()))
}

#[derive(Debug)]
struct MemoryInput {
    index: usize,
    id: String,
    owner_id: String,
    key: String,
    key_chars: usize,
    value_text: String,
    value_chars: usize,
    text: String,
    active: bool,
    expires_at: Option<f64>,
    observed_at: Option<f64>,
    status: String,
    confidence: f64,
    importance: Option<f64>,
    confirmations: i64,
    kind: String,
    explicit_tier: String,
}

#[derive(Debug)]
struct PreparedQuery {
    tokens: HashSet<String>,
    normal: String,
    vector: OnceLock<Vec<f64>>,
}

#[derive(Clone, Debug)]
struct SemanticMatch {
    coverage: f64,
    key_coverage: f64,
    phrase: f64,
    fuzzy: f64,
    cheap_score: f64,
    reason: &'static str,
}

#[derive(Debug)]
struct Candidate {
    cheap_score: f64,
    confirmations: i64,
    input_position: usize,
    prepared: Arc<PreparedMemory>,
    semantic_match: SemanticMatch,
    hypothesis: bool,
}

#[derive(Debug)]
struct RankedMemory {
    input_position: usize,
    relevance: f64,
    reason: &'static str,
    tier: &'static str,
    confirmations: i64,
}

#[derive(Debug)]
struct RankOutcome {
    selected: Vec<(usize, f64, String, String)>,
    scanned: usize,
    reranked: usize,
    cache_hits: usize,
    cache_misses: usize,
}

fn stem(token: &str) -> String {
    if token.len() > 5 && token.ends_with("ies") {
        return format!("{}y", &token[..token.len() - 3]);
    }
    for suffix in ["ing", "ed", "es", "s"] {
        if suffix == "s" && token.ends_with("ss") {
            continue;
        }
        if token.len() > suffix.len() + 3 && token.ends_with(suffix) {
            return token[..token.len() - suffix.len()].to_owned();
        }
    }
    token.to_owned()
}

fn ascii_words(text: &str) -> Vec<String> {
    let mut words = Vec::new();
    let mut current = String::new();
    for byte in text.bytes() {
        if byte.is_ascii_lowercase() || byte.is_ascii_digit() {
            current.push(byte as char);
        } else if !current.is_empty() {
            words.push(std::mem::take(&mut current));
        }
    }
    if !current.is_empty() {
        words.push(current);
    }
    words
}

fn normal(text: &str) -> String {
    ascii_words(text).join(" ")
}

fn tokens(text: &str) -> HashSet<String> {
    let stop_words: HashSet<&str> = STOP_WORDS.iter().copied().collect();
    ascii_words(text)
        .into_iter()
        .filter(|token| token.len() > 1 && !stop_words.contains(token.as_str()))
        .map(|token| stem(&token))
        .collect()
}

fn expanded(mut values: HashSet<String>) -> HashSet<String> {
    for group in ALIASES {
        let stemmed: Vec<String> = group.iter().map(|item| stem(item)).collect();
        if stemmed.iter().any(|item| values.contains(item)) {
            values.extend(stemmed);
        }
    }
    values
}

fn fingerprint(text: &str) -> [u8; 16] {
    let mut digest = [0_u8; 16];
    let mut hasher = Blake2bVar::new(digest.len()).expect("valid digest size");
    hasher.update(text.as_bytes());
    hasher
        .finalize_variable(&mut digest)
        .expect("fixed output buffer");
    digest
}

fn build_prepared_memory(memory: &MemoryInput) -> Arc<PreparedMemory> {
    Arc::new(PreparedMemory {
        document_tokens: expanded(tokens(&memory.text)),
        key_tokens: expanded(tokens(&memory.key)),
        text_normal: normal(&memory.text),
        key_normal: normal(&memory.key),
        vector: OnceLock::new(),
    })
}

fn prepare_memory(memory: &MemoryInput, capacity: usize) -> (Arc<PreparedMemory>, bool) {
    if capacity == 0 {
        return (build_prepared_memory(memory), false);
    }
    let key = fingerprint(&memory.text);
    let mut cache = prepared_cache()
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    cache.configure(capacity);
    if let Some(prepared) = cache.values.as_mut().and_then(|values| values.get(&key)) {
        return (Arc::clone(prepared), true);
    }
    let prepared = build_prepared_memory(memory);
    if let Some(values) = cache.values.as_mut() {
        values.put(key, Arc::clone(&prepared));
    }
    (prepared, false)
}

fn blake_index(feature: &str) -> usize {
    let mut digest = [0_u8; 4];
    let mut hasher = Blake2bVar::new(digest.len()).expect("valid digest size");
    hasher.update(feature.as_bytes());
    hasher
        .finalize_variable(&mut digest)
        .expect("fixed output buffer");
    u32::from_be_bytes(digest) as usize % DIMENSIONS
}

fn hashed_vector(text: &str, token_set: &HashSet<String>) -> Vec<f64> {
    let mut ordered: Vec<&str> = token_set.iter().map(String::as_str).collect();
    ordered.sort_unstable();
    let mut vector = vec![0.0_f64; DIMENSIONS];
    for token in &ordered {
        vector[blake_index(token)] += 1.0;
    }
    for pair in ordered.windows(2) {
        vector[blake_index(&format!("{}:{}", pair[0], pair[1]))] += 1.0;
    }
    let compact: String = normal(text)
        .bytes()
        .filter(|byte| *byte != b' ')
        .map(char::from)
        .collect();
    let compact = compact.as_bytes();
    if compact.len() >= 3 {
        for index in 0..=compact.len() - 3 {
            let feature = std::str::from_utf8(&compact[index..index + 3])
                .expect("normalization produces ASCII");
            vector[blake_index(feature)] += 1.0;
        }
    }
    let norm = vector.iter().map(|value| value * value).sum::<f64>().sqrt();
    let divisor = if norm == 0.0 { 1.0 } else { norm };
    for value in &mut vector {
        *value /= divisor;
    }
    vector
}

fn cosine(left: &[f64], right: &[f64]) -> f64 {
    left.iter().zip(right).map(|(a, b)| a * b).sum()
}

/// Port of difflib.SequenceMatcher(None, a, b).ratio() for normalized ASCII.
fn sequence_ratio(left: &str, right: &str) -> f64 {
    if left.is_empty() && right.is_empty() {
        return 1.0;
    }
    let a = left.as_bytes();
    let b = right.as_bytes();
    let mut positions: HashMap<u8, Vec<usize>> = HashMap::new();
    for (index, byte) in b.iter().copied().enumerate() {
        positions.entry(byte).or_default().push(index);
    }
    if b.len() >= 200 {
        let popular_after = b.len() / 100 + 1;
        positions.retain(|_, indexes| indexes.len() <= popular_after);
    }
    let mut stack = vec![(0_usize, a.len(), 0_usize, b.len())];
    let mut matches = 0_usize;
    while let Some((alo, ahi, blo, bhi)) = stack.pop() {
        let mut best_i = alo;
        let mut best_j = blo;
        let mut best_size = 0_usize;
        let mut previous: HashMap<usize, usize> = HashMap::new();
        for (i, byte) in a.iter().copied().enumerate().take(ahi).skip(alo) {
            let mut current = HashMap::new();
            if let Some(indexes) = positions.get(&byte) {
                for &j in indexes {
                    if j < blo {
                        continue;
                    }
                    if j >= bhi {
                        break;
                    }
                    let size = previous.get(&j.wrapping_sub(1)).copied().unwrap_or(0) + 1;
                    current.insert(j, size);
                    if size > best_size {
                        best_i = i + 1 - size;
                        best_j = j + 1 - size;
                        best_size = size;
                    }
                }
            }
            previous = current;
        }
        while best_i > alo && best_j > blo && a[best_i - 1] == b[best_j - 1] {
            best_i -= 1;
            best_j -= 1;
            best_size += 1;
        }
        while best_i + best_size < ahi
            && best_j + best_size < bhi
            && a[best_i + best_size] == b[best_j + best_size]
        {
            best_size += 1;
        }
        if best_size == 0 {
            continue;
        }
        matches += best_size;
        if alo < best_i && blo < best_j {
            stack.push((alo, best_i, blo, best_j));
        }
        let after_i = best_i + best_size;
        let after_j = best_j + best_size;
        if after_i < ahi && after_j < bhi {
            stack.push((after_i, ahi, after_j, bhi));
        }
    }
    2.0 * matches as f64 / (a.len() + b.len()) as f64
}

fn semantic_match(query: &PreparedQuery, memory: &PreparedMemory) -> Option<SemanticMatch> {
    if query.tokens.is_empty() {
        return None;
    }
    let overlap: HashSet<&String> = query.tokens.intersection(&memory.document_tokens).collect();
    let relation_tokens: HashSet<&str> = RELATION_TOKENS.iter().copied().collect();
    let subject_overlap = overlap
        .iter()
        .any(|token| !relation_tokens.contains(token.as_str()));
    let query_has_subject = query
        .tokens
        .iter()
        .any(|token| !relation_tokens.contains(token.as_str()));
    let coverage = overlap.len() as f64 / query.tokens.len().max(1) as f64;
    let key_coverage = query.tokens.intersection(&memory.key_tokens).count() as f64
        / query.tokens.len().max(1) as f64;
    let phrase = f64::from(
        query.normal.len() >= 3
            && (memory.text_normal.contains(&query.normal)
                || (!memory.key_normal.is_empty() && query.normal.contains(&memory.key_normal))),
    );
    let fuzzy = if memory.key_normal.is_empty() {
        0.0
    } else {
        sequence_ratio(&query.normal, &memory.key_normal)
    };
    let semantic_gate = if query_has_subject {
        subject_overlap
    } else {
        !overlap.is_empty()
    } || phrase > 0.0
        || fuzzy >= 0.62;
    if !semantic_gate {
        return None;
    }
    let reason = if phrase > 0.0 {
        "exact phrase"
    } else if coverage >= 0.5 {
        "strong topic match"
    } else if !overlap.is_empty() {
        "keyword match"
    } else {
        "fuzzy match"
    };
    Some(SemanticMatch {
        coverage,
        key_coverage,
        phrase,
        fuzzy,
        cheap_score: 0.46 * coverage + 0.25 * key_coverage + 0.18 * phrase + 0.11 * fuzzy,
        reason,
    })
}

fn default_importance(kind: &str) -> f64 {
    match kind {
        "identity" => 1.0,
        "assistant_setting" => 0.95,
        "preference" => 0.8,
        "routine" | "project" => 0.75,
        "relationship" => 0.7,
        "biography" => 0.65,
        "temporary_context" => 0.55,
        "episode" => 0.5,
        "hypothesis" => 0.35,
        _ => 0.5,
    }
}

fn memory_tier(memory: &MemoryInput) -> &'static str {
    match memory.explicit_tier.as_str() {
        "core" => "core",
        "episodic" => "episodic",
        "archival" => "archival",
        _ => match memory.kind.as_str() {
            "identity" | "preference" | "routine" | "assistant_setting" => "core",
            "temporary_context" | "episode" => "episodic",
            _ => "archival",
        },
    }
}

fn freshness(memory: &MemoryInput, tier: &str, now_epoch: f64) -> f64 {
    let Some(observed) = memory.observed_at else {
        return 0.5;
    };
    let age_days = ((now_epoch - observed) / 86_400.0).max(0.0);
    let half_life = match tier {
        "core" => 1825.0,
        "episodic" => 90.0,
        _ => 365.0,
    };
    (-std::f64::consts::LN_2 * age_days / half_life).exp()
}

fn hybrid_score(
    memory: &MemoryInput,
    prepared: &PreparedMemory,
    semantic_match: &SemanticMatch,
    query_vector: &[f64],
    now_epoch: f64,
) -> f64 {
    let memory_vector = prepared
        .vector
        .get_or_init(|| hashed_vector(&memory.text, &prepared.document_tokens));
    let hashed = cosine(query_vector, memory_vector);
    let semantic = 0.38 * semantic_match.coverage
        + 0.2 * semantic_match.key_coverage
        + 0.15 * semantic_match.phrase
        + 0.12 * semantic_match.fuzzy
        + 0.15 * hashed;
    let confidence = memory.confidence.clamp(0.0, 1.0);
    let importance = memory
        .importance
        .unwrap_or_else(|| default_importance(&memory.kind))
        .clamp(0.0, 1.0);
    let confirmations = memory.confirmations.max(0) as f64;
    let reinforcement = (confirmations.ln_1p() / 8_f64.ln()).min(1.0);
    let tier = memory_tier(memory);
    (semantic
        + 0.035 * confidence
        + 0.025 * importance
        + 0.02 * reinforcement
        + 0.02 * freshness(memory, tier, now_epoch))
    .min(1.0)
}

fn python_round_four(value: f64) -> f64 {
    (value * 10_000.0).round_ties_even() / 10_000.0
}

#[allow(clippy::too_many_arguments)]
fn rank_core(
    query: String,
    memories: Vec<MemoryInput>,
    limit: usize,
    budget: usize,
    explicit_search: bool,
    minimum: f64,
    hypothesis_minimum: f64,
    rerank_candidates: i64,
    cache_capacity: usize,
    now_epoch: f64,
    expected_owner: Option<String>,
) -> RankOutcome {
    let prepared_query = PreparedQuery {
        tokens: expanded(tokens(&query)),
        normal: normal(&query),
        vector: OnceLock::new(),
    };
    let rerank_limit = (limit.max(1) * if explicit_search { 8 } else { 4 })
        .max(rerank_candidates.clamp(0, 512) as usize);
    let mut candidates = Vec::new();
    let mut cache_hits = 0_usize;
    let mut cache_misses = 0_usize;
    for (input_position, memory) in memories.iter().enumerate() {
        if expected_owner.as_ref().is_some_and(|owner| {
            memory.owner_id.is_empty() || memory.owner_id.as_str() != owner.as_str()
        }) {
            continue;
        }
        if !memory.active || memory.expires_at.is_some_and(|expiry| expiry <= now_epoch) {
            continue;
        }
        if !matches!(
            memory.status.as_str(),
            "verified" | "recorded" | "hypothesis"
        ) {
            continue;
        }
        let hypothesis = memory.status == "hypothesis";
        if hypothesis && memory.confidence < hypothesis_minimum {
            continue;
        }
        let (prepared, cache_hit) = prepare_memory(memory, cache_capacity);
        cache_hits += usize::from(cache_hit);
        cache_misses += usize::from(!cache_hit);
        let Some(matched) = semantic_match(&prepared_query, &prepared) else {
            continue;
        };
        candidates.push(Candidate {
            cheap_score: matched.cheap_score,
            confirmations: memory.confirmations,
            input_position,
            prepared,
            semantic_match: matched,
            hypothesis,
        });
    }
    candidates.sort_by(|left, right| {
        right
            .cheap_score
            .total_cmp(&left.cheap_score)
            .then_with(|| right.confirmations.cmp(&left.confirmations))
    });
    candidates.truncate(rerank_limit);
    let reranked = candidates.len();
    let query_vector = prepared_query
        .vector
        .get_or_init(|| hashed_vector(&query, &prepared_query.tokens));
    let mut best_by_key: HashMap<String, usize> = HashMap::new();
    let mut ranked = Vec::<RankedMemory>::new();
    for candidate in candidates {
        let memory = &memories[candidate.input_position];
        let score = hybrid_score(
            memory,
            &candidate.prepared,
            &candidate.semantic_match,
            query_vector,
            now_epoch,
        );
        let required = minimum + if candidate.hypothesis { 0.08 } else { 0.0 };
        if score < required {
            continue;
        }
        let relevance = python_round_four(score);
        let dedupe_key = {
            let normalized = normal(&memory.key);
            if normalized.is_empty() {
                memory.id.clone()
            } else {
                normalized
            }
        };
        let item = RankedMemory {
            input_position: candidate.input_position,
            relevance,
            reason: candidate.semantic_match.reason,
            tier: memory_tier(memory),
            confirmations: memory.confirmations,
        };
        if let Some(position) = best_by_key.get(&dedupe_key).copied() {
            if relevance > ranked[position].relevance {
                ranked[position] = item;
            }
        } else {
            best_by_key.insert(dedupe_key, ranked.len());
            ranked.push(item);
        }
    }
    ranked.sort_by(|left, right| {
        right
            .relevance
            .total_cmp(&left.relevance)
            .then_with(|| right.confirmations.cmp(&left.confirmations))
    });
    let budget = budget.max(300);
    let limit = limit.max(1);
    let mut selected = Vec::new();
    let mut selected_values = Vec::<String>::new();
    let mut tier_counts = HashMap::from([("core", 0_usize), ("episodic", 0), ("archival", 0)]);
    let mut used = 0_usize;
    for item in ranked {
        let tier_limit = match item.tier {
            "core" | "episodic" => 3,
            _ => 4,
        };
        let tier_count = tier_counts.get(item.tier).copied().unwrap_or_default();
        if tier_count >= tier_limit {
            continue;
        }
        let memory = &memories[item.input_position];
        let cost = memory.key_chars + memory.value_chars + 80;
        if cost > budget || (!selected.is_empty() && used + cost > budget) {
            continue;
        }
        let value_normal = normal(&memory.value_text);
        if value_normal.len() >= 24
            && selected_values.iter().any(|prior| {
                value_normal == *prior
                    || ((prior.contains(&value_normal) || value_normal.contains(prior))
                        && value_normal.len().min(prior.len()) as f64
                            / value_normal.len().max(prior.len()) as f64
                            >= 0.8)
            })
        {
            continue;
        }
        selected.push((
            memory.index,
            item.relevance,
            item.reason.to_owned(),
            item.tier.to_owned(),
        ));
        selected_values.push(value_normal);
        *tier_counts.entry(item.tier).or_default() += 1;
        used += cost;
        if selected.len() >= limit {
            break;
        }
    }
    RankOutcome {
        selected,
        scanned: memories.len(),
        reranked,
        cache_hits,
        cache_misses,
    }
}

fn required<'py>(dictionary: &Bound<'py, PyDict>, key: &str) -> PyResult<Bound<'py, PyAny>> {
    dictionary
        .get_item(key)?
        .ok_or_else(|| PyValueError::new_err(format!("missing memory field: {key}")))
}

fn extract_memory(item: Bound<'_, PyAny>) -> PyResult<MemoryInput> {
    let dictionary = item
        .cast_into::<PyDict>()
        .map_err(|_| PyValueError::new_err("memory payload must be a dictionary"))?;
    Ok(MemoryInput {
        index: required(&dictionary, "index")?.extract()?,
        id: required(&dictionary, "id")?.extract()?,
        owner_id: required(&dictionary, "owner_id")?.extract()?,
        key: required(&dictionary, "key")?.extract()?,
        key_chars: required(&dictionary, "key_chars")?.extract()?,
        value_text: required(&dictionary, "value_text")?.extract()?,
        value_chars: required(&dictionary, "value_chars")?.extract()?,
        text: required(&dictionary, "text")?.extract()?,
        active: required(&dictionary, "active")?.extract()?,
        expires_at: required(&dictionary, "expires_at")?.extract()?,
        observed_at: required(&dictionary, "observed_at")?.extract()?,
        status: required(&dictionary, "status")?.extract()?,
        confidence: required(&dictionary, "confidence")?.extract()?,
        importance: required(&dictionary, "importance")?.extract()?,
        confirmations: required(&dictionary, "confirmations")?.extract()?,
        kind: required(&dictionary, "kind")?.extract()?,
        explicit_tier: required(&dictionary, "explicit_tier")?.extract()?,
    })
}

#[pyfunction(signature = (query, memories, *, limit, budget, explicit_search, minimum, hypothesis_minimum, rerank_candidates, cache_capacity, now_epoch, expected_owner=None))]
#[allow(clippy::too_many_arguments)]
fn rank_memories(
    py: Python<'_>,
    query: String,
    memories: &Bound<'_, PyAny>,
    limit: usize,
    budget: usize,
    explicit_search: bool,
    minimum: f64,
    hypothesis_minimum: f64,
    rerank_candidates: i64,
    cache_capacity: usize,
    now_epoch: f64,
    expected_owner: Option<String>,
) -> PyResult<PythonRankResult> {
    // Consume a lazy projection iterator. Python never materializes a second
    // list of records while Rust extracts the bounded fields it needs.
    let mut payload = Vec::new();
    for item in memories.try_iter()? {
        payload.push(extract_memory(item?)?);
    }
    let outcome = py.detach(move || {
        rank_core(
            query,
            payload,
            limit,
            budget,
            explicit_search,
            minimum,
            hypothesis_minimum,
            rerank_candidates,
            cache_capacity,
            now_epoch,
            expected_owner,
        )
    });
    Ok((
        outcome.selected,
        outcome.scanned,
        outcome.reranked,
        outcome.cache_hits,
        outcome.cache_misses,
    ))
}

#[pyfunction]
fn clear_cache() {
    let mut cache = prepared_cache()
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner);
    *cache = PreparedCache::new();
}

#[pyfunction]
fn cache_entries() -> usize {
    prepared_cache()
        .lock()
        .unwrap_or_else(std::sync::PoisonError::into_inner)
        .len()
}

#[pyfunction]
fn kernel_version() -> &'static str {
    KERNEL_VERSION
}

#[pymodule]
fn _curie_memory_kernel(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_function(wrap_pyfunction!(rank_memories, module)?)?;
    module.add_function(wrap_pyfunction!(clear_cache, module)?)?;
    module.add_function(wrap_pyfunction!(cache_entries, module)?)?;
    module.add_function(wrap_pyfunction!(kernel_version, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn sequence_matcher_matches_known_python_ratios() {
        assert!((sequence_ratio("favorite food", "favorite food") - 1.0).abs() < 1e-12);
        assert!(
            (sequence_ratio("food enjoy", "favorite food") - 0.347_826_086_956_521_73).abs()
                < 1e-12
        );
        assert!(
            (sequence_ratio("unrelated", "favorite food") - 0.363_636_363_636_363_65).abs() < 1e-12
        );
    }

    #[test]
    fn stemming_and_alias_expansion_match_the_python_contract() {
        assert_eq!(stem("discussed"), "discuss");
        assert_eq!(stem("allergies"), "allergy");
        let values = expanded(tokens("foods I enjoy"));
        assert!(values.contains("meal"));
        assert!(values.contains("favorite"));
    }
}
