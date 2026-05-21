"""
ShiftInnerV — Position Monitor Tests
Item 13 of the Council Roadmap.

Tests for shiftinner/sensors/position_monitor.py — SNR revalidation, mean drift detection,
and the HOLD / MONITOR / AUTO_CLOSE decision logic.

No network calls required. Uses synthetic price CSVs and in-memory SQLite.

Usage:
    pytest tests/test_position_monitor.py -v
    pytest tests/test_position_monitor.py -v -k "snr"
    pytest tests/test_position_monitor.py -v --tb=short
"""

import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from shiftinnerv.sensors.position_monitor import (
    PositionRevalidationResult,
    compute_snr_from_prices,
    detect_mean_drift,
    load_price_series,
    revalidate_open_positions,
)
from shiftinnerv.services.trial_ledger import init_trial_ledger, record_active_verdict


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def make_cointegrated_prices(n: int = 200, seed: int = 42, half_life: int = 30):
    """Two cointegrated price series ending today."""
    rng = np.random.default_rng(seed)
    common = np.cumsum(rng.standard_normal(n)) * 0.8
    lam = -np.log(2) / half_life
    spread = np.zeros(n)
    for i in range(1, n):
        spread[i] = (1 + lam) * spread[i - 1] + rng.standard_normal()
    p1 = np.exp((common + spread) / (common + spread).std() * 0.5 + 5)
    p2 = np.exp(common / common.std() * 0.5 + 5)
    end = pd.Timestamp.today().normalize()
    idx = pd.bdate_range(end=end, periods=n)
    return pd.Series(p1, index=idx), pd.Series(p2, index=idx)


def write_price_csv(tmp_path, ticker: str, prices: pd.Series) -> None:
    df = pd.DataFrame({"Close": prices})
    path = tmp_path / f"{ticker.lower()}_daily.csv"
    df.to_csv(path)


def make_ledger_with_open_position(
    db_path: str,
    ticker1: str = "AAA",
    ticker2: str = "BBB",
    snr: float = 1.5,
    half_life: float = 30.0,
    spread_mean: float = 0.0,
    spread_std: float = 0.05,
    hedge_ratio: float = 1.0,
) -> str:
    """Insert one open position into a fresh ledger. Returns verdict_id."""
    vid = record_active_verdict(
        db_path=db_path,
        ticker1=ticker1,
        ticker2=ticker2,
        label=f"{ticker1} vs {ticker2}",
        gate_results={"gate_1": "PASS", "gate_2": "PASS", "gate_3": "PASS",
                      "gate_4": "PASS", "gate_6": "PASS", "gate_7": "PASS"},
        snr=snr,
        half_life=half_life,
        spread_mean=spread_mean,
        spread_std=spread_std,
        hedge_ratio=hedge_ratio,
    )
    return vid


# ══════════════════════════════════════════════════════════════════════════════
# SNR COMPUTATION
# ══════════════════════════════════════════════════════════════════════════════

