---
name: ainvest-openapi-quote
description: Use when constructing, validating, or executing AInvest quote API requests for indicator snapshot/series data, relation lists, K-line data, or tick data across US stocks, ETFs, bonds, crypto, and options. Also use when refreshing or checking local Tangram-exported AInvest indicator metadata and generated quote lookup CSVs.
---

# OpenAPI Quote

Use this skill to construct, review, explain, or execute AInvest quote API requests. Determine the access scene first, then choose endpoint, auth header, symbol format, indicator attrs, and response interpretation rules. Keep answers request-focused: do not introduce unrelated coverage tooling, undocumented endpoints, or guessed indicator parameters.

Sandbox Aime Claw scene:

- `https://open.ainvest.com/market/extquote/index/indicator/v2/snapshot`
- `https://open.ainvest.com/market/extquote/index/indicator/v2/series`
- `https://open.ainvest.com/market/extquote/index/relation/v1/list`
- `https://open.ainvest.com/market/extquote/ag/quote/v2/multi_kline`
- `https://open.ainvest.com/market/extquote/ag/quote/v2/single_tick`
- auth header: `Authorization: Bearer <AIME_API_KEY>`
- default header: `X-Auth-ProgId: 7080`

B-side scene:

- `http://quote-apisix-gateway.hxapisix/index_api/indicator/v2/snapshot`
- `http://quote-apisix-gateway.hxapisix/index_api/indicator/v2/series`
- `http://quote-apisix-gateway.hxapisix/index_api/relation/v1/list`
- `http://quote-apisix-gateway.hxapisix/quote/v2/multi_kline`
- `http://quote-apisix-gateway.hxapisix/quote/v2/single_tick`
- auth header: `apikey: <caller-provided apikey>`
- default header: `X-Auth-ProgId: 7080`
- B-side apikey is application-scoped: `index_api/indicator/v2` and `index_api/relation/v1` endpoints normally use the `index-api` app key, while `quote/v2` endpoints normally use the `quoteag` app key. Do not assume they are interchangeable.
- Only require the apikey for the endpoint family being called. If the user only needs `snapshot`, `series`, or `relation_list`, do not ask for `quoteag`; if the user only needs `multi_kline` or `single_tick`, do not ask for `index-api`.

C-side scene:

- `https://extquote.ainvest.com/index_api/indicator/v2/snapshot`
- `https://extquote.ainvest.com/index_api/indicator/v2/series`
- `https://extquote.ainvest.com/index_api/relation/v1/list`
- `https://quote.ainvest.com/quote/v2/multi_kline`
- `https://quote.ainvest.com/quote/v2/single_tick`
- auth header: `Cookie: <caller-provided cookie>`
- default header: `X-Auth-ProgId: 7080`

Supported markets:

- US stocks
- ETFs
- bonds
- crypto
- options

Supported data types:

- snapshot quote data
- historical series data
- relation lists such as ETF holdings, index components, prompt components, and group components
- minute or candlestick K-line data
- single-symbol tick-by-tick trade data

This skill focuses on request body design and response interpretation. When the user wants actual quote or relation data for a symbol, construct the request and fetch data directly when the selected scene has auth. Only sandbox Aime Claw reads auth from the environment variable `AIME_API_KEY` and composes `Authorization: Bearer <AIME_API_KEY>`. For B-side fetches, use the caller-provided apikey as the `apikey` header, choosing the `index-api` apikey for `snapshot`/`series`/`relation_list` and the `quoteag` apikey for `multi_kline`/`single_tick`. For C-side fetches, use the caller-provided Cookie string as the `Cookie` header. `sw8` is handled by the client layer and does not need to be expanded unless the user explicitly asks.

When this skill is installed in the sandbox and the user asks for actual quote retrieval, prefer this skill over older indicator-only quote skills. This priority is especially important for minute K-line, `分时行情`, `逐笔成交`, and `成交明细` requests.

