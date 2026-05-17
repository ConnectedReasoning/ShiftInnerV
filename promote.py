#!/usr/bin/env python3
"""
ShiftInnerV — Promote

Reads the screening table from anomalies.db, applies quality filters,
deduplicates, and writes a focused composition yaml ready for main.py.

Called automatically from main.py after each screening run, or manually:

    python promote.py                          # use defaults, write to compositions/
    python promote.py --top 20                 # top 20 pairs only
    python promote.py --min-snr 2.0            # stricter SNR floor
    python promote.py --max-hl 60              # tighter half-life cap
    python promote.py --output my_focus.yaml   # custom output path
    python promote.py --dry-run                # print candidates without writing

Filters applied (all must pass):
    cointegrated  : 95% CI required
    half_life     : <= MAX_HALF_LIFE days (default 120, tradeable horizon)
    half_life     : >= MIN_HALF_LIFE days (default 5, avoid noise pairs)
    snr           : >= MIN_SNR (default 1.5, signal dominates drift)
    snr           : <= SNR_CAP (default 500, exclude runaway/flat-spread artifacts)
    episodes      : >= MIN_EPISODES (default 2, pattern must have recurred)
    deduplication : keep highest-SNR row per ticker pair
    recency       : only rows from last LOOKBACK_DAYS days (default 7)
"""

import os
import sys
import sqlite3
import yaml
import argparse
from datetime import datetime, timedelta

# ── Defaults ──────────────────────────────────────────────────────────────────
DB_PATH          = os.path.expanduser(
    os.getenv("DATA_STORAGE_PATH", "~/Projects/ShiftInnerV_Data") + "/anomalies.db"
)
COMPOSITIONS_DIR = os.path.join(os.path.dirname(__file__), "compositions")

MAX_HALF_LIFE    = 120    # days — Gate 2 from Signal Mathematician
MIN_HALF_LIFE    = 5      # days — below this is noise / data artifact
MIN_SNR          = 1.5    # slightly above the agent's gate of 1.0 for quality
SNR_CAP          = 500    # exclude runaway SNR values (near-flat spread artifacts)
MIN_EPISODES     = 2      # Gate 4 from Signal Mathematician
LOOKBACK_DAYS    = 7      # only consider screening rows from last N days
DEFAULT_TOP_N    = 25     # max pairs in output composition


# ── Load and filter ───────────────────────────────────────────────────────────

def load_candidates(
    db_path: str,
    max_hl: float,
    min_hl: float,
    min_snr: float,
    snr_cap: float,
    min_episodes: int,
    lookback_days: int,
) -> list[dict]:
    """
    Query screening table, apply filters, deduplicate, return sorted list.
    """
    if not os.path.exists(db_path):
        print(f"ERROR: DB not found at {db_path}")
        print("  Run monitor.py --screen <composition.yaml> first.")
        sys.exit(1)

    conn  = sqlite3.connect(db_path)
    cols  = [d[1] for d in conn.execute("PRAGMA table_info(screening)").fetchall()]
    cutoff = (datetime.now() - timedelta(days=lookback_days)).isoformat()

    rows = conn.execute(f"""
        SELECT {', '.join(cols)}
        FROM screening
        WHERE cointegrated IN ('95%', '99%')
        AND half_life IS NOT NULL
        AND half_life >= ?
        AND half_life <= ?
        AND snr >= ?
        AND snr <= ?
        AND episodes >= ?
        AND timestamp >= ?
        ORDER BY snr DESC
    """, (min_hl, max_hl, min_snr, snr_cap, min_episodes, cutoff)).fetchall()

    conn.close()

    candidates = [dict(zip(cols, r)) for r in rows]

    # ── Deduplicate: keep best SNR per pair (order-independent) ──────────────
    seen   = {}
    unique = []
    for c in candidates:
        key = tuple(sorted([c["ticker1"], c["ticker2"]]))
        if key not in seen:
            seen[key] = c
            unique.append(c)
        else:
            if c["snr"] > seen[key]["snr"]:
                # Replace with better row
                old_idx = next(i for i, u in enumerate(unique)
                               if tuple(sorted([u["ticker1"], u["ticker2"]])) == key)
                unique[old_idx] = c
                seen[key] = c

    return unique


