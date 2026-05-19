"""
ShiftInnerV — Transaction Cost Model
Item 3 of the Council Roadmap.

Computes round-trip costs for pairs trading: bid-ask, market impact, borrow,
and commission. Used by correlation_tool.py for detailed per-pair analysis
and by monitor.py for screening verdicts.
"""

import math

# ── Cost thresholds ──────────────────────────────────────────────────────────

BID_ASK_SPREADS_BPS = {
    "large_cap":      5,      # 2.5 bps per side
    "mid_cap":        10,     # 5 bps per side
    "small_cap":      20,     # 10 bps per side
    "etf_liquid":     4,      # 2 bps per side
    "etf_illiquid":   10,     # 5 bps per side
}

BORROW_RATES_ANNUAL = {
    "large_cap_stock":   0.005,  # 50 bps
    "mid_cap_stock":     0.02,   # 200 bps
    "small_cap_stock":   0.05,   # 500 bps
    "etf_liquid":        0.01,   # 100 bps
    "etf_illiquid":      0.03,   # 300 bps
}

COMMISSION_BPS = 1.0  # 1 bp per side, 2 bp round-trip

# ── Classification helpers ───────────────────────────────────────────────────

def classify_security(market_cap_b: float, is_etf: bool,
                      daily_volume_notional_m: float) -> str:
    """
    Classify a security as large/mid/small cap or ETF, based on market cap
    and daily volume.

    market_cap_b:              Market cap in billions
    is_etf:                    True if this is an ETF
    daily_volume_notional_m:   Average daily notional volume in millions

    Returns: e.g., "large_cap", "etf_liquid", "small_cap_stock"
    """
    if is_etf:
        # ETFs: classify by liquidity (daily volume)
        if daily_volume_notional_m >= 1.0:
            return "etf_liquid"
        else:
            return "etf_illiquid"
    else:
        # Stocks: classify by market cap
        if market_cap_b >= 10.0:
            return "large_cap"
        elif market_cap_b >= 2.0:
            return "mid_cap"
        else:
            return "small_cap"


def compute_market_impact(notional: float,
                          daily_volume_notional: float) -> float:
    """
    Estimate market impact in basis points.

    Formula: (notional / daily_volume) * 100, capped at 15 bps, floored at 2.

    notional:             Dollar amount of position (both legs summed)
    daily_volume_notional: Average daily volume in dollars

    Returns: impact in basis points
    """
    if daily_volume_notional <= 0:
        return 15.0  # Cap for missing data

    ratio = notional / daily_volume_notional
    impact = ratio * 100.0
    return max(2.0, min(15.0, impact))


# ── Main cost computation ────────────────────────────────────────────────────

