//! Curie's deterministic device/entity resolution hot path.
//!
//! Python retains provider discovery, owner-scoped alias persistence, policy,
//! device control and natural responses. Rust receives a credential-free,
//! bounded canonical projection and returns indices plus explainability.

use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyDict;
use std::cmp::Ordering;
use std::collections::{HashMap, HashSet};
use std::sync::Arc;

const RESOLVER_VERSION: &str = "rust-device-resolver-v1";
type ResolutionTuple = (String, Vec<usize>, f64, String, Vec<String>);

#[derive(Clone, Debug)]
struct DeviceInput {
    index: usize,
    canonical_id: String,
    provider: String,
    provider_id: String,
    display_name: String,
    normalized_name: String,
    room: Option<String>,
    capabilities: HashSet<String>,
    online_status: Option<bool>,
}

#[derive(Clone, Debug)]
struct AliasInput {
    normalized_alias: String,
    status: String,
    device_key: Option<String>,
    provider: Option<String>,
    provider_id: Option<String>,
    expired: bool,
}

#[derive(Clone, Debug)]
struct GroupSpec {
    capability: String,
    room: Option<String>,
    online_only: bool,
}

fn ascii_words(value: &str) -> Vec<String> {
    let mut words = Vec::new();
    let mut current = String::new();
    for byte in value.bytes() {
        let normalized = byte.to_ascii_lowercase();
        if normalized.is_ascii_lowercase() || normalized.is_ascii_digit() {
            current.push(normalized as char);
        } else if !current.is_empty() {
            words.push(std::mem::take(&mut current));
        }
    }
    if !current.is_empty() {
        words.push(current);
    }
    words
}

fn normalize(value: &str) -> String {
    ascii_words(value).join(" ")
}

fn canonical_token(value: &str) -> &str {
    match value {
        "bulb" | "bulbs" | "lamp" | "lamps" | "lights" | "led" | "leds" => "light",
        _ => value,
    }
}

fn semantic_tokens(value: &str) -> HashSet<String> {
    ascii_words(value)
        .into_iter()
        .filter(|token| !matches!(token.as_str(), "the" | "my" | "a" | "an" | "device"))
        .map(|token| canonical_token(&token).to_owned())
        .collect()
}

fn semantic_name(value: &str) -> String {
    ascii_words(value)
        .into_iter()
        .map(|token| canonical_token(&token).to_owned())
        .collect::<Vec<_>>()
        .join(" ")
}

fn strip_trailing_number(value: &str) -> String {
    let words = ascii_words(value);
    if words.last().is_some_and(|word| word.parse::<u64>().is_ok()) {
        words[..words.len().saturating_sub(1)].join(" ")
    } else {
        words.join(" ")
    }
}

fn group_spec(target: &str) -> Option<GroupSpec> {
    let mut words = ascii_words(target);
    let mut online_only = false;
    while let Some(first) = words.first().map(String::as_str) {
        if first == "online" {
            online_only = true;
        }
        if matches!(
            first,
            "all" | "every" | "each" | "both" | "the" | "my" | "our" | "online" | "of"
        ) {
            words.remove(0);
        } else {
            break;
        }
    }
    let is_light = |word: &str| {
        matches!(
            word,
            "light" | "lights" | "lamp" | "lamps" | "bulb" | "bulbs"
        )
    };
    if words.len() == 1 && is_light(&words[0]) {
        return Some(GroupSpec {
            capability: "light".to_owned(),
            room: None,
            online_only,
        });
    }
    if words.len() == 1 && matches!(words[0].as_str(), "switch" | "switches") {
        return Some(GroupSpec {
            capability: "switch".to_owned(),
            room: None,
            online_only,
        });
    }
    if matches!(words.as_slice(), [first, second] if first == "controllable" && matches!(second.as_str(), "device" | "devices"))
    {
        return Some(GroupSpec {
            capability: "controllable".to_owned(),
            room: None,
            online_only,
        });
    }
    if words.len() >= 2 && is_light(&words[0]) {
        let start = if words.get(1).is_some_and(|word| word == "in") {
            2
        } else {
            1
        };
        let mut room_words = words[start..].to_vec();
        while room_words.first().is_some_and(|word| word == "the") {
            room_words.remove(0);
        }
        if !room_words.is_empty() {
            return Some(GroupSpec {
                capability: "light".to_owned(),
                room: Some(room_words.join(" ")),
                online_only,
            });
        }
    }
    if words.len() >= 2 && words.last().is_some_and(|word| is_light(word)) {
        let room = words[..words.len() - 1].join(" ");
        if !room.is_empty() {
            return Some(GroupSpec {
                capability: "light".to_owned(),
                room: Some(room),
                online_only,
            });
        }
    }
    None
}

