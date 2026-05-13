import os
from dotenv import load_dotenv
from datetime import date
from crewai import Crew, Process

load_dotenv(os.path.expanduser("~/.innershiftv_env"))

from agents import quant_scout, forensic_researcher, skeptic_analyst
from tasks import correlation_audit, anomaly_investigation, divergence_report

data_dir = os.getenv("DATA_STORAGE_PATH", "/Volumes/Elessar/InnerShiftV_Data")
report_dir = os.getenv("REPORT_DIR", "/Volumes/Elessar/InnerShiftV_Data/reports")
os.makedirs(report_dir, exist_ok=True)

inner_shift_crew = Crew(
    agents=[quant_scout, forensic_researcher, skeptic_analyst],
    tasks=[correlation_audit, anomaly_investigation, divergence_report],
    process=Process.sequential,
    verbose=False  # suppresses the wall of text
)

if __name__ == "__main__":
    print(f"InnerShiftV — Shadow Audit — {date.today()}")
    print("Running Truth Squad...\n")

    result = inner_shift_crew.kickoff()

    # Collect tool execution logs from task outputs
    appendix_lines = []
    for task in [correlation_audit, anomaly_investigation, divergence_report]:
        if hasattr(task, 'output') and task.output:
            appendix_lines.append(f"### {task.agent.role}\n")
            appendix_lines.append(f"```\n{task.output.raw}\n```\n")

    report_path = os.path.join(report_dir, f"divergence_report_{date.today()}.md")

    with open(report_path, 'w') as f:
        f.write(f"# InnerShiftV Divergence Report\n")
        f.write(f"**Date:** {date.today()}\n\n")
        f.write("---\n\n")
        f.write("## Final Verdict\n\n")
        f.write(str(result.raw))
        f.write("\n\n---\n\n")
        f.write("## Appendix: Tool Execution Log\n\n")
        f.write("\n".join(appendix_lines))

    print(f"Report written to: {report_path}")
