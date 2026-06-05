#!/usr/bin/env python3
"""
ShiftInnerV — AI run summarizer.

Collects dossiers and verdict reports from the latest sentinel run,
submits to Claude API, and returns a ranked trade summary.

Usage:
    python summarize.py                          # summarize latest run
    python summarize.py --since 300              # look back 5 hours
    python summarize.py --top 5                  # limit to top 5 pairs in prompt
    python summarize.py --dry-run                # print prompt without calling API

Output:
    Prints ranked summary to console.
    Saves to DATA_DIR/summaries/summary_<timestamp>.md

Env (from ~/.shiftinnerv_env):
    ANTHROPIC_API_KEY       Claude API key
    REPORT_DIR              where sentinel writes reports
    DATA_DIR       base data dir
"""

import os
import sys
import argparse
import requests
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/.shiftinnerv_env"))


# ── Config ────────────────────────────────────────────────────────────────────
PROJECT_DIR      = os.path.dirname(os.path.abspath(__file__))
DATA_DIR         = os.path.join(PROJECT_DIR, "data")
REPORT_DIR         = os.path.join(PROJECT_DIR, "reports")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")


SUMMARY_DIR       = os.path.join(DATA_DIR, "summaries")
MODEL             = "claude-sonnet-4-20250514"
MAX_TOKENS        = 2000
DEFAULT_SINCE     = 120   # minutes
DEFAULT_TOP_N     = 10


# ── Summary prompt template ───────────────────────────────────────────────────

SUMMARY_PROMPT = """You are a quantitative trading analyst reviewing a pairs trading signal run.

The system has identified {n_active} ACTIVE pairs and {n_monitor} MONITOR-NEAR pairs from today's screen.
Your job is to cut through the noise and identify which signals are worth human attention.

Here are the ACTIVE pairs with their quantitative reports and dossier data:

{pairs_text}

TASK — produce a structured run summary with these sections:

## EXECUTIVE SUMMARY
One paragraph. Total signals, how many are worth acting on, overall market context from the news you see.

## TOP SETUPS (ranked by quality, max 3)
For each setup worth considering:

**[RANK]. PAIR: TICKER1/TICKER2**
- Verdict: ACTIVE/MONITOR-NEAR
- Z-score: [value] — [entry zone / extreme / watch]
- Direction: SHORT [X] / LONG [Y]
- Half-life: [n] days — expected hold
- Why it makes sense: [1-2 sentences on fundamental logic — why WOULD these be linked?]
- News catalyst: [what recent news explains the divergence?]
- Risk flag: [any reason to be cautious — earnings, thin liquidity, spurious correlation?]
- Trade setup: Entry now / Wait for pullback to [z-score]

## SKIP LIST
Pairs that are ACTIVE statistically but should be ignored. One line each with reason.
Focus on: spurious correlations (unrelated businesses), extreme z-scores past stop-loss, ETF/currency artifacts.

## SPURIOUS SIGNAL WARNING
If a large proportion of signals involve currency ETFs (FXB, FXC, FXE, FXF), inverse ETFs (UDN),
or unrelated sector pairings, flag this as a data quality issue — the promoted composition
may need thematic tightening.

## ONE ACTION
Single most important thing to do right now.

Be direct. No hedging. If the signals are weak, say so. If one setup is clearly better than the others, say that too.
This summary will be read by someone deciding whether to place a trade in the next 24 hours."""


# ── File collection ───────────────────────────────────────────────────────────

def collect_run_files(report_dir: str, since_minutes: int) -> dict:
    """
    Collect verdict reports and dossiers written in the last N minutes.
    Returns dict: { "ENPH/KRE": { "verdict": "...", "dossier": "..." }, ... }
    """
    cutoff  = datetime.now() - timedelta(minutes=since_minutes)
    reports = {}

    for f in sorted(Path(report_dir).glob("*.md")):
        if f.stat().st_mtime < cutoff.timestamp():
            continue

        text = f.read_text(errors="ignore")
        name = f.stem.upper()

        # Classify file type
        if "PAIR DOSSIER" in text:
            file_type = "dossier"
        elif "QUANTITATIVE ASSESSMENT" in text or "VERDICT:" in text:
            file_type = "verdict"
        else:
            continue

        # Extract pair key from filename
        pair_key = _pair_from_filename(name)
        if not pair_key:
            continue

        if pair_key not in reports:
            reports[pair_key] = {}
        reports[pair_key][file_type] = text

    return reports


