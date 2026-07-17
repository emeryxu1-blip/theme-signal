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
  "ThemeStocks": [
    {
      "market_code": "185:MU",
      "theme_rationale": {
        "type": "multilingual",
        "en": "English investor-facing theme rationale.",
        "zh": "面向投资者的中文主题逻辑说明。"
      },
      "Theme exposure": 4.8,
      "event_date": "2026-07-09"
    }
  ],
  "ThemeEtfs":   [ ... 5 ],
  "ThemeFAQ":    [ {"question","answer"}, ... 4-8 ]
}
```
`Theme exposure` is the only exposed score (1–5). Stock exposure combines the LLM
business-exposure signals with a small market-confirmation uplift. ETF exposure is calculated
deterministically from actual matched holding weights, breadth across selected theme stocks,
and curated-pool membership. Internal calculation fields are not included in `result.json`.
`theme_rationale` is a multilingual object containing semantically equivalent English (`en`) and
Simplified Chinese (`zh`) versions of the security's neutral, evidence-based theme connection,
financial pathway, supported catalyst, and main limitation. Neither version exposes internal
scores, rankings, price/volume calculations, or selection mechanics.

## Pipeline
1. **Validate + fetch article** (`article.py`).
2. **Event brief** — ChatGPT builds a theme taxonomy: direct beneficiaries, picks-and-shovels, second-order, false positives (`prompts.EVENT_BRIEF_*`).
3. **Finite stock screen** — stocks come from `C191` in descending market-cap order. ChatGPT evaluates business/revenue relevance in batches and stops at eight qualifying stocks or the top-N boundary.
4. **Deterministic ETF discovery** — static phrase routing selects up to four documented AInvest thematic pools. In parallel, each selected stock expands to the related ETFs that actually hold it. The sources are consumed round-robin and deduplicated until at most N unique candidates exist.
5. **Deterministic ETF filtering and ranking** — code rejects explicit leveraged, inverse, short, and single-stock wrapper products. For every remaining ETF, the quote API supplies exact weights for every selected stock. Ranking uses relevance-weighted holding exposure first, stock breadth second, curated-pool evidence third, and AUM only as the final investability tie-breaker. ETF candidates are not sent through the LLM relevance prompt.
6. **Market features (finalists only)** — daily K-lines are fetched **only for the qualifying finalists** to compute internal event-window RVOL, abnormal return vs SPY, and event→today change signals used by the small market-confirmation uplift. These values are not passed to the narrative prompt.
7. **Theme exposure + narrative + FAQ** — stock and ETF exposure scores are calibrated to differentiated 1–5 display values. ChatGPT writes semantically equivalent English and Simplified-Chinese `theme_rationale` text from business-exposure evidence and raw ETF holding facts; it does not receive internal scores, rankings, or market calculations.

## Scoring
For stocks, `Theme exposure` uses `ai_relevance × exposure_type × confidence`, with a
small volume/price-confirmation uplift. For ETFs, the preselection score uses 80% normalized
holdings-weighted exposure, 15% breadth, and 5% curated-pool evidence when stock holdings are
available. Non-equity asset pools use deterministic pool membership. AUM only breaks otherwise
similar results. Calibration spaces display scores within [1,5].

## Run
From the repository root, enter the workflow directory first:

```bash
cd /Users/zhiyangxu/Documents/Ainvest/theme_investing
```

The input needs three fields: `theme`, `date` (`YYYY-MM-DD`), and an article `url`.
Create or edit `my_input.json`:

```json
{"theme":"AI memory","date":"2026-07-09","url":"https://news.ainvest.com/deep-topic/topic/dt_01KX27F69TZTJ1QZV2RS9XX6M9"}
```

The workflow screens stocks **top-to-bottom by market cap** and stops once eight names clear
`--relevance-threshold` (default 2.5), or when the stock limit is reached. It then generates
and deterministically ranks at most the same number of theme-derived ETF candidates. The
default limit is 500; `--limit N` changes both finite boundaries.
Final JSON goes to **stdout**; progress goes to **stderr**.

The simplest default bounded run is:

```bash
python3 cli.py --target local --input my_input.json > result.json 2>run.log
python3 -m json.tool result.json
```

Use `tail -f run.log` in another terminal to monitor progress.

```bash
# 1. edit my_input.json with your theme / date / url, then run:
python3 cli.py --target local --input my_input.json > result.json 2>run.log

