"""CLI entrypoint for the theme-investing workflow.

Usage:
  python cli.py '{"theme":"AI memory","date":"2026-07-09","url":"https://..."}'
  echo '{...}' | python cli.py
  python cli.py --input input.json --limit 60   # small live smoke test

  # pick the LLM API for THIS run without editing env.json:
  python cli.py --target local --limit 120 '{...}'               # office Wi-Fi -> GPT-5.6 Sol
  python cli.py --target overseas '{...}'                        # project key -> overseas production gateway

  # advanced: pick the raw profile/environment (override --target):
  python cli.py --llm-profile local --limit 120 '{...}'          # office Wi-Fi GPT-5.6 Sol profile
  python cli.py --llm-profile claude --limit 120 '{...}'         # optional if configured from env.example.json
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

from ainvest_auth import AInvestAuthError, refresh_c_session_if_configured
from ainvest_client import AInvestClient
import config
from config import ENV_PATH, load_llm_config, load_quote_config, load_env
from llm_client import LLMClient
from workflow import ThemeWorkflow
from theme_upload import ThemeUploadError, configured_upload, upload_result


def build_llm_client(cfg):
    if cfg.provider == "anthropic":
        from anthropic_local_client import LocalClaudeClient
        return LocalClaudeClient(cfg)
    return LLMClient(cfg)


def _positive_int(value: str) -> int:
    """Argparse type for finite, non-empty universe limits."""
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than 0")
    return parsed


def _nonnegative_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed < 0:
        raise argparse.ArgumentTypeError("must be 0 or greater")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Theme-investing agentic workflow")
    ap.add_argument("payload", nargs="?", help="inline JSON payload")
    ap.add_argument("--input", default="my_input.json", help="path to JSON payload file (default: my_input.json)")
    ap.add_argument(
        "--limit",
        type=_positive_int,
        default=2000,
        help=("screen at most the top N stocks by market cap and request at most N "
              "ordinary theme-derived ETF candidates; ETFs remain hard-capped at 500 "
              "and exact direct probes are additive (default N: 2000)"),
    )
    ap.add_argument("--stock-universe", type=_positive_int,
                    help="override the top-N market-cap stock limit")
    ap.add_argument("--stock-candidate-budget", type=_positive_int,
                    help="maximum broad + theme-industry stocks sent for semantic scoring (default 120)")
    ap.add_argument("--stock-broad-lane", type=_nonnegative_int,
                    help="largest liquid names reserved in the semantic candidate set (default 20)")
    ap.add_argument("--etf-evidence-stock-limit", type=_positive_int,
                    default=30,
                    help="maximum qualified stocks retained as internal ETF evidence (default 30)")
    ap.add_argument("--etf-universe", type=_positive_int,
                    help="lower the ordinary ETF candidate boundary (hard maximum 500)")
    ap.add_argument("--relevance-batch", type=int,
                    help="LLM candidates per relevance request during the scan (default 10)")
    ap.add_argument("--max-scan", type=_nonnegative_int,
                    help="additional safety cap per asset class; use 0 to disable")
    ap.add_argument("--stock-target", type=int,
                    help="maximum qualifying stocks to emit after full-universe ranking (default 8)")
    ap.add_argument("--etf-target", type=int,
                    help="maximum independently verified ETFs to emit (default 5)")
    ap.add_argument("--rvol-threshold", type=float,
                    help="event-window peak RVOL confirmation threshold (default 1.5)")
    ap.add_argument("--relevance-threshold", type=float,
                    help="minimum LLM relevance before causal-evidence gates (default 3.3)")
    ap.add_argument("--min-relevance-confidence", type=float,
                    help="minimum LLM confidence before a stock can qualify (default 0.55)")
    ap.add_argument("--etf-min-theme-score", type=float,
                    help="minimum absolute full-portfolio ETF theme score (default 0.25)")
    ap.add_argument("--etf-holdings-portfolio-budget", type=_nonnegative_int,
                    help="maximum equity ETF portfolios assessed in full (default 40)")
    ap.add_argument("--baseline-lookback", type=int,
                    help="number of pre-event trading bars used to compute the median "
                         "volume baseline for RVOL (default 20)")
    ap.add_argument("--min-history", type=int,
                    help="minimum pre-event bars required before the volume baseline is "
                         "trusted; candidates below this are penalised (default 5)")
    ap.add_argument("--target", choices=["local", "overseas"],
                    help="pick the API set for this run: local=office-WiFi GPT-5.6 Sol plus "
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
    ap.add_argument("--dry-run", action="store_true",
                    help="validate input and configuration without performing C-side login or workflow requests")
    ap.add_argument("--no-upload", action="store_true",
                    help="skip the configured Cloudflare theme result upload")
    return ap


def workflow_options(args) -> dict:
    """Translate CLI sizing knobs into workflow options.

    ``--limit N`` sets a top-N market-cap stock boundary and requests an
    independent ordinary ETF boundary. ETF preselection enforces its hard 500
    maximum, while exact direct probes remain additive. Per-asset overrides are
    applied afterwards when supplied explicitly.
    """
    opts = {}
    if args.limit is not None:
        opts["stock_universe"] = args.limit
        opts["etf_universe"] = args.limit
    for key in ("stock_universe", "stock_candidate_budget", "stock_broad_lane",
                "etf_evidence_stock_limit",
                "etf_universe", "relevance_batch", "max_scan",
                "stock_target", "etf_target", "rvol_threshold",
                "baseline_lookback", "min_history", "relevance_threshold",
                "min_relevance_confidence", "etf_min_theme_score",
                "etf_holdings_portfolio_budget"):
        val = getattr(args, key)
        if val is not None:
            opts[key] = val
    return opts


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


def _build_output(payload: dict, workflow_result: dict) -> dict:
    """Build the public CLI result while preserving per-security event dates."""
    return {**payload, **workflow_result}


def _serialize_output(output: dict) -> str:
    """Serialize the public result as compact JSON with no trailing newline."""
    return json.dumps(output, ensure_ascii=False, separators=(",", ":"))


def main() -> int:
    args = build_parser().parse_args()

    payload = _read_payload(args)

    opts = workflow_options(args)

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

    if args.dry_run:
        print("[theme-workflow] dry run: C-side login and workflow requests skipped", file=sys.stderr)
        return 0

    # An enabled C-side login refreshes the session atomically before quotes are
    # configured. Static session cookies remain the fallback when it is disabled.
    try:
        env = refresh_c_session_if_configured(env, ENV_PATH)
    except AInvestAuthError as exc:
        print(f"error: C-side session refresh failed: {exc}", file=sys.stderr)
        return 2

    llm_cfg = load_llm_config(env)
    print(f"[theme-workflow] llm profile={env['active_profiles'].get('llm')!r} "
          f"provider={llm_cfg.provider} model={llm_cfg.model} "
          f"reasoning_effort={llm_cfg.reasoning_effort or 'provider-default'} "
          f"max_completion_tokens={llm_cfg.max_completion_tokens} "
          f"url={llm_cfg.base_url}",
          file=sys.stderr, flush=True)
    quote_cfg = load_quote_config(env)
    print(f"[theme-workflow] quote profile={quote_cfg.profile!r} scene={quote_cfg.scene}",
          file=sys.stderr, flush=True)

    llm = build_llm_client(llm_cfg)
    quotes = AInvestClient(quote_cfg)
    wf = ThemeWorkflow(llm, quotes, opts)
    result = _build_output(payload, wf.run(payload))
    serialized = _serialize_output(result)
    sys.stdout.write(serialized)
    try:
        upload = None if args.no_upload else configured_upload()
        if upload:
            archive_id = upload_result(serialized, endpoint=upload[0], token=upload[1])
            print(f"[theme-workflow] uploaded result archive_id={archive_id}", file=sys.stderr)
    except ThemeUploadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
