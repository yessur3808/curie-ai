from scripts.curie_recovery import choose_actions


def test_clock_rollback_does_not_reset_restart_budget():
    state = {
        "restart_history": {"curie-main": [3000]},
        "consecutive_failures": {"curie-main": 4},
    }
    assert not choose_actions(state, {"curie-main": "errored"}, 2000, applying=True)
    assert state["restart_history"]["curie-main"] == [3000]


def test_other_instances_are_never_restarted():
    state = {}
    for now in (1, 200, 400, 600):
        assert not choose_actions(
            state,
            {"andreja-main": "errored", "unrelated": "errored"},
            now,
            applying=True,
        )


def test_corrupt_state_fails_closed_without_pm2(monkeypatch, tmp_path):
    from scripts import curie_recovery as recovery

    monkeypatch.setattr(recovery.os, "geteuid", lambda: 1000)
    monkeypatch.setattr(recovery, "USER_STATE", tmp_path)
    (tmp_path / "status.json").write_text("{corrupt")

    def forbidden(*args):
        raise AssertionError("Must not contact PM2 after state corruption")

    monkeypatch.setattr(recovery, "pm2", forbidden)
    assert recovery.tick(apply=True)["status"] == "blocked"
    assert (tmp_path / "status.json").read_text() == "{corrupt"


def test_missing_recovery_report_does_not_claim_readiness(monkeypatch, tmp_path):
    from services import self_recovery

    monkeypatch.setattr(self_recovery, "PATHS", (tmp_path / "missing.json",))
    assert not self_recovery.recovery_status()["ready"]
    assert "can't claim" in self_recovery.handle_recovery_command("/selfheal")


def test_respects_stops_missing_and_healthy():
    state = {}
    for now in (1, 121, 241, 361):
        assert (
            choose_actions(
                state,
                {"curie-main": "stopped", "curie-inference": "online"},
                now,
                applying=True,
            )
            == []
        )


def test_requires_persistent_failure_and_limits_restarts():
    state = {}
    status = {"curie-main": "errored"}
    assert not choose_actions(state, status, 1, applying=True)
    assert not choose_actions(state, status, 121, applying=True)
    assert choose_actions(state, status, 241, applying=True) == [
        {"action": "restart", "target": "curie-main"}
    ]
    assert not choose_actions(state, status, 400, applying=True)
    assert choose_actions(state, status, 1200, applying=True)
    assert choose_actions(state, status, 2200, applying=True)
    assert not choose_actions(state, status, 4000, applying=True)


def test_observation_does_not_execute_or_consume_attempts():
    state = {}
    for now in (1, 121, 241, 400):
        assert not choose_actions(state, {"curie-main": "errored"}, now)
    assert state["restart_history"]["curie-main"] == []


def test_reboot_requires_kernel_fault_and_budget_and_uptime():
    state = {
        "restart_history": {"curie-inference": [100, 1100, 2100]},
        "consecutive_failures": {"curie-inference": 5},
    }
    status = {"curie-inference": "errored"}
    assert not choose_actions(
        state, status, 4000, applying=True, reboot_enabled=True, uptime=4000
    )
    assert not choose_actions(
        state,
        status,
        4000,
        applying=True,
        reboot_enabled=True,
        uptime=500,
        gpu_fault=True,
    )
    assert choose_actions(
        state,
        status,
        4000,
        applying=True,
        reboot_enabled=True,
        uptime=4000,
        gpu_fault=True,
    ) == [{"action": "reboot", "target": "host"}]
    assert not choose_actions(
        state,
        status,
        5000,
        applying=True,
        reboot_enabled=True,
        uptime=4000,
        gpu_fault=True,
    )
