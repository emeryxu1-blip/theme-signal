# Snapshot Indicators

Use this file for the snapshot indicators that appear most often in this skill and its templates.

| Indicator id | Canonical `req_unique_id` | Meaning | Common attrs |
| --- | --- | --- | --- |
| `10` | `latest_price` | latest price | `trade_class` |
| `13` | `volume` | trading volume | `trade_class` |
| `19` | `turnover` | trading amount / turnover | `trade_class` |
| `55` | `security_name` | security display name | none |
| `65` | `open_interest` | option open interest | none |
| `199112` | `change_pct` | latest price change percent | `trade_class` |
| `inr-price_change_ratio_pct-sum` | `change_pct` | interval change percent | `trade_class`, `time_period`, optional `period_type` |
| `ext_etf_holding_ratio` | `holding_ratio` | ETF constituent holding ratio | `match_code` |
| `fund_overall_rating` | `overall_rating` | latest overall rating | none |
| `fund_flow_rating` | `flow_rating` | latest flow rating | none |
| `fund_fundamental_rating` | `fundamental_rating` | latest fundamental rating | none |
| `fund_momentum_rating` | `momentum_rating` | latest momentum rating | none |
| `fund_perform_rating` | `perform_rating` | latest performance rating | none |
| `fund_safe_rating` | `safe_rating` | latest safety rating | none |

## Notes

- Use `55` or `security_name` for symbol names. Do not use `199112` for names.
- Use `19` for turnover. Do not use `65` when the template intends turnover.
- Many other valid ids exist in `legacy/id_dict.md`; this file only covers the common authoring set for this repository.

