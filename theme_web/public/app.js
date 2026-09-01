const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

const HTML_ESCAPES = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
};

const esc = value => String(value ?? '').replace(/[&<>"']/g, character => HTML_ESCAPES[character]);
const ticker = marketCode => String(marketCode || '').split(':').pop() || '--';
const marketId = marketCode => String(marketCode || '').split(':')[0] || '';
const appLanguage = /(?:^|[;\s])hexinLanguage\/(zh-hans|zh-hant)(?:[;\s]|$)/i.exec(navigator.userAgent || '')?.[1]?.toLowerCase();
const language = appLanguage === 'zh-hans' ? 'zh-hans' : appLanguage === 'zh-hant' ? 'zh-hant' : 'en';
const panelCache = new Map();
const bootData = window.__THEMESIGNAL_BOOT__ || null;
const VALUE_INVESTMENT_BASE_URL = 'https://value-investment.emery-xu1.workers.dev/value-opportunities';
const LIGHTWEIGHT_CHARTS_URL = 'https://unpkg.com/lightweight-charts@5.2.0/dist/lightweight-charts.standalone.production.js';
const STANDARD_CHART_URL = 'https://cdn.ainvest.com/frontResources/offline/js/standard-chart/compliance-v0.10.6.js';
const EXPOSURE_POINT_SIZE = 6;
const EXPOSURE_ACTIVE_SIZE = 14;
const EXPOSURE_HIT_RADIUS = 22;
const EXPOSURE_LABEL_SIZE = 11;
const UI_TEXT = { en: {
  stock: 'Stock', etf: 'ETF', performance: 'Performance',
  performanceSubtitle: 'Equal-weighted average of constituent returns', unableChart: 'Unable to load chart',
  noChartData: 'No chart data available', exposureMap: 'Exposure Map', exposure: 'Theme exposure (1–5)',
  marketCap: 'Market cap', marketCapTable: 'Market cap', aum: 'AUM', symbol: 'Symbol', symbols: 'Symbols',
  security: 'Security', securities: 'securities', rationale: 'Rationale', sortBy: 'Sort by',
  last: 'Last', change: 'Change%', changeSince: '% Chg since', since: 'Since',
  themeFaq: 'Theme FAQ', unableData: 'Unable to load data', noData: 'No data available', retry: 'Retry',
  loadingTheme: 'Loading event theme', loadingExposure: 'Loading exposure map',
} };

const tr = key => UI_TEXT.en[key] || key;

let detailModel = null;
let resizeFrame = 0;
let detailEventsInstalled = false;
let lightweightChartsPromise = null;
let standardChartPromise = null;
let performanceRuntime = null;
let performanceRenderToken = 0;
let exposureScatterRuntime = null;
let exposureScatterRenderToken = 0;

document.documentElement.lang = language === 'zh-hans' ? 'zh-CN' : language === 'zh-hant' ? 'zh-TW' : 'en';

function loadScriptOnce(url, globalName) {
  if (window[globalName]) return Promise.resolve(window[globalName]);
  return new Promise((resolve, reject) => {
    const existing = document.querySelector(`script[data-runtime="${globalName}"]`);
    const script = existing || document.createElement('script');
    let settled = false;
    const finish = (callback, value) => {
      if (settled) return;
      settled = true;
      callback(value);
    };
    const waitForGlobal = (attempt = 0) => {
      if (window[globalName]) finish(resolve, window[globalName]);
      else if (attempt < 80) setTimeout(() => waitForGlobal(attempt + 1), 100);
      else {
        script.remove();
        finish(reject, new Error(`${globalName} unavailable`));
      }
    };
    const onLoad = () => waitForGlobal();
    script.addEventListener('load', onLoad, { once: true });
    script.addEventListener('error', () => {
      script.remove();
      finish(reject, new Error(`Unable to load ${globalName}`));
    }, { once: true });
    if (!existing) {
      script.src = url;
      script.async = true;
      script.crossOrigin = 'anonymous';
      script.dataset.runtime = globalName;
      document.head.append(script);
    } else waitForGlobal();
  });
}

function ensureLightweightCharts() {
  if (!lightweightChartsPromise) {
    lightweightChartsPromise = loadScriptOnce(LIGHTWEIGHT_CHARTS_URL, 'LightweightCharts').catch(error => {
      lightweightChartsPromise = null;
      throw error;
    });
  }
  return lightweightChartsPromise;
}

function ensureStandardChart() {
  if (!standardChartPromise) {
    standardChartPromise = loadScriptOnce(STANDARD_CHART_URL, 'ThsDataVStandardChart').catch(error => {
      standardChartPromise = null;
      throw error;
    });
  }
  return standardChartPromise;
}

function safeUrl(value, upgradeInsecure = true) {
  try {
    const url = new URL(String(value || ''), location.origin);
    if (url.protocol !== 'http:' && url.protocol !== 'https:') return '';
    if (upgradeInsecure && location.protocol === 'https:' && url.protocol === 'http:') url.protocol = 'https:';
    return url.href;
  } catch {
    return '';
  }
}

function localizedText(value, fallback = '') {
  if (typeof value === 'string') return value.trim() || fallback;
  if (!value || typeof value !== 'object') return fallback;
  return String(value[language] || value[language.split('-')[0]] || value.en || value.zh || fallback).trim();
}

function formatDate(value, localized = false) {
  if (!value) return '';
  const date = new Date(`${value}T12:00:00Z`);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat(localized && language !== 'en' ? 'en-US' : 'en-US', localized && language !== 'en'
    ? { month: '2-digit', day: '2-digit', year: 'numeric', timeZone: 'UTC' }
    : { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' }).format(date);
}

function formatCompactDate(value) {
  if (!value) return '';
  const date = new Date(`${value}T12:00:00Z`);
  if (Number.isNaN(date.getTime())) return String(value);
  return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }).format(date);
}

function formatAxisDate(value, showDay = true) {
  const date = new Date(`${value}T12:00:00Z`);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat('en-US', showDay
    ? { month: 'short', day: 'numeric', timeZone: 'UTC' }
    : { month: 'short', timeZone: 'UTC' }).format(date);
}

function formatNumber(value, fractionDigits = 2) {
  const number = Number(value);
  if (value == null || !Number.isFinite(number)) return '--';
  return number.toLocaleString('en-US', {
    minimumFractionDigits: fractionDigits,
    maximumFractionDigits: fractionDigits,
  });
}

function formatMarketValue(value) {
  const number = Number(value);
  if (value == null || !Number.isFinite(number)) return '--';
  const absolute = Math.abs(number);
  if (absolute >= 1_000_000_000_000) return `${(number / 1_000_000_000_000).toFixed(2)}T`;
  if (absolute >= 1_000_000_000) return `${(number / 1_000_000_000).toFixed(2)}B`;
  if (absolute >= 1_000_000) return `${(number / 1_000_000).toFixed(2)}M`;
  return formatNumber(number, 2);
}

function formatMarketScale(value) {
  if (value === 0) return '$0';
  if (value >= 1000) return `$${(value / 1000).toFixed(1)}T`;
  if (value < 1) return `$${(value * 1000).toFixed(0)}M`;
  return `$${value.toFixed(value >= 10 ? 0 : 1)}B`;
}

function formatPercent(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return '--';
  return `${Number(number.toFixed(2))}%`;
}

function getSelections(data, type) {
  const source = type === 'etf' ? data.ThemeEtfs : data.ThemeStocks;
  return (Array.isArray(source) ? source : []).filter(item => item && String(item.market_code || '').includes(':'));
}

function requestHeaders() {
  return {
    accept: 'application/json',
    'accept-language': language,
  };
}

async function request(url) {
  const response = await fetch(url, { headers: requestHeaders() });
  let data = null;
  try {
    data = await response.json();
  } catch {
    throw new Error(`Request failed: ${response.status}`);
  }
  if (!response.ok || data?.unavailable) {
    throw new Error(data?.error || `Request failed: ${response.status}`);
  }
  return data;
}

function spinner(label = 'Loading') {
  return `<div class="loading-state" role="status" aria-label="${esc(label)}"><span class="spinner"></span></div>`;
}

function feedbackState({ title, type = 'network', action = '', compact = false }) {
  const icon = type === 'empty'
    ? '<span class="empty-icon empty-icon-document" aria-hidden="true"></span>'
    : '<span class="empty-icon empty-icon-network" aria-hidden="true"></span>';
  return `<div class="module-feedback${compact ? ' module-feedback-compact' : ''}">
    ${icon}
    <h3>${esc(title)}</h3>
    ${action ? `<button type="button" class="retry-button" data-retry="${esc(action)}">${esc(tr('retry'))}</button>` : ''}
  </div>`;
}

function archiveShell(content) {
  document.body.className = 'archive-body';
  document.body.innerHTML = `<div class="app-shell">
    <nav class="nav" aria-label="Main navigation">
      <div class="nav-inner">
        <a class="brand" href="/" aria-label="ThemeSignal home"><span class="brand-mark">TS</span><span class="brand-word">ThemeSignal</span></a>
      </div>
    </nav>
    ${content}
  </div>`;
}

function updateMeta({ title, description, canonical, image, jsonLd }) {
  if (title) document.title = title;
  const setMeta = (selector, attribute, value) => {
    if (!value) return;
    let element = document.head.querySelector(selector);
    if (!element) {
      element = document.createElement('meta');
      element.setAttribute(attribute, '');
      document.head.append(element);
    }
    element.setAttribute(attribute, value);
  };
  if (description) setMeta('meta[name="description"]', 'name', description);
  if (canonical) {
    let link = document.head.querySelector('link[rel="canonical"]');
    if (!link) { link = document.createElement('link'); link.rel = 'canonical'; document.head.append(link); }
    link.href = canonical;
  }
  [['og:title', title], ['og:description', description], ['og:url', canonical], ['og:image', image], ['twitter:title', title], ['twitter:description', description], ['twitter:image', image]].forEach(([property, value]) => {
    if (!value) return;
    let element = document.head.querySelector(`meta[property="${property}"], meta[name="${property}"]`);
    if (!element) { element = document.createElement('meta'); element.setAttribute(property.startsWith('og:') ? 'property' : 'name', property); document.head.append(element); }
    element.setAttribute('content', value);
  });
  if (jsonLd) {
    let script = document.head.querySelector('script[data-theme-jsonld]');
    if (!script) { script = document.createElement('script'); script.type = 'application/ld+json'; script.dataset.themeJsonld = 'true'; document.head.append(script); }
    script.textContent = JSON.stringify(jsonLd);
  }
}

function themeDescription(data) {
  const theme = String(data?.theme || 'Market theme').trim() || 'Market theme';
  const faq = Array.isArray(data?.ThemeFAQ) ? data.ThemeFAQ.find(item => item?.answer) : null;
  const answer = localizedText(faq?.answer, '').replace(/\s+/gu, ' ');
  const text = `Research context for ${theme}: explore the market event, related securities, and the evidence behind this theme.${answer ? ` ${answer}` : ''}`;
  return text.length > 160 ? `${text.slice(0, 159).trimEnd()}…` : text;
}

function themeThesis(data) {
  const faq = Array.isArray(data?.ThemeFAQ) ? data.ThemeFAQ.find(item => item?.answer) : null;
  const answer = localizedText(faq?.answer, '').replace(/\s+/gu, ' ').trim();
  if (!answer) return '';
  const sentence = answer.match(/^.*?[.!?](?:\s|$)/u)?.[0]?.trim() || answer;
  return sentence.length > 190 ? `${sentence.slice(0, 189).trimEnd()}…` : sentence;
}

function themeCardMarkup(theme, index) {
  const cover = safeUrl(theme.cover_url);
  const href = `/themes/${encodeURIComponent(theme.id)}`;
  const title = theme.theme || 'Untitled theme';
  const date = formatDate(theme.event_date);
  const featured = index === 0;
  return `<article class="theme-card-wrap${featured ? ' theme-card-wrap-featured' : ''}">
    <a class="theme-card${featured ? ' theme-card-featured' : ''}" href="${esc(href)}" aria-label="Open ${esc(title)} theme">
      <div class="theme-card-media${cover ? '' : ' theme-card-media-placeholder'}">${cover ? `<img class="theme-cover" src="${esc(cover)}" alt="${esc(title)} market theme cover" width="1600" height="900"${featured ? ' fetchpriority="high"' : ' loading="lazy"'} decoding="async">` : '<span class="theme-cover-placeholder" aria-hidden="true"></span>'}<span class="theme-card-shade" aria-hidden="true"></span></div>
      <div class="theme-card-body">
        <p class="card-date">${date ? `<time datetime="${esc(theme.event_date)}">${esc(date)}</time>` : 'Market signal'}</p>
        <h2>${esc(title)}</h2>
      </div>
    </a>
  </article>`;
}

function archiveLoading() {
  return `<div class="archive-skeleton" aria-hidden="true">
    <span></span><span></span><span></span>
  </div>`;
}

async function list() {
  const initial = bootData?.route === 'archive' ? bootData : null;
  if (initial?.origin && initial.origin !== location.origin) document.documentElement.dataset.siteOrigin = initial.origin;
  archiveShell(`<main class="archive">
    <header class="archive-hero">
      <h1>See what’s moving markets.</h1>
      <p>A clear view of the narratives, companies, and funds shaping the market right now.</p>
    </header>
    <div class="toolbar">
      <label class="search-wrap"><span class="sr-only">Search themes</span><span class="search-icon" aria-hidden="true"></span><input id="search" class="input" placeholder="Search themes" autocomplete="off" value="${esc(initial?.q || '')}"></label>
      <label class="sort-wrap"><span class="sr-only">Sort themes</span><select id="sort" class="select"><option value="new"${initial?.sort !== 'old' ? ' selected' : ''}>Newest</option><option value="old"${initial?.sort === 'old' ? ' selected' : ''}>Oldest</option></select></label>
    </div>
    <div class="archive-toolbar-meta"><span id="result-count" aria-live="polite">${initial?.total ? `${initial.total} themes` : ''}</span></div>
    <section aria-label="Theme archive"><div id="results" class="theme-grid">${initial?.themes?.length ? initial.themes.map(themeCardMarkup).join('') : archiveLoading()}</div><div id="archive-pagination" class="archive-pagination">${initial?.has_more ? '<button type="button" class="load-more" data-load-more>Load more themes <span aria-hidden="true">↓</span></button>' : ''}</div></section>
    <footer class="archive-footer">Educational research, not investment advice.</footer>
  </main>`);

  const results = $('#results');
  const pagination = $('#archive-pagination');
  const search = $('#search');
  const sort = $('#sort');
  const count = $('#result-count');
  let requestSequence = 0;
  let page = initial?.page || 1;
  let total = initial?.total || 0;
  let hasMore = Boolean(initial?.has_more);
  let rows = Array.isArray(initial?.themes) ? [...initial.themes] : [];
  let searchTimer = 0;
  const renderRows = (replace = true) => {
    if (!rows.length) results.innerHTML = feedbackState({ title: 'No themes found', type: 'empty', compact: true });
    else results.innerHTML = rows.map(themeCardMarkup).join('');
    count.textContent = total ? `${total} theme${total === 1 ? '' : 's'}` : '';
    pagination.innerHTML = hasMore ? '<button type="button" class="load-more" data-load-more>Load more themes <span aria-hidden="true">↓</span></button>' : '';
  };
  const load = async ({ append = false } = {}) => {
    const sequence = ++requestSequence;
    if (!append) { page = 1; rows = []; results.innerHTML = archiveLoading(); }
    else if (pagination) pagination.innerHTML = '<span class="load-more-status" role="status">Loading more themes…</span>';
    try {
      const params = new URLSearchParams({ page: String(append ? page + 1 : 1), limit: String(initial?.limit || 24), sort: sort.value });
      const query = search.value.trim();
      if (query) params.set('q', query);
      const data = await request(`/api/themes?${params}`);
      if (sequence !== requestSequence) return;
      const incoming = Array.isArray(data.themes) ? data.themes : [];
      rows = append ? [...rows, ...incoming] : incoming;
      page = Number(data.page) || (append ? page + 1 : 1);
      total = Number(data.total) || rows.length;
      hasMore = Boolean(data.has_more);
      renderRows();
    } catch {
      if (sequence !== requestSequence) return;
      if (!append) results.innerHTML = feedbackState({ title: 'Unable to load themes', action: 'archive', compact: true });
      if (pagination) pagination.innerHTML = '';
    }
  };
  if (!initial?.themes?.length) await load();
  search.addEventListener('input', () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => void load(), 220);
  });
  sort.addEventListener('change', () => void load());
  document.body.addEventListener('click', event => {
    if (event.target.closest('[data-retry="archive"]')) void load();
    if (event.target.closest('[data-load-more]')) void load({ append: true });
  });
  updateMeta({ title: 'Trending Market Themes & Investment Research | ThemeSignal', description: 'Explore current market themes, the companies and funds connected to them, and the research context behind each signal.', canonical: `${location.origin}/` });
}

