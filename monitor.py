#!/usr/bin/env python3
"""
ShiftInnerV — Layer 1 Monitor

Always-on lightweight watcher. No LLM, no agents, no Ollama.
Runs rolling correlation across all pairs in the compositions folder,
logs anomalies to SQLite, and writes yaml files for anomalies.

Usage:
    python monitor.py                          # run once and exit
    python monitor.py --loop                   # run continuously every 30 minutes
    python monitor.py --loop --interval 900    # every 15 minutes
    python monitor.py --summary                # print today's anomaly log and exit
    python monitor.py --screen pairs.yaml      # screen a yaml file, stats only
    python monitor.py --screen pairs.yaml --workers 8   # parallel screening
    python monitor.py --quiet                  # only print anomalies
"""

import os
import sys
import time
import glob
import sqlite3
import argparse
import yaml
import numpy as np
import pandas as pd
from datetime import datetime, date
from dotenv import load_dotenv
from concurrent.futures import ProcessPoolExecutor, as_completed
from data_manager import ensure_data
from scipy import stats as _scipy_stats
from tools.cost_model import (
    compute_round_trip_costs,
    compute_net_pnl,
)

load_dotenv(os.path.expanduser("~/.shiftinnerv_env"))

data_dir   = os.path.expanduser(os.getenv("DATA_STORAGE_PATH", "~/Projects/ShiftInnerV_Data"))
report_dir = os.path.expanduser(os.getenv("REPORT_DIR", "~/Projects/ShiftInnerV_Data/reports"))
db_path    = os.path.join(data_dir, "anomalies.db")


# ── Database setup ────────────────────────────────────────────────────────────

