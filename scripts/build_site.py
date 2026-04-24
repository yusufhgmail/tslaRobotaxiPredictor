"""
Render the static HTML chart page from `data/forecast.json`.

The page is a single self-contained HTML file that loads Plotly from a CDN
and inlines the forecast payload. It has:
- actual historical line
- dotted 1800 re-rating threshold
- fuzzy forecast cloud (p5–p95 band, p25–p75 band, p50 median)
- subsampled trajectory lines (faint)
- news markers plotted beneath the chart by date, coloured by impact

Visual identity: Tesla-inspired (black, white, Tesla red #e31937, wide-
letterspaced Inter wordmark). Independent tracker — not affiliated with
Tesla, Inc.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORECAST_JSON = ROOT / "data" / "forecast.json"
OUT_HTML = ROOT / "docs" / "index.html"

TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<title>Tesla Robotaxi Scaling Predictor</title>
<meta name="description" content="Weekly-updated forecast of Tesla's unsupervised robotaxi fleet vs. the 1,800-vehicle re-rating threshold." />
<link rel="icon" type="image/svg+xml" href="favicon.svg" />
<link rel="preconnect" href="https://fonts.googleapis.com" />
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin />
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap" rel="stylesheet" />
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/dist/umd/supabase.min.js"></script>
<style>
  :root {
    color-scheme: dark;
    --bg: #000000;
    --panel: #0f0f0f;
    --panel-2: #171717;
    --border: #262626;
    --text: #ffffff;
    --muted: #a8a8a8;
    --accent: #e31937;
    --accent-2: #ff2d45;
    --pos: #4ade80;
    --neg: #f87171;
    --zero: #737373;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0;
    background: var(--bg); color: var(--text);
    font-family: "Inter", "Helvetica Neue", Helvetica, Arial, -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
    font-size: 15px; line-height: 1.55;
    font-weight: 400;
    -webkit-font-smoothing: antialiased;
    -moz-osx-font-smoothing: grayscale;
  }
  a { color: var(--accent); }
  .wrap { max-width: 1200px; margin: 0 auto; padding: 20px 24px 80px; }

  /* ---------- top bar ---------- */
  .topbar {
    display: flex; align-items: center; gap: 20px;
    padding: 14px 0 22px;
    border-bottom: 1px solid var(--border);
    margin-bottom: 26px;
  }
  .brand {
    display: inline-flex; align-items: center; gap: 12px;
    text-decoration: none; color: var(--text);
  }
  .brand .wordmark {
    font-weight: 700; font-size: 22px;
    letter-spacing: 0.55em; padding-left: 0.55em;
    text-transform: uppercase;
    color: var(--accent);
    font-family: "Inter", "Helvetica Neue", Helvetica, Arial, sans-serif;
  }
  .brand .tag {
    font-size: 10px; letter-spacing: 0.25em; text-transform: uppercase;
    color: var(--muted); font-weight: 500;
    border-left: 1px solid var(--border); padding-left: 12px; margin-left: 4px;
  }
  .topbar .spacer { flex: 1; }
  .topbar nav { display: flex; gap: 22px; align-items: center; }
  .topbar nav a {
    color: var(--text); text-decoration: none;
    font-size: 11px; font-weight: 600; letter-spacing: 0.18em; text-transform: uppercase;
    transition: color 120ms ease;
  }
  .topbar nav a:hover { color: var(--accent); }

  h1 {
    font-size: 34px; font-weight: 500;
    margin: 0 0 6px; letter-spacing: -0.015em; line-height: 1.15;
  }
  h1 .mark {
    color: var(--accent); font-weight: 700;
    letter-spacing: 0.05em;
  }
  .sub { color: var(--muted); font-size: 13px; margin-bottom: 22px; }
  .sub a { color: var(--text); text-decoration: none; border-bottom: 1px solid var(--accent); padding-bottom: 1px; }
  .sub a:hover { color: var(--accent); }

  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 1px; margin: 20px 0 26px; background: var(--border); border: 1px solid var(--border); border-radius: 2px; overflow: hidden; }
  .card { background: var(--panel); padding: 16px 18px; border-radius: 0; }
  .card .lbl { color: var(--muted); font-size: 10px; text-transform: uppercase; letter-spacing: 0.18em; font-weight: 600; }
  .card .val { font-size: 26px; font-weight: 500; margin-top: 6px; font-variant-numeric: tabular-nums; letter-spacing: -0.01em; }
  .card .foot { color: var(--muted); font-size: 11px; margin-top: 4px; }

  #chart, #newschart { background: var(--panel); border: 1px solid var(--border); border-radius: 2px; }
  #chart { height: 540px; margin-bottom: 6px; }
  #newschart { height: 180px; }

  .scale-toggle { display: flex; align-items: center; gap: 10px; margin: 8px 0 12px; font-size: 10px; color: var(--muted); text-transform: uppercase; letter-spacing: 0.15em; font-weight: 600; }
  .scale-toggle button {
    background: transparent; color: var(--muted); border: 1px solid var(--border);
    padding: 6px 14px; border-radius: 2px; cursor: pointer;
    font-size: 10px; font-weight: 600; letter-spacing: 0.15em; text-transform: uppercase;
    font-family: inherit;
    transition: all 120ms ease;
  }
  .scale-toggle button:hover { color: var(--text); border-color: var(--muted); }
  .scale-toggle button.on { background: var(--accent); color: #ffffff; border-color: var(--accent); }
  .scale-toggle .hint { margin-left: auto; font-size: 10px; opacity: 0.7; text-transform: none; letter-spacing: 0; font-weight: 400; }

  .thresholds { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 10px; margin: 14px 0 6px; }
  .threshold { background: var(--panel); border: 1px solid var(--border); border-radius: 2px; padding: 12px 14px; display: flex; align-items: flex-start; gap: 10px; }
  .threshold .dot { width: 10px; height: 10px; border-radius: 50%; margin-top: 6px; flex: 0 0 auto; }
  .threshold .body { flex: 1; }
  .threshold .head { font-size: 12px; font-weight: 600; letter-spacing: 0.02em; }
  .threshold .head a { color: var(--accent); text-decoration: none; }
  .threshold .head a:hover { text-decoration: underline; }
  .threshold .note { color: var(--muted); font-size: 12px; margin-top: 3px; }

  .news-list { margin-top: 22px; border: 1px solid var(--border); border-radius: 2px; background: var(--panel); }
  .news-item { display: grid; grid-template-columns: 88px 44px 1fr; gap: 12px; padding: 12px 16px; border-top: 1px solid var(--border); }
  .news-item:first-child { border-top: 0; }
  .news-date { color: var(--muted); font-size: 12px; font-variant-numeric: tabular-nums; }
  .news-score { font-weight: 700; font-variant-numeric: tabular-nums; text-align: center; border-radius: 2px; padding: 2px 0; font-size: 12px; }
  .news-body .title { font-weight: 500; }
  .news-body .title a { color: var(--text); text-decoration: none; border-bottom: 1px solid var(--border); }
  .news-body .title a:hover { border-bottom-color: var(--accent); color: var(--accent); }
  .news-body .reason { color: var(--muted); font-size: 13px; margin-top: 3px; }

  /* ---------- footer ---------- */
  .site-footer {
    margin-top: 56px; padding: 32px 0 8px;
    border-top: 1px solid var(--border);
    display: flex; flex-direction: column; align-items: center; gap: 14px;
  }
  .site-footer .wordmark {
    font-weight: 700; font-size: 16px; color: var(--accent);
    letter-spacing: 0.6em; padding-left: 0.6em;
    text-transform: uppercase;
  }
  .site-footer .disclaimer {
    color: var(--muted); font-size: 11px; text-align: center; max-width: 640px;
    letter-spacing: 0.02em;
  }
  .site-footer .disclaimer .nom {
    display: block; margin-top: 6px; font-size: 10px;
    text-transform: uppercase; letter-spacing: 0.15em;
  }

  /* Subscribe + community buttons + modal */
  .header-row { display: flex; align-items: flex-end; justify-content: space-between; gap: 16px; margin-bottom: 8px; flex-wrap: wrap; }
  .header-row .heading { flex: 1; min-width: 260px; }
  .header-actions { display: flex; gap: 10px; flex-wrap: wrap; }
  .btn-subscribe, .btn-community {
    border-radius: 2px; padding: 11px 22px; font-weight: 600; font-size: 11px;
    letter-spacing: 0.18em; text-transform: uppercase;
    cursor: pointer; white-space: nowrap; font-family: inherit;
    text-decoration: none; display: inline-flex; align-items: center; gap: 6px;
    transition: all 120ms ease;
  }
  .btn-subscribe { background: var(--accent); color: #ffffff; border: 1px solid var(--accent); }
  .btn-subscribe:hover { background: var(--accent-2); border-color: var(--accent-2); }
  .btn-community {
    background: transparent; color: var(--text);
    border: 1px solid var(--border);
  }
  .btn-community:hover { background: var(--text); color: #000000; border-color: var(--text); }
  .modal { position: fixed; inset: 0; background: rgba(0,0,0,0.85); display: none;
    align-items: center; justify-content: center; z-index: 1000; padding: 20px; backdrop-filter: blur(8px); }
  .modal.on { display: flex; }
  .modal .dialog { background: var(--panel); border: 1px solid var(--border); border-radius: 2px;
    max-width: 520px; width: 100%; padding: 28px; max-height: 90vh; overflow: auto; }
  .modal h2 { margin: 0 0 8px; font-size: 20px; font-weight: 500; letter-spacing: -0.01em; }
  .modal .dialog .close { float: right; background: transparent; border: 0; color: var(--muted);
    font-size: 24px; cursor: pointer; padding: 0; line-height: 1; }
  .modal .dialog .close:hover { color: var(--accent); }
  .modal p { color: var(--muted); font-size: 13px; margin: 6px 0 14px; }
  .modal .preview { background: #000000; border: 1px solid var(--border); border-radius: 2px;
    padding: 16px; margin: 14px 0 18px; font-size: 12px; }
  .modal .preview .email-from { color: var(--muted); font-size: 11px; margin-bottom: 8px; }
  .modal .preview .email-subj { color: var(--text); font-weight: 600; margin-bottom: 8px; font-size: 13px; }
  .modal .preview table { border-collapse: collapse; font-size: 12px; margin-top: 6px; }
  .modal .preview td { padding: 2px 10px 2px 0; }
  .modal .preview td.k { color: var(--muted); }
  .modal .preview .hero { font-size: 22px; font-weight: 500; margin-bottom: 4px; letter-spacing: -0.01em; }
  .modal .preview .hero small { font-size: 12px; font-weight: 400; color: var(--muted); }
  .modal form .row { display: flex; gap: 8px; align-items: stretch; }
  .modal input[type=email] { flex: 1; background: #000000; border: 1px solid var(--border);
    border-radius: 2px; color: var(--text); padding: 11px 12px; font-family: inherit; font-size: 14px; }
  .modal input[type=email]:focus { outline: none; border-color: var(--accent); }
  .modal .msg { margin-top: 12px; font-size: 12px; min-height: 16px; }
  .modal .msg.ok { color: var(--pos); }
  .modal .msg.err { color: var(--neg); }

  @media (max-width: 640px) {
    h1 { font-size: 26px; }
    .topbar { gap: 10px; }
    .topbar nav { gap: 14px; }
    .topbar nav a { font-size: 10px; }
    .brand .wordmark { font-size: 18px; }
    .brand .tag { display: none; }
  }
</style>
</head>
<body>
<div class="wrap">

  <div class="topbar">
    <a class="brand" href="./" aria-label="Tesla Robotaxi Predictor (independent tracker)">
      <span class="wordmark">TESLA</span>
      <span class="tag">Robotaxi Predictor</span>
    </a>
    <div class="spacer"></div>
    <nav>
      <a href="snapshots.html">Snapshots</a>
      <a href="community.html">Community</a>
    </nav>
  </div>

  <div class="header-row">
    <div class="heading">
      <h1>Robotaxi <span class="mark">Scaling</span> Predictor</h1>
      <div class="sub">
        Unsupervised fleet vs. 1,800 re-rating threshold. Data from
        <a href="https://robotaxitracker.com/?provider=tesla" target="_blank" rel="noopener">robotaxitracker.com</a>.
        Updated weekly · Last run: <span id="generated">—</span>
        · <a href="snapshots.html">Past snapshots</a>
      </div>
    </div>
    <div class="header-actions">
      <a class="btn-community" href="community.html">Community</a>
      <button class="btn-subscribe" id="openSubscribe">Subscribe</button>
    </div>
  </div>

  <div class="grid" id="stats"></div>
  <div class="scale-toggle">
    <span>Y-axis scale</span>
    <button id="scale-log" class="on">Log</button>
    <button id="scale-linear">Linear</button>
  </div>
  <div id="chart"></div>
  <div id="thresholds"></div>
  <div id="newschart"></div>

  <div class="news-list" id="newslist"></div>

  <footer class="site-footer">
    <span class="wordmark">TESLA</span>
    <div class="disclaimer">
      Forecast is a prior-informed Monte Carlo exponential. The cloud narrows as weekly datapoints accumulate. Not investment advice.
      <span class="nom">Independent tracker · Not affiliated with Tesla, Inc.</span>
    </div>
  </footer>
</div>

<div class="modal" id="subscribeModal">
  <div class="dialog">
    <button type="button" class="close" aria-label="Close" id="closeSubscribe">×</button>
    <h2>Weekly robotaxi update</h2>
    <p>Every <b>Monday at 08:00 CT / 13:00 UTC</b>, right after this dashboard refreshes, I'll email you a summary. One email a week. One-click unsubscribe in every email.</p>

    <div class="preview" id="emailPreview">
      <div class="email-from">From: Robotaxi Predictor &lt;onboarding@resend.dev&gt;</div>
      <div class="email-subj" id="previewSubject">Robotaxi update: — unsupervised</div>
      <div class="hero" id="previewHero">— <small>unsupervised</small></div>
      <div id="previewDelta" style="color:var(--muted);font-size:12px;margin-bottom:8px"></div>
      <table>
        <tr><td class="k">Weekly growth</td><td id="previewGrowth">—</td></tr>
        <tr><td class="k">P50 to 1,000</td><td id="previewEta1000">—</td></tr>
        <tr><td class="k">P50 to 1,800</td><td id="previewEta1800">—</td></tr>
        <tr><td class="k">News rate shift</td><td id="previewNews">—</td></tr>
      </table>
      <div style="color:var(--muted);font-size:11px;margin-top:10px">+ fresh news items tagged with impact score</div>
    </div>

    <form id="subscribeForm">
      <div class="row">
        <input type="email" id="subscribeEmail" placeholder="you@example.com" required autofocus />
        <button type="submit" class="btn-subscribe" id="subscribeBtn">Subscribe</button>
      </div>
      <div class="msg" id="subscribeMsg"></div>
    </form>
  </div>
</div>

<script id="forecast-data" type="application/json">__FORECAST_JSON__</script>
<script>
(() => {
  const data = JSON.parse(document.getElementById('forecast-data').textContent);
  const isMobile = window.matchMedia('(max-width: 640px)').matches;

  // ---------- stats cards ----------
  const fit = data.fit || {};
  const eta = data.eta_to_target || {};
  const fmt = n => n == null ? '—' : (Math.round(n * 100) / 100).toLocaleString();
  const latest = data.historical.length ? data.historical[data.historical.length - 1].value : null;
  const targets = data.targets || [{ value: data.target, label: 'Target', color: '#facc15' }];
  const etaBy = data.eta_by_target || {};
  const targetCards = targets.map(t => {
    const k = String(Math.round(t.value));
    const e = etaBy[k] || {};
    return {
      lbl: 'P50 ETA to ' + Math.round(t.value).toLocaleString(),
      val: e.p50 || '—',
      foot: t.label + (e.p25 ? ` · P25 ${e.p25}` : ''),
    };
  });
  const cards = [
    { lbl: 'Unsupervised fleet', val: fmt(latest), foot: data.historical.length ? data.historical[data.historical.length - 1].date : '' },
    { lbl: 'Fitted weekly growth', val: ((fit.rate_weekly || 0) * 100).toFixed(1) + '%', foot: fit.prior_dominated ? 'prior-dominated (' + fit.n_points + ' pts)' : 'n=' + fit.n_points },
    { lbl: 'Annualized', val: (fit.annualized_growth_pct || 0).toLocaleString(undefined, { maximumFractionDigits: 0 }) + '%', foot: fit.doubling_weeks ? `doubles ~${fit.doubling_weeks.toFixed(1)}w` : '' },
    ...targetCards,
    { lbl: 'News rate shift', val: ((fit.news_rate_shift || 0) * 100).toFixed(2) + ' pp/wk', foot: (data.news || []).length + ' scored items' },
  ];
  document.getElementById('stats').innerHTML = cards.map(c =>
    `<div class="card"><div class="lbl">${c.lbl}</div><div class="val">${c.val}</div><div class="foot">${c.foot || ''}</div></div>`
  ).join('');

  document.getElementById('generated').textContent = (data.generated_at || '').replace('T', ' ').slice(0, 16) + ' UTC';

  // ---------- main chart ----------
  const histX = data.historical.map(h => h.date);
  const histY = data.historical.map(h => h.value);
  const fx = data.forecast_dates;

  const bandOuter = {
    x: fx.concat(fx.slice().reverse()),
    y: data.p95.concat(data.p5.slice().reverse()),
    fill: 'toself',
    fillcolor: 'rgba(227, 25, 55, 0.08)',
    line: { width: 0 },
    name: 'P5–P95',
    hoverinfo: 'skip',
    type: 'scatter',
  };
  const bandInner = {
    x: fx.concat(fx.slice().reverse()),
    y: data.p75.concat(data.p25.slice().reverse()),
    fill: 'toself',
    fillcolor: 'rgba(227, 25, 55, 0.22)',
    line: { width: 0 },
    name: 'P25–P75',
    hoverinfo: 'skip',
    type: 'scatter',
  };
  const median = {
    x: fx, y: data.p50, mode: 'lines',
    line: { color: '#e31937', width: 2, dash: 'dash' },
    name: 'Median forecast',
    type: 'scatter',
  };
  const sampleTraces = (data.samples || []).slice(0, isMobile ? 30 : 80).map((s, i) => ({
    x: fx, y: s, mode: 'lines',
    line: { color: 'rgba(227, 25, 55, 0.06)', width: 1 },
    hoverinfo: 'skip',
    showlegend: false,
    type: 'scatter',
  }));
  const actual = {
    x: histX, y: histY, mode: 'lines+markers',
    line: { color: '#ffffff', width: 2.5 },
    marker: { color: '#ffffff', size: 7 },
    name: 'Actual',
    type: 'scatter',
  };

  const allX = histX.concat(fx);
  const xRange = [allX[0], allX[allX.length - 1]];
  const thresholdTraces = targets.map(t => ({
    x: xRange,
    y: [t.value, t.value],
    mode: 'lines',
    line: { color: t.color || '#facc15', width: 1.5, dash: 'dot' },
    name: `${t.label} (${Math.round(t.value).toLocaleString()})`,
    type: 'scatter',
  }));

  const chartTraces = [bandOuter, bandInner, ...sampleTraces, median, ...thresholdTraces, actual];
  const thresholdAnnotations = targets.map(t => ({
    x: xRange[1],
    y: t.value,
    xref: 'x',
    yref: 'y',
    xanchor: 'right',
    yanchor: 'bottom',
    text: Math.round(t.value).toLocaleString() + ' — ' + (t.label || ''),
    showarrow: false,
    font: { size: 11, color: t.color || '#facc15' },
    bgcolor: 'rgba(15, 15, 15, 0.88)',
    bordercolor: t.color || '#facc15',
    borderwidth: 1,
    borderpad: 4,
  }));

  const etaShapes = [];
  const etaAnnotations = [];
  const monthShort = d => {
    const [y, m, day] = d.split('-').map(Number);
    const names = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    return `${names[m - 1]} ${day}`;
  };
  const monthYear = d => {
    const [y, m] = d.split('-').map(Number);
    const names = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
    return `${names[m - 1]} '${String(y).slice(-2)}`;
  };
  targets.forEach(t => {
    const key = String(Math.round(t.value));
    const e = etaBy[key];
    if (!e || !e.p25 || !e.p75) return;
    const color = t.color || '#facc15';
    etaShapes.push({
      type: 'line', xref: 'x', yref: 'y',
      x0: e.p25, x1: e.p25, y0: 1, y1: t.value,
      line: { color, width: 1, dash: 'dot' },
      opacity: 0.7,
    });
    etaShapes.push({
      type: 'line', xref: 'x', yref: 'y',
      x0: e.p75, x1: e.p75, y0: 1, y1: t.value,
      line: { color, width: 1, dash: 'dot' },
      opacity: 0.7,
    });
    // P5-P95 thin outer range — reaches the edges of the fuzzy cloud.
    // Distinguished from the thick P25-P75 bracket by thin line + long-dash
    // verticals (vs. the P25/P75 short-dot verticals), at lower opacity.
    if (e.p5 && e.p95) {
      etaShapes.push({
        type: 'line', xref: 'x', yref: 'y',
        x0: e.p5, x1: e.p95, y0: t.value, y1: t.value,
        line: { color, width: 1 },
        opacity: 0.55,
      });
      etaShapes.push({
        type: 'line', xref: 'x', yref: 'y',
        x0: e.p5, x1: e.p5, y0: 1, y1: t.value,
        line: { color, width: 1, dash: 'longdash' },
        opacity: 0.4,
      });
      etaShapes.push({
        type: 'line', xref: 'x', yref: 'y',
        x0: e.p95, x1: e.p95, y0: 1, y1: t.value,
        line: { color, width: 1, dash: 'longdash' },
        opacity: 0.4,
      });
    }
    // P25-P75 thick inner bracket — most-likely window.
    etaShapes.push({
      type: 'line', xref: 'x', yref: 'y',
      x0: e.p25, x1: e.p75, y0: t.value, y1: t.value,
      line: { color, width: 3 },
    });
    const labelText = (e.p25.slice(0, 7) === e.p75.slice(0, 7))
      ? monthYear(e.p25)
      : `${monthShort(e.p25)} – ${monthShort(e.p75)}`;
    const midIndex = Math.floor((new Date(e.p25).getTime() + new Date(e.p75).getTime()) / 2);
    etaAnnotations.push({
      x: new Date(midIndex).toISOString().slice(0, 10),
      y: t.value,
      xref: 'x', yref: 'y',
      xanchor: 'center', yanchor: 'bottom',
      text: labelText,
      showarrow: false,
      font: { size: 10, color },
      bgcolor: 'rgba(0, 0, 0, 0.9)',
      borderpad: 3,
    });
  });
  const chartLayoutBase = {
    annotations: thresholdAnnotations.concat(etaAnnotations),
    shapes: etaShapes,
    paper_bgcolor: '#0f0f0f',
    plot_bgcolor: '#0f0f0f',
    font: { color: '#ffffff', family: 'Inter, "Helvetica Neue", Helvetica, Arial, sans-serif' },
    margin: { t: 24, r: 20, b: 40, l: 70 },
    xaxis: { gridcolor: '#262626', zerolinecolor: '#262626', title: '' },
    yaxis: {
      gridcolor: '#262626', zerolinecolor: '#262626',
      title: 'Unsupervised robotaxis',
      type: 'log',
    },
    legend: { orientation: 'h', y: -0.14, font: { size: 11 } },
    hovermode: 'x unified',
  };
  Plotly.newPlot('chart', chartTraces, chartLayoutBase, { displaylogo: false, responsive: true, displayModeBar: false });

  const thresholdStartIdx = 2 + sampleTraces.length + 1;
  const thresholdIndices = thresholdTraces.map((_, i) => thresholdStartIdx + i);

  const btnLinear = document.getElementById('scale-linear');
  const btnLog = document.getElementById('scale-log');
  function setScale(mode) {
    btnLinear.classList.toggle('on', mode === 'linear');
    btnLog.classList.toggle('on', mode === 'log');
    const showThresholds = mode === 'log';
    if (thresholdIndices.length) {
      Plotly.restyle('chart', { visible: showThresholds ? true : 'legendonly' }, thresholdIndices);
    }
    Plotly.relayout('chart', {
      'yaxis.type': mode,
      'yaxis.rangemode': mode === 'linear' ? 'tozero' : 'normal',
      'yaxis.autorange': true,
      annotations: showThresholds ? thresholdAnnotations.concat(etaAnnotations) : [],
      shapes: showThresholds ? etaShapes : [],
    });
  }
  btnLinear.addEventListener('click', () => setScale('linear'));
  btnLog.addEventListener('click', () => setScale('log'));

  // ---------- threshold attribution block ----------
  const tEl = document.getElementById('thresholds');
  tEl.className = 'thresholds';
  tEl.innerHTML = targets.map(t => {
    const label = `${Math.round(t.value).toLocaleString()} — ${escapeHtml(t.label || '')}`;
    const attr = t.source_url
      ? `<a href="${t.source_url}" target="_blank" rel="noopener">${escapeHtml(t.source_handle || t.source_author || 'source')}</a>`
      : '';
    const note = t.note ? escapeHtml(t.note) : '';
    return `<div class="threshold">
      <div class="dot" style="background:${t.color || '#facc15'}"></div>
      <div class="body">
        <div class="head">${label}${attr ? ' · ' + attr : ''}</div>
        <div class="note">${note}</div>
      </div>
    </div>`;
  }).join('');

  // ---------- news timeline (below main chart) ----------
  const news = (data.news || []).slice().sort((a, b) => a.date.localeCompare(b.date));
  const colorFor = s => s > 0 ? '#4ade80' : s < 0 ? '#e31937' : '#737373';
  const truncate = (s, n) => (s || '').length > n ? (s || '').slice(0, n - 1) + '…' : (s || '');
  const newsTrace = {
    x: news.map(n => n.date),
    y: news.map(n => n.impact_score),
    mode: 'markers',
    marker: {
      color: news.map(n => colorFor(n.impact_score)),
      size: news.map(n => 10 + Math.abs(n.impact_score) * 4),
      line: { color: '#000000', width: 1 },
    },
    customdata: news.map(n => [truncate(n.title, 70), n.date]),
    hovertemplate: '<b>%{customdata[0]}</b><br>%{customdata[1]} · impact %{y:+d}<extra></extra>',
    hoverlabel: {
      bgcolor: '#0f0f0f',
      bordercolor: news.map(n => colorFor(n.impact_score)),
      font: { color: '#ffffff', family: 'Inter, "Helvetica Neue", Helvetica, Arial, sans-serif', size: 12 },
      align: 'left',
      namelength: -1,
    },
    type: 'scatter',
    name: 'News',
  };
  const zeroLine = {
    x: news.length ? [news[0].date, news[news.length - 1].date] : [],
    y: [0, 0], mode: 'lines',
    line: { color: '#262626', width: 1 },
    hoverinfo: 'skip', showlegend: false,
    type: 'scatter',
  };
  Plotly.newPlot('newschart', [zeroLine, newsTrace], {
    paper_bgcolor: '#0f0f0f',
    plot_bgcolor: '#0f0f0f',
    font: { color: '#ffffff', family: 'Inter, "Helvetica Neue", Helvetica, Arial, sans-serif' },
    margin: { t: 18, r: 20, b: 40, l: 60 },
    xaxis: {
      gridcolor: '#262626',
      range: [allX[0], allX[allX.length - 1]],
      title: '',
    },
    yaxis: {
      gridcolor: '#262626',
      title: 'News impact',
      range: [-3.5, 3.5],
      tickvals: [-3, -2, -1, 0, 1, 2, 3],
      zerolinecolor: '#262626',
    },
    showlegend: false,
    hovermode: 'closest',
  }, { displaylogo: false, responsive: true, displayModeBar: false });

  // ---------- news list ----------
  const nl = document.getElementById('newslist');
  nl.innerHTML = (data.news || []).map(n => {
    const c = colorFor(n.impact_score);
    const sign = n.impact_score > 0 ? '+' : '';
    return `<div class="news-item">
      <div class="news-date">${n.date}</div>
      <div class="news-score" style="background:${c}22;color:${c}">${sign}${n.impact_score}</div>
      <div class="news-body">
        <div class="title">${n.url ? `<a href="${n.url}" target="_blank" rel="noopener">${escapeHtml(n.title)}</a>` : escapeHtml(n.title)}</div>
        <div class="reason">${escapeHtml(n.impact_reason)}</div>
      </div>
    </div>`;
  }).join('') || '<div class="news-item" style="grid-template-columns:1fr;color:var(--muted);text-align:center">No news items yet. They appear after the first successful Perplexity run.</div>';

  function escapeHtml(s) {
    return String(s || '').replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  // ---------- Subscribe modal ----------
  const SUPABASE_URL = 'https://rpkxxdtgthzrucbnssjj.supabase.co';
  const SUPABASE_PUBLISHABLE_KEY = 'sb_publishable_IlSsw8rpBZlWN6riB6KCrA__PdklPUd';
  const sb = window.supabase.createClient(SUPABASE_URL, SUPABASE_PUBLISHABLE_KEY);

  const modal = document.getElementById('subscribeModal');
  const openBtn = document.getElementById('openSubscribe');
  const closeBtn = document.getElementById('closeSubscribe');
  const form = document.getElementById('subscribeForm');
  const emailInput = document.getElementById('subscribeEmail');
  const submitBtn = document.getElementById('subscribeBtn');
  const msg = document.getElementById('subscribeMsg');

  document.getElementById('previewSubject').textContent =
    `Robotaxi update: ${latest ?? '—'} unsupervised — ${(data.generated_at || '').slice(0, 10)}`;
  document.getElementById('previewHero').innerHTML =
    `${latest ?? '—'} <small>unsupervised</small>`;
  document.getElementById('previewDelta').textContent =
    latest != null ? 'Week-over-week delta shown for each run' : '';
  document.getElementById('previewGrowth').textContent =
    `${((fit.rate_weekly || 0) * 100).toFixed(1)}% (doubles ~${fit.doubling_weeks ? fit.doubling_weeks.toFixed(1) + 'w' : 'n/a'})`;
  document.getElementById('previewEta1000').textContent = (etaBy['1000']?.p50) || '—';
  document.getElementById('previewEta1800').textContent = (etaBy['1800']?.p50) || eta.p50 || '—';
  document.getElementById('previewNews').textContent =
    `${((fit.news_rate_shift || 0) * 100).toFixed(2)} pp/wk from ${(data.news || []).length} items`;

  function openModal() { modal.classList.add('on'); msg.textContent=''; msg.className='msg'; emailInput.focus(); }
  function closeModal() { modal.classList.remove('on'); }
  openBtn.addEventListener('click', openModal);
  closeBtn.addEventListener('click', closeModal);
  modal.addEventListener('click', e => { if (e.target === modal) closeModal(); });
  document.addEventListener('keydown', e => { if (e.key === 'Escape' && modal.classList.contains('on')) closeModal(); });

  form.addEventListener('submit', async e => {
    e.preventDefault();
    const email = emailInput.value.trim();
    submitBtn.disabled = true;
    msg.textContent = 'Subscribing…';
    msg.className = 'msg';
    const { error } = await sb.from('subscribers').insert({ email });
    submitBtn.disabled = false;
    if (error) {
      if (/duplicate|unique/i.test(error.message)) {
        msg.textContent = "You're already subscribed — thanks.";
        msg.className = 'msg ok';
      } else {
        msg.textContent = 'Error: ' + error.message;
        msg.className = 'msg err';
      }
      return;
    }
    msg.textContent = "Almost done — check your inbox and click the confirmation link.";
    msg.className = 'msg ok';
    emailInput.value = '';
  });
})();
</script>
</body>
</html>
"""


def main() -> int:
    if not FORECAST_JSON.exists():
        print(f"ERROR: {FORECAST_JSON} missing — run forecast.py first", file=sys.stderr)
        return 1
    forecast = json.loads(FORECAST_JSON.read_text(encoding="utf-8"))
    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(forecast).replace("</", "<\\/")
    html = TEMPLATE.replace("__FORECAST_JSON__", payload)
    OUT_HTML.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT_HTML} ({len(html):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
