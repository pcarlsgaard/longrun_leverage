const DATA = {
  frontier: 'data/leaps_frontier_monte_carlo.csv',
  sensitivity: 'data/leaps_frontier_sensitivities.csv',
  curriculum: 'data/site_guided_sensitivity.json'
};

const state = {
  frontier: [],
  sensitivity: [],
  curriculum: null,
  strategy: 'SPX_85_40',
  horizon: 20,
  view: 'frontier',
  xAxis: 'cagr_p50',
  yAxis: 'cagr_p5'
};

const metricMeta = {
  cagr_p50: { label: 'Median CAGR', pct: true },
  cagr_p5: { label: '5th-percentile CAGR', pct: true },
  max_drawdown_median: { label: 'Median max drawdown', pct: true },
  prob_drawdown_worse_than_60: { label: 'P(drawdown >60%)', pct: true },
  mean_delta_exposure: { label: 'Mean equity delta', suffix: '×' },
  premium_budget: { label: 'Premium budget', pct: true },
  prob_negative_cagr: { label: 'P(negative CAGR)', pct: true },
  terminal_wealth_p50: { label: 'Median terminal wealth', suffix: '×' }
};

function parseCSV(text) {
  const lines = text.trim().split(/\r?\n/);
  const headers = splitCSVLine(lines.shift());
  return lines.filter(Boolean).map(line => {
    const values = splitCSVLine(line);
    const row = {};
    headers.forEach((h, i) => row[h] = coerce(values[i] ?? ''));
    return row;
  });
}

function splitCSVLine(line) {
  const out = [];
  let cur = '';
  let quote = false;
  for (let i = 0; i < line.length; i++) {
    const ch = line[i];
    if (ch === '"') {
      if (quote && line[i + 1] === '"') { cur += '"'; i++; }
      else quote = !quote;
    } else if (ch === ',' && !quote) {
      out.push(cur); cur = '';
    } else cur += ch;
  }
  out.push(cur);
  return out;
}

function coerce(v) {
  if (v === '') return null;
  if (v === 'True') return true;
  if (v === 'False') return false;
  const n = Number(v);
  return Number.isFinite(n) && v.trim() !== '' ? n : v;
}

function fmt(value, key, digits = 1) {
  if (value == null || Number.isNaN(value)) return '—';
  const meta = metricMeta[key] || {};
  if (meta.pct) return `${(value * 100).toFixed(digits)}%`;
  if (meta.suffix) return `${Number(value).toFixed(digits)}${meta.suffix}`;
  return Number(value).toFixed(digits);
}

function pctPointDelta(a, b) {
  if (a == null || b == null) return '—';
  const d = (a - b) * 100;
  return `${d >= 0 ? '+' : ''}${d.toFixed(2)} pp`;
}

function primaryRows(horizon = state.horizon) {
  return state.frontier.filter(r => r.case === 'primary' && r.arm === 'baseline' && r.horizon_years === horizon);
}

function leapsRows(horizon = state.horizon) {
  return primaryRows(horizon).filter(r => String(r.family || '').includes('LEAPS'));
}

function rowFor(strategy = state.strategy, horizon = state.horizon) {
  return primaryRows(horizon).find(r => r.strategy === strategy) || null;
}

function familyForStrategy(strategy) {
  if (/^(SPX|NDX)_/.test(strategy)) return 'leaps';
  if (/SMA/.test(strategy)) return 'sma_rotation';
  if (/ALWAYS/.test(strategy)) return 'always_on_leverage';
  return 'stock_bond_leverage';
}

