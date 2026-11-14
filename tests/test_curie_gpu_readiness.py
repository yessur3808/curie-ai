from pathlib import Path

import pytest

from scripts.curie_gpu_readiness import KERNEL, validate


@pytest.fixture
def evidence():
    return dict(
        boot_id="current",
        kernel=KERNEL,
        render=True,
        amdgpu_driver=True,
        hardware_vulkan=True,
        remote_ui=True,
        passed=True,
    )


def verify(evidence, **kwargs):
    values = dict(
        boot_id="current",
        kernel=KERNEL,
        cmdline="ro curie_gpu_test=1 panic=30",
        age_seconds=30,
    )
    values.update(kwargs)
    return validate(evidence, **values)


def test_current_successful_test_boot_can_be_promoted(evidence):
    verify(evidence)


@pytest.mark.parametrize(
    "key", ["render", "amdgpu_driver", "hardware_vulkan", "remote_ui", "passed"]
)
@pytest.mark.parametrize("value", [False, None, "true", 1])
def test_every_check_requires_true_boolean(evidence, key, value):
    evidence[key] = value
    with pytest.raises(ValueError):
        verify(evidence)


@pytest.mark.parametrize(
    "override",
    [
        {"boot_id": "previous"},
        {"kernel": "7.0.0-42-generic"},
        {"cmdline": "ro nomodeset"},
        {"cmdline": "ro other_curie_gpu_test=1"},
        {"age_seconds": 181},
        {"age_seconds": -1},
    ],
)
def test_stale_wrong_boot_or_non_test_is_rejected(evidence, override):
    with pytest.raises(ValueError):
        verify(evidence, **override)


@pytest.mark.parametrize("invalid", [[], None, "passed", {}])
def test_invalid_records_fail_closed(invalid):
    with pytest.raises(ValueError):
        verify(invalid)


def test_production_entry_does_not_disable_recovery_as_a_test_boot():
    root = Path(__file__).parents[1]
    entries = (root / "deploy/43_curie_gpu_test").read_text()
    production = entries.split("--id curie-gpu-boot", 1)[1]
    safe = entries.split("--id curie-safe-boot", 1)[1].split("}", 1)[0]
    assert f"/vmlinuz-{KERNEL}" in production
    assert "curie_gpu_test=1" not in production
    assert "nomodeset" not in production
    assert "nomodeset" in safe
    assert (
        "GRUB_DEFAULT=curie-gpu-boot"
        in (root / "deploy/99-curie-gpu-boot.cfg").read_text()
    )
