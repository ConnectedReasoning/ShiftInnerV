# ShiftInnerV — Council Roadmap
**The Medallion Council · May 2026**

A two-day advisory review of ShiftInnerV by eleven quant practitioners and three domain authors (Chan, Vidyamurthy, López de Prado), chaired by Warren Buffett. This document captures all action items organized into a four-phase roadmap from current shadow-trading state to live execution readiness.

---

## Overview

| Phase | Theme | Items | Gate |
|---|---|---|---|
| **Phase 1** | Statistical foundation fixes | 5 | Must complete before trusting any verdicts |
| **Phase 2** | Trading logic & position management | 5 | Can run in parallel with Phase 1 |
| **Phase 3** | Pipeline hardening & evidence building | 5 | Requires Phase 1 complete |
| **Phase 4** | Live execution readiness | 2 | Requires positive Deflated Sharpe from Phase 3 |

---

## Phase 1 — Statistical Foundation Fixes

> Correct the methodology before trusting any verdicts. Items 11 and 17 are silent errors that make every verdict look better than it is. Nothing else is worth building on a biased cointegration test.

### Item 11 — Separate Johansen estimation window from z-score window ⚠️ BLOCKER
**Source:** Ernest Chan (Day 2)

The Johansen eigenvector (hedge ratio) is currently estimated on the same window used to compute the spread z-score. This is a form of look-ahead contamination: the hedge ratio is fit to maximize stationarity over the training period, so the spread is by construction close to stationary over that same window. The z-score appears clean because the data was used to make it clean.

**Fix:** Use a fixed 250-trading-day training period for the Johansen eigenvector. Use a separate rolling window equal to the half-life for z-score calculation. These two windows must not overlap.

---

### Item 17 — Run Johansen across k=1, 2, 3 lags; use most conservative result ⚠️ BLOCKER
**Source:** Ernest Chan, Gaurav Vidyamurthy (Day 2)

The lag parameter `k=1` is a common default but it matters enormously for the resulting test statistic. If the underlying VECM has more complex lag structure — which ETF pairs often do, especially with sector exposures — running the test with a single lag produces a biased estimate of the cointegrating vector, which propagates into all downstream SNR calculations. A pair could show SNR of 1.8 when the correct value is closer to 0.9.

**Fix:** Run the Johansen test at k=1, k=2, and k=3. Use the most conservative (lowest) cointegration statistic result for gating decisions.

---

### Item 1 — Add multiple comparisons correction to cointegration screening ⚠️ BLOCKER
**Source:** Jim Simons (Day 1)

Running Johansen cointegration tests on roughly 100 tickers at the standard 95% confidence level yields approximately 5 false positives by chance alone from the multiple comparisons problem. The system currently has no Bonferroni correction or family-wise error rate adjustment. Some ACTIVE verdicts may be statistical ghosts.

**Fix:** Apply Bonferroni correction or Benjamini-Hochberg FDR adjustment to the cointegration p-values before applying the five-gate framework. Document the adjusted pass rate.

---

### Item 12 — Add residual common factor exposure diagnostic as Gate 6
**Source:** Gaurav Vidyamurthy (Day 2)

Within tightly defined sectors (defense ETFs, China EM proxies, bank pairs), all pairs share a dominant common factor — the sector itself. The Johansen test may report cointegration that is entirely driven by shared sector exposure rather than any idiosyncratic structural relationship. When the sector reprices sharply, all pairs in the composition simultaneously deviate, turning a supposedly diversified book into a leveraged sector bet.

**Fix:** After constructing the hedged portfolio, compute the residual common factor exposure. If the residual is large — if the paired portfolio still carries significant factor loadings — flag the pair as MONITOR regardless of Johansen result. Add this as Gate 6 in the five-gate framework.

---

### Item 2 — Empirically validate gate thresholds
**Source:** Cliff Asness, David Harding (Day 1)

The five gate threshold values — SNR floor of 1.0, half-life ceiling of 120 days, z-score entry of 2.0, minimum 2 episodes — appear to have been drawn from the source literature (Chan, Vidyamurthy) rather than derived empirically from the actual universe. These are someone else's edge applied to this universe with no guarantee they survive. Vidyamurthy specifically noted that an SNR floor of 1.0 means signal and noise are equal in variance at the trading horizon — a barely-tradeable threshold.

**Fix:** Run sensitivity analysis on each gate threshold using ±20% perturbations. Test on the actual historical data with walk-forward windows. Consider raising the SNR floor to 1.5–2.0. Document which thresholds are evidence-based vs. assumed.

---

