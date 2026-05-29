#!/usr/bin/env python3
"""
ShiftInnerV — Portfolio Dashboard
Serves a live HTML dashboard at http://localhost:<port>

Usage:
    python dashboard.py --db /path/to/trial_ledger.db --port 8766

The page re-reads the database on every request; no files are written to disk.
Live prices are fetched via yfinance for open positions.
"""

import argparse
import sqlite3
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

# ── Colours & styles ──────────────────────────────────────────────────────────
BG        = "#0d0d0d"
SURFACE   = "#161616"
SURFACE2  = "#1e1e1e"
BORDER    = "#2a2a2a"
TEXT      = "#e8e8e8"
MUTED     = "#888888"
GREEN     = "#1D9E75"
RED       = "#c94040"
AMBER     = "#c9912a"
MONO      = "'JetBrains Mono', 'Fira Mono', 'Courier New', monospace"

# ── CLI ───────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="ShiftInnerV Portfolio Dashboard")
    p.add_argument(
        "--db",
        default="/Volumes/Elessar/ShiftInnerV_Data/trial_ledger.db",
        help="Path to trial_ledger.db",
    )
    p.add_argument("--port", type=int, default=8766, help="HTTP port (default 8766)")
    return p.parse_args()


# ── Data layer ────────────────────────────────────────────────────────────────

