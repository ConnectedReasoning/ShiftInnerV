# Treasury Yield Curve Pairs Trading — A Strategic Pivot Analysis

---

## The Core Insight: Treasuries Are Already a Cointegrated System

**Key difference from equity/currency pairs:**

Equity pairs: "Do these two stocks move together?" (Uncertain — depends on fundamentals)  
Currency pairs: "Do these two currencies move together?" (Sometimes — depends on rates)  
**Treasury pairs: "Is the yield curve the right shape?" (Always — by definition)**

The entire US Treasury market is one **cointegrated system**. Bonds of different maturities are linked by:
- **No-arbitrage principle** (you can't earn a risk-free profit by buying 2-year and selling 5-year)
- **Supply/demand across the curve** (when one part dislocates, it snaps back)
- **Single underlying factor** (Fed policy rate drives everything)

This is fundamentally different from pairs trading equities. **You're not looking for cointegration — it's guaranteed.** You're looking for **temporary mispricing of the curve shape**.

---

## Why Your ShiftInnerV Analysis IS Helpful Here

### **1. Johansen Test Still Works (With a Twist)**

**For equities:** Johansen answers "Are these two stocks structurally linked?"  
**For Treasuries:** Johansen answers "Is this yield curve dislocation temporary or permanent?"

Example:
- Normal curve: 2Y = 4.5%, 5Y = 4.8%, 10Y = 5.0% (positively sloped)
- Dislocation: 2Y = 4.5%, 5Y = 5.1%, 10Y = 4.9% (inverted 5-10Y)
- Johansen test: "Are these log prices cointegrated?" (Yes, strongly — they always are)
- **Real question:** "Is the inversion mean-reverting?" (Needs different test)

**Your SNR scoring becomes more useful here** because you're measuring signal (curve shape recovery) vs noise (daily fluctuations in yields).

---

### **2. Half-Life Analysis Is Perfect for Curve Trading**

Your half-life computation answers: "How long does it take for a spread to mean-revert?"

**In Treasuries, this is CRITICAL:**

When the 10Y-2Y spread dislocates:
- Half-life = 5 days? → Quick trade, tight stop-loss
- Half-life = 30 days? → Longer position, wider stops
- Half-life = 90 days? → Structural change, don't trade

Your existing half-life logic maps directly to treasury trading. **This is valuable.**

---

### **3. Your Multi-Gate Framework Is Overkill But Useful**

Your 7-gate framework (Johansen, half-life, SNR, episodes, factor exposure, position revalidation, net P&L):

**Gates 1-3 apply to Treasuries:**
- ✅ **Gate 1 (Cointegration):** Always passes (the curve is always cointegrated)
- ✅ **Gate 2 (Half-life):** Highly relevant (is the mean reversion fast enough to trade?)
- ✅ **Gate 3 (SNR):** Critical (is curve signal strong vs daily noise?)
- ❌ **Gates 4-6:** Less relevant (Treasury pairs don't have "episodes" like equity pairs; they're structural)
- ✅ **Gate 7 (Net P&L):** Essential (after transaction costs, do you actually make money?)

**So your framework works, but you'd simplify it:**
- Remove Gate 4 (episodes) — Treasury curve relationships are permanent
- Refocus Gate 6 (factor exposure) → "Single factor model" (the curve is one system)
- Keep SNR and half-life as PRIMARY signals

---

## How Treasury Pairs Trading Works (Fundamentally)

### **Three Main Strategies:**

#### **1. Curve Steepening / Flattening**
```
Setup: Bet on the shape of the yield curve (not absolute rates)
Example: "2Y-10Y spread is too flat. Buy 10Y, sell 2Y"

Your role: Detect when the spread dislocates from normal and capture mean reversion

How your system helps:
- Measure historical 2Y-10Y spread (cointegrated? yes)
- Compute half-life of dislocations (if half-life < 10d, tradeable)
- Measure SNR (is the mean reversion signal strong?)
```

#### **2. Butterfly Spread (Curve Convexity)**
```
Setup: Capture mispricings across three points (e.g., 2Y, 5Y, 10Y)
Example: "The 5Y is cheap relative to 2Y-10Y. Buy 5Y, sell 2Y and 10Y"

Your role: Detect when one maturity dislocates from the curve

How your system helps:
- Build a 3-dimensional cointegration test (2Y, 5Y, 10Y as a system)
- Measure which leg is out of line (using residuals)
- Half-life tells you how long the mispricing lasts
```

#### **3. Key Rate Duration Shift**
```
Setup: Bet that different parts of the curve will reprrice relative to each other
Example: "10Y will outperform 2Y" (buying 10Y, shorting 2Y)

Your role: Predict when curve shape changes and capture the move

How your system helps:
- Historical curve shape data (you'd need to store it)
- Half-life of shape changes (when do they happen?)
- SNR of shape deviations (how reliable is the signal?)
```

---

## What Would Change in ShiftInnerV for Treasury Trading

### **1. Universe Definition**
```
Current: 200+ mixed equities, ETFs, currencies, commodities

Treasury universe:
  - Cash: Fed Funds Rate
  - Short: 3M, 6M, 1Y Bills
  - Intermediate: 2Y, 3Y, 5Y Notes
  - Long: 7Y, 10Y, 20Y, 30Y Bonds
  - Maybe: TIPS, Strips, Agency MBS

Total: ~15-20 instruments (massive simplification)
```

### **2. Data Source Change**
```
Current: yfinance, Tiingo, CSV files

Treasury data:
  - Daily: Federal Reserve H.15 release (official Treasury rates)
  - Intraday: Bloomberg, Refinitiv, or CME futures data
  - Real-time: Fed Reserve H.15 API or FRED (St. Louis Fed)

Much cleaner data (official rates, not bid-ask midpoints)
```

### **3. Correlation Analysis → Curve Shape Analysis**
```
Current: Rolling correlation between pairs

Treasury shift:
  - Instead of correlation matrix → Store daily yield curve (15-20 points)
  - Compute curve "shape" (slope, convexity, butterfly)
  - Track how shape deviates from normal
  
Your correlation window (252 days) still applies:
  "What's the historical normal curve shape?"
  "How far is today's curve from normal?"
  "How long does it take to normalize?"
```

### **4. Scoring Function Adaptation**
```
Current weights: 40% decay, 20% strength, 20% vol, 20% history

Treasury scoring:
  - Curve displacement (40%): How far is the curve from historical mean?
  - Decay speed (20%): Is the curve converging back?
  - Volatility (20%): Is signal clear or noisy?
  - Historical mean reversion (20%): How reliable is the normalization?

This maps almost directly. You're scoring "curve dislocation likelihood."
```

### **5. Johansen Test → Factor Model**
```
Current: Johansen test on log prices

Treasury: 
  - Still use Johansen on (2Y, 5Y, 10Y) as a system
  - But also compute: Principal Component Analysis (PCA)
    - PC1 = parallel shift (everyone moves same direction)
    - PC2 = curve tilt (long-short rate differential)
    - PC3 = butterfly (curvature change)
  
Your cointegration test answers: "Is the curve tied together?" (Always yes)
Your PCA adds: "Where is the dislocation?" (PC2 vs PC3)
```

---

## Why Treasury Pairs Is Actually BETTER Than Equity Pairs

### **Reason 1: The Edge Is Structural, Not Empirical**

**Equities:** You're betting two stocks move together (might stop at any time)  
**Treasuries:** You're betting the curve normalizes (guaranteed by Fed policy + no-arbitrage)

This means:
- **Higher win rate** (you're betting on physics, not sentiment)
- **More predictable timing** (curve mean-reverts faster)
- **Less regime-dependent** (the edge exists in all market conditions)

---

### **Reason 2: Scale & Execution**

**Equities:** Need to buy/sell shares (small scale, high friction)  
**Treasuries:** Futures and swaps (any size, tight spreads, 24/5 trading)

This means:
- **Better execution** (Treasury futures have tight bid-ask)
- **Lower transaction costs** (basis points vs percent)
- **Scalable** (you can trade 1M notional or 1B notional)

---

### **Reason 3: Your Diagnostic Validates This**

Look at your anomalies.db:
- **AGG / BND — 21 episodes, rating 0.0 (NOISE)** ← Bond ETFs, very noisy
- **IEF / LQD — 3 episodes, rating 88.6 (PRIME)** ← Treasuries vs Corporates, STRONG

This tells you: **Bonds show stronger cointegration than equities.** But you were screening bond ETFs (AGG/BND), which are noisy because:
- AGG = Bloomberg aggregate (mix of Treasuries, corporates, MBS)
- BND = Total bond market fund (similar mix)

**They're both too broad.** If you traded pure Treasury points (2Y, 5Y, 10Y actual yields), you'd see much tighter relationships.

---

## The Yield Curve Specific Analysis

### **A. Curve Shape Decomposition**

```
Historical mean curve:
  2Y = 4.50%
  5Y = 4.75%
  10Y = 5.00%
  
Today's curve:
  2Y = 4.50%
  5Y = 5.10%
  10Y = 4.90%
  
Decomposition:
  - Level shift: 0 bps (average rate same)
  - Slope change: 5Y is 35bps too high (steepening signal)
  - Butterfly: 5Y should be 4.75%, not 5.10%
  
Mean reversion trade: "Sell 5Y, buy 2Y+10Y butterfly"
Half-life: "How long until 5Y returns to 4.75%?"
```

Your SNR scoring answers: "Is this signal real or noise?"

### **B. Duration Analysis**

```
Your "episodes" concept becomes: "How many times has this curve shape appeared?"

Query: "How often is the 10Y-2Y spread > 50bps?" (steep curve)
If it's > 50bps:
  - 90% of the time → No edge (normal state)
  - 10% of the time → Edge (abnormal, will mean-revert)
  
Your "episodes" = "How many times was this curve configuration abnormal?"
```

### **C. Factor Model for Curve Trading**

```
Treasuries follow a 3-factor model (proven empirically):

Factor 1: Parallel shift (all rates move together)
  - Driven by: Fed policy rate, real rates
  - Your response: Position size based on factor exposure
  
Factor 2: Slope change (long rates move differently than short rates)
  - Driven by: Growth expectations, term premium
  - Your response: Curve steepening/flattening trades
  
Factor 3: Butterfly/curvature (intermediate rates diverge)
  - Driven by: Supply/demand imbalances
  - Your response: Butterfly spread trades

Your multi-gate framework checks:
  - Gate 1: Are these factors cointegrated? (Yes, always)
  - Gate 3: Can we distinguish signal (factor moves) from noise (daily jitter)?
  - Gate 7: After costs, do factor moves make money?
```

---

## The Practical Implementation

### **Step 1: Source the Data (1 hour)**
```python
# Get 10+ years of Treasury yield curve data from FRED
import pandas_datareader as pdr

rates_2y = pdr.get_data_fred('DGS2', start='2014-01-01')   # 2-year
rates_5y = pdr.get_data_fred('DGS5', start='2014-01-01')   # 5-year
rates_10y = pdr.get_data_fred('DGS10', start='2014-01-01') # 10-year
rates_30y = pdr.get_data_fred('DGS30', start='2014-01-01') # 30-year

# Reshape to DataFrame with all maturities
yields = pd.DataFrame({
    '2Y': rates_2y,
    '5Y': rates_5y,
    '10Y': rates_10y,
    '30Y': rates_30y
})
```

### **Step 2: Compute Curve Metrics (30 min)**
```python
# Slope: 10Y - 2Y
yields['slope_10_2'] = yields['10Y'] - yields['2Y']

# Butterfly: 5Y vs average of 2Y and 10Y
yields['butterfly'] = yields['5Y'] - (yields['2Y'] + yields['10Y']) / 2

# Level: average of all rates
yields['level'] = yields[['2Y', '5Y', '10Y', '30Y']].mean(axis=1)

# Deviations from 252-day rolling mean
yields['slope_zscore'] = (yields['slope_10_2'] - yields['slope_10_2'].rolling(252).mean()) / yields['slope_10_2'].rolling(252).std()
yields['butterfly_zscore'] = (yields['butterfly'] - yields['butterfly'].rolling(252).mean()) / yields['butterfly'].rolling(252).std()
```

### **Step 3: Backtest (2-3 hours)**
```python
# Simple backtest: Trade when slope is > 2 sigma from mean
signals = []
for idx in range(252, len(yields)):
    if yields['slope_zscore'].iloc[idx] > 2.0:
        # Buy 10Y, sell 2Y (steepen the curve)
        # Exit when zscore < 0.5
        signals.append({
            'entry_date': yields.index[idx],
            'entry_zscore': yields['slope_zscore'].iloc[idx],
            'entry_slope': yields['slope_10_2'].iloc[idx],
        })
        # Track exit...

# Measure: Win rate, avg profit, Sharpe ratio
```

### **Step 4: Adapt ShiftInnerV (3-4 hours)**
```
Modified modules:
  1. pair_sourcer.py → curve_analyzer.py (compute curve shapes instead of correlations)
  2. monitor.py → Keep mostly the same (Johansen still tests cointegration)
  3. agents.py → Modify for curve verdicts ("Steep/Flat/Normal", not "ACTIVE/MONITOR/REJECT")
  4. Data layer: Add FRED connector
```

---

## The Honest Assessment: Would This Work?

### **Why It Might:**

1. **The edge is structural** (curve mean-reverts by definition)
2. **Less competition** (fewer quant shops explicitly do Treasury curve pairs)
3. **Tighter spreads** (Treasury futures have <1bp bid-ask)
4. **Proven trading strategy** (macro traders have done this for decades)
5. **Your infrastructure applies** (Johansen, half-life, SNR all useful)
6. **Your diagnostic hints at it** (IEF/LQD was PRIME, AGG/BND was noisy)

### **Why It Might Not:**

1. **Fed policy dominates** (When Fed changes rates, all bets are off)
2. **Supply shocks** (Treasury auctions and demand shifts can break relationships)
3. **You need leverage** (To make money on 5-10bps moves, need 50-100x leverage)
4. **Execution is hard** (Need Treasury futures access + real-time market data)
5. **Already crowded** (Macro funds, hedge funds, banks all trade the curve)

### **The Real Question:**

Is Treasury curve trading easier/better than equity pairs?

**Answer: YES, for structural reasons.** The curve is cointegrated by physics. You're not looking for correlation—you're looking for dislocations in a known relationship.

But is it *easy*? **No.** It's just different-hard.

---

## My Recommendation

### **If you want to explore this:**

1. **Pull 10 years of Treasury yield curve data from FRED** (free, official)
2. **Run a simple backtest:** "When 10Y-2Y is > 2-sigma from 252-day mean, trade"
3. **Measure:** Win rate, avg duration of mean reversion, Sharpe ratio
4. **Compare to equity pairs backtest**

**If Treasury backtest is positive (Sharpe > 0.5 and win rate > 55%):** Pivot.  
**If it's negative:** Stick with equities or currencies.

---

## The Philosophical Point

**Equity pairs:** You're betting two companies move together (uncertain)  
**Currency pairs:** You're betting two currencies move together (macro-driven)  
**Treasury pairs:** You're betting the curve is the right shape (physical law)

Treasury pairs is the most "fundamental" of the three because the edge isn't about correlation—it's about **shape normalization.**

Your ShiftInnerV system is actually *overbuilt* for Treasuries (you don't need all 7 gates), but it's also *perfectly* suited to the problem.

The question is just: Do you want to hunt for a structural edge that's more defendable?

---

## Final Thought

Your diagnostic found that AUDJPY/CADJPY was your best pair (STRONG rating). But your *second-best* class of pairs was treasuries (IEF/LQD, PRIME rating).

Both are telling you the same thing: **Equities are too efficient. Look elsewhere.**

You have a choice:
1. **Currency pairs** (macro-driven, less crowded, 24/5 trading)
2. **Treasury curve pairs** (physically cointegrated, structural edge, requires leverage)
3. **Equity pairs** (accept the market is efficient, move on)

The yield curve is the most elegant of the three because the edge is guaranteed. The only question is execution and discipline.
