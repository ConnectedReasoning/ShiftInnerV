#!/usr/bin/env python3
"""
ShiftInnerV — Deflated Sharpe Ratio Computation
Item 14 of the Council Roadmap.

After 50+ closed trials, computes the Deflated Sharpe Ratio to detect whether
the observed edge is real or overfitted.

Usage:
    python scripts/compute_dsr.py
    python scripts/compute_dsr.py --db /path/to/trial_ledger.db
    python scripts/compute_dsr.py --output /path/to/report.md
    python scripts/compute_dsr.py --min-trials 30   # lower threshold for early testing

Reference: Marcos López de Prado, "Advances in Financial Machine Learning",
Chapter 6 — "The Deflated Sharpe Ratio"
"""

import os
import sys
import argparse
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from dotenv import load_dotenv
load_dotenv(os.path.expanduser("~/.shiftinnerv_env"))

from shiftinnerv.services.trial_ledger import load_closed_trials, get_ledger_summary

# ── Config ────────────────────────────────────────────────────────────────────

PROJECT_DIR      = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, str(PROJECT_DIR))
DATA_DIR    = os.path.join(PROJECT_DIR, "data")

DEFAULT_DB  = os.path.join(DATA_DIR, "trial_ledger.db")
DEFAULT_OUT = os.path.join(PROJECT_DIR,  "dsr_report.md")

MIN_TRIALS_DEFAULT = 50
TRADING_DAYS_PER_YEAR = 250


# ── Sharpe helpers ────────────────────────────────────────────────────────────

def compute_sharpe(returns: np.ndarray) -> float:
    """Annualised Sharpe ratio. Returns NaN if fewer than 2 observations."""
    if len(returns) < 2:
        return np.nan
    mu  = np.mean(returns)
    sig = np.std(returns, ddof=1)
    if sig == 0:
        return np.nan
    return (mu / sig) * np.sqrt(TRADING_DAYS_PER_YEAR)


def compute_subsample_correlation(returns: np.ndarray,
                                  subsample_size: int = 5) -> float:
    """
    Estimate γ: autocorrelation of Sharpe ratios across consecutive blocks.

    High γ  → trials share a common factor (regime, risk-on/off)
               → Sharpe is inflated by correlated bets
    Low γ   → trials are approximately independent
               → Sharpe is more reliable
    """
    n = len(returns)
    if n < 2 * subsample_size:
        return np.nan

    n_blocks = n // subsample_size
    block_sharpes = []
    for i in range(n_blocks):
        blk = returns[i * subsample_size: (i + 1) * subsample_size]
        sr  = compute_sharpe(blk)
        if not np.isnan(sr):
            block_sharpes.append(sr)

    if len(block_sharpes) < 2:
        return np.nan

    gamma = float(np.corrcoef(block_sharpes[:-1], block_sharpes[1:])[0, 1])
    return float(np.nan_to_num(gamma))


def compute_dsr(sharpe: float, gamma: float) -> float:
    """
    DSR = SR × √((1 − γ) / (1 + γ))

    This deflates the observed Sharpe by the degree to which trials are
    correlated.  A strategy that happens to trade in the same direction
    repeatedly during a favourable regime will see its Sharpe penalised.

    Interpretation:
      DSR < 1.0  — likely overfitted; do not trade live
      DSR 1.0–1.5 — reasonable edge; proceed with caution
      DSR > 1.5  — robust; ready for live trading
    """
    if not np.isfinite(sharpe) or not np.isfinite(gamma):
        return np.nan
    if gamma >= 1.0 or gamma <= -1.0:
        return np.nan
    denom = 1.0 + gamma
    if denom <= 0:
        return np.nan
    return sharpe * np.sqrt((1.0 - gamma) / denom)


# ── Analysis ──────────────────────────────────────────────────────────────────

