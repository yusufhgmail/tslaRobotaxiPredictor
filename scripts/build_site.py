"""
Render the static HTML chart page from `data/forecast.json`.

The page is a single self-contained HTML file that loads Plotly from a CDN
and inlines the forecast payload. It has:
- actual historical line
- dotted 1800 re-rating threshold
- fuzzy forecast cloud (p5–p95 band, p25–p75 band, p50 median)
- subsampled trajectory lines (faint)
- news markers plotted beneath the chart by date, coloured by impact
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
<script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
<script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2/dist/umd/supabase.min.js"></script>
<style>
  :root {
    color-scheme: dark;
    --bg: #0b0d10;
    --panel: #12161b;
    --border: #1f262e;
    --text: #e6e9ee;
    --muted: #8a94a3;
    --accent: #4ea3ff;
    --pos: #34d399;
    --neg: #f87171;
    --zero: #9ca3af;
  }
  * { box-sizing: border-box; }
  html, body {
    margin: 0; padding: 0;
    background: var(--bg); color: var(--text);
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, system-ui, sans-serif;
    font-size: 15px; line-height: 1.5;
  }
  .wrap { max-width: 1180px; margin: 0 auto; padding: 24px 20px 64px; }
  h1 { font-size: 22px; font-weight: 600; margin: 0 0 4px; letter-spacing: -0.01em; }
  .sub { color: var(--muted); font-size: 13px; margin-bottom: 18px; }
  .sub a { color: var(--accent); text-decoration: none; }
  .sub a:hover { text-decoration: underline; }
  .grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin: 18px 0 22px; }
  .card { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; padding: 12px 14px; }
  .card .lbl { color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: 0.05em; }
  .card .val { font-size: 22px; font-weight: 600; margin-top: 4px; font-variant-numeric: tabular-nums; }
  .card .foot { color: var(--muted); font-size: 12px; margin-top: 2px; }
  #chart, #newschart { background: var(--panel); border: 1px solid var(--border); border-radius: 10px; }
  #chart { height: 520px; margin-bottom: 6px; }
  #newschart { height: 180px; }
  .scale-toggle { display: flex; align-items: center; gap: 8px; margin: 4px 0 10px; font-size: 12px; color: var(--muted); }
  .scale-toggle button {
    background: var(--panel); color: var(--muted); border: 1px solid var(--border);
    padding: 4px 10px; border-radius: 6px; cursor: pointer; font-size: 12px;
  }
  .scale-toggle button.on { background: var(--accent); color: #0b0d10; border-color: var(--accent); }
  .scale-toggle .hint { margin-left: auto; font-size: 11px; opacity: 0.7; }
  .thresholds { display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 10px; margin: 10px 0 6px; }
  .threshold { background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 10px 12px; display: flex; align-items: flex-start; gap: 10px; }
  .threshold .dot { width: 10px; height: 10px; border-radius: 50%; margin-top: 5px; flex: 0 0 auto; }
  .threshold .body { flex: 1; }
  .threshold .head { font-size: 12px; font-weight: 600; }
  .threshold .head a { color: var(--accent); text-decoration: none; }
  .threshold .head a:hover { text-decoration: underline; }
  .threshold .note { color: var(--muted); font-size: 12px; margin-top: 2px; }
  .news-list { margin-top: 18px; }
  .news-item { display: grid; grid-template-columns: 88px 44px 1fr; gap: 10px; padding: 10px 12px; border-top: 1px solid var(--border); }
  .news-item:first-child { border-top: 0; }
  .news-date { color: var(--muted); font-size: 12px; font-variant-numeric: tabular-nums; }
  .news-score { font-weight: 600; font-variant-numeric: tabular-nums; text-align: center; border-radius: 4px; padding: 2px 0; }
  .news-body .title { font-weight: 500; }
  .news-body .title a { color: var(--text); text-decoration: none; border-bottom: 1px dotted var(--muted); }
  .news-body .title a:hover { border-bottom-color: var(--text); }
  .news-body .reason { color: var(--muted); font-size: 13px; margin-top: 2px; }
  .footer { color: var(--muted); font-size: 12px; margin-top: 28px; text-align: center; }

  /* Subscribe button + modal */
  .header-row { display: flex; align-items: flex-start; gap: 12px; margin-bottom: 4px; }
  .header-row h1 { flex: 1; }
  .btn-subscribe {
    background: var(--accent); color: #0b0d10; border: 0; border-radius: 6px;
    padding: 7px 14px; font-weight: 600; font-size: 13px; cursor: pointer;
    white-space: nowrap;
  }
  .btn-subscribe:hover { filter: brightness(1.08); }
  .modal { position: fixed; inset: 0; background: rgba(0,0,0,0.7); display: none;
    align-items: center; justify-content: center; z-index: 1000; padding: 20px; }
  .modal.on { display: flex; }
  .modal .dialog { background: var(--panel); border: 1px solid var(--border); border-radius: 12px;
    max-width: 520px; width: 100%; padding: 22px; max-height: 90vh; overflow: auto; }
  .modal h2 { margin: 0 0 6px; font-size: 18px; font-weight: 600; }
  .modal .dialog .close { float: right; background: transparent; border: 0; color: var(--muted);
    font-size: 22px; cursor: pointer; padding: 0; line-height: 1; }
  .modal .dialog .close:hover { color: var(--text); }
  .modal p { color: var(--muted); font-size: 13px; margin: 4px 0 12px; }
  .modal .preview { background: #0b0d10; border: 1px solid var(--border); border-radius: 8px;
    padding: 14px; margin: 12px 0 16px; font-size: 12px; }
  .modal .preview .email-from { color: var(--muted); font-size: 11px; margin-bottom: 8px; }
  .modal .preview .email-subj { color: var(--text); font-weight: 600; margin-bottom: 8px; font-size: 13px; }
  .modal .preview table { border-collapse: collapse; font-size: 12px; margin-top: 6px; }
  .modal .preview td { padding: 2px 10px 2px 0; }
  .modal .preview td.k { color: var(--muted); }
  .modal .preview .hero { font-size: 20px; font-weight: 600; margin-bottom: 2px; }
  .modal .preview .hero small { font-size: 12px; font-weight: 400; color: var(--muted); }
  .modal form .row { display: flex; gap: 8px; align-items: stretch; }
  .modal input[type=email] { flex: 1; background: #0b0d10; border: 1px solid var(--border);
    border-radius: 6px; color: var(--text); padding: 8px 10px; font-family: inherit; font-size: 14px; }
  .modal input[type=email]:focus { outline: none; border-color: var(--accent); }
  .modal .msg { margin-top: 10px; font-size: 12px; min-height: 16px; }
  .modal .msg.ok { color: var(--pos); }
  .modal .msg.err { color: var(--neg); }
</style>
</head>
<body>
<div class="wrap">
  <div class="header-row">
    <h1>Tesla Robotaxi Scaling Predictor</h1>
    <button class="btn-subscribe" id="openSubscribe">📬 Subscribe to weekly updates</button>
  </div>
  <div class="sub">
    Unsupervised fleet vs. 1,800 re-rating threshold. Data from
    <a href="https://robotaxitracker.com/?provider=tesla&area=austin" target="_blank" rel="noopener">robotaxitracker.com</a>.
    Updated weekly · Last run: <span id="generated">—</span>
    · <a href="community.html">💬 Community / feature requests</a>
    · <a href="snapshots.html">📅 Past snapshots</a>
  </div>

  <div class="grid" id="stats"></div>
  <div class="scale-toggle">
    <span>Y-axis scale:</span>
    <button id="scale-log" class="on">Log</button>
    <button id="scale-linear">Linear</button>
  </div>
  <div id="chart"></div>
  <div id="thresholds"></div>
  <div id="newschart"></div>

  <div class="news-list" id="newslist"></div>

  <div class="footer">
    Forecast is a prior-informed Monte Carlo exponential. The cloud narrows as weekly datapoints accumulate. Not investment advice.
  </div>
</div>

<div class="modal" id="subscribeModal">
  <div class="dialog">
    <button type="button" class="close" aria-label="Close" id="closeSubscribe">×</button>
    <h2>📬 Weekly robotaxi update</h2>
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
    fillcolor: 'rgba(78, 163, 255, 0.10)',
    line: { width: 0 },
    name: 'P5–P95',
    hoverinfo: 'skip',
    type: 'scatter',
  };
  const bandInner = {
    x: fx.concat(fx.slice().reverse()),
    y: data.p75.concat(data.p25.slice().reverse()),
    fill: 'toself',
    fillcolor: 'rgba(78, 163, 255, 0.22)',
    line: { width: 0 },
    name: 'P25–P75',
    hoverinfo: 'skip',
    type: 'scatter',
  };
  const median = {
    x: fx, y: data.p50, mode: 'lines',
    line: { color: '#4ea3ff', width: 2, dash: 'dash' },
    name: 'Median forecast',
    type: 'scatter',
  };
  const sampleTraces = (data.samples || []).slice(0, isMobile ? 30 : 80).map((s, i) => ({
    x: fx, y: s, mode: 'lines',
    line: { color: 'rgba(78, 163, 255, 0.06)', width: 1 },
    hoverinfo: 'skip',
    showlegend: false,
    type: 'scatter',
  }));
  const actual = {
    x: histX, y: histY, mode: 'lines+markers',
    line: { color: '#e6e9ee', width: 2.5 },
    marker: { color: '#e6e9ee', size: 7 },
    name: 'Actual',
    type: 'scatter',
  };

  // Threshold lines across the full x-range.
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
    bgcolor: 'rgba(18, 22, 27, 0.85)',
    bordercolor: t.color || '#facc15',
    borderwidth: 1,
    borderpad: 4,
  }));
  const chartLayoutBase = {
    annotations: thresholdAnnotations,
    paper_bgcolor: '#12161b',
    plot_bgcolor: '#12161b',
    font: { color: '#e6e9ee', family: 'Inter, system-ui, sans-serif' },
    margin: { t: 24, r: 20, b: 40, l: 70 },
    xaxis: { gridcolor: '#1f262e', zerolinecolor: '#1f262e', title: '' },
    yaxis: {
      gridcolor: '#1f262e', zerolinecolor: '#1f262e',
      title: 'Unsupervised robotaxis',
      type: 'log',
    },
    legend: { orientation: 'h', y: -0.14, font: { size: 11 } },
    hovermode: 'x unified',
  };
  Plotly.newPlot('chart', chartTraces, chartLayoutBase, { displaylogo: false, responsive: true, displayModeBar: false });

  // Compute indices of threshold traces inside chartTraces so we can toggle them
  // together with their right-edge annotations when the y-axis scale changes.
  const thresholdStartIdx = 2 + sampleTraces.length + 1; // bandOuter, bandInner, samples, median
  const thresholdIndices = thresholdTraces.map((_, i) => thresholdStartIdx + i);

  const btnLinear = document.getElementById('scale-linear');
  const btnLog = document.getElementById('scale-log');
  function setScale(mode) {
    btnLinear.classList.toggle('on', mode === 'linear');
    btnLog.classList.toggle('on', mode === 'log');
    // On linear scale the y-axis stretches to the Monte-Carlo upper tail, which
    // flattens the 1,000/1,800 threshold lines onto the x-axis and makes them
    // useless — hide them (and their labels) when linear is active.
    const showThresholds = mode === 'log';
    if (thresholdIndices.length) {
      Plotly.restyle('chart', { visible: showThresholds ? true : 'legendonly' }, thresholdIndices);
    }
    Plotly.relayout('chart', {
      'yaxis.type': mode,
      'yaxis.rangemode': mode === 'linear' ? 'tozero' : 'normal',
      'yaxis.autorange': true,
      annotations: showThresholds ? thresholdAnnotations : [],
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
  const colorFor = s => s > 0 ? '#34d399' : s < 0 ? '#f87171' : '#9ca3af';
  const newsTrace = {
    x: news.map(n => n.date),
    y: news.map(n => n.impact_score),
    mode: 'markers',
    marker: {
      color: news.map(n => colorFor(n.impact_score)),
      size: news.map(n => 10 + Math.abs(n.impact_score) * 4),
      line: { color: '#0b0d10', width: 1 },
    },
    text: news.map(n => `<b>${n.title}</b><br>${n.impact_reason}`),
    hovertemplate: '%{text}<extra>%{x} · impact %{y:+d}</extra>',
    type: 'scatter',
    name: 'News',
  };
  const zeroLine = {
    x: news.length ? [news[0].date, news[news.length - 1].date] : [],
    y: [0, 0], mode: 'lines',
    line: { color: '#1f262e', width: 1 },
    hoverinfo: 'skip', showlegend: false,
    type: 'scatter',
  };
  Plotly.newPlot('newschart', [zeroLine, newsTrace], {
    paper_bgcolor: '#12161b',
    plot_bgcolor: '#12161b',
    font: { color: '#e6e9ee' },
    margin: { t: 18, r: 20, b: 40, l: 60 },
    xaxis: {
      gridcolor: '#1f262e',
      range: [allX[0], allX[allX.length - 1]],
      title: '',
    },
    yaxis: {
      gridcolor: '#1f262e',
      title: 'News impact',
      range: [-3.5, 3.5],
      tickvals: [-3, -2, -1, 0, 1, 2, 3],
      zerolinecolor: '#1f262e',
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
  }).join('') || '<div class="footer">No news items yet. They appear after the first successful Perplexity run.</div>';

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

  // Populate the email preview with live values from the current forecast.
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
    # Embed as JSON (escape `</script>` defensively).
    payload = json.dumps(forecast).replace("</", "<\\/")
    html = TEMPLATE.replace("__FORECAST_JSON__", payload)
    OUT_HTML.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT_HTML} ({len(html):,} bytes)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
