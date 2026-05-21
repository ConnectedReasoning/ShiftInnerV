#!/usr/bin/env python3
"""
ShiftInnerV — Lookback Window Sensitivity Analysis
Item 9 of the Council Roadmap.

Runs analyze_pair() at four lookback windows (0.5y, 1y, 2y, 3y) for every pair
in all active compositions. Classifies each pair as ROBUST or FRAGILE based on
stability of cointegration, half-life, and SNR across windows.

Output:
    lookback_sensitivity_report.md   — full results table + robustness narrative
    lookback_sensitivity.csv         — machine-readable results for further analysis

Usage:
    python scripts/lookback_sensitivity.py
    python scripts/lookback_sensitivity.py --compositions compositions/
    python scripts/lookback_sensitivity.py --workers 4
    python scripts/lookback_sensitivity.py --output reports/lookback.md
"""

import os
import sys
import csv
import argparse
from pathlib import Path
from datetime import datetime
from concurrent.futures import ProcessPoolExecutor, as_completed

import yaml
import numpy as np
import pandas as pd

PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from shiftinnerv.pipelines.monitor import analyze_pair


# ── Configuration ─────────────────────────────────────────────────────────────

WINDOWS = [0.5, 1, 2, 3]   # years to test (0.5 = 6 months)
                             # Note: 0.5y gives ~125 trading days — barely enough
                             # for 250-day training window; expect many failures.
                             # That IS the data point.

ROBUSTNESS_CRITERIA = {
    "min_coint_windows":    3,   # must be cointegrated_95 in at least 3 of 4 windows
    "max_half_life_cv":   0.5,   # coefficient of variation of half_life across windows
    "max_half_life_days": 120,   # must be tradeable in ALL windows
    "min_snr":            1.0,   # must pass Gate 3 in ALL windows
}


# ── Core classification ────────────────────────────────────────────────────────

def classify_pair_robustness(results_by_window: dict) -> dict:
    """
    Given analyze_pair() results at each window, classify as ROBUST or FRAGILE.

    Parameters
    ----------
    results_by_window : dict
        {window: analyze_pair() result dict}

    Returns
    -------
    dict with:
        classification: "ROBUST" | "FRAGILE" | "INSUFFICIENT_DATA"
        reasons: list of str (why fragile, if applicable)
        summary: concise description
        metrics: half_life mean/cv, snr mean, coint rate
    """
    valid = {
        w: r for w, r in results_by_window.items()
        if r is not None and "error" not in r
    }

    if len(valid) < 2:
        return {
            "classification": "INSUFFICIENT_DATA",
            "reasons": ["Fewer than 2 windows completed successfully"],
            "summary": "Cannot classify — too few data points",
            "metrics": {},
        }

    reasons = []

    # Cointegration stability (Gate 1)
    coint_passes = sum(
        1 for r in valid.values() if r.get("cointegrated_95", False)
    )
    coint_rate = coint_passes / len(valid)
    if coint_passes < ROBUSTNESS_CRITERIA["min_coint_windows"]:
        reasons.append(
            f"Gate 1 unstable: cointegrated_95 in only {coint_passes}/{len(valid)} windows "
            f"(need {ROBUSTNESS_CRITERIA['min_coint_windows']})"
        )

    # Half-life magnitude stability (Gate 2)
    half_lives = [r["half_life"] for r in valid.values()
                  if r.get("half_life") is not None and not np.isnan(r["half_life"])]
    hl_mean = np.mean(half_lives) if half_lives else None
    hl_cv = np.std(half_lives) / hl_mean if half_lives and hl_mean > 0 else None

    if hl_cv is not None and hl_cv > ROBUSTNESS_CRITERIA["max_half_life_cv"]:
        reasons.append(
            f"Half-life unstable: CV={hl_cv:.3f} > "
            f"{ROBUSTNESS_CRITERIA['max_half_life_cv']} threshold "
            f"(values: {[round(h, 1) for h in half_lives]}d)"
        )

    # Half-life stays tradeable in all windows (> 1d, < 120d)
    for w, r in valid.items():
        hl = r.get("half_life")
        if hl is not None and not np.isnan(hl):
            if hl > ROBUSTNESS_CRITERIA["max_half_life_days"]:
                reasons.append(
                    f"Gate 2 fails at {w}y window: "
                    f"half_life {hl:.1f}d > {ROBUSTNESS_CRITERIA['max_half_life_days']}d ceiling"
                )
                break
            if hl < 1:
                reasons.append(
                    f"Gate 2 fails at {w}y window: half_life {hl:.2f}d < 1d floor"
                )
                break

    # SNR stability (Gate 3)
    snrs = [r["snr"] for r in valid.values()
            if r.get("snr") is not None and not np.isnan(r["snr"])]
    snr_mean = np.mean(snrs) if snrs else None

    for w, r in valid.items():
        snr = r.get("snr")
        if snr is not None and snr < ROBUSTNESS_CRITERIA["min_snr"]:
            reasons.append(
                f"Gate 3 fails at {w}y window: "
                f"SNR {snr:.3f} < {ROBUSTNESS_CRITERIA['min_snr']} threshold"
            )
            break

    classification = "ROBUST" if not reasons else "FRAGILE"

    return {
        "classification": classification,
        "reasons": reasons,
        "summary": (
            f"ROBUST: stable across {len(valid)} windows" if classification == "ROBUST"
            else f"FRAGILE: {reasons[0]}"
        ),
        "metrics": {
            "coint_rate": round(coint_rate, 2),
            "hl_mean": round(hl_mean, 1) if hl_mean else None,
            "hl_cv": round(hl_cv, 3) if hl_cv else None,
            "snr_mean": round(snr_mean, 3) if snr_mean else None,
            "windows_tested": len(valid),
        },
    }


