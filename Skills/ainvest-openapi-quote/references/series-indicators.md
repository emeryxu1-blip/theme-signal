# Series Indicators

Use this file for the historical indicators that appear most often in this skill.

| Indicator id | Canonical `req_unique_id` | Typical use | Common `time_range.type` |
| --- | --- | --- | --- |
| `fund_nav` | `nav_30d` | ETF NAV history | `end_count`, `begin_end` |
| `block_average_fee_rate` | `avg_fee_rate` | chain-level fee history | `end_count`, `begin_end` |
| `fund_overall_rating` | `overall_rating_*` | prompt-self rating history | `end_count` |
| `fund_flow_rating` | `flow_rating` | prompt-self rating history | `end_count` |
| `fund_fundamental_rating` | `fundamental_rating` | prompt-self rating history | `end_count` |
| `fund_momentum_rating` | `momentum_rating` | prompt-self rating history | `end_count` |
| `fund_perform_rating` | `perform_rating` | prompt-self rating history | `end_count` |
| `fund_safe_rating` | `safe_rating` | prompt-self rating history | `end_count` |
| `inr-price_change_ratio_pct-sum` | `change_pct` | historical interval performance | `begin_end` |

## Notes

- `series` indicators do not all support the same `time_range.type`.
- Prefer `end_count` for recent-N-point templates and `begin_end` for explicit windows.
- Keep `req_unique_id` stable when requesting the same indicator with different attrs.
- Fall back to `legacy/id_dict.md` only when this summary does not cover the requested metric.
