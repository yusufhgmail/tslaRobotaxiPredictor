"""
Send a weekly summary email via Resend.

Skips silently when RESEND_API_KEY or NOTIFY_EMAIL is unset so the workflow
doesn't fail before the user has wired up the secret/variable.

The email has:
- Subject line with headline number + date
- Short HTML body: current count, week-over-week delta, fitted growth rate,
  P50 ETAs to each threshold, net news rate shift, new news items this week,
  and a link to the live dashboard
- Plain-text fallback covering the same info
"""
from __future__ import annotations

import csv
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
HISTORY_CSV = ROOT / "data" / "history.csv"
FORECAST_JSON = ROOT / "data" / "forecast.json"
NEWS_JSON = ROOT / "data" / "news.json"

DASHBOARD_URL = "https://robotaxipredictor.com/"
REPO_URL = "https://github.com/yusufhgmail/tslaRobotaxiPredictor"

RESEND_URL = "https://api.resend.com/emails"
DEFAULT_FROM = "Robotaxi Predictor <onboarding@resend.dev>"


def load_history() -> list[dict]:
    if not HISTORY_CSV.exists():
        return []
    with HISTORY_CSV.open("r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def week_over_week(history: list[dict]) -> tuple[int | None, int | None]:
    if not history:
        return None, None
    try:
        latest = int(float(history[-1]["unsupervised_cumulative"]))
    except (KeyError, ValueError):
        return None, None
    latest_date = history[-1].get("date", "")
    try:
        cutoff = datetime.fromisoformat(latest_date).date() - timedelta(days=7)
    except ValueError:
        return latest, None
    prior = None
    for r in history:
        try:
            d = datetime.fromisoformat(r["date"]).date()
        except (KeyError, ValueError):
            continue
        if d <= cutoff:
            try:
                prior = int(float(r["unsupervised_cumulative"]))
            except (ValueError, KeyError):
                continue
    return latest, prior


def new_news_this_week(news: list[dict]) -> list[dict]:
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=7)
    fresh = []
    for it in news:
        captured = it.get("captured_at") or it.get("date")
        if not captured:
            continue
        try:
            dt = datetime.fromisoformat(captured.replace("Z", "+00:00"))
        except ValueError:
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        if dt >= cutoff:
            fresh.append(it)
    return fresh


