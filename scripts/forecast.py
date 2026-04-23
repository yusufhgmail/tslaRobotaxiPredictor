"""
Exponential-growth forecast with a Monte Carlo "fuzzy cloud".

Model
-----
We assume the robotaxi count y(t) follows y = a * exp(r * t), where t is weeks
since the first observation. We fit (a, r) from the historical snapshots and
propagate two forms of uncertainty forward:

1.  Statistical uncertainty from the fit (bootstrap over observed log-residuals).
2.  Scenario uncertainty from recent news (shifts the growth-rate prior; each
    scored news item tilts r up or down by a small, bounded amount).

When fewer than 3 datapoints exist we fall back to a wide, weakly-informative
prior on the growth rate so the cloud reflects our ignorance.

Outputs a dict with:
- historical: list of {date, value}
- forecast_dates: list of ISO dates
- p5, p25, p50, p75, p95: list[float] — percentile bands over the cloud
- samples: list[list[float]] — a subsample of full trajectories for plotting
- eta_to_target: dict of percentile -> ISO date when that band hits 1800
- fit: {rate_weekly, doubling_weeks, annualized_growth, n_points}
"""
from __future__ import annotations

import csv
import json
import math
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
HISTORY_CSV = ROOT / "data" / "history.csv"
NEWS_JSON = ROOT / "data" / "news.json"
FORECAST_JSON = ROOT / "data" / "forecast.json"

TARGETS = [
    {"value": 1000.0, "label": "Min 2026 expectation", "color": "#fb923c"},
    {"value": 1800.0, "label": "Re-rating threshold",  "color": "#facc15"},
]
# Back-compat: the scalar `TARGET` is the top threshold used in CLI summary.
TARGET = TARGETS[-1]["value"]
METRIC = "unsupervised_cumulative"
FORECAST_WEEKS = 88  # horizon ends ~Dec 2027
N_SAMPLES = 2000

# Weakly informative prior on weekly growth rate r (= Δln(fleet)/week).
# Unsupervised fleet launched 2026-01-22 and is scaling from a small base,
# so center on ~15% weekly (doubles every ~4.6 weeks) with wide spread until
# real data dominates the fit.
PRIOR_R_MEAN = 0.15
PRIOR_R_SD = 0.10


