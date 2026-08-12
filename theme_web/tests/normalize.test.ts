import { buildExposureRequest, buildPerformanceRequest, buildQuotesRequest } from '../src/worker.ts';

const performance = buildPerformanceRequest('theme_abc');
if (performance.symbol[0].type !== 'group_id_self' || performance.symbol[0].value[0] !== 'theme_abc') throw new Error('performance symbol mapping changed');
if (performance.indicator.length !== 6 || performance.indicator[0].req_unique_id !== '0' || performance.indicator[5].req_unique_id !== '5') throw new Error('performance indicators changed');

const symbols = ['185:NVDA', '185:MSFT'];
const exposure = buildExposureRequest(symbols);
if (JSON.stringify(exposure.symbol[0].value) !== JSON.stringify(symbols)) throw new Error('exposure symbols changed');
if (exposure.indicator[0].id !== 'total_market_value' || exposure.indicator[0].req_unique_id !== 'marketValue') throw new Error('exposure indicator changed');

const quotes = buildQuotesRequest(symbols, '2026-08-01');
if (quotes.indicator.map(x => x.req_unique_id).join(',') !== 'name,last,changePercent,marketCap') throw new Error('quote indicators changed');
if (!/^day_[1-9][0-9]*$/.test(quotes.indicator[2].attr.time_period)) throw new Error('quote event range changed');

const raw = '{"theme":"AI","ThemeStocks":[{"market_code":"185:NVDA","Theme exposure":4.8}]}';
if (JSON.parse(raw).ThemeStocks[0].market_code !== '185:NVDA') throw new Error('raw result contract changed');
console.log('ok');
