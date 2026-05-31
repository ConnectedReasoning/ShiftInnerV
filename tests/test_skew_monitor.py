"""
Tests for shiftinnerv.sensors.skew_monitor

Covers:
  - _select_expiry: expiry selection logic
  - _find_strike: moneyness-based strike selection
  - SkewRecord: dataclass defaults
  - SkewMonitor: DB init, upsert, query helpers (fully offline — no yfinance calls)
  - compute_raw_skew: error path when yfinance unavailable
  - Normalisation logic
"""

import sqlite3
import tempfile
import os
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from shiftinnerv.sensors.skew_monitor import (
    SkewMonitor,
    SkewRecord,
    _find_strike,
    _select_expiry,
    compute_raw_skew,
    OTM_MONEYNESS_TARGET,
    ATM_MONEYNESS_TARGET,
    MONEYNESS_TOLERANCE,
    MIN_VOLUME,
    SPY_BENCHMARK,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_db():
    """Return a temp DB path and SkewMonitor instance."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    monitor = SkewMonitor(db_path=tmp.name)
    return tmp.name, monitor


def _sample_puts(spot=100.0):
    """Create a synthetic puts DataFrame around a given spot price."""
    strikes = [70, 75, 80, 85, 90, 95, 100, 105, 110]
    rows = []
    for s in strikes:
        moneyness = s / spot
        # IV rises as moneyness falls (skew shape)
        iv = 0.20 + (1.0 - moneyness) * 0.30
        rows.append({
            "strike": float(s),
            "impliedVolatility": iv,
            "volume": 50,
            "bid": 1.0,
            "ask": 1.1,
            "lastPrice": 1.05,
        })
    return pd.DataFrame(rows)


# ── _select_expiry ─────────────────────────────────────────────────────────────

class TestSelectExpiry:

    def test_picks_within_window(self):
        today = date.today()
        expiries = (
            (today + timedelta(days=5)).isoformat(),   # too close (< MIN_DTE=7)
            (today + timedelta(days=14)).isoformat(),  # in window
            (today + timedelta(days=45)).isoformat(),  # in window
        )
        result = _select_expiry(expiries)
        assert result == expiries[1]

    def test_skips_too_close(self):
        today = date.today()
        expiries = (
            (today + timedelta(days=2)).isoformat(),
            (today + timedelta(days=3)).isoformat(),
        )
        # all too close — should fall back to nearest beyond window
        result = _select_expiry(expiries, min_dte=7, max_dte=60)
        assert result is None

    def test_fallback_beyond_max_dte(self):
        today = date.today()
        expiries = (
            (today + timedelta(days=90)).isoformat(),
            (today + timedelta(days=120)).isoformat(),
        )
        result = _select_expiry(expiries, min_dte=7, max_dte=60)
        assert result == expiries[0]  # nearest beyond window

    def test_empty_returns_none(self):
        assert _select_expiry(()) is None

    def test_prefers_nearest_in_window(self):
        today = date.today()
        expiries = (
            (today + timedelta(days=30)).isoformat(),
            (today + timedelta(days=14)).isoformat(),
            (today + timedelta(days=45)).isoformat(),
        )
        result = _select_expiry(expiries)
        assert result == expiries[1]  # 14 days is nearest in window


# ── _find_strike ──────────────────────────────────────────────────────────────

class TestFindStrike:

    def test_finds_atm(self):
        puts = _sample_puts(spot=100.0)
        row = _find_strike(puts, spot=100.0, target_moneyness=1.0)
        assert row is not None
        assert abs(row["strike"] - 100.0) <= 5.0  # within tolerance

    def test_finds_otm(self):
        puts = _sample_puts(spot=100.0)
        row = _find_strike(puts, spot=100.0, target_moneyness=0.80)
        assert row is not None
        assert abs(row["strike"] / 100.0 - 0.80) <= MONEYNESS_TOLERANCE

    def test_returns_none_when_no_candidate(self):
        puts = _sample_puts(spot=100.0)
        # Target way outside available strikes
        row = _find_strike(puts, spot=100.0, target_moneyness=0.20, tolerance=0.01)
        assert row is None

    def test_returns_none_on_empty_df(self):
        empty = pd.DataFrame(columns=["strike", "impliedVolatility", "volume"])
        row = _find_strike(empty, spot=100.0, target_moneyness=1.0)
        assert row is None

    def test_returns_none_on_none_input(self):
        row = _find_strike(None, spot=100.0, target_moneyness=1.0)
        assert row is None


# ── SkewRecord ────────────────────────────────────────────────────────────────

class TestSkewRecord:

    def test_defaults(self):
        rec = SkewRecord(snapshot_date="2026-01-01", ticker="AAPL")
        assert rec.raw_skew is None
        assert rec.norm_skew is None
        assert rec.low_liquidity == 0
        assert rec.fetch_error is None

    def test_low_liquidity_flag(self):
        rec = SkewRecord(
            snapshot_date="2026-01-01", ticker="BA",
            raw_skew=1.5, low_liquidity=1
        )
        assert rec.low_liquidity == 1


# ── SkewMonitor DB ────────────────────────────────────────────────────────────

class TestSkewMonitorDB:

    def test_init_creates_table(self):
        db_path, _ = _make_db()
        conn = sqlite3.connect(db_path)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        conn.close()
        os.unlink(db_path)
        assert "skew_ledger" in tables

    def test_upsert_and_retrieve(self):
        db_path, monitor = _make_db()
        today = date.today().isoformat()
        rec = SkewRecord(
            snapshot_date=today, ticker="AAPL",
            atm_iv=0.25, otm_iv=0.35, raw_skew=1.40,
            spy_raw_skew=1.10, norm_skew=1.27,
            expiry_used="2026-07-17", dte=49,
            atm_strike=200.0, otm_strike=160.0, spot_price=200.0,
            atm_moneyness=1.0, otm_moneyness=0.80,
            atm_volume=200, otm_volume=80,
        )
        monitor._upsert(rec)

        df = monitor.get_rolling_skew("AAPL", days=5)
        os.unlink(db_path)

        assert len(df) == 1
        assert abs(df.iloc[0]["raw_skew"] - 1.40) < 0.001
        assert abs(df.iloc[0]["norm_skew"] - 1.27) < 0.001

    def test_upsert_is_idempotent(self):
        """Second upsert for same (date, ticker) updates rather than duplicates."""
        db_path, monitor = _make_db()
        today = date.today().isoformat()
        rec = SkewRecord(snapshot_date=today, ticker="JPM", raw_skew=1.20)
        monitor._upsert(rec)
        rec.raw_skew = 1.35  # updated value
        monitor._upsert(rec)

        df = monitor.get_rolling_skew("JPM", days=5)
        os.unlink(db_path)

        assert len(df) == 1
        assert abs(df.iloc[0]["raw_skew"] - 1.35) < 0.001

    def test_get_latest_snapshot(self):
        db_path, monitor = _make_db()
        today = date.today().isoformat()
        for ticker, skew in [("AAPL", 1.5), ("GS", 1.2), ("BA", 2.1)]:
            monitor._upsert(SkewRecord(
                snapshot_date=today, ticker=ticker,
                raw_skew=skew, norm_skew=skew / 1.1,
            ))

        df = monitor.get_latest_snapshot(today)
        os.unlink(db_path)

        assert len(df) == 3
        # Should be sorted by norm_skew DESC
        assert df.iloc[0]["ticker"] == "BA"

    def test_rolling_skew_respects_limit(self):
        db_path, monitor = _make_db()
        for i in range(15):
            d = (date.today() - timedelta(days=i)).isoformat()
            monitor._upsert(SkewRecord(snapshot_date=d, ticker="MSFT", raw_skew=1.0 + i * 0.01))

        df = monitor.get_rolling_skew("MSFT", days=10)
        os.unlink(db_path)
        assert len(df) == 10

    def test_silent_migration_existing_db(self):
        """Running SkewMonitor against a DB that already has skew_ledger should not error."""
        db_path, monitor1 = _make_db()
        # Second init on same DB
        monitor2 = SkewMonitor(db_path=db_path)
        monitor2._upsert(SkewRecord(snapshot_date="2026-01-01", ticker="KO"))
        os.unlink(db_path)


# ── compute_raw_skew error paths ──────────────────────────────────────────────

class TestComputeRawSkewErrorPaths:

    def test_yfinance_unavailable(self):
        """When yfinance is not installed, returns error record gracefully."""
        with patch("shiftinnerv.sensors.skew_monitor._YF_AVAILABLE", False):
            rec = compute_raw_skew("AAPL")
        assert rec.raw_skew is None
        assert rec.fetch_error == "yfinance not installed"

    def test_no_price_history(self):
        """Empty history DataFrame → error record."""
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = pd.DataFrame()
        with patch("yfinance.Ticker", return_value=mock_ticker):
            rec = compute_raw_skew("AAPL")
        assert rec.raw_skew is None
        assert "no price history" in rec.fetch_error

    def test_no_options_expirations(self):
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = pd.DataFrame({"Close": [150.0]})
        mock_ticker.options = ()
        with patch("yfinance.Ticker", return_value=mock_ticker):
            rec = compute_raw_skew("AAPL")
        assert rec.fetch_error == "no options expirations available"

    def test_no_suitable_expiry(self):
        today = date.today()
        mock_ticker = MagicMock()
        mock_ticker.history.return_value = pd.DataFrame({"Close": [150.0]})
        # Only expiries within 2 days (below MIN_DTE)
        mock_ticker.options = (
            (today + timedelta(days=1)).isoformat(),
            (today + timedelta(days=2)).isoformat(),
        )
        with patch("yfinance.Ticker", return_value=mock_ticker):
            with patch("shiftinnerv.sensors.skew_monitor._select_expiry", return_value=None):
                rec = compute_raw_skew("AAPL")
        assert rec.fetch_error == "no suitable expiry found"

    def test_low_liquidity_flagged(self):
        today = date.today()
        expiry = (today + timedelta(days=30)).isoformat()
        spot = 100.0
        puts = _sample_puts(spot)
        puts["volume"] = 2  # below MIN_VOLUME

        mock_chain = MagicMock()
        mock_chain.puts = puts

        mock_ticker = MagicMock()
        mock_ticker.history.return_value = pd.DataFrame({"Close": [spot]})
        mock_ticker.options = (expiry,)
        mock_ticker.option_chain.return_value = mock_chain

        with patch("yfinance.Ticker", return_value=mock_ticker):
            rec = compute_raw_skew("AAPL")

        assert rec.low_liquidity == 1
        assert rec.raw_skew is not None  # still computes skew


# ── Normalisation ─────────────────────────────────────────────────────────────

class TestNormalisation:

    def test_norm_skew_computed_correctly(self):
        db_path, monitor = _make_db()
        today = date.today().isoformat()

        spy_skew = 1.10
        ticker_skew = 1.65

        spy_rec = SkewRecord(
            snapshot_date=today, ticker="SPY",
            raw_skew=spy_skew, spy_raw_skew=spy_skew,
            norm_skew=1.0,
        )
        ticker_rec = SkewRecord(
            snapshot_date=today, ticker="BA",
            raw_skew=ticker_skew, spy_raw_skew=spy_skew,
            norm_skew=ticker_skew / spy_skew,
        )
        monitor._upsert(spy_rec)
        monitor._upsert(ticker_rec)

        df = monitor.get_latest_snapshot(today)
        os.unlink(db_path)

        ba_row = df[df["ticker"] == "BA"].iloc[0]
        expected = ticker_skew / spy_skew
        assert abs(ba_row["norm_skew"] - expected) < 0.001

    def test_norm_skew_null_when_spy_unavailable(self):
        """norm_skew should be NULL if SPY fetch failed."""
        db_path, monitor = _make_db()
        today = date.today().isoformat()
        rec = SkewRecord(
            snapshot_date=today, ticker="JPM",
            raw_skew=1.30, spy_raw_skew=None, norm_skew=None,
        )
        monitor._upsert(rec)
        df = monitor.get_rolling_skew("JPM", days=3)
        os.unlink(db_path)
        assert pd.isna(df.iloc[0]["norm_skew"])
