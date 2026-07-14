# Template Writing

Use these rules when adding or updating files in `assets/request-templates/`.

## Naming

- Keep file names scenario-based and concise.
- Prefer endpoint-first mental grouping even if the current file names are not prefixed.
- Use one template per primary scenario, not one template per slight metric variation.

## Indicator rules

- Every indicator must include `req_unique_id`.
- `req_unique_id` should describe the returned field, not the raw backend id.
- Reuse canonical names for common indicators:
  - `10` -> `latest_price`
  - `13` -> `volume`
  - `19` -> `turnover`
  - `55` -> `security_name`
  - `199112` -> `change_pct`
  - `65` -> `open_interest`
- If you intentionally use a different `req_unique_id`, document why in the template review.

## Symbol rules

- `prompt_id` for related-symbol requests must include `attr.market_code`.
- `link_code` requests must include `attr.link_type`.
- ETF holdings requests must use `link_type = "holding"` and exactly one ETF code.
- Use `market_code` for direct symbol lists and detail pages.

## Control-field rules

- `snapshot` templates should include `page`.
- Watchlists should omit `sort` unless sorting is part of the scenario.
- Ranked list templates should include both `sort` and `page`.
- `sort.pos` is zero-based and must point to a valid indicator index.
- `filter` should appear only when filtering is part of the scenario.

## Basic quote template rules

- `multi_kline` and `single_tick` templates use `code_list`, not `symbol`.
- Split `market_code` such as `185:AAPL` into `market = "185"` and `codes = ["AAPL"]`.
- `multi_kline` requires `trade_class`, `time_period`, and `time_range`.
- For same-day full-session minute templates, prefer `time_range.trade_date = 0` plus `date_offset = 0`.
- `multi_kline` should respect the documented max of 16 codes and 2000 returned bars per request.
- `single_tick` requires exactly one code.
- `single_tick` should use the supported `time_range` combination: `trade_date = 0`, `count`, and `end_time`.
- Only use `pre_market` and `post_market` on supported symbols such as US stocks and indices; crypto and options should stay on `intraday`.

## Relation template rules

- `relation_list` templates use top-level `relation`, `symbol`, `symbol_type`, and optional `page`.
- Supported `relation` values are `holding` and `component`.
- Supported `symbol_type` values are `market_code`, `prompt_id`, `block_id`, and `group_id`.
- Do not include `indicator`, `sort`, or `filter` in relation templates.
- When `page` is present, use `begin >= 0` and `count > 0`.

## Validation

- Run `python3 scripts/validate_templates.py` after template edits.
- Cross-check new indicator ids against the split references before falling back to `legacy/id_dict.md`.
