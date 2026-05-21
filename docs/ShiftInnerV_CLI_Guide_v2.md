# ShiftInnerV CLI Guide

**Last updated:** 2026-05-21  
**Status:** Post-reorganization (all Council Roadmap items complete)

---

## Quick Start

```bash
# 1. Generate pairs from universe
python shiftinnerv/pipelines/generate_pairs.py --random 100 --lookback 3

# 2. Run sentinel (automated orchestration)
python sentinel.py

# 3. Check verdicts
sqlite3 ~/Projects/ShiftInnerV_Data/trial_ledger.db "SELECT * FROM trial_ledger WHERE is_closed=0"
```

---

## Core Commands

### 1. **sentinel.py** — Orchestrator (use this for daily runs)

**Purpose:** Single command that runs the full pipeline: regime detection → monitor → position revalidation → promote → agents → verdicts.

```bash
# Standard run (processes new anomalies only)
python sentinel.py

# Morning run (refresh + process promoted composition)
python sentinel.py --promoted

# Dry run (check configuration without running)
python sentinel.py --dry-run
```

**What it does:**
- Checks market regime (VIX + SPY correlation)
- Runs `monitor.py` to screen anomalies
- Revalidates open positions for SNR decay / mean drift
- Processes new anomaly files through `main.py` (agents)
- Records ACTIVE verdicts to trial ledger
- Aborts cleanly if CRISIS regime detected

**Scheduling:** Configure via `launchd` (macOS) or `cron` (Linux) to run automatically.

---

### 2. **generate_pairs.py** — Pair Generation

**Purpose:** Create composition YAML files from the universe for screening.

```bash
# List available categories
python shiftinnerv/pipelines/generate_pairs.py --list-categories

# Random N pairs from full universe
python shiftinnerv/pipelines/generate_pairs.py --random 100 --lookback 3

# All pairs within a single category
python shiftinnerv/pipelines/generate_pairs.py --category semiconductors --lookback 3

# Cross-category pairs (e.g., energy vs tech)
python shiftinnerv/pipelines/generate_pairs.py --cross energy semiconductors --lookback 3

# All possible pairs (warning: very large)
python shiftinnerv/pipelines/generate_pairs.py --all --lookback 1

# Custom output location
python shiftinnerv/pipelines/generate_pairs.py --random 50 --output compositions/my_pairs.yaml
```

**Options:**
- `--universe PATH` — Path to universe.yaml (default: `./universe.yaml`)
- `--output PATH` — Output file path (default: auto-named in `compositions/`)
- `--random N` — Generate N random pairs
- `--category NAME` — All pairs within a category
- `--cross CAT1 CAT2` — Cross-category pairs
- `--all` — All possible pairs
- `--lookback {1,3,5}` — Lookback years (default: 1)
- `--list-categories` — Show available categories

**Output:** Creates `compositions/random_*.yaml` or `compositions/category_*.yaml`

---

### 3. **monitor.py** — Layer 1 Screening

**Purpose:** Fast, deterministic screening. No LLM. Runs Johansen cointegration, SNR, half-life, episodes. Flags anomalies.

```bash
# Screen a single composition file
python shiftinnerv/pipelines/monitor.py --screen compositions/composition_b_defense.yaml

# Screen with parallel workers
python shiftinnerv/pipelines/monitor.py --screen compositions/tier1_intra_sector.yaml --workers 10

# Screen all compositions in a directory
python shiftinnerv/pipelines/monitor.py --compositions compositions/ --workers 10

# Quiet mode (suppress terminal output)
python shiftinnerv/pipelines/monitor.py --screen compositions/my_pairs.yaml --quiet

# Loop mode (continuous monitoring, for dev/testing)
python shiftinnerv/pipelines/monitor.py --loop --interval 1800
```

**Options:**
- `--screen PATH` — Screen a single composition file
- `--compositions DIR` — Screen all .yaml files in directory
- `--workers N` — Parallel workers (default: 1)
- `--quiet` — Suppress output
- `--loop` — Continuous monitoring mode
- `--interval SEC` — Loop interval in seconds (default: 1800)
- `--summary` — Print summary statistics only