def _pair_from_filename(name: str) -> str | None:
    """
    Extract TICKER1/TICKER2 from a filename stem (already uppercased).
    Handles both dossier_ENPH_KRE_... and enph_vs_kre_ENPH_KRE_... formats.
    """
    parts = name.split("_")

    if parts[0] == "DOSSIER" and len(parts) >= 3:
        t1, t2 = parts[1], parts[2]
        if t1.isalpha() and t2.replace("-", "").isalpha():
            return f"{t1}/{t2}"

    # Scan for two consecutive alpha tokens that look like tickers
    for i in range(len(parts) - 1):
        t1, t2 = parts[i], parts[i + 1]
        if (t1 != "VS" and t2 != "VS"
                and t1.replace("-", "").isalpha() and t2.replace("-", "").isalpha()
                and 1 <= len(t1) <= 5 and 1 <= len(t2) <= 5):
            try:
                int(t1)
                continue
            except ValueError:
                pass
            return f"{t1}/{t2}"

    return None


# ── Field extraction ──────────────────────────────────────────────────────────

def extract_verdict(text: str) -> str:
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("VERDICT:"):
            return stripped.replace("VERDICT:", "").strip()
    return "UNKNOWN"


def extract_zscore(text: str) -> str:
    for line in text.split("\n"):
        if "Z-score" in line and ":" in line:
            return line.split(":")[1].strip().split()[0]
    return "N/A"


def extract_direction(text: str) -> str:
    for line in text.split("\n"):
        if "SHORT" in line and "LONG" in line and "Direction" not in line:
            return line.strip()
    return "N/A"


def zscore_magnitude(entry: dict) -> float:
    try:
        return abs(float(entry["zscore"].replace("+", "").replace("−", "-")))
    except (ValueError, AttributeError):
        return 0.0


# ── Prompt construction ───────────────────────────────────────────────────────

def build_pairs_text(run_data: dict, top_n: int) -> tuple[str, int, int]:
    """
    Build the pairs block for the prompt.
    Returns (pairs_text, n_active, n_monitor).
    """
    actives  = []
    monitors = []

    for pair, files in run_data.items():
        verdict_text = files.get("verdict", "")
        dossier_text = files.get("dossier", "")
        verdict      = extract_verdict(verdict_text)

        entry = {
            "pair":         pair,
            "verdict":      verdict,
            "zscore":       extract_zscore(dossier_text),
            "direction":    extract_direction(dossier_text),
            "verdict_text": verdict_text[:1500],
            "dossier_text": dossier_text[:1000],
        }

        if "ACTIVE" in verdict.upper():
            actives.append(entry)
        elif "MONITOR" in verdict.upper():
            monitors.append(entry)

    actives.sort(key=zscore_magnitude, reverse=True)
    actives = actives[:top_n]

    lines = []
    for e in actives:
        lines.append(
            f"--- PAIR: {e['pair']} | VERDICT: {e['verdict']} "
            f"| Z-SCORE: {e['zscore']} ---\n"
            f"DIRECTION: {e['direction']}\n\n"
            f"QUANTITATIVE REPORT:\n{e['verdict_text']}\n\n"
            f"DOSSIER HIGHLIGHTS:\n{e['dossier_text']}\n"
        )

    if monitors:
        lines.append("--- MONITOR-NEAR PAIRS (near-cointegrated) ---")
        for e in monitors[:3]:
            lines.append(f"{e['pair']} | z={e['zscore']} | {e['direction']}")

    return "\n".join(lines), len(actives), len(monitors)


def generate_summary_prompt(run_data: dict, top_n: int) -> str:
    pairs_text, n_active, n_monitor = build_pairs_text(run_data, top_n)
    return SUMMARY_PROMPT.format(
        n_active   = n_active,
        n_monitor  = n_monitor,
        pairs_text = pairs_text,
    )


# ── API call ──────────────────────────────────────────────────────────────────