def render(subject_suffix: str, ctx: dict, unsubscribe_url: str | None = None) -> tuple[str, str, str]:
    latest = ctx["latest"]
    delta_text = ctx["delta_text"]
    fit = ctx["fit"]
    etas = ctx["etas"]
    news_shift_pp = ctx["news_shift_pp"]
    fresh_news = ctx["fresh_news"]

    subject = f"Robotaxi update: {latest} unsupervised — {subject_suffix}"

    eta_rows_html = ""
    eta_rows_text = []
    for t in ctx["targets"]:
        k = str(int(t["value"]))
        e = etas.get(k, {})
        val = int(t["value"])
        eta_rows_html += (
            f"<tr>"
            f"<td style='padding:6px 12px 6px 0;color:#a8a8a8;font-size:11px;letter-spacing:0.1em;text-transform:uppercase;font-weight:600'>P50 to {val:,}</td>"
            f"<td style='padding:6px 0'><b style='font-weight:600'>{e.get('p50') or 'n/a'}</b>"
            f" <span style='color:#a8a8a8;font-size:12px'>"
            f"(P25 {e.get('p25') or 'n/a'} · P75 {e.get('p75') or 'n/a'})</span></td>"
            f"</tr>"
        )
        eta_rows_text.append(
            f"  P50 to {val:,}: {e.get('p50') or 'n/a'} "
            f"(P25 {e.get('p25') or 'n/a'}, P75 {e.get('p75') or 'n/a'})"
        )

    news_html = ""
    news_text_lines = []
    if fresh_news:
        items_html = "".join(
            f"<li style='margin:6px 0'>"
            f"<span style='color:{'#4ade80' if it['impact_score'] > 0 else '#e31937' if it['impact_score'] < 0 else '#737373'};font-weight:700'>"
            f"{'+' if it['impact_score'] > 0 else ''}{it['impact_score']}</span> "
            f"<span style='color:#ffffff'>{it.get('title', '')}</span>"
            f"<div style='color:#a8a8a8;font-size:12px'>{it.get('impact_reason', '')}</div>"
            f"</li>"
            for it in fresh_news
        )
        news_html = (
            f"<h3 style='margin:18px 0 8px;font-size:11px;color:#a8a8a8;"
            f"text-transform:uppercase;letter-spacing:0.14em;font-weight:600'>"
            f"New this week ({len(fresh_news)})</h3>"
            f"<ul style='margin:0;padding-left:18px;list-style:none'>{items_html}</ul>"
        )
        for it in fresh_news:
            sign = "+" if it["impact_score"] > 0 else ""
            news_text_lines.append(
                f"  [{sign}{it['impact_score']}] {it.get('title', '')}"
            )

    html = f"""<!doctype html>
<html><body style="background:#000000;color:#ffffff;font-family:'Inter','Helvetica Neue',Helvetica,Arial,-apple-system,'Segoe UI',sans-serif;padding:20px;margin:0">
<div style="max-width:600px;margin:0 auto;background:#0f0f0f;border:1px solid #262626;border-radius:2px;padding:28px">
  <div style="text-align:center;margin-bottom:20px;padding-bottom:16px;border-bottom:1px solid #262626">
    <div style="font-weight:700;font-size:20px;color:#e31937;letter-spacing:0.6em;padding-left:0.6em;text-transform:uppercase">TESLA</div>
    <div style="font-size:10px;letter-spacing:0.25em;text-transform:uppercase;color:#a8a8a8;font-weight:500;margin-top:6px">Robotaxi Predictor</div>
  </div>

  <div style="color:#a8a8a8;font-size:10px;letter-spacing:0.18em;text-transform:uppercase;font-weight:600;margin-bottom:12px">
    Weekly update · {datetime.now(timezone.utc).strftime('%Y-%m-%d')}
  </div>

  <div style="font-size:36px;font-weight:500;font-variant-numeric:tabular-nums;letter-spacing:-0.02em">
    {latest} <span style="color:#a8a8a8;font-size:14px;font-weight:400">unsupervised</span>
  </div>
  <div style="color:#a8a8a8;font-size:13px;margin-bottom:18px">{delta_text}</div>

  <table style="border-collapse:collapse;font-size:13px;margin-top:8px;width:100%">
    <tr>
      <td style="padding:6px 12px 6px 0;color:#a8a8a8;font-size:11px;letter-spacing:0.1em;text-transform:uppercase;font-weight:600">Weekly growth</td>
      <td style="padding:6px 0"><b style="font-weight:600">{fit['rate_weekly'] * 100:.1f}%</b>
        <span style="color:#a8a8a8;font-size:12px">(doubles ~{fit['doubling_weeks']:.1f}w)</span></td>
    </tr>
    {eta_rows_html}
    <tr>
      <td style="padding:6px 12px 6px 0;color:#a8a8a8;font-size:11px;letter-spacing:0.1em;text-transform:uppercase;font-weight:600">News rate shift</td>
      <td style="padding:6px 0">{news_shift_pp:+.2f} pp/wk</td>
    </tr>
  </table>

  {news_html}

  <div style="margin-top:24px;text-align:center">
    <a href="{DASHBOARD_URL}" style="display:inline-block;background:#e31937;color:#ffffff;
      padding:12px 26px;border-radius:2px;text-decoration:none;font-weight:600;font-size:11px;
      letter-spacing:0.18em;text-transform:uppercase">
      Open dashboard</a>
  </div>

  <div style="color:#a8a8a8;font-size:11px;margin-top:24px;border-top:1px solid #262626;padding-top:14px;text-align:center">
    Generated automatically every Monday 13:00 UTC ·
    <a href="{REPO_URL}" style="color:#a8a8a8">source</a>
    {f'· <a href="{unsubscribe_url}" style="color:#a8a8a8">unsubscribe</a>' if unsubscribe_url else ''}
  </div>
  <div style="color:#666;font-size:9px;margin-top:10px;text-align:center;letter-spacing:0.18em;text-transform:uppercase">
    Independent tracker · Not affiliated with Tesla, Inc.
  </div>
</div>
</body></html>"""

    text = (
        f"Tesla Robotaxi — weekly update\n"
        f"{datetime.now(timezone.utc).strftime('%Y-%m-%d')}\n\n"
        f"  Unsupervised: {latest}\n"
        f"  {delta_text}\n"
        f"  Weekly growth: {fit['rate_weekly'] * 100:.1f}% (doubles ~{fit['doubling_weeks']:.1f}w)\n"
        + "\n".join(eta_rows_text)
        + f"\n  News rate shift: {news_shift_pp:+.2f} pp/wk\n"
        + (f"\nNew this week ({len(fresh_news)}):\n" + "\n".join(news_text_lines) + "\n" if fresh_news else "")
        + f"\nDashboard: {DASHBOARD_URL}\n"
        f"Source: {REPO_URL}\n"
        + (f"Unsubscribe: {unsubscribe_url}\n" if unsubscribe_url else "")
    )
    return subject, html, text


def main() -> int:
    api_key = os.environ.get("RESEND_API_KEY")
    to_addr = os.environ.get("NOTIFY_EMAIL")
    from_addr = os.environ.get("NOTIFY_FROM", DEFAULT_FROM)

    if not api_key or not to_addr:
        missing = [k for k, v in (("RESEND_API_KEY", api_key), ("NOTIFY_EMAIL", to_addr)) if not v]
        print(f"Notify skipped — missing: {', '.join(missing)}")
        return 0

    if not FORECAST_JSON.exists():
        print("Notify skipped — forecast.json missing", file=sys.stderr)
        return 0

    forecast = json.loads(FORECAST_JSON.read_text(encoding="utf-8"))
    history = load_history()
    news = json.loads(NEWS_JSON.read_text(encoding="utf-8")) if NEWS_JSON.exists() else []

    latest, prior = week_over_week(history)
    if latest is None:
        print("Notify skipped — no latest count", file=sys.stderr)
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
        "fresh_news": new_news_this_week(news),
    }
    subject, html, text = render(datetime.now(timezone.utc).strftime("%Y-%m-%d"), ctx)

    r = requests.post(
        RESEND_URL,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "from": from_addr,
            "to": [to_addr],
            "subject": subject,
            "html": html,
            "text": text,
        },
        timeout=30,
    )
    if r.status_code >= 400:
        print(f"Resend error {r.status_code}: {r.text}", file=sys.stderr)
        return 1
    print(f"Email sent to {to_addr}: {r.json().get('id', '?')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
