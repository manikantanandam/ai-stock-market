// Dashboard: reads data/manifest.json, then draws the file named in the URL
// hash by its kind (see aistock/site/__init__.py). No build step.
'use strict';

const $ = (sel, el = document) => el.querySelector(sel);
const h = (tag, attrs = {}, ...kids) => {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') el.className = v;
    else if (k.startsWith('on')) el.addEventListener(k.slice(2), v);
    else el.setAttribute(k, v);
  }
  for (const kid of kids.flat()) if (kid != null) el.append(kid);
  return el;
};

let manifest = null;
let charts = [];
const cache = new Map();

// ---------- data ----------

async function load(path) {
  if (!cache.has(path)) {
    const text = await (await fetch('data/' + path)).text();
    const parsed = Papa.parse(text.trim(), { header: true, dynamicTyping: true, skipEmptyLines: true });
    cache.set(path, parsed.data);
  }
  return cache.get(path);
}

const num = v => typeof v === 'number' && isFinite(v);
const values = (rows, col) => rows.map(r => r[col]).filter(num);
const mean = xs => xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : NaN;

// Columns holding fractions (0.05 = 5%) are shown as percents.
const PCT = ['ret', 'rate', 'cagr', 'drawdown', 'return', 'excess', 'avg_trade', 'median_trade',
  'exposure', 'exit_stop', 'exit_target', 'exit_time', 'profitable', 'beat', 'buy_hold'];
const isPct = col => PCT.some(p => col.toLowerCase().includes(p));

function fmt(v, col = '') {
  if (v == null || v === '') return '';
  if (!num(v)) return String(v);
  if (isPct(col)) return (v * 100).toFixed(Math.abs(v) < 0.1 ? 2 : 1) + '%';
  if (Number.isInteger(v)) return v.toLocaleString('en-IN');
  return Math.abs(v) >= 100 ? v.toLocaleString('en-IN', { maximumFractionDigits: 2 }) : +v.toFixed(4) + '';
}
const pct = v => num(v) ? (v * 100).toFixed(1) + '%' : '–';

// ---------- theme & charts ----------

const css = name => getComputedStyle(document.documentElement).getPropertyValue(name).trim();
const series = i => css('--s' + ((i % 8) + 1));

function chartDefaults() {
  Chart.defaults.color = css('--ink-2');
  Chart.defaults.borderColor = css('--grid');
  Chart.defaults.font.family = getComputedStyle(document.body).fontFamily;
  Chart.defaults.animation = false;
  Chart.defaults.maintainAspectRatio = false;
  Chart.defaults.plugins.legend.labels.boxWidth = 12;
  Chart.defaults.plugins.tooltip.mode = 'index';
  Chart.defaults.plugins.tooltip.intersect = false;
  Chart.defaults.interaction = { mode: 'index', intersect: false };
}

function chart(box, config) {
  const canvas = h('canvas');
  box.append(canvas);
  charts.push(new Chart(canvas, config));
}

function card(title, ...kids) {
  return h('div', { class: 'card' }, title ? h('h3', {}, title) : null, ...kids);
}

function chartCard(title, config, tall = false) {
  const box = h('div', { class: 'chart-box' + (tall ? ' tall' : '') });
  const c = card(title, box);
  requestAnimationFrame(() => chart(box, config));
  return c;
}

function tiles(items) {
  return h('div', { class: 'tiles' }, items.map(([label, value, cls]) =>
    h('div', { class: 'tile' }, h('div', { class: 'label' }, label),
      h('div', { class: 'value' + (cls ? ' ' + cls : '') }, value))));
}

const sign = v => num(v) ? (v > 0 ? 'pos' : v < 0 ? 'neg' : '') : '';

// ---------- table ----------

