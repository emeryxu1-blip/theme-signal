# Examples

Use this file when an agent needs quick lookup patterns without reading the full CSV.

For a condensed "user phrasing -> command" table, see `references/scenarios.md`.

## Common direct lookups

| User intent | Suggested command | Expected result |
| --- | --- | --- |
| AAPL market code | `./ainvest-marketcode/scripts/get_market_code.sh AAPL` | `185:AAPL` |
| Agilent Technologies market code | `./ainvest-marketcode/scripts/get_market_code.sh "Agilent Technologies"` | `169:A` |
| 安捷伦科技 market code | `./ainvest-marketcode/scripts/get_market_code.sh 安捷伦科技` | `169:A` |
| NVDA on Nasdaq | `./ainvest-marketcode/scripts/get_market_code.sh NVDA --market nasdaq` | `185:NVDA` |
| QQQ ETF | `./ainvest-marketcode/scripts/get_market_code.sh QQQ --asset etf` | `185:QQQ` |
| SPY ETF | `./ainvest-marketcode/scripts/get_market_code.sh SPY --asset etf` | `169:SPY` |

## Crypto venue disambiguation

| User intent | Suggested command | Expected result |
| --- | --- | --- |
| Binance BTCUSDT spot | `./ainvest-marketcode/scripts/get_market_code.sh BTCUSDT --market binance --asset spot` | `UBAX:BTCUSDT` |
| Binance BTCUSDT perpetual | `./ainvest-marketcode/scripts/get_market_code.sh BTCUSDT.P --market binance --asset perpetual` | `UBAF:BTCUSDT.P` |
| Bybit BTCUSDT spot | `./ainvest-marketcode/scripts/get_market_code.sh BTCUSDT --market bybit --asset spot` | `U31X:BTCUSDT` |
| Bybit BTCUSDT perpetual | `./ainvest-marketcode/scripts/get_market_code.sh BTCUSDT.P --market bybit --asset perpetual` | `U31F:BTCUSDT.P` |
| Bitget BTCUSDT spot | `./ainvest-marketcode/scripts/get_market_code.sh BTCUSDT --market bitget --asset spot` | `U32X:BTCUSDT` |
| Bitget BTCUSDT perpetual | `./ainvest-marketcode/scripts/get_market_code.sh BTCUSDT.P --market bitget --asset perpetual` | `U32F:BTCUSDT.P` |

## Industry lookup

| User intent | Suggested command | Expected result |
| --- | --- | --- |
| 石油和天然气行业 | `./ainvest-marketcode/scripts/get_market_code.sh 石油和天然气` | `89:861105` |
| 石油四级行业指数 | `./ainvest-marketcode/scripts/get_market_code.sh 石油` | `89:861105` |
| Oil & Gas Drilling | `./ainvest-marketcode/scripts/get_market_code.sh "Oil & Gas Drilling"` | `89:861105` |
| Energy sector | `./ainvest-marketcode/scripts/get_market_code.sh Energy` | `89:861070` |

## Bulk listing

| User intent | Suggested command | Expected result |
| --- | --- | --- |
| All ETF codes | `python3 ainvest-marketcode/scripts/find_market_code.py --list --asset etf --json` | ETF rows with `security_type=CE` |
| All ETF market_code values only | `python3 ainvest-marketcode/scripts/find_market_code.py --list --asset etf --codes-only` | Plain `market_code` lines only |
| All ordinary stock codes | `python3 ainvest-marketcode/scripts/find_market_code.py --list --asset stock --json` | Ordinary share rows with `security_type=ES` |
| All bond codes | `python3 ainvest-marketcode/scripts/find_market_code.py --list --asset bond --codes-only` | Bond `market_code` lines only |
| All crypto spot pairs | `python3 ainvest-marketcode/scripts/find_market_code.py --list --asset spot --json` | Spot rows from `UBAX` / `U31X` / `U32X` |
| Binance futures universe | `python3 ainvest-marketcode/scripts/find_market_code.py --list --market binance --asset futures --json` | Binance derivative rows from `UBAF` |
| Bybit spot page 3 | `python3 ainvest-marketcode/scripts/find_market_code.py --list --market bybit --asset spot --limit 100 --offset 200 --json` | Bybit spot rows 201-300 |

