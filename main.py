import os
import re
import json
import yaml
import argparse
from dotenv import load_dotenv
from datetime import date
from crewai import Crew, Process

load_dotenv(os.path.expanduser("~/.shiftinnerv_env"))

from agents import make_crew
from tasks import build_tasks
from data_manager import ensure_data, tickers_from_pairs
from dossier import render_dossier
from promote import run as promote_run

data_dir   = os.path.expanduser(os.getenv("DATA_STORAGE_PATH", "~/Projects/ShiftInnerV_Data"))
report_dir = os.path.expanduser(os.getenv("REPORT_DIR", "~/Projects/ShiftInnerV_Data/reports"))
os.makedirs(report_dir, exist_ok=True)

# ── Parse arguments ───────────────────────────────────────────────────────────
parser = argparse.ArgumentParser(description="ShiftInnerV Shadow Audit")
parser.add_argument(
    "--pairs",
    type=str,
    default=None,
    help="Path to a pairs yaml file (default: pairs.yaml in project root)"
)
args = parser.parse_args()

# ── Load the composition ──────────────────────────────────────────────────────
if args.pairs:
    pairs_path = os.path.expanduser(args.pairs)
else:
    pairs_path = os.path.join(os.path.dirname(__file__), "pairs.yaml")
with open(pairs_path, "r") as f:
    composition = yaml.safe_load(f)

pairs = composition["pairs"]


def extract_report_text(raw: str) -> str:
    """
    Recover the actual === CORRELATION DECAY REPORT === text from the Scout's
    output regardless of whether llama3.1 returned it as plain text or wrapped
    it in a JSON blob. Strips trailing JSON closing characters ("}} etc).
    """
    # Already plain text
    if raw.strip().startswith("=== CORRELATION DECAY REPORT ==="):
        return raw.strip()

    # Find the report header anywhere in the string
    match = re.search(r"(=== CORRELATION DECAY REPORT ===.*)", raw, re.DOTALL)
    if match:
        text = match.group(1).strip()
        # Decode escaped newlines (JSON text values use \n literals)
        text = text.replace('\\n', '\n')
        # Strip trailing JSON artefacts: closing quotes, braces, brackets
        text = re.sub(r'[\"\'\}\]]+\s*$', '', text).strip()
        return text

    # Try JSON parsing — walk all string values for the report header
    try:
        blob = json.loads(raw)

        def find_report(obj):
            if isinstance(obj, str) and "=== CORRELATION DECAY REPORT ===" in obj:
                idx = obj.index("=== CORRELATION DECAY REPORT ===")
                return obj[idx:].strip()
            if isinstance(obj, dict):
                for v in obj.values():
                    r = find_report(v)
                    if r:
                        return r
            if isinstance(obj, list):
                for item in obj:
                    r = find_report(item)
                    if r:
                        return r
            return None

        found = find_report(blob)
        if found:
            return found
    except Exception:
        pass

    return raw.strip()