/// Port of `difflib.SequenceMatcher(None, a, b).ratio()` for normalized ASCII.
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

fn unique_resolution(devices: &[&DeviceInput], confidence: f64, reason: &str) -> ResolutionTuple {
    if devices.len() == 1 {
        return (
            "resolved".to_owned(),
            vec![devices[0].index],
            confidence,
            reason.to_owned(),
            Vec::new(),
        );
    }
    (
        "ambiguous".to_owned(),
        Vec::new(),
        confidence,
        format!("ambiguous_{reason}"),
        devices
            .iter()
            .map(|item| item.display_name.clone())
            .collect(),
    )
}

#[allow(clippy::too_many_arguments)]
fn resolve_core(
    target: String,
    devices: &[DeviceInput],
    aliases: &[AliasInput],
    explicit_provider: Option<&str>,
    recent_entity_ids: &[String],
    consequential: bool,
    consequential_threshold: f64,
    read_threshold: f64,
    ambiguity_gap: f64,
) -> ResolutionTuple {
    let query = normalize(&target);
    if query.is_empty() {
        return (
            "not_found".to_owned(),
            Vec::new(),
            0.0,
            "empty target".to_owned(),
            Vec::new(),
        );
    }
    let trimmed = target.trim();
    let exact: Vec<&DeviceInput> = devices
        .iter()
        .filter(|item| item.canonical_id.eq_ignore_ascii_case(trimmed))
        .collect();
    if !exact.is_empty() {
        return unique_resolution(&exact, 1.0, "canonical_id");
    }
    if let Some(provider) = explicit_provider {
        let exact: Vec<&DeviceInput> = devices
            .iter()
            .filter(|item| {
                item.provider.eq_ignore_ascii_case(provider)
                    && item.provider_id.eq_ignore_ascii_case(trimmed)
            })
            .collect();
        if !exact.is_empty() {
            return unique_resolution(&exact, 1.0, "provider_id");
        }
    }
    if aliases
        .iter()
        .any(|item| item.status == "rejected" && item.normalized_alias == query)
    {
        return (
            "rejected_alias".to_owned(),
            Vec::new(),
            1.0,
            "explicitly_rejected_alias".to_owned(),
            Vec::new(),
        );
    }
    let exact: Vec<&DeviceInput> = devices
        .iter()
        .filter(|item| item.normalized_name == query)
        .collect();
    if !exact.is_empty() {
        return unique_resolution(&exact, 1.0, "exact_display_name");
    }
    let mut alias_matches: Vec<&DeviceInput> = Vec::new();
    let mut expired_match = false;
    for alias in aliases {
        if alias.status != "confirmed" || alias.normalized_alias != query {
            continue;
        }
        if alias.expired {
            expired_match = true;
            continue;
        }
        if let Some(device) = devices.iter().find(|item| {
            alias
                .device_key
                .as_ref()
                .is_some_and(|key| key == &item.canonical_id)
                || (alias.provider.as_ref() == Some(&item.provider)
                    && alias.provider_id.as_ref() == Some(&item.provider_id))
        }) {
            if !alias_matches.iter().any(|item| item.index == device.index) {
                alias_matches.push(device);
            }
        }
    }
    if !alias_matches.is_empty() {
        return unique_resolution(&alias_matches, 0.99, "confirmed_owner_alias");
    }
    if expired_match && consequential {
        return (
            "low_confidence".to_owned(),
            Vec::new(),
            0.69,
            "expired_alias_requires_confirmation".to_owned(),
            Vec::new(),
        );
    }
    if let Some(spec) = group_spec(&target) {
        let grouped: Vec<&DeviceInput> = devices
            .iter()
            .filter(|item| {
                item.capabilities.contains(&spec.capability)
                    && spec
                        .room
                        .as_ref()
                        .is_none_or(|room| normalize(item.room.as_deref().unwrap_or("")) == *room)
                    && (!spec.online_only || item.online_status == Some(true))
            })
            .collect();
        if !grouped.is_empty() {
            return (
                "group".to_owned(),
                grouped.iter().map(|item| item.index).collect(),
                1.0,
                "capability_group".to_owned(),
                Vec::new(),
            );
        }
    }
    let query_tokens = semantic_tokens(&query);
    let token_matches: Vec<&DeviceInput> = devices
        .iter()
        .filter(|item| {
            !query_tokens.is_empty()
                && query_tokens.is_subset(&semantic_tokens(&item.normalized_name))
        })
        .collect();
    if token_matches.len() > 1 {
        return (
            "ambiguous".to_owned(),
            Vec::new(),
            0.91,
            "shared_display_name_tokens".to_owned(),
            token_matches
                .iter()
                .map(|item| item.display_name.clone())
                .collect(),
        );
    }
    if token_matches.len() == 1 {
        return unique_resolution(&token_matches, 0.93, "unique_display_name_tokens");
    }
    let compact_query = semantic_name(&query).replace(' ', "");
    let mut ranked: Vec<(f64, &DeviceInput)> = devices
        .iter()
        .map(|item| {
            let mut candidates = vec![
                item.normalized_name.clone(),
                semantic_name(&item.normalized_name),
                strip_trailing_number(&item.normalized_name),
            ];
            candidates.extend(
                ascii_words(&item.normalized_name)
                    .into_iter()
                    .filter(|token| token.len() >= 4),
            );
            let score = candidates
                .iter()
                .map(|candidate| sequence_ratio(&compact_query, &candidate.replace(' ', "")))
                .fold(0.0_f64, f64::max);
            (score, item)
        })
        .collect();
    ranked.sort_by(|left, right| {
        right
            .0
            .partial_cmp(&left.0)
            .unwrap_or(Ordering::Equal)
            .then_with(|| left.1.index.cmp(&right.1.index))
    });
    let threshold = if consequential {
        consequential_threshold
    } else {
        read_threshold
    };
    if let Some((top_score, top_device)) = ranked.first().copied() {
        if top_score >= threshold {
            if ranked
                .get(1)
                .is_some_and(|second| top_score - second.0 < ambiguity_gap)
            {
                return (
                    "ambiguous".to_owned(),
                    Vec::new(),
                    top_score,
                    "fuzzy_candidates_too_close".to_owned(),
                    ranked
                        .iter()
                        .take(5)
                        .map(|(_, item)| item.display_name.clone())
                        .collect(),
                );
            }
            return (
                "resolved".to_owned(),
                vec![top_device.index],
                top_score,
                "strong_fuzzy_name".to_owned(),
                Vec::new(),
            );
        }
    }
    const PRONOUNS: &[&str] = &[
        "it",
        "that",
        "that one",
        "this",
        "this one",
        "the device",
        "them",
        "those",
        "these",
        "both",
        "both devices",
        "both of them",
        "all of them",
        "those two",
        "these two",
        "previously referenced devices",
    ];
    if PRONOUNS.contains(&query.as_str()) && !recent_entity_ids.is_empty() {
        let wanted: HashSet<&str> = recent_entity_ids.iter().map(String::as_str).collect();
        let recent: Vec<&DeviceInput> = devices
            .iter()
            .filter(|item| wanted.contains(item.canonical_id.as_str()))
            .collect();
        if !recent.is_empty() {
            let plural = matches!(
                query.as_str(),
                "them"
                    | "those"
                    | "these"
                    | "both"
                    | "both devices"
                    | "both of them"
                    | "all of them"
                    | "those two"
                    | "these two"
                    | "previously referenced devices"
            );
            let selected = if plural { recent } else { recent[..1].to_vec() };
            return (
                "resolved".to_owned(),
                selected.iter().map(|item| item.index).collect(),
                0.98,
                "dialogue_state_reference".to_owned(),
                Vec::new(),
            );
        }
    }
    let top_score = ranked.first().map_or(0.0, |item| item.0);
    (
        if top_score >= 0.6 {
            "low_confidence".to_owned()
        } else {
            "not_found".to_owned()
        },
        Vec::new(),
        top_score,
        "clarification_required".to_owned(),
        ranked
            .iter()
            .take(5)
            .map(|(_, item)| item.display_name.clone())
            .collect(),
    )
}

