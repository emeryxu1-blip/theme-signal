# AInvest Market Code Skill

Resolves user input (codes, tickers, English/Chinese security names, industry names) into AInvest `market_code`. Also supports bulk enumeration by asset class, exchange, industry level, and expanding industry index constituent stocks.

Examples:

- `AAPL` -> `185:AAPL`
- `Agilent Technologies` -> `169:A`
- `安捷伦科技` -> `169:A`
- `BTCUSDT.P --market bybit --asset perpetual` -> `U31F:BTCUSDT.P`
- `石油和天然气` -> `89:861105`

## Features

- Supports US stocks, ETFs, bonds, crypto spot/perpetual, and industry index code resolution.
- Supports English and Chinese security name matching to `market_code`.
- Supports English and Chinese industry name, alias, and keyword matching.
- Supports `--market` and `--asset` for disambiguating codes that appear in multiple markets.
- Supports returning a single best result, candidate lists, and metadata.
- Supports enumerating all codes by category (all ETFs, all ordinary stocks, all bonds, all crypto spot pairs, per-exchange futures/perpetuals).
- Supports enumerating level-1 through level-4 industry index codes.
- Supports expanding constituent stocks by industry index code or name.
- Supports returning code lists only (`--codes-only`) or counts only (`--count-only`).

## Match Priority

1. Explicit `market:code`, direct code, ticker, `ths_code`
2. English and Chinese security names
3. English and Chinese industry names, industry aliases, industry keywords

Notes:

- If the same text could match both a security name and an industry name, the security name takes priority by default.
- If the user provides an explicit industry code such as `89:861070`, it matches that industry code directly.

## Usage

Single best result:

```bash
./ainvest-marketcode/scripts/get_market_code.sh AAPL
./ainvest-marketcode/scripts/get_market_code.sh "Agilent Technologies"
./ainvest-marketcode/scripts/get_market_code.sh 安捷伦科技
./ainvest-marketcode/scripts/get_market_code.sh NVDA --market nasdaq
./ainvest-marketcode/scripts/get_market_code.sh BTCUSDT --market binance --asset spot
./ainvest-marketcode/scripts/get_market_code.sh BTCUSDT.P --market bybit --asset perpetual
./ainvest-marketcode/scripts/get_market_code.sh 石油和天然气
./ainvest-marketcode/scripts/get_market_code.sh 89:861070
```

Candidates and metadata:

```bash
python3 ainvest-marketcode/scripts/find_market_code.py Energy --limit 5 --json
python3 ainvest-marketcode/scripts/find_market_code.py 商业和专业服务 --best --json
python3 ainvest-marketcode/scripts/find_market_code.py 安捷伦科技 --json
```

Enumerate by category:

```bash
python3 ainvest-marketcode/scripts/find_market_code.py --list --asset etf --json
python3 ainvest-marketcode/scripts/find_market_code.py --list --asset stock --json
python3 ainvest-marketcode/scripts/find_market_code.py --list --asset bond --json
python3 ainvest-marketcode/scripts/find_market_code.py --list --asset spot --json
python3 ainvest-marketcode/scripts/find_market_code.py --list --market binance --asset futures --json
python3 ainvest-marketcode/scripts/find_market_code.py --list --market bybit --asset spot --limit 100 --offset 200 --json
python3 ainvest-marketcode/scripts/find_market_code.py --list --asset etf --codes-only
python3 ainvest-marketcode/scripts/find_market_code.py --industry-level 1 --codes-only
python3 ainvest-marketcode/scripts/find_market_code.py --industry-level 2 --json
python3 ainvest-marketcode/scripts/find_market_code.py 89:861105 --components --json
python3 ainvest-marketcode/scripts/find_market_code.py Energy --components --count-only
```

## Common Examples

### Security Code / Name

- `AAPL` -> `185:AAPL`
- `Agilent Technologies` -> `169:A`
- `安捷伦科技` -> `169:A`
- `QQQ --asset etf` -> `185:QQQ`
- `SPY --asset etf` -> `169:SPY`

### Crypto

- `BTCUSDT --market binance --asset spot` -> `UBAX:BTCUSDT`
- `BTCUSDT.P --market binance --asset perpetual` -> `UBAF:BTCUSDT.P`
- `BTCUSDT --market bybit --asset spot` -> `U31X:BTCUSDT`
- `BTCUSDT.P --market bybit --asset perpetual` -> `U31F:BTCUSDT.P`
- `BTCUSDT --market bitget --asset spot` -> `U32X:BTCUSDT`
- `BTCUSDT.P --market bitget --asset perpetual` -> `U32F:BTCUSDT.P`

### Industry Names

