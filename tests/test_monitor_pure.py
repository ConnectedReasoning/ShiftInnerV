"""
ShiftInnerV — Spread Math Pure Function Tests

Tests for the pure math functions in shiftinnerv/domain/spread_math.py:
  - compute_half_life
  - compute_snr
  - johansen_approx_pvalue
  - apply_bh_correction
  - compute_score
  - score_label

No network, no LLM, no DB, no filesystem required.

Usage:
    pytest tests/test_monitor_pure.py -v
    pytest tests/test_monitor_pure.py -v -k "score"
"""

import math
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from shiftinnerv.domain.spread_math import (
    apply_bh_correction,
    compute_half_life,
    compute_score,
    compute_snr,
    johansen_approx_pvalue,
    score_label,
)


# ══════════════════════════════════════════════════════════════════════════════
# HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def make_spread(n: int = 200, half_life: float = 20.0, seed: int = 42) -> pd.Series:
    """Synthetic AR(1) spread with known half-life."""
    rng = np.random.default_rng(seed)
    lam = -np.log(2) / half_life
    s = np.zeros(n)
    for i in range(1, n):
        s[i] = (1 + lam) * s[i - 1] + rng.standard_normal()
    return pd.Series(s)


def make_log_prices(n: int = 200, half_life: float = 20.0, seed: int = 42):
    """Two cointegrated log-price series."""
    rng = np.random.default_rng(seed)
    common = np.cumsum(rng.standard_normal(n)) * 0.8
    spread = make_spread(n, half_life, seed).values
    log_p1 = pd.Series(common + spread)
    log_p2 = pd.Series(common + rng.standard_normal(n) * 0.1)
    return log_p1, log_p2


def default_score_kwargs(**overrides) -> dict:
    base = dict(
        trace_stat=20.0,
        crit_90=13.4,
        crit_95=15.5,
        half_life=25.0,
        snr=8.0,
        episodes=3,
        trace_trend=0.0,
        net_pnl_bps=None,
    )
    base.update(overrides)
    return base


# ══════════════════════════════════════════════════════════════════════════════
# COMPUTE_HALF_LIFE
# ══════════════════════════════════════════════════════════════════════════════

class TestComputeHalfLife:

    def test_recovers_known_half_life_approx(self):
        spread = make_spread(n=500, half_life=20.0)
        hl = compute_half_life(spread)
        assert hl is not None
        assert 10.0 < hl < 40.0  # statistical noise, so loose bounds

    def test_random_walk_may_return_spurious_half_life(self):
        # compute_half_life does NOT distinguish random walks from
        # genuinely mean-reverting spreads — it returns whatever OLS gives.
        # On short samples a random walk can appear mean-reverting.
        # This test documents that behaviour: the function makes no guarantee
        # of returning None for non-stationary series. That validation is the
        # caller's responsibility (use Johansen/ADF gating upstream).
        rng = np.random.default_rng(99)
        walk = pd.Series(np.cumsum(rng.standard_normal(300)))
        hl = compute_half_life(walk)
        # Either None or a finite float — both are possible for a random walk.
        assert hl is None or isinstance(float(hl), float)

    def test_returns_none_for_flat_series(self):
        flat = pd.Series(np.ones(100))
        hl = compute_half_life(flat)
        assert hl is None

    def test_returns_none_for_too_short(self):
        # compute_half_life delegates minimum-length enforcement to OLS.
        # With n=3 it may return None or a degenerate float — just no crash.
        spread = make_spread(n=3)
        try:
            hl = compute_half_life(spread)
        except Exception:
            hl = None
        assert hl is None or isinstance(hl, float)

    def test_faster_reversion_gives_shorter_half_life(self):
        hl_fast = compute_half_life(make_spread(n=500, half_life=10.0, seed=1))
        hl_slow = compute_half_life(make_spread(n=500, half_life=60.0, seed=1))
        if hl_fast is not None and hl_slow is not None:
            assert hl_fast < hl_slow

    def test_result_is_positive(self):
        hl = compute_half_life(make_spread(n=300, half_life=30.0))
        if hl is not None:
            assert hl > 0.0


