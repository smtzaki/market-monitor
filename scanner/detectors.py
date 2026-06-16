"""Signal detectors.

Currently implements: volume_spike
Options detection was removed (too noisy on liquid names).

Each detector returns a dict on hit, None otherwise.
Detectors must never throw — failures are silent so one bad ticker
doesn't kill the whole scan.

σ-aware detection (added Phase 3.8):
  If data/historical_baselines.json exists, detectors use per-ticker σ
  to set thresholds. A 1% move on RY.TO (σ=0.96%) is a 1σ event = real.
  A 1% move on COIN (σ=4.5%) is sub-1σ = noise. Without baselines,
  detectors fall back to the universal thresholds.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

import yfinance as yf

ET = ZoneInfo("America/New_York")

# Universal tunable thresholds (fallback when no baseline data)
VOLUME_SPIKE_MULTIPLIER = 2.5      # projected volume must be >= 2.5x 20-day avg
VOLUME_MIN_PRICE_MOVE_PCT = 1.0    # ignore spikes with <1% price action

# σ-aware thresholds (used when baseline data available)
SIGMA_PRICE_THRESHOLD = 1.5        # price must move >= 1.5x ticker's 60-day daily σ
SIGMA_MIN_ABSOLUTE_PCT = 0.5       # but absolute floor of 0.5% to avoid tiny noise

# Baseline cache loaded once per scanner process
_BASELINES_CACHE: Optional[dict] = None
_BASELINES_PATH = Path("../data/historical_baselines.json")


def _load_baselines() -> dict:
    """Load baselines once, cache for the rest of the process."""
    global _BASELINES_CACHE
    if _BASELINES_CACHE is not None:
        return _BASELINES_CACHE
    if _BASELINES_PATH.exists():
        try:
            data = json.loads(_BASELINES_PATH.read_text())
            _BASELINES_CACHE = data.get("tickers", {})
        except (json.JSONDecodeError, KeyError):
            _BASELINES_CACHE = {}
    else:
        _BASELINES_CACHE = {}
    return _BASELINES_CACHE


def _minutes_into_session() -> float:
    """How many minutes into the 6.5-hour US trading session we are."""
    now = datetime.now(ET)
    open_t = now.replace(hour=9, minute=30, second=0, microsecond=0)
    elapsed = (now - open_t).total_seconds() / 60
    return max(1.0, min(elapsed, 390.0))


def _price_threshold_for_ticker(ticker: str) -> float:
    """Return the price-change threshold (in %) for this specific ticker.

    Uses 1.5× the ticker's historical 60-day daily σ, with a 0.5% floor.
    Falls back to universal threshold if no baseline available.
    """
    baselines = _load_baselines()
    b = baselines.get(ticker)
    if not b or b.get("sigma_60d_pct") is None:
        return VOLUME_MIN_PRICE_MOVE_PCT
    sigma = b["sigma_60d_pct"]
    if sigma <= 0:
        return VOLUME_MIN_PRICE_MOVE_PCT
    return max(SIGMA_MIN_ABSOLUTE_PCT, sigma * SIGMA_PRICE_THRESHOLD)


def check_volume_spike(ticker: str) -> Optional[dict]:
    """Detect unusual daily volume with corroborating price action.

    Logic:
      - Pull 22 days of daily bars.
      - Compute 20-day average volume (excluding today).
      - Project today's full-day volume by linear extrapolation from elapsed time.
      - Compute per-ticker σ-aware price threshold (or fall back to 1%).
      - Fire if projected >= 2.5x avg volume AND |price change| >= per-ticker threshold.

    The σ-aware threshold means a 1% move on a sleepy bank is meaningful while
    the same 1% move on a hyper-volatile semi gets correctly classified as noise.
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
        price_threshold = _price_threshold_for_ticker(ticker)

        if (
            multiplier >= VOLUME_SPIKE_MULTIPLIER
            and abs(price_change_pct) >= price_threshold
        ):
            return {
                "signal_type": "volume_spike",
                "ticker": ticker,
                "multiplier": round(multiplier, 2),
                "today_volume": int(today["Volume"]),
                "projected_volume": int(projected),
                "avg_volume_20d": int(avg_volume),
                "price_change_pct": round(price_change_pct, 2),
                "price_threshold_used": round(price_threshold, 2),
                "price": round(float(today["Close"]), 2),
                "direction": "bullish" if price_change_pct > 0 else "bearish",
            }
    except Exception:
        # Silent — one bad ticker mustn't kill the scan.
        return None
    return None


# ============================================================
# Phase 3.8 — Momentum / Breakout / New-High detectors
# These use the historical baselines so they don't pound yfinance per scan.
# Each is tracked separately in stats so we can validate independently.
# ============================================================

