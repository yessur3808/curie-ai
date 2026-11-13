"""Private, authenticated OpenAI-shaped ranking endpoint for the Letta scout.

Reuses Curie's Unix-socket worker; never creates a second model cache.
"""

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import threading
import time


def ranking_messages(payload):
    if payload.get("model") not in {
        "curie-ai-scout",
        "curie-specialist",
    } or payload.get("tools"):
        raise ValueError("ranking_only_endpoint")
    messages = payload.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= 16:
        raise ValueError("invalid_messages")
    result = []
    for item in messages:
        if item.get("role") not in {"system", "user", "assistant"}:
            raise ValueError("invalid_role")
        content = item.get("content")
        if isinstance(content, list):
            if any(part.get("type") != "text" for part in content):
                raise ValueError("text_only")
            content = "\n".join(part.get("text", "") for part in content)
        if not isinstance(content, str):
            raise ValueError("text_only")
        result.append({"role": item["role"], "content": content})
    if sum(len(item["content"]) for item in result) > 24000:
        raise ValueError("packet_too_large")
    return result


def make_handler(token, ask, specialist_ask=None):
    gate = threading.BoundedSemaphore(1)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def json(self, status, data):
            body = json.dumps(data).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            return secrets.compare_digest(
                self.headers.get("Authorization", ""), "Bearer " + token
            )

        def do_GET(self):
            if not self.authorized():
                return self.json(401, {"error": {"message": "authentication_required"}})
            if self.path != "/v1/models":
                return self.json(404, {"error": {"message": "not_found"}})
            self.json(
                200,
                {
                    "object": "list",
                    "data": [
                        {
                            "id": "curie-ai-scout",
                            "object": "model",
                            "owned_by": "local",
                        },
                        {
                            "id": "curie-specialist",
                            "object": "model",
                            "owned_by": "local",
                        },
                    ],
                },
            )

        def do_POST(self):
            if not self.authorized():
                return self.json(401, {"error": {"message": "authentication_required"}})
            if self.path != "/v1/chat/completions":
                return self.json(404, {"error": {"message": "not_found"}})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 65536:
                    raise ValueError("packet_too_large")
                payload = json.loads(self.rfile.read(size))
                messages = ranking_messages(payload)
            except (ValueError, TypeError, AttributeError):
                return self.json(400, {"error": {"message": "invalid_ranking_packet"}})
            if not gate.acquire(blocking=False):
                return self.json(429, {"error": {"message": "scout_busy"}})
            try:
                callback = (
                    specialist_ask if payload["model"] == "curie-specialist" else ask
                )
                if callback is None:
                    raise ValueError("specialist_not_configured")
                text = callback(messages)
            except TimeoutError:
                return self.json(503, {"error": {"message": "local_inference_timeout"}})
            except Exception:
                return self.json(
                    503, {"error": {"message": "local_inference_unavailable"}}
                )
            finally:
                gate.release()
            common = {
                "id": "scout-local",
                "created": int(time.time()),
                "model": payload["model"],
            }
            if payload.get("stream"):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                for delta, finish in [
                    ({"role": "assistant", "content": text}, None),
                    ({}, "stop"),
                ]:
                    event = {
                        **common,
                        "object": "chat.completion.chunk",
                        "choices": [
                            {"index": 0, "delta": delta, "finish_reason": finish}
                        ],
                    }
                    self.wfile.write(("data: " + json.dumps(event) + "\n\n").encode())
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
            else:
                self.json(
                    200,
                    {
                        **common,
                        "object": "chat.completion",
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": text},
                                "finish_reason": "stop",
                            }
                        ],
                    },
                )

    return Handler


def ask_shared_model(messages):
    from llm.chat_prompt import ChatPrompt
    from llm.local_gateway import request

    prompt = ChatPrompt(
        "\n".join(item["content"] for item in messages), messages, json_output=True
    )
    return request(
        prompt, role="fast", temperature=0.1, max_tokens=128, owner_scope="ai-scout"
    )


def ask_shared_specialist(messages):
    from llm.chat_prompt import ChatPrompt
    from llm.local_gateway import request

    # Dedicated advisory endpoint replaces the CLI coding-harness system prompt.
    system = (
        (Path(__file__).resolve().parents[1] / "config/letta-curie-specialist.md")
        .read_text()
        .split("---", 2)[-1]
        .strip()
    )
    messages = [{"role": "system", "content": system}] + [
        item for item in messages if item["role"] != "system"
    ]
    prompt = ChatPrompt("\n".join(item["content"] for item in messages), messages)
    return request(
        prompt,
        role="fast",
        temperature=0.1,
        max_tokens=256,
        owner_scope="letta-specialist",
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--socket", required=True)
    parser.add_argument("--token-file", required=True)
    parser.add_argument("--port", type=int, default=18789)
    args = parser.parse_args()
    secret_path = Path(args.token_file)
    if (
        secret_path.is_symlink()
        or secret_path.stat().st_mode & 0o077
        or secret_path.stat().st_uid != os.getuid()
    ):
        raise SystemExit("Model bridge key must be a private owner-only file")
    token = secret_path.read_text().strip()
    if len(token) < 32:
        raise SystemExit("Invalid model bridge key")
    os.environ["CURIE_INFERENCE_SOCKET"] = args.socket
    os.environ["CURIE_INFERENCE_TIMEOUT_SECONDS"] = "55"

    ThreadingHTTPServer(
        ("127.0.0.1", args.port),
        make_handler(token, ask_shared_model, ask_shared_specialist),
    ).serve_forever()


if __name__ == "__main__":
    main()
