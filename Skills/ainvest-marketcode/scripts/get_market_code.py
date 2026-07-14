#!/usr/bin/env python3

import argparse
import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent
FINDER = ROOT / "find_market_code.py"


def main():
    parser = argparse.ArgumentParser(description="Return the best AInvest market_code only.")
    parser.add_argument("query", help="Ticker, code, or market:code")
    parser.add_argument("--market", help="Market or exchange hint such as nasdaq or binance")
    parser.add_argument("--asset", help="Asset hint such as stock, etf, spot, futures, perpetual, option")
    parser.add_argument("--no-refresh", action="store_true", help="Do not refresh the CSV when no match is found")
    args = parser.parse_args()

    command = [sys.executable, str(FINDER), args.query, "--best", "--json"]
    if args.market:
        command.extend(["--market", args.market])
    if args.asset:
        command.extend(["--asset", args.asset])
    if args.no_refresh:
        command.append("--no-refresh")

    result = subprocess.run(command, check=True, capture_output=True, text=True)
    rows = json.loads(result.stdout)
    if not rows:
        sys.exit(1)

    print(rows[0]["market_code"])


if __name__ == "__main__":
    main()