function table(rows, columns, opts = {}) {
  // labels: header text per column; cells: custom renderer (value, row) -> node; extra: columns hidden until 'All columns';
  // sortKeys: (value, row) -> number to sort a column by instead of its raw value
  const { filters = [], link = {}, pageSize = 200, labels = {}, cells = {}, extra = [], sortKeys = {} } = opts;
  let sortCol = opts.sort || null, asc = opts.asc ?? false, query = '', shown = pageSize, showAll = false;
  const active = Object.fromEntries(filters.map(f => [f, '']));
  const numeric = Object.fromEntries(columns.map(c => [c, rows.some(r => num(r[c]))]));
  const descFirst = c => numeric[c] || c in sortKeys;

  const search = h('input', { type: 'search', placeholder: 'Search…' });
  search.addEventListener('input', () => { query = search.value.toLowerCase(); shown = pageSize; draw(); });
  const selects = filters.map(f => {
    const opts = [...new Set(rows.map(r => r[f]).filter(v => v != null && v !== ''))].sort();
    const s = h('select', {}, h('option', { value: '' }, 'All ' + f), opts.map(o => h('option', { value: o }, o)));
    s.addEventListener('change', () => { active[f] = s.value; shown = pageSize; draw(); });
    return s;
  });
  const sortBy = h('select', { title: 'Sort by' }, h('option', { value: '' }, 'Sort: file order'),
    columns.map(c => h('option', { value: c }, 'Sort: ' + (labels[c] || c))));
  sortBy.addEventListener('change', () => { sortCol = sortBy.value || null; asc = !descFirst(sortCol); draw(); });
  const dir = h('button', { class: 'link', title: 'Flip sort direction' });
  dir.addEventListener('click', () => { asc = !asc; draw(); });
  const toggle = extra.length ? h('input', { type: 'checkbox' }) : null;
  toggle?.addEventListener('change', () => { showAll = toggle.checked; draw(); });
  const count = h('span', { class: 'muted' });
  const tbody = h('tbody'), headRow = h('tr');
  const ths = Object.fromEntries(columns.map(c => {
    const th = h('th', { class: numeric[c] ? 'num' : '', title: c });
    th.addEventListener('click', () => { asc = sortCol === c ? !asc : !descFirst(c); sortCol = c; draw(); });
    return [c, th];
  }));
  const more = h('button', { class: 'link' }, 'Show more');
  more.addEventListener('click', () => { shown += pageSize; draw(); });

  function draw() {
    let out = rows.filter(r =>
      filters.every(f => !active[f] || String(r[f]) === active[f]) &&
      (!query || columns.some(c => String(r[c] ?? '').toLowerCase().includes(query))));
    if (sortCol) {
      out = out.slice().sort((a, b) => {
        const key = sortKeys[sortCol] || (v => v);
        const x = key(a[sortCol], a), y = key(b[sortCol], b);
        if (x == null || x === '') return 1;
        if (y == null || y === '') return -1;
        return (x < y ? -1 : x > y ? 1 : 0) * (asc ? 1 : -1);
      });
    }
    sortBy.value = sortCol || '';
    dir.textContent = asc ? '▲ Asc' : '▼ Desc';
    dir.disabled = !sortCol;
    const visible = showAll ? columns : columns.filter(c => !extra.includes(c));
    for (const c of visible) ths[c].textContent = (labels[c] || c) + (c === sortCol ? (asc ? ' ▲' : ' ▼') : '');
    headRow.replaceChildren(...visible.map(c => ths[c]));
    tbody.replaceChildren(...out.slice(0, shown).map(r => h('tr', {}, visible.map(c => {
      const v = r[c];
      const cls = (numeric[c] ? 'num ' : '') + (isPct(c) ? sign(v) : '');
      if (cells[c]) return h('td', { class: cls }, cells[c](v, r));
      const content = link[c] && v ? h('a', { href: link[c](v) }, String(v))
        : c === 'action' && v ? h('span', { class: 'badge ' + v }, v) : fmt(v, c);
      const long = typeof v === 'string' && v.length > 60;
      return h('td', long ? { class: cls + ' clip', title: v } : { class: cls }, content);
    }))));
    count.textContent = `${out.length.toLocaleString()} of ${rows.length.toLocaleString()} rows`;
    more.style.display = out.length > shown ? '' : 'none';
  }
  draw();
  return card(null, h('div', { class: 'controls' }, search, selects, sortBy, dir,
      toggle ? h('label', {}, toggle, 'All columns') : null, count),
    h('div', { class: 'table-wrap' }, h('table', {}, h('thead', {}, headRow), tbody)),
    h('div', { class: 'more' }, more));
}