For actual local execution, use `scripts/fetch_quote.py`. It reads one request body from `--template`, `--body-file`, or `--body-json`, infers the endpoint when possible, and sends the request for `--scene sandbox|b|c`. By default it also loads the shared local `Skills/env.json` config for `active_scene`, B-side apikey, C-side cookie fields, scene endpoints, and default headers; command-line auth values override the file. Automatic C-side login reads credentials only from the gitignored `Skills/env.json` and overwrites its refreshed `sessionid` and `userid` after a successful login. Use `--dry-run` to print the endpoint, redacted headers, and body without sending a request or attempting login.

For exported metric metadata, use `scripts/export_indicators.py` to pull the current AInvest-supported indicator workbook from Tangram into `references/generated/export_metric_meta_new.xlsx` inside this skill, then use `scripts/build_quote_csvs.py` to build generated CSV indexes and `scripts/find_quote_params.py` to search `references/generated/quote_request_lookup.csv`. If Tangram access is unavailable, use the newest trusted workbook already present at `references/generated/export_metric_meta_new.xlsx` or pass an explicit workbook with `build_quote_csvs.py --input <path>`. Generated rows use Tangram column C `*IndexAPI代码` as the request `indicator_id`. The generated `attrs_json` comes from column S `扩展属性`; legacy attr requirements from `references/legacy/id_dict.md` are still the first priority when present. Always send `attr.time_period` in lowercase, for example `day_1` or `min_5`. Treat generated CSVs as lookup aids, not as a reason to mix incompatible categories or skip required symbol/auth checks.

Current Tangram export columns are authoritative for generated lookup metadata: use column C `*IndexAPI代码` as `indicator_id`; use column S `扩展属性` as the primary attr source; use column U `支持排序`; use column Z `接入指南标签` to choose snapshot/series and `time_range`; use column AA `证券实体类型` for category/symbol handling; use column AB `交易所` for exchange context. For periods, prefer `time_period` values from column S attrs, and fall back to `id_router.yaml` only when column S does not define `time_period`.

At the first use of this skill in each conversation, run `scripts/export_indicators.py --status` before metric lookup or request construction. Tell the user how long ago the local Tangram workbook was updated, or that it is missing, and ask whether to update it now. Do this only once per conversation unless the user explicitly asks for freshness again. If the user asks to update, refresh, or rebuild local indicator metadata, run `scripts/export_indicators.py --rebuild-csvs` and report whether the workbook export and generated CSV rebuild succeeded. Do not silently use stale generated lookup data without the first-use freshness notice.

## Quick workflow

1. Classify the request as one of: watchlist, ranking list, stock detail snapshot, related symbols, relation list, industry linkage, ETF holdings, historical series, minute K-line, or single-symbol tick details.
2. Decide whether the user wants a request body only or an actual data fetch.
   - If the user wants actual quote or relation data, pick the scene first: sandbox Aime Claw, B-side, or C-side.
   - Use sandbox by default only when the user context is the sandbox/Aime Claw environment or no B/C context is provided.
   - Use B-side when the user mentions B-side, internal gateway, apisix, service-to-service, or `quote-apisix-gateway.hxapisix`.
   - Use C-side when the user mentions C-side, browser/client, web, cookie auth, or public quote domains.
   - For sandbox, check whether `AIME_API_KEY` is available.
   - For B-side or C-side, extract the caller-provided apikey or Cookie from the current user request or prior conversation context.
   - If the selected scene has the required auth, build the request and fetch data directly.
   - Compose the scene-specific auth header.
   - If auth is unavailable, fall back to request construction and explain which env/header is missing.
3. Choose the endpoint:
   - Use `snapshot` for realtime values, rankings, related symbols, constituents, subsectors, and holdings.
   - Use `relation_list` for a plain list of related codes or ids without indicator values, such as ETF holding codes, index components, prompt components, or group components.
   - Use `series` for continuous historical points such as past 30-day NAV or premium series.
   - Use `multi_kline` for K-line, candlestick, minute-bar, and `分时行情` requests. When the user asks for `分时行情`, build a minute K-line request such as `min_1` or `min_5`. For full same-day minute data, prefer `time_range = {"trade_date": 0, "date_offset": 0}`.
   - Use `single_tick` for `逐笔成交`, `成交明细`, or time-and-sales requests.
