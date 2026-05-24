# Report Enhancement Prompt — Apply to Any Trading/Quantitative Project

## Overview

This prompt guides enhancement of quantitative trading/analysis reports to include context, definitions, and actionable insights. Use this template to improve briefings, dashboards, and summaries across projects.

---

## The Prompt

### For Your Code/Documentation Team:

```
We need to enhance our [REPORT_TYPE] to be readable by non-technical stakeholders 
and decision-makers. Currently it shows metrics and numbers, but lacks context and 
actionability. 

For each section of the report, add:

1. **Purpose Statement** (1-2 sentences)
   - Why does this section matter?
   - What decision does it inform?

2. **Definitions for Every Metric** (inline or table)
   - What is [METRIC_NAME]?
   - What does the value range mean? (e.g., <20 = low, 20-30 = medium, >30 = high)
   - How does it affect trading/decision-making?

3. **Interpretation Guidance**
   - What does each value mean in plain English?
   - Avoid jargon; use "moves together" instead of "cointegrated"
   - Add emoji/icons for quick visual scanning

4. **Context-Aware Recommendations**
   - Don't just state facts; say what to do about them
   - Vary recommendations based on market state/conditions
   - Make it clear why one action applies vs. another

5. **Full List of Components**
   - If the report references a universe, list it explicitly
   - If there are categories, enumerate them
   - No "etc." or "and others" — be complete

6. **Key Metrics Glossary** (at end)
   - Define every technical term used in the report
   - Keep definitions concise (1 sentence)
   - Link concepts together (e.g., "Cointegration: Two prices that move together...")

7. **Example Values & Interpretation**
   - Show what different scenarios look like
   - Example: "Correlation 0.75 = strong (move together); 0.45 = weak (independent)"
   - Help reader understand if 0.75 is good or bad for *their* use case

Structure the final report as:
- [Header with Purpose]
- [Section 1: metrics table with definitions]
- [Section 2: purpose + bullet list + context table]
- [Section 3: purpose + bullet list + interpretation]
- ... [repeat for each section]
- [Key Metrics Glossary at end]
- [Footer: timestamp + next steps]

Goal: A trader/analyst should be able to read this report cold and understand 
what happened and what to do next, without asking 3 clarifying questions.
```

---

## Step-by-Step Application

### Step 1: Identify Report Sections
List every section in your current report:
- [ ] Market metrics
- [ ] Portfolio status
- [ ] Screening results
- [ ] Signals/verdicts
- [ ] Position status
- [ ] Recommendations
- [ ] Other: ___________

### Step 2: For Each Section, Add Context

**Template:**

```markdown
## [Section Name] [Emoji]

**Purpose:** [1-2 sentences explaining why this section exists and what decision it informs]

| Metric | Current Value | Definition & Range |
|--------|---------------|-------------------|
| [NAME] | [VALUE] | [What it is]. Range: [LOW]–[HIGH]. Interpretation: [HIGH = ?], [LOW = ?] |
| [NAME] | [VALUE] | [What it is]. Range: [LOW]–[HIGH]. Interpretation: [HIGH = ?], [LOW = ?] |

**Interpretation:**
- [Value interpretation A]
- [Value interpretation B]

> **Key concept:** [Define any jargon unique to this section]

**What this means for action:**
- If [condition A]: do [action A]
- If [condition B]: do [action B]
```

### Step 3: Create Definitions Table

**For each metric in the report, create:**

```
METRIC: [Name]
DEFINITION: [Plain English explanation]
RANGE: [Minimum] to [Maximum]
INTERPRETATION:
  - [Value < 20%]: [Meaning in trader language]
  - [Value 20-50%]: [Meaning in trader language]
  - [Value > 50%]: [Meaning in trader language]
WHY IT MATTERS: [How does this affect trading decisions?]
```

### Step 4: Add Interpretation Tables for Lists/Rankings

**Before:**
```
- Top 5 signals: AAPL, MSFT, NVDA, GOOG, META
```