// ---------- views ----------

const stockHref = sym => '#journal/stocks/' + encodeURIComponent(sym) + '.csv';

function histogram(xs, lo, hi, step) {
  const edges = [];
  for (let e = lo; e < hi - 1e-9; e += step) edges.push(+e.toFixed(6));
  const counts = edges.map(() => 0);
  for (const x of xs) {
    const i = Math.min(edges.length - 1, Math.max(0, Math.floor((x - lo) / step)));
    counts[i]++;
  }
  return { edges, counts };
}

function countBy(rows, col) {
  const m = new Map();
  for (const r of rows) { const k = r[col] ?? '–'; m.set(k, (m.get(k) || 0) + 1); }
  return [...m.entries()].sort((a, b) => b[1] - a[1]);
}

function barConfig(labels, data, opts = {}) {
  return {
    type: 'bar',
    data: { labels, datasets: [{ data, backgroundColor: opts.colors || series(0), borderRadius: 4, borderSkipped: 'start', maxBarThickness: 28 }] },
    options: {
      indexAxis: opts.horizontal ? 'y' : 'x',
      plugins: { legend: { display: false }, tooltip: opts.tooltip ? { callbacks: { label: opts.tooltip } } : {} },
      scales: { x: { grid: { display: !!opts.horizontal } }, y: { grid: { display: !opts.horizontal } } },
    },
  };
}

// Scan table: key columns first with readable cells; raw ratios and reasons behind 'All columns'.
const SCAN_COLS = ['symbol', 'sector', 'action', 'buys', 'combined', 'technical', 'fundamental', 'sentiment',
  'close', 'atr', 'rs_3m', 'rs_6m', 'sector_up', 'f_marketCap', 'technical_reasons'];
const SCAN_LABELS = { symbol: 'Stock', sector: 'Sector', action: 'Action', buys: 'BUY strategies', combined: 'Combined',
  technical: 'Tech', fundamental: 'Fund', sentiment: 'News', close: 'Close', atr: 'ATR', rs_3m: 'RS 3m', rs_6m: 'RS 6m',
  sector_up: 'Sector trend', f_marketCap: 'Mkt cap', technical_reasons: 'Technical reasons' };
const dash = () => h('span', { class: 'muted' }, '–');

// A [-1, 1] score as a bar growing left or right of zero, plus the value.
const scoreCell = v => num(v) ? h('span', { class: 'score-cell' },
  h('span', { class: 'score-bar' }, h('span', { class: sign(v),
    style: `width:${Math.min(1, Math.abs(v)) * 50}%;${v >= 0 ? 'left' : 'right'}:50%` })),
  h('span', { class: sign(v) }, (v > 0 ? '+' : '') + v.toFixed(2))) : dash();

const SCAN_CELLS = {
  symbol: (v, r) => h('div', {}, h('a', { href: stockHref(v) }, v), r.name ? h('div', { class: 'cell-sub' }, r.name) : null),
  buys: v => v ? String(v).split(';').map(b => h('span', { class: 'badge tag' }, b)) : dash(),
  combined: scoreCell, technical: scoreCell, fundamental: scoreCell, sentiment: scoreCell,
  rs_3m: v => num(v) ? h('span', { class: sign(v) }, (v > 0 ? '+' : '') + pct(v)) : dash(),
  rs_6m: v => num(v) ? h('span', { class: sign(v) }, (v > 0 ? '+' : '') + pct(v)) : dash(),
  sector_up: v => v == null || v === '' ? dash() : String(v).toLowerCase() === 'true'
    ? h('span', { class: 'pos' }, '▲ Up') : h('span', { class: 'neg' }, '▼ Down'),
  f_marketCap: v => num(v) ? '₹' + Math.round(v / 1e7).toLocaleString('en-IN') + ' Cr' : dash(),
};

