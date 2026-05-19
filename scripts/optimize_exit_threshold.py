#!/usr/bin/env python3
"""
ShiftInnerV — Exit Z-Score Threshold Optimization
Item 16 of the Council Roadmap.

Tests exit thresholds of [0.0, 0.25, 0.5, 1.0] on the trial performance
ledger (Item 14). For each closed trade, reconstructs the spread from
Tiingo price history and simulates what P&L/hold-time each exit threshold
would have produced. Computes Sharpe ratio, win rate, and hold time for each
threshold, stratified by half-life bin. Recommends whether to change the
current z=0.5 exit.

Uses stored hedge_ratio, spread_mean, spread_std from the ledger where
available, falling back to OLS re-estimation from the 250-day training
window when they are NULL.

Usage:
    python scripts/optimize_exit_threshold.py
    python scripts/optimize_exit_threshold.py --db /path/to/trial_ledger.db
    python scripts/optimize_exit_threshold.py --output /path/to/report.md
    python scripts/optimize_exit_threshold.py --min-trades 10
    python scripts/optimize_exit_threshold.py --verbose

Output:
    optimize_exit_threshold_report.md  (project root, or --output path)
"""

import os
import sys
import sqlite3
import argparse
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from statsmodels.regression.linear_model import OLS
from statsmodels.tools import add_constant

# ── Project setup ─────────────────────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.expanduser("~/.shiftinnerv_env"))
except ImportError:
    pass

# ── Configuration ─────────────────────────────────────────────────────────────

# Support both env-var spellings (audit_active_verdicts.py convention)
TIINGO_KEY = os.getenv("TIINGO_KEY", "") or os.getenv("TIINGA_KEY", "")
DATA_STORAGE = os.getenv("DATA_STORAGE_PATH",
                         "/Volumes/Elessar/ShiftInnerV_Data")
DEFAULT_DB  = os.path.join(DATA_STORAGE, "trial_ledger.db")
DEFAULT_OUT = str(PROJECT_ROOT / "optimize_exit_threshold_report.md")

TIINGO_BASE    = "https://api.tiingo.com"
TIINGO_HEADERS = {"Content-Type": "application/json"}

# Thresholds to evaluate
EXIT_THRESHOLDS = [0.0, 0.25, 0.5, 1.0]
CURRENT_EXIT_Z  = 0.5          # the threshold we are testing against

# Half-life stratification bins  (min_days, max_days, label)
HL_BINS = [
    (0,   15,   "< 15d"),
    (15,  30,   "15–30d"),
    (30,  60,   "30–60d"),
    (60,  9999, "> 60d"),
]

# Minimum number of successful simulations required to publish a conclusion
MIN_TRADES_FOR_CONCLUSION = 10

# ── Tiingo API ────────────────────────────────────────────────────────────────

