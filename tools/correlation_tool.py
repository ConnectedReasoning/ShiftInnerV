import os
import numpy as np
import pandas as pd
from crewai.tools import BaseTool
from dotenv import load_dotenv
from statsmodels.regression.linear_model import OLS
from statsmodels.tools import add_constant
from statsmodels.tsa.vector_ar.vecm import coint_johansen

load_dotenv(os.path.expanduser("~/.innershiftv_env"))
data_dir = os.getenv("DATA_STORAGE_PATH", "/Volumes/Elessar/InnerShiftV_Data")


class CorrelationDecayTool(BaseTool):
    name: str = "correlation_decay_analyzer"
    description: str = """Calculates rolling correlation between any two
    tickers in the macro basket. Pass ticker1 and ticker2 symbols and
    optional window size. Flags anomalous decoupling based on each pair's
    own historical baseline rather than a fixed threshold."""

    # Set at instantiation time to lock the tool to a specific pair.
    # If set, any call with different tickers is rejected immediately.
    expected_ticker1: str = ""
    expected_ticker2: str = ""

    def _run(self, ticker1: str = "REMX", ticker2: str = "SOXX",
             window: int = None) -> str:
        try:
            # ── Hallucination guard ───────────────────────────────────────────
            if self.expected_ticker1 and self.expected_ticker2:
                allowed = {self.expected_ticker1.upper(), self.expected_ticker2.upper()}
                provided = {ticker1.upper(), ticker2.upper()}
                if provided != allowed:
                    return (
                        f"Tool error: invalid tickers {ticker1}/{ticker2}. "
                        f"This tool is locked to {self.expected_ticker1}/{self.expected_ticker2} "
                        f"for this run. Do not call other tools. "
                        f"Return your final answer using the data already provided."
                    )
            path1 = f"{data_dir}/{ticker1.lower()}_daily.csv"
            path2 = f"{data_dir}/{ticker2.lower()}_daily.csv"

            df1 = pd.read_csv(path1, index_col=0)
            df2 = pd.read_csv(path2, index_col=0)

            # ----------------------------------------------------------------
            # Johansen cointegration pre-check
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
            # Dynamic window from half-life of mean reversion
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
            # SNR pair score
            # ----------------------------------------------------------------
            # Decompose the spread into its stationary and nonstationary
            # components via an OLS cointegrating regression on the log prices.
            # This gives a proper hedge ratio rather than the 1:1 difference
            # used in the rolling correlation section above.
            #
            #   stationary component  = OLS residuals (mean-reverting part)
            #   nonstationary component = fitted values (I(1) trend component)
            #
            # SNR = var(stationary) / var(nonstationary)
            # Higher SNR → spread is dominated by mean-reverting signal
            # relative to drift; stronger tradability signal.

            ols_coint = OLS(
                log_prices[ticker1],
                add_constant(log_prices[ticker2])
            ).fit()

            residuals = pd.Series(ols_coint.resid, index=log_prices.index)
            trend_component = log_prices[ticker1] - residuals  # fitted values

            var_stationary = float(np.var(residuals, ddof=1))
            var_nonstationary = float(np.var(trend_component, ddof=1))

            if var_nonstationary > 1e-10:
                snr = var_stationary / var_nonstationary
            else:
                # Trend is essentially flat — spread is nearly pure stationary
                snr = float("inf")

            if snr == float("inf") or snr > 2.0:
                snr_tier = "STRONG"
                snr_interpretation = (
                    "The spread's mean-reverting signal dominates its trend drift. "
                    "High confidence in pair tradability."
                )
            elif snr >= 1.0:
                snr_tier = "MODERATE"
                snr_interpretation = (
                    "Meaningful mean reversion present but non-trivial trend risk remains. "
                    "Trade with position discipline."
                )
            else:
                snr_tier = "WEAK"
                snr_interpretation = (
                    "Nonstationary drift dominates the mean-reverting signal. "
                    "Low confidence in pair tradability — heightened skepticism warranted."
                )

            snr_display = f"{snr:.4f}" if snr != float("inf") else "inf (near-flat trend)"

            # ----------------------------------------------------------------
            # Mean drift flag
            # ----------------------------------------------------------------
            # Flag if the spread's rolling mean (over the dynamic window) has
            # moved more than 1.5 standard deviations from the full-sample mean.
            # This is the quantitative signal that fundamental findings may be
            # invalidating the cointegration assumption.

            rolling_mean_series = spread.rolling(window=effective_window).mean().dropna()
            full_sample_mean = float(spread.mean())
            full_sample_std = float(spread.std(ddof=1))

            latest_rolling_mean = float(rolling_mean_series.iloc[-1])
            if full_sample_std > 1e-10:
                drift_z = abs(latest_rolling_mean - full_sample_mean) / full_sample_std
            else:
                drift_z = 0.0

            mean_drift_flag = drift_z > 1.5
            mean_drift_display = "TRUE" if mean_drift_flag else "FALSE"
            mean_drift_detail = (
                f"rolling_mean({effective_window}d)={latest_rolling_mean:.4f}, "
                f"full_sample_mean={full_sample_mean:.4f}, "
                f"deviation={drift_z:.2f}σ"
            )

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
            # Episode-onset detection (unchanged)
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

            # ── Pair Score (SNR) block ────────────────────────────────────────
            report += "=== PAIR SCORE ===\n"
            report += f"pair_score (SNR): {snr_display}\n"
            report += f"pair_score_tier:  {snr_tier}\n"
            report += f"Interpretation:   {snr_interpretation}\n"
            report += "\n"

            # ── Mean Drift block ──────────────────────────────────────────────
            report += "=== MEAN DRIFT ===\n"
            report += f"mean_drift: {mean_drift_display}\n"
            report += f"Detail:     {mean_drift_detail}\n"
            if mean_drift_flag:
                report += (
                    "WARNING: Rolling mean has drifted >1.5σ from full-sample mean. "
                    "Fundamental findings may be invalidating the cointegration assumption.\n"
                )
            else:
                report += "No significant mean drift detected.\n"
            report += "\n"

            # ── Episode listing ───────────────────────────────────────────────
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
