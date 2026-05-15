#!/usr/bin/env python3
"""
ShiftInnerV — Batch Runner

Discovers all *.yaml files in the compositions/ directory and runs
main.py for each one sequentially. Designed for overnight runs.

Usage:
    python run_all.py                        # uses compositions/ in project root
    python run_all.py --compositions ~/path  # custom compositions directory
    python run_all.py --dry-run              # list files without running
"""

import os
import sys
import glob
import argparse
import subprocess
from datetime import datetime


def main():
    parser = argparse.ArgumentParser(description="ShiftInnerV Batch Runner")
    parser.add_argument(
        "--compositions",
        type=str,
        default=None,
        help="Path to compositions directory (default: ./compositions)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List composition files without running them"
    )
    args = parser.parse_args()

    # ── Find compositions directory ───────────────────────────────────────────
    if args.compositions:
        compositions_dir = os.path.expanduser(args.compositions)
    else:
        compositions_dir = os.path.join(os.path.dirname(__file__), "compositions")

    if not os.path.isdir(compositions_dir):
        print(f"ERROR: Compositions directory not found: {compositions_dir}")
        print(f"Create it and add yaml files: mkdir {compositions_dir}")
        sys.exit(1)

    # ── Discover yaml files ───────────────────────────────────────────────────
    yaml_files = sorted(glob.glob(os.path.join(compositions_dir, "*.yaml")))

    if not yaml_files:
        print(f"No yaml files found in: {compositions_dir}")
        sys.exit(0)

    print(f"ShiftInnerV Batch Runner — {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print(f"Compositions directory: {compositions_dir}")
    print(f"Found {len(yaml_files)} composition file(s):\n")
    for f in yaml_files:
        print(f"  {os.path.basename(f)}")
    print()

    if args.dry_run:
        print("Dry run — exiting without processing.")
        sys.exit(0)

    # ── Run each composition ──────────────────────────────────────────────────
    results = []
    main_py = os.path.join(os.path.dirname(__file__), "main.py")

    for i, yaml_file in enumerate(yaml_files, 1):
        label = os.path.basename(yaml_file)
        print(f"[{i}/{len(yaml_files)}] Running: {label}")
        print(f"  Started: {datetime.now().strftime('%H:%M:%S')}")

        start = datetime.now()
        try:
            result = subprocess.run(
                [sys.executable, main_py, "--pairs", yaml_file],
                check=False  # don't raise on non-zero exit — log and continue
            )
            elapsed = (datetime.now() - start).seconds
            status = "OK" if result.returncode == 0 else f"ERROR (exit {result.returncode})"
        except Exception as e:
            elapsed = (datetime.now() - start).seconds
            status = f"EXCEPTION: {e}"

        print(f"  Finished: {datetime.now().strftime('%H:%M:%S')} ({elapsed}s) — {status}\n")
        results.append((label, status, elapsed))

    # ── Summary ───────────────────────────────────────────────────────────────
    print("=" * 60)
    print("BATCH COMPLETE")
    print("=" * 60)
    total = sum(r[2] for r in results)
    for label, status, elapsed in results:
        print(f"  {status:30s} {elapsed:5d}s  {label}")
    print(f"\n  Total time: {total // 60}m {total % 60}s")
    print(f"  Completed: {datetime.now().strftime('%Y-%m-%d %H:%M')}")


if __name__ == "__main__":
    main()
