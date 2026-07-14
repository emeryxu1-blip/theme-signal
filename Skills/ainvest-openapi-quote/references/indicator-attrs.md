# Indicator Attrs

This file summarizes the request and response `attr` fields most relevant to this skill.

## Request attrs

- `trade_class`: trading session selector for quote-style metrics. Common values are `pre_market`, `intraday`, and `post_market`. Many realtime metrics require it.
- `time_period`: metric period selector such as `min_5`, `day_1`, `day_5`, `month_1`, or `year_1`.
- `period_type`: optional period mode. Use when the indicator distinguishes calendar windows from rolling windows.
- `match_code`: comparison symbol context, commonly used with linked ETF holding ratio metrics.
- `event_id`: fixed event-window identifier for event-driven ranking metrics.
- `tech_param`: parameter array for technical indicators such as MACD.

## Response attrs

- `value_type`: display classification such as `price`, `ratio`, `ratio2`, or `date`.
- `unit`: output unit such as `%`, `x100`, or currency strings.
- `trade_class`, `time_period`, `period_type`, `match_code`, `event_id`: may be echoed back for indicator context.

## Display guidance

- If `value_type` is `ratio` or `ratio2`, the consumer currently displays the value with `%`.
- If `value_type` is `date`, render using the exchange-local trading date.
- Use `display.md` for frontend handling and null-value behavior.

## Source

This summary is extracted from `legacy/id_dict.md` and optimized for template authoring.
