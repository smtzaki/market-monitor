"""Signal detectors.

Currently implements: volume_spike
Options detection was removed (too noisy on liquid names).

Each detector returns a dict on hit, None otherwise.
Detectors must never throw — failures are silent so one bad ticker
doesn't kill the whole scan.
"""

from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo

import yfinance as yf

ET = ZoneInfo("America/New_York")

# Tunable thresholds
VOLUME_SPIKE_MULTIPLIER = 2.5      # projected volume must be >= 2.5x 20-day avg
VOLUME_MIN_PRICE_MOVE_PCT = 1.0    # ignore spikes with <1% price action


def _minutes_into_session() -> float:
    """How many minutes into the 6.5-hour US trading session we are."""
    now = datetime.now(ET)
    open_t = now.replace(hour=9, minute=30, second=0, microsecond=0)
    elapsed = (now - open_t).total_seconds() / 60
    return max(1.0, min(elapsed, 390.0))


def check_volume_spike(ticker: str) -> Optional[dict]:
    """Detect unusual daily volume with corroborating price action.

    Logic:
      - Pull 22 days of daily bars.
      - Compute 20-day average volume (excluding today).
      - Project today's full-day volume by linear extrapolation from elapsed time.
      - Fire if projected >= 2.5x average AND |price change| >= 1%.

    The price filter is critical — volume spikes without price action are
    usually portfolio rebalancing or low-conviction flow.
    """
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="22d", interval="1d")
        if len(hist) < 21:
            return None

        today = hist.iloc[-1]
        avg_volume = hist.iloc[:-1].tail(20)["Volume"].mean()
        if avg_volume <= 0 or today["Open"] <= 0:
            return None

        elapsed = _minutes_into_session()
        projected = today["Volume"] * (390.0 / elapsed)
        multiplier = projected / avg_volume

        price_change_pct = (today["Close"] - today["Open"]) / today["Open"] * 100

        if (
            multiplier >= VOLUME_SPIKE_MULTIPLIER
            and abs(price_change_pct) >= VOLUME_MIN_PRICE_MOVE_PCT
        ):
            return {
                "signal_type": "volume_spike",
                "ticker": ticker,
                "multiplier": round(multiplier, 2),
                "today_volume": int(today["Volume"]),
                "projected_volume": int(projected),
                "avg_volume_20d": int(avg_volume),
                "price_change_pct": round(price_change_pct, 2),
                "price": round(float(today["Close"]), 2),
                "direction": "bullish" if price_change_pct > 0 else "bearish",
            }
    except Exception:
        # Silent — one bad ticker mustn't kill the scan.
        # Scanner logs the ticker that returned None vs raised; this is fine.
        return None
    return None