def load_history() -> list[dict]:
    """Load daily history from CSV.

    History rows live at day-granularity; we key off the `date` column and
    fabricate a timestamp at UTC midnight so downstream math stays uniform.
    Days before the first non-zero value are dropped so the log-scale fit
    doesn't crash.
    """
    if not HISTORY_CSV.exists():
        return []
    with HISTORY_CSV.open("r", newline="", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    out = []
    started = False
    for r in rows:
        val = r.get(METRIC)
        if val in (None, "", "None"):
            continue
        try:
            v = float(val)
        except ValueError:
            continue
        if not started:
            if v <= 0:
                continue
            started = True
        d = r.get("date") or r.get("timestamp_utc", "")[:10]
        if not d:
            continue
        out.append({
            "date": d,
            "timestamp": f"{d}T00:00:00+00:00",
            "value": v,
        })
    out.sort(key=lambda d: d["timestamp"])
    return out


def load_news_rate_shift() -> tuple[float, list[dict]]:
    """Aggregate news impact scores into a growth-rate adjustment.

    Each item has `impact_score` in [-3, 3] and `weight` (recency-decayed).
    We map a weighted score of +3 to +3pp weekly growth, -3 to -3pp.
    """
    if not NEWS_JSON.exists():
        return 0.0, []
    try:
        items = json.loads(NEWS_JSON.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return 0.0, []
    if not items:
        return 0.0, items

    now = datetime.now(timezone.utc)
    total_w = 0.0
    weighted = 0.0
    for it in items:
        try:
            dt = datetime.fromisoformat(it["date"].replace("Z", "+00:00"))
        except (KeyError, ValueError):
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age_days = max(0.0, (now - dt).total_seconds() / 86400)
        # Half-life of 30 days — older news matters less.
        w = 0.5 ** (age_days / 30.0)
        score = float(it.get("impact_score", 0))
        weighted += w * score
        total_w += w
    if total_w == 0:
        return 0.0, items
    avg = weighted / total_w
    # Clamp average to [-3, 3] then scale to ±0.03 weekly rate shift.
    avg = max(-3.0, min(3.0, avg))
    shift = 0.01 * avg  # ±3pp at saturation
    return shift, items


def fit(history: list[dict]) -> tuple[float, float, float, int]:
    """Return (intercept_log, rate_weekly, residual_sd, n)."""
    n = len(history)
    if n == 0:
        return math.log(1.0), PRIOR_R_MEAN, PRIOR_R_SD, 0
    t0 = datetime.fromisoformat(history[0]["timestamp"].replace("Z", "+00:00"))
    ts = np.array([
        (datetime.fromisoformat(h["timestamp"].replace("Z", "+00:00")) - t0)
        .total_seconds() / (86400 * 7)
        for h in history
    ])
    ys = np.array([max(1.0, h["value"]) for h in history])  # avoid log(0)
    log_ys = np.log(ys)

    if n == 1:
        return float(log_ys[0]), PRIOR_R_MEAN, PRIOR_R_SD, 1

    # OLS on log-scale: log(y) = a + r*t
    slope, intercept = np.polyfit(ts, log_ys, 1)
    resid = log_ys - (intercept + slope * ts)
    # Unbiased residual sd, with a small floor so early fits don't look certain.
    resid_sd = float(max(0.05, np.std(resid, ddof=1) if n > 2 else 0.15))
    return float(intercept), float(slope), resid_sd, n


def simulate(
    history: list[dict],
    weeks_ahead: int = FORECAST_WEEKS,
    n_samples: int = N_SAMPLES,
) -> dict:
    news_shift, news_items = load_news_rate_shift()

    intercept, rate, resid_sd, n = fit(history)

    if n < 3:
        # Prior-dominated: blend fit with wide prior on r.
        r_mean = 0.6 * rate + 0.4 * PRIOR_R_MEAN + news_shift
        r_sd = PRIOR_R_SD
    else:
        # Data-dominated but still news-adjusted.
        r_mean = rate + news_shift
        # Rate SD from OLS (approximate): resid_sd / sqrt(sum(t - t_mean)^2)
        t0 = datetime.fromisoformat(history[0]["timestamp"].replace("Z", "+00:00"))
        ts = np.array([
            (datetime.fromisoformat(h["timestamp"].replace("Z", "+00:00")) - t0)
            .total_seconds() / (86400 * 7)
            for h in history
        ])
        denom = math.sqrt(max(1e-6, float(np.sum((ts - ts.mean()) ** 2))))
        r_sd = max(0.015, resid_sd / denom)

    rng = np.random.default_rng(seed=42)
    r_samples = rng.normal(r_mean, r_sd, size=n_samples)
    # Sanity-clip: nothing faster than ~50%/week sustained, nothing below decay.
    r_samples = np.clip(r_samples, -0.10, 0.50)

    # Anchor to most recent observed value if we have data.
    if history:
        anchor_value = history[-1]["value"]
        anchor_dt = datetime.fromisoformat(
            history[-1]["timestamp"].replace("Z", "+00:00")
        ).date()
    else:
        anchor_value = 1.0
        anchor_dt = datetime.now(timezone.utc).date()

    weeks = np.arange(0, weeks_ahead + 1)
    # Shape: (n_samples, weeks+1)
    deterministic = anchor_value * np.exp(np.outer(r_samples, weeks))
    # Add multiplicative noise per step so the cloud widens forward.
    noise_sd = resid_sd
    noise = rng.normal(0.0, noise_sd, size=deterministic.shape)
    trajectories = deterministic * np.exp(noise)

    p5, p25, p50, p75, p95 = np.percentile(trajectories, [5, 25, 50, 75, 95], axis=0)

    forecast_dates = [
        (anchor_dt + timedelta(weeks=int(w))).isoformat() for w in weeks
    ]

    # When does each percentile cross each target?
    def first_crossing(arr: np.ndarray, thresh: float) -> str | None:
        over = np.where(arr >= thresh)[0]
        return forecast_dates[int(over[0])] if len(over) else None

    eta_by_target = {}
    for t in TARGETS:
        k = str(int(t["value"]))
        eta_by_target[k] = {
            "value": t["value"],
            "label": t["label"],
            "p5": first_crossing(p5, t["value"]),
            "p25": first_crossing(p25, t["value"]),
            "p50": first_crossing(p50, t["value"]),
            "p75": first_crossing(p75, t["value"]),
            "p95": first_crossing(p95, t["value"]),
        }
    # Top-level `eta_to_target` retained for the final (re-rating) threshold.
    top = eta_by_target[str(int(TARGETS[-1]["value"]))]
    eta = {k: top[k] for k in ("p5", "p25", "p50", "p75", "p95")}

    # Keep a subsample of full trajectories for the cloud visualisation.
    sample_idx = rng.choice(n_samples, size=min(120, n_samples), replace=False)
    samples = trajectories[sample_idx].tolist()

    annualized = (math.exp(r_mean * 52) - 1) * 100
    doubling = math.log(2) / r_mean if r_mean > 0 else float("inf")

    return {
        "metric": METRIC,
        "target": TARGET,                 # legacy scalar (top threshold)
        "targets": TARGETS,               # full list for the chart
        "eta_by_target": eta_by_target,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "historical": [{"date": h["date"], "value": h["value"]} for h in history],
        "forecast_dates": forecast_dates,
        "p5": p5.tolist(),
        "p25": p25.tolist(),
        "p50": p50.tolist(),
        "p75": p75.tolist(),
        "p95": p95.tolist(),
        "samples": samples,
        "eta_to_target": eta,
        "fit": {
            "n_points": n,
            "rate_weekly": r_mean,
            "rate_weekly_sd": r_sd,
            "news_rate_shift": news_shift,
            "doubling_weeks": doubling if math.isfinite(doubling) else None,
            "annualized_growth_pct": annualized,
            "prior_dominated": n < 3,
        },
        "news": news_items,
    }


def main() -> int:
    history = load_history()
    result = simulate(history)
    FORECAST_JSON.parent.mkdir(parents=True, exist_ok=True)
    FORECAST_JSON.write_text(json.dumps(result), encoding="utf-8")
    print(f"Wrote {FORECAST_JSON}")
    f = result["fit"]
    print(f"  n={f['n_points']}  r={f['rate_weekly']:.4f}/wk  annualized={f['annualized_growth_pct']:.0f}%  prior_dominated={f['prior_dominated']}")
    print(f"  p50 ETA to {int(TARGET)}: {result['eta_to_target']['p50']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