**After:**
```
| Signal | Score | Metric A | Metric B | What It Means |
|--------|-------|----------|----------|---------------|
| AAPL | 2.45 | 0.82 | 12d | Strong momentum, fast mean-reversion |
| MSFT | 2.31 | 0.76 | 15d | Good signal, slower reversion |
| NVDA | 2.18 | 0.65 | 18d | Moderate signal, extended timeframe |
```

### Step 5: Create Scenarios

**For each state/regime/condition, write out what the report says and what to do:**

```
### Scenario 1: Normal Market (VIX < 20, Regime = NORMAL)
Report shows: [Example metrics]
Meaning: [What it indicates about market health]
Action: [What a trader should do]

### Scenario 2: Stressed Market (VIX 20-30, Regime = ELEVATED)
Report shows: [Example metrics]
Meaning: [What it indicates about market health]
Action: [What a trader should do]

### Scenario 3: Crisis (VIX ≥ 40, Regime = CRISIS)
Report shows: [Example metrics]
Meaning: [What it indicates about market health]
Action: [What a trader should do — usually HALT or MONITORING ONLY]
```

### Step 6: Build the Glossary

At the end of your report, add:

```markdown
## 📚 Reference: Key Metrics & Definitions

- **[Term 1]:** [1-sentence definition]
- **[Term 2]:** [1-sentence definition]
- **[Term 3]:** [1-sentence definition]
```

Example:
```markdown
## 📚 Reference: Key Metrics & Definitions

- **SNR (Signal-to-Noise Ratio):** Strength of a statistical signal. >1.0 acceptable, >2.0 strong.
- **Cointegration:** Two prices that move together such that their spread is predictable.
- **Half-life:** Expected time for a diverging pair to revert to its mean.
- **Correlation:** How closely two assets move together. Range: -1 to +1. >0.5 = strong.
```

### Step 7: Test Readability

**Read-through checklist:**
- [ ] Every metric has a definition
- [ ] Every metric has a range/interpretation
- [ ] Every section has a purpose statement
- [ ] Recommendations vary based on market state
- [ ] No unexplained acronyms or jargon
- [ ] Glossary covers all technical terms
- [ ] A non-trader could read this and understand what happened
- [ ] A trader could read this and know what action to take
- [ ] All lists (pairs, signals, etc.) include interpretation columns
- [ ] Report includes timestamp and next steps

---

## Example: Before & After

### Before (Bare Facts)
```
MARKET REGIME
  State: NORMAL | VIX: 16.7 | Position Size: 1.0x

SCREENING RESULTS
  Pairs screened: 100
  Anomalies flagged: 0

AGENT VERDICTS
  ACTIVE: 0 | MONITOR: 0 | REJECT: 0

ACTION: HOLD
```

### After (With Context)
```markdown
## 📊 Market Regime

| Signal | Value | Definition |
|--------|-------|-----------|
| VIX | 16.7 | Volatility Index (S&P 500). Measures market fear/uncertainty. <20 = calm, 20-30 = elevated, >30 = stress. |
| Position Multiplier | 1.00x | Risk adjustment factor. Reduces trade size in stressed markets. 1.0x = full size, 0.5x = half size, 0.25x = quarter size. |

> ✓ **Conditions stable — full position sizing active**
> 
> Market volatility is low and stable. Full position sizing enabled. Safe to initiate new trades.

## 📋 Screening Results

**Purpose:** Evaluate each pair against statistical tests. Identifies trade candidates.

- **Pairs screened:** **100**
- **Anomalies detected:** **0** (pairs flagged for further investigation)

> **Anomaly:** A pair showing unusual behavior (e.g., breakdown of correlation, signal deterioration).

## ⚡ Agent Verdicts

**Purpose:** Classify anomalies into actionable decisions.

- **ACTIVE:** `0` (ready to trade — enter new position)
- **MONITOR:** `0` (watch closely — may resolve soon)
- **REJECT:** `0` (broken — skip until next cycle)

## ⚡ Recommended Action

**⏸ HOLD & WAIT**

> No anomalies detected, no open positions. Market is stable (NORMAL, VIX 16.7). Await next screening cycle.

---

## 📚 Reference: Key Metrics

- **SNR:** Strength of signal. >1.0 acceptable, >2.0 strong
- **Cointegration:** Two prices moving together with stationary spread
- **Anomaly:** Unusual behavior flagged for analysis
```

