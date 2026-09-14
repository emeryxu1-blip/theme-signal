# Theme Investing Agentic Workflow

Given an investing **theme**, the **date** it became hot, and a related **article URL**,
this workflow returns the most exposed US stocks and ETFs plus an SEO FAQ, combining
LLM semantic analysis (official DeepSeek Flash API by default) with live market data
(AInvest OpenAPI quote).

## Input
```json
{"theme":"AI memory","date":"2026-07-09","url":"https://news.ainvest.com/deep-topic/topic/dt_01KX27F69TZTJ1QZV2RS9XX6M9"}
```

## Output
```json
{
  "theme_cn": "AI 内存",
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
  "ThemeEtfs":   [ ... exactly 5 by default ],
  "ThemeFAQ":    [ {"question","answer"}, ... 4-8 ]
}
```
`ThemeStocks` and `ThemeEtfs` contain exactly their requested positive targets; defaults are eight
stocks and five ETFs. Zero disables that asset class. FAQ count remains a bounded range.
`theme_cn` is the theme-only LLM pass's faithful Simplified Chinese translation of the exact
input `theme`.
`Theme exposure` is the only exposed score (1–5). Public stocks receive a deterministic 5.0-to-3.0
attractiveness ladder in their frozen order; price and volume cannot change that order. Basket-ETF exposure is
calculated from independently scored full portfolios, relevant-issuer breadth, selected-stock
coverage, and a small canonical-mandate term. Baskets require at least 60% reported
holdings and normally at least 80% semantic coverage. Below 80%, a conservative partial-evidence
lower bound may qualify only if the known holdings still clear both the normal 0.25 theme-score
gate and the requirement for two relevant issuers after every unresolved holding is counted as
zero exposure.
Exact single-stock products use the verified underlying stock's causal-quality evidence, while
direct-asset funds use documented canonical pool/mandate facts. Internal direction,
price, volume, leverage, and calculation fields are not included in `result.json`.
`theme_rationale` is a multilingual object containing semantically equivalent English (`en`) and
Simplified Chinese (`zh`) versions. A stock rationale is one concise broker-style case: it first
introduces what the company sells or operates, then states the specific event/theme relationship,
and finally connects that relationship to orders, revenue, margins, or earnings. ETF rationales use
factual holdings or mandate evidence and disclose material product risks. Neither language exposes
internal scores, rankings, price/volume calculations, screening mechanics, or unsupported customer,
contract, catalyst, or financial-magnitude claims.

## Pipeline
1. **Validate + fetch article** (`article.py`).
2. **Frozen theme profile, then article context** — the first LLM pass receives only the exact
   theme label and defines its canonical scope, aliases, direct business models, pure-play
   descriptors, enablers, exclusions, and proposed core companies. A second pass analyzes the
   article for direction, catalyst, operating evidence, and security mentions without changing
   that frozen definition. Candidate discovery covers the title, lede, and article body, then adds
   an LLM event-ecosystem map of direct operators, suppliers, customers, partners, competitors,
   complementary businesses, and second-order beneficiaries. Every proposed company must resolve
   to an ordinary US stock and is hydrated with its company introduction, sector, and industry.
   Missing or invalid direction defaults to bullish.
3. **Finite, relationship-first stock screen** — the bounded semantic work set combines the
   resolved article and ecosystem companies with frozen-theme entities and theme-only
   industry/business matches. Candidates are issuer-deduplicated and ranked through descending
   evidence tiers: grounded event relationship, structural relationship, weaker directional
   relationship, article/ecosystem relevance, sector relevance, and finally the broad live/local
   investable universe. Strong evidence determines ranking rather than publication eligibility;
   share classes consume one issuer slot, and local `ES` rows backfill a short live universe.
4. **Complete and freeze both security lists** — the exact stock codes and order are frozen before
   stock-led ETF discovery begins. It retains up to 30
   issuer-deduplicated structural qualifiers as internal ETF evidence. Every theme runs
   stock-derived discovery, whether or not it matches the static catalog. The public stocks
   receive dedicated leveraged/long or inverse probes plus the generic relation; exact wrappers
   are verified from ETF security class, exact benchmark market code, structured direction, and
   leverage. Generic related ETFs are scanned for all evidence stocks, retaining the top 100 per
   stock before round-robin deduplication. Curated pools and exact concept-index matches supplement
   recall and canonical mandate evidence. The documented Consumer Staples pool participates in
   that recall, and exact `American Consumer`, `U.S. consumer`, and equivalent aliases route to
   both Consumer Staples and Consumer Discretionary discovery. Ordinary basket candidates have a
   hard 500-name cap; verified direct probes are additive. A live ETF-universe scan is used once
   to recover exact wrappers when stock-specific sources fail or return none, with the local CE
   list as the final ETF screening fallback. Eligible products rank first, followed by
   theme-adjacent discovered funds, broader live `CE` products, and the offline `CE` universe.
   ETF codes and order are then frozen, and both exact counts are asserted before narration.