def fetch_price_history(
    ticker: str,
    start_date: str,
    end_date: str,
    verbose: bool = False,
) -> pd.Series | None:
    """
    Fetch daily adjusted close prices from Tiingo for [start_date, end_date].

    Returns a timezone-naive pd.Series indexed by date, or None on failure.
    """
    if not TIINGO_KEY:
        if verbose:
            print(f"    WARNING: TIINGO_KEY not set; skipping {ticker}")
        return None

    url    = f"{TIINGO_BASE}/tiingo/daily/{ticker}/prices"
    params = {
        "startDate": start_date,
        "endDate":   end_date,
        "token":     TIINGO_KEY,
        "format":    "json",
    }
    try:
        r = requests.get(url, params=params, headers=TIINGO_HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
        if not data:
            return None
        df = pd.DataFrame(data)
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
        df = df.set_index("date").sort_index()
        col = "adjClose" if "adjClose" in df.columns else "close"
        return df[col].dropna()
    except Exception as exc:
        if verbose:
            print(f"    WARNING: Tiingo fetch failed for {ticker}: {exc}")
        return None


# ── Database ──────────────────────────────────────────────────────────────────

def load_ledger(db_path: str, verbose: bool = False) -> pd.DataFrame | None:
    """
    Load closed trades from the trial_ledger, mapped to the real schema
    produced by init_trial_ledger.py (Item 14).

    Columns returned:
        id, ticker1, ticker2, entry_timestamp, exit_timestamp,
        half_life, snr, entry_z_verdict, entry_z_actual,
        hedge_ratio, spread_mean, spread_std,
        hold_days, net_pnl_bps, exit_reason
    """
    if not os.path.exists(db_path):
        print(f"ERROR: Trial ledger not found at {db_path}")
        print(f"       Expected path: {db_path}")
        print(f"       Run init_trial_ledger.py (Item 14) and accumulate trades first.")
        return None

    try:
        conn = sqlite3.connect(db_path)
        df = pd.read_sql_query(
            """
            SELECT
                id,
                ticker1,
                ticker2,
                entry_timestamp,
                exit_timestamp,
                half_life,
                snr,
                entry_z_verdict,
                entry_z_actual,
                hedge_ratio,
                spread_mean,
                spread_std,
                hold_days,
                net_pnl_bps,
                exit_reason
            FROM trial_ledger
            WHERE is_closed = 1
              AND entry_timestamp IS NOT NULL
              AND exit_timestamp  IS NOT NULL
            ORDER BY entry_timestamp DESC
            """,
            conn,
        )
        conn.close()

        if verbose:
            print(f"  Loaded {len(df)} closed trades from {db_path}")

        return df

    except Exception as exc:
        print(f"ERROR: Could not read trial ledger: {exc}")
        return None


# ── Spread reconstruction ─────────────────────────────────────────────────────

def reconstruct_spread_series(
    prices1: pd.Series,
    prices2: pd.Series,
    entry_idx: int,
    hedge_ratio_stored: float | None,
    spread_mean_stored: float | None,
    spread_std_stored: float | None,
    training_window: int = 250,
) -> tuple[pd.Series, float, float, float] | None:
    """
    Reconstruct the z-score series for the spread from entry onward.

    Uses stored hedge_ratio / spread_mean / spread_std when available.
    Falls back to OLS estimation from the training window before entry.

    Returns (z_series, hedge_ratio, spread_mean, spread_std) or None.
    """
    # Prefer stored parameters — they reflect what the agent actually used
    if (
        hedge_ratio_stored is not None
        and spread_mean_stored  is not None
        and spread_std_stored   is not None
        and spread_std_stored   > 0
    ):
        hedge_ratio  = hedge_ratio_stored
        spread_mean  = spread_mean_stored
        spread_std   = spread_std_stored
    else:
        # Fall back to OLS on the 250-day training window
        if entry_idx < training_window:
            return None  # not enough history

        log_p1_train = np.log(prices1.iloc[entry_idx - training_window:entry_idx])
        log_p2_train = np.log(prices2.iloc[entry_idx - training_window:entry_idx])
        try:
            ols_result  = OLS(log_p1_train, add_constant(log_p2_train)).fit()
            hedge_ratio = (
                ols_result.params.iloc[1] if len(ols_result.params) > 1 else 1.0
            )
        except Exception:
            return None

        spread_train = log_p1_train - hedge_ratio * log_p2_train
        spread_mean  = spread_train.mean()
        spread_std   = spread_train.std()
        if spread_std <= 0:
            return None

    # Build z-score series from entry onward (inclusive)
    log_p1 = np.log(prices1.iloc[entry_idx:])
    log_p2 = np.log(prices2.iloc[entry_idx:])
    spread = log_p1 - hedge_ratio * log_p2
    z_series = (spread - spread_mean) / spread_std

    return z_series, hedge_ratio, spread_mean, spread_std


# ── Per-trade simulation ──────────────────────────────────────────────────────

COSTS_BPS = 15.0   # round-trip transaction cost estimate (conservative)


def simulate_trade(trade: dict, verbose: bool = False) -> dict | None:
    """
    Simulate exit at each threshold for a single trade.

    Returns a dict keyed by exit threshold float, each containing:
        pnl_bps   – net P&L in basis points
        hold_days – calendar days from entry to simulated exit
        profitable – bool
        timed_out  – bool (True if no threshold was crossed before original exit)

    Returns None if the trade cannot be simulated (missing data, fetch failure).
    """
    ticker1    = trade["ticker1"]
    ticker2    = trade["ticker2"]
    entry_ts   = pd.to_datetime(trade["entry_timestamp"])
    exit_ts    = pd.to_datetime(trade["exit_timestamp"])

    # Fetch prices: start 750 calendar days before entry (250+ trading days)
    fetch_start = (entry_ts - pd.Timedelta(days=750)).strftime("%Y-%m-%d")
    fetch_end   = exit_ts.strftime("%Y-%m-%d")

    prices1 = fetch_price_history(ticker1, fetch_start, fetch_end, verbose=verbose)
    prices2 = fetch_price_history(ticker2, fetch_start, fetch_end, verbose=verbose)

    if prices1 is None or prices2 is None:
        if verbose:
            print(f"    skip {ticker1}/{ticker2}: price fetch failed")
        return None

    # Align
    prices1, prices2 = prices1.align(prices2, join="inner")
    if len(prices1) < 50:
        if verbose:
            print(f"    skip {ticker1}/{ticker2}: too few aligned prices ({len(prices1)})")
        return None

    # Locate entry date in index
    entry_date_norm = entry_ts.normalize()
    if entry_date_norm in prices1.index:
        entry_idx = prices1.index.get_loc(entry_date_norm)
    else:
        # Find the nearest trading day on or before entry
        candidates = prices1.index[prices1.index <= entry_date_norm]
        if len(candidates) == 0:
            if verbose:
                print(f"    skip {ticker1}/{ticker2}: entry date before all prices")
            return None
        entry_idx = prices1.index.get_loc(candidates[-1])

    if entry_idx < 1:
        return None

    # Reconstruct spread z-scores from entry onward
    result_spread = reconstruct_spread_series(
        prices1, prices2, entry_idx,
        hedge_ratio_stored=trade.get("hedge_ratio"),
        spread_mean_stored=trade.get("spread_mean"),
        spread_std_stored=trade.get("spread_std"),
    )
    if result_spread is None:
        if verbose:
            print(f"    skip {ticker1}/{ticker2}: spread reconstruction failed")
        return None

    z_series, hedge_ratio, spread_mean, spread_std = result_spread

    # Locate original exit date in prices index (timeout boundary)
    exit_date_norm = exit_ts.normalize()
    if exit_date_norm in prices1.index:
        timeout_iloc = prices1.index.get_loc(exit_date_norm)
    else:
        candidates = prices1.index[prices1.index <= exit_date_norm]
        timeout_iloc = (
            prices1.index.get_loc(candidates[-1]) if len(candidates) > 0
            else len(prices1) - 1
        )

    # Prices at entry (for P&L)
    entry_p1 = float(prices1.iloc[entry_idx])
    entry_p2 = float(prices2.iloc[entry_idx])

    results = {}

    for exit_z_threshold in EXIT_THRESHOLDS:
        sim_exit_iloc = None
        timed_out     = False

        # Walk forward from entry+1; stop at first z <= threshold OR timeout
        # z_series starts at entry_idx (iloc 0 = entry)
        for step in range(1, len(z_series)):
            abs_iloc = entry_idx + step
            if abs_iloc > timeout_iloc:
                # Reached original exit without crossing threshold
                sim_exit_iloc = timeout_iloc
                timed_out     = True
                break
            if z_series.iloc[step] <= exit_z_threshold:
                sim_exit_iloc = abs_iloc
                break

        # If loop ended without break (shouldn't happen but be safe)
        if sim_exit_iloc is None:
            sim_exit_iloc = timeout_iloc
            timed_out     = True

        exit_p1 = float(prices1.iloc[sim_exit_iloc])
        exit_p2 = float(prices2.iloc[sim_exit_iloc])
        sim_hold = sim_exit_iloc - entry_idx   # trading-day count

        # P&L — long spread (long ticker1, short ticker2)
        notional      = 10_000.0
        shares_1      = notional / entry_p1
        shares_2      = (notional * abs(hedge_ratio)) / entry_p2
        total_notional = notional + notional * abs(hedge_ratio)

        pnl_leg1 = shares_1 * (exit_p1 - entry_p1)
        pnl_leg2 = shares_2 * (entry_p2 - exit_p2)
        gross_dollars = pnl_leg1 + pnl_leg2
        gross_bps     = (
            gross_dollars / total_notional * 10_000
            if total_notional > 0 else 0.0
        )
        net_bps = gross_bps - COSTS_BPS

        results[exit_z_threshold] = {
            "pnl_bps":   round(net_bps, 1),
            "hold_days": sim_hold,
            "profitable": net_bps > 0,
            "timed_out":  timed_out,
        }

    return results


# ── Aggregation ───────────────────────────────────────────────────────────────

def _stats(pnls: list[float], holds: list[int]) -> dict:
    """Compute summary statistics for a list of P&L values."""
    if not pnls:
        return {}
    arr = np.array(pnls, dtype=float)
    std = float(np.std(arr))
    return {
        "n":         len(arr),
        "mean_pnl":  round(float(np.mean(arr)), 1),
        "median_pnl": round(float(np.median(arr)), 1),
        "std_pnl":   round(std, 1),
        "sharpe":    round(float(np.mean(arr)) / std if std > 0 else 0.0, 4),
        "win_rate":  round(sum(1 for p in arr if p > 0) / len(arr), 3),
        "avg_hold":  round(float(np.mean(holds)), 1),
    }


def aggregate_overall(sim_results: list[dict]) -> dict:
    """Aggregate simulation results across all trades per threshold."""
    out = {}
    for z in EXIT_THRESHOLDS:
        pnls  = [r[z]["pnl_bps"]   for r in sim_results if z in r]
        holds = [r[z]["hold_days"] for r in sim_results if z in r]
        if pnls:
            out[z] = _stats(pnls, holds)
    return out


def aggregate_by_hl(
    ledger: pd.DataFrame,
    sim_results: list[dict],
    trade_ids: list[int],
) -> dict:
    """Stratify by half-life bin and aggregate per threshold per bin."""
    # Build lookup: trade index → half_life
    hl_map = dict(zip(ledger["id"].tolist(), ledger["half_life"].tolist()))
    # trade_ids[i] corresponds to sim_results[i]
    id_to_hl = {trade_ids[i]: ledger.iloc[i]["half_life"]
                for i in range(len(trade_ids))}

    out = {}
    for hl_min, hl_max, label in HL_BINS:
        matching_idx = [
            i for i, tid in enumerate(trade_ids)
            if hl_min <= (id_to_hl.get(tid) or 0) < hl_max
        ]
        bin_results = {}
        for z in EXIT_THRESHOLDS:
            pnls  = [sim_results[i][z]["pnl_bps"]   for i in matching_idx if z in sim_results[i]]
            holds = [sim_results[i][z]["hold_days"] for i in matching_idx if z in sim_results[i]]
            if pnls:
                bin_results[z] = _stats(pnls, holds)
        out[label] = bin_results
    return out


# ── Sensitivity metrics ───────────────────────────────────────────────────────

def compute_sensitivity(agg: dict) -> dict:
    """
    For each threshold, compute uplift vs. the current z=0.5 baseline.

    Returns dict keyed by threshold float.
    """
    baseline = agg.get(CURRENT_EXIT_Z, {})
    baseline_sharpe = baseline.get("sharpe", 0.0)
    baseline_pnl    = baseline.get("mean_pnl", 0.0)
    baseline_hold   = baseline.get("avg_hold", 1.0)

    out = {}
    for z, stats in agg.items():
        if not stats or baseline_sharpe == 0:
            out[z] = {}
            continue
        out[z] = {
            "sharpe_uplift_pct": round(
                (stats["sharpe"] - baseline_sharpe) / abs(baseline_sharpe) * 100, 1
            ) if baseline_sharpe != 0 else None,
            "pnl_uplift_pct": round(
                (stats["mean_pnl"] - baseline_pnl) / abs(baseline_pnl) * 100, 1
            ) if baseline_pnl != 0 else None,
            "hold_change_pct": round(
                (stats["avg_hold"] - baseline_hold) / baseline_hold * 100, 1
            ) if baseline_hold != 0 else None,
        }
    return out


# ── Recommendation logic ──────────────────────────────────────────────────────

def make_recommendation(agg: dict, sensitivity: dict, n_trades: int) -> tuple[str, str]:
    """
    Apply the decision framework from the Item 16 spec.

    Returns (verdict_emoji_line, detail_text).
    """
    if n_trades < MIN_TRADES_FOR_CONCLUSION:
        return (
            f"⚠️  INSUFFICIENT DATA ({n_trades} trades < {MIN_TRADES_FOR_CONCLUSION} minimum)",
            "Accumulate more closed trades before drawing a conclusion.",
        )

    best_z      = max(agg, key=lambda z: agg[z].get("sharpe", -999) if agg.get(z) else -999)
    best_stats  = agg.get(best_z, {})
    curr_stats  = agg.get(CURRENT_EXIT_Z, {})

    if not best_stats or not curr_stats:
        return ("⚠️  INCONCLUSIVE", "Could not compute Sharpe for some thresholds.")

    uplift_pct = sensitivity.get(best_z, {}).get("sharpe_uplift_pct", 0.0) or 0.0

    if best_z == 0.0 and uplift_pct > 20:
        verdict = f"✅  CHANGE EXIT THRESHOLD → z={best_z:.2f}"
        detail  = (
            f"Sharpe uplift: {uplift_pct:+.1f}% vs current z={CURRENT_EXIT_Z}. "
            f"Evidence strongly favours exiting at the mean."
        )
    elif best_z == 0.25 and uplift_pct > 10:
        verdict = f"✅  CHANGE EXIT THRESHOLD → z={best_z:.2f}"
        detail  = (
            f"Sharpe uplift: {uplift_pct:+.1f}% vs current z={CURRENT_EXIT_Z}. "
            f"Good balance of P&L capture and hold-time."
        )
    elif uplift_pct < 5 or best_z == CURRENT_EXIT_Z:
        verdict = f"✓  KEEP CURRENT THRESHOLD z={CURRENT_EXIT_Z:.2f}"
        detail  = (
            f"Best alternative (z={best_z:.2f}) offers only "
            f"{uplift_pct:+.1f}% Sharpe uplift — within noise. "
            f"Exit threshold is not the binding constraint."
        )
    else:
        # Marginal improvement
        verdict = f"🔶  MARGINAL: CONSIDER z={best_z:.2f}"
        detail  = (
            f"Sharpe uplift: {uplift_pct:+.1f}% — notable but not decisive. "
            f"Review stratified results below before changing the threshold."
        )

    return verdict, detail


# ── Report generation ─────────────────────────────────────────────────────────

def _fmt_row(z: float, stats: dict, sens: dict, mark_current: bool) -> str:
    if not stats:
        return f"| {z:.2f}      | {'N/A':>6} | {'N/A':>8} | {'N/A':>10} | {'N/A':>6} | {'N/A':>8} | {'N/A':>8} |"
    cur = " ← current" if mark_current else ""
    s_up = sens.get("sharpe_uplift_pct")
    s_up_str = f"{s_up:+.0f}%" if s_up is not None else "—"
    return (
        f"| {z:.2f}      | {stats['n']:>6} | "
        f"{stats['mean_pnl']:>8.0f} | {stats['median_pnl']:>10.0f} | "
        f"{stats['sharpe']:>6.3f} | {stats['win_rate']:>7.0%} | "
        f"{stats['avg_hold']:>8.1f} | {s_up_str:>7}{cur} |"
    )


def generate_report(
    agg_overall: dict,
    agg_by_hl:   dict,
    sensitivity: dict,
    n_trades:    int,
    n_failed:    int,
    output_path: str,
) -> None:
    """Write the optimization report as markdown."""
    lines = []
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    verdict, detail = make_recommendation(agg_overall, sensitivity, n_trades)

    best_z     = max(agg_overall, key=lambda z: agg_overall[z].get("sharpe", -999) if agg_overall.get(z) else -999)
    curr_stats = agg_overall.get(CURRENT_EXIT_Z, {})
    best_stats = agg_overall.get(best_z, {})

    lines += [
        "# ShiftInnerV — Exit Z-Score Threshold Optimization",
        f"*Generated {now} · Item 16 of the Council Roadmap*",
        "",
        "---",
        "## Decision",
        "",
        f"**{verdict}**",
        "",
        detail,
        "",
    ]

    if curr_stats and best_stats:
        lines += [
            f"| | Sharpe | Mean P&L (bps) | Win Rate | Avg Hold (days) |",
            f"|---|---|---|---|---|",
            f"| Current z={CURRENT_EXIT_Z:.2f} | "
            f"{curr_stats.get('sharpe', '—'):.3f} | "
            f"{curr_stats.get('mean_pnl', '—'):.0f} | "
            f"{curr_stats.get('win_rate', 0):.0%} | "
            f"{curr_stats.get('avg_hold', '—'):.1f} |",
            f"| Optimal z={best_z:.2f} | "
            f"{best_stats.get('sharpe', '—'):.3f} | "
            f"{best_stats.get('mean_pnl', '—'):.0f} | "
            f"{best_stats.get('win_rate', 0):.0%} | "
            f"{best_stats.get('avg_hold', '—'):.1f} |",
            "",
        ]

    lines += [
        "---",
        "## Overall Results — All Trades",
        "",
        f"Simulated on {n_trades} trades ({n_failed} failed / skipped).",
        "",
        "| Threshold | Trades | Mean P&L | Median P&L | Sharpe | Win Rate | Avg Hold | Δ Sharpe |",
        "|-----------|--------|----------|------------|--------|----------|----------|----------|",
    ]

    for z in EXIT_THRESHOLDS:
        stats = agg_overall.get(z, {})
        sens  = sensitivity.get(z, {})
        lines.append(_fmt_row(z, stats, sens, z == CURRENT_EXIT_Z))

    lines += [
        "",
        "---",
        "## Stratified Analysis by Half-Life",
        "",
        "Sharpe ratios per threshold per half-life bin. "
        "Higher is better. ← marks the optimal threshold per bin.",
        "",
    ]

    for hl_min, hl_max, label in HL_BINS:
        bin_data = agg_by_hl.get(label, {})
        if not bin_data:
            lines += [f"### {label}", "", "*No trades in this bin.*", ""]
            continue

        # Find best threshold for this bin
        best_hl_z = max(
            (z for z in EXIT_THRESHOLDS if bin_data.get(z)),
            key=lambda z: bin_data[z].get("sharpe", -999),
            default=None,
        )

        lines += [
            f"### {label}",
            "",
            "| Threshold | Trades | Mean P&L | Sharpe | Win Rate | Avg Hold |",
            "|-----------|--------|----------|--------|----------|----------|",
        ]

        for z in EXIT_THRESHOLDS:
            s = bin_data.get(z, {})
            if not s:
                lines.append(f"| {z:.2f}      | N/A    | N/A      | N/A    | N/A      | N/A      |")
                continue
            marker = " ←" if z == best_hl_z else ""
            lines.append(
                f"| {z:.2f}      | {s['n']:>6} | "
                f"{s['mean_pnl']:>8.0f} | {s['sharpe']:>6.3f} | "
                f"{s['win_rate']:>7.0%} | {s['avg_hold']:>8.1f}{marker} |"
            )

        lines += [""]

    lines += [
        "---",
        "## Sensitivity Summary",
        "",
        "Uplift relative to current z=0.5 exit threshold.",
        "",
        "| Threshold | Δ Sharpe | Δ Mean P&L | Δ Hold Time |",
        "|-----------|----------|------------|-------------|",
    ]

    for z in EXIT_THRESHOLDS:
        s = sensitivity.get(z, {})
        def _pct(v):
            return f"{v:+.1f}%" if v is not None else "—"
        lines.append(
            f"| {z:.2f}      | "
            f"{_pct(s.get('sharpe_uplift_pct'))} | "
            f"{_pct(s.get('pnl_uplift_pct'))} | "
            f"{_pct(s.get('hold_change_pct'))} |"
        )

    lines += [
        "",
        "---",
        "## Next Steps",
        "",
        "If the recommendation above calls for a threshold change, update:",
        "",
        "1. `scripts/threshold_sensitivity.py` — `THRESHOLDS[\"exit_z\"]`",
        "2. `tasks.py` — Gate 5 exit condition and agent backstory",
        "3. `cost_model.py` / `correlation_tool.py` — P&L estimates",
        "4. Re-run Item 10 (P&L audit) with the new threshold to validate",
        "5. Update composition YAML files if exit_z is parameterised",
        "",
        "---",
        "## Methodology",
        "",
        "1. Load all closed trades from the trial ledger",
        "2. For each trade, fetch Tiingo adjusted closes from 750 days before",
        "   entry through the original exit date",
        "3. Use stored hedge_ratio / spread_mean / spread_std where available;",
        "   fall back to OLS on the 250-day training window",
        "4. Walk forward from entry, recording the first day z ≤ each threshold",
        "5. If no threshold is crossed before the original exit, record timeout",
        f"6. Compute net P&L = gross P&L − {COSTS_BPS:.0f} bps (round-trip costs)",
        "7. Aggregate: Sharpe = mean_pnl / std_pnl (not annualised — consistent",
        "   across thresholds is what matters, not absolute value)",
        "8. Stratify by half-life bin; repeat aggregation",
        "",
        f"*Trade-cost assumption: {COSTS_BPS} bps round-trip (conservative; adjust*",
        "*`COSTS_BPS` in the script for your actual execution costs.)*",
        "",
        f"*Report generated {now}*",
    ]

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as fh:
        fh.write("\n".join(lines) + "\n")

    print(f"\nReport written → {output_path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="ShiftInnerV — Exit z-score threshold optimization (Item 16)"
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB,
        help=f"Path to trial_ledger.db (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUT,
        help=f"Output markdown report path (default: {DEFAULT_OUT})",
    )
    parser.add_argument(
        "--min-trades",
        type=int,
        default=MIN_TRADES_FOR_CONCLUSION,
        help=f"Minimum simulated trades for a firm recommendation (default: {MIN_TRADES_FOR_CONCLUSION})",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print per-trade diagnostics",
    )
    args = parser.parse_args()

    print("ShiftInnerV — Exit Z-Score Threshold Optimization  (Item 16)")
    print("=" * 64)

    if not TIINGO_KEY:
        print("\nERROR: TIINGO_KEY not found in ~/.shiftinnerv_env or environment.")
        print("       Set TIINGO_KEY=<your_key> and retry.")
        sys.exit(1)

    # 1. Load ledger
    ledger = load_ledger(args.db, verbose=args.verbose)
    if ledger is None or len(ledger) == 0:
        print("\nNo closed trades found. Run monitor.py to accumulate entries.")
        sys.exit(1)

    print(f"\nFound {len(ledger)} closed trades.")

    if len(ledger) < args.min_trades:
        print(
            f"WARNING: Only {len(ledger)} trades available "
            f"(recommend ≥ {args.min_trades} for reliable results). "
            "Continuing anyway."
        )

    # 2. Simulate exits for each trade
    print(f"\nSimulating {len(EXIT_THRESHOLDS)} exit thresholds × {len(ledger)} trades...")
    sim_results = []
    trade_ids   = []
    n_failed    = 0

    for idx, row in ledger.iterrows():
        trade = row.to_dict()
        counter = f"[{len(sim_results)+1+n_failed}/{len(ledger)}]"

        if args.verbose or (len(sim_results) + n_failed + 1) % 5 == 0:
            print(f"  {counter} {trade['ticker1']}/{trade['ticker2']}")

        result = simulate_trade(trade, verbose=args.verbose)

        if result is None:
            n_failed += 1
        else:
            sim_results.append(result)
            trade_ids.append(trade["id"])

    print(f"\n  Simulated: {len(sim_results)}  |  Failed/skipped: {n_failed}")

    if not sim_results:
        print("ERROR: No trades successfully simulated. Check TIINGO_KEY and DB.")
        sys.exit(1)

    # 3. Aggregate
    print("\nAggregating results...")
    agg_overall = aggregate_overall(sim_results)
    agg_by_hl   = aggregate_by_hl(ledger, sim_results, trade_ids)
    sensitivity = compute_sensitivity(agg_overall)

    # 4. Print quick summary to console
    print("\n── Quick Summary ─────────────────────────────────────────────────")
    print(f"{'Threshold':>10} | {'Sharpe':>7} | {'Mean P&L':>9} | {'Win Rate':>9} | {'Avg Hold':>9}")
    print("-" * 60)
    for z in EXIT_THRESHOLDS:
        s = agg_overall.get(z, {})
        if not s:
            continue
        mark = " ←" if z == CURRENT_EXIT_Z else "  "
        print(
            f"  z={z:.2f}{mark}   | {s['sharpe']:>7.3f} | "
            f"{s['mean_pnl']:>9.1f} | {s['win_rate']:>8.0%} | {s['avg_hold']:>9.1f}"
        )
    print()

    verdict, detail = make_recommendation(agg_overall, sensitivity, len(sim_results))
    print(f"Recommendation: {verdict}")
    print(f"  {detail}")

    # 5. Write full report
    generate_report(
        agg_overall, agg_by_hl, sensitivity,
        n_trades=len(sim_results),
        n_failed=n_failed,
        output_path=args.output,
    )

    print("\nDone.")


if __name__ == "__main__":
    main()
