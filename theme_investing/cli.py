"""CLI entrypoint for the theme-investing workflow.

Usage:
  python cli.py '{"theme":"AI memory","date":"2026-07-09","url":"https://..."}'
  echo '{...}' | python cli.py
  python cli.py --input input.json --limit 60   # small live smoke test

Only the final JSON result is printed to stdout; diagnostics go to stderr.
"""

from __future__ import annotations

import argparse
import json
import sys

from ainvest_client import AInvestClient
from config import load_llm_config, load_quote_config
from llm_client import LLMClient


def build_llm_client(cfg):
    if cfg.provider == "anthropic":
        from anthropic_local_client import LocalClaudeClient
        return LocalClaudeClient(cfg)
    return LLMClient(cfg)
from workflow import ThemeWorkflow


def _read_payload(args) -> dict:
    if args.payload:
        return json.loads(args.payload)
    if args.input:
        with open(args.input, encoding="utf-8") as fh:
            return json.load(fh)
    data = sys.stdin.read().strip()
    if not data:
        raise SystemExit("no input payload provided")
    return json.loads(data)


def main() -> int:
    ap = argparse.ArgumentParser(description="Theme-investing agentic workflow")
    ap.add_argument("payload", nargs="?", help="inline JSON payload")
    ap.add_argument("--input", help="path to JSON payload file")
    ap.add_argument("--limit", type=int, help="cap BOTH stock and ETF universe size (smoke tests)")
    ap.add_argument("--stock-universe", type=int)
    ap.add_argument("--etf-universe", type=int)
    ap.add_argument("--relevance-batch", type=int)
    ap.add_argument("--stock-shortlist", type=int)
    ap.add_argument("--etf-shortlist", type=int)
    ap.add_argument("--rvol-threshold", type=float,
                    help="event-window peak RVOL confirmation threshold (default 1.5)")
    args = ap.parse_args()

    payload = _read_payload(args)

    opts = {}
    if args.limit:
        opts["stock_universe"] = args.limit
        opts["etf_universe"] = args.limit
    for key in ("stock_universe", "etf_universe", "relevance_batch",
                "stock_shortlist", "etf_shortlist", "rvol_threshold"):
        val = getattr(args, key)
        if val is not None:
            opts[key] = val

    llm = build_llm_client(load_llm_config())
    quotes = AInvestClient(load_quote_config())
    wf = ThemeWorkflow(llm, quotes, opts)
    result = wf.run(payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
