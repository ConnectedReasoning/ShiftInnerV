"""
Regression test for the skew warm-up counter.

The original bug: briefing_generator computed days-left as `10 - min(history_days)`
across the whole warming-up bucket, so a single ticker stuck at depth 0 pinned the
counter at "10 more days" forever. These tests pin the fixed median-based behaviour
so it can't recur.

Pure functions only — no pandas / yfinance / DB.
"""

from dataclasses import dataclass
from typing import Optional

from shiftinnerv.reporting.briefing_generator import _warmup_progress


@dataclass
class _Sig:
    history_days: int
    signal: str = "INSUFFICIENT_DATA"
    ticker: str = "X"
    z_score: Optional[float] = None
    norm_skew: Optional[float] = None


def test_dead_tickers_do_not_freeze_the_counter():
    # 360 healthy names at depth 3, plus 10 permanently-dead names at depth 0.
    warming = [_Sig(3) for _ in range(360)] + [_Sig(0) for _ in range(10)]
    live, dead, median_depth, d_signal, d_baseline = _warmup_progress(warming)

    assert len(live) == 360
    assert len(dead) == 10
    assert median_depth == 3            # NOT dragged to 0 by the dead tickers
    assert d_signal == 2                # MIN_HISTORY(5) - 3
    assert d_baseline == 7              # LOOKBACK_DAYS(10) - 3


def test_counter_counts_down_as_history_grows():
    earlier = _warmup_progress([_Sig(2) for _ in range(100)])[4]   # d_baseline
    later   = _warmup_progress([_Sig(6) for _ in range(100)])[4]
    assert later < earlier              # estimate must shrink as depth increases


def test_all_dead_reports_zero_live():
    live, dead, median_depth, d_signal, d_baseline = _warmup_progress(
        [_Sig(0) for _ in range(370)]
    )
    assert live == []                   # broken-persistence signature
    assert len(dead) == 370
    assert median_depth == 0


def test_baseline_floors_at_zero_when_fully_warm():
    live, dead, median_depth, d_signal, d_baseline = _warmup_progress(
        [_Sig(12) for _ in range(50)]   # past the 10-day window
    )
    assert d_signal == 0
    assert d_baseline == 0              # max(0, ...) never goes negative
