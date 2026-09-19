from unittest.mock import patch

from llm import accelerators


def test_generation_controls_disable_qwen_thinking(monkeypatch):
    from llm.manager import _apply_generation_controls

    monkeypatch.setenv("LLM_DISABLE_THINKING", "true")
    assert _apply_generation_controls("User: hello").endswith("/no_think")
    assert _apply_generation_controls("User: hello\n/no_think").count("/no_think") == 1
    controlled = _apply_generation_controls("User: hello\nAssistant:")
    assert controlled.endswith("/no_think\nAssistant:")


def test_generation_controls_can_be_disabled(monkeypatch):
    from llm.manager import _apply_generation_controls

    monkeypatch.setenv("LLM_DISABLE_THINKING", "false")
    assert _apply_generation_controls("User: hello") == "User: hello"


def test_non_thinking_instruct_model_needs_no_control(monkeypatch):
    from llm.manager import _apply_generation_controls

    monkeypatch.setenv("LLM_DISABLE_THINKING", "true")
    prompt = "User: hello\nAssistant:"
    assert (
        _apply_generation_controls(prompt, "Qwen3-30B-A3B-Instruct-2507-q4_k_m.gguf")
        == prompt
    )


def test_non_qwen_models_do_not_receive_qwen_control_tokens(monkeypatch):
    from llm.manager import _apply_generation_controls

    monkeypatch.setenv("LLM_DISABLE_THINKING", "true")
    prompt = "User: hello\nAssistant:"
    assert _apply_generation_controls(prompt, "gpt-oss-20b-MXFP4.gguf") == prompt
    assert _apply_generation_controls(prompt, "Mistral-Small-3.2.gguf") == prompt


def test_direct_model_output_never_exposes_thinking_tags():
    from llm.manager import _sanity_filter_response

    assert (
        _sanity_filter_response("<think>secret work</think>\nFinal answer")
        == "Final answer"
    )


def test_harmony_output_returns_only_final_channel():
    from llm.manager import clean_assistant_reply

    output = (
        "<|channel|>analysis<|message|>private calculation"
        "<|channel|>final<|message|>16:10.<|end|>"
    )
    assert clean_assistant_reply(output) == "16:10."
    assert (
        clean_assistant_reply(
            "<|channel|>analysis<|message|>unfinished private calculation"
        )
        == ""
    )


def test_tagged_prompt_is_converted_to_chat_messages():
    from llm.manager import _prompt_to_chat_messages

    messages = _prompt_to_chat_messages(
        "System: Be accurate.\nAssistant: Earlier reply.\nUser: Current question\nAssistant:"
    )
    assert messages[0] == {
        "role": "system",
        "content": "Be accurate.\nAssistant: Earlier reply.",
    }
    assert messages[1] == {"role": "user", "content": "Current question"}


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


def test_fastflow_does_not_start_when_npu_is_owned_by_another_server():
    with (
        patch.object(accelerators, "npu_available", return_value=True),
        patch.object(accelerators, "_server_ready", return_value=False),
        patch.object(accelerators, "_npu_owned_by_another_server", return_value=True),
        patch.object(accelerators.subprocess, "Popen") as popen,
    ):
        assert accelerators.ensure_fastflow_server() is False
        popen.assert_not_called()