## Phase 2 — Trading Logic & Position Management

> Make the signal-to-execution chain economically sound. Can run in parallel with Phase 1 once the window separation fix is in place.

### Item 3 — Build transaction cost model ⚠️ BLOCKER
**Source:** Edward Thorp, Peter Muller (Day 1)

The system optimizes for statistical purity and completely ignores whether there is anything left in P&L after the market takes its cut. No bid-ask spread model, no slippage estimate, no borrow cost for the short leg. For liquid large-cap pairs, the bid-ask alone can consume a meaningful fraction of expected mean-reversion profit on a 28-day half-life pair. The statistical edge in pairs trading is real but thin.

**Fix:** Build an explicit round-trip cost model: estimated bid-ask on both legs, borrow cost for the short leg (especially for ETFs), and market impact estimate based on average daily volume. Compute expected net P&L per trade before generating ACTIVE verdicts.

---

### Item 10 — Compute actual P&L on last 10 ACTIVE verdicts with real costs ⚠️ BLOCKER
**Source:** Peter Muller, Edward Thorp (Day 1)

Pull the ten most recent ACTIVE verdicts from the shadow trading history. Compute what the actual P&L would have been if traded — using real bid-ask midpoint prices on actual timestamps, not close prices, with realistic transaction costs deducted. This single calculation will tell you more about the viability of the system than any statistical test. Either the edge survives execution costs or it doesn't.

**Fix:** Build the retrospective P&L audit as a script. If edge is positive after costs, size it properly and proceed. If it is not, fix the methodology before going live.

---

### Item 16 — Review exit z-score threshold of 0.5
**Source:** Marcos López de Prado, Ernest Chan (Day 2)

The current exit threshold of z=0.5 covers roughly 75% of the expected mean-reversion move before exiting. López de Prado's triple-barrier analysis of synthetic mean-reverting processes shows that for pairs with genuine positive long-run equilibrium (which passing Johansen implies), optimal profit-taking is substantially higher. Chan's implementations exit at z=0 (the moving average), not at z=0.5. The 0.5 exit is overly conservative and will degrade the Sharpe ratio on genuinely strong pairs by leaving expected P&L on the table.

**Fix:** Test exit thresholds of 0.0, 0.25, 0.5, and 1.0 on the shadow P&L ledger. Consider making the exit threshold a function of the pair's half-life, informed by the López de Prado heat-map analysis for the specific (forecast, half-life, sigma) triple.

---

### Item 13 — Add per-run SNR revalidation for all open positions
**Source:** Gaurav Vidyamurthy (Day 2)

The system has no mechanism to re-evaluate open positions against updated statistics. A position entered under one statistical regime can remain open and unchallenged indefinitely until it converges or hits the stop-loss. Vidyamurthy's mean drift analysis is explicit: the mere passage of time in an unconverged spread represents increasing risk, as the variance of the nonstationary component grows linearly with the trading horizon. The SNR deteriorates as time passes.

**Fix:** On every sentinel run, for all currently open positions: recompute the rolling SNR using the current spread data. If SNR has fallen below the entry threshold (currently 1.0), flag the position for review. If mean drift is detected and SNR is below 0.7, trigger automatic close with a time-based stop log entry.

---

### Item 15 — Add sector concentration limit
**Source:** Gaurav Vidyamurthy, Peter Muller (Day 2)

If multiple pairs from the same composition category (e.g., three defense pairs) are simultaneously ACTIVE, that is not three independent bets — it may be one sector bet three times. A defense-sector event would hit all three simultaneously, creating correlated drawdown that the pair-level risk model doesn't capture.

**Fix:** Add a same-sector simultaneous open position counter per composition category. Set a configurable concentration limit (suggested default: 2 simultaneous opens per category). When the limit is reached, new verdicts in the same category are held as MONITOR regardless of gate scores until an existing position closes.

---

## Phase 3 — Pipeline Hardening & Evidence Building

> Make the system reliable and begin accumulating the statistical evidence that live trading requires. Requires Phase 1 complete.

### Item 4 — Measure LLM malformed output rate; build deterministic fallback ⚠️ BLOCKER
**Source:** David Shaw (Day 1), Marcos López de Prado (Day 2)

The `extract_report_text` function in `main.py` has a sophisticated JSON extraction fallback that recovers the report when the LLM wraps its output in malformed blobs. This fallback exists because the model sometimes fails to render the template correctly. The malformed output rate has never been measured. If it is above 5%, the system is silently miscategorizing pairs on a regular basis.

