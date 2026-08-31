import {
  DEFAULT_SITE_ORIGIN,
  HOME_DESCRIPTION,
  HOME_TITLE,
  archiveCardMarkup,
  archiveJsonLd,
  canonicalHomeUrl,
  canonicalThemeUrl,
  detailJsonLd,
  escapeHtml,
  faqRows,
  formatDate,
  normalizeOrigin,
  safeHttpUrl,
  safeJson,
  themeDescription,
  titleForTheme,
  validDate,
  xmlEscape,
  type ThemeListRow,
} from './seo.ts';

export interface Env { DB: D1Database; ASSETS: Fetcher; INGEST_TOKEN?: string; QUOTE_C_COOKIE?: string; SNAPSHOT_URL?: string; VISITOR_AUTH_URL?: string; SITE_ORIGIN?: string; }
type SecurityType = 'stock' | 'etf'; type Period = '1M' | '6M' | '1Y';
type ThemeRecord = ThemeListRow;
type ThemeRawRecord = { raw_json: string; content_sha256: string; id?: string; theme?: string; created_at?: string; event_date?: string | null; source_url?: string | null; cover_url?: string | null; stock_count?: number; etf_count?: number };
const DEFAULT_LIST_LIMIT = 24;
const MAX_LIST_LIMIT = 100;
const DEFAULT_SNAPSHOT_URL = 'https://extquote.ainvest.com/index_api/indicator/v2/snapshot';
const DEFAULT_VISITOR_AUTH_URL = 'https://user.ainvest.com/auth/visitor/login';
const DOLLARS_PER_BILLION = 1_000_000_000;
const newYorkDateFormatter = new Intl.DateTimeFormat('en-US',{timeZone:'America/New_York',year:'numeric',month:'2-digit',day:'2-digit'});
const jsonHeaders = { 'content-type': 'application/json; charset=utf-8' };
const corsHeaders = { 'access-control-allow-origin': '*', 'access-control-allow-headers': 'content-type, authorization, x-idempotency-key, x-theme-upload-token', 'access-control-allow-methods': 'GET, POST, OPTIONS' };
let visitorCookiePromise:Promise<string|null>|null=null;
export const PERFORMANCE_INDICATORS = [['hotevent_etf_month1_chg_ratio','0','etf','1M'],['hotevent_etf_month6_chg_ratio','1','etf','6M'],['hotevent_etf_year1_chg_ratio','2','etf','1Y'],['hotevent_stock_month1_chg_ratio','3','stock','1M'],['hotevent_stock_month6_chg_ratio','4','stock','6M'],['hotevent_stock_year1_chg_ratio','5','stock','1Y']] as const;
function response(body: BodyInit | null, init: ResponseInit = {}) { const headers = new Headers(init.headers); for (const [k,v] of Object.entries(corsHeaders)) headers.set(k,v); return new Response(body,{...init,headers}); }
function error(message: string,status=400){return response(JSON.stringify({error:message}),{status,headers:{...jsonHeaders,'x-robots-tag':'noindex'}});}
function unavailable(status=200){return response(JSON.stringify({unavailable:true,error:'Live market data unavailable'}),{status,headers:{...jsonHeaders,'cache-control':'no-store','x-robots-tag':'noindex'}});}
function htmlResponse(body: string, init: ResponseInit = {}) {
  return response(body, { ...init, headers: { 'content-type': 'text/html; charset=utf-8', 'cache-control': 'public, max-age=60, must-revalidate', ...init.headers } });
}
function textResponse(body: string, contentType: string, init: ResponseInit = {}) {
  return response(body, { ...init, headers: { 'content-type': contentType, 'cache-control': 'public, max-age=300, must-revalidate', ...init.headers } });
}
function noindexHeaders(headers: HeadersInit = {}) { return { ...headers, 'x-robots-tag': 'noindex, nofollow' }; }
function authOk(request: Request,env: Env){if(!env.INGEST_TOKEN)return false;const value=request.headers.get('authorization')||'';const alternate=request.headers.get('x-theme-upload-token')||'';return value===`Bearer ${env.INGEST_TOKEN}`||alternate===env.INGEST_TOKEN;}
async function sha256(text:string){const bytes=await crypto.subtle.digest('SHA-256',new TextEncoder().encode(text));return [...new Uint8Array(bytes)].map(b=>b.toString(16).padStart(2,'0')).join('');}
function idFromHash(hash:string){return `theme_${hash.slice(0,24)}`;} function isRecord(value:unknown):value is Record<string,any>{return Boolean(value)&&typeof value==='object'&&!Array.isArray(value);}
function requestLanguage(request:Request){return (request.headers.get('accept-language')||'en').slice(0,128);}
function languageCacheKey(language:string){return language.toLowerCase().replace(/[^a-z0-9-]+/g,'_').slice(0,64)||'en';}
export function isHotEventStrategyId(value:unknown):value is string{return typeof value==='string'&&/^hotevent_id_.+$/.test(value.trim());}
export function deriveHotEventStrategyId(theme:unknown){if(typeof theme!=='string')return null;const slug=theme.normalize('NFKD').replace(/[\u0300-\u036f]/g,'').toLowerCase().trim().replace(/[’']/g,'').replace(/[^a-z0-9]+/g,'_').replace(/^_+|_+$/g,'');return slug?`hotevent_id_${slug}`:null;}
export function resolveHotEventStrategyId(value:unknown){if(!isRecord(value))return null;for(const key of ['strategy_id','strategyId'] as const){if(!Object.hasOwn(value,key))continue;const candidate=value[key];if(isHotEventStrategyId(candidate))return candidate.trim();}return Object.hasOwn(value,'theme')?deriveHotEventStrategyId(value.theme):null;}
export function buildPerformanceRequest(strategyId:string){const groupId=strategyId.trim();if(!isHotEventStrategyId(groupId))throw new Error('Invalid hot-event strategy id');return {symbol:[{type:'group_id_self',value:[groupId]}],indicator:PERFORMANCE_INDICATORS.map(([id,req_unique_id])=>({id,req_unique_id})),page:{begin:0,count:1}};}
export function buildExposureRequest(symbols:string[]){return {symbol:[{type:'market_code',value:symbols}],indicator:[{id:'total_market_value',req_unique_id:'marketValue'}],page:{begin:0,count:symbols.length}};}
function rangePeriod(date?:string,now=Date.now()){if(!date)return'day_1';const time=Date.parse(`${date}T12:00:00Z`);return Number.isFinite(time)?`day_${Math.max(1,Math.ceil((now-time)/86400000))}`:'day_1';}
export function buildQuotesRequest(symbols:string[],date?:string,now=Date.now()){return {symbol:[{type:'market_code',value:symbols}],indicator:[{id:'55',req_unique_id:'name'},{id:'10',req_unique_id:'last',attr:{trade_class:'intraday'}},{id:'inr-price_change_ratio_pct-sum',req_unique_id:'changePercent',attr:{trade_class:'intraday',time_period:rangePeriod(date,now)}},{id:'total_market_value',req_unique_id:'marketCap'}],page:{begin:0,count:symbols.length}};}
export function normalizeThemeName(value:unknown){
  if(typeof value!=='string')return null;
  const displayName=value.normalize('NFKC').trim().replace(/\s+/gu,' ');
  return displayName?{displayName,key:displayName.toLocaleLowerCase('en-US')}:null;
}
async function ingest(request:Request,env:Env){
  if(!authOk(request,env))return error('Unauthorized',401);
  const raw=await request.text();
  if(!raw||raw.length>2000000)return error('Result must be non-empty and under 2 MB',413);
  let parsed:Record<string,any>;
  try{parsed=JSON.parse(raw);}catch{return error('Invalid JSON',422);}
  const themeName=normalizeThemeName(parsed.theme);
  if(!themeName)return error('Result must include a non-empty theme name',422);
  const computedHash=await sha256(raw),hash=request.headers.get('x-content-sha256')||computedHash;
  if(!/^[a-f0-9]{64}$/.test(hash)||hash!==computedHash)return error('Invalid content hash',422);
  const candidateId=idFromHash(hash);
  const exactMatch=await env.DB.prepare('SELECT id FROM themes WHERE content_sha256 = ?').bind(hash).first<{id:string}>();
  if(exactMatch)return response(JSON.stringify({id:exactMatch.id,duplicate:true,replaced:false}),{headers:jsonHeaders});
  const previous=await env.DB.prepare('SELECT id FROM themes WHERE theme_key = ?').bind(themeName.key).first<{id:string}>();
  const now=new Date().toISOString();
  const stored=await env.DB.prepare(`INSERT INTO themes
    (id,created_at,event_date,theme,theme_key,source_url,cover_url,stock_count,etf_count,content_sha256,byte_length,raw_json)
    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
    ON CONFLICT(theme_key) DO UPDATE SET
      created_at=excluded.created_at,
      event_date=excluded.event_date,
      theme=excluded.theme,
      source_url=excluded.source_url,
      cover_url=excluded.cover_url,
      stock_count=excluded.stock_count,
      etf_count=excluded.etf_count,
      content_sha256=excluded.content_sha256,
      byte_length=excluded.byte_length,
      raw_json=excluded.raw_json
    RETURNING id`).bind(
    candidateId,now,typeof parsed.date==='string'?parsed.date:null,themeName.displayName,themeName.key,
    typeof parsed.url==='string'?parsed.url:null,typeof parsed.cover==='string'?parsed.cover:null,
    Array.isArray(parsed.ThemeStocks)?parsed.ThemeStocks.length:0,
    Array.isArray(parsed.ThemeEtfs)?parsed.ThemeEtfs.length:0,
    hash,new TextEncoder().encode(raw).byteLength,raw,
  ).first<{id:string}>();
  if(!stored?.id)throw new Error('Theme upsert did not return an archive id');
  const replaced=Boolean(previous)||stored.id!==candidateId;
  return response(JSON.stringify({id:stored.id,duplicate:false,replaced}),{
    status:replaced?200:201,
    headers:jsonHeaders,
  });
}
function parseListOptions(request: Request) {
  const url = new URL(request.url);
  const q = (url.searchParams.get('q') || '').trim().slice(0, 120);
  const requestedLimit = Number(url.searchParams.get('limit') || DEFAULT_LIST_LIMIT);
  const limit = Math.min(Math.max(Number.isFinite(requestedLimit) ? Math.floor(requestedLimit) : DEFAULT_LIST_LIMIT, 1), MAX_LIST_LIMIT);
  const requestedPage = Number(url.searchParams.get('page') || 1);
  const page = Math.max(Number.isFinite(requestedPage) ? Math.floor(requestedPage) : 1, 1);
  const sort: 'old' | 'new' = url.searchParams.get('sort') === 'old' ? 'old' : 'new';
  return { q, limit, page, sort };
}

export async function queryThemes(env: Env, options: { q?: string; limit?: number; page?: number; sort?: 'new' | 'old' } = {}) {
  const q = (options.q || '').trim();
  const limit = Math.min(Math.max(Math.floor(options.limit || DEFAULT_LIST_LIMIT), 1), MAX_LIST_LIMIT);
  const page = Math.max(Math.floor(options.page || 1), 1);
  const sort = options.sort === 'old' ? 'ASC' : 'DESC';
  const where = q ? ' WHERE theme LIKE ?' : '';
  const countStatement = env.DB.prepare(`SELECT COUNT(*) AS total FROM themes${where}`);
  const rowStatement = env.DB.prepare(`SELECT id,created_at,event_date,theme,source_url,cover_url,stock_count,etf_count FROM themes${where} ORDER BY created_at ${sort}, rowid ${sort} LIMIT ? OFFSET ?`);
  const [count, rows] = q
    ? await Promise.all([countStatement.bind(`%${q}%`).first<{ total: number }>(), rowStatement.bind(`%${q}%`, limit, (page - 1) * limit).all()])
    : await Promise.all([countStatement.bind().first<{ total: number }>(), rowStatement.bind(limit, (page - 1) * limit).all()]);
  const total = Number(count?.total || 0);
  return { themes: (rows.results || []) as unknown as ThemeRecord[], page, limit, total, has_more: page * limit < total, q, sort: options.sort === 'old' ? 'old' : 'new' as const };
}

async function listThemes(request:Request,env:Env){
  const url = new URL(request.url);
  const options = parseListOptions(request);
  const result = await queryThemes(env, options);
  const hasExplicitPagination = ['page', 'limit', 'sort'].some(key => url.searchParams.has(key));
  const payload = hasExplicitPagination ? result : { themes: result.themes };
  return response(JSON.stringify(payload),{headers:noindexHeaders(jsonHeaders)});
}

async function getRaw(id:string,env:Env){return env.DB.prepare(`SELECT id,theme,created_at,event_date,source_url,cover_url,stock_count,etf_count,raw_json,content_sha256 FROM themes WHERE id = ?
  UNION ALL
  SELECT themes.id,themes.theme,themes.created_at,themes.event_date,themes.source_url,themes.cover_url,themes.stock_count,themes.etf_count,themes.raw_json,themes.content_sha256 FROM theme_aliases
  JOIN themes ON themes.id = theme_aliases.theme_id
  WHERE theme_aliases.alias_id = ?
  LIMIT 1`).bind(id,id).first<ThemeRawRecord>();}
async function getTheme(id:string,env:Env){const row=await getRaw(id,env);if(!row)return error('Theme not found',404);return response(row.raw_json,{headers:noindexHeaders({...jsonHeaders,'cache-control':'no-store'})});}
async function getCanonicalThemeRow(id: string, env: Env) {
  return env.DB.prepare('SELECT id,created_at,event_date,theme,source_url,cover_url,stock_count,etf_count FROM themes WHERE id = ?').bind(id).first<ThemeRecord>();
}
async function resolveThemeRoute(id: string, env: Env) {
  const current = await getCanonicalThemeRow(id, env);
  if (current) return { row: current, alias: false };
  const alias = await env.DB.prepare('SELECT theme_id FROM theme_aliases WHERE alias_id = ?').bind(id).first<{ theme_id: string }>();
  if (!alias?.theme_id) return null;
  const row = await getCanonicalThemeRow(alias.theme_id, env);
  return row ? { row, alias: true } : null;
}
function selections(parsed:Record<string,any>,type:SecurityType){const key=type==='stock'?'ThemeStocks':'ThemeEtfs';const items=Array.isArray(parsed[key])?parsed[key]:[],byCode=new Map<string,{market_code:string;exposure:number;rationale:unknown}>();for(const item of items){if(!isRecord(item))continue;const market_code=String(item.market_code||'').trim();if(!market_code.includes(':')||byCode.has(market_code))continue;byCode.set(market_code,{market_code,exposure:Number(item['Theme exposure']),rationale:item.theme_rationale});}return[...byCode.values()];}

function siteOrigin(request: Request, env: Env) {
  return normalizeOrigin(env.SITE_ORIGIN || new URL(request.url).origin || DEFAULT_SITE_ORIGIN);
}

function baseHead({ title, description, canonical, image, type = 'website', noindex = false, jsonLd, published }: { title: string; description: string; canonical: string; image?: string; type?: string; noindex?: boolean; jsonLd?: unknown; published?: string | null }) {
  const meta = [
    `<meta charset="utf-8">`,
    `<meta name="viewport" content="width=device-width,initial-scale=1">`,
    `<meta name="theme-color" content="#f7f7f5">`,
    `<meta name="description" content="${escapeHtml(description)}">`,
    `<meta name="robots" content="${noindex ? 'noindex, nofollow' : 'index, follow'}">`,
    `<link rel="canonical" href="${escapeHtml(canonical)}">`,
    `<meta property="og:site_name" content="ThemeSignal">`,
    `<meta property="og:type" content="${escapeHtml(type)}">`,
    `<meta property="og:title" content="${escapeHtml(title)}">`,
    `<meta property="og:description" content="${escapeHtml(description)}">`,
    `<meta property="og:url" content="${escapeHtml(canonical)}">`,
    image ? `<meta property="og:image" content="${escapeHtml(image)}">` : '',
    `<meta name="twitter:card" content="${image ? 'summary_large_image' : 'summary'}">`,
    `<meta name="twitter:title" content="${escapeHtml(title)}">`,
    `<meta name="twitter:description" content="${escapeHtml(description)}">`,
    image ? `<meta name="twitter:image" content="${escapeHtml(image)}">` : '',
    published ? `<meta property="article:published_time" content="${escapeHtml(published)}">` : '',
    jsonLd ? `<script type="application/ld+json">${safeJson(jsonLd)}</script>` : '',
  ].filter(Boolean).join('');
  return `<head>${meta}<title>${escapeHtml(title)}</title><link rel="stylesheet" href="/styles.css"></head>`;
}

function archiveHtml(request: Request, env: Env, result: Awaited<ReturnType<typeof queryThemes>>) {
  const origin = siteOrigin(request, env);
  const canonical = canonicalHomeUrl(origin);
  const cards = result.themes.map((row, index) => archiveCardMarkup(row, origin, index)).join('');
  const boot = { route: 'archive', origin, ...result };
  const content = `<main class="archive"><header class="archive-hero"><h1>See what’s moving markets.</h1><p>A clear view of the narratives, companies, and funds shaping the market right now.</p></header><div class="toolbar"><label class="search-wrap"><span class="sr-only">Search themes</span><input class="input" placeholder="Search themes" aria-label="Search themes"></label><label class="sort-wrap"><span class="sr-only">Sort themes</span><select class="select" aria-label="Sort themes"><option>Newest</option></select></label></div><div class="archive-toolbar-meta"><span>${result.total} theme${result.total === 1 ? '' : 's'}</span></div><section aria-label="Theme archive"><div class="theme-grid">${cards || '<p>No themes are available yet.</p>'}</div></section><footer class="archive-footer">Educational research, not investment advice.</footer></main>`;
  return `<!doctype html><html lang="en">${baseHead({ title: HOME_TITLE, description: HOME_DESCRIPTION, canonical, jsonLd: archiveJsonLd(result.themes, origin) })}<body class="archive-body"><div id="app-shell" class="app-shell"><nav class="nav" aria-label="Main navigation"><div class="nav-inner"><a class="brand" href="/" aria-label="ThemeSignal home"><span class="brand-mark">TS</span><span class="brand-word">ThemeSignal</span></a></div></nav>${content}</div><noscript><p class="seo-noscript-note">Enable JavaScript for search, sorting, and live market panels.</p></noscript><script>window.__THEMESIGNAL_BOOT__=${safeJson(boot)};</script><script src="/app.js" defer></script></body></html>`;
}

function detailHtml(request: Request, env: Env, data: Record<string, any>, row: ThemeRecord) {
  const origin = siteOrigin(request, env);
  const canonical = canonicalThemeUrl(origin, row.id);
  const cover = safeHttpUrl(data.cover || row.cover_url, origin);
  const published = validDate(data.date || row.event_date);
  const faq = faqRows(data);
  const theme = String(data.theme || row.theme || 'Market theme');
  const boot = { route: 'detail', origin, id: row.id, data };
  const faqMarkup = faq.length ? `<section class="event-faq seo-faq"><h2>Theme FAQ</h2><div class="faq-list">${faq.map(item => `<details><summary>${escapeHtml(item.question)}</summary><p>${escapeHtml(item.answer)}</p></details>`).join('')}</div></section>` : '';
  const fallback = `<div id="app-shell" class="event-theme-page"><main class="event-theme-stack"><section class="event-hero seo-hero"><div class="event-hero-grid${cover ? ' has-cover' : ''}">${cover ? `<div class="event-cover"><img src="${escapeHtml(cover)}" alt="${escapeHtml(theme)} market theme cover" width="1600" height="900" fetchpriority="high"></div>` : ''}<div class="event-heading"><p class="seo-breadcrumb"><a href="/">All themes</a><span aria-hidden="true">/</span> ${escapeHtml(theme)}</p><h1>${escapeHtml(theme)}</h1>${published ? `<time datetime="${escapeHtml(published)}">${escapeHtml(formatDate(published))}</time>` : ''}</div></div></section>${faqMarkup}<p class="seo-noscript-note">Interactive market panels load in your browser.</p></main></div>`;
  return `<!doctype html><html lang="en">${baseHead({ title: titleForTheme(theme), description: themeDescription(data, row), canonical, image: cover, type: 'article', published, jsonLd: detailJsonLd(data, row, origin) })}<body class="event-body">${fallback}<script>window.__THEMESIGNAL_BOOT__=${safeJson(boot)};</script><script src="/app.js" defer></script></body></html>`;
}

async function page(request: Request, env: Env, id?: string) {
  if (!id) return htmlResponse(archiveHtml(request, env, await queryThemes(env)));
  const resolved = await resolveThemeRoute(id, env);
  if (!resolved) return htmlResponse(notFoundHtml(request, env), { status: 404, headers: noindexHeaders() });
  if (resolved.alias) return Response.redirect(canonicalThemeUrl(siteOrigin(request, env), resolved.row.id), 301);
  const raw = await getRaw(resolved.row.id, env);
  if (!raw) return htmlResponse(notFoundHtml(request, env), { status: 404, headers: noindexHeaders() });
  let data: Record<string, any>;
  try { data = JSON.parse(raw.raw_json); } catch { return htmlResponse(notFoundHtml(request, env), { status: 500, headers: noindexHeaders() }); }
  return htmlResponse(detailHtml(request, env, data, resolved.row));
}

function notFoundHtml(request: Request, env: Env) {
  const origin = siteOrigin(request, env);
  const canonical = canonicalHomeUrl(origin);
  return `<!doctype html><html lang="en">${baseHead({ title: 'Theme not found | ThemeSignal', description: 'The requested market theme could not be found.', canonical, noindex: true })}<body class="archive-body"><main class="archive error-page"><h1>That theme is no longer here.</h1><p><a href="/">Return to all themes</a></p></main></body></html>`;
}

async function robots(request: Request, env: Env) {
  const origin = siteOrigin(request, env);
  return textResponse(`User-agent: *\nAllow: /\nDisallow: /api/\nDisallow: /*?q=\nDisallow: /*?sort=\nDisallow: /*?page=\nSitemap: ${canonicalHomeUrl(origin)}sitemap.xml\n`, 'text/plain; charset=utf-8');
}

async function sitemap(request: Request, env: Env) {
  const origin = siteOrigin(request, env);
  const result = await queryThemes(env, { limit: MAX_LIST_LIMIT, page: 1 });
  const allRows = [...result.themes];
  for (let pageNumber = 2; pageNumber <= Math.ceil(result.total / MAX_LIST_LIMIT); pageNumber += 1) {
    const next = await queryThemes(env, { limit: MAX_LIST_LIMIT, page: pageNumber });
    allRows.push(...next.themes);
  }
  const urls = [`<url><loc>${xmlEscape(canonicalHomeUrl(origin))}</loc></url>`, ...allRows.map(row => {
    const lastmod = validDate(row.event_date) || String(row.created_at || '').slice(0, 10);
    return `<url><loc>${xmlEscape(canonicalThemeUrl(origin, row.id))}</loc>${/^\d{4}-\d{2}-\d{2}$/.test(lastmod) ? `<lastmod>${lastmod}</lastmod>` : ''}</url>`;
  })];
  return textResponse(`<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">${urls.join('')}</urlset>`, 'application/xml; charset=utf-8');
}
function idx(indicators:any[],...keys:string[]){const indexes=new Map<string,number>();indicators.forEach((indicator,index)=>{if(typeof indicator?.id==='string'&&!indexes.has(indicator.id))indexes.set(indicator.id,index);if(typeof indicator?.req_unique_id==='string'&&!indexes.has(indicator.req_unique_id))indexes.set(indicator.req_unique_id,index);});for(const key of keys){const position=indexes.get(key);if(position!==undefined)return position;}return-1;} function num(value:unknown){const n=Number(value);return value===null||value===undefined||value===''||!Number.isFinite(n)?null:n;} function symbol(code:string){return code.split(':').pop()||code;}
export function normalizeQuoteMarketValue(value:unknown){return num(value);}
export function normalizeExposureMarketValue(value:unknown){const absolute=num(value);return absolute!==null&&absolute>0?absolute/DOLLARS_PER_BILLION:null;}
export function normalizePerformancePoints(value:unknown){if(!Array.isArray(value))return[];return value.flatMap((point:any)=>{const timestamp=Number(point?.t),percentage=num(point?.p);if(!Number.isFinite(timestamp)||percentage===null)return[];const date=new Date(timestamp);if(Number.isNaN(date.getTime()))return[];const parts=newYorkDateFormatter.formatToParts(date),part=(type:string)=>parts.find(item=>item.type===type)?.value;const year=part('year'),month=part('month'),day=part('day');return year&&month&&day?[{date:`${year}-${month}-${day}`,percentage}]:[];});}
export function normalizePerformanceSeries(indicators:any[],values:any[]){const series:Record<SecurityType,Record<Period,ReturnType<typeof normalizePerformancePoints>>>={stock:{'1M':[],'6M':[],'1Y':[]},etf:{'1M':[],'6M':[],'1Y':[]}};for(const [id,reqId,type,period] of PERFORMANCE_INDICATORS){const position=idx(indicators,reqId,id);series[type][period]=position>=0?normalizePerformancePoints(values[position]?.v):[];}return series;}
function responseCookies(headers:Headers){const extended=headers as Headers&{getSetCookie?:()=>string[]},values=extended.getSetCookie?.()||[];if(values.length)return values;const combined=headers.get('set-cookie');return combined?combined.split(/,(?=\s*[^=;,\s]+=)/):[];}
async function createVisitorCookie(env:Env){const fingerprint=crypto.randomUUID(),body=new URLSearchParams({udid:fingerprint,clientType:'WEB'}),result=await fetch(env.VISITOR_AUTH_URL||DEFAULT_VISITOR_AUTH_URL,{method:'POST',headers:{fingerprint,'content-type':'application/x-www-form-urlencoded'},body});if(!result.ok)return null;const cookie=responseCookies(result.headers).map(value=>value.split(';',1)[0]?.trim()).filter(Boolean).join('; ');return cookie||null;}
async function getVisitorCookie(env:Env){if(!visitorCookiePromise)visitorCookiePromise=createVisitorCookie(env).catch(()=>null);const cookie=await visitorCookiePromise;if(!cookie)visitorCookiePromise=null;return cookie;}
async function fetchSnapshot(body:unknown,env:Env,language:string,cookie:string){const result=await fetch(env.SNAPSHOT_URL||DEFAULT_SNAPSHOT_URL,{method:'POST',headers:{'content-type':'application/json',accept:'application/json','accept-language':language,cookie,'x-auth-version':'1.0','x-auth-progid':'7047','x-auth-select-market-level':'uus:level0'},body:JSON.stringify(body)});if(!result.ok)return null;const data:any=await result.json();return data?.status_code===0?data:null;}
async function upstream(body:unknown,env:Env,language:string){const configuredCookie=env.QUOTE_C_COOKIE?.trim()||'',cookie=configuredCookie||await getVisitorCookie(env);if(!cookie)return null;const first=await fetchSnapshot(body,env,language,cookie);if(first)return first;visitorCookiePromise=null;const fallbackCookie=await getVisitorCookie(env);return fallbackCookie?fetchSnapshot(body,env,language,fallbackCookie):null;}
async function cached(request:Request,key:string,fn:()=>Promise<Response>){const cache=(caches as any).default;const cacheRequest=new Request(`${new URL(request.url).origin}/__live-cache/${key}`);const hit=await cache?.match(cacheRequest);if(hit)return hit;const out=await fn();if(out.ok)await cache?.put(cacheRequest,out.clone());return out;}
async function live(request:Request,env:Env,id:string){const row=await getRaw(id,env);if(!row)return error('Theme not found',404);const url=new URL(request.url),requestedType=url.searchParams.get('securityType'),requestedPeriod=url.searchParams.get('period');if(requestedType&&!['stock','etf'].includes(requestedType)||requestedPeriod&&!['1M','6M','1Y'].includes(requestedPeriod))return error('Invalid live query',422);let parsed:unknown;try{parsed=JSON.parse(row.raw_json);}catch{return unavailable(502);}const strategyId=resolveHotEventStrategyId(parsed);if(!strategyId)return unavailable();const language=requestLanguage(request);return cached(request,`${id}-${row.content_sha256}-${strategyId}-live-${languageCacheKey(language)}`,async()=>{const body=await upstream(buildPerformanceRequest(strategyId),env,language);if(!body)return unavailable(502);const indicators=body.data?.indicator||[],values=body.data?.data?.[0]?.value||[],series=normalizePerformanceSeries(indicators,values);return response(JSON.stringify({series}),{headers:{...jsonHeaders,'cache-control':'public, max-age=120'}});});}
async function marketData(request:Request,env:Env,id:string,type:SecurityType,kind:'quotes'|'exposure-map'){const row=await getRaw(id,env);if(!row)return error('Theme not found',404);if(!['stock','etf'].includes(type))return error('Invalid security type',422);let parsed:any;try{parsed=JSON.parse(row.raw_json);}catch{return unavailable(502);}const selected=selections(parsed,type),symbols=selected.map(x=>x.market_code);if(!symbols.length)return response(JSON.stringify(kind==='quotes'?{rows:[]}:{points:[]}),{headers:jsonHeaders});const language=requestLanguage(request);return cached(request,`${id}-${row.content_sha256}-${kind}-${type}-${languageCacheKey(language)}`,async()=>{const body=await upstream(kind==='quotes'?buildQuotesRequest(symbols,parsed.date):buildExposureRequest(symbols),env,language);if(!body)return unavailable(502);const indicators=body.data?.indicator||[],rows=body.data?.data||[];if(kind==='exposure-map'){const marketIndex=idx(indicators,'marketValue','total_market_value');const points=selected.flatMap(s=>{const r=rows.find((x:any)=>x?.symbol_code===s.market_code),marketValue=normalizeExposureMarketValue(r?.value?.[marketIndex]?.v);return marketValue!==null?[{symbol:symbol(s.market_code),marketCode:s.market_code,exposure:num(s.exposure),marketValue}]:[]});return response(JSON.stringify({points}),{headers:{...jsonHeaders,'cache-control':'public, max-age=120'}});}const indexes={name:idx(indicators,'55','name'),last:idx(indicators,'10','last'),changePercent:idx(indicators,'inr-price_change_ratio_pct-sum','199112','changePercent'),marketValue:idx(indicators,'total_market_value','marketCap')};const out=selected.map(s=>{const r=rows.find((x:any)=>x?.symbol_code===s.market_code),value=(key:keyof typeof indexes)=>indexes[key]>=0?r?.value?.[indexes[key]]?.v:null;return{symbol:symbol(s.market_code),marketCode:s.market_code,name:value('name')==null?null:String(value('name')),last:num(value('last')),changePercent:num(value('changePercent')),marketValue:normalizeQuoteMarketValue(value('marketValue')),exposure:num(s.exposure),rationale:s.rationale??null};});return response(JSON.stringify({rows:out}),{headers:{...jsonHeaders,'cache-control':'public, max-age=120'}});});}
export default {async fetch(request:Request,env:Env):Promise<Response>{if(request.method==='OPTIONS')return response(null,{status:204});const url=new URL(request.url);try{if(url.pathname==='/api/themes'&&request.method==='POST')return ingest(request,env);if(url.pathname==='/api/themes'&&request.method==='GET')return listThemes(request,env);if(url.pathname.startsWith('/api/themes/')&&request.method==='GET'){const parts=url.pathname.slice('/api/themes/'.length).split('/'),id=parts[0];if(parts[1]==='live')return live(request,env,id);if(parts[1]==='quotes'||parts[1]==='exposure-map')return marketData(request,env,id,(url.searchParams.get('securityType')||'stock') as SecurityType,parts[1]);return getTheme(id,env);}if(request.method==='GET'&&url.pathname==='/robots.txt')return robots(request,env);if(request.method==='GET'&&url.pathname==='/sitemap.xml')return sitemap(request,env);if(request.method==='GET'&&url.pathname==='/')return page(request,env);const detailMatch=url.pathname.match(/^\/themes\/([^/]+)\/?$/);if(request.method==='GET'&&detailMatch)return page(request,env,decodeURIComponent(detailMatch[1]));return env.ASSETS.fetch(request);}catch(cause){console.error(cause instanceof Error?cause.message:'request failed');return error('Internal server error',500);}}};