def analyze_trials(df: pd.DataFrame, subsample_size: int = 5) -> dict:
    """
    Compute full trial statistics and DSR from a closed-trials DataFrame.

    Raises no exceptions — all errors surfaced via the 'error' key.
    """
    if df is None or len(df) == 0:
        return {"error": "No closed trials found in ledger."}

    pnls       = df["net_pnl_bps"].values.astype(float)
    profitable = int(df["is_profitable"].sum())
    n          = len(df)
    win_rate   = profitable / n

    sharpe = compute_sharpe(pnls)
    gamma  = compute_subsample_correlation(pnls, subsample_size=subsample_size)
    dsr    = compute_dsr(sharpe, gamma)

    # Time span
    df["_date"] = pd.to_datetime(df["verdict_timestamp"]).dt.date
    first_date  = df["_date"].min()
    last_date   = df["_date"].max()
    days_elapsed = (last_date - first_date).days

    # Stratify by half-life (fast <30d vs slow ≥30d)
    fast_mask = df["half_life"].notna() & (df["half_life"] < 30)
    slow_mask = df["half_life"].notna() & (df["half_life"] >= 30)
    fast_mean = float(np.mean(pnls[fast_mask])) if fast_mask.any() else np.nan
    slow_mean = float(np.mean(pnls[slow_mask])) if slow_mask.any() else np.nan

    # Exit reason breakdown
    exit_counts = df["exit_reason"].value_counts().to_dict() if "exit_reason" in df else {}

    # Gate pass-rates for self-audit
    gate_pass = {}
    for g in ["gate_1", "gate_2", "gate_3", "gate_4", "gate_6", "gate_7"]:
        col = f"{g}_result"
        if col in df:
            pass_n = (df[col].str.upper().str.startswith("PASS")).sum()
            gate_pass[g] = pass_n / n

    return {
        "n_trials":       n,
        "days_elapsed":   days_elapsed,
        "first_verdict":  str(first_date),
        "last_verdict":   str(last_date),
        "profitable":     profitable,
        "win_rate":       win_rate,
        "median_pnl_bps": float(np.median(pnls)),
        "mean_pnl_bps":   float(np.mean(pnls)),
        "std_pnl_bps":    float(np.std(pnls, ddof=1)),
        "min_pnl_bps":    float(np.min(pnls)),
        "max_pnl_bps":    float(np.max(pnls)),
        "sharpe":         float(sharpe),
        "gamma":          float(gamma),
        "dsr":            float(dsr),
        "subsample_size": subsample_size,
        "fast_hl_mean_pnl": fast_mean,
        "slow_hl_mean_pnl": slow_mean,
        "exit_counts":    exit_counts,
        "gate_pass_rates": gate_pass,
    }


# ── Report generation ─────────────────────────────────────────────────────────

def _fmt(val, fmt=".2f", fallback="N/A"):
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return fallback
    return format(val, fmt)


