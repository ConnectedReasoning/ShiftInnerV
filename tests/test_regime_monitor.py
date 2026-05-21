"""
ShiftInnerV — Regime Monitor Tests
Item 8 of the Council Roadmap.

Tests for shiftinner/sensors/regime_monitor.py — VIX-driven state machine, pair-SPY
correlation detection, and position sizing multiplier logic.

No network calls required — VIX and price loading are mocked throughout.

Usage:
    pytest tests/test_regime_monitor.py -v
    pytest tests/test_regime_monitor.py -v -k "vix"
    pytest tests/test_regime_monitor.py -v --tb=short
"""

import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from shiftinnerv.sensors.regime_monitor import (
    RegimeDetector,
    RegimeSnapshot,
    RegimeState,
    get_position_size_multiplier,
)


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def make_detector(tmp_path) -> RegimeDetector:
    return RegimeDetector(data_dir=str(tmp_path))


def detector_with_vix(tmp_path, vix: float) -> RegimeDetector:
    """Return a detector whose fetch_vix() always returns a fixed value."""
    d = make_detector(tmp_path)
    d.fetch_vix = MagicMock(return_value=vix)
    return d


def detector_no_correlation(tmp_path, vix: float) -> RegimeDetector:
    """Detector with fixed VIX and no open positions (no correlation check)."""
    d = detector_with_vix(tmp_path, vix)
    d.compute_pair_spy_correlation = MagicMock(return_value=None)
    return d


# ══════════════════════════════════════════════════════════════════════════════
# VIX STATE MACHINE
# ══════════════════════════════════════════════════════════════════════════════

class TestRegimeStateFromVIX:
    """RegimeState and base multiplier driven solely by VIX level."""

    def test_normal_below_elevated_threshold(self, tmp_path):
        d = detector_no_correlation(tmp_path, vix=15.0)
        snap = d.detect_regime()
        assert snap.state == RegimeState.NORMAL
        assert snap.position_size_multiplier == 1.0

    def test_normal_at_boundary_just_below_elevated(self, tmp_path):
        d = detector_no_correlation(tmp_path, vix=19.9)
        snap = d.detect_regime()
        assert snap.state == RegimeState.NORMAL

    def test_elevated_at_threshold(self, tmp_path):
        d = detector_no_correlation(tmp_path, vix=20.0)
        snap = d.detect_regime()
        assert snap.state == RegimeState.ELEVATED
        assert snap.position_size_multiplier == 0.5

    def test_elevated_mid_range(self, tmp_path):
        d = detector_no_correlation(tmp_path, vix=25.0)
        snap = d.detect_regime()
        assert snap.state == RegimeState.ELEVATED
        assert snap.position_size_multiplier == 0.5

    def test_elevated_just_below_high_stress(self, tmp_path):
        d = detector_no_correlation(tmp_path, vix=29.9)
        snap = d.detect_regime()
        assert snap.state == RegimeState.ELEVATED

    def test_high_stress_at_threshold(self, tmp_path):
        d = detector_no_correlation(tmp_path, vix=30.0)
        snap = d.detect_regime()
        assert snap.state == RegimeState.HIGH_STRESS
        assert snap.position_size_multiplier == 0.25

    def test_high_stress_mid_range(self, tmp_path):
        d = detector_no_correlation(tmp_path, vix=35.0)
        snap = d.detect_regime()
        assert snap.state == RegimeState.HIGH_STRESS
        assert snap.position_size_multiplier == 0.25

    def test_high_stress_just_below_crisis(self, tmp_path):
        d = detector_no_correlation(tmp_path, vix=39.9)
        snap = d.detect_regime()
        assert snap.state == RegimeState.HIGH_STRESS

    def test_crisis_at_threshold(self, tmp_path):
        d = detector_no_correlation(tmp_path, vix=40.0)
        snap = d.detect_regime()
        assert snap.state == RegimeState.CRISIS
        assert snap.position_size_multiplier == 0.0

    def test_crisis_above_threshold(self, tmp_path):
        d = detector_no_correlation(tmp_path, vix=55.0)
        snap = d.detect_regime()
        assert snap.state == RegimeState.CRISIS
        assert snap.position_size_multiplier == 0.0

    def test_vix_level_stored_in_snapshot(self, tmp_path):
        d = detector_no_correlation(tmp_path, vix=27.3)
        snap = d.detect_regime()
        assert snap.vix_level == pytest.approx(27.3)

    def test_rationale_contains_vix_value(self, tmp_path):
        d = detector_no_correlation(tmp_path, vix=22.5)
        snap = d.detect_regime()
        assert "22.5" in snap.rationale

    def test_snapshot_timestamp_is_recent(self, tmp_path):
        d = detector_no_correlation(tmp_path, vix=15.0)
        before = datetime.now()
        snap = d.detect_regime()
        assert snap.timestamp >= before