More fundamentally, López de Prado's point stands: the five-gate logic is fully deterministic given numerical inputs. A language model is the wrong tool for boolean gate evaluation. If a regulator asks why a position was entered, "the LLM said ACTIVE" is not a traceable answer.

**Fix:** (1) Instrument and log every LLM call outcome — success, fallback-recovery, or failure. (2) Build a deterministic Python gate evaluator as a parallel path: same inputs, same logic, outputs a structured verdict. (3) Use the deterministic path as the primary trading decision. Retain the LLM path for dossier narrative generation only.

---

### Item 5 — Add data staleness hard-abort ⚠️ BLOCKER
**Source:** Ken Griffin, Peter Muller (Day 1)

The current system logs a warning if data appears stale but does not abort. If Tiingo is down at the 7am run and yesterday's prices are silently used, the z-score computed is wrong — a pair that appears to be at z=2.1 entry threshold may actually be at z=0.8 based on current prices. The system can generate ACTIVE verdicts on stale data with no indication that anything is wrong.

**Fix:** Add explicit price staleness checks with hard aborts. If any price series in an active composition has not been updated within a configurable staleness window (suggested: 26 hours), abort the run entirely and write a STALE_DATA sentinel status to the log. Do not generate verdicts. Alert via the existing notification path.

---

### Item 14 — Build trial performance ledger ⚠️ BLOCKER
**Source:** Marcos López de Prado (Day 2)

There is currently no performance ledger. Verdicts are logged, dossiers are generated, but there is no record of: entry timestamp and price, exit timestamp and price, transaction costs, net P&L, and which composition and gate scores generated the verdict. Without this ledger, there is no statistical basis for claiming edge. López de Prado's Deflated Sharpe Ratio requires the variance of Sharpe ratios across trials — which requires the trials to be recorded.

**Fix:** On every ACTIVE verdict, write a trial record to a persistent ledger (suggested: SQLite or append-only CSV). Record: pair, verdict timestamp, entry z-score, entry prices, hedge ratio, half-life at entry, SNR at entry. On position close (convergence or stop), record: exit timestamp, exit prices, gross P&L, transaction cost estimate, net P&L. After 90 days and 50+ closed trials, compute the Deflated Sharpe Ratio. If DSR < 1.0, do not proceed to live trading.

---

### Item 9 — Run lookback window sensitivity analysis
**Source:** David Harding (Day 1)

The YAML compositions use a 1-year lookback for some pairs and a 3-year lookback for others. The theoretical basis for this bifurcation has not been documented or tested. A 1-year lookback will produce tighter half-life estimates that may not generalize out-of-sample. A 3-year lookback may include structural breaks that contaminate the cointegration test. Neither is obviously correct without empirical testing.

**Fix:** Run the full screening pipeline on the current universe with lookback windows of 6 months, 1 year, 2 years, and 3 years. Track pass rates, half-life distributions, and SNR distributions across windows. Document which pairs are stable across windows (robust) vs. window-sensitive (fragile). Prefer robust pairs for live trading.

---

### Item 8 — Add regime detection sensor
**Source:** Ray Dalio, David Harding (Day 1)

The system has no awareness of market regime. Pairs that cointegrate during low-volatility, trending-rates environments may decorrelate catastrophically during a credit event or rate shock. There is currently no mechanism that detects when the market environment has structurally changed and no automatic response.

**Fix:** Add a simple regime sensor that runs before each screening cycle. Suggested indicators: (1) VIX level — if above 30, flag all new ACTIVE verdicts as ELEVATED_REGIME and reduce suggested position size by 50%. (2) Rolling 20-day correlation of active pairs to SPY — if above 0.7 across more than half the active pairs, flag CORRELATION_REGIME. (3) At VIX above 40, halt new position entries entirely and require manual restart. Store regime state in the sentinel log.

---

## Phase 4 — Live Execution Readiness

> The minimum viable infrastructure for real capital. These are gates, not projects. Do not enter Phase 4 until Phase 3's trial ledger shows a positive Deflated Sharpe Ratio.

### Item 6 — Integrate broker API: paper mode first, then live 🔴 GATE TO LIVE
**Source:** Ken Griffin (Day 1)

There is currently no broker integration. The system generates verdicts but cannot execute them. Before any live dollar is deployed, the full order management cycle must be tested: entry on both legs at market open on the day of the ACTIVE verdict, continuous position tracking, exit on z-score convergence or stop-loss breach, position reconciliation against broker records.

