from unittest.mock import patch

from llm import accelerators


def test_auto_selects_full_gpu_offload_when_render_device_is_available(monkeypatch):
    monkeypatch.setenv("LLM_ACCELERATOR_MODE", "auto")
    monkeypatch.setenv("LLM_GPU_LAYERS", "auto")
    with patch.object(accelerators, "gpu_available", return_value=True):
        assert accelerators.select_llama_gpu_layers() == -1


def test_auto_falls_back_to_cpu_without_render_device(monkeypatch):
    monkeypatch.setenv("LLM_ACCELERATOR_MODE", "auto")
    monkeypatch.setenv("LLM_GPU_LAYERS", "auto")
    with patch.object(accelerators, "gpu_available", return_value=False):
        assert accelerators.select_llama_gpu_layers() == 0


def test_explicit_cpu_wins_even_when_gpu_exists(monkeypatch):
    monkeypatch.setenv("LLM_ACCELERATOR_MODE", "cpu")
    monkeypatch.setenv("LLM_GPU_LAYERS", "auto")
    with patch.object(accelerators, "gpu_available", return_value=True):
        assert accelerators.select_llama_gpu_layers() == 0


def test_auto_routes_only_lightweight_chat_to_npu(monkeypatch):
    monkeypatch.setenv("LLM_ACCELERATOR_MODE", "auto")
    monkeypatch.setenv("LLM_NPU_ROUTING", "simple")
    with patch.object(accelerators, "npu_available", return_value=True):
        assert accelerators.should_use_npu(
            "System instructions\nUser: Bonjour!\nAssistant:"
        )
        assert not accelerators.should_use_npu(
            "System instructions\nUser: Analyze this database migration\nAssistant:"
        )


def test_npu_all_policy_routes_substantive_work(monkeypatch):
    monkeypatch.setenv("LLM_ACCELERATOR_MODE", "auto")
    monkeypatch.setenv("LLM_NPU_ROUTING", "all")
    with patch.object(accelerators, "npu_available", return_value=True):
        assert accelerators.should_use_npu(
            "User: Explain quantum tunneling\nAssistant:"
        )