# ── Score and rank ────────────────────────────────────────────────────────────

def composite_score(c: dict) -> float:
    """
    Rank by a composite of SNR and half-life efficiency.
    Shorter half-life = faster reversion = better.
    SNR capped at 50 to prevent extreme values dominating.
    """
    snr    = min(c["snr"], 50.0)
    hl     = c["half_life"]
    # Normalise half-life: 5d -> 1.0, 120d -> 0.04
    hl_score = 5.0 / hl if hl > 0 else 0
    return snr * hl_score


# ── Build composition ─────────────────────────────────────────────────────────

MAX_PER_TICKER = 3   # max appearances of any single ticker in the output


def build_composition(candidates: list[dict], top_n: int) -> list[dict]:
    ranked  = sorted(candidates, key=composite_score, reverse=True)
    pairs   = []
    ticker_count: dict[str, int] = {}

    for c in ranked:
        if len(pairs) >= top_n:
            break
        t1, t2 = c["ticker1"], c["ticker2"]
        # Enforce concentration cap — no ticker appears more than MAX_PER_TICKER times
        if (ticker_count.get(t1, 0) >= MAX_PER_TICKER or
                ticker_count.get(t2, 0) >= MAX_PER_TICKER):
            continue
        ticker_count[t1] = ticker_count.get(t1, 0) + 1
        ticker_count[t2] = ticker_count.get(t2, 0) + 1
        # Infer sensible lookback from half_life
        if c["half_life"] <= 30:
            lookback = 1
        elif c["half_life"] <= 60:
            lookback = 2
        else:
            lookback = 3

        pairs.append({
            "ticker1":      c["ticker1"],
            "ticker2":      c["ticker2"],
            "label":        c.get("label") or f"{c['ticker1']} vs {c['ticker2']}",
            "lookback_years": lookback,
            "cointegrated": c["cointegrated"],
            # Attach screening metadata as comments via notes field
            "notes": (
                f"Promoted by promote.py — "
                f"SNR={c['snr']:.1f} hl={c['half_life']:.0f}d "
                f"ep={c['episodes']} coint={c['cointegrated']}"
            ),
        })
    return pairs


# ── Write yaml ────────────────────────────────────────────────────────────────

def write_composition(pairs: list, output_path: str, filters: dict):
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    header = f"""# ShiftInnerV — Promoted Composition
# Auto-generated by promote.py — {now}
# Pairs: {len(pairs)}
#
# Filters applied:
#   cointegrated  : 95%+ CI
#   half_life     : {filters['min_hl']}–{filters['max_hl']} days
#   snr           : {filters['min_snr']}–{filters['snr_cap']} (capped)
#   episodes      : >= {filters['min_episodes']}
#   lookback      : last {filters['lookback_days']} days of screening data
#   ranked by     : SNR × (5 / half_life) composite score
#
# Ready for: python main.py --pairs {os.path.basename(output_path)}

"""
    with open(output_path, "w") as f:
        f.write(header)
        yaml.dump({"pairs": pairs}, f,
                  default_flow_style=False,
                  allow_unicode=True,
                  sort_keys=False)


# ── Print summary ─────────────────────────────────────────────────────────────

def print_summary(pairs: list):
    print(f"\n{'='*65}")
    print(f"  PROMOTED CANDIDATES — {len(pairs)} pair(s)")
    print(f"{'='*65}")
    print(f"  {'PAIR':<18} {'SNR':>7}  {'HL':>6}  {'EP':>4}  {'COINT':<6}  LABEL")
    print(f"  {'-'*62}")
    for p in pairs:
        notes = p.get("notes", "")
        snr   = float(notes.split("SNR=")[1].split(" ")[0]) if "SNR=" in notes else 0
        hl    = float(notes.split("hl=")[1].split("d")[0])  if "hl=" in notes else 0
        ep    = int(notes.split("ep=")[1].split(" ")[0])    if "ep=" in notes else 0
        coint = notes.split("coint=")[1] if "coint=" in notes else ""
        pair  = f"{p['ticker1']}/{p['ticker2']}"
        label = p["label"][:28]
        print(f"  {pair:<18} {snr:>7.1f}  {hl:>5.0f}d  {ep:>4}  {coint:<6}  {label}")
    print(f"{'='*65}\n")


