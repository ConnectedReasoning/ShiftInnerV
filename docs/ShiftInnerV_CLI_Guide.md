# ShiftInnerV — CLI Usage Guide

> *"The signal is the structural tension; the noise is the price."*

This guide covers every script you can invoke from the command line, organized by operational role. All scripts load environment from `~/.shiftinnerv_env` unless noted. The system runs entirely locally — no cloud strategy leakage.

---

## Environment Setup

All scripts read from `~/.shiftinnerv_env`. Critical keys:

| Variable | Used By | Default |
|---|---|---|
| `DATA_DIR` | All scripts | `/Users/manuel/projects/github/ShiftInnerV/data` |
| `TIINGO_KEY` | `monitor.py`, `dossier.py`, audit scripts | *(required for price data)* |
| `ANTHROPIC_API_KEY` | `summarize.py` | *(required for AI summaries)* |
| `REPORT_DIR` | `dossier.py`, `summarize.py`, `sentinel.py` | `$\/reports` |
| `PRICE_DATA_STALENESS_HOURS` | `main.py` | `26` |
| `CREWAI_TELEMETRY_OPT_OUT` | `main.py`, `agents.py` | Set to `true` — mandatory |

---

## Core Pipeline Scripts

### `main.py` — Single Composition Audit

The primary entry point. Runs the full two-agent CrewAI pipeline (Quant Scout + Signal Mathematician) on one pairs composition file, applies all gate logic, enforces composition concentration limits, records verdicts to the trial ledger, and writes dossier reports.

**Called by:** `sentinel.py`, `run_all.py`. Can also be run directly.

```bash
# Run on the default pairs.yaml
python main.py

# Run on a specific composition file
python main.py --pairs compositions/energy_pairs.yaml

# Skip the data staleness check (development only)
python main.py --pairs my_pairs.yaml --staleness-hours 999
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `--pairs` | path | `./pairs.yaml` | Path to a composition yaml file |
| `--staleness-hours` | int | 26 (or `PRICE_DATA_STALENESS_HOURS` env) | Data freshness threshold in hours. Aborts with exit code 1 if any ticker's CSV is older than this. Use `999` to disable during development. |

**Exit codes:** `0` = clean run or monitoring-only (CRISIS regime); `1` = data staleness abort or fatal error.

**Output files written to `REPORT_DIR`:**
- `verdict_<TICKER1>_<TICKER2>_<timestamp>.md` — Signal Mathematician full verdict
- `dossier_<TICKER1>_<TICKER2>_<timestamp>.md` — Fundamental + news dossier (ACTIVE pairs only)

---

### `sentinel.py` — Scheduled Orchestrator

Single-run orchestrator designed for `launchd`. Runs once, does its work, exits. Manages a lock file to prevent overlapping runs. On startup: checks market regime (VIX + SPY correlation), runs `monitor.py` to scan compositions, processes new anomaly yamls through `main.py`, and optionally runs the promoted composition.

**Called by:** `launchd` (see `launchd/` directory). Can also be run manually.

```bash
# Monitor + process new anomalies only (evening run)
python sentinel.py

# Also run the promoted composition (morning run, 07:00)
python sentinel.py --promoted

# Print config and exit without doing anything
python sentinel.py --dry-run
```

| Flag | Description |
|---|---|
| `--promoted` | After anomaly processing, runs `promote.py` to refresh the focused composition, then runs `main.py` on it |
| `--dry-run` | Prints path config and flag values, then exits immediately |

**Lock file:** `$DATA_DIRsentinel.lock` — prevents launchd overlap. If it exists on startup, sentinel exits immediately (previous run still in progress).

**Log file:** `$DATA_DIR/sentinel.log`

**launchd schedules (from `launchd/` plists):**
- `07:00` → `python sentinel.py --promoted`
- `19:00` → `python sentinel.py`

---

### `monitor.py` — Layer 1 Lightweight Watcher

No LLM, no agents. Runs rolling correlation and Johansen cointegration across compositions, logs anomalies to `anomalies.db`, and writes anomaly yaml files that `sentinel.py` then feeds into `main.py`. Can also run continuously or screen a single file.

```bash
# Run once across all compositions and exit
python monitor.py

# Run continuously, every 30 minutes
python monitor.py --loop

# Run continuously at a custom interval (seconds)
python monitor.py --loop --interval 900

# Screen a single composition file (statistics only, no yaml output)
python monitor.py --screen compositions/energy_pairs.yaml

# Parallel screening with multiple workers
python monitor.py --screen compositions/energy_pairs.yaml --workers 8

