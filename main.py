import os
import re
import json
import yaml
from dotenv import load_dotenv
from datetime import date
from crewai import Crew, Process

load_dotenv(os.path.expanduser("~/.shiftinnerv_env"))

from agents import quant_scout, forensic_researcher, skeptic_analyst
from tasks import build_tasks
from data_manager import ensure_data, tickers_from_pairs

data_dir   = os.path.expanduser(os.getenv("DATA_STORAGE_PATH", "~/Projects/ShiftInnerV_Data"))
report_dir = os.path.expanduser(os.getenv("REPORT_DIR", "~/Projects/ShiftInnerV_Data/reports"))
os.makedirs(report_dir, exist_ok=True)

# ── Load the composition ──────────────────────────────────────────────────────
pairs_path = os.path.join(os.path.dirname(__file__), "pairs_cisco_ai_reallocation.yaml")
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


# ── Run the crew for each pair ────────────────────────────────────────────────
if __name__ == "__main__":
    print(f"ShiftInnerV — Shadow Audit — {date.today()}")
    print(f"Loaded {len(pairs)} pair(s) from pairs.yaml\n")

    # ── Ensure data is present and fresh before running agents ───────────────
    tickers = tickers_from_pairs(pairs)
    print(f"Checking data for: {', '.join(tickers)}")
    data_status = ensure_data(tickers, data_dir)

    failed = [t for t, s in data_status.items() if s == "failed"]
    if failed:
        print(f"\n  WARNING: Failed to fetch data for: {', '.join(failed)}")
        print("  Agents will error on these tickers. Check your connection.\n")
    else:
        print()

    # ── Run crew for each pair ────────────────────────────────────────────────
    for pair in pairs:
        ticker1 = pair["ticker1"]
        ticker2 = pair["ticker2"]
        label   = pair["label"]

        if ticker1 in failed or ticker2 in failed:
            print(f"Skipping {label} — missing data for one or both tickers.\n")
            continue

        print(f"Running Truth Squad: {label} ({ticker1} / {ticker2})\n")

        correlation_audit, anomaly_investigation, divergence_report = build_tasks(
            pair=pair,
            agents=(quant_scout, forensic_researcher, skeptic_analyst)
        )

        crew = Crew(
            agents=[quant_scout, forensic_researcher, skeptic_analyst],
            tasks=[correlation_audit, anomaly_investigation, divergence_report],
            process=Process.sequential,
            verbose=False
        )

        result = crew.kickoff()

        # ── Build appendix — clean outputs per agent role ────────────────────
        appendix_lines = []
        for task in [correlation_audit, anomaly_investigation, divergence_report]:
            if hasattr(task, "output") and task.output:
                raw  = task.output.raw or ""
                role = task.agent.role

                if role == "Lead Quantitative Scout":
                    cleaned = extract_report_text(raw)
                elif role == "Macro Context Researcher":
                    cleaned = extract_search_findings(raw)
                else:
                    cleaned = raw

                appendix_lines.append(f"### {role}\n")
                appendix_lines.append(f"```\n{cleaned}\n```\n")

        # ── Write report ──────────────────────────────────────────────────────
        safe_label = f"{ticker1}_{ticker2}"
        report_path = os.path.join(
            report_dir,
            f"divergence_report_{safe_label}_{date.today()}.md"
        )

        with open(report_path, "w") as f:
            f.write(f"# ShiftInnerV Divergence Report\n")
            f.write(f"**Pair:** {label}\n")
            f.write(f"**Date:** {date.today()}\n\n")
            f.write("---\n\n")
            f.write("## Final Verdict\n\n")
            f.write(str(result.raw))
            f.write("\n\n---\n\n")
            f.write("## Appendix: Tool Execution Log\n\n")
            f.write("\n".join(appendix_lines))

        print(f"Report written to: {report_path}\n")
