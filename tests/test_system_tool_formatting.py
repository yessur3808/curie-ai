import asyncio

from agent.tooling.contracts import ToolContext
from agent.tooling.system_tools import HardwareTool


def test_hardware_tool_formats_chat_summary_instead_of_raw_json(monkeypatch):
    from llm import accelerators

    monkeypatch.setattr(
        accelerators,
        "hardware_status",
        lambda: {
            "mode": "auto",
            "cpu": True,
            "gpu": True,
            "npu": False,
            "llama_gpu_layers": -1,
            "npu_model": "qwen3-it:4b",
            "npu_routing": "simple",
        },
    )

    result = asyncio.run(
        HardwareTool().execute({}, ToolContext("owner", platform="telegram"))
    )

    assert result.text.startswith("**Hardware acceleration**\n\n")
    assert "- **GPU:** Available" in result.text
    assert "- **NPU:** Unavailable" in result.text
    assert "- **GPU offload:** All supported layers" in result.text
    assert "```json" not in result.text
