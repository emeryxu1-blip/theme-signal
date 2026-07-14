# Template Index

Use this file to quickly choose a request template by scenario.

This template set is for AInvest quote retrieval across US stocks, ETFs, bonds, crypto, and options, and covers indicator-style quote data, relation lists, plus the basic quote APIs.

| Scenario | Endpoint | Template | Key constraints |
| --- | --- | --- | --- |
| Watchlist basics | snapshot | `assets/request-templates/watchlist.json` | Default keep watchlist order, omit `sort` unless requested |
| Homepage ranking by block | snapshot | `assets/request-templates/ranking-block.json` | Use `block_id`, include `sort` and `page` |
| Homepage ranking by market | snapshot | `assets/request-templates/ranking-market.json` | Use `market`, include `sort` and `page` |
| Homepage ranking by prompt | snapshot | `assets/request-templates/ranking-prompt.json` | Use `prompt_id`, include `sort` and `page` |
| Stock detail snapshot | snapshot | `assets/request-templates/stock-detail.json` | Single `market_code` |
| Stock detail by THS code | snapshot | `assets/request-templates/stock-detail-ths-code.json` | `ths_code` input returns THS code by default |
| Related stocks | snapshot | `assets/request-templates/related-stock.json` | `prompt_id` plus `attr.market_code` |
| Related ETFs | snapshot | `assets/request-templates/related-etf.json` | `prompt_id` plus `attr.market_code` |
| Industry constituents | snapshot | `assets/request-templates/industry-components.json` | `link_code` plus `link_type=component` |
| Industry subsectors | snapshot | `assets/request-templates/industry-subsector.json` | `link_code` plus `link_type=subsector` |
| ETF holdings | snapshot | `assets/request-templates/etf-holdings.json` | `link_code` plus `link_type=holding`, one ETF code only |
| ETF holding ratio | snapshot | `assets/request-templates/etf-holding-ratio.json` | `match_code` required in indicator attr |
| Group rating list with filter | snapshot | `assets/request-templates/group-rating-filter.json` | `group_id`, `sort`, `filter` |
| Prompt-self rating snapshot | snapshot | `assets/request-templates/prompt-self-ratings.json` | `prompt_id_self` |
| Group-self full symbol list | snapshot | `assets/request-templates/group-self-full-symbols.json` | `group_id_self`, `full_symbols=true` adds response `symbol_list` |
| Macro region snapshot | snapshot | `assets/request-templates/macro-region-snapshot.json` | `macro_region`; indicator ids are macro catalog `indicator_code` values |
| Macro region group list | snapshot | `assets/request-templates/macro-region-list.json` | `group_id=macro-region-list`; supports sorting by macro catalog indicator id |
| Macro metric snapshot | snapshot | `assets/request-templates/macro-metric-snapshot.json` | `macro_metric`; symbol values are subjects, indicators are `macro_*` fields |
| Event window block ranking | snapshot | `assets/request-templates/block-event-window.json` | `event_id` required |
| Technical MACD snapshot | snapshot | `assets/request-templates/technical-macd.json` | `tech_param` required |
| Market env snapshot | snapshot | `assets/request-templates/market-env-snapshot.json` | No `symbol` needed for supported macro ids |
| Options market ranking | snapshot | `assets/request-templates/options-market-ranking.json` | Uses `market=UAOS` |
| Multi-market basic snapshot | snapshot | `assets/request-templates/multi-market-basics.json` | Mixed crypto spot/perp symbols |
| Minute K-line / intraday chart | multi_kline | `assets/request-templates/multi-kline-minute.json` | Use minute `time_period`; same-day full-session default is `trade_date=0,date_offset=0`; max 16 codes and 2000 returned bars |
| Single-symbol tick details | single_tick | `assets/request-templates/single-tick.json` | Exactly one code; use `trade_date=0`, `count`, and `end_time` |
| ETF holding code list | relation_list | `assets/request-templates/relation-etf-holding.json` | `relation=holding`, `symbol_type=market_code`, optional `page` |
| Index or industry component list | relation_list | `assets/request-templates/relation-index-components.json` | `relation=component`, `symbol_type=market_code` |
| Prompt component list | relation_list | `assets/request-templates/relation-prompt-components.json` | `relation=component`, `symbol_type=prompt_id`, optional `page` |
| Block component list | relation_list | `assets/request-templates/relation-block-components.json` | `relation=component`, `symbol_type=block_id`, optional `page` |
| Group component list | relation_list | `assets/request-templates/relation-group-components.json` | `relation=component`, `symbol_type=group_id` |
| Group id list from relation JSON | relation_list | `assets/request-templates/relation-group-list.json` | Returned `data.symbol_type` comes from Redis JSON `item_type` |
| Prompt holding empty-list case | relation_list | `assets/request-templates/relation-prompt-holding-empty.json` | Valid request that returns an empty market-code list |
| ETF 30-day series | series | `assets/request-templates/series-etf-30d.json` | Check indicator support for `time_range.type` |
| Chain history series | series | `assets/request-templates/series-chain-history.json` | `symbol.type=chain_id` |
| Macro metric history series | series | `assets/request-templates/series-macro-metric-history.json` | `symbol.type=macro_metric`, `time_range.type=begin_end` |
| Prompt-self rating series | series | `assets/request-templates/series-prompt-self-ratings.json` | `symbol.type=prompt_id_self` |

## Authoring notes

- Validate template changes with `python3 scripts/validate_templates.py`.
- Use `references/template-writing.md` for naming, indicator mapping, and control-field conventions.