function heroCover(data) {
  const coverUrl = safeUrl(data.cover);
  if (!coverUrl) return '';
  return `<div class="event-cover">
    <img src="${esc(coverUrl)}" alt="${esc(data.theme || 'Event theme')}" width="1600" height="900" fetchpriority="high">
  </div>`;
}

function eventHero(data, activeType) {
  const stocks = getSelections(data, 'stock');
  const etfs = getSelections(data, 'etf');
  const tabs = [
    stocks.length ? { type: 'stock', label: tr('stock') } : null,
    etfs.length ? { type: 'etf', label: tr('etf') } : null,
  ].filter(Boolean);
  const thesis = themeThesis(data);
  const cover = heroCover(data);
  return `<section class="event-hero">
    <div class="event-hero-grid${cover ? ' has-cover' : ''}">
      ${cover}
      <div class="event-heading">
        <p class="seo-breadcrumb"><a href="/">All themes</a><span aria-hidden="true">/</span> ${esc(data.theme || 'Untitled theme')}</p>
        <h1>${esc(data.theme || 'Untitled theme')}</h1>
        ${data.date ? `<time datetime="${esc(data.date)}">${esc(formatDate(data.date))}</time>` : ''}
        ${thesis ? `<p class="theme-thesis">${esc(thesis)}</p>` : ''}
        ${tabs.length ? `<div class="security-tabs" role="tablist" aria-label="Security type">
          ${tabs.map(tab => `<button type="button" role="tab" id="security-tab-${tab.type}" class="security-tab${activeType === tab.type ? ' active' : ''}" data-security-type="${tab.type}"${activeType === tab.type ? ` aria-controls="security-panel-${tab.type}"` : ''} aria-selected="${activeType === tab.type}" tabindex="${activeType === tab.type ? '0' : '-1'}">${esc(tab.label)}</button>`).join('')}
        </div>` : ''}
      </div>
    </div>
  </section>`;
}

