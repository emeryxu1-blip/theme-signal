# Basic Quote APIs

Use this file for the two basic quote endpoints added on top of the indicator-style `snapshot` and `series` APIs.

These endpoints use the same scene selection rule as the indicator-style endpoints:

- sandbox Aime Claw: `Authorization: Bearer <AIME_API_KEY>`
- B-side: `apikey`
- C-side: `Cookie`

Prefer these endpoints when the user is asking for base market data instead of indicator-style derived fields.

## Sandbox endpoints

- `https://open.ainvest.com/market/extquote/ag/quote/v2/multi_kline`
- `https://open.ainvest.com/market/extquote/ag/quote/v2/single_tick`

## B-side endpoints

- `http://quote-apisix-gateway.hxapisix/quote/v2/multi_kline`
- `http://quote-apisix-gateway.hxapisix/quote/v2/single_tick`

## C-side endpoints

- `https://quote.ainvest.com/quote/v2/multi_kline`
- `https://quote.ainvest.com/quote/v2/single_tick`

## Routing summary

- Use `multi_kline` for K-line or candlestick data.
- Use `multi_kline` for minute-line or intraday time-series quote requests.
- When the user asks for `分时行情`, build a minute K-line request with `time_period` such as `min_1` or `min_5`.
- For full-session same-day minute data, prefer `time_range = {"trade_date": 0, "date_offset": 0}`.
- Use `single_tick` for `逐笔成交`, `成交明细`, or trade-print style data.
- Do not use `single_trend`; the local protocol doc marks it as unavailable.
- For actual quote retrieval in the sandbox, prefer this skill over older indicator-only skills, especially for minute K-line and tick requests.

## Request shape differences from indicator APIs

- Basic quote APIs use `code_list`, not `symbol`.
- Convert a `market_code` such as `185:AAPL` into:

```json
{
  "code_list": [
    {
      "market": "185",
      "codes": ["AAPL"]
    }
  ]
}
```

- Keep the market value aligned with the request context. When the user already gives a small market like `169` or `185`, do not rewrite it unless the API requires a different market.

## `multi_kline`

### Use cases

- minute chart
- intraday line rendered from minute bars
- candlestick chart
- OHLCV history for one or more symbols

### Key limits

- total code count per request: `<= 16`
- total K-line rows returned per request: `<= 2000`
- when `trade_date = -1`, `begin_time` and `end_time` cannot both be `0`

### Required fields

- `code_list`
- `trade_class`
- `time_period`
- `time_range`

### `trade_class` support

- crypto and options: only `intraday`
- US stocks and indices: `pre_market`, `intraday`, `post_market`

When the user asks for pre-market or post-market minute data, only use those sessions for supported symbols.

### Optional fields

- `adjust_type`: `forward`, `backward`, or `actual`

### Common `time_period` values

- `min_1`
- `min_5`
- `min_10`
- `hour_1`
- `day_1`
- `week_1`
- `month_1`
- `quarter_1`
- `year_1`

### Supported `time_range` combinations

1. One trading day
   - `trade_date`
   - optional `date_offset`
2. Backward from a time point
   - `count`
   - `end_time`
3. Forward from a time point
   - `begin_time`
   - `count`
4. Closed interval
   - `begin_time`
   - `end_time`

For same-day full-session minute data, use this shape by default:

```json
{
  "time_range": {
    "trade_date": 0,
    "date_offset": 0
  }
}
```

### Response fields

- `1`: time
- `7`: open
- `8`: high
- `9`: low
- `11`: close
- `13`: volume
- `19`: turnover

### Interpretation notes

- `minute_window_type` may appear for minute K-line cases.
- `base_price` may appear when `trade_date >= 0`.
- The response is symbol-keyed under `data.quote_data[]`.

## `single_tick`

### Use cases

- trade prints
- time and sales
- 成交明细
- 逐笔成交

### Key limits

- total code count per request: exactly `1`
- only one time-range combination is supported in the local doc

### Required fields

- `code_list`
- `trade_class`
- `time_range.trade_date = 0`
- `time_range.count`
- `time_range.end_time`

### `trade_class` support

- crypto and options: only `intraday`
- US stocks and indices: `pre_market`, `intraday`, `post_market`

### `time_range` rule

Use the supported combination only:

- `trade_date`: `0` for latest trading day
- `count`: positive integer
- `end_time`: millisecond timestamp, or `0` for latest

When `end_time = 0`, the result is closed on the right edge. When `end_time != 0`, the result is left-open and right-closed.

### Response fields

- `1`: time
- `10`: trade price
- `12`: side, such as `buy`, `sell`, or `unknown`
- `49`: size
- `65541`: trade period
- `65552`: trade type
- `65558`: trade date

### Interpretation notes

- The response `data_class` is `tick`.
- `value[]` is a trade-print list, not an OHLC series.
- This endpoint is currently documented for `UUS`.