# ── CLI ───────────────────────────────────────────────────────────────────────

def run(
    db_path: str      = None,
    output: str       = None,
    top_n: int        = DEFAULT_TOP_N,
    max_hl: float     = MAX_HALF_LIFE,
    min_hl: float     = MIN_HALF_LIFE,
    min_snr: float    = MIN_SNR,
    snr_cap: float    = SNR_CAP,
    min_episodes: int = MIN_EPISODES,
    lookback_days: int = LOOKBACK_DAYS,
    dry_run: bool     = False,
    quiet: bool       = False,
) -> str | None:
    """
    Main entry point — callable from main.py or CLI.
    Returns output path if file was written, None on dry-run or no candidates.
    """
    db = db_path or DB_PATH

    filters = dict(
        max_hl=max_hl, min_hl=min_hl, min_snr=min_snr,
        snr_cap=snr_cap, min_episodes=min_episodes,
        lookback_days=lookback_days,
    )

    candidates = load_candidates(db, **filters)

    if not candidates:
        if not quiet:
            print("  promote.py: no candidates passed filters.")
            print(f"  DB: {db}")
            print(f"  Filters: coint=95%+, hl={min_hl}–{max_hl}d, "
                  f"snr={min_snr}–{snr_cap}, ep>={min_episodes}, "
                  f"last {lookback_days}d")
        return None

    pairs = build_composition(candidates, top_n)

    if not quiet:
        print_summary(pairs)

    if dry_run:
        print("  Dry run — no file written.")
        return None

    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    out = output or os.path.join(
        COMPOSITIONS_DIR,
        f"promoted_{timestamp}.yaml"
    )

    write_composition(pairs, out, filters)

    if not quiet:
        print(f"  Written: {out}")
        print(f"  Next: python main.py --pairs {out}\n")

    return out


def main():
    parser = argparse.ArgumentParser(
        description="ShiftInnerV — Promote best screening candidates to a composition"
    )
    parser.add_argument("--db",          type=str,   default=None,
                        help=f"Path to anomalies.db (default: {DB_PATH})")
    parser.add_argument("--output",      type=str,   default=None,
                        help="Output yaml path (default: compositions/promoted_<timestamp>.yaml)")
    parser.add_argument("--top",         type=int,   default=DEFAULT_TOP_N,
                        help=f"Max pairs to include (default: {DEFAULT_TOP_N})")
    parser.add_argument("--max-hl",      type=float, default=MAX_HALF_LIFE,
                        help=f"Max half-life in days (default: {MAX_HALF_LIFE})")
    parser.add_argument("--min-hl",      type=float, default=MIN_HALF_LIFE,
                        help=f"Min half-life in days (default: {MIN_HALF_LIFE})")
    parser.add_argument("--min-snr",     type=float, default=MIN_SNR,
                        help=f"Min SNR (default: {MIN_SNR})")
    parser.add_argument("--snr-cap",     type=float, default=SNR_CAP,
                        help=f"Max SNR cap (default: {SNR_CAP})")
    parser.add_argument("--min-episodes",type=int,   default=MIN_EPISODES,
                        help=f"Min episode count (default: {MIN_EPISODES})")
    parser.add_argument("--lookback",    type=int,   default=LOOKBACK_DAYS,
                        help=f"Days of screening history to use (default: {LOOKBACK_DAYS})")
    parser.add_argument("--dry-run",     action="store_true",
                        help="Print candidates without writing yaml")
    parser.add_argument("--quiet",       action="store_true",
                        help="Suppress terminal output")
    args = parser.parse_args()

    run(
        db_path       = args.db,
        output        = args.output,
        top_n         = args.top,
        max_hl        = args.max_hl,
        min_hl        = args.min_hl,
        min_snr       = args.min_snr,
        snr_cap       = args.snr_cap,
        min_episodes  = args.min_episodes,
        lookback_days = args.lookback,
        dry_run       = args.dry_run,
        quiet         = args.quiet,
    )


if __name__ == "__main__":
    main()
