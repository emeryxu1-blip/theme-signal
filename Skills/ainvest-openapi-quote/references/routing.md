# Routing

Use this file to decide whether a quote scenario should call `snapshot`, `series`, `relation_list`, `multi_kline`, or `single_tick`.

This skill supports AInvest quote retrieval for:

- US stocks
- ETFs
- bonds
- crypto
- options

For each market, the same top-level routing rule applies: use indicator endpoints for derived indicators and list-style quote modules, use `relation_list` for plain relation expansions, and use the basic quote endpoints for minute K-line and tick-by-tick trade data.

## Default routing

- Watchlist list data: `snapshot`
- Homepage rankings and leaderboard lists: `snapshot`
- Stock detail snapshot: `snapshot`
- Related stocks: `snapshot`
- Related ETFs: `snapshot`
- Industry constituents: `snapshot`
- Industry subsectors: `snapshot`
- ETF holdings: `snapshot`
- Plain relation lists without indicator values: `relation_list`
- Minute K-line or intraday chart data: `multi_kline`
- Candlestick data for one or more symbols: `multi_kline`
- Tick-by-tick trade details: `single_tick`
- Historical NAV, premium, return, or other continuous past-N-day values: `series`

## Choose `snapshot` when

- The user needs the latest value for one or more indicators.
- The user needs sorting, ranking, or pagination.
- The user needs a symbol pool resolved from `market`, `block_id`, `prompt_id`, or `link_code`.
- The request is for realtime quote cards, tables, or related-symbol modules.

## Choose `series` when

- The user needs points over time, not just the latest value.
- The UI is a line chart or trend chart for derived indicators.
- The request is phrased like "past 30 days", "daily series", or "historical values".

## Choose `relation_list` when

- The user needs only related symbols or ids, not indicator values.
- The request is an ETF holding-code list, index or industry component list, prompt component list, block component list, or group component list.
- The desired response is `data.data[].v` plus `data.page.total`.
- The request should use top-level `relation`, `symbol`, and `symbol_type` instead of the snapshot `symbol` array.

## Choose `multi_kline` when

- The user needs OHLCV bars.
- The UI is a candlestick chart.
- The user asks for intraday minute data, minute bars, or `分时行情`.
- The request is for one or more symbols and the output should be base price bars rather than derived indicator series.
- For same-day full-session minute data, default to `time_range.trade_date = 0` plus `date_offset = 0`.

## Choose `single_tick` when

- The user asks for `逐笔成交`, `成交明细`, or time-and-sales data.
- The result should be a trade-print list with price, side, size, and trade time.
- The request is for exactly one symbol.

## Parameter error rules

Treat these as request issues and call them out:

- Missing required `attr` for a symbol source, such as `prompt_id` without `attr.market_code`.
- `link_code` without `attr.link_type`.
- ETF holdings request where `value` contains more than one code.
- `series` request using a `time_range.type` not supported by the requested indicator.
- `multi_kline` request with more than 16 symbols or an invalid `time_range` combination.
- `single_tick` request with anything other than exactly one symbol.
- `relation_list` request with unsupported `relation`, unsupported `symbol_type`, empty `symbol`, or invalid optional `page`.

Do not treat these as request errors:

- Null metric values in `snapshot`
- Empty series arrays
- Missing invalid indicators in the response
- Empty K-line or tick lists when the request itself is valid
- Empty relation lists when the source relation is valid but has no items

## Cross-checks

- Macro indicators and security-keyed indicators should not be mixed in one request.
- For `series`, verify the requested historical indicator supports the requested `time_range` mode in `references/series-indicators.md` or `references/legacy/id_dict.md`.
- For `snapshot`, if the request is a watchlist and the user does not ask to sort, omit `sort`.
- For plain ETF holdings, index components, prompt components, block components, and group components, prefer `relation_list` when indicator values, sorting, and filtering are not needed.
- For `分时行情`, prefer `multi_kline` with a minute `time_period` instead of trying to force the request into `series`.
- For `逐笔成交`, use `single_tick`; do not route it to `snapshot` or `series`.
- The local GMS doc marks `single_trend` as unavailable, so do not use it as the default minute endpoint.
- Do not use `pre_market` or `post_market` for crypto or options; those symbols only support `intraday`.
