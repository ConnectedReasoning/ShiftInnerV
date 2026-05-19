from crewai import Task


def build_tasks(pair: dict, agents: tuple) -> tuple:
    """
    Build the two-task crew pipeline for a single pair.

    Architecture: three-agent pipeline (Scout / Researcher / Skeptic) has
    been replaced by a two-agent pipeline (Scout / Signal Mathematician).
    The Researcher is retired. All judgment is purely quantitative.

    Parameters
    ----------
    pair : dict
        A single entry from pairs.yaml — uses ticker1, ticker2, label only.
        The relationship, notes, and leading_indicators fields are ignored
        by the agents — they are retained in yaml for human reference only.
    agents : tuple
        (quant_scout, signal_mathematician)

    Returns
    -------
    tuple of (correlation_audit, quant_assessment)
    """
    quant_scout, signal_mathematician = agents

    ticker1        = pair["ticker1"]
    ticker2        = pair["ticker2"]
    label          = pair["label"]
    lookback_years = pair.get("lookback_years", 5)

    # ── Task 1 — Run the math ─────────────────────────────────────────────────
    correlation_audit = Task(
        description=f"""Run the correlation_decay_analyzer tool with
    ticker1='{ticker1}' and ticker2='{ticker2}'.

    After the tool returns, copy its COMPLETE output text into your
    final answer exactly as-is. Do not summarize it. Do not reformat it.
    Do not call any other tool. The tool output starts with the line:
    === CORRELATION DECAY REPORT ===
    Your final answer must start with that same line.""",
        expected_output="""The complete verbatim text output from the
    correlation_decay_analyzer tool, starting with:
    === CORRELATION DECAY REPORT ===
    Include every line the tool returned, unchanged.""",
        agent=quant_scout
    )

    # ── Task 2 — Apply the decision framework ─────────────────────────────────
    quant_assessment = Task(
        description=f"""You are the Signal Mathematician. Apply the quantitative
    decision framework to the Scout's correlation report for {label}
    ({ticker1} / {ticker2}) — {lookback_years}-year lookback.

    STEP 1 — Extract these values from the Scout's report:
    - Johansen cointegration result: YES or NO
    - Johansen trace statistic and 95% critical value
    - Half-life of spread mean reversion in days (or N/A)
    - SNR pair score (numeric value) and tier (STRONG / MODERATE / WEAK)
    - Number of distinct decoupling episodes
    - Mean drift flag (TRUE or FALSE) and deviation in sigma
    - For each episode: onset date, duration, worst correlation, worst deviation

    STEP 2 — Apply the hard gates in sequence. Stop at the first failure
    and assign REJECT. Do not skip gates or apply judgment to override them.

    GATE LABEL RULES — apply silently, do not print these rules in your output:
    - Gate label must exactly match the outcome: never write PASS when the value fails the threshold.
    - Gate 2 PASS requires half-life between 1 and 120 days AND lambda < 0.
    - Gate 4 label is N/A when any of Gates 1-3 resulted in FAIL or REJECT.
    - Gate 4 label is FAIL when gates 1-3 passed but fewer than 2 episodes found.
    - Gate 4 label is PASS only when gates 1-3 all passed AND 2+ episodes found.

    Gate 1 — Cointegration
      The Scout's report shows results at 90%, 95%, and 99% CI.
      PASS: 95% CI result is PASS
      MONITOR-NEAR: 95% CI is FAIL but 90% CI is PASS -> assign MONITOR-NEAR:
                    "Passes at 90% CI (trace {{value}} > crit {{value}}) but fails
                     at 95% CI. Near-cointegrated. Re-evaluate in 30 days or
                     on shorter lookback."
      FAIL -> REJECT: "Johansen failed at all CI levels. Trace [value] < 90%
                       critical value [value]. No structural tether confirmed."

    Gate 2 — Half-life
      Evaluate in this order:
      Step A — If the report shows "N/A" or "lambda >= 0": label FAIL.
               Write: "FAIL — Lambda >= 0. Spread is non-mean-reverting."
      Step B — If half-life numeric value > 120 days: label FAIL.
               Write: "FAIL — Half-life [value]d exceeds 120-day tradeable horizon."
      Step C — If half-life is between 1 and 120 days: label PASS.
               Write: "PASS — Half-life [value]d is within the tradeable horizon."
      CRITICAL: Never write PASS if half-life exceeds 120 days or lambda >= 0.

    Gate 3 — SNR
      PASS: SNR >= 1.5 (STRONG tier, or a max value of 99.9999 indicating a near-flat trend)
        Write: "PASS — SNR [value] >= 1.5 ([tier] tier)."
      MONITOR-LOW-SNR: SNR >= 1.0 and SNR < 1.5
        Write: "MONITOR-LOW-SNR — SNR [value] is above the bare-minimum threshold (1.0)
                but below the evidence-based floor (1.5). Signal and noise are near-equal.
                Re-evaluate after 30 days or when more data is available."
      FAIL: SNR < 1.0
        Write: "FAIL — SNR [value] < 1.0. Nonstationary drift dominates mean-reverting signal."
      NOTE: The floor of 1.5 is evidence-based (threshold_sensitivity_report.md, May 2026).
            SNR=1.0 (Vidyamurthy literature default) means signal and noise are equal —
            barely tradeable. Council recommended 1.5–2.0; 1.5 adopted as initial floor.

    Gate 4 — Episode persistence
      IMPORTANT: Only evaluate Gate 4 if Gates 1, 2, and 3 all passed.
      If any prior gate was FAIL or REJECT, write "N/A — prior gate failed." for Gate 4.
      If evaluating:
        PASS: 2 or more distinct decoupling episodes detected.
          Write: "PASS — [n] distinct episodes detected."
        FAIL: fewer than 2 episodes.
          Write: "FAIL — Only [n] episode(s) detected. Insufficient evidence of persistent pattern."
      Note: Gate 4 FAIL results in MONITOR verdict, not REJECT.
      CRITICAL: Never write PASS if fewer than 2 episodes were detected.

    Gate 5 — All gates pass (Gates 1-4) -> provisionally ACTIVE

    Gate 6 — Common Factor Exposure (from Scout report section
              "=== GATE 6 — COMMON FACTOR EXPOSURE ===")
      PASS:                 proceed; ACTIVE verdict stands
      FACTOR_CONTAMINATED:  downgrade to MONITOR regardless of Gate 1-4 results.
                            Note: "Cointegration may be factor-driven.
                            Pair capped at MONITOR pending sector
                            concentration review."
      SKIPPED_*:            proceed on Gates 1-4; add note in verdict:
                            "Gate 6 factor diagnostic unavailable. Manual
                             sector check recommended before entry."

    STEP 3 — For ACTIVE verdicts only, compute and report:
    - Entry threshold: spread z-score >= 2.0 standard deviations from mean
    - Exit threshold: spread z-score <= 0.25 standard deviations from mean
      (raised from 0.5 per threshold_sensitivity_report.md May 2026;
       OU simulation shows exit=0.25 delivers +0.15–0.17 Sharpe improvement
       across all tested half-lives vs. the prior 0.5 exit)
    - Stop-loss threshold: spread z-score >= 3.0 standard deviations
    - Expected holding period: [half-life] trading days
    - Position sizing note: weight inversely proportional to half-life;
      a 30-day half-life pair receives 2x the weight of a 60-day half-life pair
      at equal SNR.

    STEP 4 — Report mean drift impact:
    - If mean_drift is TRUE: note that the spread's rolling mean has shifted
      [deviation]sigma from the full-sample mean. Entry thresholds should be
      recalibrated to the current rolling mean, not the full-sample mean.
    - If mean_drift is FALSE: thresholds computed from full-sample mean are valid.

    STEP 5 — Write the final verdict block in this exact format:

    === QUANTITATIVE ASSESSMENT ===
    Pair: {ticker1} / {ticker2}
    Label: {label}

    GATE RESULTS:
    Gate 1 Cointegration: [PASS/FAIL/MONITOR-NEAR] — [one line with trace stat and critical value]
    Gate 2 Half-life:     [PASS/FAIL] — [one line with numeric half-life value and threshold]
    Gate 3 SNR:           [PASS/FAIL] — [one line with numeric SNR value and tier]
    Gate 4 Episodes:      [PASS/FAIL/N/A] — [one line with episode count; N/A if prior gate failed]
    Gate 6 Factor Exposure: [PASS/FACTOR_CONTAMINATED/SKIPPED] — [one line]

    VERDICT: [REJECT / MONITOR / ACTIVE]

    [If REJECT: one paragraph stating which gate failed and why the pair
     is not tradeable. No speculation about cause.]

    [If MONITOR: one paragraph stating what passed, what triggered MONITOR,
     and the specific numeric condition required for upgrade to ACTIVE.
     State the re-evaluation date as today + 30 days.]

    [If ACTIVE: trading parameter block as computed in Step 3, plus
     one paragraph assessing episode consistency — are the episodes
     similar in duration and severity, or erratic?]

    MEAN DRIFT: [impact statement from Step 4]
    ====""",
        expected_output=f"""A structured quantitative assessment of {label}
    in the exact format specified, ending with a verdict of REJECT, MONITOR,
    or ACTIVE. All claims referenced to numeric values from the Scout's report.
    No narrative, no business context, no speculation about causes.""",
        agent=signal_mathematician,
        context=[correlation_audit]
    )

    return correlation_audit, quant_assessment
