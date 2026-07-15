# AInvest Theme Investing CLI

This repository contains the theme-investing workflow and command-line interface.

## Requirements

- Python 3.10 or newer
- Access to the configured LLM and quote APIs
- The configuration file at `Skills/env.json`

The CLI uses the API keys and endpoints configured in `Skills/env.json`. Do not commit
new API keys or publish existing keys outside the intended repository.

## Run the workflow

Change into the project directory:

```bash
cd theme_investing
```

### Local mode

Use the personal LLM key with the local LiteLLM proxy and the public quote API:

```bash
python3 cli.py --target local --input my_input.json --limit 150 > result.json
```

This is the recommended command for local/off-network testing. The `--limit` option
caps both the stock and ETF universes; lower values such as `--limit 1` or `--limit 10`
are useful for quick smoke tests.

### Overseas production mode

Use the project LLM key with the overseas production gateway and production quote API:

```bash
python3 cli.py --target overseas --input my_input.json --limit 150 > result.json
```

This mode requires network access to the configured overseas production endpoints.

## Input formats

Use the checked-in sample input:

```bash
python3 cli.py --target local --input my_input.json --limit 150 > result.json
```

Or provide the payload inline:

```bash
python3 cli.py --target local '{"theme":"AI memory","date":"2026-07-09","url":"https://example.com/article"}' > result.json
```

The CLI also accepts JSON from standard input:

```bash
cat my_input.json | python3 cli.py --target local --limit 150 > result.json
```

## CLI options

```text
--target local|overseas
    Select the complete local or overseas API set.

--input PATH
    Read the JSON payload from PATH.

--limit N
    Cap both stock and ETF universe sizes.

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
python3 cli.py --target local --input my_input.json --limit 150 > result.json
```

Progress diagnostics are written to stderr and remain visible in the terminal. Validate
the generated result with:

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
