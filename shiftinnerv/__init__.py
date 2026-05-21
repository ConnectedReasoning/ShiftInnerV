"""
ShiftInnerV — Quantitative pairs trading platform.

Package layout:

    shiftinnerv/
        domain/     — Pure logic and data shapes. No I/O.
                      gate_evaluator, cost_model, spread_math,
                      position_math, regime_math.

        sensors/    — Evaluators that read data to produce verdicts.
                      correlation, position_monitor, regime_monitor,
                      composition_monitor.

        services/   — I/O boundary: data loading and persistence.
                      data_manager (CSV/yfinance), trial_ledger (SQLite).

    (Future, step 5):
        pipelines/  — Multi-step workflows: monitor, dossier, summarize.

Entry points (root-level):
    main.py, sentinel.py, run_all.py, promote.py

Dependency rule (innermost to outermost):
    domain ← sensors ← services ← pipelines ← entry points

A module at any layer may import from layers to its left.
A module must never import from a layer to its right.
"""
