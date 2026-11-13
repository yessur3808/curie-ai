# Curie situational capabilities

This layer gives Curie live, explicitly sourced awareness without turning model
guesses into facts or permissions.

## Google Calendar

Use the existing owner-only OAuth endpoint with `product=calendar`. Read-only
authorization is the minimum; scheduling needs `write=true`. Calendar reads are
cached into Curie's owner-scoped agenda so the already opt-in proactive service
can make timely agenda suggestions. Event creation is an external write and
always goes through the normal preview and fresh-approval flow.

Examples:

- `What's on my calendar tomorrow?`
- `/calendar list week`
- `/calendar create Dentist | 2026-10-01T09:00:00+08:00 | 2026-10-01T10:00:00+08:00 | Asia/Hong_Kong | Central | Check-up`

## Google Maps and Places

Map search and directions links work without a key. `GOOGLE_MAPS_API_KEY`
enables Places API (New) results such as addresses, ratings, current opening
state, phone numbers, websites, and coordinates. Place data is treated as
untrusted external data and field masks keep cost and disclosure bounded.

Examples:

- `Find a coffee shop near Central`
- `Show doctors near me`
- `/maps post offices in Wan Chai`

## Flights

`AVIATIONSTACK_API_KEY` enables commercial schedule, status, airport, terminal,
gate, delay, and aircraft data. `OPENSKY_LIVE_TRACKING=true` adds current
transponder position, altitude, speed, heading, and a map link. OpenSky callsigns
do not always match passenger flight numbers, so a missing live position is
reported as uncertainty rather than a cancelled flight.

Examples:

- `Track CX101`
- `Where is BA 28 right now?`
- `/flight CX101 2026-10-01`

## Personality-specific trained voices

Each personality JSON assigns a `voice.trained_id`. Curie keeps the provisioned
`data/trained-voice` model. Other identities use
`data/trained-voices/<trained_id>` or an environment variable such as
`CURIE_VOICE_HOME_JARVIS`. A missing assigned model falls back to text when
trained voice is required; Curie never silently speaks in another personality's
voice.

Telegram sends voice when voice replies are enabled, when the owner explicitly
asks for a voice/audio reply, or when replying to an inbound voice note.

## Awareness, dashboard, and growth

An exhaustive compact index from the live capability registry is injected at every
conversational model invocation. It names every registered chat tool and runtime
component, including unavailable integrations, and includes a registry fingerprint.
For the capabilities relevant to the current request it also supplies availability,
scope, risk, approval policy, required permissions, accepted input names, and the
live description. There is no fixed-size name subset or manually maintained tool
list. Registry status and actual execution evidence remain authoritative, so model
awareness cannot grant permission or fabricate a completed action.

Political, social, legal, scientific, market, and news questions with current
wording route to live research. `dashboard_snapshot` reads the same bounded
instance, connector, task, model, resource, error, and readiness data used by
Curie's operations dashboard.

`observatory_project_analytics` separately reads bounded aggregate GA4 reports
from Curie Observatory's local cache. It exposes project name, reporting range,
views, users, sessions, previous-period totals, source status, and update time;
it never reads the dashboard access key, Google credentials, or detailed page
breakdowns. The default data directory is
`~/Desktop/server/dashboard/data`; override it with
`CURIE_OBSERVATORY_DATA_DIR` when Observatory is installed elsewhere.

Examples:

- `Did my Atmosphere website get any views lately?`
- `Show Personal website analytics for the last 7 days`
- `/analytics Atmosphere 30`

Learning stays controlled: explicit corrections, verified tool outcomes,
owner-approved learned abilities, and measured evaluations may improve future
behavior. Learning cannot expand permissions, change safety policy, or promote
an unsupported model inference to a fact.

Proactive behavior remains opt-in and rate-limited. Calendar is refreshed before
candidate ranking, and external writes still require approval.
