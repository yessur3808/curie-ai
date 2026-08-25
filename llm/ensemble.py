"""Bounded parallel local-LLM specialists and optional answer synthesis."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import os
import re
from typing import Iterable

from llm import manager

_ENSEMBLE_REQUEST = re.compile(
    r"\b(?:use (?:multiple|several) (?:agents?|models?)|parallel agents?|"
    r"independent(?:ly)? (?:review|verify|check)|double[- ]check with)\b",
    re.IGNORECASE,
)
_AUTO_ENSEMBLE_TASK = re.compile(
    r"\b(?:security review|threat model|architecture review|root cause analysis|"
    r"compare (?:the|these|multiple|several)|evaluate (?:the|these|multiple)|"
    r"high[- ]stakes|verify (?:the|these|this)|audit (?:the|this))\b",
    re.IGNORECASE,
)
_HIGH_STAKES_TASK = re.compile(
    r"\b(?:medical|medicine|dose|dosage|symptom|legal|law|contract|financial|"
    r"investment|tax|security|credential|vulnerability|emergency)\b",
    re.IGNORECASE,
)


def should_use_ensemble(user_text: str) -> bool:
    """Use bounded specialists explicitly or after a recorded evaluation gain."""
    enabled = os.getenv("LLM_ENSEMBLE_ENABLED", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    automatic = os.getenv("LLM_ENSEMBLE_AUTO_COMPLEX", "true").lower() in (
        "1",
        "true",
        "yes",
    )
    try:
        evaluated_gain = float(os.getenv("LLM_ENSEMBLE_EVAL_GAIN", "0"))
        minimum_gain = max(0.0, float(os.getenv("LLM_ENSEMBLE_MIN_GAIN", "0.03")))
    except ValueError:
        evaluated_gain, minimum_gain = 0.0, 0.03
    return enabled and bool(
        _ENSEMBLE_REQUEST.search(user_text)
        or _HIGH_STAKES_TASK.search(user_text)
        or (
            automatic
            and evaluated_gain >= minimum_gain
            and _AUTO_ENSEMBLE_TASK.search(user_text)
        )
    )


@dataclass(frozen=True)
class LocalTask:
    name: str
    prompt: str
    role: str = "general"
    temperature: float = 0.2
    max_tokens: int = 512


def run_parallel(
    tasks: Iterable[LocalTask], max_workers: int | None = None
) -> dict[str, str]:
    """Run independent specialist prompts concurrently with bounded resources."""
    task_list = list(tasks)
    if not task_list:
        return {}
    configured = int(os.getenv("LLM_PARALLEL_WORKERS", "2"))
    workers = max(1, min(max_workers or configured, configured, len(task_list)))

    def _run(task: LocalTask) -> str:
        return manager.ask_llm(
            task.prompt,
            temperature=task.temperature,
            max_tokens=task.max_tokens,
            role=task.role,
        )

    results: dict[str, str] = {}
    with ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix="curie-agent"
    ) as pool:
        futures = {pool.submit(_run, task): task for task in task_list}
        for future in as_completed(futures):
            task = futures[future]
            try:
                results[task.name] = future.result()
            except Exception as exc:
                results[task.name] = f"[Error: {exc}]"
    return results


def ask_ensemble(
    prompt: str,
    perspectives: tuple[str, ...] = ("reasoning", "critic"),
    max_tokens: int = 512,
) -> str:
    """Run independent perspectives in parallel and synthesize one final answer."""
    critic_backend = os.getenv("LLM_ENSEMBLE_CRITIC_BACKEND", "fast")
    tasks = [
        LocalTask(
            name=role,
            role=critic_backend if role == "critic" else role,
            prompt=(
                f"Act as the {role} specialist. Independently solve or assess the "
                f"request. Return concise findings only.\n\nRequest:\n{prompt}"
            ),
            max_tokens=max_tokens,
        )
        for role in perspectives
    ]
    findings = run_parallel(tasks)
    evidence = "\n\n".join(f"[{name}]\n{text}" for name, text in findings.items())
    synthesis_prompt = (
        "Synthesize the specialist findings into one accurate, concise final answer. "
        "Resolve disagreements; do not mention the specialists or expose hidden reasoning.\n\n"
        f"Original request:\n{prompt}\n\nFindings:\n{evidence}"
    )
    return manager.ask_llm(
        synthesis_prompt,
        temperature=0.2,
        max_tokens=max_tokens,
        role="general",
    )
