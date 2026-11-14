"""Opt-in synthetic local-only personality sampling; no tools or personal memory."""

import argparse
import json
import os
from pathlib import Path
import time

CASES = (
    (
        "command",
        "Synthetic verified result: the desk lamp is OFF. Confirm that result in one sentence.",
    ),
    (
        "research",
        "Synthetic evidence: an experimental robot completed 18 of 20 trials. Give a brief useful update; do not invent comparisons or sources.",
    ),
    ("distress", "I'm overwhelmed today. Give me one small practical step. No jokes."),
)

FRENCH_CASES = (
    ("greeting", "Hello Curie"),
    ("thanks", "Thanks, that helped."),
    ("preference", "I prefer tea to coffee."),
    ("discovery", "I found the missing puzzle piece under the sofa."),
    ("casual_choice", "Tea and a book or another puzzle tonight? Pick one."),
    ("appreciation", "Thank you."),
    ("english_only", "English only: I found the missing piece."),
    ("exact", "Reply with exactly this: The lamp is OFF."),
    ("urgent", "Urgent: the fire alarm is sounding. Give one immediate step."),
    ("distress", "I'm overwhelmed today. Give one small practical step. No jokes."),
    (
        "research",
        "Synthetic evidence: 18 of 20 trials succeeded. What percentage succeeded?",
    ),
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", required=True)
    parser.add_argument("--suite", choices=("baseline", "french"), default="baseline")
    args = parser.parse_args()
    os.environ.setdefault(
        "CURIE_INFERENCE_SOCKET",
        str(Path(__file__).resolve().parents[1] / "log/inference/worker.sock"),
    )
    os.environ.setdefault("CURIE_INFERENCE_TIMEOUT_SECONDS", "90")
    from agent.persona_contract import active_persona, build_persona_contract
    from agent.personality_context import PersonalityContext
    from agent.orchestration.response_policy import ResponsePolicy
    from llm.chat_prompt import conversation_prompt
    from llm.local_gateway import request

    persona = active_persona()
    context = PersonalityContext(persona)
    policy = ResponsePolicy(persona, context)
    rows = []
    history = []
    cases = FRENCH_CASES if args.suite == "french" else CASES
    for index, (name, question) in enumerate(cases):
        # First six French cases form a conversation; constraint cases are isolated.
        if args.suite != "french" or index >= 6:
            history = []
        guidance = (
            "\n"
            + "\n".join(context.build_live_prompt_directives(question, history=history))
            if args.suite == "french"
            else ""
        )
        prompt = conversation_prompt(
            build_persona_contract(persona, medium=name, compact=True)
            + " This is a synthetic evaluation. No tools or private memories are available."
            + guidance,
            history,
            question,
        )
        started = time.monotonic()
        try:
            raw = request(prompt, role="general", temperature=0.25, max_tokens=120)
            row = dict(
                case=name,
                raw_reply=raw,
                reply=policy.finalize(raw, question, history=history),
                seconds=round(time.monotonic() - started, 2),
            )
        except Exception as exc:
            row = dict(
                case=name,
                error=type(exc).__name__,
                seconds=round(time.monotonic() - started, 2),
            )
        rows.append(row)
        if "reply" in row:
            history.extend([("user", question), ("assistant", row["reply"])])
        print(json.dumps(row), flush=True)
        Path(args.report).write_text(
            json.dumps(
                {
                    "synthetic": True,
                    "suite": args.suite,
                    "human_review_required": True,
                    "rows": rows,
                },
                indent=2,
            )
        )
        if "error" in row:
            break


if __name__ == "__main__":
    main()
