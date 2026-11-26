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

// ---------- URL state ----------

// The hash is "<path>?<view state>": chart range, log scale and each table's search, sort, page and filters,
// so a link reopens the same view. Updates use replaceState, which fires no hashchange.
let params = new URLSearchParams();
let tableSeq = 0;  // tables number themselves in drawing order; reset per page
const route = () => {
  const [p, q = ''] = location.hash.slice(1).split('?');
  return [decodeURIComponent(p), new URLSearchParams(q)];
};

// entries: [key, value]; a null value drops the key.
function setParams(entries) {
  for (const [k, v] of entries) v == null ? params.delete(k) : params.set(k, v);
  const path = location.hash.slice(1).split('?')[0], q = params.toString();
  history.replaceState(null, '', '#' + path + (q ? '?' + q : ''));
}
const setParam = (k, v) => setParams([[k, v]]);

// ---------- stars ----------

// Starred symbols, kept in this browser only (shown on Today and as a scan table filter).
const stars = new Set((() => {
  try { return JSON.parse(localStorage.getItem('stars')) || []; } catch (e) { return []; }
})());

function starButton(sym) {
  const b = h('button', { class: 'star' });
  const paint = () => {
    const on = stars.has(sym);
    b.textContent = on ? '★' : '☆';
    b.classList.toggle('on', on);
    b.title = on ? 'Unstar' : 'Star to follow on Today';
  };
  b.addEventListener('click', e => {
    e.preventDefault();
    stars.has(sym) ? stars.delete(sym) : stars.add(sym);
    try { localStorage.setItem('stars', JSON.stringify([...stars])); } catch (e) {}
    paint();
  });
  paint();
  return b;
}

// ---------- data ----------

async function load(path) {
  if (!cache.has(path)) {
    const text = await (await fetch('data/' + path)).text();
    const parsed = Papa.parse(text.trim(), { header: true, dynamicTyping: true, skipEmptyLines: true });
    cache.set(path, parsed.data);
  }
  return cache.get(path);
}
const optional = async path => manifest.files.some(f => f.path === path) ? await load(path) : [];

const num = v => typeof v === 'number' && isFinite(v);
const values = (rows, col) => rows.map(r => r[col]).filter(num);
const mean = xs => xs.length ? xs.reduce((a, b) => a + b, 0) / xs.length : NaN;

// Columns holding fractions (0.05 = 5%) are shown as percents.
const PCT = ['ret', 'rate', 'cagr', 'drawdown', 'return', 'excess', 'avg_trade', 'median_trade',
  'exposure', 'exit_stop', 'exit_target', 'exit_time', 'profitable', 'beat', 'buy_hold', 'growth', 'margin'];
const isPct = col => PCT.some(p => col.toLowerCase().includes(p));

function fmt(v, col = '') {
  if (v == null || v === '') return '';
  if (!num(v)) return String(v);
  if (isPct(col)) return (v * 100).toFixed(Math.abs(v) < 0.1 ? 2 : 1) + '%';
  if (Number.isInteger(v)) return v.toLocaleString('en-IN');
  return Math.abs(v) >= 100 ? v.toLocaleString('en-IN', { maximumFractionDigits: 2 }) : +v.toFixed(4) + '';
}
const pct = v => num(v) ? (v * 100).toFixed(1) + '%' : '–';

// Large numbers in Indian units: Cr = 1e7, L = 1e5, K = 1e3 (also for amounts reported in USD).
function compact(v) {
  const [n, u] = [[1e7, 'Cr'], [1e5, 'L'], [1e3, 'K']].find(([n]) => Math.abs(v) >= n) || [1, ''];
  return (v / n).toLocaleString('en-IN', { maximumFractionDigits: 2 }) + (u ? ' ' + u : '');
}

// History columns shown compact: 'count' (no currency) or 'money' (in the row's `currency`, else INR).
const AMOUNTS = { Volume: 'count', vol_avg20: 'count', shares: 'count', marketCap: 'money', revenue: 'money',
  net_income: 'money', equity: 'money', debt: 'money', current_assets: 'money', current_liabilities: 'money' };
const SYMBOL = { INR: '₹', USD: '$' };

function amount(v, c, r) {
  if (!AMOUNTS[c] || !num(v)) return fmt(v, c);
  if (AMOUNTS[c] === 'count') return compact(v);
  const cur = r.currency || 'INR';
  return (v < 0 ? '-' : '') + (SYMBOL[cur] ?? cur + ' ') + compact(Math.abs(v));
}

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

// ---------- reasons ----------

// *_reasons cells hold " | "-joined reasons (journal.REASONS_SEP), drawn as chips toned by what they say:
// fundamental reasons end in "(good)"/"(weak)" (fundamental/score.py), technical ones name the trend,
// RSI and MACD (technical/indicators.py). News reasons are a count summary plus scored headlines
// (sentiment/finbert.py), which fold away behind the counts.
const BULLISH = /^(uptrend|MACD histogram positive)/, BEARISH = /^(downtrend|MACD histogram negative|RSI overbought|news veto)/;
const NEWS_SUMMARY = /^(\d+) headlines: (\d+) positive, (\d+) negative, (\d+) neutral$/;
const HEADLINE = /^([+-]\d+(?:\.\d+)?) (\S*) (.*?)(?: \(([^()]*)\))?$/;

// The figures behind a technical reason, from the session's bar in the price history (stock pages only).
function withFigures(text, bar) {
  if (!bar) return text;
  const { Close, sma50, sma200, macd_hist, Volume, vol_avg20 } = bar;
  const trend = text.match(/^(uptrend|downtrend):/);
  if (trend && [Close, sma50, sma200].every(num)) {
    const op = trend[1] === 'uptrend' ? ' > ' : ' < ';
    return `${trend[1]}: close ${fmt(Close)}${op}SMA50 ${fmt(sma50)}${op}SMA200 ${fmt(sma200)}`;
  }
  if (text.startsWith('MACD histogram') && num(macd_hist)) return `${text} (${macd_hist > 0 ? '+' : ''}${macd_hist.toFixed(2)})`;
  if (text.startsWith('volume spike') && num(Volume) && vol_avg20 > 0) return `${text} (${(Volume / vol_avg20).toFixed(1)}×)`;
  return text;
}

function reasonChip(text, bar) {
  const m = text.match(/^(.*) \((good|weak)\)$/);
  const tone = m ? (m[2] === 'good' ? 'pos' : 'neg') : BULLISH.test(text) ? 'pos' : BEARISH.test(text) ? 'neg' : '';
  return h('span', { class: 'chip ' + tone }, m ? m[1] : withFigures(text, bar));
}

function headlineRow(text) {
  const m = text.match(HEADLINE);
  if (!m) return h('div', { class: 'headline' }, text);
  const [, score, date, title, source] = m;
  return h('div', { class: 'headline' }, h('span', { class: 'chip ' + sign(+score) }, score),
    h('a', { href: 'https://news.google.com/search?q=' + encodeURIComponent(`"${title}"`), target: '_blank', rel: 'noopener' }, title),
    h('div', { class: 'cell-sub' }, [date, source].filter(Boolean).join(' · ')));
}

// bar: the session's price-history row, to put numbers on technical reasons.
function reasonsCell(col, value, bar) {
  const [first, ...rest] = value.split(' | ');
  const counts = col === 'sentiment_reasons' && first.match(NEWS_SUMMARY);
  if (!counts) return h('div', { class: 'chips' }, [first, ...rest].map(t => reasonChip(t, bar)));
  const [, total, pos, neg, neutral] = counts.map(Number);
  const chips = [h('span', { class: 'chip pos' }, `▲ ${pos} positive`), h('span', { class: 'chip neg' }, `▼ ${neg} negative`),
    h('span', { class: 'chip' }, `${neutral} neutral`)];
  return rest.length ? h('details', {}, h('summary', {}, ...chips, h('span', { class: 'muted' }, `${total} headlines`)),
    rest.map(headlineRow)) : h('div', { class: 'chips' }, chips);
}

