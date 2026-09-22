"""Content-free microbenchmark for connector queue state transitions."""

from __future__ import annotations

import argparse
import json
import os
import time

from connectors.delivery_gateway import DeliveryQueue


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs", type=int, default=10_000)
    parser.add_argument("--mode", choices=("auto", "rust", "python"), default="auto")
    args = parser.parse_args()
    jobs = max(1, min(1_000_000, args.jobs))
    os.environ["CURIE_CONNECTOR_GATEWAY"] = args.mode
    queue = DeliveryQueue(capacity=max(4, min(jobs, 65_536)), concurrency=4)
    started = time.perf_counter()
    completed = 0
    for index in range(jobs):
        status, job_id = queue._gateway.enqueue(
            idempotency_key=None,
            priority=index % 3,
            deadline_ms=None,
        )
        if status != "accepted":
            raise RuntimeError(status)
        if queue._gateway.try_claim(job_id, 0) != "claimed":
            raise RuntimeError("claim_failed")
        completed += int(queue._gateway.complete(job_id))
    elapsed = time.perf_counter() - started
    print(
        json.dumps(
            {
                "backend": queue.backend,
                "jobs": jobs,
                "completed": completed,
                "milliseconds": round(elapsed * 1000, 3),
                "operations_per_second": round(jobs / max(elapsed, 1e-9), 1),
            },
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
