# Curie Cache and Index Policy

Every data cache must expose an owner scope, TTL, maximum entry count,
invalidation event, sensitivity classification, and hit-rate metric. Runtime
policy caches expose these fields through `ChatWorkflow.get_cache_stats()` and
the API health response. Cached values and keys are never included in metrics.

| Cache | Owner scope | TTL | Maximum | Invalidation | Sensitivity |
|---|---|---:|---:|---|---|
| Geocoding | Public | 1 hour | 256 | TTL or geocoder configuration change | Public |
| Exchange rates | Public | 1 hour | 32 bases | TTL or provider change | Public |
| Project indexes | Internal user | 5 minutes | 32 | Any path, size, or modification-time change | Personal |
| Personalized prompts | Internal user | 5 minutes | 100 | TTL, persona/profile/history change, workflow reset | Personal |
| Message deduplication | Platform conversation | 10 minutes | 5,000 | TTL, connector identity reset, process restart | Personal |
| Model responses | Internal user | 5 minutes | 100 | TTL, model change, runtime reset | Personal |

Project indexes are keyed by canonical project root and a signature containing
relative paths, file sizes, and nanosecond modification times. Known credential
files are excluded from both signatures and previews. Calls without an internal
owner do not use the project-index cache. Model calls without an explicit owner
scope do not use the response cache.

Loaded LLM and Whisper model objects are bounded process resources, not data
response caches: they hold no user content. They invalidate on model
configuration change or process restart. Peripheral discovery is an explicit
operator-created snapshot rather than an automatic cache; `--fresh` replaces it
after device change. Current weather, RAM/process usage, approval tokens, and
mutable filesystem operation results are not cached.

New caches must use `utils.ttl_cache.TTLCache` unless their storage semantics
require a reviewed specialized implementation. Personal or sensitive entries
are rejected unless the cache is user-scoped.
