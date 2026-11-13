# Local model availability failover

Gateway callers use `llm.gateway_failover.request_with_failover`. If a primary
generation disconnects, returns an error marker, or returns no text, retry the
same prompt using `LLM_FAST_MODEL` (default `Qwen3-4B-Q4_K_M.gguf`). The current
deployment runs that model on CPU through `LLM_GPU_LAYERS_BY_MODEL`. Model files
and CPU mapping must be maintained when changing the fast model.

The original structured messages, evidence, JSON schema, owner scope,
temperature, and token budget are retained. This is generation recovery, not
tool retry or authorization bypass. Successful answers, refusals, and tool
permission messages do not trigger this recovery. Socket permission failures
are not retried. Existing alternate-model behavior is separate and unchanged.

After failure, the calling process avoids that primary model for five minutes.
Cooldown is process-local and resets on process restart. It is shared across
roles that resolve to the same configured model within that process. After the
cooldown, the primary is tried again. The fast model and explicit alternate
requests do not recursively fail over.

Recovery transport attempts are limited to five, with two seconds between
attempts so the existing PM2 worker can restart. Generation requests retain the
gateway's configured timeout. Model-level errors stop the retries. If recovery
also fails, return an explicit error; do not claim the model answered.

Logs identify recovery selection, success, or exhaustion without storing
conversation content. Existing requested-role labels are not a guarantee of
the model that ultimately answered; consult recovery logs. No cloud provider,
model download, additional worker, or scheduled automation is created.
