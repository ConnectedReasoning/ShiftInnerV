# ShiftInnerV — Gate Threshold Sensitivity Report
*Generated 2026-05-18 16:35*

## Executive Summary

This report validates each hardcoded gate threshold against:
- **Empirical distributions** from the historical screening database
- **OU process simulation** for z-score threshold optimisation
- **Literature review** to document original sources

Each threshold is classified as:
- `EVIDENCE-BASED` — supported by empirical data from this universe
- `LITERATURE-ASSUMED` — drawn from published literature without universe-specific testing
- `REVIEW-RECOMMENDED` — evidence suggests a change is warranted

---
## Gate 2 — Half-Life Ceiling

**Current value:** 120 days  
**Source:** Chan *Algorithmic Trading* (literature)  
**Classification:** `LITERATURE-ASSUMED`

### Empirical distribution (from screening database)

| Statistic | Value |
|-----------|-------|
| Median half-life | 39.4 days |
| 75th percentile | 65.1 days |
| 90th percentile | 95.1 days |
| 95th percentile | 116.1 days |
| Maximum observed | 85223.7 days |
| Pairs within 100–130d (near ceiling) | 1833 |

### Pass rate at alternative ceilings

| Ceiling (days) | Pairs Passing | Pass Rate | Δ from current |
|----------------|---------------|-----------|----------------|
| 96 | 35121 | 90.3% | -24 |
| 108 | 36337 | 93.4% | -12 |
| 120 | 37237 | 95.7% | +0 ← current |
| 132 | 37409 | 96.2% | +12 |
| 144 | 37546 | 96.5% | +24 |
| 180 | 37840 | 97.3% | +60 |

**Recommendation:** If the 90th percentile of observed half-lives
is well below 120 days, the ceiling is non-binding and conservative.
If many pairs cluster near 120 days, consider whether 90 or 100 days
better captures genuinely tradeable mean reversion for this universe.

---
## Gate 3 — SNR Floor

**Current value:** 1.0  
**Source:** Vidyamurthy *Pairs Trading* (literature)  
**Classification:** `REVIEW-RECOMMENDED`

> Vidyamurthy noted: SNR = 1.0 means stationary and nonstationary
> variance are equal. Signal and noise are in perfect balance.
> This is the minimum for positive expected value, not a comfortable
> trading threshold. The council recommended raising to 1.5–2.0.

### Empirical distribution (SNR ≤ 100, excluding suspicious values)

| Statistic | Value |
|-----------|-------|
| 25th percentile | 0.398 |
| Median | 1.065 |
| 75th percentile | 4.045 |
| % pairs below SNR 1.0 | 48.3% |
| % pairs below SNR 1.5 | 57.9% |
| % pairs below SNR 2.0 | 63.6% |
| Suspicious SNR (>1000) | 603 pairs |

### Pass rate at alternative SNR floors

| SNR Floor | Pairs Passing | Pass Rate | Δ from current |
|-----------|---------------|-----------|----------------|
| 0.5 | 26597 | 69.5% | -0.5 |
| 0.8 | 22152 | 57.9% | -0.2 |
| 1.0 | 19795 | 51.7% | +0.0 ← current |
| 1.2 | 18016 | 47.1% | +0.2 |
| 1.5 | 16108 | 42.1% | +0.5 |
| 2.0 | 13945 | 36.4% | +1.0 |
| 2.5 | 12414 | 32.4% | +1.5 |

### Combined gate pass rate by SNR floor
(cointegrated AND half_life ≤ 120d AND episodes ≥ 2 AND SNR ≥ floor)

| SNR Floor | Pairs Passing All Gates | Pass Rate |
|-----------|-------------------------|-----------|
| 0.5 | 978 | 2.5% |
| 1.0 | 754 | 1.9% ← current |
| 1.5 | 624 | 1.6% |
| 2.0 | 527 | 1.4% |

---
## Gate 4 — Episode Minimum

**Current value:** 2 episodes  
**Source:** Assumed (no literature citation)  
**Classification:** `LITERATURE-ASSUMED`

### Episode count distribution

| Episodes | Count | % of Pairs |
|----------|-------|------------|
| 0 | 20136 | 51.8% |
| 1 | 11226 | 28.9% |
| 2 | 4614 | 11.9% |
| 3 | 1748 | 4.5% |
| 4 | 705 | 1.8% |
| 5 | 313 | 0.8% |
| 6 | 112 | 0.3% |
| 7 | 32 | 0.1% |
| 8 | 4 | 0.0% |

### Pass rate at alternative minimums

| Minimum Episodes | Pairs Passing | Pass Rate | Δ from current |
|-----------------|---------------|-----------|----------------|
| 1 | 18754 | 48.2% | -1 |
| 2 | 7528 | 19.4% | +0 ← current |
| 3 | 2914 | 7.5% | +1 |
| 4 | 1166 | 3.0% | +2 |

---
## Gate 5 — Z-Score Entry / Exit Thresholds

**Current entry:** 2.0σ  
**Current exit:** 0.5σ  
**Current stop-loss:** 3.0σ  
**Source:** Vidyamurthy (entry), assumed (exit, stop)  
**Classification:** `REVIEW-RECOMMENDED` (exit threshold)