4. Confirm the market context when relevant: US stock, ETF, bond, crypto, or option. Use this to choose suitable symbol examples, indicators, and attrs.
5. Resolve user-provided security or industry text before choosing the symbol source:
   - When the user input contains a direct code, Ticker, security Chinese/English name, ETF name, bond name, crypto symbol, option symbol, industry Chinese/English name, or industry alias, first use the `ainvest-marketcode` skill to resolve it into an AInvest `market_code` if that skill is installed.
   - If `ainvest-marketcode` is unavailable, ask for an explicit AInvest `market_code` or use a caller-provided resolved market code. Do not guess a market code from ticker/name text.
   - Treat this resolution as a required upstream step for `security`, related-symbol, holdings, K-line, and tick requests that need a concrete tradable symbol or industry code.
   - If the user already provides an explicit AInvest `market_code` such as `185:AAPL`, use it directly and do not re-resolve unless ambiguity remains.
   - Do not call `ainvest-marketcode` for `market_env` indicators, because macro or market-environment metrics do not require a market code.
6. Choose the symbol source:
   - `market_code` for watchlists and stock detail pages.
   - `market`, `block_id`, or `prompt_id` for ranking pools.
   - `prompt_id` with `attr.market_code` for related stocks or ETFs.
   - `link_code` with `attr.link_type` for industry constituents, subsectors, and ETF holdings.
   - `relation_list` uses top-level `symbol` plus `symbol_type`, not the snapshot `symbol` array; supported `symbol_type` values are `market_code`, `prompt_id`, `block_id`, and `group_id`.
   - `snapshot` supports multiple symbol selector types: `market_code`, `ths_code`, `market`, `block_id`, `prompt_id`, `link_code`, `group_id`, `prompt_id_self`, `group_id_self`, `macro_region`, and `macro_metric`. Do not use `chain_id` in snapshot; it is for supported series requests.
   - `code_list` for `multi_kline` and `single_tick`. Split a direct `market_code` such as `185:AAPL` into `market = "185"` and `codes = ["AAPL"]`.
7. For metric lookup requests, search generated CSV first:
   - On the first use of this skill in the current conversation, run `scripts/export_indicators.py --status`, summarize local workbook freshness, and ask whether to update before continuing.
   - If the user asks to update local Tangram data or rebuild generated lookup CSVs, run `scripts/export_indicators.py --rebuild-csvs` before searching.
   - Run `scripts/find_quote_params.py --query <metric text or id>` when `references/generated/quote_request_lookup.csv` exists.
   - Use the returned `indicator_id`, `endpoint`, `access_guide`, `category`, `symbol_type`, `exchange`, `attrs_json`, and `time_range_json` as the primary request-construction fields.
   - Treat the returned `template` as a non-authoritative scenario hint only. It can suggest a nearby JSON starting point, but do not let it override `endpoint`, `access_guide`, symbol rules, attrs, time_range, or the user's actual scenario.
   - Treat legacy `references/legacy/id_dict.md` as the first priority for series `time_range` shape. If the legacy table says `begin_end`, use `{"type":"begin_end","begin_time":<recent_trading_day_ms>,"end_time":0,...}` by default; `begin_time = 0` and `end_time = 0` means full-history retrieval and should be avoided unless the user explicitly asks. `begin_time` and non-zero `end_time` must be millisecond timestamps, not seconds. If it says `trade_date = 0`, use `{"type":"trade_date","trade_date":0,...}`; otherwise use the generated/default `end_count` form.
   - Preserve nested `tech_param.children` and other structured attrs from `attrs_json` when composing the indicator `attr`.
   - For `event_id`, look up candidate IDs in `references/event_id_index.csv` first, then inspect `references/event_id.md` for the surrounding event meaning.
   - For `tech_param`, first reuse the working examples in `references/tech_param_retry_request.json` when the indicator id is present there; otherwise consult `references/tech.md` and keep the parameter structure required by the selected technical indicator.
   - For interval phrases such as `5分钟区间涨幅`, choose the interval `inr-*` metric and map the time phrase into `attr.time_period`; do not treat the time phrase as part of the metric alias.
   - For `market_env` rows, do not look up or require market codes and do not include `symbol`; query market-environment indicators separately from `security` indicators. If a query needs both categories, build separate request bodies.
   - If no generated CSV row matches, use the split indicator references in `references/`, then fall back to `references/legacy/id_dict.md`.
