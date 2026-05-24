# Report Enhancement — Quick Reference Card

## 7-Step Enhancement Framework

### 1. Add Purpose Statements
**Every section needs:** Why does this section exist? What decision does it inform?

```markdown
## Market Regime

**Purpose:** Classify current market stress level. Determines position sizing risk 
and which trades are allowed to initiate.
```

### 2. Define Every Metric (Use Tables)

| Instead Of | Add |
|-----------|-----|
| VIX: 16.7 | VIX: 16.7 — Volatility Index. <20=calm, 20-30=elevated, >30=stress |
| Position Size: 1.0x | Position Size: 1.0x — Risk adjustment. 1.0x=full, 0.5x=half, 0.25x=quarter |

### 3. Show Interpretation

| Metric | Value | Interpretation |
|--------|-------|-----------------|
| Correlation | 0.75 | Strong (move together) |
| Correlation | 0.45 | Moderate (some co-movement) |
| Correlation | 0.15 | Weak (independent) |

### 4. Add Context-Aware Actions

**Instead of:** "HOLD"

**Say:** 
```
⏸ HOLD & WAIT — No anomalies detected, no open positions. 
Market is stable (NORMAL, VIX 16.7). Await next screening cycle.
```

### 5. List Everything Explicitly

| Instead | Better |
|---------|--------|
| "etc." | Complete list of all 24 tickers |
| "and others" | All 5 top pairs shown |
| "various verdicts" | ACTIVE=0, MONITOR=0, REJECT=0 |

### 6. Add Scenario Descriptions

```markdown
## Scenario: NORMAL Regime (VIX < 20)
- Report shows: Low volatility, stable correlations
- Meaning: Safe market conditions, full position sizing allowed
- Action: Initiate new trades, standard position sizes

## Scenario: CRISIS Regime (VIX ≥ 40)
- Report shows: High volatility, correlation breakdowns
- Meaning: Severe market stress, liquidation risks
- Action: HALT new entries, monitor existing positions closely
```

### 7. Create Glossary

```markdown
## 📚 Key Terms

- **SNR:** Signal-to-Noise Ratio. Strength of signal. >1.0 acceptable, >2.0 strong.
- **Cointegration:** Two prices that move together with stationary spread.
- **Anomaly:** Unusual behavior (correlation breakdown, signal collapse).
- **Half-life:** Time for diverging pair to revert to mean.
```

---

## Before/After Examples

### Example 1: Market Metrics

**Before:**
```
MARKET REGIME
  State: NORMAL
  VIX: 16.7
  Position Size: 1.0x
```

**After:**
```markdown
## 📊 Market Regime

| Signal | Value | Definition |
|--------|-------|-----------|
| VIX | 16.7 | Volatility Index (S&P 500). <20=calm, 20-30=elevated, >30=stress. |
| Position Multiplier | 1.00x | Risk adjustment. 1.0x=full, 0.5x=half, 0.25x=quarter. |

> ✓ **Conditions stable — full position sizing active**
> Market volatility is low. Safe to initiate new trades.
```

### Example 2: Screening Results

**Before:**
```
SCREENING RESULTS
  Pairs screened: 100
  Anomalies: 0
```

**After:**
```markdown
## 📋 Screening Results

**Purpose:** Evaluate each pair against cointegration tests and SNR thresholds. 
Identify statistically sound trade candidates.

- **Pairs screened:** 100 candidate pairs
- **Anomalies detected:** 0 pairs flagged for further investigation

> **Anomaly:** A pair showing unusual behavior (correlation breakdown, signal 
> deterioration, mean-reversion failure). Flagged for agent analysis.
```

### Example 3: Verdicts

**Before:**
```
VERDICTS
  ACTIVE: 0
  MONITOR: 0
  REJECT: 0
```

**After:**
```markdown
## ⚡ Agent Verdicts

**Purpose:** Classify anomalies into actionable trading decisions.

- **ACTIVE:** `0` — Ready to trade (enter new position)
- **MONITOR:** `0` — Watch closely (may resolve soon)
- **REJECT:** `0` — Broken signal (skip until next cycle)
```