## Industry listing and constituents

| User intent | Suggested command | Expected result |
| --- | --- | --- |
| All level-1 industry indexes | `python3 ainvest-marketcode/scripts/find_market_code.py --industry-level 1 --codes-only` | 11 first-level industry index codes |
| All level-2 industry indexes | `python3 ainvest-marketcode/scripts/find_market_code.py --industry-level 2 --count-only` | Count of second-level industry indexes |
| All level-4 industry indexes | `python3 ainvest-marketcode/scripts/find_market_code.py --industry-level 4 --json` | Fourth-level industry index rows |
| Components of Oil & Gas Drilling | `python3 ainvest-marketcode/scripts/find_market_code.py 89:861105 --components --json` | Stock rows inside the level-4 industry |
| Components of Energy sector | `python3 ainvest-marketcode/scripts/find_market_code.py Energy --components --codes-only` | Aggregated component stock codes under Energy |

## Interpretation rules

- `--market` should reflect the exchange or venue named by the user.
- `--asset` is important for duplicated crypto symbols such as spot vs perpetual.
- For US equities and ETFs, if the user names Nasdaq, prefer `185` first and `186` second.
- For NYSE or Arca, prefer `169`.
- For CBOE or BATS, prefer `171`.

## Natural language to command

| User phrasing | Recommended command shape |
| --- | --- |
| 所有 ETF 代码 | `python3 ainvest-marketcode/scripts/find_market_code.py --list --asset etf --codes-only` |
| 所有普通股票 | `python3 ainvest-marketcode/scripts/find_market_code.py --list --asset stock --codes-only` |
| 所有债券代码表 | `python3 ainvest-marketcode/scripts/find_market_code.py --list --asset bond --codes-only` |
| 所有数字货币现货 | `python3 ainvest-marketcode/scripts/find_market_code.py --list --asset spot --codes-only` |
| 币安的期货 | `python3 ainvest-marketcode/scripts/find_market_code.py --list --market binance --asset futures --codes-only` |
| 所有一级行业指数代码 | `python3 ainvest-marketcode/scripts/find_market_code.py --industry-level 1 --codes-only` |
| 所有二级行业指数代码 | `python3 ainvest-marketcode/scripts/find_market_code.py --industry-level 2 --codes-only` |
| 所有三级行业指数代码 | `python3 ainvest-marketcode/scripts/find_market_code.py --industry-level 3 --codes-only` |
| 所有四级行业指数代码 | `python3 ainvest-marketcode/scripts/find_market_code.py --industry-level 4 --codes-only` |
| 某个四级行业指数代码的成分股 | `python3 ainvest-marketcode/scripts/find_market_code.py <industry code or name> --components --codes-only` |
| 有多少个二级行业指数 | `python3 ainvest-marketcode/scripts/find_market_code.py --industry-level 2 --count-only` |
| Energy 有多少成分股 | `python3 ainvest-marketcode/scripts/find_market_code.py Energy --components --count-only` |

## Fallback behavior

- If the user gives only a ticker, return the best exact code match.
- If the user gives an English or Chinese security name, match against `ainvest_market_code_names_en.csv` and `ainvest_market_code_names_zh.csv`.
- If the symbol appears in multiple markets, add `--market`.
- If the user asks for "all ETFs", "all ordinary stocks", "all spot pairs", or "Binance futures", switch to `--list` mode instead of single-result lookup.
- If the user asks for all bonds, use `--list --asset bond`.
- If the user asks for all first/second/third/fourth-level industry index codes, use `--industry-level 1|2|3|4`.
- If the user asks for the component stocks of an industry index, use `<industry code or name> --components`.
- If the user explicitly asks only for the codes and not the metadata, add `--codes-only`.
- If the user asks only for the amount, add `--count-only`.
- If the same text can mean both a security and an industry, prefer the security name unless the user provides an explicit industry code such as `89:861070`.
- If the query is an industry name and no security name matches first, the skill will search `gics_sector_tree.json`.
- If the user needs metadata or multiple candidates, switch to `scripts/find_market_code.py --best --json` or increase `--limit`.
