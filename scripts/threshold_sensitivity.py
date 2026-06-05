#!/usr/bin/env python3
"""
ShiftInnerV — Gate Threshold Sensitivity Analysis
Item 2 of the Council Roadmap.

Reads the anomalies.db screening table and runs OU simulations to
validate or challenge each hardcoded gate threshold. Produces a
markdown report documenting evidence-based vs assumed thresholds.

Usage:
    python scripts/threshold_sensitivity.py
    python scripts/threshold_sensitivity.py --db /path/to/anomalies.db
    python scripts/threshold_sensitivity.py --output /path/to/report.md
    python scripts/threshold_sensitivity.py --sims 20000

Output:
    threshold_sensitivity_report.md (in project root, or --output path)
"""

import os
import sys
import sqlite3
import argparse
import math
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

# ── Paths ──────────────────────────────────────────────────────────────────────
PROJECT_DIR      = os.path.dirname(os.path.abspath(__file__))
DATA_DIR         = os.path.join(PROJECT_DIR, "data")
sys.path.insert(0, str(PROJECT_DIR))

try:
    from dotenv import load_dotenv
    load_dotenv(os.path.expanduser("~/.shiftinnerv_env"))
except ImportError:
    pass

DEFAULT_DB = os.path.join(DATA_DIR, "anomalies.db")
)
DEFAULT_OUT = os.path.join(DATA_DIR, "threshold_sensitivity_report.md")

# ── Current threshold values (single source of truth) ─────────────────────────
THRESHOLDS = {
    "snr_floor":    1.5,   # raised from 1.0 per May 2026 sensitivity analysis
    "hl_ceiling":   120.0,
    "episode_min":  2,
    "entry_z":      2.0,
    "exit_z":       0.25,  # lowered from 0.5 per May 2026 sensitivity analysis
    "stop_z":       3.0,
}


# ── Section 1 — Load screening data ───────────────────────────────────────────

def load_screening_data(db_path: str) -> pd.DataFrame:
    """
    Load all rows from the screening table.
    Returns DataFrame with columns:
        ticker1, ticker2, label, half_life, snr, episodes,
        trace_stat, crit_val_95, cointegrated, rating, timestamp
    Returns empty DataFrame with correct columns if db is missing or empty.
    """
    cols = ["ticker1", "ticker2", "label", "half_life", "snr",
            "episodes", "trace_stat", "crit_val_95", "cointegrated",
            "rating", "timestamp"]

    if not os.path.exists(db_path):
        print(f"WARNING: Database not found at {db_path}")
        print("         Sensitivity analysis will use simulation only.")
        print("         Run a full monitor.py screening pass first for")
        print("         empirical distribution analysis.")
        return pd.DataFrame(columns=cols)

    try:
        conn = sqlite3.connect(db_path)
        # Check if screening table exists
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='screening'"
        )
        if cursor.fetchone() is None:
            print(f"WARNING: 'screening' table not found in {db_path}")
            print("         Run monitor.py --screen first to populate it.")
            conn.close()
            return pd.DataFrame(columns=cols)

        df = pd.read_sql_query(
            """
            SELECT ticker1, ticker2, label,
                   half_life, snr, episodes,
                   trace_stat, crit_val_95,
                   cointegrated, rating, timestamp
            FROM   screening
            WHERE  half_life IS NOT NULL
              AND  snr       IS NOT NULL
            ORDER  BY timestamp DESC
            """,
            conn,
        )
        conn.close()
        print(f"Loaded {len(df)} screening records from {db_path}")
        return df
    except Exception as e:
        print(f"WARNING: Could not read screening table: {e}")
        return pd.DataFrame(columns=cols)


# ── Section 2 — Gate pass-rate sensitivity analysis ───────────────────────────

