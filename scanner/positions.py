"""Positions tracker — unified handling of real + shadow positions.

This is the data layer underneath the dashboard's BUY / SHADOW / DISMISS buttons.
Each position has the same structure regardless of type; only the "type" field
distinguishes real money from paper-trade.

Position lifecycle:
  open → (held N days, price tracked daily) → closed (manual exit, stop-loss, or timeout)

Reads: data/positions.json (current state), data/latest.json (current prices)
Writes: data/positions.json (updated with current values)

The dashboard creates positions via "actions" (recorded in positions.json's
pending_actions array) which this script processes on next run. This avoids
needing a backend server — everything is JSON files committed to git.

Run by a GitHub Action every scan cycle to:
  1. Process any pending actions (open new positions, close requested ones)
  2. Update current price + P&L on all open positions
  3. Auto-close positions that hit stop-loss or max-hold-days
  4. Compute realized + unrealized P&L summary
"""

import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Optional
import uuid

import yfinance as yf

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger(__name__)

DATA_DIR = Path("../data")
POSITIONS_FILE       = DATA_DIR / "positions.json"
LATEST_FILE          = DATA_DIR / "latest.json"
RECOMMENDATIONS_FILE = DATA_DIR / "recommendations.json"

MAX_HOLD_DAYS = 7
STOP_LOSS_PCT = -2.0


def _empty_state() -> dict:
    return {
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "positions": [],
        "pending_actions": [],
        "summary": {
            "real": {"open": 0, "closed": 0, "realized_pnl_cad": 0.0, "unrealized_pnl_cad": 0.0, "total_pnl_cad": 0.0, "win_rate": None},
            "shadow": {"open": 0, "closed": 0, "realized_pnl_cad": 0.0, "unrealized_pnl_cad": 0.0, "total_pnl_cad": 0.0, "win_rate": None},
        },
    }


def _load() -> dict:
    if not POSITIONS_FILE.exists():
        return _empty_state()
    try:
        data = json.loads(POSITIONS_FILE.read_text())
        # Backfill missing keys for forward-compat
        if "positions" not in data: data["positions"] = []
        if "pending_actions" not in data: data["pending_actions"] = []
        if "summary" not in data: data["summary"] = _empty_state()["summary"]
        return data
    except (json.JSONDecodeError, OSError):
        return _empty_state()


def _save(state: dict) -> None:
    state["updated_at"] = datetime.now(timezone.utc).isoformat()
    POSITIONS_FILE.write_text(json.dumps(state, indent=2))


def _fetch_current_price(ticker: str) -> Optional[float]:
    """Fetch latest price for a ticker. Returns None on failure."""
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="2d", interval="1d")
        if hist.empty:
            return None
        return float(hist.iloc[-1]["Close"])
    except Exception as e:
        log.warning(f"Price fetch failed for {ticker}: {e}")
        return None


def _days_held(opened_at_iso: str) -> int:
    opened = datetime.fromisoformat(opened_at_iso)
    return (datetime.now(timezone.utc) - opened).days


def open_position(state: dict, action: dict) -> dict:
    """Create a new open position from a pending action."""
    rec = action.get("recommendation", {})
    ptype = action.get("type", "shadow")  # "real" or "shadow"
    ticker = action.get("ticker") or rec.get("ticker")
    entry_price = action.get("entry_price") or rec.get("current_price")
    allocation_cad = action.get("allocation_cad")
    if not allocation_cad and rec.get("allocation"):
        allocation_cad = rec["allocation"].get("allocation_cad")
    if not allocation_cad:
        allocation_cad = 0.0

    position = {
        "id": str(uuid.uuid4())[:8],
        "type": ptype,
        "ticker": ticker,
        "direction": rec.get("direction", "bullish"),
        "opened_at": datetime.now(timezone.utc).isoformat(),
        "entry_price": entry_price,
        "allocation_cad": round(allocation_cad, 2),
        "current_price": entry_price,
        "current_value_cad": round(allocation_cad, 2),
        "pnl_cad": 0.0,
        "pnl_pct": 0.0,
        "max_hold_until": (datetime.now(timezone.utc) + timedelta(days=MAX_HOLD_DAYS)).isoformat(),
        "stop_loss_pct": STOP_LOSS_PCT,
        "stop_loss_price": round(entry_price * (1 + STOP_LOSS_PCT/100), 4) if entry_price else None,
        "status": "open",
        "exit_reason": None,
        "exit_price": None,
        "closed_at": None,
        "rationale_at_open": rec.get("rationale", ""),
        "score_at_open": rec.get("score"),
        "regime_at_open": rec.get("regime"),
    }
    state["positions"].append(position)
    log.info(f"Opened {ptype} position {position['id']}: {ticker} @ ${entry_price} (${allocation_cad})")
    return state


