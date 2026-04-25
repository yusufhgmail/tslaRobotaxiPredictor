"""
Shared helpers for daily-update email gating.

`data/last_emailed.json` tracks `{count, emailed_at}` from the most recent
batch we emailed. When the daily auto-run sees a different count, it sends
emails and the workflow updates this file. Manual admin runs skip both
sending and the state update — that way the next auto-run still sees the
old value as "last emailed" and emails normally.
"""
from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HISTORY_CSV = ROOT / "data" / "history.csv"
LAST_EMAILED_JSON = ROOT / "data" / "last_emailed.json"


def get_current_count() -> int | None:
    if not HISTORY_CSV.exists():
        return None
    with HISTORY_CSV.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    if not rows:
        return None
    try:
        return int(float(rows[-1]["unsupervised_cumulative"]))
    except (KeyError, ValueError):
        return None


def get_last_emailed_count() -> int | None:
    if not LAST_EMAILED_JSON.exists():
        return None
    try:
        data = json.loads(LAST_EMAILED_JSON.read_text(encoding="utf-8"))
        return int(data.get("count")) if data.get("count") is not None else None
    except (json.JSONDecodeError, ValueError, TypeError):
        return None


def should_email() -> bool:
    current = get_current_count()
    if current is None:
        return False
    last = get_last_emailed_count()
    return last != current


def mark_emailed(count: int | None = None) -> None:
    if count is None:
        count = get_current_count()
    if count is None:
        return
    LAST_EMAILED_JSON.write_text(
        json.dumps(
            {"count": count, "emailed_at": datetime.now(timezone.utc).isoformat()},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
