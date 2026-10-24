# Coordinated models and evidence handoffs

Curie's Telegram workflow and dashboard conversation adapter share the model conversation service. Tool execution remains owned by the permission-aware tool registry and durable task engine; model specialists receive evidence, not execution authority. The dashboard conversation-only adapter does not acquire device or account permissions through a model upgrade.

## Request flow

1. Normalize the owner request and select relevant, unexpired, owner-scoped dialogue context.
2. Preserve system, user and assistant roles in `ChatPrompt`. Attach application-selected tool receipts separately from conversational text. Retain the previous verified action when a subsequent status read updates the latest observation.
3. Route ordinary conversation to one model. Explicit independent review, a substantive high-stakes judgement, or an explicit request for a deep analytical review selects a specialist followed by a critic. Drafting/editing also receives a targeted factual-details review; a question about a draft does not automatically receive one. The `deep_reasoning` role is reserved for explicit depth, not every analytical follow-up. Other automatic complex-task delegation additionally requires a recorded evaluation gain; keep `LLM_ENSEMBLE_EVAL_GAIN=0` until a controlled comparison justifies changing it.
4. Give both passes the same conversation, constraints and evidence. Treat specialist drafts and dashboard snapshots as untrusted reference data, not system instructions.
5. Check action and authentication claims deterministically. Evidence-bearing answers and selected specialist work also receive a structured critic check. Local reviewers use constrained JSON generation, followed by schema and evidence-ID validation. A failed review produces an honest evidence-based response, not the unverified draft. Explain an action's initiating request, not just its completion receipt, when asked why it happened.
6. Apply output policy and recheck prohibited claims before storing history or replying.

The normal reviewed path has two model-service calls. Provider fallback and existing presentation repair paths can add work; metadata reports service calls, roles and estimated token usage. These are not exact provider-billed tokens, nor a guarantee that a native runtime never retries generation internally. Deterministic checks are conservative guards, not proof of arbitrary natural-language correctness. A critic can also make mistakes: keep held-out conversational evaluation alongside unit tests.

## Shared local inference worker

`llm.local_gateway` owns one process-wide model cache behind an owner-only Unix socket. Telegram, dashboard and background model calls use the socket when `CURIE_INFERENCE_SOCKET` is set. Each client retains its own application queue; the shared worker serializes native inference. Independent tools may still run concurrently through the existing planner. This is selective orchestration, not unrestricted concurrent GPU agents.

The worker validates frame size, roles, model filenames and generation limits. It rejects public sockets, uses a lifetime lock to prevent two workers claiming the endpoint, and evicts before loading another model. It permits at most one GPU-resident model while retaining a CPU reviewer within the cache limit. `LLM_GPU_LAYERS_BY_MODEL` selects tested per-model backend overrides. Clients fail closed when it is unavailable instead of loading duplicate model copies. A requested model is never silently replaced by another model inside this worker.

Cancellation stops queued work and prevents the next coordination stage or reply. It does not interrupt an already-running llama.cpp generation. Timeouts bound transport waiting and prevent starting an overdue review; they are not a hard GPU computation deadline. Replies are released after verification as complete answers, not advertised as token streaming.

### Deployment

1. Download the selected GGUF files into the ignored `models/` directory. Verify public source revisions, byte counts and SHA-256 values against `config/model_catalog.json` for the new candidates. Never commit model weights or `.env`.
2. Set model roles and resource limits in the server's private `.env`. Start with a two-model cache and test the peak memory of the selected pair on the actual host.
3. Set `CURIE_INFERENCE_SOCKET` to an absolute path in a private directory, for example the checkout's `log/inference/worker.sock`.
4. Start `pm2 start deploy/ecosystem.inference.config.cjs`. It forwards only allowlisted model settings and disables automatic dotenv loading in the worker. Do not launch it from a shell containing account credentials.
5. Restart `curie-main` through `ecosystem.config.js` with updated environment, then restart the dashboard adapter. Both must use the same socket. The worker's role configuration is authoritative for local requests.
6. Check socket permissions, `llm.local_gateway.status()`, process memory, dashboard `/health`, an ephemeral `/chat` request, and the sandboxed Telegram workflow. Remove only test sessions afterwards.