# ══════════════════════════════════════════════════════════════════════════════
# VIX UNAVAILABLE FALLBACK
# ══════════════════════════════════════════════════════════════════════════════

class TestVIXUnavailableFallback:
    """When VIX fetch returns None, default to VIX_DEFAULT_UNAVAILABLE (20.0)."""

    def test_none_vix_defaults_to_elevated_boundary(self, tmp_path):
        d = make_detector(tmp_path)
        d.fetch_vix = MagicMock(return_value=None)
        snap = d.detect_regime()
        # Default 20.0 → ELEVATED
        assert snap.state == RegimeState.ELEVATED
        assert snap.vix_unavailable is True

    def test_unavailable_flag_set(self, tmp_path):
        d = make_detector(tmp_path)
        d.fetch_vix = MagicMock(return_value=None)
        snap = d.detect_regime()
        assert snap.vix_unavailable is True

    def test_unavailable_noted_in_rationale(self, tmp_path):
        d = make_detector(tmp_path)
        d.fetch_vix = MagicMock(return_value=None)
        snap = d.detect_regime()
        assert "VIX UNAVAILABLE" in snap.rationale or "unavailable" in snap.rationale.lower()

    def test_multiplier_is_conservative_on_unavailable(self, tmp_path):
        d = make_detector(tmp_path)
        d.fetch_vix = MagicMock(return_value=None)
        snap = d.detect_regime()
        # Should not be 1.0 — unavailable VIX should not result in full position size
        assert snap.position_size_multiplier <= 0.5


# ══════════════════════════════════════════════════════════════════════════════
# VIX CACHE
# ══════════════════════════════════════════════════════════════════════════════

class TestVIXCache:
    """fetch_vix() caches result for 1 hour."""

    def test_cache_hit_avoids_second_download(self, tmp_path):
        d = make_detector(tmp_path)
        d._last_vix = 18.5
        d._last_vix_fetch = datetime.now() - timedelta(minutes=30)

        with patch("shiftinnerv.sensors.regime_monitor.yf.download") as mock_dl:
            result = d.fetch_vix(use_cache=True)

        mock_dl.assert_not_called()
        assert result == pytest.approx(18.5)

    def test_cache_miss_after_expiry_re_fetches(self, tmp_path):
        d = make_detector(tmp_path)
        d._last_vix = 18.5
        d._last_vix_fetch = datetime.now() - timedelta(hours=2)

        # Simulate yfinance multi-level column format (Close, ^VIX)
        cols = pd.MultiIndex.from_tuples([("Close", "^VIX")])
        mock_df = pd.DataFrame([[21.0]], columns=cols)
        with patch("shiftinnerv.sensors.regime_monitor.yf.download", return_value=mock_df):
            result = d.fetch_vix(use_cache=True)

        assert result == pytest.approx(21.0)

    def test_cache_disabled_always_fetches(self, tmp_path):
        d = make_detector(tmp_path)
        d._last_vix = 18.5
        d._last_vix_fetch = datetime.now()

        cols = pd.MultiIndex.from_tuples([("Close", "^VIX")])
        mock_df = pd.DataFrame([[25.0]], columns=cols)
        with patch("shiftinnerv.sensors.regime_monitor.yf.download", return_value=mock_df):
            result = d.fetch_vix(use_cache=False)

        assert result == pytest.approx(25.0)

    def test_empty_download_returns_cached(self, tmp_path):
        d = make_detector(tmp_path)
        d._last_vix = 17.0
        d._last_vix_fetch = datetime.now() - timedelta(hours=2)

        with patch("shiftinnerv.sensors.regime_monitor.yf.download", return_value=pd.DataFrame()):
            result = d.fetch_vix(use_cache=True)

        assert result == pytest.approx(17.0)

    def test_download_exception_returns_cached(self, tmp_path):
        d = make_detector(tmp_path)
        d._last_vix = 22.0
        d._last_vix_fetch = datetime.now() - timedelta(hours=2)

        with patch("shiftinnerv.sensors.regime_monitor.yf.download", side_effect=Exception("network error")):
            result = d.fetch_vix(use_cache=True)

        assert result == pytest.approx(22.0)

    def test_download_exception_no_cache_returns_none(self, tmp_path):
        d = make_detector(tmp_path)
        # No prior cache
        with patch("shiftinnerv.sensors.regime_monitor.yf.download", side_effect=Exception("network error")):
            result = d.fetch_vix(use_cache=True)

        assert result is None


# ══════════════════════════════════════════════════════════════════════════════
# CORRELATION REGIME
# ══════════════════════════════════════════════════════════════════════════════

