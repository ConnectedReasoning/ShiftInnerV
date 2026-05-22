"""
Sentinel Run Briefing Generator — Streamlined

Generates a professional markdown summary focusing on:
- Market regime (NORMAL/ELEVATED/HIGH_STRESS/CRISIS)
- Top 5 sourced pairs (real signal)
- Trading status (ACTIVE verdicts)

No slow backtest. Just real data.
"""

from datetime import datetime
from typing import Dict, List


def generate_sentinel_briefing(
    regime_state: str,
    regime_vix: float,
    regime_multiplier: float,
    sourced_pairs: List[Dict],
    screening_counts: Dict[str, int],
    verdicts: Dict[str, int],
    rejected_pairs: List[Dict],
    open_positions: int,
    total_pairs_sourced: int = 0,
    total_pairs_screened: int = 0,
    universe_name: str = "currencies",
) -> str:
    """
    Generate a professional markdown briefing.
    Now tracks multiple universes (currencies, small caps) in parallel.
    """
    
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    briefing_lines = []
    
    # Header
    briefing_lines.append("# Sentinel Run Briefing")
    briefing_lines.append(f"\n**{timestamp}** | Universe: **{universe_name.upper()}**\n")
    
    # REGIME — Primary Context
    briefing_lines.append("## 📊 Market Regime\n")
    briefing_lines.append(f"**State:** {regime_state} | **VIX:** {regime_vix:.1f} | **Position Size:** {regime_multiplier:.1f}x\n")
    
    if regime_state == "NORMAL":
        briefing_lines.append("> ✓ Conditions stable — full position sizing enabled\n")
    elif regime_state == "ELEVATED":
        briefing_lines.append("> ⚠ Elevated stress — position size 0.75x\n")
    elif regime_state == "HIGH_STRESS":
        briefing_lines.append("> ⚠⚠ High stress — position size 0.5x\n")
    else:
        briefing_lines.append("> 🚨 CRISIS — new entries HALTED\n")
    
    # TOP 5 PAIRS
    briefing_lines.append("## 🎯 Top 5 Pairs by Score\n")
    briefing_lines.append("> **Score** = correlation divergence. **Corr** = current 50-day rolling correlation.\n")
    if sourced_pairs:
        briefing_lines.append("| Rank | Pair | Score | Correlation |")
        briefing_lines.append("|------|------|-------|-------------|")
        for i, p in enumerate(sourced_pairs[:5], 1):
            t1 = p['ticker1'].replace('=X', '')
            t2 = p['ticker2'].replace('=X', '')
            briefing_lines.append(f"| {i} | `{t1}` / `{t2}` | {p['score']:5.1f} | {p['corr']:.3f} |")
        briefing_lines.append("")
    else:
        briefing_lines.append("*No pairs sourced today*\n")
    
    # TRADING STATUS
    briefing_lines.append("## ⚡ Trading Status\n")
    briefing_lines.append(f"- **Pairs sourced:** {total_pairs_sourced}")
    briefing_lines.append(f"- **Pairs screened:** {total_pairs_screened}")
    briefing_lines.append(f"- **Cointegrated (90% CI):** {verdicts.get('active', 0) + verdicts.get('monitor', 0)}")
    briefing_lines.append(f"- **🟢 ACTIVE trades:** {verdicts.get('active', 0)}")
    briefing_lines.append(f"- **Open positions:** {open_positions}")
    briefing_lines.append("")
    
    # ACTION
    if verdicts.get('active', 0) > 0:
        briefing_lines.append("## ✓ GO\n")
        briefing_lines.append(f"**{verdicts.get('active', 0)} trade(s) ready.** Execute on your $50k play money.\n")
    elif verdicts.get('monitor', 0) > 0:
        briefing_lines.append("## ⏳ MONITOR\n")
        briefing_lines.append(f"**{verdicts.get('monitor', 0)} pair(s) near gate.** Watch for cointegration confirmation.\n")
    else:
        briefing_lines.append("## ⏹ HOLD\n")
        if universe_name == "currencies" and regime_vix < 18:
            briefing_lines.append("VIX < 18 (calm). Currencies dormant. Retry when VIX > 18.\n")
        else:
            briefing_lines.append("No cointegrated pairs today. Wait for next screening or lower the gate.\n")
    
    # Footer
    briefing_lines.append("---")
    briefing_lines.append(f"*{timestamp} | {universe_name}*")
    
    return "\n".join(briefing_lines)


def format_rejected_pair(ticker1: str, ticker2: str, gate_failure: str, details: str) -> Dict:
    """Helper to format a rejected pair for the briefing."""
    return {
        'pair': f"{ticker1}/{ticker2}",
        'reason': f"Gate {gate_failure} — {details}"
    }