function performanceSkeleton() {
  return `<div class="chart-skeleton" aria-hidden="true"><span></span><span></span><span></span><span></span></div>`;
}

function getPanel(type) {
  const key = `${detailModel.id}:${type}`;
  if (!panelCache.has(key)) {
    panelCache.set(key, {
      type,
      period: '1M',
      exposure: null,
      exposureState: 'idle',
      quotes: null,
      quotesState: 'idle',
      sort: null,
    });
  }
  return panelCache.get(key);
}

function panelMarkup(type) {
  const panel = getPanel(type);
  const performanceState = detailModel.performanceState;
  return `<div class="event-panel" id="security-panel-${type}" role="tabpanel" aria-labelledby="security-tab-${type}">
    <div class="analysis-grid">
      <section class="performance-section">
        <div class="module-heading-row">
          <div class="module-heading"><h2>${esc(tr('performance'))}</h2><p>${esc(tr('performanceSubtitle'))}</p></div>
          <div class="period-tabs" role="tablist" aria-label="${esc(tr('performance'))}">
            ${['1M', '6M', '1Y'].map(period => `<button type="button" role="tab" id="period-tab-${period}" class="period-tab${panel.period === period ? ' active' : ''}" data-period="${period}" aria-controls="performance-plot" aria-selected="${panel.period === period}" tabindex="${panel.period === period ? '0' : '-1'}">${period}</button>`).join('')}
          </div>
        </div>
        <div id="performance-plot" class="performance-plot" aria-live="polite">${performanceState === 'loading' || performanceState === 'idle' ? performanceSkeleton() : ''}</div>
      </section>
      <section class="exposure-section">
        <div class="module-heading" id="exposure-scatter-heading"><h2>${esc(tr('exposureMap'))}</h2><p>${esc(`Farther right = stronger theme exposure · Higher = larger ${type === 'etf' ? tr('aum') : tr('marketCap').toLowerCase()}`)}</p></div>
        <div class="exposure-scatter-layout">
          <div class="exposure-scatter-y-label">${esc(type === 'etf' ? tr('aum') : tr('marketCap'))}</div>
          <div id="exposure-scatter-plot" class="exposure-scatter-plot" role="group" tabindex="0" aria-labelledby="exposure-scatter-heading" aria-describedby="exposure-scatter-help exposure-scatter-status">${panel.exposureState === 'loading' || panel.exposureState === 'idle' ? spinner(tr('loadingExposure')) : ''}</div>
          <div class="exposure-scatter-x-label">${esc(tr('exposure'))}</div>
        </div>
        <p id="exposure-scatter-help" class="sr-only">Use the arrow keys to explore securities. Press Enter or Space to pin details, and Escape to clear them.</p>
        <p id="exposure-scatter-status" class="sr-only" role="status" aria-live="polite" aria-atomic="true"></p>
        <ol id="exposure-scatter-summary" class="sr-only" data-exposure-summary></ol>
      </section>
    </div>
    <span id="quote-table-status" class="sr-only" role="status" aria-live="polite" aria-atomic="true"></span>
    <div id="quote-table-root" class="stock-table-root">${panel.quotesState === 'loading' || panel.quotesState === 'idle' ? quoteSkeleton(type) : ''}</div>
  </div>`;
}

function quoteSkeleton(type) {
  const count = Math.min(Math.max(getSelections(detailModel.data, type).length || 4, 4), 5);
  return `<div class="quote-skeleton" aria-hidden="true">
    <div class="quote-skeleton-heading">
      <span class="quote-skeleton-title"></span>
      <div class="quote-skeleton-mobile-sort"><span></span><span></span><span></span></div>
    </div>
    <div class="quote-skeleton-frame">
      <div class="quote-skeleton-head"><span></span><span></span><span></span><span></span></div>
      ${Array.from({ length: count }, () => `<div class="quote-skeleton-record">
        <div class="quote-skeleton-row"><span></span><span></span><span></span><span></span></div>
        <div class="quote-skeleton-rationale"><span></span><span></span></div>
      </div>`).join('')}
    </div>
  </div>`;
}

function normalizedPerformancePoints(points) {
  const byDate = new Map();
  for (const point of Array.isArray(points) ? points : []) {
    const percentage = Number(point?.percentage);
    const date = String(point?.date || '');
    if (/^\d{4}-\d{2}-\d{2}$/.test(date) && Number.isFinite(percentage)) byDate.set(date, percentage);
  }
  return [...byDate].sort(([left], [right]) => left.localeCompare(right)).map(([date, percentage]) => ({ date, percentage }));
}

function niceStep(range, intervals = 4) {
  const rough = Math.max(range, 1) / intervals;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const normalized = rough / magnitude;
  const multiplier = normalized <= 1 ? 1 : normalized <= 2 ? 2 : normalized <= 2.5 ? 2.5 : normalized <= 5 ? 5 : 10;
  return multiplier * magnitude;
}

function renderPerformanceFallback(container, points) {
  const data = normalizedPerformancePoints(points);
  if (!data.length) {
    container.innerHTML = feedbackState({ title: tr('noChartData'), type: 'empty' });
    return;
  }
  const width = Math.max(320, Math.round(container.clientWidth || 320));
  const height = Math.max(240, Math.round(container.clientHeight || 240));
  const margin = { top: 14, right: 12, bottom: 30, left: 42 };
  const values = data.map(point => point.percentage);
  const rawMin = Math.min(0, ...values);
  const rawMax = Math.max(0, ...values);
  const step = niceStep(Math.max(rawMax - rawMin, 1), 5);
  const minimum = Math.floor(rawMin / step) * step;
  const maximum = Math.ceil(rawMax / step) * step || step;
  const range = maximum - minimum || 1;
  const plotWidth = width - margin.left - margin.right;
  const plotHeight = height - margin.top - margin.bottom;
  const x = index => margin.left + index * plotWidth / Math.max(1, data.length - 1);
  const y = value => margin.top + (maximum - value) * plotHeight / range;
  const coordinates = data.map((point, index) => [x(index), y(point.percentage)]);
  const linePath = coordinates.map(([pointX, pointY], index) => `${index ? 'L' : 'M'}${pointX.toFixed(2)} ${pointY.toFixed(2)}`).join(' ');
  const zeroY = y(0);
  const areaPath = `${linePath} L${coordinates.at(-1)[0].toFixed(2)} ${zeroY.toFixed(2)} L${coordinates[0][0].toFixed(2)} ${zeroY.toFixed(2)} Z`;
  const yTicks = [];
  for (let value = minimum; value <= maximum + step / 2; value += step) yTicks.push(value);
  const xTickCount = width < 520 ? 3 : Math.min(10, data.length);
  const xIndexes = [...new Set(Array.from({ length: xTickCount }, (_, index) => Math.round(index * (data.length - 1) / Math.max(1, xTickCount - 1))))];
  const firstTime = Date.parse(`${data[0].date}T12:00:00Z`);
  const lastTime = Date.parse(`${data.at(-1).date}T12:00:00Z`);
  const showDay = Number.isFinite(firstTime) && Number.isFinite(lastTime) && (lastTime - firstTime) / 86_400_000 <= 70;
  container.innerHTML = `<svg class="performance-svg" viewBox="0 0 ${width} ${height}" role="img" aria-label="Equal-weighted average of constituent returns">
    ${yTicks.map(value => `<g><line x1="${margin.left}" y1="${y(value)}" x2="${width - margin.right}" y2="${y(value)}" class="chart-grid-line"></line><text x="${margin.left - 8}" y="${y(value) + 4}" class="chart-y-text">${esc(formatPercent(value))}</text></g>`).join('')}
    <path d="${areaPath}" class="performance-area"></path>
    <path d="${linePath}" class="performance-line"></path>
    ${xIndexes.map(index => `<text x="${x(index)}" y="${height - 5}" class="chart-x-text">${esc(formatAxisDate(data[index].date, showDay))}</text>`).join('')}
  </svg>`;
}

function destroyPerformanceChart() {
  performanceRenderToken += 1;
  if (performanceRuntime?.chart) performanceRuntime.chart.remove();
  performanceRuntime = null;
}

