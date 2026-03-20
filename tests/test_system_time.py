#!/usr/bin/env python3
"""
Tests for utils/system_time.py:
- get_verified_now() always returns a timezone-aware datetime from the system clock
- is_internet_time_available() reflects successful / failed internet checks
- get_time_source_label() returns an appropriate label
- Internet check is skipped when ENABLE_TIME_VERIFICATION=false
- Drift warning is logged when system clock differs significantly from internet time
"""

import sys
import os
from datetime import datetime, timezone as _stdtz
from unittest.mock import patch, MagicMock

import pytest
import pytz

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import utils.system_time as st


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reset_cache():
    """Reset module-level cache so each test starts fresh."""
    st._last_check_mono = 0.0
    st._internet_ok = False
    st._last_drift_seconds = 0.0


# ---------------------------------------------------------------------------
# get_verified_now
# ---------------------------------------------------------------------------

class TestGetVerifiedNow:
    def test_returns_timezone_aware_datetime(self):
        _reset_cache()
        with patch.object(st, "_ENABLED", False):
            result = st.get_verified_now()
        assert result.tzinfo is not None

    def test_returns_utc_when_no_tz_given(self):
        _reset_cache()
        with patch.object(st, "_ENABLED", False):
            result = st.get_verified_now(tz=None)
        # UTC offset should be zero
        assert result.utcoffset().total_seconds() == 0

    def test_applies_given_timezone(self):
        _reset_cache()
        tz = pytz.timezone("Asia/Tokyo")
        with patch.object(st, "_ENABLED", False):
            result = st.get_verified_now(tz=tz)
        assert result.tzinfo is not None
        # JST is UTC+9
        assert result.utcoffset().total_seconds() == 9 * 3600

    def test_system_clock_is_authoritative(self):
        """The returned time must be within 2 seconds of the real system clock."""
        _reset_cache()
        with patch.object(st, "_ENABLED", False):
            before = datetime.now(_stdtz.utc)
            result = st.get_verified_now()
            after = datetime.now(_stdtz.utc)
        result_utc = result.astimezone(_stdtz.utc)
        assert before <= result_utc <= after


# ---------------------------------------------------------------------------
# Internet verification — successful path
# ---------------------------------------------------------------------------

class TestInternetVerificationSuccess:
    def setup_method(self):
        _reset_cache()

    def test_sets_internet_ok_on_success(self):
        """A valid API response marks internet_ok=True."""
        from datetime import timezone as _tz
        fake_utc = datetime.now(_tz.utc).isoformat()
        fake_resp = MagicMock()
        fake_resp.json.return_value = {"utc_datetime": fake_utc}
        fake_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = fake_resp

        with patch("utils.system_time._ENABLED", True), \
             patch("httpx.Client", return_value=mock_client):
            st._check_internet_time()

        assert st._internet_ok is True

    def test_internet_ok_label(self):
        st._internet_ok = True
        st._last_check_mono = 1.0  # non-zero = checked before
        with patch.object(st, "_ENABLED", True):
            label = st.get_time_source_label()
        assert "internet-verified" in label

    def test_drift_warning_logged(self, caplog):
        """A drift > DRIFT_WARN_SECONDS should produce a warning log."""
        import logging
        from datetime import timedelta, timezone as _tz

        # Internet time that is 30 s in the past (system clock 30 s ahead)
        internet_utc = (datetime.now(_tz.utc) - timedelta(seconds=30)).isoformat()
        fake_resp = MagicMock()
        fake_resp.json.return_value = {"utc_datetime": internet_utc}
        fake_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = fake_resp

        with patch("utils.system_time._ENABLED", True), \
             patch("httpx.Client", return_value=mock_client), \
             caplog.at_level(logging.WARNING, logger="utils.system_time"):
            st._check_internet_time()

        assert any("clock" in rec.message.lower() for rec in caplog.records)


# ---------------------------------------------------------------------------
# Internet verification — failure path
# ---------------------------------------------------------------------------

class TestInternetVerificationFailure:
    def setup_method(self):
        _reset_cache()

    def test_network_error_leaves_internet_ok_false(self):
        with patch("utils.system_time._ENABLED", True), \
             patch("httpx.Client", side_effect=Exception("network unreachable")):
            st._check_internet_time()
        assert st._internet_ok is False

    def test_bad_json_leaves_internet_ok_false(self):
        fake_resp = MagicMock()
        fake_resp.json.return_value = {}  # no utc_datetime key
        fake_resp.raise_for_status = MagicMock()

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.get.return_value = fake_resp

        with patch("utils.system_time._ENABLED", True), \
             patch("httpx.Client", return_value=mock_client):
            st._check_internet_time()

        assert st._internet_ok is False

    def test_unavailable_label_after_failed_check(self):
        st._internet_ok = False
        st._last_check_mono = 1.0  # non-zero = checked before, but failed
        with patch.object(st, "_ENABLED", True):
            label = st.get_time_source_label()
        assert "unavailable" in label


# ---------------------------------------------------------------------------
# ENABLE_TIME_VERIFICATION=false
# ---------------------------------------------------------------------------

class TestVerificationDisabled:
    def setup_method(self):
        _reset_cache()

    def test_disabled_skips_network_call(self):
        with patch.object(st, "_ENABLED", False), \
             patch("httpx.Client") as mock_http:
            st._maybe_refresh()
        mock_http.assert_not_called()

    def test_disabled_label_is_system_clock(self):
        with patch.object(st, "_ENABLED", False):
            label = st.get_time_source_label()
        assert label == "system clock"

    def test_is_internet_time_available_false_when_disabled(self):
        with patch.object(st, "_ENABLED", False):
            # Even if _internet_ok were True from a previous run, the accessor
            # just reflects the last cached value — but when ENABLED=False no
            # checks ever run so it stays False.
            assert st.is_internet_time_available() is False


# ---------------------------------------------------------------------------
# Cache TTL — no redundant HTTP calls
# ---------------------------------------------------------------------------

class TestCacheTTL:
    def test_no_second_request_within_ttl(self):
        """After a successful check, a second call within TTL must not fire HTTP."""
        _reset_cache()
        # Mark a very recent check
        import time as _time
        st._last_check_mono = _time.monotonic()

        with patch.object(st, "_ENABLED", True), \
             patch("httpx.Client") as mock_http:
            st._maybe_refresh()

        mock_http.assert_not_called()
