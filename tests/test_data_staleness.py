"""
tests/test_data_staleness.py
Item 5 — Data Staleness Hard-Abort
"""
import os
import time
import tempfile
from pathlib import Path

import pytest

from shiftinnerv.services.data_manager import check_data_staleness, get_stalest_ticker


@pytest.fixture
def temp_data_dir():
    """Temporary directory that is cleaned up after each test."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


# ── check_data_staleness ──────────────────────────────────────────────────────

def test_fresh_data(temp_data_dir):
    """A file touched moments ago must be 'fresh'."""
    csv_path = os.path.join(temp_data_dir, "aapl_daily.csv")
    Path(csv_path).touch()

    results = check_data_staleness(["AAPL"], temp_data_dir, staleness_hours=26)
    assert results["AAPL"] == "fresh"


def test_stale_data(temp_data_dir):
    """A file 48 h old must be 'stale' at the 26 h threshold."""
    csv_path = os.path.join(temp_data_dir, "baba_daily.csv")
    Path(csv_path).touch()
    old_time = time.time() - (48 * 3600)
    os.utime(csv_path, (old_time, old_time))

    results = check_data_staleness(["BABA"], temp_data_dir, staleness_hours=26)
    assert results["BABA"] == "stale"


def test_missing_data(temp_data_dir):
    """A ticker with no CSV on disk must be 'missing'."""
    results = check_data_staleness(["NVDA"], temp_data_dir, staleness_hours=26)
    assert results["NVDA"] == "missing"


def test_mixed_staleness(temp_data_dir):
    """Fresh / stale / missing tickers each return the correct status."""
    # Fresh
    Path(os.path.join(temp_data_dir, "aapl_daily.csv")).touch()

    # Stale (48 h)
    baba_path = os.path.join(temp_data_dir, "baba_daily.csv")
    Path(baba_path).touch()
    old_time = time.time() - (48 * 3600)
    os.utime(baba_path, (old_time, old_time))

    # Missing: NVDA — no file created

    results = check_data_staleness(
        ["AAPL", "BABA", "NVDA"], temp_data_dir, staleness_hours=26
    )

    assert results["AAPL"] == "fresh"
    assert results["BABA"] == "stale"
    assert results["NVDA"] == "missing"


def test_staleness_threshold_fresh(temp_data_dir):
    """A 10 h old file is fresh when the threshold is 26 h."""
    csv_path = os.path.join(temp_data_dir, "jpm_daily.csv")
    Path(csv_path).touch()
    old_time = time.time() - (10 * 3600)
    os.utime(csv_path, (old_time, old_time))

    results = check_data_staleness(["JPM"], temp_data_dir, staleness_hours=26)
    assert results["JPM"] == "fresh"


def test_staleness_threshold_stale(temp_data_dir):
    """The same 10 h old file is stale when the threshold is 5 h."""
    csv_path = os.path.join(temp_data_dir, "jpm_daily.csv")
    Path(csv_path).touch()
    old_time = time.time() - (10 * 3600)
    os.utime(csv_path, (old_time, old_time))

    results = check_data_staleness(["JPM"], temp_data_dir, staleness_hours=5)
    assert results["JPM"] == "stale"


def test_ticker_casing(temp_data_dir):
    """Ticker symbols are case-insensitive when locating the CSV."""
    # File stored as lowercase (per convention)
    Path(os.path.join(temp_data_dir, "gs_daily.csv")).touch()

    results = check_data_staleness(["GS"], temp_data_dir, staleness_hours=26)
    assert results["GS"] == "fresh"


def test_logger_receives_warning(temp_data_dir):
    """A stale file should trigger a logger.warning call."""
    import logging
    from unittest.mock import MagicMock

    csv_path = os.path.join(temp_data_dir, "ms_daily.csv")
    Path(csv_path).touch()
    old_time = time.time() - (50 * 3600)
    os.utime(csv_path, (old_time, old_time))

    mock_logger = MagicMock(spec=logging.Logger)
    check_data_staleness(["MS"], temp_data_dir, staleness_hours=26, logger=mock_logger)

    mock_logger.warning.assert_called_once()
    call_args = mock_logger.warning.call_args[0][0]
    assert "MS" in call_args
    assert "stale" in call_args.lower()


def test_no_logger_prints_warning(temp_data_dir, capsys):
    """Without a logger, a warning should be printed to stdout."""
    csv_path = os.path.join(temp_data_dir, "c_daily.csv")
    Path(csv_path).touch()
    old_time = time.time() - (50 * 3600)
    os.utime(csv_path, (old_time, old_time))

    check_data_staleness(["C"], temp_data_dir, staleness_hours=26)

    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    assert "C" in captured.out


def test_all_fresh_returns_empty_stale_list(temp_data_dir):
    """If every ticker is fresh, the stale list must be empty."""
    for ticker in ["AAPL", "MSFT", "GOOG"]:
        Path(os.path.join(temp_data_dir, f"{ticker.lower()}_daily.csv")).touch()

    results = check_data_staleness(
        ["AAPL", "MSFT", "GOOG"], temp_data_dir, staleness_hours=26
    )
    stale = [t for t, s in results.items() if s != "fresh"]
    assert stale == []


# ── get_stalest_ticker ────────────────────────────────────────────────────────

def test_get_stalest_ticker_all_fresh():
    results = {"AAPL": "fresh", "MSFT": "fresh"}
    ticker, status = get_stalest_ticker(results)
    assert ticker is None
    assert status == "all_fresh"


def test_get_stalest_ticker_has_stale():
    results = {"AAPL": "fresh", "BABA": "stale", "NVDA": "missing"}
    ticker, status = get_stalest_ticker(results)
    assert ticker in {"BABA", "NVDA"}
    assert status in {"stale", "missing"}


def test_get_stalest_ticker_only_missing():
    results = {"NVDA": "missing"}
    ticker, status = get_stalest_ticker(results)
    assert ticker == "NVDA"
    assert status == "missing"
