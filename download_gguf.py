import os
from huggingface_hub import snapshot_download

# Faster downloads (optional)
os.environ["HF_HUB_ENABLE_HF_TRANSFER"] = "1"

def dl(repo_id: str, out_dir: str, patterns):
    print(f"\n==> Downloading from {repo_id} to {out_dir}")
    snapshot_download(
        repo_id=repo_id,
        local_dir=out_dir,
        local_dir_use_symlinks=False,
        allow_patterns=patterns,
    )

# 1) Llama 3.3 70B Instruct (Q4_K_M)
dl(
    "lmstudio-community/Llama-3.3-70B-Instruct-GGUF",
    "./models/llama-3.3-70b-instruct-gguf",
    ["*Q4_K_M*.gguf"],
)

# 2) Qwen2.5-Coder 32B Instruct (Q4_K_M)
dl(
    "bartowski/Qwen2.5-Coder-32B-Instruct-GGUF",
    "./models/qwen2.5-coder-32b-instruct-gguf",
    ["*Q4_K_M*.gguf"],
)

# 3) Optional: Qwen2.5 32B Instruct (Q4_K_M)
dl(
    "bartowski/Qwen2.5-32B-Instruct-GGUF",
    "./models/qwen2.5-32b-instruct-gguf",
    ["*Q4_K_M*.gguf"],
)

print("\nAll done.")

# 4) Qwen3.5-9B (Q4_K_M)
dl(
    "unsloth/Qwen3.5-9B-GGUF",
    "./models",
    ["Qwen3.5-9B-Q4_K_M.gguf"],
)

# 5) Qwen3.5-4B (Q4_K_M)
dl(
    "unsloth/Qwen3.5-4B-GGUF",
    "./models",
    ["Qwen3.5-4B-Q4_K_M.gguf"],
)

# 6) Qwen3-Coder-30B-A3B-Instruct (IQ4_NL — SOTA coding/agentic model, fast MoE)
dl(
    "unsloth/Qwen3-Coder-30B-A3B-Instruct-GGUF",
    "./models",
    ["Qwen3-Coder-30B-A3B-Instruct-IQ4_NL.gguf"],
)
