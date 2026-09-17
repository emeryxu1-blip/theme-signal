# ThemeSignal — Market Theme Signals

This repository contains the theme-investing workflow and command-line interface.

## Requirements

- Python 3.10 or newer
- Access to the configured LLM and quote APIs
- The configuration file at `Skills/env.json`

The CLI uses the API keys and endpoints configured in `Skills/env.json`. Do not commit
new API keys or publish existing keys outside the intended repository.
The default local target uses the official [DeepSeek API](https://api-docs.deepseek.com/)
with `deepseek-flash`. Set `DEEPSEEK_API_KEY` in the environment, or save the key in
`llm_profiles.local.api_key` in the gitignored `Skills/env.json`; the environment value
takes precedence. The example configuration retains the Office-WiFi gateway as the
optional `office_wifi` LLM profile.

### AInvest C-side manual session

Copy `Skills/env.example.json` to the gitignored `Skills/env.json` and set the
active local quote profile's `sessionid` and `userid` values from an authenticated browser
session. The application always uses these saved values and never performs an account login or
session refresh. Keep the values local and update them manually when the website session expires.
Do not put cookies or session IDs in command lines, tests, logs, or documentation.

For quote-request inspection, use `--dry-run`; it sends no network request. An explicit
`--auth-value` Cookie takes precedence over the saved session for that one invocation.

## Quick start

1. From the repository root, enter the workflow directory:

```bash
cd theme_investing
```

2. Create `my_input.json` in that directory. Replace the example values with the
theme, event date, and related article you want to analyze:

```json
{
  "theme": "AI memory",
  "date": "2026-07-09",
  "url": "https://example.com/article"
}
```

The date must use `YYYY-MM-DD`, and the URL must be an absolute `http://` or
`https://` URL.

3. Run a local screen that builds and scores broad-liquidity plus theme-industry
stock lanes from the top-100 liquid boundary, plus at most 100 theme-derived ETF candidates:

```bash
python3 cli.py --target local --input my_input.json --limit 100 \
  > result.json 2> run.log
```

4. Monitor progress from another terminal:

```bash
cd theme_investing
tail -f run.log
```

Press `Ctrl-C` to stop watching the log; this does not stop the workflow itself.

5. After the command finishes, validate and inspect the result:

```bash
python3 -m json.tool result.json
```

The output includes the LLM-translated `theme_cn` plus `ThemeStocks`, `ThemeEtfs`, and `ThemeFAQ`.

### Choosing the limit

`--limit N` creates two independent finite boundaries. Market cap defines the top-N
liquid-stock quote universe; a fixed 120-name semantic set combines 40 broad-liquidity names
with term-balanced theme-industry lanes, retaining several pure plays per explicit pathway and
sampling equal taxonomy matches independently of market-cap order. Causal relevance determines
ranking; progressively weaker relationship, ecosystem, sector, and broad-universe tiers backfill
the exact stock count, with deterministic issuer/code tie-breakers. ETF selection is stock-led for
every theme: the published stocks are probed for exact, direction-aligned >1× through 3×
single-stock products, and up to 30 causally qualified stocks provide evidence for conventional
1× equity baskets. Leveraged products are prioritized, not used as an exclusive ETF universe.
Curated pools and exact concept-index matches supplement discovery; American Consumer and
equivalent U.S.-consumer aliases search both Consumer Staples and Consumer Discretionary pools.
Mandate wording corroborates an equity basket but does not gate its holdings assessment.

Basket component scoring is theme-first: the exact theme and frozen profile are authoritative,
while article evidence is secondary and may be empty. Valid stock scores already produced in the
run, including negative scores, are reused; only unresolved holdings need another LLM request.
Requests that fail because of payload size or a retryable transport error recover in bounded
batches of at most five, ordered by portfolio-weight impact. Baskets still require at least 60%
reported weight. The normal semantic-coverage requirement is 80%; below it, a basket may qualify
only when a conservative lower bound that counts unresolved holdings as zero still clears both the
0.25 theme-score gate and the requirement for two relevant issuers. This partial-evidence mode is
marked in diagnostics and does not change the public JSON schema.

Conventional baskets can represent upside exposure in bullish runs or vulnerable-company downside
exposure in bearish runs. Before economic deduplication, ETFs with `output_eligible` true and
finite, authoritative `static_theme_exposure >= 0.375` receive preference, independently of
public display scores. Missing evidence or stale provisional scores cannot
establish that preference. These funds, including permitted economic overlaps, fill slots before
weaker candidates. Within each group, composition prioritizes the highest-ranked eligible
leveraged wrapper and reserves a conventional-basket slot when the target allows it, preserving
the existing ranking for remaining slots. A weaker wrapper or basket cannot displace a preferred
fund. Theme-adjacent, live `CE`, and offline `CE` fallback tiers still fill the exact target,
including five unique ETFs by default. Inverse, leveraged, option-income, hedged, and alternative
strategies may fill lower-ranked slots. Explicit ETNs and invalid or duplicate market codes remain
excluded. ETFs use the same public `Theme exposure` rank ladder as stocks, evenly distributed
from 5.0 to 3.0 in frozen selection order and rounded to one decimal. Five ETFs receive
`5.0, 4.5, 4.0, 3.5, 3.0`; a singleton receives 5.0. Internal evidence, ETF selection, stock
behavior, and the public JSON schema remain unchanged.

```bash
# Top 100 stocks by market cap + at most 100 theme-derived ETF candidates
python3 cli.py --target local --input my_input.json --limit 100 \
  > result.json 2> run.log

# Top 1,000 stocks by market cap + at most 500 ordinary ETF candidates;
# exact single-stock product probes are additive and cannot be evicted by this cap
python3 cli.py --target local --input my_input.json --limit 1000 \
  > result.json 2> run.log
```

If `--limit` is omitted, the CLI default is 2,000 stocks. Ordinary ETF discovery is hard-capped at
500 candidates; exact single-stock product probes remain additive. Use
`--etf-evidence-stock-limit` to change the internal basket-evidence stock limit from its default of
30. Defaults are exact: eight unique stocks and five unique ETFs; zero disables either class.

### Overseas production mode

Use the project LLM key with the overseas production gateway and production quote API:

```bash
python3 cli.py --target overseas --input my_input.json --limit 100 \
  > result.json 2> run.log
```

This mode requires network access to the configured overseas production endpoints.

## Input formats

Use the input file created in the quick start:

```bash
python3 cli.py --target local --input my_input.json --limit 100 \
  > result.json 2> run.log
```

Or provide the payload inline:

```bash
python3 cli.py --target local --limit 100 \
  '{"theme":"AI memory","date":"2026-07-09","url":"https://example.com/article"}' \
  > result.json 2> run.log
```

The CLI also accepts JSON from standard input:

```bash
cat my_input.json | python3 cli.py --target local --limit 100 \
  > result.json 2> run.log
```

## CLI options

```text
--target local|overseas
    Select the complete local or overseas API set.

--input PATH
    Read the JSON payload from PATH.

--limit N
    Fetch the top-N liquid-stock quote boundary and rank at most N
    theme-derived ETF candidates. Stock semantic scoring defaults to a
    120-name broad + theme-industry work set.
    The CLI default is 2,000 per asset class.

--stock-candidate-budget N
    Maximum stocks sent for semantic scoring (default 120).

--stock-broad-lane N
    Broad-liquidity slots inside the stock candidate budget (default 40).

--llm-profile PROFILE
    Advanced override for the LLM profile in Skills/env.json.

--llm-env ENVIRONMENT
    Advanced override for the selected gateway environment.

--quote-profile PROFILE
    Advanced override for the quote profile.
```

The convenience `--target` option is applied first. Explicit `--llm-profile`,
`--llm-env`, and `--quote-profile` options override the corresponding target setting.

## Output and diagnostics

The final workflow result is written to stdout, so redirect it to a JSON file:

```bash
python3 cli.py --target local --input my_input.json --limit 100 \
  > result.json 2> run.log
```

Progress diagnostics are written to `run.log`. Validate the generated result with:

```bash
python3 -m json.tool result.json
```

The result contains these top-level sections:

- `theme_cn` (the LLM's Simplified Chinese translation of the input `theme`)
- `ThemeStocks`
- `ThemeEtfs`
- `ThemeFAQ`

## Troubleshooting

### `401 Authentication Error`

For `--target local`, check the DeepSeek key in `DEEPSEEK_API_KEY` or
`llm_profiles.local.api_key` in `Skills/env.json`. This route uses Bearer authentication
against `https://api.deepseek.com/chat/completions` with `deepseek-flash`.

### `RemoteDisconnected` from the quote API

This usually means the selected quote endpoint is not reachable from the current network.
For local testing, use:

```bash
python3 cli.py --target local --input my_input.json --limit 1 > result.json
```

The overseas target requires access to the overseas production network.