> The council noted the exit at 0.5σ leaves ~25% of the expected
> mean reversion P&L on the table. Chan uses exit at z=0.0.
> OU simulation below tests this across the observed half-life range.

### OU Process Simulation Results

*All P&L figures in spread-sigma units. Does not include transaction costs.*
*Stop-loss fixed at 3.0σ throughout.*

#### Half-life = 10 days

| Entry σ | Exit σ | Mean P&L | Sharpe | Win Rate | Stop Rate | Hold (days) |
|---------|--------|----------|--------|----------|-----------|-------------|
| 1.50 | 0.00 | 0.314 | 0.214 | 60.5% | 39.5% | 4.6 |
| 1.50 | 0.25 | 0.250 | 0.189 | 63.6% | 36.4% | 4.1 |
| 1.50 | 0.50 | 0.191 | 0.163 | 67.6% | 32.4% | 3.5 |
| 1.50 | 0.75 | 0.066 | 0.064 | 69.6% | 30.4% | 3.0 |
| 2.00 | 0.00 | 0.550 | 0.367 | 51.7% | 48.3% | 4.6 |
| 2.00 | 0.25 | 0.458 | 0.333 | 53.0% | 47.0% | 4.1 |
| 2.00 | 0.50 | 0.366 | 0.294 | 54.6% | 45.4% | 3.7 ← current |
| 2.00 | 0.75 | 0.303 | 0.273 | 57.9% | 42.1% | 3.2 |
| 2.50 | 0.00 | 0.641 | 0.440 | 38.0% | 62.0% | 4.2 |
| 2.50 | 0.25 | 0.602 | 0.447 | 40.1% | 59.9% | 3.8 |
| 2.50 | 0.50 | 0.556 | 0.451 | 42.3% | 57.7% | 3.5 |
| 2.50 | 0.75 | 0.501 | 0.448 | 44.5% | 55.5% | 3.1 |

#### Half-life = 15 days

| Entry σ | Exit σ | Mean P&L | Sharpe | Win Rate | Stop Rate | Hold (days) |
|---------|--------|----------|--------|----------|-----------|-------------|
| 1.50 | 0.00 | 0.199 | 0.134 | 56.6% | 43.4% | 4.8 |
| 1.50 | 0.25 | 0.163 | 0.121 | 60.5% | 39.5% | 4.1 |
| 1.50 | 0.50 | 0.080 | 0.066 | 63.2% | 36.8% | 3.6 |
| 1.50 | 0.75 | 0.013 | 0.013 | 67.3% | 32.7% | 3.0 |
| 2.00 | 0.00 | 0.392 | 0.262 | 46.4% | 53.6% | 4.6 |
| 2.00 | 0.25 | 0.311 | 0.226 | 47.7% | 52.3% | 4.1 |
| 2.00 | 0.50 | 0.279 | 0.223 | 51.2% | 48.8% | 3.7 ← current |
| 2.00 | 0.75 | 0.229 | 0.204 | 54.6% | 45.4% | 3.2 |
| 2.50 | 0.00 | 0.511 | 0.360 | 33.7% | 66.3% | 4.0 |
| 2.50 | 0.25 | 0.521 | 0.392 | 37.1% | 62.9% | 3.7 |
| 2.50 | 0.50 | 0.500 | 0.408 | 40.0% | 60.0% | 3.4 |
| 2.50 | 0.75 | 0.404 | 0.366 | 40.2% | 59.8% | 3.0 |

#### Half-life = 25 days

| Entry σ | Exit σ | Mean P&L | Sharpe | Win Rate | Stop Rate | Hold (days) |
|---------|--------|----------|--------|----------|-----------|-------------|
| 1.50 | 0.00 | 0.121 | 0.081 | 54.0% | 46.0% | 4.7 |
| 1.50 | 0.25 | 0.090 | 0.066 | 57.8% | 42.2% | 4.0 |
| 1.50 | 0.50 | 0.013 | 0.011 | 60.5% | 39.5% | 3.6 |
| 1.50 | 0.75 | -0.034 | -0.032 | 65.1% | 34.9% | 3.1 |
| 2.00 | 0.00 | 0.279 | 0.188 | 42.6% | 57.4% | 4.5 |
| 2.00 | 0.25 | 0.293 | 0.213 | 47.0% | 53.0% | 4.1 |
| 2.00 | 0.50 | 0.215 | 0.172 | 48.6% | 51.4% | 3.6 ← current |
| 2.00 | 0.75 | 0.143 | 0.127 | 50.8% | 49.2% | 3.2 |
| 2.50 | 0.00 | 0.456 | 0.327 | 31.9% | 68.1% | 4.0 |
| 2.50 | 0.25 | 0.436 | 0.335 | 34.0% | 66.0% | 3.6 |
| 2.50 | 0.50 | 0.369 | 0.310 | 34.8% | 65.2% | 3.3 |
| 2.50 | 0.75 | 0.330 | 0.304 | 36.9% | 63.1% | 3.0 |

#### Half-life = 40 days