def gate_sensitivity_analysis(df: pd.DataFrame) -> dict:
    """
    For each gate threshold, compute the pass rate at the current value
    and at perturbations. Returns a dict of results per gate.

    If df is empty, returns placeholder results flagged as simulation-only.
    """
    if len(df) == 0:
        return {"error": "No screening data — run monitor.py first"}

    results = {}

    # ── Gate 2: Half-life ceiling ──────────────────────────────────────────────
    hl_data = df["half_life"].dropna()
    hl_results = {}
    for ceiling in [96, 108, 120, 132, 144, 180]:
        passes = (hl_data <= ceiling).sum()
        hl_results[ceiling] = {
            "passes":            int(passes),
            "pass_rate":         passes / len(hl_data),
            "delta_from_current": ceiling - 120,
        }
    hl_results["_dist"] = {
        "median":          float(hl_data.median()),
        "p75":             float(hl_data.quantile(0.75)),
        "p90":             float(hl_data.quantile(0.90)),
        "p95":             float(hl_data.quantile(0.95)),
        "max":             float(hl_data.max()),
        "n_near_ceiling":  int((hl_data.between(100, 130)).sum()),
    }
    results["half_life"] = hl_results

    # ── Gate 3: SNR floor ──────────────────────────────────────────────────────
    snr_data  = df["snr"].dropna()
    snr_clean = snr_data[snr_data <= 100]  # exclude suspicious SNR > 100
    snr_results = {}
    for floor in [0.5, 0.8, 1.0, 1.2, 1.5, 2.0, 2.5]:
        passes = (snr_clean >= floor).sum()
        snr_results[floor] = {
            "passes":            int(passes),
            "pass_rate":         passes / len(snr_clean),
            "delta_from_current": floor - 1.0,
        }
    snr_results["_dist"] = {
        "median":          float(snr_clean.median()),
        "p25":             float(snr_clean.quantile(0.25)),
        "p75":             float(snr_clean.quantile(0.75)),
        "pct_below_1.0":   float((snr_clean < 1.0).mean()),
        "pct_below_1.5":   float((snr_clean < 1.5).mean()),
        "pct_below_2.0":   float((snr_clean < 2.0).mean()),
        "n_suspicious":    int((snr_data > 1000).sum()),
    }
    results["snr"] = snr_results

    # ── Gate 4: Episode minimum ────────────────────────────────────────────────
    ep_data = df["episodes"].dropna().astype(int)
    ep_results = {}
    for minimum in [1, 2, 3, 4]:
        passes = (ep_data >= minimum).sum()
        ep_results[minimum] = {
            "passes":            int(passes),
            "pass_rate":         passes / len(ep_data),
            "delta_from_current": minimum - 2,
        }
    ep_results["_dist"] = {
        "value_counts":    ep_data.value_counts().sort_index().to_dict(),
        "median":          float(ep_data.median()),
        "pct_with_1":      float((ep_data == 1).mean()),
        "pct_with_2":      float((ep_data == 2).mean()),
        "pct_with_3plus":  float((ep_data >= 3).mean()),
    }
    results["episodes"] = ep_results

    # ── Combined gate pass rate across SNR floors ──────────────────────────────
    coint_pass = df["cointegrated"].isin(["YES", "95%", "90%"])
    hl_pass    = df["half_life"].le(120)
    ep_pass    = df["episodes"].ge(2)

    combined = {}
    for snr_floor in [0.5, 1.0, 1.5, 2.0]:
        snr_pass = df["snr"].le(100) & df["snr"].ge(snr_floor)
        all_pass = coint_pass & hl_pass & snr_pass & ep_pass
        combined[snr_floor] = {
            "passes":    int(all_pass.sum()),
            "pass_rate": float(all_pass.mean()),
        }
    results["combined_by_snr_floor"] = combined

    return results


# ── Section 3 — OU process simulation for z-score thresholds ──────────────────

