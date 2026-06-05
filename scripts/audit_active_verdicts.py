#!/usr/bin/env python3
"""
ShiftInnerV — Retrospective P&L Audit on Last 10 ACTIVE Verdicts
Item 10 of the Council Roadmap.

Fetches the 10 most recent ACTIVE verdicts from anomalies.db, reconstructs
the spread and hedge ratio for each, and computes what the actual P&L would
have been if traded with realistic costs deducted.

This is THE validation test: does the statistical edge survive execution?

Usage:
    python scripts/audit_active_verdicts.py
    python scripts/audit_active_verdicts.py --db /path/to/anomalies.db
    python scripts/audit_active_verdicts.py --verbose
    python scripts/audit_active_verdicts.py --output /path/to/audit_report.md
    python scripts/audit_active_verdicts.py --n 20          # audit more verdicts

Output:
    audit_active_verdicts_report.md (default: project root)
    Includes: individual trade P&L, aggregated metrics, verdict.
"""

import os
import sys
import sqlite3
import argparse
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import requests
from statsmodels.regression.linear_model import OLS
from statsmodels.tools import add_constant

PROJECT_DIR      = os.path.dirname(os.path.abspath(__file__))
DATA_DIR         = os.path.join(PROJECT_DIR, "data")
sys.path.insert(0, str(PROJECT_DIR))

from dotenv import load_dotenv
load_dotenv(os.path.expanduser("~/.shiftinnerv_env"))

from cost_model import compute_round_trip_costs, compute_net_pnl

# ── Configuration ────────────────────────────────────────────────────────────

# Support both spellings; TIINGO_KEY (dossier.py convention) takes priority
TIINGO_KEY = (
    os.getenv("TIINGO_KEY", "")
    or os.getenv("TIINGA_KEY", "")  # typo variant in the prompt spec
)


DEFAULT_DB  = os.path.join(DATA_DIR, "anomalies.db")
DEFAULT_OUT = os.path.join(PROJECT_DIR, "audit_active_verdicts_report.md")

TIINGO_BASE    = "https://api.tiingo.com"
TIINGO_HEADERS = {"Content-Type": "application/json"}

# Training window length (trading days)
TRAIN_WINDOW_DAYS = 250

# Maximum hold period in calendar days before we call timeout
MAX_HOLD_CALENDAR_DAYS = 120

# Entry signal threshold — skip pair if |entry_z| < this on verdict date
ENTRY_Z_THRESHOLD = 1.5

# Exit thresholds
EXIT_Z_PROFIT = 0.0   # z reverts to mean → profit-take
EXIT_Z_STOP   = 3.0   # z expands past 3σ   → stop-loss

# $10k test position size per leg
NOTIONAL = 10_000.0

# Known ETF tickers (for cost model classification)
ETF_SET = {
    "KWEB", "FXI", "ITA", "XLF", "SMH", "SPY", "QQQ", "IWM",
    "EEM", "ASHR", "CQQQ", "KBE", "KRE", "SOXX", "XLE", "GDX",
    "ICLN", "XBI", "BOTZ", "VNQ", "BDRY", "MOO", "DBC", "UUP",
    "TLT", "GLD", "SLV", "USO", "UDN", "REM", "XAR", "XLK",
}


# ── Tiingo API ───────────────────────────────────────────────────────────────