// ---------- table ----------

// Numbered pages (0-based `page`): ‹ Prev 1 … 4 5 6 … 20 Next ›; go(p) shows page p.
function pager(page, pages, go) {
  if (pages <= 1) return null;
  const btn = (label, p, cls = '') => {
    const b = h('button', { class: 'page ' + cls }, label);
    if (p === page || p < 0 || p >= pages) b.disabled = true;
    else b.addEventListener('click', () => go(p));
    return b;
  };
  const nums = [...new Set([0, page - 1, page, page + 1, pages - 1])].filter(p => p >= 0 && p < pages).sort((a, b) => a - b);
  return h('div', { class: 'pager' }, btn('‹ Prev', page - 1),
    nums.flatMap((p, i) => [i && p - nums[i - 1] > 1 ? h('span', { class: 'muted' }, '…') : null,
      btn(String(p + 1), p, p === page ? 'current' : '')]),
    btn('Next ›', page + 1));
}

// Bring a paged box's top back into view when a new page is shorter or it scrolled out above.
const keepInView = el => { if (el.getBoundingClientRect().top < 0) el.scrollIntoView({ block: 'start' }); };

const PAGE_SIZES = [10, 25, 50, 100];

function table(rows, columns, opts = {}) {
  // labels: header text per column; cells: custom renderer (value, row) -> node; extra: columns hidden until 'All columns';
  // sortKeys: (value, row) -> number to sort a column by instead of its raw value
  // pageSize: rows per page (the viewer can change it)
  // more: extra filters [{ key, def, control: (value, set) -> node, test: (row, value) -> bool }], values as strings
  // The view is kept in the URL under this table's number (see setParams), so a link reopens it.
  const { filters = [], link = {}, pageSize = 10, labels = {}, cells = {}, extra = [], sortKeys = {}, more = [] } = opts;
  const key = 't' + tableSeq++ + '.';
  const defaults = { q: '', sort: opts.sort || '', asc: opts.asc ? '1' : '0', page: '1', size: String(pageSize), all: '0',
    ...Object.fromEntries(filters.map(f => ['f.' + f, ''])), ...Object.fromEntries(more.map(m => [m.key, m.def])) };
  const saved = k => params.get(key + k) ?? defaults[k];
  let sortCol = saved('sort') || null, asc = saved('asc') === '1', query = saved('q'), page = Math.max(0, +saved('page') - 1 || 0),
    size = +saved('size') || pageSize, showAll = saved('all') === '1';
  const active = Object.fromEntries(filters.map(f => [f, saved('f.' + f)]));
  const chosen = Object.fromEntries(more.map(m => [m.key, saved(m.key)]));
  const numeric = Object.fromEntries(columns.map(c => [c, rows.some(r => num(r[c]))]));
  const descFirst = c => numeric[c] || c in sortKeys;
  const save = () => {
    const now = { q: query, sort: sortCol || '', asc: asc ? '1' : '0', page: String(page + 1), size: String(size),
      all: showAll ? '1' : '0', ...Object.fromEntries(filters.map(f => ['f.' + f, active[f]])), ...chosen };
    setParams(Object.entries(now).map(([k, v]) => [key + k, v === defaults[k] ? null : v]));
  };

  const search = h('input', { type: 'search', placeholder: 'Search…', value: query });
  search.addEventListener('input', () => { query = search.value.toLowerCase(); page = 0; draw(); });
  const selects = filters.map(f => {
    const opts = [...new Set(rows.map(r => r[f]).filter(v => v != null && v !== ''))].sort();
    const s = h('select', {}, h('option', { value: '' }, 'All ' + (labels[f] || f).toLowerCase()), opts.map(o => h('option', { value: o }, o)));
    s.value = active[f];
    s.addEventListener('change', () => { active[f] = s.value; page = 0; draw(); });
    return s;
  });
  const extras = more.map(m => m.control(chosen[m.key], v => { chosen[m.key] = v; page = 0; draw(); }));
  const sortBy = h('select', { title: 'Sort by' }, h('option', { value: '' }, 'Sort: file order'),
    columns.map(c => h('option', { value: c }, 'Sort: ' + (labels[c] || c))));
  sortBy.addEventListener('change', () => { sortCol = sortBy.value || null; asc = !descFirst(sortCol); page = 0; draw(); });
  const dir = h('button', { class: 'link', title: 'Flip sort direction' });
  dir.addEventListener('click', () => { asc = !asc; page = 0; draw(); });
  const toggle = extra.length ? h('input', { type: 'checkbox' }) : null;
  if (toggle) toggle.checked = showAll;
  toggle?.addEventListener('change', () => { showAll = toggle.checked; draw(); });
  const count = h('span', { class: 'muted' });
  const tbody = h('tbody'), headRow = h('tr');
  const ths = Object.fromEntries(columns.map(c => {
    const th = h('th', { class: numeric[c] ? 'num' : '', title: c });
    th.addEventListener('click', () => { asc = sortCol === c ? !asc : !descFirst(c); sortCol = c; page = 0; draw(); });
    return [c, th];
  }));
  const sizes = h('select', { title: 'Rows per page' },
    [...new Set([...PAGE_SIZES, pageSize])].sort((a, b) => a - b).map(n => h('option', { value: n }, n + ' / page')));
  sizes.value = size;
  sizes.addEventListener('change', () => { size = +sizes.value; page = 0; draw(); });
  const pages = h('div');

  function draw() {
    let out = rows.filter(r =>
      filters.every(f => !active[f] || String(r[f]) === active[f]) &&
      more.every(m => m.test(r, chosen[m.key])) &&
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
    const last = Math.max(0, Math.ceil(out.length / size) - 1);
    page = Math.min(page, last);
    tbody.replaceChildren(...out.slice(page * size, (page + 1) * size).map(r => h('tr', {}, visible.map(c => {
      const v = r[c];
      const cls = (numeric[c] ? 'num ' : '') + (isPct(c) ? sign(v) : '');
      if (cells[c]) return h('td', { class: cls + (c.endsWith('_reasons') ? ' reasons' : '') }, cells[c](v, r));
      const content = link[c] && v ? h('a', { href: link[c](v) }, String(v))
        : c === 'action' && v ? h('span', { class: 'badge ' + v }, v) : fmt(v, c);
      if (c.endsWith('_reasons') && typeof v === 'string') return h('td', { class: cls + ' reasons' }, reasonsCell(c, v));
      const long = typeof v === 'string' && v.length > 60;
      return h('td', long ? { class: cls + ' clip', title: v } : { class: cls }, content);
    }))));
    const from = out.length ? page * size + 1 : 0, to = Math.min(out.length, (page + 1) * size);
    count.textContent = `${from.toLocaleString()}–${to.toLocaleString()} of ${out.length.toLocaleString()}` +
      (out.length < rows.length ? ` (filtered from ${rows.length.toLocaleString()})` : '') + ' rows';
    pages.replaceChildren(pager(page, last + 1, p => { page = p; draw(); keepInView(box); }) || '');
    save();
  }
  const box = card(null, h('div', { class: 'controls' }, search, selects, extras, sortBy, dir,
      toggle ? h('label', {}, toggle, 'All columns') : null, sizes, count),
    h('div', { class: 'table-wrap' }, h('table', {}, h('thead', {}, headRow), tbody)),
    pages);
  draw();
  return box;
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
const SCAN_COLS = ['symbol', 'sector', 'action', 'buys', 'combined', 'change', 'technical', 'fundamental', 'sentiment',
  'close', 'atr', 'rs_3m', 'rs_6m', 'sector_up', 'f_marketCap', 'technical_reasons'];
const SCAN_LABELS = { symbol: 'Stock', sector: 'Sector', action: 'Action', buys: 'BUY strategies', combined: 'Combined',
  change: 'Δ prev',
  technical: 'Tech', fundamental: 'Fund', sentiment: 'News', close: 'Close', atr: 'ATR', rs_3m: 'RS 3m', rs_6m: 'RS 6m',
  sector_up: 'Sector trend', f_marketCap: 'Mkt cap', technical_reasons: 'Technical reasons' };
const dash = () => h('span', { class: 'muted' }, '–');

// A [-1, 1] score as a bar growing left or right of zero, plus the value.
const scoreCell = v => num(v) ? h('span', { class: 'score-cell' },
  h('span', { class: 'score-bar' }, h('span', { class: sign(v),
    style: `width:${Math.min(1, Math.abs(v)) * 50}%;${v >= 0 ? 'left' : 'right'}:50%` })),
  h('span', { class: sign(v) }, (v > 0 ? '+' : '') + v.toFixed(2))) : dash();

const signed = v => num(v) ? h('span', { class: sign(v) }, (v > 0 ? '+' : '') + v.toFixed(2)) : dash();

const SCAN_CELLS = {
  symbol: (v, r) => h('div', {}, starButton(v), h('a', { href: stockHref(v) }, v), r.name ? h('div', { class: 'cell-sub' }, r.name) : null),
  // fresh: a BUY that wasn't one in the previous session (withChanges)
  action: (v, r) => v ? h('span', {}, h('span', { class: 'badge ' + v }, v),
    r.fresh ? h('span', { class: 'badge new', title: 'Not a BUY in the previous session' }, 'NEW') : null) : dash(),
  change: signed,
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

// Scan table filters beyond action and sector: one BUY strategy, a minimum combined score, starred stocks.
function scanFilters(rows) {
  const tags = r => r.buys ? String(r.buys).split(';') : [];
  const strategies = [...new Set(rows.flatMap(tags))].sort();
  const strategy = {
    key: 'strategy', def: '', test: (r, v) => !v || tags(r).includes(v),
    control: (v, set) => {
      const s = h('select', { title: 'BUY strategy' }, h('option', { value: '' }, 'All strategies'),
        strategies.map(o => h('option', { value: o }, o)));
      s.value = v;
      s.addEventListener('change', () => set(s.value));
      return s;
    },
  };
  const min = {
    key: 'min', def: '-1', test: (r, v) => +v <= -1 || (num(r.combined) && r.combined >= +v),
    control: (v, set) => {
      const s = h('input', { type: 'range', min: -1, max: 1, step: 0.05, value: v, title: 'Minimum combined score' });
      const out = h('span', { class: 'range-value' });
      const label = () => { out.textContent = 'Combined ≥ ' + (+s.value <= -1 ? 'any' : (+s.value > 0 ? '+' : '') + (+s.value).toFixed(2)); };
      s.addEventListener('input', () => { label(); set(s.value); });
      label();
      return h('label', {}, s, out);
    },
  };
  const starred = {
    key: 'starred', def: '0', test: (r, v) => v !== '1' || stars.has(r.symbol),
    control: (v, set) => {
      const c = h('input', { type: 'checkbox' });
      c.checked = v === '1';
      c.addEventListener('change', () => set(c.checked ? '1' : '0'));
      return h('label', {}, c, '★ Starred');
    },
  };
  return [strategies.length ? strategy : null, min, starred].filter(Boolean);
}

function scanTable(rows, columns) {
  const main = SCAN_COLS.filter(c => columns.includes(c));
  const extra = columns.filter(c => !main.includes(c));
  return table(rows, [...main, ...extra], { filters: ['action', 'sector'], more: scanFilters(rows), sort: 'combined',
    labels: SCAN_LABELS, cells: SCAN_CELLS, sortKeys: SCAN_SORT, extra, link: { symbol: stockHref } });
}

// ---------- session changes ----------

// The stock session before `path` (the manifest lists day files by date).
function previousDay(path) {
  const days = manifest.files.filter(f => f.kind === 'day');
  const i = days.findIndex(f => f.path === path);
  return i > 0 ? days[i - 1] : null;
}

// Copies of a session's rows with `change` (combined score minus the previous session's) and `fresh`
// (a BUY that wasn't one in the previous session).
function withChanges(rows, prevRows) {
  if (!prevRows) return rows;
  const before = new Map(prevRows.map(r => [r.symbol, r]));
  return rows.map(r => {
    const p = before.get(r.symbol);
    return { ...r, change: num(r.combined) && num(p?.combined) ? r.combined - p.combined : null,
      fresh: r.action === 'BUY' && p?.action !== 'BUY' };
  });
}

// New BUYs, BUYs gone, and the largest combined score moves since the previous session.
function changesCard(rows, prevRows, prevDate) {
  const now = new Map(rows.map(r => [r.symbol, r]));
  const dropped = prevRows.filter(p => p.action === 'BUY' && now.get(p.symbol)?.action !== 'BUY');
  const moved = rows.filter(r => num(r.change)).sort((a, b) => b.change - a.change);
  const up = moved.slice(0, 8).filter(r => r.change > 0), down = moved.slice(-8).reverse().filter(r => r.change < 0);
  const chip = (sym, text, tone) => h('a', { class: 'chip ' + tone, href: stockHref(sym) }, text);
  const group = (title, items) => h('div', { class: 'change-group' }, h('div', { class: 'label' }, `${title} (${items.length})`),
    items.length ? h('div', { class: 'chips' }, items) : dash());
  return card(`Changes since ${prevDate}`,
    group('New BUYs', rows.filter(r => r.fresh).map(r => chip(r.symbol, r.symbol, 'pos'))),
    group('No longer BUY', dropped.map(p => chip(p.symbol, `${p.symbol} → ${now.get(p.symbol)?.action || 'not scanned'}`, 'neg'))),
    group('Combined score up most', up.map(r => chip(r.symbol, `${r.symbol} +${r.change.toFixed(2)}`, 'pos'))),
    group('Combined score down most', down.map(r => chip(r.symbol, `${r.symbol} ${r.change.toFixed(2)}`, 'neg'))));
}

// LLM notes explain verdicts the rules already reached (stock BUYs; every commodity); they never pick trades.
// days: journal rows (keyed by date and symbol), for every strategy entering and the combined score.
// entries: day row -> [[strategy, action]]; href: symbol -> link.
const stockEntries = day => day.buys ? String(day.buys).split(';').map(b => [b, 'BUY']) : [];

const NOTES_PER_PAGE = 5;

// A note as one bullet per sentence. Splits only after two non-dot characters, so initials
// ("K.P.R. Mill") and decimals ("1110.96") stay whole.
const SENTENCE = /(?<=[A-Za-z0-9)%'"]{2}[.!?])\s+(?=[A-Z'"])/;
// Terms colored by kind so they're easy to find: figures (prices, ratios, %), technical indicators,
// fundamental ratios, news/sentiment. Group order is the class order in NOTE_TERMS.
const NOTE_TERMS = [['fig', 'Figures'], ['tech', 'Technical'], ['fund', 'Fundamental'], ['news', 'News']];
const TERM = new RegExp([
  /(\d[\d,]*\.\d+%?x?|\d+%)/,
  /(\b\d+-(?:day|period|session|week|bar)\b|\b(?:(?:simple|exponential) )?moving averages?(?: \((?:SMA|EMA)s?\))?|\bAverage True Range(?: \(ATR\))?|\b(?:SMAs?|EMAs?|MACD(?: histogram| line)?|RSI|ATR|ADX|OBV|VWAP|Bollinger Bands?|Donchian(?: channel)?|stochastic|golden cross|crossover|relative strength|uptrend|downtrend|breakout|momentum|volume(?: spikes?)?|overbought|oversold|support|resistance)\b)/,
  /(\b(?:ROE|ROCE|ROA|EPS|P\/E|P\/B|PEG|D\/E|Debt\/Equity|debt-to-equity|price-to-(?:earnings|book)|(?:net|operating|profit|gross) margins?|revenue(?: growth)?|earnings(?: growth)?|dividend yield|market cap(?:italization)?|book value|free cash flow|valuation)\b)/,
  /(\b(?:news|sentiment|headlines?|(?:buy|sell|hold) rating|ratings?|upgrade[ds]?|downgrade[ds]?|analysts?)\b)/,
].map(r => r.source).join('|'), 'gi');
const highlight = text => {
  const parts = [];
  let at = 0;
  for (const m of text.matchAll(TERM)) {
    const kind = NOTE_TERMS[m.slice(1).findIndex(g => g !== undefined)][0];
    parts.push(text.slice(at, m.index), h('span', { class: 'term ' + kind }, m[0]));
    at = m.index + m[0].length;
  }
  return [...parts, text.slice(at)];
};
// A sentence about risk is drawn wholly in red (terms stay bold).
const RISK = /\b(?:risks?|risky|downside|caution|concerns?)\b/i;
const noteBody = text => h('ul', { class: 'note-body' },
  String(text || '').split(SENTENCE).map(s => h('li', RISK.test(s) ? { class: 'risk' } : {}, highlight(s))));
const noteLegend = () => h('p', { class: 'sub note-legend' },
  NOTE_TERMS.map(([k, label]) => h('span', { class: 'term ' + k }, label)), h('span', { class: 'term risk' }, 'Risk'));

function notesCard(rows, title, days, entries = stockEntries, href = stockHref, perDate = false) {
  const key = r => r.date + ' ' + r.symbol;
  const byKey = new Map(days.map(d => [key(d), d]));
  const list = h('div'), pages = h('div');
  const box = card(title,
    h('p', { class: 'sub' }, `Written by a local LLM (qwen2.5:3b) from the signal's numbers; the rules made the decision. ` +
      `${rows.length} notes.`), noteLegend(), list, pages);
  const show = page => {
    list.replaceChildren(...rows.slice(page * NOTES_PER_PAGE, (page + 1) * NOTES_PER_PAGE).map(note));
    pages.replaceChildren(pager(page, Math.ceil(rows.length / NOTES_PER_PAGE), p => { show(p); keepInView(box); }) || '');
  };
  const note = r => {
    const day = byKey.get(key(r)) || {};
    const tags = entries(day);
    const buys = tags.length ? tags : [[r.strategy, r.action || 'BUY']].filter(t => t[0]);
    return h('div', { class: 'note' },
      h('div', { class: 'note-head' }, h('a', { href: href(r.symbol) }, r.symbol), ' ',
        perDate ? h('span', { class: 'muted' }, r.date + ' · ') : null,
        h('span', { class: 'muted' }, r.name || ''),
        day.action && day.side ? h('span', {}, h('span', { class: 'badge ' + day.action }, day.action), ' ', sideBadge(day.side)) : null,
        num(day.combined) ? h('span', { class: 'score ' + (day.combined >= 0 ? 'pos' : 'neg') },
          'combined ' + (day.combined >= 0 ? '+' : '') + day.combined.toFixed(2)) : null,
        buys.map(([b, a]) => h('span', { class: 'badge ' + (b === r.strategy ? a : 'tag'),
          title: b === r.strategy ? 'The strategy this note explains' : 'Also says ' + a }, b)),
        !r.strategy && r.action ? h('span', { class: 'badge ' + r.action, title: 'No strategy has a position or an entry' }, r.action) : null,
        r.source === 'backfill' ? h('span', { class: 'badge', title: 'Written later from the journal row; fundamental and news reasons were not saved for this session' }, 'backfilled') : null),
      noteBody(r.note));
  };
  show(0);
  return box;
}

async function renderNotes(file, rows) {
  const dayPath = 'journal/days/' + file.path.split('/').pop();
  const days = manifest.files.some(f => f.path === dayPath) ? await load(dayPath) : [];
  return [notesCard(rows, null, days)];
}

// A session's LLM notes (also under the LLM notes group) are drawn under its scan table.
async function renderDay(file, rows) {
  const notes = await optional('journal/notes/' + file.path.split('/').pop());
  const prevFile = previousDay(file.path);
  const prevRows = prevFile ? await load(prevFile.path) : null;
  rows = withChanges(rows, prevRows);
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
    prevRows ? changesCard(rows, prevRows, prevFile.title) : null,
    scanTable(rows, prevRows ? [...file.columns, 'change'] : file.columns),
    notes.length ? notesCard(notes, 'LLM notes', rows) : null,
  ];
}

// ---------- today ----------

// Landing page: the latest session's BUYs and changes, the paper accounts, and starred stocks.
async function renderToday() {
  const days = manifest.files.filter(f => f.kind === 'day');
  const file = days[days.length - 1];
  if (!file) return [h('p', { class: 'muted' }, 'No sessions logged yet.')];
  const prevFile = previousDay(file.path);
  const prevRows = prevFile ? await load(prevFile.path) : null;
  const rows = withChanges(await load(file.path), prevRows);
  const columns = prevRows ? [...file.columns, 'change'] : file.columns;
  const buys = rows.filter(r => r.action === 'BUY'), starred = rows.filter(r => stars.has(r.symbol));
  const accounts = await loadAccounts();
  const open = accounts.reduce((n, a) => n + a.positions.length, 0);
  const exits = accounts.flatMap(a => a.trades.filter(t => String(t.exit_date) === file.title));
  const cdays = manifest.files.filter(f => f.kind === 'cday'), cday = cdays[cdays.length - 1];
  return [
    h('p', { class: 'sub' }, 'Latest stock session ', h('a', { href: '#' + file.path }, file.title),
      prevFile ? ` · compared with ${prevFile.title}` : '',
      cday ? [' · commodities ', h('a', { href: '#' + cday.path }, cday.title)] : null),
    tiles([['BUY signals', buys.length.toLocaleString(), 'pos'],
      ['New BUYs', rows.filter(r => r.fresh).length.toLocaleString(), 'pos'],
      ['WATCH', rows.filter(r => r.action === 'WATCH').length.toLocaleString()],
      ['Stocks scanned', rows.length.toLocaleString()],
      ...(accounts.length ? [['Open paper positions', open.toLocaleString()],
        ['Paper exits this session', exits.length.toLocaleString(), exits.length ? 'neg' : '']] : [])]),
    prevRows ? changesCard(rows, prevRows, prevFile.title) : null,
    h('h3', { class: 'section' }, `BUY signals (${buys.length})`),
    buys.length ? scanTable(buys, columns) : h('p', { class: 'muted' }, 'No BUY signals in this session.'),
    accounts.length ? [h('h3', { class: 'section' }, 'Paper accounts'),
      h('p', { class: 'sub' }, h('a', { href: '#accounts' }, 'Equity curves, open positions and trades')),
      accountsTable(accounts)] : null,
    h('h3', { class: 'section' }, `Starred (${starred.length})`),
    starred.length ? scanTable(starred, columns)
      : h('p', { class: 'muted' }, 'Star stocks (☆) in a scan table or on their page to follow them here. Stars are kept in this browser.'),
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
  return h('span', { class: 'badge ' + cls, title: `${cls} ${side}` }, `${name} · ${side}`);
}) : dash();

const COMMODITY_COLS = ['symbol', 'group', 'action', 'buys', 'sells', 'exits', 'holds', 'combined', 'technical',
  'sentiment', 'close', 'atr', 'close_usd', 'usdinr', 'technical_reasons', 'sentiment_reasons'];
const COMMODITY_LABELS = { symbol: 'Contract', group: 'Group', action: 'Action', buys: 'BUY (long)',
  sells: 'SELL (short)', exits: 'EXIT', holds: 'HOLD', combined: 'Combined', technical: 'Tech', sentiment: 'News',
  close: 'Close ₹', atr: 'ATR ₹', close_usd: 'Close USD', usdinr: 'USDINR', technical_reasons: 'Technical reasons',
  sentiment_reasons: 'News reasons' };
// Direction of a commodity verdict (journal "side" column: long, short, mixed or blank for WATCH).
const sideBadge = v => v === 'long' || v === 'short' ? h('span', { class: 'badge ' + v }, v.toUpperCase())
  : v ? h('span', { class: 'badge MIXED' }, String(v).toUpperCase()) : null;

const COMMODITY_CELLS = {
  action: (v, r) => h('span', {}, h('span', { class: 'badge ' + v }, v), ' ', sideBadge(r.side)),
  symbol: (v, r) => h('div', {}, h('a', { href: contractHref(v) }, v), r.name ? h('div', { class: 'cell-sub' }, r.name) : null),
  buys: v => v ? String(v).split(';').map(b => h('span', { class: 'badge BUY' }, b)) : dash(),
  sells: v => v ? String(v).split(';').map(b => h('span', { class: 'badge SELL' }, b)) : dash(),
  exits: v => sideTags(v, 'EXIT'), holds: v => sideTags(v, 'HOLD'),
  combined: scoreCell, technical: scoreCell, sentiment: scoreCell,
  close: (v, r) => num(v) ? h('div', {}, fmt(v), r.unit ? h('div', { class: 'cell-sub' }, 'per ' + r.unit) : null) : dash(),
};

// Every headline behind the session's news scores, strongest first; one contract at a time via the filter.
const HEADLINE_LABELS = { symbol: 'Contract', published: 'Published (UTC)', score: 'Score', weight: 'Weight',
  title: 'Headline', source: 'Source' };
const HEADLINE_CELLS = {
  score: scoreCell,
  // PapaParse's dynamicTyping turns ISO timestamps into Dates
  published: v => v instanceof Date ? v.toISOString().slice(0, 16).replace('T', ' ') : v ? String(v) : dash(),
  // the journal keeps no links (Google News URLs are long), so search the exact title
  title: v => h('a', { href: 'https://news.google.com/search?q=' + encodeURIComponent(`"${v}"`),
    target: '_blank', rel: 'noopener' }, v),
};

function headlinesCard(rows) {
  return [h('h3', { class: 'section' }, `Headlines (${rows.length})`),
    h('p', { class: 'sub' }, 'Score = FinBERT positive − negative; weight halves every 3 days. ' +
      'The news score is the weighted average.'),
    table(rows, ['symbol', 'score', 'weight', 'published', 'title', 'source'], { filters: ['symbol'],
      sort: 'score', labels: HEADLINE_LABELS, cells: HEADLINE_CELLS,
      sortKeys: { score: v => num(v) ? Math.abs(v) : v } })];
}

// A session's commodity notes (~one per contract) and headlines are drawn under its scan table.
async function renderCommodityDay(file, rows) {
  const day = file.path.split('/').pop();
  const notes = await optional('journal/commodities/notes/' + day);
  const headlines = await optional('journal/commodities/headlines/' + day);
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
    ...(headlines.length ? headlinesCard(headlines) : []),
  ];
}

function lineConfig(labels, datasets, opts = {}) {
  return {
    type: 'line',
    data: { labels, datasets: datasets.map(d => ({ pointRadius: 0, pointHoverRadius: 4, borderWidth: 2, spanGaps: false, ...d })) },
    options: {
      plugins: { legend: { display: datasets.length > 1 },
        // a dataset's optional `notes` add text per point (e.g. which paper trade exited there)
        tooltip: { callbacks: { label: c => `${c.dataset.label}: ${opts.pct ? pct(c.raw) : fmt(c.raw)}` +
          (c.dataset.notes?.[c.dataIndex] ? ' · ' + c.dataset.notes[c.dataIndex] : '') } } },
      scales: {
        x: { ticks: { maxTicksLimit: 10, maxRotation: 0 }, grid: { display: false } },
        y: { type: opts.log ? 'logarithmic' : 'linear', min: opts.min, max: opts.max,
          ticks: opts.pct ? { callback: v => pct(v) } : {} },
      },
    },
  };
}

// Full close, technical and estimated fundamental score history (data/prices/, written at build time), with
// logged BUYs and the logged fundamental scores at their sessions; range buttons trim both charts.
// [label, how far back (n units of 'day' or 'month', 0 = all), bucket]: longer ranges plot one point per
// week or month, or the daily technical score (which flips between a few levels) is unreadable. Close and
// fundamental take the bucket's last value, technical its average.
const RANGES = [['1W', 7, 'day', 'day'], ['1M', 1, 'month', 'day'], ['3M', 3, 'month', 'day'], ['6M', 6, 'month', 'day'],
  ['1Y', 12, 'month', 'day'], ['5Y', 60, 'month', 'week'], ['Max', 0, 'month', 'month']];
const BUCKET = {
  day: d => d,
  week: d => { const t = new Date(d + 'T00:00:00Z'); t.setUTCDate(t.getUTCDate() - (t.getUTCDay() + 6) % 7); return t.toISOString().slice(0, 10); },
  month: d => d.slice(0, 7),
};

function bucketed(pts, key) {
  const groups = new Map();
  pts.forEach(p => { const k = key(p.Date); groups.has(k) ? groups.get(k).push(p) : groups.set(k, [p]); });
  return [...groups.values()].map(g => {
    const tech = g.map(p => p.Technical).filter(num);
    return { ...g[g.length - 1], Technical: tech.length ? mean(tech) : null };
  });
}

// Stop and target of the latest logged BUY per trading day, from its session until a close crosses either or the
// next BUY. Closes only, so it approximates the intraday exits of the backtest; an unbounded target (inf) is left out.
function tradeLevels(history, rows) {
  const buys = new Map(rows.filter(r => r.action === 'BUY' && num(r.stop)).map(r => [String(r.date), r]));
  const out = new Map();
  let open = null;
  for (const b of history) {
    const d = String(b.Date);
    if (buys.has(d)) open = buys.get(d);
    if (!open) continue;
    out.set(d, { stop: open.stop, target: num(open.target) ? open.target : null });
    if (b.Close <= open.stop || (num(open.target) && b.Close >= open.target)) open = null;
  }
  return out;
}

// paper: this symbol's closed paper-account trades (with `strategy`), marked at their exits.
function historyCard(history, rows, paper = []) {
  const buys = new Map(rows.filter(r => r.action === 'BUY').map(r => [r.date, r.close]));
  const fund = new Map(rows.filter(r => num(r.fundamental)).map(r => [r.date, r.fundamental]));
  const levels = tradeLevels(history, rows);
  const priceBox = h('div', { class: 'chart-box tall' }), scoreBox = h('div', { class: 'chart-box' });
  const draw = (n, unit, bucket) => {
    const mine = charts.filter(c => [priceBox, scoreBox].includes(c.canvas.parentNode));  // destroy() nulls c.canvas, so split first
    charts = charts.filter(c => !mine.includes(c));
    mine.forEach(c => c.destroy());
    priceBox.replaceChildren(); scoreBox.replaceChildren();
    const start = new Date(history[history.length - 1].Date + 'T00:00:00Z');
    if (unit === 'day') start.setUTCDate(start.getUTCDate() - n); else start.setUTCMonth(start.getUTCMonth() - n);
    const from = n ? start.toISOString().slice(0, 10) : '';
    const key = BUCKET[bucket];
    const pts = bucketed(history.filter(p => String(p.Date) >= from), key);
    const labels = pts.map(p => p.Date);
    const inBucket = m => new Map([...m].map(([d, v]) => [key(d), v]));  // a BUY or logged score shows on its bucket's point
    const buysAt = inBucket(buys), fundAt = inBucket(fund);
    const prices = [{ label: 'Close', data: pts.map(p => p.Close), borderColor: series(0) }];
    if (buys.size) prices.push({ label: 'BUY', data: pts.map(p => buysAt.get(key(p.Date)) ?? null), showLine: false,
      pointRadius: 4, borderColor: series(1), backgroundColor: series(1) });
    // A bucket's point is its last day, so the levels in force at the end of the bucket.
    const level = side => pts.map(p => levels.get(String(p.Date))?.[side] ?? null);
    if (levels.size) prices.push(
      { label: 'Stop', data: level('stop'), stepped: true, borderWidth: 1.5, borderDash: [5, 4], borderColor: css('--critical') },
      { label: 'Target', data: level('target'), stepped: true, borderWidth: 1.5, borderDash: [5, 4], borderColor: css('--good') });
    if (paper.length) {
      const exits = new Map();
      for (const t of paper) {
        const k = key(String(t.exit_date)), e = exits.get(k) || { price: t.exit, notes: [] };
        e.notes.push(`${t.strategy} ${t.reason} ${(t.ret > 0 ? '+' : '') + pct(t.ret)}`);
        exits.set(k, e);
      }
      const at = pts.map(p => exits.get(key(p.Date)));
      prices.push({ label: 'Paper exit', data: at.map(e => e?.price ?? null), notes: at.map(e => e?.notes.join('; ')),
        showLine: false, pointRadius: 6, pointStyle: 'crossRot', borderWidth: 2, borderColor: series(6), backgroundColor: series(6) });
    }
    chart(priceBox, lineConfig(labels, prices));
    const scores = [{ label: bucket === 'day' ? 'technical' : `technical (${bucket}ly average)`, data: pts.map(p => p.Technical), borderColor: series(1), borderWidth: 1.5 }];
    if (pts.some(p => num(p.Fundamental))) scores.push({ label: 'fundamental (estimated from annual results)',
      data: pts.map(p => p.Fundamental), borderColor: series(2), borderWidth: 1.5, borderDash: [5, 4] });
    if (fund.size) scores.push({ label: 'fundamental (logged)', data: pts.map(p => fundAt.get(key(p.Date)) ?? null),
      spanGaps: true, pointRadius: 3, borderColor: series(3), backgroundColor: series(3) });
    chart(scoreBox, lineConfig(labels, scores, { min: -1, max: 1 }));
  };
  const btns = RANGES.map(([label, n, unit, bucket]) => {
    const b = h('button', { class: 'link' }, label);
    b.addEventListener('click', () => {
      btns.forEach(x => x.disabled = x === b);
      setParam('range', label === 'Max' ? null : label);
      draw(n, unit, bucket);
    });
    return b;
  });
  const found = RANGES.findIndex(r => r[0] === params.get('range'));
  const start = found >= 0 ? found : RANGES.length - 1;
  btns[start].disabled = true;
  requestAnimationFrame(() => draw(...RANGES[start].slice(1)));
  return card(`Price history (${history[0].Date} to ${history[history.length - 1].Date})`,
    h('div', { class: 'controls' }, btns), priceBox, h('h3', {}, 'Scores (−1 to 1)'), scoreBox);
}

// Full history tables (data/prices/ and data/fundamentals/, written at build time; see aistock/site/__init__.py).
const DAILY_LABELS = { Date: 'Date', Volume: 'Volume', sma20: 'SMA20', sma50: 'SMA50', sma200: 'SMA200', ema100: 'EMA100',
  ema200: 'EMA200', rsi14: 'RSI14', macd_hist: 'MACD hist', atr14: 'ATR14', vol_avg20: 'Avg vol 20', Technical: 'Tech score',
  trailingPE: 'P/E (est.)', priceToBook: 'P/B (est.)', marketCap: 'Mkt cap (est.)', Fundamental: 'Fund score (est.)' };
const DAILY_EXTRA = ['Open', 'High', 'Low', 'ema100', 'ema200', 'vol_avg20', 'priceToBook', 'marketCap'];
const YEAR_LABELS = { date: 'Fiscal year end', start: 'In effect from', currency: 'Currency', converted_from: 'Converted from',
  revenue: 'Revenue', net_income: 'Net income',
  eps: 'EPS', equity: 'Equity', debt: 'Debt', current_assets: 'Current assets', current_liabilities: 'Current liabilities',
  shares: 'Shares', returnOnEquity: 'ROE', debtToEquity: 'Debt/Equity (%)', revenueGrowth: 'Revenue growth',
  earningsGrowth: 'Earnings growth', profitMargins: 'Net margin', currentRatio: 'Current ratio', check: 'Data check' };

// Change from the previous row (trading day or fiscal year) under each value: percent for levels and
// amounts, a difference where a percent misleads: [scale, unit] (RSI is already 0-100, MACD hist crosses
// zero, scores are -1..1; ratios held as fractions change in percentage points, debtToEquity is in percent).
const POINT_CHANGE = { rsi14: [1, ' pt'], macd_hist: [1, ' pt'], Technical: [1, ' pt'], Fundamental: [1, ' pt'],
  returnOnEquity: [100, ' pp'], revenueGrowth: [100, ' pp'], earningsGrowth: [100, ' pp'], profitMargins: [100, ' pp'],
  debtToEquity: [1, ' pp'], currentRatio: [1, ''] };
const NO_CHANGE = ['Date', 'date', 'start', 'currency', 'converted_from', 'check'];

function changeCells(rows, columns) {
  const prev = new Map(rows.map((p, i) => [p, rows[i - 1]]));
  const cell = c => (v, r) => {
    const before = prev.get(r)?.[c];
    if (!num(v)) return dash();
    if (!num(before)) return amount(v, c, r);
    const [scale, unit] = POINT_CHANGE[c] || [];
    const d = scale ? (v - before) * scale : before ? v / before - 1 : NaN;
    const text = !num(d) ? '' : d === 0 ? '0' : (d > 0 ? '+' : '') + (scale ? +d.toFixed(2) + unit : (d * 100).toFixed(2) + '%');
    return h('div', {}, amount(v, c, r), h('div', { class: 'cell-sub ' + sign(d) }, text));
  };
  return Object.fromEntries(columns.filter(c => !NO_CHANGE.includes(c)).map(c => [c, cell(c)]));
}

// Yahoo's statements for banks, NBFCs and insurers don't match their filings (e.g. HDFCBANK equity +47% vs
// Screener after the merger), and debt/equity means little for a lender.
const UNRELIABLE_SECTORS = ['Financial Services'];
const fundamentalsWarning = sector => UNRELIABLE_SECTORS.includes(sector)
  ? h('p', { class: 'warning' }, `Fundamentals for ${sector} stocks (banks, NBFCs, insurers) are unreliable on Yahoo: `
    + "figures such as equity and revenue can differ widely from the company's filings. Verify before relying on them.")
  : null;

function historyTables(history, years, sector) {
  const columns = Object.keys(history[0]);
  const flagged = years.filter(y => y.check).length;
  return [
    card('Daily history: prices and technicals',
      h('p', { class: 'muted' }, 'Every trading day Yahoo has; under each value, its change from the previous trading day (points for RSI, MACD and scores). P/E, P/B, market cap and fundamental score are estimated from annual results.'),
      table(history.slice().reverse(), columns,
        { labels: DAILY_LABELS, extra: DAILY_EXTRA, pageSize: 25, cells: changeCells(history, columns) })),
    years.length ? card('Fundamentals by fiscal year',
      h('p', { class: 'muted' }, "Amounts are in ₹; companies reporting in USD are converted at the fiscal year's average USD/INR rate (income) or its year-end rate (balance sheet). Ratios take effect about 60 days after the year end. Under each value, its change from the previous year (percentage points for ratios)."),
      fundamentalsWarning(sector),
      flagged ? h('p', { class: 'warning' }, `${flagged} of ${years.length} years fail a data check (last column): verify those figures against the filings.`) : null,
      table(years.slice().reverse(), Object.keys(years[0]), { labels: YEAR_LABELS, cells: changeCells(years, Object.keys(years[0])) })) : null,
  ];
}

async function renderStock(file, rows) {
  const last = rows[rows.length - 1] || {};
  const labels = rows.map(r => r.date);
  const history = file.kind === 'stock' && (manifest.prices || []).includes(file.title)
    ? (await load('prices/' + file.title + '.csv')).filter(p => num(p.Close)) : [];
  const years = history.length && (manifest.fundamentals || []).includes(file.title)
    ? await load('fundamentals/' + file.title + '.csv') : [];
  const scores = ['combined', 'technical', 'fundamental', 'sentiment'].filter(c => file.columns.includes(c));
  // This stock's LLM notes from every session, newest first.
  const notesDir = file.kind === 'stock' ? 'journal/notes/' : 'journal/commodities/notes/';
  const notes = (await Promise.all(manifest.files.filter(f => f.path.startsWith(notesDir)).map(f => load(f.path))))
    .flat().filter(n => n.symbol === file.title).sort((a, b) => String(b.date).localeCompare(String(a.date)));
  // Paper-account trades in this stock: closed ones are marked on the chart; open ones join them in a table.
  const accounts = file.kind === 'stock' ? await loadAccounts() : [];
  const paper = accounts.flatMap(a => a.trades.filter(t => t.symbol === file.title).map(t => ({ strategy: a.name, ...t })));
  const held = positionRows(accounts).filter(p => p.symbol === file.title);
  return [
    h('p', { class: 'sub' }, [last.name, last.sector || last.group, last.unit && 'INR per ' + last.unit].filter(Boolean).join(' · ')),
    fundamentalsWarning(last.sector),
    tiles([['Last action', last.action || '–', last.action === 'BUY' ? 'pos' : last.action === 'SELL' ? 'neg' : ''],
      ['Close', fmt(last.close)], ...scores.map(c => [c[0].toUpperCase() + c.slice(1), fmt(last[c])]),
      ['Sessions logged', rows.length]]),
    history.length ? historyCard(history, rows, paper) : h('div', { class: 'charts' },
      chartCard('Close', lineConfig(labels, [{ label: 'Close', data: rows.map(r => r.close), borderColor: series(0) }])),
      chartCard('Scores (−1 to 1)', lineConfig(labels, scores.map((c, i) =>
        ({ label: c, data: rows.map(r => r[c]), borderColor: series(i), backgroundColor: series(i), pointRadius: 3 })), { min: -1, max: 1 }))),
    held.length ? card('Open paper positions', positionsTable(held)) : null,
    paper.length ? card('Closed paper trades', tradesTable(paper)) : null,
    ...(history.length ? historyTables(history, years, last.sector) : []),
    notes.length ? (file.kind === 'stock' ? notesCard(notes, 'LLM notes', rows, stockEntries, stockHref, true)
      : notesCard(notes, 'LLM notes', rows, commodityEntries, contractHref, true)) : null,
    card('Logged sessions', sessionsTable(file, rows, history)),
  ];
}

// A stock's or contract's journal rows, newest first, with the scan tables' cells. Name, sector and unit
// are in the page subtitle; close shows its change since the previous logged session, raw ratios sit
// behind 'All columns'.
const SESSION_HIDDEN = ['symbol', 'name', 'sector', 'group', 'unit', 'side'];
const SESSION_LABELS = { date: 'Session', stop: 'Stop', target: 'Target', shares: 'Shares',
  sentiment_reasons: 'News reasons', fundamental_reasons: 'Fundamental reasons', f_trailingPE: 'P/E',
  f_priceToBook: 'P/B', f_returnOnEquity: 'ROE', f_debtToEquity: 'Debt/Equity (%)', f_revenueGrowth: 'Revenue growth',
  f_earningsGrowth: 'Earnings growth', f_profitMargins: 'Net margin', f_currentRatio: 'Current ratio',
  f_dividendYield: 'Dividend yield (%)', f_trailingPegRatio: 'PEG' };

function sessionsTable(file, rows, history) {
  const stock = file.kind === 'stock';
  const bars = new Map(history.map(b => [String(b.Date), b]));
  const prev = new Map(rows.map((r, i) => [r, rows[i - 1]]));
  const close = (v, r) => {
    if (!num(v)) return dash();
    const before = prev.get(r)?.close, d = num(before) && before ? v / before - 1 : NaN;
    return h('div', {}, fmt(v), num(d) ? h('div', { class: 'cell-sub ' + sign(d) }, (d > 0 ? '+' : '') + pct(d)) : null);
  };
  const order = ['date', ...(stock ? SCAN_COLS : COMMODITY_COLS), 'stop', 'target', 'fundamental_reasons', 'sentiment_reasons'];
  const columns = file.columns.filter(c => !SESSION_HIDDEN.includes(c));
  const main = order.filter(c => columns.includes(c) && !c.startsWith('f_') && rows.some(r => r[c] != null && r[c] !== ''));
  const extra = columns.filter(c => !main.includes(c));
  return table(rows.slice().reverse(), [...main, ...extra], { filters: ['action'], extra,
    labels: { ...(stock ? SCAN_LABELS : COMMODITY_LABELS), ...SESSION_LABELS },
    cells: { ...(stock ? SCAN_CELLS : COMMODITY_CELLS), close,
      technical_reasons: (v, r) => v ? reasonsCell('technical_reasons', String(v), bars.get(String(r.date))) : dash() } });
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
  let log = positive && params.get('log') === '1';
  const wrap = h('div');
  const draw = () => {
    charts.forEach(c => c.destroy()); charts = [];
    wrap.replaceChildren(chartCard(null, lineConfig(labels, datasets, { log }), true));
  };
  const toggle = h('input', { type: 'checkbox' });
  toggle.checked = log;
  toggle.addEventListener('change', () => { log = toggle.checked; setParam('log', log ? '1' : null); draw(); });
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

// ---------- paper accounts ----------

// Each saved account (manifest.accounts: settings per strategy) with its equity, positions and trades files.
async function loadAccounts() {
  return Promise.all(Object.entries(manifest.accounts || {}).map(async ([name, cfg]) => {
    const [equity, positions, trades] = await Promise.all(['equity', 'positions', 'trades']
      .map(k => optional(`journal/paper/${name}/${k}.csv`)));
    return { name, cfg, equity, positions, trades };
  }));
}

const ACCOUNT_LABELS = { strategy: 'Strategy', date: 'As of', equity: 'Equity ₹', return: 'Return', cash: 'Cash ₹',
  open: 'Open', trades: 'Closed trades', win_rate: 'Win rate', pnl: 'Realized P&L ₹' };

function accountsTable(accounts) {
  const rows = accounts.map(a => {
    const last = a.equity[a.equity.length - 1] || {}, rets = values(a.trades, 'ret');
    return { strategy: a.name, date: last.date, equity: last.equity,
      return: num(last.equity) && a.cfg.capital ? last.equity / a.cfg.capital - 1 : null, cash: last.cash,
      open: a.positions.length, trades: a.trades.length,
      win_rate: rets.length ? rets.filter(r => r > 0).length / rets.length : null,
      pnl: values(a.trades, 'pnl').reduce((s, x) => s + x, 0) };
  });
  return table(rows, Object.keys(ACCOUNT_LABELS), { pageSize: 25, sort: 'return', labels: ACCOUNT_LABELS,
    cells: { pnl: signed } });
}

// Open positions valued at their last close (ref_close); to_stop is how far the close may fall before the stop.
function positionRows(accounts) {
  return accounts.flatMap(a => a.positions.map(p => {
    const last = num(p.ref_close) ? p.ref_close : null;
    return { strategy: a.name, symbol: p.sym, entry_date: p.entry_date, entry: p.entry_px, last, ret: last ? last / p.entry_px - 1 : null,
      stop: p.stop, to_stop: last && num(p.stop) ? p.stop / last - 1 : null, target: num(p.target) ? p.target : null,
      shares: p.shares, value: last ? last * p.shares : null };
  }));
}

const POSITION_LABELS = { strategy: 'Strategy', symbol: 'Stock', entry_date: 'Entry date', entry: 'Entry', last: 'Last close',
  ret: 'P&L', stop: 'Stop', to_stop: 'To stop', target: 'Target', shares: 'Shares', value: 'Value ₹' };

const positionsTable = rows => table(rows, Object.keys(POSITION_LABELS), { filters: ['strategy'], sort: 'ret', pageSize: 25,
  labels: POSITION_LABELS, link: { symbol: stockHref },
  cells: { to_stop: v => num(v) ? h('span', { class: v > -0.03 ? 'neg' : '' }, pct(v)) : dash(), target: v => num(v) ? fmt(v) : dash() } });

const tradesTable = rows => table(rows, ['strategy', 'symbol', 'entry_date', 'exit_date', 'entry', 'exit', 'bars', 'reason', 'pnl', 'ret'],
  { filters: ['strategy', 'reason'], sort: 'exit_date', pageSize: 25, link: { symbol: stockHref }, cells: { pnl: signed },
    labels: { strategy: 'Strategy', symbol: 'Stock', entry_date: 'Entry date', exit_date: 'Exit date', entry: 'Entry', exit: 'Exit',
      bars: 'Bars', reason: 'Reason', pnl: 'P&L ₹', ret: 'Return' } });

async function renderAccounts() {
  const accounts = await loadAccounts();
  if (!accounts.length) return [h('p', { class: 'muted' }, 'No paper accounts yet.')];
  const dates = [...new Set(accounts.flatMap(a => a.equity.map(e => String(e.date))))].sort();
  const curves = accounts.map((a, i) => {
    const by = new Map(a.equity.map(e => [String(e.date), e.equity / a.cfg.capital - 1]));
    return { label: a.name, data: dates.map(d => by.get(d) ?? null), borderColor: series(i), backgroundColor: series(i),
      pointRadius: dates.length < 30 ? 3 : 0 };
  });
  const positions = positionRows(accounts);
  const trades = accounts.flatMap(a => a.trades.map(t => ({ strategy: a.name, ...t })));
  const settings = Object.keys(manifest.accounts);
  return [
    h('p', { class: 'sub' }, 'One saved account per strategy (shared capital, same rules as the portfolio backtest), ' +
      'stepped through every session by the daily run.'),
    accountsTable(accounts),
    chartCard('Return since start', lineConfig(dates, curves, { pct: true })),
    h('h3', { class: 'section' }, `Open positions (${positions.length})`),
    positions.length ? positionsTable(positions) : h('p', { class: 'muted' }, 'No open positions.'),
    h('h3', { class: 'section' }, `Closed trades (${trades.length})`),
    trades.length ? tradesTable(trades) : h('p', { class: 'muted' }, 'No closed trades yet.'),
    h('h3', { class: 'section' }, 'Settings'),
    table(settings.map(n => ({ strategy: n, ...manifest.accounts[n] })),
      ['strategy', ...new Set(settings.flatMap(n => Object.keys(manifest.accounts[n])))], { pageSize: 50 }),
  ];
}

const RENDER = { day: renderDay, notes: renderNotes, stock: renderStock, trades: renderTrades, series: renderSeries,
  cday: renderCommodityDay, contract: renderStock,
  yearly: renderYearly, table: (f, rows) => [table(rows, f.columns)] };
// Pages that aren't one file: [title, render].
const PAGES = { today: ['Today', renderToday], accounts: ['Paper accounts', renderAccounts] };

let showSeq = 0;  // a slower earlier render must not replace a newer page

async function show() {
  const [hashPath, query] = route();
  const path = hashPath || 'today', seq = ++showSeq;
  params = query; tableSeq = 0;
  charts.forEach(c => c.destroy()); charts = [];
  document.querySelectorAll('nav a').forEach(a => {
    const on = a.getAttribute('href') === '#' + path;
    a.classList.toggle('active', on);
    if (on) a.closest('details')?.setAttribute('open', '');
  });
  $('#nav').classList.remove('open');
  const main = $('#main');
  window.scrollTo(0, 0);
  const file = manifest.files.find(f => f.path === path);
  if (!PAGES[path] && !file) { main.replaceChildren(h('p', { class: 'muted' }, 'File not found: ' + path)); return; }
  main.replaceChildren(h('p', { class: 'muted' }, 'Loading…'));
  const parts = PAGES[path] ? await PAGES[path][1]() : await RENDER[file.kind](file, await load(path));
  if (seq !== showSeq) return;
  const head = PAGES[path] ? [h('h2', {}, PAGES[path][0])] : [
    h('h2', {}, file.title, ['stock', 'contract'].includes(file.kind) ? starButton(file.title) : null),
    h('p', { class: 'sub' }, `${file.path} · ${file.rows.toLocaleString()} rows · `, h('a', { href: 'data/' + file.path, download: '' }, 'download CSV'))];
  main.replaceChildren(...head, ...parts.flat().filter(Boolean));
}

// ---------- navigation ----------

const navLink = f => h('a', { href: '#' + f.path, title: f.path }, f.title);
const monthName = m => /^\d{4}-\d{2}$/.test(m)
  ? new Date(m + '-01T00:00:00Z').toLocaleDateString('en-IN', { month: 'long', year: 'numeric', timeZone: 'UTC' }) : m;

function buildNav() {
  const nav = $('#nav');
  const byGroup = g => manifest.files.filter(f => f.group === g);
  // One box finds any page: a symbol opens the stock's history; sessions, contracts and reports match "title · group".
  const targets = new Map(manifest.files.filter(f => f.group)
    .map(f => [f.group === 'Stock history' ? f.title : `${f.title} · ${f.group}`, f.path]));
  const list = h('datalist', { id: 'page-list' }, [...targets.keys()].map(v => h('option', { value: v })));
  const input = h('input', { type: 'search', placeholder: `Find a stock or page (${targets.size})`, list: 'page-list' });
  input.addEventListener('change', () => {
    const want = input.value.trim().toUpperCase();
    const hit = [...targets].find(([v]) => v.toUpperCase() === want);
    if (hit) { location.hash = hit[1]; input.value = ''; }
  });
  nav.append(input, list, h('a', { href: '#today', class: 'top-link' }, 'Today'));
  for (const g of manifest.groups) {
    let files = byGroup(g);
    if (!files.length) continue;
    nav.append(h('h2', {}, g));
    if (['Stocks', 'Commodities'].includes(g)) {
      // Newest session first, folded by month; the latest month starts open.
      const months = new Map();
      for (const f of files.slice().reverse()) {
        const m = f.title.slice(0, 7);
        months.has(m) ? months.get(m).push(f) : months.set(m, [f]);
      }
      [...months].forEach(([m, fs], i) => nav.append(
        h('details', i ? {} : { open: '' }, h('summary', {}, `${monthName(m)} (${fs.length})`), fs.map(navLink))));
    } else if (g === 'Paper accounts') {
      if (Object.keys(manifest.accounts || {}).length) nav.append(h('a', { href: '#accounts' }, 'Overview'));
      nav.append(h('details', {}, h('summary', {}, `Account files (${files.length})`), files.map(navLink)));
    } else {
      if (g === 'Reports') files = files.slice().sort((a, b) => b.path.localeCompare(a.path));
      nav.append(...files.map(navLink));
    }
  }
}

async function init() {
  $('#theme').addEventListener('click', () => {
    const dark = css('--page') === '#0d0d0d';
    document.documentElement.setAttribute('data-theme', dark ? 'light' : 'dark');
    try { localStorage.setItem('theme', dark ? 'light' : 'dark'); } catch (e) {}
    chartDefaults();
    show();
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
  window.addEventListener('hashchange', show);
  show();
}

init();