async function init() {
  try {
    const [frontierText, sensText, curriculum] = await Promise.all([
      fetch(DATA.frontier).then(r => { if (!r.ok) throw new Error(`frontier ${r.status}`); return r.text(); }),
      fetch(DATA.sensitivity).then(r => { if (!r.ok) throw new Error(`sensitivity ${r.status}`); return r.text(); }),
      fetch(DATA.curriculum).then(r => { if (!r.ok) throw new Error(`curriculum ${r.status}`); return r.json(); })
    ]);
    state.frontier = parseCSV(frontierText);
    state.sensitivity = parseCSV(sensText);
    state.curriculum = curriculum;
    if (!rowFor(state.strategy, 20)) state.strategy = leapsRows(20)[0]?.strategy || state.strategy;
    document.getElementById('snapshot-status').textContent = 'Research snapshot loaded';
    document.getElementById('snapshot-status').classList.add('ready');
    bindUI();
    populateStrategySelect();
    renderAll();
  } catch (err) {
    console.error(err);
    document.getElementById('snapshot-status').textContent = 'Research snapshot unavailable';
    document.getElementById('snapshot-status').title = String(err);
    document.querySelector('.analysis-area').innerHTML = `<div class="insight-card"><strong>Data could not be loaded.</strong><p>The GitHub Pages build must copy the committed report files into <code>site/data/</code>.</p></div>`;
  }
}

function bindUI() {
  document.querySelectorAll('.nav-tab').forEach(btn => btn.addEventListener('click', () => {
    state.view = btn.dataset.view;
    document.querySelectorAll('.nav-tab').forEach(b => b.classList.toggle('active', b === btn));
    document.querySelectorAll('.view').forEach(v => v.classList.toggle('active', v.id === `view-${state.view}`));
    renderAll();
  }));

  document.querySelectorAll('#horizon-control button').forEach(btn => btn.addEventListener('click', () => {
    state.horizon = Number(btn.dataset.horizon);
    document.querySelectorAll('#horizon-control button').forEach(b => b.classList.toggle('active', b === btn));
    renderAll();
  }));

  document.getElementById('strategy-select').addEventListener('change', e => {
    state.strategy = e.target.value;
    renderAll();
  });
  document.getElementById('x-axis').addEventListener('change', e => { state.xAxis = e.target.value; renderFrontier(); });
  document.getElementById('y-axis').addEventListener('change', e => { state.yAxis = e.target.value; renderFrontier(); });
  window.addEventListener('resize', debounce(() => state.view === 'frontier' && renderFrontier(), 120));
}

function populateStrategySelect() {
  const select = document.getElementById('strategy-select');
  const unique = [...new Set(state.frontier.filter(r => r.case === 'primary' && r.arm === 'baseline' && String(r.family || '').includes('LEAPS')).map(r => r.strategy))];
  unique.sort((a,b) => a.localeCompare(b, undefined, { numeric: true }));
  select.innerHTML = unique.map(s => `<option value="${s}">${prettyStrategy(s)}</option>`).join('');
  select.value = state.strategy;
}

function renderAll() {
  const select = document.getElementById('strategy-select');
  if (select && select.value !== state.strategy) select.value = state.strategy;
  renderRail();
  if (state.view === 'frontier') renderFrontier();
  if (state.view === 'montecarlo') renderMonteCarlo();
  if (state.view === 'sensitivity') renderSensitivity();
  if (state.view === 'longrange') renderLongRange();
}

function renderRail() {
  const row = rowFor();
  if (!row) return;
  document.getElementById('strategy-summary').innerHTML = `
    <strong>${prettyStrategy(row.strategy)}</strong><br>
    ${Math.round(row.moneyness * 100)}% strike · ${Math.round(row.premium_budget * 100)}% premium budget<br>
    ${row.roll} roll · ${(row.iv_premium * 100).toFixed(0)} vol-point modeled loading`;

  const frontierState = row.on_frontier ? 'non-dominated in the combined baseline frontier' : 'inside the opportunity set, but dominated on the baseline combined frontier';
  document.getElementById('point-reading').innerHTML = `
    <p><strong>${fmt(row.cagr_p50,'cagr_p50')} median CAGR</strong> with ${fmt(row.cagr_p5,'cagr_p5')} at p5 over ${state.horizon} years.</p>
    <p>Median max drawdown: <strong>${fmt(row.max_drawdown_median,'max_drawdown_median')}</strong>.</p>
    <p>This point is <strong>${frontierState}</strong>.</p>`;
}

