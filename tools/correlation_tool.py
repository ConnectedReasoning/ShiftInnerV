import os
import numpy as np
import pandas as pd
from datetime import datetime, timedelta
from crewai.tools import BaseTool
from dotenv import load_dotenv
from statsmodels.regression.linear_model import OLS
from statsmodels.tools import add_constant
from statsmodels.tsa.vector_ar.vecm import coint_johansen

load_dotenv(os.path.expanduser("~/.shiftinnerv_env"))
data_dir = os.getenv("DATA_STORAGE_PATH", "/Volumes/Elessar/ShiftInnerV_Data")


class CorrelationDecayTool(BaseTool):
    name: str = "correlation_decay_analyzer"
    description: str = """Calculates rolling correlation between any two
    tickers in the macro basket. Pass ticker1 and ticker2 symbols and
    optional window size. Flags anomalous decoupling based on each pair's
    own historical baseline rather than a fixed threshold."""

    # Set at instantiation time — lock tool to specific pair and lookback.
    # The LLM never controls these; they are set by the orchestration layer.
    expected_ticker1: str = ""
    expected_ticker2: str = ""
    lookback_years: int = 5   # 1, 3, or 5 — default is full 5-year history
    n_pairs_in_composition: int = 1  # used for Gate 1 threshold adjustment

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

            # ── Apply lookback window ─────────────────────────────────────────
            cutoff = (datetime.today() - timedelta(days=self.lookback_years * 365)).strftime("%Y-%m-%d")
            df1 = df1[df1.index >= cutoff]
            df2 = df2[df2.index >= cutoff]

            if len(df1) < 60 or len(df2) < 60:
                return (
                    f"Tool error: insufficient data after applying "
                    f"{self.lookback_years}-year lookback. "
                    f"Got {len(df1)} rows for {ticker1}, {len(df2)} rows for {ticker2}. "
                    f"Minimum 60 rows required."
                )

            # ── Johansen cointegration pre-check ──────────────────────────────
            log_p1 = np.log(df1["Close"].dropna())
            log_p2 = np.log(df2["Close"].dropna())
            shared_idx = log_p1.index.intersection(log_p2.index)
            log_prices = pd.DataFrame(
                {ticker1: log_p1.loc[shared_idx], ticker2: log_p2.loc[shared_idx]}
            ).dropna()

            # ── Gate 1 threshold selection (Item 1 — multiple comparisons) ──
            # The threshold used for Gate 1 depends on how many pairs are
            # being tested in this composition. More pairs = higher threshold
            # required to maintain the family-wise error rate.
            n_pairs = self.n_pairs_in_composition
            if n_pairs <= 10:
                _gate1_ci_label = "95%"
                _gate1_pass = lambda: is_cointegrated_95
            elif n_pairs <= 30:
                _gate1_ci_label = "99%"
                _gate1_pass = lambda: is_cointegrated_99
            else:
                # For large compositions, require 99% CI AND trace ratio >= 1.3
                _gate1_ci_label = "99%+ratio"
                _gate1_pass = lambda: (
                    is_cointegrated_99 and
                    (trace_stat / crit_val_99 >= 1.3)
                )

            # ── Window separation (Chan fix) ──────────────────────────────────────────
            # Training window: first 250 rows — used ONLY for Johansen eigenvector
            # Signal window:   remaining rows — used for all spread calculations
            #
            # NOTE: With lookback_years=1 (~252 rows total), the signal window will
            # be only ~2 rows — too short for any reliable z-score. Prefer
            # lookback_years=2 (signal ~254 rows) or lookback_years=3 (signal ~506 rows).
            TRAIN_WINDOW = 250
            if len(log_prices) < TRAIN_WINDOW + 60:
                return (
                    f"Tool error: insufficient data for window separation. "
                    f"Need at least {TRAIN_WINDOW + 60} aligned rows "
                    f"(250 training + 60 signal). "
                    f"Got {len(log_prices)} rows for {ticker1}/{ticker2}. "
                    f"Increase lookback_years or check data availability."
                )

            log_prices_train  = log_prices.iloc[:TRAIN_WINDOW]
            log_prices_signal = log_prices.iloc[TRAIN_WINDOW:]

            # ── Multi-lag Johansen (Item 17 — Chan/Vidyamurthy fix) ──────────────────
            # Run at k=1, 2, 3. Use the most conservative result (lowest trace stat).
            # Critical values are k-invariant — only the trace statistic changes.
            # The eigenvector from the conservative run is used for the hedge ratio
            # and propagates into all SNR calculations.
            _johansen_runs = {}
            for _k in [1, 2, 3]:
                _r = coint_johansen(log_prices_train, det_order=0, k_ar_diff=_k)
                _johansen_runs[_k] = _r

            conservative_k    = min(_johansen_runs, key=lambda k: _johansen_runs[k].lr1[0])
            coint_result      = _johansen_runs[conservative_k]

            trace_stat        = coint_result.lr1[0]
            crit_val_90       = coint_result.cvt[0, 0]
            crit_val_95       = coint_result.cvt[0, 1]
            crit_val_99       = coint_result.cvt[0, 2]

            # All three trace statistics — for reporting
            trace_by_k = {k: _johansen_runs[k].lr1[0] for k in [1, 2, 3]}

            # Report all three confidence levels — agent sees the full picture
            is_cointegrated_90 = trace_stat > crit_val_90
            is_cointegrated_95 = trace_stat > crit_val_95
            is_cointegrated_99 = trace_stat > crit_val_99

            # Primary gate uses 95% CI
            is_cointegrated = is_cointegrated_95

            # ── Dynamic window from half-life ─────────────────────────────────
            spread = log_prices_signal[ticker1] - log_prices_signal[ticker2]
            spread_lagged = spread.shift(1)
            delta_spread  = spread.diff()

            valid = pd.concat(
                [delta_spread, spread_lagged], axis=1
            ).dropna()
            valid.columns = ["delta_spread", "lagged_spread"]

            ols_model = OLS(
                valid["delta_spread"],
                add_constant(valid["lagged_spread"])
            ).fit()

            lam = ols_model.params["lagged_spread"]

            if lam >= 0:
                half_life_raw   = None
                computed_window = 30
            else:
                half_life_raw   = -np.log(2) / lam
                computed_window = int(round(half_life_raw))

            computed_window  = max(10, min(120, computed_window))
            effective_window = computed_window

            # ── SNR pair score ────────────────────────────────────────────────
            ols_coint = OLS(
                log_prices_signal[ticker1],
                add_constant(log_prices_signal[ticker2])
            ).fit()

            residuals       = pd.Series(ols_coint.resid, index=log_prices_signal.index)
            trend_component = log_prices_signal[ticker1] - residuals

            var_stationary    = float(np.var(residuals, ddof=1))
            var_nonstationary = float(np.var(trend_component, ddof=1))

            if var_nonstationary > 1e-10:
                snr = var_stationary / var_nonstationary
            else:
                snr = float("inf")

            if snr == float("inf") or snr > 2.0:
                snr_tier           = "STRONG"
                snr_interpretation = (
                    "The spread's mean-reverting signal dominates its trend drift. "
                    "High confidence in pair tradability."
                )
            elif snr >= 1.0:
                snr_tier           = "MODERATE"
                snr_interpretation = (
                    "Meaningful mean reversion present but non-trivial trend risk remains. "
                    "Trade with position discipline."
                )
            else:
                snr_tier           = "WEAK"
                snr_interpretation = (
                    "Nonstationary drift dominates the mean-reverting signal. "
                    "Low confidence in pair tradability — heightened skepticism warranted."
                )

            snr_display = f"{snr:.4f}" if snr != float("inf") else "99.9999"

            # ── Mean drift flag ───────────────────────────────────────────────
            rolling_mean_series  = spread.rolling(window=effective_window).mean().dropna()
            full_sample_mean     = float(spread.mean())
            full_sample_std      = float(spread.std(ddof=1))
            latest_rolling_mean  = float(rolling_mean_series.iloc[-1])

            if full_sample_std > 1e-10:
                drift_z = abs(latest_rolling_mean - full_sample_mean) / full_sample_std
            else:
                drift_z = 0.0

            mean_drift_flag    = drift_z > 1.5
            mean_drift_display = "TRUE" if mean_drift_flag else "FALSE"
            mean_drift_detail  = (
                f"rolling_mean({effective_window}d)={latest_rolling_mean:.4f}, "
                f"full_sample_mean={full_sample_mean:.4f}, "
                f"deviation={drift_z:.2f}σ"
            )

            # ── Rolling correlation ───────────────────────────────────────────
            signal_idx = log_prices_signal.index
            close1 = df1["Close"].loc[df1["Close"].index.intersection(signal_idx)]
            close2 = df2["Close"].loc[close1.index]

            corr      = close1.rolling(effective_window).corr(close2)
            mean_corr = corr.mean()
            std_corr  = corr.std()
            threshold = mean_corr - (2 * std_corr)
            decoupled = corr[corr < threshold].dropna()

            # ── Episode-onset detection ───────────────────────────────────────
            episodes = []
            if len(decoupled) > 0:
                corr_index_list = list(corr.index)
                corr_pos        = {label: i for i, label in enumerate(corr_index_list)}
                decoupled_labels = list(decoupled.index)
                decoupled_labels.sort(key=lambda lbl: corr_pos.get(lbl, 0))

                # Helper function to encapsulate episode calculation and eliminate repetition
                def finalize_episode(start_lbl, label_list):
                    ep_corrs = decoupled.loc[label_list]
                    worst_corr = ep_corrs.min()
                    # Safe standard deviation division guard included here once
                    worst_dev = (worst_corr - mean_corr) / max(std_corr, 1e-6)
                    return {
                        "onset":     str(start_lbl)[:10],
                        "duration":  len(label_list),
                        "worst_corr": worst_corr,
                        "worst_dev":  worst_dev,
                    }

                episode_start  = decoupled_labels[0]
                episode_labels = [decoupled_labels[0]]

                for prev_lbl, curr_lbl in zip(decoupled_labels[:-1], decoupled_labels[1:]):
                    pos_gap = corr_pos.get(curr_lbl, 0) - corr_pos.get(prev_lbl, 0)
                    if pos_gap <= 1:
                        episode_labels.append(curr_lbl)
                    else:
                        # Call the helper inside the loop
                        episodes.append(finalize_episode(episode_start, episode_labels))
                        episode_start  = curr_lbl
                        episode_labels = [curr_lbl]

                # Call the helper one final time for the trailing episode block
                episodes.append(finalize_episode(episode_start, episode_labels))

            # ── Build report ──────────────────────────────────────────────────
            train_start  = log_prices_train.index[0]
            train_end    = log_prices_train.index[-1]
            signal_start = log_prices_signal.index[0]
            signal_end   = log_prices_signal.index[-1]

            report  = "=== CORRELATION DECAY REPORT ===\n"
            report += f"Pair: {ticker1} vs {ticker2} (window={effective_window} days)\n"
            report += f"Lookback: {self.lookback_years} year(s)\n"
            report += f"Training window (Johansen):  {train_start} to {train_end} ({TRAIN_WINDOW} days)\n"
            report += f"Signal window (z-score):     {signal_start} to {signal_end} ({len(log_prices_signal)} days)\n\n"

            if len(log_prices_signal) < 60:
                report += (
                    f"WARNING: Signal window has only {len(log_prices_signal)} rows after "
                    f"reserving 250 days for Johansen training. "
                    f"Consider increasing lookback_years to 2 or 3 for reliable z-score estimates.\n\n"
                )
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

            # Report all three CI levels for Johansen — conservative multi-lag result
            report += "Johansen cointegration (multi-lag conservative):\n"
            report += f"  Lag traces — k=1: {trace_by_k[1]:.4f}  k=2: {trace_by_k[2]:.4f}  k=3: {trace_by_k[3]:.4f}\n"
            report += f"  Conservative lag selected: k={conservative_k} (lowest trace = {trace_stat:.4f})\n"
            report += f"  90% CI critical value: {crit_val_90:.4f}  — {'PASS' if is_cointegrated_90 else 'FAIL'}\n"
            report += f"  95% CI critical value: {crit_val_95:.4f}  — {'PASS' if is_cointegrated_95 else 'FAIL'}\n"
            report += f"  99% CI critical value: {crit_val_99:.4f}  — {'PASS' if is_cointegrated_99 else 'FAIL'}\n"
            # Gate 1 uses composition-size-adjusted threshold
            _gate1_result = _gate1_pass()
            report += f"  Composition size: {n_pairs} pair(s) tested\n"
            report += f"  Adjusted gate threshold: {_gate1_ci_label} CI\n"
            report += (
                f"  Primary gate ({_gate1_ci_label}): "
                f"{'YES — STRUCTURAL TETHER CONFIRMED' if _gate1_result else 'NO — TETHER FAILS ADJUSTED THRESHOLD'}\n"
            )
            if is_cointegrated_95 and not _gate1_result:
                report += (
                    f"  NOTE: Pair passes standard 95% CI but fails the "
                    f"{_gate1_ci_label} threshold required for a composition "
                    f"of {n_pairs} pairs. Recommend treating as MONITOR rather "
                    f"than ACTIVE. Would pass with n_pairs ≤ 10.\n"
                )
            if not is_cointegrated_95:
                report += (
                    "  WARNING: Pair is NOT cointegrated at 95% CI (conservative k). Rolling correlation "
                    "patterns may not reflect a durable structural relationship.\n"
                )
            report += "\n"

            report += "=== PAIR SCORE ===\n"
            report += f"pair_score (SNR): {snr_display}\n"
            report += f"pair_score_tier:  {snr_tier}\n"
            report += f"Interpretation:   {snr_interpretation}\n\n"

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
