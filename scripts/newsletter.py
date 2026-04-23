"""
Send the weekly summary email to every confirmed newsletter subscriber.

Runs after notify.py in the weekly workflow. Shares notify.py's `render`
function so the owner email and subscriber emails stay in sync.

Environment variables required (all skippable — missing env = no-op):
    RESEND_API_KEY           sender auth
    SUPABASE_URL             https://<ref>.supabase.co
    SUPABASE_SECRET_KEY      sb_secret_... (service role; bypasses RLS)

Subscribers considered eligible:
    confirmed_at is not null AND unsubscribed_at is null
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import notify  # reuses render() and data-loading

RESEND_URL = "https://api.resend.com/emails"
FROM = "Robotaxi Predictor <onboarding@resend.dev>"


def list_subscribers(supabase_url: str, service_key: str) -> list[dict]:
    """Return list of {email, unsubscribe_token} for every confirmed, non-unsubbed row."""
    r = requests.get(
        f"{supabase_url}/rest/v1/subscribers",
        params={
            "select": "email,unsubscribe_token",
            "confirmed_at": "not.is.null",
            "unsubscribed_at": "is.null",
        },
        headers={
            "apikey": service_key,
            "Authorization": f"Bearer {service_key}",
            "Accept": "application/json",
        },
        timeout=30,
    )
    r.raise_for_status()
    return [row for row in r.json() if row.get("email")]


def main() -> int:
    api_key = os.environ.get("RESEND_API_KEY")
    supabase_url = os.environ.get("SUPABASE_URL")
    service_key = os.environ.get("SUPABASE_SECRET_KEY")
    if not (api_key and supabase_url and service_key):
        print("Newsletter skipped — missing RESEND/SUPABASE env.", file=sys.stderr)
        return 0

    forecast = json.loads(notify.FORECAST_JSON.read_text(encoding="utf-8"))
    history = notify.load_history()
    news = (
        json.loads(notify.NEWS_JSON.read_text(encoding="utf-8"))
        if notify.NEWS_JSON.exists()
        else []
    )

    latest, prior = notify.week_over_week(history)
    if latest is None:
        print("Newsletter skipped — no history yet.", file=sys.stderr)
        return 0
    if prior is None:
        delta_text = "First observation — no week-over-week delta yet."
    else:
        diff = latest - prior
        pct = (diff / prior * 100) if prior > 0 else 0.0
        sign = "+" if diff >= 0 else ""
        delta_text = f"{sign}{diff} vs. 7d ago ({sign}{pct:.1f}%)"

    ctx = {
        "latest": latest,
        "delta_text": delta_text,
        "fit": forecast["fit"],
        "etas": forecast.get("eta_by_target", {}),
        "targets": forecast.get("targets", []),
        "news_shift_pp": forecast["fit"].get("news_rate_shift", 0.0) * 100,
        "fresh_news": notify.new_news_this_week(news),
    }

    try:
        subscribers = list_subscribers(supabase_url, service_key)
    except Exception as e:
        print(f"Newsletter fetch failed: {e}", file=sys.stderr)
        return 0

    if not subscribers:
        print("No subscribers yet.")
        return 0

    sent = 0
    failed = 0
    for row in subscribers:
        email = row["email"]
        unsub_url = (
            f"{notify.DASHBOARD_URL}unsubscribe.html?token={row.get('unsubscribe_token', '')}"
            if row.get("unsubscribe_token") else None
        )
        subject, html, text = notify.render(
            datetime.now(timezone.utc).strftime("%Y-%m-%d"), ctx, unsub_url,
        )
        try:
            r = requests.post(
                RESEND_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"from": FROM, "to": [email], "subject": subject, "html": html, "text": text},
                timeout=30,
            )
            if r.status_code < 400:
                sent += 1
            else:
                failed += 1
                print(f"  FAIL {email}: {r.status_code} {r.text[:120]}", file=sys.stderr)
        except Exception as e:
            failed += 1
            print(f"  ERR  {email}: {e}", file=sys.stderr)
        time.sleep(0.15)

    print(f"Newsletter: sent={sent} failed={failed} total={len(subscribers)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