class TestCorrelationRegime:
    """Pair-SPY correlation stacks an additional 0.5x on the base multiplier."""

    def _make_positions(self):
        return [("LMT", "NOC"), ("BABA", "JD"), ("GLD", "SLV"), ("XLE", "XOP")]

    def test_no_correlation_regime_when_no_open_positions(self, tmp_path):
        d = detector_with_vix(tmp_path, vix=15.0)
        snap = d.detect_regime(open_positions=[])
        assert snap.correlation_regime is False
        assert snap.position_size_multiplier == 1.0

    def test_no_correlation_regime_when_correlations_low(self, tmp_path):
        d = detector_with_vix(tmp_path, vix=15.0)
        # All correlations below threshold
        d.compute_pair_spy_correlation = MagicMock(return_value=0.3)
        snap = d.detect_regime(open_positions=self._make_positions())
        assert snap.correlation_regime is False
        assert snap.position_size_multiplier == 1.0

    def test_correlation_regime_fires_when_majority_correlated(self, tmp_path):
        d = detector_with_vix(tmp_path, vix=15.0)
        # 3 of 4 pairs above threshold → > 50%
        call_count = [0]
        def corr_side(*args, **kwargs):
            call_count[0] += 1
            return 0.85 if call_count[0] <= 3 else 0.2
        d.compute_pair_spy_correlation = MagicMock(side_effect=corr_side)
        snap = d.detect_regime(open_positions=self._make_positions())
        assert snap.correlation_regime is True

    def test_correlation_regime_halves_multiplier(self, tmp_path):
        d = detector_with_vix(tmp_path, vix=15.0)
        d.compute_pair_spy_correlation = MagicMock(return_value=0.85)
        snap = d.detect_regime(open_positions=self._make_positions())
        # NORMAL (1.0) * 0.5 = 0.5
        assert snap.position_size_multiplier == pytest.approx(0.5)

    def test_correlation_regime_stacks_on_elevated(self, tmp_path):
        d = detector_with_vix(tmp_path, vix=25.0)
        d.compute_pair_spy_correlation = MagicMock(return_value=0.85)
        snap = d.detect_regime(open_positions=self._make_positions())
        # ELEVATED (0.5) * 0.5 = 0.25
        assert snap.position_size_multiplier == pytest.approx(0.25)

    def test_correlation_regime_stacks_on_high_stress(self, tmp_path):
        d = detector_with_vix(tmp_path, vix=35.0)
        d.compute_pair_spy_correlation = MagicMock(return_value=0.85)
        snap = d.detect_regime(open_positions=self._make_positions())
        # HIGH_STRESS (0.25) * 0.5 = 0.125
        assert snap.position_size_multiplier == pytest.approx(0.125)

    def test_correlation_regime_does_not_stack_on_crisis(self, tmp_path):
        """CRISIS is already 0.0 — correlation regime should not change it."""
        d = detector_with_vix(tmp_path, vix=45.0)
        d.compute_pair_spy_correlation = MagicMock(return_value=0.85)
        snap = d.detect_regime(open_positions=self._make_positions())
        assert snap.state == RegimeState.CRISIS
        assert snap.position_size_multiplier == 0.0

    def test_correlated_pairs_list_populated(self, tmp_path):
        d = detector_with_vix(tmp_path, vix=15.0)
        d.compute_pair_spy_correlation = MagicMock(return_value=0.85)
        positions = [("LMT", "NOC"), ("GLD", "SLV")]
        snap = d.detect_regime(open_positions=positions)
        assert len(snap.correlated_pairs) == 2

    def test_none_correlation_skipped(self, tmp_path):
        """compute_pair_spy_correlation returning None should not count as correlated."""
        d = detector_with_vix(tmp_path, vix=15.0)
        d.compute_pair_spy_correlation = MagicMock(return_value=None)
        snap = d.detect_regime(open_positions=self._make_positions())
        assert snap.correlation_regime is False
        assert len(snap.correlated_pairs) == 0


# ══════════════════════════════════════════════════════════════════════════════
# PRICE LOADING FROM CSV
# ══════════════════════════════════════════════════════════════════════════════

class TestLoadPricesFromCSV:
    """_load_prices() reads from pipeline CSVs when present."""

    def _write_csv(self, tmp_path, ticker: str, n: int = 30) -> None:
        end = pd.Timestamp.today().normalize()
        idx = pd.bdate_range(end=end, periods=n)
        df = pd.DataFrame({"Close": np.linspace(100, 110, n)}, index=idx)
        path = tmp_path / f"{ticker.lower()}_daily.csv"
        df.to_csv(path)

    def test_loads_close_column(self, tmp_path):
        self._write_csv(tmp_path, "SPY", n=30)
        d = make_detector(tmp_path)
        prices = d._load_prices("SPY", window=20)
        assert prices is not None
        assert len(prices) == 20

    def test_returns_none_for_missing_ticker(self, tmp_path):
        d = make_detector(tmp_path)
        with patch("shiftinnerv.sensors.regime_monitor.yf.download", return_value=pd.DataFrame()):
            prices = d._load_prices("NONEXISTENT", window=20)
        assert prices is None

    def test_ticker_case_insensitive(self, tmp_path):
        self._write_csv(tmp_path, "spy", n=30)
        d = make_detector(tmp_path)
        prices = d._load_prices("SPY", window=20)
        assert prices is not None


