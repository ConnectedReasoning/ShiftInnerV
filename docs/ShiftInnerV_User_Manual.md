# ShiftInnerV — User Manual

> *"The signal is the structural tension; the noise is the price."*

---

## Table of Contents

1. [What Is ShiftInnerV?](#1-what-is-shiftinnerv)
2. [The Philosophy](#2-the-philosophy)
3. [The Theoretical Foundation](#3-the-theoretical-foundation)
4. [System Architecture](#4-system-architecture)
5. [Core Concepts](#5-core-concepts)
6. [The Signal Pipeline](#6-the-signal-pipeline)
7. [The Gate Framework](#7-the-gate-framework)
8. [Verdict Types](#8-verdict-types)
9. [Features](#9-features)
10. [Composition Files](#10-composition-files)
11. [Databases](#11-databases)
12. [Operating Modes](#12-operating-modes)
13. [Usage Tips & Operational Wisdom](#13-usage-tips--operational-wisdom)
14. [Safety Protocols](#14-safety-protocols)
15. [Execution Phases](#15-execution-phases)

---

## 1. What Is ShiftInnerV?

ShiftInnerV is a **local, sovereign, statistical pairs trading system** built to identify when the market's internal pricing mechanisms have temporarily failed to reflect structural economic reality.

It is not a prediction engine. It does not forecast prices. It does not trade on news sentiment or macroeconomic narratives.

It is an **auditor** — a continuous quantitative observer that monitors the spread between historically linked asset pairs and flags when that spread has decoupled beyond statistical noise, creating a mean-reversion opportunity.

The system runs entirely on local hardware (Mac Studio M1). No strategy data reaches any cloud provider. No execution is autonomous. Every trade decision requires a human to review the evidence and act.

---

## 2. The Philosophy

### Forensic Skepticism

ShiftInnerV exists to automate the same style of forensic skepticism that uncovered the Archer Daniels Midland price-fixing scheme in the 1990s — the discipline of asking not "what is the price?" but "why is this price here, and is that structurally justified?"

In a market increasingly dominated by passive index flows and institutional whales, pure price-following strategies erode. What remains persistent is **structural tension**: the force-based relationships between assets that share real-world supply chains, input costs, or regulatory environments. When those tethers stretch beyond normal variance, the market has mispriced something. ShiftInnerV is designed to find that.

### Three Founding Principles

**Privacy is Alpha.** Financial strategy is intellectual property. The moment a strategy is transmitted to a third-party cloud provider, it is no longer exclusively yours. The system is architecturally incapable of leaking strategy — `OPENAI_API_KEY` is hardcoded to `"NA"`, and `CREWAI_TELEMETRY_OPT_OUT=true` is mandatory. Local Ollama inference handles all agent reasoning.

**Deterministic Focus.** The system prefers rule-based, auditable logic over black-box models. The gate framework produces explicit numerical justifications. Every verdict can be traced to specific statistics. The Signal Mathematician agent reasons from Johansen trace statistics, half-lives, SNR scores, and episode counts — not from vibes or narrative pattern-matching.

**Cumulative Micro-Bets.** The goal is not to find home runs. It is the consistent scavenging of small, structurally justified efficiency leaks. Pairs trading by nature produces many small wins and limits loss via the mean-reversion mechanism itself. The edge compounds quietly.

### What ShiftInnerV Is Not

- It is not a high-frequency trading system
- It is not a momentum or trend-following system
- It is not an autonomous trading bot — no order is ever placed without human approval
- It is not a sentiment or news trading system (dossier data informs context, not the quantitative verdict)
- It does not use cloud LLMs for any trading-relevant computation

---

## 3. The Theoretical Foundation

The system's quantitative framework is grounded in three texts, each contributing a specific analytical layer:

### Vidyamurthy — *Pairs Trading* (2004)

The primary theoretical source. Vidyamurthy's Arbitrage Pricing Theory (APT) framework defines pairs trading not as "two stocks that move together" but as securities sharing **common factor exposure** — the same underlying economic drivers. When two securities have common factor loading, their spread is theoretically stationary. Deviation from that stationary mean is the signal.

ShiftInnerV inherits Vidyamurthy's SNR concept directly: **SNR is the ratio of stationary variance to nonstationary variance in the spread**. A high SNR means the signal (mean-reverting component) dominates the noise (random walk component). An SNR below 1.0 means noise dominates — there is no tradeable signal.

### Isichenko — *Quantitative Portfolio Management* (2021)

Provides the operational framework for translating a statistical signal into trade parameters. Most critically: **half-life as the forecast horizon**. The Ornstein-Uhlenbeck mean-reversion speed parameter directly determines how long you should expect to hold a position before the spread returns to mean. A half-life of 10 days means a position should resolve in roughly 10 trading days. A half-life of 90 days is technically valid but ties up capital for months with substantial regime risk.

Isichenko also provides the position sizing principle: **size inversely with half-life**. Short half-life pairs justify larger positions; slow pairs should be sized down to account for the extended exposure window.

### López de Prado — *Advances in Financial Machine Learning* (2018)

Supplies the exit framework and the intellectual honesty test. The **triple barrier method** formalizes when to take profit (z-score returns to 0.5σ), when to cut loss (spread reaches 3.0σ), and how to think about time-based exits.

Critically, López de Prado provides the **Deflated Sharpe Ratio** — the mathematical correction for multiple hypothesis testing. If you screen 1,000 pairs and pick the best performers, the observed edge may be pure selection bias. After 50+ closed trades, `compute_dsr.py` applies this test to determine whether the system's edge is statistically real or a consequence of data mining.

---

## 4. System Architecture

```
universe.yaml / compositions/*.yaml
          │
          ▼
    monitor.py  ◄────────────── Layer 1: No LLM
    (rolling correlation, Johansen screening,
     anomaly detection, cost modeling)
          │
          │  anomaly yamls + screening DB
          ▼
    promote.py  ◄────────────── Filter + prioritize
    (SNR floor, half-life bounds, deduplication)
          │
          │  promoted composition yaml
          ▼
     main.py   ◄────────────── Layer 2: LLM Agents (local Ollama)
    (CrewAI two-agent pipeline)
          │
     ┌────┴────┐
     │         │
  Scout    Signal Mathematician
  (runs    (applies gate framework,
  tool,    computes entry/exit,
  returns  writes verdict)
  raw
  data)
          │
          │  verdict + dossier reports
          ▼
    trial_ledger.db  ◄────── Performance tracking
    sentinel.py      ◄────── Scheduled orchestration
    summarize.py     ◄────── AI-powered run summary
```

**Layer 1 (monitor.py)** is fast, deterministic, and runs without LLM overhead. It screens hundreds of pairs in minutes using rolling correlation and Johansen cointegration tests. It writes to SQLite and flags candidates.

**Layer 2 (main.py / agents)** is the expensive layer — local LLM inference via Ollama. Only pairs that pass Layer 1 screening reach this stage. The two-agent pipeline applies the full gate framework, computes entry/exit parameters, and writes detailed reports.

**sentinel.py** is the scheduler glue that runs both layers on the launchd schedule, manages lock files to prevent overlaps, checks market regime before doing anything, and handles the promoted → main.py handoff.

---

## 5. Core Concepts

### Pairs Trading

The premise: if two assets share a fundamental economic tether — same input costs, same regulatory environment, same customer base — their prices should move together over time. When they diverge, one of two things is true: (a) something has permanently changed the relationship (new information), or (b) the market has temporarily mispriced the spread. The system is designed to identify (b) and ignore (a).

A trade on a diverging pair means going **long the underperformer, short the outperformer** — betting on convergence rather than direction.

### Spread

The spread is `price1 - β × price2`, where `β` (the hedge ratio) is estimated by OLS regression. The hedge ratio ensures the position is market-neutral: you are not taking a directional view on either ticker, only on the relationship between them.

### Cointegration

Two price series are cointegrated if their spread is stationary — i.e., it mean-reverts. ShiftInnerV uses the **Johansen test**, which is more robust than the simpler Engle-Granger test for evaluating cointegration at different confidence levels (90%, 95%, 99%). Cointegration at 95% CI is the minimum threshold for an ACTIVE verdict. Failing to pass Johansen is an immediate REJECT.

### Half-Life

The Ornstein-Uhlenbeck half-life measures how quickly the spread reverts to its mean. Derived from the autoregressive coefficient of the spread. A half-life of 20 days means the spread is expected to close half the gap every 20 days. ShiftInnerV gates: minimum 5 days (below this, the pair is too noisy), maximum 120 days (above this, holding through regime changes is impractical).

### SNR (Signal-to-Noise Ratio)

The variance of the stationary (mean-reverting) component divided by the variance of the nonstationary (random walk) component. SNR > 1.0 is the minimum gate. Higher is better. STRONG tier: SNR > 3.0. MODERATE: 1.5–3.0. WEAK: 1.0–1.5.

### Episodes

The number of distinct historical decoupling events where the pair has diverged and subsequently converged. One episode might be lucky. Two or more establishes that the mean-reversion pattern has **recurred** — that the tether is real and self-correcting.

### Z-Score

The current spread deviation expressed in standard deviations from the historical mean. Entry signal: |z| ≥ 2.0. Exit target: |z| ≤ 0.5. Stop-loss: |z| ≥ 3.0.

---

## 6. The Signal Pipeline

A pair travels through the following stages before reaching a human:

```
1. COMPOSITION  →  Defined in a yaml file with economic rationale
2. SCREENING    →  monitor.py runs cointegration + SNR checks (fast, no LLM)
3. ANOMALY      →  Rolling correlation flags a live decoupling event
4. PROMOTION    →  promote.py applies quality filters, writes focused yaml
5. AGENT AUDIT  →  main.py runs the full two-agent gate framework
6. VERDICT      →  REJECT / MONITOR / MONITOR-NEAR / ACTIVE
7. DOSSIER      →  dossier.py adds fundamental + news context (ACTIVE only)
8. SUMMARY      →  summarize.py ranks all ACTIVE pairs for the run
9. HUMAN REVIEW →  You decide whether to act
```

Only pairs that survive every stage in sequence reach step 9. The system is designed to be maximally skeptical — most pairs are rejected. A clean ACTIVE verdict on a strong pair is a relatively rare event, which is the point.

---

## 7. The Gate Framework

The Signal Mathematician applies six gates in strict sequence. Failure at any gate produces an immediate REJECT (or MONITOR) — later gates are not evaluated.

| Gate | Test | Threshold | Fail Outcome |
|---|---|---|---|
| **Gate 1** | Johansen cointegration | 95% CI required | REJECT (90% CI only: MONITOR-NEAR) |
| **Gate 2** | Half-life range | 5–120 days; λ < 0 | REJECT |
| **Gate 3** | SNR score | ≥ 1.0 | REJECT |
| **Gate 4** | Episode count | ≥ 2 distinct episodes | MONITOR |
| **Gate 6** | Common factor exposure | Factor loading ≤ 0.3 | MONITOR (FACTOR_CONTAMINATED) |
| **Gate 7** | Net P&L after costs | > 25 bps | MARGINAL or UNPROFITABLE |

**Note on Gate 5:** Gates 1–4 all passing produces a provisional ACTIVE. Gate 6 (factor contamination check) can downgrade it. Gate 7 applies the cost model — if the expected net P&L is eaten by transaction costs, the setup is marked MARGINAL or UNPROFITABLE even if statistically clean.

**Why no Gate 5?** The numbering reflects the Council Roadmap development history. Gate 5 is the "all pass → ACTIVE" condition, not a separate test.

### The Cost Model (Gate 7)

ShiftInnerV models four cost components for every pair:

- **Bid-ask spread:** 4–20 bps per round trip depending on security type (liquid ETF to small cap)
- **Market impact:** slippage estimate based on position size relative to daily volume
- **Borrow cost:** annualized short borrow rate (50 bps for large cap, up to 500 bps for small cap)
- **Commission:** 1 bp per side

If costs exceed the expected gross P&L, the pair is MARGINAL or UNPROFITABLE regardless of statistical quality. A beautiful cointegration statistic means nothing if the edge is consumed by friction.

---

## 8. Verdict Types

| Verdict | Meaning | Action |
|---|---|---|
| **ACTIVE** | All gates pass, costs viable, live decoupling confirmed | Human reviews dossier, considers entry |
| **MONITOR** | Statistical quality sufficient but one gate soft-fails (e.g. only 1 episode, or factor-contaminated) | Watch for re-entry conditions |
| **MONITOR-NEAR** | Passes at 90% CI but not 95% CI — nearly cointegrated | Re-evaluate in 30 days or on shorter lookback window |
| **REJECT** | Hard gate failure (cointegration, half-life, or SNR) | No action — pair is not structurally sound |

**ACTIVE entry parameters computed automatically:**
- Entry z-score: **2.0σ** from spread mean
- Exit z-score: **0.5σ** (toward mean)
- Stop-loss: **3.0σ**
- Expected hold: approximately **1× half-life** in trading days
- Direction: SHORT the ticker above its expected value, LONG the one below

---

## 9. Features

### Market Regime Detection (Item 8)

Before every sentinel run, ShiftInnerV classifies the current market environment using VIX level and rolling pair-SPY correlation of open positions.

| Regime | VIX | Position Size Multiplier | Effect |
|---|---|---|---|
| NORMAL | < 20 | 1.0× | Full operation |
| ELEVATED | 20–30 | 0.5× | Half size on new entries |
| HIGH_STRESS | 30–40 | 0.25× | Quarter size, extra caution |
| CRISIS | ≥ 40 | 0.0× | **Hard halt** — monitoring only, no new entries |

In CRISIS regime, sentinel completes a monitoring-only run, logs the regime state, and exits cleanly. No new ACTIVE verdicts are processed. VIX data is cached for 1 hour to avoid repeated network calls within a run. If VIX is unavailable, the system defaults conservatively to ELEVATED.

### Position Revalidation (Item 13)

On every sentinel run, open positions are revalidated against current price data. The system recomputes SNR using the most recent 63 days and checks for mean drift against entry-time spread statistics.

| SNR | Drift | Decision |
|---|---|---|
| ≥ 1.0 | Any | HOLD |
| 0.7–1.0 | Any | MONITOR |
| < 0.7 | > 2σ drift | AUTO_CLOSE (flagged, pending execution integration) |
| < 0.7 | No significant drift | MONITOR |

AUTO_CLOSE decisions are recorded in the trial ledger but are not yet wired to an execution layer — they require human confirmation.

### Composition Concentration Monitor (Item 15)

Prevents over-concentration in any single sector or composition category. Hard limits are enforced: by default, a maximum of 2 simultaneous open positions per composition. The `commodity_equity_proxy` category has a tighter limit of 1 (higher cross-asset correlation risk).

When the limit is reached, new ACTIVE verdicts in that composition are automatically downgraded to MONITOR until an existing position closes. This is a circuit breaker that overrides the numerical gates.

### Data Staleness Guard (Item 5)

`main.py` checks the modification time of every price CSV before running. If any ticker's data is older than 26 hours (one trading day + overnight buffer), the run aborts with exit code 1. This prevents agent analysis on stale data — a silent but serious failure mode. The threshold is configurable via `PRICE_DATA_STALENESS_HOURS` env var or `--staleness-hours` flag.

### Common Factor Exposure Check (Gate 6)

Pairs that appear cointegrated may be driven by the same sector ETF rather than by a genuine bilateral tether. Gate 6 uses Johansen eigenvectors to measure factor loading against a category-appropriate proxy ETF (e.g., `XLE` for energy pairs, `SOXX` for semiconductor pairs). If the factor loading coefficient exceeds 0.3, the pair is labeled FACTOR_CONTAMINATED and downgraded to MONITOR.

This prevents false signals where two energy stocks "cointegrate" simply because oil prices moved them both — not because of any direct structural relationship.

### Lookback Sensitivity Analysis (Item 9)

A pair that only cointegrates at one specific lookback window (e.g., passes at 1 year but fails at 2 years) is fragile. `scripts/lookback_sensitivity.py` runs every pair in your compositions at four windows (0.5y, 1y, 2y, 3y) and classifies pairs as ROBUST (stable across windows) or FRAGILE (window-dependent). Fragile pairs should be sized down or avoided.

### Deflated Sharpe Ratio (Item 14)

After 50+ closed trades, `scripts/compute_dsr.py` applies the Deflated Sharpe Ratio correction to account for multiple hypothesis testing. If you screened 500 pairs and selected the 20 best performers, some of that observed edge is selection bias. The DSR adjusts for this statistically. A positive DSR confirms real edge. A negative DSR means the system needs recalibration.

### AI-Powered Run Summary

`summarize.py` calls the Claude API with all ACTIVE verdicts and dossiers from a run, returning a structured ranked summary: executive summary, top 3 setups with entry logic and risk flags, and a skip list with rejection reasons. This is the fastest path from "sentinel ran" to "here are the two setups worth looking at."

---

## 10. Composition Files

A composition yaml file is the input unit for the system. It defines the pairs to be evaluated, along with their economic rationale. The economic rationale is for human reference only — agents do not read it.

```yaml
pairs:
  - ticker1: REMX
    ticker2: SOXX
    label: "Rare Earth Miners vs Semiconductor ETF"
    relationship: >
      REEs are critical inputs for semiconductor manufacturing.
      Supply constraints propagate into chip production costs.
    lead: REMX
    lag_days: 30
    cointegrated: unknown
    lookback_years: 3
    sentinel_goals:
      - correlation_decay
    leading_indicators:
      - China rare earth export quota announcements
    notes: >
      June-July 2024 decoupling confirmed — China export controls.
      Use as benchmark for future episodes.
```

### Composition File Naming Convention

| Prefix | Type | Purpose |
|---|---|---|
| `composition_*.yaml` | Production | Tracked for concentration monitoring |
| `promoted_*.yaml` | Transient | Output of promote.py, used for a single run |
| `anomaly_*.yaml` | Auto-generated | Written by monitor.py for live decoupling events |
| *(anything else)* | Screening | Not tracked for concentration limits |

Only files matching `composition_*.yaml` are tracked for concentration limits. Promoted and anomaly files are transient — concentration monitoring against them would be misleading.

### The Universe File

`universe.yaml` is the master ticker list, organized into 18 categories: broad market, sectors, semiconductors, miners, energy, commodities, fixed income, volatility/macro, currencies, shipping, defense, financials, healthcare, AI/cloud, clean energy, real estate, agriculture, China/EM. `generate_pairs.py` reads this file to produce composition yamls for screening.

---

## 11. Databases

ShiftInnerV maintains two SQLite databases in `DATA_DIR`:

### `anomalies.db`

Written by `monitor.py`. Contains two tables:

**`anomalies`** — live decoupling events as they occur, with rolling correlation values, deviation magnitudes, and a flag for whether the CrewAI agents have processed this anomaly.

**`screening`** — results of full composition scans via `monitor.py --screen`. Contains cointegration statistics, half-life, SNR, episode count, and composite scores for every pair evaluated. This is the source for `promote.py`.

### `trial_ledger.db`

Written by `main.py` and `sentinel.py`. The performance record. Contains:

- Full statistical snapshot at verdict time (z-score, half-life, SNR, hedge ratio, spread stats)
- Entry and exit execution timestamps and prices (when manually populated)
- P&L fields (gross, net after costs, bps)
- Hold period, exit reason, open/closed status
- Market regime at entry time
- Composition label and concentration tracking

The ledger is the evidence base for `compute_dsr.py`, `audit_active_verdicts.py`, and `optimize_exit_threshold.py`. Without a populated ledger, those analyses cannot run.

---

## 12. Operating Modes

### Automated Mode (Normal Operation)

launchd runs sentinel on schedule. You review `summarize.py` output each morning.

```
07:00  sentinel.py --promoted    → full run with promote step
19:00  sentinel.py               → anomaly scan only
```

### Manual Screening Mode

Use when evaluating a new sector or building a new composition.

```bash
python generate_pairs.py --cross energy financials --output compositions/energy_fin.yaml
python monitor.py --screen compositions/energy_fin.yaml --workers 8
python promote.py --lookback 1 --min-snr 2.0
python main.py --pairs compositions/promoted_<date>.yaml
```

### On-Demand Pair Research

Use when a pair is flagged and you want deeper context before deciding.

```bash
python dossier.py XOM CVX --lookback 90 --save
```

### Development / Testing Mode

Skip the data staleness check during development:

```bash
python main.py --pairs test_pairs.yaml --staleness-hours 999
```

### Validation Mode

Run periodically to ensure the system's edge is real:

```bash
python scripts/audit_active_verdicts.py --n 20 --verbose
python scripts/compute_dsr.py --force
python scripts/lookback_sensitivity.py --workers 4
python scripts/threshold_sensitivity.py --sims 20000
```

---

## 13. Usage Tips & Operational Wisdom

### On Pair Selection

**Require a causal story, not just correlation.** Two stocks can have high rolling correlation for years and still be a bad pair — they might be correlated because they're both large-cap US equities, not because they share a specific economic tether. REMX vs SOXX is a good pair because there's a named supply chain link (rare earths → chips). The relationship should survive a simple sentence: "X leads Y because Z."

**Cross-sector pairs are higher risk.** Pairs within the same sector share macro drivers by definition; cross-sector pairs need a stronger fundamental justification to pass Gate 6. The composition concentration limit for `commodity_equity_proxy` (limit: 1) reflects this — commodity-equity pairs carry higher correlation regime risk.

**Check sensitivity before sizing.** Run `lookback_sensitivity.py` on new compositions before committing to them. A ROBUST pair — one that cointegrates at 1y, 2y, and 3y lookbacks — is a fundamentally more reliable signal than one that only passes at a specific window.

### On Verdicts

**MONITOR-NEAR is not a consolation prize.** A pair that passes at 90% CI but not 95% CI is in a waiting state. Set a calendar reminder to re-evaluate in 30 days or after a significant market move. Sometimes a MONITOR-NEAR pair converges to a clean 95% pass as more data accumulates.

**ACTIVE does not mean act.** The system surfaces candidates; it does not execute. An ACTIVE verdict means the quantitative gates are satisfied and there is a live decoupling. You still need to verify: (1) no pending earnings announcement for either ticker, (2) no thin liquidity around open/close, (3) the dossier doesn't reveal a fundamental regime change (e.g., one company was acquired). ACTIVE + clean dossier = strong setup.

**Trust Gate 7.** If a pair produces a MARGINAL or UNPROFITABLE verdict due to costs, respect it. The borrow cost on smaller or harder-to-borrow names can easily consume a spread move of 1–2σ. The cost model is conservative but realistic.

### On Position Management

**The half-life is your hold timer.** If a pair has a 25-day half-life and you've been in the trade for 40 days with no convergence, something has changed. Do not extend the trade indefinitely hoping for convergence — the statistical basis for the trade was defined at entry.

**Check position revalidation output each run.** If sentinel logs a MONITOR decision on an open position (SNR deteriorating), that is a warning to watch closely. An AUTO_CLOSE flag means the spread dynamics have materially changed — treat it as a strong signal to exit manually.

**Regime matters for sizing.** In ELEVATED regime (VIX 20–30), the system automatically halves position size multiplier. In HIGH_STRESS (VIX 30–40), it quarters it. These are not suggestions — they are the system's risk management speaking. Don't override them by manually sizing up.

### On the LLM Layer

**The agents are not the decision-makers.** The Quant Scout's only job is to run the correlation tool and return its output verbatim. The Signal Mathematician applies deterministic gate logic. The LLM is used for structured reasoning and formatting, not for creative judgment. If an agent output looks unusual, the `gate_evaluator.py` provides a fully deterministic backup path.

**Monitor malformation rates.** Run `measure_llm_malformation.py` periodically. If the malformation rate exceeds 5%, some agent outputs will have incorrect gate labels or missing values. The system will log warnings, but ACTIVE verdicts produced during high-malformation periods should be treated with extra skepticism. Exit code 1 from that script is a hard signal to pause trading.

**The local model matters.** The system is calibrated for `qwen2.5:14b` via Ollama. Switching models (e.g., to Llama 3 or a smaller variant) may increase malformation rates. If you change models, run a calibration batch through `measure_llm_malformation.py` before relying on verdicts.

### On Data

**Tiingo is the primary price source; yfinance is the backup.** Tiingo provides cleaner adjusted-close data. yfinance is used for fundamentals (P/E, leverage) in the dossier layer. Both require keys in `~/.shiftinnerv_env`.

**The staleness guard is a feature, not an obstacle.** Data that is 26+ hours old in a system that produces same-day trading signals is actively dangerous. If the staleness guard triggers, investigate why — it usually means the data update process has failed, which is a problem independent of any particular pair.

**Run the data sanity audit on new tickers.** Before adding a new ticker to any composition, verify that it has sufficient history (5+ years preferred, 1 year minimum) and reasonable data quality. `scripts/exploratory/data_sanity_audit.py` is the right tool for this, though you'll need to edit the ticker list directly.

### On the Databases

**Do not manually edit `trial_ledger.db`.** The composition concentration monitor and position revalidation both read from it. Inconsistent manual edits can cause concentration limits to fire incorrectly. If you need to make corrections, use targeted SQL with a backup.

**Back up `anomalies.db` and `trial_ledger.db` regularly.** These are the institutional memory of the system. The trial ledger in particular cannot be reconstructed from any other source once a trade is closed and the price window has passed.

**The `composition_label` field requires correct keys.** A known historical bug stored human-readable labels (e.g., "Rare Earth Miners vs Semiconductor ETF") instead of composition keys (e.g., "semiconductors") in earlier trial ledger rows. If you have pre-fix ledger data, check this field manually before relying on concentration monitoring statistics.

---

## 14. Safety Protocols

These are non-negotiable constraints, hardwired into the system or its operating procedures:

**No cloud strategy leakage.** `OPENAI_API_KEY=NA` is enforced. All LLM inference is local via Ollama. The only permitted cloud API call is `summarize.py` to the Anthropic API — which receives dossier and verdict reports, not raw price data or strategy logic.

**No autonomous execution.** The system produces signals and records verdicts. It never places orders. The execution gap between AUTO_CLOSE in the trial ledger and actual order submission is intentional — it is the human review checkpoint.

**Human-in-the-loop is mandatory.** Before any real-money trade, even $1, a human must review the ACTIVE verdict, the dossier, and the current regime state. The summarize.py output is the synthesis layer, but the decision belongs to you.

**Telemetry opt-out.** `CREWAI_TELEMETRY_OPT_OUT=true` must be set. CrewAI's default telemetry would transmit agent interaction data externally. With strategy privacy as a first principle, this is unacceptable.

**Lock file prevents overlap.** `sentinel.lock` ensures that a slow run (e.g., a composition with many pairs and slow Ollama inference) does not start a second instance when launchd fires again. If the lock file is stale due to a crash, remove it manually before the next run.

---

## 15. Execution Phases

ShiftInnerV is designed to scale into live trading gradually, with each phase requiring demonstrated evidence before proceeding:

### Phase 1 — Shadow / Paper Trading (Current)

Run the system for 30+ days without spending any capital. Observe ACTIVE verdicts, log hypothetical entries and exits manually in the trial ledger, and evaluate how the signals would have performed. Goal: accumulate 50+ closed trials for DSR computation. Validate that the edge survives transaction costs in backtested audit.

**Gate to advance:** Positive DSR, win rate > 50%, net P&L > costs across audit sample.

### Phase 2 — Cumulative Micro-Bets ($1 live trades)

Execute the smallest possible real trades to test operational plumbing: order routing, fee structure, borrow availability, tax lot tracking. The goal here is not P&L — it is proving that the execution layer works as expected and that no surprising friction exists.

**Gate to advance:** 20+ live micro-trades with no operational surprises. Fees match model. Borrow available on all shorted names at modeled rates.

### Phase 3 — Scaling

Increase position size in proportion to demonstrated edge. Use Isichenko's sizing guidance: scale inversely with half-life, and cap any single position at a fraction of daily volume to avoid self-impacting liquidity. Continue running DSR quarterly.

**Ongoing:** Position revalidation, concentration monitoring, and regime-based sizing adjustments remain active at all scales.

---

*ShiftInnerV — Sovereign statistical arbitrage. The signal is the structural tension; the noise is the price.*