**Fix:** (1) Select a broker API with Python support and good paper trading mode (Interactive Brokers TWS/ibapi or Alpaca are the standard choices for this type of system). (2) Build the execution module: translate ACTIVE verdict into paired market orders with the Johansen hedge ratio, submit, confirm fills, record actual fill prices in the trial ledger. (3) Run in paper mode for a minimum of 90 days, covering at least 20 complete trade cycles (entry + exit). (4) Build a daily position reconciliation check: system's expected open positions vs. broker's actual open positions. Alert on any discrepancy. (5) Only after 90 days of clean paper execution with positive net P&L, enable live mode with a hard capital limit.

---

### Item 7 — Build drawdown circuit breaker 🔴 GATE TO LIVE
**Source:** Ken Griffin, Ray Dalio (Day 1)

There is currently no automatic halt mechanism. Chan's *Algorithmic Trading* is explicit on this: for mean-reverting strategies, stop-losses must be set larger than the backtest maximum intraday drawdown to avoid triggering in-sample, yet still prevent catastrophic tail losses when cointegration breaks. The system as currently designed can accumulate losses across multiple ACTIVE positions simultaneously with no automatic response.

**Fix:** Implement a multi-level circuit breaker: (1) **Position level:** If any single pair's spread moves beyond the stop-loss z-score (currently 3.0), close both legs immediately and mark the pair as SUSPENDED for 30 days. (2) **Portfolio level:** If total open position P&L across all active pairs reaches -X% of allocated capital (suggested: -5%), halt all new entries and alert. (3) **System level:** If the portfolio-level threshold is breached twice in 30 days, the system requires manual password-protected restart. Log the full state at the time of halt for post-mortem analysis.

---

## Summary Table

| # | Item | Phase | Priority | Source |
|---|---|---|---|---|
| 11 | Separate Johansen estimation window from z-score window | 1 | Blocker | Chan |
| 17 | Run Johansen at k=1,2,3; use most conservative | 1 | Blocker | Chan, Vidyamurthy |
| 1 | Multiple comparisons correction | 1 | Blocker | Simons |
| 12 | Residual common factor exposure as Gate 6 | 1 | Blocker | Vidyamurthy |
| 2 | Empirically validate gate thresholds | 1 | Important | Asness, Harding |
| 3 | Build transaction cost model | 2 | Blocker | Thorp, Muller |
| 10 | Actual P&L on last 10 ACTIVE verdicts | 2 | Blocker | Muller, Thorp |
| 16 | Review exit z-score threshold of 0.5 | 2 | Important | López de Prado, Chan |
| 13 | Per-run SNR revalidation for open positions | 2 | Important | Vidyamurthy |
| 15 | Sector concentration limit | 2 | Important | Vidyamurthy, Muller |
| 4 | LLM malformed output rate + deterministic fallback | 3 | Blocker | Shaw, López de Prado |
| 5 | Data staleness hard-abort | 3 | Blocker | Griffin, Muller |
| 14 | Trial performance ledger + Deflated Sharpe | 3 | Blocker | López de Prado |
| 9 | Lookback window sensitivity analysis | 3 | Important | Harding |
| 8 | Regime detection sensor | 3 | Important | Dalio, Harding |
| 6 | Broker API integration — paper then live | 4 | Gate to live | Griffin |
| 7 | Drawdown circuit breaker | 4 | Gate to live | Griffin, Dalio |

---

## Sequencing Logic

**Start here:** Items 11 and 17 first. They are silent errors corrupting every verdict generated today. They take a day to fix and unblock everything else.

**Phase 1 and Phase 2 can overlap** once the window separation is done. Run the actual P&L audit (#10) and cost model (#3) as soon as possible — Muller's point was right. The market will answer the edge question faster than any statistical test.

**Phase 3** begins after Phase 1 is complete. The trial ledger (#14) should start accumulating on day one of Phase 3. Everything else in Phase 3 is hardening around it.

**Phase 4 is a gate, not a sprint.** Do not touch broker integration until the trial ledger shows a positive Deflated Sharpe Ratio across at least 50 closed trades. The system is doing the right things. Let the shadow evidence accumulate first.

---

## Council Attribution

**Day 1 participants:** Warren Buffett (Chair), Jim Simons, Edward Thorp, David Shaw, Cliff Asness, Peter Muller, Ray Dalio, Ken Griffin, John Overdeck & David Siegel, David Harding, Igor Tulchinsky

**Day 2 special guests:** Ernest Chan (*Algorithmic Trading*), Gaurav Vidyamurthy (*Pairs Trading*), Marcos López de Prado (*Advances in Financial Machine Learning*)

---

*Document generated from two-day advisory council transcripts · ShiftInnerV v1 · May 2026*
