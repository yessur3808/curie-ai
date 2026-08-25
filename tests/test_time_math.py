from datetime import datetime

from utils.time_math import calculate_split_sleep, split_sleep_reply


def test_split_sleep_uses_current_local_time():
    now = datetime(2026, 8, 24, 15, 42)
    result = calculate_split_sleep(
        "I slept at 6:30am, woke up at 9am then slept again at 11am and woke up now",
        now,
    )
    assert result == {"intervals": [150, 282], "total_minutes": 432}
    assert "7 hours and 12 minutes" in split_sleep_reply(
        "I slept at 6:30am, woke up at 9am then slept again at 11am and woke up now",
        now,
    )


def test_unrelated_times_are_not_intercepted():
    assert (
        calculate_split_sleep("Meet me at 6pm and leave at 9pm", datetime.now()) is None
    )
