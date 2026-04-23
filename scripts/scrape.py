"""
Scrape Tesla Austin robotaxi counts from robotaxitracker.com.

The site is a Next.js App Router SPA backed by Convex. Data for the initial
page render is streamed through `self.__next_f.push([1, "..."])` calls in the
HTML. We reconstruct that stream and pull the fleet metrics out of the
`getHomepageData` query blob.

Metrics captured per snapshot:
- total_vehicles: `totalVehiclesCount` (non-test vehicles ever spotted)
- total_with_test: `totalVehiclesCountWithTest` (includes cybercabs etc.)
- active_30d: `recentVehiclesCount30d` (seen in last 30 days)
- unsupervised: `unsupervisedPassengerCount` (no safety driver)
- cybercabs: `cybercabCount`
- deprecated: `deprecatedCount`

The "total robotaxis" metric used downstream is `total_with_test` by default
(switchable in the chart).
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

URL = "https://robotaxitracker.com/?provider=tesla&area=austin"
USER_AGENT = "Mozilla/5.0 (RobotaxiScalingPredictor/1.0)"

HISTORY_CSV = Path(__file__).resolve().parent.parent / "data" / "history.csv"

FIELDS = [
    "timestamp_utc",
    "total_vehicles",
    "total_with_test",
    "active_30d",
    "unsupervised",
    "cybercabs",
    "deprecated",
    "unsupervised_percent_7d",
    "unsupervised_percent_30d",
    "unsupervised_percent_since_launch",
]


def fetch_html(url: str = URL) -> str:
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    r.raise_for_status()
    return r.text


def extract_rsc_stream(html: str) -> str:
    """Concatenate all __next_f.push payloads back into the RSC stream."""
    pushes = re.findall(r"self\.__next_f\.push\(\[\d+,(\".*?\")\]\)", html, re.S)
    stream = ""
    for p in pushes:
        try:
            stream += json.loads(p)
        except json.JSONDecodeError:
            continue
    if not stream:
        raise RuntimeError("No __next_f.push payload found — page structure changed?")
    return stream


_NUM = r"-?\d+(?:\.\d+)?"


def _find_number(stream: str, key: str) -> float | None:
    m = re.search(rf'"{re.escape(key)}":({_NUM})', stream)
    return float(m.group(1)) if m else None


def _find_percent(stream: str, window: str) -> float | None:
    """Pull unsupervisedRideShareWindows[window].percent."""
    # Anchor on the window key then grab the next "percent":N value.
    m = re.search(
        rf'"{re.escape(window)}":\{{[^{{}}]*?"percent":({_NUM})', stream
    )
    return float(m.group(1)) if m else None


def parse_metrics(stream: str) -> dict:
    metrics = {
        "total_vehicles": _find_number(stream, "totalVehiclesCount"),
        "total_with_test": _find_number(stream, "totalVehiclesCountWithTest"),
        "active_30d": _find_number(stream, "recentVehiclesCount30d"),
        "unsupervised": _find_number(stream, "unsupervisedPassengerCount"),
        "cybercabs": _find_number(stream, "cybercabCount"),
        "deprecated": _find_number(stream, "deprecatedCount"),
        "unsupervised_percent_7d": _find_percent(stream, "7d"),
        "unsupervised_percent_30d": _find_percent(stream, "30d"),
        "unsupervised_percent_since_launch": _find_percent(stream, "since_launch"),
    }
    # Require the core count so we fail loudly when scraping breaks.
    if metrics["total_with_test"] is None and metrics["total_vehicles"] is None:
        raise RuntimeError("Could not extract vehicle counts — page structure changed?")
    return metrics


def load_history() -> list[dict]:
    if not HISTORY_CSV.exists():
        return []
    with HISTORY_CSV.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def append_snapshot(metrics: dict) -> dict:
    HISTORY_CSV.parent.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    row = {"timestamp_utc": ts, **{k: metrics.get(k) for k in FIELDS if k != "timestamp_utc"}}

    existing = load_history()
    today = ts[:10]
    existing = [r for r in existing if not r["timestamp_utc"].startswith(today)]
    existing.append(row)
    existing.sort(key=lambda r: r["timestamp_utc"])

    with HISTORY_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in existing:
            w.writerow(r)
    return row


def main() -> int:
    try:
        html = fetch_html()
        stream = extract_rsc_stream(html)
        metrics = parse_metrics(stream)
    except Exception as e:
        print(f"ERROR: scrape failed: {e}", file=sys.stderr)
        return 1

    row = append_snapshot(metrics)
    print(json.dumps(row, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
