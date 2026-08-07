"""Orchestration for the theme-investing agentic workflow.

Pipeline:
  1. validate input + fetch article
  2. LLM event brief (theme taxonomy + internal bullish/bearish direction)
  3. stream stocks by market cap and LLM-score their directional business exposure
  4. deterministically derive ETFs from theme-stock relations and static pools;
     conditionally admit verified 1x-3x inverse products for bearish briefs
  5. fetch daily k-lines for finalists -> signed facts + |Chg %|/RVOL strength
  6. apply a bounded market-strength uplift and calibrate diverse 1-5 values
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
_NARRATIVE_META_RE = re.compile(
    r"\b(?:select(?:ed|ing|ion|s)?|identif(?:y|ies|ied|ying|ication)|"
    r"screen(?:ed|ing|s)?|evaluat(?:e|ed|es|ing|ion)|"
    r"qualif(?:y|ies|ied|ying|ication)|chosen|candidate)\b|"
    r"\b(?:method(?:ology)?|criteria|workflow)\b|"
    r"\b(?:align(?:s|ed|ing)?|fit(?:s|ted|ting)?|match(?:es|ed|ing)?)\s+"
    r"(?:(?:to|for|with)\s+)?(?:(?:a|the|this)\s+)?theme\b|"
    r"\b(?:is|are|was|were)\s+(?:a\s+)?(?:good\s+)?fit\s+for\s+"
    r"(?:the|this)\s+theme\b|"
    r"\b(?:consistent\s+with|thematically\s+(?:aligned|matched|suited))\b|"
    r"\b(?:strong\s+)?alignment\s+(?:to|with)\s+(?:the|this)\s+theme\b|"
    r"\b(?:a\s+)?thematic(?:al)?\s+(?:fit|match|alignment)\b|"
    r"\btheme\s+(?:fit|match|alignment)\b|"
    r"(?:筛选|评估|入选|被识别为|识别为|认定为|被选(?:为|中)?|遴选|评判|方法论|工作流程)|"
    r"(?:契合|符合|匹配|适合)(?:了)?(?:本|该|此|这一|这个|所述)?主题|"
    r"(?:与|同)?(?:本|该|此|这一|这个|所述)?主题(?:高度|较为|十分|非常|相)?"
    r"(?:契合|符合|匹配|适配|相符)",
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


def _norm_theme_direction(v) -> str:
    """Only an explicit bearish brief may activate inverse products."""
    return "bearish" if str(v or "").strip().lower() == "bearish" else "bullish"


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
        brief = self.llm.chat_json(prompts.EVENT_BRIEF_SYS, user)
        if not isinstance(brief, dict):
            brief = {}
        brief["theme_direction"] = _norm_theme_direction(brief.get("theme_direction"))
        return brief

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
            market_strength = _clamp_float(
                c.get("market_strength"), 0.0, 1.0, default=0.0)
            if c.get("static_theme_exposure") is not None:
                # ETF exposure is factual holdings/pool arithmetic from
                # etf_preselection.py; do not replace it with an LLM-derived score.
                base_exposure = _clamp_float(
                    c["static_theme_exposure"], 0.0, 1.0, default=0.0)
                c["theme_exposure_raw"] = min(
                    1.0, base_exposure * (1.0 + 0.10 * market_strength))
            else:
                c["theme_exposure_raw"] = scoring.theme_exposure(
                    c.get("ai_relevance", 1), c.get("exposure_type", "unclear"),
                    c.get("confidence", 0.0),
                    market_strength=market_strength,
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
        if (_NARRATIVE_INTERNAL_RE.search(text) or _NARRATIVE_META_RE.search(text)
                or _SNAKE_CASE_RE.search(text)):
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
    def _is_inverse_candidate(candidate: dict) -> bool:
        direction = str(candidate.get("direction") or "").strip().lower()
        return bool(
            candidate.get("is_inverse") or candidate.get("verified_inverse")
            or candidate.get("inverse_aligned") or direction == "short"
        )

    @staticmethod
    def _inverse_concentration_label(candidate: dict) -> str:
        """Return a factual concentration context, or an empty string."""
        if candidate.get("single_stock_inverse"):
            return "single underlying"
        labels = [
            str(label).strip()
            for label in candidate.get("pool_labels", [])
            if str(label).strip() and "inverse s&p 500" not in str(label).lower()
        ]
        if labels:
            return labels[0]
        broad_re = re.compile(
            r"\b(?:broad|large cap|total market|all cap|multi[ -]asset|global|world|"
            r"s&p 500|nasdaq[ -]?100)\b",
            re.IGNORECASE,
        )
        sector_re = re.compile(
            r"\b(?:sector|semiconductor|technology|financial|energy|biotech|health"
            r"care|industrial|materials|utilities|consumer|real estate|communication)\b",
            re.IGNORECASE,
        )
        for key in ("fund_niche", "fund_focus"):
            value = str(candidate.get(key) or "").strip()
            if value and sector_re.search(value):
                return value
            if (
                value
                and value.lower() not in {
                    "equity", "index", "market", "inverse", "leveraged", "short"
                }
                and not broad_re.search(value)
            ):
                return value
        for key in ("benchmark", "index_tracked"):
            value = str(candidate.get(key) or "").strip()
            if value and sector_re.search(value):
                return value
        return ""

    @staticmethod
    def _inverse_reference_values(candidate: dict) -> list[str]:
        """Return only supplied benchmark/underlying names suitable for narration."""
        benchmark = str(candidate.get("benchmark") or "").strip()
        if benchmark:
            return [benchmark.partition(":")[2] if ":" in benchmark else benchmark]
        tracked_index = str(candidate.get("index_tracked") or "").strip()
        if tracked_index:
            return [tracked_index]
        if not candidate.get("single_stock_inverse"):
            return []
        related = candidate.get("related_stock_codes") or []
        if isinstance(related, str):
            related = [related]
        values = []
        for code in related:
            ticker = str(code or "").partition(":")[2] or str(code or "")
            ticker = ticker.strip()
            if ticker and ticker not in values:
                values.append(ticker)
        if values:
            return values[:1]
        for holding in candidate.get("matched_holdings", []):
            ticker = str(
                holding.get("ticker")
                or str(holding.get("code") or "").partition(":")[2]
            ).strip()
            if ticker:
                return [ticker]
        return []

    @staticmethod
    def _multilingual(en: str, zh: str) -> dict:
        return {"type": "multilingual", "en": en, "zh": zh}

    @classmethod
    def _validated_candidate_rationale(cls, candidate: dict, value) -> dict | None:
        """Validate public prose, including required inverse-product risk facts."""
        rationale = cls._validated_theme_rationale(value)
        if rationale is None or not cls._is_inverse_candidate(candidate):
            return rationale

        en = rationale["en"].lower()
        zh = rationale["zh"]
        required_en = (
            r"\b(?:daily|one[ -]day)\b",
            r"\b(?:inverse|short exposure)\b",
            r"\breset\b",
            r"\bcompound(?:ing|ed)?\b",
            r"\bpath[ -]depend(?:ence|ent)\b",
        )
        if any(re.search(pattern, en) is None for pattern in required_en):
            return None
        if any(term not in zh for term in ("反向", "重置", "复利", "路径依赖")):
            return None
        if not ("单日" in zh or "每日" in zh):
            return None

        denial_en = re.compile(
            r"\b(?:eliminat\w*|remov\w*|avoid\w*|prevent\w*|neutraliz\w*|"
            r"harmless|no risk|without risk)\b",
            re.IGNORECASE,
        )
        if denial_en.search(en) or re.search(r"(?:消除|避免|无风险|没有风险|降低风险)", zh):
            return None
        path_risk_en = re.compile(
            r"\bpath[ -]depend(?:ence|ent)\b.{0,100}"
            r"\b(?:risk|diverg\w*|differ\w*|deviat\w*|loss\w*|volatil\w*)\b|"
            r"\b(?:risk|diverg\w*|differ\w*|deviat\w*|loss\w*|volatil\w*)\b"
            r".{0,100}\bpath[ -]depend(?:ence|ent)\b",
            re.IGNORECASE,
        )
        if path_risk_en.search(en) is None:
            return None
        if re.search(
                r"路径依赖.{0,60}(?:风险|偏离|差异|损失|波动)|"
                r"(?:风险|偏离|差异|损失|波动).{0,60}路径依赖", zh
        ) is None:
            return None

        try:
            multiple = float(candidate.get("leverage"))
        except (TypeError, ValueError):
            multiple = 1.0
        multiple_text = f"{max(1.0, min(3.0, multiple)):g}"
        if re.search(
                rf"(?<![\d.]){re.escape(multiple_text)}\s*(?:x|×|times?)(?!\w)", en
        ) is None:
            return None
        if re.search(
                rf"(?<![\d.]){re.escape(multiple_text)}\s*倍", zh
        ) is None:
            return None

        reference_values = cls._inverse_reference_values(candidate)
        if reference_values:
            en_flat = re.sub(r"[^a-z0-9]+", "", en.casefold())
            zh_flat = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", zh.casefold())
            reference_tokens = [
                re.sub(r"[^a-z0-9]+", "", value.casefold())
                for value in reference_values
            ]
            if not any(token and token in en_flat for token in reference_tokens):
                return None
            if not any(token and token in zh_flat for token in reference_tokens):
                return None
        else:
            if re.search(
                    r"\b(?:do not|does not|did not)\s+name\b|"
                    r"\b(?:not supplied|not provided|unavailable|unknown)\b",
                    en,
            ) is None or re.search(
                    r"(?:未列明|未提供|不可用|未知).{0,24}(?:基准|标的)|"
                    r"(?:基准|标的).{0,24}(?:未列明|未提供|不可用|未知)", zh
            ) is None:
                return None

        if cls._inverse_concentration_label(candidate):
            if re.search(
                    r"\bconcentrat\w*.{0,40}\brisk\b|\brisk\b.{0,40}\bconcentrat\w*",
                    en,
            ) is None or re.search(
                    r"集中.{0,24}风险|风险.{0,24}集中", zh
            ) is None:
                return None
        return rationale

    @classmethod
    def _fallback_theme_rationale(
        cls, candidate: dict, theme_direction: str = "bullish"
    ) -> dict:
        """Build bilingual, factual prose when LLM output is absent or unsafe."""
        name = str(candidate.get("name") or candidate.get("code") or "This security").strip()
        direction_mode = _norm_theme_direction(theme_direction)
        is_etf = candidate.get("static_theme_exposure") is not None
        if is_etf:
            is_inverse = cls._is_inverse_candidate(candidate)
            holding_tickers = []
            for holding in candidate.get("matched_holdings", []):
                ticker = str(
                    holding.get("ticker")
                    or str(holding.get("code") or "").partition(":")[2]
                ).strip()
                if ticker and ticker not in holding_tickers:
                    holding_tickers.append(ticker)

            related = candidate.get("related_stock_codes") or []
            if isinstance(related, str):
                related = [related]
            related_tickers = []
            for code in related:
                ticker = str(code or "").partition(":")[2] or str(code or "")
                ticker = ticker.strip()
                if ticker and ticker not in related_tickers:
                    related_tickers.append(ticker)

            benchmark = str(candidate.get("benchmark") or "").strip()
            benchmark_ticker = benchmark.partition(":")[2] if ":" in benchmark else benchmark
            tracked_index = str(candidate.get("index_tracked") or "").strip()

            if is_inverse:
                try:
                    leverage = float(candidate.get("leverage"))
                except (TypeError, ValueError):
                    leverage = 1.0
                leverage = max(1.0, min(3.0, leverage))
                multiple = f"{leverage:g}"
                single_stock = bool(candidate.get("single_stock_inverse"))
                reference_values = cls._inverse_reference_values(candidate)
                concentration_label = cls._inverse_concentration_label(candidate)
                if single_stock:
                    concentration_en = (
                        " A single-underlying mandate also creates concentration risk."
                    )
                    concentration_zh = "单一标的结构还带来集中度风险。"
                elif concentration_label:
                    focus = concentration_label
                    concentration_en = (
                        f" Its focus on {focus} also creates concentration risk."
                    )
                    concentration_zh = f"其对{focus}的集中配置还带来集中度风险。"
                else:
                    concentration_en = ""
                    concentration_zh = ""
                if reference_values:
                    reference = cls._human_join(reference_values)
                    reference_zh = cls._human_join_zh(reference_values)
                    objective_en = (
                        f"{name} seeks approximately {multiple} times the inverse of the daily "
                        f"return of {reference}."
                    )
                    objective_zh = (
                        f"{name}力求实现{reference_zh}单日收益的约{multiple}倍反向表现。"
                    )
                else:
                    objective_en = (
                        f"{name} seeks approximately {multiple} times daily inverse performance, "
                        "but the available fund facts do not name its reference benchmark or underlying."
                    )
                    objective_zh = (
                        f"{name}力求实现约{multiple}倍单日反向表现，但现有基金资料未列明其"
                        "参考基准或标的。"
                    )
                return cls._multilingual(
                    en=(
                        f"{objective_en} Daily reset, compounding, and path dependence can make "
                        "multi-day performance differ from a simple inverse multiple of the "
                        f"reference asset's cumulative move.{concentration_en}"
                    ),
                    zh=(
                        f"{objective_zh}每日重置、复利和路径依赖可能使其多日表现偏离参考标的"
                        f"累计涨跌幅的简单反向倍数。{concentration_zh}"
                    ),
                )

            if holding_tickers:
                examples = cls._human_join(holding_tickers[:3])
                examples_zh = cls._human_join_zh(holding_tickers[:3])
                if direction_mode == "bearish":
                    return cls._multilingual(
                        en=(
                            f"{name} holds securities such as {examples}. Under the downside "
                            "thesis, declines in these positions would reduce the fund's net asset "
                            "value; the sensitivity depends on portfolio weights that can change."
                        ),
                        zh=(
                            f"{name}持有{examples_zh}等证券。在下行情景下，这些持仓下跌会压低"
                            "基金净值；实际敏感度取决于可能发生变化的组合权重。"
                        ),
                    )
                return cls._multilingual(
                    en=(
                        f"{name} holds securities such as {examples}. Its sensitivity depends on "
                        "their current portfolio weights, which can change as the fund rebalances."
                    ),
                    zh=(
                        f"{name}持有{examples_zh}等证券。其实际敏感度取决于这些证券的当前"
                        "组合权重，而基金再平衡可能改变相关权重。"
                    ),
                )
            if related_tickers:
                examples = cls._human_join(related_tickers[:3])
                examples_zh = cls._human_join_zh(related_tickers[:3])
                return cls._multilingual(
                    en=(
                        f"{name} has a reported relationship to securities such as {examples}, but "
                        "no physical holding weight is available. The mandate and benchmark "
                        "determine how changes in those securities affect the fund."
                    ),
                    zh=(
                        f"{name}与{examples_zh}等证券存在已披露关联，但未提供该关联的"
                        "实物持仓权重。相关证券的变动如何影响基金取决于其投资范围与参考基准。"
                    ),
                )
            if benchmark_ticker or tracked_index:
                reference = benchmark_ticker or tracked_index
                if direction_mode == "bearish":
                    return cls._multilingual(
                        en=(
                            f"{name} references {reference}. Under the downside thesis, a long "
                            "mandate would lose value when that reference declines; tracking and "
                            "portfolio differences can change the magnitude."
                        ),
                        zh=(
                            f"{name}以{reference}为参考。若下行逻辑兑现，多头基金会在该基准"
                            "下跌时损失价值；跟踪方式与组合差异可能改变实际幅度。"
                        ),
                    )
                return cls._multilingual(
                    en=(
                        f"{name} references {reference}. Its return sensitivity depends on the "
                        "mandate, tracking method, and current portfolio."
                    ),
                    zh=(
                        f"{name}以{reference}为参考，其收益敏感度取决于投资范围、跟踪方式"
                        "与最新组合。"
                    ),
                )
            categories = [str(v).strip() for v in candidate.get("pool_labels", []) if str(v).strip()]
            if categories:
                category_text = cls._human_join(categories[:2])
                category_text_zh = cls._human_join_zh(categories[:2])
                if direction_mode == "bearish":
                    return cls._multilingual(
                        en=(
                            f"{name}'s fund categories include {category_text}. A long mandate can "
                            "lose value when assets affected by the downside thesis decline, while "
                            "current holdings determine the magnitude of that sensitivity."
                        ),
                        zh=(
                            f"{name}的基金类别包括{category_text_zh}。当受下行逻辑影响的资产"
                            "下跌时，多头基金可能损失价值，具体敏感度取决于最新持仓。"
                        ),
                    )
                return cls._multilingual(
                    en=(
                        f"{name}'s fund categories include {category_text}. Category labels alone do "
                        "not establish concentrated exposure; current holdings and the mandate "
                        "determine the fund's actual sensitivity."
                    ),
                    zh=(
                        f"{name}的基金类别包括{category_text_zh}。类别标签本身不能证明敞口"
                        "集中度，实际敏感度取决于最新持仓与基金投资范围。"
                    ),
                )
            if direction_mode == "bearish":
                return cls._multilingual(
                    en=(
                        f"Available fund data does not establish concentrated exposure for {name}. "
                        "As a long fund, its downside sensitivity depends on the current mandate "
                        "and holdings, which remain the principal limitation in the available data."
                    ),
                    zh=(
                        f"现有基金资料无法确认{name}是否具有集中敞口。作为多头基金，其下行"
                        "敏感度取决于最新投资范围与持仓，而现有资料在这两方面仍有限。"
                    ),
                )
            return cls._multilingual(
                en=(
                    f"Available fund data does not establish concentrated exposure for {name}. "
                    "Its current holdings and mandate determine its sensitivity to the affected assets."
                ),
                zh=(
                    f"现有基金资料无法确认{name}是否具有集中敞口，其对相关资产的实际敏感度"
                    "取决于最新持仓与基金投资范围。"
                ),
            )

        evidence = str(candidate.get("reason") or "").strip().rstrip(".")
        if cls._validated_rationale_text(evidence):
            if direction_mode == "bearish":
                return cls._multilingual(
                    en=(
                        f"{name} has reported product or business-segment exposure to the affected "
                        "market. If the downside pathway develops as described, that exposure could "
                        "pressure demand, revenue, earnings, margins, or valuation; available "
                        "disclosures do not quantify the sensitivity."
                    ),
                    zh=(
                        f"{name}在受影响市场拥有已披露的产品或业务分部敞口。若所述下行路径"
                        "兑现，该敞口可能对需求、收入、盈利、利润率或估值形成压力；现有资料"
                        "尚未量化敏感度。"
                    ),
                )
            return cls._multilingual(
                en=(
                    f"{name} has reported product or business-segment exposure to the affected "
                    "market. Changes in demand could affect revenue, earnings, margins, or "
                    "valuation, while available disclosures do not quantify the sensitivity."
                ),
                zh=(
                    f"{name}在受影响市场拥有已披露的产品或业务分部敞口。需求变化可能影响"
                    "收入、盈利、利润率或估值，但现有资料尚未量化敏感度。"
                ),
            )
        if direction_mode == "bearish":
            return cls._multilingual(
                en=(
                    f"Available evidence does not establish a specific downside pathway through a "
                    f"product or segment for {name}. Financial sensitivity remains unverified."
                ),
                zh=(
                    f"现有资料尚未建立{name}通过具体产品或业务分部承受下行影响的路径，"
                    "其财务敏感度仍未得到验证。"
                ),
            )
        return cls._multilingual(
            en=(
                f"Available evidence does not establish a specific product or segment link for {name}. "
                "The revenue pathway and financial materiality remain unverified."
            ),
            zh=(
                f"现有资料尚未建立{name}与具体产品或业务分部的联系，收入传导路径及财务"
                "重要性仍未得到验证。"
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

        direction = str(candidate.get("direction") or "").strip()
        if direction:
            record["fund direction"] = direction
        try:
            leverage = float(candidate.get("leverage"))
        except (TypeError, ValueError):
            leverage = None
        if leverage is not None:
            record["daily leverage multiple"] = leverage

        related = candidate.get("related_stock_codes") or []
        if isinstance(related, str):
            related = [related]
        related_tickers = []
        for code in related:
            ticker = str(code or "").partition(":")[2] or str(code or "")
            ticker = ticker.strip()
            if ticker and ticker not in related_tickers:
                related_tickers.append(ticker)
        if related_tickers:
            record["related underlying securities"] = related_tickers

        if candidate.get("single_stock_inverse"):
            record["single-underlying inverse mandate"] = True

        mandates = []
        for key in ("mandate", "investment_objective", "fund_strategy"):
            value = str(candidate.get(key) or "").strip()
            if value and value not in mandates:
                mandates.append(value)
        if mandates:
            record["fund mandate"] = mandates[0] if len(mandates) == 1 else mandates

        benchmark = str(candidate.get("benchmark") or "").strip()
        if benchmark:
            record["reference benchmark"] = benchmark
        tracked_index = str(candidate.get("index_tracked") or "").strip()
        if tracked_index:
            record["tracked index"] = tracked_index
        index_construction = str(candidate.get("selection_criteria") or "").strip()
        if index_construction:
            record["index construction facts"] = index_construction

        fund_context = []
        for key in ("fund_focus", "fund_niche"):
            value = str(candidate.get(key) or "").strip()
            if value and value not in fund_context:
                fund_context.append(value)
        if fund_context:
            record["fund context"] = fund_context

        categories = [str(v).strip() for v in candidate.get("pool_labels", []) if str(v).strip()]
        if categories:
            record["theme categories"] = categories
        return record

    def _assemble(self, chosen, narr, event_date, theme_direction="bullish"):
        self._assign_theme_exposure(chosen)
        items = []
        for c in chosen:
            n = narr.get(c["code"], {})
            rationale = self._validated_candidate_rationale(
                c, n.get("theme_rationale"))
            if rationale is None:
                rationale = self._fallback_theme_rationale(c, theme_direction)
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
        """Attach sign-neutral price-magnitude and volume confirmation signals."""
        thr = self.opts["rvol_threshold"]
        change_magnitudes = scoring.absolute_percentiles(
            [c.get("chg_pct") for c in cands])
        for c, magnitude in zip(cands, change_magnitudes):
            c["volume_confirmed"] = bool(
                c.get("rvol_event") is not None and c["rvol_event"] >= thr)
            c["change_magnitude_percentile"] = magnitude
            c["market_strength"] = 0.5 * magnitude + 0.5 * float(c["volume_confirmed"])
            # Retained for internal compatibility; scoring uses market_strength.
            c["market_confirmed"] = c["market_strength"] > 0

    # -- stage 7 -------------------------------------------------------------
    def narrate(self, inp: dict, brief: dict, chosen: list[dict], kind: str) -> dict:
        records = [self._narrative_record(c, kind) for c in chosen]
        user = prompts.NARRATIVE_USER.format(
            theme=inp["theme"], summary=brief.get("summary", ""),
            thesis=brief.get("thesis", ""), as_of=self.as_of,
            theme_direction=_norm_theme_direction(brief.get("theme_direction")),
            kind=kind, records=json.dumps(records, ensure_ascii=False))
        try:
            arr = self.llm.chat_json(prompts.NARRATIVE_SYS, user)
        except (ValueError, RuntimeError):
            arr = []
        if not isinstance(arr, list):
            arr = []
        output = {}
        candidate_by_code = {str(candidate.get("code") or ""): candidate for candidate in chosen}
        for obj in arr:
            if not isinstance(obj, dict):
                continue
            code = str(obj.get("market_code") or "")
            candidate = candidate_by_code.get(code)
            rationale = (
                self._validated_candidate_rationale(
                    candidate, obj.get("theme_rationale"))
                if candidate is not None else None
            )
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
        theme_direction = _norm_theme_direction(brief.get("theme_direction"))
        _log(f"event brief ready direction={theme_direction}")

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
            theme_direction=theme_direction,
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
            "ThemeStocks": self._assemble(
                top_stocks, stock_narr, inp["date"], theme_direction),
            "ThemeEtfs": self._assemble(
                top_etfs, etf_narr, inp["date"], theme_direction),
            "ThemeFAQ": faq,
        }