function renderFrontier() {
  const holder = document.getElementById('frontier-chart');
  if (!holder) return;
  // Frontier view intentionally shows non-dominated points only; the selector retains all cells for audit/exploration.
  const all = leapsRows().filter(r => r[state.xAxis] != null && r[state.yAxis] != null);
  let rows = all.filter(r => r.on_frontier === true);
  if (!rows.length) rows = all;
  const selected = rowFor();
  // If selected is dominated, include it as a faint reference point.
  if (selected && !rows.some(r => r.strategy === selected.strategy)) rows = [...rows, selected];

  const width = Math.max(720, holder.clientWidth || 900), height = 450;
  const pad = { l: 64, r: 28, t: 24, b: 56 };
  const xs = rows.map(r => r[state.xAxis]);
  const ys = rows.map(r => r[state.yAxis]);
  const [xmin,xmax] = paddedExtent(xs), [ymin,ymax] = paddedExtent(ys);
  const X = v => pad.l + (v - xmin) / (xmax - xmin || 1) * (width - pad.l - pad.r);
  const Y = v => height - pad.b - (v - ymin) / (ymax - ymin || 1) * (height - pad.t - pad.b);
  const spx = css('--accent'), ndx = css('--accent-2');

  let svg = `<svg viewBox="0 0 ${width} ${height}" aria-label="${metricMeta[state.xAxis].label} versus ${metricMeta[state.yAxis].label}">`;
  for (let i=0;i<5;i++) {
    const tx = xmin + (xmax-xmin)*i/4, ty = ymin + (ymax-ymin)*i/4;
    svg += `<line class="chart-grid" x1="${X(tx)}" x2="${X(tx)}" y1="${pad.t}" y2="${height-pad.b}"/>`;
    svg += `<text class="chart-label" x="${X(tx)}" y="${height-30}" text-anchor="middle">${axisFmt(tx,state.xAxis)}</text>`;
    svg += `<line class="chart-grid" x1="${pad.l}" x2="${width-pad.r}" y1="${Y(ty)}" y2="${Y(ty)}"/>`;
    svg += `<text class="chart-label" x="${pad.l-10}" y="${Y(ty)+4}" text-anchor="end">${axisFmt(ty,state.yAxis)}</text>`;
  }
  svg += `<line class="chart-axis" x1="${pad.l}" x2="${width-pad.r}" y1="${height-pad.b}" y2="${height-pad.b}"/>`;
  svg += `<line class="chart-axis" x1="${pad.l}" x2="${pad.l}" y1="${pad.t}" y2="${height-pad.b}"/>`;
  svg += `<text class="chart-label" x="${(pad.l+width-pad.r)/2}" y="${height-7}" text-anchor="middle">${metricMeta[state.xAxis].label}</text>`;
  svg += `<text class="chart-label" transform="translate(14 ${(pad.t+height-pad.b)/2}) rotate(-90)" text-anchor="middle">${metricMeta[state.yAxis].label}</text>`;

  rows.forEach(r => {
    const isSelected = r.strategy === state.strategy;
    const dominatedReference = !r.on_frontier;
    const fill = String(r.strategy).startsWith('NDX_') ? ndx : spx;
    const radius = isSelected ? 8 : 6;
    svg += `<g class="point-group" data-strategy="${r.strategy}">`;
    svg += `<circle class="chart-point ${r.on_frontier ? 'frontier' : 'dim'} ${isSelected ? 'selected' : ''}" cx="${X(r[state.xAxis])}" cy="${Y(r[state.yAxis])}" r="${radius}" fill="${fill}">`;
    svg += `<title>${prettyStrategy(r.strategy)}\n${metricMeta[state.xAxis].label}: ${axisFmt(r[state.xAxis],state.xAxis)}\n${metricMeta[state.yAxis].label}: ${axisFmt(r[state.yAxis],state.yAxis)}${dominatedReference?'\nSelected dominated reference':''}</title></circle>`;
    if (isSelected || r.on_frontier) svg += `<text class="chart-label" x="${X(r[state.xAxis])+9}" y="${Y(r[state.yAxis])-9}">${shortStrategy(r.strategy)}</text>`;
    svg += `</g>`;
  });
  svg += `</svg>`;
  holder.innerHTML = svg;
  holder.querySelectorAll('.point-group').forEach(g => g.addEventListener('click', () => {
    state.strategy = g.dataset.strategy;
    renderAll();
  }));
  renderFrontierInsight(all);
}

