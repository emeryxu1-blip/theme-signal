# Live Fetch Examples

Use this file for concrete successful response examples when the skill performs a live fetch.

## Auth reminder

Live fetches in this skill use:

- sandbox indicator endpoint base: `https://open.ainvest.com/market/extquote/index/indicator/v2`
- sandbox basic quote endpoint base: `https://open.ainvest.com/market/extquote/ag/quote/v2`
- sandbox auth header: `Authorization: Bearer <AIME_API_KEY>`
- B-side auth header: caller-provided `apikey`
- C-side auth header: caller-provided `Cookie`

If the selected scene auth value is unavailable, do not present these examples as if the skill has already fetched live data. Fall back to request construction only.

## Example 1: `snapshot` success

Scenario: fetch two symbols' 5-minute interval change plus security name, sort by 5-minute change descending, and keep the top 2.

```json
{
  "status_code": 0,
  "status_msg": "success",
  "data": {
    "indicator": [
      {
        "id": "inr-price_change_ratio_pct-sum",
        "attr": {
          "trade_class": "intraday",
          "time_period": "min_5"
        },
        "req_unique_id": "id_0"
      },
      {
        "id": "55",
        "req_unique_id": "id_1"
      }
    ],
    "symbol_type": "market_code",
    "data": [
      {
        "symbol_code": "185:AAPL",
        "value": [
          {
            "v": 13.5
          },
          {
            "v": "Apple"
          }
        ]
      },
      {
        "symbol_code": "185:TSLA",
        "value": [
          {
            "v": 0.89
          },
          {
            "v": "Tsla"
          }
        ]
      }
    ],
    "page": {
      "total": 2
    }
  }
}
```

Interpretation notes:

- This is a normal `snapshot` success shape with `status_code = 0`.
- Returned values align to the request indicator order, but the stable mapping key is still `req_unique_id`.
- `55` is the correct security-name indicator in this repository.

## Example 2: `series` success

Scenario: fetch the past 5 days of `fund_overall_rating` for two ETF symbols.

```json
{
  "status_code": 0,
  "status_msg": "success",
  "data": {
    "symbol_type": "market_code",
    "indicator": [
      {
        "id": "fund_overall_rating",
        "req_unique_id": "id0"
      }
    ],
    "data": [
      {
        "symbol_code": "185:QQQ",
        "value": [
          {
            "value": [
              {
                "t": 1773590400000,
                "v": null
              },
              {
                "t": 1773676800000,
                "v": 7.788541740450224
              },
              {
                "t": 1773763200000,
                "v": null
              },
              {
                "t": 1773849600000,
                "v": 7.856919001568468
              },
              {
                "t": 1773936000000,
                "v": 7.353239006583557
              }
            ]
          }
        ]
      },
      {
        "symbol_code": "185:TQQQ",
        "value": [
          {
            "value": [
              {
                "t": 1773590400000,
                "v": 7.344985483308587
              },
              {
                "t": 1773676800000,
                "v": 7.237737045474129
              },
              {
                "t": 1773763200000,
                "v": null
              },
              {
                "t": 1773849600000,
                "v": 7.459472748614996
              },
              {
                "t": 1773936000000,
                "v": null
              }
            ]
          }
        ]
      }
    ]
  }
}
```

Interpretation notes:

- This is a normal `series` success shape with one indicator and multiple symbols.
- Each symbol returns one `value` item per requested indicator.
- The historical points sit under `value[].value[]` and are ordered by `t` ascending.
- `v: null` is an acceptable data result, not necessarily a request error.

## Example 3: `multi_kline` success

Scenario: fetch two US symbols' 1-minute intraday bars.

```json
{
  "status_code": 0,
  "status_msg": "ok",
  "data": {
    "quote_data": [
      {
        "market": "185",
        "code": "AAPL",
        "minute_window_type": "minute_window_pre",
        "data_fields": ["1", "7", "8", "9", "11", "13", "19"],
        "value": [
          [1774661400000, 222.11, 222.35, 221.98, 222.24, 182334, 40481216],
          [1774661460000, 222.24, 222.4, 222.18, 222.33, 95642, 21246851]
        ]
      },
      {
        "market": "185",
        "code": "TSLA",
        "minute_window_type": "minute_window_pre",
        "data_fields": ["1", "7", "8", "9", "11", "13", "19"],
        "value": [
          [1774661400000, 171.55, 171.88, 171.44, 171.73, 245118, 42032211],
          [1774661460000, 171.73, 171.9, 171.62, 171.79, 118020, 20276489]
        ]
      }
    ]
  }
}
```

Interpretation notes:

- This is the normal basic-quote K-line shape under `data.quote_data[]`.
- `data_fields` maps to OHLCV-plus-turnover values in each row.
- For `分时行情`, the request should use a minute `time_period` such as `min_1` or `min_5`.

## Example 4: `single_tick` success

Scenario: fetch the latest 5 intraday trades for one US stock.

```json
{
  "status_code": 0,
  "status_msg": "ok",
  "data": {
    "data_class": "tick",
    "quote_data": [
      {
        "market": "185",
        "code": "AAPL",
        "data_fields": ["1", "10", "12", "49", "65558", "65541"],
        "value": [
          [1774661495123, 222.31, "buy", 100, 20260327, "intraday"],
          [1774661495210, 222.3, "sell", 200, 20260327, "intraday"],
          [1774661495302, 222.32, "buy", 50, 20260327, "intraday"],
          [1774661495488, 222.29, "unknown", 10, 20260327, "intraday"],
          [1774661495601, 222.28, "sell", 80, 20260327, "intraday"]
        ]
      }
    ]
  }
}
```

Interpretation notes:

- `single_tick` returns trade-print rows, not bar aggregates.
- The stable shape is `data.quote_data[0].value[]`, with fields described by `data_fields`.
- This endpoint is for `逐笔成交` and `成交明细`, not for line or candlestick charts.