async function renderPerformanceChart(container, points) {
  const data = normalizedPerformancePoints(points);
  destroyPerformanceChart();
  container.replaceChildren();
  if (!data.length) {
    container.innerHTML = feedbackState({ title: tr('noChartData'), type: 'empty' });
    return;
  }
  const token = ++performanceRenderToken;
  try {
    const charts = await ensureLightweightCharts();
    if (token !== performanceRenderToken || !container.isConnected) return;
    const spanDays = data.length > 1
      ? Math.round((Date.parse(`${data.at(-1).date}T00:00:00Z`) - Date.parse(`${data[0].date}T00:00:00Z`)) / 86_400_000)
      : 0;
    const dayGranularity = spanDays > 0 && spanDays <= 70;
    const chartTime = time => typeof time === 'string'
      ? time
      : `${time.year}-${String(time.month).padStart(2, '0')}-${String(time.day).padStart(2, '0')}`;
    const chart = charts.createChart(container, {
      autoSize: true,
      layout: {
        background: { type: charts.ColorType.Solid, color: 'transparent' },
        textColor: 'rgba(0,0,0,.6)',
        fontSize: 11,
        attributionLogo: false,
      },
      grid: {
        horzLines: { color: 'rgba(0,0,0,.05)', style: charts.LineStyle.Dashed },
        vertLines: { visible: false },
      },
      leftPriceScale: { visible: true, borderVisible: false },
      rightPriceScale: { visible: false },
      timeScale: {
        borderVisible: false,
        fixLeftEdge: true,
        fixRightEdge: true,
        tickMarkMaxCharacterLength: dayGranularity ? 8 : 4,
        tickMarkFormatter: (time, tickMarkType) => {
          const value = chartTime(time);
          if (dayGranularity) return formatAxisDate(value, true);
          if (tickMarkType === charts.TickMarkType.DayOfMonth) return '';
          const date = new Date(`${value}T00:00:00Z`);
          const month = new Intl.DateTimeFormat('en-US', { month: 'short', timeZone: 'UTC' }).format(date);
          if (tickMarkType === charts.TickMarkType.Year) {
            const year = new Intl.DateTimeFormat('en-US', { year: '2-digit', timeZone: 'UTC' }).format(date);
            return `${month} '${year}`;
          }
          return month;
        },
      },
      crosshair: {
        mode: charts.CrosshairMode.Magnet,
        vertLine: { color: 'rgba(0,0,0,.6)', width: 1, style: charts.LineStyle.Solid, labelVisible: false },
        horzLine: { visible: false, labelVisible: false },
      },
      handleScroll: false,
      handleScale: false,
      localization: { priceFormatter: formatPercent },
    });
    const series = chart.addSeries(charts.BaselineSeries, {
      baseValue: { type: 'price', price: 0 },
      priceScaleId: 'left',
      lineWidth: 2,
      topLineColor: '#265ffc',
      bottomLineColor: '#265ffc',
      topFillColor1: 'rgba(38,95,252,.12)',
      topFillColor2: 'rgba(38,95,252,.12)',
      bottomFillColor1: 'rgba(38,95,252,.12)',
      bottomFillColor2: 'rgba(38,95,252,.12)',
      priceLineVisible: false,
      lastValueVisible: false,
      priceFormat: { type: 'custom', formatter: formatPercent, minMove: .01 },
    });
    series.setData(data.map(point => ({ time: point.date, value: point.percentage })));
    chart.timeScale().fitContent();
    performanceRuntime = { chart, series, container };
  } catch {
    if (token === performanceRenderToken && container.isConnected) renderPerformanceFallback(container, data);
  }
}

function exposureScatterPoints(points, type) {
  const quoteRows = getPanel(type).quotes?.rows || [];
  const namesByCode = new Map(quoteRows.map(row => [String(row.marketCode || ''), String(row.name || row.symbol || '')]));
  const namesBySymbol = new Map(quoteRows.map(row => [String(row.symbol || ticker(row.marketCode)), String(row.name || row.symbol || '')]));
  return (Array.isArray(points) ? points : []).flatMap(point => {
    const exposure = Number(point?.exposure);
    const marketValue = Number(point?.marketValue);
    if (!Number.isFinite(exposure) || exposure < 1 || exposure > 5 || !Number.isFinite(marketValue) || marketValue <= 0) return [];
    const marketCode = String(point.marketCode || '');
    const symbol = String(point.symbol || ticker(marketCode));
    const name = namesByCode.get(marketCode) || namesBySymbol.get(symbol) || symbol;
    return [{ symbol, name, marketCode, exposure, marketValue }];
  }).sort((left, right) => left.exposure - right.exposure || right.marketValue - left.marketValue || left.symbol.localeCompare(right.symbol));
}

function exposureScatterAxis(maxValue) {
  const step = niceStep(maxValue, 4);
  return { step, max: Math.max(step, Math.ceil(maxValue / step) * step) };
}

