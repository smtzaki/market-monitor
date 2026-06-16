"""Main scanner. Run once per GitHub Actions invocation.

Outputs (written to ../data/):
  - latest.json       : current tile prices + active signals (dashboard reads this)
  - signal_log.json   : appended history of all signals (forward returns added later)
  - alert_state.json  : dedup state across runs

Flow:
  1. Fetch USD/CAD rate.
  2. Fetch tile prices (indexes + curated stocks) — always, market open or not.
  3. If market is open: scan recommender universe for signals.
  4. Write latest.json. Discord pushes fire inline.
  5. GitHub Action commits data/ back to the repo.
"""

import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import yfinance as yf

import alerts
import detectors
import regime
import state
from universe import (
    TILE_INDEXES,
    TILE_STOCKS,
    all_recommender_tickers,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")
DATA_DIR = Path("../data")
LATEST_FILE = DATA_DIR / "latest.json"
SIGNAL_LOG_FILE = DATA_DIR / "signal_log.json"


def is_market_open() -> bool:
    """Naive market hours check. Doesn't account for US market holidays —
    fine for now, signal scanner will just find nothing on those days."""
    now = datetime.now(ET)
    if now.weekday() >= 5:
        return False
    open_t = now.replace(hour=9, minute=30, second=0, microsecond=0)
    close_t = now.replace(hour=16, minute=0, second=0, microsecond=0)
    return open_t <= now <= close_t


def fetch_usdcad() -> float:
    """Current USD/CAD exchange rate. Fallback to a reasonable default."""
    try:
        fx = yf.Ticker("CAD=X")  # Yahoo's symbol for USD->CAD
        hist = fx.history(period="2d", interval="1d")
        if not hist.empty:
            return round(float(hist.iloc[-1]["Close"]), 4)
    except Exception as e:
        log.warning(f"FX fetch failed: {e}")
    return 1.36


def fetch_tile_data(tile_list: list) -> list:
    """Fetch current price + day change for each entry in a tile list."""
    results = []
    for entry in tile_list:
        try:
            t = yf.Ticker(entry["ticker"])
            hist = t.history(period="2d", interval="1d")
            if hist.empty:
                continue
            today = hist.iloc[-1]
            prev_close = hist.iloc[-2]["Close"] if len(hist) > 1 else today["Open"]
            change_pct = (today["Close"] - prev_close) / prev_close * 100 if prev_close > 0 else 0.0
            results.append({
                "ticker": entry["ticker"],
                "name": entry["name"],
                "currency": entry["currency"],
                "price": round(float(today["Close"]), 2),
                "change_pct": round(change_pct, 2),
                "volume": int(today["Volume"]) if today["Volume"] > 0 else None,
            })
        except Exception as e:
            log.warning(f"Tile fetch failed for {entry['ticker']}: {e}")
        time.sleep(0.3)
    return results


def append_to_signal_log(signal: dict, current_regime: dict) -> None:
    """Append signal to signal_log.json with regime tag.

    Forward returns populated later by a separate Action.
    Regime is captured at fire-time so we can analyze how the same signal
    type performs in different market environments.
    """
    SIGNAL_LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    if SIGNAL_LOG_FILE.exists():
        try:
            data = json.loads(SIGNAL_LOG_FILE.read_text())
        except json.JSONDecodeError:
            data = {"signals": []}
    else:
        data = {"signals": []}

    fired_at = datetime.now(timezone.utc).isoformat()
    # Compact ID for cross-referencing — ticker_type_YYYYMMDDHHMMSS
    ts = fired_at.replace("-", "").replace(":", "").replace("T", "")[:14]
    signal_id = f"{signal['ticker']}_{signal['signal_type']}_{ts}"

    data["signals"].append({
        "id": signal_id,
        "ticker": signal["ticker"],
        "signal_type": signal["signal_type"],
        "fired_at": fired_at,
        "entry_price": signal["price"],
        "regime": current_regime,
        "details": signal,
        "forward_returns": {"1d": None, "3d": None, "7d": None, "30d": None},
    })
    SIGNAL_LOG_FILE.write_text(json.dumps(data, indent=2))


def scan_signals(current_regime: dict) -> list:
    """Scan the full recommender universe for signals. Push alerts + log.

    current_regime is passed in (computed once per scan, not per signal)
    and attached to each signal for later per-regime analysis.

    Runs all detectors in priority order, with dedup so a single ticker
    that fires multiple detectors only generates one alert per detector per day.
    """
    tickers = all_recommender_tickers()
    log.info(f"Scanning {len(tickers)} tickers across {4} detectors...")
    alert_state = state.load_state()
    today = datetime.now(ET).date().isoformat()
    active = []

    # Detector registry — each returns dict or None
    detector_fns = [
        ("vol",       detectors.check_volume_spike),
        ("momentum",  detectors.check_momentum),
        ("newhigh",   detectors.check_new_high),
        ("breakout",  detectors.check_breakout),
    ]

    for ticker in tickers:
        for prefix, fn in detector_fns:
            try:
                signal = fn(ticker)
            except Exception as e:
                log.debug(f"  {prefix} failed on {ticker}: {e}")
                signal = None
            if not signal:
                continue
            key = f"{prefix}:{ticker}:{today}"
            if state.should_alert(key, alert_state):
                price_chg = signal.get("price_change_pct", 0)
                detail = signal.get("multiplier") or signal.get("pct_5d") or signal.get("breakout_pct") or 0
                log.info(
                    f"  ✓ {ticker} [{signal['signal_type']}]: "
                    f"signal={detail:.2f} ({price_chg:+.2f}%)"
                )
                # All detectors push to Discord via the volume_spike alert format for now
                # (could specialize per detector type later)
                if signal["signal_type"] == "volume_spike":
                    alerts.volume_spike_alert(signal)
                else:
                    alerts.generic_signal_alert(signal)
                state.mark_alerted(key, alert_state)
                append_to_signal_log(signal, current_regime)
            active.append(signal)
        time.sleep(0.4)  # politeness; 4 detectors but most short-circuit early

    state.save_state(alert_state)
    return active


def write_latest(usdcad: float, tiles_idx: list, tiles_stk: list, signals: list, current_regime: dict) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "scanned_at": datetime.now(timezone.utc).isoformat(),
        "market_open": is_market_open(),
        "usdcad_rate": usdcad,
        "regime": current_regime,
        "tiles": {"indexes": tiles_idx, "stocks": tiles_stk},
        "active_signals": signals,
    }
    LATEST_FILE.write_text(json.dumps(payload, indent=2))
    log.info(f"Wrote {LATEST_FILE} — {len(signals)} active signals")


def main() -> int:
    log.info("Market Activity Monitor — scan starting")

    log.info("Fetching FX rate...")
    usdcad = fetch_usdcad()
    log.info(f"  USD/CAD = {usdcad}")

    log.info("Classifying market regime...")
    current_regime = regime.classify_current_regime()
    log.info(
        f"  Regime: {current_regime.get('classification', 'unknown')} "
        f"(VIX {current_regime.get('vix', '?')}, "
        f"SPY 5d {current_regime.get('spy_5d_pct', '?')}%)"
    )

    log.info("Fetching tile data...")
    tiles_idx = fetch_tile_data(TILE_INDEXES)
    tiles_stk = fetch_tile_data(TILE_STOCKS)
    log.info(f"  Got {len(tiles_idx)} indexes, {len(tiles_stk)} stocks")

    if is_market_open():
        signals = scan_signals(current_regime)
    else:
        log.info("Market closed — skipping signal scan (tile data still refreshed)")
        signals = []

    write_latest(usdcad, tiles_idx, tiles_stk, signals, current_regime)
    log.info("Scan complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
