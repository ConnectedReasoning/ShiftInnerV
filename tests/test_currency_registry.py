"""
tests/test_currency_registry.py
Item 21 — Tests for currency_registry.py
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest
from shiftinnerv.news.currency_registry import (
    CURRENCY_DATA_SOURCES,
    get_currencies_for_pair,
    _REQUIRED_KEYS,
)

# ── Registry structure tests ──────────────────────────────────────────────────

class TestRegistryStructure:

    def test_all_entries_have_required_keys(self):
        """Every entry must have all required keys."""
        for currency, entry in CURRENCY_DATA_SOURCES.items():
            for key in _REQUIRED_KEYS:
                assert key in entry, (
                    f"Currency {currency!r} missing required key {key!r}"
                )

    def test_calendar_series_is_dict(self):
        """calendar_series must be a dict for every entry."""
        for currency, entry in CURRENCY_DATA_SOURCES.items():
            assert isinstance(entry["calendar_series"], dict), (
                f"Currency {currency!r}: calendar_series must be a dict"
            )

    def test_calendar_series_not_empty(self):
        """calendar_series must have at least one entry per currency."""
        for currency, entry in CURRENCY_DATA_SOURCES.items():
            assert len(entry["calendar_series"]) > 0, (
                f"Currency {currency!r}: calendar_series is empty"
            )

    def test_calendar_source_is_known(self):
        """calendar_source must be one of the known source types."""
        known = {"fred", "ecb", "boc", "banxico", "bcb", "fred_proxy"}
        for currency, entry in CURRENCY_DATA_SOURCES.items():
            assert entry["calendar_source"] in known, (
                f"Currency {currency!r}: unknown calendar_source "
                f"{entry['calendar_source']!r}"
            )

    def test_api_key_env_is_none_or_string(self):
        """api_key_env must be None or a valid env var name string."""
        for currency, entry in CURRENCY_DATA_SOURCES.items():
            val = entry["api_key_env"]
            assert val is None or (
                isinstance(val, str) and len(val) > 0 and " " not in val
            ), (
                f"Currency {currency!r}: api_key_env {val!r} is invalid"
            )

    def test_cb_name_is_non_empty_string(self):
        for currency, entry in CURRENCY_DATA_SOURCES.items():
            assert isinstance(entry["cb_name"], str) and len(entry["cb_name"]) > 0, (
                f"Currency {currency!r}: cb_name must be a non-empty string"
            )

    def test_cb_statement_url_is_none_or_string(self):
        for currency, entry in CURRENCY_DATA_SOURCES.items():
            val = entry["cb_statement_url"]
            assert val is None or (
                isinstance(val, str) and val.startswith("http")
            ), (
                f"Currency {currency!r}: cb_statement_url {val!r} is invalid"
            )

    def test_minimum_registry_coverage(self):
        """Registry must include the minimum viable set of currencies."""
        required = {"USD", "EUR", "GBP", "JPY", "CAD", "AUD", "CHF",
                    "MXN", "BRL", "CNY", "KRW"}
        for ccy in required:
            assert ccy in CURRENCY_DATA_SOURCES, (
                f"Required currency {ccy!r} missing from registry"
            )


# ── get_currencies_for_pair tests ─────────────────────────────────────────────

class TestGetCurrenciesForPair:

    def test_eurusd_6char(self):
        """6-char FX ticker should be split correctly."""
        result = get_currencies_for_pair("EURUSD", "")
        assert result == ["EUR", "USD"]

    def test_eur_usd_slash(self):
        """Slash-separated FX pair in ticker1."""
        result = get_currencies_for_pair("EUR/USD", "")
        assert result == ["EUR", "USD"]

    def test_eur_usd_underscore(self):
        """Underscore-separated FX pair."""
        result = get_currencies_for_pair("EUR_USD", "")
        assert result == ["EUR", "USD"]

    def test_eur_gbp_slash(self):
        """EUR/GBP via ticker1."""
        result = get_currencies_for_pair("EUR/GBP", "")
        assert result == ["EUR", "GBP"]

    def test_equity_tickers_return_empty(self):
        """Equity tickers (KWEB, FXI) must return empty list."""
        result = get_currencies_for_pair("KWEB", "FXI")
        assert result == []

    def test_single_equity_returns_empty(self):
        result = get_currencies_for_pair("SPY", "QQQ")
        assert result == []

    def test_ticker1_plus_ticker2_as_pair(self):
        """When ticker1 and ticker2 together form EUR/GBP."""
        result = get_currencies_for_pair("EUR", "GBP")
        assert "EUR" in result and "GBP" in result

    def test_unknown_currency_pair_returns_empty(self):
        """Unknown currency codes must return empty list."""
        result = get_currencies_for_pair("XYZABC", "")
        assert result == []

    def test_lowercase_input_normalised(self):
        """Lowercase input should be normalised to uppercase."""
        result = get_currencies_for_pair("eurusd", "")
        assert result == ["EUR", "USD"]

    def test_no_duplicates_in_output(self):
        """Output should not contain duplicate currency codes."""
        result = get_currencies_for_pair("EUR/USD", "")
        assert len(result) == len(set(result))

    def test_dash_separated(self):
        """Dash-separated FX pair."""
        result = get_currencies_for_pair("GBP-JPY", "")
        assert result == ["GBP", "JPY"]