# Print today's anomaly log and exit
python monitor.py --summary

# Suppress all output except anomalies
python monitor.py --quiet

# Filter screen results (e.g. show only ACTIVE)
python monitor.py --screen pairs.yaml --filter ACTIVE

# Set minimum composite score threshold
python monitor.py --screen pairs.yaml --min-score 2.0

# Show suspicious pairs (near-threshold, watch list)
python monitor.py --screen pairs.yaml --show-suspicious

# Limit output to top N pairs by score
python monitor.py --screen pairs.yaml --top 20

# Use custom compositions directory
python monitor.py --compositions ~/my_compositions/
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `--loop` | bool | false | Run continuously rather than once |
| `--interval` | int | 1800 | Loop interval in seconds |
| `--summary` | bool | false | Print today's anomaly summary and exit |
| `--screen` | path | — | Screen a specific yaml file, write results to `anomalies.db` screening table |
| `--compositions` | path | `./compositions` | Directory to scan for yaml files |
| `--quiet` | bool | false | Only print anomalies, suppress info output |
| `--workers` | int | 1 | Parallel workers for `--screen` mode |
| `--top` | int | — | Limit output to top N pairs |
| `--filter` | string | — | Filter results by verdict label (e.g. `ACTIVE`) |
| `--min-score` | float | — | Minimum composite score threshold |
| `--show-suspicious` | bool | false | Include near-threshold pairs in output |

**Databases written:**
- `$DATA_DIR/anomalies.db` — `anomalies` table (live anomaly events) and `screening` table (full composition scans)

---

### `run_all.py` — Batch Runner

Discovers all `*.yaml` files in `compositions/` and runs `main.py` on each sequentially. Designed for overnight batch runs. Handles non-zero exit codes gracefully — logs and continues rather than aborting the batch.

```bash
# Run all compositions in ./compositions/
python run_all.py

# Use a custom compositions directory
python run_all.py --compositions ~/my_compositions/

# List composition files without running them
python run_all.py --dry-run
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `--compositions` | path | `./compositions` | Directory to scan for yaml files |
| `--dry-run` | bool | false | List files and exit without running |

---

### `promote.py` — Composition Promoter

Reads the screening table from `anomalies.db`, applies quality filters (cointegration, half-life bounds, SNR floor, episode count, recency), deduplicates by ticker pair, and writes a focused composition yaml ready for `main.py`. This is the bridge from mass screening to targeted agent analysis.

```bash
# Promote top candidates with defaults
python promote.py

# Stricter filters
python promote.py --top 20 --min-snr 2.0 --max-hl 60

# Custom output path
python promote.py --output compositions/focus_today.yaml

# Preview candidates without writing
python promote.py --dry-run

# Suppress output (used by sentinel.py)
python promote.py --quiet
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `--db` | path | `$DATA_DIR/anomalies.db` | Source database |
| `--output` | path | `compositions/promoted_<date>.yaml` | Output composition yaml |
| `--top` | int | 25 | Maximum pairs in output |
| `--max-hl` | float | 120 | Half-life ceiling in days (tradeable horizon) |
| `--min-hl` | float | 5 | Half-life floor in days (below this = noise) |
| `--min-snr` | float | 1.5 | Minimum SNR score |
| `--snr-cap` | float | 500 | Maximum SNR (excludes near-flat spread artifacts) |
| `--min-episodes` | int | 2 | Minimum decoupling episodes required |
| `--lookback` | int | 7 | Only consider screening rows from last N days |
| `--dry-run` | bool | false | Print candidates without writing yaml |
| `--quiet` | bool | false | Suppress console output |

---

### `dossier.py` — On-Demand Pair Deep Dive

Generates a deep-dive report for a specific pair: Tiingo price history, yfinance fundamentals (P/E, leverage, cash flow, earnings date), recent news headlines, and SEC EDGAR filings (8-K, 10-Q). Called automatically from `main.py` for ACTIVE verdicts; can also be run manually on any pair.

```bash
# Print dossier to terminal
python dossier.py AAPL MSFT

# Custom lookback window
python dossier.py AAPL MSFT --lookback 90

# Save as markdown to REPORT_DIR
python dossier.py AAPL MSFT --save

# Save without terminal output
python dossier.py AAPL MSFT --save --quiet
```

| Argument | Type | Required | Description |
|---|---|---|---|
| `ticker1` | positional | yes | First ticker symbol |
| `ticker2` | positional | yes | Second ticker symbol |
| `--lookback` | int | no (default: 90) | Days of price history to analyse |
| `--save` | bool | no | Write markdown report to `REPORT_DIR` |
| `--quiet` | bool | no | Suppress terminal output (use with `--save`) |

