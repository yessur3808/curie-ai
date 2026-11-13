# Coordinated models and evidence handoffs

Curie's Telegram workflow and dashboard conversation adapter share the model conversation service. Tool execution remains owned by the permission-aware tool registry and durable task engine; model specialists receive evidence, not execution authority. The dashboard conversation-only adapter does not acquire device or account permissions through a model upgrade.

## Request flow

1. Normalize the owner request and select relevant, unexpired, owner-scoped dialogue context.
2. Preserve system, user and assistant roles in `ChatPrompt`. Attach application-selected tool receipts separately from conversational text. Retain the previous verified action when a subsequent status read updates the latest observation.
3. Route ordinary conversation to one model. Explicit independent review, a substantive high-stakes judgement, or an explicit request for a deep analytical review selects a specialist followed by a critic. Drafting/editing also receives a targeted factual-details review; a question about a draft does not automatically receive one. The `deep_reasoning` role is reserved for explicit depth, not every analytical follow-up. Other automatic complex-task delegation additionally requires a recorded evaluation gain; keep `LLM_ENSEMBLE_EVAL_GAIN=0` until a controlled comparison justifies changing it.
4. Give the solver the complete relevant conversation and capability context. Give the critic a compact immutable review packet containing only the newest request, draft, recent owner messages, and authoritative evidence. Solver-only persona, capability, and answer-style instructions are deliberately excluded from the critic packet. Treat drafts and dashboard snapshots as untrusted data, not system instructions.
5. Check action and authentication claims deterministically. Evidence-bearing answers and selected specialist work also receive a structured critic check. Local reviewers use constrained JSON generation, followed by schema and evidence-ID validation. Passing reviews return only a minimal verdict so the CPU critic does not regenerate the answer; corrected reviews return the complete corrected answer. A failed review produces an honest evidence-based response, not the unverified draft. Explain an action's initiating request, not just its completion receipt, when asked why it happened.
6. Apply output policy and recheck prohibited claims before storing history or replying.

The normal reviewed path has two model-service calls. Provider fallback and existing presentation repair paths can add work; metadata reports service calls, roles, per-stage latency/approximate output throughput, and estimated token usage. These are not exact provider-billed tokens, nor a guarantee that a native runtime never retries generation internally. Deterministic checks are conservative guards, not proof of arbitrary natural-language correctness. A critic can also make mistakes: keep held-out conversational evaluation alongside unit tests.

## Shared local inference worker

`llm.local_gateway` owns one process-wide model cache behind an owner-only Unix socket. Telegram, dashboard and background model calls use the socket when `CURIE_INFERENCE_SOCKET` is set. Each client retains its own application queue; the shared worker serializes native inference. Independent tools may still run concurrently through the existing planner. This is selective orchestration, not unrestricted concurrent GPU agents.

The worker validates frame size, roles, model filenames and generation limits. It rejects public sockets, uses a lifetime lock to prevent two workers claiming the endpoint, and evicts before loading another model. It permits at most one GPU-resident model while retaining a CPU reviewer within both the model-count limit and the optional `LLM_MAX_RESIDENT_BYTES` checkpoint-byte ceiling. The byte ceiling prevents a larger deep-analysis checkpoint from satisfying the count limit while exhausting host memory. `LLM_GPU_LAYERS_BY_MODEL` selects tested per-model backend overrides. Clients fail closed when it is unavailable instead of loading duplicate model copies. A requested model is never silently replaced by another model inside this worker.

Cancellation stops queued work and prevents the next coordination stage or reply. It does not interrupt an already-running llama.cpp generation. Timeouts bound transport waiting and prevent starting an overdue review; they are not a hard GPU computation deadline. Replies are released after verification as complete answers, not advertised as token streaming.

### Deployment

1. Download the selected GGUF files into the ignored `models/` directory. Verify public source revisions, byte counts and SHA-256 values against `config/model_catalog.json` for the new candidates. Never commit model weights or `.env`.
2. Set model roles and resource limits in the server's private `.env`. The selected profile uses a measured three-model warm set: routine CPU model, CPU critic, and GPU specialist. Test peak memory on the actual host before increasing it.
3. Set `CURIE_INFERENCE_SOCKET` to an absolute path in a private directory, for example the checkout's `log/inference/worker.sock`.
4. Run `scripts/reload_curie_pm2.sh`. It rebuilds the inference worker, Telegram process, and both dashboard adapters from their current ecosystem files, forwards only allowlisted model settings to the worker, and saves the verified process snapshot used by cold-boot recovery. Do not launch the inference worker from a shell containing account credentials.
5. Confirm the worker, `curie-main`, and both dashboard adapters report the same socket and intended role mapping in `pm2 env`. The worker's role configuration is authoritative for local requests; a stale PM2 snapshot must not silently replace the fast or critic model after reboot.
6. Check socket permissions, `llm.local_gateway.status()`, process memory, dashboard `/health`, an ephemeral `/chat` request, and the sandboxed Telegram workflow. Remove only test sessions afterwards.