# ══════════════════════════════════════════════════════════════════════════════
# PAIR-SPY CORRELATION COMPUTATION
# ══════════════════════════════════════════════════════════════════════════════

class TestComputePairSPYCorrelation:
    """compute_pair_spy_correlation() returns float or None."""

    def _write_price_csv(self, tmp_path, ticker: str, prices: np.ndarray) -> None:
        end = pd.Timestamp.today().normalize()
        n = len(prices)
        idx = pd.bdate_range(end=end, periods=n)
        df = pd.DataFrame({"Close": prices}, index=idx)
        (tmp_path / f"{ticker.lower()}_daily.csv").write_text(df.to_csv())

    def test_returns_float_with_valid_data(self, tmp_path):
        rng = np.random.default_rng(42)
        n = 30
        base = np.cumsum(rng.standard_normal(n)) + 100
        self._write_price_csv(tmp_path, "AAA", base + rng.standard_normal(n) * 0.5)
        self._write_price_csv(tmp_path, "BBB", base * 0.9 + rng.standard_normal(n) * 0.5)
        self._write_price_csv(tmp_path, "SPY", base + rng.standard_normal(n) * 0.3)

        d = make_detector(tmp_path)
        corr = d.compute_pair_spy_correlation("AAA", "BBB", window=20)
        assert corr is not None
        assert -1.0 <= corr <= 1.0

    def test_returns_none_when_prices_missing(self, tmp_path):
        d = make_detector(tmp_path)
        with patch("shiftinnerv.sensors.regime_monitor.yf.download", return_value=pd.DataFrame()):
            corr = d.compute_pair_spy_correlation("MISSING1", "MISSING2", window=20)
        assert corr is None

    def test_returns_none_on_insufficient_data(self, tmp_path):
        rng = np.random.default_rng(7)
        # Only 3 rows — below any reasonable minimum
        tiny = rng.standard_normal(3) + 100
        self._write_price_csv(tmp_path, "AAA", tiny)
        self._write_price_csv(tmp_path, "BBB", tiny * 0.9)
        self._write_price_csv(tmp_path, "SPY", tiny * 1.1)

        d = make_detector(tmp_path)
        corr = d.compute_pair_spy_correlation("AAA", "BBB", window=20)
        assert corr is None


# ══════════════════════════════════════════════════════════════════════════════
# SNAPSHOT STRUCTURE
# ══════════════════════════════════════════════════════════════════════════════

class TestSnapshotStructure:
    """RegimeSnapshot fields are always populated."""

    def test_all_fields_present(self, tmp_path):
        d = detector_no_correlation(tmp_path, vix=18.0)
        snap = d.detect_regime()
        assert isinstance(snap.state, RegimeState)
        assert isinstance(snap.timestamp, datetime)
        assert isinstance(snap.vix_level, float)
        assert isinstance(snap.correlation_regime, bool)
        assert isinstance(snap.correlated_pairs, list)
        assert isinstance(snap.position_size_multiplier, float)
        assert isinstance(snap.rationale, str)
        assert isinstance(snap.vix_unavailable, bool)

    def test_get_position_size_multiplier_helper(self, tmp_path):
        d = detector_no_correlation(tmp_path, vix=30.0)
        snap = d.detect_regime()
        assert get_position_size_multiplier(snap) == snap.position_size_multiplier

    def test_logger_called_on_detect(self, tmp_path):
        log = MagicMock()
        d = detector_no_correlation(tmp_path, vix=15.0)
        d.detect_regime(logger=log)
        log.info.assert_called()


# ══════════════════════════════════════════════════════════════════════════════
# LOGGING INTEGRATION
# ══════════════════════════════════════════════════════════════════════════════

class TestLoggingIntegration:
    """Detector logs correctly at each state transition."""

    def test_no_error_with_real_logger(self, tmp_path):
        log = logging.getLogger("test_regime")
        d = detector_no_correlation(tmp_path, vix=22.0)
        snap = d.detect_regime(logger=log)
        assert snap is not None

    def test_no_logger_does_not_crash(self, tmp_path):
        d = make_detector(tmp_path)
        d.fetch_vix = MagicMock(return_value=15.0)
        d.compute_pair_spy_correlation = MagicMock(return_value=None)
        snap = d.detect_regime(logger=None)
        assert snap is not None
