"""Forward-return updater.

Runs once per day after US market close (separate cron Action from scan.py).
Walks signal_log.json, finds signals whose forward_returns slots are still null
but enough time has passed, fetches the actual close price at that point,
and writes the realized return back.

Also computes aggregate stats per signal type and writes signal_stats.json
for the recommender (Phase 4) and dashboard to consume.

Convention:
  - Labels (1d, 3d, 7d, 30d) are CALENDAR days from fire date.
  - We resolve to the first trading day's close ON OR AFTER fire_date + N days.
    So a Friday signal's "1d" return = Monday's close. Holidays handled the
    same way (next trading day).
"""

import json
import logging
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone, date
from pathlib import Path
from statistics import stdev
from typing import Optional

import yfinance as yf

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

DATA_DIR = Path("../data")
SIGNAL_LOG_FILE = DATA_DIR / "signal_log.json"
STATS_FILE = DATA_DIR / "signal_stats.json"
STATS_BY_TICKER_FILE = DATA_DIR / "signal_stats_by_ticker.json"
STATS_BY_REGIME_FILE = DATA_DIR / "signal_stats_by_regime.json"

INTERVALS_DAYS = {"1d": 1, "3d": 3, "7d": 7, "30d": 30}


# ============================================================
# I/O
# ============================================================

def load_log() -> dict:
    if not SIGNAL_LOG_FILE.exists():
        return {"signals": []}
    try:
        return json.loads(SIGNAL_LOG_FILE.read_text())
    except json.JSONDecodeError:
        log.error("signal_log.json is malformed — refusing to overwrite")
        sys.exit(1)


def save_log(data: dict) -> None:
    SIGNAL_LOG_FILE.write_text(json.dumps(data, indent=2))


def save_stats(stats: dict) -> None:
    STATS_FILE.write_text(json.dumps(stats, indent=2))


def save_per_ticker_stats(stats: dict) -> None:
    STATS_BY_TICKER_FILE.write_text(json.dumps(stats, indent=2))


def save_per_regime_stats(stats: dict) -> None:
    STATS_BY_REGIME_FILE.write_text(json.dumps(stats, indent=2))


# ============================================================
# Price lookup
# ============================================================

def find_close_on_or_after(hist, target: date) -> Optional[float]:
    """First close in `hist` whose date is >= target. None if no such row."""
    for idx in hist.index:
        idx_date = idx.date() if hasattr(idx, "date") else idx
        if idx_date >= target:
            return float(hist.loc[idx]["Close"])
    return None


def update_signal(signal: dict) -> bool:
    """Fill in any forward_returns slots that have come due. Returns True if changed."""
    try:
        fired_at = datetime.fromisoformat(signal["fired_at"])
    except (ValueError, KeyError):
        return False

    fire_date = fired_at.date()
    entry = signal.get("entry_price")
    ticker = signal.get("ticker")
    if not entry or not ticker or entry <= 0:
        return False

    elapsed = (datetime.now(timezone.utc).date() - fire_date).days

    # Identify slots that are (a) still null and (b) old enough to fill
    needed = [
        (label, days)
        for label, days in INTERVALS_DAYS.items()
        if signal["forward_returns"].get(label) is None and elapsed >= days
    ]
    if not needed:
        return False

    # Fetch one history pull covering the longest interval, plus weekend buffer
    max_offset = max(d for _, d in needed)
    try:
        hist = yf.Ticker(ticker).history(
            start=fire_date,
            end=fire_date + timedelta(days=max_offset + 7),
            interval="1d",
        )
    except Exception as e:
        log.warning(f"  history failed for {ticker}: {e}")
        return False

    if hist.empty:
        return False

    changed = False
    for label, days in needed:
        target = fire_date + timedelta(days=days)
        close = find_close_on_or_after(hist, target)
        if close is not None:
            ret_pct = (close - entry) / entry * 100
            signal["forward_returns"][label] = round(ret_pct, 3)
            log.info(f"  {ticker:<8} {signal['signal_type']:<14} {label}: {ret_pct:+.2f}%")
            changed = True

    return changed


# ============================================================
# Aggregate stats
# ============================================================

def compute_stats(signals: list) -> dict:
    """Bucket realized returns by signal_type + direction + interval. Compute summary stats.

    "Hit" definition:
      - bullish signal → return > 0 counts as a hit
      - bearish signal → return < 0 counts as a hit (the signal correctly anticipated a drop)
    """
    buckets = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))

    for sig in signals:
        st = sig.get("signal_type")
        direction = sig.get("details", {}).get("direction", "unknown")
        for label, val in sig.get("forward_returns", {}).items():
            if val is not None:
                buckets[st][direction][label].append(val)

    out = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "total_signals_logged": len(signals),
        "total_signals_with_any_return": sum(
            1 for s in signals if any(v is not None for v in s.get("forward_returns", {}).values())
        ),
        "by_signal_type": {},
    }

    for st, dirs in buckets.items():
        out["by_signal_type"][st] = {}
        for direction, intervals in dirs.items():
            out["by_signal_type"][st][direction] = {}
            for label, vals in intervals.items():
                if not vals:
                    continue
                hits = sum(
                    1 for v in vals
                    if (direction == "bullish" and v > 0)
                    or (direction == "bearish" and v < 0)
                )
                out["by_signal_type"][st][direction][label] = {
                    "n": len(vals),
                    "hit_rate": round(hits / len(vals), 3),
                    "avg_return_pct": round(sum(vals) / len(vals), 3),
                    "std_pct": round(stdev(vals), 3) if len(vals) > 1 else 0.0,
                    "min_pct": round(min(vals), 3),
                    "max_pct": round(max(vals), 3),
                    # Note: a signal needs ~20-30 fires before these numbers
                    # mean anything. Sample size warning is the recommender's job.
                }

    return out