fn required<'py>(dictionary: &Bound<'py, PyDict>, key: &str) -> PyResult<Bound<'py, PyAny>> {
    dictionary
        .get_item(key)?
        .ok_or_else(|| PyValueError::new_err(format!("missing device field: {key}")))
}

fn optional<'py>(
    dictionary: &Bound<'py, PyDict>,
    key: &str,
) -> PyResult<Option<Bound<'py, PyAny>>> {
    dictionary.get_item(key)
}

fn extract_device(item: Bound<'_, PyAny>) -> PyResult<DeviceInput> {
    let dictionary = item
        .cast_into::<PyDict>()
        .map_err(|_| PyValueError::new_err("device payload must be a dictionary"))?;
    Ok(DeviceInput {
        index: required(&dictionary, "index")?.extract()?,
        canonical_id: required(&dictionary, "canonical_id")?.extract()?,
        provider: required(&dictionary, "provider")?.extract()?,
        provider_id: required(&dictionary, "provider_id")?.extract()?,
        display_name: required(&dictionary, "display_name")?.extract()?,
        normalized_name: required(&dictionary, "normalized_name")?.extract()?,
        room: optional(&dictionary, "room")?
            .filter(|value| !value.is_none())
            .map(|value| value.extract())
            .transpose()?,
        capabilities: required(&dictionary, "capabilities")?
            .extract::<Vec<String>>()?
            .into_iter()
            .collect(),
        online_status: optional(&dictionary, "online_status")?
            .filter(|value| !value.is_none())
            .map(|value| value.extract())
            .transpose()?,
    })
}