# ── Per-pair worker ───────────────────────────────────────────────────────────

def analyze_pair_all_windows(
    ticker1: str,
    ticker2: str,
    label: str,
    windows: list = WINDOWS,
) -> dict:
    """
    Run analyze_pair at each lookback window and classify robustness.

    Returns dict with:
        pair: (ticker1, ticker2, label)
        results_by_window: {window: analyze_pair() result}
        robustness: classify_pair_robustness() output
    """
    results_by_window = {}

    for window in windows:
        try:
            result = analyze_pair(ticker1, ticker2, label, lookback_years=window)
            results_by_window[window] = result
        except Exception as e:
            results_by_window[window] = {"error": str(e)}

    robustness = classify_pair_robustness(results_by_window)

    return {
        "ticker1":          ticker1,
        "ticker2":          ticker2,
        "label":            label,
        "results":          results_by_window,
        "robustness":       robustness,
    }


def _worker(args):
    ticker1, ticker2, label, windows = args
    return analyze_pair_all_windows(ticker1, ticker2, label, windows)


# ── Load compositions ─────────────────────────────────────────────────────────

def load_all_pairs(compositions_dir: str) -> list:
    """
    Load all pairs from all composition_*.yaml files.

    Returns list of (ticker1, ticker2, label, composition_name)
    """
    pairs = []

    for yaml_file in sorted(Path(compositions_dir).glob("composition_*.yaml")):
        comp_name = yaml_file.stem  # e.g., composition_b_defense

        with open(yaml_file) as f:
            data = yaml.safe_load(f)

        for pair_dict in data.get("pairs", []):
            pairs.append((
                pair_dict["ticker1"],
                pair_dict["ticker2"],
                pair_dict.get("label", f"{pair_dict['ticker1']}/{pair_dict['ticker2']}"),
                comp_name,
            ))

    return pairs


# ── Report generation ─────────────────────────────────────────────────────────