// Most BUY strategies first; combined/10 stays within ±0.1, so it only breaks ties.
const SCAN_SORT = { buys: (v, r) => (v ? String(v).split(';').length : 0) + (num(r.combined) ? r.combined / 10 : 0) };

function scanTable(rows, columns) {
  const main = SCAN_COLS.filter(c => columns.includes(c));
  const extra = columns.filter(c => !main.includes(c));
  return table(rows, [...main, ...extra], { filters: ['action', 'sector'], sort: 'combined',
    labels: SCAN_LABELS, cells: SCAN_CELLS, sortKeys: SCAN_SORT, extra, link: { symbol: stockHref } });
}

// LLM notes explain verdicts the rules already reached (stock BUYs; every commodity); they never pick trades.
// days: the session's journal rows, for every strategy entering and the combined score.
// entries: day row -> [[strategy, action]]; href: symbol -> link.
const stockEntries = day => day.buys ? String(day.buys).split(';').map(b => [b, 'BUY']) : [];

function notesCard(rows, title, days, entries = stockEntries, href = stockHref) {
  const bySymbol = new Map(days.map(d => [d.symbol, d]));
  return card(title,
    h('p', { class: 'sub' }, 'Written by a local LLM (qwen2.5:3b) from the signal\'s numbers; the rules made the decision.'),
    rows.map(r => {
      const day = bySymbol.get(r.symbol) || {};
      const tags = entries(day);
      const buys = tags.length ? tags : [[r.strategy, r.action || 'BUY']].filter(t => t[0]);
      return h('div', { class: 'note' },
        h('div', { class: 'note-head' }, h('a', { href: href(r.symbol) }, r.symbol), ' ',
          h('span', { class: 'muted' }, r.name || ''),
          num(day.combined) ? h('span', { class: 'score ' + (day.combined >= 0 ? 'pos' : 'neg') },
            'combined ' + (day.combined >= 0 ? '+' : '') + day.combined.toFixed(2)) : null,
          buys.map(([b, a]) => h('span', { class: 'badge ' + (b === r.strategy ? a : 'tag'),
            title: b === r.strategy ? 'The strategy this note explains' : 'Also says ' + a }, b)),
          !r.strategy && r.action ? h('span', { class: 'badge ' + r.action, title: 'No strategy has a position or an entry' }, r.action) : null,
          r.source === 'backfill' ? h('span', { class: 'badge', title: 'Written later from the journal row; fundamental and news reasons were not saved for this session' }, 'backfilled') : null),
        h('p', {}, r.note));
    }));
}

async function renderNotes(file, rows) {
  const dayPath = 'journal/days/' + file.path.split('/').pop();
  const days = manifest.files.some(f => f.path === dayPath) ? await load(dayPath) : [];
  return [notesCard(rows, null, days)];
}

function renderDay(file, rows) {
  const actions = countBy(rows, 'action');
  const buys = rows.filter(r => r.action === 'BUY');
  const sectors = countBy(buys.length ? buys : rows.filter(r => r.action === 'WATCH'), 'sector').slice(0, 15);
  const hist = histogram(values(rows, 'combined'), -1, 1, 0.1);
  return [
    tiles([['Stocks scanned', rows.length.toLocaleString()],
      ...actions.map(([a, n]) => [a, n.toLocaleString(), a === 'BUY' ? 'pos' : '']),
      ['Avg combined score', fmt(mean(values(rows, 'combined')))],
      ['Avg technical score', fmt(mean(values(rows, 'technical')))]]),
    h('div', { class: 'charts' },
      chartCard((buys.length ? 'BUY' : 'WATCH') + ' signals by sector', barConfig(sectors.map(s => s[0]), sectors.map(s => s[1]), { horizontal: true })),
      chartCard('Combined score distribution', barConfig(hist.edges.map(e => e.toFixed(1)), hist.counts, {
        colors: hist.edges.map(e => e < 0 ? css('--s8') : css('--s3')),
      }))),
    scanTable(rows, file.columns),
  ];
}