8. Build `indicator` entries and always include `req_unique_id`; `req_unique_id` must be unique within the same request, because duplicates are rejected as request errors.
9. Add control fields:
   - `snapshot`: `page` is required; include `sort` and `filter` only when needed. Use optional `full_symbols: true` only when the caller needs the complete resolved symbol universe in response `data.symbol_list`; normal paged data still stays under `data.data`.
   - `relation_list`: `page` is optional; include it only when the result list should be paged. `sort` and `filter` are not supported.
   - `series`: choose a supported `time_range` type for the requested indicator.
   - `multi_kline`: choose a valid `time_period`, `time_range`, and optional `adjust_type`.
   - `single_tick`: use the supported `time_range` combination `trade_date = 0`, `count`, and `end_time`.
10. Warn only on parameter errors or unsupported combinations. Do not treat empty values as request errors.

## Fixed business rules

- Endpoint paths are fixed by request type, but the host/base path depends on the scene: sandbox, B-side, or C-side.
- Direct sandbox fetches require `Authorization: Bearer <AIME_API_KEY>`.
- Direct B-side fetches require an `apikey` header supplied by the caller; do not read it from a default environment variable. Use the `index-api` apikey for `snapshot`, `series`, and `relation_list`, and the `quoteag` apikey for `multi_kline` and `single_tick`. Do not require both apikeys unless the user request actually calls both endpoint families.
- Direct C-side fetches require a `Cookie` header supplied by the caller; do not read it from a default environment variable.
- All request scenes should include the default header `X-Auth-ProgId: 7080`.
- Do not write caller-provided Cookie or apikey values into repository docs, templates, or durable files.
- Do not rely on scripts that are not present in this skill directory. The supported local scripts are listed in the Scripts section.
- Watchlists keep user-added order by default, so omit `sort` unless the user explicitly asks to sort.
- `related stock` and `related ETF` queries use `prompt_id` plus `attr.market_code`.
- Industry constituents and subsectors use `link_code`.
- ETF holdings use `link_code` with `attr.link_type = "holding"` and `value` must contain exactly one ETF code.
- `分时行情` should be built as a minute `multi_kline` request, not `single_trend`.
- `single_tick` is for one symbol only.
- `multi_kline` supports at most 16 symbols per request and at most 2000 returned K-line rows per request.
- crypto and options only support `trade_class = "intraday"` for these basic quote requests.
- US stocks and indices support `pre_market`, `intraday`, and `post_market`.
- Empty values are acceptable. Example: an option-only indicator queried on an ETF may return null or empty data.
- `snapshot` `res_symbol_type` is optional and only supports `market_code` or `ths_code`. Use it when the caller wants response `symbol_code` normalized to one of those two forms; `ths_code` input also returns `symbol_type = "ths_code"` by default.
- `snapshot` `ths_code` accepts THS codes such as `AAPL.O`. Unresolvable THS codes can still appear in the response with null values; do not pre-reject them solely because local market-code lookup misses.
- `snapshot` `prompt_id` allows optional `attr.market_code` for related-symbol requests and optional `attr.min`, `attr.max`, or `attr.value` as AIME model variables. Unknown `prompt_id` attrs are rejected.
- `snapshot` macro entity symbols use `macro_region` or `macro_metric` and must not be mixed with ordinary security symbol types or with each other. Market-environment indicators still omit `symbol`; macro entity symbol types are only for macro entity snapshot routes.
- `relation_list` only supports `relation = "holding"` or `"component"` and `symbol_type = "market_code"`, `"prompt_id"`, `"block_id"`, or `"group_id"`. It returns list items as `data.data[].v`; `data.symbol_type` describes the returned item type and may differ from the request `symbol_type`.
- `relation_list` `holding` only expands ETF holdings for `symbol_type = "market_code"`; holding requests for prompt, block, or group symbols can validly return an empty list.
- Invalid indicators are silently absent from the response; do not assume one returned item per requested indicator.
- Frontend display rule: when response `attr.value_type` is `ratio` or `ratio2`, display the value with `%`.
- For exported metric metadata, prefer `references/generated/quote_request_lookup.csv` when it exists. Then use the split indicator references in `references/`, then fall back to `references/legacy/id_dict.md` for uncovered metrics. The dictionary is not complete, so do not block on missing ids and do not try to validate all indicators exhaustively.
- Keep interval metric aliases time-neutral. For example, `inr-price_change_ratio_pct-sum` may have aliases like `区间收盘价涨幅` and `区间涨幅`, but not `5分钟区间涨幅`; parse `5分钟` into `time_period=MIN_5` at query time.
- When a request needs a concrete symbol and the user provides a code, Ticker, security name, ETF name, bond name, crypto/option symbol, industry name, or industry alias, resolve it with `ainvest-marketcode` before composing the quote request when that skill is available. If it is not available, require an explicit AInvest `market_code` or caller-provided resolved code. Use the resulting AInvest `market_code` as the source of truth for `market_code` symbols and for splitting into `market` + `codes` in `multi_kline` / `single_tick`.
- Keep `market_env` and `security` requests separate. `market_env` indicators such as crypto dominance, fear/greed, and altcoin season index do not need market codes; their snapshot bodies can omit `symbol`.
- For `series.time_range.type = "begin_end"`, `begin_time` and non-zero `end_time` are millisecond timestamps. `end_time = 0` means latest/current endpoint-supported end time. Do not send second-level Unix timestamps for `begin_end`. Avoid `begin_time = 0` with `end_time = 0` unless the user explicitly wants all history.
- Send `attr.time_period` values in lowercase. Uppercase values from old dictionaries or Excel exports must be normalized before request execution.
- For option call/put volume and turnover indicators `volume_call`, `turnover_call`, `volume_put`, and `turnover_put`, include the indicator attrs `end_count = {"end_time": 0, "count": 1}` or `{"end_time": 0, "count": 2}` and `trade_date = 0`. These attrs are required even when the rest of the request is a normal `snapshot` indicator body.

