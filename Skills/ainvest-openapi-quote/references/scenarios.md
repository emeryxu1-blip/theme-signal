# Scenarios

Use these patterns as the default quote request shapes.

These scenarios apply across the AInvest-supported quote markets in this skill:

- US stocks
- ETFs
- bonds
- crypto
- options

The request shape varies by market and indicator support, but the endpoint choice remains:

- `snapshot` for latest quote data
- `series` for historical quote data
- `relation_list` for plain related-code/id lists
- `multi_kline` for minute bars and candlesticks
- `single_tick` for tick-by-tick trade data

When you need a concrete known-good request pattern, start from the matching JSON in `assets/request-templates/`, then use this file to broaden or adjust the scenario.

## 1. Watchlist snapshot

- Endpoint: `snapshot`
- Symbol source: `market_code`
- Default order: keep watchlist order
- Default behavior: omit `sort`
- Common metrics: latest price, change, volume, amount, turnover, high, low, open, prev close, 52-week high/low, market cap, total shares, PE, EPS, ROA, ROE, PB, ex-dividend date, five-minute change, YTD change, 120-day change, 20-day change, 5-day change, 10-day change, 250-day change

Template: `assets/request-templates/watchlist.json`

## 2. Homepage ranking list

- Endpoint: `snapshot`
- Symbol source: `block_id`, `market`, or `prompt_id`
- Default behavior: include `sort` and `page`
- Use `block_id` for full-market stock rankings
- Use `prompt_id` for 24h hot stocks and many ETF or bond homepage lists
- Use `market` for some options homepage all-market rankings

Templates:

- `assets/request-templates/ranking-block.json`
- `assets/request-templates/ranking-market.json`
- `assets/request-templates/ranking-prompt.json`

## 3. Stock detail snapshot

- Endpoint: `snapshot`
- Symbol source: `market_code`
- Common metrics: high, low, open, prev close, turnover rate, volume, amount, market cap, total shares, PE, PB, TTM dividend yield, NAV, 52-week high/low, 4-week average volume, all-time high/low, asset type, 1-year NAV return, 1-year return, beta

Template: `assets/request-templates/stock-detail.json`

## 4. Related stocks and ETFs

- Endpoint: `snapshot`
- Symbol source: `prompt_id`
- Required attr: `market_code`

Templates:

- `assets/request-templates/related-stock.json`
- `assets/request-templates/related-etf.json`

## 5. Industry constituents and subsectors

- Endpoint: `snapshot`
- Symbol source: `link_code`
- Required attr: `link_type`
- Industry code example: `89:861076`

Templates:

- `assets/request-templates/industry-components.json`
- `assets/request-templates/industry-subsector.json`

## 6. ETF holdings

- Endpoint: `snapshot`
- Symbol source: `link_code`
- Required attr: `link_type = "holding"`
- `value` must contain exactly one ETF code

Template: `assets/request-templates/etf-holdings.json`

## 7. Minute K-line or intraday chart

- Endpoint: `multi_kline`
- Request family: basic quote API
- Symbol source: `code_list`
- Use for candlesticks, intraday minute bars, and `分时行情`
- For `分时行情`, choose a minute `time_period` such as `min_1` or `min_5`
- For same-day full-session minute data, prefer `time_range = {"trade_date": 0, "date_offset": 0}`
- Session support:
  - crypto and options: `intraday`
  - US stocks and indices: `pre_market`, `intraday`, `post_market`
- Limit: at most 16 codes and at most 2000 returned K-line rows per request

Template: `assets/request-templates/multi-kline-minute.json`

## 8. Single-symbol tick details

- Endpoint: `single_tick`
- Request family: basic quote API
- Symbol source: `code_list`
- Use for `逐笔成交`, `成交明细`, and time-and-sales views
- Limit: exactly one code per request
- Use `time_range.trade_date = 0`
- Session support:
  - crypto and options: `intraday`
  - US stocks and indices: `pre_market`, `intraday`, `post_market`

Template: `assets/request-templates/single-tick.json`

## 9. Relation lists

- Endpoint: `relation_list`
- Request family: index-api relation API
- Request shape: top-level `relation`, `symbol`, `symbol_type`, and optional `page`
- Supported relations: `holding`, `component`
- Supported source types: `market_code`, `prompt_id`, `block_id`, `group_id`
- Use for plain lists of related codes or ids when no indicator values, sort, or filter are needed.
- Response items are under `data.data[].v`; `data.symbol_type` is the returned item type.

Templates:

- `assets/request-templates/relation-etf-holding.json`
- `assets/request-templates/relation-index-components.json`
- `assets/request-templates/relation-prompt-components.json`
- `assets/request-templates/relation-group-components.json`
- `assets/request-templates/relation-group-list.json`
- `assets/request-templates/relation-prompt-holding-empty.json`

## 10. ETF historical series

- Endpoint: `series`
- Symbol source: `market_code`
- Use for past N-day NAV, premium, or similar trend metrics
- Check `references/legacy/id_dict.md` for supported `time_range.type`

Template: `assets/request-templates/series-etf-30d.json`

## Additional local patterns

These patterns are not the primary first-pass quote flows, but they are represented by local templates and are valid references when the user asks for them:

- custom pool rankings via `group_id`
- prompt-self snapshot and series via `prompt_id_self`
- chain history via `chain_id`
- market-environment or macro requests without `symbol`
- filter-heavy snapshot queries
- technical indicators with `tech_param`

Additional templates for extended local cases:

- `assets/request-templates/group-rating-filter.json`
- `assets/request-templates/prompt-self-ratings.json`
- `assets/request-templates/block-event-window.json`
- `assets/request-templates/etf-holding-ratio.json`
- `assets/request-templates/technical-macd.json`
- `assets/request-templates/series-chain-history.json`
- `assets/request-templates/market-env-snapshot.json`
- `assets/request-templates/series-prompt-self-ratings.json`
- `assets/request-templates/options-market-ranking.json`
- `assets/request-templates/multi-market-basics.json`

Validate template updates with `python3 scripts/validate_templates.py` and follow `references/template-writing.md` when adding new scenarios.
