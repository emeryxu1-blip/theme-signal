"""Orchestration for the theme-investing agentic workflow.

Pipeline:
  1. validate input + fetch article
  2. LLM event brief (theme taxonomy)
  3. build universes: top stocks by market cap (block C191),
     top ETFs by AUM (prompt 67a9b535...)
  4. LLM relevance score across the universe (batched)
  5. fetch daily k-lines for the relevance shortlist -> event->today change % + relative volume
  6. composite score, select top 8 stocks / 5 ETFs, calibrate diverse 1-5 scores
  7. LLM narrative (intro + why_bullish) + SEO FAQ
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import sys

import prompts
import scoring
from ainvest_client import AInvestClient
from article import fetch_article
from llm_client import LLMClient

# universe pool identifiers (validated against local AInvest references)
STOCK_POOL = {"type": "block_id", "value": ["C191"]}          # all US stocks
ETF_POOL = {"type": "prompt_id", "value": ["67a9b53525d79817b68f6d38"]}  # largest-AUM US ETFs
MKTCAP_ID = "total_market_value"
AUM_ID = "国际北美etf@Assets Under Management(Latest)"

DEFAULTS = dict(
    stock_universe=2000,
    etf_universe=2000,
    relevance_batch=60,
    stock_shortlist=40,
    etf_shortlist=25,
    kline_count=90,           # enough daily bars for a ~20-day PRE-event baseline
    rvol_threshold=1.5,       # event-window peak RVOL required to be "volume-confirmed"
    benchmark="169:SPY",      # market benchmark for abnormal-return calculation
)


def _log(msg: str):
    print(f"[theme-workflow] {msg}", file=sys.stderr, flush=True)


def validate_input(payload: dict) -> dict:
    theme = (payload.get("theme") or "").strip()
    date = (payload.get("date") or "").strip()
    url = (payload.get("url") or "").strip()
    if not theme:
        raise ValueError("theme is required")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise ValueError("date must be YYYY-MM-DD")
    d = _dt.date.fromisoformat(date)
    if d > _dt.date.today():
        raise ValueError("date cannot be in the future")
    if not re.match(r"^https?://", url):
        raise ValueError("url must be an absolute http(s) URL")
    return {"theme": theme, "date": date, "url": url}


def _batched(seq, size):
    for i in range(0, len(seq), size):
        yield seq[i:i + size]


class ThemeWorkflow:
    def __init__(self, llm: LLMClient, quotes: AInvestClient, opts: dict | None = None):
        self.llm = llm
        self.quotes = quotes
        self.opts = {**DEFAULTS, **(opts or {})}
        self.as_of = _dt.date.today().isoformat()

    # -- stage 2 -------------------------------------------------------------
    def event_brief(self, inp: dict, art: dict) -> dict:
        user = prompts.EVENT_BRIEF_USER.format(
            theme=inp["theme"], date=inp["date"], title=art.get("title", ""),
            url=inp["url"], excerpt=art.get("text", "")[:6000],
        )
        return self.llm.chat_json(prompts.EVENT_BRIEF_SYS, user)

    # -- stage 3 -------------------------------------------------------------
    def build_universe(self, pool, indicator_id, req_id, total, sort_pos):
        rows = self.quotes.rank_universe(
            pool, [{"id": indicator_id, "req_unique_id": req_id}],
            total=total, sort_pos=sort_pos,
        )
        for r in rows:
            r["name"] = self.quotes.name_of(r["code"])
            r["metric"] = r["values"].get(req_id)
        return rows

    # -- stage 4 -------------------------------------------------------------
    def score_relevance(self, brief: dict, rows: list[dict]) -> dict:
        brief_txt = json.dumps(brief, ensure_ascii=False)
        scores: dict[str, dict] = {}
        for batch in _batched(rows, self.opts["relevance_batch"]):
            cand = "\n".join(f"{r['code']} | {r['name']}" for r in batch)
            user = prompts.RELEVANCE_USER.format(brief=brief_txt, candidates=cand)
            try:
                arr = self.llm.chat_json(prompts.RELEVANCE_SYS, user)
            except (ValueError, RuntimeError) as exc:
                _log(f"relevance batch failed ({exc}); scoring 0")
                arr = []
            by_code = {str(o.get("market_code")): o for o in arr if isinstance(o, dict)}
            for r in batch:
                o = by_code.get(r["code"], {})
                scores[r["code"]] = {
                    "ai_relevance": int(o.get("ai_relevance", 1) or 1),
                    "exposure_type": o.get("exposure_type", "unclear"),
                    "confidence": float(o.get("confidence", 0.3) or 0.3),
                    "reason": o.get("reason", ""),
                }
        return scores

    # -- stage 5 -------------------------------------------------------------
    def add_market_features(self, cands: list[dict], ev_int: int, benchmark_return):
        codes = [c["code"] for c in cands]
        klines = self.quotes.fetch_klines(codes, count=self.opts["kline_count"])
        for c in cands:
            feats = scoring.compute_kline_features(
                klines.get(c["code"], []), ev_int, benchmark_return)
            c.update(feats)

    # -- stage 6 -------------------------------------------------------------
    def select(self, cands: list[dict], top_n: int, kind: str) -> list[dict]:
        thr = self.opts["rvol_threshold"]
        rvol_pct = scoring.percentiles([c.get("rvol_event") for c in cands])
        abret_pct = scoring.percentiles([c.get("abnormal_return") for c in cands])
        chg_pct = scoring.percentiles([c.get("chg_pct") for c in cands])
        for c, rv, ab, cg in zip(cands, rvol_pct, abret_pct, chg_pct):
            c["rvol_pct"], c["abret_pct"], c["chg_rank"] = rv, ab, cg
            c["composite"] = scoring.composite(
                c["ai_relevance"], rv, ab, cg, c.get("quality", 1.0))
            c["volume_confirmed"] = bool(
                c.get("rvol_event") is not None and c["rvol_event"] >= thr)

        # keep meaningful relevance; fall back only if too few
        strong = [c for c in cands if c["ai_relevance"] >= 3]
        pool = strong if len(strong) >= top_n else cands
        key = lambda c: (c["composite"], c["ai_relevance"], c["rvol_pct"],
                         c["abret_pct"], -c["rank"])
        # volume-confirmed names first; unconfirmed only fill remaining slots
        confirmed = sorted([c for c in pool if c["volume_confirmed"]], key=key, reverse=True)
        rest = sorted([c for c in pool if not c["volume_confirmed"]], key=key, reverse=True)
        chosen = (confirmed + rest)[:top_n]
        n_conf = sum(1 for c in chosen if c["volume_confirmed"])
        _log(f"{kind}: {n_conf}/{len(chosen)} volume-confirmed (event RVOL>={thr}); "
             f"{len(confirmed)} of {len(pool)} candidates passed the gate")

        display = scoring.calibrate_scores([c["composite"] for c in chosen])
        for c, s in zip(chosen, display):
            c["score"] = s
        return chosen

    # -- stage 7 -------------------------------------------------------------
    def narrate(self, inp: dict, brief: dict, chosen: list[dict], kind: str) -> dict:
        records = [{
            "market_code": c["code"], "name": c["name"],
            "exposure_type": c["exposure_type"],
            "relevance": c["ai_relevance"],
            "event_to_today_change_pct": round(c["chg_pct"], 2) if c.get("chg_pct") is not None else None,
            "relative_volume": round(c["rel_volume"], 2) if c.get("rel_volume") is not None else None,
        } for c in chosen]
        user = prompts.NARRATIVE_USER.format(
            theme=inp["theme"], thesis=brief.get("thesis", ""), as_of=self.as_of,
            kind=kind, records=json.dumps(records, ensure_ascii=False))
        try:
            arr = self.llm.chat_json(prompts.NARRATIVE_SYS, user)
        except (ValueError, RuntimeError):
            arr = []
        return {str(o.get("market_code")): o for o in arr if isinstance(o, dict)}

    def faq(self, inp: dict, brief: dict, stocks, etfs) -> list[dict]:
        user = prompts.FAQ_USER.format(
            theme=inp["theme"], thesis=brief.get("thesis", ""),
            stocks=", ".join(s["name"] for s in stocks),
            etfs=", ".join(e["name"] for e in etfs),
            date=inp["date"], n_min=4, n_max=8)
        try:
            arr = self.llm.chat_json(prompts.FAQ_SYS, user)
        except (ValueError, RuntimeError):
            arr = []
        out = [{"question": o.get("question", ""), "answer": o.get("answer", "")}
               for o in arr if isinstance(o, dict) and o.get("question")]
        return out[:8]

    def _assemble(self, chosen, narr, event_date):
        items = []
        for c in chosen:
            n = narr.get(c["code"], {})
            items.append({
                "market_code": c["code"],
                "why_bullish": n.get("why_bullish", c.get("reason", "")),
                "score": c["score"],
                "score_components": {
                    "ai_relevance": c["ai_relevance"],
                    "rvol_event": (round(c["rvol_event"], 2)
                                   if c.get("rvol_event") is not None else None),
                    "volume_confirmed": c.get("volume_confirmed", False),
                    "abnormal_return_pct": (round(c["abnormal_return"], 2)
                                            if c.get("abnormal_return") is not None else None),
                    "event_to_today_change_pct": (round(c["chg_pct"], 2)
                                                  if c.get("chg_pct") is not None else None),
                },
                "event_date": event_date,
            })
        return items

    # -- driver --------------------------------------------------------------
    def run(self, payload: dict) -> dict:
        inp = validate_input(payload)
        ev_int = scoring.event_date_int(inp["date"])
        _log(f"theme={inp['theme']!r} date={inp['date']} scene={self.quotes.cfg.scene}")

        art = fetch_article(inp["url"])
        _log(f"article ok={art['ok']} title={art.get('title','')[:60]!r}")

        brief = self.event_brief(inp, art)
        _log("event brief ready")

        stock_rows = self.build_universe(STOCK_POOL, MKTCAP_ID, "mktcap",
                                         self.opts["stock_universe"], sort_pos=0)
        etf_rows = self.build_universe(ETF_POOL, AUM_ID, "aum",
                                       self.opts["etf_universe"], sort_pos=None)
        _log(f"universe: {len(stock_rows)} stocks, {len(etf_rows)} ETFs")

        stock_scores = self.score_relevance(brief, stock_rows)
        etf_scores = self.score_relevance(brief, etf_rows)
        for r in stock_rows:
            r.update(stock_scores.get(r["code"], {"ai_relevance": 1, "exposure_type": "unclear",
                                                  "confidence": 0.0, "reason": ""}))
        for r in etf_rows:
            r.update(etf_scores.get(r["code"], {"ai_relevance": 1, "exposure_type": "unclear",
                                               "confidence": 0.0, "reason": ""}))
        _log("relevance scored")

        stock_short = sorted(stock_rows, key=lambda r: (r["ai_relevance"], r["confidence"]),
                             reverse=True)[:self.opts["stock_shortlist"]]
        etf_short = sorted(etf_rows, key=lambda r: (r["ai_relevance"], r["confidence"]),
                           reverse=True)[:self.opts["etf_shortlist"]]

        # Fetch SPY once to remove broad-market drift from each candidate's return.
        benchmark_bars = self.quotes.fetch_klines(
            [self.opts["benchmark"]], count=self.opts["kline_count"]
        ).get(self.opts["benchmark"], [])
        benchmark_return = scoring.window_return(benchmark_bars, ev_int)
        _log(f"benchmark={self.opts['benchmark']} return={benchmark_return}")

        self.add_market_features(stock_short, ev_int, benchmark_return)
        self.add_market_features(etf_short, ev_int, benchmark_return)
        _log("market features computed")

        top_stocks = self.select(stock_short, 8, "stocks")
        top_etfs = self.select(etf_short, 5, "ETFs")

        stock_narr = self.narrate(inp, brief, top_stocks, "stock")
        etf_narr = self.narrate(inp, brief, top_etfs, "ETF")
        faq = self.faq(inp, brief, top_stocks, top_etfs)
        _log("narratives + FAQ ready")

        return {
            "ThemeStocks": self._assemble(top_stocks, stock_narr, inp["date"]),
            "ThemeEtfs": self._assemble(top_etfs, etf_narr, inp["date"]),
            "ThemeFAQ": faq,
        }