def generate_report(all_results: list, output_path: str) -> None:
    """Write full sensitivity report as markdown."""
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    lines = []

    lines += [
        "# ShiftInnerV — Lookback Window Sensitivity Report",
        f"*Generated {now}*",
        "",
        "## Purpose",
        "",
        "This report classifies each pair as ROBUST or FRAGILE based on the",
        "stability of its cointegration, half-life, and SNR across four lookback",
        "windows: 6 months, 1 year, 2 years, and 3 years.",
        "",
        "**ROBUST pairs** are stable across all windows — a genuine structural",
        "relationship exists regardless of the observation window chosen.",
        "",
        "**FRAGILE pairs** show metrics that flip or vary wildly — window-sensitive",
        "artifacts. May not generalize out-of-sample.",
        "",
        "---",
        "## Summary",
        "",
    ]

    robust = [r for r in all_results
              if r["robustness"]["classification"] == "ROBUST"]
    fragile = [r for r in all_results
               if r["robustness"]["classification"] == "FRAGILE"]
    insufficient = [r for r in all_results
                    if r["robustness"]["classification"] == "INSUFFICIENT_DATA"]

    total = len(all_results)
    lines += [
        f"| Classification | Count | % |",
        f"|---|---|---|",
        f"| ROBUST | {len(robust)} | {len(robust)/total*100:.0f}% |",
        f"| FRAGILE | {len(fragile)} | {len(fragile)/total*100:.0f}% |",
        f"| INSUFFICIENT_DATA | {len(insufficient)} | {len(insufficient)/total*100:.0f}% |",
        f"| **Total** | **{total}** | |",
        "",
        "### Recommendation",
        "",
        "Prefer **ROBUST pairs** for live trading. Their statistical edge is",
        "genuine — stable regardless of how far back you look.",
        "",
        "**FRAGILE pairs** require manual review before live entry. Their edge",
        "may be real but is window-dependent. Consider excluding from ACTIVE",
        "verdicts until a structural explanation is found.",
        "",
        "---",
        "## Results by Pair",
        "",
        "| Pair | Composition | Robust? | Coint Rate | HL Mean | HL CV | "
        "SNR Mean | Reasons |",
        "|---|---|---|---|---|---|---|---|",
    ]

    for r in all_results:
        m = r["robustness"]["metrics"]
        cl = r["robustness"]["classification"]
        icon = "✅" if cl == "ROBUST" else "⚠️" if cl == "FRAGILE" else "?"
        reasons = "; ".join(r["robustness"]["reasons"][:1])  # first reason only

        lines.append(
            f"| {r['ticker1']}/{r['ticker2']} | {r.get('composition', '')} | "
            f"{icon} {cl} | "
            f"{m.get('coint_rate', '—')} | "
            f"{m.get('hl_mean', '—')} | "
            f"{m.get('hl_cv', '—')} | "
            f"{m.get('snr_mean', '—')} | "
            f"{reasons} |"
        )

    # Detailed section for FRAGILE pairs
    if fragile:
        lines += [
            "",
            "---",
            "## Fragile Pair Details",
            "",
        ]
        for r in fragile:
            lines += [f"### {r['ticker1']}/{r['ticker2']} — {r.get('composition', '')}"]
            for reason in r["robustness"]["reasons"]:
                lines.append(f"- {reason}")
            lines.append("")
            lines += [
                "| Window | Coint_95 | Half-life | SNR | Rating |",
                "|---|---|---|---|---|",
            ]
            for w in WINDOWS:
                res = r["results"].get(w, {})
                if "error" in res:
                    lines.append(f"| {w}y | ERROR: {res['error']} | | | |")
                else:
                    coint = "✓" if res.get("cointegrated_95") else "✗"
                    hl = f"{res.get('half_life', '—'):.1f}d" if res.get("half_life") else "—"
                    snr = f"{res.get('snr', '—'):.3f}" if res.get("snr") else "—"
                    rating = res.get("rating", "—")
                    lines.append(f"| {w}y | {coint} | {hl} | {snr} | {rating} |")
            lines.append("")

    # Methodology
    lines += [
        "---",
        "## Robustness Criteria",
        "",
        f"A pair is **ROBUST** if all four criteria are met:",
        f"1. cointegrated_95 = True in >= {ROBUSTNESS_CRITERIA['min_coint_windows']}/4 windows",
        f"2. Half-life coefficient of variation (CV) <= {ROBUSTNESS_CRITERIA['max_half_life_cv']}",
        f"3. Half-life stays within [1d, {ROBUSTNESS_CRITERIA['max_half_life_days']}d] in all windows",
        f"4. SNR >= {ROBUSTNESS_CRITERIA['min_snr']} in all windows",
        "",
        f"A pair is **FRAGILE** if any criterion fails.",
        "",
        f"Windows tested: {[f'{w}y' for w in WINDOWS]}",
        "",
        f"*ShiftInnerV lookback sensitivity report — {now}*",
    ]

    with open(output_path, "w") as f:
        f.write("\n".join(lines))

    print(f"Report written: {output_path}")


