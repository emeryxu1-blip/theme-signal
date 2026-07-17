"""Orchestration for the theme-investing agentic workflow.

Pipeline:
  1. validate input + fetch article
  2. LLM event brief (theme taxonomy)
  3. stream stocks by market cap and LLM-score their business exposure
  4. deterministically derive ETFs from selected-stock holdings and static
     curated theme pools; filter/rank with factual quote data (not an LLM)
  5. fetch daily k-lines for the qualifying finalists -> event->today change % + relative volume
  6. calibrate the LLM theme-exposure scores to diverse 1-5 display values
  7. LLM theme rationale + SEO FAQ
"""

from __future__ import annotations

import datetime as _dt
import json
import re
import sys
from itertools import islice

import prompts
import scoring
from ainvest_client import AInvestClient
from article import fetch_article
from etf_preselection import preselect_etfs
from llm_client import LLMClient

# universe pool identifiers (validated against local AInvest references)
STOCK_POOL = {"type": "block_id", "value": ["C191"]}  # all US stocks
MKTCAP_ID = "total_market_value"

DEFAULTS = dict(
    # Stocks: top-N market-cap boundary. ETFs: maximum unique candidates produced
    # by finite theme pools / selected-stock holding relationships.
    stock_universe=500,       # top-N stocks by market cap the scan may walk
    etf_universe=500,         # maximum theme-derived ETF candidates to rank
    etf_theme_pools=4,        # maximum statically matched curated pools
    max_scan=None,            # optional extra safety ceiling; 0/None disables it
    relevance_batch=20,       # candidates per LLM relevance request during the scan
    stock_target=8,           # stocks to emit (scan stops once this many qualify)
    etf_target=5,             # ETFs to emit (scan stops once this many qualify)
    relevance_threshold=2.5,  # minimum LLM relevance (1-5, fractional) for output
    kline_count=90,           # enough daily bars for the PRE-event baseline window
    baseline_lookback=20,     # pre-event bars used to compute the median volume baseline
    min_history=5,            # minimum pre-event bars required before baseline is trusted
    rvol_threshold=1.5,       # event-window peak RVOL that earns a "volume-confirmed" badge
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


def _chunks(iterable, size):
    """Yield successive ``size``-length lists from any iterable (lazy)."""
    it = iter(iterable)
    while True:
        chunk = list(islice(it, size))
        if not chunk:
            return
        yield chunk


_EXPOSURE_TYPES = {"direct", "enabler", "supply_chain", "beneficiary",
                   "diversified", "unclear"}

_NARRATIVE_INTERNAL_RE = re.compile(
    r"\b(?:ai_relevance|exposure_type|theme_exposure_raw|static_theme_exposure|"
    r"preselect_score|score_components|event_to_today_change_pct|abnormal_return|"
    r"volume_confirmed|relative_volume|theme_stock_breadth|"
    r"aggregate_theme_holding_weight_pct|weighted_theme_exposure_pct|pool_strength|"
    r"rvol|relevance|confidence|ranking|ranked|threshold|percentile|score|scoring)\b|"
    r"\brelative volume\b|\babnormal return\b|\bevent[- ]to[- ]today\b|"
    r"\bvolume[- ]confirmed\b|\bselection mechanics\b|"
    r"\b(?:aggregate|combined|total|weighted)\s+(?:theme\s+)?(?:exposure|holdings?|weight)\b|"
    r"\d+(?:\.\d+)?%\s*(?:\+|=|×)|"
    r"(?:相关性|相关度)\s*(?:评分|得分)?\s*[:：=]?\s*\d|"
    r"内部(?:评分|得分|排名|计算|变量|字段)|置信度|阈值|百分位|"
    r"相对成交量|异常收益|加权主题敞口|总主题敞口",
    re.IGNORECASE,
)
_SNAKE_CASE_RE = re.compile(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_LATIN_RE = re.compile(r"[A-Za-z]")


def _clamp_int(v, lo, hi, *, default):
    try:
        return max(lo, min(hi, int(v)))
    except (TypeError, ValueError):
        return default


def _clamp_float(v, lo, hi, *, default):
    try:
        return max(lo, min(hi, float(v)))
    except (TypeError, ValueError):
        return default


def _norm_exposure(v) -> str:
    v = str(v or "").strip().lower()
    return v if v in _EXPOSURE_TYPES else "unclear"


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
    def _pool_rows(self, pool, indicator_id, req_id, sort_pos):
        """Lazily yield named rows from a ranked API pool (market cap / AUM order)."""
        for r in self.quotes.iter_ranked(
                pool, [{"id": indicator_id, "req_unique_id": req_id}], sort_pos=sort_pos):
            r["name"] = self.quotes.name_of(r["code"])
            r["metric"] = r["values"].get(req_id)
            yield r

    def stock_source(self):
        """Yield all U.S. stocks in descending market-cap order."""
        yield from self._pool_rows(STOCK_POOL, MKTCAP_ID, "mktcap", sort_pos=0)

    # -- stage 4 -------------------------------------------------------------
    def screen_until_target(self, rows, brief: dict, article: dict,
                            target: int, kind: str, cap: int | None = None,
                            rank_label: str = "rank") -> list[dict]:
        """Stream a ranked universe lazily, LLM-scoring each batch, and
        collect names whose relevance clears the threshold until ``target`` names
        qualify or the universe is exhausted.

        ``rows`` is any iterable of ranked candidate rows. ``cap`` bounds the eligible
        universe to the first N rows; an explicitly configured ``max_scan`` can add a
        lower safety ceiling. Pages are pulled only while more names are needed.
        Screening is purely article-grounded relevance; the finalists are the
        highest-ranked names the LLM judges genuinely exposed to the theme.
        """
        thr = float(self.opts["relevance_threshold"])
        batch_size = self.opts["relevance_batch"]
        # --limit supplies `cap` independently for stocks and ETFs. max_scan is an
        # optional second ceiling retained for callers that need a stricter guard.
        if cap is not None and cap <= 0:
            raise ValueError("universe cap must be greater than 0")
        safety_cap = self.opts.get("max_scan")
        if safety_cap is not None and safety_cap < 0:
            raise ValueError("max_scan cannot be negative")
        safety_cap = safety_cap or None
        scan_cap = min(cap, safety_cap) if cap and safety_cap else (cap or safety_cap)
        if scan_cap is not None:
            rows = islice(rows, scan_cap)
        qualified: list[dict] = []
        scanned = 0
        for batch in _chunks(rows, batch_size):
            scores = self.score_relevance(brief, batch, article)
            for r in batch:
                r.update(scores.get(r["code"], {
                    "ai_relevance": 1.0, "exposure_type": "unclear",
                    "confidence": 0.0, "reason": "", "relevance_status": "missing"}))
            scanned += len(batch)
            for r in batch:
                if r.get("ai_relevance", 0) >= thr and len(qualified) < target:
                    qualified.append(r)
            _log(f"{kind}: scanned {scanned} by {rank_label}; "
                 f"{len(qualified)}/{target} qualify (relevance >= {thr})")
            if len(qualified) >= target:
                break
        if len(qualified) < target:
            why = (f"scan cap {scan_cap} reached" if scan_cap and scanned >= scan_cap
                   else "universe exhausted")
            _log(f"{kind}: {why} with {len(qualified)}/{target} names >= relevance {thr}")
        return qualified

    @staticmethod
    def _coerce_relevance_rows(payload) -> list[dict]:
        """Normalize common LLM JSON wrappers to the expected candidate array."""
        if isinstance(payload, list):
            return [o for o in payload if isinstance(o, dict)]
        if isinstance(payload, dict):
            for key in ("results", "scores", "candidates", "items", "data", "output"):
                value = payload.get(key)
                if isinstance(value, list):
                    return [o for o in value if isinstance(o, dict)]
            # A single candidate object is still useful for a one-row response.
            if payload.get("market_code") is not None:
                return [payload]
        return []

    def score_relevance(self, brief: dict, rows: list[dict], article: dict | None = None) -> dict:
        article = article or {}
        article_txt = json.dumps({
            "title": article.get("title", ""),
            "url": article.get("url", ""),
            "excerpt": article.get("text", "")[:6000],
        }, ensure_ascii=False)
        brief_txt = json.dumps(brief, ensure_ascii=False)
        scores: dict[str, dict] = {}
        for batch in _batched(rows, self.opts["relevance_batch"]):
            cand = "\n".join(
                f"{r['code']} | {r['name']} | "
                f"rvol_event={r.get('rvol_event'):.2f} | "
                f"chg_pct={r.get('chg_pct'):.2f} | "
                f"abnormal_return={r.get('abnormal_return'):.2f}"
                if all(r.get(k) is not None for k in ("rvol_event", "chg_pct", "abnormal_return"))
                else f"{r['code']} | {r['name']} | market data unavailable"
                for r in batch
            )
            user = prompts.RELEVANCE_USER.format(
                article_context=prompts.ARTICLE_CONTEXT.format(article=article_txt),
                brief=brief_txt, candidates=cand)
            raw = None
            try:
                raw = self.llm.chat_json(prompts.RELEVANCE_SYS, user)
                arr = self._coerce_relevance_rows(raw)
            except (ValueError, RuntimeError) as exc:
                _log(f"relevance batch failed ({exc}); marking {len(batch)} candidates unresolved")
                arr = []
            if not arr:
                shape = (f"dict keys={list(raw)[:8]}" if isinstance(raw, dict)
                         else f"type={type(raw).__name__}")
                preview = repr(raw)[:500]
                _log(f"relevance batch returned no usable rows for {len(batch)} "
                     f"candidates (response {shape}); preview={preview}")
                # An empty list is a valid model response meaning "none relevant".
                # Do not recursively retry it: repeated calls waste time and can
                # cause the local model to exhaust its request budget. Missing rows
                # remain safely ineligible below.
                arr = []
            # Normalize codes so minor formatting differences (whitespace, case)
            # in the echoed market_code still match the requested candidate.
            def _norm_code(v):
                return str(v or "").strip().upper()
            by_code = {}
            for o in arr:
                by_code[_norm_code(o.get("market_code"))] = o
            for r in batch:
                o = by_code.get(_norm_code(r["code"]))
                if o is None and len(arr) == len(batch):
                    # Some models return valid rows in order but alter/omit the
                    # identifier. Only use positional recovery when coverage is
                    # complete, never to silently assign partial results.
                    o = arr[batch.index(r)]
                if o is None:
                    # Missing from the LLM reply: mark unresolved, do NOT treat a
                    # transport/coverage gap as a confident "no exposure" verdict.
                    scores[r["code"]] = {
                        "ai_relevance": 1.0, "exposure_type": "unclear",
                        "confidence": 0.0, "reason": "", "relevance_status": "missing",
                    }
                    continue
                scores[r["code"]] = {
                    "ai_relevance": _clamp_float(o.get("ai_relevance"), 1.0, 5.0, default=1.0),
                    "exposure_type": _norm_exposure(o.get("exposure_type")),
                    "confidence": _clamp_float(o.get("confidence"), 0.0, 1.0, default=0.3),
                    "reason": str(o.get("reason", ""))[:200],
                    "relevance_status": "scored",
                }
        n_missing = sum(1 for v in scores.values() if v.get("relevance_status") == "missing")
        if n_missing:
            _log(f"relevance: {n_missing}/{len(scores)} candidates had no valid LLM row")
        return scores

    # -- stage 5 -------------------------------------------------------------
    def add_market_features(self, cands: list[dict], ev_int: int, benchmark_return):
        """Populate full-window market features for names that lack them."""
        pending = [c for c in cands if "chg_pct" not in c]
        if not pending:
            return
        codes = [c["code"] for c in pending]
        klines = self.quotes.fetch_klines(codes, count=self.opts["kline_count"])
        for c in pending:
            feats = scoring.compute_kline_features(
                klines.get(c["code"], []), ev_int, benchmark_return,
                baseline_lookback=self.opts["baseline_lookback"],
                min_history=self.opts["min_history"],
            )
            c.update(feats)

    def _assign_theme_exposure(self, chosen: list[dict]) -> None:
        """Order by, then calibrate, the unified headline Theme exposure scores."""
        for c in chosen:
            if c.get("static_theme_exposure") is not None:
                # ETF exposure is factual holdings/pool arithmetic from
                # etf_preselection.py; do not replace it with an LLM-derived score.
                c["theme_exposure_raw"] = _clamp_float(
                    c["static_theme_exposure"], 0.0, 1.0, default=0.0)
            else:
                c["theme_exposure_raw"] = scoring.theme_exposure(
                    c.get("ai_relevance", 1), c.get("exposure_type", "unclear"),
                    c.get("confidence", 0.0),
                    market_confirm=bool(c.get("market_confirmed")),
                )
        # calibrate_scores expects a descending list; sort so headline scores are
        # monotonic and the emitted list is ranked by Theme exposure.
        chosen.sort(key=lambda c: c["theme_exposure_raw"], reverse=True)
        display = scoring.calibrate_scores([c["theme_exposure_raw"] for c in chosen])
        for c, score in zip(chosen, display):
            c["theme_exposure"] = score
            c["score"] = score

    @staticmethod
    def _validated_rationale_text(value) -> str | None:
        """Return one safe language string, or ``None`` when details leaked."""
        text = str(value or "").strip()
        if not text:
            return None
        if _NARRATIVE_INTERNAL_RE.search(text) or _SNAKE_CASE_RE.search(text):
            return None
        return text[:1200]

    @classmethod
    def _validated_theme_rationale(cls, value) -> dict | None:
        """Normalize a complete, safe English/Simplified-Chinese rationale."""
        if not isinstance(value, dict) or value.get("type") != "multilingual":
            return None
        en = cls._validated_rationale_text(value.get("en"))
        zh = cls._validated_rationale_text(value.get("zh"))
        if en is None or zh is None or not _LATIN_RE.search(en) or not _CJK_RE.search(zh):
            return None
        return {"type": "multilingual", "en": en, "zh": zh}

    @staticmethod
    def _human_join(values: list[str]) -> str:
        if len(values) < 2:
            return values[0] if values else ""
        if len(values) == 2:
            return f"{values[0]} and {values[1]}"
        return f"{', '.join(values[:-1])}, and {values[-1]}"

    @staticmethod
    def _human_join_zh(values: list[str]) -> str:
        return "、".join(values)

    @staticmethod
    def _multilingual(en: str, zh: str) -> dict:
        return {"type": "multilingual", "en": en, "zh": zh}

    @classmethod
    def _fallback_theme_rationale(cls, candidate: dict) -> dict:
        """Build bilingual, calculation-free prose when LLM output is absent or unsafe."""
        name = str(candidate.get("name") or candidate.get("code") or "This security").strip()
        is_etf = candidate.get("static_theme_exposure") is not None
        if is_etf:
            tickers = []
            for holding in candidate.get("matched_holdings", []):
                ticker = str(
                    holding.get("ticker")
                    or str(holding.get("code") or "").partition(":")[2]
                ).strip()
                if ticker and ticker not in tickers:
                    tickers.append(ticker)
            if tickers:
                examples = cls._human_join(tickers[:3])
                examples_zh = cls._human_join_zh(tickers[:3])
                return cls._multilingual(
                    en=(
                        f"{name} provides a route to the theme through holdings such as {examples}. "
                        "Investors should verify that these positions remain material because fund "
                        "holdings and portfolio weights can change."
                    ),
                    zh=(
                        f"{name}通过持有{examples_zh}等标的为投资者提供该主题敞口。"
                        "鉴于基金持仓及权重可能变化，投资者应持续核实这些持仓是否仍具实质性。"
                    ),
                )
            categories = [str(v).strip() for v in candidate.get("pool_labels", []) if str(v).strip()]
            if categories:
                category_text = cls._human_join(categories[:2])
                category_text_zh = cls._human_join_zh(categories[:2])
                return cls._multilingual(
                    en=(
                        f"{name} is associated with {category_text}, providing a fund-level route "
                        "to the theme. Investors should verify the current holdings and mandate "
                        "because category membership alone may not provide concentrated exposure."
                    ),
                    zh=(
                        f"{name}被归入{category_text_zh}，可作为基金层面的主题配置工具。"
                        "但类别归属本身并不代表敞口集中，投资者仍应核实其最新持仓与投资范围。"
                    ),
                )
            return cls._multilingual(
                en=(
                    f"{name} may provide fund-level access to the theme, but the available evidence "
                    "does not establish how concentrated that exposure is. Review the fund's current "
                    "holdings and mandate before treating it as a targeted vehicle."
                ),
                zh=(
                    f"{name}可能提供基金层面的主题配置渠道，但现有证据无法确认其敞口集中度。"
                    "在将其视为针对性工具前，应核查基金的最新持仓与投资范围。"
                ),
            )

        evidence = str(candidate.get("reason") or "").strip().rstrip(".")
        if cls._validated_rationale_text(evidence):
            return cls._multilingual(
                en=(
                    f"{name}'s connection to the theme is supported by {evidence}. The available "
                    "evidence does not quantify the potential revenue or earnings contribution, so "
                    "investors should monitor segment-level disclosures."
                ),
                zh=(
                    f"{name}与该主题的业务联系基于以下证据：{evidence}。"
                    "现有资料尚未量化其对收入或盈利的潜在贡献，投资者应关注分部层面的后续披露。"
                ),
            )
        return cls._multilingual(
            en=(
                f"{name} was identified as a potential beneficiary, but the available evidence does "
                "not establish a specific product or segment link. Investors should confirm the "
                "revenue pathway and financial materiality before treating it as a theme exposure."
            ),
            zh=(
                f"{name}被识别为潜在受益标的，但现有证据尚未建立具体的产品或业务分部联系。"
                "在将其视为主题标的前，投资者应确认收入传导路径及其财务重要性。"
            ),
        )

    @staticmethod
    def _narrative_record(candidate: dict, kind: str) -> dict:
        """Expose only evidence needed for prose, never ranking or calculation inputs."""
        record = {
            "market_code": candidate["code"],
            "name": candidate["name"],
        }
        if kind.lower() != "etf":
            record["exposure evidence"] = (
                str(candidate.get("reason") or "").strip()
                or "No specific product or segment evidence was supplied."
            )
            return record

        holdings = []
        for holding in candidate.get("matched_holdings", []):
            ticker = str(
                holding.get("ticker")
                or str(holding.get("code") or "").partition(":")[2]
            ).strip()
            if not ticker:
                continue
            try:
                weight = float(holding.get("weight_pct"))
                holdings.append(f"{ticker} ({weight:.2f}% of the fund)")
            except (TypeError, ValueError):
                holdings.append(ticker)
        if holdings:
            record["relevant holdings"] = holdings

        fund_context = []
        for key in ("fund_focus", "fund_niche", "fund_strategy", "benchmark"):
            value = str(candidate.get(key) or "").strip()
            if value and value not in fund_context:
                fund_context.append(value)
        if fund_context:
            record["fund context"] = fund_context

        categories = [str(v).strip() for v in candidate.get("pool_labels", []) if str(v).strip()]
        if categories:
            record["theme categories"] = categories
        return record

    def _assemble(self, chosen, narr, event_date):
        self._assign_theme_exposure(chosen)
        items = []
        for c in chosen:
            n = narr.get(c["code"], {})
            rationale = self._validated_theme_rationale(n.get("theme_rationale"))
            if rationale is None:
                rationale = self._fallback_theme_rationale(c)
            items.append({
                "market_code": c["code"],
                "theme_rationale": rationale,
                "Theme exposure": c["theme_exposure"],
                "event_date": event_date,
            })
        return items

    # -- legacy assembly name retained above for compatibility --
    def _assemble_legacy_removed(self, chosen, narr, event_date):
        """Unused compatibility marker; real assembly is the method above."""
        return []

    def _mark_volume_confirmed(self, cands: list[dict]) -> None:
        """Attach the internal market-confirmation signal used by theme scoring."""
        thr = self.opts["rvol_threshold"]
        for c in cands:
            c["volume_confirmed"] = bool(
                c.get("rvol_event") is not None and c["rvol_event"] >= thr)
            c["market_confirmed"] = bool(
                c["volume_confirmed"]
                and c.get("abnormal_return") is not None and c["abnormal_return"] > 0)

    # -- stage 7 -------------------------------------------------------------
    def narrate(self, inp: dict, brief: dict, chosen: list[dict], kind: str) -> dict:
        records = [self._narrative_record(c, kind) for c in chosen]
        user = prompts.NARRATIVE_USER.format(
            theme=inp["theme"], summary=brief.get("summary", ""),
            thesis=brief.get("thesis", ""), as_of=self.as_of,
            kind=kind, records=json.dumps(records, ensure_ascii=False))
        try:
            arr = self.llm.chat_json(prompts.NARRATIVE_SYS, user)
        except (ValueError, RuntimeError):
            arr = []
        if not isinstance(arr, list):
            arr = []
        output = {}
        for obj in arr:
            if not isinstance(obj, dict):
                continue
            code = str(obj.get("market_code") or "")
            rationale = self._validated_theme_rationale(obj.get("theme_rationale"))
            if not code or rationale is None:
                if code:
                    _log(f"narrative rejected for {code}; using evidence-only fallback")
                continue
            output[code] = {"theme_rationale": rationale}
        return output

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

    # -- driver --------------------------------------------------------------
    def run(self, payload: dict) -> dict:
        inp = validate_input(payload)
        ev_int = scoring.event_date_int(inp["date"])
        _log(f"theme={inp['theme']!r} date={inp['date']} scene={self.quotes.cfg.scene}")

        art = fetch_article(inp["url"])
        _log(f"article ok={art['ok']} title={art.get('title','')[:60]!r}")

        brief = self.event_brief(inp, art)
        _log("event brief ready")

        # Stocks retain the market-cap-ordered semantic screen.
        top_stocks = self.screen_until_target(
            self.stock_source(), brief, art, self.opts["stock_target"], "stocks",
            cap=self.opts["stock_universe"], rank_label="market cap")

        # ETF facts that are universal (fund holdings, leverage/direction, source
        # membership, deduplication, and ranking) are implemented deterministically.
        # max_scan remains an optional lower safety ceiling for both asset classes.
        etf_cap = int(self.opts["etf_universe"])
        safety_cap = self.opts.get("max_scan")
        if safety_cap:
            etf_cap = min(etf_cap, int(safety_cap))
        etf_result = preselect_etfs(
            self.quotes,
            top_stocks,
            theme=inp["theme"],
            brief=brief,
            article_title=art.get("title", ""),
            limit=etf_cap,
            max_pools=int(self.opts.get("etf_theme_pools", 4)),
            log=_log,
        )
        top_etfs = etf_result.candidates[:int(self.opts["etf_target"])]
        _log(f"selected {len(top_stocks)} stocks and {len(top_etfs)} ETFs")

        # Fetch SPY once to remove broad-market drift, then compute market features
        # for the finalists only. These remain internal scoring inputs and are not
        # passed to the narrative stage.
        benchmark_bars = self.quotes.fetch_klines(
            [self.opts["benchmark"]], count=self.opts["kline_count"]
        ).get(self.opts["benchmark"], [])
        benchmark_return = scoring.window_return(benchmark_bars, ev_int)
        self.add_market_features(top_stocks, ev_int, benchmark_return)
        self.add_market_features(top_etfs, ev_int, benchmark_return)
        self._mark_volume_confirmed(top_stocks)
        self._mark_volume_confirmed(top_etfs)
        _log("market features computed")

        stock_narr = self.narrate(inp, brief, top_stocks, "stock")
        etf_narr = self.narrate(inp, brief, top_etfs, "ETF")
        faq = self.faq(inp, brief, top_stocks, top_etfs)
        _log("narratives + FAQ ready")

        return {
            "ThemeStocks": self._assemble(top_stocks, stock_narr, inp["date"]),
            "ThemeEtfs": self._assemble(top_etfs, etf_narr, inp["date"]),
            "ThemeFAQ": faq,
        }