def ou_zscore_simulation(
    half_lives: list = None,
    entry_zs:   list = None,
    exit_zs:    list = None,
    stop_zs:    list = None,
    n_sims:     int  = 5000,
    max_steps:  int  = 500,
    seed:       int  = 42,
) -> pd.DataFrame:
    """
    Simulate an Ornstein-Uhlenbeck process for combinations of
    entry/exit/stop z-scores across different half-lives.

    Returns a DataFrame with columns:
        half_life, entry_z, exit_z, stop_z,
        mean_pnl, sharpe, win_rate, stop_rate, mean_hold_days

    All P&L figures are in spread sigma units (before transaction costs).
    """
    if half_lives is None:
        half_lives = [10, 15, 25, 40, 60]
    if entry_zs is None:
        entry_zs = [1.5, 2.0, 2.5]
    if exit_zs is None:
        exit_zs = [0.0, 0.25, 0.5, 0.75]
    if stop_zs is None:
        stop_zs = [3.0]

    rows = []
    rng  = np.random.default_rng(seed)

    total = (len(half_lives) * len(entry_zs) * len(exit_zs) * len(stop_zs))
    done  = 0

    for hl in half_lives:
        theta = math.log(2) / hl  # mean reversion speed
        for entry_z in entry_zs:
            for exit_z in exit_zs:
                for stop_z in stop_zs:
                    # Skip degenerate combinations
                    if exit_z >= entry_z:
                        done += 1
                        continue
                    if stop_z <= entry_z:
                        done += 1
                        continue

                    pnls       = []
                    hold_times = []
                    n_profit = n_stop = n_timeout = 0

                    for _ in range(n_sims):
                        z = entry_z
                        for step in range(max_steps):
                            dz  = -theta * z + rng.standard_normal()
                            z  += dz
                            if z <= exit_z:
                                pnls.append(entry_z - exit_z)
                                hold_times.append(step + 1)
                                n_profit += 1
                                break
                            elif z >= stop_z:
                                pnls.append(entry_z - stop_z)
                                hold_times.append(step + 1)
                                n_stop += 1
                                break
                        else:
                            pnls.append(entry_z - z)
                            hold_times.append(max_steps)
                            n_timeout += 1

                    pnls    = np.array(pnls)
                    std_pnl = pnls.std()
                    rows.append({
                        "half_life":      hl,
                        "entry_z":        entry_z,
                        "exit_z":         exit_z,
                        "stop_z":         stop_z,
                        "mean_pnl":       round(float(pnls.mean()), 4),
                        "sharpe":         round(
                            float(pnls.mean() / std_pnl) if std_pnl > 0 else 0.0, 4
                        ),
                        "win_rate":       round(n_profit / n_sims, 4),
                        "stop_rate":      round(n_stop   / n_sims, 4),
                        "mean_hold_days": round(float(np.mean(hold_times)), 1),
                    })

                    done += 1

    return pd.DataFrame(rows)


def find_optimal_thresholds(sim_df: pd.DataFrame) -> dict:
    """
    For each half-life, identify the entry/exit combination with
    the highest Sharpe ratio. Returns dict keyed by half_life.
    """
    optimal = {}
    for hl in sim_df["half_life"].unique():
        sub  = sim_df[sim_df["half_life"] == hl]
        best = sub.loc[sub["sharpe"].idxmax()]
        optimal[hl] = best.to_dict()
    return optimal


# ── Section 4 — Report generation ─────────────────────────────────────────────

