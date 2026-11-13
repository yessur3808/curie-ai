# Curie's Letta specialists

Curie can request three bounded local assessments through the capability registry:
`letta_workflow_review`, `letta_engineering_review`, and `letta_operations_review`.
Natural routes include “compare agent tools”, “review this architecture” and
“diagnose service latency”. Each accepts `text` containing the request and relevant
sanitized evidence. They do not retrieve files or current web facts themselves.
Curie can gather evidence using her existing tools, then request a specialist view.
No result can execute a plan, restart a service, send a message or grant permission.

Training means versioned curricula under `skills/curie-letta-*/SKILL.md` plus explicit
owner preferences, not model weight training or unsupervised transcript ingestion.
The curricula are mirrored to each dedicated agent's private MemFS for inspection;
runs explicitly inject the current repo curriculum because stateless mode disables
MemFS sync. Input evidence is not automatically saved as learning. Update curricula
only from reviewed feedback and verified outcomes, then rerun behavioral tests.

Local setup uses named, distinct Letta agents and a private config at
`~/.config/curie-letta/specialists.json` (override with
`CURIE_LETTA_SPECIALISTS_CONFIG`). Schema: `enabled`, `owner` (internal ID), and
`agents` mapping workflow/engineering/operations to local agent IDs. Other owners
are rejected before inference. Config contains no provider credentials.

The existing authenticated localhost scout bridge now advertises `curie-specialist`.
Its fast-role requests reuse Curie's Unix-socket inference worker, capped at256
output tokens; the scout remains fast-role128. Letta agents use model handle
`openai-compatible/curie-specialist`, toolset none, reflection off, no mods/skills,
and a new stateless conversation for each assessment. Agents are logical identities,
not additional resident model workers. Model choice follows Curie's fast role; the initial general-role smoke test failed near the inference deadline. Complex reviews should request more evidence rather than claim deep verification.

One specialist runs at a time across processes, with a nonblocking lock. Governor
limits:4calls/hour,360seconds/hour,8192reserved output tokens/day. Severe recent
runtime pressure defers work. Output ceilings reserve budget even on failure;
input-token usage is not measured. A90second subprocess deadline,55second bridge
inference timeout, bounded output and process-group cleanup prevent hanging CLI
children. Ordinary chat does not automatically invoke a reviewer on every turn.

Failures expose status codes only: not_configured,owner_not_enabled,invalid_request,
sensitive_input,resource_deferred,busy,launch_failed,process_failed,agent_error,
timeout,invalid_output. No raw stderr/prompts are logged. The scout shares this runner
and now distinguishes failures in its durable reviewer status, including
invalid_selection. This does not retroactively diagnose older generic fallbacks.

Validate with `tests/test_letta_specialists.py`, `tests/test_ai_scout_bridge.py`,
`tests/test_ai_scout.py` and registry/normal-routing tests. Smoke-test all three
roles on synthetic tasks, with no outbound user messages or production mutations.
Enable/reload only Curie's main service and the existing model bridge; preserve
VPN/Meshnet, open X browser and unrelated inference services.