Rollback is explicit: restore the previous role settings and restart the worker. To remove the shared worker entirely, clear `CURIE_INFERENCE_SOCKET` in the file and actual PM2 client environments, then restart both clients before stopping it. PM2 can retain omitted environment keys; explicitly setting an empty value with `--update-env` clears this routing setting. Do not leave clients pointed at a stopped worker. Keep previous weights available until rollout evaluation completes.

## Remote models

The Qwen and Z.ai compatible adapters are opt-in. `CURIE_REMOTE_MODELS_ENABLED=false` prevents these new adapters from sending conversation data even when keys are present. To opt in deliberately, set the flag, provide the relevant key privately, and select provider priority or `LLM_REASONING_PROVIDER` / `LLM_CRITIC_PROVIDER`. Role-specific `QWEN_<ROLE>_MODEL` and `ZAI_<ROLE>_MODEL` override provider defaults. Check current model availability, data handling and pricing first. No API keys belong in Git.

Large frontier model families are not automatically downloaded just because an adapter exists. Local model capacity, licence and measured quality remain separate promotion gates.

## Repeatable evaluation

Run `pytest -q` for regression coverage. `evaluation.model_conversation_probe` compares raw GGUF responses on eight synthetic prompts. `evaluation.contextual_assistant_probe` exercises 21 turns through the actual Telegram workflow, replacing only external capability availability and execution with fixtures. `evaluation.natural_capability_probe` uses the same sandboxed boundary to verify natural, command-free requests across calendar, weather, analytics, health, places, flights, research, email, smart-home follow-ups, and live capability discovery. Both probes use temporary memory and stop on failed behavioral gates. `evaluation.coordinated_system_probe` checks real model handoffs, selective call counts, role audit metadata, draft-only boundaries and historical action attribution. `evaluation.dashboard_coordination_probe` exercises six real HTTP exchanges against a loopback test adapter and closes its synthetic session even on failed checks. Each run uses a unique owner identity to avoid cached replies from previous runs.

Store generated JSON reports outside Git or under ignored `evaluation/reports/`. Mechanical checks establish a behavioral floor; manually read replies for invented details, unnecessary verbosity, awkwardness and overconfident hypotheses. Keep production conversations out of benchmark artifacts.

## Selected local profile

| Work | Model | Backend |
| --- | --- | --- |
| Lightweight choices, summaries and fast edits | Qwen 3 4B, Q4_K_M | CPU (8 threads) |
| Normal chat, technical reasoning and coding | Qwen 3.6 35B-A3B, UD-Q4_K_M | GPU |
| Independent critic | Qwen 3 30B-A3B Instruct 2507, Q4_K_M | CPU |
| Explicit deep analysis | Qwen 3.8 27B, UD-Q4_K_M | CPU |
| Lightweight greetings and explicit NPU work | Qwen 3.5 4B | NPU |

Lightweight choices and fast transformations use the small CPU checkpoint, measured at roughly 19–30 output tokens/second on warm isolated prompts. General conversation, drafting, technical reasoning and coding stay on the 35B GPU specialist because the 4B model failed the live named-recipient drafting gate. Explicit review preserves the selected solver tier and then uses the distinct 30B CPU critic. The three selected checkpoints can remain resident together; only one model is GPU-resident. Local verifier probes passed strict JSON, arithmetic correction, evidence attribution and invented-detail removal; Qwen3.5 9B was rejected because it preserved an invented excuse and ETA. Deep analysis remains CPU-only and can replace one cached CPU model only for explicit depth. GLM 4.7 Flash remains an unpromoted candidate: its GPU trial hung, and CPU review failed schema and attribution gates. Its download is retained for isolated evaluation, not used for production review. Larger remote Qwen/Z.ai adapters remain opt-in and were not exercised with private conversations.

## Throughput refinement on 30 October 2026

