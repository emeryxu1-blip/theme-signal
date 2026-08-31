import assert from 'node:assert/strict';

import worker, {
  buildExposureRequest,
  buildPerformanceRequest,
  buildQuotesRequest,
  deriveHotEventStrategyId,
  normalizeExposureMarketValue,
  normalizePerformancePoints,
  normalizePerformanceSeries,
  normalizeQuoteMarketValue,
  resolveHotEventStrategyId,
} from '../src/worker.ts';

const performance = buildPerformanceRequest('  hotevent_id_big_tech_crashed  ');
assert.deepEqual(performance, {
  symbol: [{ type: 'group_id_self', value: ['hotevent_id_big_tech_crashed'] }],
  indicator: [
    { id: 'hotevent_etf_month1_chg_ratio', req_unique_id: '0' },
    { id: 'hotevent_etf_month6_chg_ratio', req_unique_id: '1' },
    { id: 'hotevent_etf_year1_chg_ratio', req_unique_id: '2' },
    { id: 'hotevent_stock_month1_chg_ratio', req_unique_id: '3' },
    { id: 'hotevent_stock_month6_chg_ratio', req_unique_id: '4' },
    { id: 'hotevent_stock_year1_chg_ratio', req_unique_id: '5' },
  ],
  page: { begin: 0, count: 1 },
});
assert.throws(
  () => buildPerformanceRequest('theme_fd08fe7fc07c62c85a17de5b'),
  /Invalid hot-event strategy id/,
  'a D1 theme id must never become a group_id_self request',
);

assert.equal(deriveHotEventStrategyId('Inflation Cooling'), 'hotevent_id_inflation_cooling');
assert.equal(deriveHotEventStrategyId('  BIG Tech--Crashed  '), 'hotevent_id_big_tech_crashed');
assert.equal(deriveHotEventStrategyId("Café & Fed’s Pause"), 'hotevent_id_cafe_feds_pause');
assert.equal(deriveHotEventStrategyId('___'), null);
assert.equal(deriveHotEventStrategyId(42), null);

assert.equal(
  resolveHotEventStrategyId({ strategy_id: ' hotevent_id_big_tech_crashed ' }),
  'hotevent_id_big_tech_crashed',
);
assert.equal(
  resolveHotEventStrategyId({ strategyId: 'hotevent_id_cooling_inflation' }),
  'hotevent_id_cooling_inflation',
);
assert.equal(
  resolveHotEventStrategyId({
    strategy_id: 'theme_not_valid',
    strategyId: 'hotevent_id_valid_fallback',
    theme: 'Ignored Theme',
  }),
  'hotevent_id_valid_fallback',
);
assert.equal(
  resolveHotEventStrategyId({ theme: 'Inflation Cooling' }),
  'hotevent_id_inflation_cooling',
);
assert.equal(
  resolveHotEventStrategyId({ strategy_id: 'theme_not_valid', theme: 'Inflation Cooling' }),
  'hotevent_id_inflation_cooling',
);
assert.equal(resolveHotEventStrategyId({ id: 'hotevent_id_not_an_explicit_strategy_field' }), null);
assert.equal(resolveHotEventStrategyId({ strategy_id: 'theme_fd08fe7fc07c62c85a17de5b' }), null);
assert.equal(resolveHotEventStrategyId({}), null);
const inheritedStrategy = Object.create({ strategy_id: 'hotevent_id_inherited' });
assert.equal(resolveHotEventStrategyId(inheritedStrategy), null);
const inheritedTheme = Object.create({ theme: 'Inflation Cooling' });
assert.equal(resolveHotEventStrategyId(inheritedTheme), null);

