from datetime import datetime, timezone

import pytest

from services.oauth_state import consume_oauth_state, save_oauth_state


def _state(provider: str = "x", *, expires_in: int = 600) -> dict:
    return {
        "id": f"state-{provider}",
        "state": f"opaque-{provider}-state",
        "provider": provider,
        "verifier": "private-verifier",
        "used": False,
        "expires_at": datetime.now(timezone.utc).timestamp() + expires_in,
    }


def test_oauth_state_is_provider_bound_and_single_use():
    save_oauth_state("owner-a", _state("x"))

    with pytest.raises(PermissionError):
        consume_oauth_state("opaque-x-state", provider="google")

    owner, pending = consume_oauth_state("opaque-x-state", provider="x")
    assert owner == "owner-a"
    assert pending["used"] is True

    with pytest.raises(PermissionError):
        consume_oauth_state("opaque-x-state", provider="x")


def test_expired_oauth_state_cannot_be_consumed():
    save_oauth_state("owner-a", _state("google", expires_in=-1))

    with pytest.raises(PermissionError):
        consume_oauth_state("opaque-google-state", provider="google")
