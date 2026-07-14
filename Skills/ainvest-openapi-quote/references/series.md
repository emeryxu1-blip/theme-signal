# Series

Use this file for quote requests to:

- `https://open.ainvest.com/market/extquote/index/indicator/v2/series`

For B-side use `http://quote-apisix-gateway.hxapisix/index_api/indicator/v2/series`.
For C-side use `https://extquote.ainvest.com/index_api/indicator/v2/series`.

## When to use

Use `series` for historical trend queries such as:

- ETF NAV over the past 30 days
- ETF premium over the past 30 days
- security daily or minute historical metrics

## Limits

- code count `<= 16`
- indicator count `<= 20`
- per-code per-indicator point count `<= 2000`

## Request shape

```json
{
  "symbol": {
    "type": "market_code",
    "value": ["185:QQQ"]
  },
  "indicator": [
    {
      "id": "some_indicator",
      "req_unique_id": "id_0",
      "attr": {
        "time_period": "day_1"
      }
    }
  ],
  "time_range": {
    "type": "begin_end",
    "begin_time": 0,
    "end_time": 0,
    "time_period": "day_1"
  }
}
```

## Supported `time_range` modes

- `begin_end`: explicit start and end timestamps. `begin_time` and non-zero `end_time` are millisecond timestamps, not second-level Unix timestamps. Use `end_time = 0` for latest/current endpoint-supported end time.
- `end_count`: latest N points ending at `end_time`
- `trade_date`: one trading date, only for supported minute-level cases

## Important rules

- Always include `req_unique_id`.
- Use `references/series-indicators.md` first for common historical indicator ids and `time_range` guidance, then fall back to `references/legacy/id_dict.md`.
- `legacy/id_dict.md` is incomplete; do not fail a request only because a requested id is absent from that reference.
- Historical indicators do not all support the same `time_range.type`; verify support in the split references first and `references/legacy/id_dict.md` when needed.
- For day-or-longer timestamps, docs indicate using the exchange-local noon timestamp convention, still expressed as a millisecond timestamp.
- Empty historical arrays are acceptable and should not be treated as errors.
- Within one indicator series, points are sorted by `t` ascending.
- Local cases show that `series` can also be used with `prompt_id_self`, `chain_id`, and macro-style requests without `symbol` for supported indicators.

## Response shape

```json
{
  "status_code": 0,
  "status_msg": "success",
  "data": {
    "indicator": [],
    "symbol_type": "market_code",
    "data": [
      {
        "symbol_code": "185:QQQ",
        "value": [
          {
            "value": [
              { "t": 1677196800000, "v": 123.45 }
            ]
          }
        ]
      }
    ]
  }
}
```