// ---------- commodity futures ----------

const contractHref = sym => '#journal/commodities/contracts/' + encodeURIComponent(sym) + '.csv';
// Every strategy with a verdict other than WATCH; exits/holds are "strategy:side" pairs.
const tagNames = v => v ? String(v).split(';').map(t => t.split(':')[0]) : [];
const commodityEntries = day => [['buys', 'BUY'], ['sells', 'SELL'], ['exits', 'EXIT'], ['holds', 'HOLD']]
  .flatMap(([col, action]) => tagNames(day[col]).map(n => [n, action]));
// exits/holds are "strategy:side" pairs
const sideTags = (v, cls) => v ? String(v).split(';').map(t => {
  const [name, side] = t.split(':');
  return h('span', { class: 'badge ' + cls, title: side }, `${name} ${side === 'short' ? '▼' : '▲'}`);
}) : dash();

const COMMODITY_COLS = ['symbol', 'group', 'action', 'buys', 'sells', 'exits', 'holds', 'combined', 'technical',
  'sentiment', 'close', 'atr', 'close_usd', 'usdinr', 'technical_reasons', 'sentiment_reasons'];
const COMMODITY_LABELS = { symbol: 'Contract', group: 'Group', action: 'Action', buys: 'BUY (long)',
  sells: 'SELL (short)', exits: 'EXIT', holds: 'HOLD', combined: 'Combined', technical: 'Tech', sentiment: 'News',
  close: 'Close ₹', atr: 'ATR ₹', close_usd: 'Close USD', usdinr: 'USDINR', technical_reasons: 'Technical reasons',
  sentiment_reasons: 'News reasons' };
const COMMODITY_CELLS = {
  symbol: (v, r) => h('div', {}, h('a', { href: contractHref(v) }, v), r.name ? h('div', { class: 'cell-sub' }, r.name) : null),
  buys: v => v ? String(v).split(';').map(b => h('span', { class: 'badge BUY' }, b)) : dash(),
  sells: v => v ? String(v).split(';').map(b => h('span', { class: 'badge SELL' }, b)) : dash(),
  exits: v => sideTags(v, 'EXIT'), holds: v => sideTags(v, 'HOLD'),
  combined: scoreCell, technical: scoreCell, sentiment: scoreCell,
  close: (v, r) => num(v) ? h('div', {}, fmt(v), r.unit ? h('div', { class: 'cell-sub' }, 'per ' + r.unit) : null) : dash(),
};

// A session's commodity notes (~one per contract) are drawn under its scan table.
async function renderCommodityDay(file, rows) {
  const notesPath = 'journal/commodities/notes/' + file.path.split('/').pop();
  const notes = manifest.files.some(f => f.path === notesPath) ? await load(notesPath) : [];
  const actions = countBy(rows, 'action');
  const main = COMMODITY_COLS.filter(c => file.columns.includes(c));
  const extra = file.columns.filter(c => !main.includes(c));
  return [
    h('p', { class: 'sub' }, 'Global futures (COMEX/NYMEX) in INR per MCX unit. BUY/SELL open a long/short at the next open; ' +
      'EXIT and HOLD follow each strategy\'s backtested position.'),
    tiles([['Contracts', rows.length.toLocaleString()],
      ...actions.map(([a, n]) => [a, n.toLocaleString(), a === 'BUY' ? 'pos' : a === 'SELL' ? 'neg' : ''])]),
    table(rows, [...main, ...extra], { filters: ['action', 'group'], sort: 'combined', labels: COMMODITY_LABELS,
      cells: COMMODITY_CELLS, extra, link: { symbol: contractHref } }),
    notes.length ? notesCard(notes, 'LLM notes', rows, commodityEntries, contractHref) : null,
  ];
}

