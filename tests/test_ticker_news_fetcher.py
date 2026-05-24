"""
tests/test_ticker_news_fetcher.py
Item 21 — Tests for ticker_news_fetcher.py

All network calls are mocked.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import patch, MagicMock
import pytest

from shiftinnerv.news.ticker_news_fetcher import fetch_ticker_headlines


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_tiingo_item(title: str, source: str = "Reuters",
                      pub: str = "2026-05-23T10:00:00Z") -> dict:
    return {
        "title":         title,
        "source":        source,
        "publishedDate": pub,
    }


def _mock_tiingo_response(items: list) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = items
    return mock_resp


# ── Key absent ────────────────────────────────────────────────────────────────

class TestNoApiKey:

    def test_returns_empty_when_tiingo_key_not_set(self, monkeypatch):
        monkeypatch.delenv("TIINGO_KEY", raising=False)
        monkeypatch.delenv("TIINGO_API_KEY", raising=False)
        result = fetch_ticker_headlines("AAPL")
        assert result == []


# ── Network failure ───────────────────────────────────────────────────────────

class TestNetworkFailure:

    def test_returns_empty_on_request_exception(self, monkeypatch):
        monkeypatch.setenv("TIINGO_KEY", "testkey")
        import requests
        with patch("shiftinnerv.news.ticker_news_fetcher.requests.get",
                   side_effect=requests.RequestException("timeout")):
            result = fetch_ticker_headlines("KWEB")
        assert result == []

    def test_returns_empty_on_json_parse_error(self, monkeypatch):
        monkeypatch.setenv("TIINGO_KEY", "testkey")
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.side_effect = ValueError("bad json")
        with patch("shiftinnerv.news.ticker_news_fetcher.requests.get",
                   return_value=mock_resp):
            result = fetch_ticker_headlines("FXI")
        assert result == []

    def test_never_raises(self, monkeypatch):
        monkeypatch.setenv("TIINGO_KEY", "testkey")
        with patch("shiftinnerv.news.ticker_news_fetcher.requests.get",
                   side_effect=Exception("unexpected")):
            try:
                result = fetch_ticker_headlines("SPY")
                assert isinstance(result, list)
            except Exception as exc:
                pytest.fail(f"fetch_ticker_headlines raised: {exc}")


# ── Truncation ────────────────────────────────────────────────────────────────

class TestTruncation:

    def test_truncates_to_max_headlines(self, monkeypatch):
        monkeypatch.setenv("TIINGO_KEY", "testkey")
        items = [_make_tiingo_item(f"Headline {i}") for i in range(10)]
        mock_resp = _mock_tiingo_response(items)
        with patch("shiftinnerv.news.ticker_news_fetcher.requests.get",
                   return_value=mock_resp):
            result = fetch_ticker_headlines("KWEB", max_headlines=3)
        assert len(result) <= 3

    def test_returns_all_when_fewer_than_max(self, monkeypatch):
        monkeypatch.setenv("TIINGO_KEY", "testkey")
        items = [_make_tiingo_item(f"Headline {i}") for i in range(2)]
        mock_resp = _mock_tiingo_response(items)
        with patch("shiftinnerv.news.ticker_news_fetcher.requests.get",
                   return_value=mock_resp):
            result = fetch_ticker_headlines("KWEB", max_headlines=3)
        assert len(result) == 2


# ── Required result dict keys ─────────────────────────────────────────────────

class TestResultStructure:

    def test_each_result_has_required_keys(self, monkeypatch):
        monkeypatch.setenv("TIINGO_KEY", "testkey")
        items = [_make_tiingo_item("Some headline")]
        mock_resp = _mock_tiingo_response(items)
        with patch("shiftinnerv.news.ticker_news_fetcher.requests.get",
                   return_value=mock_resp):
            result = fetch_ticker_headlines("AAPL", max_headlines=3)
        assert len(result) == 1
        for item in result:
            assert "ticker"        in item
            assert "headline"      in item
            assert "source"        in item
            assert "published_utc" in item

    def test_ticker_uppercased_in_result(self, monkeypatch):
        monkeypatch.setenv("TIINGO_KEY", "testkey")
        items = [_make_tiingo_item("ETF news")]
        mock_resp = _mock_tiingo_response(items)
        with patch("shiftinnerv.news.ticker_news_fetcher.requests.get",
                   return_value=mock_resp):
            result = fetch_ticker_headlines("kweb", max_headlines=3)
        assert result[0]["ticker"] == "KWEB"

    def test_empty_items_skipped(self, monkeypatch):
        """Items with empty headlines should be skipped."""
        monkeypatch.setenv("TIINGO_KEY", "testkey")
        items = [
            {"title": "", "source": "Reuters", "publishedDate": "2026-05-23"},
            {"title": None, "source": "Reuters", "publishedDate": "2026-05-23"},
            _make_tiingo_item("Valid headline"),
        ]
        mock_resp = _mock_tiingo_response(items)
        with patch("shiftinnerv.news.ticker_news_fetcher.requests.get",
                   return_value=mock_resp):
            result = fetch_ticker_headlines("SPY", max_headlines=3)
        assert len(result) == 1
        assert result[0]["headline"] == "Valid headline"

    def test_supports_tiingo_api_key_env(self, monkeypatch):
        """TIINGO_API_KEY spelling (Item 21) should also work."""
        monkeypatch.delenv("TIINGO_KEY", raising=False)
        monkeypatch.setenv("TIINGO_API_KEY", "alt_key")
        items = [_make_tiingo_item("News via alt key")]
        mock_resp = _mock_tiingo_response(items)
        with patch("shiftinnerv.news.ticker_news_fetcher.requests.get",
                   return_value=mock_resp):
            result = fetch_ticker_headlines("AAPL", max_headlines=3)
        assert len(result) == 1