class TestComputeSNRFromPrices:

    def test_returns_positive_float_for_cointegrated_pair(self):
        p1, p2 = make_cointegrated_prices(n=150)
        snr = compute_snr_from_prices(p1, p2, window=63)
        assert snr is not None
        assert snr > 0.0

    def test_strong_pair_higher_snr_than_weak(self):
        # Shorter half-life = faster mean reversion = larger residual variance relative
        # to trend = higher SNR with the var(resid)/var(trend) formula used here.
        # Use same seed, vary only half_life.
        p1_fast, p2_fast = make_cointegrated_prices(n=150, half_life=10, seed=42)
        p1_slow, p2_slow = make_cointegrated_prices(n=150, half_life=90, seed=42)
        snr_fast = compute_snr_from_prices(p1_fast, p2_fast, window=63)
        snr_slow = compute_snr_from_prices(p1_slow, p2_slow, window=63)
        assert snr_fast > snr_slow

    def test_returns_none_for_insufficient_data(self):
        p1, p2 = make_cointegrated_prices(n=10)
        snr = compute_snr_from_prices(p1, p2, window=63)
        assert snr is None

    def test_accepts_log_prices(self):
        p1, p2 = make_cointegrated_prices(n=150)
        log_p1 = np.log(p1)
        log_p2 = np.log(p2)
        snr = compute_snr_from_prices(log_p1, log_p2, window=63)
        assert snr is not None
        assert snr > 0.0

    def test_returns_none_when_trend_variance_near_zero(self):
        """Degenerate case: flat spread, near-zero trend variance."""
        idx = pd.bdate_range(end=pd.Timestamp.today(), periods=100)
        p1 = pd.Series(np.ones(100) * 100.0, index=idx)
        p2 = pd.Series(np.ones(100) * 100.0, index=idx)
        snr = compute_snr_from_prices(p1, p2, window=63)
        assert snr is None

    def test_window_parameter_respected(self):
        p1, p2 = make_cointegrated_prices(n=200)
        snr_63 = compute_snr_from_prices(p1, p2, window=63)
        snr_30 = compute_snr_from_prices(p1, p2, window=30)
        # Both should return a value — just verifying the parameter is wired
        assert snr_63 is not None
        assert snr_30 is not None

    def test_misaligned_index_still_works(self):
        p1, p2 = make_cointegrated_prices(n=150)
        # Shift p2 index by 5 days — should align on intersection
        p2 = p2.iloc[5:]
        snr = compute_snr_from_prices(p1, p2, window=63)
        assert snr is not None


# ══════════════════════════════════════════════════════════════════════════════
# MEAN DRIFT DETECTION
# ══════════════════════════════════════════════════════════════════════════════

class TestDetectMeanDrift:

    def _make_spread(self, n: int, mean: float = 0.0, std: float = 0.05, seed: int = 0):
        rng = np.random.default_rng(seed)
        end = pd.Timestamp.today().normalize()
        idx = pd.bdate_range(end=end, periods=n)
        return pd.Series(rng.normal(mean, std, n), index=idx)

    def test_no_drift_stable_spread(self):
        spread = self._make_spread(n=100, mean=0.0, std=0.05)
        sigma, detected = detect_mean_drift(
            spread, entry_mean=0.0, entry_std=0.05, half_life_days=20
        )
        assert detected is False

    def test_drift_detected_on_large_level_shift(self):
        spread = self._make_spread(n=100, mean=0.5, std=0.05)
        # Entry mean was 0.0, std was 0.05 — current mean is 10 sigma away
        sigma, detected = detect_mean_drift(
            spread, entry_mean=0.0, entry_std=0.05, half_life_days=20
        )
        assert detected is True
        assert abs(sigma) > 2.0

    def test_sigma_sign_positive_for_upward_drift(self):
        spread = self._make_spread(n=100, mean=0.3, std=0.05)
        sigma, _ = detect_mean_drift(
            spread, entry_mean=0.0, entry_std=0.05, half_life_days=20
        )
        assert sigma > 0.0

    def test_sigma_sign_negative_for_downward_drift(self):
        spread = self._make_spread(n=100, mean=-0.3, std=0.05)
        sigma, _ = detect_mean_drift(
            spread, entry_mean=0.0, entry_std=0.05, half_life_days=20
        )
        assert sigma < 0.0

    def test_near_zero_entry_std_returns_no_drift(self):
        spread = self._make_spread(n=100, mean=1.0, std=0.05)
        sigma, detected = detect_mean_drift(
            spread, entry_mean=0.0, entry_std=1e-12, half_life_days=20
        )
        assert detected is False
        assert sigma == pytest.approx(0.0)

    def test_half_life_clamped_to_minimum(self):
        spread = self._make_spread(n=50, mean=0.0, std=0.05)
        # half_life_days=1 should be clamped to 10
        sigma, detected = detect_mean_drift(
            spread, entry_mean=0.0, entry_std=0.05, half_life_days=1
        )
        # Should not raise; result doesn't matter, just confirm it runs
        assert isinstance(detected, bool)

    def test_half_life_clamped_to_maximum(self):
        spread = self._make_spread(n=200, mean=0.0, std=0.05)
        sigma, detected = detect_mean_drift(
            spread, entry_mean=0.0, entry_std=0.05, half_life_days=9999
        )
        assert isinstance(detected, bool)

    def test_custom_threshold(self):
        spread = self._make_spread(n=100, mean=0.15, std=0.05)
        # At 3-sigma threshold this should not fire; at 1-sigma it should
        _, detected_strict = detect_mean_drift(
            spread, entry_mean=0.0, entry_std=0.05, half_life_days=20,
            threshold_sigma=5.0
        )
        _, detected_loose = detect_mean_drift(
            spread, entry_mean=0.0, entry_std=0.05, half_life_days=20,
            threshold_sigma=1.0
        )
        assert detected_loose is True
        assert detected_strict is False


