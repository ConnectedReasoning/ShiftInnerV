# Currency Pairs Pivot — Implementation Guide
## ShiftInnerV: From Equities to FX Edge

**Status:** Data confirmed 66.7% STRONG/PRIME success rate for currency pairs (vs 14% treasuries, 12% equities)  
**Decision:** Pivot to currency pairs trading strategy  
**Timeline:** Immediate (configuration changes only, no code changes needed)

---

## Phase 1: Configuration (Today)

### Step 1: Choose Your Universe

**Option A: Full Currency Universe (40 pairs)**
```bash
# Use the expanded universe.yaml with all currency sections
# Includes: 7 majors + 7 JPY crosses + 5 EUR crosses + 5 commodity + 6 emerging
# Estimated pairs to screen: 40 × 39 / 2 = 780 possible pairs
# Compute time: ~2-3 minutes with cached data
```

**Option B: Focused Currency Universe (25 pairs) — RECOMMENDED FOR VALIDATION**
```bash
# Use universe_currencies_only.yaml (included in this guide)
# Prioritizes: Majors (7) + JPY crosses (6) + EUR crosses (5) + commodity (4) + emerging (3)
# Estimated pairs to screen: 25 × 24 / 2 = 300 possible pairs
# Compute time: ~60 seconds with cached data
# Rationale: Easier to validate edge with smaller universe, easier to execute
```

**Decision:** Start with Option B (25 pairs). Scale to Option A after validation.

### Step 2: Update Configuration

```bash
# Copy the universe file
cp universe_currencies_only.yaml ~/projects/github/shiftinnerv/universe.yaml

# Or manually edit existing universe.yaml to replace the forex_direct section
# (I've already updated it with expanded currency section)
```

### Step 3: Adjust Pair Sourcer Parameters

Your current pair_sourcer.py uses:
```python
CORRELATION_WINDOW = 252     # 1 year rolling window
DECAY_WINDOW = 126           # 6 months
```

**For currencies, use shorter windows:**

Edit `shiftinnerv/pipelines/pair_sourcer.py`:

```python
# Line ~52: Change correlation window
CORRELATION_WINDOW = 50      # 50-day (2 months) rolling window
DECAY_WINDOW = 25            # 25-day (1 month) lookback

# Rationale: Currency relationships shift faster than equities
# - Interest rates change quarterly (not annually)
# - Carry trades unwind in weeks, not months
# - Regime changes (Fed vs ECB policy) are more frequent
```

**Why this matters:** With 252-day windows, you're looking at ancient history. With 50-day windows, you capture recent divergences that actually mean-revert.

### Step 4: Adjust Scoring Weights

Current weights (optimized for equities):
```python
0.40 * decay_score +          # 40% — correlation decay
0.20 * strength_score +       # 20% — correlation strength
0.20 * volatility_score +     # 20% — volatility matching
0.20 * history_score          # 20% — historical success
```

**For currencies, use:**
```python
0.50 * decay_score +          # 50% — INCREASE: Recent divergence is primary signal
0.15 * strength_score +       # 15% — DECREASE: Strength less predictive in FX
0.15 * volatility_score +     # 15% — DECREASE: Vol matching less critical
0.20 * history_score          # 20% — KEEP: Historical success still matters
```

**Rationale:**
- Currencies diverge *faster* (carry trade unwinding, policy shifts)
- Correlation strength is less stable (rates change, regime shifts)
- Volatility matching less important (FX pairs naturally related via rates)
- Historical success is your signal that "this pair has meant-reverted before"

Edit `shiftinnerv/pipelines/pair_sourcer.py` line ~376:
```python
total_score = (
    0.50 * decay_score +
    0.15 * correlation_score +
    0.15 * volatility_score +
    0.20 * history_score
)
```

### Step 5: Update Minimum Correlation Threshold

Current: `MIN_CORRELATION = 0.3` (allows very weak pairs)