**Output:**
- Terminal: Ranked pair table (PRIME / STRONG / SOLID / WATCH / NOISE)
- Database: `anomalies.db` with screening results
- Files: `compositions/anomalies/anomaly_*.yaml` for flagged divergences

---

### 4. **promote.py** — Filter Best Candidates

**Purpose:** Take screening results from `anomalies.db`, apply quality filters, output a focused composition for agent processing.

```bash
# Promote top 25 pairs from last 7 days
python promote.py

# Custom filters
python promote.py --top 50 --min-snr 2.0 --lookback-days 14

# Custom output location
python promote.py --output compositions/promoted_custom.yaml

# Use different anomalies database
python promote.py --db /path/to/custom_anomalies.db
```

**Options:**
- `--db PATH` — Path to anomalies.db (default: `$DATA_DIR/anomalies.db`)
- `--output PATH` — Output yaml (default: `compositions/promoted_<timestamp>.yaml`)
- `--top N` — Max pairs to include (default: 25)
- `--min-snr FLOAT` — Minimum SNR threshold (default: 1.5)
- `--max-hl DAYS` — Max half-life in days (default: 120)
- `--min-hl DAYS` — Min half-life in days (default: 5)
- `--min-eps N` — Minimum episodes (default: 2)
- `--lookback-days N` — Only consider screening rows from last N days (default: 7)

**Output:** `compositions/promoted_*.yaml` with top-ranked pairs

---

### 5. **main.py** — Agent Pipeline (Layer 2)

**Purpose:** Run CrewAI agents (Quant Scout + Signal Mathematician) on pairs. Applies 7-gate framework. Records verdicts.

```bash
# Process a specific composition
python main.py --pairs compositions/promoted_2026-05-21.yaml

# Process with staleness check (abort if data > 2 days old)
python main.py --pairs compositions/my_pairs.yaml --staleness-days 2

# Single composition mode
python main.py --composition compositions/composition_b_defense.yaml
```

**Options:**
- `--pairs PATH` — Path to pairs yaml file
- `--composition PATH` — Process all pairs in a composition (single run mode)
- `--staleness-days N` — Abort if CSV data older than N days (default: 3)

**What it does:**
1. Loads pair definitions from yaml
2. For each pair, runs 2-agent pipeline:
   - **Quant Scout**: Correlation decay report + Johansen results
   - **Signal Mathematician**: 7-gate framework evaluation
3. Parses verdict (ACTIVE / MONITOR / MONITOR-NEAR / REJECT)
4. Records ACTIVE verdicts to `trial_ledger.db`
5. Writes markdown reports to `$REPORT_DIR/`
6. (Optional) Runs dossier.py for ACTIVE pairs
7. (Optional) Runs summarize.py for AI summary of all ACTIVE verdicts

**Output:**
- Database: `trial_ledger.db` with ACTIVE verdicts
- Reports: `$REPORT_DIR/verdict_<pair>_<date>.md`
- Dossiers: `$REPORT_DIR/dossier_<pair>_<date>.md` (ACTIVE only)

---

### 6. **dossier.py** — Deep-Dive Reports

**Purpose:** Generate detailed fundamental + technical analysis for a pair. Uses Tiingo API for news/fundamentals.

```bash
# Generate dossier for a specific pair
python shiftinnerv/pipelines/dossier.py AAPL MSFT

# Custom lookback window
python shiftinnerv/pipelines/dossier.py LMT NOC --lookback 180

# Custom output directory
python shiftinnerv/pipelines/dossier.py RTX XLI --output /tmp/reports
```

**Options:**
- `ticker1` — First ticker (required)
- `ticker2` — Second ticker (required)
- `--lookback DAYS` — Days of history to analyze (default: 90)
- `--output DIR` — Report directory (default: `$REPORT_DIR`)

**Output:** `dossier_<ticker1>_<ticker2>_<date>.md`