def compute_per_ticker_stats(signals: list, min_n: int = 3) -> dict:
    """Per-ticker breakdown of realized returns.

    Reveals which tickers actually have signal value vs which are noise.
    Tickers with <min_n samples are excluded (insufficient data).
    """
    buckets = defaultdict(lambda: defaultdict(lambda: defaultdict(list)))

    for sig in signals:
        ticker = sig.get("ticker")
        direction = sig.get("details", {}).get("direction", "unknown")
        if not ticker:
            continue
        for label, val in sig.get("forward_returns", {}).items():
            if val is not None:
                buckets[ticker][direction][label].append(val)

    out = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "min_sample_size": min_n,
        "tickers": {},
    }

    for ticker, dirs in buckets.items():
        ticker_data = {}
        for direction, intervals in dirs.items():
            dir_data = {}
            for label, vals in intervals.items():
                if len(vals) < min_n:
                    continue
                hits = sum(
                    1 for v in vals
                    if (direction == "bullish" and v > 0)
                    or (direction == "bearish" and v < 0)
                )
                dir_data[label] = {
                    "n": len(vals),
                    "hit_rate": round(hits / len(vals), 3),
                    "avg_return_pct": round(sum(vals) / len(vals), 3),
                    "std_pct": round(stdev(vals), 3) if len(vals) > 1 else 0.0,
                }
            if dir_data:
                ticker_data[direction] = dir_data
        if ticker_data:
            out["tickers"][ticker] = ticker_data

    return out


def compute_per_regime_stats(signals: list) -> dict:
    """Bucket stats by market regime (bull/correction/bear/neutral/crisis).

    Reveals whether signals work differently in different market environments.
    Critical for the recommender — same signal type may have very different
    edge in correction vs bull market.
    """
    buckets = defaultdict(lambda: defaultdict(lambda: defaultdict(lambda: defaultdict(list))))

    for sig in signals:
        regime = sig.get("regime", {}).get("classification", "unknown")
        st = sig.get("signal_type")
        direction = sig.get("details", {}).get("direction", "unknown")
        for label, val in sig.get("forward_returns", {}).items():
            if val is not None:
                buckets[regime][st][direction][label].append(val)

    out = {
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "by_regime": {},
    }

    for regime, types in buckets.items():
        out["by_regime"][regime] = {}
        for st, dirs in types.items():
            out["by_regime"][regime][st] = {}
            for direction, intervals in dirs.items():
                out["by_regime"][regime][st][direction] = {}
                for label, vals in intervals.items():
                    if not vals:
                        continue
                    hits = sum(
                        1 for v in vals
                        if (direction == "bullish" and v > 0)
                        or (direction == "bearish" and v < 0)
                    )
                    out["by_regime"][regime][st][direction][label] = {
                        "n": len(vals),
                        "hit_rate": round(hits / len(vals), 3),
                        "avg_return_pct": round(sum(vals) / len(vals), 3),
                        "std_pct": round(stdev(vals), 3) if len(vals) > 1 else 0.0,
                    }

    return out


# ============================================================
# Main
# ============================================================

def main() -> int:
    log.info("Forward-return updater starting")
    data = load_log()
    signals = data.get("signals", [])
    log.info(f"  signal_log: {len(signals)} signals")

    updated_count = 0
    for sig in signals:
        if update_signal(sig):
            updated_count += 1
        time.sleep(0.3)  # rate-limit politeness for yfinance

    if updated_count:
        save_log(data)
        log.info(f"Persisted updates to {updated_count} signals")
    else:
        log.info("No new returns to fill in")

    # Always recompute stats — even if no new returns landed today, the
    # output is small and gives the dashboard a fresh timestamp.
    stats = compute_stats(signals)
    save_stats(stats)
    log.info(
        f"Stats written: {stats['total_signals_with_any_return']}/"
        f"{stats['total_signals_logged']} signals have realized returns"
    )

    # Per-ticker stats — reveals which tickers actually have edge vs which are noise
    ticker_stats = compute_per_ticker_stats(signals)
    save_per_ticker_stats(ticker_stats)
    log.info(f"Per-ticker stats: {len(ticker_stats['tickers'])} tickers with sufficient data")

    # Per-regime stats — reveals whether signals work in different market environments
    regime_stats = compute_per_regime_stats(signals)
    save_per_regime_stats(regime_stats)
    n_regimes = len(regime_stats["by_regime"])
    log.info(f"Per-regime stats: {n_regimes} regimes represented in signal log")

    return 0


if __name__ == "__main__":
    sys.exit(main())
