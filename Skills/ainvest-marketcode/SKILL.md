---
name: ainvest-marketcode
description: Resolve or enumerate AInvest market_code from a ticker, code, security name, market hint, industry name, or asset class filter. Use this when a user or downstream tool needs a single `market:code`, a filtered security universe such as all ETFs, all ordinary stocks, all bonds, all crypto spot pairs, or Binance derivatives, or industry index code sets and constituent stocks.
---

# AInvest Market Code

Use this skill to convert a security symbol, English/Chinese security name, or industry name into AInvest `market_code`, or to enumerate all codes in a requested asset/market bucket.

## Use

Run `scripts/get_market_code.sh` or `scripts/get_market_code.py` for the single best result.

```bash
./ainvest-marketcode/scripts/get_market_code.sh AAPL
./ainvest-marketcode/scripts/get_market_code.sh "Agilent Technologies"
./ainvest-marketcode/scripts/get_market_code.sh 安捷伦科技
./ainvest-marketcode/scripts/get_market_code.sh NVDA --market nasdaq
./ainvest-marketcode/scripts/get_market_code.sh BTCUSDT --market binance --asset spot
./ainvest-marketcode/scripts/get_market_code.sh BTCUSDT.P --market bybit --asset perpetual
./ainvest-marketcode/scripts/get_market_code.sh 石油和天然气
```

Use `scripts/find_market_code.py` when you need ranked candidates, metadata, or category-wide listing.

```bash
python3 ainvest-marketcode/scripts/find_market_code.py 商业和专业服务 --best --json
python3 ainvest-marketcode/scripts/find_market_code.py --list --asset etf --json
python3 ainvest-marketcode/scripts/find_market_code.py --list --asset stock --json
python3 ainvest-marketcode/scripts/find_market_code.py --list --asset spot --json
python3 ainvest-marketcode/scripts/find_market_code.py --list --market binance --asset futures --json
python3 ainvest-marketcode/scripts/find_market_code.py --list --asset etf --codes-only
python3 ainvest-marketcode/scripts/find_market_code.py --list --asset bond --codes-only
python3 ainvest-marketcode/scripts/find_market_code.py --industry-level 1 --codes-only
python3 ainvest-marketcode/scripts/find_market_code.py --industry-level 4 --codes-only
python3 ainvest-marketcode/scripts/find_market_code.py 89:861105 --components --codes-only
python3 ainvest-marketcode/scripts/find_market_code.py --industry-level 2 --count-only
```

## Data

- `references/security_config_V1.1.csv`: security market_code table
- `references/ainvest_market_code_names_en.csv`: market_code to English name
- `references/ainvest_market_code_names_zh.csv`: market_code to Chinese name
- `references/gics_sector_tree.json`: raw 同花顺行业树
- `references/industry_aliases.json`: industry aliases
- `references/industry_name_index.json`: generated 1-4 level industry index
- `references/examples.md`: quick examples
- `references/scenarios.md`: natural language to command mapping for common requests

## Behavior

- Security lookup uses exact symbol match first.
- If no exact symbol match is found, try English/Chinese security name matching first.
- If security name lookup does not match, try industry lookup and aliases.
- `--list` switches the tool into enumeration mode and returns every matching security row instead of a ranked lookup result.
- In enumeration mode, use `--asset etf` for all ETFs, `--asset stock` for ordinary stocks, `--asset spot` for crypto spot pairs, and `--market <venue> --asset futures` for venue-scoped derivatives such as Binance futures.
- Use `--list --asset bond` for all bond codes.
- Use `--industry-level 1|2|3|4` to enumerate first-level through fourth-level GICS industry index codes.
- Use `<industry code or name> --components` to enumerate the component securities under an industry index. Fourth-level indices return their direct stock constituents; higher levels return the aggregated descendants.
- Enumeration mode also supports `--offset` and optional `--limit` for pagination or chunked export.
- `--codes-only` returns only the final `market_code` values, which is the preferred output when the user explicitly asks for a code universe rather than metadata.
- `--count-only` returns only the number of matches, which is useful for "how many ETFs" or "how many level-2 industries" style questions.
- Industry lookup supports English names, Chinese aliases, and keyword matching.
- Explicit `market:code` or direct code matches still win before name matching, including industry codes such as `89:861070`.
- If multiple industry levels share the same name, prefer the higher level first.
- If no security match is found, refresh `security_config_V1.1.csv` once and retry unless `--no-refresh` is set.

## Natural Language Mapping

See `references/scenarios.md` for the full user-phrasing to command table.

- "all ETFs", "全部 ETF", "ETF code table" -> `--list --asset etf`
- "all ordinary stocks", "全部普通股", "all stocks" -> `--list --asset stock`
- "all bonds", "全部债券" -> `--list --asset bond`
- "all crypto spot pairs", "全部数字货币现货", "全部币圈现货" -> `--list --asset spot`
- "Binance futures", "币安期货", "Binance perpetuals" -> `--list --market binance --asset futures`
- "Bybit spot", "Bybit 现货" -> `--list --market bybit --asset spot`
- "all level-1 industries", "所有一级行业指数" -> `--industry-level 1`
- "all level-2 industries", "所有二级行业指数" -> `--industry-level 2`
- "all level-3 industries", "所有三级行业指数" -> `--industry-level 3`
- "all level-4 industries", "所有四级行业指数" -> `--industry-level 4`
- "components of 89:861105", "89:861105 的成分股", "Oil & Gas Drilling components" -> `<query> --components`
- If the user explicitly asks only for codes, append `--codes-only`.
- If the user explicitly asks only for counts, append `--count-only`.

## Maintenance

- After editing `references/industry_aliases.json`, rebuild the industry index with `python3 ainvest-marketcode/scripts/build_industry_index.py`.
- Do not invent a `market_code` when the references do not support the requested symbol.
