# Generated Quote CSVs

This directory stores generated CSV indexes for `ainvest-openapi-quote`.

- `id_dict_quote.csv`: `id_dict.csv` compatible rows derived from the exported Tangram metric workbook.
- `quote_request_lookup.csv`: request-ready lookup rows used by the skill to choose indicator IDs, attrs, endpoint type, symbol context, and time_range hints.
  - `aliases` stores time-neutral aliases.
  - `is_interval_metric` marks interval-style metrics.
  - `period_values_json` lists supported `time_period` values for query-time period parsing.
  - `exchange` stores the Tangram `交易所` value used to decide symbol market/exchange context.
  - `access_guide` stores the Tangram `接入指南标签` value that determines snapshot/series and time_range shape.
  - `template` is only a non-authoritative scenario hint. Use it as a nearby JSON starting point if helpful; do not let it override `endpoint`, `access_guide`, symbol rules, attrs, or time_range.
  - `category` separates `market_env` from `security`; never mix these categories into one request.

Current Tangram workbook mapping:

- Use column C `*IndexAPI代码` as the generated `indicator_id` and code lookup key.
- Use column S `扩展属性` as the primary request attr source.
- Use column U `支持排序` for generated sortability.
- Use column Z `接入指南标签` to choose request shape:
  - `STANDARD_SNAPSHOT`: security snapshot.
  - `STANDARD_SNAPSHOT_MARKET_ENV`: market_env snapshot.
  - `STANDARD_SERIES_TIME_RANGE_ALL`: series with both `begin_end` and `end_count` support.
  - `STANDARD_SERIES_TIME_RANGE_BEGIN_END`: series `begin_end`.
  - `STANDARD_SERIES_TIME_RANGE_END_COUNT`: series `end_count`.
  - `STANDARD_SERIES_MARKET_ENV`: market_env series.
- Use column AA `证券实体类型` to decide symbol/category handling.
- Use column AB `交易所` as the exchange/market context.
- For periods, prefer `time_period` values from column S attrs; when absent, fall back to `id_router.yaml`.

Refresh the source workbook from Tangram first when network/session access is available:

```bash
python3 skills/ainvest-openapi-quote/scripts/export_indicators.py
```

The default workbook path is
`skills/ainvest-openapi-quote/references/generated/export_metric_meta_new.xlsx`.
Keep this source workbook inside the skill directory.

Check local freshness without network access:

```bash
python3 skills/ainvest-openapi-quote/scripts/export_indicators.py --status
```

Then rebuild the generated CSVs from the repository root with:

```bash
python3 skills/ainvest-openapi-quote/scripts/build_quote_csvs.py
```

Or export the workbook and rebuild both generated CSVs in one step:

```bash
python3 skills/ainvest-openapi-quote/scripts/export_indicators.py --rebuild-csvs
```

If Tangram access is unavailable, rebuild from the newest trusted local workbook
at this generated path, or pass another temporary workbook explicitly with
`--input`.

This directory is for generated lookup metadata only. Keep bulk response captures and ad hoc coverage output outside the skill tree unless their scripts, tests, and documentation are committed together.

Do not store API keys, cookies, or database credentials in these generated files.
