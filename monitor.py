#!/usr/bin/env python3
"""
ShiftInnerV — Layer 1 Monitor

Always-on lightweight watcher. No LLM, no agents, no Ollama.
Runs rolling correlation across all pairs in the compositions folder,
logs anomalies to SQLite, and optionally triggers the full crew.

Usage:
    python monitor.py                    # run once and exit
    python monitor.py --loop             # run continuously every 30 minutes
    python monitor.py --loop --interval 900   # every 15 minutes
    python monitor.py --summary          # print today's anomaly log and exit
    python monitor.py --screen pairs.yaml  # screen a yaml file, stats only
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
from data_manager import ensure_data

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
            crit_val     REAL,
            half_life    REAL,
            window       INTEGER,
            episodes     INTEGER,
            worst_corr   REAL,
            worst_dev    REAL,
            rating       TEXT
        )
    """)
    conn.commit()
    conn.close()


# ── Core math — no LLM, no agents ────────────────────────────────────────────

def load_csv(ticker: str) -> pd.Series:
    path = os.path.join(data_dir, f"{ticker.lower()}_daily.csv")
    if not os.path.exists(path):
        return None
    df = pd.read_csv(path, index_col=0)
    if "Close" not in df.columns:
        return None
    return df["Close"].dropna()


def compute_half_life(spread: pd.Series) -> float:
    """OLS regression of delta_spread on lagged_spread."""
    try:
        spread_lagged = spread.shift(1)
        delta_spread  = spread.diff()
        valid = pd.concat([delta_spread, spread_lagged], axis=1).dropna()
        valid.columns = ["delta", "lagged"]
        from statsmodels.tools import add_constant
        from statsmodels.regression.linear_model import OLS
        model = OLS(valid["delta"], add_constant(valid["lagged"])).fit()
        lam = model.params["lagged"]
        if lam >= 0:
            return None
        return -np.log(2) / lam
    except Exception:
        return None


def run_johansen(log_prices: pd.DataFrame):
    """Returns (is_cointegrated, trace_stat, crit_val_95)."""
    try:
        from statsmodels.tsa.vector_ar.vecm import coint_johansen
        result   = coint_johansen(log_prices, det_order=0, k_ar_diff=1)
        trace    = result.lr1[0]
        crit_val = result.cvt[0, 1]
        return trace > crit_val, trace, crit_val
    except Exception:
        return None, None, None


def analyze_pair(ticker1: str, ticker2: str, label: str = "") -> dict:
    """
    Full statistical analysis of a pair. Returns a results dict.
    No LLM involved.
    """
    s1 = load_csv(ticker1)
    s2 = load_csv(ticker2)

    if s1 is None or s2 is None:
        return {"error": f"Missing data for {ticker1} or {ticker2}"}

    shared = s1.index.intersection(s2.index)
    if len(shared) < 60:
        return {"error": f"Insufficient shared data ({len(shared)} rows)"}

    c1 = s1.loc[shared]
    c2 = s2.loc[shared]

    # Log prices for Johansen and half-life
    log_p1 = np.log(c1)
    log_p2 = np.log(c2)
    log_prices = pd.DataFrame({ticker1: log_p1, ticker2: log_p2}).dropna()

    # Half-life
    spread = log_p1 - log_p2
    hl = compute_half_life(spread)
    if hl is None:
        window = 30
    else:
        window = int(np.clip(round(hl), 10, 120))

    # Johansen
    is_coint, trace_stat, crit_val = run_johansen(log_prices)

    # Rolling correlation
    corr = c1.rolling(window).corr(c2)
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
                ep_corrs = decoupled.loc[ep_labels]
                wc = ep_corrs.min()
                episodes.append({
                    "onset":    str(ep_start)[:10],
                    "duration": len(ep_labels),
                    "worst_corr": wc,
                    "worst_dev":  (wc - mean_corr) / std_corr
                })
                ep_start = curr; ep_labels = [curr]

        ep_corrs = decoupled.loc[ep_labels]
        wc = ep_corrs.min()
        episodes.append({
            "onset":    str(ep_start)[:10],
            "duration": len(ep_labels),
            "worst_corr": wc,
            "worst_dev":  (wc - mean_corr) / std_corr
        })

    worst = min(episodes, key=lambda e: e["worst_corr"]) if episodes else None

    # Simple rating for screening
    if is_coint and hl and hl < 60 and len(episodes) >= 2:
        rating = "Strong candidate"
    elif is_coint and hl and hl < 120:
        rating = "Moderate candidate"
    elif is_coint:
        rating = "Weak candidate"
    else:
        rating = "Not cointegrated"

    return {
        "ticker1":      ticker1,
        "ticker2":      ticker2,
        "label":        label,
        "date_range":   f"{shared[0]} to {shared[-1]}",
        "cointegrated": is_coint,
        "trace_stat":   trace_stat,
        "crit_val":     crit_val,
        "half_life":    hl,
        "window":       window,
        "mean_corr":    mean_corr,
        "std_corr":     std_corr,
        "threshold":    threshold,
        "episodes":     len(episodes),
        "worst":        worst,
        "current_corr": float(corr.iloc[-1]) if not corr.empty else None,
        "rating":       rating,
    }


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
        datetime.now().isoformat(),
        str(date.today()),
        result["ticker1"],
        result["ticker2"],
        result.get("label", ""),
        result.get("current_corr"),
        result.get("threshold"),
        worst.get("worst_dev"),
        result.get("half_life"),
        result.get("window"),
        "YES" if result.get("cointegrated") else "NO"
    ))
    conn.commit()
    conn.close()


