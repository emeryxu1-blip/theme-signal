# AInvest Theme Investing CLI

This repository contains the theme-investing workflow and command-line interface.

## Requirements

- Python 3.10 or newer
- Access to the configured LLM and quote APIs
- The configuration file at `Skills/env.json`

The CLI uses the API keys and endpoints configured in `Skills/env.json`. Do not commit
new API keys or publish existing keys outside the intended repository.

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

3. Run a local screen using the top 100 stocks by market cap and at most 100
theme-derived ETF candidates:

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

The output contains `ThemeStocks`, `ThemeEtfs`, and `ThemeFAQ`.

### Choosing the limit

`--limit N` creates two independent finite boundaries. Stocks are screened from the
top N names by market cap. ETFs are generated from static thematic pools and funds that
hold the selected stocks, then deduplicated, filtered, and capped at N before the final
five are chosen. AUM is an investability tie-breaker, not the ETF universe.

```bash
# Top 100 stocks by market cap + at most 100 theme-derived ETF candidates
python3 cli.py --target local --input my_input.json --limit 100 \
  > result.json 2> run.log

# Top 1,000 stocks by market cap + at most 1,000 theme-derived ETF candidates
python3 cli.py --target local --input my_input.json --limit 1000 \
  > result.json 2> run.log
```

If `--limit` is omitted, the default is 500 stocks and 500 theme-derived ETF candidates.

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
    Screen at most the top N stocks by market cap and rank at most N
    theme-derived ETF candidates.
    The default is 500 per asset class.

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

- `ThemeStocks`
- `ThemeEtfs`
- `ThemeFAQ`

## Troubleshooting

### `401 Authentication Error`

Use `--target local` or `--target overseas` instead of manually setting an unrelated
Anthropic environment variable. The configured local client uses Bearer authentication
for the LiteLLM proxy and no longer requires `ANTHROPIC_BASE_URL`.

### `RemoteDisconnected` from the quote API

This usually means the selected quote endpoint is not reachable from the current network.
For local testing, use:

```bash
python3 cli.py --target local --input my_input.json --limit 1 > result.json
```

The overseas target requires access to the overseas production network.
