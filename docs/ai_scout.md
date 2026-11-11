# Curie + Letta AI scout

The shared scout checks models/LLMs, tools, agents, harnesses, local inference and
research. Curie delivers the digest; the existing Letta Twitter Scout ranks bounded
source packets. This is skill, workflow and explicit-preference training, not a
model-weight fine-tune. See `skills/ai-twitter-scout/SKILL.md` and its evaluation cases.

## Cadence and efficiency

The existing Rust task scheduler checks due work. Frequent mode uses 08:00, 13:00,
16:00 and 22:00 on weekdays; weekends use 11:00, 15:00, 19:00 and 23:00.
All times follow the owner profile timezone. Daily mode uses 19:30 every day.
Scout updates stay quiet from midnight until 08:00 on weekdays and 11:00 on weekends.
Quiet hours are checked at delivery time, including research that crosses midnight. Missing a slot
by over an hour skips it instead of producing catch-up messages. The evening slot
can share saved findings that have not already been reported. The scout has a one-hour topic cooldown; snooze, explicit feedback backoff,
channel preferences and global proactive budgets still apply. Duplicate or
ambiguous sends are not repeated.

There is one browser collector and one shared history for both agents. No new
Letta inference runs occur without new candidates. At most three updates are sent
per digest. Letta reviews have a small context/output budget and a deadline; invalid
IDs or failures fall back to deterministic ranking. Recent resource pressure defers
browser/model work while lightweight public feeds continue. Pressure uses only the
last 15 minutes of diagnostics, so an old slow turn cannot pause research forever.
The governor reserves the 128-token output ceiling per model attempt; the private
gateway does not yet expose measured input-token usage. Its private local model adapter
uses Curie's existing Unix-socket model worker and fast model, not a second model
cache. The adapter binds only to 127.0.0.1, requires a generated private bearer key,
and rejects tools, other model names and oversized requests.

## Sources and evidence

X uses only the authorized persistent server profile, with no paid X API. HTTP
403/429 and login challenges pause browser reads until a new session is enrolled.
X posts remain explicitly labeled unverified leads. At most one such lead is
included per digest. Ten project release feeds plus OpenAI and Hugging Face feeds
provide separately labeled coverage when X is unavailable. Feeds use conditional
requests, bounded downloads, hardened XML parsing and a three-request concurrency
limit. Project links must match their configured repository. Feed failures are
reported as coverage gaps; publisher claims are not independent benchmark evidence.

Findings carry source URLs, publication/observation/expiry times, evidence labels
and heuristic confidence. Reposts, exact story repeats, future timestamps and
items older than 48 hours are filtered. Preference weights are bounded and change
only through explicit more/less/reset feedback. Topics are models, tools, agents,
harnesses, local-ai and research.

## Controls

- `/aiscout status`, `/aiscout latest`
- `/aiscout daily`, `/aiscout frequent`
- `/aiscout more harnesses`, `/aiscout less local-ai`, `/aiscout reset agents`
- `/aiscout pause`, `/aiscout resume`

Enable `ai_scout_enabled=true` and set `ai_scout_cadence=frequent` or `daily` in the
owner profile. Existing X account/risk opt-ins are still required for browser reads,
but public source coverage does not require a working X session. Configure the
private `CURIE_AI_SCOUT_LETTA_LAUNCHER` to the dedicated local scout launcher. With
no launcher, ranking is deterministic and status says so. Do not export login
profiles, credentials, synthetic evaluation packets or generated model keys.

`scripts.ai_scout_run --owner OWNER` checks an actual due slot using the same durable
receipts; it is a diagnostic/one-shot entry point, not a second required schedule.
It loads the installation environment followed by `instances/INSTANCE.env`, just
like PM2. Profile changes and scheduler checks must use that same live instance
database, not a leftover installation-wide SQLite file.

## Validation

Run `pytest -q tests/test_ai_scout.py tests/test_ai_scout_bridge.py
 tests/test_x_browser_profile.py tests/test_x_browser_research.py`. Tests cover
cadence, source identity, freshness, explicit preferences, owner isolation, prompt
injection boundaries, invented model selections, blocked X fallback and duplicate
or ambiguous delivery. Live source and model checks are separate: passing fixture
tests does not establish that X accepts automated access.

The portable skill layout follows [Letta's skill format](https://docs.letta.com/configuration/skills).

## Dependency audit scope

The November 2026 rollout updates PyMongo to 4.18.3, rusqlite to the 0.41
series (libsqlite3-sys 0.39.1), and rustix to 1.1.6. The SQLite kernels retain
shared-library linking: Python and both Rust extensions must use the same SQLite
library in a process for safe WAL locking. Do not enable bundled SQLite on just
one extension. Cargo's lockfile audit does not certify the operating system's
SQLite security backports; that shared library must be maintained separately.
See [RustSec's SQLite advisory](https://github.com/RustSec/advisory-db/blob/main/crates/libsqlite3-sys/RUSTSEC-2026-0459.md)
and [rustix advisory](https://github.com/RustSec/advisory-db/blob/main/crates/rustix/RUSTSEC-2026-0486.md).
