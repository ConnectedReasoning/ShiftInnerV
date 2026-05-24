# How to Use the Briefing Enhancement Prompt

## Two Documents to Use

### 1. **BRIEFING_ENHANCEMENT_PROMPT.md** (Full Version)
- Complete framework with detailed explanations
- Step-by-step process for enhancing any report
- Python code examples
- Testing checklist
- Deep context on why each enhancement matters

**Use this when:**
- You're enhancing a report for the first time
- You want to understand the philosophy behind each change
- You're building tooling to auto-generate enhanced reports
- You need to explain to others why you're making changes

### 2. **BRIEFING_ENHANCEMENT_QUICK_REFERENCE.md** (Quick Card)
- 7-step framework at a glance
- Before/after examples for common report sections
- Quick checklist for each section
- Writing tips and emoji guide
- Applied to different report types

**Use this when:**
- You've done this before and just need a checklist
- You want to quickly refer back while writing
- You're reviewing someone else's enhanced report
- You need to explain the framework to a teammate in 5 minutes

---

## Quick Start (5 Minutes)

1. **Read the Quick Reference** (BRIEFING_ENHANCEMENT_QUICK_REFERENCE.md)
   - Look at "7-Step Enhancement Framework"
   - Scan the "Before/After Examples"
   - Keep the "Checklist" open

2. **For each section of your report, ask:**
   - [ ] Does it have a purpose statement?
   - [ ] Is every metric defined?
   - [ ] Does it show interpretation?
   - [ ] Does it have context-aware recommendations?
   - [ ] Are all lists complete (no "etc.")?

3. **Apply the templates** from the Quick Reference
   - Use the table templates as starting points
   - Adapt the before/after examples to your metrics
   - Copy the glossary format for your key terms

4. **Test it** (final checklist in Quick Reference)
   - Can a non-technical person understand it?
   - Can a trader act on it?
   - Did you explain the "why" not just the "what"?

---

## Full Implementation (30 Minutes)

1. **Read BRIEFING_ENHANCEMENT_PROMPT.md** completely
   - Understand the 7 steps
   - Review the Step-by-Step Application section
   - Look at the templates

2. **Answer the "Questions to Answer for Your Project"** section
   - Who reads this?
   - What decisions do they make?
   - What jargon might confuse them?
   - What actions could they take?

3. **Build your enhanced report section by section**
   - Use the templates from the prompt
   - Follow the checklist for each section
   - Test against the final principles

4. **Create your glossary** using the reference format

5. **Test readability** with someone unfamiliar with your domain

---

## How to Apply to Other Projects

### To Enhance a Report from Project X:

1. Use Quick Reference as a template
2. Identify your report's sections (e.g., Portfolio Status, Risk Metrics, Signals)
3. For each section:
   - Copy the template structure from Quick Reference
   - Replace metrics with your project's metrics
   - Add definitions specific to your domain
   - Create interpretation tables for your data ranges
   - Write scenario descriptions for your conditions
4. Create a glossary of your domain's key terms
5. Test with a non-expert reader

### To Integrate Into Code (Auto-Generate Enhanced Reports):

Use the Python pattern in BRIEFING_ENHANCEMENT_PROMPT.md (section "For Code Implementation"):

```python
from report_enhancer import enhance_report_section

# Define your metrics and definitions
metrics = {
    'Sharpe': 1.45,
    'MaxDD': -15.3,
    'WinRate': 62.1
}

definitions = {
    'Sharpe': 'Return per unit of risk. >1.0 acceptable, >2.0 excellent.',
    'MaxDD': 'Maximum drawdown from peak. |-15.3| = 15.3% loss.',
    'WinRate': 'Percentage of winning trades. >50% is profitable.'
}

# Generate enhanced section
section = enhance_report_section(
    section_name="Performance Metrics",
    metrics=metrics,
    definitions=definitions,
    interpretations={
        'Sharpe': {'>2.0': 'Excellent', '1.0-2.0': 'Good', '<1.0': 'Weak'},
        'MaxDD': {'>-10%': 'Low drawdown', '-10% to -30%': 'Moderate', '<-30%': 'High'}
    }
)
print(section)
```

---

## Common Use Cases

### Use Case 1: Enhance Existing Daily Briefing