let upstreamCalls = 0;
const originalFetch = globalThis.fetch;
globalThis.fetch = async () => {
  upstreamCalls += 1;
  throw new Error('the upstream must not be called without a strategy id');
};
try {
  const unavailableResponse = await worker.fetch(
    new Request('https://example.test/api/themes/theme_without_strategy/live?securityType=stock&period=1M'),
    {
      DB: {
        prepare() {
          return {
            bind() {
              return {
                async first() {
                  return { raw_json: '{}' };
                },
              };
            },
          };
        },
      },
      ASSETS: { fetch: async () => new Response('not reached') },
      QUOTE_C_COOKIE: 'server-only-secret',
    } as never,
  );
  assert.equal(unavailableResponse.status, 200);
  assert.deepEqual(await unavailableResponse.json(), {
    unavailable: true,
    error: 'Live market data unavailable',
  });
  assert.equal(upstreamCalls, 0);
} finally {
  globalThis.fetch = originalFetch;
}

const originalCaches = Object.getOwnPropertyDescriptor(globalThis, 'caches');
let capturedPerformanceBody: unknown;
let capturedPerformanceHeaders: Record<string, string> | undefined;
let validStrategyUpstreamCalls = 0;
let performanceCacheEntry: Response | undefined;
Object.defineProperty(globalThis, 'caches', {
  configurable: true,
  value: {
    default: {
      match: async () => performanceCacheEntry?.clone(),
      put: async (_request: Request, response: Response) => {
        performanceCacheEntry = response.clone();
      },
    },
  },
});
globalThis.fetch = async (_input, init) => {
  validStrategyUpstreamCalls += 1;
  capturedPerformanceBody = JSON.parse(String(init?.body));
  capturedPerformanceHeaders = init?.headers as Record<string, string>;
  return new Response(JSON.stringify({
    status_code: 0,
    data: {
      indicator: [{ id: 'hotevent_stock_month1_chg_ratio', req_unique_id: '3' }],
      data: [{ value: [{ v: [{ p: 2.5, t: Date.parse('2026-08-11T20:00:00Z') }] }] }],
    },
  }), { status: 200, headers: { 'content-type': 'application/json' } });
};
try {
  const liveResponse = await worker.fetch(
    new Request('https://example.test/api/themes/theme_record/live?securityType=stock&period=1M'),
    {
      DB: {
        prepare() {
          return {
            bind() {
              return {
                async first() {
                  return {
                    raw_json: JSON.stringify({
                      theme: 'Inflation Cooling',
                    }),
                  };
                },
              };
            },
          };
        },
      },
      ASSETS: { fetch: async () => new Response('not reached') },
      QUOTE_C_COOKIE: 'server-only-secret',
      SNAPSHOT_URL: 'https://snapshot.example.test',
    } as never,
  );
  assert.equal(liveResponse.status, 200);
  assert.equal(validStrategyUpstreamCalls, 1);
  assert.deepEqual(
    capturedPerformanceBody,
    buildPerformanceRequest('hotevent_id_inflation_cooling'),
  );
  assert.equal(capturedPerformanceHeaders?.['x-auth-version'], '1.0');
  assert.equal(capturedPerformanceHeaders?.['x-auth-progid'], '7047');
  assert.equal(capturedPerformanceHeaders?.['x-auth-select-market-level'], 'uus:level0');
  assert.equal(capturedPerformanceHeaders?.cookie, 'server-only-secret');
  const liveText = await liveResponse.text();
  assert.doesNotMatch(liveText, /server-only-secret/);
  assert.deepEqual(JSON.parse(liveText), {
    series: {
      stock: {
        '1M': [{ date: '2026-08-11', percentage: 2.5 }],
        '6M': [],
        '1Y': [],
      },
      etf: { '1M': [], '6M': [], '1Y': [] },
    },
  });
  const cachedPeriodResponse = await worker.fetch(
    new Request('https://example.test/api/themes/theme_record/live?securityType=etf&period=1Y'),
    {
      DB: {
        prepare() {
          return {
            bind() {
              return {
                async first() {
                  return {
                    raw_json: JSON.stringify({
                      theme: 'Inflation Cooling',
                    }),
                  };
                },
              };
            },
          };
        },
      },
      ASSETS: { fetch: async () => new Response('not reached') },
      QUOTE_C_COOKIE: 'server-only-secret',
      SNAPSHOT_URL: 'https://snapshot.example.test',
    } as never,
  );
  assert.equal(cachedPeriodResponse.status, 200);
  assert.equal(validStrategyUpstreamCalls, 1, 'all period/type reads share one performance snapshot');
} finally {
  globalThis.fetch = originalFetch;
  if (originalCaches) Object.defineProperty(globalThis, 'caches', originalCaches);
  else Reflect.deleteProperty(globalThis, 'caches');
}