5. **Evidence lanes and exact ETF composition** — direction-aligned >1× through 3× exact
   single-stock products can qualify without physical holdings. Conventional 1× long baskets can
   qualify in either direction: in bearish runs they represent downside sensitivity among
   vulnerable holdings and are narrated that way, not as bullish exposure. Up to 40 basket
   portfolios are assessed (initially 30 stock-evidence and 10 pool/concept-diversity candidates,
   with cross-lane backfill). Basket eligibility requires at least 60% reported weight, normally
   80% semantic scoring coverage, a 0.25 theme score, and two causally relevant issuers. Below 80%
   coverage, a basket qualifies only when its conservative lower bound—unresolved holdings counted
   as zero exposure—still clears the same score and issuer gates; this partial-evidence mode is
   explicitly logged. A free-form mandate mismatch cannot block holdings assessment; canonical
   mandate alignment is only a 5% corroborating signal. Direct-asset products still require
   explicit canonical mandate evidence. Strictly eligible products remain preferred. If needed for
   the exact target, lower tiers may include inverse, leveraged, option-income, buffered, hedged,
   long-short, alternative-strategy, or economically overlapping `CE` products. Explicit ETNs,
   invalid market codes, and duplicate codes remain excluded. Known daily-reset products are
   narrated with a concise compounding/path-dependence disclosure.
6. **Market features (finalists only)** — daily K-lines are fetched **only for the already-qualified finalists**. Signed change and abnormal return remain available internally. ETF labels may use a small, direction-neutral uplift based on a tie-aware percentile of `|Chg %|` plus RVOL confirmation; public-stock labels remain relationship-only. Equal positive and negative moves contribute equally, and price action never establishes thematic exposure. These values are not passed to the narrative prompt.
7. **Independent narration + FAQ** — only after both baskets are frozen does narration begin.
   Each missing, malformed, mismatched, unsafe, or duplicated row gets one same-code retry and then
   deterministic bilingual fallback prose; narration never substitutes a security, changes order,
   reduces counts, or fails the run. Strong stock cases follow business → event relationship →
   financial pathway. Weaker cases use business → core demand/scale/execution lever → financial
   pathway without exposing screening weakness. A run fails for counts only when the combined
   bounded live and offline universe lacks enough valid unique securities.

## Scoring
For public stocks, relationship ranking covers direct exposure, suppliers, customers, partners,
competitors, complementary businesses, and defensible second-order beneficiaries. Directional
consistency, evidence strength, confidence, specificity, materiality, and a concrete financial
pathway rank ahead of generic factor and broad-universe fallbacks. Public `Theme exposure` is the
deterministic 5.0-to-3.0 rank ladder; price and volume remain private diagnostics. ETF
discovery continues to use the stricter structural theme score privately, so a broker-relevant
indirect public stock does not automatically become ETF holdings evidence. Basket component scoring
treats the exact theme and frozen theme profile as authoritative; article context is secondary and
may be empty. Every valid stock
score already produced in the run is reused, including negative/ineligible evidence, and only
unknown holdings are sent for component scoring. Payload-related HTTP 400/413/422 responses and
retryable transport failures recover through bounded groups of at most five holdings, prioritized
by portfolio-weight impact; authentication and configuration failures remain fail-closed. Basket ETF theme
evidence is `70% × full-portfolio thematic mass + 15% × relevant-issuer breadth + 10% ×
quality-weighted selected-stock coverage + 5% × canonical mandate corroboration`. Exact direct
products use their underlying selected stock's causal quality; direct assets retain explicit
canonical mandate/pool evidence. Investability is `50% × turnover percentile + 35% × AUM
percentile + 15% × inverse expense-ratio percentile`, with missing metrics scoring zero. Final ETF
rank is `80% × theme evidence + 10% × investability + 10% × exact-selected-stock bonus`; the
post-deduplication composition rule then makes leverage first and reserves a basket slot when
eligible and the target is at least two. Leverage does not increase thematic evidence. AUM below
$25 million or current turnover below $1 million is a soft warning and ranking disadvantage, not
an exclusion. Public `Theme exposure` remains based on thematic evidence rather than investability
or leverage, and the `ThemeEtfs` JSON shape is unchanged.

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