function lineConfig(labels, datasets, opts = {}) {
  return {
    type: 'line',
    data: { labels, datasets: datasets.map(d => ({ pointRadius: 0, pointHoverRadius: 4, borderWidth: 2, spanGaps: false, ...d })) },
    options: {
      plugins: { legend: { display: datasets.length > 1 },
        tooltip: { callbacks: { label: c => `${c.dataset.label}: ${opts.pct ? pct(c.raw) : fmt(c.raw)}` } } },
      scales: {
        x: { ticks: { maxTicksLimit: 10, maxRotation: 0 }, grid: { display: false } },
        y: { type: opts.log ? 'logarithmic' : 'linear', min: opts.min, max: opts.max,
          ticks: opts.pct ? { callback: v => pct(v) } : {} },
      },
    },
  };
}

function renderStock(file, rows) {
  const last = rows[rows.length - 1] || {};
  const labels = rows.map(r => r.date);
  const scores = ['combined', 'technical', 'fundamental', 'sentiment'].filter(c => file.columns.includes(c));
  return [
    h('p', { class: 'sub' }, [last.name, last.sector || last.group, last.unit && 'INR per ' + last.unit].filter(Boolean).join(' · ')),
    tiles([['Last action', last.action || '–', last.action === 'BUY' ? 'pos' : last.action === 'SELL' ? 'neg' : ''],
      ['Close', fmt(last.close)], ...scores.map(c => [c[0].toUpperCase() + c.slice(1), fmt(last[c])]),
      ['Sessions logged', rows.length]]),
    h('div', { class: 'charts' },
      chartCard('Close', lineConfig(labels, [{ label: 'Close', data: rows.map(r => r.close), borderColor: series(0) }])),
      chartCard('Scores (−1 to 1)', lineConfig(labels, scores.map((c, i) =>
        ({ label: c, data: rows.map(r => r[c]), borderColor: series(i), backgroundColor: series(i), pointRadius: 3 })), { min: -1, max: 1 }))),
    table(rows.slice().reverse(), file.columns),
  ];
}

function renderTrades(file, rows) {
  const rets = values(rows, 'ret');
  const wins = rets.filter(r => r > 0), losses = rets.filter(r => r <= 0);
  const gross = a => a.reduce((s, x) => s + x, 0);
  const pf = losses.length ? gross(wins) / -gross(losses) : NaN;
  const hist = histogram(rets.map(r => Math.max(-0.3, Math.min(0.5, r))), -0.3, 0.5, 0.025);
  const reasons = countBy(rows, 'reason').filter(([k]) => k !== '–');
  const by = ['strategy', 'segment'].find(c => file.columns.includes(c));
  const groups = by ? countBy(rows, by).map(([k]) => {
    const g = values(rows.filter(r => r[by] === k), 'ret');
    return { [by]: k, trades: g.length, win_rate: g.filter(x => x > 0).length / (g.length || 1), avg_ret: mean(g) };
  }) : [];
  const filters = ['strategy', 'segment', 'status', 'reason', 'symbol'].filter(c => file.columns.includes(c)).slice(0, 3);
  return [
    tiles([['Trades', rows.length.toLocaleString()], ['Win rate', pct(wins.length / (rets.length || 1))],
      ['Avg return', pct(mean(rets)), sign(mean(rets))], ['Profit factor', num(pf) ? pf.toFixed(2) : '–'],
      ['Best', pct(Math.max(...rets)), 'pos'], ['Worst', pct(Math.min(...rets)), 'neg']]),
    h('div', { class: 'charts' },
      chartCard('Trade return distribution (clipped to −30%…+50%)', barConfig(hist.edges.map(e => pct(e)), hist.counts, {
        colors: hist.edges.map(e => e < 0 ? css('--s8') : css('--s3')),
      })),
      reasons.length ? chartCard('Exit reasons', barConfig(reasons.map(r => r[0]), reasons.map(r => r[1]), { horizontal: true })) : null),
    groups.length ? table(groups, [by, 'trades', 'win_rate', 'avg_ret'], { pageSize: 50 }) : null,
    table(rows, file.columns, { filters, link: { symbol: file.path.includes('commodities_') ? contractHref : stockHref } }),
  ];
}

