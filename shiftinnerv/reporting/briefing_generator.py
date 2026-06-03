"""
ShiftInnerV Sentinel Briefing Generator — Skew Signal Strategy

Generates a clean daily briefing based solely on the options skew signal.
No pairs trading, no cointegration, no agent verdicts.
"""

from datetime import datetime, timedelta
from typing import Dict, List, Optional


def generate_sentinel_briefing(
    regime_state: str,
    regime_vix: float,
    regime_multiplier: float,
    sourced_pairs: List[Dict],       # unused — kept for call-site compatibility
    screening_counts: Dict[str, int], # unused
    verdicts: Dict[str, int],         # unused
    rejected_pairs: List[Dict],       # unused
    open_positions: int,
    universe_name: str = "Dow Skew",
    skew_signals: Optional[List] = None,
    ticker_names: Optional[Dict[str, str]] = None,
) -> str:

    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    next_run  = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")

    regime_icon = {
        "NORMAL":      "✓",
        "ELEVATED":    "⚠️",
        "HIGH_STRESS": "⚠️",
        "CRISIS":      "🔴",
    }.get(regime_state, "?")

    regime_description = {
        "NORMAL":      "Market volatility is low and stable. Full position sizing enabled.",
        "ELEVATED":    "Market stress is rising. Position sizing reduced to 50%.",
        "HIGH_STRESS": "Significant market stress detected. Position sizing at 25%.",
        "CRISIS":      "CRISIS regime active (VIX ≥ 40). New trade entries are HALTED.",
    }.get(regime_state, "Unknown regime state.")

    regime_status = {
        "NORMAL":      "Conditions stable — full position sizing active",
        "ELEVATED":    "Elevated stress — position sizing reduced to 50%",
        "HIGH_STRESS": "High stress — 25% sizing only",
        "CRISIS":      "CRISIS regime — monitoring only, new entries halted",
    }.get(regime_state, "Unknown regime")

    skew_signals = skew_signals or []
    actionable   = [s for s in skew_signals if s.signal in ("SHORT", "LONG")]
    warming_up   = [s for s in skew_signals if s.signal == "INSUFFICIENT_DATA"]
    shorts       = [s for s in actionable if s.signal == "SHORT"]
    longs        = [s for s in actionable if s.signal == "LONG"]

    lines = []

    # ── Header ────────────────────────────────────────────────────────────────
    lines.append("# ShiftInnerV Sentinel Briefing")
    lines.append("")
    lines.append(f"**{timestamp}** | Universe: {universe_name}")
    lines.append("")
    lines.append("> **Purpose:** Daily options skew signal scan. Identifies stocks where the options market is pricing in stress or calm that the equity price has not yet acknowledged.")
    lines.append("")

    # ── Market Regime ─────────────────────────────────────────────────────────
    lines.append("## 📊 Market Regime")
    lines.append("")
    lines.append("| Signal | Value | Definition |")
    lines.append("|--------|-------|-----------|")
    lines.append(f"| VIX | {regime_vix:.1f} | Volatility Index. <20 = calm, 20–30 = elevated, >30 = stress. |")
    lines.append(f"| Regime | {regime_state} {regime_icon} | Market stress classification. Determines position sizing. |")
    lines.append(f"| Position Multiplier | {regime_multiplier:.2f}x | 1.0x = full size, 0.5x = half, 0.25x = quarter. |")
    lines.append("")
    lines.append(f"> {regime_icon} **{regime_status}**")
    lines.append(">")
    lines.append(f"> {regime_description}")
    lines.append("")

    # ── Skew Signals ──────────────────────────────────────────────────────────
    lines.append("## 🎯 Skew Signals")
    lines.append("")
    lines.append("**Purpose:** Stocks where put skew z-score exceeds ±1.0 — options market diverging from equity price.")
    lines.append("")
    lines.append(f"- **Universe:** {universe_name}")
    lines.append(f"- **Method:** Rolling 10-day z-score of normalised put skew (OTM IV / ATM IV, SPY-normalised)")
    lines.append(f"- **Entry:** z > +1.0 → SHORT | z < -1.0 → LONG")
    lines.append(f"- **Exit:** z-score reverts to 0, or 5-day time stop")
    lines.append("")

    if actionable:
        lines.append(f"**{len(actionable)} Actionable Signal(s) — {len(shorts)} SHORT, {len(longs)} LONG:**")
        lines.append("")
        lines.append("| Ticker | Company | Signal | Z-Score | Norm Skew | History |")
        lines.append("|--------|---------|--------|---------|-----------|---------|")
        names = ticker_names or {}
        for s in sorted(actionable, key=lambda x: abs(x.z_score or 0), reverse=True):
            arrow   = "⬇ SHORT" if s.signal == "SHORT" else "⬆ LONG"
            z_str   = f"{s.z_score:+.2f}" if s.z_score is not None else "N/A"
            ns_str  = f"{s.norm_skew:.3f}" if s.norm_skew is not None else "N/A"
            company = names.get(s.ticker, s.ticker)
            lines.append(f"| {s.ticker} | {company} | {arrow} | {z_str} | {ns_str} | {s.history_days}d |")
        lines.append("")
    else:
        lines.append("**No actionable signals today** — all tickers within normal skew range.")
        lines.append("")

    if warming_up:
        min_hist  = min(s.history_days for s in warming_up)
        days_left = max(0, 10 - min_hist)
        lines.append(f"> ⏳ **{len(warming_up)} ticker(s) warming up** — {days_left} more trading day(s) until z-score baseline is established.")
        lines.append("")

    # ── Position Status ───────────────────────────────────────────────────────
    lines.append("## 📈 Position Status")
    lines.append("")
    lines.append(f"- **Open positions:** **{open_positions}**")
    lines.append(f"- **Monitoring:** {'✓ Active' if open_positions > 0 else 'Inactive (no open positions)'}")
    lines.append(f"- **Next screening:** Scheduled for {next_run} (daily cycle)")
    lines.append("")

    # ── Recommended Action ────────────────────────────────────────────────────
    lines.append("## ⚡ Recommended Action")
    lines.append("")

    if regime_state == "CRISIS":
        lines.append("**🛑 HALT**")
        lines.append(f"> CRISIS regime (VIX {regime_vix:.1f} ≥ 40). No new entries. Monitor open positions only.")
    elif actionable:
        names = ticker_names or {}
        def _label(s):
            company = names.get(s.ticker)
            return f"{s.ticker} ({company})" if company and company != s.ticker else s.ticker
        ticker_summary = "  |  ".join(
            ([f"⬇ SHORT: {', '.join(_label(s) for s in shorts)}"] if shorts else []) +
            ([f"⬆ LONG: {', '.join(_label(s) for s in longs)}"] if longs else [])
        )
        lines.append("**📊 ACT**")
        lines.append(f"> {len(actionable)} signal(s) ready: {ticker_summary}")
        lines.append(f"> Respect position multiplier ({regime_multiplier:.2f}x).")
    elif warming_up:
        days_left = max(0, 10 - min(s.history_days for s in warming_up))
        lines.append("**⏳ WARMING UP**")
        lines.append(f"> No signals yet — {days_left} more trading day(s) until z-score baseline is established.")
    elif open_positions > 0:
        lines.append("**⏸ HOLD**")
        lines.append(f"> {open_positions} position(s) open. No new signals. Monitor for exit conditions.")
    else:
        lines.append("**⏸ WAIT**")
        lines.append(f"> No signals, no open positions. Market stable (VIX {regime_vix:.1f}). Await next cycle.")

    lines.append("")

    # ── Reference ─────────────────────────────────────────────────────────────
    lines.append("---")
    lines.append("")
    lines.append("## 📚 Reference: Key Metrics")
    lines.append("")
    lines.append("- **Norm Skew:** OTM put IV / ATM put IV for a ticker, divided by SPY's same ratio. Strips market-wide fear, leaving company-specific stress. >1.0 = more fearful than market.")
    lines.append("- **Z-Score:** How many standard deviations today's norm skew is from its own 10-day average. >+1.0 triggers SHORT, <-1.0 triggers LONG.")
    lines.append("- **VIX:** S&P 500 implied volatility. Regime multiplier scales position size under stress.")
    lines.append("- **Time Stop:** Maximum 5-day hold. Closes position regardless of z-score to limit exposure.")
    lines.append("")

    # ── Footer ────────────────────────────────────────────────────────────────
    lines.append("---")
    lines.append(f"*Generated {timestamp} by ShiftInnerV Sentinel*")
    lines.append(f"*Next report: {next_run} | Universe: {universe_name}*")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("📊 **Portfolio Dashboard**: [http://localhost:8766](http://localhost:8766)")

    return "\n".join(lines)


def format_rejected_pair(ticker1: str, ticker2: str, gate_failure: str, details: str) -> Dict:
    """Retained for import compatibility — unused in skew strategy."""
    return {"pair": f"{ticker1}/{ticker2}", "reason": f"Gate {gate_failure} — {details}"}