def write_csv(all_results: list, csv_path: str) -> None:
    """Write machine-readable CSV for further analysis."""
    rows = []
    for r in all_results:
        for w in WINDOWS:
            res = r["results"].get(w, {})
            rows.append({
                "ticker1":        r["ticker1"],
                "ticker2":        r["ticker2"],
                "label":          r["label"],
                "composition":    r.get("composition", ""),
                "window_years":   w,
                "cointegrated_95": res.get("cointegrated_95"),
                "half_life":      res.get("half_life"),
                "snr":            res.get("snr"),
                "episodes":       res.get("episodes"),
                "trace_stat":     res.get("trace_stat"),
                "rating":         res.get("rating"),
                "error":          res.get("error"),
                "classification": r["robustness"]["classification"],
            })

    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    print(f"CSV written: {csv_path}")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="ShiftInnerV lookback window sensitivity analysis"
    )
    parser.add_argument(
        "--compositions",
        default=str(PROJECT_ROOT / "compositions"),
        help="Path to compositions directory",
    )
    parser.add_argument(
        "--output",
        default=str(PROJECT_ROOT / "lookback_sensitivity_report.md"),
        help="Output markdown report path",
    )
    parser.add_argument(
        "--csv",
        default=str(PROJECT_ROOT / "lookback_sensitivity.csv"),
        help="Output CSV path",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Parallel workers (default 4; each pair runs 4x = expensive)",
    )
    parser.add_argument(
        "--composition",
        default=None,
        help="Run only a single composition (e.g. composition_b_defense)",
    )
    args = parser.parse_args()

    print("ShiftInnerV — Lookback Window Sensitivity Analysis")
    print("=" * 60)
    print()

    # Load pairs
    all_pairs = load_all_pairs(args.compositions)

    if args.composition:
        all_pairs = [p for p in all_pairs if p[3] == args.composition]
        print(f"Filtered to: {args.composition} ({len(all_pairs)} pairs)")

    n = len(all_pairs)
    print(f"Pairs to test: {n}")
    print(f"Windows per pair: {WINDOWS}")
    print(f"Total analyze_pair() calls: {n * len(WINDOWS)}")
    print(f"Workers: {args.workers}")
    print()

    # Run sensitivity analysis
    work = [(t1, t2, lbl, WINDOWS) for t1, t2, lbl, comp in all_pairs]
    comp_lookup = {(t1, t2): comp for t1, t2, lbl, comp in all_pairs}

    all_results = []

    if args.workers > 1:
        with ProcessPoolExecutor(max_workers=args.workers) as ex:
            futures = {ex.submit(_worker, w): w for w in work}
            for i, fut in enumerate(as_completed(futures), 1):
                result = fut.result()
                t1, t2 = result["ticker1"], result["ticker2"]
                result["composition"] = comp_lookup.get((t1, t2), "")
                all_results.append(result)
                cl = result["robustness"]["classification"]
                icon = "✅" if cl == "ROBUST" else "⚠️" if cl == "FRAGILE" else "?"
                print(f"  [{i:>3}/{n}] {icon} {t1}/{t2}")
    else:
        for i, args_tuple in enumerate(work, 1):
            result = _worker(args_tuple)
            t1, t2 = result["ticker1"], result["ticker2"]
            result["composition"] = comp_lookup.get((t1, t2), "")
            all_results.append(result)
            cl = result["robustness"]["classification"]
            icon = "✅" if cl == "ROBUST" else "⚠️" if cl == "FRAGILE" else "?"
            print(f"  [{i:>3}/{n}] {icon} {t1}/{t2}")

    # Generate output
    print("\nGenerating reports...")
    generate_report(all_results, args.output)
    write_csv(all_results, args.csv)

    # Print summary
    robust = sum(1 for r in all_results
                 if r["robustness"]["classification"] == "ROBUST")
    fragile = sum(1 for r in all_results
                  if r["robustness"]["classification"] == "FRAGILE")
    insufficient = sum(1 for r in all_results
                       if r["robustness"]["classification"] == "INSUFFICIENT_DATA")

    print(f"\nResults:")
    print(f"  ROBUST:            {robust:>4} ({robust/n*100:.0f}%)")
    print(f"  FRAGILE:           {fragile:>4} ({fragile/n*100:.0f}%)")
    print(f"  INSUFFICIENT_DATA: {insufficient:>4} ({insufficient/n*100:.0f}%)")


if __name__ == "__main__":
    main()
