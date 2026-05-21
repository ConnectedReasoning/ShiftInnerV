"""
shiftinnerv.sensors — Stateless evaluation modules.

A sensor takes inputs (prices, ledger state, market data) and returns a
verdict, score, or decision. Sensors do not own state across runs; they
read what they need each invocation and return a result.

Modules:
    correlation         — OLS-based correlation and cointegration analysis.
    cost_model          — Spread cost and slippage estimates.
    gate_evaluator      — Numerical gate evaluation for verdict assignment.
    position_monitor    — Open-position SNR revalidation and drift detection.
    regime_monitor      — Market regime classification (VIX + pair-SPY corr).
    composition_monitor — Concentration limits per composition category.
"""
