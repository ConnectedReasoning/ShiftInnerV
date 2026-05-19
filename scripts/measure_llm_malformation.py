#!/usr/bin/env python3
"""
ShiftInnerV — LLM Malformation Rate Analyser
Item 4 of the Council Roadmap.

Reads llm_calls.log and computes the malformed output rate across all agents.
Run after 100+ pairs to get a statistically meaningful measurement.

Usage:
    python scripts/measure_llm_malformation.py
    python scripts/measure_llm_malformation.py --logfile /path/to/llm_calls.log
    python scripts/measure_llm_malformation.py --output report.txt
    python scripts/measure_llm_malformation.py --agent "Lead Quantitative Scout"

Exit codes:
    0 — malformation rate acceptable (< 5%)
    1 — malformation rate UNACCEPTABLE (>= 5%) — halt trading
"""

import argparse
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path


ACCEPTABLE_MALFORMATION_RATE = 5.0  # percent


def parse_log(logfile: str) -> dict:
    """
    Parse llm_calls.log and aggregate recovery outcome counts.

    Returns a dict keyed by agent_name with sub-dict of recovery counts.
    Also includes an "ALL" key with totals.
    """
    recovery_types = [
        "success",
        "fallback_regex",
        "fallback_json_extraction",
        "failure",
    ]

    # per-agent counts + overall
    counts: dict[str, dict[str, int]] = defaultdict(
        lambda: {rt: 0 for rt in recovery_types}
    )

    with open(logfile) as f:
        for line in f:
            # Identify agent name from the log format:
            # "[OK] Lead Quantitative Scout BABA/JD | recovery=success | ..."
            agent = "Unknown"
            for known in ("Lead Quantitative Scout", "Signal Mathematician"):
                if known in line:
                    agent = known
                    break

            for rt in recovery_types:
                if f"recovery={rt}" in line:
                    counts[agent][rt] += 1
                    counts["ALL"][rt] += 1
                    break

    return counts


def malformation_rate(counts: dict) -> float:
    total = sum(counts.values())
    if total == 0:
        return 0.0
    malformed = counts["fallback_regex"] + counts["fallback_json_extraction"] + counts["failure"]
    return malformed / total * 100


def format_section(name: str, counts: dict) -> str:
    total = sum(counts.values())
    if total == 0:
        return f"\n{name}: no data\n"

    rate = malformation_rate(counts)
    status = "✅ ACCEPTABLE" if rate < ACCEPTABLE_MALFORMATION_RATE else "❌ UNACCEPTABLE — HALT TRADING"

    lines = [
        f"\n{name}",
        f"  Total calls:                        {total}",
        f"  Success (no recovery):              {counts['success']:>5}  ({counts['success']/total*100:>5.1f}%)",
        f"  Fallback regex:                     {counts['fallback_regex']:>5}  ({counts['fallback_regex']/total*100:>5.1f}%)",
        f"  Fallback JSON extraction:           {counts['fallback_json_extraction']:>5}  ({counts['fallback_json_extraction']/total*100:>5.1f}%)",
        f"  Failure (unrecoverable):            {counts['failure']:>5}  ({counts['failure']/total*100:>5.1f}%)",
        f"  Malformed output rate:              {rate:>5.1f}%  —  {status}",
    ]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Analyse LLM call logs to measure malformed output rate."
    )
    parser.add_argument(
        "--logfile",
        default=None,
        help="Path to llm_calls.log (default: auto-detect from DATA_STORAGE_PATH env var)",
    )
    parser.add_argument(
        "--output",
        default="llm_malformation_report.txt",
        help="Output report file (default: llm_malformation_report.txt)",
    )
    parser.add_argument(
        "--agent",
        default=None,
        help="Filter to a specific agent name (optional)",
    )
    args = parser.parse_args()

    # Resolve log path
    if args.logfile:
        logfile = args.logfile
    else:
        import os
        data_dir = os.path.expanduser(
            os.getenv("DATA_STORAGE_PATH", "~/Projects/ShiftInnerV_Data")
        )
        logfile = os.path.join(data_dir, "llm_calls.log")

    if not Path(logfile).exists():
        print(f"❌ Log file not found: {logfile}")
        print("   Run main.py to generate llm_calls.log first.")
        sys.exit(1)

    counts = parse_log(logfile)

    if not counts or not counts.get("ALL") or sum(counts["ALL"].values()) == 0:
        print("⚠️  Log file exists but contains no LLM outcome entries.")
        print("   Ensure main.py has been updated to instrument LLM calls (Item 4).")
        sys.exit(0)

    # Build report
    separator = "─" * 70
    report_lines = [
        "ShiftInnerV — LLM Malformation Rate Report",
        f"Generated: {datetime.now().isoformat()}",
        f"Log file:  {logfile}",
        separator,
    ]

    # Overall section
    report_lines.append(format_section("OVERALL (all agents)", counts["ALL"]))

    # Per-agent breakdowns (skip ALL and Unknown if no data)
    for agent in sorted(k for k in counts if k not in ("ALL",)):
        if sum(counts[agent].values()) > 0:
            if args.agent is None or args.agent.lower() in agent.lower():
                report_lines.append(format_section(agent, counts[agent]))

    report_lines.append("")
    report_lines.append(separator)

    overall_rate = malformation_rate(counts["ALL"])
    if overall_rate >= ACCEPTABLE_MALFORMATION_RATE:
        report_lines.append(
            "⚠️  RECOMMENDATION: Malformation rate >= 5%. "
            "The system is systematically producing malformed output."
        )
        report_lines.append(
            "   → HALT live trading. Debug Scout template / model / prompt."
        )
    else:
        report_lines.append(
            "✅  Malformation rate within acceptable threshold. "
            "Monitor as call volume grows."
        )

    report = "\n".join(report_lines)

    print(report)

    with open(args.output, "w") as f:
        f.write(report + "\n")
    print(f"\nReport written → {args.output}")

    # Exit 1 if unacceptable (for CI/scripting)
    sys.exit(1 if overall_rate >= ACCEPTABLE_MALFORMATION_RATE else 0)


if __name__ == "__main__":
    main()
