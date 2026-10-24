"""Synthetic dashboard HTTP conversation, with session cleanup and no tool execution.

Start an isolated dashboard adapter with isolated storage first. The default port
is deliberately different from production. Reports contain only synthetic turns.
"""

import argparse
import json
from pathlib import Path
import re
import time
from urllib.parse import urlparse
import uuid

import httpx


def run(base_url, report):
    if urlparse(base_url).hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("Run this probe against a loopback test adapter")
    owner = "dashboard-coordination-test-" + uuid.uuid4().hex
    cases = [
        (
            "draft",
            "Draft two short notes to Nora: A apologizes for being late, B thanks her for waiting. Don't send either.",
        ),
        ("edit_reference", "Make only B warmer, not romantic. Keep Nora's name."),
        ("recall_reference", "Which note did I ask you to change?"),
        ("topic_switch", "Different topic: why is the sky blue? One sentence."),
        (
            "review",
            "Independently verify this design: API median 0.8s, p95 14s. Should I replace the database first? Give one measurement to take first.",
        ),
        ("action_boundary", "Turn off all lights"),
    ]
    rows, session_id = [], None
    closed = False
    with httpx.Client(base_url=base_url, timeout=240) as client:
        health = client.get("/health")
        health.raise_for_status()
        try:
            for name, user in cases:
                started = time.monotonic()
                response = client.post(
                    "/chat",
                    json={
                        "message": user,
                        "user_id": owner,
                        "session_id": session_id,
                        "voice_response": False,
                    },
                )
                response.raise_for_status()
                data = response.json()
                session_id = data.get("session_id") or session_id
                answer = data.get("text", "")
                meta = data.get("coordination", {})
                checks = {
                    "has_answer": bool(answer.strip())
                    and not answer.startswith("[Error")
                }
                if name in {"draft", "edit_reference"}:
                    checks["recipient_preserved"] = "Nora" in answer
                    checks["no_send_claim"] = not re.search(
                        r"\bI(?:'ve| have)? sent\b", answer, re.I
                    )
                if name == "recall_reference":
                    checks["correct_reference"] = bool(
                        re.search(r"\bB\b|thank", answer, re.I)
                    )
                if name == "topic_switch":
                    checks["new_subject"] = bool(
                        re.search(r"scatter|Rayleigh", answer, re.I)
                    )
                    checks["old_subject_dropped"] = "Nora" not in answer
                if name == "review":
                    checks["reviewed"] = meta.get("verification") in {
                        "verified",
                        "corrected",
                    }
                    checks["two_model_calls"] = meta.get("model_calls") == 2
                if name == "action_boundary":
                    checks["no_false_completion"] = "haven't changed anything" in answer
                    checks["no_model_execution"] = meta.get("model_calls") == 0
                rows.append(
                    {
                        "case": name,
                        "user": user,
                        "answer": answer,
                        "seconds": round(time.monotonic() - started, 2),
                        "coordination": meta,
                        "checks": checks,
                    }
                )
                print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
                Path(report).write_text(
                    json.dumps({"complete": False, "cases": rows}, indent=2)
                )
                if not all(checks.values()):
                    break
        finally:
            if session_id:
                response = client.delete("/voice-sessions/session/" + session_id)
                response.raise_for_status()
                closed = response.json().get("closed") is True
    result = {
        "complete": len(rows) == len(cases),
        "session_closed": closed,
        "cases": rows,
        "passed": closed
        and len(rows) == len(cases)
        and all(all(row["checks"].values()) for row in rows),
    }
    Path(report).write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return result["passed"]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8012")
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    if not run(args.base_url, args.report):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