**Output filename:** `dossier_<TICKER1>_<TICKER2>_<timestamp>.md`

---

### `summarize.py` — AI Run Summarizer

Collects dossier and verdict reports from the latest sentinel run, submits to the Claude API, and returns a ranked trade summary with executive summary, top setups, and skip list. Saves output to `$DATA_DIR/summaries/`.

```bash
# Summarize the latest run (default: look back 120 minutes)
python summarize.py

# Look back further
python summarize.py --since 300

# Limit pairs included in the prompt
python summarize.py --top 5

# Preview prompt without calling the API
python summarize.py --dry-run

# Suppress console output (save only)
python summarize.py --quiet
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `--report-dir` | path | `REPORT_DIR` env var | Directory containing verdict/dossier files |
| `--since` | int | 120 | Look back N minutes for recent report files |
| `--top` | int | 10 | Max ACTIVE pairs to include in the prompt |
| `--dry-run` | bool | false | Print prompt without calling the Claude API |
| `--quiet` | bool | false | Suppress console output |

**Requires:** `ANTHROPIC_API_KEY` in `~/.shiftinnerv_env`

---

## Universe & Composition Tools

### `generate_pairs.py` — Pair Generator

Generates composition yaml files from `universe.yaml` for screening. Supports random sampling, single-category, cross-category, or full-universe pair generation. Output files are tagged as screening files — run through `monitor.py --screen` before promoting.

```bash
# 50 random pairs
python generate_pairs.py --random 50

# 100 random pairs, custom output path
python generate_pairs.py --random 100 --output compositions/tier3_random.yaml

# All pairs within a category
python generate_pairs.py --category semiconductors

# Cross-category pairs (miners vs semiconductors)
python generate_pairs.py --cross miners semiconductors

# Cross-category: energy vs currencies
python generate_pairs.py --cross energy currencies

# All possible pairs (warning: very large)
python generate_pairs.py --all

# List available categories
python generate_pairs.py --list-categories

# Custom lookback year in the output yaml
python generate_pairs.py --random 50 --lookback 3
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `--universe` | path | `./universe.yaml` | Source universe file |
| `--output` | path | auto-named in `compositions/` | Output yaml path |
| `--random` | int | — | Generate N random pairs from full universe |
| `--category` | string | — | All pairs within a single category |
| `--cross` | string string | — | Cross-category pairs (two category names) |
| `--all` | bool | false | All possible pairs |
| `--lookback` | int | 1 | Lookback years written into output yaml (choices: 1, 3, 5) |
| `--workers` | int | 1 | Parallel workers for generation |
| `--list-categories` | bool | false | Print available categories and exit |

---

### `scripts/build_liquid_universe.py` — S&P 500 Universe Builder

Builds an intra-sector (or all-pairs) composition yaml from the S&P 500 universe. Filters by average daily volume. Downloads price history via yfinance. Tier 2 adds sub-industry pairs on top of sector pairs.

```bash
# Intra-sector pairs only (default, ~8-10K pairs)
python scripts/build_liquid_universe.py

# All combinations (~123K pairs, very large)
python scripts/build_liquid_universe.py --all-pairs

# Check data download status
python scripts/build_liquid_universe.py --check

# Generate yaml from already-downloaded data
python scripts/build_liquid_universe.py --generate-only

# Download only, skip yaml generation
python scripts/build_liquid_universe.py --download-only

# Add sub-industry pairs (tier 2)
python scripts/build_liquid_universe.py --tier 2

# Custom ADV filter (default: $50M)
python scripts/build_liquid_universe.py --adv 100

# Custom lookback for ADV calculation (years)
python scripts/build_liquid_universe.py --lookback 5

# Force re-download even if data exists
python scripts/build_liquid_universe.py --force

# Custom output path
python scripts/build_liquid_universe.py --output compositions/sp500_intra.yaml
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `--tier` | int (1 or 2) | 1 | 1 = sector pairs; 2 = sector + sub-industry pairs |
| `--adv` | float | 50.0 | Minimum average daily volume in $M |
| `--lookback` | int | 3 | Years of history for ADV calculation |
| `--all-pairs` | bool | false | Generate all ticker combinations (ignores sector grouping) |
| `--generate-only` | bool | false | Skip download, regenerate yaml from existing data |
| `--download-only` | bool | false | Download data only, skip yaml generation |
| `--check` | bool | false | Print data download status and exit |
| `--force` | bool | false | Re-download even if data already exists |
| `--output` | path | auto-named | Output yaml path |

---

## Analysis & Validation Scripts

### `scripts/audit_active_verdicts.py` — Retrospective P&L Audit

Fetches recent ACTIVE verdicts from `anomalies.db`, reconstructs the spread and hedge ratio for each, and computes what actual P&L would have been with realistic costs deducted. This is the primary validation test: does the statistical edge survive execution? (Council Roadmap Item 10)

```bash
python scripts/audit_active_verdicts.py