def generate_report(analysis: dict, output_path: str) -> None:
    """Write the DSR report as Markdown."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines = [
        "# ShiftInnerV — Deflated Sharpe Ratio Report",
        f"*Generated {now}*",
        "",
        "---",
        "",
        "## Executive Summary",
        "",
    ]

    if "error" in analysis:
        lines += [f"⚠️  {analysis['error']}", ""]
    else:
        a = analysis
        dsr = a["dsr"]

        # ── Verdict block ──────────────────────────────────────────────────
        if np.isnan(dsr):
            lines += [
                "⚠️  **DSR could not be computed** — insufficient variance in block Sharpes.",
                "",
                "Accumulate more trials with varied entry/exit conditions and retry.",
                "",
            ]
        elif dsr < 1.0:
            lines += [
                f"❌ **DSR = {dsr:.3f}  (< 1.0 — likely overfitted)**",
                "",
                "**STOP.** The observed Sharpe ratio is not reliable.",
                "",
                "Recommended actions:",
                "1. Review methodology — are gate thresholds too permissive?",
                "2. Check for look-ahead bias in the statistical analysis.",
                "3. Accumulate 100+ trials before re-testing.",
                "4. **Do NOT proceed to live trading.**",
                "",
            ]
        elif dsr < 1.5:
            lines += [
                f"⚠️  **DSR = {dsr:.3f}  (1.0 – 1.5 — reasonable, proceed with caution)**",
                "",
                "The edge exists with moderate confidence.",
                "",
                "Recommended actions:",
                "1. Continue shadow trading to accumulate data.",
                "2. Re-run DSR after 100+ closed trials.",
                "3. If live trading: start with ¼ of target position size.",
                "4. Monitor DSR monthly; pause if it drops below 1.0.",
                "",
            ]
        else:
            lines += [
                f"✅ **DSR = {dsr:.3f}  (> 1.5 — robust)**",
                "",
                "The edge is statistically significant and unlikely due to luck.",
                "",
                "Recommended actions:",
                "1. Proceed to live trading with Kelly-optimal position sizing.",
                "2. Maintain the ledger; track live P&L against shadow P&L.",
                "3. Re-compute DSR monthly on the expanding trial set.",
                "",
            ]

        # ── Performance table ──────────────────────────────────────────────
        lines += [
            "## Performance Metrics",
            "",
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Closed trials | {a['n_trials']} |",
            f"| Time span | {a['days_elapsed']} days ({a['first_verdict']} → {a['last_verdict']}) |",
            f"| Win rate | {a['win_rate']:.1%}  ({a['profitable']}/{a['n_trials']}) |",
            f"| Median net P&L | {_fmt(a['median_pnl_bps'], '.0f')} bps |",
            f"| Mean net P&L | {_fmt(a['mean_pnl_bps'], '.0f')} bps |",
            f"| Std dev | {_fmt(a['std_pnl_bps'], '.0f')} bps |",
            f"| Min / Max | {_fmt(a['min_pnl_bps'], '.0f')} / {_fmt(a['max_pnl_bps'], '.0f')} bps |",
            f"| Annualised Sharpe | {_fmt(a['sharpe'], '.3f')} |",
            f"| Block correlation γ | {_fmt(a['gamma'], '.3f')} |",
            f"| **Deflated Sharpe (DSR)** | **{_fmt(a['dsr'], '.3f')}** |",
            f"| Block size (subsample) | {a['subsample_size']} trials |",
            "",
        ]

        # ── Half-life breakdown ────────────────────────────────────────────
        if not (np.isnan(a["fast_hl_mean_pnl"]) and np.isnan(a["slow_hl_mean_pnl"])):
            lines += [
                "## Performance by Half-Life",
                "",
                "| Cohort | Mean P&L |",
                "|--------|----------|",
                f"| Fast (HL < 30d) | {_fmt(a['fast_hl_mean_pnl'], '.0f')} bps |",
                f"| Slow (HL ≥ 30d) | {_fmt(a['slow_hl_mean_pnl'], '.0f')} bps |",
                "",
            ]

        # ── Exit reasons ───────────────────────────────────────────────────
        if a["exit_counts"]:
            lines += ["## Exit Reasons", ""]
            for reason, count in sorted(a["exit_counts"].items(),
                                        key=lambda x: -x[1]):
                pct = count / a["n_trials"] * 100
                lines.append(f"- {reason}: {count} ({pct:.0f}%)")
            lines.append("")

        # ── Gate pass-rates ────────────────────────────────────────────────
        if a["gate_pass_rates"]:
            lines += [
                "## Gate Pass Rates (on closed trials)",
                "",
                "| Gate | Pass Rate |",
                "|------|-----------|",
            ]
            gate_labels = {
                "gate_1": "Gate 1 — Cointegration",
                "gate_2": "Gate 2 — Half-life",
                "gate_3": "Gate 3 — SNR",
                "gate_4": "Gate 4 — Episodes",
                "gate_6": "Gate 6 — Factor Exposure",
                "gate_7": "Gate 7 — Net P&L",
            }
            for g, rate in a["gate_pass_rates"].items():
                label = gate_labels.get(g, g)
                lines.append(f"| {label} | {rate:.0%} |")
            lines.append("")

    # ── Methodology ────────────────────────────────────────────────────────
    lines += [
        "---",
        "",
        "## Methodology",
        "",
        "**Deflated Sharpe Ratio (López de Prado, 2018)**",
        "",
        "```",
        "DSR = SR × √((1 − γ) / (1 + γ))",
        "```",
        "",
        "- **SR** = annualised Sharpe ratio of net P&L (bps per trade)",
        "- **γ** = autocorrelation of block Sharpes across consecutive groups of trials",
        "",
        "The adjustment penalises strategies where trades are correlated — i.e. where",
        "the strategy repeatedly bets in the same direction during a favourable regime",
        "rather than generating independent edge.",
        "",
        "**Decision thresholds:**",
        "",
        "| DSR | Decision |",
        "|-----|----------|",
        "| < 1.0 | STOP — likely overfitted |",
        "| 1.0 – 1.5 | CAUTION — reasonable edge, limited sample |",
        "| > 1.5 | PROCEED — robust; go live |",
        "",
        "*Reference: López de Prado, 'Advances in Financial Machine Learning', Chapter 6.*",
    ]

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as f:
        f.write("\n".join(lines))
    print(f"Report written: {output_path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Compute Deflated Sharpe Ratio from the ShiftInnerV trial ledger"
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB,
        help=f"Path to trial_ledger.db  (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUT,
        help=f"Output markdown path  (default: {DEFAULT_OUT})",
    )
    parser.add_argument(
        "--min-trials",
        type=int,
        default=MIN_TRIALS_DEFAULT,
        help=f"Minimum closed trials required  (default: {MIN_TRIALS_DEFAULT})",
    )
    parser.add_argument(
        "--subsample",
        type=int,
        default=5,
        help="Block size for subsample correlation  (default: 5)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Run even if below minimum trial count",
    )
    args = parser.parse_args()

    print("ShiftInnerV — Deflated Sharpe Ratio Computation")
    print("=" * 60)

    # ── Summary ──────────────────────────────────────────────────────────────
    summary = get_ledger_summary(args.db)
    if not summary.get("exists"):
        print(f"ERROR: trial_ledger.db not found at {args.db}")
        print("       Run main.py to generate ACTIVE verdicts first.")
        sys.exit(1)

    closed = summary.get("closed") or 0
    open_n = summary.get("open") or 0
    total  = summary.get("total") or 0
    print(f"\nLedger: {total} total trials  |  {closed} closed  |  {open_n} open")

    if closed < args.min_trials and not args.force:
        print(f"\nInsufficient data: {closed} closed trials < {args.min_trials} minimum.")
        print(f"  Continue shadow trading and re-run when you have {args.min_trials}+ closed trials.")
        print(f"  Use --force to compute anyway (unreliable at this sample size).")
        sys.exit(0)

    if closed < args.min_trials:
        print(f"  ⚠️  Forced run with {closed} trials (below {args.min_trials} threshold).")

    # ── Load ──────────────────────────────────────────────────────────────────
    print("\nLoading closed trials...")
    df = load_closed_trials(args.db)
    if df is None or len(df) == 0:
        print("ERROR: Could not load closed trials.")
        sys.exit(1)
    print(f"  Loaded {len(df)} closed trials")

    # ── Analyse ───────────────────────────────────────────────────────────────
    print("\nComputing DSR...")
    analysis = analyze_trials(df, subsample_size=args.subsample)

    # ── Console summary ───────────────────────────────────────────────────────
    print()
    if "error" in analysis:
        print(f"  ERROR: {analysis['error']}")
    else:
        a = analysis
        print(f"  Trials : {a['n_trials']} over {a['days_elapsed']} days")
        print(f"  Win rate: {a['win_rate']:.1%}  "
              f"({a['profitable']}/{a['n_trials']})")
        print(f"  Mean P&L: {a['mean_pnl_bps']:.0f} bps  "
              f"(median: {a['median_pnl_bps']:.0f} bps)")
        print(f"  Sharpe  : {a['sharpe']:.3f}")
        print(f"  γ       : {a['gamma']:.3f}")
        print(f"  DSR     : {a['dsr']:.3f}")
        print()
        dsr = a["dsr"]
        if np.isnan(dsr):
            print("  ⚠️  DSR: not computable — accumulate more trials")
        elif dsr >= 1.5:
            print("  ✅  Ready for live trading")
        elif dsr >= 1.0:
            print("  ⚠️  Proceed with caution; continue shadow trading")
        else:
            print("  ❌  Do NOT proceed; revise methodology")

    # ── Write report ──────────────────────────────────────────────────────────
    print("\nGenerating report...")
    generate_report(analysis, args.output)


if __name__ == "__main__":
    main()
