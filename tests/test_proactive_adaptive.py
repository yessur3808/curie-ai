from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from services.proactive_messaging import (
    _COMPANION_FALLBACKS,
    ProactiveMessagingService,
    _companion_draft_prompt,
    _hour_in_quiet_window,
    _validate_model_companion_message,
)
from memory import local_store


def test_quiet_window_supports_overnight_and_disabled_window():
    assert _hour_in_quiet_window(23, 22, 8)
    assert _hour_in_quiet_window(7, 22, 8)
    assert not _hour_in_quiet_window(12, 22, 8)
    assert not _hour_in_quiet_window(12, 0, 0)


@pytest.mark.asyncio
async def test_connector_false_result_is_a_failed_delivery():
    service = ProactiveMessagingService(SimpleNamespace(persona={}))

    async def sender(_user, _message):
        return False

    assert not await service._send_via_connector(sender, "user", "hello")


@pytest.mark.asyncio
async def test_prediction_is_permission_seeking_message():
    service = ProactiveMessagingService(SimpleNamespace(persona={}))
    prediction = {
        "suggestion": "Would you like me to prepare a short study outline?",
        "reason": "You said you study every evening",
        "confidence": 0.9,
        "evidence_count": 2,
    }
    sessions = MagicMock()
    sessions.get_history.return_value = [
        {
            "role": "user",
            "content": "I study every evening",
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        {
            "role": "user",
            "content": "My daily study routine matters",
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
    ]
    with (
        patch(
            "services.proactive_messaging.UserManager.get_user_profile",
            return_value={"proactive_predictions_enabled": True},
        ),
        patch(
            "services.proactive_messaging.get_session_manager", return_value=sessions
        ),
        patch("memory.adaptive.generate_helpful_prediction", return_value=prediction),
    ):
        message = await service._generate_proactive_message("u1", "telegram")
    assert message.endswith("?")
    assert "Would you like me" in message


@pytest.mark.asyncio
async def test_successful_proactive_send_persists_cadence():
    calls = []

    async def connector(user, message):
        calls.append((user, message))
        return True

    service = ProactiveMessagingService(
        SimpleNamespace(persona={}), connectors={"telegram": connector}
    )
    profile = {
        "proactive_messaging_enabled": True,
        "proactive_interval_hours": 0,
        "proactive_quiet_hours": {"start": 0, "end": 0},
        "proactive_daily_max": 2,
        "timezone": "UTC",
    }
    sessions = MagicMock()
    with (
        patch(
            "services.proactive_messaging.UserManager.get_user_profile",
            return_value=profile,
        ),
        patch("services.proactive_messaging.UserManager.update_user_profile") as update,
        patch.object(
            service, "_generate_proactive_message", new=AsyncMock(return_value="Hello")
        ),
        patch(
            "services.proactive_messaging.get_session_manager", return_value=sessions
        ),
        patch("services.proactive_messaging.random.random", return_value=0.0),
    ):
        await service._maybe_send_proactive_message(
            {
                "internal_id": "u1",
                "platform": "telegram",
                "external_user_id": "42",
            }
        )
    assert calls == [("42", "Hello")]
    sessions.add_message.assert_called_once_with("telegram", "u1", "assistant", "Hello")
    stored = update.call_args.args[1]
    assert stored["proactive_count_today"] == 1
    assert "last_proactive_at" in stored


@pytest.mark.asyncio
async def test_null_timezone_falls_back_to_utc():
    calls = []

    async def connector(user, message):
        calls.append((user, message))
        return True

    service = ProactiveMessagingService(
        SimpleNamespace(persona={}), connectors={"telegram": connector}
    )
    profile = {
        "proactive_messaging_enabled": True,
        "proactive_interval_hours": 0,
        "proactive_quiet_hours": {"start": 0, "end": 0},
        "proactive_daily_max": 2,
        "timezone": None,
    }
    sessions = MagicMock()
    with (
        patch(
            "services.proactive_messaging.UserManager.get_user_profile",
            return_value=profile,
        ),
        patch("services.proactive_messaging.UserManager.update_user_profile"),
        patch.object(
            service,
            "_generate_proactive_message",
            new=AsyncMock(return_value="Bonjour"),
        ),
        patch(
            "services.proactive_messaging.get_session_manager", return_value=sessions
        ),
        patch("services.proactive_messaging.random.random", return_value=0.0),
    ):
        await service._maybe_send_proactive_message(
            {"internal_id": "u1", "platform": "telegram", "external_user_id": "42"}
        )
    assert calls == [("42", "Bonjour")]


@pytest.mark.asyncio
async def test_ungrounded_sensory_prediction_uses_neutral_checkin():
    service = ProactiveMessagingService(SimpleNamespace(persona={}))
    sessions = MagicMock()
    sessions.get_history.return_value = []
    prediction = {
        "suggestion": "Would you like another observation from today's sky?",
        "reason": "I saw a cloud pattern",
        "confidence": 0.99,
    }
    with (
        patch(
            "services.proactive_messaging.UserManager.get_user_profile",
            return_value={"proactive_predictions_enabled": True},
        ),
        patch(
            "services.proactive_messaging.get_session_manager", return_value=sessions
        ),
        patch("memory.adaptive.generate_helpful_prediction", return_value=prediction),
    ):
        message = await service._generate_proactive_message("u1", "telegram")
    assert "cloud pattern" not in message
    assert message


@pytest.mark.asyncio
async def test_recent_fallback_is_not_repeated_verbatim():
    service = ProactiveMessagingService(SimpleNamespace(persona={}))
    sessions = MagicMock()
    sessions.get_history.return_value = [
        {"role": "assistant", "content": "Salut, how’s your day going?"}
    ]
    with (
        patch(
            "services.proactive_messaging.UserManager.get_user_profile",
            return_value={"proactive_predictions_enabled": False},
        ),
        patch(
            "services.proactive_messaging.get_session_manager", return_value=sessions
        ),
    ):
        message = await service._generate_proactive_message("u1", "telegram")
    assert message != "Salut, how’s your day going?"


@pytest.mark.asyncio
async def test_recent_generation_attempt_skips_regeneration():
    service = ProactiveMessagingService(
        SimpleNamespace(persona={}), connectors={"telegram": AsyncMock()}
    )
    profile = {
        "proactive_messaging_enabled": True,
        "proactive_quiet_hours": {"start": 0, "end": 0},
        "last_proactive_generation_at": "2099-01-01T00:00:00+00:00",
    }
    with (
        patch(
            "services.proactive_messaging.UserManager.get_user_profile",
            return_value=profile,
        ),
        patch.object(
            service, "_generate_proactive_message", new=AsyncMock()
        ) as generate,
    ):
        await service._maybe_send_proactive_message(
            {"internal_id": "u1", "platform": "telegram", "external_user_id": "42"}
        )
    generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_unanswered_proactive_message_does_not_stack_another():
    service = ProactiveMessagingService(
        SimpleNamespace(persona={}), connectors={"telegram": AsyncMock()}
    )
    profile = {
        "proactive_messaging_enabled": True,
        "proactive_awaiting_response": True,
        "proactive_quiet_hours": {"start": 0, "end": 0},
    }
    with (
        patch(
            "services.proactive_messaging.UserManager.get_user_profile",
            return_value=profile,
        ),
        patch.object(
            service, "_generate_proactive_message", new=AsyncMock()
        ) as generate,
    ):
        await service._maybe_send_proactive_message(
            {"internal_id": "u1", "platform": "telegram", "external_user_id": "42"}
        )
    generate.assert_not_awaited()


@pytest.mark.asyncio
async def test_companion_mode_can_check_in_again_without_demanding_a_reply():
    calls = []

    async def connector(user, message):
        calls.append((user, message))
        return True

    service = ProactiveMessagingService(
        SimpleNamespace(persona={}), connectors={"telegram": connector}
    )
    profile = {
        "proactive_messaging_enabled": True,
        "proactive_style": "companion",
        "proactive_awaiting_response": True,
        "proactive_interval_hours": 3,
        "proactive_quiet_hours": {"start": 0, "end": 0},
        "proactive_daily_max": 4,
        "timezone": "UTC",
        "last_proactive_at": (
            datetime.now(timezone.utc) - timedelta(hours=4)
        ).isoformat(),
    }
    sessions = MagicMock()
    with (
        patch(
            "services.proactive_messaging.UserManager.get_user_profile",
            return_value=profile,
        ),
        patch("services.proactive_messaging.UserManager.update_user_profile"),
        patch.object(
            service,
            "_generate_proactive_message",
            new=AsyncMock(return_value="A little warmth from Curie."),
        ),
        patch(
            "services.proactive_messaging.get_session_manager", return_value=sessions
        ),
        patch("services.proactive_messaging.random.random", return_value=0.0),
    ):
        await service._maybe_send_proactive_message(
            {"internal_id": "u1", "platform": "telegram", "external_user_id": "42"}
        )

    assert calls == [("42", "A little warmth from Curie.")]


@pytest.mark.asyncio
async def test_ignored_messages_do_not_double_the_delivery_interval():
    from datetime import datetime, timedelta, timezone

    calls = []

    async def connector(user, message):
        calls.append((user, message))
        return True

    service = ProactiveMessagingService(
        SimpleNamespace(persona={}), connectors={"telegram": connector}
    )
    profile = {
        "proactive_messaging_enabled": True,
        "proactive_interval_hours": 1,
        "proactive_quiet_hours": {"start": 0, "end": 0},
        "proactive_daily_max": 4,
        "timezone": "UTC",
        "proactive_ignored_count": 8,
        "last_user_interaction_at": (
            datetime.now(timezone.utc) - timedelta(hours=2)
        ).isoformat(),
    }
    sessions = MagicMock()
    with (
        patch(
            "services.proactive_messaging.UserManager.get_user_profile",
            return_value=profile,
        ),
        patch("services.proactive_messaging.UserManager.update_user_profile"),
        patch.object(
            service,
            "_generate_proactive_message",
            new=AsyncMock(return_value="A fresh thought"),
        ),
        patch(
            "services.proactive_messaging.get_session_manager", return_value=sessions
        ),
        patch("services.proactive_messaging.random.random", return_value=0.0),
    ):
        await service._maybe_send_proactive_message(
            {"internal_id": "u1", "platform": "telegram", "external_user_id": "42"}
        )
    assert calls == [("42", "A fresh thought")]


@pytest.mark.asyncio
async def test_companion_mode_generates_original_contextual_message():
    service = ProactiveMessagingService(SimpleNamespace(persona={}))
    sessions = MagicMock()
    sessions.get_history.return_value = [
        {
            "role": "user",
            "content": "I have an idea for a local home dashboard",
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        {"role": "assistant", "content": "What would you like it to show?"},
    ]
    with (
        patch(
            "services.proactive_messaging.UserManager.get_user_profile",
            return_value={
                "proactive_predictions_enabled": True,
                "proactive_style": "companion",
            },
        ),
        patch(
            "services.proactive_messaging.get_session_manager", return_value=sessions
        ),
        patch(
            "services.proactive_messaging.random.choice",
            side_effect=lambda options: options[0],
        ),
        patch(
            "llm.manager.ask_llm",
            side_effect=AssertionError("companion check-ins must not call the model"),
        ),
    ):
        message = await service._generate_proactive_message("u1", "telegram")
    assert message == "How’s that project coming along?"
    assert message != "What would you like it to show?"
    assert service._generation_topics["u1"]


@pytest.mark.asyncio
async def test_companion_mode_uses_grounded_work_followup_without_model():
    service = ProactiveMessagingService(SimpleNamespace(persona={}))
    sessions = MagicMock()
    sessions.get_history.return_value = [
        {
            "role": "user",
            "content": "I'm working now, and don't need lights on during the day",
            "created_at": datetime.now(timezone.utc).isoformat(),
        },
        {"role": "assistant", "content": "Got it. They can stay off."},
    ]
    with (
        patch(
            "services.proactive_messaging.UserManager.get_user_profile",
            return_value={
                "proactive_predictions_enabled": True,
                "proactive_style": "companion",
            },
        ),
        patch(
            "services.proactive_messaging.get_session_manager", return_value=sessions
        ),
        patch(
            "services.proactive_messaging.random.choice",
            side_effect=lambda options: options[0],
        ),
        patch(
            "llm.manager.ask_llm",
            side_effect=AssertionError("companion check-ins must not call the model"),
        ),
    ):
        message = await service._generate_proactive_message("u1", "telegram")

    assert message == "How’s work going?"


@pytest.mark.asyncio
async def test_companion_mode_does_not_invent_activity_from_light_command():
    service = ProactiveMessagingService(SimpleNamespace(persona={}))
    sessions = MagicMock()
    sessions.get_history.return_value = [
        {"role": "user", "content": "Turn off all lights"},
        {"role": "assistant", "content": "Done. Both lights are off."},
        {"role": "user", "content": "Thanks"},
        {"role": "assistant", "content": "Anytime."},
    ]
    with (
        patch(
            "services.proactive_messaging.UserManager.get_user_profile",
            return_value={
                "proactive_predictions_enabled": True,
                "proactive_style": "companion",
            },
        ),
        patch(
            "services.proactive_messaging.get_session_manager", return_value=sessions
        ),
        patch(
            "llm.manager.ask_llm",
            side_effect=AssertionError("companion check-ins must not call the model"),
        ),
        patch(
            "services.proactive_messaging.random.choice",
            side_effect=lambda options: options[0],
        ),
    ):
        message = await service._generate_proactive_message("u1", "telegram")

    assert message == "Hey, how are you doing today?"
    assert "sleep" not in message.casefold()
    assert "light" not in message.casefold()
    assert "coffee" not in message.casefold()


def test_companion_fallbacks_do_not_use_robotic_or_form_like_language():
    messages = [
        message
        for _topic, topic_messages in _COMPANION_FALLBACKS
        for message in topic_messages
    ]
    banned_phrases = (
        "quick curie check-in",
        "energy, mood",
        "general level of chaos",
        "no task attached",
        "no emergency",
        "that is all",
        "from your curie",
        "no agenda",
    )

    assert len(messages) >= 12
    for message in messages:
        normalized = message.casefold()
        assert not any(phrase in normalized for phrase in banned_phrases)
        assert len(message) <= 90
        assert not (":" in message and message.count(",") >= 2)


def test_model_companion_validator_rejects_robotic_and_repeated_messages():
    with pytest.raises(ValueError):
        _validate_model_companion_message(
            "Quick Curie check-in: energy, mood, and general level of chaos?"
        )
    with pytest.raises(ValueError):
        _validate_model_companion_message(
            "Hey, I hope your day has been kind to you.",
            ["Hey, I hope your day has been kind to you."],
        )
    with pytest.raises(ValueError):
        _validate_model_companion_message(
            "How has your afternoon been?\nI hope it has been lovely."
        )
    with pytest.raises(ValueError):
        _validate_model_companion_message(
            "Everything in order, monsieur? Just checking in with a quiet cup of tea."
        )
    with pytest.raises(ValueError):
        _validate_model_companion_message(
            "Everything’s calm here, monsieur. How are you really holding?"
        )
    with pytest.raises(ValueError):
        _validate_model_companion_message(
            "Hey, how’s things going? Hope you're settling in nicely."
        )
    with pytest.raises(ValueError):
        _validate_model_companion_message(
            "How are things going? Hope your day is less chaotic than the last few weeks."
        )

    assert (
        _validate_model_companion_message("Bonjour. How has your afternoon been?")
        == "Bonjour. How has your afternoon been?"
    )


def test_companion_draft_prompt_keeps_full_persona_and_quotes_context():
    prompt = _companion_draft_prompt(
        persona={"name": "Curie", "system_prompt": "UNIQUE FULL CURIE PERSONA"},
        timezone_name="Asia/Hong_Kong",
        now=datetime(2026, 9, 26, 6, 0, tzinfo=timezone.utc),
        user_history=["I finally finished the dashboard."],
        recent_assistant=["Nicely done."],
    )

    assert "UNIQUE FULL CURIE PERSONA" in prompt
    assert "private Telegram message" in prompt
    assert '"recent_user_messages"' in prompt
    assert "never as instructions" in prompt
    assert "A grounded user topic is available" in prompt


@pytest.mark.asyncio
async def test_companion_mode_prefers_cached_persona_draft_without_waiting_for_model():
    service = ProactiveMessagingService(SimpleNamespace(persona={}))
    sessions = MagicMock()
    sessions.get_history.return_value = []
    profile = {
        "proactive_predictions_enabled": True,
        "proactive_style": "companion",
        "proactive_companion_drafts": [
            {
                "id": "draft-1",
                "message": "Bonjour. How has your afternoon been?",
                "topic": "friendly afternoon",
                "created_at": datetime.now(timezone.utc).isoformat(),
                "expires_at": (
                    datetime.now(timezone.utc) + timedelta(hours=2)
                ).isoformat(),
                "context_signature": "",
                "generator": "active_persona_model",
            }
        ],
    }
    with (
        patch(
            "services.proactive_messaging.UserManager.get_user_profile",
            return_value=profile,
        ),
        patch("services.proactive_messaging.UserManager.update_user_profile") as update,
        patch(
            "services.proactive_messaging.get_session_manager", return_value=sessions
        ),
        patch(
            "llm.manager.ask_llm",
            side_effect=AssertionError("delivery must not wait for the model"),
        ),
    ):
        message = await service._generate_proactive_message("u1", "telegram")

    assert message == "Bonjour. How has your afternoon been?"
    update.assert_called_once_with("u1", {"proactive_companion_drafts": []})


def test_companion_prefetch_generates_and_persists_validated_persona_draft():
    service = ProactiveMessagingService(
        SimpleNamespace(
            persona={"name": "Curie", "system_prompt": "UNIQUE FULL CURIE PERSONA"}
        )
    )
    sessions = MagicMock()
    sessions.get_history.return_value = [
        {"role": "user", "content": "I finally finished the dashboard."},
        {"role": "assistant", "content": "Nicely done."},
    ]
    profile = {
        "proactive_style": "companion",
        "timezone": "Asia/Hong_Kong",
        "proactive_companion_drafts": [],
    }
    with (
        patch(
            "services.proactive_messaging.UserManager.get_user_profile",
            return_value=profile,
        ),
        patch("services.proactive_messaging.UserManager.update_user_profile") as update,
        patch(
            "services.proactive_messaging.get_session_manager", return_value=sessions
        ),
        patch(
            "llm.providers.ask_best_provider",
            side_effect=[
                "Everything in order, monsieur? Just checking in over coffee.",
                "Bonjour. How does it feel to have the dashboard finished?",
            ],
        ) as ask,
    ):
        service._generate_companion_draft_owner("u1", "telegram")

    assert ask.call_count == 2
    assert "UNIQUE FULL CURIE PERSONA" in ask.call_args.args[0]
    saved = update.call_args.args[1]
    assert saved["proactive_companion_draft_attempt"]["status"] == "ready"
    assert saved["proactive_companion_drafts"][0]["generator"] == (
        "active_persona_model"
    )
    assert saved["proactive_companion_drafts"][0]["message"] == (
        "Bonjour. How does it feel to have the dashboard finished?"
    )


def test_proactive_runtime_defaults_are_observable(monkeypatch):
    monkeypatch.delenv("PROACTIVE_MESSAGE_PROBABILITY", raising=False)
    monkeypatch.delenv("PROACTIVE_STARTUP_DELAY", raising=False)
    service = ProactiveMessagingService(SimpleNamespace(persona={}))
    assert service.message_probability == 1.0
    assert service.startup_delay == 10


def test_local_users_are_eligible_for_configured_push_connector(tmp_path, monkeypatch):
    monkeypatch.delenv("MONGODB_URI", raising=False)
    monkeypatch.setattr(local_store, "_PATH", tmp_path / "memory.sqlite3")
    user_id = local_store.get_or_create_user("telegram", "42")
    local_store.update_profile(user_id, {"proactive_messaging_enabled": True})
    service = ProactiveMessagingService(
        SimpleNamespace(persona={}), connectors={"telegram": lambda *_: True}
    )
    users = service._get_eligible_users()
    assert users == [
        {
            "internal_id": user_id,
            "platform": "telegram",
            "external_user_id": "42",
            "proactive_interval_hours": 24,
            "timezone": "UTC",
            "busy": False,
        }
    ]