# Custom database path
python scripts/audit_active_verdicts.py --db /path/to/anomalies.db

# Detailed per-trade diagnostics
python scripts/audit_active_verdicts.py --verbose

# Custom output report path
python scripts/audit_active_verdicts.py --output reports/audit.md

# Audit more verdicts (default: 10)
python scripts/audit_active_verdicts.py --n 20
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `--db` | path | `$DATA_DIR/anomalies.db` | Source database |
| `--output` | path | `audit_active_verdicts_report.md` | Output markdown report |
| `--n` | int | 10 | Number of recent ACTIVE verdicts to audit |
| `--verbose` | bool | false | Print detailed per-trade diagnostics |

**Requires:** `TIINGO_KEY` for price history reconstruction.

---

### `scripts/compute_dsr.py` — Deflated Sharpe Ratio

After 50+ closed trials, computes the Deflated Sharpe Ratio (López de Prado, Chapter 6) to detect whether the observed edge is real or the result of multiple hypothesis testing / overfitting. (Council Roadmap Item 14)

```bash
python scripts/compute_dsr.py

python scripts/compute_dsr.py --db /path/to/trial_ledger.db
python scripts/compute_dsr.py --output reports/dsr.md

# Lower trial count threshold for early testing
python scripts/compute_dsr.py --min-trials 30

# Run even below minimum trial count
python scripts/compute_dsr.py --force

# Block size for subsample correlation
python scripts/compute_dsr.py --subsample 10
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `--db` | path | `$DATA_DIR/trial_ledger.db` | Source ledger database |
| `--output` | path | `dsr_report.md` | Output markdown report |
| `--min-trials` | int | 50 | Minimum closed trials required before running |
| `--subsample` | int | 5 | Block size for subsample correlation estimate |
| `--force` | bool | false | Run even if below minimum trial count |

---

### `scripts/lookback_sensitivity.py` — Lookback Window Sensitivity

Runs `analyze_pair()` at four lookback windows (0.5y, 1y, 2y, 3y) for every pair in all active compositions. Classifies each pair as ROBUST or FRAGILE based on stability of cointegration, half-life, and SNR across windows. (Council Roadmap Item 9)

```bash
python scripts/lookback_sensitivity.py

python scripts/lookback_sensitivity.py --compositions compositions/
python scripts/lookback_sensitivity.py --workers 4
python scripts/lookback_sensitivity.py --output reports/lookback.md

# Run only a single composition
python scripts/lookback_sensitivity.py --composition composition_b_defense
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `--compositions` | path | `./compositions` | Compositions directory |
| `--output` | path | `lookback_sensitivity_report.md` | Output markdown report |
| `--csv` | path | `lookback_sensitivity.csv` | Machine-readable CSV output |
| `--workers` | int | 4 | Parallel workers (each pair runs 4 analyses — expensive) |
| `--composition` | string | — | Run only a single named composition file (no extension) |

---

### `scripts/optimize_exit_threshold.py` — Exit Z-Score Optimizer

Tests exit thresholds of [0.0, 0.25, 0.5, 1.0] on the trial ledger. For each closed trade, reconstructs the spread from Tiingo price history and simulates what P&L and hold-time each threshold would have produced. Stratified by half-life bin. Recommends whether to change the current z=0.5 exit. (Council Roadmap Item 16)

```bash
python scripts/optimize_exit_threshold.py

python scripts/optimize_exit_threshold.py --db /path/to/trial_ledger.db
python scripts/optimize_exit_threshold.py --output reports/exit_opt.md
python scripts/optimize_exit_threshold.py --min-trades 10
python scripts/optimize_exit_threshold.py --verbose
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `--db` | path | `$DATA_DIR/trial_ledger.db` | Source ledger database |
| `--output` | path | `optimize_exit_threshold_report.md` | Output markdown report |
| `--min-trades` | int | 20 | Minimum simulated trades required for a firm recommendation |
| `--verbose` | bool | false | Print per-trade diagnostics |

**Requires:** `TIINGO_KEY` for price history reconstruction.

---

### `scripts/threshold_sensitivity.py` — Gate Threshold Sensitivity

Reads the `anomalies.db` screening table and runs Ornstein-Uhlenbeck simulations to validate or challenge each hardcoded gate threshold. Produces a markdown report documenting evidence-based vs assumed thresholds. (Council Roadmap Item 2)

```bash
python scripts/threshold_sensitivity.py

