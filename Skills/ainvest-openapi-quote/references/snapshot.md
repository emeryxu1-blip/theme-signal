# Snapshot

Use this file for quote requests to:

- `https://open.ainvest.com/market/extquote/index/indicator/v2/snapshot`

For B-side use `http://quote-apisix-gateway.hxapisix/index_api/indicator/v2/snapshot`.
For C-side use `https://extquote.ainvest.com/index_api/indicator/v2/snapshot`.

## Request shape

```json
{
  "symbol": [],
  "indicator": [],
  "sort": [],
  "page": {
    "begin": 0,
    "count": 20
  },
  "full_symbols": false,
  "res_symbol_type": "market_code"
}
```

## Core fields

- `symbol`: array of symbol pool selectors
- `indicator`: array of requested metrics
- `sort`: optional; sort by indicator position
- `page`: required for all snapshot requests
- `filter`: optional; used when the list should be constrained before paging
- `full_symbols`: optional; when `true`, response includes `data.symbol_list` containing the full resolved symbol universe in addition to paged `data.data`
- `res_symbol_type`: optional; only `market_code` or `ths_code`, used to force response `symbol_code` format when possible

## Limits

- total input codes `<= 10000`
- indicator count `<= 50`
- `page.count <= 1000`
- `market` selector currently accepts one value only, and only `UAOS` or `UDC`
- `prompt_id`, `block_id`, and `group_id` selectors currently accept one value per selector object

## Important rules

- Always include `req_unique_id` for every indicator.
- Use `references/snapshot-indicators.md` first for common indicator ids and attrs, then fall back to `references/legacy/id_dict.md` when needed.
- `legacy/id_dict.md` is incomplete; missing ids should not be treated as proof that an indicator is invalid.
- Response indicator order is not guaranteed. Map by `req_unique_id`.
- If `sort` is omitted, result order follows the resolved symbol order.
- Watchlists should omit `sort` by default to preserve user-added order.
- When an indicator has no value for a symbol, `snapshot` may return `{"v": null}`.
- If a sorted value is null, it sorts to the end.
- `symbol.op` may be empty, `union`, `intersect`, or `exclude`. Resolved symbols are deduplicated; union order is preserved, intersect results follow the first intersect set, and excluded symbols are removed.
- `ths_code` input is converted internally to `market_code` and returned as `ths_code` unless `res_symbol_type` says otherwise. Unknown THS codes are treated as invalid symbols and can produce null/empty values rather than a request-construction error.
- `prompt_id` supports `attr.market_code` for related-symbol requests and also allows `attr.min`, `attr.max`, and `attr.value` as AIME model variables. Do not add other prompt attrs.
- Macro entity symbols (`macro_region`, `macro_metric`) cannot be mixed with ordinary security symbol types, and the two macro symbol types cannot be mixed with each other.
- For `macro_region`, indicator ids are macro upstream `indicator_code` values from `/indicators/macro/v1/catalog/?catalog_type=indicators`, for example `GDP`, `CPI`, `GDPQQ`, or `IRYY`. The upstream value field is fixed to `macro_last`.
- For `macro_metric`, symbol values are macro subjects such as `USGDP`, and indicator ids are `macro_*` fields such as `macro_last`, `macro_previous`, or `macro_unit`.
- `market_env` indicators are still different from macro entity symbols: market-environment snapshot requests omit `symbol`, `sort`, and `filter`.

## Supported symbol source patterns

- `market_code`: explicit security list
- `ths_code`: THS code input, returned as THS code by default
- `market`: full market pools
- `block_id`: predefined market block pools
- `prompt_id`: prompt-driven pools and related-symbol pools
- `link_code`: constituent, subsector, and holding relationships
- `group_id`: product-defined custom pools
- `prompt_id_self`: request the prompt items themselves instead of their component symbols
- `group_id_self`: request group items themselves instead of their component securities
- `macro_region`: macro region entities such as country/region ids; use catalog indicator codes as indicator ids
- `macro_metric`: macro subject entities such as `USGDP`; use `macro_*` fields as indicator ids

See `references/symbols.md` for when to use each one.

## Sorting

`sort.pos` is zero-based and refers to the `indicator` array position.

Example:

```json
"sort": [
  {
    "pos": 0,
    "order": "desc"
  }
]
```

## Filtering

Use `filter` only when the user explicitly needs value-based filtering. Supported operations include:

- `between`
- `in`
- `gt`
- `ge`
- `lt`
- `le`
- `eq`

Conditions are combined with `AND`.

## Advanced attrs seen in local test cases

Besides the common `trade_class`, `time_period`, and `period_type`, local cases also show:

- `event_id`: event-window metrics on `block_id` pools
- `match_code`: linked comparison context such as ETF holding ratio
- `tech_param`: technical indicator parameter lists

These attrs are indicator-specific. Check `references/indicator-attrs.md` first, then `references/legacy/id_dict.md` if needed.

## Response shape

```json
{
  "status_code": 0,
  "status_msg": "success",
  "data": {
    "indicator": [],
    "symbol_type": "market_code",
    "symbol_list": ["185:AAPL", "185:NVDA"],
    "data": [
      {
        "symbol_code": "185:AAPL",
        "value": [
          { "v": 123.45 }
        ]
      }
    ],
    "page": {
      "total": 1
    }
  }
}
```

`symbol_list` appears only when `full_symbols` is enabled and is the full resolved universe before page slicing; `data.data` remains the paged values.
