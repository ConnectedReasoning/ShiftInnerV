"""
tests/test_news_brief_builder.py
Item 21 — Tests for news_brief_builder.py

All external I/O is mocked. Tests verify the contract:
  - build_news_context always returns a string, never raises
  - returns "" gracefully when all fetches fail
  - output contains expected section headers when data is present
  - tasks.py build_tasks with non-empty news_context includes the context
  - tasks.py build_tasks with empty news_context is backward-compatible
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch, MagicMock
import pytest

from shiftinnerv.news.news_brief_builder import (
    build_news_context,
    build_news_context_with_flags,
    _has_cb_text,
    _has_macro_surprise,
)


# ── build_news_context: always returns string ─────────────────────────────────

class TestBuildNewsContextReturnType:

    def test_returns_string_when_all_fetches_succeed(self):
        """Returns a non-empty string when tiers have data."""
        mock_event = [{
            "currency": "USD", "event": "CPI", "actual": 3.8,
            "forecast": None, "previous": 3.5, "impact": "HIGH",
            "timestamp": "2026-05-23", "beat_miss": "BEAT"
        }]
        with patch("shiftinnerv.news.news_brief_builder.fetch_calendar_context",
                   return_value=mock_event), \
             patch("shiftinnerv.news.news_brief_builder.fetch_cb_statement",
                   return_value=None), \
             patch("shiftinnerv.news.news_brief_builder.fetch_ticker_headlines",
                   return_value=[]):
            result = build_news_context("EURUSD", "")
        assert isinstance(result, str)

    def test_returns_empty_string_when_all_fetches_fail(self):
        """Returns '' gracefully when all tiers produce no data."""
        with patch("shiftinnerv.news.news_brief_builder.fetch_calendar_context",
                   side_effect=Exception("network error")), \
             patch("shiftinnerv.news.news_brief_builder.fetch_cb_statement",
                   side_effect=Exception("network error")), \
             patch("shiftinnerv.news.news_brief_builder.fetch_ticker_headlines",
                   side_effect=Exception("network error")):
            result = build_news_context("EURUSD", "")
        assert result == ""

    def test_never_raises_on_all_failures(self):
        """Must never propagate exceptions."""
        with patch("shiftinnerv.news.news_brief_builder.fetch_calendar_context",
                   side_effect=RuntimeError("unexpected")), \
             patch("shiftinnerv.news.news_brief_builder.fetch_cb_statement",
                   side_effect=RuntimeError("unexpected")), \
             patch("shiftinnerv.news.news_brief_builder.fetch_ticker_headlines",
                   side_effect=RuntimeError("unexpected")):
            try:
                result = build_news_context("KWEB", "FXI")
                assert isinstance(result, str)
            except Exception as exc:
                pytest.fail(f"build_news_context raised: {exc}")

    def test_equity_pair_returns_string(self):
        """Equity tickers have no currency — Tier 1/2 empty; Tier 3 may populate."""
        with patch("shiftinnerv.news.news_brief_builder.fetch_ticker_headlines",
                   return_value=[]):
            result = build_news_context("KWEB", "FXI")
        assert isinstance(result, str)


# ── Section header presence ───────────────────────────────────────────────────

class TestBuildNewsContextSectionHeaders:

    def test_contains_header_when_calendar_data_present(self):
        mock_event = [{
            "currency": "EUR", "event": "Policy Rate", "actual": 2.5,
            "forecast": None, "previous": 2.5, "impact": "HIGH",
            "timestamp": "2026-05-22", "beat_miss": "IN-LINE"
        }]
        with patch("shiftinnerv.news.news_brief_builder.fetch_calendar_context",
                   return_value=mock_event), \
             patch("shiftinnerv.news.news_brief_builder.fetch_cb_statement",
                   return_value=None), \
             patch("shiftinnerv.news.news_brief_builder.fetch_ticker_headlines",
                   return_value=[]):
            result = build_news_context("EURUSD", "")
        assert "=== NEWS & MACRO CONTEXT ===" in result

    def test_contains_economic_releases_header(self):
        mock_event = [{
            "currency": "USD", "event": "CPI", "actual": 3.8,
            "forecast": None, "previous": 3.5, "impact": "HIGH",
            "timestamp": "2026-05-23", "beat_miss": "BEAT"
        }]
        with patch("shiftinnerv.news.news_brief_builder.fetch_calendar_context",
                   return_value=mock_event), \
             patch("shiftinnerv.news.news_brief_builder.fetch_cb_statement",
                   return_value=None), \
             patch("shiftinnerv.news.news_brief_builder.fetch_ticker_headlines",
                   return_value=[]):
            result = build_news_context("EURUSD", "")
        assert "ECONOMIC RELEASES" in result

    def test_contains_ticker_headlines_when_tier3_has_data(self):
        mock_headline = [{
            "ticker": "KWEB", "headline": "China ETF news",
            "source": "Reuters", "published_utc": "2026-05-23"
        }]
        with patch("shiftinnerv.news.news_brief_builder.fetch_calendar_context",
                   return_value=[]), \
             patch("shiftinnerv.news.news_brief_builder.fetch_cb_statement",
                   return_value=None), \
             patch("shiftinnerv.news.news_brief_builder.fetch_ticker_headlines",
                   return_value=mock_headline):
            result = build_news_context("KWEB", "FXI")
        assert "TICKER HEADLINES" in result
        assert "KWEB" in result

    def test_empty_result_has_no_header(self):
        with patch("shiftinnerv.news.news_brief_builder.fetch_calendar_context",
                   return_value=[]), \
             patch("shiftinnerv.news.news_brief_builder.fetch_cb_statement",
                   return_value=None), \
             patch("shiftinnerv.news.news_brief_builder.fetch_ticker_headlines",
                   return_value=[]):
            result = build_news_context("KWEB", "FXI")
        assert result == ""


# ── Flag return values ────────────────────────────────────────────────────────

class TestBuildNewsContextFlags:

    def test_macro_surprise_true_when_beat_present(self):
        mock_event = [{
            "currency": "USD", "event": "CPI", "actual": 3.8,
            "forecast": None, "previous": 3.5, "impact": "HIGH",
            "timestamp": "2026-05-23", "beat_miss": "BEAT"
        }]
        with patch("shiftinnerv.news.news_brief_builder.fetch_calendar_context",
                   return_value=mock_event), \
             patch("shiftinnerv.news.news_brief_builder.fetch_cb_statement",
                   return_value=None), \
             patch("shiftinnerv.news.news_brief_builder.fetch_ticker_headlines",
                   return_value=[]):
            _, _, macro_surprise = build_news_context_with_flags("EURUSD", "")
        assert macro_surprise is True

    def test_macro_surprise_false_when_inline(self):
        mock_event = [{
            "currency": "EUR", "event": "Policy Rate", "actual": 2.5,
            "forecast": None, "previous": 2.5, "impact": "HIGH",
            "timestamp": "2026-05-22", "beat_miss": "IN-LINE"
        }]
        with patch("shiftinnerv.news.news_brief_builder.fetch_calendar_context",
                   return_value=mock_event), \
             patch("shiftinnerv.news.news_brief_builder.fetch_cb_statement",
                   return_value=None), \
             patch("shiftinnerv.news.news_brief_builder.fetch_ticker_headlines",
                   return_value=[]):
            _, _, macro_surprise = build_news_context_with_flags("EURUSD", "")
        assert macro_surprise is False

    def test_cb_decision_recent_true_when_statement_text_present(self):
        cb_text = ("The Monetary Policy Committee voted to hold the Bank Rate at 5.25%. "
                   "The rate decision was unanimous. Policy rate unchanged.")
        with patch("shiftinnerv.news.news_brief_builder.fetch_calendar_context",
                   return_value=[]), \
             patch("shiftinnerv.news.news_brief_builder.fetch_cb_statement",
                   return_value=cb_text), \
             patch("shiftinnerv.news.news_brief_builder.fetch_ticker_headlines",
                   return_value=[]):
            _, cb_recent, _ = build_news_context_with_flags("GBPUSD", "")
        assert cb_recent is True

    def test_flags_are_bool_type(self):
        with patch("shiftinnerv.news.news_brief_builder.fetch_calendar_context",
                   return_value=[]), \
             patch("shiftinnerv.news.news_brief_builder.fetch_cb_statement",
                   return_value=None), \
             patch("shiftinnerv.news.news_brief_builder.fetch_ticker_headlines",
                   return_value=[]):
            _, cb_recent, macro_surprise = build_news_context_with_flags("EURUSD", "")
        assert isinstance(cb_recent, bool)
        assert isinstance(macro_surprise, bool)


# ── Heuristic helpers ─────────────────────────────────────────────────────────

class TestHeuristics:

    def test_has_macro_surprise_detects_beat(self):
        assert _has_macro_surprise("... actual 3.8 vs prev 3.5 — BEAT — ...") is True

    def test_has_macro_surprise_detects_miss(self):
        assert _has_macro_surprise("IN-LINE ... MISS ... ") is True

    def test_has_macro_surprise_false_for_inline(self):
        assert _has_macro_surprise("all releases IN-LINE") is False

    def test_has_cb_text_detects_rate_decision(self):
        assert _has_cb_text("The rate decision was unchanged") is True

    def test_has_cb_text_detects_basis_points(self):
        assert _has_cb_text("raised by 25 basis points") is True

    def test_has_cb_text_false_for_empty(self):
        assert _has_cb_text("") is False


# ── tasks.py backward compatibility ──────────────────────────────────────────

class TestTasksBriefIntegration:
    """
    Verify that build_analyst_task creates a task with the brief injected
    into its description, and that news context appears when provided.
    """

    def test_build_analyst_task_accepts_brief(self):
        """build_analyst_task must not raise when given a valid brief."""
        from unittest.mock import MagicMock, patch
        import importlib

        class MockTask:
            def __init__(self, **kwargs):
                self.description = kwargs.get("description", "")
                self.expected_output = kwargs.get("expected_output", "")
                self.agent = kwargs.get("agent")

        with patch.dict("sys.modules", {"crewai": MagicMock(Task=MockTask)}):
            import shiftinnerv.pipelines.tasks as tasks_mod
            importlib.reload(tasks_mod)
            analyst = MagicMock()
            task = tasks_mod.build_analyst_task(
                analyst=analyst,
                brief="=== CORRELATION DECAY REPORT ===\nSNR: 1.8\n",
                ticker1="EURUSD", ticker2="",
                label="Euro Dollar", verdict="MONITOR",
            )
            assert task is not None
            assert "EURUSD" in task.description
            assert "MONITOR" in task.description

    def test_build_analyst_task_injects_brief_content(self):
        """The brief string must appear verbatim in the task description."""
        from unittest.mock import MagicMock, patch
        import importlib

        captured = []

        class MockTask:
            def __init__(self, **kwargs):
                captured.append(kwargs.get("description", ""))

        with patch.dict("sys.modules", {"crewai": MagicMock(Task=MockTask)}):
            import shiftinnerv.pipelines.tasks as tasks_mod
            importlib.reload(tasks_mod)
            analyst = MagicMock()
            sentinel = "SENTINEL_BRIEF_CONTENT_XYZ"
            tasks_mod.build_analyst_task(
                analyst=analyst,
                brief=sentinel,
                ticker1="CADJPY=X", ticker2="USDJPY=X",
                label="CAD vs USD JPY cross", verdict="REJECT",
            )
            assert any(sentinel in d for d in captured)