def close_position(state: dict, position_id: str, reason: str = "manual") -> dict:
    for p in state["positions"]:
        if p["id"] == position_id and p["status"] == "open":
            p["status"] = "closed"
            p["closed_at"] = datetime.now(timezone.utc).isoformat()
            p["exit_reason"] = reason
            p["exit_price"] = p["current_price"]
            log.info(f"Closed {p['type']} position {p['id']}: {p['ticker']} ({reason}) P&L ${p['pnl_cad']:.2f}")
            break
    return state


def process_pending_actions(state: dict) -> dict:
    """Process queued actions from the dashboard (open, close, dismiss)."""
    actions = state.get("pending_actions", [])
    if not actions:
        return state
    log.info(f"Processing {len(actions)} pending actions")
    for action in actions:
        op = action.get("op")
        if op == "open":
            state = open_position(state, action)
        elif op == "close":
            state = close_position(state, action["position_id"], action.get("reason", "manual"))
        elif op == "dismiss":
            # Just log for now — could feed a "rejected signals" stats tracker later
            log.info(f"Dismissed recommendation for {action.get('ticker')}")
    state["pending_actions"] = []
    return state


def update_open_positions(state: dict) -> dict:
    """Refresh prices on open positions, check stop-loss + max-hold, update P&L."""
    for p in state["positions"]:
        if p["status"] != "open":
            continue
        price = _fetch_current_price(p["ticker"])
        if price is None:
            continue
        p["current_price"] = round(price, 4)
        if p["entry_price"] and p["entry_price"] > 0:
            pnl_pct = (price - p["entry_price"]) / p["entry_price"] * 100
            p["pnl_pct"] = round(pnl_pct, 2)
            p["pnl_cad"] = round(p["allocation_cad"] * pnl_pct / 100, 2)
            p["current_value_cad"] = round(p["allocation_cad"] + p["pnl_cad"], 2)
            # Auto-close: stop-loss
            if pnl_pct <= p["stop_loss_pct"]:
                state = close_position(state, p["id"], reason="stop_loss")
                continue
        # Auto-close: max hold reached
        if _days_held(p["opened_at"]) >= MAX_HOLD_DAYS:
            state = close_position(state, p["id"], reason="max_hold_reached")
    return state


def compute_summary(state: dict) -> dict:
    summary = {"real": _summary_for(state["positions"], "real"),
               "shadow": _summary_for(state["positions"], "shadow")}
    state["summary"] = summary
    return state


def _summary_for(positions: list, ptype: str) -> dict:
    subset = [p for p in positions if p["type"] == ptype]
    opened = [p for p in subset if p["status"] == "open"]
    closed = [p for p in subset if p["status"] == "closed"]
    realized = round(sum(p["pnl_cad"] for p in closed), 2)
    unrealized = round(sum(p["pnl_cad"] for p in opened), 2)
    wins = sum(1 for p in closed if p["pnl_cad"] > 0)
    win_rate = round(wins / len(closed), 3) if closed else None
    return {
        "open": len(opened),
        "closed": len(closed),
        "realized_pnl_cad": realized,
        "unrealized_pnl_cad": unrealized,
        "total_pnl_cad": round(realized + unrealized, 2),
        "win_rate": win_rate,
    }


def main() -> int:
    state = _load()
    state = process_pending_actions(state)
    state = update_open_positions(state)
    state = compute_summary(state)
    _save(state)
    s = state["summary"]
    log.info(
        f"Positions: real {s['real']['open']}o/{s['real']['closed']}c (PnL ${s['real']['total_pnl_cad']:+.2f}) | "
        f"shadow {s['shadow']['open']}o/{s['shadow']['closed']}c (PnL ${s['shadow']['total_pnl_cad']:+.2f})"
    )
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