function renderFrontierInsight(allRows) {
  const row = rowFor();
  const box = document.getElementById('frontier-insight');
  if (!row || !box) return;
  const family = allRows.filter(r => r.underlying === row.underlying && r.moneyness === row.moneyness).sort((a,b) => a.premium_budget-b.premium_budget);
  const i = family.findIndex(r => r.strategy === row.strategy);
  const lower = family[i-1], higher = family[i+1];
  let copy = `<p><strong>Read this as a trade, not a ranking.</strong> ${prettyStrategy(row.strategy)} uses ${fmt(row.mean_delta_exposure,'mean_delta_exposure')} average equity delta and ${fmt(row.premium_budget,'premium_budget',0)} of NAV as option premium.</p>`;
  if (higher) copy += `<p>Moving one budget step higher to <strong>${prettyStrategy(higher.strategy)}</strong> changes median CAGR by ${pctPointDelta(higher.cagr_p50,row.cagr_p50)}, p5 CAGR by ${pctPointDelta(higher.cagr_p5,row.cagr_p5)}, and P(DD&gt;60%) by ${pctPointDelta(higher.prob_drawdown_worse_than_60,row.prob_drawdown_worse_than_60)}. Ask whether that is greater efficiency or simply more exposure.</p>`;
  else if (lower) copy += `<p>The nearest lower-budget neighbor, <strong>${prettyStrategy(lower.strategy)}</strong>, gives up ${pctPointDelta(row.cagr_p50,lower.cagr_p50)} of median CAGR while changing the lower tail by ${pctPointDelta(row.cagr_p5,lower.cagr_p5)}.</p>`;
  box.innerHTML = copy;
}

function renderMonteCarlo() {
  const row = rowFor();
  if (!row) return;
  const metrics = [
    ['Median CAGR', fmt(row.cagr_p50,'cagr_p50'), `${state.horizon}-year path median`],
    ['5th-percentile CAGR', fmt(row.cagr_p5,'cagr_p5'), 'bad-but-plausible growth path'],
    ['Median max drawdown', fmt(row.max_drawdown_median,'max_drawdown_median'), 'within simulated path'],
    ['P(drawdown >60%)', fmt(row.prob_drawdown_worse_than_60,'prob_drawdown_worse_than_60'), 'severe-loss frequency']
  ];
  document.getElementById('mc-metrics').innerHTML = metrics.map(([l,v,s]) => `<div class="metric"><span class="label">${l}</span><span class="value">${v}</span><span class="sub">${s}</span></div>`).join('');

  const vals = [row.cagr_p5,row.cagr_p10,row.cagr_p50,row.cagr_p90,row.cagr_p95];
  const min = Math.min(...vals), max = Math.max(...vals), pos = v => 2 + (v-min)/(max-min||1)*96;
  document.getElementById('distribution-strip').innerHTML = `<div class="dist-line"></div>` + vals.map((v,i) => `<div class="dist-point ${i===2?'median':''}" style="left:${pos(v)}%"></div><span class="dist-value" style="left:${pos(v)}%">${(v*100).toFixed(1)}%</span>`).join('');
  document.getElementById('mc-explanation').innerHTML = `<p><strong>The median is not the forecast.</strong> A ${fmt(row.cagr_p50,'cagr_p50')} median sits beside a ${fmt(row.cagr_p5,'cagr_p5')} p5 outcome. The useful question is whether the lower tail is tolerable enough to stay invested through it—not whether the median is attractive.</p>`;
}