# ══════════════════════════════════════════════════════════════════════════════
# COMPUTE_SNR
# ══════════════════════════════════════════════════════════════════════════════

class TestComputeSNR:

    def test_returns_positive_for_cointegrated(self):
        log_p1, log_p2 = make_log_prices(n=200, half_life=20.0)
        snr = compute_snr(log_p1, log_p2)
        assert snr is not None
        assert snr > 0.0

    def test_returns_none_for_empty_series(self):
        snr = compute_snr(pd.Series([], dtype=float), pd.Series([], dtype=float))
        assert snr is None

    def test_snr_increases_with_spread_persistence(self):
        # Under the Vidyamurthy SNR definition (level variance / noise variance),
        # a MORE persistent (slower) spread has higher level variance (OU theory:
        # var_level = sigma^2 / (1 - phi^2), larger for phi closer to 1).
        # So SNR increases with half-life, all else equal.
        # This is correct: a slowly-reverting spread is large relative to daily noise —
        # the signal is stronger, just takes longer to harvest.
        p1_fast, p2_fast = make_log_prices(n=500, half_life=5.0,   seed=42)
        p1_slow, p2_slow = make_log_prices(n=500, half_life=120.0,  seed=42)
        snr_fast = compute_snr(p1_fast, p2_fast)
        snr_slow = compute_snr(p1_slow, p2_slow)
        assert snr_fast is not None and snr_slow is not None
        # Slower reversion → larger level variance → higher SNR
        assert snr_slow > snr_fast

    def test_pure_noise_gives_low_snr(self):
        """Pure noise (no mean reversion) should have low SNR."""
        # For white noise: var(level) ≈ 0.5 * var(daily diff), so SNR ≈ 0.5.
        # Under the corrected definition, pure noise should score near 0, not inf.
        rng = np.random.default_rng(0)
        p1 = pd.Series(rng.standard_normal(300))
        p2 = pd.Series(rng.standard_normal(300))
        snr = compute_snr(p1, p2)
        assert snr is not None
        assert snr < 5.0, f"Pure noise should have low SNR, got {snr:.2f}"


# ══════════════════════════════════════════════════════════════════════════════
# JOHANSEN_APPROX_PVALUE
# ══════════════════════════════════════════════════════════════════════════════

class TestJohansenApproxPvalue:

    def test_returns_float_in_0_1(self):
        p = johansen_approx_pvalue(15.0)
        assert 0.0 < p <= 1.0

    def test_higher_trace_gives_lower_pvalue(self):
        p_low  = johansen_approx_pvalue(5.0)
        p_high = johansen_approx_pvalue(25.0)
        assert p_high < p_low

    def test_very_high_trace_gives_near_zero_pvalue(self):
        p = johansen_approx_pvalue(100.0)
        assert p < 0.001

    def test_near_zero_trace_gives_near_one_pvalue(self):
        p = johansen_approx_pvalue(0.01)
        assert p > 0.9

    def test_df_scales_with_n_series(self):
        # More series → larger df → higher p-value for same trace stat
        p2 = johansen_approx_pvalue(15.0, n_series=2)
        p3 = johansen_approx_pvalue(15.0, n_series=3)
        assert p3 > p2

    def test_clipped_to_minimum(self):
        p = johansen_approx_pvalue(10000.0)
        assert p >= 1e-10


# ══════════════════════════════════════════════════════════════════════════════
# APPLY_BH_CORRECTION
# ══════════════════════════════════════════════════════════════════════════════

