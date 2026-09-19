"""Deterministic release gate for response composition and unified memory."""

from __future__ import annotations

from dataclasses import replace
import json
import re
from typing import Iterable

from agent.response_composer import build_response_plan, render_response
from memory.service import MemoryRecord, MemoryService
from services.proactive_policy import rank_candidates


class _MemoryRepository:
    def __init__(self):
        self.records: dict[tuple[str, str], MemoryRecord] = {}
        self.events: list[tuple[str, str, tuple[str, ...], str]] = []

    def upsert(self, record: MemoryRecord) -> None:
        self.records[(record.owner_id, record.record_id)] = record

    def get(self, owner_id: str, record_id: str) -> MemoryRecord | None:
        return self.records.get((str(owner_id), str(record_id)))

    def list_owner(self, owner_id: str) -> list[MemoryRecord]:
        return [
            record
            for (stored_owner, _), record in self.records.items()
            if stored_owner == str(owner_id)
        ]

    def record_retrieval(
        self, owner_id: str, query_hash: str, record_ids: Iterable[str], outcome: str
    ) -> None:
        self.events.append((owner_id, query_hash, tuple(record_ids), outcome))

    def legacy_documents(self, owner_id: str) -> list[dict]:
        return [item.to_document() for item in self.list_owner(owner_id)]


def _response_metrics() -> dict[str, float | int]:
    fact_lock_passed = 0
    natural_commands = 0
    for index in range(100):
        result = {
            "text": f"Done. Lamp {index} is off.",
            "verification_status": "verified",
            "device_id": f"lamp-{index}",
            "execution_status": "completed",
        }
        plan = build_response_plan(
            result,
            user_text=f"Turn off lamp {index}",
            response_mode="command_ack",
            connector="telegram",
        )
        rendered = render_response(plan, result)
        fact_lock_passed += int(
            rendered["device_id"] == result["device_id"]
            and rendered["verification_status"] == result["verification_status"]
        )
        natural_commands += int(
            len(rendered["text"].split()) <= 12
            and not re.search(
                r"\b(?:certainly|absolutely|at your service|how may i assist|monsieur)\b",
                rendered["text"],
                re.I,
            )
        )
    return {
        "response_examples": 100,
        "fact_lock_passed": fact_lock_passed,
        "natural_command_passed": natural_commands,
        "human_naturalness_proxy": round(natural_commands / 100 * 5, 2),
    }


def _proactive_metrics() -> dict[str, int]:
    unsupported_selected = 0
    for index in range(100):
        selected = rank_candidates(
            [
                {
                    "kind": "routine",
                    "topic": f"mood-{index}",
                    "reason": "Friendly observation",
                    "message": "I noticed your mood seems low.",
                    "confidence": 0.99,
                    "usefulness": 0.9,
                }
            ],
            {},
        )
        unsupported_selected += int(bool(selected))
    return {
        "unsupported_proactive_examples": 100,
        "unsupported_proactive_selected": unsupported_selected,
    }


def _memory_metrics() -> dict[str, float | int]:
    repository = _MemoryRepository()
    service = MemoryService(repository)
    expected: dict[str, str] = {}
    for index in range(100):
        predicate = f"favorite_snack_{index}"
        record = service.remember(
            "owner-a",
            predicate=predicate,
            value=f"snack-{index}",
            type="preference",
            evidence=f"My {predicate} is snack-{index}",
        )
        assert record is not None
        expected[predicate] = record.record_id

    correct = 0
    for predicate, record_id in expected.items():
        result = service.retrieve(
            "owner-a", f"What is my {predicate}?", limit=1, explicit_search=True
        )
        correct += int(
            bool(result.hits) and result.hits[0].record.record_id == record_id
        )

    cross_owner = sum(
        bool(service.retrieve("owner-b", f"What is my {predicate}?").hits)
        for predicate in list(expected)[:25]
    )
    first_id = next(iter(expected.values()))
    service.forget("owner-a", record_id=first_id)
    deleted_recalled = any(
        hit.record.record_id == first_id
        for hit in service.retrieve(
            "owner-a", "favorite snack 0", explicit_search=True
        ).hits
    )
    # Ensure retrieval counters remain repository-owned data, not relevance policy.
    repository.upsert(
        replace(
            repository.get("owner-a", list(expected.values())[1]),
            retrieval_count=1,
        )
    )
    return {
        "memory_queries": len(expected),
        "memory_correct": correct,
        "memory_precision": round(correct / len(expected), 4),
        "cross_owner_leaks": cross_owner,
        "deleted_records_recalled": int(deleted_recalled),
    }


def run() -> dict:
    report = {
        **_response_metrics(),
        **_proactive_metrics(),
        **_memory_metrics(),
    }
    checks = {
        "response_fact_lock": report["fact_lock_passed"] == report["response_examples"],
        "naturalness": report["human_naturalness_proxy"] >= 4.5,
        "unsupported_proactive_claims": report["unsupported_proactive_selected"] == 0,
        "memory_precision": report["memory_precision"] >= 0.98,
        "owner_isolation": report["cross_owner_leaks"] == 0,
        "deletion": report["deleted_records_recalled"] == 0,
    }
    report["checks"] = checks
    report["score"] = round(10 * sum(checks.values()) / len(checks), 2)
    return report


def main() -> None:
    report = run()
    print(json.dumps(report, indent=2, sort_keys=True))
    if not all(report["checks"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
