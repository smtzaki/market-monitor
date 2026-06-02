"""Discord webhook alerts."""

import logging
import os
from datetime import datetime, timezone

import requests

log = logging.getLogger(__name__)

DISCORD_WEBHOOK_URL = os.getenv("DISCORD_WEBHOOK_URL", "")

# Discord embed colors
RED = 0xFF4444      # bearish / dump
GREEN = 0x44FF44    # bullish / accumulation
YELLOW = 0xFFAA00   # neutral unusual


def _send(title: str, description: str, color: int, fields: list = None) -> None:
    """Low-level webhook POST. Fails silently — alerts aren't critical."""
    if not DISCORD_WEBHOOK_URL:
        log.warning(f"[NO WEBHOOK] {title} — {description}")
        return

    embed = {
        "title": title,
        "description": description,
        "color": color,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "footer": {"text": "Market Activity Monitor"},
    }
    if fields:
        embed["fields"] = fields

    try:
        r = requests.post(
            DISCORD_WEBHOOK_URL,
            json={"embeds": [embed]},
            timeout=10,
        )
        r.raise_for_status()
    except requests.RequestException as e:
        log.error(f"Discord webhook failed: {e}")


def volume_spike_alert(signal: dict) -> None:
    """Send a formatted Discord embed for a volume spike signal."""
    bullish = signal["direction"] == "bullish"
    color = GREEN if bullish else RED
    direction = "📈 BULLISH SURGE" if bullish else "📉 BEARISH DUMP"

    _send(
        title=f"🚨 Volume Spike: ${signal['ticker']}",
        description=(
            f"{direction} — projecting **{signal['multiplier']:.1f}x** average daily volume\n"
            f"Price today: **{signal['price_change_pct']:+.2f}%** at ${signal['price']:.2f}"
        ),
        color=color,
        fields=[
            {"name": "Today's Vol",  "value": f"{signal['today_volume']:,}",       "inline": True},
            {"name": "Projected",    "value": f"{signal['projected_volume']:,}",   "inline": True},
            {"name": "20-Day Avg",   "value": f"{signal['avg_volume_20d']:,}",     "inline": True},
        ],
    )