def call_claude(prompt: str) -> str:
    if not ANTHROPIC_API_KEY:
        return "ERROR: ANTHROPIC_API_KEY not set in ~/.shiftinnerv_env"

    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "Content-Type":      "application/json",
                "x-api-key":         ANTHROPIC_API_KEY,
                "anthropic-version": "2023-06-01",
            },
            json={
                "model":      MODEL,
                "max_tokens": MAX_TOKENS,
                "messages":   [{"role": "user", "content": prompt}],
            },
            timeout=60,
        )
        r.raise_for_status()
        return r.json()["content"][0]["text"]
    except requests.exceptions.HTTPError as e:
        return f"API error: {e}\n{r.text}"
    except Exception as e:
        return f"Request failed: {e}"


# ── Output ────────────────────────────────────────────────────────────────────

def save_summary(text: str) -> str:
    os.makedirs(SUMMARY_DIR, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M")
    path      = os.path.join(SUMMARY_DIR, f"summary_{timestamp}.md")
    header    = (
        f"# ShiftInnerV Run Summary\n"
        f"**Generated:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n"
        f"---\n\n"
    )
    with open(path, "w") as f:
        f.write(header + text)
    return path


def show_run_stats(run_data: dict):
    verdicts = {k: extract_verdict(v.get("verdict", "")) for k, v in run_data.items()}
    actives  = sum(1 for v in verdicts.values() if "ACTIVE"  in v.upper())
    monitors = sum(1 for v in verdicts.values() if "MONITOR" in v.upper())
    rejects  = sum(1 for v in verdicts.values() if "REJECT"  in v.upper())
    print(f"\n{'=' * 55}")
    print(f"  Run files found : {len(run_data)}")
    print(f"  ACTIVE          : {actives}")
    print(f"  MONITOR-NEAR    : {monitors}")
    print(f"  REJECT          : {rejects}")
    print(f"{'=' * 55}\n")


# ── Entry point ───────────────────────────────────────────────────────────────

def run(
    report_dir:    str  = None,
    since_minutes: int  = DEFAULT_SINCE,
    top_n:         int  = DEFAULT_TOP_N,
    dry_run:       bool = False,
    quiet:         bool = False,
) -> str | None:
    """
    Callable from sentinel.py or directly.
    Returns path to saved summary, or None on dry-run / no data.
    """
    rdir     = report_dir or REPORT_DIR
    run_data = collect_run_files(rdir, since_minutes)

    if not run_data:
        if not quiet:
            print(f"  summarize.py: no files found in {rdir} (last {since_minutes}m)")
        return None

    if not quiet:
        show_run_stats(run_data)

    prompt = generate_summary_prompt(run_data, top_n)

    if dry_run:
        print("=" * 55)
        print("DRY RUN — prompt preview (first 3000 chars):")
        print("=" * 55)
        print(prompt[:3000] + ("..." if len(prompt) > 3000 else ""))
        return None

    if not quiet:
        print("  Calling Claude API...")

    summary = call_claude(prompt)

    print(f"\n{'=' * 55}")
    print(summary)
    print(f"{'=' * 55}\n")

    path = save_summary(summary)
    if not quiet:
        print(f"  Summary saved → {path}")

    return path


def main():
    parser = argparse.ArgumentParser(
        description="ShiftInnerV — AI run summary via Claude API"
    )
    parser.add_argument(
        "--report-dir", type=str, default=None,
        help=f"Report directory (default: {REPORT_DIR})"
    )
    parser.add_argument(
        "--since", type=int, default=DEFAULT_SINCE,
        help=f"Look back N minutes for recent files (default: {DEFAULT_SINCE})"
    )
    parser.add_argument(
        "--top", type=int, default=DEFAULT_TOP_N,
        help=f"Max ACTIVE pairs to include in prompt (default: {DEFAULT_TOP_N})"
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Print prompt without calling API"
    )
    parser.add_argument(
        "--quiet", action="store_true",
        help="Suppress console output"
    )
    args = parser.parse_args()

    run(
        report_dir    = args.report_dir,
        since_minutes = args.since,
        top_n         = args.top,
        dry_run       = args.dry_run,
        quiet         = args.quiet,
    )


if __name__ == "__main__":
    main()
