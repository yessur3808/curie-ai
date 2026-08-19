"""Runtime hardware discovery and direct accelerator backends.

The GGUF path uses llama.cpp for CPU, GPU, or CPU+GPU execution. Ryzen AI
NPU requests use FastFlowLM directly; Lemonade Server is deliberately not a
dependency. In ``auto`` mode the smaller NPU model handles lightweight chat
while the stronger GGUF model handles substantive work on GPU (or CPU when
GPU initialization is unavailable).
"""

from __future__ import annotations

import atexit
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import threading
import time
from typing import Optional

import requests

logger = logging.getLogger(__name__)

_FLM_DEFAULT = Path.home() / ".cache/lemonade-npu/bin/flm/npu/flm"
_flm_process: Optional[subprocess.Popen] = None
_flm_lock = threading.Lock()


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _device_accessible(path: str) -> bool:
    return os.path.exists(path) and os.access(path, os.R_OK | os.W_OK)


def gpu_available() -> bool:
    """Return whether a real DRM render device is accessible."""
    return _device_accessible("/dev/dri/renderD128")


def _flm_binary() -> Path:
    return Path(_env("LLM_NPU_BINARY", str(_FLM_DEFAULT)))


def npu_available() -> bool:
    """Return whether the Ryzen AI device and direct runtime are accessible."""
    return _device_accessible("/dev/accel/accel0") and _flm_binary().is_file()


def hardware_status() -> dict:
    """Return a serializable snapshot for diagnostics and CLI reporting."""
    mode = _env("LLM_ACCELERATOR_MODE", "auto").lower()
    return {
        "mode": mode,
        "cpu": True,
        "gpu": gpu_available(),
        "npu": npu_available(),
        "llama_gpu_layers": select_llama_gpu_layers(),
        "npu_model": _env("LLM_NPU_MODEL", "qwen3-it:4b"),
        "npu_routing": _env("LLM_NPU_ROUTING", "simple").lower(),
    }


def select_llama_gpu_layers() -> int:
    """Choose llama.cpp offload according to policy and device availability.

    ``-1`` asks llama.cpp to offload every supported layer. Because an iGPU
    uses unified memory, llama.cpp still uses the CPU for orchestration and
    unsupported operations, giving an effective CPU+iGPU hybrid path.
    """
    configured = _env("LLM_GPU_LAYERS", "auto").lower()
    if configured not in {"", "auto"}:
        try:
            return int(configured)
        except ValueError:
            logger.warning("Invalid LLM_GPU_LAYERS=%r; using auto", configured)

    mode = _env("LLM_ACCELERATOR_MODE", "auto").lower()
    if mode in {"cpu", "npu"}:
        return 0
    return -1 if gpu_available() else 0


def _current_user_text(prompt: str) -> str:
    matches = re.findall(r"(?:^|\n)User:\s*(.*?)(?=\nAssistant:|\Z)", prompt, re.S)
    return matches[-1].strip() if matches else prompt.strip()


_SIMPLE_CHAT = re.compile(
    r"^(?:hi+|hello+|hey+|bonjour|thanks?|thank you|goodbye|bye|"
    r"how are you|who are you|what(?:'s| is) your name)[.!? ]*$",
    re.I,
)


def should_use_npu(prompt: str) -> bool:
    """Route according to explicit policy or lightweight auto classification."""
    if not npu_available():
        return False
    mode = _env("LLM_ACCELERATOR_MODE", "auto").lower()
    routing = _env("LLM_NPU_ROUTING", "simple").lower()
    if mode == "npu" or routing == "all":
        return True
    if mode in {"cpu", "gpu"} or routing in {"off", "false", "none"}:
        return False
    return bool(_SIMPLE_CHAT.fullmatch(_current_user_text(prompt)))


def _npu_base_url() -> str:
    return f"http://127.0.0.1:{int(_env('LLM_NPU_PORT', '13306'))}"


def _server_ready() -> bool:
    try:
        response = requests.get(f"{_npu_base_url()}/api/v1/health", timeout=1)
        if response.ok:
            return True
    except requests.RequestException:
        pass
    # FastFlowLM exposes an OpenAI-compatible models endpoint across versions.
    try:
        return requests.get(f"{_npu_base_url()}/v1/models", timeout=1).ok
    except requests.RequestException:
        return False


def _stop_fastflow() -> None:
    global _flm_process
    if _flm_process and _flm_process.poll() is None:
        _flm_process.terminate()
        try:
            _flm_process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            _flm_process.kill()
    _flm_process = None


atexit.register(_stop_fastflow)


def ensure_fastflow_server() -> bool:
    """Start the direct NPU runtime lazily, without Lemonade Server."""
    global _flm_process
    if not npu_available():
        return False
    if _server_ready():
        return True

    with _flm_lock:
        if _server_ready():
            return True
        if _flm_process and _flm_process.poll() is None:
            return False

        command = [
            str(_flm_binary()),
            "serve",
            _env("LLM_NPU_MODEL", "qwen3-it:4b"),
            "--host",
            "127.0.0.1",
            "--port",
            _env("LLM_NPU_PORT", "13306"),
            "--pmode",
            _env("LLM_NPU_POWER_MODE", "performance"),
            "--ctx-len",
            _env("LLM_NPU_CONTEXT_SIZE", "16384"),
            "--quiet",
        ]
        try:
            _flm_process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            logger.warning("Could not start direct NPU runtime: %s", exc)
            return False

        deadline = time.monotonic() + float(_env("LLM_NPU_START_TIMEOUT", "90"))
        while time.monotonic() < deadline:
            if _flm_process.poll() is not None:
                logger.warning("Direct NPU runtime exited during startup")
                return False
            if _server_ready():
                logger.info("Direct FastFlowLM NPU runtime is ready")
                return True
            time.sleep(0.5)
    return False


def ask_npu(
    prompt: str, temperature: float, max_tokens: Optional[int]
) -> Optional[str]:
    """Generate through FastFlowLM's OpenAI-compatible local endpoint."""
    if not ensure_fastflow_server():
        return None
    payload = {
        "model": _env("LLM_NPU_MODEL", "qwen3-it:4b"),
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens or int(_env("LLM_NPU_MAX_TOKENS", "1024")),
        "stream": False,
    }
    try:
        response = requests.post(
            f"{_npu_base_url()}/v1/chat/completions",
            json=payload,
            timeout=float(_env("LLM_NPU_REQUEST_TIMEOUT", "180")),
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"].strip()
    except (
        requests.RequestException,
        KeyError,
        IndexError,
        TypeError,
        json.JSONDecodeError,
    ) as exc:
        logger.warning("NPU inference failed; falling back: %s", exc)
        return None
