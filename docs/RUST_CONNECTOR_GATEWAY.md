# Rust connector queue and delivery gateway

Curie's connector gateway is an ABI3 PyO3 extension. It owns bounded queue
admission, priority/FIFO ordering, concurrency, monotonic deadlines,
cancellation, live idempotency keys, retry delays, and content-free metrics.

Python deliberately retains Telegram, Discord, Slack, API, and other provider
SDK calls. Authorization, message formatting, Curie's personality, delivery
receipts, and SLO reporting also remain in Python. The native boundary receives
no message text and no recipient identifier.

## Build and verify

```bash
make connector-gateway
make connector-gateway-check
```

`CURIE_CONNECTOR_GATEWAY` accepts:

- `rust`: require the native gateway and fail readiness if it is unavailable;
- `auto`: use Rust when installed, otherwise use the audited Python rollback;
- `python`: explicitly select the rollback during incident recovery.

Production uses `rust`. `CONNECTOR_DELIVERY_CONCURRENCY` controls simultaneous
provider sends inside each bounded connector queue. Capacity remains separate,
so bursts wait in deterministic order instead of creating unbounded tasks.

`send_with_receipt` accepts optional idempotency, priority, deadline, and retry
arguments. Retries require an explicit idempotency key; non-idempotent sends are
never replayed automatically. Receipts expose content-free queue wait, attempt,
backend, and truthful delivery status.

## Safety invariants

- Full queues reject immediately and increment overload counters.
- A cancellation removes both queued and active state.
- Deadlines use monotonic time and expire before provider execution.
- Duplicate live idempotency keys never execute twice.
- Priority changes ordering, but FIFO is stable within one priority.
- Provider exceptions cannot be reported as successful delivery.
- The dormant Python implementation is a rollback, not a silent production
  fallback when strict Rust mode is configured.
