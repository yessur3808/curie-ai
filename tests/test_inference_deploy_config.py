import json
from pathlib import Path
import shutil
import subprocess

import pytest


def test_inference_worker_receives_npu_configuration(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to evaluate the PM2 ecosystem config")

    (tmp_path / ".env").write_text(
        "\n".join(
            (
                "LLM_MAX_LOADED_MODELS=1",
                "LLM_NPU_ROUTING=simple",
                "LLM_NPU_MODEL=qwen3.5:4b",
                "LLM_NPU_PORT=13306",
                "LLM_NPU_POWER_MODE=performance",
                "LLM_NPU_CONTEXT_SIZE=16384",
                'LLM_THREADS_BY_MODEL={"critic.gguf":8}',
                "LLM_FLASH_ATTENTION=auto",
            )
        ),
        encoding="utf-8",
    )
    config = Path(__file__).parents[1] / "deploy" / "ecosystem.inference.config.cjs"
    script = (
        f"const config = require({json.dumps(str(config))});"
        "process.stdout.write(JSON.stringify(config.apps[0].env));"
    )
    result = subprocess.run(
        [node, "-e", script],
        check=True,
        capture_output=True,
        text=True,
        env={"CURIE_ROOT": str(tmp_path)},
    )

    worker_env = json.loads(result.stdout)
    assert worker_env["LLM_MAX_LOADED_MODELS"] == "1"
    assert worker_env["LLM_NPU_ROUTING"] == "simple"
    assert worker_env["LLM_NPU_MODEL"] == "qwen3.5:4b"
    assert worker_env["LLM_NPU_PORT"] == "13306"
    assert worker_env["LLM_NPU_POWER_MODE"] == "performance"
    assert worker_env["LLM_NPU_CONTEXT_SIZE"] == "16384"
    assert worker_env["LLM_THREADS_BY_MODEL"] == '{"critic.gguf":8}'
    assert worker_env["LLM_FLASH_ATTENTION"] == "auto"


def test_inference_worker_defaults_to_disabled_npu_routing(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to evaluate the PM2 ecosystem config")

    (tmp_path / ".env").write_text("LLM_MAX_LOADED_MODELS=1\n", encoding="utf-8")
    config = Path(__file__).parents[1] / "deploy" / "ecosystem.inference.config.cjs"
    script = (
        f"const config = require({json.dumps(str(config))});"
        "process.stdout.write(JSON.stringify(config.apps[0].env));"
    )
    result = subprocess.run(
        [node, "-e", script],
        check=True,
        capture_output=True,
        text=True,
        env={"CURIE_ROOT": str(tmp_path)},
    )

    assert json.loads(result.stdout)["LLM_NPU_ROUTING"] == "off"


def test_dashboard_adapters_share_current_model_profile_and_socket(tmp_path):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js is required to evaluate the PM2 ecosystem config")

    socket_path = tmp_path / "private" / "worker.sock"
    (tmp_path / ".env").write_text(
        "\n".join(
            (
                "LLM_MODELS=general.gguf,fast.gguf,critic.gguf",
                "LLM_GENERAL_MODEL=general.gguf",
                "LLM_FAST_MODEL=fast.gguf",
                "LLM_REASONING_MODEL=general.gguf",
                "LLM_CODING_MODEL=general.gguf",
                "LLM_CRITIC_MODEL=critic.gguf",
                "LLM_MAX_LOADED_MODELS=3",
                f"CURIE_INFERENCE_SOCKET={socket_path}",
            )
        ),
        encoding="utf-8",
    )
    config = Path(__file__).parents[1] / "deploy" / "ecosystem.trained-voice.config.cjs"
    script = (
        f"const config = require({json.dumps(str(config))});"
        "process.stdout.write(JSON.stringify(config.apps));"
    )
    result = subprocess.run(
        [node, "-e", script],
        check=True,
        capture_output=True,
        text=True,
        env={"CURIE_ROOT": str(tmp_path)},
    )

    apps = json.loads(result.stdout)
    assert [item["name"] for item in apps] == [
        "curie-dashboard-api",
        "curie-dashboard-api-recovery",
    ]
    assert [item["env"]["CURIE_API_PORT"] for item in apps] == ["8010", "8011"]
    for item in apps:
        env = item["env"]
        assert env["CURIE_INFERENCE_SOCKET"] == str(socket_path)
        assert env["LLM_GENERAL_MODEL"] == "general.gguf"
        assert env["LLM_FAST_MODEL"] == "fast.gguf"
        assert env["LLM_CRITIC_MODEL"] == "critic.gguf"
        assert env["LLM_MAX_LOADED_MODELS"] == "3"
