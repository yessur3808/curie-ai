"""Private Unix-socket model worker shared by all Curie application processes.

The directory and socket are owner-only. Requests have bounded frames and timeouts,
and inference is serialized inside the worker, which owns the only model cache.
No tools, credentials, conversation persistence, or external providers live here.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import socketserver
import stat
import struct

from llm.chat_prompt import ChatPrompt, as_chat_prompt

MAX_FRAME = 2 * 1024 * 1024


def _read_exact(connection, size):
    chunks = bytearray()
    while len(chunks) < size:
        chunk = connection.recv(size - len(chunks))
        if not chunk:
            raise ConnectionError("incomplete_gateway_frame")
        chunks.extend(chunk)
    return bytes(chunks)


def receive(connection):
    size = struct.unpack("!I", _read_exact(connection, 4))[0]
    if size > MAX_FRAME:
        raise ValueError("gateway_frame_too_large")
    return json.loads(_read_exact(connection, size))


def send(connection, value):
    payload = json.dumps(value, ensure_ascii=False).encode()
    if len(payload) > MAX_FRAME:
        raise ValueError("gateway_frame_too_large")
    connection.sendall(struct.pack("!I", len(payload)) + payload)


def _check_socket(path):
    info = Path(path).lstat()
    parent = Path(path).parent.stat()
    if (
        not stat.S_ISSOCK(info.st_mode)
        or info.st_uid != os.getuid()
        or info.st_mode & 0o077
        or parent.st_uid != os.getuid()
        or parent.st_mode & 0o077
    ):
        raise PermissionError("gateway_socket_not_private")


def request(prompt, **kwargs):
    """Fail closed if the gateway is unavailable; never load a duplicate model."""
    path = os.environ["CURIE_INFERENCE_SOCKET"]
    _check_socket(path)
    prompt = as_chat_prompt(prompt)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(
            float(os.getenv("CURIE_INFERENCE_TIMEOUT_SECONDS", "240"))
        )
        connection.connect(path)
        send(
            connection,
            {"messages": prompt.messages, "evidence": prompt.evidence, **kwargs},
        )
        result = receive(connection)
    if not isinstance(result, dict) or not isinstance(result.get("text"), str):
        raise ValueError("invalid_gateway_response")
    return result["text"]


def status():
    """Owner-only operational metadata, never prompts or responses."""
    path = os.environ["CURIE_INFERENCE_SOCKET"]
    _check_socket(path)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
        connection.settimeout(10)
        connection.connect(path)
        send(connection, {"operation": "status"})
        return receive(connection)


def execute(payload, ask):
    if not isinstance(payload, dict):
        raise ValueError("invalid_gateway_request")
    messages = payload.get("messages")
    if not isinstance(messages, list) or not 1 <= len(messages) <= 128:
        raise ValueError("invalid_gateway_messages")
    for item in messages:
        if (
            not isinstance(item, dict)
            or item.get("role") not in {"system", "user", "assistant"}
            or not isinstance(item.get("content"), str)
        ):
            raise ValueError("invalid_gateway_message")
    name = payload.get("model_name")
    if name and (
        not isinstance(name, str)
        or Path(name).name != name
        or not name.endswith(".gguf")
    ):
        raise ValueError("invalid_gateway_model")
    role = payload.get("role")
    if role is not None and role not in {
        "general",
        "fast",
        "reasoning",
        "deep_reasoning",
        "coding",
        "critic",
        "vision",
        "npu",
    }:
        raise ValueError("invalid_gateway_role")
    temperature = float(payload.get("temperature", 0.3))
    tokens = payload.get("max_tokens")
    if not 0 <= temperature <= 2 or (
        tokens is not None and (not isinstance(tokens, int) or not 1 <= tokens <= 8192)
    ):
        raise ValueError("invalid_gateway_budget")
    text = "\n".join(m["role"] + ": " + m["content"] for m in messages)
    prompt = ChatPrompt(text, messages, evidence=payload.get("evidence", []))
    return ask(
        prompt,
        model_name=name,
        role=role,
        temperature=temperature,
        max_tokens=tokens,
        owner_scope=payload.get("owner_scope"),
    )


class Gateway(socketserver.UnixStreamServer):
    request_queue_size = 16


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(240)
        try:
            payload = receive(self.request)
            from llm.manager import ask_llm, llama_models_cache, MAX_MODELS_IN_CACHE

            if isinstance(payload, dict) and payload.get("operation") == "status":
                send(
                    self.request,
                    {
                        "pid": os.getpid(),
                        "loaded_models": list(llama_models_cache),
                        "capacity": MAX_MODELS_IN_CACHE,
                        "gpu_capacity": 1,
                        "gpu_models": [
                            name
                            for name, model in llama_models_cache.items()
                            if getattr(model, "_curie_gpu_layers", 0) != 0
                        ],
                        "serialized": True,
                    },
                )
                return

            result = execute(payload, ask_llm)
            send(self.request, {"text": result})
        except (OSError, ValueError, TypeError, KeyError):
            try:
                send(self.request, {"text": "[Error: local model request failed]"})
            except OSError:
                pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--socket", required=True)
    args = parser.parse_args()
    path = Path(args.socket).absolute()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    parent = path.parent.stat()
    if parent.st_uid != os.getuid() or parent.st_mode & 0o077:
        raise PermissionError("gateway_directory_not_private")
    # Hold an exclusive lifetime lock. Never unlink another live worker's socket.
    import fcntl

    lock_fd = os.open(
        str(path) + ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600
    )
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if path.exists():
        _check_socket(path)
        path.unlink()
    os.environ["CURIE_INFERENCE_WORKER"] = "1"
    os.environ["CURIE_INFERENCE_SOCKET"] = str(path)
    os.umask(0o077)
    with Gateway(str(path), Handler) as server:
        print("Curie private inference gateway ready", flush=True)
        try:
            server.serve_forever(poll_interval=0.2)
        finally:
            path.unlink(missing_ok=True)
            os.close(lock_fd)


if __name__ == "__main__":
    main()