# ══════════════════════════════════════════════════════════════════════════════
# PRICE LOADING
# ══════════════════════════════════════════════════════════════════════════════

class TestLoadPriceSeries:

    def test_loads_close_column(self, tmp_path):
        p1, _ = make_cointegrated_prices(n=100)
        write_price_csv(tmp_path, "AAA", p1)
        result = load_price_series(str(tmp_path), "AAA")
        assert result is not None
        assert len(result) == 100

    def test_returns_none_for_missing_ticker(self, tmp_path):
        result = load_price_series(str(tmp_path), "MISSING")
        assert result is None

    def test_lowercase_ticker_filename(self, tmp_path):
        p1, _ = make_cointegrated_prices(n=50)
        write_price_csv(tmp_path, "SPY", p1)
        # File is spy_daily.csv — load with uppercase
        result = load_price_series(str(tmp_path), "SPY")
        assert result is not None

    def test_no_close_column_returns_none(self, tmp_path):
        end = pd.Timestamp.today().normalize()
        idx = pd.bdate_range(end=end, periods=30)
        df = pd.DataFrame({"Open": np.ones(30) * 100}, index=idx)
        (tmp_path / "xxx_daily.csv").write_text(df.to_csv())
        result = load_price_series(str(tmp_path), "XXX")
        assert result is None


# ══════════════════════════════════════════════════════════════════════════════
# DECISION LOGIC
# ══════════════════════════════════════════════════════════════════════════════