Rollback is explicit: restore the previous role settings and restart the worker. To remove the shared worker entirely, clear `CURIE_INFERENCE_SOCKET` in the file and actual PM2 client environments, then restart both clients before stopping it. PM2 can retain omitted environment keys; explicitly setting an empty value with `--update-env` clears this routing setting. Do not leave clients pointed at a stopped worker. Keep previous weights available until rollout evaluation completes.

## Remote models

The Qwen and Z.ai compatible adapters are opt-in. `CURIE_REMOTE_MODELS_ENABLED=false` prevents these new adapters from sending conversation data even when keys are present. To opt in deliberately, set the flag, provide the relevant key privately, and select provider priority or `LLM_REASONING_PROVIDER` / `LLM_CRITIC_PROVIDER`. Role-specific `QWEN_<ROLE>_MODEL` and `ZAI_<ROLE>_MODEL` override provider defaults. Check current model availability, data handling and pricing first. No API keys belong in Git.

Large frontier model families are not automatically downloaded just because an adapter exists. Local model capacity, licence and measured quality remain separate promotion gates.

## Repeatable evaluation

Run `pytest -q` for regression coverage. `evaluation.model_conversation_probe` compares raw GGUF responses on eight synthetic prompts. `evaluation.contextual_assistant_probe` exercises 21 turns through the actual Telegram workflow, replacing only external capability availability and execution with fixtures. It uses temporary memory and stops on failed behavioral gates. `evaluation.coordinated_system_probe` checks real model handoffs, selective call counts, role audit metadata, draft-only boundaries and historical action attribution. `evaluation.dashboard_coordination_probe` exercises six real HTTP exchanges against a loopback test adapter and closes its synthetic session even on failed checks. Each run uses a unique owner identity to avoid cached replies from previous runs.

Store generated JSON reports outside Git or under ignored `evaluation/reports/`. Mechanical checks establish a behavioral floor; manually read replies for invented details, unnecessary verbosity, awkwardness and overconfident hypotheses. Keep production conversations out of benchmark artifacts.

## Selected local profile

| Work | Model | Backend |
| --- | --- | --- |
| Normal chat, coding, fast edits and reviewer | Qwen 3.6 35B-A3B, UD-Q4_K_M | GPU |
| Explicit deep analysis | Qwen 3.8 27B, UD-Q4_K_M | CPU |

The normal reviewer is a separate pass of the same checkpoint, not an independent source of truth. Deep analysis uses the other checkpoint before review. Keeping the deep model CPU-only avoids GPU model churn. GLM 4.7 Flash remains an unpromoted candidate: its GPU trial hung, and CPU review failed schema and attribution gates. Its download is retained for isolated evaluation, not used for production review. Larger remote Qwen/Z.ai adapters remain opt-in and were not exercised with private conversations.

## Rollout evidence on 25 October 2026

- 1,851 regression tests passed, with two opt-in tests deselected and seven passing subtests.
- Five live model-coordination cases passed 24 behavioral checks.
- The 21-turn sandboxed Telegram workflow passed 57 checks. The changed draft/edit flow was rerun separately: four turns, 12 checks.
- Six dashboard HTTP cases passed 19 checks, including the regression for invented traffic/ETA details and explicit session closure.
- Formatting, lint, high-severity Bandit scanning and tracked-file secret scanning passed. The only model-related baseline allowances are public revisions/checksums.
- The production main process, both dashboard adapters and shared inference worker were restarted. Both dashboard health endpoints responded, and an ephemeral production chat returned a model-generated reply and its synthetic session was closed.
- Worker metadata confirmed one GPU-resident Qwen 3.6 model alongside CPU Qwen 3.8 in staging. Synthetic reports are retained locally under ignored `evaluation/reports/coordination-2026-10-25/`; test memory is temporary and staging workers are stopped.

These are behavioral floors, not a 9/10 quality rating. Manual review still observed a missed invented excuse in a Telegram draft, occasional unnecessary apologies, overconfident technical hypotheses, and imperfect sentence-count compliance. The stricter dashboard draft regression passed, but that does not prove every draft is grounded. Keep human approval before sending/publishing generated content and continue held-out evaluation. No real device was switched by these synthetic tests, and Telegram desktop delivery was not manually exercised during this rollout.

The shared status endpoint is serialized with inference and can time out while a long generation is active; query it while idle. Native generation itself is not forcibly interrupted by a client timeout. GitHub CI results must be checked separately from these server-side results.