function renderSensitivity() {
  const famKey = familyForStrategy(state.strategy);
  const fam = state.curriculum?.families?.[famKey];
  if (!fam) return;
  document.getElementById('thesis-card').innerHTML = `
    <p class="eyebrow">WHY THIS CAN WORK</p>
    <div class="thesis-main">${fam.thesis}</div>
    <div class="thesis-columns">
      <div><h3>Implicit bets</h3><ul class="mini-list">${fam.implicit_bets.map(x=>`<li>${x}</li>`).join('')}</ul></div>
      <div><h3>It does not require</h3><ul class="mini-list">${(fam.does_not_require||[]).map(x=>`<li>${x}</li>`).join('')}</ul></div>
    </div>`;
  const hierarchy = fam.sensitivity_hierarchy;
  document.getElementById('question-list').innerHTML = fam.questions.map((q,i)=>`<button class="question-card" data-question="${q.id}"><span class="q-order">${hierarchy.first_order.some(x => questionTouches(q.id,x)) ? 'FIRST-ORDER' : i<3 ? 'HIGH-VALUE TEST' : 'FOLLOW-UP'}</span><h4>${q.prompt}</h4><p>${q.answer_hint}</p></button>`).join('');
  document.querySelectorAll('.question-card').forEach(card => card.addEventListener('click', () => {
    document.querySelectorAll('.question-card').forEach(c => c.classList.toggle('active', c === card));
    const q = fam.questions.find(x => x.id === card.dataset.question);
    renderExperiment(q, fam);
  }));
}

