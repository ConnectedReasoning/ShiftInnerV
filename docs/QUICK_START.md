# Quick Start: Currency Pairs Pivot
## 5-Minute Setup

Your data shows **66.7% STRONG/PRIME success rate for currency pairs** vs 12-14% for equities/treasuries.  
Time to act.

---

## Step 1: Copy the Universe (1 min)

```bash
cd ~/projects/github/shiftinnerv

# Backup your current universe
cp universe.yaml universe.yaml.equity_backup

# Use the expanded version with 40 currency pairs
# The universe.yaml already has the expanded forex sections
# OR use the focused 25-pair version:
cp universe_currencies_only.yaml universe.yaml
```

---

## Step 2: Update pair_sourcer.py (2 min)

Edit `shiftinnerv/pipelines/pair_sourcer.py`:

**Line ~52 (Windows):**
```python
# OLD:
CORRELATION_WINDOW = 252
DECAY_WINDOW = 126

# NEW:
CORRELATION_WINDOW = 50
DECAY_WINDOW = 25
```

**Line ~50 (Min correlation):**
```python
# OLD:
MIN_CORRELATION = 0.3

# NEW:
MIN_CORRELATION = 0.4
```

**Line ~376 (Scoring weights):**
```python
# OLD:
total_score = (
    0.20 * correlation_score +
    0.40 * decay_score +
    0.20 * volatility_score +
    0.20 * history_score
)

# NEW:
total_score = (
    0.50 * decay_score +
    0.15 * correlation_score +
    0.15 * volatility_score +
    0.20 * history_score
)
```

---

## Step 3: Run Pair Sourcer (1 min)

```bash
python3 shiftinnerv/pipelines/pair_sourcer.py \
    --top 50 \
    --lookback 1
```

**Expected:** 50 currency pairs sourced, JPY crosses near the top.

---

## Step 4: Run Sentinel Daily (ongoing)

```bash
python3 sentinel.py
```

**Track:** How many ACTIVE verdicts? Your equity system had 0. Currency should have 1-2/week.

---

## Step 5: Backtest Your Winners (1 week)

Once you see ACTIVE verdicts, backtest the three historical winners:

```python
import pandas as pd
import yfinance as yf

# Your historical winners (from the diagnostic):
pairs = [
    ('EURJPY=X', 'GBPJPY=X', 78.8),
    ('AUDJPY=X', 'CADJPY=X', 81.0),
    ('EURUSD=X', 'GBPUSD=X', 78.6),
]

for t1, t2, rating in pairs:
    df1 = yf.download(t1, start='2024-01-01', end='2026-05-21')['Close']
    df2 = yf.download(t2, start='2024-01-01', end='2026-05-21')['Close']
    
    # Spread = log price ratio
    spread = (df1 / df2).apply(np.log)
    
    # 30-day rolling mean / std
    mean = spread.rolling(30).mean()
    std = spread.rolling(30).std()
    
    # z-score
    z = (spread - mean) / std
    
    # Entry: z > 2, Exit: z < 0.5
    # Calculate: win rate, avg profit, Sharpe ratio
    
    print(f"{t1}/{t2} (rating={rating}): [your results]")
```

**If Sharpe > 0.5 and win rate > 55%:** You have an edge. Scale.

---

## Expected Results

| Metric | Equity Pairs | Currency Pairs | Target |
|--------|---|---|---|
| Pairs sourced | 100 | 50 | ✓ |
| % passing gate | 0% | 5-10% | ✓ |
| ACTIVE verdicts/week | 0 | 1-2 | ✓ |
| Avg rating sourced | 55 | 75+ | ✓ |

---

## Files You Have

1. **universe_currencies_only.yaml** — 25-pair focused universe (start here)
2. **pair_sourcer_currencies.py** — Pre-configured version (or edit your own)
3. **CURRENCY_PIVOT_IMPLEMENTATION.md** — Full guide with execution setup

---

## Next Steps

1. **Today:** Make the 4 edits to pair_sourcer.py
2. **Tomorrow:** Run `python3 sentinel.py`
3. **This week:** Track ACTIVE verdicts, backtest winners
4. **Next week:** Decide to scale or adjust

---

## The Data Is Clear

```
Currency:       66.7% STRONG/PRIME ← Your edge
Treasury:       14.1% STRONG/PRIME
Equity:         12.2% STRONG/PRIME
```

You have the signal. Execute it.

---

**Questions?** Refer to CURRENCY_PIVOT_IMPLEMENTATION.md for full details.

**Confidence level:** HIGH. Your own data confirmed the edge. Now prove it with execution.
