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
