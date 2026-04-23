"""
Pull Tesla Austin robotaxi data from robotaxitracker.com.

The site is backed by Convex and exposes a public HTTP query endpoint. We call
the same `queries/fleet:getHomepageData` function the homepage uses, but with
vehicleLimit=500 so we get the full fleet list. Each vehicle carries a
`first_unsupervised_spotted` timestamp — that lets us reconstruct the full
daily cumulative-unsupervised-fleet curve from launch (2026-01-22) through
today, rather than only collecting one new point per weekly run.

Outputs
-------
- `data/history.csv` — one row per day from the first activation through today.
  Columns: date, unsupervised_cumulative, unsupervised_first_spotted_count,
  total_vehicles_current, total_with_test_current, active_30d_current,
  cybercabs_current, source ("reconstructed" | "snapshot").
  The "_current" fields are the same across all rows in a single run because
  we only have today's current-totals snapshot; they're kept for context.
- `data/snapshot.json` — the raw Convex response so downstream code can read
  fresh metrics without re-querying.

Run weekly (or whenever). The history CSV is rewritten from scratch every run
because the Convex data may update retroactively (newly-discovered past
activations backfill into earlier dates).
"""
from __future__ import annotations

import csv
import json
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

CONVEX_URL = "https://graceful-eel-151.convex.cloud/api/query"
QUERY_PATH = "queries/fleet:getHomepageData"
# Tesla Austin service area ID — discovered by inspecting the SSR payload.
SERVICE_AREA_ID = "jx72bv82f8vfhp6n2hcd5ynq4h7yz7rn"
VEHICLE_LIMIT = 500

ROOT = Path(__file__).resolve().parent.parent
HISTORY_CSV = ROOT / "data" / "history.csv"
SNAPSHOT_JSON = ROOT / "data" / "snapshot.json"

FIELDS = [
    "date",
    "unsupervised_cumulative",
    "unsupervised_new_that_day",
    "total_vehicles_current",
    "total_with_test_current",
    "active_30d_current",
    "cybercabs_current",
    "source",
]


def fetch() -> dict:
    r = requests.post(
        CONVEX_URL,
        json={
            "path": QUERY_PATH,
            "args": {
                "provider": "tesla",
                "serviceAreaId": SERVICE_AREA_ID,
                "sortBy": "recently_discovered",
                "tripLimit": 1,
                "vehicleLimit": VEHICLE_LIMIT,
            },
            "format": "json",
        },
        headers={"User-Agent": "RobotaxiScalingPredictor/1.0"},
        timeout=30,
    )
    r.raise_for_status()
    body = r.json()
    if "value" not in body:
        raise RuntimeError(f"Unexpected Convex response: {body!r}")
    return body["value"]


def daily_rows(data: dict) -> list[dict]:
    """Reconstruct daily unsupervised cumulative counts."""
    vehicles = data.get("vehicles", [])
    dates_with_unsup = [
        v["first_unsupervised_spotted"][:10]
        for v in vehicles
        if v.get("first_unsupervised_spotted")
    ]
    if not dates_with_unsup:
        # No unsupervised history yet — return just today with zeros.
        today = datetime.now(timezone.utc).date()
        return [{
            "date": today.isoformat(),
            "unsupervised_cumulative": 0,
            "unsupervised_new_that_day": 0,
            "total_vehicles_current": int(data.get("totalVehiclesCount", 0) or 0),
            "total_with_test_current": int(data.get("totalVehiclesCountWithTest", 0) or 0),
            "active_30d_current": int(data.get("recentVehiclesCount30d", 0) or 0),
            "cybercabs_current": int(data.get("cybercabCount", 0) or 0),
            "source": "snapshot",
        }]

    activations = Counter(dates_with_unsup)
    first = date.fromisoformat(min(dates_with_unsup))
    today = datetime.now(timezone.utc).date()
    rows = []
    cumulative = 0
    d = first
    total_vehicles_now = int(data.get("totalVehiclesCount", 0) or 0)
    total_with_test_now = int(data.get("totalVehiclesCountWithTest", 0) or 0)
    active_30d_now = int(data.get("recentVehiclesCount30d", 0) or 0)
    cybercabs_now = int(data.get("cybercabCount", 0) or 0)
    while d <= today:
        iso = d.isoformat()
        new_today = activations.get(iso, 0)
        cumulative += new_today
        rows.append({
            "date": iso,
            "unsupervised_cumulative": cumulative,
            "unsupervised_new_that_day": new_today,
            # Historical rows share today's totals (we only have today's
            # snapshot for those metrics). The forecast module keys off
            # `unsupervised_cumulative` so this is fine.
            "total_vehicles_current": total_vehicles_now,
            "total_with_test_current": total_with_test_now,
            "active_30d_current": active_30d_now,
            "cybercabs_current": cybercabs_now,
            "source": "reconstructed" if d < today else "snapshot",
        })
        d += timedelta(days=1)
    return rows


def write_history(rows: list[dict]) -> None:
    HISTORY_CSV.parent.mkdir(parents=True, exist_ok=True)
    with HISTORY_CSV.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main() -> int:
    try:
        data = fetch()
    except Exception as e:
        print(f"ERROR: Convex fetch failed: {e}", file=sys.stderr)
        return 1

    snapshot = {
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "totalVehiclesCount": data.get("totalVehiclesCount"),
        "totalVehiclesCountWithTest": data.get("totalVehiclesCountWithTest"),
        "recentVehiclesCount30d": data.get("recentVehiclesCount30d"),
        "unsupervisedPassengerCount": data.get("unsupervisedPassengerCount"),
        "cybercabCount": data.get("cybercabCount"),
        "deprecatedCount": data.get("deprecatedCount"),
        "unsupervisedRideShareWindows": data.get("unsupervisedRideShareWindows"),
        "unsupervisedRideShareSince": data.get("unsupervisedRideShareSince"),
        "vehicles_returned": len(data.get("vehicles", [])),
    }
    SNAPSHOT_JSON.parent.mkdir(parents=True, exist_ok=True)
    SNAPSHOT_JSON.write_text(json.dumps(snapshot, indent=2), encoding="utf-8")

    rows = daily_rows(data)
    write_history(rows)

    last = rows[-1]
    print(f"History: {len(rows)} days ({rows[0]['date']} to {rows[-1]['date']})")
    print(f"Latest unsupervised cumulative: {last['unsupervised_cumulative']}")
    print(f"Current totals — unsup: {snapshot['unsupervisedPassengerCount']}, "
          f"total w/ test: {snapshot['totalVehiclesCountWithTest']}, "
          f"cybercabs: {snapshot['cybercabCount']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