def init_db():
    os.makedirs(data_dir, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS anomalies (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL,
            date        TEXT NOT NULL,
            ticker1     TEXT NOT NULL,
            ticker2     TEXT NOT NULL,
            label       TEXT,
            corr_value  REAL,
            threshold   REAL,
            deviation   REAL,
            half_life   REAL,
            window      INTEGER,
            cointegrated TEXT,
            crew_run    INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS screening (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp    TEXT NOT NULL,
            ticker1      TEXT NOT NULL,
            ticker2      TEXT NOT NULL,
            label        TEXT,
            cointegrated TEXT,
            trace_stat   REAL,
            crit_val_90  REAL,
            crit_val_95  REAL,
            half_life    REAL,
            window       INTEGER,
            episodes     INTEGER,
            worst_corr   REAL,
            worst_dev    REAL,
            snr          REAL,
            rating       TEXT
        )
    """)
    conn.commit()
    conn.close()


# ── Core math — no LLM, no agents ────────────────────────────────────────────

def load_csv(ticker: str, lookback_years: int = 5) -> pd.Series:
    path = os.path.join(data_dir, f"{ticker.lower()}_daily.csv")
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path, index_col=0)
    if "Close" not in df.columns:
        return None
    s = df["Close"].dropna()
    if lookback_years < 5:
        cutoff = (pd.Timestamp.today() - pd.DateOffset(years=lookback_years)).strftime("%Y-%m-%d")
        s = s[s.index >= cutoff]
    return s


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


def analyze_pair(ticker1: str, ticker2: str, label: str = "",
                 lookback_years: int = 5) -> dict:
    """Full statistical analysis. No LLM."""
    s1 = load_csv(ticker1, lookback_years)
    s2 = load_csv(ticker2, lookback_years)

    if s1 is None or s2 is None:
        return {"error": f"Missing data for {ticker1} or {ticker2}"}

    shared = s1.index.intersection(s2.index)
    if len(shared) < 60:
        return {"error": f"Insufficient shared data ({len(shared)} rows)"}

    c1 = s1.loc[shared]
    c2 = s2.loc[shared]
    log_p1 = np.log(c1)
    log_p2 = np.log(c2)
    log_prices = pd.DataFrame({ticker1: log_p1, ticker2: log_p2}).dropna()

    spread = log_p1 - log_p2
    hl = compute_half_life(spread)
    window = int(np.clip(round(hl), 10, 120)) if hl else 30

    coint_90, coint_95, trace_stat, crit_90, crit_95 = run_johansen(log_prices)
    snr = compute_snr(log_p1, log_p2)

    corr      = c1.rolling(window).corr(c2)
    mean_corr = corr.mean()
    std_corr  = corr.std()
    threshold = mean_corr - 2 * std_corr
    decoupled = corr[corr < threshold].dropna()

    # Episode detection
    episodes = []
    if len(decoupled) > 0:
        corr_pos = {lbl: i for i, lbl in enumerate(corr.index)}
        labels   = sorted(decoupled.index, key=lambda x: corr_pos.get(x, 0))
        ep_start = labels[0]; ep_labels = [labels[0]]

        for prev, curr in zip(labels[:-1], labels[1:]):
            if corr_pos.get(curr, 0) - corr_pos.get(prev, 0) <= 1:
                ep_labels.append(curr)
            else:
                wc = decoupled.loc[ep_labels].min()
                episodes.append({"onset": str(ep_start)[:10],
                                  "duration": len(ep_labels),
                                  "worst_corr": wc,
                                  "worst_dev": (wc - mean_corr) / std_corr})
                ep_start = curr; ep_labels = [curr]
        wc = decoupled.loc[ep_labels].min()
        episodes.append({"onset": str(ep_start)[:10],
                          "duration": len(ep_labels),
                          "worst_corr": wc,
                          "worst_dev": (wc - mean_corr) / std_corr})

    worst = min(episodes, key=lambda e: e["worst_corr"]) if episodes else None

    # Johansen trend — is cointegration strengthening or weakening?
    trace_trend = compute_johansen_trend(log_prices)

    # ── Item 3: Simplified cost model for screening ──────────────────────────
    # Assume $10k notional with standard large-cap liquidity
    _known_etfs = {
        "KWEB", "FXI", "ITA", "XLF", "SMH", "SPY", "QQQ", "IWM",
        "EEM", "ASHR", "CQQQ", "KBE", "KRE", "SOXX", "XLE", "GDX",
        "ICLN", "XBI", "BOTZ", "VNQ", "BDRY", "MOO", "DBC", "UUP",
        "TLT", "GLD", "SLV", "USO", "UDN", "REM", "XAR", "XLK",
    }
    _notional_1_screen = 10000.0
    _notional_2_screen = _notional_1_screen * (abs(hl) / abs(hl) if hl else 1.0)

    costs_screen = compute_round_trip_costs(
        notional_leg1=_notional_1_screen,
        notional_leg2=_notional_2_screen,
        market_cap1_b=None,      # unknown at screening layer
        market_cap2_b=None,
        daily_volume1_m=None,    # assume defaults
        daily_volume2_m=None,
        is_etf1=ticker1.upper() in _known_etfs,
        is_etf2=ticker2.upper() in _known_etfs,
        half_life_days=hl or 30.0,
        ticker1=ticker1,
        ticker2=ticker2,
    )

    # Gross P&L estimate (entry=2.0σ, exit=0.0σ)
    # Use SNR to estimate spread volatility
    if snr and snr > 0:
        # SNR = var(stationary) / var(nonstationary)
        # Approximate spread_std from overall price volatility
        if ticker1 in log_prices.columns and len(log_prices) > 20:
            price_returns = log_prices[ticker1].diff().dropna()
            spread_std_estimate = price_returns.std() * (1 + 1 / snr) ** 0.5
        else:
            spread_std_estimate = 0.04  # default
        _gross_pnl_screen = (2.0 - 0.0) * spread_std_estimate * 10000 / \
                            (_notional_1_screen + _notional_2_screen)
    else:
        _gross_pnl_screen = 100.0  # default if SNR unknown

    net_pnl_screen = compute_net_pnl(_gross_pnl_screen,
                                     costs_screen["total_cost_bps"])

    # Continuous score
    scoring = compute_score(
        trace_stat=trace_stat or 0,
        crit_90=crit_90 or 15.0,
        crit_95=crit_95 or 18.0,
        half_life=hl,
        snr=snr,
        episodes=len(episodes),
        trace_trend=trace_trend,
        net_pnl_bps=net_pnl_screen["net_pnl_bps"],
    )

    return {
        "ticker1":         ticker1,
        "ticker2":         ticker2,
        "label":           label,
        "lookback_years":  lookback_years,
        "cointegrated_90": coint_90,
        "cointegrated_95": coint_95,
        "trace_stat":      trace_stat,
        "crit_90":         crit_90,
        "crit_95":         crit_95,
        "trace_trend":     trace_trend,
        "half_life":       hl,
        "window":          window,
        "snr":             snr,
        "mean_corr":       mean_corr,
        "std_corr":        std_corr,
        "threshold":       threshold,
        "episodes":        len(episodes),
        "worst":           worst,
        "current_corr":    float(corr.iloc[-1]) if not corr.empty else None,
        "score":           scoring["score"],
        "score_breakdown": scoring,
        "rating":          score_label(scoring["score"]),
        "suspicious":      scoring["suspicious"],
        "net_pnl_bps":     net_pnl_screen["net_pnl_bps"],
        "net_pnl_pct":     net_pnl_screen["net_pnl_pct"],
        "costs_bps":       costs_screen["total_cost_bps"],
        "is_profitable":   net_pnl_screen["is_profitable"],
        "marginal_edge":   net_pnl_screen["marginal"],
    }


# ── Worker for parallel screening ─────────────────────────────────────────────

def _analyze_pair_worker(args):
    """Top-level function so ProcessPoolExecutor can pickle it."""
    ticker1, ticker2, label, lookback_years = args
    try:
        return analyze_pair(ticker1, ticker2, label, lookback_years)
    except Exception as e:
        return {"error": str(e), "ticker1": ticker1, "ticker2": ticker2}


# ── Anomaly logging ───────────────────────────────────────────────────────────

def log_anomaly(result: dict):
    worst = result.get("worst") or {}
    conn  = sqlite3.connect(db_path)
    conn.execute("""
        INSERT INTO anomalies
        (timestamp, date, ticker1, ticker2, label, corr_value, threshold,
         deviation, half_life, window, cointegrated)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (
        datetime.now().isoformat(), str(date.today()),
        result["ticker1"], result["ticker2"], result.get("label", ""),
        result.get("current_corr"), result.get("threshold"),
        worst.get("worst_dev"), result.get("half_life"), result.get("window"),
        "YES" if result.get("cointegrated_95") else
        "90%" if result.get("cointegrated_90") else "NO"
    ))
    conn.commit()
    conn.close()


def log_screening(result: dict):
    worst = result.get("worst") or {}
    sb    = result.get("score_breakdown") or {}
    conn  = sqlite3.connect(db_path)
    conn.execute("""
        INSERT INTO screening
        (timestamp, ticker1, ticker2, label, cointegrated, trace_stat,
         crit_val_90, crit_val_95, half_life, window, episodes,
         worst_corr, worst_dev, snr, rating)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        datetime.now().isoformat(),
        result["ticker1"], result["ticker2"], result.get("label", ""),
        "95%" if result.get("cointegrated_95") else
        "90%" if result.get("cointegrated_90") else "NO",
        result.get("trace_stat"), result.get("crit_90"), result.get("crit_95"),
        result.get("half_life"), result.get("window"), result.get("episodes", 0),
        worst.get("worst_corr"), worst.get("worst_dev"),
        result.get("snr"),
        f"{result.get('score', 0):.1f} {result.get('rating', '')}"
    ))
    conn.commit()
    conn.close()


# ── Anomaly yaml writer ───────────────────────────────────────────────────────

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

    # Suspicious SNR flag
    suspicious = snr is not None and snr > 1000

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


def write_anomaly_yaml(flagged: list, compositions_dir: str) -> list:
    """
    For each flagged anomaly write a single-pair yaml into
    compositions/anomalies/ ready to run with main.py --pairs.
    Skips if a yaml already exists for this pair/lookback today.
    """
    anomaly_dir = os.path.join(compositions_dir, "anomalies")
    os.makedirs(anomaly_dir, exist_ok=True)
    today   = str(date.today())
    written = []

    for result in flagged:
        t1       = result["ticker1"]
        t2       = result["ticker2"]
        label    = result.get("label", f"{t1} vs {t2}")
        hl       = result.get("half_life")
        eps      = result.get("episodes", 0)
        curr     = result.get("current_corr")
        thresh   = result.get("threshold")
        worst    = result.get("worst") or {}
        lookback = result.get("lookback_years", 1)

        clean_label = label
        for tag in ["[1yr]", "[3yr]", "[5yr]"]:
            clean_label = clean_label.replace(tag, "").strip()

        filename = f"anomaly_{t1}_{t2}_{lookback}yr_{today}.yaml"
        filepath = os.path.join(anomaly_dir, filename)
        if os.path.exists(filepath):
            continue

        coint_str  = "95%" if result.get("cointegrated_95") else \
                     "90%" if result.get("cointegrated_90") else "no"
        snr_str    = f"{result.get('snr', 0):.3f}" if result.get("snr") else "N/A"
        hl_str     = f"{hl:.1f}" if hl else "N/A"
        curr_str   = f"{curr:.3f}" if curr is not None else "N/A"
        thresh_str = f"{thresh:.3f}" if thresh is not None else "N/A"
        onset_str  = worst.get("onset", "unknown")
        dur_str    = str(worst.get("duration", "unknown"))
        wc_str     = f"{worst.get('worst_corr', 0):.3f}"
        wd_str     = f"{worst.get('worst_dev', 0):.1f}"
        rating     = result.get("rating", "unknown")

        content = f"""# ShiftInnerV — Anomaly Investigation
# Auto-generated by monitor.py on {today}
# Pair flagged: current correlation {curr_str} below threshold {thresh_str}
#
# Statistical context:
#   Rating:        {rating}
#   Cointegrated:  {coint_str} (Johansen)
#   SNR:           {snr_str}
#   Half-life:     {hl_str} days
#   Episodes:      {eps}
#   Worst episode: onset {onset_str} | duration {dur_str}d | corr {wc_str} | dev {wd_str}σ

pairs:
  - ticker1: {t1}
    ticker2: {t2}
    label: "{clean_label} — Anomaly Investigation"
    lookback_years: {lookback}
    cointegrated: unknown
"""
        with open(filepath, "w") as f:
            f.write(content)
        written.append(filepath)
        print(f"  📄 Anomaly yaml: {filename}")

    return written


# ── Screening — supports parallel workers ─────────────────────────────────────

def run_screening(yaml_path: str, workers: int = 1, top_n: int = None,
                  ratings_filter: list = None):
    """
    Screen a yaml file — pure math, no LLM.
    workers > 1 enables parallel processing via ProcessPoolExecutor.
    top_n: if set, only show top N results sorted by rating tier.
    ratings_filter: if set, only show results matching these ratings.
    """
    with open(yaml_path) as f:
        comp = yaml.safe_load(f)

    pairs = comp.get("pairs", [])

    # Auto-fetch missing tickers
    tickers_needed = list(set(
        t for p in pairs for t in [p["ticker1"], p["ticker2"]]
        if not os.path.exists(os.path.join(data_dir, f"{t.lower()}_daily.csv"))
    ))
    if tickers_needed:
        print(f"  Fetching {len(tickers_needed)} missing ticker(s)...")
        ensure_data(tickers_needed, data_dir)
        print()

    print(f"\nScreening {len(pairs)} pair(s) from {os.path.basename(yaml_path)}")
    if workers > 1:
        print(f"  Parallel mode: {workers} workers")
    print(f"\n{'Pair':<16} {'Score':>5} {'Ratio':>6} {'HL':>6} {'SNR':>6} "
          f"{'Eps':>4} {'Trend':>6}  Rating")
    print("-" * 80)

    # Build worker args
    work_args = [
        (p["ticker1"], p["ticker2"],
         p.get("label", f"{p['ticker1']}/{p['ticker2']}"),
         p.get("lookback_years", 5))
        for p in pairs
    ]

    results = []
    if workers > 1:
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(_analyze_pair_worker, a): a for a in work_args}
            done = 0
            for fut in as_completed(futures):
                done += 1
                result = fut.result()
                results.append(result)
                if done % 50 == 0:
                    print(f"  ... {done}/{len(pairs)} screened")
    else:
        for args in work_args:
            results.append(_analyze_pair_worker(args))

    # Sort by score descending
    results.sort(key=lambda r: r.get("score", 0), reverse=True)

    # ── Multiple comparisons correction (Item 1 — Simons fix) ────────────────
    n_tested = len([r for r in results if "error" not in r])
    results = apply_bh_correction(results, alpha=0.05)
    bh_flagged = [r for r in results if r.get("bh_flag")]

    displayed = 0
    score_threshold = 15.0  # hide pure noise unless --all flag
    for result in results:
        if "error" in result:
            continue

        score = result.get("score", 0)
        if ratings_filter and result.get("rating") not in ratings_filter:
            continue
        if not ratings_filter and score < score_threshold:
            continue

        t1     = result["ticker1"]
        t2     = result["ticker2"]
        ratio  = result.get("score_breakdown", {}).get("trace_ratio", 0)
        hl     = f"{result['half_life']:.0f}d" if result.get("half_life") else "N/A"
        snr    = result.get("snr", 0) or 0
        snr_str= f"{min(snr, 9999):.1f}" if snr < 1000 else "HIGH*"
        eps    = result.get("episodes", 0)
        trend  = result.get("trace_trend", 0)
        trend_str = f"+{trend:.2f}" if trend >= 0 else f"{trend:.2f}"
        rating = result.get("rating", "")
        flag   = "⚠" if result.get("suspicious") else (
                 "⚑" if result.get("bh_flag") else " "
        )

        print(f"{t1}/{t2:<12} {score:>5.1f} {ratio:>6.3f} {hl:>6} "
              f"{snr_str:>6} {eps:>4} {trend_str:>6}  {flag}{rating}")
        log_screening(result)
        displayed += 1

        if top_n and displayed >= top_n:
            break

    # Summary bands
    prime  = [r for r in results if r.get("score", 0) >= 75]
    strong = [r for r in results if 60 <= r.get("score", 0) < 75]
    solid  = [r for r in results if 45 <= r.get("score", 0) < 60]
    watch  = [r for r in results if 30 <= r.get("score", 0) < 45]

    print(f"\n{'─'*80}")
    print(f"{'Pairs screened:':<25} {len(results):>6}")
    bh_flagged_count = len([r for r in results if r.get("bh_flag")])
    raw_pass_count   = len([r for r in results if r.get("cointegrated_95")])
    bh_pass_count    = len([r for r in results if r.get("passes_bh")])
    print(f"{'Cointegrated (raw 95% CI):':<25} {raw_pass_count:>6}")
    print(f"{'Cointegrated (BH-adjusted):':<25} {bh_pass_count:>6}  "
          f"(FDR α=0.05, n={n_tested})")
    if bh_flagged_count > 0:
        print(f"{'⚑ Marginal (raw pass/BH fail):':<25} {bh_flagged_count:>6}  "
              f"(treat as MONITOR, not ACTIVE)")
    print(f"{'★★★ PRIME  (≥75):':<25} {len(prime):>6}")
    print(f"{'★★  STRONG (60-74):':<25} {len(strong):>6}")
    print(f"{'★   SOLID  (45-59):':<25} {len(solid):>6}")
    print(f"{'◆   WATCH  (30-44):':<25} {len(watch):>6}")

    if prime:
        print(f"\n{'─'*80}")
        print("PRIME pairs (score ≥ 75):")
        for r in prime:
            sb   = r.get("score_breakdown", {})
            hl_s = f"{r['half_life']:.0f}d" if r.get("half_life") else "N/A"
            snr  = r.get("snr", 0) or 0
            snr_s= f"{snr:.2f}" if snr < 1000 else "HIGH*"
            trend= r.get("trace_trend", 0)
            flag = " ⚠ SUSPICIOUS SNR" if r.get("suspicious") else ""
            print(f"  {r['ticker1']}/{r['ticker2']:<10} "
                  f"score={r['score']} "
                  f"ratio={sb.get('trace_ratio', 0):.3f} "
                  f"hl={hl_s} snr={snr_s} eps={r['episodes']} "
                  f"trend={trend:+.2f}{flag}")


# ── Monitoring pass ───────────────────────────────────────────────────────────

def run_monitor(compositions_dir: str, verbose: bool = True, workers: int = 1):
    yaml_files = sorted(glob.glob(os.path.join(compositions_dir, "*.yaml")))
    if not yaml_files:
        print(f"No yaml files in {compositions_dir}")
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n[{now}] Monitor pass — {len(yaml_files)} composition file(s)")

    all_pairs = []
    for yaml_file in yaml_files:
        with open(yaml_file) as f:
            comp = yaml.safe_load(f)
        all_pairs.extend(comp.get("pairs", []))

    tickers_needed = list(set(
        t for p in all_pairs for t in [p["ticker1"], p["ticker2"]]
        if not os.path.exists(os.path.join(data_dir, f"{t.lower()}_daily.csv"))
    ))
    if tickers_needed:
        print(f"  Fetching {len(tickers_needed)} missing ticker(s)...")
        ensure_data(tickers_needed, data_dir)
        print()

    work_args = [
        (p["ticker1"], p["ticker2"],
         p.get("label", f"{p['ticker1']}/{p['ticker2']}"),
         p.get("lookback_years", 5))
        for yaml_file in yaml_files
        for p in yaml.safe_load(open(yaml_file)).get("pairs", [])
    ]

    if workers > 1:
        results = []
        with ProcessPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(_analyze_pair_worker, a): a for a in work_args}
            for fut in as_completed(futures):
                results.append(fut.result())
    else:
        results = [_analyze_pair_worker(a) for a in work_args]

    flagged = []
    for result in results:
        if "error" in result:
            if verbose:
                t1 = result.get("ticker1", "?")
                t2 = result.get("ticker2", "?")
                print(f"  SKIP {t1}/{t2}: {result['error']}")
            continue

        curr   = result.get("current_corr")
        thresh = result.get("threshold")
        is_anomaly = curr is not None and thresh is not None and curr < thresh

        if verbose or is_anomaly:
            status = "🚨 ANOMALY" if is_anomaly else "  OK     "
            hl_str = f"{result['half_life']:.0f}d" if result.get('half_life') else "N/A"
            score  = result.get("score", 0)
            rating = result.get("rating", "")
            t1 = result["ticker1"]; t2 = result["ticker2"]
            label = result.get("label", "")[:35]
            print(f"  {status} {t1}/{t2:6s} | score={score:>5.1f} hl={hl_str} "
                  f"| {rating:<14} | {label}")

        if is_anomaly:
            log_anomaly(result)
            flagged.append(result)

    print(f"\n  Flagged: {len(flagged)} anomaly(ies)")
    if flagged:
        write_anomaly_yaml(flagged, compositions_dir)
    return flagged


def print_summary():
    conn  = sqlite3.connect(db_path)
    today = str(date.today())
    rows  = conn.execute("""
        SELECT timestamp, ticker1, ticker2, label, corr_value,
               threshold, deviation, cointegrated
        FROM anomalies WHERE date = ? ORDER BY timestamp DESC
    """, (today,)).fetchall()
    conn.close()

    print(f"\nAnomaly log for {today} — {len(rows)} event(s)\n")
    if not rows:
        print("  No anomalies logged today.")
        return
    for r in rows:
        ts, t1, t2, label, corr, thresh, dev, coint = r
        dev_str = f"{dev:.1f}σ" if dev else "N/A"
        print(f"  {ts[11:16]}  {t1}/{t2:<8} corr={corr:.3f} "
              f"thresh={thresh:.3f} dev={dev_str} coint={coint}")
        if label:
            print(f"           {label}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ShiftInnerV Layer 1 Monitor")
    parser.add_argument("--loop",         action="store_true")
    parser.add_argument("--interval",     type=int, default=1800)
    parser.add_argument("--summary",      action="store_true")
    parser.add_argument("--screen",       type=str, default=None)
    parser.add_argument("--compositions", type=str, default=None)
    parser.add_argument("--quiet",        action="store_true")
    parser.add_argument("--workers",      type=int, default=1,
                        help="Parallel workers for screening (default: 1)")
    parser.add_argument("--top",          type=int, default=None,
                        help="Show only top N results in --screen mode")
    parser.add_argument("--filter",       type=str, default=None,
                        help="Filter: prime|strong|solid|watch (score bands)")
    parser.add_argument("--min-score",    type=float, default=None,
                        help="Minimum score threshold (overrides --filter)")
    parser.add_argument("--show-suspicious", action="store_true",
                        help="Include pairs with suspicious SNR (>1000)")
    args = parser.parse_args()

    init_db()

    if args.summary:
        print_summary()
        return

    if args.screen:
        score_band_map = {
            "prime":  ["★★★ PRIME"],
            "strong": ["★★  STRONG"],
            "solid":  ["★   SOLID"],
            "watch":  ["◆   WATCH"],
        }
        rf = score_band_map.get(args.filter) if args.filter else None
        run_screening(os.path.expanduser(args.screen),
                      workers=args.workers,
                      top_n=args.top,
                      ratings_filter=rf)
        return

    compositions_dir = os.path.expanduser(
        args.compositions or
        os.path.join(os.path.dirname(__file__), "compositions")
    )

    if args.loop:
        print(f"ShiftInnerV Monitor — loop every {args.interval}s")
        print(f"Anomaly log: {db_path}")
        while True:
            run_monitor(compositions_dir, verbose=not args.quiet,
                        workers=args.workers)
            print(f"  Next run in {args.interval // 60}m — "
                  f"{datetime.now().strftime('%H:%M')}")
            time.sleep(args.interval)
    else:
        run_monitor(compositions_dir, verbose=not args.quiet,
                    workers=args.workers)


if __name__ == "__main__":
    main()
