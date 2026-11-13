import json
import os
from pathlib import Path
import shutil
import subprocess
import pytest

NODE = (
    shutil.which("node") or "/home/curlycoffee3808/.nvm/versions/node/v24.18.0/bin/node"
)


@pytest.mark.skipif(not Path(NODE).exists(), reason="Node required for PM2 config")
def test_named_pm2_instances_keep_independent_identity(tmp_path):
    shutil.copyfile("ecosystem.config.js", tmp_path / "ecosystem.config.js")
    (tmp_path / ".env").write_text(
        "TELEGRAM_BOT_TOKEN=curie-test\nPERSONA_FILE=curie.json\nCURIE_LOCAL_MEMORY_DB=curie.sqlite\n"
    )
    (tmp_path / "instances").mkdir()
    (tmp_path / "instances/andreja.env").write_text(
        "TELEGRAM_BOT_TOKEN=andreja-test\nPERSONA_FILE=andreja.json\nCURIE_LOCAL_MEMORY_DB=andreja.sqlite\nRUN_API=false\n"
    )

    def config(name):
        env = {**os.environ, "CURIE_INSTANCE": name}
        return subprocess.run(
            [
                NODE,
                "-e",
                'process.stdout.write(JSON.stringify(require("./ecosystem.config.js").apps[0]))',
            ],
            cwd=tmp_path,
            env=env,
            capture_output=True,
            text=True,
        )

    assert config("andreja").returncode != 0  # No shared-checkout fallback.
    c = json.loads(config("curie").stdout)
    (tmp_path / ".env").write_text(
        "CURIE_INSTANCE=andreja\nTELEGRAM_BOT_TOKEN=andreja-test\nPERSONA_FILE=andreja.json\nCURIE_LOCAL_MEMORY_DB=andreja.sqlite\n"
    )
    a = json.loads(config("andreja").stdout)
    assert a["name"] == "andreja-main" and c["name"] == "curie-main"
    for key in ("TELEGRAM_BOT_TOKEN", "PERSONA_FILE", "CURIE_LOCAL_MEMORY_DB"):
        assert a["env"][key] != c["env"][key]
    assert a["error_file"] != c["error_file"]
    assert config("../escape").returncode != 0
    assert config("missing").returncode != 0
