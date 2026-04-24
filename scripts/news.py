"""
Pull news about Tesla robotaxi scaling and have Perplexity (Sonar) score each
item's likely impact on the scaling velocity.

One request does both jobs: `sonar-pro` searches the live web and returns a
structured list of {date, title, url, summary, impact_score, impact_reason}.
A `response_format` of `json_schema` keeps the output parseable.

Items are merged into `data/news.json` by URL so we accumulate a history.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
NEWS_JSON = ROOT / "data" / "news.json"

PERPLEXITY_URL = "https://api.perplexity.ai/chat/completions"
MODEL = "sonar-pro"

SYSTEM_PROMPT = """You are a research analyst tracking the scaling of Tesla's unsupervised robotaxi fleet across all active US markets (Austin, Dallas, Houston, Bay Area, and any newly launched cities).

Your job: surface the most important news from the last 14 days that is directly relevant to how fast Tesla is scaling its unsupervised / driverless robotaxi fleet.

What matters:
- Fleet size announcements, geofence expansions, new city launches, unsupervised (no safety driver) milestones in any market.
- Regulatory decisions at state or federal level (Texas DMV, California DMV, NHTSA, local ordinances).
- Hardware / software rollouts that gate scaling (HW5, FSD v14+, Cybercab production).
- Incidents that could pause or accelerate scaling anywhere.
- Competitive / market structure news that materially changes the glide path (Waymo expansion, partnerships, etc.).

What does NOT matter (exclude):
- Stock price movements without underlying operational news.
- Pure punditry, analyst price targets with no new fact.
- Musk tweets unless they announce a concrete operational change.
- Tangential FSD consumer news unrelated to robotaxi scaling.

For each qualifying item, assign an `impact_score` from -3 to +3:
  +3  Major positive catalyst (large fleet expansion, key regulatory green light)
  +2  Clear positive (meaningful fleet growth or approval)
  +1  Mildly positive
   0  Neutral / ambiguous
  -1  Mildly negative
  -2  Clear negative (incident, regulatory setback)
  -3  Major negative catalyst (service pause, severe regulatory action)

Be conservative. Most news is -1 to +1. Reserve +3/-3 for genuine catalysts.
Return strictly 3-8 items, most impactful first."""

USER_PROMPT = """Find the most impactful news from the past 14 days about Tesla unsupervised robotaxi scaling across all active US markets.

For each item return:
- date: ISO 8601 date (YYYY-MM-DD) of the event
- title: short headline
- url: canonical source URL
- summary: 1-2 sentences describing the fact (not opinion)
- impact_score: integer -3 to +3 following the rubric
- impact_reason: 1 sentence explaining why you gave that score in terms of fleet scaling velocity"""

SCHEMA = {
    "type": "object",
    "properties": {
        "items": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "date": {"type": "string"},
                    "title": {"type": "string"},
                    "url": {"type": "string"},
                    "summary": {"type": "string"},
                    "impact_score": {"type": "integer"},
                    "impact_reason": {"type": "string"},
                },
                "required": [
                    "date", "title", "url", "summary",
                    "impact_score", "impact_reason",
                ],
            },
        }
    },
    "required": ["items"],
}


def call_perplexity(api_key: str) -> list[dict]:
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": USER_PROMPT},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": {"schema": SCHEMA},
        },
        "search_recency_filter": "month",
    }
    r = requests.post(
        PERPLEXITY_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=90,
    )
    r.raise_for_status()
    body = r.json()
    content = body["choices"][0]["message"]["content"]
    parsed = json.loads(content)
    return parsed.get("items", [])


def load_existing() -> list[dict]:
    if not NEWS_JSON.exists():
        return []
    try:
        return json.loads(NEWS_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def normalize_item(it: dict) -> dict | None:
    """Validate and coerce one item; return None if unusable."""
    try:
        score = int(it["impact_score"])
    except (KeyError, ValueError, TypeError):
        return None
    score = max(-3, min(3, score))
    date = str(it.get("date", "")).strip()
    if len(date) < 10:
        return None
    return {
        "date": date[:10],
        "title": str(it.get("title", "")).strip(),
        "url": str(it.get("url", "")).strip(),
        "summary": str(it.get("summary", "")).strip(),
        "impact_score": score,
        "impact_reason": str(it.get("impact_reason", "")).strip(),
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def merge(existing: list[dict], fresh: list[dict]) -> list[dict]:
    by_url = {it["url"]: it for it in existing if it.get("url")}
    for it in fresh:
        n = normalize_item(it)
        if not n:
            continue
        if n["url"] in by_url:
            # Refresh score/summary but keep original capture date for recency decay.
            prev = by_url[n["url"]]
            prev.update({
                "title": n["title"],
                "summary": n["summary"],
                "impact_score": n["impact_score"],
                "impact_reason": n["impact_reason"],
                "date": n["date"],
            })
        else:
            by_url[n["url"]] = n
    merged = list(by_url.values())
    merged.sort(key=lambda x: x["date"], reverse=True)
    return merged


def main() -> int:
    api_key = os.environ.get("PERPLEXITY_API_KEY")
    if not api_key:
        print(
            "WARN: PERPLEXITY_API_KEY not set — skipping news fetch. "
            "Forecast will proceed without news adjustment.",
            file=sys.stderr,
        )
        # Write empty list on first run so downstream has a file to read.
        if not NEWS_JSON.exists():
            NEWS_JSON.parent.mkdir(parents=True, exist_ok=True)
            NEWS_JSON.write_text("[]", encoding="utf-8")
        return 0

    try:
        fresh = call_perplexity(api_key)
    except Exception as e:
        print(f"ERROR: Perplexity call failed: {e}", file=sys.stderr)
        # Non-fatal — use existing news.
        return 0

    existing = load_existing()
    merged = merge(existing, fresh)
    NEWS_JSON.parent.mkdir(parents=True, exist_ok=True)
    NEWS_JSON.write_text(json.dumps(merged, indent=2), encoding="utf-8")
    print(f"Wrote {len(merged)} news items ({len(fresh)} fresh this run)")
    for it in merged[:5]:
        print(f"  [{it['impact_score']:+d}] {it['date']} {it['title'][:80]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