let visitorAuthCalls = 0;
let visitorSnapshotCalls = 0;
let visitorAuthFingerprint = '';
Object.defineProperty(globalThis, 'caches', {
  configurable: true,
  value: {
    default: {
      match: async () => undefined,
      put: async () => undefined,
    },
  },
});
globalThis.fetch = async (input, init) => {
  const url = String(input);
  const headers = new Headers(init?.headers);
  if (url === 'https://visitor.example.test/login') {
    visitorAuthCalls += 1;
    visitorAuthFingerprint = headers.get('fingerprint') || '';
    assert.match(visitorAuthFingerprint, /^[0-9a-f-]{36}$/i);
    assert.equal(init?.method, 'POST');
    assert.equal(headers.get('content-type'), 'application/x-www-form-urlencoded');
    const form = new URLSearchParams(String(init?.body));
    assert.equal(form.get('udid'), visitorAuthFingerprint);
    assert.equal(form.get('clientType'), 'WEB');
    const responseHeaders = new Headers({ 'content-type': 'application/json' });
    responseHeaders.append('set-cookie', 'userid=visitor-user; Path=/; HttpOnly');
    responseHeaders.append('set-cookie', 'sessionid=visitor-session; Path=/; HttpOnly');
    return new Response('{"status_code":0}', { status: 200, headers: responseHeaders });
  }
  assert.equal(url, 'https://snapshot.example.test');
  visitorSnapshotCalls += 1;
  if (headers.get('cookie') === 'stale-server-cookie') {
    return new Response('{"status_code":106}', { status: 401 });
  }
  assert.equal(headers.get('cookie'), 'userid=visitor-user; sessionid=visitor-session');
  return new Response(JSON.stringify({
    status_code: 0,
    data: {
      indicator: [
        { id: '55', req_unique_id: 'name' },
        { id: '10', req_unique_id: 'last' },
        { id: 'inr-price_change_ratio_pct-sum', req_unique_id: 'changePercent' },
        { id: 'total_market_value', req_unique_id: 'marketCap' },
      ],
      data: [{
        symbol_code: '185:NVDA',
        value: [{ v: 'NVIDIA' }, { v: 181.25 }, { v: 3.5 }, { v: 4_500_000_000_000 }],
      }],
    },
  }), { status: 200, headers: { 'content-type': 'application/json' } });
};
try {
  const quoteResponse = await worker.fetch(
    new Request('https://example.test/api/themes/theme_record/quotes?securityType=stock'),
    {
      DB: {
        prepare() {
          return {
            bind() {
              return {
                async first() {
                  return {
                    raw_json: JSON.stringify({
                      date: '2026-08-01',
                      ThemeStocks: [{
                        market_code: '185:NVDA',
                        'Theme exposure': 4.9,
                        theme_rationale: 'AI infrastructure leader',
                      }, {
                        market_code: '169:MISSING',
                        'Theme exposure': 2.1,
                        theme_rationale: 'Stored selection without a live quote row',
                      }],
                    }),
                  };
                },
              };
            },
          };
        },
      },
      ASSETS: { fetch: async () => new Response('not reached') },
      QUOTE_C_COOKIE: 'stale-server-cookie',
      VISITOR_AUTH_URL: 'https://visitor.example.test/login',
      SNAPSHOT_URL: 'https://snapshot.example.test',
    } as never,
  );
  assert.equal(quoteResponse.status, 200);
  assert.equal(visitorSnapshotCalls, 2);
  assert.equal(visitorAuthCalls, 1);
  assert.deepEqual(await quoteResponse.json(), {
    rows: [{
      symbol: 'NVDA',
      marketCode: '185:NVDA',
      name: 'NVIDIA',
      last: 181.25,
      changePercent: 3.5,
      marketValue: 4_500_000_000_000,
      exposure: 4.9,
      rationale: 'AI infrastructure leader',
    }, {
      symbol: 'MISSING',
      marketCode: '169:MISSING',
      name: null,
      last: null,
      changePercent: null,
      marketValue: null,
      exposure: 2.1,
      rationale: 'Stored selection without a live quote row',
    }],
  });
} finally {
  globalThis.fetch = originalFetch;
  if (originalCaches) Object.defineProperty(globalThis, 'caches', originalCaches);
  else Reflect.deleteProperty(globalThis, 'caches');
}

