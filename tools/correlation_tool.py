import pandas as pd
import os
from crewai.tools import BaseTool
from dotenv import load_dotenv

load_dotenv(os.path.expanduser("~/.innershiftv_env"))
data_dir = os.getenv("DATA_STORAGE_PATH", "/Volumes/Elessar/InnerShiftV_Data")

class CorrelationDecayTool(BaseTool):
    name: str = "correlation_decay_analyzer"
    description: str = """Calculates rolling correlation between any two
    tickers in the macro basket. Pass ticker1 and ticker2 symbols and
    optional window size. Flags anomalous decoupling based on each pair's
    own historical baseline rather than a fixed threshold."""

    def _run(self, ticker1: str = "REMX", ticker2: str = "SOXX",
             window: int = 30) -> str:
        try:
            path1 = f"{data_dir}/{ticker1.lower()}_daily.csv"
            path2 = f"{data_dir}/{ticker2.lower()}_daily.csv"

            df1 = pd.read_csv(path1, index_col=0)
            df2 = pd.read_csv(path2, index_col=0)

            corr = df1['Close'].rolling(window).corr(df2['Close'])

            # Dynamic threshold — anomaly is 2 std deviations below mean
            mean_corr = corr.mean()
            std_corr = corr.std()
            threshold = mean_corr - (2 * std_corr)

            decoupled = corr[corr < threshold].dropna()

            report = f"=== CORRELATION DECAY REPORT ===\n"
            report += f"Pair: {ticker1} vs {ticker2} (window={window} days)\n"
            report += f"Data range: {df1.index[0]} to {df1.index[-1]}\n\n"
            report += f"Baseline mean correlation: {mean_corr:.3f}\n"
            report += f"Baseline std deviation: {std_corr:.3f}\n"
            report += f"Anomaly threshold (2 std): {threshold:.3f}\n\n"

            if len(decoupled) == 0:
                report += "No anomalous decoupling events found.\n"
            else:
                report += f"Anomalous decoupling events ({len(decoupled)} found):\n"
                for date, corr_val in decoupled.items():
                    deviation = (corr_val - mean_corr) / std_corr
                    report += f"  {date}: correlation = {corr_val:.3f} ({deviation:.1f} std devs below mean)\n"

            return report

        except Exception as e:
            return f"Tool error: {e}"