**Steps:**
1. Take your current daily briefing
2. Use Quick Reference step 1-3 (7-Step Framework)
3. Apply to each section
4. Add glossary from reference section
5. Test and ship

**Time:** 30-60 minutes

### Use Case 2: Build New Report Generator

**Steps:**
1. Read full BRIEFING_ENHANCEMENT_PROMPT.md
2. Design your report sections
3. Create templates for each section using the patterns
4. Build code using the Python example
5. Test with real data

**Time:** 2-4 hours

### Use Case 3: Review Someone Else's Report

**Steps:**
1. Use the Checklist from Quick Reference
2. For each section, check:
   - Purpose? Definition? Interpretation? Action?
3. Mark gaps
4. Request enhancements

**Time:** 10-15 minutes

### Use Case 4: Document Report Standards for Team

**Steps:**
1. Take Quick Reference
2. Customize the examples to your domain
3. Create your team's standard checklist
4. Distribute to team
5. Review reports against checklist

**Time:** 1-2 hours

---

## Example: Applying to StratixCap

If you wanted to enhance a StratixCap report using this framework:

1. **Current sections:** Market Regime, Factor Signal, Portfolio Status, Options Plays
2. **Add purpose statements:**
   - Factor Signal: "Identify momentum-driven stock positions based on multi-factor analysis"
   - Options Plays: "Leverage factor exposure with measured options strategies"
3. **Add definitions:**
   - LONG/SHORT: What each means for position sizing
   - IV: Implied Volatility thresholds and interpretations
   - Greeks: Delta, Theta, Vega definitions
4. **Add scenario recommendations:**
   - Market rally scenario (VIX < 15): Action is different
   - Market stress (VIX > 30): Action is different
5. **Add glossary:**
   - Factor, Momentum, Long, Short, IV, Delta, Theta, etc.

**Result:** Same report, now shareable with stakeholders who don't speak finance

---

## Choosing Between Quick Reference vs Full Prompt

| Situation | Use |
|-----------|-----|
| First time enhancing a report | Full Prompt |
| Quick checklist while writing | Quick Reference |
| Building automated tooling | Full Prompt (code section) |
| Teaching someone your process | Quick Reference (examples) |
| Deep understanding of the philosophy | Full Prompt |
| Remembering key principles | Quick Reference |
| Customizing for your domain | Full Prompt (Questions section) |
| Quick review/audit | Quick Reference (Checklist) |

---

## Key Principles to Remember

Whenever you enhance a report, keep these in mind:

1. **Explain Why** — Not just what (e.g., "VIX is low = safe to trade" not just "VIX 16.7")
2. **Define Everything** — No jargon without explanation
3. **Show Ranges** — "0.75 correlation (strong, range -1 to +1)" not just "0.75"
4. **Add Context** — "If X, then do Y" recommendations based on conditions
5. **Be Complete** — List all 24 items, not "top 5 and others"
6. **Use Tables** — Structured data is scannable and professional
7. **Professional Format** — Emoji headers, blockquotes, markdown tables

---

## Questions to Ask About Your Enhanced Report

Before you ship it, ask:

1. Could a trader read this and act immediately? (Yes/No)
2. Could a manager read this and understand the state? (Yes/No)
3. Could a non-expert explain it to someone else? (Yes/No)
4. Did I explain every term used? (Yes/No)
5. Did I show what to do, not just what happened? (Yes/No)
6. Are all lists complete (no "etc.")? (Yes/No)
7. Would I be comfortable sharing this with a client? (Yes/No)

If all are "Yes" → ready to ship.
If any are "No" → enhance that section before shipping.

---

## Next Steps

1. **Choose your report:** Which report will you enhance first?
2. **Pick your document:**
   - First time? Start with BRIEFING_ENHANCEMENT_PROMPT.md
   - Quick refresh? Use BRIEFING_ENHANCEMENT_QUICK_REFERENCE.md
3. **Follow the framework:** 7 steps, one section at a time
4. **Test with a reader:** Ask someone unfamiliar with your domain
5. **Iterate:** Refine based on feedback
6. **Ship it:** Use this enhanced report for all future runs

---

## Support

**If you get stuck:**
- Re-read the appropriate section in the full prompt
- Look for a similar example in the quick reference
- Test your draft with a non-expert reader
- Ask: "Would I understand this if I knew nothing about trading?"

Good luck! Your reports are about to become much more professional and actionable.