def fetch_price_history(ticker: str, start_date: str, end_date: str) -> pd.Series | None:
    """
    Fetch daily adjusted close prices from Tiingo.

    Parameters
    ----------
    ticker     : stock/ETF ticker symbol
    start_date : 'YYYY-MM-DD'
    end_date   : 'YYYY-MM-DD'

    Returns
    -------
    pd.Series indexed by normalized datetime, or None on failure.
    """
    if not TIINGO_KEY:
        print(f"  WARNING: TIINGO_KEY not set; cannot fetch {ticker}")
        return None

    url    = f"{TIINGO_BASE}/tiingo/daily/{ticker}/prices"
    params = {
        "startDate": start_date,
        "endDate":   end_date,
        "token":     TIINGO_KEY,
    }
    try:
        r = requests.get(url, params=params, headers=TIINGO_HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
        if not data:
            print(f"  WARNING: Tiingo returned empty data for {ticker}")
            return None
        df = pd.DataFrame(data)
        df["date"] = pd.to_datetime(df["date"]).dt.normalize()
        df = df.set_index("date").sort_index()
        col = "adjClose" if "adjClose" in df.columns else "close"
        series = df[col].dropna()
        if series.empty:
            return None
        return series
    except Exception as e:
        print(f"  WARNING: Tiingo fetch failed for {ticker}: {e}")
        return None


# ── Database ─────────────────────────────────────────────────────────────────

def get_last_active_verdicts(db_path: str, n: int = 10) -> list[dict]:
    """
    Fetch the n most recent ACTIVE verdicts from the screening table.

    Returns list of dicts; empty list on any error.
    """
    if not os.path.exists(db_path):
        print(f"ERROR: Database not found at {db_path}")
        return []

    try:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        cur  = conn.cursor()

        cur.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='screening'"
        )
        if cur.fetchone() is None:
            print("ERROR: 'screening' table not found in database")
            conn.close()
            return []

        cur.execute("""
            SELECT timestamp, ticker1, ticker2, rating,
                   half_life, snr, episodes, trace_stat, crit_val_95
            FROM   screening
            WHERE  rating = 'ACTIVE'
            ORDER  BY timestamp DESC
            LIMIT  ?
        """, (n,))

        rows = [dict(row) for row in cur.fetchall()]
        conn.close()
        return rows

    except Exception as e:
        print(f"ERROR: Database query failed: {e}")
        return []


# ── Core audit logic ──────────────────────────────────────────────────────────

