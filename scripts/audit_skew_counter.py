#!/usr/bin/env python3
"""
ShiftInnerV — Skew Counter Audit
================================

Resolves the "10 more days" frozen-counter mystery. Answers, definitively:

  1. Is skew_ledger actually accumulating rows each day? (persistence heartbeat)
  2. Are those rows producing non-null norm_skew, or just raw_skew?
     (a raw-OK / norm-NULL row is the signature of an SPY-normalisation gap)
  3. Did SPY itself produce a usable skew each day? (the single point of failure)
  4. How is history depth actually distributed across the universe?
     (one dead ticker vs. all of them — this is what pins the counter)

Run on Gandalf:
    python scripts/audit_skew_counter.py
    python scripts/audit_skew_counter.py --db ~/projects/github/ShiftInnerV/data/trial_ledger.db --days 14

Pure stdlib — no pandas. Read-only; never writes.
"""

import argparse
import os
import sqlite3
import sys
from collections import Counter

# sentinel.py writes to PROJECT_DIR/data/trial_ledger.db
_DEFAULT_DB = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "trial_ledger.db",
)

MIN_HISTORY   = 5    # SkewSignalGenerator: rows needed before a signal can fire
LOOKBACK_DAYS = 10   # rolling window for a full z-score baseline


def _connect(db_path):
    if not os.path.exists(db_path):
        sys.exit(f"✗ DB not found: {db_path}\n  Pass the right path with --db.")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _table_exists(conn, name):
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    ).fetchone()
    return row is not None


def section(title):
    print("\n" + "=" * 70)
    print(title)
    print("=" * 70)


