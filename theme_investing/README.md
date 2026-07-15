# Theme Investing Agentic Workflow

Given an investing **theme**, the **date** it became hot, and a related **article URL**,
this workflow returns the most exposed US stocks and ETFs plus an SEO FAQ, combining
LLM semantic analysis (ChatGPT gateway) with live market data (AInvest OpenAPI quote).

## Input
```json
{"theme":"AI memory","date":"2026-07-09","url":"https://news.ainvest.com/deep-topic/topic/dt_01KX27F69TZTJ1QZV2RS9XX6M9"}
```

## Output
```json
{
  "ThemeStocks": [ {"market_code","ticker","name","intro","why_bullish","exposure_type","score","score_components","data_as_of"} , ... 8 ],
  "ThemeEtfs":   [ ... 5 ],
  "ThemeFAQ":    [ {"question","answer"}, ... 4-8 ]
}
```

## Pipeline
1. **Validate + fetch article** (`article.py`).
2. **Event brief** — ChatGPT builds a theme taxonomy: direct beneficiaries, picks-and-shovels, second-order, false positives (`prompts.EVENT_BRIEF_*`).
3. **Universes** — top stocks by market cap (`block_id C191`, `total_market_value`) and top ETFs by AUM (`prompt_id 67a9b535…`).
4. **Relevance scoring** — ChatGPT scores every candidate 1–5 by *real business/revenue exposure*, batched (`RELEVANCE_*`). The prompt enforces strict rules: no ticker/name keyword matching, direct beneficiaries must plausibly drive >5% incremental revenue within 12 months, unknown companies default to score 1 with low confidence.
5. **Market features** — daily K-lines (`multi_kline`, ≤16/call) give event→today change % and relative volume (latest vs median of prior `--baseline-lookback` pre-event days, requiring `--min-history` bars).
6. **Composite + selection** — `0.45·AI + 0.25·event-RVOL%ile + 0.20·abnormal-return%ile + 0.10·chg%ile`, data-quality penalty, pick top 8 / 5, then calibrate to **differentiated** 1–5 scores (never eight identical values). A candidate is volume-confirmed when peak event-window RVOL ≥ **`--rvol-threshold`×** its pre-event baseline; unconfirmed high-relevance candidates may fill slots when necessary.
7. **Narrative + FAQ** — ChatGPT writes intros, bullish rationale, and SEO FAQs grounded in the measured data.

## Scoring weights
`scoring.py`: `W_AI=0.55`, `W_RVOL=0.25`, `W_CHG=0.20`. Calibration spaces scores from 5.0 downward by rank-proportional gaps (min 0.1), staying within [1,5].

## Run
```bash
# full run (2000 stocks + 2000 ETFs) — the intended production scale
python cli.py '{"theme":"AI memory","date":"2026-07-09","url":"https://news.ainvest.com/..."}'

# bounded smoke test (caps both universes)
python cli.py --limit 120 --input input.json

# all knobs
#   universe/shortlist sizing
#     --stock-universe N      candidates pulled from C191 (default 2000)
#     --etf-universe N        candidates pulled from AUM pool (default 2000)
#     --relevance-batch N     LLM batch size for relevance scoring (default 60)
#     --stock-shortlist N     top-relevance stocks sent to market-feature stage (default 40)
#     --etf-shortlist N       top-relevance ETFs sent to market-feature stage (default 25)
#
#   volume-confirmation / RVOL
#     --rvol-threshold F      peak event-window RVOL required to be "volume-confirmed" (default 1.5)
#     --baseline-lookback N   pre-event trading bars used to compute median volume baseline (default 20)
#     --min-history N         minimum pre-event bars before baseline is trusted; fewer → quality penalty (default 5)
```
Only the final JSON goes to **stdout**; diagnostics go to **stderr**.

## Config
All endpoints/secrets come from `../Skills/env.json`:
- ChatGPT: `chatgpt_api.active_environment` (must be a reachable gateway, e.g. `internal_equ`).
- Quotes: `active_scene` (use `c` off-network) + cookie (`sessionid`/`userid`).
TLS verification is disabled for the quote/LLM hosts because the corporate proxy uses a self-signed chain.

## Notes / limits
- **ETF universe size matters.** Thematic sector ETFs (e.g. SMH/SOXX for semis) have lower AUM than broad-market funds; a small `--limit` can exclude them, leaving only diversified funds. Run the full 2000-ETF universe for thematic ETF coverage.
- Relevance ignores ticker/name coincidence and marks unknown names low-confidence.
- `event_date` is the supplied event date; `event_to_today_change_pct` anchors on the first trading bar on/after it (handles weekends/holidays). `score_components` now exposes peak event RVOL, volume confirmation, abnormal return vs SPY, and raw event-to-today change.

## Tests
```bash
python tests/test_workflow.py     # 8 unit + mocked end-to-end tests, no network
```

*Educational tooling, not investment advice.*