### Example 4: Top Pairs List

**Before:**
```
Top pairs: EURUSD/GBPUSD, USDJPY/AUDJPY, EURGBP/EURJPY, ...
```

**After:**
```markdown
| Pair | Score | Correlation | Interpretation |
|------|-------|-------------|-----------------|
| EURUSD/GBPUSD | 2.45 | 0.782 | Strong (move together) |
| USDJPY/AUDJPY | 2.31 | 0.695 | Moderate (co-movement) |
| EURGBP/EURJPY | 2.18 | 0.651 | Moderate (co-movement) |
```

### Example 5: Action Recommendations

**Before:**
```
ACTION: HOLD
```

**After:** (Context-aware)
```markdown
## ⚡ Recommended Action

**⏸ HOLD & WAIT**
> No anomalies detected, no open positions. Market is stable (NORMAL, VIX 16.7). 
> Await next screening cycle.
```

---

## Checklist

For each section of your report:

- [ ] **Purpose Statement** — Why does this section exist?
- [ ] **Definitions** — What does each metric mean?
- [ ] **Range/Interpretation** — What values mean low/medium/high?
- [ ] **Context** — How does this affect trading decisions?
- [ ] **Scenarios** — Different recommendations for different market states?
- [ ] **Complete Lists** — All pairs, tickers, signals shown (no "etc.")?
- [ ] **Clear Action** — What should the reader do?

---

## Common Enhancements by Report Type

### Daily Briefing
- [ ] Market regime table with definitions
- [ ] Top signals with correlation interpretation
- [ ] Verdict counts with meanings
- [ ] Scenario-based action recommendations
- [ ] Key metrics glossary

### Weekly Portfolio Report
- [ ] Open positions with regime-adjusted sizing
- [ ] Performance metrics with ranges (good/bad/warning)
- [ ] Risk metrics with thresholds
- [ ] Scenario analysis (what if VIX rises?)
- [ ] Recommended next steps by condition

### Screening Report
- [ ] Screening method explanation
- [ ] Results table with interpretation columns
- [ ] Rejected pairs with reasons
- [ ] Top candidates with scores + rankings
- [ ] Recommendation for which to investigate

### Risk Report
- [ ] Risk metrics with thresholds
- [ ] Exposure summary (what's at risk?)
- [ ] Stress scenarios (what if market moves 5%? 10%?)
- [ ] Margin requirements
- [ ] Recommended hedges/rebalancing

---

## Writing Tips

### Use Plain Language
- ❌ "Johansen cointegration statistic exceeds critical value"
- ✅ "The two prices move together (cointegration confirmed)"

### Show Ranges
- ❌ "Correlation 0.75"
- ✅ "Correlation 0.75 (strong; range 0-1, >0.5 is strong)"

### Explain Thresholds
- ❌ "SNR > 1.0"
- ✅ "SNR > 1.0 (acceptable signal strength; >2.0 is strong)"

### Use Emoji for Quick Scanning
- 📊 Market Regime
- 🎯 Pair Sourcing
- ⚡ Verdicts
- 📈 Position Status
- 🛑 Alert (crisis, halt)
- ✓ Good (normal, conditions stable)

### Make Recommendations Actionable
- ❌ "Monitor the situation"
- ✅ "Watch EURUSD/GBPUSD over next 24-48 hours. If correlation falls below 0.6, escalate to REJECT."

---

## Apply To Any Project

This framework works for:
- ✅ Trading briefings
- ✅ Portfolio reports
- ✅ Screening results
- ✅ Risk dashboards
- ✅ Backtest summaries
- ✅ AI agent decision logs
- ✅ Alert/anomaly reports
- ✅ Performance summaries
- ✅ Regulatory/compliance reports
- ✅ Stakeholder briefings

---

## One Final Rule

**A person reading this report for the first time should:**
1. Understand what happened (market state, results, verdicts)
2. Know what to do next (action recommendation)
3. Be able to explain it to someone else (clear language)
4. Not need to ask clarifying questions (complete definitions)

If any of those fail, enhance that section.