fn extract_alias(item: Bound<'_, PyAny>) -> PyResult<AliasInput> {
    let dictionary = item
        .cast_into::<PyDict>()
        .map_err(|_| PyValueError::new_err("alias payload must be a dictionary"))?;
    let extract_optional_string = |key: &str| -> PyResult<Option<String>> {
        optional(&dictionary, key)?
            .filter(|value| !value.is_none())
            .map(|value| value.extract())
            .transpose()
    };
    Ok(AliasInput {
        normalized_alias: required(&dictionary, "normalized_alias")?.extract()?,
        status: required(&dictionary, "status")?.extract()?,
        device_key: extract_optional_string("device_key")?,
        provider: extract_optional_string("provider")?,
        provider_id: extract_optional_string("provider_id")?,
        expired: required(&dictionary, "expired")?.extract()?,
    })
}

fn extract_devices(devices: &Bound<'_, PyAny>) -> PyResult<Vec<DeviceInput>> {
    let mut payload = Vec::new();
    for item in devices.try_iter()? {
        payload.push(extract_device(item?)?);
    }
    Ok(payload)
}

fn extract_aliases(aliases: &Bound<'_, PyAny>) -> PyResult<Vec<AliasInput>> {
    let mut payload = Vec::new();
    for item in aliases.try_iter()? {
        payload.push(extract_alias(item?)?);
    }
    Ok(payload)
}

#[pyclass(skip_from_py_object)]
struct DeviceIndex {
    devices: Arc<Vec<DeviceInput>>,
}

#[pymethods]
impl DeviceIndex {
    #[new]
    fn new(devices: &Bound<'_, PyAny>) -> PyResult<Self> {
        Ok(Self {
            devices: Arc::new(extract_devices(devices)?),
        })
    }

    fn __len__(&self) -> usize {
        self.devices.len()
    }