---

## Checklist for Your Project

Before you start: [ ] Do you have a report that needs enhancement?
After enhancement: [ ] Does the report explain its purpose?
[ ] Does the report define every metric?
[ ] Does the report show what actions to take?
[ ] Can a non-technical person read it?
[ ] Can a trader act on it immediately?

---

## Key Principles

1. **Explain Why, Not Just What**
   - "VIX 16.7 = low volatility; safe to trade" NOT just "VIX 16.7"

2. **Make Recommendations Conditional**
   - Different market states → different actions
   - Spell out: "IF condition, THEN action"

3. **Show Interpretation, Not Just Numbers**
   - "0.75 correlation = move together" vs. just "0.75"

4. **Complete Lists, Not Summaries**
   - List all 24 tickers, all 5 pairs, all verdicts
   - No "and others" — readers hate that

5. **Glossary at the End**
   - One sentence per term
   - Alphabetical or grouped by concept

6. **Professional Formatting**
   - Emoji headers for visual scanning
   - Tables for structured data
   - Blockquotes for emphasis
   - Bullet lists for clarity

7. **Context for Stakeholders**
   - A report should be shareable with C-suite or investors
   - No jargon they don't understand
   - Clear "what happened" + "what to do"

---

## For Code Implementation

### In Python:

```python
def enhance_report_section(section_name, metrics, definitions, interpretations):
    """
    Build a report section with full context.
    
    Args:
        section_name: "Market Regime", "Screening Results", etc.
        metrics: {'VIX': 16.7, 'Regime': 'NORMAL'}
        definitions: {'VIX': 'Volatility Index...', 'Regime': 'Market stress...'}
        interpretations: {'VIX': {'<20': 'calm', '20-30': 'elevated'}}
    
    Returns:
        Formatted markdown section with tables, definitions, and context.
    """
    lines = []
    lines.append(f"## {section_name}")
    lines.append("")
    lines.append(f"**Purpose:** [Explain why this section exists]")
    lines.append("")
    
    # Build definition table
    lines.append("| Metric | Value | Definition |")
    lines.append("|--------|-------|-----------|")
    for metric, value in metrics.items():
        defn = definitions.get(metric, "")
        lines.append(f"| {metric} | {value} | {defn} |")
    
    lines.append("")
    lines.append("> [Context/interpretation]")
    
    return "\n".join(lines)
```

---

## Apply This Template to:

- [ ] Daily briefings
- [ ] Weekly performance reports
- [ ] Risk assessment dashboards
- [ ] Signal screening results
- [ ] Portfolio status reports
- [ ] Backtest summaries
- [ ] Market regime reports
- [ ] AI/agent decision logs
- [ ] Anomaly detection alerts
- [ ] Position management briefings

---

## Questions to Answer for Your Project

**Before you start the enhancement:**

1. Who reads this report? (Traders, analysts, managers, investors?)
2. What decisions do they make based on it?
3. What jargon will they NOT understand?
4. What would cause them to ask "what does this mean?"
5. What is the worst action they could take if they misread it?
6. What metrics could they misinterpret without context?
7. What scenarios (market regimes, conditions) should have different recommendations?

**Answer these, then use the prompt above to build your enhanced report.**

---

## Final Notes

This enhancement approach:
- ✅ Makes reports shareable with non-technical stakeholders
- ✅ Reduces "what does this mean?" questions
- ✅ Provides actionable guidance, not just metrics
- ✅ Handles different market states explicitly
- ✅ Defines jargon so readers don't need external resources
- ✅ Uses tables and emoji for visual clarity
- ✅ Keeps sections focused and scannable

Use this prompt on any quantitative trading, analysis, or monitoring report. The pattern applies everywhere.
