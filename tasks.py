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
      PASS: half-life is between 1 and 120 days
      FAIL -> REJECT: "Half-life [value] days exceeds 120-day tradeable
                       horizon. Spread reverts too slowly for actionable signal."
      N/A -> REJECT: "Lambda >= 0. Spread is non-mean-reverting."

    Gate 3 — SNR
      PASS: SNR >= 1.0 (MODERATE or STRONG tier)
      FAIL -> REJECT: "SNR [value] < 1.0. Nonstationary drift dominates
                       mean-reverting signal. Pair fails tradability threshold."

    Gate 4 — Episode persistence
      PASS: 2 or more distinct decoupling episodes
      FAIL -> MONITOR: "Only [n] episode(s) detected over the data range.
                        Insufficient evidence of persistent structural pattern.
                        Re-evaluate in 30 days."

    Gate 5 — All gates pass -> ACTIVE

    STEP 3 — For ACTIVE verdicts only, compute and report:
    - Entry threshold: spread z-score >= 2.0 standard deviations from mean
    - Exit threshold: spread z-score <= 0.5 standard deviations from mean
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
    Gate 1 Cointegration: [PASS/FAIL] — [one line explanation with numbers]
    Gate 2 Half-life:     [PASS/FAIL] — [one line explanation with numbers]
    Gate 3 SNR:           [PASS/FAIL] — [one line explanation with numbers]
    Gate 4 Episodes:      [PASS/FAIL/N/A] — [one line explanation with numbers]

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
