from types import SimpleNamespace
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.proactive_messaging import ProactiveMessagingService


@pytest.mark.asyncio
@pytest.mark.parametrize("has_discovery", [False, True])
async def test_opportunity_mode_requires_content_not_a_clock_tick(has_discovery):
    service = ProactiveMessagingService(SimpleNamespace(persona={}))
    profile = {
        "proactive_messaging_enabled": True,
        "proactive_opportunity_only": True,
        "proactive_style": "companion",
        "proactive_concierge_enabled": True,
        "proactive_quiet_hours": {"start": 0, "end": 0},
    }
    sessions = MagicMock()
    sessions.get_history.return_value = []
    discovery = {
        "message": "A new discovery about your research topic, with sources.",
        "topic": "research discovery",
        "kind": "tracked_goal",
        "reason": "A new relevant research finding is ready.",
        "confidence": 0.95,
        "usefulness": 0.9,
        "evidence": [
            {"source": "research", "reference": "finding-1", "confidence": 0.95}
        ],
    }
    with (
        patch(
            "services.proactive_messaging.UserManager.get_user_profile",
            return_value=profile,
        ),
        patch(
            "services.proactive_messaging.get_session_manager", return_value=sessions
        ),
        patch.object(
            service._autonomous_learning,
            "share_candidate",
            return_value=discovery if has_discovery else None,
        ),
        patch(
            "connectors.google_oauth.calendar_list_events",
            new=AsyncMock(return_value=[]),
        ),
        patch("services.personal_ops.daily_briefing_candidate", return_value=None),
        patch.object(
            service, "_generate_conversational_candidate", new=AsyncMock()
        ) as companion,
    ):
        result = await service._generate_proactive_message("owner", "telegram")
    assert result == (discovery["message"] if has_discovery else "")
    companion.assert_not_called()


@pytest.mark.asyncio
async def test_fresh_opportunity_does_not_wait_for_checkin_cadence():
    service = ProactiveMessagingService(
        SimpleNamespace(persona={}), connectors={"telegram": object()}
    )
    now = datetime.now(timezone.utc).isoformat()
    profile = {
        "proactive_messaging_enabled": True,
        "proactive_opportunity_only": True,
        "proactive_awaiting_response": True,
        "proactive_interval_hours": 24,
        "last_proactive_at": now,
        "last_proactive_generation_at": now,
        "proactive_quiet_hours": {"start": 0, "end": 0},
    }
    with (
        patch(
            "services.proactive_messaging.UserManager.get_user_profile",
            return_value=profile,
        ),
        patch("services.proactive_messaging.UserManager.update_user_profile"),
        patch("services.proactive_messaging.get_session_manager"),
        patch.object(
            service,
            "_generate_proactive_message",
            new=AsyncMock(return_value="Useful new discovery"),
        ),
        patch.object(
            service, "_send_via_connector", new=AsyncMock(return_value=True)
        ) as send,
    ):
        await service._maybe_send_proactive_message(
            {"internal_id": "owner", "platform": "telegram", "external_user_id": "test"}
        )
    send.assert_awaited_once()