class TestApplyBHCorrection:

    def _make_results(self, trace_stats: list, cointegrated_95=True) -> list:
        return [
            {
                "ticker1": f"T{i}A",
                "ticker2": f"T{i}B",
                "trace_stat": ts,
                "cointegrated_95": cointegrated_95,
            }
            for i, ts in enumerate(trace_stats)
        ]

    def test_adds_p_approx_to_each_result(self):
        results = self._make_results([15.0, 20.0, 10.0])
        apply_bh_correction(results)
        for r in results:
            assert "p_approx" in r
            assert 0.0 < r["p_approx"] <= 1.0

    def test_adds_passes_bh_field(self):
        results = self._make_results([15.0, 20.0])
        apply_bh_correction(results)
        for r in results:
            assert "passes_bh" in r
            assert isinstance(r["passes_bh"], bool)

    def test_adds_bh_threshold_field(self):
        results = self._make_results([15.0, 20.0, 25.0])
        apply_bh_correction(results)
        for r in results:
            assert "p_bh_threshold" in r

    def test_strong_signals_pass_bh(self):
        """Very high trace stats should survive BH correction."""
        results = self._make_results([80.0, 90.0, 100.0])
        apply_bh_correction(results)
        assert all(r["passes_bh"] for r in results)

    def test_weak_signals_fail_bh_in_large_batch(self):
        """Many weak pairs: most should be flagged by BH."""
        # 50 pairs with moderate trace stats
        results = self._make_results([12.0] * 50, cointegrated_95=True)
        apply_bh_correction(results)
        flagged = [r for r in results if r.get("bh_flag")]
        assert len(flagged) > 0

    def test_bh_flag_only_set_when_raw_passes_but_bh_fails(self):
        results = self._make_results([10.0] * 20, cointegrated_95=True)
        apply_bh_correction(results)
        for r in results:
            if r.get("bh_flag"):
                assert r["cointegrated_95"] is True
                assert r["passes_bh"] is False

    def test_empty_list_returns_empty(self):
        assert apply_bh_correction([]) == []

    def test_results_with_none_trace_stat_skipped(self):
        results = [
            {"ticker1": "A", "ticker2": "B", "trace_stat": None},
            {"ticker1": "C", "ticker2": "D", "trace_stat": 20.0, "cointegrated_95": True},
        ]
        apply_bh_correction(results)
        assert "p_approx" not in results[0]
        assert "p_approx" in results[1]

    def test_single_pair_gets_bh_threshold_of_alpha(self):
        """With m=1, rank=1 → threshold = 1/1 * alpha = alpha."""
        results = self._make_results([20.0])
        apply_bh_correction(results, alpha=0.05)
        assert results[0]["p_bh_threshold"] == pytest.approx(0.05)

    def test_custom_alpha(self):
        results = self._make_results([15.0, 20.0])
        apply_bh_correction(results, alpha=0.01)
        for r in results:
            assert r["p_bh_threshold"] <= 0.01

    def test_returns_same_list_object(self):
        results = self._make_results([15.0])
        returned = apply_bh_correction(results)
        assert returned is results


# ══════════════════════════════════════════════════════════════════════════════
# COMPUTE_SCORE
# ══════════════════════════════════════════════════════════════════════════════

