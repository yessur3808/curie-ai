"""Reusable, self-cleaning live conversation evaluation runner.

Templates are version-controlled test inputs, never Curie memories. Each run uses a
unique owner and closes its native voice session in ``finally`` so synthetic history
does not leak into later conversations.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import re
import sys
import time
from typing import Any, Callable, Protocol
from urllib.parse import quote
from urllib.request import Request, urlopen
import uuid

TEMPLATE_DIRECTORY = Path(__file__).with_name("conversation_templates")
_IDENTIFIER = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
_SUPPORTED_CHECKS = {
    "all_replies_nonempty",
    "bullet_count",
    "forbidden_phrases_absent",
    "max_lines",
    "max_pair_similarity",
    "max_words",
    "no_error_responses",
    "terminal_punctuation_max",
    "turn_contains_any",
    "turn_not_contains_any",
    "voice_generated",
}


class TemplateValidationError(ValueError):
    """Raised when a stored conversation template violates the contract."""


class ConversationClient(Protocol):
    def open_session(self, user_id: str) -> str: ...

    def chat(self, payload: dict[str, Any]) -> dict[str, Any]: ...

    def close_session(self, session_id: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class CheckResult:
    check_id: str
    passed: bool
    detail: str


@dataclass(frozen=True, slots=True)
class RunResult:
    template_id: str
    run_id: str
    duration_seconds: float
    passed: int
    total: int
    cleanup_succeeded: bool
    checks: tuple[CheckResult, ...]
    transcript: tuple[dict[str, Any], ...]

    @property
    def successful(self) -> bool:
        return self.passed == self.total and self.cleanup_succeeded

    def as_dict(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id,
            "run_id": self.run_id,
            "duration_seconds": self.duration_seconds,
            "passed": self.passed,
            "total": self.total,
            "cleanup_succeeded": self.cleanup_succeeded,
            "successful": self.successful,
            "checks": [asdict(item) for item in self.checks],
            "transcript": list(self.transcript),
        }


class HttpConversationClient:
    def __init__(self, base_url: str, timeout_seconds: float = 240.0):
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def _request(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        request = Request(
            self.base_url + path,
            data=body,
            headers={"Content-Type": "application/json"},
            method=method,
        )
        with urlopen(request, timeout=self.timeout_seconds) as response:
            return json.loads(response.read())

    def chat(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/chat", payload)

    def open_session(self, user_id: str) -> str:
        result = self._request("POST", "/voice-sessions/" + quote(user_id, safe=""))
        session_id = str(result.get("session_id") or "")
        if not session_id:
            raise RuntimeError("Curie did not return a native session id")
        return session_id

    def close_session(self, session_id: str) -> bool:
        result = self._request(
            "DELETE", "/voice-sessions/session/" + quote(session_id, safe="")
        )
        return result.get("closed") is True


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise TemplateValidationError(message)


def validate_template(template: dict[str, Any], *, source: str = "template") -> None:
    _require(template.get("schema_version") == 1, f"{source}: schema_version must be 1")
    template_id = template.get("id")
    _require(
        isinstance(template_id, str) and bool(_IDENTIFIER.fullmatch(template_id)),
        f"{source}: id must be a lowercase identifier",
    )
    _require(
        isinstance(template.get("name"), str) and bool(template["name"].strip()),
        f"{source}: name is required",
    )
    turns = template.get("turns")
    _require(isinstance(turns, list) and bool(turns), f"{source}: turns are required")
    for index, turn in enumerate(turns, start=1):
        _require(isinstance(turn, dict), f"{source}: turn {index} must be an object")
        _require(
            isinstance(turn.get("user"), str) and bool(turn["user"].strip()),
            f"{source}: turn {index} needs non-empty user text",
        )
        if "voice_response" in turn:
            _require(
                isinstance(turn["voice_response"], bool),
                f"{source}: turn {index} voice_response must be boolean",
            )

    checks = template.get("checks")
    _require(
        isinstance(checks, list) and bool(checks), f"{source}: checks are required"
    )
    check_ids: set[str] = set()
    for index, check in enumerate(checks, start=1):
        _require(isinstance(check, dict), f"{source}: check {index} must be an object")
        check_id = check.get("id")
        _require(
            isinstance(check_id, str) and bool(_IDENTIFIER.fullmatch(check_id)),
            f"{source}: check {index} needs a lowercase id",
        )
        _require(check_id not in check_ids, f"{source}: duplicate check id {check_id}")
        check_ids.add(check_id)
        check_type = check.get("type")
        _require(
            check_type in _SUPPORTED_CHECKS,
            f"{source}: unsupported check type {check_type!r}",
        )
        if "turn" in check:
            _require(
                isinstance(check["turn"], int) and 1 <= check["turn"] <= len(turns),
                f"{source}: check {check_id} has an invalid turn",
            )
        if check_type in {
            "bullet_count",
            "max_lines",
            "max_words",
            "terminal_punctuation_max",
            "turn_contains_any",
            "turn_not_contains_any",
            "voice_generated",
        }:
            _require(
                "turn" in check,
                f"{source}: check {check_id} requires a turn",
            )
        if check_type in {
            "bullet_count",
            "max_lines",
            "max_pair_similarity",
            "max_words",
            "terminal_punctuation_max",
        }:
            value = check.get("value")
            _require(
                isinstance(value, (int, float)) and not isinstance(value, bool),
                f"{source}: check {check_id} requires a numeric value",
            )
        if check_type in {
            "forbidden_phrases_absent",
            "turn_contains_any",
            "turn_not_contains_any",
        }:
            phrases = check.get("phrases")
            _require(
                isinstance(phrases, list)
                and bool(phrases)
                and all(isinstance(item, str) and item for item in phrases),
                f"{source}: check {check_id} requires phrases",
            )


def load_template(path: Path) -> dict[str, Any]:
    template = json.loads(path.read_text(encoding="utf-8"))
    _require(isinstance(template, dict), f"{path}: root must be an object")
    validate_template(template, source=str(path))
    return template


def discover_templates(directory: Path = TEMPLATE_DIRECTORY) -> dict[str, Path]:
    catalog: dict[str, Path] = {}
    for path in sorted(directory.glob("*.json")):
        if path.name == "schema.json":
            continue
        template = load_template(path)
        template_id = template["id"]
        _require(template_id not in catalog, f"duplicate template id {template_id}")
        catalog[template_id] = path
    return catalog


def _words(text: str) -> list[str]:
    return re.findall(r"[\w']+", text.casefold())


def _similarity(left: str, right: str) -> float:
    first, second = set(_words(left)), set(_words(right))
    return len(first & second) / max(len(first | second), 1)


def _turn(transcript: list[dict[str, Any]], check: dict[str, Any]) -> dict[str, Any]:
    return transcript[int(check["turn"]) - 1]


def evaluate_check(
    check: dict[str, Any], transcript: list[dict[str, Any]]
) -> CheckResult:
    check_id = str(check["id"])
    check_type = check["type"]
    replies = [str(item.get("assistant") or "") for item in transcript]
    folded = [reply.casefold() for reply in replies]
    passed = False
    detail = ""

    if check_type == "all_replies_nonempty":
        missing = [
            str(index) for index, reply in enumerate(replies, 1) if not reply.strip()
        ]
        passed = not missing
        detail = (
            "all turns answered"
            if passed
            else "empty replies at turns " + ", ".join(missing)
        )
    elif check_type == "no_error_responses":
        failures = [
            str(index)
            for index, reply in enumerate(folded, 1)
            if reply.startswith("[error") or "no response was available" in reply
        ]
        passed = not failures
        detail = (
            "no error replies"
            if passed
            else "error replies at turns " + ", ".join(failures)
        )
    elif check_type == "forbidden_phrases_absent":
        phrases = [str(item).casefold() for item in check.get("phrases", [])]
        matches = sorted(
            {phrase for phrase in phrases if any(phrase in reply for reply in folded)}
        )
        passed = not matches
        detail = (
            "forbidden phrases absent" if passed else "found: " + ", ".join(matches)
        )
    elif check_type == "max_pair_similarity":
        observed = max(
            (
                _similarity(replies[left], replies[right])
                for left in range(len(replies))
                for right in range(left)
            ),
            default=0.0,
        )
        limit = float(check["value"])
        passed = observed < limit
        detail = f"maximum similarity {observed:.3f}; required < {limit:.3f}"
    elif check_type == "max_lines":
        item = _turn(transcript, check)
        reply = str(item.get("assistant") or "")
        observed = sum(bool(line.strip()) for line in reply.splitlines())
        limit = int(check["value"])
        passed = observed <= limit
        detail = f"turn {check['turn']} has {observed} lines; required <= {limit}"
    elif check_type == "max_words":
        item = _turn(transcript, check)
        observed = len(_words(str(item.get("assistant") or "")))
        limit = int(check["value"])
        passed = observed <= limit
        detail = f"turn {check['turn']} has {observed} words; required <= {limit}"
    elif check_type == "turn_contains_any":
        reply = str(_turn(transcript, check).get("assistant") or "").casefold()
        phrases = [str(item).casefold() for item in check.get("phrases", [])]
        matches = [phrase for phrase in phrases if phrase in reply]
        passed = bool(matches)
        detail = (
            "matched: " + ", ".join(matches)
            if passed
            else "none of the expected phrases matched"
        )
    elif check_type == "turn_not_contains_any":
        reply = str(_turn(transcript, check).get("assistant") or "").casefold()
        phrases = [str(item).casefold() for item in check.get("phrases", [])]
        matches = [phrase for phrase in phrases if phrase in reply]
        passed = not matches
        detail = (
            "forbidden phrases absent" if passed else "found: " + ", ".join(matches)
        )
    elif check_type == "bullet_count":
        reply = str(_turn(transcript, check).get("assistant") or "")
        prefixes = tuple(str(item) for item in check.get("prefixes", ["-", "•"]))
        observed = sum(
            1 for line in reply.splitlines() if line.lstrip().startswith(prefixes)
        )
        expected = int(check["value"])
        passed = observed == expected
        detail = f"turn {check['turn']} has {observed} bullets; required {expected}"
    elif check_type == "terminal_punctuation_max":
        reply = str(_turn(transcript, check).get("assistant") or "")
        observed = sum(reply.count(mark) for mark in ".!?")
        limit = int(check["value"])
        passed = observed <= limit
        detail = (
            f"turn {check['turn']} has {observed} sentence marks; required <= {limit}"
        )
    elif check_type == "voice_generated":
        passed = bool(_turn(transcript, check).get("voice_generated"))
        detail = f"turn {check['turn']} voice generated: {passed}"
    else:  # pragma: no cover - validation rejects unsupported types
        raise TemplateValidationError(f"unsupported check type {check_type!r}")

    return CheckResult(check_id, passed, detail)


def evaluate_template(
    template: dict[str, Any], transcript: list[dict[str, Any]]
) -> tuple[CheckResult, ...]:
    validate_template(template)
    return tuple(evaluate_check(check, transcript) for check in template["checks"])


def run_template(
    template: dict[str, Any],
    client: ConversationClient,
    *,
    disable_voice: bool = False,
    on_turn: Callable[[dict[str, Any]], None] | None = None,
) -> RunResult:
    validate_template(template)
    run_id = uuid.uuid4().hex
    user_id = f"conversation-eval-{template['id']}-{run_id}"
    defaults = template.get("defaults") or {}
    session_id: str | None = None
    transcript: list[dict[str, Any]] = []
    cleanup_succeeded = False
    started = time.perf_counter()
    try:
        session_id = client.open_session(user_id)
        if not session_id:
            raise RuntimeError(
                "Curie did not return a native session id; refusing an uncleanable "
                "multi-turn evaluation"
            )
        for index, turn in enumerate(template["turns"], start=1):
            result = client.chat(
                {
                    "message": turn["user"],
                    "user_id": user_id,
                    "session_id": session_id,
                    "voice_response": bool(turn.get("voice_response", False))
                    and not disable_voice,
                    "voice_profile": turn.get(
                        "voice_profile", defaults.get("voice_profile", "trained")
                    ),
                    "live": bool(defaults.get("live", False)),
                    "ephemeral": False,
                }
            )
            returned_session_id = str(result.get("session_id") or session_id)
            if returned_session_id != session_id:
                raise RuntimeError(
                    "Curie changed the native session id during the evaluation"
                )
            item = {
                "turn": index,
                "user": turn["user"],
                "assistant": str(result.get("text") or ""),
                "model": result.get("model_used"),
                "latency_ms": result.get("processing_time_ms"),
                "voice_generated": bool(result.get("voice_url")),
            }
            transcript.append(item)
            if on_turn is not None:
                on_turn(item)
    finally:
        if session_id is not None:
            cleanup_succeeded = client.close_session(session_id)

    checks = evaluate_template(template, transcript)
    return RunResult(
        template_id=str(template["id"]),
        run_id=run_id,
        duration_seconds=round(time.perf_counter() - started, 2),
        passed=sum(item.passed for item in checks),
        total=len(checks),
        cleanup_succeeded=cleanup_succeeded,
        checks=checks,
        transcript=tuple(transcript),
    )


def _print_turn(item: dict[str, Any]) -> None:
    print(json.dumps(item, ensure_ascii=False), flush=True)


def _write_report(path: Path, results: list[RunResult]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "schema_version": 1,
        "results": [result.as_dict() for result in results],
    }
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("template", nargs="?", help="stored template id or JSON path")
    parser.add_argument("--all", action="store_true", help="run every stored template")
    parser.add_argument("--list", action="store_true", help="list stored templates")
    parser.add_argument("--api-base", default="http://127.0.0.1:8010")
    parser.add_argument("--timeout", type=float, default=240.0)
    parser.add_argument("--no-voice", action="store_true")
    parser.add_argument(
        "--report", type=Path, help="optional explicit JSON report path"
    )
    args = parser.parse_args(argv)

    catalog = discover_templates()
    if args.list:
        for template_id, path in catalog.items():
            template = load_template(path)
            print(f"{template_id}\t{template['name']}")
        return 0
    if args.all and args.template:
        parser.error("choose a template or --all, not both")
    if not args.all and not args.template:
        parser.error("provide a template id/path or use --all")

    paths: list[Path]
    if args.all:
        paths = list(catalog.values())
    else:
        requested = str(args.template)
        path = catalog.get(requested, Path(requested))
        if not path.is_file():
            parser.error(f"unknown template: {requested}")
        paths = [path]

    client = HttpConversationClient(args.api_base, args.timeout)
    results: list[RunResult] = []
    for path in paths:
        result = run_template(
            load_template(path),
            client,
            disable_voice=args.no_voice,
            on_turn=_print_turn,
        )
        results.append(result)
        print(
            json.dumps(
                {**result.as_dict(), "transcript": "omitted"},
                ensure_ascii=False,
            )
        )
    if args.report:
        _write_report(args.report, results)
    return 0 if all(result.successful for result in results) else 1


if __name__ == "__main__":
    sys.exit(main())