// Max drawdown and CAGR over the non-empty stretch of one series.
function seriesStats(labels, data) {
  const pts = data.map((v, i) => [labels[i], v]).filter(p => num(p[1]));
  if (pts.length < 2) return null;
  let peak = -Infinity, dd = 0;
  for (const [, v] of pts) { peak = Math.max(peak, v); dd = Math.min(dd, v / peak - 1); }
  const [d0, v0] = pts[0], [d1, v1] = pts[pts.length - 1];
  const years = (new Date(d1) - new Date(d0)) / 3.15576e10;
  return { start: d0, end: d1, final: v1, total_return: v1 / v0 - 1,
    cagr: years > 0.5 ? Math.pow(v1 / v0, 1 / years) - 1 : null, max_drawdown: dd };
}

function renderSeries(file, rows) {
  const [dateCol, ...cols] = file.columns;
  const labels = rows.map(r => r[dateCol]);
  // "trend In-sample" / "trend Out-of-sample" share a color; out-of-sample is dashed.
  const entity = c => c.replace(/ (In-sample|Out-of-sample)$/, '');
  const entities = [...new Set(cols.map(entity))];
  const datasets = cols.map(c => ({
    label: c, data: rows.map(r => r[c]), borderColor: series(entities.indexOf(entity(c))),
    backgroundColor: series(entities.indexOf(entity(c))), borderDash: /Out-of-sample$/.test(c) ? [6, 4] : [],
  }));
  const stats = cols.map(c => ({ series: c, ...seriesStats(labels, rows.map(r => r[c])) })).filter(s => s.start);
  const positive = cols.every(c => values(rows, c).every(v => v > 0));
  let log = false;
  const wrap = h('div');
  const draw = () => {
    charts.forEach(c => c.destroy()); charts = [];
    wrap.replaceChildren(chartCard(null, lineConfig(labels, datasets, { log }), true));
  };
  const toggle = h('input', { type: 'checkbox' });
  toggle.addEventListener('change', () => { log = toggle.checked; draw(); });
  draw();
  return [
    positive ? h('div', { class: 'controls' }, h('label', {}, toggle, 'Log scale')) : null,
    wrap,
    stats.length ? table(stats, ['series', 'start', 'end', 'final', 'total_return', 'cagr', 'max_drawdown'], { pageSize: 50 }) : null,
    table(rows.slice().reverse(), file.columns),
  ];
}

function renderYearly(file, rows) {
  const [yearCol, ...cols] = file.columns;
  const config = {
    type: 'bar',
    data: { labels: rows.map(r => r[yearCol]), datasets: cols.map((c, i) => ({
      label: c, data: rows.map(r => r[c]), backgroundColor: series(i), borderRadius: 4, borderSkipped: 'start' })) },
    options: {
      plugins: { tooltip: { callbacks: { label: c => `${c.dataset.label}: ${pct(c.raw)}` } } },
      scales: { x: { grid: { display: false } }, y: { ticks: { callback: v => pct(v) } } },
    },
  };
  const pctRows = rows.map(r => Object.fromEntries(Object.entries(r).map(([k, v]) => [k, k === yearCol ? v : pct(v)])));
  return [chartCard('Return per calendar year', config, true), table(pctRows, file.columns, { pageSize: 50 })];
}

function renderAccounts() {
  const accounts = manifest.accounts || {};
  const names = Object.keys(accounts);
  if (!names.length) return [h('p', { class: 'muted' }, 'No paper accounts yet.')];
  return [table(names.map(n => ({ strategy: n, ...accounts[n] })), ['strategy', ...new Set(names.flatMap(n => Object.keys(accounts[n])))], { pageSize: 50 })];
}

