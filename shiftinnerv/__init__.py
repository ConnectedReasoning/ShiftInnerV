"""
ShiftInnerV — Quantitative pairs trading platform.

Package layout (in progress, per Council Roadmap reorganization):

    shiftinner/
        sensors/    — Stateless evaluators: correlation, gates, regime,
                      cost model, position monitor, composition monitor.

    (Future):
        domain/     — Pure data shapes and math (Pair, SNR, regime classification).
        services/   — I/O-facing: data_manager, ledger.
        pipelines/  — Multi-step workflows: monitor, dossier, summarize.
        utils/      — Stateless helpers.
"""
