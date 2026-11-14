#!/usr/bin/env python3
"""Confirm hardware rendering and remote access; otherwise fall back once."""

import json
from pathlib import Path
import subprocess
import sys
import urllib.request

ROOT = Path("/var/lib/curie-recovery")


def run(args):
    try:
        result = subprocess.run(args, capture_output=True, text=True, timeout=25)
        return result.returncode, result.stdout
    except (OSError, subprocess.TimeoutExpired):
        return 124, ""


def main():
    if "curie_gpu_test=1" not in Path("/proc/cmdline").read_text():
        return
    ROOT.mkdir(parents=True, exist_ok=True)
    boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
    record_path = ROOT / "gpu-validation.json"
    if "--fallback" in sys.argv:
        try:
            evidence = json.loads(record_path.read_text())
            if evidence.get("passed") and evidence.get("boot_id") == boot_id:
                return
        except (OSError, ValueError):
            pass
        # grub-reboot has consumed the test entry: next boot is the safe default.
        run(["/usr/bin/systemctl", "reboot"])
        return
    render = bool(list(Path("/dev/dri").glob("renderD*")))
    driver = any(
        (path / "device/driver").resolve().name == "amdgpu"
        for path in Path("/sys/class/drm").glob("card[0-9]")
    )
    code, output = run(["/usr/bin/vulkaninfo", "--summary"])
    hardware = code == 0 and any(
        "devicename" in line.lower()
        and ("amd" in line.lower() or "radeon" in line.lower())
        and "llvmpipe" not in line.lower()
        for line in output.splitlines()
    )
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open("http://100.104.252.183:3773/", timeout=10) as response:
            reachable = response.status == 200
    except Exception:
        reachable = False
    evidence = dict(
        boot_id=boot_id,
        kernel=Path("/proc/sys/kernel/osrelease").read_text().strip(),
        render=render,
        amdgpu_driver=driver,
        hardware_vulkan=hardware,
        remote_ui=reachable,
        passed=render and driver and hardware and reachable,
    )
    temporary = record_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(evidence, indent=2) + "\n")
    temporary.chmod(0o644)
    temporary.replace(record_path)
    print(json.dumps(evidence))


if __name__ == "__main__":
    main()