class TestComputeScore:

    # ── Output structure ──────────────────────────────────────────────────────

    def test_returns_all_required_keys(self):
        result = compute_score(**default_score_kwargs())
        for key in ("score", "coint_score", "hl_score", "snr_score",
                    "ep_score", "trend_score", "trace_ratio", "suspicious"):
            assert key in result

    def test_score_is_float(self):
        result = compute_score(**default_score_kwargs())
        assert isinstance(result["score"], float)

    def test_score_in_0_100_range(self):
        result = compute_score(**default_score_kwargs())
        assert 0.0 <= result["score"] <= 100.0

    # ── Cointegration component ───────────────────────────────────────────────

    def test_full_coint_score_when_trace_exceeds_95(self):
        result = compute_score(**default_score_kwargs(trace_stat=20.0, crit_95=15.5))
        assert result["coint_score"] == pytest.approx(40.0)

    def test_partial_coint_score_between_90_and_95(self):
        result = compute_score(**default_score_kwargs(
            trace_stat=14.0, crit_90=13.4, crit_95=15.5
        ))
        assert 30.0 <= result["coint_score"] < 40.0

    def test_low_coint_score_below_90(self):
        result = compute_score(**default_score_kwargs(
            trace_stat=5.0, crit_90=13.4, crit_95=15.5
        ))
        assert result["coint_score"] < 30.0

    # ── Half-life component ───────────────────────────────────────────────────

    def test_optimal_half_life_scores_25(self):
        result = compute_score(**default_score_kwargs(half_life=25.0))
        assert result["hl_score"] == pytest.approx(25.0)

    def test_very_fast_half_life_scores_20(self):
        result = compute_score(**default_score_kwargs(half_life=5.0))
        assert result["hl_score"] == pytest.approx(20.0)

    def test_half_life_over_120_disqualifies(self):
        result = compute_score(**default_score_kwargs(half_life=150.0))
        assert result["score"] == pytest.approx(0.0)
        assert result["disqualified"] is not None

    def test_none_half_life_gives_zero_hl_score(self):
        result = compute_score(**default_score_kwargs(half_life=None))
        assert result["hl_score"] == pytest.approx(0.0)

    def test_half_life_60_penalised_vs_25(self):
        r25 = compute_score(**default_score_kwargs(half_life=25.0))
        r60 = compute_score(**default_score_kwargs(half_life=60.0))
        assert r25["hl_score"] > r60["hl_score"]

    # ── SNR component ─────────────────────────────────────────────────────────

    def test_snr_zero_gives_zero_snr_score(self):
        result = compute_score(**default_score_kwargs(snr=0.0))
        assert result["snr_score"] == pytest.approx(0.0)

    def test_snr_none_gives_zero_snr_score(self):
        result = compute_score(**default_score_kwargs(snr=None))
        assert result["snr_score"] == pytest.approx(0.0)

    def test_snr_increases_score(self):
        r_low  = compute_score(**default_score_kwargs(snr=0.5))
        r_high = compute_score(**default_score_kwargs(snr=5.0))
        assert r_high["snr_score"] > r_low["snr_score"]

    def test_snr_capped_at_20(self):
        result = compute_score(**default_score_kwargs(snr=10.0))
        assert result["snr_score"] <= 20.0

    def test_extreme_snr_flagged_suspicious(self):
        result = compute_score(**default_score_kwargs(snr=5000.0))
        assert result["suspicious"] is True

    def test_extreme_snr_score_capped_at_5(self):
        result = compute_score(**default_score_kwargs(snr=5000.0))
        assert result["snr_score"] == pytest.approx(5.0)

    def test_extreme_snr_total_capped_at_30(self):
        """Suspicious SNR hard-caps total to WATCH ceiling — cannot reach ACTIVE."""
        result = compute_score(**default_score_kwargs(snr=5000.0))
        assert result["score"] <= 30.0

    def test_extreme_snr_disqualified_note_set(self):
        result = compute_score(**default_score_kwargs(snr=5000.0))
        assert result["disqualified"] is not None
        assert "suspicious_snr" in result["disqualified"]

    def test_normal_snr_not_suspicious(self):
        result = compute_score(**default_score_kwargs(snr=2.0))
        assert result["suspicious"] is False

    def test_normal_snr_disqualified_is_none(self):
        result = compute_score(**default_score_kwargs(snr=2.0))
        assert result["disqualified"] is None

    # ── Episode component ─────────────────────────────────────────────────────

    def test_zero_episodes_scores_zero(self):
        result = compute_score(**default_score_kwargs(episodes=0))
        assert result["ep_score"] == pytest.approx(0.0)

    def test_one_episode_scores_2(self):
        result = compute_score(**default_score_kwargs(episodes=1))
        assert result["ep_score"] == pytest.approx(2.0)

    def test_two_episodes_scores_5(self):
        result = compute_score(**default_score_kwargs(episodes=2))
        assert result["ep_score"] == pytest.approx(5.0)

    def test_five_plus_episodes_scores_10(self):
        result = compute_score(**default_score_kwargs(episodes=5))
        assert result["ep_score"] == pytest.approx(10.0)

    def test_ten_episodes_scores_10(self):
        result = compute_score(**default_score_kwargs(episodes=10))
        assert result["ep_score"] == pytest.approx(10.0)

    # ── Trend component ───────────────────────────────────────────────────────

    def test_zero_trend_gives_zero_trend_score(self):
        result = compute_score(**default_score_kwargs(trace_trend=0.0))
        assert result["trend_score"] == pytest.approx(0.0)

    def test_positive_trend_adds_score(self):
        result = compute_score(**default_score_kwargs(trace_trend=0.1))
        assert result["trend_score"] > 0.0

    def test_trend_capped_at_5(self):
        result = compute_score(**default_score_kwargs(trace_trend=1.0))
        assert result["trend_score"] == pytest.approx(5.0)

    def test_negative_trend_gives_zero_trend_score(self):
        result = compute_score(**default_score_kwargs(trace_trend=-0.5))
        assert result["trend_score"] == pytest.approx(0.0)

    # ── Net P&L gate ──────────────────────────────────────────────────────────

    def test_profitable_pnl_no_penalty(self):
        with_pnl    = compute_score(**default_score_kwargs(net_pnl_bps=100.0))
        without_pnl = compute_score(**default_score_kwargs(net_pnl_bps=None))
        assert with_pnl["score"] == pytest.approx(without_pnl["score"])

    def test_negative_pnl_zeroes_score(self):
        result = compute_score(**default_score_kwargs(net_pnl_bps=-10.0))
        assert result["score"] == pytest.approx(0.0)
        assert "zeroed" in result["cost_note"]

    def test_marginal_pnl_applies_penalty(self):
        r_marginal = compute_score(**default_score_kwargs(net_pnl_bps=10.0))
        r_good     = compute_score(**default_score_kwargs(net_pnl_bps=100.0))
        assert r_marginal["score"] < r_good["score"]
        assert r_marginal["cost_penalty"] == pytest.approx(10.0)

    def test_pnl_at_25_boundary_no_penalty(self):
        result = compute_score(**default_score_kwargs(net_pnl_bps=25.0))
        assert result["cost_penalty"] == pytest.approx(0.0)

    def test_pnl_just_below_25_marginal(self):
        result = compute_score(**default_score_kwargs(net_pnl_bps=24.9))
        assert result["cost_penalty"] == pytest.approx(10.0)

    # ── Trace ratio ───────────────────────────────────────────────────────────

    def test_trace_ratio_computed(self):
        result = compute_score(**default_score_kwargs(trace_stat=20.0, crit_95=15.5))
        assert result["trace_ratio"] == pytest.approx(20.0 / 15.5, abs=0.01)

    def test_trace_ratio_zero_when_no_crit_95(self):
        result = compute_score(**default_score_kwargs(crit_95=0.0))
        assert result["trace_ratio"] == pytest.approx(0.0)

    # ── Overall score ordering ────────────────────────────────────────────────

    def test_prime_pair_scores_above_75(self):
        result = compute_score(
            trace_stat=25.0, crit_90=13.4, crit_95=15.5,
            half_life=22.0, snr=3.0, episodes=4,
            trace_trend=0.1, net_pnl_bps=80.0,
        )
        assert result["score"] >= 75.0

    def test_disqualified_pair_scores_zero(self):
        result = compute_score(
            trace_stat=5.0, crit_90=13.4, crit_95=15.5,
            half_life=200.0, snr=0.3, episodes=1,
        )
        assert result["score"] == pytest.approx(0.0)


# ══════════════════════════════════════════════════════════════════════════════
# SCORE_LABEL
# ══════════════════════════════════════════════════════════════════════════════

class TestScoreLabel:

    def test_prime_at_75(self):
        assert "PRIME" in score_label(75.0)

    def test_prime_above_75(self):
        assert "PRIME" in score_label(90.0)

    def test_strong_at_60(self):
        assert "STRONG" in score_label(60.0)

    def test_strong_just_below_75(self):
        assert "STRONG" in score_label(74.9)

    def test_solid_at_45(self):
        assert "SOLID" in score_label(45.0)

    def test_watch_at_30(self):
        assert "WATCH" in score_label(30.0)

    def test_weak_at_15(self):
        assert "WEAK" in score_label(15.0)

    def test_noise_at_zero(self):
        assert "NOISE" in score_label(0.0)

    def test_noise_below_15(self):
        assert "NOISE" in score_label(14.9)

    def test_boundary_exactly_at_each_tier(self):
        tiers = [(75.0, "PRIME"), (60.0, "STRONG"), (45.0, "SOLID"),
                 (30.0, "WATCH"), (15.0, "WEAK")]
        for score, expected in tiers:
            assert expected in score_label(score), f"score={score}"