def audit_active_verdict(verdict: dict, verbose: bool = False) -> dict:
    """
    Compute actual P&L for a single ACTIVE verdict.

    Returns a result dict.  On any unrecoverable error the 'error' key is
    set to a short string and all financial fields are absent.
    """
    ticker1 = verdict["ticker1"]
    ticker2 = verdict["ticker2"]

    result: dict = {
        "ticker1":      ticker1,
        "ticker2":      ticker2,
        "verdict_date": verdict["timestamp"],
        "error":        None,
    }

    try:
        # ── Step 1: parse dates ───────────────────────────────────────────────
        verdict_date = pd.to_datetime(verdict["timestamp"]).normalize()
        train_start  = verdict_date - timedelta(days=int(TRAIN_WINDOW_DAYS * 1.5))
        signal_end   = min(
            pd.Timestamp.now().normalize(),
            verdict_date + timedelta(days=MAX_HOLD_CALENDAR_DAYS),
        )

        if verbose:
            print(f"  [{ticker1}/{ticker2}] verdict={verdict_date.date()}, "
                  f"fetch window={train_start.date()}→{signal_end.date()}")

        # ── Step 2: fetch prices ──────────────────────────────────────────────
        prices1 = fetch_price_history(
            ticker1,
            train_start.strftime("%Y-%m-%d"),
            signal_end.strftime("%Y-%m-%d"),
        )
        prices2 = fetch_price_history(
            ticker2,
            train_start.strftime("%Y-%m-%d"),
            signal_end.strftime("%Y-%m-%d"),
        )

        if prices1 is None or prices2 is None:
            result["error"] = "price_fetch_failed"
            return result

        # Align on common dates
        prices1, prices2 = prices1.align(prices2, join="inner")

        if len(prices1) < TRAIN_WINDOW_DAYS + 5:
            result["error"] = f"insufficient_price_data ({len(prices1)} rows)"
            return result

        # ── Step 3: find verdict date in aligned series ───────────────────────
        # Use the last available trading day on or before verdict_date
        candidates = prices1.index[prices1.index <= verdict_date]
        if len(candidates) == 0:
            result["error"] = "verdict_date_before_price_history"
            return result
        entry_date = candidates[-1]
        entry_idx  = prices1.index.get_loc(entry_date)

        if entry_idx < TRAIN_WINDOW_DAYS:
            result["error"] = (
                f"insufficient_training_data "
                f"(only {entry_idx} days before verdict)"
            )
            return result

        # ── Step 4: reconstruct hedge ratio from training window ──────────────
        log_p1_train = np.log(prices1.iloc[entry_idx - TRAIN_WINDOW_DAYS : entry_idx])
        log_p2_train = np.log(prices2.iloc[entry_idx - TRAIN_WINDOW_DAYS : entry_idx])

        ols_fit      = OLS(log_p1_train, add_constant(log_p2_train)).fit()
        # params: [intercept, beta]
        hedge_ratio  = float(ols_fit.params.iloc[1]) if len(ols_fit.params) > 1 else 1.0

        spread_train = log_p1_train - hedge_ratio * log_p2_train
        spread_mean  = float(spread_train.mean())
        spread_std   = float(spread_train.std())

        if spread_std == 0.0:
            result["error"] = "spread_std_is_zero"
            return result

        # ── Step 5: compute entry spread and z-score ──────────────────────────
        log_p1_entry = float(np.log(prices1.iloc[entry_idx]))
        log_p2_entry = float(np.log(prices2.iloc[entry_idx]))
        entry_spread = log_p1_entry - hedge_ratio * log_p2_entry
        entry_z      = (entry_spread - spread_mean) / spread_std

        # We need |z| ≥ threshold; handle both long and short spread entries
        if abs(entry_z) < ENTRY_Z_THRESHOLD:
            result["error"] = "entry_signal_not_triggered"
            result["entry_z"] = round(entry_z, 3)
            return result

        # Determine direction: if spread is above mean we are SHORT the spread
        # (short ticker1, long ticker2); below mean we are LONG spread.
        long_spread = entry_z > 0  # True → long ticker1 / short ticker2

        # ── Step 6: simulate hold and find exit ───────────────────────────────
        half_life_days = int(verdict.get("half_life") or 30)
        max_idx        = min(len(prices1) - 1, entry_idx + half_life_days)

        exit_date   = None
        exit_z      = None
        exit_reason = None
        hold_days   = 0
        exit_idx    = max_idx  # default to timeout

        for fut_idx in range(entry_idx + 1, max_idx + 1):
            log_p1_f   = float(np.log(prices1.iloc[fut_idx]))
            log_p2_f   = float(np.log(prices2.iloc[fut_idx]))
            spread_f   = log_p1_f - hedge_ratio * log_p2_f
            z_f        = (spread_f - spread_mean) / spread_std

            if long_spread:
                # Long spread → profit when z falls toward 0
                if z_f <= EXIT_Z_PROFIT:
                    exit_idx, exit_z, exit_reason = fut_idx, z_f, "profit_take"
                    break
                elif z_f >= EXIT_Z_STOP:
                    exit_idx, exit_z, exit_reason = fut_idx, z_f, "stop_loss"
                    break
            else:
                # Short spread → profit when z rises toward 0
                if z_f >= -EXIT_Z_PROFIT:
                    exit_idx, exit_z, exit_reason = fut_idx, z_f, "profit_take"
                    break
                elif z_f <= -EXIT_Z_STOP:
                    exit_idx, exit_z, exit_reason = fut_idx, z_f, "stop_loss"
                    break

        # Settle any un-exited trade as timeout
        if exit_reason is None:
            log_p1_t   = float(np.log(prices1.iloc[exit_idx]))
            log_p2_t   = float(np.log(prices2.iloc[exit_idx]))
            spread_t   = log_p1_t - hedge_ratio * log_p2_t
            exit_z     = (spread_t - spread_mean) / spread_std
            exit_reason = "timeout"

        exit_date  = prices1.index[exit_idx]
        hold_days  = exit_idx - entry_idx

        # ── Step 7: compute gross P&L in dollars ─────────────────────────────
        entry_price_1 = float(prices1.iloc[entry_idx])
        entry_price_2 = float(prices2.iloc[entry_idx])
        exit_price_1  = float(prices1.iloc[exit_idx])
        exit_price_2  = float(prices2.iloc[exit_idx])

        if long_spread:
            # Long ticker1, short ticker2
            shares_1 = NOTIONAL / entry_price_1
            shares_2 = (NOTIONAL * abs(hedge_ratio)) / entry_price_2

            pnl_leg1 = shares_1 * (exit_price_1 - entry_price_1)   # long
            pnl_leg2 = shares_2 * (entry_price_2 - exit_price_2)   # short
        else:
            # Short ticker1, long ticker2
            shares_1 = NOTIONAL / entry_price_1
            shares_2 = (NOTIONAL * abs(hedge_ratio)) / entry_price_2

            pnl_leg1 = shares_1 * (entry_price_1 - exit_price_1)   # short
            pnl_leg2 = shares_2 * (exit_price_2 - entry_price_2)   # long

        gross_pnl_dollars   = pnl_leg1 + pnl_leg2
        total_entry_notional = NOTIONAL + NOTIONAL * abs(hedge_ratio)
        gross_pnl_bps       = (
            gross_pnl_dollars / total_entry_notional * 10_000
            if total_entry_notional > 0 else 0.0
        )

        # ── Step 8: transaction costs ─────────────────────────────────────────
        costs = compute_round_trip_costs(
            notional_leg1=NOTIONAL,
            notional_leg2=NOTIONAL * abs(hedge_ratio),
            market_cap1_b=None,
            market_cap2_b=None,
            daily_volume1_m=None,
            daily_volume2_m=None,
            is_etf1=ticker1.upper() in ETF_SET,
            is_etf2=ticker2.upper() in ETF_SET,
            half_life_days=max(1.0, float(hold_days)),
            ticker1=ticker1,
            ticker2=ticker2,
        )

        # ── Step 9: net P&L ───────────────────────────────────────────────────
        net_pnl = compute_net_pnl(gross_pnl_bps, costs["total_cost_bps"])

        result.update({
            "entry_date":     entry_date.strftime("%Y-%m-%d"),
            "exit_date":      exit_date.strftime("%Y-%m-%d"),
            "exit_reason":    exit_reason,
            "hold_days":      hold_days,
            "entry_z":        round(entry_z, 3),
            "exit_z":         round(float(exit_z), 3),
            "hedge_ratio":    round(hedge_ratio, 4),
            "long_spread":    long_spread,
            "gross_pnl_bps":  round(gross_pnl_bps, 1),
            "total_cost_bps": round(costs["total_cost_bps"], 1),
            "net_pnl_bps":    round(net_pnl["net_pnl_bps"], 1),
            "net_pnl_pct":    round(net_pnl["net_pnl_pct"], 4),
            "profitable":     net_pnl["is_profitable"],
            "marginal":       net_pnl["marginal"],
            "cost_breakdown": costs["cost_breakdown"],
            # Prices included for ledger integration (Item 14)
            "entry_price_1":  round(entry_price_1, 4),
            "entry_price_2":  round(entry_price_2, 4),
            "exit_price_1":   round(exit_price_1, 4),
            "exit_price_2":   round(exit_price_2, 4),
        })

        if verbose:
            direction = "long_spread" if long_spread else "short_spread"
            print(f"    → {direction} | entry_z={entry_z:.2f} | "
                  f"exit={exit_reason} in {hold_days}d | "
                  f"net={net_pnl['net_pnl_bps']:.0f} bps")

    except Exception as e:
        result["error"] = str(e)
        if verbose:
            import traceback
            traceback.print_exc()

    return result


