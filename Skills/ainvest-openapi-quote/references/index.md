# Reference Index

Use this file as the entry point for the skill references.

## Core references

- `routing.md`: choose `snapshot`, `series`, `relation_list`, `multi_kline`, or `single_tick`
- `snapshot.md`: snapshot request structure, limits, sort, filter, and response shape
- `series.md`: series request structure, limits, `time_range`, and response shape
- `basic-quote.md`: basic minute/candlestick quote routing plus `multi_kline` and `single_tick` rules
- `relation.md`: `relation/v1/list` request structure, supported relations, examples, and response shape
- `symbols.md`: symbol source selection and required attrs
- `display.md`: null handling and frontend display rules
- `scenarios.md`: scenario-level request patterns
- `template-index.md`: template lookup table
- `template-writing.md`: template authoring rules for this repository
- `testing.md`: production header requirements and manual validation notes
- `live-fetch-examples.md`: successful `snapshot`, `series`, `multi_kline`, and `single_tick` live-fetch response examples

## Indicator references

- `indicator-attrs.md`: request and response `attr` rules
- `snapshot-indicators.md`: commonly used snapshot indicators and mappings
- `series-indicators.md`: commonly used series indicators and `time_range` guidance
- `business-pools.md`: `block_id`, `prompt_id`, and `group_id` notes

## Legacy raw protocol docs

These files are useful when the concise references are insufficient, but they are not the primary authoring entry points:

- `gms-http-v2.md`
- `legacy/snapshot_cf.md`
- `legacy/series_cf.md`
- `legacy/id_dict.md`