**For currencies:**
```python
MIN_CORRELATION = 0.4        # Higher threshold — FX pairs are linked by rates, not random
```

**Rationale:** Two unrelated currencies (like USDBRL and EURJPY) shouldn't pair. But EURUSD and GBPUSD (both vs USD) naturally correlate. Filter noise early.

---

## Phase 2: Validation (Days 1-7)

### Day 1: Run Pair Sourcer with New Universe

```bash
cd ~/projects/github/shiftinnerv
python3 shiftinnerv/pipelines/pair_sourcer.py \
    --universe universe_currencies_only.yaml \
    --output compositions/sourced_currencies_20260521.yaml \
    --top 50 \
    --lookback 1
```

**Expected output:**
```
Loaded universe: 25 tickers across 1 category
Computing rolling correlation...
Clustering tickers into 15 groups...
Scoring pairs (min_correlation=0.4)...
Scored 300 pairs
Written: compositions/sourced_currencies_20260521.yaml (50 pairs)

Top 5 pairs by score:
  EURJPY=X / GBPJPY=X  score=62.1  corr=0.850  cluster=3
  AUDJPY=X / CADJPY=X  score=61.9  corr=0.825  cluster=2
  EURUSD=X / GBPUSD=X  score=60.7  corr=0.920  cluster=1
  AUDNZD=X / NZDCAD=X  score=59.4  corr=0.765  cluster=4
  EURGBP=X / EURAUD=X  score=58.8  corr=0.710  cluster=5
```

**What you're looking for:**
- High correlations (0.7+) — currencies linked by rates
- JPY crosses at the top (your winners were there)
- Decay scores driving the ranking

### Days 2-5: Screen Daily

```bash
python3 sentinel.py
```

**Track in trial_ledger.db:**
- How many pairs pass Johansen at 95% CI?
- What's the distribution of SNR values?
- Do any generate ACTIVE verdicts?

**Expected:** Better hit rate than equities because:
- Currencies are structurally cointegrated (by no-arbitrage)
- Shorter windows mean more recent signal
- Your three historical winners should show up in sourced pairs

### Days 6-7: Backtest Your Historical Winners

```python
# Backtest the three pairs you know work
pairs = [
    ('EURJPY=X', 'GBPJPY=X'),   # 78.8 rating
    ('AUDJPY=X', 'CADJPY=X'),   # 81.0 rating
    ('EURUSD=X', 'GBPUSD=X'),   # 78.6 rating
]

# For each pair:
# 1. Load daily FX data (2024-2026)
# 2. Compute 30-day rolling spread mean and std
# 3. Entry: Spread > 2 sigma from mean
# 4. Exit: Spread < 0.5 sigma OR 20 days, whichever first
# 5. Measure: Win rate, avg holding period, Sharpe ratio

# Example:
# If you backtest EURJPY/GBPJPY and get:
#   - Win rate: 58%
#   - Avg holding: 8 days
#   - Sharpe: 0.7
# → That's tradeable. Scale to other pairs.
```

---

## Phase 3: Go Live (Week 2+)

### Option A: Gradual Expansion
```bash
# Week 1: 25 currency pairs (validation phase above)
# Week 2: Expand to 40 pairs (add commodity + emerging)
# Week 3: Expand to 60 pairs (add less-liquid, higher-vol pairs)
# Track: Hit rate, win rate, average P&L
```

### Option B: Parallel Running (Recommended)
```bash
# Run both systems in parallel:
#   Equities: sentinel.py --universe universe.yaml (200 tickers)
#   Currencies: sentinel.py --universe universe_currencies_only.yaml (25 tickers)
# 
# Compare:
#   - How many ACTIVE verdicts each produces
#   - Which has higher win rate
#   - Which is easier to execute
# 
# After 2 weeks, kill whichever is underperforming
```

### Execution Setup (Once You Have ACTIVE Verdicts)