def extract_search_findings(raw: str) -> str:
    """
    The Researcher agent often wraps its final answer in JSON instead of
    returning plain prose. This function attempts to recover meaningful text:
    - If the raw output is already prose, return it
    - If it's JSON, extract any 'text', 'output', or 'result' string values
    - If Serper results are embedded, summarise the titles/snippets found
    Falls back to raw if nothing useful is found.
    """
    stripped = raw.strip()

    # Already plain prose — no JSON brace at start
    if not stripped.startswith("{") and not stripped.startswith("["):
        return stripped

    try:
        blob = json.loads(stripped)

        # Look for prose text in common parameter keys
        prose_keys = {"text", "output", "result", "findings", "summary", "answer"}

        def find_prose(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if k.lower() in prose_keys and isinstance(v, str) and len(v) > 40:
                        return v.strip()
                for v in obj.values():
                    r = find_prose(v)
                    if r:
                        return r
            if isinstance(obj, list):
                for item in obj:
                    r = find_prose(item)
                    if r:
                        return r
            return None

        prose = find_prose(blob)
        if prose and "=== CORRELATION DECAY REPORT ===" not in prose:
            return prose

        # If the JSON contains a search_query, note what was searched
        def find_query(obj):
            if isinstance(obj, dict):
                for k, v in obj.items():
                    if "query" in k.lower() and isinstance(v, str):
                        return v
                for v in obj.values():
                    r = find_query(v)
                    if r:
                        return r
            return None

        query = find_query(blob)
        if query:
            return f"[Researcher described search for: {query} — no results extracted]"

    except Exception:
        pass

    return stripped


# ── Logging setup ────────────────────────────────────────────────────────────
import logging
import sys as _sys

def _setup_log():
    log_path = os.path.join(data_dir, "main.log")
    os.makedirs(data_dir, exist_ok=True)
    logger = logging.getLogger("main")
    logger.setLevel(logging.DEBUG)
    fmt = logging.Formatter("%(asctime)s  %(levelname)-7s  %(message)s",
                            datefmt="%Y-%m-%d %H:%M:%S")
    fh = logging.FileHandler(log_path)
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger, log_path

# ── Run the crew for each pair ────────────────────────────────────────────────
if __name__ == "__main__":
    from datetime import datetime
    log, log_path = _setup_log()

    n      = len(pairs)
    source = os.path.basename(pairs_path)
    print(f"ShiftInnerV  {date.today()}  |  {n} pair(s) from {source}")
    print(f"Log → {log_path}")
    print()
    log.info(f"=== RUN START  {source}  ({n} pairs) ===")

    # ── Ensure data ───────────────────────────────────────────────────────────
    tickers     = tickers_from_pairs(pairs)
    data_status = ensure_data(tickers, data_dir)
    failed      = [t for t, s in data_status.items() if s == "failed"]

    for t, s in data_status.items():
        log.info(f"  data  {t}: {s}")

    if failed:
        print(f"  ⚠️  Data fetch failed: {', '.join(failed)}")
        log.warning(f"Data fetch failed: {', '.join(failed)}")

    # ── Run crew for each pair ────────────────────────────────────────────────
    verdicts = []

    for i, pair in enumerate(pairs, 1):
        ticker1 = pair["ticker1"]
        ticker2 = pair["ticker2"]
        label   = pair["label"]

        if ticker1 in failed or ticker2 in failed:
            print(f"  [{i:>3}/{n}]  SKIP   {ticker1}/{ticker2}  — missing data")
            log.warning(f"SKIP {ticker1}/{ticker2} — missing data")
            continue

        print(f"  [{i:>3}/{n}]  ...    {ticker1}/{ticker2}  {label}", end="", flush=True)
        log.info(f"START {ticker1}/{ticker2}  ({label})")

        lookback_years = pair.get("lookback_years", 5)
        n_pairs = len(pairs)
        quant_scout, signal_mathematician = make_crew(
            ticker1, ticker2,
            lookback_years=lookback_years,
            n_pairs_in_composition=n_pairs,
            pair_label=pair.get("label", ""),
            factor_proxy_ticker=pair.get("factor_proxy", ""),
        )
        correlation_audit, quant_assessment = build_tasks(
            pair=pair,
            agents=(quant_scout, signal_mathematician)
        )
        crew = Crew(
            agents=[quant_scout, signal_mathematician],
            tasks=[correlation_audit, quant_assessment],
            process=Process.sequential,
            verbose=False
        )

        crew_error = None
        try:
            result = crew.kickoff()
        except Exception as e:
            crew_error = str(e)
            log.error(f"Crew failed for {label}: {e}")

        # ── Extract verdict for console line ──────────────────────────────────
        verdict_text  = str(result.raw) if not crew_error else ""
        verdict_upper = verdict_text.upper()
        if crew_error:
            verdict_tag = "ERROR  "
        elif "ACTIVE" in verdict_upper and "MONITOR" not in verdict_upper:
            verdict_tag = "ACTIVE ✅"
        elif "MONITOR-NEAR" in verdict_upper:
            verdict_tag = "MONITOR-NEAR 👀"
        elif "MONITOR" in verdict_upper:
            verdict_tag = "MONITOR 👀"
        else:
            verdict_tag = "REJECT "

        print(f"\r  [{i:>3}/{n}]  {verdict_tag:<16}  {ticker1}/{ticker2}  {label}")
        log.info(f"VERDICT {ticker1}/{ticker2}: {verdict_tag.strip()}")
        if verdict_text:
            log.debug(f"FULL VERDICT:\n{verdict_text}")
        verdicts.append((ticker1, ticker2, verdict_tag))

        # ── Build appendix ────────────────────────────────────────────────────
        appendix_lines = []
        for task in [correlation_audit, quant_assessment]:
            if hasattr(task, "output") and task.output:
                raw  = task.output.raw or ""
                role = task.agent.role
                cleaned = extract_report_text(raw) if role == "Lead Quantitative Scout" else raw
                appendix_lines.append(f"### {role}\n")
                appendix_lines.append(f"```\n{cleaned}\n```\n")
                log.debug(f"TASK OUTPUT [{role}]:\n{cleaned}")

        # ── Write report ──────────────────────────────────────────────────────
        safe_label = label.lower()
        safe_label = "".join(c if c.isalnum() or c in "-_ " else "" for c in safe_label)
        safe_label = safe_label.strip().replace(" ", "_")
        timestamp  = datetime.now().strftime("%Y-%m-%d_%H%M")
        filename   = f"{safe_label}_{ticker1}_{ticker2}_{timestamp}.md"
        report_path = os.path.join(report_dir, filename)

        with open(report_path, "w") as f:
            f.write(f"# ShiftInnerV Divergence Report\n")
            f.write(f"**Pair:** {label}\n")
            f.write(f"**Date:** {date.today()}\n\n")
            f.write("---\n\n")
            f.write("## Final Verdict\n\n")
            if crew_error:
                f.write(f"**ERROR:** Crew failed to complete — {crew_error}\n\n")
                f.write("Partial tool outputs may be available in the appendix below.\n")
            else:
                f.write(str(result.raw))
            f.write("\n\n---\n\n")
            f.write("## Appendix: Tool Execution Log\n\n")
            f.write("\n".join(appendix_lines))

        log.info(f"REPORT  → {report_path}")

        # ── Dossier on actionable verdicts ────────────────────────────────────
        is_actionable = not crew_error and (
            "ACTIVE" in verdict_upper or "MONITOR-NEAR" in verdict_upper
        )
        if is_actionable:
            try:
                lookback_days = pair.get("lookback_years", 5) * 252 // 12
                lookback_days = min(max(lookback_days, 60), 180)
                dossier_text  = render_dossier(ticker1, ticker2, lookback_days)
                dossier_path  = os.path.join(report_dir, f"dossier_{ticker1}_{ticker2}_{timestamp}.md")
                with open(dossier_path, "w") as f:
                    f.write(dossier_text)
                print(f"         ↳ dossier → {dossier_path}")
                log.info(f"DOSSIER → {dossier_path}")
            except Exception as e:
                log.warning(f"Dossier failed for {label}: {e}")

    # ── Summary ───────────────────────────────────────────────────────────────
    actives  = [v for v in verdicts if "ACTIVE"  in v[2]]
    monitors = [v for v in verdicts if "MONITOR" in v[2]]
    rejects  = [v for v in verdicts if "REJECT"  in v[2]]
    print()
    print(f"  Done  {len(verdicts)} pair(s) — "
          f"ACTIVE: {len(actives)}  MONITOR: {len(monitors)}  REJECT: {len(rejects)}")
    log.info(f"=== RUN END  ACTIVE:{len(actives)} MONITOR:{len(monitors)} REJECT:{len(rejects)} ===")

    # ── Auto-promote ──────────────────────────────────────────────────────────
    if len(pairs) > 1:
        try:
            promoted_path = promote_run(quiet=True)
            if promoted_path:
                print(f"  Promoted → {promoted_path}")
                log.info(f"PROMOTED → {promoted_path}")
        except Exception as e:
            log.warning(f"promote.py failed: {e}")