- `石油和天然气` -> `89:861105`
- `石油` -> `89:861105`
- `Oil & Gas Drilling` -> `89:861105`
- `银行` -> `89:861095`
- `89:861070` -> `89:861070`

### Bulk Enumeration

- `--list --asset etf` -> all ETF codes
- `--list --asset stock` -> all ordinary stock codes
- `--list --asset bond` -> all bond codes
- `--list --asset spot` -> all crypto spot codes
- `--list --market binance --asset futures` -> Binance futures/perpetual codes
- `--list --market bybit --asset spot` -> Bybit spot codes

### Industry Index and Constituents

- `--industry-level 1` -> all level-1 industry index codes
- `--industry-level 2` -> all level-2 industry index codes
- `--industry-level 3` -> all level-3 industry index codes
- `--industry-level 4` -> all level-4 industry index codes
- `89:861105 --components` -> constituent stocks of level-4 industry `Oil & Gas Drilling`
- `Energy --components` -> aggregated constituent stocks of level-1 industry `Energy`

## Parameters

- `--market` — specify exchange or venue, e.g. `nasdaq`, `nyse`, `binance`, `bybit`, `bitget`.
- `--asset` — specify asset type, e.g. `stock`, `etf`, `bond`, `spot`, `futures`, `perpetual`, `option`.
- `--best` — return only the best match.
- `--list` — enter enumeration mode; return all matching securities filtered by `--market`, `--asset`, optional query.
- `--industry-level` — return industry index codes for the specified level (1–4).
- `--components` — return constituent stocks for an industry index code or name. Level-4 returns direct constituents; higher levels return aggregated subtree constituents.
- `--codes-only` — output only `market_code` values, suitable for code-universe queries.
- `--count-only` — output only the count, suitable for "how many ETFs" style queries.
- `--limit` — max candidates in search mode; max output rows in enumeration mode.
- `--offset` — skip first N results in enumeration mode, for pagination.
- `--json` — output results as JSON.
- `--no-refresh` — do not attempt to refresh `security_config_V1.1.csv` on miss.

## Natural Language Mapping

- "all ETF codes" -> `--list --asset etf`
- "all ordinary stocks" -> `--list --asset stock`
- "all bond codes" -> `--list --asset bond`
- "all crypto spot" -> `--list --asset spot`
- "Binance futures" -> `--list --market binance --asset futures`
- "all level-1 industry index codes" -> `--industry-level 1`
- "all level-2 industry index codes" -> `--industry-level 2`
- "all level-3 industry index codes" -> `--industry-level 3`
- "all level-4 industry index codes" -> `--industry-level 4`
- "constituent stocks of a level-4 industry index" -> `<industry code or name> --components`
- If user wants only codes, no metadata: append `--codes-only`
- If user wants only counts: append `--count-only`

## Data Sources

- `references/security_config_V1.1.csv` — primary security code table.
- `references/ainvest_market_code_names_en.csv` — market_code to English security name.
- `references/ainvest_market_code_names_zh.csv` — market_code to Chinese security name.
- `references/gics_sector_tree.json` — raw industry tree data.
- `references/industry_aliases.json` — industry aliases and Chinese-to-English token replacement config.
- `references/industry_name_index.json` — generated industry search index.
- `references/examples.md` — quick examples.
- `references/scenarios.md` — common user phrasing to command mapping.

## Core Scripts

- `scripts/get_market_code.sh` — shell entry point, returns single best `market_code`.
- `scripts/get_market_code.py` — Python entry point, wraps best-result output.
- `scripts/find_market_code.py` — core matching logic with candidate lists, scoring, industry/name matching, CSV refresh, and enumeration by asset class/market, industry levels, and constituents.
- `scripts/build_industry_index.py` — rebuilds `industry_name_index.json` from industry tree and alias config.

## Maintenance

- After editing industry alias config, run:

```bash
python3 ainvest-marketcode/scripts/build_industry_index.py
```

- `security_config_V1.1.csv` is updated daily; `find_market_code.py` auto-refreshes once on miss unless `--no-refresh` is set.
- Do not fabricate a `market_code` when the reference data does not contain the requested item.

## Response Structure

`find_market_code.py --list --json` and `--components --json` share the same security result structure; the difference is `score=0` in enumeration mode (no ranking score). With `--codes-only`, only `market_code` strings are returned. With `--count-only`, only the count is returned.

Security results from `find_market_code.py --json` typically include:

- `market_code`, `market`, `code`, `security_type`, `listing_market`, `listing_market_name`, `ths_code`, `security_name`, `security_name_zh`, `score`

Industry results typically include:

- `market_code`, `market`, `code`, `name`, `category`, `level`, `path`, `score`

`--industry-level --json` returns industry index structures with the same fields as industry results.