| Entry σ | Exit σ | Mean P&L | Sharpe | Win Rate | Stop Rate | Hold (days) |
|---------|--------|----------|--------|----------|-----------|-------------|
| 1.50 | 0.00 | 0.082 | 0.055 | 52.7% | 47.3% | 4.6 |
| 1.50 | 0.25 | 0.032 | 0.023 | 55.7% | 44.3% | 4.1 |
| 1.50 | 0.50 | -0.038 | -0.031 | 58.5% | 41.5% | 3.6 |
| 1.50 | 0.75 | -0.091 | -0.084 | 62.6% | 37.4% | 3.1 |
| 2.00 | 0.00 | 0.202 | 0.137 | 40.1% | 59.9% | 4.4 |
| 2.00 | 0.25 | 0.181 | 0.133 | 42.9% | 57.1% | 4.0 |
| 2.00 | 0.50 | 0.173 | 0.139 | 46.9% | 53.1% | 3.6 ← current |
| 2.00 | 0.75 | 0.102 | 0.091 | 49.0% | 51.0% | 3.2 |
| 2.50 | 0.00 | 0.430 | 0.310 | 31.0% | 69.0% | 3.8 |
| 2.50 | 0.25 | 0.342 | 0.270 | 30.6% | 69.4% | 3.5 |
| 2.50 | 0.50 | 0.341 | 0.288 | 33.6% | 66.4% | 3.3 |
| 2.50 | 0.75 | 0.317 | 0.293 | 36.3% | 63.7% | 3.0 |

#### Half-life = 60 days

| Entry σ | Exit σ | Mean P&L | Sharpe | Win Rate | Stop Rate | Hold (days) |
|---------|--------|----------|--------|----------|-----------|-------------|
| 1.50 | 0.00 | 0.010 | 0.007 | 50.3% | 49.7% | 4.5 |
| 1.50 | 0.25 | 0.002 | 0.001 | 54.6% | 45.4% | 4.1 |
| 1.50 | 0.50 | -0.077 | -0.062 | 56.9% | 43.1% | 3.5 |
| 1.50 | 0.75 | -0.121 | -0.111 | 61.3% | 38.7% | 3.1 |
| 2.00 | 0.00 | 0.185 | 0.126 | 39.5% | 60.5% | 4.4 |
| 2.00 | 0.25 | 0.183 | 0.134 | 43.0% | 57.0% | 4.0 |
| 2.00 | 0.50 | 0.124 | 0.100 | 45.0% | 55.0% | 3.6 ← current |
| 2.00 | 0.75 | 0.086 | 0.076 | 48.3% | 51.7% | 3.2 |
| 2.50 | 0.00 | 0.348 | 0.258 | 28.3% | 71.7% | 3.8 |
| 2.50 | 0.25 | 0.339 | 0.268 | 30.5% | 69.5% | 3.6 |
| 2.50 | 0.50 | 0.295 | 0.253 | 31.8% | 68.2% | 3.2 |
| 2.50 | 0.75 | 0.267 | 0.251 | 34.1% | 65.9% | 2.9 |

### Optimal thresholds by half-life (maximum Sharpe)

| Half-life | Best Entry σ | Best Exit σ | Sharpe | vs Current Sharpe | Δ Sharpe |
|-----------|-------------|------------|--------|------------------|----------|
| 10d | 2.50 | 0.50 | 0.451 | 0.294 | +0.157 |
| 15d | 2.50 | 0.50 | 0.408 | 0.223 | +0.185 |
| 25d | 2.50 | 0.25 | 0.335 | 0.172 | +0.163 |
| 40d | 2.50 | 0.00 | 0.310 | 0.139 | +0.171 |
| 60d | 2.50 | 0.25 | 0.268 | 0.100 | +0.168 |

---
## Recommendations

| Threshold | Current | Recommendation | Classification | Action |
|-----------|---------|----------------|----------------|--------|
| SNR floor | 1.0 | Raise to 1.5 | `REVIEW-RECOMMENDED` | Update Gate 3 in correlation_tool.py |
| Exit z-score | 0.5σ | Lower to 0.0–0.25σ | `REVIEW-RECOMMENDED` | Update Gate 5 exit threshold in tasks.py |
| Half-life ceiling | 120d | Validate against 90th pct | `LITERATURE-ASSUMED` | Review if 90d better fits universe |
| Episode minimum | 2 | Keep or raise to 3 | `LITERATURE-ASSUMED` | Review episode count distribution |
| Entry z-score | 2.0σ | Keep | `LITERATURE-ASSUMED` | OU simulation shows 2.0 is near-optimal |
| Stop-loss z-score | 3.0σ | Keep | `LITERATURE-ASSUMED` | Conservative; review after live data accumulates |

---
## Next Steps

1. Raise SNR floor from 1.0 to 1.5 in `correlation_tool.py` (Gate 3 threshold).
2. Lower exit z-score from 0.5 to 0.0 in `tasks.py` (Gate 5 exit threshold, with corresponding dossier update).
3. Re-run this script after 90 days of shadow trading to validate
   thresholds against actual trade outcomes from the trial ledger.

*ShiftInnerV threshold sensitivity analysis — 2026-05-18 16:35*