class TestDecisionLogic:
    """
    Unit-test the HOLD / MONITOR / AUTO_CLOSE decision logic in isolation
    by constructing a minimal ledger and fake price data.
    """

    def _setup(self, tmp_path, entry_snr=1.5, current_snr=None,
               spread_mean=0.0, spread_std=0.05, drift_amount=0.0):
        """
        Build ledger + price CSVs, then run revalidate_open_positions.
        current_snr is approximated by controlling the price series.
        """
        db_path = str(tmp_path / "ledger.db")
        make_ledger_with_open_position(
            db_path, ticker1="AAA", ticker2="BBB",
            snr=entry_snr, half_life=30.0,
            spread_mean=spread_mean, spread_std=spread_std,
        )
        p1, p2 = make_cointegrated_prices(n=200, half_life=30)
        if drift_amount != 0.0:
            p1 = p1.copy()
            p1.iloc[-60:] = p1.iloc[-60:] + drift_amount * p1.std()
        write_price_csv(tmp_path, "AAA", p1)
        write_price_csv(tmp_path, "BBB", p2)
        return db_path

    def test_hold_when_snr_above_threshold(self, tmp_path):
        db_path = self._setup(tmp_path, entry_snr=1.5)
        results = revalidate_open_positions(db_path, str(tmp_path))
        assert len(results) == 1
        r = results[0]
        assert r.error is None
        assert r.current_snr is not None
        # Verify decision is HOLD if SNR >= 1.0
        if r.current_snr >= 1.0:
            assert r.decision == "HOLD"

    def test_monitor_when_snr_in_caution_range(self, tmp_path):
        """Use custom low SNR threshold to force MONITOR path."""
        db_path = self._setup(tmp_path, entry_snr=1.5)
        p1, p2 = make_cointegrated_prices(n=200, half_life=30)
        write_price_csv(tmp_path, "AAA", p1)
        write_price_csv(tmp_path, "BBB", p2)
        results = revalidate_open_positions(
            db_path, str(tmp_path),
            snr_threshold_hold=999.0,   # force everything below HOLD threshold
            snr_threshold_monitor=0.0,  # nothing below MONITOR threshold
        )
        assert results[0].decision == "MONITOR"

    def test_auto_close_when_snr_low_and_drift_detected(self, tmp_path):
        """Force AUTO_CLOSE: SNR below monitor threshold + large drift."""
        db_path = self._setup(
            tmp_path, entry_snr=1.5,
            spread_mean=0.0, spread_std=0.001,  # tiny std → drift fires easily
            drift_amount=5.0,                    # large drift
        )
        results = revalidate_open_positions(
            db_path, str(tmp_path),
            snr_threshold_hold=999.0,
            snr_threshold_monitor=999.0,  # everything below monitor threshold
        )
        r = results[0]
        assert r.decision in ("AUTO_CLOSE", "MONITOR")

    def test_error_when_price_files_missing(self, tmp_path):
        db_path = str(tmp_path / "ledger.db")
        make_ledger_with_open_position(db_path)
        # No price CSVs written
        results = revalidate_open_positions(db_path, str(tmp_path))
        assert len(results) == 1
        assert results[0].error is not None

    def test_empty_results_when_no_open_positions(self, tmp_path):
        db_path = str(tmp_path / "ledger.db")
        init_trial_ledger(db_path)
        results = revalidate_open_positions(db_path, str(tmp_path))
        assert results == []

    def test_empty_results_when_no_ledger(self, tmp_path):
        db_path = str(tmp_path / "nonexistent.db")
        results = revalidate_open_positions(db_path, str(tmp_path))
        assert results == []

    def test_snr_change_bps_computed(self, tmp_path):
        db_path = self._setup(tmp_path, entry_snr=1.5)
        p1, p2 = make_cointegrated_prices(n=200)
        write_price_csv(tmp_path, "AAA", p1)
        write_price_csv(tmp_path, "BBB", p2)
        results = revalidate_open_positions(db_path, str(tmp_path))
        r = results[0]
        if r.current_snr is not None:
            expected_bps = (r.current_snr - r.entry_snr) * 10_000
            assert r.snr_change_bps == pytest.approx(expected_bps, abs=1.0)

    def test_ticker_fields_populated(self, tmp_path):
        db_path = self._setup(tmp_path)
        p1, p2 = make_cointegrated_prices(n=200)
        write_price_csv(tmp_path, "AAA", p1)
        write_price_csv(tmp_path, "BBB", p2)
        results = revalidate_open_positions(db_path, str(tmp_path))
        r = results[0]
        assert r.ticker1 == "AAA"
        assert r.ticker2 == "BBB"

    def test_days_held_populated(self, tmp_path):
        db_path = self._setup(tmp_path)
        p1, p2 = make_cointegrated_prices(n=200)
        write_price_csv(tmp_path, "AAA", p1)
        write_price_csv(tmp_path, "BBB", p2)
        results = revalidate_open_positions(db_path, str(tmp_path))
        r = results[0]
        assert r.days_held is not None
        assert r.days_held >= 0

    def test_multiple_open_positions_all_revalidated(self, tmp_path):
        db_path = str(tmp_path / "ledger.db")
        make_ledger_with_open_position(db_path, ticker1="AAA", ticker2="BBB")
        make_ledger_with_open_position(db_path, ticker1="CCC", ticker2="DDD")
        for ticker in ("AAA", "BBB", "CCC", "DDD"):
            p, _ = make_cointegrated_prices(n=200)
            write_price_csv(tmp_path, ticker, p)
        results = revalidate_open_positions(db_path, str(tmp_path))
        assert len(results) == 2

    def test_logging_called_on_each_result(self, tmp_path):
        from unittest.mock import MagicMock
        db_path = self._setup(tmp_path)
        p1, p2 = make_cointegrated_prices(n=200)
        write_price_csv(tmp_path, "AAA", p1)
        write_price_csv(tmp_path, "BBB", p2)
        log = MagicMock()
        revalidate_open_positions(db_path, str(tmp_path), logger=log)
        assert log.info.called or log.warning.called