const symbols = ['185:NVDA', '185:MSFT'];
assert.deepEqual(buildExposureRequest(symbols), {
  symbol: [{ type: 'market_code', value: symbols }],
  indicator: [{ id: 'total_market_value', req_unique_id: 'marketValue' }],
  page: { begin: 0, count: 2 },
});

const quoteNow = Date.parse('2026-08-12T12:00:00Z');
assert.deepEqual(buildQuotesRequest(symbols, '2026-08-01', quoteNow), {
  symbol: [{ type: 'market_code', value: symbols }],
  indicator: [
    { id: '55', req_unique_id: 'name' },
    { id: '10', req_unique_id: 'last', attr: { trade_class: 'intraday' } },
    {
      id: 'inr-price_change_ratio_pct-sum',
      req_unique_id: 'changePercent',
      attr: { trade_class: 'intraday', time_period: 'day_11' },
    },
    { id: 'total_market_value', req_unique_id: 'marketCap' },
  ],
  page: { begin: 0, count: 2 },
});
assert.equal(
  buildQuotesRequest(symbols, undefined, quoteNow).indicator[2].attr.time_period,
  'day_1',
);

const absoluteMarketValue = 3_741_064_073_820.71;
assert.equal(normalizeQuoteMarketValue(absoluteMarketValue), absoluteMarketValue);
assert.equal(normalizeExposureMarketValue(absoluteMarketValue), 3_741.06407382071);
assert.equal(normalizeQuoteMarketValue('1065440'), 1_065_440);
assert.equal(normalizeExposureMarketValue('1065440'), 0.00106544);
assert.equal(normalizeQuoteMarketValue(null), null);
assert.equal(normalizeExposureMarketValue(0), null);
assert.equal(normalizeExposureMarketValue(-1), null);

assert.deepEqual(normalizePerformancePoints([
  { t: Date.parse('2026-08-12T01:00:00Z'), p: '2.5' },
  { t: 'not-a-timestamp', p: 3 },
  { t: Date.parse('2026-08-12T20:00:00Z'), p: null },
]), [
  { date: '2026-08-11', percentage: 2.5 },
]);

assert.deepEqual(normalizePerformanceSeries(
  [
    { id: 'hotevent_stock_year1_chg_ratio', req_unique_id: '5' },
    { id: 'hotevent_etf_month1_chg_ratio', req_unique_id: '0' },
  ],
  [
    { v: [{ t: Date.parse('2026-08-12T20:00:00Z'), p: 8 }] },
    { v: [{ t: Date.parse('2026-08-12T20:00:00Z'), p: -1.25 }] },
  ],
), {
  stock: {
    '1M': [],
    '6M': [],
    '1Y': [{ date: '2026-08-12', percentage: 8 }],
  },
  etf: {
    '1M': [{ date: '2026-08-12', percentage: -1.25 }],
    '6M': [],
    '1Y': [],
  },
});

const raw = '{"theme":"AI","ThemeStocks":[{"market_code":"185:NVDA","Theme exposure":4.8}]}';
assert.equal(JSON.parse(raw).ThemeStocks[0].market_code, '185:NVDA');

console.log('ok');