You'll need:
1. **FX Futures Account** (for execution)
   - Interactive Brokers (best for FX)
   - Saxo Bank
   - OANDA

2. **Position Sizing** (FX trades require leverage)
   - If you want $10k notional exposure on EURUSD
   - Trade 0.1 lot (10,000 units) with 100:1 leverage
   - Adjust position size based on volatility

3. **Execution Integration**
   - Your trial_ledger already tracks ACTIVE verdicts
   - Add a simple FX execution bridge (place order when ACTIVE, close when position revalidation says MONITOR)

---

## Configuration Checklist

- [ ] Backed up current universe.yaml
- [ ] Updated universe.yaml with expanded forex sections (OR using universe_currencies_only.yaml)
- [ ] Changed CORRELATION_WINDOW from 252 to 50
- [ ] Changed DECAY_WINDOW from 126 to 25
- [ ] Updated scoring weights to 0.50/0.15/0.15/0.20
- [ ] Updated MIN_CORRELATION from 0.3 to 0.4
- [ ] Ran pair_sourcer with new universe
- [ ] Got sourced pairs with high correlations (0.7+)
- [ ] Confirmed JPY crosses in top 5

---

## Expected Outcomes

**If currency edge is real:**
- 40+ pairs sourced daily
- 5-10% pass Johansen at 95% CI (vs 0% for equities)
- 1-2 ACTIVE verdicts per week
- 55%+ win rate on backtests
- Sharpe ratio > 0.5

**If currency edge is weak:**
- Same 0% pass rate as equities (edge was noise)
- No ACTIVE verdicts
- Negative backtest results

**What that means:**
- Real edge → Start trading, scale up
- Weak edge → Pivot back to equities or try different angle

---

## Files to Modify

1. **universe.yaml** — Already updated with forex sections
2. **shiftinnerv/pipelines/pair_sourcer.py** — Lines 52, 376 (windows, weights)
3. **NEW: universe_currencies_only.yaml** — Provided (copy if using focused 25-pair universe)

---

## Risk Management (Critical for FX)

**Currency pairs use leverage.** You'll need position limits:

1. **Maximum position size:** 1 lot (100,000 units) per pair
2. **Maximum correlation-monitored pairs:** 5 simultaneously
3. **Margin requirement:** Keep 40% free margin (don't use full leverage)
4. **Stop-loss:** Automatic 3-sigma stops
5. **Market hours:** Only trade during overlapping sessions (EU/US: 1pm-5pm EST)

Your regime_monitor already handles VIX-based position sizing. In FX, also respect:
- **High volatility days** (NFP, ECB/Fed announcements) → Reduce position size 50%
- **Low liquidity hours** (Asia session) → Avoid pairs (wider spreads)

---

## Success Metrics (30-Day Checkpoint)

After 30 days of running the currency pairs system, measure:

| Metric | Equity Pairs Target | Currency Pairs Target |
|--------|-------------------|----------------------|
| Pairs sourced per day | 100 | 50 |
| % passing Johansen | 0% | 5-10% |
| ACTIVE verdicts per week | 0 | 1-2 |
| Avg SNR when cointegrated | 0.5 | 1.2+ |
| Historical rating of sourced pairs | 55 | 75+ |
| Backtest Sharpe ratio | negative | > 0.5 |

**If currencies win:** Scale to 60-80 pairs, integrate execution  
**If equities win:** Stick with expanded equity universe, optimize scoring  
**If both fail:** Pivot to factor timing or volatility arbitrage

---

## Next Steps

1. **Today:** Make the 5 configuration changes above
2. **Tomorrow:** Run `python3 sentinel.py` with new currency universe
3. **This week:** Track how many ACTIVE verdicts you get
4. **Next week:** Backtest your historical winners
5. **Week 3:** Decide to scale or pivot

The data is clear: **66.7% of your currency pairs are STRONG/PRIME.** You have the signal.

Now execute.
