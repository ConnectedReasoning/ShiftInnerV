#!/usr/bin/env python3
"""
ShiftInnerV — Pair Generator

Generates composition yaml files from universe.yaml for screening.

Modes:
  --random N        Random N pairs from the full universe
  --category CAT    All pairs within a single category
  --cross CAT1 CAT2 Cross-category pairs (CAT1 tickers vs CAT2 tickers)
  --all             All possible pairs (warning: can be very large)

Usage:
    python generate_pairs.py --random 50
    python generate_pairs.py --random 100 --output compositions/tier3_random.yaml
    python generate_pairs.py --category semiconductors
    python generate_pairs.py --cross miners semiconductors
    python generate_pairs.py --cross energy currencies
    python generate_pairs.py --list-categories
"""

import os
import sys
import yaml
import random
import argparse
import itertools
from datetime import datetime
from pathlib import Path

# Project root — two levels up from shiftinnerv/pipelines/
_PROJECT_ROOT = str(Path(__file__).resolve().parent.parent.parent)


def load_universe(universe_path: str) -> dict:
    with open(universe_path) as f:
        data = yaml.safe_load(f)
    return data["universe"]


def make_pair_block(t1: str, t2: str, label: str = "",
                    lookback_years: int = 3) -> dict:
    return {
        "ticker1": t1,
        "ticker2": t2,
        "label": label or f"{t1} vs {t2}",
        "lookback_years": lookback_years,
        "cointegrated": "unknown",
    }


def write_composition(pairs: list, output_path: str, title: str = ""):
    os.makedirs(os.path.dirname(output_path) if os.path.dirname(output_path) else ".", exist_ok=True)

    header = f"""# ShiftInnerV — Generated Composition
# {title}
# Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}
# Pairs: {len(pairs)}
#
# SCREENING FILE — run through monitor.py --screen before promoting to production
# python monitor.py --screen {os.path.basename(output_path)}

"""
    with open(output_path, "w") as f:
        f.write(header)
        yaml.dump({"pairs": pairs}, f,
                  default_flow_style=False,
                  allow_unicode=True,
                  sort_keys=False)

    print(f"Written: {output_path} ({len(pairs)} pairs)")


