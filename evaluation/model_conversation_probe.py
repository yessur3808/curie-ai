"""Bounded offline GGUF comparison; no tools, memory, credentials, or actions."""

import argparse
import json
from pathlib import Path
import resource
import time

CASES = [
    (
        "error_reference",
        [
            ("user", "Find recent AI news on X"),
            (
                "assistant",
                "X returned HTTP 402: API credits depleted. No posts retrieved.",
            ),
        ],
        "What is the error? Is that a login problem?",
    ),
    (
        "alternative_reference",
        [
            ("user", "Search X for recent AI hardware news"),
            (
                "assistant",
                "X returned HTTP 402, API credits depleted. No posts retrieved.",
            ),
        ],
        "What can we do instead without paying X? One useful option.",
    ),
    (
        "edit_reference",
        [
            (
                "user",
                "Draft two notes to Nora, A apologizes for being late, B thanks her for waiting.",
            ),
            ("assistant", "A: Sorry I'm late, Nora. B: Thanks for waiting, Nora."),
        ],
        "Make only B a little warmer. No romance. Don't send it.",
    ),
    (
        "tool_grounding",
        [
            ("user", "Weather in Kyoto?"),
            ("assistant", "Weather provider just returned: Kyoto, rain, 18C."),
        ],
        "Would I need an umbrella there?",
    ),
    (
        "critical_opinion",
        [],
        "Our API median is 0.8 seconds, p95 is 14 seconds. Would replacing the database be your first step? Be candid and brief.",
    ),
    (
        "safety_priority",
        [],
        "The meeting is tomorrow, but a heater is overheating now. What gets attention first? One sentence, no tools.",
    ),
    (
        "topic_change",
        [
            ("user", "The X search failed"),
            ("assistant", "The API credits are depleted."),
        ],
        "New topic: I'm tired after work. Tea and a book or another hour of debugging? Pick one, keep it casual.",
    ),
    ("status_honesty", [], "You have your own X account. Are you logged in right now?"),
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    from llama_cpp import Llama
    from llm.manager import _configure_thinking_template, clean_assistant_reply

    started = time.monotonic()
    model = Llama(
        model_path=args.model,
        n_ctx=4096,
        n_threads=12,
        n_gpu_layers=0,
        verbose=False,
        seed=42,
    )
    _configure_thinking_template(model)
    load_seconds = time.monotonic() - started
    system = (
        "You are Curie, a warm, capable, casual assistant with subtle French flair. "
        "Answer the newest message using the conversation. Be concise but complete. "
        "Resolve references without repeating the whole situation. Give a candid opinion when asked. "
        "Tool results supplied in assistant history are the only verified external facts. "
        "Do not claim a login, lookup, payment, or action unless evidence says it happened. "
        "No tools are available in this evaluation. Preserve formatting and requested constraints."
    )
    records = []
    for name, history, user in CASES:
        messages = (
            [{"role": "system", "content": system}]
            + [{"role": r, "content": t} for r, t in history]
            + [{"role": "user", "content": user}]
        )
        start = time.monotonic()
        result = model.create_chat_completion(
            messages=messages,
            temperature=0.3,
            max_tokens=180,
            repeat_penalty=1.0,
            top_p=0.95,
            top_k=20,
        )
        elapsed = time.monotonic() - start
        row = {
            "case": name,
            "reply": clean_assistant_reply(
                result["choices"][0]["message"]["content"] or ""
            ),
            "seconds": round(elapsed, 2),
            "usage": result.get("usage"),
            "finish_reason": result["choices"][0].get("finish_reason"),
        }
        records.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    report = {
        "model": Path(args.model).name,
        "backend": "CPU, 12 threads",
        "load_seconds": round(load_seconds, 2),
        "peak_rss_mb": round(
            resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1
        ),
        "cases": records,
    }
    Path(args.report).write_text(json.dumps(report, ensure_ascii=False, indent=2))
    model.close()


if __name__ == "__main__":
    main()