function renderExperiment(q, fam) {
  const panel = document.getElementById('experiment-panel');
  if (!q) return;
  if (q.test === 'iv_loading_sensitivity') {
    const rows = state.sensitivity.filter(r => r.case === 'iv_premium' && r.strategy === state.strategy && r.horizon_years === 30).sort((a,b)=>a.iv_premium-b.iv_premium);
    if (!rows.length) return renderUnavailableExperiment(q, fam);
    const baseline = rowFor(state.strategy,30);
    panel.innerHTML = `<p class="eyebrow">TEST · OPTION-PRICING HURDLE</p><h3>${q.prompt}</h3><p>${q.answer_hint}</p><div id="iv-controls" class="control-row">${rows.map(r=>`<button class="control-chip ${Math.abs(r.iv_premium-.03)<1e-8?'active':''}" data-iv="${r.iv_premium}">RV +${Math.round(r.iv_premium*100)} vol</button>`).join('')}</div><div id="iv-result"></div>`;
    const draw = iv => {
      const r = rows.find(x=>x.iv_premium===iv) || rows[0];
      panel.querySelectorAll('.control-chip').forEach(c=>c.classList.toggle('active',Number(c.dataset.iv)===iv));
      const dcagr = baseline ? pctPointDelta(r.cagr_p50, baseline.cagr_p50) : '—';
      const ddelta = baseline && r.mean_delta_exposure != null ? `${(r.mean_delta_exposure-baseline.mean_delta_exposure)>=0?'+':''}${(r.mean_delta_exposure-baseline.mean_delta_exposure).toFixed(2)}×` : '—';
      document.getElementById('iv-result').innerHTML = `
        <div class="experiment-result">
          <div class="result-box"><span class="label">Median 30y CAGR</span><span class="value">${fmt(r.cagr_p50,'cagr_p50')}</span><small>${dcagr} vs +3 baseline</small></div>
          <div class="result-box"><span class="label">p5 30y CAGR</span><span class="value">${fmt(r.cagr_p5,'cagr_p5')}</span><small>lower-tail growth</small></div>
          <div class="result-box"><span class="label">Mean delta</span><span class="value">${fmt(r.mean_delta_exposure,'mean_delta_exposure')}</span><small>${ddelta} vs baseline</small></div>
        </div>
        <div class="explanation-stack">
          <div class="explanation-block"><h4>What changed?</h4><p>At the selected volatility loading, the fixed premium budget produces ${fmt(r.mean_delta_exposure,'mean_delta_exposure')} mean equity delta and ${fmt(r.cagr_p50,'cagr_p50')} median 30-year CAGR.</p></div>
          <div class="explanation-block"><h4>Why did it change?</h4><p>Higher implied volatility makes each call more expensive. With the budget fixed, the portfolio buys fewer contracts, altering both effective equity exposure and the price paid for convexity.</p></div>
          <div class="explanation-block"><h4>What should I ask next?</h4><p>Compare the live chain's IV-plus-skew hurdle with these sensitivity arms before tuning maturity or roll frequency.</p></div>
        </div>`;
    };
    panel.querySelectorAll('.control-chip').forEach(c=>c.addEventListener('click',()=>draw(Number(c.dataset.iv))));
    draw(rows.some(r=>Math.abs(r.iv_premium-.03)<1e-8) ? .03 : rows[0].iv_premium);
    return;
  }

  if (q.test === 'neighbor_compare') {
    const row = rowFor();
    const family = leapsRows().filter(r=>r.underlying===row.underlying && r.moneyness===row.moneyness).sort((a,b)=>a.premium_budget-b.premium_budget);
    const i = family.findIndex(r=>r.strategy===row.strategy);
    const candidates = [family[i-1], row, family[i+1]].filter(Boolean);
    panel.innerHTML = `<p class="eyebrow">TEST · NEIGHBORING RISK REGIMES</p><h3>${q.prompt}</h3><p>${q.answer_hint}</p><div class="experiment-result">${candidates.map(r=>`<div class="result-box"><span class="label">${prettyStrategy(r.strategy)}</span><span class="value">${fmt(r.cagr_p50,'cagr_p50')}</span><small>p5 ${fmt(r.cagr_p5,'cagr_p5')} · delta ${fmt(r.mean_delta_exposure,'mean_delta_exposure')}</small></div>`).join('')}</div><div class="explanation-stack"><div class="explanation-block"><h4>What changed?</h4><p>Premium budget, delta exposure, median return, and tail risk move together.</p></div><div class="explanation-block"><h4>Why did it change?</h4><p>A larger budget buys more option exposure. A higher CAGR alone is therefore not evidence of a more efficient structure.</p></div><div class="explanation-block"><h4>What should I ask next?</h4><p>Move to the Frontier and decide whether the extra median return compensates for the change in p5 CAGR and severe-drawdown probability.</p></div></div>`;
    return;
  }

  renderUnavailableExperiment(q, fam);
}

function renderUnavailableExperiment(q, fam) {
  const panel = document.getElementById('experiment-panel');
  panel.innerHTML = `<p class="eyebrow">PRECOMPUTED RESEARCH TEST</p><h3>${q.prompt}</h3><p>${q.answer_hint}</p><div class="stop-rule"><strong>This test exists in the research repository but is not yet wired into the browser dataset.</strong><br>The site does not invent a result when a precomputed table is unavailable. See the linked source reports in the Long-range assessment.</div><div class="explanation-stack" style="margin-top:16px"><div class="explanation-block"><h4>What should I ask next?</h4><p>${fam.discoveries?.[0]?.lesson || 'Prefer a falsification or matched-exposure test over a nearby parameter tweak.'}</p></div></div>`;
}

