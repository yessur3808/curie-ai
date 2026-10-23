# Coordinated models and evidence handoffs

Curie's Telegram workflow and dashboard conversation adapter share the model conversation service. Tool execution remains owned by the permission-aware tool registry and durable task engine; model specialists receive evidence, not execution authority. The dashboard conversation-only adapter does not acquire device or account permissions through a model upgrade.

## Request flow

1. Normalize the owner request and select relevant, unexpired, owner-scoped dialogue context.
2. Preserve system, user and assistant roles in `ChatPrompt`. Attach application-selected tool receipts separately from conversational text. Retain the previous verified action when a subsequent status read updates the latest observation.
3. Route ordinary conversation to one model. Explicit independent review, a substantive high-stakes judgement, or an explicit request for a deep analytical review selects a specialist followed by a critic. The `deep_reasoning` role is reserved for explicit depth, not every analytical follow-up. Other automatic complex-task delegation additionally requires a recorded evaluation gain; keep `LLM_ENSEMBLE_EVAL_GAIN=0` until a controlled comparison justifies changing it.
4. Give both passes the same conversation, constraints and evidence. Treat specialist drafts and dashboard snapshots as untrusted reference data, not system instructions.
5. Check action and authentication claims deterministically. Evidence-bearing answers and selected specialist work also receive a structured critic check. Reject malformed reviews and invented evidence IDs. A failed review produces an honest evidence-based response, not the unverified draft.
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

Rollback is explicit: restore the previous role settings and restart the worker. To remove the shared worker entirely, remove `CURIE_INFERENCE_SOCKET` from both client environments and restart both clients before stopping it. Do not leave clients pointed at a stopped worker. Keep previous weights available until rollout evaluation completes.

## Remote models

The Qwen and Z.ai compatible adapters are opt-in. `CURIE_REMOTE_MODELS_ENABLED=false` prevents these new adapters from sending conversation data even when keys are present. To opt in deliberately, set the flag, provide the relevant key privately, and select provider priority or `LLM_REASONING_PROVIDER` / `LLM_CRITIC_PROVIDER`. Role-specific `QWEN_<ROLE>_MODEL` and `ZAI_<ROLE>_MODEL` override provider defaults. Check current model availability, data handling and pricing first. No API keys belong in Git.

Large frontier model families are not automatically downloaded just because an adapter exists. Local model capacity, licence and measured quality remain separate promotion gates.

## Repeatable evaluation

Run `pytest -q` for regression coverage. `evaluation.model_conversation_probe` compares raw GGUF responses on eight synthetic prompts. `evaluation.contextual_assistant_probe` exercises 21 turns through the actual Telegram workflow, replacing only external capability availability and execution with fixtures. It uses temporary memory and stops on failed behavioral gates. `evaluation.coordinated_system_probe` checks real model handoffs, selective call counts, role audit metadata, draft-only boundaries and historical action attribution.

Store generated JSON reports outside Git or under ignored `evaluation/reports/`. Mechanical checks establish a behavioral floor; manually read replies for invented details, unnecessary verbosity, awkwardness and overconfident hypotheses. Keep production conversations out of benchmark artifacts.

## Rollout status on 24 October 2026

The server regression suite passed 1,838 tests before the additional GPU-residency guard. The final focused coordinator suite, including that guard and socket permissions, passed 35 tests locally. The Qwen specialist/reviewer probe passed all four original scenarios, and the sandboxed 21-turn Telegram workflow passed its behavioral checks. Formatting, lint and secret scanning passed; the only newly allowlisted strings are public model repository revisions and file checksums.

Both new downloads passed their pinned SHA-256 checks. Raw eight-prompt trials completed for Qwen 3.8 on CPU/GPU, Qwen 3.6 on GPU, and GLM 4.7 Flash on CPU. GLM was fast on CPU but its subsequent GPU review trial stalled the native process with SIGKILL pending. This is a server-recovery blocker, not a passed model promotion gate. The default example therefore keeps GLM on CPU and the worker prevents simultaneous GPU model residency.

Production source/configuration has not been changed. Final Qwen-to-GLM coordination, dashboard HTTP verification, restart and rollout remain pending server recovery. Do not treat the successful standalone benchmarks or earlier same-family handoff as proof that the final cross-family deployment has passed.