def compute_round_trip_costs(
    notional_leg1: float,
    notional_leg2: float,
    market_cap1_b: float,
    market_cap2_b: float,
    daily_volume1_m: float,
    daily_volume2_m: float,
    is_etf1: bool,
    is_etf2: bool,
    half_life_days: float,
    ticker1: str = "T1",
    ticker2: str = "T2",
) -> dict:
    """
    Compute all transaction costs for a pairs trade.

    Parameters
    ----------
    notional_leg1, notional_leg2:  Dollar amount for each leg at entry
    market_cap1_b, market_cap2_b:  Market cap in billions (optional, can be None)
    daily_volume1_m, daily_volume2_m: Avg daily volume in millions (optional)
    is_etf1, is_etf2:             Whether each leg is an ETF
    half_life_days:                Mean reversion half-life in days
    ticker1, ticker2:              Ticker symbols (for reporting)

    Returns
    -------
    dict with keys:
        - bid_ask_leg1_cost, bid_ask_leg2_cost (in dollars)
        - impact_leg1_cost, impact_leg2_cost (in dollars)
        - borrow_cost (in dollars, for the short leg — leg2 by convention)
        - commission_cost (in dollars)
        - total_cost (in dollars)
        - total_cost_bps (in basis points of total notional)
        - cost_breakdown (text summary)
        - details (dict of per-component breakdowns for reporting)
    """
    total_notional = notional_leg1 + notional_leg2

    # ── Classify securities ────────────────────────────────────────────────────
    # Handle missing market cap (use defaults)
    if market_cap1_b is None:
        market_cap1_b = 5.0  # default to mid-cap
    if market_cap2_b is None:
        market_cap2_b = 5.0

    if daily_volume1_m is None:
        daily_volume1_m = 10.0
    if daily_volume2_m is None:
        daily_volume2_m = 10.0

    security1 = classify_security(market_cap1_b, is_etf1, daily_volume1_m)
    security2 = classify_security(market_cap2_b, is_etf2, daily_volume2_m)

    # ── Bid-ask costs (paid on entry and exit, so ×2) ──────────────────────────
    bid_ask1_bps = BID_ASK_SPREADS_BPS.get(security1, 5)
    bid_ask2_bps = BID_ASK_SPREADS_BPS.get(security2, 5)

    bid_ask1_cost = (notional_leg1 * bid_ask1_bps / 10000) * 2  # entry + exit
    bid_ask2_cost = (notional_leg2 * bid_ask2_bps / 10000) * 2

    # ── Market impact (×2 for entry and exit) ───────────────────────────────────
    impact1_bps = compute_market_impact(total_notional, daily_volume1_m * 1e6)
    impact2_bps = compute_market_impact(total_notional, daily_volume2_m * 1e6)

    impact1_cost = (notional_leg1 * impact1_bps / 10000) * 2
    impact2_cost = (notional_leg2 * impact2_bps / 10000) * 2

    # ── Borrow cost (leg2 is shorted; charged daily over hold period) ──────────
    # Map security2 classification to borrow rate key
    borrow_key_map = {
        "large_cap":    "large_cap_stock",
        "mid_cap":      "mid_cap_stock",
        "small_cap":    "small_cap_stock",
        "etf_liquid":   "etf_liquid",
        "etf_illiquid": "etf_illiquid",
    }
    borrow_key = borrow_key_map.get(security2, "large_cap_stock")
    borrow_rate = BORROW_RATES_ANNUAL.get(borrow_key, 0.01)
    daily_borrow_cost = (notional_leg2 * borrow_rate) / 252.0
    borrow_cost = daily_borrow_cost * max(1.0, half_life_days)  # min 1 day

    # ── Commission (small safety margin; most brokers are commission-free) ──────
    commission_bps = COMMISSION_BPS
    commission1_cost = (notional_leg1 * commission_bps / 10000) * 2
    commission2_cost = (notional_leg2 * commission_bps / 10000) * 2

    # ── Total costs ──────────────────────────────────────────────────────────────
    total_cost = (bid_ask1_cost + bid_ask2_cost +
                  impact1_cost + impact2_cost +
                  borrow_cost +
                  commission1_cost + commission2_cost)

    total_cost_bps = (total_cost / total_notional * 10000) if total_notional > 0 else 0

    cost_breakdown = (
        f"Bid-ask ({bid_ask1_bps:.0f}+{bid_ask2_bps:.0f} bps): "
        f"${bid_ask1_cost + bid_ask2_cost:.2f} | "
        f"Impact ({impact1_bps:.1f}+{impact2_bps:.1f} bps): "
        f"${impact1_cost + impact2_cost:.2f} | "
        f"Borrow ({borrow_rate*100:.0f}% @ {half_life_days:.0f}d): "
        f"${borrow_cost:.2f} | "
        f"Commission: ${commission1_cost + commission2_cost:.2f} | "
        f"Total: ${total_cost:.2f} ({total_cost_bps:.0f} bps)"
    )

    return {
        "bid_ask_leg1_cost":     round(bid_ask1_cost, 2),
        "bid_ask_leg2_cost":     round(bid_ask2_cost, 2),
        "impact_leg1_cost":      round(impact1_cost, 2),
        "impact_leg2_cost":      round(impact2_cost, 2),
        "borrow_cost":           round(borrow_cost, 2),
        "commission_cost":       round(commission1_cost + commission2_cost, 2),
        "total_cost":            round(total_cost, 2),
        "total_cost_bps":        round(total_cost_bps, 1),
        "cost_breakdown":        cost_breakdown,
        "details": {
            "ticker1":           ticker1,
            "ticker2":           ticker2,
            "security1":         security1,
            "security2":         security2,
            "notional_leg1":     round(notional_leg1, 2),
            "notional_leg2":     round(notional_leg2, 2),
            "total_notional":    round(total_notional, 2),
            "half_life_days":    round(half_life_days, 1),
        },
    }


def compute_net_pnl(gross_pnl_bps: float,
                    total_cost_bps: float) -> dict:
    """
    Compute net P&L after costs.

    gross_pnl_bps:  Gross expected P&L in basis points
    total_cost_bps: Transaction costs in basis points

    Returns dict with:
      - net_pnl_bps: Net P&L in basis points
      - net_pnl_pct: Net P&L as a percentage
      - is_profitable: True if net > 0
      - marginal: True if 0 < net < 25 bps (caution zone)
    """
    net_pnl_bps = gross_pnl_bps - total_cost_bps
    net_pnl_pct = net_pnl_bps / 10000

    return {
        "gross_pnl_bps":    round(gross_pnl_bps, 1),
        "total_cost_bps":   round(total_cost_bps, 1),
        "net_pnl_bps":      round(net_pnl_bps, 1),
        "net_pnl_pct":      round(net_pnl_pct, 4),
        "is_profitable":    net_pnl_bps > 0,
        "marginal":         0 < net_pnl_bps < 25,  # caution zone
    }