const RENDER = { day: renderDay, notes: renderNotes, stock: renderStock, trades: renderTrades, series: renderSeries,
  cday: renderCommodityDay, contract: renderStock,
  yearly: renderYearly, table: (f, rows) => [table(rows, f.columns)] };

async function show(path) {
  charts.forEach(c => c.destroy()); charts = [];
  document.querySelectorAll('nav a').forEach(a => a.classList.toggle('active', a.getAttribute('href') === '#' + path));
  $('#nav').classList.remove('open');
  const main = $('#main');
  window.scrollTo(0, 0);
  if (path === 'accounts') {
    main.replaceChildren(h('h2', {}, 'Paper accounts'), h('p', { class: 'sub' }, 'Saved account settings per strategy'), ...renderAccounts());
    return;
  }
  const file = manifest.files.find(f => f.path === path);
  if (!file) { main.replaceChildren(h('p', { class: 'muted' }, 'File not found: ' + path)); return; }
  main.replaceChildren(h('p', { class: 'muted' }, 'Loading…'));
  const rows = await load(path);
  const parts = await RENDER[file.kind](file, rows);
  main.replaceChildren(
    h('h2', {}, file.title),
    h('p', { class: 'sub' }, `${file.path} · ${file.rows.toLocaleString()} rows · `, h('a', { href: 'data/' + file.path, download: '' }, 'download CSV')),
    ...parts.filter(Boolean));
}

// ---------- navigation ----------

function buildNav() {
  const nav = $('#nav');
  const byGroup = g => manifest.files.filter(f => f.group === g);
  for (const g of manifest.groups) {
    let files = byGroup(g);
    if (g === 'Paper accounts' && Object.keys(manifest.accounts || {}).length) {
      nav.append(h('h2', {}, g), h('a', { href: '#accounts' }, 'Account settings'));
    } else if (files.length) nav.append(h('h2', {}, g));
    if (['Signals', 'LLM notes', 'Commodities'].includes(g)) files = files.slice().reverse();  // newest session first
    if (g === 'Reports') files = files.slice().sort((a, b) => b.path.localeCompare(a.path) || 0);
    if (g === 'Stocks') {
      // ~500 stocks: a search box instead of a long list.
      const list = h('datalist', { id: 'stock-list' }, files.map(f => h('option', { value: f.title })));
      const input = h('input', { type: 'search', placeholder: `Find a stock (${files.length})`, list: 'stock-list' });
      input.addEventListener('change', () => {
        const f = files.find(x => x.title.toUpperCase() === input.value.trim().toUpperCase());
        if (f) location.hash = f.path;
      });
      nav.append(input, list);
      continue;
    }
    for (const f of files) nav.append(h('a', { href: '#' + f.path, title: f.path }, f.title));
  }
}

function defaultPath() {
  const days = manifest.files.filter(f => f.kind === 'day');
  return days.length ? days[days.length - 1].path : manifest.files[0]?.path;
}

async function init() {
  $('#theme').addEventListener('click', () => {
    const dark = css('--page') === '#0d0d0d';
    document.documentElement.setAttribute('data-theme', dark ? 'light' : 'dark');
    try { localStorage.setItem('theme', dark ? 'light' : 'dark'); } catch (e) {}
    chartDefaults();
    show(decodeURIComponent(location.hash.slice(1)) || defaultPath());
  });
  $('#menu').addEventListener('click', () => $('#nav').classList.toggle('open'));
  chartDefaults();
  try {
    manifest = await (await fetch('data/manifest.json')).json();
  } catch (e) {
    $('#main').replaceChildren(h('p', {}, 'Could not load data/manifest.json. Serve this folder over HTTP (uv run aistock site --serve).'));
    return;
  }
  $('#built').textContent = 'Updated ' + new Date(manifest.built).toLocaleString();
  buildNav();
  window.addEventListener('hashchange', () => show(decodeURIComponent(location.hash.slice(1))));
  show(decodeURIComponent(location.hash.slice(1)) || defaultPath());
}

init();
