"""CLI entrypoint for the theme-investing workflow.

Usage:
  python cli.py '{"theme":"AI memory","date":"2026-07-09","url":"https://..."}'
  echo '{...}' | python cli.py
  python cli.py --input input.json --limit 60   # small live smoke test

  # pick the LLM API for THIS run without editing env.json:
  python cli.py --target local --limit 120 '{...}'               # personal key -> Claude/LiteLLM proxy
  python cli.py --target overseas '{...}'                        # project key -> overseas production gateway

  # advanced: pick the raw profile/environment (override --target):
  python cli.py --llm-profile local --limit 120 '{...}'          # Claude, works off-network
  python cli.py --llm-profile production '{...}'                  # gateway; default env internal_equ (overseas prod)
  # choose a specific gateway environment (internal_equ / overseas_prod / wuchang_prod = production;
  # office_wifi / test = office network incl. test):
  python cli.py --llm-profile production --llm-env internal_equ '{...}'

Only the final JSON result is printed to stdout; diagnostics go to stderr.
"""

from __future__ import annotations

import argparse
import json
import sys

from ainvest_client import AInvestClient
import config
from config import load_llm_config, load_quote_config, load_env
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
    ap.add_argument("--baseline-lookback", type=int,
                    help="number of pre-event trading bars used to compute the median "
                         "volume baseline for RVOL (default 20)")
    ap.add_argument("--min-history", type=int,
                    help="minimum pre-event bars required before the volume baseline is "
                         "trusted; candidates below this are penalised (default 5)")
    ap.add_argument("--target", choices=["local", "overseas"],
                    help="pick the API set for this run: local=personal LLM key plus "
                         "public quote API; overseas=project LLM key plus production "
                         "quote API. --llm-profile/--llm-env/--quote-profile override this.")
    ap.add_argument("--llm-profile",
                    help="LLM profile from env.json to use for this run (e.g. local, "
                         "production); overrides active_profiles.llm")
    ap.add_argument("--llm-env",
                    help="override active_environment of the selected LLM profile "
                         "for gateway providers. Production envs: internal_equ / "
                         "overseas_prod (overseas prod), wuchang_prod (Wuchang prod). "
                         "Non-production: office_wifi / test (office network incl. test)")
    ap.add_argument("--quote-profile",
                    help="quote profile from env.json to use for this run (e.g. "
                         "production); overrides active_profiles.quote")
    args = ap.parse_args()

    payload = _read_payload(args)

    opts = {}
    if args.limit:
        opts["stock_universe"] = args.limit
        opts["etf_universe"] = args.limit
    for key in ("stock_universe", "etf_universe", "relevance_batch",
                "stock_shortlist", "etf_shortlist", "rvol_threshold",
                "baseline_lookback", "min_history"):
        val = getattr(args, key)
        if val is not None:
            opts[key] = val

    # Resolve config once, applying per-run profile overrides (no writes to env.json).
    env = load_env()
    env.setdefault("active_profiles", {})
    # --target is a convenience switch resolved first; explicit --llm-profile /
    # --llm-env below take precedence if both are supplied.
    if args.target == "local":
        env["active_profiles"]["llm"] = "local"
        env["active_profiles"]["quote"] = "local"
    elif args.target == "overseas":
        env["active_profiles"]["llm"] = "production"
        env["active_profiles"]["quote"] = "production"
        _, prof = config.selected_llm_profile(env)
        prof["active_environment"] = "overseas_prod"
    if args.llm_profile:
        env["active_profiles"]["llm"] = args.llm_profile
    if args.quote_profile:
        env["active_profiles"]["quote"] = args.quote_profile
    if args.llm_env:
        _, prof = config.selected_llm_profile(env)
        prof["active_environment"] = args.llm_env

    llm_cfg = load_llm_config(env)
    print(f"[theme-workflow] llm profile={env['active_profiles'].get('llm')!r} "
          f"provider={llm_cfg.provider} model={llm_cfg.model} url={llm_cfg.base_url}",
          file=sys.stderr, flush=True)
    quote_cfg = load_quote_config(env)
    print(f"[theme-workflow] quote profile={quote_cfg.profile!r} scene={quote_cfg.scene}",
          file=sys.stderr, flush=True)

    llm = build_llm_client(llm_cfg)
    quotes = AInvestClient(quote_cfg)
    wf = ThemeWorkflow(llm, quotes, opts)
    result = wf.run(payload)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
