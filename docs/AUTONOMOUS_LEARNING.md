# Curie autonomous research

Curie's autonomous research service is a bounded public-information system. It
does not rewrite Curie's code, alter model weights, or copy web claims into the
owner's personal memory. Findings live in a separate owner-scoped journal.

## Default schedule

- Relevant-area and global news: 08:30 and 18:30 in the owner's timezone.
- Relevant-topic rotation: one topic every 24 hours.
- Stale refresh: expired findings are rechecked before new topic work.
- Sharing: at most one qualifying discovery per day, subject to Curie's normal
  proactive-message quiet hours, daily/weekly caps, and topic cooldowns.

The proactive service only starts due research. Network work runs on a separate
daemon worker, so slow sources never block Telegram messages or reminders.

## Evidence policy

Every journal entry records:

- topic, query, and concise synthesis;
- source URL, publisher/domain, fetch time, and evidence excerpt where available;
- independent reliable-source count;
- confidence, agreement/conflict note, novelty, and usefulness;
- observation, verification, expiration, refresh, share, and supersession times;
- status and a content fingerprint used for deduplication.

News expires after 36 hours. Topic research expires after 14 days. Expired
entries are refreshed and superseded instead of silently treated as current.
Single-source or low-confidence entries may be retained for auditability, but
they cannot become proactive messages. Sharing requires at least two independent
reliable sources, fresh evidence, sufficient novelty, and the owner's configured
confidence threshold.

News discovery uses a bounded public RSS endpoint. Similar headlines are
clustered before comparison. The default synthesis is deterministic and cited,
so autonomous work does not compete with latency-sensitive chat-model inference.

## Owner controls

Use these in chat:

- `/autolearn status`
- `/autolearn enable`
- `/autolearn disable`
- `/autolearn refresh`
- `/autolearn journal`
- `/autolearn share on`
- `/autolearn share off`
- `/autolearn topic add <topic>`
- `/autolearn topic remove <topic>`

Disabling research preserves the existing journal. Disabling sharing allows
research and stale refresh to continue without unsolicited discovery messages.

## Profile settings

The owner profile stores the opt-in and schedule:

- `autonomous_learning_enabled`
- `autonomous_news_enabled`
- `autonomous_topic_research_enabled`
- `autonomous_learning_share_enabled`
- `autonomous_news_times`
- `autonomous_research_interval_hours`
- `autonomous_research_topics`
- `autonomous_learning_min_confidence`
- `autonomous_learning_daily_share_max`

The service derives optional research scope only from explicit research topics,
interests, project names, location/region/country, and an upcoming trip
destination. Credential-like topics and proactive excluded topics are rejected.

## Persistence kinds

The SQLite `personal_items` store uses:

- `autonomous_research_finding`
- `autonomous_research_state`

All records are scoped to the owner's internal identity by the persistence
kernel. No credentials or private message bodies are placed in source records.

## Current awareness and requested follow-ups

The following features are separate from the twice-daily learning journal and
its unsolicited sharing policy. Enabling them does not increase companion-message
frequency or enable public posting.

### Current time, weather, and news

- Every normal chat prompt receives fresh application time in the owner's
  timezone, outside the prompt cache. Dashboard/voice prompts use the operator's
  default timezone. The application clock is authoritative over old conversation.
- Proactive topic selection uses timestamped history from the past 48 hours;
  cached companion drafts cannot cross local midnight. Past trip date ranges are
  not silently moved to next year or presented as future preparation.
- `hourly_awareness_enabled: true` enables a separate background loop. It fetches
  home-region and global news plus current weather, without generating an LLM
  summary or sending an hourly message. Unchanged successful snapshots are reused
  for one hour; degraded refreshes retry after ten minutes.
- Location comes from the owner's configured location, not a remembered holiday.
  Timezone and location must be accurate; Curie does not infer live GPS location.
- Observations retain fetch/publication times and source links. News must be at
  most 24 hours old; weather observations must be at most two hours old. Expired
  snapshots, location changes, and a new local date invalidate the context.
- RSS headlines are explicitly labeled headline-only evidence. They are not
  represented as verified article analysis or independently corroborated facts.

### Research requested during a private chat

`autonomous_followup_enabled: true` enables source-linked follow-ups on supported
private push channels. Explicit detailed research requests are queued; simple
lookups still run immediately. An explicitly uncertain answer to a self-contained
public factual question can also queue research. This is deliberately conservative:
ambiguous personal references, credentials, private diagnostics, and action
requests are not automatically exported into a public search.

The system persists an owner-scoped `research_followup` record and a Rust scheduled
task before acknowledging it. It requires the Rust task engine, a running delivery
worker, an authenticated private-chat origin, and a real message ID. Budgets are
30 minutes for ordinary research, 60 for detailed comparisons/analysis, and 120
for comprehensive reports. Work starts immediately and can finish early; these
are deadlines, not artificial delays. At most three jobs per owner are pending.

The worker retries missing evidence within the deadline, then reports honestly
if no defensible answer is available. Time targets depend on Curie, its network,
source providers, and the connector staying available. This is bounded web
research, not a guarantee of exhaustive analysis or two hours of continuous LLM
reasoning. Public search discovery uses actual search results, never LLM-invented
URLs. Provider rate limits can prevent completion; the system does not bypass
access restrictions or silently substitute model guesses for sources.

- `/research status` lists saved follow-ups and their states.
- `/research cancel ID` cancels one of the owner's jobs.
- Reports retain source URLs; single-source results are marked provisional.
- Rust leases prevent simultaneous claims; persistent delivery receipts prevent
  automatic replay after a confirmed or ambiguous send. An ambiguous transport
  result is marked `delivery_uncertain`, not blindly retried.
- Requested follow-ups are direct responses to a request, not unsolicited
  discoveries, so they are not held behind the companion-message daily quota.
- `current_awareness` and `research_followup` items stay in the active instance's
  owner-scoped store. Do not migrate an unrelated old database or overwrite a
  profile merely to enable these two flags.

Both background loops start with the proactive service and require
`ENABLE_PROACTIVE_MESSAGING=true` and an outbound connector. Turning either owner
flag off disables that owner's corresponding work. The dashboard chat-only route
receives current time but does not acquire Telegram tools or private delivery
rights through these changes.

### Regression and live checks

`tests/test_current_awareness.py`, `tests/test_research_followup.py`, and
`tests/test_awareness_native.py` cover time boundaries, stale observations, owner
isolation, queue-before-promise, deadlines, cancellation, Rust lease recovery, and
delivery replay. `tests/test_planning_execution.py` also verifies that queued work
does not unlock dependent steps or count as completed.

`python -m evaluation.temporal_awareness_probe` is the reusable six-turn live-model
probe: past-trip correction, today's date, home weather, current news, detailed
research, and status. It requires an isolated `/tmp/curie-awareness.*` database,
disables learning for its synthetic exchanges, and replaces the final research
and delivery steps with labeled fixtures. Weather and news calls are live. It
does not send test messages to Telegram or write into the owner's memory.
