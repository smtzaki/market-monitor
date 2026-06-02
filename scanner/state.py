"""Alert deduplication state.

Stored as data/alert_state.json (committed back to repo each run).
Each entry: alert_key -> ISO timestamp of last fire.
If a key fired within ALERT_COOLDOWN_HOURS, we skip alerting again.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

STATE_FILE = Path("../data/alert_state.json")
ALERT_COOLDOWN_HOURS = 4


def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=2))


def should_alert(key: str, state: dict) -> bool:
    last = state.get(key)
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - last_dt > timedelta(hours=ALERT_COOLDOWN_HOURS)


def mark_alerted(key: str, state: dict) -> None:
    state[key] = datetime.now(timezone.utc).isoformat()