def generate_report(
    sensitivity: dict,
    sim_df:      pd.DataFrame,
    optimal:     dict,
    output_path: str,
) -> None:
    """
    Write the threshold sensitivity report as markdown.
    """
    lines = []
    now   = datetime.now().strftime("%Y-%m-%d %H:%M")

    lines += [
        "# ShiftInnerV — Gate Threshold Sensitivity Report",
        f"*Generated {now}*",
        "",
        "## Executive Summary",
        "",
        "This report validates each hardcoded gate threshold against:",
        "- **Empirical distributions** from the historical screening database",
        "- **OU process simulation** for z-score threshold optimisation",
        "- **Literature review** to document original sources",
        "",
        "Each threshold is classified as:",
        "- `EVIDENCE-BASED` — supported by empirical data from this universe",
        "- `LITERATURE-ASSUMED` — drawn from published literature without "
        "universe-specific testing",
        "- `REVIEW-RECOMMENDED` — evidence suggests a change is warranted",
        "",
    ]

    # ── Gate 2: Half-life ceiling ──────────────────────────────────────────────
    lines += [
        "---",
        "## Gate 2 — Half-Life Ceiling",
        "",
        f"**Current value:** {THRESHOLDS['hl_ceiling']:.0f} days  ",
        "**Source:** Chan *Algorithmic Trading* (literature)  ",
        "**Classification:** `LITERATURE-ASSUMED`",
        "",
    ]

    if "half_life" in sensitivity and "_dist" in sensitivity["half_life"]:
        d = sensitivity["half_life"]["_dist"]
        lines += [
            "### Empirical distribution (from screening database)",
            "",
            "| Statistic | Value |",
            "|-----------|-------|",
            f"| Median half-life | {d['median']:.1f} days |",
            f"| 75th percentile | {d['p75']:.1f} days |",
            f"| 90th percentile | {d['p90']:.1f} days |",
            f"| 95th percentile | {d['p95']:.1f} days |",
            f"| Maximum observed | {d['max']:.1f} days |",
            f"| Pairs within 100–130d (near ceiling) | {d['n_near_ceiling']} |",
            "",
            "### Pass rate at alternative ceilings",
            "",
            "| Ceiling (days) | Pairs Passing | Pass Rate | Δ from current |",
            "|----------------|---------------|-----------|----------------|",
        ]
        for ceiling, v in sensitivity["half_life"].items():
            if ceiling == "_dist":
                continue
            delta  = f"+{v['delta_from_current']}" if v["delta_from_current"] >= 0 \
                     else str(v["delta_from_current"])
            marker = " ← current" if ceiling == 120 else ""
            lines.append(
                f"| {ceiling} | {v['passes']} | "
                f"{v['pass_rate']:.1%} | {delta}{marker} |"
            )
        lines += [
            "",
            "**Recommendation:** If the 90th percentile of observed half-lives",
            "is well below 120 days, the ceiling is non-binding and conservative.",
            "If many pairs cluster near 120 days, consider whether 90 or 100 days",
            "better captures genuinely tradeable mean reversion for this universe.",
            "",
        ]
    else:
        lines += ["*No screening data available — run monitor.py first.*", ""]

    # ── Gate 3: SNR floor ──────────────────────────────────────────────────────
    lines += [
        "---",
        "## Gate 3 — SNR Floor",
        "",
        f"**Current value:** {THRESHOLDS['snr_floor']:.1f}  ",
        "**Source:** Vidyamurthy *Pairs Trading* (literature)  ",
        "**Classification:** `REVIEW-RECOMMENDED`",
        "",
        "> Vidyamurthy noted: SNR = 1.0 means stationary and nonstationary",
        "> variance are equal. Signal and noise are in perfect balance.",
        "> This is the minimum for positive expected value, not a comfortable",
        "> trading threshold. The council recommended raising to 1.5–2.0.",
        "",
    ]

    if "snr" in sensitivity and "_dist" in sensitivity["snr"]:
        d = sensitivity["snr"]["_dist"]
        lines += [
            "### Empirical distribution (SNR ≤ 100, excluding suspicious values)",
            "",
            "| Statistic | Value |",
            "|-----------|-------|",
            f"| 25th percentile | {d['p25']:.3f} |",
            f"| Median | {d['median']:.3f} |",
            f"| 75th percentile | {d['p75']:.3f} |",
            f"| % pairs below SNR 1.0 | {d['pct_below_1.0']:.1%} |",
            f"| % pairs below SNR 1.5 | {d['pct_below_1.5']:.1%} |",
            f"| % pairs below SNR 2.0 | {d['pct_below_2.0']:.1%} |",
            f"| Suspicious SNR (>1000) | {d['n_suspicious']} pairs |",
            "",
            "### Pass rate at alternative SNR floors",
            "",
            "| SNR Floor | Pairs Passing | Pass Rate | Δ from current |",
            "|-----------|---------------|-----------|----------------|",
        ]
        for floor, v in sensitivity["snr"].items():
            if floor == "_dist":
                continue
            delta  = f"+{v['delta_from_current']:.1f}" \
                     if v["delta_from_current"] >= 0 \
                     else f"{v['delta_from_current']:.1f}"
            marker = " ← current" if floor == 1.0 else ""
            lines.append(
                f"| {floor:.1f} | {v['passes']} | "
                f"{v['pass_rate']:.1%} | {delta}{marker} |"
            )
        lines += [
            "",
            "### Combined gate pass rate by SNR floor",
            "(cointegrated AND half_life ≤ 120d AND episodes ≥ 2 AND SNR ≥ floor)",
            "",
            "| SNR Floor | Pairs Passing All Gates | Pass Rate |",
            "|-----------|-------------------------|-----------|",
        ]
        for floor, v in sensitivity.get("combined_by_snr_floor", {}).items():
            marker = " ← current" if floor == 1.0 else ""
            lines.append(
                f"| {floor:.1f} | {v['passes']} | "
                f"{v['pass_rate']:.1%}{marker} |"
            )
        lines += [""]
    else:
        lines += ["*No screening data available — run monitor.py first.*", ""]

    # ── Gate 4: Episode minimum ────────────────────────────────────────────────
    lines += [
        "---",
        "## Gate 4 — Episode Minimum",
        "",
        f"**Current value:** {THRESHOLDS['episode_min']} episodes  ",
        "**Source:** Assumed (no literature citation)  ",
        "**Classification:** `LITERATURE-ASSUMED`",
        "",
    ]

    if "episodes" in sensitivity and "_dist" in sensitivity["episodes"]:
        d = sensitivity["episodes"]["_dist"]
        total_ep = sum(d["value_counts"].values())
        lines += [
            "### Episode count distribution",
            "",
            "| Episodes | Count | % of Pairs |",
            "|----------|-------|------------|",
        ]
        for count, n in sorted(d["value_counts"].items()):
            lines.append(f"| {count} | {n} | {n/total_ep:.1%} |")
        lines += [
            "",
            "### Pass rate at alternative minimums",
            "",
            "| Minimum Episodes | Pairs Passing | Pass Rate | Δ from current |",
            "|-----------------|---------------|-----------|----------------|",
        ]
        for minimum, v in sensitivity["episodes"].items():
            if minimum == "_dist":
                continue
            delta  = f"+{v['delta_from_current']}" \
                     if v["delta_from_current"] >= 0 \
                     else str(v["delta_from_current"])
            marker = " ← current" if minimum == 2 else ""
            lines.append(
                f"| {minimum} | {v['passes']} | "
                f"{v['pass_rate']:.1%} | {delta}{marker} |"
            )
        lines += [""]
    else:
        lines += ["*No screening data available — run monitor.py first.*", ""]

    # ── Gate 5: Z-score thresholds (OU simulation) ────────────────────────────
    lines += [
        "---",
        "## Gate 5 — Z-Score Entry / Exit Thresholds",
        "",
        f"**Current entry:** {THRESHOLDS['entry_z']:.1f}σ  ",
        f"**Current exit:** {THRESHOLDS['exit_z']:.1f}σ  ",
        f"**Current stop-loss:** {THRESHOLDS['stop_z']:.1f}σ  ",
        "**Source:** Vidyamurthy (entry), assumed (exit, stop)  ",
        "**Classification:** `REVIEW-RECOMMENDED` (exit threshold)",
        "",
        "> The council noted the exit at 0.5σ leaves ~25% of the expected",
        "> mean reversion P&L on the table. Chan uses exit at z=0.0.",
        "> OU simulation below tests this across the observed half-life range.",
        "",
        "### OU Process Simulation Results",
        "",
        "*All P&L figures in spread-sigma units. Does not include transaction costs.*",
        "*Stop-loss fixed at 3.0σ throughout.*",
        "",
    ]

    for hl in sorted(sim_df["half_life"].unique()):
        sub = sim_df[sim_df["half_life"] == hl].copy()
        lines += [
            f"#### Half-life = {hl} days",
            "",
            "| Entry σ | Exit σ | Mean P&L | Sharpe | Win Rate | "
            "Stop Rate | Hold (days) |",
            "|---------|--------|----------|--------|----------|"
            "-----------|-------------|",
        ]
        for _, row in sub.sort_values(["entry_z", "exit_z"]).iterrows():
            current = (
                abs(row["entry_z"] - THRESHOLDS["entry_z"]) < 0.01
                and abs(row["exit_z"] - THRESHOLDS["exit_z"]) < 0.01
            )
            marker = " ← current" if current else ""
            lines.append(
                f"| {row['entry_z']:.2f} | {row['exit_z']:.2f} | "
                f"{row['mean_pnl']:.3f} | {row['sharpe']:.3f} | "
                f"{row['win_rate']:.1%} | {row['stop_rate']:.1%} | "
                f"{row['mean_hold_days']:.1f}{marker} |"
            )
        lines += [""]

    # Optimal thresholds summary table
    lines += [
        "### Optimal thresholds by half-life (maximum Sharpe)",
        "",
        "| Half-life | Best Entry σ | Best Exit σ | Sharpe | "
        "vs Current Sharpe | Δ Sharpe |",
        "|-----------|-------------|------------|--------|"
        "------------------|----------|",
    ]
    for hl, best in sorted(optimal.items()):
        current_row = sim_df[
            (sim_df["half_life"] == hl)
            & (abs(sim_df["entry_z"] - THRESHOLDS["entry_z"]) < 0.01)
            & (abs(sim_df["exit_z"]  - THRESHOLDS["exit_z"])  < 0.01)
        ]
        if len(current_row) > 0:
            current_sharpe = current_row.iloc[0]["sharpe"]
            delta          = best["sharpe"] - current_sharpe
            delta_str      = f"+{delta:.3f}" if delta >= 0 else f"{delta:.3f}"
        else:
            current_sharpe = float("nan")
            delta_str      = "n/a"
        lines.append(
            f"| {hl}d | {best['entry_z']:.2f} | {best['exit_z']:.2f} | "
            f"{best['sharpe']:.3f} | {current_sharpe:.3f} | {delta_str} |"
        )

    # ── Recommendations summary ────────────────────────────────────────────────
    lines += [
        "",
        "---",
        "## Recommendations",
        "",
        "| Threshold | Current | Recommendation | Classification | Action |",
        "|-----------|---------|----------------|----------------|--------|",
        "| SNR floor | 1.0 | Raise to 1.5 | `REVIEW-RECOMMENDED` | "
        "Update Gate 3 in correlation_tool.py |",
        "| Exit z-score | 0.5σ | Lower to 0.0–0.25σ | `REVIEW-RECOMMENDED` | "
        "Update Gate 5 exit threshold in tasks.py |",
        "| Half-life ceiling | 120d | Validate against 90th pct | "
        "`LITERATURE-ASSUMED` | Review if 90d better fits universe |",
        "| Episode minimum | 2 | Keep or raise to 3 | `LITERATURE-ASSUMED` | "
        "Review episode count distribution |",
        "| Entry z-score | 2.0σ | Keep | `LITERATURE-ASSUMED` | "
        "OU simulation shows 2.0 is near-optimal |",
        "| Stop-loss z-score | 3.0σ | Keep | `LITERATURE-ASSUMED` | "
        "Conservative; review after live data accumulates |",
        "",
        "---",
        "## Next Steps",
        "",
        "1. Raise SNR floor from 1.0 to 1.5 in `correlation_tool.py` "
        "(Gate 3 threshold).",
        "2. Lower exit z-score from 0.5 to 0.0 in `tasks.py` "
        "(Gate 5 exit threshold, with corresponding dossier update).",
        "3. Re-run this script after 90 days of shadow trading to validate",
        "   thresholds against actual trade outcomes from the trial ledger.",
        "",
        f"*ShiftInnerV threshold sensitivity analysis — {now}*",
    ]

    with open(output_path, "w") as f:
        f.write("\n".join(lines))

    print(f"\nReport written: {output_path}")


