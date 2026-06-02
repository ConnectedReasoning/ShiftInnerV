"""
Sentinel Run Briefing Generator

Generates a human-readable summary of sentinel execution at end of run.
Uses Ollama for local synthesis (no API calls, fast execution).
"""

import json
from datetime import datetime
from typing import Dict, List, Optional
import subprocess


def call_ollama(prompt: str, model: str = "mistral") -> str:
    """
    Call Ollama locally to generate briefing.
    Falls back gracefully if Ollama is not running.
    
    Args:
        prompt: The prompt to send to Ollama
        model: Ollama model to use (default: mistral for speed)
    
    Returns:
        Generated text from Ollama, or empty string if unavailable
    """
    try:
        result = subprocess.run(
            ["ollama", "run", model],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=15
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        else:
            return ""  # Silent fallback
    except (FileNotFoundError, subprocess.TimeoutExpired, Exception):
        return ""  # Silent fallback — Ollama not available


def generate_sentinel_briefing(
    regime_state: str,
    regime_vix: float,
    regime_multiplier: float,
    sourced_pairs: List[Dict],
    screening_counts: Dict[str, int],
    verdicts: Dict[str, int],
    rejected_pairs: List[Dict],
    open_positions: int,
    universe_name: str = "Dow Skew",
    skew_signals: Optional[List] = None,
) -> str:
    """
    Generate a structured briefing for end-of-sentinel-run.
    Styled like StratixCap: emoji headers, tables, clean markdown.
    Includes contextual explanations and actionable insights.
    
    Args:
        regime_state: Market regime (NORMAL, ELEVATED, HIGH_STRESS, CRISIS)
        regime_vix: Current VIX level
        regime_multiplier: Position size multiplier (0.0-1.0)
        sourced_pairs: List of top sourced pairs [{'ticker1', 'ticker2', 'score', 'corr'}]
        screening_counts: Dict with counts {'PRIME': n, 'STRONG': n, ...}
        verdicts: Dict with verdict counts {'active': n, 'monitor': n, 'reject': n}
        rejected_pairs: List of rejected pairs with reasons [{'pair', 'reason'}]
        open_positions: Number of currently open positions
    
    Returns:
        Formatted briefing string (markdown)
    """
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    
    # Determine regime icon and status
    regime_icon = {
        "NORMAL": "✓",
        "ELEVATED": "⚠️",
        "HIGH_STRESS": "⚠️",
        "CRISIS": "🔴",
    }.get(regime_state, "?")
    
    regime_description = {
        "NORMAL": "Market volatility is low and stable. Full position sizing enabled. Safe to initiate new trades.",
        "ELEVATED": "Market stress is rising. Position sizing reduced to 50%. Use caution with new entries; prioritize existing position management.",
        "HIGH_STRESS": "Significant market stress detected. Only SNR ≥ 2.0 pairs accepted. Position sizing at 25%. New entries only for strongest signals.",
        "CRISIS": "CRISIS regime active (VIX ≥ 40). New trade entries are HALTED. Monitoring open positions only. Manual intervention may be required.",
    }.get(regime_state, "Unknown regime state.")
    
    regime_status = {
        "NORMAL": "Conditions stable — full position sizing active",
        "ELEVATED": "Elevated stress — position sizing reduced to 50%",
        "HIGH_STRESS": "High stress — SNR ≥ 2.0 pairs only, 25% sizing",
        "CRISIS": "CRISIS regime — monitoring only, new entries halted",
    }.get(regime_state, "Unknown regime")
    
    # Build markdown
    lines = []
    
    # Header with purpose
    lines.append("# ShiftInnerV Sentinel Briefing")
    lines.append("")
    lines.append(f"**{timestamp}** | Universe: {universe_name}")
    lines.append("")
    lines.append("> **Purpose:** Daily options skew signal scan. Identifies Dow stocks where the options market is pricing in stress or calm that the equity price has not yet acknowledged.")
    lines.append("")

    # Market Regime Section (Table)
    lines.append("## 📊 Market Regime")
    lines.append("")
    lines.append("| Signal | Value | Definition |")
    lines.append("|--------|-------|-----------|")
    lines.append(f"| VIX | {regime_vix:.1f} | Volatility Index (S&P 500). Measures market fear/uncertainty. <20 = calm, 20-30 = elevated, >30 = stress. |")
    lines.append(f"| Regime | {regime_state} {regime_icon} | Market stress level classification. Determines position sizing risk. |")
    lines.append(f"| Position Multiplier | {regime_multiplier:.2f}x | Risk adjustment factor. Reduces trade size in stressed markets. 1.0x = full size, 0.5x = half size, 0.25x = quarter size. |")
    lines.append("")
    lines.append(f"> {regime_icon} **{regime_status}**")
    lines.append(f"> ")
    lines.append(f"> {regime_description}")
    lines.append("")
    
    # Skew Signals Section
    lines.append("## 🎯 Skew Signals")
    lines.append("")
    lines.append("**Purpose:** Stocks where put skew z-score exceeds ±1.0 — options market diverging from equity price.")
    lines.append("")
    lines.append(f"- **Universe:** {universe_name}")
    lines.append(f"- **Method:** Rolling 10-day z-score of normalised put skew (OTM IV / ATM IV, SPY-normalised)")
    lines.append(f"- **Entry threshold:** z-score > +1.0 → SHORT | z-score < -1.0 → LONG")
    lines.append(f"- **Exit:** z-score reverts to 0, or 5-day time stop")
    lines.append("")

    skew_signals = skew_signals or []
    actionable   = [s for s in skew_signals if s.signal in ("SHORT", "LONG")]
    warming_up   = [s for s in skew_signals if s.signal == "INSUFFICIENT_DATA"]

    if actionable:
        lines.append("**Actionable Signals:**")
        lines.append("")
        lines.append("| Ticker | Signal | Z-Score | Norm Skew | History |")
        lines.append("|--------|--------|---------|-----------|---------|")
        for s in sorted(actionable, key=lambda x: abs(x.z_score or 0), reverse=True):
            arrow  = "⬇ SHORT" if s.signal == "SHORT" else "⬆ LONG"
            z_str  = f"{s.z_score:+.2f}" if s.z_score is not None else "N/A"
            ns_str = f"{s.norm_skew:.3f}" if s.norm_skew is not None else "N/A"
            lines.append(f"| {s.ticker} | {arrow} | {z_str} | {ns_str} | {s.history_days}d |")
        lines.append("")
    else:
        lines.append("**No actionable signals today** — all tickers within normal skew range.")
        lines.append("")

    if warming_up:
        min_hist = min(s.history_days for s in warming_up)
        lines.append(f"> ⏳ **{len(warming_up)} ticker(s) still warming up** — need {10 - min_hist} more day(s) of history before signalling.")
        lines.append("")

    # Screening Results Section
    lines.append("## 📋 Screening Results")
    lines.append("")
    lines.append("**Purpose:** Evaluate each pair against cointegration tests (Johansen), signal-to-noise ratio (SNR ≥ 1.0), and half-life of mean reversion. Identifies statistically sound trade candidates.")
    lines.append("")
    lines.append(f"- **Pairs screened:** **100** candidate pairs")
    
    if any(screening_counts.values()):
        ratings = " | ".join([f"{k}={v}" for k, v in screening_counts.items() if v > 0])
        lines.append(f"- **Rating distribution:** {ratings}")
    
    anomaly_count = len(rejected_pairs)
    lines.append(f"- **Anomalies detected:** **{anomaly_count}** pairs flagged for further investigation")
    lines.append("")
    lines.append("> **Anomaly:** A pair that shows unusual statistical behavior (e.g., sudden breakdown of cointegration, mean reversion failure, or SNR collapse). Flagged pairs are sent to the agent for analysis.")
    lines.append("")
    
    # Agent Verdicts Section
    lines.append("## ⚡ Agent Verdicts")
    lines.append("")
    lines.append("**Purpose:** Classify flagged anomalies into actionable decisions. Each anomaly receives an independent AI analysis and verdict.")
    lines.append("")
    active_count = verdicts.get('active', 0)
    monitor_count = verdicts.get('monitor', 0)
    reject_count = verdicts.get('reject', 0)
    
    lines.append(f"- **Anomalies analyzed:** **{anomaly_count}**")
    lines.append(f"- **ACTIVE:** `{active_count}` (ready to trade — enter new position)")
    lines.append(f"- **MONITOR:** `{monitor_count}` (watch closely — may resolve soon)")
    lines.append(f"- **REJECT:** `{reject_count}` (broken — skip until next cycle)")
    
    if rejected_pairs:
        lines.append("")
        lines.append("**Rejected Pairs (Reasons):**")
        for p in rejected_pairs[:5]:
            pair = p['pair'] if 'pair' in p else f"{p.get('ticker1', '?')}/{p.get('ticker2', '?')}"
            reason = p.get('reason', 'Unknown')
            lines.append(f"- `{pair}`: {reason}")
    
    lines.append("")
    
    # Position Status Section
    lines.append("## 📈 Position Status")
    lines.append("")
    lines.append("**Purpose:** Report current open positions and monitoring status. Open positions are continuously monitored for SNR deterioration and regime-appropriate sizing.")
    lines.append("")
    lines.append(f"- **Open positions:** **{open_positions}**")
    
    if open_positions > 0:
        lines.append(f"- **Under regime-aware monitoring:** ✓ Active")
        lines.append(f"- **Position revalidation:** Enabled (checks for mean-drift breakdowns and SNR decay)")
    else:
        lines.append("- **Monitoring:** Inactive (no open positions)")
    
    from datetime import timedelta
    next_run = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    lines.append(f"- **Next screening:** Scheduled for {next_run} (daily cycle)")
    lines.append("")
    
    # Action Section
    lines.append("## ⚡ Recommended Action")
    lines.append("")
    
    if regime_state == "CRISIS":
        lines.append("**🛑 HALT**")
        lines.append(f"> CRISIS regime detected (VIX {regime_vix:.1f} ≥ 40). No new trade entries. Monitor open positions for forced liquidation risks. Manual intervention may be required.")
    elif active_count > 0:
        lines.append(f"**📊 MONITOR & PREPARE**")
        lines.append(f"> {active_count} active trade signal(s) identified. Review dossier(s) and prepare entry conditions. Respect position multiplier ({regime_multiplier:.2f}x) in trade sizing.")
    elif monitor_count > 0:
        lines.append("**👀 WATCH**")
        lines.append(f"> {monitor_count} pair(s) showing deterioration but not yet rejected. Monitor next 24-48 hours. May resolve or escalate to REJECT.")
    elif open_positions > 0:
        lines.append("**⏸ HOLD**")
        lines.append(f"> {open_positions} position(s) currently open. No new signals. Maintain existing positions and revalidation monitoring.")
    else:
        lines.append("**⏸ HOLD & WAIT**")
        lines.append(f"> No anomalies detected, no open positions. Market is stable ({regime_state}, VIX {regime_vix:.1f}). Await next screening cycle.")
    
    lines.append("")
    
    # Key Metrics Reference
    lines.append("---")
    lines.append("")
    lines.append("## 📚 Reference: Key Metrics")
    lines.append("")
    lines.append("- **SNR (Signal-to-Noise Ratio):** Strength of cointegration signal. >1.0 is acceptable; >2.0 is strong.")
    lines.append("- **Correlation:** How closely two currencies move together. Range: -1 to +1. >0.5 suggests strong relationship.")
    lines.append("- **Half-life:** Expected time for a diverging pair to revert to its mean. Shorter = faster profit potential but higher volatility.")
    lines.append("- **Johansen Test:** Statistical test for cointegration. Determines if pair prices move together long-term.")
    lines.append("- **Cointegration:** Two non-stationary series moving together such that their spread is stationary. Foundation for pairs trading.")
    lines.append("")
    
    # Footer
    lines.append("---")
    lines.append(f"*Generated {timestamp} by ShiftInnerV Sentinel*")
    lines.append(f"*Next report: {next_run} | Universe: {universe_name}*")

    # Dashboard link
    dashboard_link = "http://localhost:8766"
    lines.append(f"\n---\n\n📊 **Portfolio Dashboard**: [{dashboard_link}]({dashboard_link})")

    return "\n".join(lines)


def format_rejected_pair(ticker1: str, ticker2: str, gate_failure: str, details: str) -> Dict:
    """Helper to format a rejected pair for the briefing."""
    return {
        'pair': f"{ticker1}/{ticker2}",
        'reason': f"Gate {gate_failure} — {details}"
    }