The workflow fetches the configured top-N liquid stock boundary and builds a bounded semantic work
set from resolved title, lede, article-body, frozen-theme, and event-ecosystem entities plus
term-balanced industry/business lanes. It applies the grounded relationship gate
(`--relevance-threshold` defaults to 3.3), then ranks and issuer-deduplicates the survivors and
walks reserves until the exact stock target is valid. It does not stop when early mega-caps fill the
target. ETF discovery remains finite, but progressively broader `CE` fallback tiers fill the exact
ETF target. The CLI default limit is 2,000 for the cheap stock boundary; the ordinary ETF candidate set
never exceeds 500, while exact wrappers are additive.
Final JSON goes to **stdout**; progress goes to **stderr**.

The simplest default run is:

```bash
./run_theme.sh
```

`run_theme.sh` automatically loads `.env.upload`, runs the official DeepSeek `deepseek-flash` workflow with
`my_input.json` and the CLI's default `--limit 2000`, streams progress to the terminal and
`run.log`, and stores the successful public result in Cloudflare. The runner first captures output
in a temporary file and atomically replaces `result.json` only after the complete run succeeds. A
genuine live-plus-offline universe-exhaustion failure exits nonzero, uploads nothing, and preserves
the previous `result.json`; narration outages still produce a complete result.
Arguments are forwarded to the CLI, so a smaller local-only run is:

```bash
./run_theme.sh --limit 200 --no-upload
```

Narration retries and deterministic fallback use are recorded by market code in `run.log`.
Universe-exhaustion diagnostics report the asset class, requested count, available unique count,
and exhausted sources; stdout contains no partial JSON.

`--limit` reduces the quote-universe boundaries, but it does not reduce the default
120-stock semantic budget or the 40 full ETF portfolio assessments. For a quick
pipeline smoke test, lower those expensive budgets explicitly; setting the ETF
portfolio budget to zero keeps exact direct products but makes ordinary baskets fail closed:

```bash
./run_theme.sh --limit 200 \
  --stock-candidate-budget 30 \
  --stock-broad-lane 5 \
  --etf-evidence-stock-limit 8 \
  --etf-holdings-portfolio-budget 0 \
  --no-upload
```

Inspect the generated result with:

```bash
python3 -m json.tool result.json
```

The equivalent explicit command is:

```bash
set -a
source .env.upload
set +a
python3 cli.py --target local --input my_input.json --limit 2000 > result.json 2>run.log
```

Direct shell redirection can truncate its destination before the CLI starts. Use `run_theme.sh`
when the previous `result.json` must be preserved on failure.

The CLI also defaults `--input` to `my_input.json` and `--limit` to `2000`, so after
loading `.env.upload` this shorter command has the same behavior:

```bash
python3 cli.py > result.json 2>run.log
```

Use `--no-upload` when an intentional local-only run is needed.

```bash
python3 cli.py --no-upload > result.json 2>run.log
```

*Note: the default runner selects the local official DeepSeek `deepseek-flash` profile.
`--target` remains available for explicit local/overseas selection.*

## Config

Configure endpoints and profile secrets in the gitignored `../Skills/env.json`.
`DEEPSEEK_API_KEY` overrides the local DeepSeek profile's `api_key` when set.
Use `tail -f run.log` in another terminal to monitor progress.

```bash
# 1. edit my_input.json with your theme / date / url, then run:
python3 cli.py --target local --input my_input.json > result.json 2>run.log

# 2. watch progress live in another terminal:
tail -f run.log
#   [theme-workflow] stocks: scan cap 2000 reached; 31 qualify, emitting 8/8 after semantic ranking and issuer dedupe
#   [theme-workflow] selected 8 stocks and 3 ETFs

# inline payload instead of a file:
python3 cli.py --target local \
  '{"theme":"AI memory","date":"2026-07-09","url":"https://news.ainvest.com/..."}' \
  > result.json 2>run.log

# use the overseas production gateway:
python3 cli.py --target overseas --input my_input.json > result.json 2>run.log

# optional Office-WiFi gateway profile from env.example.json:
python3 cli.py --llm-profile office_wifi --input my_input.json > result.json 2>run.log

# top 100 stocks by market cap + at most 100 theme-derived ETF candidates:
python3 cli.py --target local --input my_input.json --limit 100 > result.json 2>run.log

# top 1,000 stocks + at most 500 ordinary theme-derived ETF candidates:
python3 cli.py --target local --input my_input.json --limit 1000 > result.json 2>run.log
```

**Semantic scoring is explicitly bounded.** Raising the liquid boundary increases quote-data work,
while the default LLM stock set remains 120 names. For a long run, launch it in the background and
tail the log:

```bash
nohup python3 cli.py --target local --input my_input.json > result.json 2>run.log &
tail -f run.log     # Ctrl-C to stop watching; the run keeps going
```