def check_new_high(ticker: str) -> Optional[dict]:
    """Fires when current price closes within 1% of (or above) 52-week high.

    Often a momentum continuation signal — stocks at new highs tend to make
    further new highs in the near term. Especially true in bull regimes.
    """
    baselines = _load_baselines()
    b = baselines.get(ticker)
    if not b:
        return None
    high_52w = b.get("high_52w")
    if not high_52w:
        return None
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="2d", interval="1d")
        if hist.empty:
            return None
        current_price = float(hist.iloc[-1]["Close"])
        pct_from_high = (current_price - high_52w) / high_52w * 100
        # Fire if within 1% of 52w high, OR if breaking through
        if pct_from_high >= -1.0:
            # Volume confirmation — at least average to count
            avg_vol = b.get("avg_volume_60d", 0)
            today_vol = float(hist.iloc[-1]["Volume"])
            elapsed = _minutes_into_session()
            projected_vol = today_vol * (390.0 / elapsed)
            vol_ratio = projected_vol / max(avg_vol, 1)
            if vol_ratio < 0.8:  # need at least 80% of normal volume to confirm
                return None
            return {
                "signal_type": "new_high",
                "ticker": ticker,
                "price": round(current_price, 2),
                "high_52w": round(high_52w, 2),
                "pct_from_high": round(pct_from_high, 2),
                "volume_ratio": round(vol_ratio, 2),
                "direction": "bullish",  # new highs are inherently bullish
            }
    except Exception:
        return None
    return None


def check_momentum(ticker: str) -> Optional[dict]:
    """Sustained-trend detector.

    Fires when:
      - Current price > 200-day MA (long-term uptrend)
      - Recent 5-day return strongly positive (short-term momentum)
      - Today not extreme (avoid duplicating volume_spike fires)

    Catches steady climbers — the BB.TO-style runs that volume_spike misses.
    """
    baselines = _load_baselines()
    b = baselines.get(ticker)
    if not b:
        return None
    ma_200 = b.get("ma_200d")
    if not ma_200:
        return None
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="7d", interval="1d")
        if len(hist) < 6:
            return None
        current_price = float(hist.iloc[-1]["Close"])
        price_5d_ago = float(hist.iloc[-6]["Close"])
        if price_5d_ago <= 0:
            return None
        pct_5d = (current_price - price_5d_ago) / price_5d_ago * 100
        # Today's intraday move
        today_open = float(hist.iloc[-1]["Open"])
        today_pct = (current_price - today_open) / today_open * 100 if today_open > 0 else 0
        # Fire criteria:
        #   - above 200d MA (structural uptrend)
        #   - 5d return > 3% (real momentum, not noise)
        #   - today not extreme (skip if today_pct > 2% — volume_spike will catch those)
        if current_price > ma_200 and pct_5d > 3.0 and abs(today_pct) < 2.0:
            return {
                "signal_type": "momentum",
                "ticker": ticker,
                "price": round(current_price, 2),
                "pct_5d": round(pct_5d, 2),
                "pct_above_200d": round((current_price - ma_200) / ma_200 * 100, 2),
                "today_pct": round(today_pct, 2),
                "direction": "bullish",
            }
    except Exception:
        return None
    return None


def check_breakout(ticker: str) -> Optional[dict]:
    """Range-breakout detector.

    Fires when a stock that was consolidating (tight range, low recent σ)
    breaks decisively above the range with volume.

    These can be the *start* of new trends, which momentum/new_high miss
    because they fire mid-trend.
    """
    baselines = _load_baselines()
    b = baselines.get(ticker)
    if not b:
        return None
    sigma = b.get("sigma_60d_pct", 0)
    if sigma <= 0:
        return None
    try:
        t = yf.Ticker(ticker)
        hist = t.history(period="31d", interval="1d")
        if len(hist) < 25:
            return None
        # Last 22 days excluding today
        prior = hist.iloc[:-1].tail(22)
        prior_high = float(prior["High"].max())
        prior_low = float(prior["Low"].min())
        prior_range_pct = (prior_high - prior_low) / prior_low * 100 if prior_low > 0 else 100
        # Recent range must have been tight (< 6%) for a breakout to mean something
        if prior_range_pct >= 6.0:
            return None
        current_price = float(hist.iloc[-1]["Close"])
        # Must break decisively above the prior high
        breakout_pct = (current_price - prior_high) / prior_high * 100
        if breakout_pct < 2.0:
            return None
        # Volume confirmation
        avg_vol = b.get("avg_volume_60d", 0)
        today_vol = float(hist.iloc[-1]["Volume"])
        elapsed = _minutes_into_session()
        projected_vol = today_vol * (390.0 / elapsed)
        vol_ratio = projected_vol / max(avg_vol, 1)
        if vol_ratio < 1.5:
            return None
        return {
            "signal_type": "breakout",
            "ticker": ticker,
            "price": round(current_price, 2),
            "prior_range_high": round(prior_high, 2),
            "prior_range_low": round(prior_low, 2),
            "prior_range_pct": round(prior_range_pct, 2),
            "breakout_pct": round(breakout_pct, 2),
            "volume_ratio": round(vol_ratio, 2),
            "direction": "bullish",
        }
    except Exception:
        return None
    return None