---

### 7. **summarize.py** — AI Run Summary

**Purpose:** Create an AI-powered synthesis of all verdicts from the most recent run. Uses Claude API.

```bash
# Summarize latest run
python shiftinnerv/pipelines/summarize.py

# Summarize specific date range
python shiftinnerv/pipelines/summarize.py --days 7

# Custom report directory
python shiftinnerv/pipelines/summarize.py --report-dir /custom/path

# Custom output location
python shiftinnerv/pipelines/summarize.py --output summaries/my_summary.md
```

**Options:**
- `--report-dir PATH` — Directory with verdict markdown files (default: `$REPORT_DIR`)
- `--days N` — Look back N days for verdicts (default: 1)
- `--output PATH` — Output file (default: `$DATA_DIR/summaries/summary_<date>.md`)

**Requires:** `ANTHROPIC_API_KEY` in `~/.shiftinnerv_env`

**Output:** `summaries/summary_<date>.md` with ranked ACTIVE pairs + synthesis

---

### 8. **run_all.py** — Batch Runner

**Purpose:** Process all composition files in a directory sequentially (useful for bulk screening).

```bash
# Process all compositions
python run_all.py

# Custom compositions directory
python run_all.py --compositions /custom/compositions

# Dry run (list files without running)
python run_all.py --dry-run
```

**Options:**
- `--compositions DIR` — Compositions directory (default: `./compositions`)
- `--dry-run` — List files without processing

**What it does:** Runs `main.py --composition <file>` for each .yaml in the directory.

---

## Environment Variables

Configure in `~/.shiftinnerv_env`:

```bash
# Data storage location
DATA_STORAGE_PATH=~/Projects/ShiftInnerV_Data

# Report output directory
REPORT_DIR=~/Projects/ShiftInnerV_Data/reports

# API keys
TIINGO_KEY=your_tiingo_api_key_here
ANTHROPIC_API_KEY=your_anthropic_api_key_here

# Ollama endpoint (for local LLM agents)
OLLAMA_HOST=http://localhost:11434

# Item 8: Regime detection thresholds (optional overrides)
VIX_ELEVATED=20.0
VIX_HIGH_STRESS=30.0
VIX_CRISIS=40.0
PAIR_SPY_CORR_THRESHOLD=0.70

# Item 5: Data staleness (optional override)
DATA_STALENESS_DAYS=3
```

---

## Database Locations

All databases are stored in `$DATA_STORAGE_PATH` (default: `~/Projects/ShiftInnerV_Data/`):

- **`anomalies.db`** — Screening results from monitor.py
  - Table: `screening_results` (pair, timestamp, score, SNR, half-life, etc.)
  
- **`trial_ledger.db`** — Verdict tracking + P&L
  - Table: `trial_ledger` (verdict metadata, entry/exit prices, P&L)
  - Table: `position_revalidations` (Item 13: SNR decay / mean drift checks)

Query examples:

```bash
# Show all ACTIVE verdicts
sqlite3 ~/Projects/ShiftInnerV_Data/trial_ledger.db \
  "SELECT ticker1, ticker2, entry_z_verdict, snr, half_life FROM trial_ledger WHERE is_closed=0"

# Show screening results from last 7 days
sqlite3 ~/Projects/ShiftInnerV_Data/anomalies.db \
  "SELECT ticker1, ticker2, score, snr, half_life FROM screening_results 
   WHERE timestamp >= datetime('now', '-7 days') ORDER BY score DESC LIMIT 20"
```

---

## Typical Workflow

### Daily Run (Automated via sentinel)

```bash
# Morning run (processes promoted + new anomalies)
python sentinel.py --promoted
```

**What happens:**
1. Market regime check (VIX + SPY correlation)
2. If CRISIS → monitoring mode only, exit cleanly
3. Run monitor.py (screen for new anomalies)
4. Position revalidation (check open trades for SNR decay)
5. Process new anomaly files through main.py agents
6. Record ACTIVE verdicts to trial ledger
7. Done