python scripts/threshold_sensitivity.py --db /path/to/anomalies.db
python scripts/threshold_sensitivity.py --output reports/thresholds.md
python scripts/threshold_sensitivity.py --sims 20000
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `--db` | path | `$DATA_DIR/anomalies.db` | Source database |
| `--output` | path | `threshold_sensitivity_report.md` | Output markdown report |
| `--sims` | int | 5000 | Number of OU simulations per threshold combination |

---

### `scripts/measure_llm_malformation.py` — LLM Malformation Rate

Reads `llm_calls.log` and computes the malformed output rate across all agents. Run after 100+ pairs to get a statistically meaningful measurement. Exit code 1 if malformation rate exceeds 5% — signals that trading should be halted. (Council Roadmap Item 4)

```bash
python scripts/measure_llm_malformation.py

python scripts/measure_llm_malformation.py --logfile /path/to/llm_calls.log
python scripts/measure_llm_malformation.py --output report.txt

# Filter to a specific agent
python scripts/measure_llm_malformation.py --agent "Lead Quantitative Scout"
```

| Flag | Type | Default | Description |
|---|---|---|---|
| `--logfile` | path | `$DATA_DIR/llm_calls.log` | Input log file |
| `--output` | path | `llm_malformation_report.txt` | Output report file |
| `--agent` | string | — | Filter analysis to a specific agent name |

**Exit codes:** `0` = malformation rate < 5% (acceptable); `1` = malformation rate ≥ 5% (halt trading).

---

## Exploratory Script

### `scripts/exploratory/data_sanity_audit.py` — Data Sanity Audit

Spot-checks the macro signal basket (commodities, currencies, bonds, logistics, critical materials, tech) to verify price data availability and sanity. No CLI flags — edit the `MACRO_TICKERS` dict at the top of the file and run directly.

```bash
python scripts/exploratory/data_sanity_audit.py
```

---

## Output File Naming Conventions

Most scripts write markdown reports. The naming patterns are consistent:

| Pattern | Written by | Location |
|---|---|---|
| `verdict_<T1>_<T2>_<timestamp>.md` | `main.py` | `REPORT_DIR` |
| `dossier_<T1>_<T2>_<timestamp>.md` | `dossier.py` / `main.py` | `REPORT_DIR` |
| `summary_<timestamp>.md` | `summarize.py` | `$DATA_DIR/summaries/` |
| `audit_active_verdicts_report.md` | `scripts/audit_active_verdicts.py` | project root |
| `dsr_report.md` | `scripts/compute_dsr.py` | project root |
| `lookback_sensitivity_report.md` | `scripts/lookback_sensitivity.py` | project root |
| `lookback_sensitivity.csv` | `scripts/lookback_sensitivity.py` | project root |
| `optimize_exit_threshold_report.md` | `scripts/optimize_exit_threshold.py` | project root |
| `threshold_sensitivity_report.md` | `scripts/threshold_sensitivity.py` | project root |
| `llm_malformation_report.txt` | `scripts/measure_llm_malformation.py` | project root |
| `promoted_<date>.yaml` | `promote.py` | `compositions/` |

---

## Typical Workflows

### Daily Automated (via launchd)
```
07:00  sentinel.py --promoted    # full morning run
19:00  sentinel.py               # evening anomaly scan only
```

### Manual Full Screen + Promote
```bash
python monitor.py --screen compositions/energy_pairs.yaml --workers 4
python promote.py --top 20 --min-snr 2.0
python main.py --pairs compositions/promoted_<date>.yaml
python summarize.py --since 60
```

### On-Demand Pair Dive
```bash
python dossier.py XOM CVX --lookback 90 --save
```

### Generate and Screen New Pairs
```bash
python generate_pairs.py --cross energy financials --output compositions/energy_fin.yaml
python monitor.py --screen compositions/energy_fin.yaml --workers 8
python promote.py --lookback 1
```

### Validation Run
```bash
python scripts/audit_active_verdicts.py --n 20 --verbose
python scripts/compute_dsr.py --force
python scripts/lookback_sensitivity.py --workers 4
```

---

*ShiftInnerV — Sovereign statistical arbitrage. Local silicon only.*
