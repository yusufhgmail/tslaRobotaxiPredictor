# Tesla Robotaxi Scaling Predictor

A weekly-updated dashboard that tracks the Tesla robotaxi fleet in Austin, projects future scaling as a probabilistic "fuzzy cloud", and overlays news with LLM-scored impact on the trajectory. The goal: predict when the fleet crosses the **1,800-vehicle re-rating threshold**.

**Live chart:** `https://<your-github-username>.github.io/<this-repo>/` (once GitHub Pages is enabled — see setup below).

## How it works

Every Monday at 13:00 UTC, a GitHub Actions workflow:

1. **`scripts/scrape.py`** — pulls the latest fleet metrics from [robotaxitracker.com](https://robotaxitracker.com/?provider=tesla&area=austin) by parsing the Next.js RSC stream embedded in the page. Extracts: total vehicles, active-30d, unsupervised count, cybercabs, deprecated, and unsupervised ride percentages. Appends one row to `data/history.csv`.
2. **`scripts/news.py`** — queries Perplexity (`sonar-pro`) with a JSON-schema response to find the most impactful news of the last 14 days about Tesla robotaxi scaling in Austin. Each item is scored `-3..+3` on its likely impact to scaling velocity. Results merged into `data/news.json`.
3. **`scripts/forecast.py`** — fits an exponential to the log of the historical series, runs a 2,000-sample Monte Carlo forward 52 weeks, and produces a fuzzy cloud (P5/P25/P50/P75/P95 bands). News sentiment shifts the growth-rate prior (news score of +3 ≈ +3 pp/week, weighted by a 30-day half-life). Writes `data/forecast.json`.
4. **`scripts/build_site.py`** — renders a single-file static page (`docs/index.html`) with Plotly.js showing the actual line, target, fuzzy cloud, subsampled trajectories, and a news timeline with impact markers underneath.
5. Commits data + site, then deploys `docs/` to GitHub Pages.

When history is sparse (fewer than 3 points) the forecast is prior-dominated: weekly growth centered on 10% with wide spread, so the cloud is large until real data takes over.

## Setup

1. **Create a GitHub repo** and push this project.
2. **Add your Perplexity API key** as a repo secret:
   - Repo → Settings → Secrets and variables → Actions → New repository secret
   - Name: `PERPLEXITY_API_KEY`
   - Value: your key from [perplexity.ai/settings/api](https://www.perplexity.ai/settings/api)
3. **Enable GitHub Pages**:
   - Repo → Settings → Pages → Source: **GitHub Actions**
4. **Trigger the first run**:
   - Actions tab → "Weekly robotaxi update" → "Run workflow"
   - Subsequent runs happen every Monday automatically.

## Running locally

```bash
pip install -r requirements.txt
python scripts/scrape.py            # appends today's snapshot
export PERPLEXITY_API_KEY=pplx-...  # optional; skips if unset
python scripts/news.py              # fetch + score news
python scripts/forecast.py          # run Monte Carlo
python scripts/build_site.py        # render docs/index.html
```

Then open `docs/index.html`.

## Tuning

Edit `scripts/forecast.py`:
- `METRIC` — which history column to forecast. Default is `total_with_test` (fleet incl. test vehicles). Change to `unsupervised`, `total_vehicles`, or `active_30d` as needed.
- `TARGET` — the re-rating threshold (default 1800).
- `PRIOR_R_MEAN` / `PRIOR_R_SD` — prior on weekly growth when data is sparse.
- `FORECAST_WEEKS` — horizon (default 52).
- `N_SAMPLES` — Monte Carlo sample count (default 2000).

Edit `scripts/news.py` `SYSTEM_PROMPT` to tighten or loosen what counts as "impactful news".

## Data files

- **`data/history.csv`** — one row per snapshot. Columns: `timestamp_utc, total_vehicles, total_with_test, active_30d, unsupervised, cybercabs, deprecated, unsupervised_percent_7d, unsupervised_percent_30d, unsupervised_percent_since_launch`. You can manually add historical rows if you have them — the forecast picks them up automatically.
- **`data/news.json`** — accumulating list of scored news items, deduped by URL.
- **`data/forecast.json`** — regenerated every run; consumed by `build_site.py`.

## Caveats

- Exponential growth assumption: real scaling will hit S-curve constraints (hardware supply, regulatory gating, mapping). The forecast will over-project once scaling stalls. Revisit the model when/if daily adds flatten.
- robotaxitracker.com is community-driven; under-reporting bias is possible. The `total_with_test` metric is the most complete proxy.
- Perplexity news search has failure modes (hallucinated URLs, stale dates); the `normalize_item` step filters obvious bad entries but manual review of `data/news.json` is wise before leaning on any one score.
- Not investment advice.
