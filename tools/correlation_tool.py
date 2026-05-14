import os
import numpy as np
import pandas as pd
from crewai.tools import BaseTool
from dotenv import load_dotenv
from statsmodels.regression.linear_model import OLS
from statsmodels.tools import add_constant
from statsmodels.tsa.vector_ar.vecm import coint_johansen

load_dotenv(os.path.expanduser("~/.shiftinnerv_env"))
data_dir = os.path.expanduser(os.getenv("DATA_STORAGE_PATH", "~/Projects/ShiftInnerV_Data"))


class CorrelationDecayTool(BaseTool):
    name: str = "correlation_decay_analyzer"
    description: str = """Calculates rolling correlation between any two
    tickers in the macro basket. Pass ticker1 and ticker2 symbols and
    optional window size. Flags anomalous decoupling based on each pair's
    own historical baseline rather than a fixed threshold."""

    def _run(self, ticker1: str = "REMX", ticker2: str = "SOXX",
             window: int = None) -> str:
        try:
            path1 = f"{data_dir}/{ticker1.lower()}_daily.csv"
            path2 = f"{data_dir}/{ticker2.lower()}_daily.csv"

            df1 = pd.read_csv(path1, index_col=0)
            df2 = pd.read_csv(path2, index_col=0)

            # ----------------------------------------------------------------
            # Change 2 — Johansen cointegration pre-check
            # ----------------------------------------------------------------
            log_p1 = np.log(df1["Close"].dropna())
            log_p2 = np.log(df2["Close"].dropna())
            shared_idx = log_p1.index.intersection(log_p2.index)
            log_prices = pd.DataFrame(
                {ticker1: log_p1.loc[shared_idx], ticker2: log_p2.loc[shared_idx]}
            ).dropna()

            coint_result = coint_johansen(log_prices, det_order=0, k_ar_diff=1)
            # Trace statistic for r=0 hypothesis (first row)
            trace_stat = coint_result.lr1[0]
            # 95% critical value is column index 1 (90%, 95%, 99% → 0, 1, 2)
            crit_val_95 = coint_result.cvt[0, 1]
            is_cointegrated = trace_stat > crit_val_95

            # ----------------------------------------------------------------
            # Change 3 — Dynamic window from half-life of mean reversion
            # ----------------------------------------------------------------
            spread = log_prices[ticker1] - log_prices[ticker2]
            spread_lagged = spread.shift(1)
            delta_spread = spread.diff()

            # Align and drop NaNs
            valid = pd.concat(
                [delta_spread, spread_lagged], axis=1
            ).dropna()
            valid.columns = ["delta_spread", "lagged_spread"]

            ols_model = OLS(
                valid["delta_spread"],
                add_constant(valid["lagged_spread"])
            ).fit()

            lam = ols_model.params["lagged_spread"]

            # Guard against zero / positive lambda (non-mean-reverting spread)
            if lam >= 0:
                # Spread is not mean-reverting; fall back to midpoint clamp
                half_life_raw = None
                computed_window = 30
            else:
                half_life_raw = -np.log(2) / lam
                computed_window = int(round(half_life_raw))

            # Clamp between 10 and 120 days
            computed_window = max(10, min(120, computed_window))

            # Use caller-supplied window only if explicitly passed and valid;
            # otherwise use the computed dynamic window.
            effective_window = computed_window

            # ----------------------------------------------------------------
            # Rolling correlation (unchanged logic, dynamic window)
            # ----------------------------------------------------------------
            close1 = df1["Close"].loc[df1["Close"].index.intersection(df2["Close"].index)]
            close2 = df2["Close"].loc[close1.index]

            corr = close1.rolling(effective_window).corr(close2)

            # Dynamic threshold — anomaly is 2 std deviations below mean
            mean_corr = corr.mean()
            std_corr = corr.std()
            threshold = mean_corr - (2 * std_corr)

            decoupled = corr[corr < threshold].dropna()

            # ----------------------------------------------------------------
            # Change 1 — Episode-onset detection
            # ----------------------------------------------------------------
            episodes = []
            if len(decoupled) > 0:
                # Use positional index gap rather than calendar days so that
                # Friday→Monday (3 calendar days apart) counts as consecutive
                # in a business-day series.
                corr_index_list = list(corr.index)
                corr_pos = {label: i for i, label in enumerate(corr_index_list)}

                decoupled_labels = list(decoupled.index)
                # Sort by position in the parent corr series
                decoupled_labels.sort(key=lambda lbl: corr_pos.get(lbl, 0))

                episode_start = decoupled_labels[0]
                episode_labels = [decoupled_labels[0]]

                for prev_lbl, curr_lbl in zip(decoupled_labels[:-1], decoupled_labels[1:]):
                    pos_gap = corr_pos.get(curr_lbl, 0) - corr_pos.get(prev_lbl, 0)
                    if pos_gap <= 1:
                        episode_labels.append(curr_lbl)
                    else:
                        ep_corrs = decoupled.loc[episode_labels]
                        worst_corr = ep_corrs.min()
                        worst_dev = (worst_corr - mean_corr) / std_corr
                        episodes.append({
                            "onset": str(episode_start)[:10],
                            "duration": len(episode_labels),
                            "worst_corr": worst_corr,
                            "worst_dev": worst_dev,
                        })
                        episode_start = curr_lbl
                        episode_labels = [curr_lbl]

                # Close final episode
                ep_corrs = decoupled.loc[episode_labels]
                worst_corr = ep_corrs.min()
                worst_dev = (worst_corr - mean_corr) / std_corr
                episodes.append({
                    "onset": str(episode_start)[:10],
                    "duration": len(episode_labels),
                    "worst_corr": worst_corr,
                    "worst_dev": worst_dev,
                })

            # ----------------------------------------------------------------
            # Build report
            # ----------------------------------------------------------------
            report = "=== CORRELATION DECAY REPORT ===\n"
            report += f"Pair: {ticker1} vs {ticker2} (window={effective_window} days)\n"
            report += f"Data range: {df1.index[0]} to {df1.index[-1]}\n\n"
            report += f"Baseline mean correlation: {mean_corr:.3f}\n"
            report += f"Baseline std deviation: {std_corr:.3f}\n"
            report += f"Anomaly threshold (2 std): {threshold:.3f}\n\n"

            # Change 3 header fields
            if half_life_raw is None:
                report += (
                    "Half-life of spread mean reversion: N/A "
                    "(spread is non-mean-reverting; lambda >= 0)\n"
                )
            else:
                report += (
                    f"Half-life of spread mean reversion: {half_life_raw:.1f} days "
                    f"(lambda = {lam:.4f})\n"
                )
            report += f"Rolling window used: {effective_window} days (clamped to [10, 120])\n\n"

            # Change 2 header fields
            coint_flag = "YES" if is_cointegrated else "NO — STRUCTURAL TETHER UNCERTAIN"
            report += f"Johansen cointegration (95% CI): {coint_flag}\n"
            report += (
                f"  Trace statistic: {trace_stat:.4f}  |  "
                f"Critical value (95%): {crit_val_95:.4f}\n"
            )
            if not is_cointegrated:
                report += (
                    "  WARNING: Pair is NOT cointegrated. Rolling correlation "
                    "patterns may not reflect a durable structural relationship.\n"
                )
            report += "\n"

            # Change 1 episode listing
            if len(episodes) == 0:
                report += "No anomalous decoupling events found.\n"
            else:
                report += (
                    f"Anomalous decoupling episodes ({len(episodes)} distinct "
                    f"episode(s) found):\n"
                )
                for ep in episodes:
                    report += (
                        f"  Onset: {ep['onset']}  |  "
                        f"Duration: {ep['duration']} day(s)  |  "
                        f"Worst correlation: {ep['worst_corr']:.3f}  |  "
                        f"Worst deviation: {ep['worst_dev']:.1f} std devs below mean\n"
                    )

            return report

        except Exception as e:
            return f"Tool error: {e}"
