# Relation List

Use `relation_list` for plain related-code lists that do not need indicator values.

## Endpoint

Sandbox Aime Claw:

- `https://open.ainvest.com/market/extquote/index/relation/v1/list`

B-side:

- `http://quote-apisix-gateway.hxapisix/index_api/relation/v1/list`

C-side:

- `https://extquote.ainvest.com/index_api/relation/v1/list`

Auth follows the index-api family:

- sandbox: `Authorization: Bearer <AIME_API_KEY>`
- B-side: `apikey: <caller-provided index-api apikey>`
- C-side: `Cookie: <caller-provided cookie>`
- all scenes: `X-Auth-ProgId: 7080`

## Request Shape

```json
{
  "relation": "component",
  "symbol": "89:861076",
  "symbol_type": "market_code",
  "page": {
    "begin": 0,
    "count": 10
  }
}
```

Fields:

- `relation`: required; supported values are `holding` and `component`
- `symbol`: required; exactly one source symbol or id
- `symbol_type`: required; supported values are `market_code`, `prompt_id`, `block_id`, and `group_id`
- `page`: optional; when provided, `begin >= 0` and `count > 0`

This endpoint does not support `indicator`, `sort`, or `filter`.

## Supported Patterns

ETF holdings:

```json
{
  "relation": "holding",
  "symbol": "185:QQQ",
  "symbol_type": "market_code",
  "page": {
    "begin": 0,
    "count": 10
  }
}
```

Index or industry components:

```json
{
  "relation": "component",
  "symbol": "89:861076",
  "symbol_type": "market_code"
}
```

Prompt components:

```json
{
  "relation": "component",
  "symbol": "6908b551069a48065f15938d",
  "symbol_type": "prompt_id",
  "page": {
    "begin": 0,
    "count": 10
  }
}
```

Group components:

```json
{
  "relation": "component",
  "symbol": "scenarios-beginner-friendly",
  "symbol_type": "group_id"
}
```

Group list backed by Redis relation JSON:

```json
{
  "relation": "component",
  "symbol": "scenarios-list",
  "symbol_type": "group_id",
  "page": {
    "begin": 0,
    "count": 10
  }
}
```

Holding requested for non-`market_code` symbols:

```json
{
  "relation": "holding",
  "symbol": "6908b551069a48065f15938d",
  "symbol_type": "prompt_id"
}
```

This is valid but returns an empty market-code list. The same rule applies to `block_id` and `group_id`.

## Response Shape

Successful response:

```json
{
  "status_code": 0,
  "status_msg": "success",
  "data": {
    "symbol_type": "market_code",
    "data": [
      {
        "v": "185:AAPL"
      }
    ],
    "page": {
      "total": 1
    }
  }
}
```

Response rules:

- `data.data` is a list of relation items.
- Each item returns the related value under `v`.
- `data.symbol_type` is the returned item type. It can differ from request `symbol_type`, especially for `group_id` queries where Redis JSON top-level `item_type` decides the result type.
- `data.page.total` is the total count before optional pagination.
- Empty lists are valid data results when a relation is unsupported for the source symbol type.

## Template Files

- `assets/request-templates/relation-etf-holding.json`
- `assets/request-templates/relation-index-components.json`
- `assets/request-templates/relation-prompt-components.json`
- `assets/request-templates/relation-block-components.json`
- `assets/request-templates/relation-group-components.json`
- `assets/request-templates/relation-group-list.json`
- `assets/request-templates/relation-prompt-holding-empty.json`