def _connect(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_open_positions(db_path: str) -> list[dict]:
    try:
        conn = _connect(db_path)
        rows = conn.execute(
            """
            SELECT
                verdict_id, verdict_timestamp,
                ticker1, ticker2,
                entry_price_1, entry_price_2,
                entry_notional, entry_timestamp,
                hedge_ratio, snr, composition_label,
                regime_state
            FROM trial_ledger
            WHERE is_closed = 0
            ORDER BY verdict_timestamp DESC
            """
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        return [{"_error": str(exc)}]


def fetch_closed_trades(db_path: str) -> list[dict]:
    try:
        conn = _connect(db_path)
        rows = conn.execute(
            """
            SELECT
                verdict_id, verdict_timestamp,
                ticker1, ticker2,
                entry_price_1, entry_price_2,
                exit_price_1, exit_price_2,
                entry_timestamp, exit_timestamp,
                hold_days, gross_pnl_dollars, net_pnl_bps, net_pnl_pct,
                exit_reason, is_profitable,
                snr, composition_label
            FROM trial_ledger
            WHERE is_closed = 1
            ORDER BY exit_timestamp DESC
            LIMIT 100
            """
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]
    except Exception as exc:
        return [{"_error": str(exc)}]


def fetch_live_prices(tickers: list[str]) -> dict[str, float | None]:
    """Batch-fetch last prices via yfinance. Returns {ticker: price}."""
    if not tickers:
        return {}
    try:
        import yfinance as yf
        data = yf.download(
            tickers,
            period="2d",
            interval="1d",
            progress=False,
            auto_adjust=True,
            group_by="ticker" if len(tickers) > 1 else None,
        )
        prices: dict[str, float | None] = {}
        for t in tickers:
            try:
                if len(tickers) == 1:
                    prices[t] = float(data["Close"].dropna().iloc[-1])
                else:
                    prices[t] = float(data["Close"][t].dropna().iloc[-1])
            except Exception:
                prices[t] = None
        return prices
    except Exception:
        return {t: None for t in tickers}


# ── P&L calc for open positions ───────────────────────────────────────────────

def compute_unrealized(pos: dict, prices: dict[str, float | None]) -> dict:
    """
    Compute unrealized P&L for a pairs position.

    We always assume the trade is long ticker1 / short ticker2 (long spread),
    which matches the convention in close_trial().  Entry prices may be NULL
    for verdicts that were recorded before execution fills; those positions
    are flagged as awaiting entry.
    """
    ep1 = pos.get("entry_price_1")
    ep2 = pos.get("entry_price_2")
    notional = pos.get("entry_notional") or 0.0
    hedge = pos.get("hedge_ratio") or 1.0

    lp1 = prices.get(pos["ticker1"])
    lp2 = prices.get(pos["ticker2"])

    if ep1 is None or ep2 is None:
        return {"status": "awaiting_entry", "live_price_1": lp1, "live_price_2": lp2,
                "upnl": None, "upnl_pct": None}

    notional_1 = 10_000.0
    notional_2 = 10_000.0 * abs(hedge)

    if ep1 > 0 and ep2 > 0 and lp1 and lp2:
        shares_1 = notional_1 / ep1
        shares_2 = notional_2 / ep2
        pnl1 = shares_1 * ((lp1 - ep1))
        pnl2 = shares_2 * ((ep2 - lp2))  # short leg
        upnl = pnl1 + pnl2
        total_notional = notional_1 + notional_2
        upnl_pct = (upnl / total_notional * 100) if total_notional > 0 else 0.0
        return {
            "status": "live",
            "live_price_1": lp1,
            "live_price_2": lp2,
            "upnl": upnl,
            "upnl_pct": upnl_pct,
        }
    return {"status": "no_price", "live_price_1": lp1, "live_price_2": lp2,
            "upnl": None, "upnl_pct": None}


# ── Summary metrics ───────────────────────────────────────────────────────────

def build_summary(open_pos: list[dict], closed: list[dict], upnls: list[dict]) -> dict:
    total_notional = sum(
        (p.get("entry_notional") or 0.0) for p in open_pos
        if not p.get("_error")
    )
    total_upnl = sum(
        u["upnl"] for u in upnls
        if u.get("upnl") is not None
    )
    realized_pnl = sum(
        (t.get("gross_pnl_dollars") or 0.0) for t in closed
        if not t.get("_error")
    )
    return {
        "open_count": len([p for p in open_pos if not p.get("_error")]),
        "closed_count": len([t for t in closed if not t.get("_error")]),
        "total_notional": total_notional,
        "total_upnl": total_upnl,
        "realized_pnl": realized_pnl,
    }


# ── HTML helpers ──────────────────────────────────────────────────────────────

def _fmt(val: Any, decimals: int = 2, prefix: str = "", suffix: str = "") -> str:
    if val is None:
        return f'<span style="color:{MUTED}">—</span>'
    try:
        v = float(val)
        return f"{prefix}{v:,.{decimals}f}{suffix}"
    except (TypeError, ValueError):
        return str(val)


def _pnl_cell(val: Any, suffix: str = "") -> str:
    if val is None:
        return f'<span style="color:{MUTED}">—</span>'
    try:
        v = float(val)
        colour = GREEN if v >= 0 else RED
        sign = "+" if v > 0 else ""
        return f'<span style="color:{colour};font-family:{MONO}">{sign}{v:,.2f}{suffix}</span>'
    except (TypeError, ValueError):
        return str(val)


def _regime_badge(regime: str | None) -> str:
    colours = {
        "NORMAL":     GREEN,
        "ELEVATED":   AMBER,
        "HIGH_STRESS": RED,
        "CRISIS":     RED,
    }
    r = (regime or "NORMAL").upper()
    c = colours.get(r, MUTED)
    return (
        f'<span style="background:{c}22;color:{c};border:1px solid {c}55;'
        f'border-radius:3px;padding:1px 6px;font-size:11px;font-family:{MONO}">'
        f'{r}</span>'
    )


def css() -> str:
    return f"""
    <style>
      *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}
      body {{
        background: {BG}; color: {TEXT};
        font-family: -apple-system, 'Segoe UI', sans-serif;
        font-size: 14px; line-height: 1.5;
      }}
      a {{ color: {GREEN}; text-decoration: none; }}
      a:hover {{ text-decoration: underline; }}
      .shell {{ max-width: 1300px; margin: 0 auto; padding: 24px 20px; }}
      header {{
        display: flex; justify-content: space-between; align-items: flex-end;
        border-bottom: 1px solid {BORDER}; padding-bottom: 14px; margin-bottom: 24px;
      }}
      header h1 {{ font-size: 20px; font-weight: 600; letter-spacing: .5px; }}
      header h1 span {{ color: {GREEN}; }}
      .meta {{ color: {MUTED}; font-size: 12px; font-family: {MONO}; text-align: right; line-height: 1.8; }}
      .metrics {{
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(160px, 1fr));
        gap: 12px; margin-bottom: 28px;
      }}
      .card {{
        background: {SURFACE}; border: 1px solid {BORDER};
        border-radius: 6px; padding: 14px 16px;
      }}
      .card .label {{ color: {MUTED}; font-size: 11px; text-transform: uppercase;
                      letter-spacing: .8px; margin-bottom: 6px; }}
      .card .value {{ font-size: 22px; font-weight: 600; font-family: {MONO}; }}
      .card .value.green {{ color: {GREEN}; }}
      .card .value.red   {{ color: {RED}; }}
      .card .value.amber {{ color: {AMBER}; }}
      section {{ margin-bottom: 32px; }}
      section h2 {{
        font-size: 13px; text-transform: uppercase; letter-spacing: 1px;
        color: {MUTED}; margin-bottom: 12px; padding-bottom: 6px;
        border-bottom: 1px solid {BORDER};
      }}
      table {{ width: 100%; border-collapse: collapse; }}
      th {{
        text-align: left; font-size: 11px; text-transform: uppercase;
        letter-spacing: .7px; color: {MUTED}; padding: 8px 10px;
        border-bottom: 1px solid {BORDER};
      }}
      td {{
        padding: 9px 10px; border-bottom: 1px solid {BORDER}22;
        font-size: 13px; vertical-align: middle;
      }}
      tr:last-child td {{ border-bottom: none; }}
      tr:hover td {{ background: {SURFACE2}; }}
      .mono {{ font-family: {MONO}; }}
      .muted {{ color: {MUTED}; }}
      .empty-state {{
        padding: 28px; text-align: center;
        color: {MUTED}; font-size: 13px;
        background: {SURFACE}; border: 1px solid {BORDER}; border-radius: 6px;
      }}
      .ticker {{ font-family: {MONO}; font-weight: 600; color: {TEXT}; }}
      .pair-sep {{ color: {MUTED}; margin: 0 3px; }}
    </style>
    """


def render_open_table(open_pos: list[dict], upnl_map: dict[str, dict]) -> str:
    if not open_pos:
        return '<div class="empty-state">No open positions.</div>'

    rows = ""
    for p in open_pos:
        if p.get("_error"):
            continue
        vid = p["verdict_id"]
        u = upnl_map.get(vid, {})
        status = u.get("status", "")
        ep1 = p.get("entry_price_1")
        ep2 = p.get("entry_price_2")
        lp1 = u.get("live_price_1")
        lp2 = u.get("live_price_2")
        entry_date = (p.get("entry_timestamp") or p.get("verdict_timestamp") or "")[:10]
        upnl = u.get("upnl")
        upnl_pct = u.get("upnl_pct")

        status_label = {
            "live": "",
            "awaiting_entry": f'<span style="color:{AMBER};font-size:11px">AWAITING ENTRY</span>',
            "no_price": f'<span style="color:{MUTED};font-size:11px">NO PRICE</span>',
        }.get(status, "")

        rows += f"""
        <tr>
          <td>
            <span class="ticker">{p['ticker1']}</span>
            <span class="pair-sep">/</span>
            <span class="ticker">{p['ticker2']}</span>
            {status_label}
          </td>
          <td class="mono muted">{p.get('composition_label') or '—'}</td>
          <td class="mono">{_fmt(ep1, prefix='$')}</td>
          <td class="mono">{_fmt(ep2, prefix='$')}</td>
          <td class="mono">{_fmt(lp1, prefix='$')}</td>
          <td class="mono">{_fmt(lp2, prefix='$')}</td>
          <td class="mono">{_fmt(p.get('entry_notional'), prefix='$', decimals=0)}</td>
          <td>{_pnl_cell(upnl, suffix='')}</td>
          <td>{_pnl_cell(upnl_pct, suffix='%')}</td>
          <td class="mono muted">{_fmt(p.get('snr'), decimals=4)}</td>
          <td>{_regime_badge(p.get('regime_state'))}</td>
          <td class="muted mono">{entry_date}</td>
          <td class="muted mono" style="font-size:11px">{vid}</td>
        </tr>
        """

    return f"""
    <table>
      <thead><tr>
        <th>Pair</th>
        <th>Composition</th>
        <th>Entry P₁</th>
        <th>Entry P₂</th>
        <th>Live P₁</th>
        <th>Live P₂</th>
        <th>Notional</th>
        <th>Unreal. P&L</th>
        <th>Unreal. %</th>
        <th>SNR</th>
        <th>Regime</th>
        <th>Entry Date</th>
        <th>ID</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
    """


def render_closed_table(closed: list[dict]) -> str:
    if not closed:
        return '<div class="empty-state">No closed trades recorded yet.</div>'

    rows = ""
    for t in closed:
        if t.get("_error"):
            continue
        pnl_d = t.get("gross_pnl_dollars")
        pnl_bps = t.get("net_pnl_bps")
        entry_date = (t.get("entry_timestamp") or t.get("verdict_timestamp") or "")[:10]
        exit_date  = (t.get("exit_timestamp") or "")[:10]

        rows += f"""
        <tr>
          <td>
            <span class="ticker">{t['ticker1']}</span>
            <span class="pair-sep">/</span>
            <span class="ticker">{t['ticker2']}</span>
          </td>
          <td class="mono muted">{t.get('composition_label') or '—'}</td>
          <td class="mono">{_fmt(t.get('entry_price_1'), prefix='$')}</td>
          <td class="mono">{_fmt(t.get('entry_price_2'), prefix='$')}</td>
          <td class="mono">{_fmt(t.get('exit_price_1'), prefix='$')}</td>
          <td class="mono">{_fmt(t.get('exit_price_2'), prefix='$')}</td>
          <td>{_pnl_cell(pnl_d)}</td>
          <td>{_pnl_cell(pnl_bps, suffix=' bps')}</td>
          <td class="muted mono">{t.get('hold_days') or '—'} d</td>
          <td class="muted mono">{t.get('exit_reason') or '—'}</td>
          <td class="muted mono">{entry_date}</td>
          <td class="muted mono">{exit_date}</td>
        </tr>
        """

    return f"""
    <table>
      <thead><tr>
        <th>Pair</th>
        <th>Composition</th>
        <th>Entry P₁</th>
        <th>Entry P₂</th>
        <th>Exit P₁</th>
        <th>Exit P₂</th>
        <th>Gross P&L $</th>
        <th>Net P&L (bps)</th>
        <th>Hold</th>
        <th>Exit Reason</th>
        <th>Entry Date</th>
        <th>Exit Date</th>
      </tr></thead>
      <tbody>{rows}</tbody>
    </table>
    """


# ── Page builder ──────────────────────────────────────────────────────────────

def build_page(db_path: str) -> str:
    open_pos = fetch_open_positions(db_path)
    closed   = fetch_closed_trades(db_path)

    # Collect unique tickers from open positions
    tickers: list[str] = []
    for p in open_pos:
        if not p.get("_error"):
            for key in ("ticker1", "ticker2"):
                t = p.get(key)
                if t and t not in tickers:
                    tickers.append(t)

    prices   = fetch_live_prices(tickers)
    upnl_map = {
        p["verdict_id"]: compute_unrealized(p, prices)
        for p in open_pos if not p.get("_error")
    }
    summary  = build_summary(open_pos, closed, list(upnl_map.values()))

    # Summary card colours
    upnl_val = summary["total_upnl"]
    rpnl_val = summary["realized_pnl"]
    upnl_cls = "green" if upnl_val >= 0 else "red"
    rpnl_cls = "green" if rpnl_val >= 0 else "red"

    as_of = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>ShiftInnerV Dashboard</title>
  {css()}
</head>
<body>
<div class="shell">

  <header>
    <div>
      <h1>Shift<span>Inner</span>V &nbsp;<span style="color:{MUTED};font-size:14px;font-weight:400">Portfolio Dashboard</span></h1>
    </div>
    <div class="meta">
      <div>as of {as_of}</div>
      <div style="color:{MUTED}44">{db_path}</div>
      <div style="margin-top:4px"><a href="/">↻ Refresh</a></div>
    </div>
  </header>

  <div class="metrics">
    <div class="card">
      <div class="label">Open Positions</div>
      <div class="value">{summary['open_count']}</div>
    </div>
    <div class="card">
      <div class="label">Closed Trades</div>
      <div class="value">{summary['closed_count']}</div>
    </div>
    <div class="card">
      <div class="label">Notional Deployed</div>
      <div class="value mono">${summary['total_notional']:,.0f}</div>
    </div>
    <div class="card">
      <div class="label">Unrealised P&L</div>
      <div class="value {upnl_cls}">${upnl_val:+,.2f}</div>
    </div>
    <div class="card">
      <div class="label">Realised P&L (gross)</div>
      <div class="value {rpnl_cls}">${rpnl_val:+,.2f}</div>
    </div>
  </div>

  <section>
    <h2>Open Positions ({summary['open_count']})</h2>
    {render_open_table(open_pos, upnl_map)}
  </section>

  <section>
    <h2>Closed Trades — last 100 ({summary['closed_count']})</h2>
    {render_closed_table(closed)}
  </section>

</div>
</body>
</html>"""


# ── HTTP server ───────────────────────────────────────────────────────────────

def make_handler(db_path: str):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):  # silence access log spam
            pass

        def do_GET(self):
            if self.path not in ("/", "/favicon.ico"):
                self.send_response(404)
                self.end_headers()
                return
            if self.path == "/favicon.ico":
                self.send_response(204)
                self.end_headers()
                return
            try:
                html = build_page(db_path).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(html)))
                self.end_headers()
                self.wfile.write(html)
            except Exception as exc:
                body = f"<pre>Error: {exc}</pre>".encode()
                self.send_response(500)
                self.send_header("Content-Type", "text/html")
                self.end_headers()
                self.wfile.write(body)

    return Handler


def main() -> None:
    args = parse_args()
    db_path = args.db
    port    = args.port

    print(f"Dashboard running at http://localhost:{port}")
    print(f"Reading from: {db_path}")

    server = HTTPServer(("", port), make_handler(db_path))
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        sys.exit(0)


if __name__ == "__main__":
    main()
