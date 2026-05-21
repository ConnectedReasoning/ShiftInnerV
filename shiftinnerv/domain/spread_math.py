"""
shiftinnerv.domain.spread_math — Pure math for spread analysis.

Extracted from monitor.py in step 2 of the package reorganization.
Every function in this module is a pure function: same inputs always
yield the same outputs, no I/O, no global state, no environment dependencies.

Functions:
    compute_half_life      — Half-life of mean reversion from spread series.
    compute_snr            — Signal-to-noise ratio of a spread (Item 1).
    run_johansen           — Johansen cointegration trace test.
    johansen_approx_pvalue — Approximate p-value for Johansen trace statistic.
    apply_bh_correction    — Benjamini-Hochberg multiple-testing correction.
    compute_score          — Composite score for a pair candidate.
    compute_johansen_trend — Trend stability of trace statistic across lags.
    score_label            — Bucket a score into a human-readable label.
"""

import numpy as np
import pandas as pd
from scipy import stats as _scipy_stats
from statsmodels.regression.linear_model import OLS
from statsmodels.tools import add_constant
from statsmodels.tsa.vector_ar.vecm import coint_johansen


def compute_half_life(spread: pd.Series):
    try:
        from statsmodels.tools import add_constant
        from statsmodels.regression.linear_model import OLS
        valid = pd.concat([spread.diff(), spread.shift(1)], axis=1).dropna()
        valid.columns = ["delta", "lagged"]
        lam = OLS(valid["delta"], add_constant(valid["lagged"])).fit().params["lagged"]
        if lam >= 0:
            return None
        return -np.log(2) / lam
    except Exception:
        return None


def compute_snr(log_p1: pd.Series, log_p2: pd.Series) -> float:
    try:
        from statsmodels.tools import add_constant
        from statsmodels.regression.linear_model import OLS
        fit = OLS(log_p1, add_constant(log_p2)).fit()
        resid = pd.Series(fit.resid, index=log_p1.index)
        trend = log_p1 - resid
        var_s = float(np.var(resid, ddof=1))
        var_n = float(np.var(trend, ddof=1))
        return var_s / var_n if var_n > 1e-10 else float("inf")
    except Exception:
        return None


def run_johansen(log_prices: pd.DataFrame):
    try:
        from statsmodels.tsa.vector_ar.vecm import coint_johansen
        r = coint_johansen(log_prices, det_order=0, k_ar_diff=1)
        trace = r.lr1[0]
        c90   = r.cvt[0, 0]
        c95   = r.cvt[0, 1]
        return trace > c90, trace > c95, trace, c90, c95
    except Exception:
        return None, None, None, None, None


def johansen_approx_pvalue(trace_stat: float, n_series: int = 2) -> float:
    """
    Approximate p-value for the Johansen trace statistic using a chi-squared
    distribution with df = n_series^2. This is a standard approximation
    (asymptotically valid) used when exact p-values are unavailable.

    NOTE: The approximation is conservative for small samples (n < 500).
    Use for ranking and FDR correction only — not as a substitute for the
    critical value gate.

    Returns a p-value in [0, 1].
    """
    df = n_series ** 2
    p = 1.0 - _scipy_stats.chi2.cdf(trace_stat, df=df)
    return float(np.clip(p, 1e-10, 1.0))