The reviewed path now recognizes explicit concise requests with a 192-token ceiling, instructs the solver to avoid orchestration disclaimers, sends the critic a compact purpose-built packet, and uses a minimal pass verdict rather than repeating the accepted answer. High-confidence unsupported draft details such as an invented traffic cause or ETA are identified for mandatory reviewer correction; the application still refuses a pass that ignores those corrections.

The production dashboard/voice and Telegram prompt builders now use the compact canonical persona contract and compact turn-specific personality directives instead of repeating the full persona, style JSON, response planner, and scope policies. The dashboard's always-on persona/scope/time layer fell from 14,151 to 2,782 characters while retaining Curie's identity, professional clean formatting, execution and evidence boundaries, and current-time awareness. Relevant tool details remain query-selected, while every registered capability name stays in the always-on live index.

On the same warm two-model queue-policy probe, latency fell from 33.416 seconds to 8.652 seconds and effective final-answer throughput rose from 1.83 to 8.15 tokens/second. That is about 74% lower latency and 4.45 times the effective throughput for this controlled reviewed case. The final run remained verified; separate live probes passed evidence-ID attribution, invented traffic/ETA removal, and arithmetic correction. After deployment, the real dashboard path for the same class of request fell from 16.519 to 9.748 seconds and estimated token work fell from 4,627 to 2,250. Its GPU solver stage produced 72 approximate output tokens at 9.93 tokens/second, followed by a 2.26-second independent CPU verdict. Ordinary single-model answers avoid the critic entirely and therefore have a shorter critical path.

## Accelerator ceiling and tiered fast lane on 31 October 2026

The production host cannot honestly deliver 200–400 generated tokens/second with Curie's current assistant-quality checkpoints. Measured warm output rates were about 19–30 tokens/second for Qwen 3 4B on CPU/GPU, 11–12 tokens/second for the 4B NPU runtime, about 37 tokens/second for the local Qwen 2.5 VL 3B checkpoint, and 11–13 tokens/second for the 35B GPU specialist on production-shaped reviewed prompts. The 3B checkpoint was rejected despite its higher rate: it refused a requested edit, mishandled an overheating safety priority, and recommended a database replacement without diagnosis. A sub-1B checkpoint is the only plausible software-only route toward triple-digit decoding on this host, but shell network access could not retrieve the candidate during this evaluation and it must not be promoted without the same conversation-quality gates.

The validated profile therefore keeps normal conversation, drafting, reasoning and coding on the 35B GPU model; routes bounded lightweight choices, summaries and transformations to the 4B CPU model; and keeps the 30B critic on CPU. All three remain resident in the shared cache. The reviewer now treats an explicitly named draft recipient omitted by the solver as a mandatory correction, and verifies that a corrected answer restored the name. Explicit review preserves the selected solver role instead of silently upgrading every routine review.

Final evaluation on this profile: the offline flow gate passed 25/25 checks, the sandboxed Telegram workflow passed 57/57 checks across 21 turns, the five-case coordination probe passed, the six-case dashboard HTTP probe passed, and the full suite passed 1,984 tests plus seven subtests (one skipped, two deselected). The five-case coordination probe measured the warm 4B casual lane at 1.15 seconds with a compact prompt, while the full Telegram fast turn took 10.69 seconds because its production capability/persona/history context was much larger. Prompt ingestion and sequential verification are now the dominant latency costs; generation-rate claims must not mix prompt-processing throughput, aggregate concurrent throughput, or deterministic tool responses with generated output tokens/second.

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

### Optional local alternate

`LLM_ALTERNATE_ENABLED=true` enables a single alternate attempt for a direct
conversation response that is empty, reports a generation error, or begins with
a short first-person refusal. `LLM_ALTERNATE_MODEL` selects the installed Ollama
model (default `huihui_ai/qwen3.6-abliterated:35b`). This is not an automatic
quality judge: a confidently incorrect answer does not trigger it. Already
verified/coordinated answers and application-level permission, evidence, and
resource-governor blocks are not escalated.

The existing private worker accepts role `alternate`, closes its GGUF cache,
and calls only `http://127.0.0.1:11434`, without proxy inheritance or redirects.
The original structured conversation is preserved. Output and context are
bounded (8K context), no tools are supplied, and Ollama residency is released
afterward. The candidate still goes through ordinary answer verification.
Failures retain the original response. Metadata records `alternate_reason`,
`alternate_status`, and model `ollama:alternate` for successful replacements.
This trades cold-load latency for avoiding a second resident 35B model; unrelated
Ollama clients are outside Curie's memory coordination.

CyberStrike remains on hold pending investigation; it is not part of this path.
