# Scenarios

Use this file when the user intent is already clear and you need the fastest mapping from natural language to command shape.

## Single code resolution

| User phrasing | Recommended command | Output shape |
| --- | --- | --- |
| AAPL 的 market_code | `./ainvest-marketcode/scripts/get_market_code.sh AAPL` | Single `market_code` |
| 安捷伦科技 的代码 | `./ainvest-marketcode/scripts/get_market_code.sh 安捷伦科技` | Single `market_code` |
| NVDA 在纳斯达克的代码 | `./ainvest-marketcode/scripts/get_market_code.sh NVDA --market nasdaq` | Single `market_code` |
| 石油和天然气行业代码 | `./ainvest-marketcode/scripts/get_market_code.sh 石油和天然气` | Single industry `market_code` |

## Security universe listing

| User phrasing | Recommended command | Output shape |
| --- | --- | --- |
| 所有 ETF 代码 | `python3 ainvest-marketcode/scripts/find_market_code.py --list --asset etf --codes-only` | Plain code list |
| 所有普通股票 | `python3 ainvest-marketcode/scripts/find_market_code.py --list --asset stock --codes-only` | Plain code list |
| 所有债券代码表 | `python3 ainvest-marketcode/scripts/find_market_code.py --list --asset bond --codes-only` | Plain code list |
| 所有数字货币现货 | `python3 ainvest-marketcode/scripts/find_market_code.py --list --asset spot --codes-only` | Plain code list |
| 币安的期货 | `python3 ainvest-marketcode/scripts/find_market_code.py --list --market binance --asset futures --codes-only` | Plain code list |
| Bybit 现货列表 | `python3 ainvest-marketcode/scripts/find_market_code.py --list --market bybit --asset spot --json` | Security rows |

## Industry index listing

| User phrasing | Recommended command | Output shape |
| --- | --- | --- |
| 所有一级行业指数代码 | `python3 ainvest-marketcode/scripts/find_market_code.py --industry-level 1 --codes-only` | Plain code list |
| 所有二级行业指数代码 | `python3 ainvest-marketcode/scripts/find_market_code.py --industry-level 2 --codes-only` | Plain code list |
| 所有三级行业指数代码 | `python3 ainvest-marketcode/scripts/find_market_code.py --industry-level 3 --codes-only` | Plain code list |
| 所有四级行业指数代码 | `python3 ainvest-marketcode/scripts/find_market_code.py --industry-level 4 --codes-only` | Plain code list |
| 二级行业指数有多少个 | `python3 ainvest-marketcode/scripts/find_market_code.py --industry-level 2 --count-only` | Count |
| 四级行业指数详情 | `python3 ainvest-marketcode/scripts/find_market_code.py --industry-level 4 --json` | Industry rows |

## Industry constituents

| User phrasing | Recommended command | Output shape |
| --- | --- | --- |
| 89:861105 的成分股 | `python3 ainvest-marketcode/scripts/find_market_code.py 89:861105 --components --codes-only` | Plain stock code list |
| Oil & Gas Drilling 的成分股 | `python3 ainvest-marketcode/scripts/find_market_code.py "Oil & Gas Drilling" --components --json` | Security rows |
| Energy 的成分股 | `python3 ainvest-marketcode/scripts/find_market_code.py Energy --components --codes-only` | Aggregated stock code list |
| Energy 有多少成分股 | `python3 ainvest-marketcode/scripts/find_market_code.py Energy --components --count-only` | Count |

## Output rules

- Default to `--codes-only` when the user asks for "代码", "code list", "代码表", or similar wording.
- Use `--json` when the user asks for names, metadata, or when downstream parsing is likely.
- Use `--count-only` when the user asks only for the amount.
- Prefer `<industry code> --components` over trying to infer constituents from security CSV data alone.