def apply_bh_correction(
    results: list,
    alpha: float = 0.05,
    trace_key: str = "trace_stat",
) -> list:
    """
    Apply Benjamini-Hochberg FDR correction to a list of pair results.

    For each result, adds:
      - 'p_approx':       approximate p-value from trace statistic
      - 'p_bh_threshold': BH threshold for this pair's rank
      - 'passes_bh':      True if p_approx <= p_bh_threshold
      - 'bh_flag':        True if pair passes raw gate but fails BH correction

    Pairs without a valid trace_stat are assigned passes_bh=None (not flagged).

    Parameters
    ----------
    results : list of dicts, each with a 'trace_stat' key
    alpha   : FDR level (default 0.05)
    trace_key : key name for trace statistic in result dicts

    Returns the same list with BH fields added in place.
    """
    # Collect valid trace stats with their original indices
    valid = [
        (i, r) for i, r in enumerate(results)
        if r.get(trace_key) is not None and r.get(trace_key, 0) > 0
    ]

    if not valid:
        return results

    m = len(valid)

    # Compute approximate p-values
    for i, r in valid:
        r["p_approx"] = johansen_approx_pvalue(r[trace_key])

    # Sort by p-value ascending for BH step-up procedure
    valid_sorted = sorted(valid, key=lambda x: x[1]["p_approx"])

    # Apply BH thresholds: k/m * alpha for rank k (1-indexed)
    for rank, (i, r) in enumerate(valid_sorted, start=1):
        threshold = (rank / m) * alpha
        r["p_bh_threshold"] = round(threshold, 6)
        r["passes_bh"] = r["p_approx"] <= threshold

    # Flag pairs that pass the raw 95% CI gate but fail BH correction
    for i, r in valid:
        raw_passes = r.get("cointegrated_95", False)
        bh_passes  = r.get("passes_bh", True)  # None means not evaluated
        r["bh_flag"] = bool(raw_passes and not bh_passes)

    return results


def compute_score(trace_stat: float, crit_90: float, crit_95: float,
                  half_life: float, snr: float, episodes: int,
                  trace_trend: float = 0.0,
                  net_pnl_bps: float = None) -> dict:
    """
    Compute a continuous 0-100 pair quality score.

    Components:
      Cointegration score (40pts): trace/crit_95 ratio, capped at 1.5x
      Half-life score     (25pts): optimal 10-30d, degrades to 0 at 120d
      SNR score           (20pts): log-scaled, saturates above 10.0
      Episode score       (10pts): 2 eps = 5pts, 3=7, 4=9, 5+=10
      Trend score          (5pts): trace stat rising toward threshold

    Returns dict with total score and component breakdown.
    """
    # Hard disqualifier — half-life above tradeable horizon
    if half_life is not None and half_life > 120:
        ratio = (trace_stat / crit_95) if (crit_95 and crit_95 > 0) else 0.0
        return {
            "score":        0.0,
            "coint_score":  0.0,
            "hl_score":     0.0,
            "snr_score":    0.0,
            "ep_score":     0.0,
            "trend_score":  0.0,
            "trace_ratio":  round(ratio, 3),
            "suspicious":   False,
            "disqualified": f"hl={half_life:.0f}d>120",
        }

    # Cointegration component — core signal
    if crit_95 and crit_95 > 0:
        ratio = trace_stat / crit_95
    else:
        ratio = 0.0

    # Interpolate across CI levels for finer granularity
    if crit_90 and crit_90 > 0:
        ratio_90 = trace_stat / crit_90
    else:
        ratio_90 = ratio

    # Score: linear up to threshold, bonus for exceeding it
    if ratio >= 1.0:
        coint_score = 40.0  # full marks — cointegrated at 95%
    elif ratio_90 >= 1.0:
        # Passes 90% CI but not 95% — interpolate between 30 and 40
        # based on where trace_stat sits between crit_90 and crit_95
        span = crit_95 - crit_90
        frac = (trace_stat - crit_90) / span if span > 0 else 0.0
        coint_score = 30.0 + max(0.0, min(1.0, frac)) * 10.0
    else:
        coint_score = min(28.0, ratio_90 * 28.0)

    # Half-life component — sweet spot is 10-30 days
    if half_life is None or half_life <= 0:
        hl_score = 0.0
    elif half_life <= 10:
        hl_score = 20.0  # very fast — slightly penalise (noise risk)
    elif half_life <= 30:
        hl_score = 25.0  # optimal
    elif half_life <= 60:
        hl_score = 25.0 - (half_life - 30) / 30 * 10.0
    elif half_life <= 120:
        hl_score = 15.0 - (half_life - 60) / 60 * 15.0
    else:
        hl_score = 0.0

    # SNR component — log-scaled so extreme values don't dominate
    import math
    if snr is None or snr <= 0:
        snr_score = 0.0
    elif snr == float("inf") or snr > 1000:
        snr_score = 5.0  # extreme SNR is suspicious — cap and flag
    else:
        snr_score = min(20.0, math.log1p(snr) / math.log1p(10.0) * 20.0)

    # Episode component
    ep_map = {0: 0, 1: 2, 2: 5, 3: 7, 4: 9}
    ep_score = ep_map.get(episodes, 10.0)

    # Trend component — is trace stat rising toward threshold?
    trend_score = max(0.0, min(5.0, trace_trend * 25.0))

    total = coint_score + hl_score + snr_score + ep_score + trend_score

    # ── Item 3: Penalise marginal or unprofitable edges ──────────────────────
    cost_penalty = 0.0
    cost_note = None
    if net_pnl_bps is not None:
        if net_pnl_bps < 0:
            # Unprofitable: zero out the score entirely
            total = 0.0
            cost_note = f"zeroed (net_pnl={net_pnl_bps:.0f} bps < 0)"
        elif net_pnl_bps < 25:
            # Marginal edge: 10-point penalty
            cost_penalty = 10.0
            total = max(0.0, total - cost_penalty)
            cost_note = f"−{cost_penalty:.0f}pts (net_pnl={net_pnl_bps:.0f} bps marginal)"

    # Suspicious SNR flag — SNR > 1000 means var(trend) ≈ 0:
    # the spread is dominated by noise but the trend component has
    # collapsed, making the SNR ratio meaningless. Hard-cap score
    # to WATCH ceiling (30) so these pairs cannot reach ACTIVE.
    suspicious = snr is not None and snr > 1000
    if suspicious:
        total = min(total, 30.0)

    return {
        "score":        round(total, 1),
        "coint_score":  round(coint_score, 1),
        "hl_score":     round(hl_score, 1),
        "snr_score":    round(snr_score, 1),
        "ep_score":     round(ep_score, 1),
        "trend_score":  round(trend_score, 1),
        "trace_ratio":  round(ratio, 3),
        "suspicious":   suspicious,
        "cost_penalty": cost_penalty,
        "cost_note":    cost_note,
        "disqualified": f"suspicious_snr={snr:.0f}" if suspicious else None,
    }