# 2. watch progress live in another terminal:
tail -f run.log
#   [theme-workflow] stocks: scanned 120 by market cap; 8/8 qualify (relevance >= 2.5)
#   [theme-workflow] selected 8 stocks and 5 ETFs

# inline payload instead of a file:
python3 cli.py --target local \
  '{"theme":"AI memory","date":"2026-07-09","url":"https://news.ainvest.com/..."}' \
  > result.json 2>run.log

# overseas production gateway instead of the local Claude/LiteLLM key:
python3 cli.py --target overseas --input my_input.json > result.json 2>run.log

# top 100 stocks by market cap + at most 100 theme-derived ETF candidates:
python3 cli.py --target local --input my_input.json --limit 100 > result.json 2>run.log

# top 1,000 stocks + at most 1,000 theme-derived ETF candidates:
python3 cli.py --target local --input my_input.json --limit 1000 > result.json 2>run.log
```

**Narrow themes scan deep and take a while.** Screening LLM-scores names in market-cap order
until the targets fill, so a theme with few large-cap beneficiaries can walk many hundreds of
names (minutes of LLM calls). For a long run, launch it in the background and tail the log:

```bash
nohup python3 cli.py --target local --input my_input.json > result.json 2>run.log &
tail -f run.log     # Ctrl-C to stop watching; the run keeps going
```

# all knobs
#   universe / scan
#     --limit N               top N stocks + at most N thematic ETFs (default: 500)
#     --stock-universe N      override the market-cap stock boundary
#     --etf-universe N        override the thematic ETF candidate boundary
#     --relevance-batch N     candidates per LLM relevance request during the scan (default 20)
#     --stock-target N        qualifying stocks to emit before stopping (default 8)
#     --etf-target N          qualifying ETFs to emit before stopping (default 5)
#     --relevance-threshold F minimum LLM relevance 1–5 (fractional) for output (default 2.5)
#
#   volume-confirmation / RVOL (finalist colour only)
#     --rvol-threshold F      peak event-window RVOL required to be "volume-confirmed" (default 1.5)
#     --baseline-lookback N   pre-event trading bars used to compute median volume baseline (default 20)
#     --min-history N         minimum pre-event bars before baseline is trusted (default 5)
```
Only the final JSON goes to **stdout**; diagnostics go to **stderr**.

## Config
All endpoints/secrets come from `../Skills/env.json`:
- ChatGPT: `chatgpt_api.active_environment` (must be a reachable gateway, e.g. `internal_equ`).
- Quotes: `active_scene` (use `c` off-network) + cookie (`sessionid`/`userid`).
TLS verification is disabled for the quote/LLM hosts because the corporate proxy uses a self-signed chain.

## Notes / limits
- **Finite boundaries.** Stocks use `C191` sorted by market cap. ETFs come only from finite curated/holding-relation sources and are globally capped after round-robin deduplication. The default boundary is 500 per class; `--limit N` changes both independently to N.
- **Deeper stock scans cost more.** A narrow theme can require more stock relevance batches. Raising the ETF limit increases quote-data work, not ETF LLM calls; ETF membership and ranking are deterministic.
- Relevance ignores ticker/name coincidence and marks unknown names low-confidence.
- `event_date` is the supplied event date; `event_to_today_change_pct` anchors on the first trading bar on/after it (handles weekends/holidays).

## Tests
```bash
python3 tests/test_workflow.py    # unit + mocked end-to-end tests, no network
```

*Educational tooling, not investment advice.*