function renderLongRange() {
  const famKey = familyForStrategy(state.strategy), fam = state.curriculum?.families?.[famKey];
  const row = rowFor();
  if (!fam || !row) return;
  const firstOrder = fam.sensitivity_hierarchy.first_order.map(humanize).join(', ');
  const implementation = fam.sensitivity_hierarchy.implementation.map(humanize).join(', ');
  document.getElementById('longrange-interpretation').innerHTML = `
    <div class="longrange-card"><span class="label">CENTRAL THESIS</span><p>${fam.thesis}</p></div>
    <div class="longrange-card"><span class="label">STRONGEST EVIDENCE</span><p>The selected cell is evaluated across 10-, 20-, and 30-year jointly resampled paths, with explicit lower-tail and drawdown outcomes rather than CAGR alone.</p></div>
    <div class="longrange-card"><span class="label">MOST IMPORTANT UNRESOLVED UNCERTAINTY</span><p>${firstOrder}. These can change whether the modeled advantage survives at all.</p></div>
    <div class="longrange-card"><span class="label">PARAMETERS THAT MATTER LESS THAN THEY APPEAR</span><p>${implementation}. The repo's duration study found several maturity differences smaller than one volatility point of option-pricing uncertainty.</p></div>`;
  renderValuesTable();
}

function renderValuesTable() {
  const rows = leapsRows().slice().sort((a,b)=>a.cagr_p50-b.cagr_p50);
  const table = document.getElementById('values-table');
  table.innerHTML = `<thead><tr><th>Strategy</th><th>Median CAGR</th><th>p5 CAGR</th><th>Median DD</th><th>P(DD&gt;60)</th><th>Mean delta</th><th>Premium</th><th>Frontier</th></tr></thead><tbody>${rows.map(r=>`<tr class="${r.strategy===state.strategy?'selected-row':''}" data-strategy="${r.strategy}"><td>${prettyStrategy(r.strategy)}</td><td>${fmt(r.cagr_p50,'cagr_p50')}</td><td>${fmt(r.cagr_p5,'cagr_p5')}</td><td>${fmt(r.max_drawdown_median,'max_drawdown_median')}</td><td>${fmt(r.prob_drawdown_worse_than_60,'prob_drawdown_worse_than_60')}</td><td>${fmt(r.mean_delta_exposure,'mean_delta_exposure')}</td><td>${fmt(r.premium_budget,'premium_budget',0)}</td><td>${r.on_frontier?'yes':'—'}</td></tr>`).join('')}</tbody>`;
  table.querySelectorAll('tbody tr').forEach(tr=>tr.addEventListener('click',()=>{ state.strategy=tr.dataset.strategy; renderAll(); }));
}

function paddedExtent(vals) {
  let min = Math.min(...vals), max = Math.max(...vals);
  if (min === max) return [min-1,max+1];
  const p = (max-min)*.09;
  return [min-p,max+p];
}
function axisFmt(v,key) {
  const meta = metricMeta[key] || {};
  if (meta.pct) return `${(v*100).toFixed(Math.abs(v)<.1?1:0)}%`;
  if (meta.suffix) return `${v.toFixed(1)}${meta.suffix}`;
  return v.toFixed(1);
}
function prettyStrategy(s) { return String(s).replaceAll('_',' ').replace('SPX ','SPX ').replace('NDX ','NDX '); }
function shortStrategy(s) { return String(s).replace('SPX_','SPX ').replace('NDX_','NDX '); }
function humanize(s) { return String(s).replaceAll('_',' '); }
function questionTouches(qid, hierarchyItem) { return qid.includes('iv') && hierarchyItem.includes('iv') || qid.includes('treasury') && hierarchyItem.includes('treasury') || qid.includes('source') && hierarchyItem.includes('growth'); }
function css(name) { return getComputedStyle(document.documentElement).getPropertyValue(name).trim(); }
function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t=setTimeout(()=>fn(...a),ms); }; }

document.addEventListener('DOMContentLoaded', init);
