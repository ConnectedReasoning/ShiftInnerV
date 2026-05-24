"""
tests/test_macro_fetcher.py
Item 21 — Tests for macro_fetcher.py

All network calls are mocked — no real HTTP requests in tests.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch, MagicMock
import pytest

from shiftinnerv.news.macro_fetcher import (
    fetch_calendar_context,
    _classify_beat_miss,
    _make_event,
    _fetch_fred,
    _fetch_fred_series,
)


# ── Beat/miss classification tests ───────────────────────────────────────────

class TestClassifyBeatMiss:

    def test_beat_when_actual_greater_than_previous(self):
        assert _classify_beat_miss(3.8, 3.5) == "BEAT"

    def test_miss_when_actual_less_than_previous(self):
        assert _classify_beat_miss(3.2, 3.5) == "MISS"

    def test_inline_when_within_threshold(self):
        """Values within 0.1% of each other should be IN-LINE."""
        assert _classify_beat_miss(3.503, 3.5) == "IN-LINE"

    def test_inline_exact_match(self):
        assert _classify_beat_miss(2.5, 2.5) == "IN-LINE"

    def test_beat_rate_increase(self):
        """Rate raised above previous — BEAT."""
        assert _classify_beat_miss(4.75, 4.5) == "BEAT"

    def test_miss_rate_decrease(self):
        assert _classify_beat_miss(4.25, 4.5) == "MISS"

    def test_zero_previous_returns_inline(self):
        """Edge case: previous == 0 must not divide-by-zero."""
        result = _classify_beat_miss(0.1, 0.0)
        assert result == "IN-LINE"

    def test_large_beat(self):
        assert _classify_beat_miss(5.0, 3.0) == "BEAT"

    def test_large_miss(self):
        assert _classify_beat_miss(1.0, 5.0) == "MISS"


# ── Event dict structure tests ────────────────────────────────────────────────

class TestMakeEvent:

    def test_event_has_required_keys(self):
        evt = _make_event("USD", "cpi", 3.8, 3.5, "2026-05-23")
        required = {"currency", "event", "actual", "forecast", "previous",
                    "impact", "timestamp", "beat_miss"}
        assert required.issubset(evt.keys())

    def test_event_impact_always_high(self):
        evt = _make_event("EUR", "rate", 2.5, 2.5, "2026-05-22")
        assert evt["impact"] == "HIGH"

    def test_event_beat_miss_computed(self):
        evt = _make_event("USD", "cpi", 3.8, 3.5, "2026-05-23")
        assert evt["beat_miss"] == "BEAT"

    def test_event_currency_preserved(self):
        evt = _make_event("GBP", "gdp", 1.2, 1.0, "2026-05-20")
        assert evt["currency"] == "GBP"


# ── fetch_calendar_context failure handling ───────────────────────────────────

class TestFetchCalendarContext:

    def test_returns_empty_list_on_network_failure(self):
        """Network failure should return [] not raise."""
        with patch("shiftinnerv.news.macro_fetcher.requests.get",
                   side_effect=Exception("network error")):
            result = fetch_calendar_context("USD")
        assert result == []

    def test_returns_empty_list_for_unknown_currency(self):
        result = fetch_calendar_context("XYZ")
        assert result == []

    def test_returns_list_type_always(self):
        """Return type must always be a list."""
        with patch("shiftinnerv.news.macro_fetcher.requests.get",
                   side_effect=ConnectionError()):
            result = fetch_calendar_context("EUR")
        assert isinstance(result, list)

    def test_never_raises(self):
        """Must never propagate exceptions."""
        with patch("shiftinnerv.news.macro_fetcher._fetch_fred",
                   side_effect=RuntimeError("unexpected")):
            try:
                result = fetch_calendar_context("USD")
                # Either returns [] or any list
                assert isinstance(result, list)
            except Exception as exc:
                pytest.fail(f"fetch_calendar_context raised unexpectedly: {exc}")

    def test_missing_fred_api_key_returns_empty(self, monkeypatch):
        """Without FRED_API_KEY set, FRED path returns []."""
        monkeypatch.delenv("FRED_API_KEY", raising=False)
        result = fetch_calendar_context("USD")
        assert result == []


# ── FRED series fetch ─────────────────────────────────────────────────────────

class TestFetchFredSeries:

    def _mock_fred_response(self, obs_list):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {"observations": obs_list}
        return mock_resp

    def test_returns_none_on_http_error(self):
        import requests as req_mod
        with patch("shiftinnerv.news.macro_fetcher.requests.get",
                   side_effect=req_mod.RequestException("timeout")):
            result = _fetch_fred_series("CPIAUCSL", "testkey")
        assert result is None

    def test_returns_none_with_fewer_than_2_obs(self):
        mock_resp = self._mock_fred_response(
            [{"date": "2026-04-01", "value": "3.5"}]
        )
        with patch("shiftinnerv.news.macro_fetcher.requests.get",
                   return_value=mock_resp):
            result = _fetch_fred_series("CPIAUCSL", "testkey")
        assert result is None

    def test_returns_tuple_with_2_obs(self):
        mock_resp = self._mock_fred_response([
            {"date": "2026-05-01", "value": "3.8"},
            {"date": "2026-04-01", "value": "3.5"},
        ])
        with patch("shiftinnerv.news.macro_fetcher.requests.get",
                   return_value=mock_resp):
            result = _fetch_fred_series("CPIAUCSL", "testkey")
        assert result is not None
        actual, previous, obs_date = result
        assert actual == 3.8
        assert previous == 3.5
        assert obs_date == "2026-05-01"

    def test_returns_none_on_invalid_json(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = {}  # missing 'observations' key
        with patch("shiftinnerv.news.macro_fetcher.requests.get",
                   return_value=mock_resp):
            result = _fetch_fred_series("CPIAUCSL", "testkey")
        assert result is None
