import os
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

        # Collect tool execution logs
        appendix_lines = []
        for task in [correlation_audit, anomaly_investigation, divergence_report]:
            if hasattr(task, "output") and task.output:
                appendix_lines.append(f"### {task.agent.role}\n")
                appendix_lines.append(f"```\n{task.output.raw}\n```\n")

        # Write report
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
