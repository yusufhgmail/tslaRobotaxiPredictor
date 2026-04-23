"""
Archive the current forecast.json as a dated snapshot so the community can
browse past weekly predictions.

Writes:
- docs/data/snapshots/YYYY-MM-DD.json  — a full copy of data/forecast.json
- docs/data/snapshots/index.json       — list of {date, total, ...} for the viewer

The snapshots live under docs/ so they're served by GitHub Pages alongside the
rest of the site. The viewer page (docs/snapshots.html) fetches index.json and
loads any individual snapshot on demand.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORECAST = ROOT / "data" / "forecast.json"
OUT_DIR = ROOT / "docs" / "data" / "snapshots"
INDEX = OUT_DIR / "index.json"


def main() -> int:
    if not FORECAST.exists():
        print("ERROR: forecast.json missing", file=sys.stderr)
        return 1
    forecast = json.loads(FORECAST.read_text(encoding="utf-8"))
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # Use the forecast's generation date; fall back to today.
    gen = forecast.get("generated_at", "")
    date = gen[:10] if len(gen) >= 10 else datetime.now(timezone.utc).date().isoformat()

    snap_path = OUT_DIR / f"{date}.json"
    snap_path.write_text(json.dumps(forecast), encoding="utf-8")

    # Build / refresh the index from whatever snapshots exist.
    entries = []
    for p in sorted(OUT_DIR.glob("*.json")):
        if p.name == "index.json":
            continue
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        hist = d.get("historical") or []
        latest = hist[-1]["value"] if hist else None
        fit = d.get("fit") or {}
        etas = d.get("eta_by_target") or {}
        entries.append({
            "date": p.stem,
            "file": f"data/snapshots/{p.name}",
            "latest": latest,
            "weekly_growth_pct": round((fit.get("rate_weekly") or 0) * 100, 2),
            "eta_1000": (etas.get("1000") or {}).get("p50"),
            "eta_1800": (etas.get("1800") or {}).get("p50"),
            "n_points": fit.get("n_points"),
        })
    entries.sort(key=lambda e: e["date"], reverse=True)
    INDEX.write_text(json.dumps(entries, indent=2), encoding="utf-8")
    print(f"Archived {snap_path.name}. Index has {len(entries)} snapshots.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