# ── Report generation ─────────────────────────────────────────────────────────

def generate_audit_report(trades: list[dict], output_path: str) -> None:
    """Write the full audit report as markdown."""
    lines: list[str] = []
    now   = datetime.now().strftime("%Y-%m-%d %H:%M")

    valid   = [t for t in trades if not t.get("error")]
    invalid = [t for t in trades if t.get("error")]

    lines += [
        "# ShiftInnerV — Retrospective P&L Audit Report",
        f"*Generated {now}*",
        "",
        "## Executive Summary",
        "",
        "Retrospective audit of the most recent ACTIVE verdicts. Each verdict is",
        "simulated as if entered on the verdict date at closing price, held until",
        "the first exit signal (z→0, stop-loss at 3σ, or timeout at half-life),",
        "then closed. Transaction costs are deducted using `cost_model.py`.",
        "",
    ]

    if valid:
        net_pnls        = [t["net_pnl_bps"] for t in valid]
        profitable_count = sum(1 for t in valid if t["profitable"])
        win_rate        = profitable_count / len(valid)
        median_pnl      = float(np.median(net_pnls))
        mean_pnl        = float(np.mean(net_pnls))
        total_pnl       = float(np.sum(net_pnls))
        mean_hold       = float(np.mean([t["hold_days"] for t in valid]))
        mean_cost       = float(np.mean([t["total_cost_bps"] for t in valid]))
        mean_gross      = float(np.mean([t["gross_pnl_bps"] for t in valid]))

        lines += [
            f"| Metric | Value |",
            f"|--------|-------|",
            f"| Verdicts audited | {len(valid)} ({len(invalid)} errors) |",
            f"| Win rate | {win_rate:.0%} ({profitable_count}/{len(valid)}) |",
            f"| Median net P&L | {median_pnl:.0f} bps |",
            f"| Mean net P&L | {mean_pnl:.0f} bps |",
            f"| Mean gross P&L | {mean_gross:.0f} bps |",
            f"| Mean costs | {mean_cost:.0f} bps |",
            f"| Total net P&L | {total_pnl:.0f} bps |",
            f"| Average hold | {mean_hold:.1f} trading days |",
            "",
            "### Decision Verdict",
            "",
        ]

        if mean_pnl > 50:
            lines += [
                "✅ **POSITIVE EDGE CONFIRMED**",
                "",
                f"Mean net P&L of {mean_pnl:.0f} bps is robust after costs.",
                "Recommend: proceed to position sizing (Kelly criterion) and "
                "shadow P&L tracking before going live.",
            ]
        elif mean_pnl > 0:
            lines += [
                "⚠️  **MARGINAL EDGE**",
                "",
                f"Mean net P&L of {mean_pnl:.0f} bps is positive but thin.",
                "Recommend: review cost assumptions; consider optimising execution "
                "(better entry/exit thresholds, larger notional to reduce per-unit costs).",
                "",
                "Questions to answer before proceeding:",
                "- Are entry/exit z-score thresholds too conservative?",
                "- Can you tighten bid-ask via limit orders?",
                "- Does a larger position size improve cost efficiency?",
            ]
        else:
            lines += [
                "❌ **NO EDGE OR LOSSES**",
                "",
                f"Mean net P&L of {mean_pnl:.0f} bps — costs exceed profit.",
                "**Do not proceed to live trading.**",
                "",
                "Review:",
                "- Are SNR and half-life screening thresholds calibrated correctly?",
                "- Is the Johansen cointegration real under walk-forward validation?",
                "- Are entry/exit z-scores optimal for these pairs?",
                "- Are transaction cost estimates realistic for your broker?",
            ]
    else:
        lines += [
            "⚠️  **NO VALID TRADES**",
            "",
            "All verdicts encountered errors (price data unavailable, entry signal "
            "not triggered, etc.).",
            "Cannot assess edge.",
        ]

    # ── Individual trade table ────────────────────────────────────────────────
    lines += [
        "",
        "---",
        "## Individual Trade Audit",
        "",
        "| Pair | Verdict | Entry | Exit | Reason | Hold | Dir | "
        "Z In→Out | Hedge | Gross | Costs | Net | ✓? |",
        "|------|---------|-------|------|--------|------|-----|"
        "---------|-------|-------|-------|-----|-----|",
    ]

    for t in trades:
        if t.get("error"):
            lines.append(
                f"| {t['ticker1']}/{t['ticker2']} "
                f"| {str(t['verdict_date'])[:10]} "
                f"| — | — | ERROR: `{t['error']}` "
                f"| — | — | — | — | — | — | — | — |"
            )
        else:
            mark = "✓" if t["profitable"] else "✗"
            direction = "L↑" if t["long_spread"] else "S↓"
            lines.append(
                f"| {t['ticker1']}/{t['ticker2']} "
                f"| {str(t['verdict_date'])[:10]} "
                f"| {t['entry_date']} "
                f"| {t['exit_date']} "
                f"| {t['exit_reason']} "
                f"| {t['hold_days']}d "
                f"| {direction} "
                f"| {t['entry_z']:.2f}→{t['exit_z']:.2f} "
                f"| {t['hedge_ratio']:.3f} "
                f"| {t['gross_pnl_bps']:.0f} "
                f"| {t['total_cost_bps']:.0f} "
                f"| {t['net_pnl_bps']:.0f} "
                f"| {mark} |"
            )

    lines += [""]

    # ── Detailed per-trade results ─────────────────────────────────────────────
    lines += [
        "---",
        "## Detailed Trade Records",
        "",
    ]

    for t in trades:
        header = f"### {t['ticker1']}/{t['ticker2']}  ({str(t['verdict_date'])[:10]})"
        lines += [header]

        if t.get("error"):
            lines += [
                f"> ⚠️  Skipped — `{t['error']}`",
                *(
                    [f"> Entry z-score at verdict: {t['entry_z']:.3f}σ "
                     f"(threshold: {ENTRY_Z_THRESHOLD}σ)"]
                    if "entry_z" in t else []
                ),
                "",
            ]
            continue

        direction = (
            f"**Long spread** (long {t['ticker1']}, short {t['ticker2']})"
            if t["long_spread"]
            else f"**Short spread** (short {t['ticker1']}, long {t['ticker2']})"
        )
        status = (
            "PROFITABLE" if t["profitable"]
            else ("MARGINAL" if t["marginal"] else "LOSS")
        )

        lines += [
            f"- {direction}",
            f"- **Entry:** z = {t['entry_z']:.2f}σ on {t['entry_date']}",
            f"- **Exit:**  z = {t['exit_z']:.2f}σ on {t['exit_date']} "
            f"({t['exit_reason']}, {t['hold_days']} trading days)",
            f"- **Hedge ratio:** {t['hedge_ratio']:.4f}",
            f"- **Gross P&L:** {t['gross_pnl_bps']:.0f} bps",
            f"- **Costs:** {t['total_cost_bps']:.0f} bps",
            f"  - {t['cost_breakdown']}",
            f"- **Net P&L:** {t['net_pnl_bps']:.0f} bps "
            f"({t['net_pnl_pct']:.2%})",
            f"- **Status:** {status}",
            "",
        ]

    # ── Methodology notes ──────────────────────────────────────────────────────
    lines += [
        "---",
        "## Methodology",
        "",
        f"1. **Price data:** Tiingo adjusted close prices",
        f"2. **Training window:** {TRAIN_WINDOW_DAYS} trading days before verdict date",
        f"3. **Hedge ratio:** OLS regression log(P1) ~ log(P2) on training window",
        f"4. **Entry filter:** |entry z-score| ≥ {ENTRY_Z_THRESHOLD}σ on verdict date",
        f"5. **Exit — profit-take:** z crosses 0σ (spread mean-reverts)",
        f"6. **Exit — stop-loss:** |z| ≥ {EXIT_Z_STOP}σ (spread diverges further)",
        f"7. **Exit — timeout:** half-life trading days elapsed with no signal",
        f"8. **Notional:** ${NOTIONAL:,.0f} per leg",
        f"9. **Costs:** bid-ask + market impact + borrow + commission via `cost_model.py`",
        f"10. **Direction awareness:** long spread when entry_z > 0; "
        f"short spread when entry_z < 0",
        "",
        f"*ShiftInnerV audit report — {now}*",
    ]

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w") as fh:
        fh.write("\n".join(lines) + "\n")

    print(f"Report written: {output_path}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="ShiftInnerV — retrospective P&L audit on ACTIVE verdicts (Item 10)"
    )
    parser.add_argument(
        "--db",
        default=DEFAULT_DB,
        help=f"Path to anomalies.db (default: {DEFAULT_DB})",
    )
    parser.add_argument(
        "--output",
        default=DEFAULT_OUT,
        help=f"Output markdown report path (default: {DEFAULT_OUT})",
    )
    parser.add_argument(
        "--n",
        type=int,
        default=10,
        help="Number of ACTIVE verdicts to audit (default: 10)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print detailed per-trade diagnostics",
    )
    args = parser.parse_args()

    print("ShiftInnerV — Retrospective P&L Audit (Item 10)")
    print("=" * 52)
    print()

    if not TIINGO_KEY:
        print("WARNING: Neither TIINGO_KEY nor TIINGA_KEY found in environment.")
        print("         Set one in ~/.shiftinnerv_env before running.\n")

    # Load verdicts
    verdicts = get_last_active_verdicts(args.db, n=args.n)
    if not verdicts:
        print("No ACTIVE verdicts found in database.  Nothing to audit.")
        sys.exit(1)

    print(f"Loaded {len(verdicts)} ACTIVE verdicts from {args.db}")
    print()

    # Audit each verdict
    trades: list[dict] = []
    for i, verdict in enumerate(verdicts, 1):
        label = f"{verdict['ticker1']}/{verdict['ticker2']}"
        print(f"[{i:2d}/{len(verdicts)}] {label} ({str(verdict['timestamp'])[:10]}) …")
        trade = audit_active_verdict(verdict, verbose=args.verbose)
        trades.append(trade)
        if trade.get("error"):
            print(f"         → SKIP: {trade['error']}")
        else:
            marker = "✓" if trade["profitable"] else "✗"
            print(f"         → {marker}  net={trade['net_pnl_bps']:+.0f} bps  "
                  f"({trade['exit_reason']}, {trade['hold_days']}d)")

    # Generate report
    print()
    print("Generating report …")
    generate_audit_report(trades, args.output)

    # Print summary
    valid = [t for t in trades if not t.get("error")]
    print()
    print("── Summary ──────────────────────────────────────")
    print(f"  Audited:    {len(trades)}  |  Valid: {len(valid)}  |  "
          f"Errors: {len(trades) - len(valid)}")

    if valid:
        net_pnls = [t["net_pnl_bps"] for t in valid]
        winners  = sum(1 for t in valid if t["profitable"])
        print(f"  Win rate:   {winners}/{len(valid)} ({winners/len(valid):.0%})")
        print(f"  Median net P&L:  {np.median(net_pnls):+.0f} bps")
        print(f"  Mean net P&L:    {np.mean(net_pnls):+.0f} bps")

        mean_pnl = np.mean(net_pnls)
        if mean_pnl > 50:
            verdict_str = "✅ POSITIVE EDGE — proceed to position sizing"
        elif mean_pnl > 0:
            verdict_str = "⚠️  MARGINAL EDGE — optimise execution before live"
        else:
            verdict_str = "❌ NO EDGE — halt; revise methodology"

        print(f"\n  {verdict_str}")

    # ── Close trials in ledger (Item 14) ─────────────────────────────────────
    ledger_db = os.path.join(DATA_DIR, "trial_ledger.db")
    if os.path.exists(ledger_db) and valid:
        sys.path.insert(0, str(PROJECT_DIR))
        try:
            from shiftinnerv.services.trial_ledger import close_trial
            closed_ok = closed_fail = 0
            for t in valid:
                # Audit doesn't carry verdict_id; match by ticker pair + entry date
                import sqlite3 as _sqlite3
                try:
                    conn = _sqlite3.connect(ledger_db)
                    row = conn.execute(
                        """
                        SELECT verdict_id FROM trial_ledger
                        WHERE ticker1 = ? AND ticker2 = ?
                          AND is_closed = 0
                        ORDER BY ABS(JULIANDAY(verdict_timestamp) -
                                     JULIANDAY(?)) ASC
                        LIMIT 1
                        """,
                        (t["ticker1"], t["ticker2"], t["entry_date"]),
                    ).fetchone()
                    conn.close()
                    verdict_id = row[0] if row else None
                except Exception:
                    verdict_id = None

                if verdict_id:
                    ok = close_trial(
                        db_path=ledger_db,
                        verdict_id=verdict_id,
                        entry_timestamp=t["entry_date"],
                        entry_price_1=t["entry_price_1"],
                        entry_price_2=t["entry_price_2"],
                        exit_timestamp=t["exit_date"],
                        exit_price_1=t["exit_price_1"],
                        exit_price_2=t["exit_price_2"],
                        exit_z=t["exit_z"],
                        exit_reason=t["exit_reason"],
                        hedge_ratio=t["hedge_ratio"],
                        estimated_costs_bps=t["total_cost_bps"],
                        entry_z_actual=t.get("entry_z"),
                    )
                    if ok:
                        closed_ok += 1
                    else:
                        closed_fail += 1
                else:
                    closed_fail += 1

            if closed_ok or closed_fail:
                print(f"\n  Ledger: {closed_ok} trial(s) closed"
                      + (f"  ({closed_fail} unmatched)" if closed_fail else ""))
        except ImportError:
            pass  # trial_ledger not yet on path — silent

    print()


if __name__ == "__main__":
    main()
