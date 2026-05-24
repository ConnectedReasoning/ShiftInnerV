"""
shiftinnerv/pipelines/narrative_router.py
Item 21 — Complexity Router for Narrative Pipeline

Computes a complexity score for a pair result to guide narrative depth.
Higher scores indicate that the LLM narrative agent should produce a more
detailed, context-rich assessment.

The complexity score is additive: each trigger adds 1 point.
Currently used to log and expose routing intent; wiring to narrative
depth selection is left to the caller.
"""

from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def compute_complexity_score(result: dict) -> tuple[int, list[str]]:
    """
    Compute a complexity score for a pair's analysis result.

    Parameters
    ----------
    result : dict
        Pair analysis dict. Expected optional keys:
          - snr              : float — signal-to-noise ratio
          - episodes         : int   — number of distinct decoupling episodes
          - half_life        : float — spread half-life in days
          - mean_drift       : bool  — whether mean drift was detected
          - factor_loading   : float — common factor loading
          - cb_decision_recent : bool — CB decision within 7 days (Item 21)
          - macro_surprise     : bool — BEAT/MISS in calendar data  (Item 21)

    Returns
    -------
    (score, reasons) : (int, list[str])
        score   — total complexity points
        reasons — human-readable list of triggered factors
    """
    score   = 0
    reasons = []

    # ── Pre-existing complexity triggers ────────────────────────────────────

    snr = result.get("snr")
    if snr is not None and 1.0 <= snr < 1.5:
        score += 1
        reasons.append(f"borderline_snr ({snr:.3f} in 1.0–1.5 range)")

    episodes = result.get("episodes")
    if isinstance(episodes, int) and episodes == 2:
        score += 1
        reasons.append(f"minimal_episode_count ({episodes} — exactly at threshold)")

    half_life = result.get("half_life")
    if half_life is not None and 80 <= half_life <= 120:
        score += 1
        reasons.append(f"near_horizon_half_life ({half_life:.1f}d approaching 120d limit)")

    if result.get("mean_drift") is True:
        score += 1
        reasons.append("mean_drift_detected (spread mean has shifted >2σ)")

    factor_loading = result.get("factor_loading")
    if factor_loading is not None and abs(factor_loading) > 0.7:
        score += 1
        reasons.append(f"high_factor_loading ({factor_loading:.3f} > 0.7)")

    # ── Item 21 — News & Macro context triggers ──────────────────────────────

    if result.get("cb_decision_recent") is True:
        score += 1
        reasons.append("cb_decision_within_7_days (central bank text requires interpretation)")

    if result.get("macro_surprise") is True:
        score += 1
        reasons.append("macro_data_surprise (beat/miss in relevant calendar event)")

    if reasons:
        log.debug(
            f"[narrative_router] complexity_score={score} "
            f"triggers=[{', '.join(reasons)}]"
        )

    return score, reasons


def route_narrative_depth(score: int) -> str:
    """
    Map a complexity score to a narrative depth label.

    Returns one of: "STANDARD", "ELEVATED", "DEEP"

    Intended for use by the narrative agent or report formatter
    to scale the depth of qualitative commentary.
    """
    if score == 0:
        return "STANDARD"
    if score <= 2:
        return "ELEVATED"
    return "DEEP"
