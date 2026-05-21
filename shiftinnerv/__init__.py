"""
ShiftInnerV — Quantitative pairs trading platform.

Package layout (in progress, per Council Roadmap reorganization):

    shiftinnerv/
        domain/     — Pure logic and data shapes. No I/O.
                      Modules: gate_evaluator, cost_model, spread_math.
        sensors/    — Evaluators that touch the outside world (DB, network, files).
                      Modules: correlation, position_monitor, regime_monitor,
                               composition_monitor.

    (Future, per step 3+):
        services/   — I/O-facing: data_manager, ledger.
        pipelines/  — Multi-step workflows: monitor, dossier, summarize.
        utils/      — Stateless helpers.

Dependency rule: domain knows nothing about sensors or anything above.
Sensors can import from domain. Pipelines can import from both.
If you ever feel the urge to import something from a higher layer into
a lower one, you have a smell.
"""