    #[pyo3(signature = (target, aliases, *, explicit_provider=None, recent_entity_ids=Vec::new(), consequential=true, consequential_threshold=0.90, read_threshold=0.82, ambiguity_gap=0.10))]
    #[allow(clippy::too_many_arguments)]
    fn resolve(
        &self,
        py: Python<'_>,
        target: String,
        aliases: &Bound<'_, PyAny>,
        explicit_provider: Option<String>,
        recent_entity_ids: Vec<String>,
        consequential: bool,
        consequential_threshold: f64,
        read_threshold: f64,
        ambiguity_gap: f64,
    ) -> PyResult<ResolutionTuple> {
        let alias_payload = extract_aliases(aliases)?;
        let devices = Arc::clone(&self.devices);
        Ok(py.detach(move || {
            resolve_core(
                target,
                devices.as_slice(),
                &alias_payload,
                explicit_provider.as_deref(),
                &recent_entity_ids,
                consequential,
                consequential_threshold,
                read_threshold,
                ambiguity_gap,
            )
        }))
    }
}

#[pyfunction(signature = (target, devices, aliases, *, explicit_provider=None, recent_entity_ids=Vec::new(), consequential=true, consequential_threshold=0.90, read_threshold=0.82, ambiguity_gap=0.10))]
#[allow(clippy::too_many_arguments)]
fn resolve_devices(
    py: Python<'_>,
    target: String,
    devices: &Bound<'_, PyAny>,
    aliases: &Bound<'_, PyAny>,
    explicit_provider: Option<String>,
    recent_entity_ids: Vec<String>,
    consequential: bool,
    consequential_threshold: f64,
    read_threshold: f64,
    ambiguity_gap: f64,
) -> PyResult<ResolutionTuple> {
    let device_payload = extract_devices(devices)?;
    let alias_payload = extract_aliases(aliases)?;
    Ok(py.detach(move || {
        resolve_core(
            target,
            &device_payload,
            &alias_payload,
            explicit_provider.as_deref(),
            &recent_entity_ids,
            consequential,
            consequential_threshold,
            read_threshold,
            ambiguity_gap,
        )
    }))
}

#[pyfunction]
fn resolver_version() -> &'static str {
    RESOLVER_VERSION
}

#[pymodule]
fn _curie_device_resolver(module: &Bound<'_, PyModule>) -> PyResult<()> {
    module.add_class::<DeviceIndex>()?;
    module.add_function(wrap_pyfunction!(resolve_devices, module)?)?;
    module.add_function(wrap_pyfunction!(resolver_version, module)?)?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    fn device(index: usize, name: &str, room: Option<&str>) -> DeviceInput {
        DeviceInput {
            index,
            canonical_id: format!("fake:{index}"),
            provider: "fake".to_owned(),
            provider_id: index.to_string(),
            display_name: name.to_owned(),
            normalized_name: normalize(name),
            room: room.map(str::to_owned),
            capabilities: HashSet::from(["light".to_owned(), "controllable".to_owned()]),
            online_status: Some(true),
        }
    }

    #[test]
    fn resolves_generic_and_room_light_groups() {
        let devices = vec![
            device(0, "Floor Lamp 2", Some("living room")),
            device(1, "Kitchen Strip", Some("kitchen")),
        ];
        let all = resolve_core(
            "all of the lights".to_owned(),
            &devices,
            &[],
            None,
            &[],
            true,
            0.85,
            0.82,
            0.1,
        );
        assert_eq!(all.0, "group");
        assert_eq!(all.1, vec![0, 1]);
        let room = resolve_core(
            "lights in the kitchen".to_owned(),
            &devices,
            &[],
            None,
            &[],
            true,
            0.9,
            0.82,
            0.1,
        );
        assert_eq!(room.1, vec![1]);
    }

    #[test]
    fn fuzzy_matching_is_strong_but_never_breaks_close_ties() {
        let one = device(0, "Floor Lamp One", None);
        let two = device(1, "Floor Lamp Two", None);
        let result = resolve_core(
            "flor lamp one".to_owned(),
            &[one.clone(), two.clone()],
            &[],
            None,
            &[],
            true,
            0.85,
            0.82,
            0.1,
        );
        assert_eq!(result.1, vec![0]);
        let ambiguous = resolve_core(
            "floor lamp".to_owned(),
            &[one, two],
            &[],
            None,
            &[],
            true,
            0.9,
            0.82,
            0.1,
        );
        assert_eq!(ambiguous.0, "ambiguous");
    }
}