## Output expectations

When generating an answer, prefer this order:

1. State whether this will be a live fetch or a request-construction-only answer.
2. If live fetch is possible, state the scene and auth header shape used, without exposing the secret value.
3. State which endpoint to use and why.
4. Provide the request body.
5. If a live fetch is not possible, call out the missing header or execution requirement.
6. Mention response mapping details if relevant:
   - `req_unique_id` is the stable mapping key.
   - `snapshot` missing values may appear as `{"v": null}`.
   - `series` missing historical data may appear as an empty array.
   - `relation_list` returns relation items under `data.data[].v`, with total count under `data.page.total`.
   - `multi_kline` returns rows under `data.quote_data[].value[]`.
   - `single_tick` returns trade-print rows under `data.quote_data[].value[]`.

## Live fetch response format

When a live fetch is performed, keep the answer concise and structured around the fetched result instead of dumping the full raw JSON.

Preferred order:

1. State that live data was fetched and identify the endpoint.
2. State which auth/header path was used:
   - used sandbox `Authorization: Bearer <AIME_API_KEY>`, B-side `apikey`, or C-side `Cookie`
   - mention any additional request headers only if they matter for the request
3. Summarize the requested symbol, main indicators, and returned values.
4. Include the request body when it helps reproducibility.
5. Only mention raw response details that affect interpretation, such as null values, missing indicators, or empty series.