def log_screening(result: dict):
    worst = result.get("worst") or {}
    conn  = sqlite3.connect(db_path)
    conn.execute("""
        INSERT INTO screening
        (timestamp, ticker1, ticker2, label, cointegrated, trace_stat,
         crit_val, half_life, window, episodes, worst_corr, worst_dev, rating)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        datetime.now().isoformat(),
        result["ticker1"],
        result["ticker2"],
        result.get("label", ""),
        "YES" if result.get("cointegrated") else "NO",
        result.get("trace_stat"),
        result.get("crit_val"),
        result.get("half_life"),
        result.get("window"),
        result.get("episodes", 0),
        worst.get("worst_corr"),
        worst.get("worst_dev"),
        result.get("rating", "")
    ))
    conn.commit()
    conn.close()


# ── Modes ─────────────────────────────────────────────────────────────────────

def run_monitor(compositions_dir: str, verbose: bool = True):
    """Single monitoring pass across all compositions."""
    yaml_files = sorted(glob.glob(os.path.join(compositions_dir, "*.yaml")))
    if not yaml_files:
        print(f"No yaml files in {compositions_dir}")
        return

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"\n[{now}] Monitor pass — {len(yaml_files)} composition file(s)")

    # ── Auto-fetch any missing tickers across all compositions ──────────────
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

    flagged = []
    for yaml_file in yaml_files:
        with open(yaml_file) as f:
            comp = yaml.safe_load(f)
        for pair in comp.get("pairs", []):
            t1 = pair["ticker1"]; t2 = pair["ticker2"]
            label = pair.get("label", f"{t1}/{t2}")
            result = analyze_pair(t1, t2, label)

            if "error" in result:
                if verbose:
                    print(f"  SKIP {t1}/{t2}: {result['error']}")
                continue

            curr = result.get("current_corr")
            thresh = result.get("threshold")

            # Flag if current correlation is below threshold
            is_anomaly = curr is not None and thresh is not None and curr < thresh

            status = "🚨 ANOMALY" if is_anomaly else "  OK     "
            if verbose or is_anomaly:
                hl_str = f"{result['half_life']:.0f}d" if result['half_life'] else "N/A"
                coint  = "Y" if result['cointegrated'] else "N"
                print(f"  {status} {t1}/{t2:6s} | corr={curr:.3f} thresh={thresh:.3f} "
                      f"| hl={hl_str} coint={coint} | {label[:40]}")

            if is_anomaly:
                log_anomaly(result)
                flagged.append(result)

    print(f"\n  Flagged: {len(flagged)} anomaly(ies)")
    return flagged


def run_screening(yaml_path: str):
    """Screen a yaml file — stats only, no LLM. Writes to screening table."""
    with open(yaml_path) as f:
        comp = yaml.safe_load(f)

    pairs = comp.get("pairs", [])

    # ── Auto-fetch any missing tickers ───────────────────────────────────────
    tickers_needed = list(set(
        t for p in pairs for t in [p["ticker1"], p["ticker2"]]
        if not os.path.exists(os.path.join(data_dir, f"{t.lower()}_daily.csv"))
    ))
    if tickers_needed:
        print(f"  Fetching {len(tickers_needed)} missing ticker(s): "
              f"{', '.join(sorted(tickers_needed))}")
        ensure_data(tickers_needed, data_dir)
        print()

    print(f"\nScreening {len(pairs)} pair(s) from {os.path.basename(yaml_path)}")
    print(f"{'Ticker':<12} {'Coint':>5} {'HalfLife':>9} {'Window':>7} "
          f"{'Episodes':>9} {'Rating'}")
    print("-" * 70)

    results = []
    for pair in pairs:
        t1 = pair["ticker1"]; t2 = pair["ticker2"]
        label = pair.get("label", f"{t1}/{t2}")
        result = analyze_pair(t1, t2, label)

        if "error" in result:
            print(f"{t1}/{t2:<10} ERROR: {result['error']}")
            continue

        coint  = "YES" if result["cointegrated"] else "NO"
        hl     = f"{result['half_life']:.1f}" if result["half_life"] else "N/A"
        window = result["window"]
        eps    = result["episodes"]
        rating = result["rating"]

        print(f"{t1}/{t2:<10} {coint:>5} {hl:>9} {window:>7} {eps:>9}    {rating}")
        log_screening(result)
        results.append(result)

    # Summary
    cointed = [r for r in results if r.get("cointegrated")]
    strong  = [r for r in results if "Strong" in r.get("rating", "")]
    print(f"\nSummary: {len(results)} pairs | {len(cointed)} cointegrated "
          f"| {len(strong)} strong candidates")

    if strong:
        print("\nStrong candidates:")
        for r in strong:
            print(f"  {r['ticker1']}/{r['ticker2']} — hl={r['half_life']:.0f}d "
                  f"episodes={r['episodes']}")


def print_summary():
    """Print today's anomaly log."""
    conn = sqlite3.connect(db_path)
    today = str(date.today())
    rows = conn.execute("""
        SELECT timestamp, ticker1, ticker2, label, corr_value,
               threshold, deviation, cointegrated
        FROM anomalies
        WHERE date = ?
        ORDER BY timestamp DESC
    """, (today,)).fetchall()
    conn.close()

    print(f"\nAnomaly log for {today} — {len(rows)} event(s)\n")
    if not rows:
        print("  No anomalies logged today.")
        return

    for r in rows:
        ts, t1, t2, label, corr, thresh, dev, coint = r
        time_str = ts[11:16]
        dev_str  = f"{dev:.1f}σ" if dev else "N/A"
        print(f"  {time_str}  {t1}/{t2:<8} corr={corr:.3f} "
              f"thresh={thresh:.3f} dev={dev_str} coint={coint}")
        if label:
            print(f"           {label}")


# ── Entry point ───────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="ShiftInnerV Layer 1 Monitor")
    parser.add_argument("--loop",         action="store_true",
                        help="Run continuously")
    parser.add_argument("--interval",     type=int, default=1800,
                        help="Loop interval in seconds (default: 1800)")
    parser.add_argument("--summary",      action="store_true",
                        help="Print today's anomaly log and exit")
    parser.add_argument("--screen",       type=str, default=None,
                        help="Screen a yaml file (stats only, no LLM)")
    parser.add_argument("--compositions", type=str, default=None,
                        help="Compositions directory (default: ./compositions)")
    parser.add_argument("--quiet",        action="store_true",
                        help="Only print anomalies, not OK pairs")
    args = parser.parse_args()

    init_db()

    if args.summary:
        print_summary()
        return

    if args.screen:
        run_screening(os.path.expanduser(args.screen))
        return

    compositions_dir = os.path.expanduser(
        args.compositions or
        os.path.join(os.path.dirname(__file__), "compositions")
    )

    if args.loop:
        print(f"ShiftInnerV Monitor — loop every {args.interval}s")
        print(f"Anomaly log: {db_path}")
        while True:
            run_monitor(compositions_dir, verbose=not args.quiet)
            print(f"  Next run in {args.interval // 60}m — "
                  f"{datetime.now().strftime('%H:%M')}")
            time.sleep(args.interval)
    else:
        run_monitor(compositions_dir, verbose=not args.quiet)


if __name__ == "__main__":
    main()