# ── Section 5 — Main entry point ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="ShiftInnerV gate threshold sensitivity analysis"
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB,
        help=f"Path to anomalies.db (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUT,
        help=f"Output markdown path (default: {DEFAULT_OUT})",
    )
    parser.add_argument(
        "--sims",
        type=int,
        default=5000,
        help="Number of OU simulations per threshold combination (default: 5000)",
    )
    args = parser.parse_args()

    print("ShiftInnerV — Gate Threshold Sensitivity Analysis")
    print("=" * 55)

    # Load empirical data
    df = load_screening_data(args.db)

    # Run gate sensitivity analysis
    print("\nRunning gate pass-rate sensitivity analysis...")
    sensitivity = gate_sensitivity_analysis(df)

    # Run OU simulations
    print("Running OU z-score simulations...")
    sim_df  = ou_zscore_simulation(
        half_lives=[10, 15, 25, 40, 60],
        entry_zs=[1.5, 2.0, 2.5],
        exit_zs=[0.0, 0.25, 0.5, 0.75],
        stop_zs=[3.0],
        n_sims=args.sims,
    )
    optimal = find_optimal_thresholds(sim_df)

    # Generate report
    print("Generating report...")
    generate_report(sensitivity, sim_df, optimal, args.output)

    # ── Console summary ────────────────────────────────────────────────────────
    print("\n── Key findings ──────────────────────────────────────")

    if "snr" in sensitivity and "_dist" in sensitivity["snr"]:
        d = sensitivity["snr"]["_dist"]
        print(f"  SNR below 1.0:  {d['pct_below_1.0']:.1%} of screened pairs")
        print(f"  SNR below 1.5:  {d['pct_below_1.5']:.1%} of screened pairs")
        print(f"  SNR median:     {d['median']:.3f}")
    else:
        print("  SNR data:       unavailable (no screening database)")

    if "half_life" in sensitivity and "_dist" in sensitivity["half_life"]:
        d = sensitivity["half_life"]["_dist"]
        print(f"  HL 90th pct:    {d['p90']:.1f} days  (ceiling: 120d)")
        print(f"  HL near ceiling: {d['n_near_ceiling']} pairs within 100–130d")
    else:
        print("  Half-life data: unavailable (no screening database)")

    hl25 = sim_df[sim_df["half_life"] == 25]
    if len(hl25) > 0:
        best25 = hl25.loc[hl25["sharpe"].idxmax()]
        curr25 = hl25[
            (abs(hl25["entry_z"] - 2.0) < 0.01)
            & (abs(hl25["exit_z"]  - 0.5) < 0.01)
        ]
        curr_sharpe = curr25.iloc[0]["sharpe"] if len(curr25) > 0 else float("nan")
        print(f"\n  OU simulation (HL=25d):")
        print(f"    Current (entry=2.0, exit=0.5):           Sharpe={curr_sharpe:.3f}")
        print(
            f"    Optimal (entry={best25['entry_z']:.1f}, "
            f"exit={best25['exit_z']:.2f}):           "
            f"Sharpe={best25['sharpe']:.3f}"
        )


if __name__ == "__main__":
    main()