def main():
    parser = argparse.ArgumentParser(description="ShiftInnerV Pair Generator")
    parser.add_argument("--universe",  type=str, default=None,
                        help="Path to universe.yaml (default: ./universe.yaml)")
    parser.add_argument("--output",    type=str, default=None,
                        help="Output yaml path (default: auto-named in compositions/)")
    parser.add_argument("--random",    type=int, default=None,
                        help="Generate N random pairs from full universe")
    parser.add_argument("--category",  type=str, default=None,
                        help="All pairs within a category")
    parser.add_argument("--cross",     type=str, nargs=2, default=None,
                        metavar=("CAT1", "CAT2"),
                        help="Cross-category pairs: all CAT1 vs all CAT2")
    parser.add_argument("--all",       action="store_true",
                        help="All possible pairs from universe (large)")
    parser.add_argument("--lookback",  type=int, default=3, choices=[1, 3, 5],
                        help="Lookback years for all pairs (default: 3)")
    parser.add_argument("--workers",   type=int, default=1,
                        help="Parallel workers for immediate screen (default: 1)")
    parser.add_argument("--seed",      type=int, default=None,
                        help="Random seed for reproducible pair selection (default: None)")
    parser.add_argument("--list-categories", action="store_true",
                        help="List available categories and exit")
    args = parser.parse_args()

    # ── Load universe ─────────────────────────────────────────────────────────
    universe_path = args.universe or os.path.join(_PROJECT_ROOT, "universe.yaml")
    if not os.path.exists(universe_path):
        print(f"ERROR: universe.yaml not found at {universe_path}")
        sys.exit(1)

    universe = load_universe(universe_path)

    if args.list_categories:
        print(f"\nAvailable categories ({len(universe)}):\n")
        for cat, tickers in universe.items():
            print(f"  {cat:<25} {len(tickers):3d} tickers  "
                  f"{', '.join(tickers[:5])}{'...' if len(tickers) > 5 else ''}")
        total = sum(len(v) for v in universe.values())
        total_pairs = total * (total - 1) // 2
        print(f"\nTotal tickers: {total}")
        print(f"Total possible pairs: {total_pairs:,}")
        return

    compositions_dir = os.path.join(_PROJECT_ROOT, "compositions")

    # ── Generate pairs ────────────────────────────────────────────────────────
    if args.random is not None:
        if args.seed is not None:
            random.seed(args.seed)

        # Flatten all tickers
        all_tickers = []
        for tickers in universe.values():
            all_tickers.extend(tickers)
        all_tickers = list(set(all_tickers))  # deduplicate

        # All possible pairs
        all_pairs_raw = list(itertools.combinations(sorted(all_tickers), 2))

        if args.random > len(all_pairs_raw):
            print(f"WARNING: Requested {args.random} pairs but only "
                  f"{len(all_pairs_raw)} possible. Using all.")
            sample = all_pairs_raw
        else:
            sample = random.sample(all_pairs_raw, args.random)

        pairs = [make_pair_block(t1, t2, lookback_years=args.lookback)
                 for t1, t2 in sample]
        title = f"Random sample — {len(pairs)} pairs from universe"
        output = args.output or os.path.join(
            compositions_dir,
            f"tier3_random_{len(pairs)}_{datetime.now().strftime('%Y%m%d')}.yaml"
        )

    elif args.category:
        cat = args.category.lower()
        if cat not in universe:
            print(f"ERROR: Category '{cat}' not found.")
            print(f"Available: {', '.join(universe.keys())}")
            sys.exit(1)

        tickers = universe[cat]
        raw = list(itertools.combinations(tickers, 2))
        pairs = [make_pair_block(t1, t2, f"{cat} pair: {t1} vs {t2}",
                                 lookback_years=args.lookback)
                 for t1, t2 in raw]
        title = f"Category: {cat} — {len(pairs)} pairs"
        output = args.output or os.path.join(
            compositions_dir,
            f"tier2_{cat}_{datetime.now().strftime('%Y%m%d')}.yaml"
        )

    elif args.cross:
        cat1, cat2 = [c.lower() for c in args.cross]
        for cat in [cat1, cat2]:
            if cat not in universe:
                print(f"ERROR: Category '{cat}' not found.")
                print(f"Available: {', '.join(universe.keys())}")
                sys.exit(1)

        t1_list = universe[cat1]
        t2_list = universe[cat2]
        raw = list(itertools.product(t1_list, t2_list))
        # Remove same-ticker pairs
        raw = [(a, b) for a, b in raw if a != b]
        pairs = [make_pair_block(t1, t2, f"{cat1}/{cat2}: {t1} vs {t2}",
                                 lookback_years=args.lookback)
                 for t1, t2 in raw]
        title = f"Cross: {cat1} x {cat2} — {len(pairs)} pairs"
        output = args.output or os.path.join(
            compositions_dir,
            f"tier2_cross_{cat1}_{cat2}_{datetime.now().strftime('%Y%m%d')}.yaml"
        )

    elif args.all:
        all_tickers = list(set(t for tickers in universe.values() for t in tickers))
        raw = list(itertools.combinations(sorted(all_tickers), 2))
        pairs = [make_pair_block(t1, t2, lookback_years=args.lookback)
                 for t1, t2 in raw]
        title = f"Full universe — {len(pairs)} pairs"
        output = args.output or os.path.join(
            compositions_dir,
            f"tier3_all_{datetime.now().strftime('%Y%m%d')}.yaml"
        )
        print(f"WARNING: Generating {len(pairs):,} pairs. "
              f"This will take hours to screen.")

    else:
        parser.print_help()
        print("\nExample: python generate_pairs.py --random 50")
        sys.exit(0)

    write_composition(pairs, output, title)
    print(f"\nNext step — screen statistically before running full crew:")
    print(f"  python monitor.py --screen {output} --workers 8 --filter active")


if __name__ == "__main__":
    main()
