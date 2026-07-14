# Symbols

Use this file to choose the correct `symbol.type` and required attributes.

## `market_code`

Use for:

- watchlists
- stock detail pages
- single security snapshot queries
- direct historical series for a known symbol

Example:

```json
{
  "type": "market_code",
  "value": ["185:AAPL", "185:QQQ"]
}
```

## `ths_code`

Use when the caller needs to send or receive THS-style codes instead of AInvest `market:code`.

Example:

```json
{
  "type": "ths_code",
  "value": ["AAPL.O", "QQQ.O"]
}
```

Notes:

- The service converts THS codes to internal `market_code` before routing.
- The snapshot response uses `symbol_type: "ths_code"` by default for THS-code input and restores the returned `symbol_code`.
- Unknown THS codes should not be rejected by the skill solely because they cannot be resolved locally; the API can return them as empty/null rows.

## `market`

Use for market-wide leaderboard pools when the feature is defined by market instead of block or prompt.

Known usage:

- many option homepage all-market rankings

Current validation accepts one value and only `UAOS` or `UDC`.

## `block_id`

Use for predefined market-wide pools such as full-market stock rankings.

## `prompt_id`

Use for:

- homepage ranking pools driven by product prompts
- related stocks
- related ETFs

Known prompt examples:

- US stocks related stocks: `67aeeb7c5a963469b5ef9a6d`
- Crypto related stocks: `6746dfde784e3a2b800f5b9c`
- US stocks related ETFs: `677251bbbc4823684c64145d`

For related-symbol requests, include `attr.market_code`.

Example:

```json
{
  "type": "prompt_id",
  "value": ["67aeeb7c5a963469b5ef9a6d"],
  "attr": {
    "market_code": "185:AAPL"
  }
}
```

For AIME model-SQL prompt pools, `prompt_id` also allows optional `attr.min`, `attr.max`, and `attr.value`. These values may be numbers, strings, booleans, or null and are passed as typed variables. Unknown prompt attrs are rejected.

## `link_code`

Use for linked pools derived from one code.

Known patterns:

- `component`: industry constituents
- `subsector`: child industry indices
- `holding`: ETF holdings

Industry codes are THS industry index symbols such as `89:861076`.

Example: industry constituents

```json
{
  "type": "link_code",
  "value": ["89:861076"],
  "attr": {
    "link_type": "component"
  }
}
```

Example: subsectors

```json
{
  "type": "link_code",
  "value": ["89:861076"],
  "attr": {
    "link_type": "subsector"
  }
}
```

Example: ETF holdings

```json
{
  "type": "link_code",
  "value": ["185:QQQ"],
  "attr": {
    "link_type": "holding"
  }
}
```

ETF holdings rule:

- `value` must contain exactly one ETF code

## `group_id`

Use for custom product-defined pools when the product already maintains a named group.

Example from local cases:

```json
{
  "type": "group_id",
  "value": ["etf-rating-prompt-list"]
}
```

Macro group ids currently used by `index_api`:

- `macro-region-list`: expands to macro regions/countries and does not need `attr`.
- `macro-metric-by-indicator`: expands to macro subjects for one indicator; requires `attr.id`, for example `GDP`.
- `macro-metric-by-region`: expands to macro subjects for one region; requires `attr.region`, for example `US`.

## `prompt_id_self`

Use when the request should target the prompt items themselves instead of resolving component securities.

Example:

```json
{
  "type": "prompt_id_self",
  "value": ["6908b551069a48065f15938d", "6908b52b069a48065f15938c"]
}
```

## `group_id_self`

Use when the request should target group items themselves instead of resolving to component securities.

Example:

```json
{
  "type": "group_id_self",
  "value": ["185:ALLW:1"]
}
```

The service uses an internal routing market and strips that internal prefix from `symbol_code` in the response.

## `macro_region`

Use for macro entity snapshot queries keyed by region or country ids.

Example:

```json
{
  "type": "macro_region",
  "value": ["US", "CN"]
}
```

`macro_region` indicator ids are uppercase macro upstream `indicator_code` values from `/indicators/macro/v1/catalog/?catalog_type=indicators`. Examples include `GDP`, `CPI`, `GDPQQ`, and `IRYY`. The service sends them upstream as `indicator_codes` and reads the latest value from `macro_last`.

## `macro_metric`

Use for macro entity snapshot or series queries keyed by macro metric ids.

Example:

```json
{
  "type": "macro_metric",
  "value": ["USGDP"]
}
```

`macro_metric` symbol values are macro subjects. Snapshot indicator ids are `macro_*` fields such as `macro_last`, `macro_previous`, `macro_unit`, `macro_source`, or `macro_observation_period`. Series currently supports `macro_metric` with `time_range.type = "begin_end"`.

Macro entity rules:

- `macro_region` and `macro_metric` values must be non-empty and must not contain `:`.
- Do not mix macro symbol types with ordinary security selectors.
- Do not mix `macro_region` and `macro_metric` in the same request.
- Market-environment indicators still omit `symbol`; do not use macro symbol types for those market_env rows.

## `chain_id`

Use for chain-based historical metrics in `series`.

Example:

```json
{
  "type": "chain_id",
  "value": ["L000000006"]
}
```

## Selection summary

- Known explicit symbols: `market_code`
- THS-code input/output: `ths_code`
- All-market ranking pool: `market` or `block_id`
- Prompt-defined pool or related symbols: `prompt_id`
- Linked symbols from one source symbol: `link_code`
- Product-defined custom pool: `group_id`
- Prompt items themselves: `prompt_id_self`
- Group items themselves: `group_id_self`
- Macro entity rows: `macro_region` or `macro_metric`
- Chain-level history: `chain_id`

## Relation list symbol fields

`relation_list` does not use the indicator `symbol` array. It uses top-level fields:

```json
{
  "relation": "component",
  "symbol": "89:861076",
  "symbol_type": "market_code"
}
```

Supported `symbol_type` values are:

- `market_code`: ETF holdings or index/industry components
- `prompt_id`: prompt components
- `block_id`: block components
- `group_id`: group components or group item lists backed by Redis relation JSON

The returned `data.symbol_type` describes the type of `data.data[].v` and can differ from the request `symbol_type`.