### Ad-Hoc Pair Generation + Screening

```bash
# 1. Generate 100 random pairs with 3-year lookback
python shiftinnerv/pipelines/generate_pairs.py --random 100 --lookback 3

# 2. Screen them immediately (parallel mode)
python shiftinnerv/pipelines/monitor.py \
  --screen compositions/random_20260521_1234.yaml \
  --workers 10

# 3. Check results
sqlite3 ~/Projects/ShiftInnerV_Data/anomalies.db \
  "SELECT ticker1, ticker2, score, snr, half_life FROM screening_results 
   ORDER BY timestamp DESC, score DESC LIMIT 20"

# 4. Promote top candidates
python promote.py --top 25

# 5. Process promoted pairs through agents
python main.py --pairs compositions/promoted_20260521_1456.yaml
```

### Deep-Dive on Specific Pair

```bash
# Generate dossier
python shiftinnerv/pipelines/dossier.py LMT NOC --lookback 180

# View report
cat ~/Projects/ShiftInnerV_Data/reports/dossier_LMT_NOC_20260521.md
```

---

## Scheduling

### macOS (launchd)

```bash
# Install scheduled job
cp launchd/com.shiftinnerv.sentinel.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.shiftinnerv.sentinel.plist

# Check status
launchctl list | grep shiftinnerv

# View logs
tail -f ~/Projects/ShiftInnerV_Data/sentinel.log
```

### Linux (cron)

```bash
# Edit crontab
crontab -e

# Add entry (runs every 4 hours)
0 */4 * * * cd /path/to/ShiftInnerV && /path/to/python sentinel.py --promoted >> ~/Projects/ShiftInnerV_Data/cron.log 2>&1
```

---

## Flags Reference

### Common Flags Across Scripts

- `--help` — Show usage and exit
- `--dry-run` — Print config without executing (sentinel, run_all)
- `--quiet` — Suppress terminal output (monitor)
- `--workers N` — Parallel execution (monitor, generate_pairs)
- `--output PATH` — Custom output location (most scripts)

### Filtering Flags (promote.py)

- `--top N` — Limit to top N pairs
- `--min-snr FLOAT` — Minimum SNR threshold
- `--max-hl DAYS` — Maximum half-life
- `--min-hl DAYS` — Minimum half-life
- `--min-eps N` — Minimum episodes
- `--lookback-days N` — Recency filter

### Pair Generation Modes (generate_pairs.py)

- `--random N` — Random pairs
- `--category NAME` — Intra-category pairs
- `--cross CAT1 CAT2` — Cross-category pairs
- `--all` — All combinations (large)

---

## Testing

```bash
# Run all tests
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_trial_ledger.py -v

# Run with coverage
python -m pytest tests/ --cov=shiftinnerv --cov-report=html
```

---

## Troubleshooting

### "No module named 'shiftinnerv'"

Install in development mode:
```bash
pip install -e .
```

### "Data staleness check failed"

CSV files are older than threshold. Force refresh:
```bash
# Option 1: Download fresh data
python -c "from shiftinnerv.services.data_manager import ensure_data; ensure_data(['SPY', 'AAPL'])"

# Option 2: Skip staleness check
python main.py --pairs compositions/my_pairs.yaml --staleness-days 999
```

### "Ollama connection refused"

Start Ollama server:
```bash
ollama serve
```

### "CRISIS regime detected — exiting"

System detected VIX > 40 or high SPY correlation among open positions. This is intentional — no new trades in crisis mode. Check:
```bash
python sentinel.py --dry-run
```

---

## Next Steps

1. **Set up scheduling** — Configure launchd/cron for automated runs
2. **Generate initial universe** — Create composition files for sectors you want to track
3. **Run first screening** — Process compositions through monitor.py
4. **Review verdicts** — Check trial_ledger.db for ACTIVE pairs
5. **Build execution harness** — (Future work) Connect verdicts to broker API

For detailed methodology, see `ShiftInnerV_User_Manual.md`.

---

**End of CLI Guide**
