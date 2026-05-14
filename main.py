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
pairs_path = os.path.join(os.path.dirname(__file__), "pairs.yaml")
with open(pairs_path, "r") as f:
    composition = yaml.safe_load(f)

pairs = composition["pairs"]


def extract_report_text(raw: str) -> str:
    """
    llama3.1 8B often wraps tool output in a JSON blob instead of returning
    plain text. This function recovers the actual report text from either:
      - Plain text starting with === CORRELATION DECAY REPORT ===
      - JSON with a string parameter containing the report text
    Falls back to the raw string if neither pattern matches.
    """
    # Already plain text
    if raw.strip().startswith("=== CORRELATION DECAY REPORT ==="):
        return raw.strip()

    # Try to find the report header anywhere in the string
    match = re.search(r"(=== CORRELATION DECAY REPORT ===.*)", raw, re.DOTALL)
    if match:
        return match.group(1).strip()

    # Try to parse as JSON and extract any string value containing the report
    try:
        # Handle single-quoted JSON by converting to double quotes
        normalized = raw.replace("'", '"')
        blob = json.loads(normalized)
        # Walk all string values in the JSON looking for the report header
        def find_report(obj):
            if isinstance(obj, str) and "=== CORRELATION DECAY REPORT ===" in obj:
                idx = obj.index("=== CORRELATION DECAY REPORT ===")
                return obj[idx:].strip()
            if isinstance(obj, dict):
                for v in obj.values():
                    result = find_report(v)
                    if result:
                        return result
            if isinstance(obj, list):
                for item in obj:
                    result = find_report(item)
                    if result:
                        return result
            return None
        found = find_report(blob)
        if found:
            return found
    except Exception:
        pass

    # Fall back to raw — better than nothing
    return raw.strip()


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

        # Skip pair if either ticker failed to download
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

        # ── Build appendix — clean Scout output if JSON-wrapped ──────────────
        appendix_lines = []
        for task in [correlation_audit, anomaly_investigation, divergence_report]:
            if hasattr(task, "output") and task.output:
                raw = task.output.raw or ""
                role = task.agent.role

                # Apply report extraction only to the Scout
                if role == "Lead Quantitative Scout":
                    cleaned = extract_report_text(raw)
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