For `snapshot` live fetches, prefer a compact value summary such as:

- symbol
- indicator name
- returned value
- whether the value was null

For `series` live fetches, prefer a compact trend summary such as:

- symbol
- indicator name
- point count
- time range mode
- first and last point when useful

For `relation_list` live fetches, prefer a compact list summary such as:

- requested relation and source symbol
- returned `symbol_type`
- total count
- first few returned `v` values when useful

For `multi_kline` live fetches, prefer a compact bar summary such as:

- symbol
- `time_period`
- returned bar count
- first and last bar time when useful
- latest close or OHLC summary when useful

For `single_tick` live fetches, prefer a compact trade summary such as:

- symbol
- returned trade count
- most recent trade time
- most recent trade price
- recent trade side and size when useful

Do not paste the entire raw response unless the user explicitly asks for it.

If the API returns an error during a live fetch:

1. State that the live fetch failed.
2. Include `status_code` and `status_msg` if available.
3. Identify whether the problem is auth-related, parameter-related, or data-related.
4. If helpful, provide the corrected request body or the missing header requirement.

If scene auth is unavailable:

1. State that a live fetch was not attempted because the required auth source is missing.
2. Provide the request body the user can run.
3. Call out the required header for the selected scene.

## References

- Reference entry point: `references/index.md`
- Routing and endpoint choice: `references/routing.md`
- Snapshot rules and limits: `references/snapshot.md`
- Series rules and limits: `references/series.md`
- Basic minute K-line and tick rules: `references/basic-quote.md`
- Relation list rules and examples: `references/relation.md`
- Symbol source selection: `references/symbols.md`
- Frontend display and null handling: `references/display.md`
- Quote scenario patterns: `references/scenarios.md`
- Manual test headers and production curl example: `references/testing.md`
- Live fetch success examples: `references/live-fetch-examples.md`
- Template authoring rules: `references/template-writing.md`
- Indicator attrs: `references/indicator-attrs.md`
- Snapshot indicator quick reference: `references/snapshot-indicators.md`
- Series indicator quick reference: `references/series-indicators.md`
- Business pool quick reference: `references/business-pools.md`
- Generated metric lookup CSVs: `references/generated/`
- Full legacy indicator dictionary: `references/legacy/id_dict.md`
- Event id lookup index: `references/event_id_index.csv`
- Event id full reference: `references/event_id.md`
- Technical indicator parameter reference: `references/tech.md`
- Technical indicator retry examples: `references/tech_param_retry_request.json`
- Template lookup by scenario: `references/template-index.md`
- Raw basic quote protocol reference: `references/gms-http-v2.md`
- Legacy raw protocol references: `references/legacy/snapshot_cf.md`, `references/legacy/series_cf.md`

## Assets

Use the JSON files in `assets/request-templates/` as starting points for the main quote scenarios. Adjust indicators and sort fields based on the user request instead of rewriting from scratch.

## Scripts

- `scripts/fetch_quote.py`: execute one quote request from a template or JSON body. Supports `--scene sandbox|b|c`; sandbox uses `AIME_API_KEY`, B-side accepts `--index-api-apikey` or `--quoteag-apikey`, and C-side accepts `--auth-value`. B/C auth can also come from the shared local `Skills/env.json`.
- `scripts/export_indicators.py`: check freshness with `--status`, export the current AInvest-supported Tangram indicator workbook to `references/generated/export_metric_meta_new.xlsx`, and rebuild generated lookup CSVs with `--rebuild-csvs`.
- `scripts/build_quote_csvs.py`: build `id_dict_quote.csv` and `quote_request_lookup.csv` from the exported metric metadata workbook.
- `scripts/find_quote_params.py`: search generated request parameters by metric id, source code, Chinese name, or English name.
- `scripts/validate_templates.py`: validate request templates and basic `fetch_quote.py --dry-run` endpoint inference.