def compute_johansen_trend(log_prices: pd.DataFrame,
                           windows: list = None) -> float:
    """
    Compute the slope of Johansen trace stat across rolling sub-windows.
    Returns a normalised slope: positive = trace rising toward threshold.
    """
    if windows is None:
        windows = [90, 180, 270, 365]

    n = len(log_prices)
    stats = []
    for w in windows:
        if n < w + 10:
            continue
        sub = log_prices.iloc[-w:]
        try:
            from statsmodels.tsa.vector_ar.vecm import coint_johansen
            r = coint_johansen(sub, det_order=0, k_ar_diff=1)
            stats.append((w, r.lr1[0]))
        except Exception:
            continue

    if len(stats) < 2:
        return 0.0

    # Simple slope: (latest - earliest) / crit_95 normalised
    earliest = stats[0][1]
    latest   = stats[-1][1]
    # Positive if trace is increasing over time
    slope = (latest - earliest) / max(abs(earliest), 1.0)
    return float(np.clip(slope, -1.0, 1.0))


def score_label(score: float) -> str:
    if score >= 75:  return "★★★ PRIME"
    if score >= 60:  return "★★  STRONG"
    if score >= 45:  return "★   SOLID"
    if score >= 30:  return "◆   WATCH"
    if score >= 15:  return "·   WEAK"
    return                   "    NOISE"