function clampNumber(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function boxesOverlap(left, right, gap = 0) {
  return left.x < right.x + right.width + gap
    && left.x + left.width + gap > right.x
    && left.y < right.y + right.height + gap
    && left.y + left.height + gap > right.y;
}

function measureExposureLabel(text) {
  const canvas = measureExposureLabel.canvas || (measureExposureLabel.canvas = document.createElement('canvas'));
  const context = canvas.getContext('2d');
  if (context) {
    context.font = `600 ${EXPOSURE_LABEL_SIZE}px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`;
    return Math.ceil(context.measureText(text).width) + 4;
  }
  return Math.ceil(String(text).length * EXPOSURE_LABEL_SIZE * .64) + 4;
}

function exposureScatterGeometry(data, containerWidth, containerHeight) {
  const width = Math.max(280, Math.round(containerWidth || 280));
  const height = Math.max(240, Math.round(containerHeight || 260));
  const margin = { top: 22, right: 18, bottom: 32, left: width < 420 ? 54 : 64 };
  const plotWidth = Math.max(1, width - margin.left - margin.right);
  const plotHeight = Math.max(1, height - margin.top - margin.bottom);
  const axis = exposureScatterAxis(Math.max(...data.map(point => point.marketValue)));
  const x = value => margin.left + (value - 1) / 4 * plotWidth;
  const y = value => margin.top + (axis.max - value) / axis.max * plotHeight;
  const marks = data.map(point => ({ x: x(point.exposure), y: y(point.marketValue) }));
  const placed = [];
  const labelHeight = 15;
  const plotBounds = {
    left: margin.left + 2,
    right: width - margin.right - 2,
    top: margin.top + 1,
    bottom: height - margin.bottom - 1,
  };
  const fit = box => ({
    ...box,
    x: clampNumber(box.x, plotBounds.left, plotBounds.right - box.width),
    y: clampNumber(box.y, plotBounds.top, plotBounds.bottom - box.height),
  });
  const labels = data.map((point, index) => {
    const mark = marks[index];
    const width = measureExposureLabel(point.symbol);
    const gap = 7;
    const candidates = [
      { x: mark.x - width / 2, y: mark.y - gap - labelHeight },
      { x: mark.x + gap, y: mark.y - gap - labelHeight },
      { x: mark.x - gap - width, y: mark.y - gap - labelHeight },
      { x: mark.x + gap, y: mark.y - labelHeight / 2 },
      { x: mark.x - gap - width, y: mark.y - labelHeight / 2 },
      { x: mark.x - width / 2, y: mark.y + gap },
      { x: mark.x + gap, y: mark.y + gap },
      { x: mark.x - gap - width, y: mark.y + gap },
    ].map(candidate => fit({ ...candidate, width, height: labelHeight }));
    const markBoxes = marks.map(other => ({ x: other.x - 5, y: other.y - 5, width: 10, height: 10 }));
    const available = candidate => !placed.some(other => boxesOverlap(candidate, other, 3))
      && !markBoxes.some((other, otherIndex) => otherIndex !== index && boxesOverlap(candidate, other, 2));
    let box = candidates.find(available);
    if (!box) {
      const inwardX = mark.x > margin.left + plotWidth / 2
        ? mark.x - gap - width
        : mark.x + gap;
      const rows = [];
      for (let rowY = plotBounds.top; rowY <= plotBounds.bottom - labelHeight; rowY += labelHeight + 4) {
        rows.push(fit({ x: inwardX, y: rowY, width, height: labelHeight }));
      }
      rows.sort((left, right) => Math.abs((left.y + labelHeight / 2) - mark.y) - Math.abs((right.y + labelHeight / 2) - mark.y));
      box = rows.find(available);
    }
    box ||= candidates.find(candidate => !placed.some(other => boxesOverlap(candidate, other, 1))) || candidates[0];
    placed.push(box);
    const endX = clampNumber(mark.x, box.x, box.x + box.width);
    const endY = clampNumber(mark.y, box.y, box.y + box.height);
    const leaderDistance = Math.hypot(mark.x - endX, mark.y - endY);
    return {
      ...box,
      textX: box.x + box.width / 2,
      textY: box.y + box.height / 2,
      leader: leaderDistance > 8 ? { x1: mark.x, y1: mark.y, x2: endX, y2: endY } : null,
    };
  });
  const yTicks = [];
  for (let value = 0; value <= axis.max + axis.step / 2; value += axis.step) yTicks.push(value);
  return { width, height, margin, plotWidth, plotHeight, axis, x, y, marks, labels, yTicks };
}

function exposureScatterDescription(point, type) {
  const identity = point.name && point.name !== point.symbol ? `${point.name} (${point.symbol})` : point.symbol;
  return `${identity}. Theme exposure ${point.exposure.toFixed(1)} out of 5. ${type === 'etf' ? tr('aum') : tr('marketCap')}: ${formatExposureTooltipValue(point.marketValue)}.`;
}

function renderExposureScatterSummary(data, type) {
  const summary = $('#exposure-scatter-summary');
  if (summary) summary.innerHTML = data.map(point => `<li>${esc(exposureScatterDescription(point, type))}</li>`).join('');
}

function exposureScatterGraphics(geometry, data) {
  return geometry.labels.flatMap((label, index) => [
    ...(label.leader ? [{
      id: `exposure-leader-${index}`,
      type: 'line',
      silent: true,
      z: 2,
      shape: label.leader,
      style: { stroke: 'rgba(60,60,67,.34)', lineWidth: 1 },
    }] : []),
    {
      id: `exposure-label-${index}`,
      type: 'text',
      silent: true,
      z: 5,
      x: label.textX,
      y: label.textY,
      style: {
        text: data[index].symbol,
        fill: '#1d1d1f',
        stroke: '#fff',
        lineWidth: 3,
        font: `600 ${EXPOSURE_LABEL_SIZE}px -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`,
        textAlign: 'center',
        textVerticalAlign: 'middle',
      },
    },
  ]);
}

function trimMarketValue(value) {
  return Number(value).toFixed(2).replace(/\.?0+$/, '');
}

function formatExposureTooltipValue(value) {
  if (!Number.isFinite(value) || value <= 0) return '$0';
  if (value >= 1000) return `$${trimMarketValue(value / 1000)}T`;
  if (value < 1) return `$${trimMarketValue(value * 1000)}M`;
  return `$${trimMarketValue(value)}B`;
}

function buildExposureScatterOption(data, geometry, activeIndex = -1) {
  const reduceMotion = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;
  const active = data[activeIndex];
  return {
    backgroundColor: 'transparent',
    animation: !reduceMotion,
    animationDuration: reduceMotion ? 0 : 360,
    aria: { enabled: false },
    grid: {
      top: geometry.margin.top,
      left: geometry.margin.left,
      right: geometry.margin.right,
      bottom: geometry.margin.bottom,
      containLabel: false,
    },
    tooltip: { show: false },
    xAxis: {
      type: 'value', min: 1, max: 5, interval: 1,
      axisTick: { show: false }, axisLine: { show: false },
      splitLine: { lineStyle: { color: 'rgba(0,0,0,.055)', type: 'dashed' } },
      axisLabel: { color: 'rgba(60,60,67,.72)', fontSize: 11, fontWeight: 500, margin: 10, formatter: value => Number(value).toFixed(0) },
    },
    yAxis: {
      type: 'value', min: 0, max: geometry.axis.max, interval: geometry.axis.step,
      axisTick: { show: false }, axisLine: { show: false },
      splitLine: { lineStyle: { color: 'rgba(0,0,0,.055)', type: 'dashed' } },
      axisLabel: { color: 'rgba(60,60,67,.72)', fontSize: 11, fontWeight: 500, margin: 10, formatter: formatMarketScale },
    },
    graphic: exposureScatterGraphics(geometry, data),
    series: [
      {
        id: 'exposure-points',
        name: 'Exposure points',
        type: 'scatter',
        silent: true,
        z: 4,
        symbol: 'circle',
        symbolSize: EXPOSURE_POINT_SIZE,
        data: data.map(point => ({ ...point, name: point.symbol, value: [point.exposure, point.marketValue] })),
        itemStyle: { color: '#265ffc', borderColor: '#fff', borderWidth: 1 },
        emphasis: { disabled: true },
      },
      {
        id: 'exposure-active',
        name: 'Active exposure point',
        type: 'scatter',
        silent: true,
        z: 6,
        symbol: 'circle',
        symbolSize: EXPOSURE_ACTIVE_SIZE,
        data: active ? [[active.exposure, active.marketValue]] : [],
        itemStyle: { color: 'rgba(255,255,255,0)', borderColor: '#265ffc', borderWidth: 2 },
        emphasis: { disabled: true },
      },
    ],
  };
}

function ensureExposureScatterTooltip(runtime) {
  if (runtime.tooltip?.isConnected) return runtime.tooltip;
  const tooltip = document.createElement('div');
  tooltip.className = 'exposure-scatter-tooltip';
  tooltip.dataset.exposureTooltip = '';
  tooltip.setAttribute('role', 'tooltip');
  tooltip.setAttribute('aria-hidden', 'true');
  tooltip.hidden = true;
  runtime.container.append(tooltip);
  runtime.tooltip = tooltip;
  return tooltip;
}

function updateExposureScatterActive(runtime) {
  const point = runtime.data[runtime.activeIndex];
  if (runtime.echarts) {
    runtime.echarts.setOption({
      series: [{
        id: 'exposure-active',
        data: point ? [[point.exposure, point.marketValue]] : [],
      }],
    }, { lazyUpdate: true });
  }
  const ring = $('.exposure-scatter-active-ring', runtime.container);
  if (ring) {
    if (point) {
      const mark = runtime.geometry.marks[runtime.activeIndex];
      ring.setAttribute('cx', String(mark.x));
      ring.setAttribute('cy', String(mark.y));
      ring.removeAttribute('hidden');
    } else ring.setAttribute('hidden', '');
  }
}

function positionExposureScatterTooltip(runtime) {
  const tooltip = ensureExposureScatterTooltip(runtime);
  const mark = runtime.geometry.marks[runtime.activeIndex];
  if (!mark || tooltip.hidden) return;
  const tooltipWidth = tooltip.offsetWidth || 190;
  const tooltipHeight = tooltip.offsetHeight || 82;
  let left = mark.x + 12;
  if (left + tooltipWidth > runtime.geometry.width - 8) left = mark.x - tooltipWidth - 12;
  let top = mark.y - tooltipHeight - 12;
  if (top < 8) top = mark.y + 12;
  tooltip.style.left = `${clampNumber(left, 8, Math.max(8, runtime.geometry.width - tooltipWidth - 8))}px`;
  tooltip.style.top = `${clampNumber(top, 8, Math.max(8, runtime.geometry.height - tooltipHeight - 8))}px`;
}

function showExposureScatterPoint(runtime, index, announce = false) {
  const point = runtime.data[index];
  if (!point) return;
  runtime.activeIndex = index;
  updateExposureScatterActive(runtime);
  const tooltip = ensureExposureScatterTooltip(runtime);
  const marketValueLabel = runtime.type === 'etf' ? tr('aum') : tr('marketCap');
  tooltip.innerHTML = `<div class="exposure-tooltip-identity"><strong>${esc(point.symbol)}</strong>${point.name && point.name !== point.symbol ? `<span>${esc(point.name)}</span>` : ''}</div>
    <div class="exposure-tooltip-values"><span>${esc(tr('exposure'))}<strong>${point.exposure.toFixed(1)}/5</strong></span><span>${esc(marketValueLabel)}<strong>${esc(formatExposureTooltipValue(point.marketValue))}</strong></span></div>`;
  tooltip.hidden = false;
  tooltip.setAttribute('aria-hidden', 'false');
  tooltip.classList.toggle('is-pinned', runtime.pinnedIndex === index);
  positionExposureScatterTooltip(runtime);
  if (announce) {
    const status = $('#exposure-scatter-status');
    if (status) status.textContent = exposureScatterDescription(point, runtime.type);
  }
}

function clearExposureScatterPoint(runtime, clearPinned = false) {
  if (clearPinned) runtime.pinnedIndex = -1;
  runtime.activeIndex = -1;
  updateExposureScatterActive(runtime);
  const tooltip = ensureExposureScatterTooltip(runtime);
  tooltip.hidden = true;
  tooltip.setAttribute('aria-hidden', 'true');
  tooltip.classList.remove('is-pinned');
}

function nearestExposureScatterPoint(runtime, clientX, clientY) {
  const box = runtime.container.getBoundingClientRect();
  const x = (clientX - box.left) * runtime.geometry.width / Math.max(1, box.width);
  const y = (clientY - box.top) * runtime.geometry.height / Math.max(1, box.height);
  let nearest = -1;
  let nearestDistance = Number.POSITIVE_INFINITY;
  runtime.geometry.marks.forEach((mark, index) => {
    const distance = Math.hypot(mark.x - x, mark.y - y);
    if (distance < nearestDistance) {
      nearest = index;
      nearestDistance = distance;
    }
  });
  return nearestDistance <= EXPOSURE_HIT_RADIUS ? nearest : -1;
}

function installExposureScatterInteractions(runtime) {
  const onPointerMove = event => {
    if (event.pointerType && event.pointerType !== 'mouse' && event.pointerType !== 'pen') return;
    const index = nearestExposureScatterPoint(runtime, event.clientX, event.clientY);
    if (index >= 0 && runtime.pinnedIndex < 0) showExposureScatterPoint(runtime, index);
    else if (index < 0 && runtime.pinnedIndex < 0 && document.activeElement !== runtime.container) clearExposureScatterPoint(runtime);
  };
  const onPointerLeave = () => {
    if (runtime.pinnedIndex >= 0) showExposureScatterPoint(runtime, runtime.pinnedIndex);
    else if (document.activeElement !== runtime.container) clearExposureScatterPoint(runtime);
  };
  const onClick = event => {
    const index = nearestExposureScatterPoint(runtime, event.clientX, event.clientY);
    if (index < 0 || runtime.pinnedIndex === index) {
      clearExposureScatterPoint(runtime, true);
      return;
    }
    runtime.pinnedIndex = index;
    showExposureScatterPoint(runtime, index, true);
  };
  const onFocus = event => {
    if (event.target === runtime.container && runtime.activeIndex < 0) showExposureScatterPoint(runtime, 0);
  };
  const onBlur = () => {
    if (runtime.pinnedIndex < 0) clearExposureScatterPoint(runtime);
  };
  const onKeyDown = event => {
    const forward = event.key === 'ArrowRight' || event.key === 'ArrowDown';
    const backward = event.key === 'ArrowLeft' || event.key === 'ArrowUp';
    if (forward || backward || event.key === 'Home' || event.key === 'End') {
      event.preventDefault();
      const current = runtime.activeIndex < 0 ? 0 : runtime.activeIndex;
      const index = event.key === 'Home'
        ? 0
        : event.key === 'End'
          ? runtime.data.length - 1
          : (current + (forward ? 1 : -1) + runtime.data.length) % runtime.data.length;
      showExposureScatterPoint(runtime, index, true);
      return;
    }
    if ((event.key === 'Enter' || event.key === ' ') && runtime.activeIndex >= 0) {
      event.preventDefault();
      runtime.pinnedIndex = runtime.pinnedIndex === runtime.activeIndex ? -1 : runtime.activeIndex;
      showExposureScatterPoint(runtime, runtime.activeIndex, true);
      return;
    }
    if (event.key === 'Escape') {
      event.preventDefault();
      clearExposureScatterPoint(runtime, true);
    }
  };
  const onDocumentPointerDown = event => {
    if (runtime.pinnedIndex >= 0 && !runtime.container.contains(event.target)) clearExposureScatterPoint(runtime, true);
  };
  const onDocumentKeyDown = event => {
    if (event.key === 'Escape' && runtime.pinnedIndex >= 0 && event.target !== runtime.container) clearExposureScatterPoint(runtime, true);
  };
  runtime.container.addEventListener('pointermove', onPointerMove);
  runtime.container.addEventListener('pointerleave', onPointerLeave);
  runtime.container.addEventListener('click', onClick);
  runtime.container.addEventListener('focus', onFocus);
  runtime.container.addEventListener('blur', onBlur);
  runtime.container.addEventListener('keydown', onKeyDown);
  document.addEventListener('pointerdown', onDocumentPointerDown);
  document.addEventListener('keydown', onDocumentKeyDown);
  runtime.cleanup = () => {
    runtime.container.removeEventListener('pointermove', onPointerMove);
    runtime.container.removeEventListener('pointerleave', onPointerLeave);
    runtime.container.removeEventListener('click', onClick);
    runtime.container.removeEventListener('focus', onFocus);
    runtime.container.removeEventListener('blur', onBlur);
    runtime.container.removeEventListener('keydown', onKeyDown);
    document.removeEventListener('pointerdown', onDocumentPointerDown);
    document.removeEventListener('keydown', onDocumentKeyDown);
  };
}

function drawExposureScatterFallback(runtime) {
  const { container, data, type } = runtime;
  const geometry = exposureScatterGeometry(data, container.clientWidth, container.clientHeight);
  runtime.geometry = geometry;
  container.innerHTML = `<svg class="exposure-scatter-svg" viewBox="0 0 ${geometry.width} ${geometry.height}" aria-hidden="true" focusable="false">
    ${[1, 2, 3, 4, 5].map(value => `<g><line x1="${geometry.x(value)}" y1="${geometry.margin.top}" x2="${geometry.x(value)}" y2="${geometry.height - geometry.margin.bottom}" class="chart-grid-line"></line><text x="${geometry.x(value)}" y="${geometry.height - 8}" class="exposure-scatter-axis-text">${value}</text></g>`).join('')}
    ${geometry.yTicks.map(value => `<g><line x1="${geometry.margin.left}" y1="${geometry.y(value)}" x2="${geometry.width - geometry.margin.right}" y2="${geometry.y(value)}" class="chart-grid-line"></line><text x="${geometry.margin.left - 8}" y="${geometry.y(value) + 4}" class="exposure-scatter-y-text">${esc(formatMarketScale(value))}</text></g>`).join('')}
    ${geometry.labels.flatMap(label => label.leader ? [`<line x1="${label.leader.x1}" y1="${label.leader.y1}" x2="${label.leader.x2}" y2="${label.leader.y2}" class="exposure-scatter-leader"></line>`] : []).join('')}
    ${data.map((point, index) => `<circle cx="${geometry.marks[index].x}" cy="${geometry.marks[index].y}" r="${EXPOSURE_POINT_SIZE / 2}" class="exposure-scatter-point" data-exposure-marker data-market-code="${esc(point.marketCode)}"><title>${esc(exposureScatterDescription(point, type))}</title></circle>`).join('')}
    <circle cx="0" cy="0" r="${EXPOSURE_ACTIVE_SIZE / 2}" class="exposure-scatter-active-ring" hidden></circle>
    ${data.map((point, index) => `<text x="${geometry.labels[index].textX}" y="${geometry.labels[index].textY}" class="exposure-scatter-label" data-exposure-label="${esc(point.symbol)}">${esc(point.symbol)}</text>`).join('')}
  </svg>`;
  ensureExposureScatterTooltip(runtime);
  updateExposureScatterActive(runtime);
  if (runtime.activeIndex >= 0) showExposureScatterPoint(runtime, runtime.activeIndex);
}

function destroyExposureScatter() {
  exposureScatterRenderToken += 1;
  clearTimeout(exposureScatterRuntime?.resizeTimer);
  exposureScatterRuntime?.observer?.disconnect();
  exposureScatterRuntime?.cleanup?.();
  exposureScatterRuntime?.chart?.destroy();
  exposureScatterRuntime = null;
}

async function renderExposureScatter(container, points, type) {
  const data = exposureScatterPoints(points, type);
  destroyExposureScatter();
  container.replaceChildren();
  renderExposureScatterSummary(data, type);
  const status = $('#exposure-scatter-status');
  if (status) status.textContent = '';
  if (!data.length) {
    container.removeAttribute('tabindex');
    container.innerHTML = feedbackState({ title: tr('noChartData'), type: 'empty' });
    return;
  }
  container.tabIndex = 0;
  const token = ++exposureScatterRenderToken;
  let chart = null;
  try {
    const library = await ensureStandardChart();
    if (token !== exposureScatterRenderToken || !container.isConnected) return;
    chart = library.init(container, null, { renderer: 'svg' });
    const runtime = {
      kind: 'chart', chart, echarts: null, observer: null, cleanup: null, tooltip: null,
      container, data, type, geometry: exposureScatterGeometry(data, container.clientWidth, container.clientHeight),
      activeIndex: -1, pinnedIndex: -1, resizeTimer: 0,
    };
    exposureScatterRuntime = runtime;
    const syncEngine = () => {
      if (exposureScatterRuntime !== runtime) return;
      runtime.echarts = chart.getECharts?.() || runtime.echarts;
      runtime.echarts?.resize();
      updateExposureScatterActive(runtime);
    };
    chart.on('dv:afterinit', syncEngine);
    chart.play({
      option: buildExposureScatterOption(data, runtime.geometry, runtime.activeIndex),
      opts: { replaceMerge: ['graphic', 'series'] },
    });
    ensureExposureScatterTooltip(runtime);
    installExposureScatterInteractions(runtime);
    requestAnimationFrame(syncEngine);
    const observer = new ResizeObserver(() => {
      clearTimeout(runtime.resizeTimer);
      runtime.resizeTimer = setTimeout(() => {
        if (exposureScatterRuntime !== runtime) return;
        runtime.geometry = exposureScatterGeometry(data, container.clientWidth, container.clientHeight);
        chart.getECharts?.()?.resize();
        chart.play({
          option: buildExposureScatterOption(data, runtime.geometry, runtime.activeIndex),
          opts: { replaceMerge: ['graphic', 'series'] },
        });
        ensureExposureScatterTooltip(runtime);
        requestAnimationFrame(syncEngine);
        if (runtime.activeIndex >= 0) positionExposureScatterTooltip(runtime);
      }, 180);
    });
    observer.observe(container);
    runtime.observer = observer;
  } catch {
    chart?.destroy?.();
    if (token !== exposureScatterRenderToken || !container.isConnected) return;
    const runtime = {
      kind: 'fallback', chart: null, echarts: null, observer: null, cleanup: null, tooltip: null,
      container, data, type, geometry: exposureScatterGeometry(data, container.clientWidth, container.clientHeight),
      activeIndex: -1, pinnedIndex: -1, resizeTimer: 0,
    };
    exposureScatterRuntime = runtime;
    drawExposureScatterFallback(runtime);
    installExposureScatterInteractions(runtime);
    const observer = new ResizeObserver(() => {
      clearTimeout(runtime.resizeTimer);
      runtime.resizeTimer = setTimeout(() => {
        if (exposureScatterRuntime === runtime) drawExposureScatterFallback(runtime);
      }, 180);
    });
    observer.observe(container);
    runtime.observer = observer;
  }
}

function sortRows(rows, sort) {
  if (!sort) return [...rows];
  const direction = sort.order === 'asc' ? 1 : -1;
  return [...rows].sort((left, right) => {
    const leftValue = left[sort.key] ?? Number.NEGATIVE_INFINITY;
    const rightValue = right[sort.key] ?? Number.NEGATIVE_INFINITY;
    return (Number(leftValue) - Number(rightValue)) * direction;
  });
}

function valueOpportunityUrl(row, type) {
  const code = ticker(row.marketCode || row.id);
  const market = marketId(row.marketCode || row.id);
  const exchange = type === 'etf'
    ? market === '169' ? 'arca' : market === '171' ? 'cboe' : market === '185' || market === '186' ? 'nasdaq' : market === '170' ? 'amex' : ''
    : market === '185' || market === '186' ? 'nasdaq' : market === '169' ? 'nyse' : market === '170' ? 'amex' : market === '171' ? 'cboe' : '';
  return exchange && code !== '--'
    ? `${VALUE_INVESTMENT_BASE_URL}/${exchange}/${encodeURIComponent(code.toLowerCase())}`
    : '';
}

function symbolLogoUrl(row, type) {
  const code = encodeURIComponent(ticker(row.marketCode || row.id));
  return type === 'etf'
    ? `https://cdn.ainvest.com/icon/us/etf/${code}.png`
    : `https://cdn.ainvest.com/icon/us/${code}.png`;
}

function sortHeader(panel, key, label, location = 'desktop') {
  const active = panel.sort?.key === key;
  const order = active ? panel.sort.order : '';
  const symbol = order === 'asc' ? '↑' : order === 'desc' ? '↓' : '↕';
  return `<button type="button" class="sort-header${active ? ' active' : ''}" data-sort="${key}" data-sort-location="${location}" aria-label="${esc(tr('sortBy'))} ${esc(label)}${order ? ` ${order}` : ''}">${esc(label)} <span aria-hidden="true">${symbol}</span></button>`;
}

function mobileSortButton(panel, key, label, accessibleLabel = label) {
  const active = panel.sort?.key === key;
  const order = active ? panel.sort.order : '';
  const symbol = order === 'asc' ? '↑' : order === 'desc' ? '↓' : '↕';
  return `<button type="button" class="mobile-sort-button${active ? ' active' : ''}" data-sort="${key}" data-sort-location="mobile" aria-pressed="${active}" aria-label="${esc(tr('sortBy'))} ${esc(accessibleLabel)}${order ? ` ${order}` : ''}">${esc(label)} <span aria-hidden="true">${symbol}</span></button>`;
}

function sortDirection(panel, key) {
  if (panel.sort?.key !== key) return 'none';
  return panel.sort.order === 'asc' ? 'ascending' : 'descending';
}

function quoteTable(rows, type) {
  const panel = getPanel(type);
  const sourceByCode = new Map();
  getSelections(detailModel.data, type).forEach(item => {
    if (!sourceByCode.has(item.market_code)) sourceByCode.set(item.market_code, item);
  });
  const sourceItems = [...sourceByCode.values()];
  const selectionByCode = new Map(sourceItems.map(item => [item.market_code, item]));
  const liveByCode = new Map(
    (Array.isArray(rows) ? rows : [])
      .filter(row => row?.marketCode)
      .map(row => [row.marketCode, row]),
  );
  const mergedRows = sourceItems.map(item => {
    const marketCode = item.market_code;
    return {
      symbol: ticker(marketCode),
      name: null,
      last: null,
      changePercent: null,
      marketValue: null,
      exposure: Number(item['Theme exposure']),
      rationale: item.theme_rationale ?? null,
      ...(liveByCode.get(marketCode) || {}),
      marketCode,
    };
  });
  const sorted = sortRows(mergedRows, panel.sort);
  const changeLabel = detailModel.data.date ? `${tr('changeSince')} ${formatDate(detailModel.data.date, true)}` : tr('change');
  const compactChangeLabel = detailModel.data.date ? `${tr('since')} ${formatCompactDate(detailModel.data.date)}` : tr('change');
  const valueLabel = type === 'etf' ? tr('aum') : tr('marketCapTable');
  if (!sorted.length) return feedbackState({ title: tr('noData'), type: 'empty' });
  return `<div class="stock-table-heading">
      <h2>${sorted.length} ${esc(tr('securities'))}</h2>
      <div class="stock-mobile-sort" role="group" aria-label="${esc(tr('sortBy'))}">
        ${mobileSortButton(panel, 'last', tr('last'))}
        ${mobileSortButton(panel, 'changePercent', compactChangeLabel, changeLabel)}
        ${mobileSortButton(panel, 'marketValue', valueLabel)}
      </div>
    </div>
    <div class="stock-table-frame">
      <div class="stock-table-scroll">
        <div class="stock-table" role="table" aria-label="${type === 'etf' ? 'ETF' : 'Stock'} quotes">
          <div class="stock-table-head" role="row">
            <span role="columnheader">${esc(tr('security'))}</span>
            <span role="columnheader" aria-sort="${sortDirection(panel, 'last')}">${sortHeader(panel, 'last', tr('last'))}</span>
            <span role="columnheader" aria-sort="${sortDirection(panel, 'changePercent')}">${sortHeader(panel, 'changePercent', changeLabel)}</span>
            <span role="columnheader" aria-sort="${sortDirection(panel, 'marketValue')}">${sortHeader(panel, 'marketValue', valueLabel)}</span>
          </div>
      ${sorted.map(row => {
        const source = selectionByCode.get(row.marketCode) || {};
        const rationale = localizedText(row.rationale ?? source.theme_rationale, '') || '--';
        const change = Number(row.changePercent);
        const link = valueOpportunityUrl(row, type);
        const displaySymbol = row.symbol || ticker(row.marketCode);
        const displayName = row.name || displaySymbol;
        const changeValue = row.changePercent == null ? '--' : `${change >= 0 ? '+' : ''}${change.toFixed(2)}%`;
        const identity = `<img class="symbol-logo" src="${esc(symbolLogoUrl(row, type))}" alt="" width="28" height="28" loading="lazy">
              <span class="symbol-copy"><strong>${esc(displayName)}</strong><small>${esc(displaySymbol)}</small></span>`;
        return `<div class="stock-record" role="rowgroup">
          <div class="stock-data-row" role="row">
            <div class="stock-symbol-cell" role="cell">
              ${link ? `<a class="stock-symbol-link" href="${esc(link)}" aria-label="Open ${esc(displayName)} investment analysis">${identity}</a>` : `<span class="stock-symbol-link">${identity}</span>`}
            </div>
            <div class="stock-metric-cell numeric-cell" role="cell"><span class="stock-metric-label">${esc(tr('last'))}</span><span class="stock-metric-value">${formatNumber(row.last, 2)}</span></div>
            <div class="stock-metric-cell numeric-cell ${row.changePercent == null ? '' : change >= 0 ? 'price-up' : 'price-down'}" role="cell" aria-label="${esc(`${changeLabel}: ${changeValue}`)}"><span class="stock-metric-label">${esc(compactChangeLabel)}</span><span class="stock-metric-value">${changeValue}</span></div>
            <div class="stock-metric-cell numeric-cell" role="cell"><span class="stock-metric-label">${esc(valueLabel)}</span><span class="stock-metric-value">${esc(formatMarketValue(row.marketValue))}</span></div>
          </div>
          <div class="stock-rationale-row" role="row">
            <div class="stock-rationale-cell" role="cell" aria-colspan="4">
              <span class="stock-rationale-label">${esc(tr('rationale'))}</span>
              <p class="stock-rationale-text">${esc(rationale)}</p>
            </div>
          </div>
        </div>`;
      }).join('')}
        </div>
      </div>
    </div>
  `;
}

function renderPerformance(type) {
  const container = $('#performance-plot');
  if (!container || detailModel.activeType !== type) return;
  if (detailModel.performanceState === 'loading' || detailModel.performanceState === 'idle') {
    destroyPerformanceChart();
    container.innerHTML = performanceSkeleton();
  } else if (detailModel.performanceState === 'error') {
    destroyPerformanceChart();
    container.innerHTML = feedbackState({ title: tr('unableChart') });
  } else {
    const period = getPanel(type).period;
    void renderPerformanceChart(container, detailModel.performance?.series?.[type]?.[period] || []);
  }
}

function renderExposure(type) {
  const panel = getPanel(type);
  const container = $('#exposure-scatter-plot');
  if (!container || detailModel.activeType !== type) return;
  if (panel.exposureState === 'loading' || panel.exposureState === 'idle') {
    destroyExposureScatter();
    renderExposureScatterSummary([], type);
    container.removeAttribute('tabindex');
    container.innerHTML = spinner(tr('loadingExposure'));
  } else if (panel.exposureState === 'error') {
    destroyExposureScatter();
    renderExposureScatterSummary([], type);
    container.removeAttribute('tabindex');
    container.innerHTML = feedbackState({ title: tr('unableChart'), action: 'exposure' });
  } else {
    void renderExposureScatter(container, panel.exposure?.points || [], type);
  }
}

function announceQuoteStatus(message) {
  const status = $('#quote-table-status');
  if (!status) return;
  status.textContent = '';
  requestAnimationFrame(() => {
    if (status.isConnected) status.textContent = message;
  });
}

function renderQuotes(type) {
  const panel = getPanel(type);
  const container = $('#quote-table-root');
  if (!container || detailModel.activeType !== type) return;
  if (panel.quotesState === 'loading' || panel.quotesState === 'idle') {
    container.innerHTML = quoteSkeleton(type);
  } else if (panel.quotesState === 'error') {
    container.innerHTML = quoteTable([], type);
  } else {
    container.innerHTML = quoteTable(panel.quotes?.rows || [], type);
  }
}

function renderPanel(type) {
  const root = $('#active-panel');
  if (!root) return;
  root.innerHTML = panelMarkup(type);
  renderPerformance(type);
  renderExposure(type);
  renderQuotes(type);
}

async function loadPerformance(force = false) {
  if (!force && detailModel.performanceState === 'success') {
    renderPerformance(detailModel.activeType);
    return;
  }
  if (!force && detailModel.performanceState === 'loading') return detailModel.performancePromise;
  detailModel.performanceState = 'loading';
  renderPerformance(detailModel.activeType);
  const loadForModel = detailModel;
  const promise = request(`/api/themes/${encodeURIComponent(loadForModel.id)}/live`);
  loadForModel.performancePromise = promise;
  try {
    const data = await promise;
    if (detailModel !== loadForModel || loadForModel.performancePromise !== promise) return;
    loadForModel.performance = data;
    loadForModel.performanceState = 'success';
  } catch {
    if (detailModel !== loadForModel || loadForModel.performancePromise !== promise) return;
    loadForModel.performance = null;
    loadForModel.performanceState = 'error';
  } finally {
    if (detailModel === loadForModel && loadForModel.performancePromise === promise) loadForModel.performancePromise = null;
  }
  if (detailModel === loadForModel) renderPerformance(detailModel.activeType);
}

async function loadExposure(type, force = false) {
  const panel = getPanel(type);
  if (!force && (panel.exposureState === 'loading' || panel.exposureState === 'success')) return;
  panel.exposureState = 'loading';
  renderExposure(type);
  try {
    panel.exposure = await request(`/api/themes/${encodeURIComponent(detailModel.id)}/exposure-map?securityType=${type}`);
    panel.exposureState = 'success';
  } catch {
    panel.exposure = null;
    panel.exposureState = 'error';
  }
  renderExposure(type);
}

async function loadQuotes(type, force = false) {
  const panel = getPanel(type);
  if (!force && (panel.quotesState === 'loading' || panel.quotesState === 'success')) return;
  panel.quotesState = 'loading';
  renderQuotes(type);
  try {
    panel.quotes = await request(`/api/themes/${encodeURIComponent(detailModel.id)}/quotes?securityType=${type}`);
    panel.quotesState = 'success';
  } catch {
    panel.quotes = null;
    panel.quotesState = 'error';
  }
  renderQuotes(type);
  if (detailModel.activeType === type) {
    if (panel.exposureState === 'success') renderExposure(type);
    announceQuoteStatus(panel.quotesState === 'success'
      ? `${getSelections(detailModel.data, type).length} ${tr('securities')} loaded.`
      : tr('unableData'));
  }
}

function loadPanel(type, force = false) {
  return Promise.all([
    loadPerformance(force),
    loadExposure(type, force),
    loadQuotes(type, force),
  ]);
}

function faqMarkup(faq) {
  const rows = (Array.isArray(faq) ? faq : []).flatMap(item => {
    const question = localizedText(item?.question);
    const answer = localizedText(item?.answer);
    return question && answer ? [{ question, answer }] : [];
  });
  if (!rows.length) return '';
  return `<section class="event-faq">
    <h2>${esc(tr('themeFaq'))}</h2>
    <div class="faq-list">
      ${rows.map((item, index) => `<details${index === 0 ? ' open' : ''}><summary>${esc(item.question)}</summary><p>${esc(item.answer)}</p></details>`).join('')}
    </div>
  </section>`;
}

function renderDetailPage() {
  destroyPerformanceChart();
  destroyExposureScatter();
  document.body.className = 'event-body';
  const theme = detailModel.data.theme || 'Event theme';
  const cover = safeUrl(detailModel.data.cover);
  const canonical = `${location.origin}/themes/${encodeURIComponent(detailModel.id)}`;
  updateMeta({ title: `${theme} — Market Theme Signal | ThemeSignal`, description: themeDescription(detailModel.data), canonical, image: cover });
  document.body.innerHTML = `<div class="event-theme-page">
    <main class="event-theme-stack">
      <div id="event-hero-root">${eventHero(detailModel.data, detailModel.activeType)}</div>
      <div id="active-panel">${panelMarkup(detailModel.activeType)}</div>
      ${faqMarkup(detailModel.data.ThemeFAQ)}
    </main>
  </div>`;
  renderPerformance(detailModel.activeType);
  renderExposure(detailModel.activeType);
  renderQuotes(detailModel.activeType);
}

function switchSecurityType(type) {
  if (!detailModel || detailModel.activeType === type || !getSelections(detailModel.data, type).length) return;
  detailModel.activeType = type;
  const panel = getPanel(type);
  panel.period = '1M';
  panel.sort = null;
  panel.quotesState = 'idle';
  $('#event-hero-root').innerHTML = eventHero(detailModel.data, type);
  renderPanel(type);
  void Promise.all([
    loadPerformance(),
    loadExposure(type),
    loadQuotes(type, true),
  ]);
}

function cycleSort(type, key) {
  const panel = getPanel(type);
  if (panel.sort?.key !== key) panel.sort = { key, order: 'desc' };
  else panel.sort = { key, order: panel.sort.order === 'desc' ? 'asc' : 'desc' };
  renderQuotes(type);
  const changeLabel = detailModel.data.date ? `${tr('changeSince')} ${formatDate(detailModel.data.date, true)}` : tr('change');
  const label = key === 'last' ? tr('last') : key === 'changePercent' ? changeLabel : type === 'etf' ? tr('aum') : tr('marketCapTable');
  announceQuoteStatus(`${getSelections(detailModel.data, type).length} ${tr('securities')} sorted by ${label}, ${panel.sort.order === 'asc' ? 'ascending' : 'descending'}.`);
}

function installDetailEvents() {
  if (detailEventsInstalled) return;
  detailEventsInstalled = true;
  document.body.addEventListener('click', event => {
    const securityTab = event.target.closest('[data-security-type]');
    if (securityTab) {
      const nextType = securityTab.dataset.securityType === 'etf' ? 'etf' : 'stock';
      const changed = detailModel.activeType !== nextType;
      switchSecurityType(nextType);
      if (changed) requestAnimationFrame(() => $(`[data-security-type="${nextType}"]`)?.focus());
      return;
    }
    const periodTab = event.target.closest('[data-period]');
    if (periodTab) {
      const panel = getPanel(detailModel.activeType);
      const nextPeriod = ['1M', '6M', '1Y'].includes(periodTab.dataset.period) ? periodTab.dataset.period : '1M';
      panel.period = nextPeriod;
      $$('[data-period]').forEach(button => {
        const selected = button.dataset.period === nextPeriod;
        button.classList.toggle('active', selected);
        button.setAttribute('aria-selected', String(selected));
        button.tabIndex = selected ? 0 : -1;
      });
      renderPerformance(detailModel.activeType);
      return;
    }
    const retry = event.target.closest('[data-retry]');
    if (retry) {
      const action = retry.dataset.retry;
      if (action === 'detail') void detail(detailModel.id);
      if (action === 'performance') void loadPerformance(true);
      if (action === 'exposure') void loadExposure(detailModel.activeType, true);
      if (action === 'quotes') void loadQuotes(detailModel.activeType, true);
      return;
    }
    const sort = event.target.closest('[data-sort]');
    if (sort) {
      const key = sort.dataset.sort;
      const sortLocation = sort.dataset.sortLocation;
      cycleSort(detailModel.activeType, key);
      requestAnimationFrame(() => $(`[data-sort="${CSS.escape(key)}"][data-sort-location="${CSS.escape(sortLocation)}"]`)?.focus());
      return;
    }
  });
  document.body.addEventListener('keydown', event => {
    const tab = event.target.closest?.('[role="tab"]');
    if (tab && ['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) {
      const tabList = tab.closest('[role="tablist"]');
      const tabs = $$('[role="tab"]', tabList);
      const current = tabs.indexOf(tab);
      const next = event.key === 'Home' ? 0 : event.key === 'End' ? tabs.length - 1 : (current + (event.key === 'ArrowRight' ? 1 : -1) + tabs.length) % tabs.length;
      event.preventDefault();
      tabs[next]?.focus();
      tabs[next]?.click();
      return;
    }
  });
  window.addEventListener('resize', () => {
    cancelAnimationFrame(resizeFrame);
    resizeFrame = requestAnimationFrame(() => {
      if (!detailModel) return;
      performanceRuntime?.chart?.timeScale().fitContent();
      exposureScatterRuntime?.chart?.getECharts()?.resize();
    });
  });
}

async function detail(id) {
  destroyPerformanceChart();
  destroyExposureScatter();
  document.body.className = 'event-body';
  const initial = bootData?.route === 'detail' && bootData.id === id ? bootData : null;
  document.body.innerHTML = initial?.data
    ? `<div class="event-theme-page"><div class="page-loading">${spinner('Loading event theme')}</div></div>`
    : `<div class="event-theme-page"><div class="page-loading">${spinner('Loading event theme')}</div></div>`;
  try {
    const data = initial?.data || await request(`/api/themes/${encodeURIComponent(id)}`);
    const canonical = `${location.origin}/themes/${encodeURIComponent(id)}`;
    updateMeta({ title: `${data.theme || 'Event theme'} — Market Theme Signal | ThemeSignal`, description: themeDescription(data), canonical, image: safeUrl(data.cover) });
    if (!getSelections(data, 'stock').length && !getSelections(data, 'etf').length) {
      detailModel = { id, data, activeType: 'stock', performance: null, performanceState: 'idle', performancePromise: null };
      document.body.innerHTML = `<div class="event-theme-page"><div class="page-loading">${feedbackState({ title: tr('noData'), type: 'empty' })}</div></div>`;
      installDetailEvents();
      return;
    }
    const initialType = getSelections(data, 'stock').length ? 'stock' : 'etf';
    detailModel = { id, data, activeType: initialType, performance: null, performanceState: 'idle', performancePromise: null };
    renderDetailPage();
    installDetailEvents();
    await loadPanel(initialType);
  } catch {
    updateMeta({ title: 'Theme not found | ThemeSignal', description: 'The requested market theme could not be found.', canonical: `${location.origin}/` });
    detailModel = { id, data: {}, activeType: 'stock', performance: null, performanceState: 'idle', performancePromise: null };
    document.body.innerHTML = `<div class="event-theme-page"><div class="page-loading">${feedbackState({ title: tr('unableData'), action: 'detail' })}</div></div>`;
    installDetailEvents();
  }
}

const detailMatch = location.pathname.match(/^\/themes\/([^/]+)\/?$/);
if (detailMatch) void detail(decodeURIComponent(detailMatch[1]));
else void list();