# all knobs
#   universe / scan
#     --limit N               top-N stock boundary; ETF request remains hard-capped at 500
#     --stock-universe N      override the market-cap stock boundary
#     --stock-candidate-budget N maximum stocks sent for semantic scoring (default 120)
#     --stock-broad-lane N    broad-liquidity slots inside that budget (default 20)
#     --etf-evidence-stock-limit N qualified internal ETF anchors (default 30)
#     --etf-universe N        lower the ordinary ETF boundary (hard maximum 500)
#     --relevance-batch N     candidates per LLM relevance request during the scan (default 10)
#     --stock-target N        exact stock count; 0 disables stocks (default 8)
#     --etf-target N          exact ETF count; 0 disables ETFs (default 5)
#     --relevance-threshold F minimum LLM relevance 1–5 before evidence gates (default 3.3)
#     --min-relevance-confidence F minimum confidence before stock eligibility (default 0.55)
#     --etf-min-theme-score F absolute full-portfolio ETF threshold (default 0.25)
#     --etf-holdings-portfolio-budget N maximum full equity-ETF assessments (default 40)
#
#   volume-confirmation / RVOL (finalist colour only)
#     --rvol-threshold F      peak event-window RVOL required to be "volume-confirmed" (default 1.5)
#     --baseline-lookback N   pre-event trading bars used to compute median volume baseline (default 20)
#     --min-history N         minimum pre-event bars before baseline is trusted (default 5)
```
Only the final JSON goes to **stdout**; diagnostics go to **stderr**.

## ThemeSignal archive upload

When both environment variables are set, each successful run uploads the exact compact JSON bytes written to stdout:

```bash
export THEME_SIGNAL_UPLOAD_URL="https://theme-signal-archive.workers.dev/api/themes"
export THEME_SIGNAL_UPLOAD_TOKEN="your-worker-secret"
python3 cli.py --target local --input my_input.json > result.json 2>run.log
```

Use `--no-upload` for an intentional offline run. Uploads use the content SHA-256 as an idempotency key and retry transient timeouts or gateway failures up to three times. A changed result with the same normalized theme name replaces the existing archive entry while retaining its URL. An exhausted upload failure leaves stdout/result JSON unchanged and returns exit code 3. Never commit the token.

Retry a completed `result.json` without rerunning any LLM or quote stages:

```bash
./upload_result.sh result.json
```

## Config
Configure endpoints and profile secrets in the gitignored `../Skills/env.json`:
- LLM: `active_profiles.llm` selects a profile; the default `llm_profiles.local` route uses
  the official DeepSeek API at `https://api.deepseek.com/chat/completions` and
  `deepseek-flash`, with thinking enabled, low reasoning effort, an 8,000-token completion
  budget, and a 600-second timeout. `DEEPSEEK_API_KEY` overrides the profile's `api_key`.
  Use `llm_profiles.office_wifi` for the optional Office-WiFi gateway, or `--target overseas`
  for the production LLM and quote profiles.
- Quotes: `active_scene` (use `c` off-network) + manually maintained cookie values
  (`sessionid`/`userid`). The application never logs in or refreshes these values.
TLS verification is enabled by default. When no `ca_file` is configured, the client supplements
Python's default trust store with the installed `certifi` bundle; configure `ca_file` when a
corporate CA is required.

The DeepSeek client uses `system` messages and sends the configured completion budget as
`max_tokens`. Structured workflow requests use JSON object mode with the expected schema
included in the prompt; existing workflow result validation checks identities, required fields,
and accepted values. See the official [API reference](https://api-docs.deepseek.com/api/create-chat-completion)
and [JSON Output guide](https://api-docs.deepseek.com/guides/json_mode/).

## Notes / limits
- **Finite boundaries.** Stocks use `C191` market cap as the normal quote/liquidity boundary, not a first-passing membership rule. Validated theme and title/lede anchors may sit outside it only after batched live-profile confirmation. Entity, broad, and term-balanced theme-only GICS/business lanes form the fixed semantic set. Ordinary ETFs come from bounded stock-related, curated-pool, and exact concept-index sources and are hard-capped at 500 after round-robin deduplication. Verified direct-product probes remain outside that cap.
- **Bounded semantic work.** Stock relevance defaults to 120 candidates regardless of a larger quote boundary. Full-portfolio work remains capped at 40 ETFs and 5,000 unique companies.
- Relevance ignores ticker/name coincidence and marks unknown names low-confidence.
- `event_date` is the supplied event date; `event_to_today_change_pct` anchors on the first trading bar on/after it (handles weekends/holidays).

## Tests
```bash
python3 tests/test_workflow.py    # unit + mocked end-to-end tests, no network
python3 tests/test_marketcode_resolver.py  # local resolver safety + CRWV/NBIS checks
```

*Educational tooling, not investment advice.*
