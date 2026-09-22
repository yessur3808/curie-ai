import importlib.util
import pathlib
import tempfile
import unittest


@unittest.skipUnless(
    importlib.util.find_spec("torch")
    and importlib.util.find_spec("safetensors")
    and importlib.util.find_spec("transformers"),
    "isolated voice runtime only",
)
class AdapterTests(unittest.TestCase):
    def test_saved_adapter_merge_matches_training_projection(self):
        import torch
        from transformers.pytorch_utils import Conv1D
        from services.trained_voice.voice_lora import ProjectionAdapter, save, merge
        from torch import nn

        base = Conv1D(6, 4)
        original = base.weight.detach().clone()
        adapter = ProjectionAdapter(base, rank=2, alpha=4)
        with torch.no_grad():
            adapter.b.normal_(0, 0.1)
        x = torch.randn(2, 3, 4)
        expected = adapter(x).detach()
        t3 = nn.Module()
        t3.tfmr = nn.Module()
        t3.tfmr.c_attn = base
        with tempfile.TemporaryDirectory() as folder:
            path = pathlib.Path(folder) / "adapter.safetensors"
            save({"c_attn": adapter}, path)
            merge(t3, path)
        self.assertTrue(torch.allclose(t3.tfmr.c_attn(x), expected, atol=1e-6))
        self.assertFalse(torch.equal(base.weight, original))

    def test_new_adapter_preserves_original_output(self):
        import torch
        from transformers.pytorch_utils import Conv1D
        from services.trained_voice.voice_lora import ProjectionAdapter

        base = Conv1D(6, 4)
        adapter = ProjectionAdapter(base, rank=2)
        x = torch.randn(2, 3, 4)
        self.assertTrue(torch.equal(base(x), adapter(x)))


if __name__ == "__main__":
    unittest.main()