def audit(db_path, days):
    print(f"DB: {db_path}")
    conn = _connect(db_path)

    if not _table_exists(conn, "skew_ledger"):
        sys.exit("✗ skew_ledger table does not exist. The monitor has never "
                 "written, or this is the wrong DB / a fresh reset that never ran.")

    # ── 1. Persistence heartbeat: rows per day ────────────────────────────────
    section("1. PERSISTENCE HEARTBEAT — rows written per snapshot_date")
    rows = conn.execute(
        """
        SELECT snapshot_date                                    AS d,
               COUNT(*)                                         AS total,
               SUM(norm_skew IS NOT NULL)                       AS norm_ok,
               SUM(raw_skew  IS NOT NULL)                       AS raw_ok,
               SUM(raw_skew  IS NOT NULL AND norm_skew IS NULL) AS raw_ok_norm_null,
               SUM(fetch_error IS NOT NULL)                     AS errors
        FROM   skew_ledger
        GROUP  BY snapshot_date
        ORDER  BY snapshot_date DESC
        LIMIT  ?
        """,
        (days,),
    ).fetchall()

    if not rows:
        sys.exit("✗ skew_ledger exists but is EMPTY. Persistence is broken — "
                 "the monitor is not writing. This is today's fix.")

    print(f"{'date':<12}{'total':>7}{'norm_ok':>9}{'raw_ok':>8}"
          f"{'spy_gap':>9}{'errors':>8}")
    print("-" * 70)
    for r in rows:
        print(f"{r['d']:<12}{r['total']:>7}{r['norm_ok']:>9}{r['raw_ok']:>8}"
              f"{r['raw_ok_norm_null']:>9}{r['errors']:>8}")
    print("\n  norm_ok  = rows with a usable norm_skew (these count toward history)")
    print("  spy_gap  = raw_skew computed but norm_skew NULL → SPY normalisation failed that day")

    # ── 2. SPY single point of failure ────────────────────────────────────────
    section("2. SPY BENCHMARK — did the normaliser work each day?")
    spy = conn.execute(
        """
        SELECT snapshot_date AS d, raw_skew, fetch_error
        FROM   skew_ledger
        WHERE  ticker = 'SPY'
        ORDER  BY snapshot_date DESC
        LIMIT  ?
        """,
        (days,),
    ).fetchall()
    if not spy:
        print("  ⚠ No SPY rows at all. norm_skew will be NULL for the ENTIRE universe.")
    else:
        print(f"{'date':<12}{'spy_raw_skew':>14}  fetch_error")
        print("-" * 70)
        for r in spy:
            rs = f"{r['raw_skew']:.4f}" if r['raw_skew'] is not None else "NULL"
            print(f"{r['d']:<12}{rs:>14}  {r['fetch_error'] or ''}")
        bad = [r['d'] for r in spy if r['raw_skew'] is None]
        if bad:
            print(f"\n  ⚠ SPY failed on {len(bad)} day(s): {', '.join(bad)}")
            print("    Every ticker's norm_skew is NULL on those days. This alone can")
            print("    starve the whole universe of history.")
        else:
            print("\n  ✓ SPY produced a usable skew on every day shown.")

    # ── 3. History-depth distribution (what the counter sees) ─────────────────
    section("3. HISTORY DEPTH — non-null norm_skew rows per ticker")
    depths = conn.execute(
        """
        SELECT ticker, SUM(norm_skew IS NOT NULL) AS depth
        FROM   skew_ledger
        WHERE  ticker != 'SPY'
        GROUP  BY ticker
        """
    ).fetchall()

    if not depths:
        sys.exit("✗ No non-SPY tickers in skew_ledger.")

    depth_vals = [r["depth"] for r in depths]
    hist = Counter(depth_vals)
    n = len(depth_vals)
    at_zero  = hist.get(0, 0)
    live     = [d for d in depth_vals if d >= 1]
    live_med = sorted(live)[len(live) // 2] if live else 0
    universe_min = min(depth_vals)
    universe_max = max(depth_vals)

    print(f"  Universe size (ex-SPY): {n} tickers\n")
    print("  depth  tickers")
    print("  -----  -------")
    for d in sorted(hist):
        bar = "#" * min(50, hist[d])
        print(f"  {d:>5}  {hist[d]:>5}  {bar}")

    print(f"\n  min depth across universe : {universe_min}  ← what the briefing's "
          f"min() uses")
    print(f"  max depth across universe : {universe_max}")
    print(f"  tickers at depth 0 (dead) : {at_zero}")
    print(f"  median depth of LIVE names: {live_med}  ← honest progress measure")

    # ── 4. Verdict ────────────────────────────────────────────────────────────
    section("4. VERDICT")

    days_present = len(rows)
    bulk_alive = len(live) >= 0.5 * n  # at least half the universe has ≥1 row

    if universe_max == 0:
        print("  ✗ BROKEN PERSISTENCE.")
        print("    No ticker has a single usable norm_skew row. skew_ledger has rows")
        print("    but norm_skew is NULL everywhere — almost certainly SPY failing")
        print("    every day (see section 2) or the normalisation step never running.")
        print("    → Fix the data path. The counter is the least of your problems.")
    elif bulk_alive and at_zero > 0:
        print("  ✓ COSMETIC COUNTER BUG (data is healthy).")
        print(f"    {len(live)}/{n} tickers are accumulating history (median {live_med}d).")
        print(f"    The counter froze because {at_zero} dead ticker(s) hold min()=0.")
        print(f"    Real progress: median live ticker needs ~{max(0, MIN_HISTORY - live_med)} "
              f"more day(s) to first signal, ~{max(0, LOOKBACK_DAYS - live_med)} to full baseline.")
        print("    → Apply the briefing_generator fix; investigate the dead tickers")
        print("      separately (likely no options chain — fine to drop from universe).")
    elif not bulk_alive:
        print("  ⚠ PARTIAL DATA — most of the universe is not accumulating.")
        print(f"    Only {len(live)}/{n} tickers have any history. Check section 2 for")
        print("    SPY gaps and section 1 for missing days before trusting signals.")
    else:
        print("  ✓ Data looks healthy and no dead tickers — counter should already move.")
        print("    If it still reads frozen, you're on stale briefings; re-run sentinel.")

    print(f"\n  Days of data present (last {days} checked): {days_present}")
    conn.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Audit the skew warm-up counter.")
    ap.add_argument("--db", default=_DEFAULT_DB, help=f"Path to trial_ledger.db (default: {_DEFAULT_DB})")
    ap.add_argument("--days", type=int, default=14, help="How many recent days to inspect (default: 14)")
    args = ap.parse_args()
    audit(args.db, args.days)
