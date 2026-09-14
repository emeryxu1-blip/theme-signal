"""Orchestration for the theme-investing agentic workflow.

Pipeline:
  1. validate input + fetch article
  2. freeze a theme-only profile, then analyze the article as secondary evidence
  3. screen and freeze an exact stock basket, using progressively weaker real
     securities when strict relationship evidence is sparse
  4. discover, assess, recover, and freeze an exact ETF basket
  5. fetch finalist market facts without changing either basket
  6. generate same-security broker rationales, falling back deterministically
  7. apply display scores + SEO FAQ and assemble the unchanged public shape
"""

from __future__ import annotations

import datetime as _dt
from difflib import SequenceMatcher
import hashlib
import json
import math
import re
import sys
import time
import unicodedata
from collections.abc import Iterable
from collections import deque
from itertools import islice

import prompts
import scoring
from ainvest_client import AInvestClient
from article import fetch_article
from etf_preselection import (PreselectionResult, compose_output_etfs, preselect_etfs,
                              requires_component_holdings,
                              rerank_with_component_holdings,
                              select_output_etfs)
from llm_client import LLMClient
from universe_fallbacks import extend_etf_candidates

# universe pool identifiers (validated against local AInvest references)
STOCK_POOL = {"type": "block_id", "value": ["C191"]}  # all US stocks
MKTCAP_ID = "total_market_value"

DEFAULTS = dict(
    # Stocks: top-N market-cap boundary. ETFs: maximum unique candidates produced
    # by finite theme pools / selected-stock holding relationships.
    stock_universe=500,       # top-N stocks by market cap the scan may walk
    stock_candidate_budget=120,  # semantic work budget after cheap universe fetch
    stock_theme_entity_lane=20,    # resolved entities proposed by the theme-only pass
    stock_article_anchor_lane=20,  # resolved title/first-1,500-character mentions
    stock_broad_lane=20,      # largest liquid names retained regardless of taxonomy
    etf_universe=500,         # maximum theme-derived ETF candidates to rank
    etf_theme_pools=4,        # maximum statically matched curated pools
    max_scan=None,            # optional extra safety ceiling; 0/None disables it
    relevance_batch=10,       # bounded structured output fits the 4k response budget
    etf_holding_relevance_batch=20,   # same guard for component-company scoring
    etf_holding_relevance_retry_budget=100,  # cap recovery after sparse/transient batches
    etf_holdings_unique_budget=5000,  # hard cap on deduplicated component companies scored
    etf_holdings_portfolio_budget=40, # maximum equity ETF portfolios assessed per run
    etf_stock_evidence_portfolio_budget=30,
    etf_pool_diversity_portfolio_budget=10,
    etf_evidence_stock_limit=30,      # internal qualified stocks retained as ETF evidence
    stock_target=8,           # exact ranked public-stock count (0 disables)
    etf_target=5,             # exact ranked ETF count (0 disables)
    relevance_threshold=3.3,  # prompt rubric: <3 is marginal/speculative
    min_relevance_confidence=0.55,
    etf_min_theme_score=0.25, # strict-evidence gate; weaker CE tiers fill exact output
    kline_count=90,           # enough daily bars for the PRE-event baseline window
    baseline_lookback=20,     # pre-event bars used to compute the median volume baseline
    min_history=5,            # minimum pre-event bars required before baseline is trusted
    rvol_threshold=1.5,       # event-window peak RVOL that earns a "volume-confirmed" badge
    benchmark="169:SPY",      # market benchmark for abnormal-return calculation
)

THEME_SCORE_WEIGHT = 0.80
ARTICLE_SCORE_WEIGHT = 0.20
ARTICLE_ANCHOR_TEXT_CHARS = 1500
ARTICLE_BODY_TEXT_CHARS = 12000
_US_STOCK_MARKETS = {"169", "170", "171", "185", "186"}
_SECURITY_CODE_RE = re.compile(r"\d+:[A-Za-z0-9.\-]+")
_ENTITY_ROLES = {
    "pure_play_operator", "direct_operator", "enabler", "supply_chain",
    "beneficiary", "direct", "supplier", "customer", "partner",
    "competitor", "complementary", "second_order",
}


class StockRationaleError(RuntimeError):
    """Legacy import-compatible error; production narration no longer raises it."""

    def __init__(
        self,
        message: str,
        *,
        missing_codes: Iterable[str] = (),
        rejections: dict[str, str] | None = None,
    ) -> None:
        super().__init__(message)
        self.missing_codes = tuple(
            str(code) for code in missing_codes if str(code).strip()
        )
        self.rejections = dict(rejections or {})
        self.rejected_codes = tuple(self.rejections)


class StockUniverseError(RuntimeError):
    """Legacy base error for an unavailable or exhausted stock universe."""


class SelectionUniverseError(StockUniverseError):
    """Raised when an asset's real, deduplicated selection universe is too small."""

    def __init__(
        self,
        asset_class: str,
        expected: int,
        available: int,
        sources: Iterable[str] = (),
    ) -> None:
        self.asset_class = str(asset_class or "security").strip().lower()
        self.expected = int(expected)
        self.available = int(available)
        self.sources = tuple(dict.fromkeys(
            str(source).strip() for source in sources if str(source).strip()
        ))
        source_text = ",".join(self.sources) or "none"
        super().__init__(
            f"{self.asset_class} selection universe exhausted: "
            f"expected={self.expected} available={self.available} "
            f"sources={source_text}"
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
                   "factor_proxy", "diversified", "unclear"}
_THEME_SPECIFICITIES = {"company_specific", "industry_specific", "broad_factor", "none"}
_MATERIALITIES = {"high", "medium", "low", "unknown"}
_EVIDENCE_STRENGTHS = {"explicit", "derived", "speculative", "none"}
_IMPACT_CHANNELS = {
    "revenue_demand", "input_cost_margin", "financing_sensitive_demand",
    "supply_chain_orders", "policy_or_regulatory", "industry_valuation",
    "valuation_only", "market_beta", "none",
}
_PUBLIC_RELATION_TYPES = {
    "direct", "supplier", "customer", "partner", "competitor",
    "complementary", "second_order", "none",
}
_DIRECTIONAL_EFFECTS = {"positive", "negative", "mixed", "none"}
_RELATION_EVIDENCE_BASES = {
    "article", "company_profile", "combined", "derived", "none",
}
_EVENT_RELATION_ROLES = {
    "direct", "supplier", "customer", "partner", "competitor",
    "complementary", "second_order",
}


def _event_ecosystem_response_schema(limit: int) -> dict:
    """Strict discovery schema; identities are still resolved by the application."""
    entity = {
        "type": "object",
        "properties": {
            "name": {"type": ["string", "null"]},
            "ticker": {"type": ["string", "null"]},
            "market_code": {"type": ["string", "null"]},
            "role": {"type": "string", "enum": sorted(_EVENT_RELATION_ROLES)},
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "operating_evidence": {"type": "string"},
        },
        "required": [
            "name", "ticker", "market_code", "role", "confidence",
            "operating_evidence",
        ],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "entities": {
                "type": "array", "minItems": 0, "maxItems": limit,
                "items": entity,
            },
        },
        "required": ["entities"],
        "additionalProperties": False,
    }


def _narrative_response_schema(batch: list[dict]) -> dict:
    """Require one identity-preserving bilingual rationale per frozen record."""
    identifiers = [str(row["candidate_id"]) for row in batch]
    market_codes = [str(row["code"]) for row in batch]
    rationale = {
        "type": "object",
        "properties": {
            "type": {"type": "string", "const": "multilingual"},
            "en": {"type": "string"},
            "zh": {"type": "string"},
        },
        "required": ["type", "en", "zh"],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {
            "items": {
                "type": "array",
                "minItems": len(batch),
                "maxItems": len(batch),
                "items": {
                    "type": "object",
                    "properties": {
                        "candidate_id": {
                            "type": "string", "enum": identifiers,
                        },
                        "market_code": {
                            "type": "string", "enum": market_codes,
                        },
                        "theme_rationale": rationale,
                    },
                    "required": [
                        "candidate_id", "market_code", "theme_rationale",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["items"],
        "additionalProperties": False,
    }


def _relevance_response_schema(batch: list[dict], *, stock: bool) -> dict:
    """Strict schema that makes one scored row per application-owned candidate."""
    identifiers = [str(row["candidate_id"]) for row in batch]
    market_codes = [str(row["code"]) for row in batch]
    properties = {
        "candidate_id": {"type": "string", "enum": identifiers},
        "market_code": {"type": "string", "enum": market_codes},
        "exposure_type": {"type": "string", "enum": sorted(_EXPOSURE_TYPES)},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
        "impact_channel": {"type": "string", "enum": sorted(_IMPACT_CHANNELS)},
        "theme_specificity": {
            "type": "string", "enum": sorted(_THEME_SPECIFICITIES),
        },
        "materiality": {"type": "string", "enum": sorted(_MATERIALITIES)},
        "evidence_strength": {
            "type": "string", "enum": sorted(_EVIDENCE_STRENGTHS),
        },
        "reason": {"type": "string"},
    }
    if stock:
        properties.update({
            "theme_relevance": {"type": "number", "minimum": 1, "maximum": 5},
            "article_support": {"type": "number", "minimum": 0, "maximum": 1},
            "article_reason": {"type": "string"},
            "public_relation_score": {
                "type": "number", "minimum": 1, "maximum": 5,
            },
            "public_relation_confidence": {
                "type": "number", "minimum": 0, "maximum": 1,
            },
            "relation_type": {
                "type": "string", "enum": sorted(_PUBLIC_RELATION_TYPES),
            },
            "directional_effect": {
                "type": "string", "enum": sorted(_DIRECTIONAL_EFFECTS),
            },
            "business_fact": {"type": "string"},
            "theme_connection": {"type": "string"},
            "financial_pathway": {"type": "string"},
            "evidence_basis": {
                "type": "string", "enum": sorted(_RELATION_EVIDENCE_BASES),
            },
        })
    else:
        properties["ai_relevance"] = {
            "type": "number", "minimum": 1, "maximum": 5,
        }
    return {
        "type": "object",
        "properties": {
            "results": {
                "type": "array",
                "minItems": len(batch),
                "maxItems": len(batch),
                "items": {
                    "type": "object",
                    "properties": properties,
                    "required": list(properties),
                    "additionalProperties": False,
                },
            },
        },
        "required": ["results"],
        "additionalProperties": False,
    }
_SPECIFICITY_FACTOR = {
    "company_specific": 1.0,
    "industry_specific": 0.9,
    "broad_factor": 0.15,
    "none": 0.0,
}
_MATERIALITY_FACTOR = {"high": 1.0, "medium": 0.85, "unknown": 0.55, "low": 0.35}
_EVIDENCE_FACTOR = {"explicit": 1.0, "derived": 0.85, "speculative": 0.30, "none": 0.0}
_BROAD_FACTOR_REASON_RE = re.compile(
    r"\b(?:discount rates?|duration|long[ -]duration|risk[ -]on|market beta|"
    r"multiple expansion|technology multiples?|present value|cost of capital|"
    r"falling (?:treasury )?yields?|lower (?:treasury )?yields?|valuation support|"
    r"valuation uplift|(?:lower|falling) yields? lift|broad financial conditions|"
    r"nasdaq|s&p(?: 500)?|market rally|shares? (?:rise|rally|climb)\w* with|"
    r"risk appetite)\b",
    re.IGNORECASE,
)

_STOCK_LANE_BRIEF_FIELDS = (
    "primary_shock", "primary_transmission_channels", "direct_beneficiaries",
    "picks_and_shovels", "etf_exposure_terms",
)
_STOCK_DIRECT_LANE_BRIEF_FIELDS = (
    "direct_beneficiaries", "picks_and_shovels",
)
# Event-like labels describe a move rather than an operating taxonomy. These
# aliases are recall hints only: they widen the scored candidate set but never
# grant eligibility or imply that a company is exposed to the theme.
_EVENT_ASSET_RECALL_HINTS = (
    (re.compile(r"\bbitcoin\b|\bbtc\b|比特币", re.IGNORECASE), (
        "bitcoin", "btc", "cryptocurrency", "digital asset", "crypto mining",
        "bitcoin mining", "blockchain", "gpu mining",
    )),
)
_STOCK_LANE_STOPWORDS = {
    "a", "an", "and", "business", "businesses", "companies", "company",
    "corporation", "corporations", "industry", "industries", "market", "markets",
    "of", "or", "product", "products", "sector", "sectors", "service", "services",
    "the", "to", "with",
}
_CJK_BUSINESS_CONCEPT_RE = {
    "homebuilder": re.compile(
        r"房屋建筑商|住宅建筑|住宅建设|住宅建造|住宅开发|房屋建造|建造(?:和销售)?房屋"
    ),
    "housing": re.compile(r"住宅建筑|住宅建设|住宅建造|住宅开发|房屋建造|住房建设"),
    "residential": re.compile(r"住宅|住房|公寓"),
    "mortgage": re.compile(r"抵押贷款|按揭|房贷"),
    "build": re.compile(r"建筑产品|建筑材料|建材|暖通|空调|屋顶|涂料"),
    "construction": re.compile(r"建筑施工|工程建设|承包工程|建筑工程"),
    "retail": re.compile(r"零售"),
}
_CJK_GENERIC_LANE_FRAGMENTS = {
    "公司", "企业", "行业", "市场", "产品", "服务", "业务", "相关",
    "主要", "从事", "以及", "生产商", "制造商", "提供商", "运营商",
}

_NARRATIVE_INTERNAL_RE = re.compile(
    r"\b(?:ai_relevance|exposure_type|theme_exposure_raw|static_theme_exposure|"
    r"preselect_score|score_components|event_to_today_change_pct|abnormal_return|"
    r"volume_confirmed|relative_volume|theme_stock_breadth|"
    r"aggregate_theme_holding_weight_pct|weighted_theme_exposure_pct|pool_strength|"
    r"rvol|relevance|confidence|ranking|ranked|threshold|percentile|score|scoring)\b|"
    r"\brelative volume\b|\babnormal return\b|\bevent[- ]to[- ]today\b|"
    r"\bvolume[- ]confirmed\b|\bselection mechanics\b|"
    r"\b(?:internal theme direction|theme direction|required investment direction|"
    r"investment direction|relationship type|directional effect|market code|"
    r"theme rationale)\b|"
    r"\b(?:pass(?:ed|es)?|fail(?:ed|s)?)\s+(?:the\s+)?(?:\w+\s+){0,3}gate\b|"
    r"\bpublic relationship gate\b|"
    r"\b(?:aggregate|combined|total|weighted)\s+(?:theme\s+)?(?:exposure|holdings?|weight)\b|"
    r"\d+(?:\.\d+)?%\s*(?:\+|=|×)|"
    r"(?:相关性|相关度)\s*(?:评分|得分)?\s*[:：=]?\s*\d|"
    r"内部(?:评分|得分|排名|计算|变量|字段)|置信度|阈值|百分位|"
    r"关系类型|方向性影响|方向影响|所需投资方向|投资方向|内部主题方向|主题方向|"
    r"市场代码|主题理由|主题逻辑字段|通过.{0,10}(?:门槛|关卡)|"
    r"相对成交量|异常收益|加权主题敞口|总主题敞口",
    re.IGNORECASE,
)
_NARRATIVE_META_RE = re.compile(
    r"\b(?:select(?:ed|ing|ion|s)?|identif(?:y|ies|ied|ying|ication)|"
    r"screen(?:ed|ing|s)?|evaluat(?:e|ed|es|ing|ion)|"
    r"qualif(?:y|ies|ied|ying|ication)|chosen|candidate|"
    r"eligible|ineligible|admit(?:ted|ting|s)?|admission)\b|"
    r"\b(?:method(?:ology)?|criteria|workflow)\b|"
    r"\b(?:fallback|evidence)\s+(?:tier|lane|gate)\b|"
    r"\b(?:tier|lane)\s+(?:\d+|one|two|three|four|five|six)\b|"
    r"\b(?:membership|frozen (?:membership|basket|list|selection))\b|"
    r"\bshortlist(?:ed|ing|s)?\b|\bmade (?:it to )?the final cut\b|"
    r"\bbelongs? in (?:the |our )?(?:final )?(?:basket|portfolio|list)\b|"
    r"\b(?:one of )?our picks?\b|"
    r"\b(?:includ(?:e|ed)|add(?:ed)?|plac(?:e|ed)|pick(?:ed)?)\s+"
    r"(?:in|into|for|to)\s+(?:the\s+)?(?:final\s+|frozen\s+|selected\s+)?"
    r"(?:basket|list|output|selection)\b|"
    r"\b(?:align(?:s|ed|ing)?|fit(?:s|ted|ting)?|match(?:es|ed|ing)?)\s+"
    r"(?:(?:to|for|with)\s+)?(?:(?:a|the|this)\s+)?theme\b|"
    r"\b(?:is|are|was|were)\s+(?:a\s+)?(?:good\s+)?fit\s+for\s+"
    r"(?:the|this)\s+theme\b|"
    r"\b(?:consistent\s+with|thematically\s+(?:aligned|matched|suited))\b|"
    r"\b(?:strong\s+)?alignment\s+(?:to|with)\s+(?:the|this)\s+theme\b|"
    r"\b(?:a\s+)?thematic(?:al)?\s+(?:fit|match|alignment)\b|"
    r"\btheme\s+(?:fit|match|alignment)\b|"
    r"(?:筛选|评估|入选|被识别为|识别为|认定为|被选(?:为|中)?|遴选|评判|方法论|工作流程|"
    r"成员资格|最终(?:篮子|组合|名单|候选股)|候选股|成功上榜|冻结(?:名单|篮子|选择)|排名|"
    r"(?:被)?(?:纳入|列入).{0,8}(?:最终)?(?:组合|篮子|名单|输出|选择))|"
    r"(?:契合|符合|匹配|适合)(?:了)?(?:本|该|此|这一|这个|所述)?主题|"
    r"(?:与|同)?(?:本|该|此|这一|这个|所述)?主题(?:高度|较为|十分|非常|相)?"
    r"(?:契合|符合|匹配|适配|相符)",
    re.IGNORECASE,
)
_NARRATIVE_BOILERPLATE_RE = re.compile(
    r"\bavailable disclosures do not quantify (?:the )?sensitivity\b|"
    r"\bavailable disclosures (?:are|remain) (?:too )?limited\b|"
    r"\bcould affect revenue, earnings, margins, or valuation\b|"
    r"\bsecondary (?:downside )?watchlist\b|"
    r"\b(?:lower[- ]conviction )?watchlist (?:name|idea)\b|"
    r"\b(?:watchlist name|watchlist idea) rather than (?:a )?core trade\b|"
    r"\bnot (?:a )?(?:high[- ]?)?conviction (?:theme )?(?:trade|name|idea)\b|"
    r"\b(?:clear )?(?:earnings )?catalyst (?:still )?(?:needs? to|has yet to) emerge\b|"
    r"\b(?:still )?needs? to emerge\b|"
    r"\bsole company directly governed\b|"
    r"\bstronger theme demand\b|"
    r"\b(?:no|without(?: creating)?) (?:a )?direct (?:summit|event)(?:[- ]service)? "
    r"(?:operating )?(?:role|exposure)\b|"
    r"\bnot (?:directly )?tied to (?:the )?(?:summit|event)(?:'s)? operations\b|"
    r"\brather than (?:summit|event)[- ]related revenue\b|"
    r"\bnot actionable (?:as|on) (?:a |the )?(?:theme trade|downside)\b|"
    r"现有(?:资料|披露).{0,12}(?:尚未|未).{0,4}量化.{0,4}(?:敏感度|财务影响)|"
    r"次级(?:下行)?观察标的|非高确信度主题交易|仍需.{0,8}(?:出现|看到)|"
    r"唯一直接受影响的公司|更强的主题需求|"
    r"(?:不|并不)(?:承担|参与|具备).{0,12}(?:峰会|活动).{0,12}(?:运营|角色|敞口)|"
    r"不会形成.{0,8}(?:峰会|活动).{0,12}敞口|而非带来(?:峰会|活动)相关收入|"
    r"不具备可操作的(?:主题|下行)逻辑|"
    r"(?:披露|资料)(?:仍然|仍|依然)?有限|观察名单(?:标的|名称|想法)?|"
    r"(?:盈利)?催化剂(?:尚未|仍未|还未)出现|非(?:高)?确信度(?:主题)?(?:交易|标的)",
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
        value = float(v)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value):
        return default
    return max(lo, min(hi, value))


def _normalised_text(value) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value or ""))).strip()


def _string_list(value, *, limit: int = 40) -> list[str]:
    if not isinstance(value, list):
        return []
    output: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _normalised_text(item)
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        output.append(text[:500])
        if len(output) >= limit:
            break
    return output


def _normalise_entity(value, *, source: str) -> dict | None:
    if not isinstance(value, dict):
        return None
    market_code = _normalised_text(value.get("market_code")).upper()
    ticker = _normalised_text(value.get("ticker")).upper()
    name = _normalised_text(value.get("name"))
    if not any((market_code, ticker, name)):
        return None
    role = _normalised_text(value.get("role")).lower()
    if role not in _ENTITY_ROLES:
        role = "direct_operator" if source == "theme_profile" else "beneficiary"
    return {
        "market_code": market_code or None,
        "ticker": ticker or None,
        "name": name or None,
        "role": role,
        "confidence": _clamp_float(
            value.get("confidence"), 0.0, 1.0,
            default=0.5 if source == "theme_profile" else 1.0,
        ),
        "article_role": _normalised_text(value.get("article_role")).lower() or None,
        "market_hint": _normalised_text(
            value.get("exchange") or value.get("market_hint") or value.get("market")
        ) or None,
        "operating_evidence": _normalised_text(value.get("operating_evidence"))[:500],
        "source": source,
    }


def _article_anchor_region(article: dict) -> str:
    """Title plus the first 1,500 normalized article characters."""
    title = _normalised_text(article.get("title"))
    lede = _normalised_text(article.get("text"))[:ARTICLE_ANCHOR_TEXT_CHARS]
    return "\n".join(part for part in (title, lede) if part)


def _entity_is_mentioned(entity: dict, region: str) -> bool:
    """Require a literal title/lede identity before granting an output guarantee."""
    normalised_region = _normalised_text(region)
    if not normalised_region:
        return False
    ticker = _normalised_text(entity.get("ticker"))
    if ticker:
        escaped_ticker = re.escape(ticker)
        explicit_ticker = re.search(
            rf"(?:\${escaped_ticker}(?![A-Za-z0-9])|"
            rf"\((?:NASDAQ|NYSE|AMEX)\s*:\s*{escaped_ticker}\)|"
            rf"\b(?:NASDAQ|NYSE|AMEX)\s*:\s*{escaped_ticker}(?![A-Za-z0-9]))",
            normalised_region,
            re.IGNORECASE,
        )
        # Bare symbols can also be ordinary words (ON, AI, NOW, ALL, etc.).
        # Require explicit market notation; the issuer name can establish the
        # identity normally below.
        if explicit_ticker:
            return True
    name = _normalised_text(entity.get("name"))
    if not name:
        return False

    # Legal punctuation is not identity-bearing: ``CoreWeave, Inc.`` should also
    # validate a literal ``CoreWeave`` mention. Match Latin names on token
    # boundaries so short brands such as Meta or IBM cannot match metadata or
    # pineapple. CJK names have no whitespace boundary convention, so retain a
    # conservative literal substring check after punctuation normalization.
    legal_name = _normalised_text(re.sub(r"[,.;:]+", " ", name))
    variants = [legal_name]
    stripped = re.sub(
        r"\s+(?:group|holdings?|technolog(?:y|ies)|incorporated|inc\.?|corporation|corp\.?|"
        r"company|co\.?|plc|limited|ltd\.?)$",
        "", legal_name, flags=re.IGNORECASE,
    ).strip()
    if stripped and stripped.casefold() != legal_name.casefold():
        variants.append(stripped)
    haystack_original = re.sub(
        r"[^A-Za-z0-9\u4e00-\u9fff]+", " ", normalised_region,
    ).strip()
    haystack = haystack_original.casefold()

    def single_word_company_context(word: str) -> bool:
        """Require syntax that treats a one-word brand as an issuer.

        Capitalization alone is insufficient at a sentence boundary: ordinary
        imperatives such as ``Toast the success ...`` must not become evidence
        for Toast, Inc. This intentionally fails closed on context-free brand
        tokens while retaining normal corporate prose (``Micron sells ...``,
        ``Apple's roadmap ...``, ``shares of Meta ...``).
        """
        token_pattern = re.compile(
            rf"(?<![A-Za-z0-9]){re.escape(word)}(?![A-Za-z0-9])"
        )
        action = re.compile(
            r"^\s*(?:[,;:]\s*)?(?:"
            r"acquir\w*|agree\w*|announc\w*|appoint\w*|build\w*|"
            r"collaborat\w*|cut\w*|design\w*|develop\w*|expand\w*|"
            r"expect\w*|forecast\w*|grew|grow\w*|invest\w*|launch\w*|"
            r"manufactur\w*|name\w*|offer\w*|operat\w*|partner\w*|"
            r"plan\w*|post\w*|produc\w*|provid\w*|rais\w*|report\w*|"
            r"said|says|sell\w*|serve\w*|sign\w*|suppl\w*|unveil\w*|"
            r"use\w*"
            r")\b",
            re.IGNORECASE,
        )
        corporate_noun = re.compile(
            r"^\s*(?:[,;:]\s*)?(?:Inc\.?|Incorporated|Corp\.?|Corporation|"
            r"Company|Co\.?|Group|Holdings?|Ltd\.?|PLC)\b",
            re.IGNORECASE,
        )
        corporate_prefix = re.compile(
            r"(?:shares? of|stock in|issuer|the company|CEO of|CFO of|"
            r"agreement with|collaboration with|contract with|deal with|"
            r"partnership with)\s*$",
            re.IGNORECASE,
        )
        coordinated_subject = re.compile(
            r"^\s*(?:and|&)\s+"
            r"[A-Z][A-Za-z0-9&.'’-]*(?:\s+[A-Z][A-Za-z0-9&.'’-]*){0,3}\s+"
            r"(?:announc\w*|build\w*|develop\w*|expand\w*|lead\w*|"
            r"operat\w*|plan\w*|report\w*|sell\w*|suppl\w*)\b"
        )
        for match in token_pattern.finditer(normalised_region):
            before = normalised_region[:match.start()]
            after = normalised_region[match.end():]
            if re.match(r"^\s*['’]s\b", after, re.IGNORECASE):
                return True
            if (action.match(after) or corporate_noun.match(after)
                    or coordinated_subject.match(after)):
                return True
            if corporate_prefix.search(before):
                return True
        return False

    for variant in variants:
        needle_original = re.sub(
            r"[^A-Za-z0-9\u4e00-\u9fff]+", " ", variant,
        ).strip()
        needle = needle_original.casefold()
        if not needle:
            continue
        if re.search(r"[a-z0-9]", needle):
            # A one-word issuer is only an identity when its brand casing is
            # literal. This prevents ordinary prose such as "Apple strategy"
            # from being treated as a mention of Strategy/MSTR.
            single_word = " " not in needle_original
            if single_word and needle in _AMBIGUOUS_ISSUER_NAMES:
                legal_pattern = (
                    re.escape(needle_original)
                    + r"\s+(?:Co\.?|Company|Corp\.?|Corporation|Inc\.?|"
                    r"Incorporated|Ltd\.?|PLC)\b"
                )
                if re.search(
                    rf"(?<![A-Za-z0-9]){legal_pattern}",
                    haystack_original,
                    re.IGNORECASE,
                ):
                    return True
                continue
            if single_word:
                if single_word_company_context(needle_original):
                    return True
                continue
            source = haystack_original if single_word else haystack
            expected = needle_original if single_word else needle
            pattern = re.escape(expected).replace(r"\ ", r"\s+")
            if re.search(rf"(?<![A-Za-z0-9]){pattern}(?![A-Za-z0-9])", source):
                return True
        elif len(needle) >= 2 and needle in haystack:
            return True
    return False


def _structured_entity_reference_is_mentioned(entity: dict, text: str) -> bool:
    """Bind a resolved event entity to a structured relationship claim.

    Raw article identity remains subject to ``_entity_is_mentioned``'s strict
    issuer syntax.  A model-authored relationship field is different: the
    entity has already been resolved, and coordinated prose commonly shortens
    ``Nebius Group`` to ``Nebius`` or writes ``CoreWeave and Nebius expansion``.
    Permit that distinctive stem only beside an operating/relationship context;
    an ordinary imperative such as ``Toast the success`` still cannot bind the
    listed issuer.
    """
    normalised_text = _normalised_text(text)
    if not normalised_text:
        return False
    if _entity_is_mentioned(entity, normalised_text):
        return True

    name = _normalised_text(entity.get("name"))
    if not name:
        return False
    stem = re.sub(
        r"[,.;:]*(?:\s+(?:group|holdings?|technolog(?:y|ies)|incorporated|"
        r"inc\.?|corporation|corp\.?|company|co\.?|plc|limited|ltd\.?))$",
        "", name, flags=re.IGNORECASE,
    ).strip()
    # The fallback exists for a shortened, single distinctive brand. Full
    # multi-word names are already handled literally by _entity_is_mentioned.
    if not stem or " " in stem or stem.casefold() in _AMBIGUOUS_ISSUER_NAMES:
        return False

    operating_noun = (
        r"agreement|alliance|capacity|collaboration|components?|contract|"
        r"customer|demand|devices?|distribution|expansion|integration|orders?|"
        r"partnership|products?|programs?|revenue|roadmap|sales|services?|"
        r"shipments?|supplier|supply|usage|utilization|volumes?"
    )
    action = (
        r"acquir\w*|announc\w*|build\w*|collaborat\w*|develop\w*|expand\w*|"
        r"manufactur\w*|operat\w*|partner\w*|produc\w*|provid\w*|report\w*|"
        r"sell\w*|sign\w*|suppl\w*|support\w*|use\w*"
    )
    direct_context = re.compile(
        rf"^\s*(?:['’]s\b|[,;:]?\s*(?:[A-Za-z0-9][A-Za-z0-9-]*\s+)"
        rf"{{0,3}}(?:{operating_noun})\b|[,;:]?\s*(?:{action})\b)",
        re.IGNORECASE,
    )
    coordinated_context = re.compile(
        rf"^\s*(?:and|&)\s+"
        rf"(?:(?-i:[A-Z])[A-Za-z0-9&'’-]*\s+){{1,4}}"
        rf"(?i:{operating_noun}|{action})\b",
    )
    prefix_context = re.compile(
        r"(?:agreement|alliance|collaboration|contract|customer|deal|"
        r"distribution|partnership|supplier)\s+(?:for|from|to|with)\s*$",
        re.IGNORECASE,
    )
    token_pattern = re.compile(
        rf"(?<![A-Za-z0-9]){re.escape(stem)}(?![A-Za-z0-9])"
    )
    for match in token_pattern.finditer(normalised_text):
        before = normalised_text[:match.start()]
        after = normalised_text[match.end():]
        if (direct_context.match(after) or coordinated_context.match(after)
                or prefix_context.search(before)):
            return True
    return False


def _theme_profile_terms(theme: str, profile: dict) -> list[str]:
    """Article-independent vocabulary used solely for candidate recall."""
    terms = [_normalised_text(theme)]
    theme_text = _normalised_text(theme)
    for pattern, aliases in _EVENT_ASSET_RECALL_HINTS:
        if pattern.search(theme_text):
            terms.extend(aliases)
    for field in (
        "canonical_name", "canonical_definition", "aliases",
        "direct_business_models", "pure_play_descriptors", "enablers",
    ):
        value = profile.get(field)
        if isinstance(value, list):
            terms.extend(_string_list(value))
        elif value:
            terms.append(_normalised_text(value))
    output: list[str] = []
    seen: set[str] = set()
    for term in terms:
        key = term.casefold()
        if term and key not in seen:
            seen.add(key)
            output.append(term)
    return output


def _norm_exposure(v) -> str:
    v = str(v or "").strip().lower()
    return v if v in _EXPOSURE_TYPES else "unclear"


def _norm_choice(value, allowed: set[str], default: str) -> str:
    value = str(value or "").strip().lower()
    return value if value in allowed else default


def _lane_stem(token: str) -> str:
    """Small deterministic stemmer for taxonomy/brief candidate generation."""
    token = token.casefold()
    if token == "macs":
        return "mac"
    if token in {"building", "builder", "builders"}:
        return "build"
    if token in {"homebuilder", "homebuilders", "homebuilding"}:
        return "homebuilder"
    if token in {"house", "houses", "housing"}:
        return "housing"
    if token in {"licence", "licenced", "licences", "licensing",
                 "license", "licensed", "licenses"}:
        return "licens"
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    for suffix in ("building", "builders", "builder", "banking", "retailers", "retailer"):
        if token.endswith(suffix) and len(token) > len(suffix):
            return token[:-len(suffix)] + {
                "building": "build", "builders": "build", "builder": "build",
                "banking": "bank", "retailers": "retail", "retailer": "retail",
            }[suffix]
    for suffix in ("ing", "ers", "ed", "s"):
        if token.endswith(suffix) and len(token) > len(suffix) + 3:
            return token[:-len(suffix)]
    return token


def _lane_tokens(value) -> set[str]:
    return {
        _lane_stem(token)
        for token in re.findall(r"[a-z0-9]+", str(value or "").casefold())
        if token not in _STOCK_LANE_STOPWORDS and len(token) > 1
    }


def _brief_lane_terms(
    brief: dict, fields: tuple[str, ...] = _STOCK_LANE_BRIEF_FIELDS,
) -> list[str]:
    terms: list[str] = []
    for field in fields:
        value = brief.get(field)
        if isinstance(value, list):
            terms.extend(str(item) for item in value if item)
        elif value:
            terms.append(str(value))
    return terms


def _cjk_overlap_length(term: object, candidate_text: object) -> int:
    """Return the longest meaningful exact CJK fragment shared by two texts.

    This is a conservative, language-agnostic recall fallback. It does not infer
    synonyms; it only prevents an exact Chinese product concept from disappearing
    because the Latin tokenizer produced no tokens. Final eligibility remains an
    evidence-gated LLM decision.
    """
    haystack = str(candidate_text or "")
    best = 0
    for sequence in re.findall(r"[\u4e00-\u9fff]{3,}", str(term or "")):
        for size in range(min(8, len(sequence)), 2, -1):
            if size <= best:
                break
            found = False
            for start in range(len(sequence) - size + 1):
                fragment = sequence[start:start + size]
                if fragment in _CJK_GENERIC_LANE_FRAGMENTS:
                    continue
                if fragment in haystack:
                    best = size
                    found = True
                    break
            if found:
                break
    return best


def _lane_tiebreak(seed: object, code: object) -> str:
    """Market-cap-independent stable sampling key for taxonomy-score ties."""
    payload = f"{seed}\0{str(code or '').strip().upper()}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _taxonomy_match_score(row: dict, brief_terms: list[str]) -> float:
    """Cheap recall-oriented match used only to choose rows for LLM judgment."""
    taxonomy = " ".join(str(row.get(key) or "") for key in ("sector", "industry"))
    taxonomy_tokens = _lane_tokens(taxonomy)
    business = str(row.get("company_introduction") or "")
    business_tokens = _lane_tokens(business)
    cjk_candidate_text = f"{taxonomy} {business}"
    candidate_cjk_concepts = {
        concept for concept, pattern in _CJK_BUSINESS_CONCEPT_RE.items()
        if pattern.search(cjk_candidate_text)
    }
    if (not taxonomy_tokens and not business_tokens and not candidate_cjk_concepts
            and not _CJK_RE.search(cjk_candidate_text)):
        return 0.0
    taxonomy_norm = " ".join(re.findall(r"[a-z0-9]+", taxonomy.casefold()))
    distinctive_taxonomy_tokens = {
        "build", "construction", "homebuilder", "housing", "mortgage",
        "residential",
    }
    best = 0.0
    for term in brief_terms:
        # Quote taxonomy is frequently bilingual. Map supported CJK concepts on
        # both sides before the Latin-token guard so a Chinese-only brief can
        # recall a Chinese company description instead of falling back to size.
        term_tokens = _lane_tokens(term) | {
            concept for concept, pattern in _CJK_BUSINESS_CONCEPT_RE.items()
            if pattern.search(str(term or ""))
        }
        cjk_overlap_length = _cjk_overlap_length(term, cjk_candidate_text)
        if not term_tokens and not cjk_overlap_length:
            continue
        term_norm = " ".join(re.findall(r"[a-z0-9]+", term.casefold()))
        overlap = len(taxonomy_tokens & term_tokens)
        exact_phrase = bool(overlap and (
            taxonomy_norm and term_norm
            and (taxonomy_norm in term_norm or term_norm in taxonomy_norm)
        ))
        taxonomy_share = overlap / len(taxonomy_tokens) if taxonomy_tokens else 0.0
        term_share = overlap / len(term_tokens) if term_tokens else 0.0
        # A single distinctive word such as "retail" is useful when it covers
        # at least half of a taxonomy label. Longer labels require two matches.
        distinctive_taxonomy_match = bool(
            (taxonomy_tokens & term_tokens) & distinctive_taxonomy_tokens)
        if (exact_phrase or overlap >= 2 or taxonomy_share >= 0.5
                or distinctive_taxonomy_match):
            best = max(
                best,
                (2.0 if exact_phrase else 0.0) + overlap + taxonomy_share + term_share,
            )

        business_overlap = business_tokens & term_tokens
        distinctive = any(len(token) >= 7 for token in business_overlap)
        if len(business_overlap) >= 2 or distinctive:
            best = max(
                best,
                1.0 + len(business_overlap)
                + len(business_overlap) / len(term_tokens),
            )
        cjk_matches = len(candidate_cjk_concepts & term_tokens)
        if cjk_matches:
            best = max(best, 4.0 + 0.25 * (cjk_matches - 1))
        if cjk_overlap_length:
            best = max(best, 3.5 + 0.10 * min(cjk_overlap_length - 3, 5))
    return best


def _issuer_key(candidate: dict) -> str:
    """Best-effort issuer identity so share classes do not consume two slots."""
    explicit = str(candidate.get("issuer_id") or candidate.get("issuer_key") or "").strip()
    if explicit:
        return explicit.casefold()
    name = str(candidate.get("name") or candidate.get("code") or "").strip()
    name = re.sub(
        r"\s+(?:(?:series|pref(?:erred)?(?:\s+(?:stock|shares?))?)\s*)"
        r"[a-z0-9.-]+(?:\s+(?:stock|shares?))?$", "", name,
        flags=re.IGNORECASE,
    )
    name = re.sub(
        r"\s+(?:class\s+)?(?:a|b|c)(?:\s+(?:shares?|common stock))?$", "", name,
        flags=re.IGNORECASE,
    )
    name = re.sub(
        r"\s+(?:incorporated|inc\.?|corp(?:oration)?\.?|company|co\.?|plc|ltd\.?)$",
        "", name, flags=re.IGNORECASE,
    )
    return re.sub(r"[^a-z0-9]+", " ", name.casefold()).strip()


def _normalise_relevance_object(
    value: dict | None,
    *,
    status: str = "scored",
    require_public_fields: bool = False,
) -> dict:
    """Normalize model evidence; missing structured causal evidence fails closed."""
    value = value if isinstance(value, dict) else {}
    raw_exposure = str(value.get("exposure_type") or "").strip().lower()
    exposure = _norm_exposure(raw_exposure)
    reason = str(value.get("reason") or "")[:200]
    article_reason = str(value.get("article_reason") or "")[:200]
    factor_only = exposure == "factor_proxy" or bool(_BROAD_FACTOR_REASON_RE.search(reason))

    if factor_only:
        inferred_specificity = "broad_factor"
        inferred_materiality = "low"
        inferred_evidence = "derived"
        exposure = "factor_proxy"
    elif exposure == "direct":
        inferred_specificity, inferred_materiality, inferred_evidence = (
            "company_specific", "medium", "derived")
    elif exposure in {"enabler", "supply_chain", "beneficiary"}:
        inferred_specificity, inferred_materiality, inferred_evidence = (
            "industry_specific", "medium", "derived")
    else:
        inferred_specificity, inferred_materiality, inferred_evidence = (
            "none", "unknown", "none")

    specificity = _norm_choice(
        value.get("theme_specificity"), _THEME_SPECIFICITIES, inferred_specificity)
    materiality = _norm_choice(
        value.get("materiality"), _MATERIALITIES, inferred_materiality)
    evidence = _norm_choice(
        value.get("evidence_strength"), _EVIDENCE_STRENGTHS, inferred_evidence)
    channel = _norm_choice(
        value.get("impact_channel"), _IMPACT_CHANNELS,
        "valuation_only" if factor_only else (
            "supply_chain_orders" if exposure in {"enabler", "supply_chain"}
            else "revenue_demand" if exposure in {"direct", "beneficiary"}
            else "none"
        ),
    )
    # A broad factor remains ineligible even if a malformed response labels it
    # company-specific elsewhere in the same object.
    if factor_only or channel in {"valuation_only", "market_beta"}:
        specificity = "broad_factor"
        exposure = "factor_proxy"
    def finite_number(raw) -> bool:
        try:
            return math.isfinite(float(raw))
        except (TypeError, ValueError):
            return False

    def json_number_in_range(raw, lower: float, upper: float) -> bool:
        return bool(
            isinstance(raw, (int, float))
            and not isinstance(raw, bool)
            and math.isfinite(float(raw))
            and lower <= float(raw) <= upper
        )

    def json_string(raw) -> bool:
        return isinstance(raw, str)

    def json_choice(raw, allowed: set[str]) -> bool:
        return bool(
            isinstance(raw, str)
            and raw.strip().lower() in allowed
        )

    if require_public_fields:
        # Some adapters do not enforce the provider response schema. Enforce the
        # stock contract again before normalization so coercion/clamping cannot
        # turn malformed or out-of-range model output into publishable evidence.
        required_schema_valid = bool(
            json_choice(value.get("exposure_type"), _EXPOSURE_TYPES)
            and json_number_in_range(value.get("theme_relevance"), 1.0, 5.0)
            and json_number_in_range(value.get("confidence"), 0.0, 1.0)
            and json_choice(value.get("impact_channel"), _IMPACT_CHANNELS)
            and json_choice(value.get("theme_specificity"), _THEME_SPECIFICITIES)
            and json_choice(value.get("materiality"), _MATERIALITIES)
            and json_choice(value.get("evidence_strength"), _EVIDENCE_STRENGTHS)
            and json_string(value.get("reason"))
        )
    else:
        required_schema_valid = bool(
            raw_exposure in _EXPOSURE_TYPES
            and finite_number(value.get("theme_relevance", value.get("ai_relevance")))
            and finite_number(value.get("confidence"))
            and str(value.get("impact_channel") or "").strip().lower()
            in _IMPACT_CHANNELS
            and str(value.get("theme_specificity") or "").strip().lower()
            in _THEME_SPECIFICITIES
            and str(value.get("materiality") or "").strip().lower()
            in _MATERIALITIES
            and str(value.get("evidence_strength") or "").strip().lower()
            in _EVIDENCE_STRENGTHS
        )
    if status == "scored" and not required_schema_valid:
        status = "invalid_schema"
    theme_relevance = _clamp_float(
        value.get("theme_relevance", value.get("ai_relevance")),
        1.0, 5.0, default=1.0,
    )
    relation_fallback = {
        "direct": "direct",
        "enabler": "complementary",
        "supply_chain": "supplier",
        "beneficiary": "second_order",
    }.get(exposure, "none")
    raw_relation_type = str(value.get("relation_type") or "").strip().lower()
    relation_type = _norm_choice(
        raw_relation_type, _PUBLIC_RELATION_TYPES, relation_fallback)
    raw_directional_effect = str(
        value.get("directional_effect") or ""
    ).strip().lower()
    directional_effect = _norm_choice(
        raw_directional_effect,
        _DIRECTIONAL_EFFECTS,
        "positive" if relation_type != "none" else "none",
    )
    raw_evidence_basis = str(value.get("evidence_basis") or "").strip().lower()
    evidence_basis = _norm_choice(
        raw_evidence_basis,
        _RELATION_EVIDENCE_BASES,
        "derived" if evidence in {"explicit", "derived"} else "none",
    )
    business_fact = _normalised_text(
        value.get("business_fact")
        if require_public_fields else value.get("business_fact") or reason
    )[:500]
    theme_connection = _normalised_text(
        value.get("theme_connection")
        if require_public_fields
        else value.get("theme_connection") or article_reason or reason
    )[:500]
    financial_pathway = _normalised_text(
        value.get("financial_pathway")
        if require_public_fields else value.get("financial_pathway") or reason
    )[:500]
    public_factor_only = bool(_BROAD_FACTOR_REASON_RE.search(
        " ".join((theme_connection, financial_pathway))))
    public_relation_score = _clamp_float(
        value.get("public_relation_score"), 1.0, 5.0,
        default=theme_relevance,
    )
    public_relation_confidence = _clamp_float(
        value.get("public_relation_confidence"), 0.0, 1.0,
        default=_clamp_float(value.get("confidence"), 0.0, 1.0, default=0.3),
    )
    if factor_only or public_factor_only or channel in {"valuation_only", "market_beta"}:
        relation_type = "none"
        directional_effect = "none"
        evidence_basis = "none"
        public_relation_score = min(public_relation_score, 2.9)

    public_field_names = (
        "public_relation_score", "public_relation_confidence", "relation_type",
        "directional_effect", "business_fact", "theme_connection",
        "financial_pathway", "evidence_basis",
    )
    public_fields_present = any(key in value for key in public_field_names)
    all_public_fields_present = all(key in value for key in public_field_names)
    public_schema_valid = bool(
        (not require_public_fields and not public_fields_present)
        or (
            all_public_fields_present
            and json_number_in_range(value.get("article_support"), 0.0, 1.0)
            and json_string(value.get("article_reason"))
            and json_number_in_range(value.get("public_relation_score"), 1.0, 5.0)
            and json_number_in_range(
                value.get("public_relation_confidence"), 0.0, 1.0)
            and json_choice(value.get("relation_type"), _PUBLIC_RELATION_TYPES)
            and json_choice(value.get("directional_effect"), _DIRECTIONAL_EFFECTS)
            and json_string(value.get("business_fact"))
            and json_string(value.get("theme_connection"))
            and json_string(value.get("financial_pathway"))
            and json_choice(value.get("evidence_basis"), _RELATION_EVIDENCE_BASES)
            and (
                (
                    raw_relation_type == "none"
                    and raw_directional_effect == "none"
                    and raw_evidence_basis == "none"
                )
                or (
                    bool(business_fact)
                    and bool(theme_connection)
                    and bool(financial_pathway)
                )
            )
        )
    )
    if status == "scored" and not public_schema_valid:
        status = "invalid_schema"
    return {
        # ``ai_relevance`` remains an internal compatibility alias for ETF logic
        # and older callers. New stock prompts and ranking use theme_relevance.
        "theme_relevance": theme_relevance,
        "ai_relevance": theme_relevance,
        "article_support": _clamp_float(
            value.get("article_support"), 0.0, 1.0, default=0.0),
        "exposure_type": exposure,
        "confidence": _clamp_float(
            value.get("confidence"), 0.0, 1.0, default=0.3),
        "reason": reason,
        "article_reason": article_reason,
        "impact_channel": channel,
        "theme_specificity": specificity,
        "materiality": materiality,
        "evidence_strength": evidence,
        "public_relation_score": public_relation_score,
        "public_relation_confidence": public_relation_confidence,
        "relation_type": relation_type,
        "directional_effect": directional_effect,
        "business_fact": business_fact,
        "theme_connection": theme_connection,
        "financial_pathway": financial_pathway,
        "evidence_basis": evidence_basis,
        "relevance_status": status,
    }


_GROUNDING_BUSINESS_STOPWORDS = {
    "about", "and", "business", "company", "corporation", "does", "for", "from", "into",
    "makes", "operates", "provides", "sells", "service", "services", "that",
    "the", "their", "through", "with",
}
_GROUNDING_RELATION_STOPWORDS = {
    "about", "and", "business", "company", "connection", "contract", "customer",
    "derived", "direct", "event", "exact", "exposure", "impact", "link",
    "linked", "partner", "relationship", "specific", "supplier", "theme",
    "the", "this", "with", "for", "from", "has", "have", "its", "more",
    "new", "that", "their", "through",
}
_SOURCE_RELATION_GENERIC_STOPWORDS = {
    "accelerate", "accelerates", "affect", "affects", "can", "capacity", "capture",
    "captures", "demand", "drive", "driven", "enable", "enables", "growth",
    "increase", "increases", "launch", "launches", "reduce", "reduces", "rising",
    "shape", "shapes",
}
_DERIVED_BRIDGE_STOPWORDS = {
    "business", "company", "corporation", "platform", "platforms", "product",
    "products", "service", "services", "software", "solution", "solutions",
    "system", "systems", "technology", "technologies",
}
_SHORT_GROUNDING_TOKENS = {"ai", "ar", "ev", "vr", "5g"}
_IDENTITY_STOPWORDS = {
    "class", "co", "company", "corp", "corporation", "group", "holding",
    "holdings", "inc", "incorporated", "limited", "ltd", "ordinary", "plc",
    "shares", "technology", "technologies", "the",
}
_AMBIGUOUS_ISSUER_NAMES = {
    # Listed-company names that are also ordinary English nouns/verbs. A bare
    # occurrence cannot prove issuer identity; require a legal suffix or an
    # explicitly formatted ticker instead.
    "affirm", "block", "gap", "honest", "progress", "root", "strategy",
    "target", "unity", "upstart",
}
_RELATION_SOURCE_PATTERNS = {
    "partner": re.compile(
        r"\b(?:advis\w*|agreement|allianc\w*|collaborat\w*|distribut\w*|"
        r"integrat\w*|joint venture|partner\w*)\b",
        re.IGNORECASE,
    ),
    "supplier": re.compile(
        r"\b(?:carr(?:y|ies|ied)|component\w*|contain\w*|fabricat\w*|"
        r"manufactur\w*|provid\w*|sell\w*|"
        r"suppl\w*|us(?:e|es|ed|ing)|vendor\w*)\b",
        re.IGNORECASE,
    ),
    "customer": re.compile(
        r"\b(?:buy\w*|client\w*|customer\w*|order\w*|procur\w*|purchas\w*|source\w*)\b",
        re.IGNORECASE,
    ),
    "competitor": re.compile(
        r"\b(?:alternativ\w*|compet\w*|rival\w*|versus)\b",
        re.IGNORECASE,
    ),
}
_COMMERCIAL_CLAIM_PATTERNS = {
    "agreement": re.compile(
        r"\b(?:agreement|contract|deal)\b|协议|合同|合约", re.IGNORECASE),
    "guarantee": re.compile(
        r"\b(?:committed|exclusive|guaranteed)\b|承诺|独家|保证", re.IGNORECASE),
    "partner": re.compile(
        r"\b(?:alliance|collaboration|joint venture|partner(?:ship)?)\b|"
        r"联盟|合作|合资|伙伴",
        re.IGNORECASE,
    ),
    "supplier": re.compile(
        r"\b(?:suppliers?|vendors?)\b|供应商|供货商", re.IGNORECASE),
    "procurement": re.compile(
        r"\b(?:buy|buys|bought|procur\w*|purchas\w*|sources? from|"
        r"plac(?:e|es|ed|ing)(?:\s+\w+){0,2}\s+orders?)\b|"
        r"采购|购买|购入|下单",
        re.IGNORECASE,
    ),
    "customer_claim": re.compile(
        r"\b(?:(?:major|large|key|named|new) customer|client|buyer|"
        r"customer (?:contract|order|win)|winning? (?:a )?(?:major |large |key )?customer)\b|"
        r"主要客户|大客户|关键客户|客户订单|赢得客户",
        re.IGNORECASE,
    ),
}
_FINANCIAL_MAGNITUDE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?P<number>\d+(?:\.\d+)?)\s*"
    r"(?P<unit>%|percent(?:age points?)?|bps|basis points?|x|倍|个百分点)",
    re.IGNORECASE,
)
_CURRENCY_MAGNITUDE_RE = re.compile(
    r"(?:[$¥￥€£]\s*\d+(?:\.\d+)?\s*(?:million|billion|mn|bn|m|b)?)|"
    r"(?:\b\d+(?:\.\d+)?\s*(?:million|billion|mn|bn)\s*"
    r"(?:U\.?S\.?\s*)?(?:dollars?|euros?|yuan|renminbi)\b)|"
    r"(?:\d+(?:\.\d+)?\s*(?:万|亿)?(?:美元|欧元|人民币|元))",
    re.IGNORECASE,
)
_WORD_MULTIPLE_RE = re.compile(
    r"\b(?:double|triple|quadruple|twofold|threefold|fourfold|fivefold|"
    r"sixfold|sevenfold|eightfold|ninefold|tenfold)\b|"
    r"[一二三四五六七八九十百]+倍",
    re.IGNORECASE,
)
_WORD_QUANTITY_RE = re.compile(
    r"\b(?:"
    r"(?:about|approximately|around|nearly|over|roughly|more than|less than)\s+"
    r")?(?:"
    r"(?:a few|few|several|dozens of|scores of)\s+(?:thousand|million|billion)s?|"
    r"(?:one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|"
    r"thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|twenty|"
    r"thirty|forty|fifty|sixty|seventy|eighty|ninety|hundred)(?:[- ](?:one|two|"
    r"three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|"
    r"fifteen|sixteen|seventeen|eighteen|nineteen|twenty|thirty|forty|fifty|"
    r"sixty|seventy|eighty|ninety|hundred))*\s+(?:thousand|million|billion)s?|"
    r"(?:tens|hundreds|thousands) of (?:thousands|millions|billions)|"
    r"multi[- ](?:thousand|million|billion)"
    r")(?:\s+(?:U\.?S\.?\s*)?(?:dollars?|euros?|yuan|renminbi))?\b|"
    r"(?:约|大约|接近|超过|逾|不足|几|数)?(?:十|百|千|万|亿)+(?:美元|欧元|"
    r"人民币|元)?|(?:数十|数百|数千|数万|数亿|几十|几百|几千|几万|几亿)"
    r"(?:美元|欧元|人民币|元)?",
    re.IGNORECASE,
)
_FINANCIAL_TERM_RE = re.compile(
    r"\b(?:orders?|sales|revenue|costs?|margins?|earnings|profits?|cash flow)\b",
    re.IGNORECASE,
)
_DIRECTIONAL_POSITIVE_RE = re.compile(
    r"\b(?:add|benefit|boost|broaden|expand|grow|improv|increas|lift|rais|"
    r"strengthen|support)\w*\b",
    re.IGNORECASE,
)
_DIRECTIONAL_NEGATIVE_RE = re.compile(
    r"\b(?:compress|cut|declin|drag|erod|fall|fewer|hurt|lower|pressure|"
    r"reduc|slow|squeez|weaken|weigh)\w*\b",
    re.IGNORECASE,
)
_DIRECTIONAL_VERBS = (
    r"(?:add|benefit|boost|broaden|compress|cut|declin\w*|drag|erod\w*|expand|fall|"
    r"grow|hurt|improv|increas|lift|lower|pressure|rais|reduc\w*|slow|"
    r"squeez\w*|strengthen|support|weaken|weigh)\w*"
)
_NEGATED_DIRECTION_EN_RE = re.compile(
    r"\b(?:cannot|can't|can not|couldn't|could not|doesn't|does not|don't|"
    r"do not|failed to|fails? to|is unlikely to|are unlikely to|unlikely to|"
    r"may not|might not|never|shouldn't|should not|will not|won't|wouldn't|"
    r"would not)\s+(?:\w+\s+){0,2}" + _DIRECTIONAL_VERBS + r"\b|"
    r"\b(?:is|are|was|were)?\s*unlikely that\s+(?:\w+\s+){0,8}" +
    _DIRECTIONAL_VERBS + r"\b|"
    r"\b(?:there is )?no chance\s+(?:\w+\s+){0,8}" +
    _DIRECTIONAL_VERBS + r"\b|"
    r"\b(?:is|are|was|were)?\s*expected not to\s+(?:\w+\s+){0,2}" +
    _DIRECTIONAL_VERBS + r"\b|"
    r"\bwithout\s+(?:\w+\s+){0,3}" + _DIRECTIONAL_VERBS + r"\b",
    re.IGNORECASE,
)
_NEGATED_FINANCIAL_SUPPORT_RE = re.compile(
    r"\bno support for\b.{0,80}\b(?:orders?|sales|revenue|margins?|earnings|profits?)\b|"
    r"\black of support for\b.{0,80}\b(?:orders?|sales|revenue|margins?|earnings|profits?)\b|"
    r"\b(?:orders?|sales|revenue|margins?|earnings|profits?|growth)\b"
    r".{0,40}\blacks? support\b",
    re.IGNORECASE,
)
_NEGATED_DIRECTION_ZH_RE = re.compile(
    r"(?:不太可能|从未|不(?:会|能|再)?|未能|难以|无法)"
    r"(?:直接|显著|有效|持续|明显)?"
    r"(?:提升|增加|增长|推动|扩大|改善|支撑|提振|增强|带动|压低|拖累|"
    r"减少|下降|下滑|承压|侵蚀|压缩|削弱|放缓|走弱|挤压)"
)
_BULLISH_ANTI_STANCE_RE = re.compile(
    r"\b(?:do not|don't|should not|shouldn't)\s+(?:buy|own)\b|"
    r"\bno reason to (?:buy|own|hold)\b|"
    r"\bnot worth (?:buying|owning|holding)\b|"
    r"\b(?:avoid|sell|short|underweight)\s+(?:the )?(?:stock|shares?)\b|"
    r"\b(?:the )?(?:stock|shares?) remains? (?:a )?(?:sell|short|underweight)\b|"
    r"不(?:应|要|值得)(?:买入|持有)|不值得买|没有理由(?:买入|持有)|"
    r"(?:卖出|回避|减持|做空)(?:该股|股票|股份|标的)?",
    re.IGNORECASE,
)
_NARRATIVE_GROUNDING_STOPWORDS = set("""
    a an the and or but if as at by for from in into of on onto over through to under
    with without its their this that these those it they them where when which who whose
    can could may might will would should business company firm issuer stock share shares
    event theme catalyst exposure relationship link linkage route pathway channel bridge
    direct distinct specific clear operating operational commercial financial result resulting
    outcome setup condition conditions starts start begins begin reaches matters matter means
    creating create creates turning turn turns allowing allow allows gives give establishing
    establish established because after before while during then so therefore thus as a
    activity customer customers product products pull leverage transmission impact effect
    core main primary principal focused focus market markets investors shareholders
    broader broaden broadens broadened broadening
    add adds added benefit benefits benefiting boost boosts boosted expand expands expanded
    grow grows growing improve improves improved increase increases increased lift lifts lifted
    raise raises raised strengthen strengthens strengthened support supports supported
    compress compresses cut cuts decline declines drag drags erode erodes fall falls fewer
    hurt hurts lower lowers pressure pressures reduce reduces reduced slow slows squeeze squeezes
    weaken weakens weigh weighs rising higher more new demand orders order sales sale revenue
    revenues cost costs margin margins earnings earning profit profits cash flow flows
    is are was were be been being has have had do does did via including include includes
    based driven due gets get make makes made provide provides sell sells serve serves
    run runs operate operates own owns
""".split())
_COST_INCREASE_RE = re.compile(
    r"\b(?:higher|rising|increas\w*|rais\w*|grow\w*)\s+(?:input\s+)?costs?\b",
    re.IGNORECASE,
)
_COST_DECREASE_RE = re.compile(
    r"\b(?:cut|decreas\w*|lower\w*|reduc\w*)\s+(?:input\s+)?costs?\b",
    re.IGNORECASE,
)
_COST_INCREASE_ZH_RE = re.compile(
    r"(?:成本(?:上升|增加|提高)|(?:增加|推高|提高)成本)"
)
_COST_DECREASE_ZH_RE = re.compile(
    r"(?:成本(?:下降|降低|减少)|(?:降低|削减|减少)成本)"
)
_BILINGUAL_CONCEPT_PATTERNS = {
    "advertising": (
        re.compile(r"\b(?:advertis\w*|ad[- ]tech)\b", re.IGNORECASE),
        re.compile(r"广告"),
    ),
    "ai": (
        re.compile(r"\b(?:ai|artificial intelligence|gemini)\b", re.IGNORECASE),
        re.compile(r"\bAI\b|人工智能|\bGemini\b", re.IGNORECASE),
    ),
    "capacity": (
        re.compile(r"\b(?:capacity|utilization|volumes?)\b", re.IGNORECASE),
        re.compile(r"产能|利用率|产量|业务量"),
    ),
    "cloud": (
        re.compile(r"\b(?:cloud|data centers?|datacenters?)\b", re.IGNORECASE),
        re.compile(r"云|数据中心"),
    ),
    "connectivity": (
        re.compile(r"\b(?:cellular|connectivity|modems?|wireless)\b", re.IGNORECASE),
        re.compile(r"蜂窝|连接|调制解调器|无线"),
    ),
    "content": (
        re.compile(r"\b(?:content|configuration|upgrade cycles?|upgrades?)\b", re.IGNORECASE),
        re.compile(r"容量|含量|配置|升级|换机"),
    ),
    "demand": (
        re.compile(r"\b(?:demand|orders?|sales|shipments?)\b", re.IGNORECASE),
        re.compile(r"需求|订单|销售|出货"),
    ),
    "devices": (
        re.compile(r"\b(?:devices?|handsets?|iphones?|macs?|smartphones?|wearables?)\b", re.IGNORECASE),
        re.compile(
            r"(?:苹果|消费|移动|智能|高端|可穿戴|联网|Apple).{0,4}设备|"
            r"终端|手机|iPhone|Mac|可穿戴",
            re.IGNORECASE,
        ),
    ),
    "energy": (
        re.compile(r"\b(?:electricity|energy|gas|oil|power)\b", re.IGNORECASE),
        re.compile(r"电力|能源|天然气|石油|油气"),
    ),
    "finance": (
        re.compile(r"\b(?:banking|credit|loans?|mortgages?|payments?)\b", re.IGNORECASE),
        re.compile(r"银行|信贷|贷款|按揭|支付"),
    ),
    "housing": (
        re.compile(r"\b(?:construction|homebuild\w*|housing|residential)\b", re.IGNORECASE),
        re.compile(r"建筑|建造|住房|住宅"),
    ),
    "leadership": (
        re.compile(r"\b(?:ceo|leadership|management|succession|transition)\b", re.IGNORECASE),
        re.compile(r"CEO|首席执行官|领导|管理层|接班|继任|更替|换帅|过渡", re.IGNORECASE),
    ),
    "memory": (
        re.compile(r"\b(?:dram|hbm|memory|nand|storage)\b", re.IGNORECASE),
        re.compile(r"DRAM|HBM|NAND|内存|存储", re.IGNORECASE),
    ),
    "packaging_test": (
        re.compile(r"\b(?:packaging|test(?:ing)?|test equipment)\b", re.IGNORECASE),
        re.compile(r"封装|测试"),
    ),
    "partnership": (
        re.compile(r"\b(?:agreement|collaborat\w*|distribut\w*|integrat\w*|partner\w*)\b", re.IGNORECASE),
        re.compile(r"协议|合作|伙伴|分发|整合|集成"),
    ),
    "products": (
        re.compile(r"\b(?:equipment|platforms?|products?|solutions?|systems?)\b", re.IGNORECASE),
        # ``设备`` belongs to the device concept below; treating it as both a
        # consumer device and generic equipment creates false bilingual facts.
        re.compile(
            r"平台|产品|解决方案|系统|"
            r"(?:测试|生产|制造|工业|半导体|冷却|电力|网络).{0,4}设备"
        ),
    ),
    "requirement": (
        re.compile(r"\b(?:cycles?|launches?|needs?|programs?|requires?)\b", re.IGNORECASE),
        re.compile(r"周期|发布|上市|需要|要求|项目"),
    ),
    "roadmap": (
        re.compile(r"\b(?:execution|roadmaps?|strategy)\b", re.IGNORECASE),
        re.compile(r"执行|路线图|战略"),
    ),
    "semiconductors": (
        re.compile(r"\b(?:chips?|chipsets?|foundr\w*|gpus?|modems?|processors?|semiconductors?|silicon|wafers?)\b", re.IGNORECASE),
        re.compile(r"芯片|代工|调制解调器|处理器|半导体|硅|晶圆"),
    ),
    "services": (
        re.compile(r"\bservices?\b", re.IGNORECASE),
        re.compile(r"服务"),
    ),
    "software": (
        re.compile(r"\b(?:applications?|software)\b", re.IGNORECASE),
        re.compile(r"应用|软件"),
    ),
    "supply_chain": (
        re.compile(r"\b(?:components?|supply chain|suppliers?|vendors?)\b", re.IGNORECASE),
        re.compile(r"元件|组件|供应链|供应商|供货商"),
    ),
}

# Exact factual translations admitted in stock Chinese copy. These are narrower
# than the broad concept buckets above: a supplied "chip" is not permission to
# add a modem, GPU, foundry, or any other semiconductor product.
_ZH_FACT_TRANSLATIONS = (
    (re.compile(r"\b(?:ai|artificial intelligence)\b", re.I), re.compile(r"人工智能")),
    (re.compile(r"\badvertis\w*\b", re.I), re.compile(r"广告")),
    (re.compile(r"\baccelerators?\b", re.I), re.compile(r"加速器")),
    (re.compile(r"\badvanced[- ]node\b", re.I), re.compile(r"先进制程")),
    (re.compile(r"\banalytics?\b", re.I), re.compile(r"分析")),
    (re.compile(r"\bautomated?\b", re.I), re.compile(r"自动化")),
    (re.compile(r"\bcapacity\b", re.I), re.compile(r"产能")),
    (re.compile(r"\bcellular\b", re.I), re.compile(r"蜂窝")),
    (re.compile(r"\bchips?\b", re.I), re.compile(r"芯片")),
    (re.compile(r"\bcloud\b", re.I), re.compile(r"云")),
    (re.compile(r"\bcomponents?\b", re.I), re.compile(r"元件|组件")),
    (re.compile(r"\bconnectivity\b", re.I), re.compile(r"连接")),
    (re.compile(r"\bcontinuity\b", re.I), re.compile(r"延续性")),
    (re.compile(r"\bconsumer\b", re.I), re.compile(r"消费")),
    (re.compile(r"\bcontent\b", re.I), re.compile(r"含量|容量")),
    (re.compile(r"\bcosts?\b", re.I), re.compile(r"成本")),
    (re.compile(r"\bcycles?\b", re.I), re.compile(r"周期")),
    (re.compile(r"\bdata centers?\b", re.I), re.compile(r"数据中心")),
    (re.compile(r"\bdefen[cs]e\b", re.I), re.compile(r"防务|国防")),
    (re.compile(r"\bdemand\b", re.I), re.compile(r"需求")),
    (re.compile(r"\bdevices?\b", re.I), re.compile(r"设备|终端")),
    (re.compile(r"\bdigital\b", re.I), re.compile(r"数字")),
    (re.compile(r"\bdistribution\b", re.I), re.compile(r"分发")),
    (re.compile(r"\bearnings?\b|\bprofits?\b", re.I), re.compile(r"盈利|利润(?!率)")),
    (re.compile(r"\belectricity\b|\bpower\b", re.I), re.compile(r"电力")),
    (re.compile(r"\benergy\b", re.I), re.compile(r"能源")),
    (re.compile(r"\bequipment\b", re.I), re.compile(r"设备")),
    (re.compile(r"\bexecution\b", re.I), re.compile(r"执行")),
    (re.compile(r"\bfiber\b", re.I), re.compile(r"光纤")),
    (re.compile(r"\bfoundr\w*\b", re.I), re.compile(r"代工")),
    (re.compile(r"\bgas\b|\blng\b", re.I), re.compile(r"天然气|油气")),
    (re.compile(r"\bgpus?\b", re.I), re.compile(r"图形处理器")),
    (re.compile(r"\bhigh[- ]end\b|\bpremium\b", re.I), re.compile(r"高端")),
    (re.compile(r"\bindustry\b", re.I), re.compile(r"行业")),
    (re.compile(r"\blasers?\b", re.I), re.compile(r"激光")),
    (re.compile(r"\bleadership\b", re.I), re.compile(r"领导层")),
    (re.compile(r"\blicens\w*\b", re.I), re.compile(r"授权")),
    (re.compile(r"\bmargins?\b", re.I), re.compile(r"利润率")),
    (re.compile(r"\bmemory\b|\bstorage\b", re.I), re.compile(r"存储|内存")),
    (re.compile(r"\bmodems?\b", re.I), re.compile(r"调制解调器")),
    (re.compile(r"\boil\b", re.I), re.compile(r"石油|油气")),
    (re.compile(r"\borders?\b", re.I), re.compile(r"订单")),
    (re.compile(r"\bpackag\w*\b", re.I), re.compile(r"封装")),
    (re.compile(r"\b(?:partner(?:ship)?|collaborat\w*)\b", re.I), re.compile(r"合作")),
    (re.compile(r"\bprocessors?\b", re.I), re.compile(r"处理器")),
    (re.compile(r"\bproducts?\b", re.I), re.compile(r"产品")),
    (re.compile(r"\bprograms?\b|\bprojects?\b", re.I), re.compile(r"项目")),
    (re.compile(r"\brevenue\b", re.I), re.compile(r"收入|营收")),
    (re.compile(r"\broadmaps?\b", re.I), re.compile(r"路线图")),
    (re.compile(r"\b(?:sell|sells|sold|sales)\b", re.I), re.compile(r"销售|销量")),
    (re.compile(r"\bsemiconductors?\b", re.I), re.compile(r"半导体")),
    (re.compile(r"\bservices?\b", re.I), re.compile(r"服务")),
    (re.compile(r"\bshipments?\b", re.I), re.compile(r"出货")),
    (re.compile(r"\bsilicon\b", re.I), re.compile(r"硅")),
    (re.compile(r"\bsoftware\b", re.I), re.compile(r"软件")),
    (re.compile(r"\bsuppliers?\b", re.I), re.compile(r"供应商")),
    (re.compile(r"\bsystems?\b", re.I), re.compile(r"系统")),
    (re.compile(r"\btest(?:ing)?\b", re.I), re.compile(r"测试")),
    (re.compile(r"\btechnology\b", re.I), re.compile(r"技术")),
    (re.compile(r"\busage\b", re.I), re.compile(r"使用量")),
    (re.compile(r"\butilization\b", re.I), re.compile(r"利用率")),
    (re.compile(r"\bvolumes?\b", re.I), re.compile(r"产量|业务量")),
    (re.compile(r"\bwafers?\b", re.I), re.compile(r"晶圆")),
    (re.compile(r"\bwearables?\b", re.I), re.compile(r"可穿戴")),
    (re.compile(r"\bwireless\b", re.I), re.compile(r"无线")),
)

_ZH_NARRATIVE_FUNCTION_WORDS = tuple(sorted({
    "是一家公司", "是一家", "是一间", "首席执行官", "持续推进", "有望",
    "主营", "专注于", "从事", "提供", "生产", "制造", "销售", "运营", "经营",
    "开发", "设计", "供应", "依赖", "依靠", "通过", "围绕", "旗下", "核心",
    "主要", "业务", "用于", "面向", "服务于", "包括", "拥有", "流经", "使用",
    "采用", "需要", "要求", "影响", "支撑", "支持", "推动", "带动", "提振",
    "促进", "提升", "提高", "增加", "增长", "扩大", "改善", "增强", "减少",
    "降低", "下降", "下滑", "压低", "拖累", "挤压", "承压", "侵蚀", "压缩",
    "削弱", "放缓", "走弱", "稳定", "持续", "更高", "更低", "更多", "新",
    "相关", "这些", "此类", "该公司", "这家公司", "公司", "企业", "其", "该",
    "从而", "因此", "随着", "由于", "其中", "同时", "直接", "可", "能", "将",
    "会", "若", "并", "且", "或", "与", "和", "及", "为", "供", "在", "中",
    "对", "由", "以", "的", "了",
}, key=len, reverse=True))


def _unsupported_chinese_factual_content(
    candidate: dict, zh: str, supplied_evidence: str,
) -> set[str]:
    """Reject Chinese facts that are neither supplied nor translation grammar."""
    residue = str(zh or "")
    for identity in (
        candidate.get("name_zh"), candidate.get("security_name_zh"),
    ):
        identity = _normalised_text(identity)
        if identity:
            residue = residue.replace(identity, " ")

    # Several legitimate translations are polysemous: ``设备`` can translate
    # either "device" or "equipment", and ``油气`` can be grounded by oil or
    # gas.  Decide support per matched span across *all* admissible source
    # patterns before rejecting it, rather than letting tuple order decide.
    matches: dict[tuple[int, int], dict[str, object]] = {}
    for source_pattern, chinese_pattern in _ZH_FACT_TRANSLATIONS:
        source_supported = bool(source_pattern.search(supplied_evidence))
        for match in chinese_pattern.finditer(residue):
            entry = matches.setdefault(
                match.span(), {"text": match.group(0), "supported": False})
            if source_supported:
                entry["supported"] = True
    supported_spans = [
        (start, end) for (start, end), entry in matches.items()
        if entry["supported"]
    ]
    unsupported = {
        str(entry["text"])
        for (start, end), entry in matches.items()
        if not entry["supported"]
        and not any(
            accepted_start <= start and end <= accepted_end
            for accepted_start, accepted_end in supported_spans
        )
    }
    if unsupported:
        return unsupported

    for start, end in sorted(supported_spans, reverse=True):
        residue = f"{residue[:start]}{' ' * (end - start)}{residue[end:]}"

    for phrase in _ZH_NARRATIVE_FUNCTION_WORDS:
        residue = residue.replace(phrase, " ")
    residue = re.sub(r"[A-Za-z0-9\s\W_]+", " ", residue)
    return {
        token for token in re.findall(r"[\u4e00-\u9fff]+", residue)
        if token
    }


def _candidate_identity_stems(candidate: dict) -> set[str]:
    output = {
        _lane_stem(token)
        for token in re.findall(
            r"[A-Za-z0-9]+", _normalised_text(candidate.get("name")).casefold())
        if len(token) >= 3 and _lane_stem(token) not in _IDENTITY_STOPWORDS
    }
    ticker = re.sub(
        r"[^A-Za-z0-9]", "",
        str(candidate.get("code") or "").partition(":")[2],
    ).casefold()
    if ticker:
        output.add(_lane_stem(ticker))
    return output


def _grounding_tokens(
    value: object,
    candidate: dict,
    *,
    stopwords: set[str],
) -> set[str]:
    identity = _candidate_identity_stems(candidate)
    normalised_stopwords = {_lane_stem(token) for token in stopwords}
    output = set()
    for token in re.findall(r"[A-Za-z0-9]+", _normalised_text(value).casefold()):
        stem = _lane_stem(token)
        if (len(stem) < 3 and stem not in _SHORT_GROUNDING_TOKENS) \
                or stem in identity or stem in normalised_stopwords:
            continue
        output.add(stem)
    return output


def _external_proper_nouns(value: object, candidate: dict) -> set[str]:
    """Return named counterpart tokens, excluding the candidate and common acronyms."""
    identity = _candidate_identity_stems(candidate)
    ignored = {
        "ai", "ceo", "cfo", "company", "corp", "corporation", "cpu",
        "dram", "etf", "etn", "gpu", "group", "hbm", "holdings", "inc",
        "ltd", "nand", "nav", "plc", "ucits", "usd",
    }
    sentence_starters = {
        "broader", "continued", "device", "event", "execution", "faster",
        "fewer", "higher", "improved", "lower", "more", "new", "rack",
        "rising", "steady", "strong", "supplier", "sustained", "the",
    }
    output = set()
    text = str(value or "")
    for match in re.finditer(
        r"(?<![A-Za-z0-9])[A-Z][A-Za-z0-9&.]{2,}", text,
    ):
        token = match.group(0).rstrip(".")
        prefix = text[:match.start()].rstrip()
        if (not token.isupper()
                and (not prefix or prefix[-1:] in {".", "!", "?", ";", ":", "\n"})
                and (
                    token.casefold() in sentence_starters
                    or _lane_stem(token.casefold()) in {
                        _lane_stem(value) for value in sentence_starters
                    }
                )):
            continue
        if "." in token:
            continue
        stem = _lane_stem(token.casefold())
        if stem not in identity and stem not in ignored:
            output.add(stem)
    return output


def _claim_has_source_coverage(
    claim_tokens: set[str], source_tokens: set[str], *, minimum_ratio: float,
) -> bool:
    """Require meaningful claim coverage, not one convenient shared noun."""
    if not claim_tokens:
        return False
    overlap = claim_tokens & source_tokens
    minimum_overlap = 1 if len(claim_tokens) <= 2 else 2
    return bool(
        len(overlap) >= minimum_overlap
        and len(overlap) / len(claim_tokens) >= minimum_ratio
    )


def _financial_magnitude_claims(value: object) -> set[str]:
    """Normalize percentage/multiple claims for evidence containment checks."""
    claims = set()
    for match in _FINANCIAL_MAGNITUDE_RE.finditer(str(value or "")):
        unit = match.group("unit").casefold()
        unit = "pct" if unit in {"%", "percent", "percentage point",
                                  "percentage points", "个百分点"} else unit
        unit = "bps" if unit in {"bps", "basis point", "basis points"} else unit
        unit = "x" if unit in {"x", "倍"} else unit
        claims.add(f"{match.group('number')}:{unit}")
    claims.update(
        "currency:" + re.sub(r"\s+", "", match.group(0).casefold())
        for match in _CURRENCY_MAGNITUDE_RE.finditer(str(value or ""))
    )
    claims.update(
        "multiple:" + re.sub(r"\s+", "", match.group(0).casefold())
        for match in _WORD_MULTIPLE_RE.finditer(str(value or ""))
    )
    claims.update(
        "quantity:" + re.sub(r"\s+", "", match.group(0).casefold())
        for match in _WORD_QUANTITY_RE.finditer(str(value or ""))
    )
    return claims


def _commercial_claim_categories(value: object) -> set[str]:
    return {
        name for name, pattern in _COMMERCIAL_CLAIM_PATTERNS.items()
        if pattern.search(str(value or ""))
    }


def _commercial_match_is_negated(text: str, start: int) -> bool:
    prefix = text[max(0, start - 50):start]
    return bool(re.search(
        r"(?:\bno\b|\bnot\b|\bwithout\b|\blacks?\b|\bden(?:y|ies|ied)\b)"
        r".{0,24}$|(?:无|没有|并无|未|否认).{0,10}$",
        prefix,
        re.IGNORECASE,
    ))


def _affirmative_commercial_claim_categories(value: object) -> set[str]:
    text = str(value or "")
    return {
        name for name, pattern in _COMMERCIAL_CLAIM_PATTERNS.items()
        if any(
            not _commercial_match_is_negated(text, match.start())
            for match in pattern.finditer(text)
        )
    }


def _named_party_commercial_associations(
    value: object, candidate: dict, *, known_parties: set[str] | None = None,
) -> dict[str, set[str]]:
    """Bind customer/partner claims to the named party in the same clause."""
    text = str(value or "")
    parties = set(known_parties or ()) | _external_proper_nouns(text, candidate)
    clauses = [
        _normalised_text(clause)
        for clause in re.split(r"[.!?。！？;；\r\n]+", text)
        if _normalised_text(clause)
    ]
    output: dict[str, set[str]] = {}
    for party in parties:
        party_pattern = re.compile(
            rf"(?<![A-Za-z0-9]){re.escape(party)}(?![A-Za-z0-9])",
            re.IGNORECASE,
        )
        categories: set[str] = set()
        for clause in clauses:
            if party_pattern.search(clause):
                categories |= _affirmative_commercial_claim_categories(clause)
        output[party] = categories
    return output


def _commercial_clauses_are_supported(
    candidate: dict, narrative: str, relationship_evidence: str,
) -> bool:
    """Require each commercial predicate/party pair in one evidence clause.

    Non-commercial details in the same sentence are checked against the full
    supplied record separately.  Restricting this check to the commercial
    predicate and its named counterparty prevents role swapping without
    rejecting a supported financial consequence that follows the predicate.
    """
    split_pattern = r"[.!?。！？;；\r\n]+"
    claim_clauses = [
        _normalised_text(clause)
        for clause in re.split(split_pattern, narrative)
        if _commercial_claim_categories(clause)
    ]
    source_clauses = [
        _normalised_text(clause)
        for clause in re.split(split_pattern, relationship_evidence)
        if _affirmative_commercial_claim_categories(clause)
    ]
    relationship_parties = _external_proper_nouns(
        candidate.get("theme_connection"), candidate)
    business_parties = _external_proper_nouns(
        ". ".join(_normalised_text(candidate.get(field)) for field in (
            "company_introduction", "business_fact",
        )),
        candidate,
    )
    # Capitalized product names (for example Gemini) can sit in the same broker
    # sentence as a partnership without being the commercial counterparty.
    product_like_parties = business_parties - relationship_parties
    for claim_clause in claim_clauses:
        claim_categories = _commercial_claim_categories(claim_clause)
        claim_parties = (
            _external_proper_nouns(claim_clause, candidate)
            - product_like_parties
        )
        if not any(
            claim_categories <= _affirmative_commercial_claim_categories(
                source_clause)
            and claim_parties <= _external_proper_nouns(
                source_clause, candidate)
            for source_clause in source_clauses
        ):
            return False
    return True


def _unsupported_english_factual_tokens(
    candidate: dict, narrative: str, supplied_evidence: str,
) -> set[str]:
    """Find content words introduced by prose but absent from supplied facts."""
    narrative_tokens = _grounding_tokens(
        narrative, candidate, stopwords=_NARRATIVE_GROUNDING_STOPWORDS)
    evidence_tokens = _grounding_tokens(
        supplied_evidence, candidate, stopwords=set())
    unsupported = narrative_tokens - evidence_tokens
    return unsupported


_RELATION_CLAUSE_BOUNDARY_RE = re.compile(
    r"[.!?。！？;；\r\n]+|"
    r"\b(?:but|while|whereas)\b|"
    r"，|"
    r",(?=\s*(?:(?:but|while|whereas)\b|"
    # Keep the subject's initial case-sensitive even though the rest of the
    # expression is case-insensitive.  Otherwise a list tail such as
    # ``wearables, and digital services`` is mistaken for a new company clause.
    r"(?:(?-i:[A-Z])[A-Za-z0-9&'’-]*(?:\s+(?-i:[A-Z])[A-Za-z0-9&'’-]*){0,3}|"
    r"it|its|the company|this company)\s+"
    r"(?:acquir\w*|advanc\w*|affect\w*|announc\w*|appoint\w*|"
    r"boost\w*|build\w*|buy\w*|collaborat\w*|cut\w*|design\w*|"
    r"develop\w*|drive\w*|expand\w*|expect\w*|increase\w*|launch\w*|"
    r"manufactur\w*|name\w*|operat\w*|partner\w*|plan\w*|produc\w*|"
    r"provid\w*|rais\w*|reduc\w*|report\w*|require\w*|sell\w*|"
    r"sign\w*|suppl\w*|support\w*|use\w*)))|"
    r"\band\b(?=\s+(?:has|have|had|is|are|was|were|does|do|did|"
    r"den(?:y|ies|ied)|lacks?|without|no\b|not\b))",
    re.IGNORECASE,
)

_RELATION_FINITE_PREDICATE_RE = re.compile(
    r"\b(?:acquir\w*|advanc\w*|affect\w*|announc\w*|appoint\w*|"
    r"boost\w*|build\w*|buy\w*|collaborat\w*|cut\w*|design\w*|"
    r"develop\w*|drive\w*|expand\w*|expect\w*|increase\w*|launch\w*|"
    r"manufactur\w*|name\w*|operat\w*|partner\w*|plan\w*|produc\w*|"
    r"provid\w*|rais\w*|reduc\w*|report\w*|require\w*|sell\w*|"
    r"sign\w*|suppl\w*|support\w*|use\w*)\b",
    re.IGNORECASE,
)
_INDEPENDENT_AND_CLAUSE_RE = re.compile(
    r"\band\b(?=\s+"
    r"(?:(?-i:[A-Z])[A-Za-z0-9&'’-]*(?:\s+(?-i:[A-Z])"
    r"[A-Za-z0-9&'’-]*){0,3})\s+"
    r"(?:acquir\w*|advanc\w*|affect\w*|announc\w*|appoint\w*|"
    r"boost\w*|build\w*|buy\w*|collaborat\w*|cut\w*|design\w*|"
    r"develop\w*|drive\w*|expand\w*|expect\w*|increase\w*|launch\w*|"
    r"manufactur\w*|name\w*|operat\w*|partner\w*|plan\w*|produc\w*|"
    r"provid\w*|rais\w*|reduc\w*|report\w*|require\w*|sell\w*|"
    r"sign\w*|suppl\w*|support\w*|use\w*)\b)",
    re.IGNORECASE,
)


def _relationship_source_clauses(source_text: str) -> list[str]:
    """Split independent assertions without breaking coordinated noun phrases."""
    base_clauses = [
        _normalised_text(clause)
        for clause in _RELATION_CLAUSE_BOUNDARY_RE.split(source_text)
        if _normalised_text(clause)
    ]
    clauses: list[str] = []
    for base_clause in base_clauses:
        start = 0
        for boundary in _INDEPENDENT_AND_CLAUSE_RE.finditer(base_clause):
            left = _normalised_text(base_clause[start:boundary.start()])
            # ``CoreWeave and Nebius expansion increases ...`` is one
            # coordinated subject and has no predicate before ``and``. By
            # contrast, ``TSMC manufactures ... and Apple advances ...`` has
            # complete assertions on both sides and must be separated.
            if left and _RELATION_FINITE_PREDICATE_RE.search(left):
                clauses.append(left.rstrip(" ,"))
                start = boundary.end()
        tail = _normalised_text(base_clause[start:])
        if tail:
            clauses.append(tail.lstrip(" ,"))
    return [clause for clause in clauses if clause]


def _verified_article_support(candidate: dict, article: dict | None) -> float:
    """Return article support only for a candidate-local relationship sentence."""
    article = article if isinstance(article, dict) else {}
    article_text = _normalised_text(
        f"{article.get('title') or ''}. "
        f"{str(article.get('text') or '')[:ARTICLE_BODY_TEXT_CHARS]}"
    )
    provenance = candidate.get("candidate_provenance") or []
    if isinstance(provenance, str):
        provenance = [provenance]
    verified = bool(
        float(candidate.get("article_support") or 0.0) > 0
        and {"title_lede", "article_body"} & set(provenance)
        and article_text
    )
    if not verified:
        return 0.0
    evidence_text = _article_entity_evidence_text(candidate, article_text)
    relation_tokens = _grounding_tokens(
        candidate.get("theme_connection"), candidate,
        stopwords=(
            _GROUNDING_RELATION_STOPWORDS
            | _SOURCE_RELATION_GENERIC_STOPWORDS
        ),
    )
    if not evidence_text or not _source_supports_relation(
            candidate, relation_tokens, evidence_text):
        return 0.0
    return _clamp_float(
        candidate.get("article_support"), 0.0, 1.0, default=0.0)


def _article_entity_evidence_text(candidate: dict, article_text: str) -> str:
    """Keep candidate clauses plus tightly linked adjacent event context."""
    entity = {
        "name": candidate.get("name"),
        "ticker": str(candidate.get("code") or "").partition(":")[2],
    }
    clauses = _relationship_source_clauses(article_text)
    if not clauses:
        return ""
    selected: list[str] = []
    for index, clause in enumerate(clauses):
        if not _entity_is_mentioned(entity, clause):
            continue
        selected.append(clause)
        if index + 1 >= len(clauses):
            continue
        following = clauses[index + 1]
        pronoun_link = bool(re.match(
            r"^(?:it|its|the company|this company|this business|these products|"
            r"such products|其|该公司|这家公司|这些产品)\b",
            following,
            re.IGNORECASE,
        ))
        if pronoun_link:
            selected.append(following)
    return ". ".join(dict.fromkeys(selected))


def _verified_candidate_article_evidence(
    candidate: dict,
    article: dict | None,
    entity: dict | None = None,
) -> str:
    """Return literal candidate-local article text, never an LLM paraphrase.

    Entity extraction and ecosystem-map prose are useful discovery hints, but
    allowing those generated strings back into the scoring or narrative evidence
    would let one model response validate another. Resolve the identity first,
    then recover only sentences that are actually present in the fetched article.
    """
    article = article if isinstance(article, dict) else {}
    article_text = _normalised_text(
        f"{article.get('title') or ''}. "
        f"{str(article.get('text') or '')[:ARTICLE_BODY_TEXT_CHARS]}"
    )
    if not article_text:
        return ""

    identities = [{
        "name": candidate.get("name"),
        "ticker": str(candidate.get("code") or "").partition(":")[2],
    }]
    entity = entity if isinstance(entity, dict) else {}
    if entity.get("name") or entity.get("ticker"):
        identities.append({
            "name": entity.get("name"),
            "ticker": entity.get("ticker"),
        })

    evidence: list[str] = []
    for identity in identities:
        probe = dict(candidate)
        probe["name"] = identity.get("name") or candidate.get("name")
        ticker = _normalised_text(identity.get("ticker"))
        if ticker:
            probe["code"] = f"0:{ticker}"
        literal = _article_entity_evidence_text(probe, article_text)
        if literal:
            evidence.append(literal)
    return ". ".join(dict.fromkeys(evidence))[:1200]


def _source_supports_relation(
    candidate: dict, relation_tokens: set[str], source_text: str,
    *, candidate_context: bool = False,
) -> bool:
    source_tokens = {
        _lane_stem(token)
        for token in re.findall(r"[A-Za-z0-9]+", source_text.casefold())
        if len(token) >= 3 or token.casefold() in _SHORT_GROUNDING_TOKENS
    }
    relation_type = str(candidate.get("relation_type") or "")
    if not _claim_has_source_coverage(
        relation_tokens,
        source_tokens,
        minimum_ratio=1.0,
    ):
        return False
    named_parties = _external_proper_nouns(
        candidate.get("theme_connection"), candidate)
    if not named_parties <= source_tokens:
        return False
    relation_pattern = _RELATION_SOURCE_PATTERNS.get(relation_type)
    clauses = _relationship_source_clauses(source_text)
    candidate_entity = {
        "name": candidate.get("name"),
        "ticker": str(candidate.get("code") or "").partition(":")[2],
    }

    def candidate_linked(clause: str) -> bool:
        return bool(
            candidate_context
            or _entity_is_mentioned(candidate_entity, clause)
            or re.match(
                r"^(?:it|its|the company|this company|this business|"
                r"these products|such products|其|该公司|这家公司|这些产品)\b",
                clause,
                re.IGNORECASE,
            )
        )

    if relation_pattern is None:
        clause_tokens = [
            (clause, {
                _lane_stem(token)
                for token in re.findall(r"[A-Za-z0-9]+", clause.casefold())
                if len(token) >= 3
                or token.casefold() in _SHORT_GROUNDING_TOKENS
            })
            for clause in clauses
        ]
        # A shared noun across adjacent sentences is not evidence that the two
        # facts are causally related. Direct, complementary, and second-order
        # claims therefore need one source clause that contains the complete
        # relationship, rather than a document-level bag-of-words match.
        return any(
            relation_tokens <= tokens and candidate_linked(clause)
            for clause, tokens in clause_tokens
        )

    def affirmative_relation(segment: str) -> bool:
        return any(
            not _commercial_match_is_negated(segment, match.start())
            for match in relation_pattern.finditer(segment)
        )

    commercial_clauses = clauses
    relation_clauses = [
        clause for clause in commercial_clauses
        if affirmative_relation(clause) and candidate_linked(clause)
    ]
    if not relation_clauses:
        return False
    if named_parties:
        for party in named_parties:
            if not any(
                party in {
                    _lane_stem(token)
                    for token in re.findall(
                        r"[A-Za-z0-9]+", clause.casefold())
                    if len(token) >= 3
                }
                and affirmative_relation(clause)
                for clause in relation_clauses
            ):
                return False
    return True


def _event_evidence_text(
    article: dict | None, brief: dict | None,
) -> str:
    """Return only event facts that did not originate with the analysis LLM.

    The generated brief is useful for discovery, but its summary, ecosystem
    buckets, and operating evidence cannot validate claims generated from that
    same brief.  Grounding is limited to the fetched article and the user's
    literal input theme, which ``event_brief`` copies into ``input_theme``.
    """
    article = article if isinstance(article, dict) else {}
    brief = brief if isinstance(brief, dict) else {}
    values: list[str] = [
        _normalised_text(article.get("title")),
        _normalised_text(str(article.get("text") or "")[:ARTICLE_BODY_TEXT_CHARS]),
        _normalised_text(brief.get("input_theme")),
    ]
    return ". ".join(value for value in values if value)


def _public_relation_evidence_is_grounded(
    candidate: dict, article: dict | None = None, brief: dict | None = None,
) -> bool:
    """Cross-check the model's business and relationship claims against supplied facts."""
    evidence_basis = str(candidate.get("evidence_basis") or "").strip().lower()
    profile_text = _normalised_text(" ".join(
        str(candidate.get(field) or "")
        for field in ("company_introduction", "sector", "industry")
    ))
    business_tokens = _grounding_tokens(
        candidate.get("business_fact"), candidate,
        stopwords=_GROUNDING_BUSINESS_STOPWORDS,
    )
    relation_tokens = _grounding_tokens(
        candidate.get("theme_connection"), candidate,
        stopwords=(
            _GROUNDING_RELATION_STOPWORDS
            | _SOURCE_RELATION_GENERIC_STOPWORDS
        ),
    )
    if not profile_text or not business_tokens or not relation_tokens:
        return False

    event_claim_tokens = relation_tokens - business_tokens - {
        _lane_stem(token) for token in _DERIVED_BRIDGE_STOPWORDS
    }
    event_source_text = _event_evidence_text(article, brief)
    event_source_tokens = _grounding_tokens(
        event_source_text,
        candidate,
        stopwords=(
            _GROUNDING_RELATION_STOPWORDS
            | _SOURCE_RELATION_GENERIC_STOPWORDS
        ),
    )
    # When the exact event phrase is also the company's product description
    # (for example "hollow-core fiber"), subtracting business tokens would
    # erase the event claim. Keep the trusted source overlap in that case.
    if not event_claim_tokens:
        event_claim_tokens = relation_tokens & event_source_tokens
    distinctive_singletons = {"ai", "ar", "ceo", "dram", "ev", "gpu", "hbm", "nand", "vr", "5g"}
    event_support = bool(
        len(event_claim_tokens) == 1
        and (
            event_claim_tokens <= distinctive_singletons
            or all(len(token) >= 5 for token in event_claim_tokens)
        )
        and event_claim_tokens <= event_source_tokens
    ) or bool(
        len(event_claim_tokens) >= 2
        and _claim_has_source_coverage(
            event_claim_tokens, event_source_tokens, minimum_ratio=0.34)
    )
    event_claim_concepts = {
        name for name, (source_pattern, _) in _BILINGUAL_CONCEPT_PATTERNS.items()
        if source_pattern.search(_normalised_text(candidate.get("theme_connection")))
    }
    event_source_concepts = {
        name for name, (source_pattern, chinese_pattern)
        in _BILINGUAL_CONCEPT_PATTERNS.items()
        if source_pattern.search(event_source_text)
        or chinese_pattern.search(event_source_text)
    }
    concept_overlap = event_claim_concepts & event_source_concepts
    distinctive_concepts = {
        "ai", "cloud", "connectivity", "content", "devices", "energy",
        "leadership", "memory", "packaging_test", "semiconductors",
    }
    concept_event_support = bool(concept_overlap & distinctive_concepts)
    if not event_source_text or not (event_support or concept_event_support):
        return False
    event_entities = [
        entity for entity in (brief or {}).get("title_lede_entities") or []
        if isinstance(entity, dict)
        and any(_normalised_text(entity.get(key)) for key in (
            "name", "ticker", "market_code"))
    ]
    candidate_is_anchor = False
    if event_entities:
        candidate_code = _normalised_text(candidate.get("code")).casefold()
        candidate_ticker = candidate_code.partition(":")[2]
        candidate_is_anchor = any(
            candidate_code == _normalised_text(
                entity.get("market_code")).casefold()
            or candidate_ticker == _normalised_text(
                entity.get("ticker")).casefold()
            or (
                _normalised_text(entity.get("name"))
                and _issuer_key({"name": entity.get("name")})
                == _issuer_key(candidate)
            )
            for entity in event_entities
        )
        connection_text = _normalised_text(candidate.get("theme_connection"))
        if not candidate_is_anchor and not any(
            _structured_entity_reference_is_mentioned(entity, connection_text)
            for entity in event_entities
        ):
            return False

    profile_tokens = {
        _lane_stem(token)
        for token in re.findall(r"[A-Za-z0-9]+", profile_text.casefold())
        if len(token) >= 3 or token.casefold() in _SHORT_GROUNDING_TOKENS
    }
    profile_business_support = _claim_has_source_coverage(
        business_tokens, profile_tokens, minimum_ratio=1.0,
    )
    profile_relation_support = _source_supports_relation(
        candidate, relation_tokens, profile_text, candidate_context=True,
    )

    article = article if isinstance(article, dict) else {}
    article_text = _normalised_text(
        f"{article.get('title') or ''}. "
        f"{str(article.get('text') or '')[:ARTICLE_BODY_TEXT_CHARS]}"
    )
    article_candidate_source = _verified_article_support(candidate, article) > 0
    article_evidence_text = _article_entity_evidence_text(
        candidate, article_text)
    article_tokens = {
        _lane_stem(token)
        for token in re.findall(
            r"[A-Za-z0-9]+", article_evidence_text.casefold())
        if len(token) >= 3 or token.casefold() in _SHORT_GROUNDING_TOKENS
    }
    article_business_support = bool(
        article_candidate_source
        and _claim_has_source_coverage(
            business_tokens, article_tokens, minimum_ratio=1.0)
    )
    article_relation_support = bool(
        article_candidate_source
        and _source_supports_relation(
            candidate, relation_tokens, article_evidence_text)
    )
    brief_source_text = _event_evidence_text(None, brief)
    candidate_identity = {
        "name": candidate.get("name"),
        "ticker": str(candidate.get("code") or "").partition(":")[2],
    }
    bridge_article_segments = [
        _normalised_text(segment)
        for segment in re.split(
            r"[.!?。！？;；\r\n]+",
            _normalised_text(
                f"{article.get('title') or ''}. "
                f"{str(article.get('text') or '')[:ARTICLE_BODY_TEXT_CHARS]}"
            ),
        )
        if _normalised_text(segment)
        and not _entity_is_mentioned(candidate_identity, segment)
    ]
    bridge_event_source_text = ". ".join(
        [*bridge_article_segments, brief_source_text]
    )
    bridge_profile_tokens = _grounding_tokens(
        profile_text, candidate,
        stopwords=(
            _GROUNDING_BUSINESS_STOPWORDS | _DERIVED_BRIDGE_STOPWORDS
        ),
    )
    bridge_event_tokens = _grounding_tokens(
        bridge_event_source_text, candidate,
        stopwords=(
            _GROUNDING_RELATION_STOPWORDS
            | _SOURCE_RELATION_GENERIC_STOPWORDS
            | _DERIVED_BRIDGE_STOPWORDS
        ),
    )
    bridge_overlap = bridge_profile_tokens & bridge_event_tokens
    input_theme_tokens = _grounding_tokens(
        brief_source_text,
        candidate,
        stopwords=(
            _GROUNDING_RELATION_STOPWORDS
            | _SOURCE_RELATION_GENERIC_STOPWORDS
            | _DERIVED_BRIDGE_STOPWORDS
        ),
    )
    safe_input_singletons = {
        token for token in bridge_overlap & input_theme_tokens
        if (len(token) >= 5 or token in {"dram", "gpu", "hbm", "nand", "5g"})
        and token not in {
            "advertising", "business", "cloud", "company", "connectivity",
            "consumer", "content", "demand", "devices", "energy", "finance",
            "growth", "housing", "leadership", "market", "product", "sector",
            "service", "software", "technology",
        }
    }
    union_source_tokens = bridge_profile_tokens | bridge_event_tokens
    bridge_relation_tokens = relation_tokens - {
        _lane_stem(token) for token in _DERIVED_BRIDGE_STOPWORDS
    }
    company_specific_event = bool(re.search(
        r"\b(?:appoint\w*|ceo|cfo|chief executive|earnings|leadership|"
        r"merger|acquisition|product launch|resign\w*|succession|takeover|"
        r"transition)\b",
        bridge_event_source_text,
        re.IGNORECASE,
    )) or bool(event_entities and not candidate_is_anchor)
    brief_bridge_support = bool(
        bridge_event_source_text
        # A complementary or second-order relationship is a causal assertion,
        # not a taxonomy synonym. It must appear explicitly in a candidate-local
        # article/profile clause; only a direct product/theme exposure may be
        # conservatively derived across sources.
        and candidate.get("relation_type") == "direct"
        # A named-company event cannot be bridged to another issuer from shared
        # taxonomy words. It needs a candidate-local article/profile clause.
        and not company_specific_event
        # A semantic bucket such as software/cloud/energy is not a causal
        # relationship. Derived bridges need two literal shared facts or one
        # deliberately narrow exact-theme token (for example HBM or neocloud).
        and (len(bridge_overlap) >= 2 or safe_input_singletons)
        and _claim_has_source_coverage(
            bridge_relation_tokens, union_source_tokens, minimum_ratio=1.0)
        and _external_proper_nouns(
            candidate.get("theme_connection"), candidate) <= union_source_tokens
    )
    derived_relation_support = bool(
        profile_relation_support
        or article_relation_support
        or brief_bridge_support
    )

    claim_text = ". ".join(_normalised_text(candidate.get(field)) for field in (
        "business_fact", "theme_connection", "financial_pathway",
    ))
    if evidence_basis == "company_profile":
        allowed_source_text = profile_text
    elif evidence_basis == "article":
        allowed_source_text = article_text if article_candidate_source else ""
    elif evidence_basis in {"combined", "derived"}:
        allowed_source_text = " ".join(
            value for value in (
                profile_text,
                article_evidence_text if article_candidate_source else "",
                brief_source_text if brief_bridge_support else "",
            ) if value
        )
    else:
        allowed_source_text = ""
    # Broker cases do not need numerical uplift claims. Excluding them from the
    # model-authored relationship/pathway avoids laundering a historical metric
    # into an unsupported event sensitivity.
    event_financial_claim_text = ". ".join(_normalised_text(candidate.get(field)) for field in (
        "theme_connection", "financial_pathway",
    ))
    if _financial_magnitude_claims(event_financial_claim_text):
        return False
    commercial_source_text = profile_text
    if article_candidate_source:
        commercial_source_text = f"{commercial_source_text} {article_evidence_text}"
    claim_categories = _commercial_claim_categories(claim_text)
    affirmative_claim_categories = _affirmative_commercial_claim_categories(
        claim_text)
    if claim_categories != affirmative_claim_categories:
        return False
    if not claim_categories <= _affirmative_commercial_claim_categories(
            commercial_source_text):
        return False
    allowed_source_tokens = {
        _lane_stem(token)
        for token in re.findall(
            r"[A-Za-z0-9]+", allowed_source_text.casefold())
        if len(token) >= 3 or token.casefold() in _SHORT_GROUNDING_TOKENS
    }
    if not _external_proper_nouns(claim_text, candidate) <= allowed_source_tokens:
        return False

    # The model may state a directional financial consequence, but it may not
    # insert a new product or operating asset into that consequence.  Ignore
    # ordinary transmission vocabulary and require every remaining noun to be
    # present in the grounded business/relationship sources (or a supported
    # domain synonym such as modem/chipset within semiconductors).
    pathway_source_text = ". ".join(value for value in (
        profile_text,
        article_evidence_text if article_candidate_source else "",
        _normalised_text(candidate.get("business_fact")),
        _normalised_text(candidate.get("theme_connection")),
    ) if value)
    pathway_tokens = _grounding_tokens(
        candidate.get("financial_pathway"),
        candidate,
        stopwords=_NARRATIVE_GROUNDING_STOPWORDS | {
            "activities", "activity", "additions", "addition", "adoption",
            "components", "component", "continued", "continuity", "cycle",
            "deployments", "deployment", "execution", "shipments", "shipment",
            "spending", "steady", "sustained", "usage", "utilization",
            "volumes", "volume",
        },
    )
    pathway_source_tokens = _grounding_tokens(
        pathway_source_text, candidate, stopwords=set())
    unsupported_pathway_tokens = pathway_tokens - pathway_source_tokens
    if unsupported_pathway_tokens:
        return False

    if evidence_basis == "company_profile":
        return profile_business_support and profile_relation_support
    if evidence_basis == "article":
        return article_business_support and article_relation_support
    if evidence_basis == "combined":
        return bool(
            (profile_business_support or article_business_support)
            and article_candidate_source
            and article_relation_support
        )
    if evidence_basis == "derived":
        return bool(profile_business_support or article_business_support) and bool(
            derived_relation_support)
    return False


def _directional_pathway_is_consistent(value: object, expected: str) -> bool:
    """Fail closed when prose and the structured direction label disagree."""
    text = _normalised_text(value)
    if not text or not _FINANCIAL_TERM_RE.search(text):
        return False
    if (_NEGATED_DIRECTION_EN_RE.search(text)
            or _NEGATED_FINANCIAL_SUPPORT_RE.search(text)
            or _NEGATED_DIRECTION_ZH_RE.search(text)):
        return False
    cost_increases = list(_COST_INCREASE_RE.finditer(text))
    cost_decreases = list(_COST_DECREASE_RE.finditer(text))

    def outside(matches: list[re.Match], spans: list[re.Match]) -> list[int]:
        return [
            match.start() for match in matches
            if not any(span.start() <= match.start() < span.end() for span in spans)
        ]

    positive_positions = outside(
        list(_DIRECTIONAL_POSITIVE_RE.finditer(text)), cost_increases)
    negative_positions = outside(
        list(_DIRECTIONAL_NEGATIVE_RE.finditer(text)), cost_decreases)
    positive_positions.extend(match.start() for match in cost_decreases)
    negative_positions.extend(match.start() for match in cost_increases)
    latest_positive = max(positive_positions, default=-1)
    latest_negative = max(negative_positions, default=-1)
    if expected == "positive":
        return latest_positive >= 0 and latest_positive > latest_negative
    if expected == "negative":
        return latest_negative >= 0 and latest_negative > latest_positive
    return False


def _directional_chinese_is_consistent(value: object, expected: str) -> bool:
    text = _normalised_text(value)
    if not text or _NEGATED_DIRECTION_ZH_RE.search(text):
        return False
    positive_pattern = re.compile(r"提升|增加|增长|推动|扩大|改善|支撑|提振|增强|带动")
    negative_pattern = re.compile(
        r"压低|拖累|减少|下降|下滑|承压|侵蚀|压缩|削弱|放缓|走弱|挤压")
    cost_increases = list(_COST_INCREASE_ZH_RE.finditer(text))
    cost_decreases = list(_COST_DECREASE_ZH_RE.finditer(text))

    def outside(pattern: re.Pattern, spans: list[re.Match]) -> list[int]:
        return [
            match.start() for match in pattern.finditer(text)
            if not any(span.start() <= match.start() < span.end() for span in spans)
        ]

    positive_positions = outside(positive_pattern, cost_increases)
    negative_positions = outside(negative_pattern, cost_decreases)
    positive_positions.extend(match.start() for match in cost_decreases)
    negative_positions.extend(match.start() for match in cost_increases)
    latest_positive = max(positive_positions, default=-1)
    latest_negative = max(negative_positions, default=-1)
    if expected == "positive":
        return latest_positive >= 0 and latest_positive > latest_negative
    if expected == "negative":
        return latest_negative >= 0 and latest_negative > latest_positive
    return False


def _is_public_relation_eligible(
    candidate: dict, *, theme_direction: str, min_confidence: float,
    article: dict | None = None, brief: dict | None = None,
) -> bool:
    """Gate public picks on a grounded event/business/financial relationship."""
    if candidate.get("relevance_status") != "scored":
        return False
    if float(candidate.get("public_relation_score") or 0.0) < 3.0:
        return False
    if float(candidate.get("public_relation_confidence") or 0.0) < min_confidence:
        return False
    if candidate.get("relation_type") not in _PUBLIC_RELATION_TYPES - {"none"}:
        return False
    required_effect = "negative" if _norm_theme_direction(theme_direction) == "bearish" else "positive"
    if candidate.get("directional_effect") != required_effect:
        return False
    if not _directional_pathway_is_consistent(
            candidate.get("financial_pathway"), required_effect):
        return False
    if not _public_relation_evidence_is_grounded(candidate, article, brief):
        return False
    if not all(_normalised_text(candidate.get(field)) for field in (
        "business_fact", "theme_connection", "financial_pathway",
    )):
        return False
    if candidate.get("impact_channel") in {"valuation_only", "market_beta", "none"}:
        return False
    if candidate.get("exposure_type") == "factor_proxy":
        return False
    return True


def _semantic_score(candidate: dict) -> float:
    """Absolute theme score; market cap and traversal order are deliberately absent."""
    base = scoring.theme_exposure(
        candidate.get("theme_relevance", candidate.get("ai_relevance", 1.0)),
        candidate.get("exposure_type", "unclear"),
        candidate.get("confidence", 0.0),
    )
    return base * _SPECIFICITY_FACTOR.get(candidate.get("theme_specificity"), 0.0) * (
        _MATERIALITY_FACTOR.get(candidate.get("materiality"), 0.0)
    ) * _EVIDENCE_FACTOR.get(candidate.get("evidence_strength"), 0.0)


def _selection_score(candidate: dict) -> float:
    """Theme-dominant 80/20 score; article evidence never alters eligibility."""
    semantic = _clamp_float(
        candidate.get("semantic_score"), 0.0, 1.0,
        default=_semantic_score(candidate),
    )
    article = _clamp_float(
        candidate.get("article_support"), 0.0, 1.0, default=0.0)
    return THEME_SCORE_WEIGHT * semantic + ARTICLE_SCORE_WEIGHT * article


def _is_semantically_eligible(candidate: dict, *, threshold: float,
                              min_confidence: float) -> bool:
    """Hard evidence gate applied before output slots are filled."""
    if candidate.get("relevance_status") != "scored":
        return False
    theme_relevance = float(
        candidate.get("theme_relevance", candidate.get("ai_relevance")) or 0.0)
    if theme_relevance < threshold:
        return False
    if float(candidate.get("confidence") or 0.0) < min_confidence:
        return False
    if candidate.get("exposure_type") in {"factor_proxy", "diversified", "unclear"}:
        return False
    if candidate.get("theme_specificity") not in {"company_specific", "industry_specific"}:
        return False
    if candidate.get("impact_channel") not in {
        "revenue_demand", "input_cost_margin", "financing_sensitive_demand",
        "supply_chain_orders", "policy_or_regulatory",
    }:
        return False
    if candidate.get("evidence_strength") not in {"explicit", "derived"}:
        return False
    materiality = candidate.get("materiality")
    if materiality not in {"high", "medium"}:
        if not (
            materiality == "unknown"
            and theme_relevance >= 4.0
            and candidate.get("exposure_type") in {"direct", "supply_chain"}
            and candidate.get("evidence_strength") == "explicit"
        ):
            return False
    return _semantic_score(candidate) >= 0.20


def _norm_theme_direction(v) -> str:
    """Only an explicit bearish brief may activate inverse products."""
    return "bearish" if str(v or "").strip().lower() == "bearish" else "bullish"


class ThemeWorkflow:
    def __init__(
        self,
        llm: LLMClient,
        quotes: AInvestClient,
        opts: dict | None = None,
        *,
        marketcode_resolver=None,
    ):
        self.llm = llm
        self.quotes = quotes
        self.opts = {**DEFAULTS, **(opts or {})}
        self.as_of = _dt.date.today().isoformat()
        self._marketcode_resolver = marketcode_resolver
        self._marketcode_resolver_loaded = marketcode_resolver is not None
        self._theme_profile: dict = {}
        self._exact_theme = ""
        # Reset at the start of every run. This remains private because public stock
        # output stays capped by ``stock_target`` while ETF discovery may use a
        # broader, still-bounded evidence set.
        self._last_stock_evidence: list[dict] = []
        self._last_stock_public_reserves: list[dict] = []
        self._last_stock_public_rejections: dict[str, str] = {}
        self._last_stock_membership_codes: tuple[str, ...] = ()
        self._last_etf_membership_codes: tuple[str, ...] = ()
        self._last_stock_narrative_errors: dict[str, str] = {}
        self._last_stock_narrative_retried_codes: set[str] = set()
        self._last_stock_narrative_transport_error: str | None = None

    # -- stage 2 -------------------------------------------------------------
    def theme_profile(self, theme: str) -> dict:
        """Freeze the trusted theme definition before any article is supplied."""
        exact_theme = _normalised_text(theme)
        try:
            raw = self.llm.chat_json(
                prompts.THEME_PROFILE_SYS,
                prompts.THEME_PROFILE_USER.format(theme=exact_theme),
                max_tokens=4000,
            )
        except Exception as exc:
            _log(f"theme profile failed ({exc}); using exact-label fallback")
            raw = {}
        raw = raw if isinstance(raw, dict) else {}
        exact_echo = _normalised_text(raw.get("exact_theme"))
        if exact_echo != exact_theme:
            exact_echo = exact_theme
        core_entities = []
        for value in raw.get("core_entities") or []:
            entity = _normalise_entity(value, source="theme_profile")
            if entity is not None:
                core_entities.append(entity)
            if len(core_entities) >= int(self.opts["stock_theme_entity_lane"]):
                break
        profile = {
            "exact_theme": exact_echo,
            "theme_cn": _normalised_text(raw.get("theme_cn")),
            "canonical_name": _normalised_text(raw.get("canonical_name")) or exact_theme,
            "canonical_definition": _normalised_text(raw.get("canonical_definition")),
            "aliases": _string_list(raw.get("aliases")),
            "direct_business_models": _string_list(raw.get("direct_business_models")),
            "pure_play_descriptors": _string_list(raw.get("pure_play_descriptors")),
            "enablers": _string_list(raw.get("enablers")),
            "exclusions": _string_list(raw.get("exclusions")),
            "core_entities": core_entities,
        }
        # JSON round-tripping makes the frozen value independent of the model
        # object's containers and prevents the article brief from mutating it.
        self._theme_profile = json.loads(json.dumps(profile, ensure_ascii=False))
        self._exact_theme = exact_theme
        _log(
            f"theme profile ready: {len(core_entities)} proposed core entities; "
            f"{len(_theme_profile_terms(exact_theme, profile))} recall terms"
        )
        return json.loads(json.dumps(self._theme_profile, ensure_ascii=False))

    def event_brief(
        self, inp: dict, art: dict, theme_profile: dict | None = None,
    ) -> dict:
        profile = theme_profile if isinstance(theme_profile, dict) else {
            "exact_theme": inp["theme"], "theme_cn": "",
            "canonical_name": inp["theme"], "canonical_definition": "",
            "aliases": [], "direct_business_models": [],
            "pure_play_descriptors": [], "enablers": [], "exclusions": [],
            "core_entities": [],
        }
        lede = _normalised_text(art.get("text"))[:ARTICLE_ANCHOR_TEXT_CHARS]
        user = prompts.EVENT_BRIEF_USER.format(
            theme=inp["theme"],
            theme_profile=json.dumps(profile, ensure_ascii=False),
            date=inp["date"], title=art.get("title", ""),
            url=inp["url"], lede=lede,
            excerpt=art.get("text", "")[:ARTICLE_BODY_TEXT_CHARS],
        )
        try:
            brief = self.llm.chat_json(
                prompts.EVENT_BRIEF_SYS, user, max_tokens=6000,
            )
        except Exception as exc:
            _log(
                f"event brief request failed ({exc}); "
                "using exact-theme screening fallback"
            )
            brief = {}
        if not isinstance(brief, dict):
            brief = {}
        theme_cn = brief.get("theme_cn")
        frozen_theme_cn = _normalised_text(profile.get("theme_cn"))
        brief["theme_cn"] = (
            frozen_theme_cn
            or (theme_cn.strip() if isinstance(theme_cn, str) else "")
        )
        brief["theme_direction"] = _norm_theme_direction(brief.get("theme_direction"))
        # Preserve the literal user input as the only brief-carried event source.
        # All other brief fields are model-generated analysis and may guide
        # discovery, but they cannot ground a public stock relationship.
        brief["input_theme"] = _normalised_text(inp["theme"])
        title_lede_entities = []
        region = _article_anchor_region(art)
        for value in brief.get("title_lede_entities") or []:
            entity = _normalise_entity(value, source="title_lede")
            if entity is not None and _entity_is_mentioned(entity, region):
                title_lede_entities.append(entity)
            elif entity is not None:
                _log(
                    "article entity rejected: identity was not literal in the "
                    f"title/lede ({entity.get('name') or entity.get('ticker')})"
                )
            if len(title_lede_entities) >= int(self.opts["stock_article_anchor_lane"]):
                break
        brief["title_lede_entities"] = title_lede_entities
        brief["body_entities"] = [
            entity for value in (brief.get("body_entities") or [])
            if (entity := _normalise_entity(value, source="article_body")) is not None
        ][:40]
        return brief

    def event_ecosystem(
        self, inp: dict, brief: dict, profile: dict, article: dict,
    ) -> list[dict]:
        """Discover event-adjacent issuers without granting them publication."""
        target = max(0, int(self.opts.get("stock_target", 8)))
        if target == 0:
            return []
        limit = min(
            int(self.opts.get("stock_candidate_budget", 120)),
            max(24, target * 3),
        )
        if not limit:
            return []
        user = prompts.EVENT_ECOSYSTEM_USER.format(
            theme=inp["theme"],
            theme_profile=json.dumps(profile, ensure_ascii=False),
            brief=json.dumps(brief, ensure_ascii=False),
            article=json.dumps({
                "title": article.get("title", ""),
                "url": article.get("url", ""),
                "excerpt": article.get("text", "")[:ARTICLE_BODY_TEXT_CHARS],
            }, ensure_ascii=False),
            limit=limit,
        )
        started = time.monotonic()
        _log(f"event ecosystem LLM request started (limit={limit})")
        try:
            raw = self.llm.chat_json(
                prompts.EVENT_ECOSYSTEM_SYS,
                user,
                max_tokens=5000,
                response_schema=_event_ecosystem_response_schema(limit),
                schema_name="event_stock_ecosystem",
            )
        except Exception as exc:
            _log(f"event ecosystem request failed ({exc}); continuing with article entities")
            return []
        rows = raw.get("entities", []) if isinstance(raw, dict) else []
        output: list[dict] = []
        seen: set[tuple[str, str, str]] = set()
        for value in rows if isinstance(rows, list) else []:
            entity = _normalise_entity(value, source="event_ecosystem")
            if entity is None or float(entity.get("confidence") or 0.0) < 0.45:
                continue
            key = (
                str(entity.get("market_code") or "").casefold(),
                str(entity.get("ticker") or "").casefold(),
                str(entity.get("name") or "").casefold(),
            )
            if key in seen:
                continue
            seen.add(key)
            output.append(entity)
        _log(
            f"event ecosystem stage finished in {time.monotonic() - started:.1f}s; "
            f"{len(output)} grounded discovery hints"
        )
        return output

    # -- stage 3 -------------------------------------------------------------
    def _pool_rows(self, pool, indicator_id, req_id, sort_pos):
        """Lazily yield named rows from a ranked API pool (market cap / AUM order)."""
        for r in self.quotes.iter_ranked(
                pool, [{"id": indicator_id, "req_unique_id": req_id}], sort_pos=sort_pos):
            r["name"] = self.quotes.name_of(r["code"])
            r["metric"] = r["values"].get(req_id)
            yield r

    def stock_source(self):
        """Yield live ranked stocks, then recover from the local ``ES`` universe."""
        indicators = [
            {"id": MKTCAP_ID, "req_unique_id": "mktcap"},
            {"id": "55", "req_unique_id": "name"},
            {"id": "company_introduction", "req_unique_id": "company_introduction"},
            {"id": "ext_metric_sector_1_name", "req_unique_id": "sector"},
            {"id": "ext_metric_sector_3_name", "req_unique_id": "industry"},
        ]
        seen: set[str] = set()
        live_failure: Exception | None = None
        try:
            rows = self.quotes.iter_ranked(
                STOCK_POOL, indicators, sort_pos=0, strict=True,
            )
            for row in rows:
                code = str(row.get("code") or "").strip().upper()
                if not _SECURITY_CODE_RE.fullmatch(code) or code in seen:
                    continue
                values = row.get("values") or {}
                name = values.get("name")
                if not _normalised_text(name):
                    try:
                        name = self.quotes.name_of(code)
                    except Exception:
                        name = code.partition(":")[2] or code
                row.update({
                    "code": code,
                    "name": name,
                    "metric": values.get("mktcap"),
                    "company_introduction": values.get("company_introduction") or "",
                    "sector": values.get("sector") or "",
                    "industry": values.get("industry") or "",
                    "universe_source": "live_ranked",
                })
                seen.add(code)
                yield row
        except Exception as exc:
            live_failure = exc
            _log(f"stock live universe failed ({exc}); using offline ES recovery")

        offline_codes: list[str] = []
        try:
            loader = getattr(self.quotes, "security_codes")
            offline_codes = list(loader("ES"))
        except Exception as exc:
            _log(f"stock offline ES universe failed ({exc})")
        opts = getattr(self, "opts", {})
        offline_unique: list[str] = []
        offline_seen = set(seen)
        for raw_code in offline_codes:
            code = str(raw_code or "").strip().upper()
            if not _SECURITY_CODE_RE.fullmatch(code):
                continue
            if code in offline_seen:
                continue
            offline_seen.add(code)
            offline_unique.append(code)
        offline_added = 0
        # Hydrate lazily in bounded batches. The caller stops this generator
        # after reaching its *unique issuer* boundary, so duplicate share
        # classes cannot exhaust a raw-symbol slice and later profiles are not
        # fetched unless they are actually needed.
        profile_batch_size = max(
            20,
            min(100, int(opts.get("stock_candidate_budget", 120))),
        )
        for batch_start in range(0, len(offline_unique), profile_batch_size):
            batch_codes = offline_unique[
                batch_start:batch_start + profile_batch_size
            ]
            offline_profiles: dict[str, dict] = {}
            try:
                profile_loader = getattr(self.quotes, "security_profiles")
                raw_profiles = profile_loader(batch_codes)
                if isinstance(raw_profiles, dict):
                    offline_profiles = {
                        str(raw_code or "").strip().upper(): facts
                        for raw_code, facts in raw_profiles.items()
                        if isinstance(facts, dict)
                    }
            except Exception as exc:
                _log(
                    f"stock offline ES profile hydration failed ({exc}); "
                    "retaining code/name fallback"
                )
            for offset, code in enumerate(batch_codes, 1):
                offline_rank = batch_start + offset
                seen.add(code)
                offline_added += 1
                facts = offline_profiles.get(code, {})
                try:
                    name = self.quotes.name_of(code)
                except Exception:
                    name = code.partition(":")[2]
                row = {
                    "code": code,
                    "rank": len(seen),
                    "metric": None,
                    "name": facts.get("name") or name or code.partition(":")[2],
                    "company_introduction": facts.get("company_introduction") or "",
                    "sector": facts.get("sector") or "",
                    "industry": facts.get("industry") or "",
                    "universe_source": "offline_es",
                    "offline_rank": offline_rank,
                }
                for key, value in facts.items():
                    if value not in (None, ""):
                        row.setdefault(key, value)
                yield row
        if offline_added:
            _log(f"stock offline ES recovery made {offline_added} additional codes available")
        if not seen:
            detail = str(live_failure or "stock snapshot returned an empty universe")
            raise SelectionUniverseError(
                "stock", int(opts.get("stock_target", 8)), 0,
                ("live_ranked", "offline_es", detail),
            )

    def _resolver(self):
        if not self._marketcode_resolver_loaded:
            self._marketcode_resolver_loaded = True
            try:
                from marketcode_resolver import MarketCodeResolver
                self._marketcode_resolver = MarketCodeResolver()
            except Exception as exc:
                self._marketcode_resolver = None
                _log(f"market-code resolver unavailable ({exc}); entity lanes disabled")
        return self._marketcode_resolver

    def _resolve_entity_hints(self, entities: list[dict]) -> list[dict]:
        if not entities:
            return []
        resolver = self._resolver()
        if resolver is None:
            return []
        output: list[dict] = []
        for entity in entities:
            try:
                if callable(getattr(resolver, "resolve_entity", None)):
                    result = resolver.resolve_entity(
                        market_code=entity.get("market_code"),
                        ticker=entity.get("ticker"),
                        name=entity.get("name"),
                        market_hint=entity.get("market_hint"),
                    )
                else:
                    # Lightweight injected resolvers used by embedders/tests may
                    # expose a single entity-oriented ``resolve`` operation.
                    result = resolver.resolve(entity)
                value = (
                    result.get
                    if isinstance(result, dict)
                    else lambda key, default=None: getattr(result, key, default)
                )
                status = str(value("status") or "")
                market_code = str(value("market_code") or "").strip().upper()
                match_kind = value("match_kind")
                resolved_flag = value("resolved")
                is_resolved = (
                    bool(resolved_flag)
                    if resolved_flag is not None
                    else status == "resolved" and bool(market_code)
                )
                security_name = value("security_name") or value("name") or ""
                security_name_zh = value("security_name_zh") or ""
                _log(
                    f"entity resolution source={entity.get('source')} "
                    f"query={entity.get('name') or entity.get('ticker') or entity.get('market_code')} "
                    f"status={status or '-'} code={market_code or '-'} "
                    f"match={match_kind or '-'}"
                )
            except Exception as exc:
                _log(
                    f"entity resolution failed source={entity.get('source')} "
                    f"query={entity.get('name') or entity.get('ticker')}: {exc}"
                )
                continue
            if not is_resolved or not market_code:
                continue
            market, _, _ = market_code.partition(":")
            if market not in _US_STOCK_MARKETS:
                continue
            resolved = dict(entity)
            resolved.update({
                "resolved_code": market_code,
                "resolved_name": security_name or entity.get("name") or "",
                "resolved_name_zh": security_name_zh,
                "resolution_match_kind": match_kind,
                "resolution_status": status,
            })
            output.append(resolved)
        return output

    def stock_candidates(
        self,
        brief: dict,
        theme_profile: dict | None = None,
        article: dict | None = None,
        ecosystem_entities: list[dict] | None = None,
    ) -> list[dict]:
        """Build entity, event-ecosystem, broad-liquidity, and theme lanes."""
        universe_cap = int(self.opts["stock_universe"])
        if universe_cap <= 0:
            raise ValueError("stock_universe must be greater than 0")
        safety_cap = self.opts.get("max_scan")
        if safety_cap:
            universe_cap = min(universe_cap, int(safety_cap))
        # Count the configured boundary in unique issuers. Retain alternate
        # share-class rows encountered inside that walk so entity/article hints
        # can still merge their provenance into the retained issuer.
        universe: list[dict] = []
        universe_issuers: set[str] = set()
        for row in self.stock_source():
            code = str(row.get("code") or "").strip().upper()
            if not _SECURITY_CODE_RE.fullmatch(code):
                continue
            issuer = _issuer_key(row) or code.casefold()
            universe.append(row)
            universe_issuers.add(issuer)
            if len(universe) >= universe_cap:
                if len(universe_issuers) >= universe_cap:
                    break
        universe_by_code = {row["code"]: row for row in universe}
        budget = max(1, int(self.opts.get("stock_candidate_budget", 120)))
        profile = theme_profile if isinstance(theme_profile, dict) else {}
        legacy_mode = not profile and article is None
        exact_theme = _normalised_text(
            profile.get("exact_theme") or self._exact_theme)

        theme_hints = [
            entity for value in (profile.get("core_entities") or [])
            if (entity := _normalise_entity(value, source="theme_profile")) is not None
        ][:max(0, int(self.opts["stock_theme_entity_lane"]))]
        anchor_region = _article_anchor_region(article or {})
        article_hints = [
            entity for value in (brief.get("title_lede_entities") or [])
            if (entity := _normalise_entity(value, source="title_lede")) is not None
            and (article is None or _entity_is_mentioned(entity, anchor_region))
        ][:max(0, int(self.opts["stock_article_anchor_lane"]))]
        article_body_region = _normalised_text((article or {}).get("text"))
        body_hints = [
            entity for value in (brief.get("body_entities") or [])
            if (entity := _normalise_entity(value, source="article_body")) is not None
            and (article is None or _entity_is_mentioned(entity, article_body_region))
        ][:max(0, min(40, budget))]
        ecosystem_hints = [
            entity for value in (ecosystem_entities or [])
            if (entity := _normalise_entity(value, source="event_ecosystem")) is not None
        ][:max(0, budget)]
        resolved_theme = self._resolve_entity_hints(theme_hints)
        resolved_article = self._resolve_entity_hints(article_hints)
        resolved_body = self._resolve_entity_hints(body_hints)
        resolved_ecosystem = self._resolve_entity_hints(ecosystem_hints)

        resolved_codes = {
            entity["resolved_code"] for entity in [
                *resolved_theme, *resolved_article, *resolved_body,
                *resolved_ecosystem,
            ]
        }
        outside_codes = sorted(resolved_codes - set(universe_by_code))
        outside_profiles: dict[str, dict] = {}
        if outside_codes:
            try:
                outside_profiles = self.quotes.security_profiles(outside_codes)
            except Exception as exc:
                _log(
                    f"anchor live confirmation failed for {len(outside_codes)} "
                    f"out-of-universe codes ({exc})"
                )
                outside_profiles = {}
            _log(
                f"anchor live profile confirmation: {len(outside_profiles)}/{len(outside_codes)} "
                "locally validated out-of-universe stocks returned a profile"
            )

        selected: list[dict] = []
        selected_by_code: dict[str, dict] = {}
        selected_by_issuer: dict[str, dict] = {}

        def merge_candidate(
            existing: dict, *, provenance: str, entity: dict | None,
        ) -> None:
            sources = existing.setdefault("candidate_provenance", [])
            if provenance not in sources:
                sources.append(provenance)
            if provenance == "theme_profile":
                existing["candidate_lane"] = "theme_entity"
                if entity:
                    existing.update({
                        "entity_role": entity.get("role"),
                        "entity_confidence": entity.get("confidence"),
                        "resolution_match_kind": entity.get("resolution_match_kind"),
                    })
            if entity and entity.get("resolved_name_zh"):
                existing["name_zh"] = entity["resolved_name_zh"]
            if provenance == "title_lede":
                existing["guaranteed_article_anchor"] = True
                literal = _verified_candidate_article_evidence(
                    existing, article, entity)
                if literal:
                    existing["article_anchor_evidence"] = literal
                    existing["verified_article_evidence"] = literal
            if provenance in {"article_body", "event_ecosystem"} and entity:
                existing["event_relation_hint"] = entity.get("role")
                literal = _verified_candidate_article_evidence(
                    existing, article, entity)
                if literal:
                    existing["verified_article_evidence"] = literal

        def add_candidate(
            row: dict, *, lane: str, provenance: str,
            entity: dict | None = None,
        ) -> bool:
            code = str(row.get("code") or "").strip().upper()
            if not code:
                return False
            if code in selected_by_code:
                merge_candidate(
                    selected_by_code[code], provenance=provenance, entity=entity)
                return True
            candidate = dict(row)
            candidate["code"] = code
            if entity and entity.get("resolved_name"):
                candidate["name"] = entity["resolved_name"]
            if not _normalised_text(candidate.get("name")):
                try:
                    candidate["name"] = self.quotes.name_of(code)
                except Exception:
                    candidate["name"] = code.partition(":")[2] or code
            issuer = _issuer_key(candidate) or code.casefold()
            if issuer in selected_by_issuer:
                retained = selected_by_issuer[issuer]
                merge_candidate(retained, provenance=provenance, entity=entity)
                # Remember the alternate code so later lanes also merge into the
                # retained issuer rather than repeatedly treating it as new.
                selected_by_code[code] = retained
                _log(
                    f"candidate dedupe merged {code} into {retained['code']}: "
                    f"duplicate issuer {issuer}"
                )
                return True
            # Capacity applies only to a genuinely new issuer. Duplicate identity
            # evidence must still merge after reservations have filled the budget.
            if len(selected) >= budget:
                return False
            candidate.update({
                "candidate_lane": lane,
                "candidate_provenance": [provenance],
                "issuer_key": issuer,
            })
            if entity:
                candidate.update({
                    "entity_role": entity.get("role"),
                    "entity_confidence": entity.get("confidence"),
                    "resolution_match_kind": entity.get("resolution_match_kind"),
                })
                if entity.get("resolved_name_zh"):
                    candidate["name_zh"] = entity["resolved_name_zh"]
            if provenance == "title_lede":
                candidate["guaranteed_article_anchor"] = True
                literal = _verified_candidate_article_evidence(
                    candidate, article, entity)
                if literal:
                    candidate["article_anchor_evidence"] = literal
                    candidate["verified_article_evidence"] = literal
            if provenance in {"article_body", "event_ecosystem"} and entity:
                candidate["event_relation_hint"] = entity.get("role")
                literal = _verified_candidate_article_evidence(
                    candidate, article, entity)
                if literal:
                    candidate["verified_article_evidence"] = literal
            selected.append(candidate)
            selected_by_code[code] = candidate
            selected_by_issuer[issuer] = candidate
            return True

        def row_for_entity(entity: dict) -> dict | None:
            code = entity["resolved_code"]
            if code in universe_by_code:
                return universe_by_code[code]
            profile_row = outside_profiles.get(code)
            if not isinstance(profile_row, dict):
                _log(f"entity rejected code={code}: outside universe and not live-confirmed")
                return None
            return {
                "code": code,
                "rank": None,
                "metric": None,
                "name": profile_row.get("name") or entity.get("resolved_name") or code,
                "company_introduction": profile_row.get("company_introduction") or "",
                "sector": profile_row.get("sector") or "",
                "industry": profile_row.get("industry") or "",
                "outside_liquidity_universe": True,
            }

        # Reservations flow in the approved order. A duplicate theme/article
        # entity preserves theme-lane priority while acquiring the guarantee.
        for entity in resolved_theme:
            row = row_for_entity(entity)
            if row is not None:
                add_candidate(
                    row, lane="theme_entity", provenance="theme_profile", entity=entity)
        for entity in resolved_article:
            row = row_for_entity(entity)
            if row is not None:
                add_candidate(
                    row, lane="article_anchor", provenance="title_lede", entity=entity)
        for entity in resolved_body:
            row = row_for_entity(entity)
            if row is not None:
                add_candidate(
                    row, lane="article_entity", provenance="article_body", entity=entity)
        for entity in resolved_ecosystem:
            row = row_for_entity(entity)
            if row is not None:
                add_candidate(
                    row, lane="event_ecosystem", provenance="event_ecosystem", entity=entity)

        desired_broad = max(0, int(self.opts.get("stock_broad_lane", 20)))
        broad_count = 0
        for row in universe:
            if broad_count >= desired_broad or len(selected) >= budget:
                break
            before = len(selected)
            add_candidate(row, lane="broad_liquidity", provenance="broad_liquidity")
            if len(selected) > before:
                broad_count += 1

        if profile:
            brief_terms = _theme_profile_terms(exact_theme, profile)
            direct_terms = [
                *_string_list(profile.get("direct_business_models")),
                *_string_list(profile.get("pure_play_descriptors")),
            ]
        else:
            # Compatibility for direct unit callers. Production always supplies
            # the frozen profile, so article-derived fields never drive recall.
            brief_terms = _brief_lane_terms(brief)
            direct_terms = _brief_lane_terms(brief, _STOCK_DIRECT_LANE_BRIEF_FIELDS)

        # Reserve most thematic capacity for the brief's explicit beneficiaries,
        # suppliers, and ETF mandate terms. A capped amount of depth per industry
        # lets several pure plays compete (for example DHI, PHM, LEN, NVR), while
        # preventing one industry from monopolising the complete work budget.
        thematic_capacity = max(0, budget - len(selected))
        direct_budget = min(
            thematic_capacity,
            max(1, math.ceil(0.75 * thematic_capacity)),
        ) if thematic_capacity else 0
        direct_group_cap = max(4, min(10, math.ceil(max(direct_budget, 1) / 6)))
        # De-duplicate equivalent prose while preserving the brief's priority
        # order (direct beneficiaries, suppliers, then ETF mandate terms).
        unique_direct_terms: list[str] = []
        seen_direct_terms: set[str] = set()
        for term in direct_terms:
            key = re.sub(r"\s+", " ", str(term).casefold()).strip()
            if not key or key in seen_direct_terms:
                continue
            seen_direct_terms.add(key)
            unique_direct_terms.append(term)

        matches_by_term: list[
            tuple[str, deque[tuple[float, str, str, dict]]]
        ] = []
        for term in unique_direct_terms:
            matches: list[tuple[float, str, str, dict]] = []
            for row in universe:
                score = _taxonomy_match_score(row, [term])
                if score <= 0:
                    continue
                group_key = str(
                    row.get("industry") or row.get("sector") or "other"
                ).casefold()
                matches.append((
                    score, _lane_tiebreak(term, row["code"]), group_key, row,
                ))
            matches.sort(key=lambda item: (-item[0], item[1], item[3]["code"]))
            if matches:
                matches_by_term.append((term, deque(matches)))

        direct_group_counts: dict[str, int] = {}
        direct_count = 0
        # One candidate per term per pass: a long list of mortgage/REIT/bank
        # matches cannot crowd all but one homebuilder out of the work set.
        while direct_count < direct_budget and matches_by_term:
            made_progress = False
            remaining_terms: list[
                tuple[str, deque[tuple[float, str, str, dict]]]
            ] = []
            for term, matches in matches_by_term:
                if direct_count >= direct_budget:
                    remaining_terms.append((term, matches))
                    continue
                chosen = None
                while matches:
                    score, _, group_key, row = matches.popleft()
                    if row["code"] in selected_by_code:
                        continue
                    if direct_group_counts.get(group_key, 0) >= direct_group_cap:
                        continue
                    chosen = (score, group_key, row)
                    break
                if chosen is not None:
                    score, group_key, row = chosen
                    candidate = dict(row)
                    candidate["candidate_lane_priority"] = "direct_pathway"
                    candidate["candidate_lane_term"] = term
                    candidate["candidate_lane_score"] = score
                    if add_candidate(
                        candidate, lane="theme_industry", provenance="theme_taxonomy"):
                        direct_group_counts[group_key] = (
                            direct_group_counts.get(group_key, 0) + 1)
                        direct_count += 1
                        made_progress = True
                if matches:
                    remaining_terms.append((term, matches))
            matches_by_term = remaining_terms
            if not made_progress:
                break

        industry_groups: dict[str, list[tuple[float, dict]]] = {}
        for row in universe:
            if row["code"] in selected_by_code:
                continue
            score = _taxonomy_match_score(row, brief_terms)
            if score <= 0:
                continue
            row["candidate_lane_score"] = score
            group = str(row.get("industry") or row.get("sector") or "other").casefold()
            industry_groups.setdefault(group, []).append((score, row))
        for group_key, group in industry_groups.items():
            group.sort(key=lambda item: (
                -item[0], _lane_tiebreak(group_key, item[1]["code"]),
                item[1]["code"],
            ))

        # Round-robin industry groups so one broad phrase (for example "banks")
        # cannot consume the whole thematic lane before adjacent pathways compete.
        ordered_groups = sorted(
            industry_groups.values(),
            key=lambda group: (
                -group[0][0],
                _lane_tiebreak(
                    group[0][1].get("industry") or group[0][1].get("sector"),
                    group[0][1]["code"],
                ),
                group[0][1]["code"],
            ),
        )
        cursor = 0
        while len(selected) < budget and ordered_groups:
            group = ordered_groups[cursor % len(ordered_groups)]
            _, row = group.pop(0)
            if row["code"] not in selected_by_code:
                add_candidate(
                    row, lane="theme_industry", provenance="theme_taxonomy")
            if not group:
                ordered_groups.remove(group)
                if not ordered_groups:
                    break
                cursor %= len(ordered_groups)
            else:
                cursor += 1

        # Preserve a deterministic fallback when the brief contains no taxonomy
        # vocabulary. These rows still compete together and do not occupy output
        # slots merely by passing early. Event-asset aliases above are recall-only
        # and therefore flow through the same LLM evidence gate as every other row.
        if len(selected) < budget:
            for row in universe:
                if row["code"] in selected_by_code:
                    continue
                add_candidate(row, lane="broad_fallback", provenance="broad_fallback")
                if len(selected) >= budget:
                    break
        theme_count = sum(row.get("candidate_lane") == "theme_industry" for row in selected)
        broad_rows = [
            row for row in selected if row.get("candidate_lane") == "broad_liquidity"
        ]
        thematic_rows = [
            row for row in selected if row.get("candidate_lane") != "broad_liquidity"
        ]
        for index, row in enumerate(selected, 1):
            row["candidate_id"] = f"S{index:04d}"
        if not legacy_mode:
            _log(
                f"stocks: built {len(selected)} candidates from {len(universe)} liquid names "
                f"({sum(row.get('candidate_lane') == 'theme_entity' for row in selected)} "
                f"theme entities, {sum(row.get('candidate_lane') == 'article_anchor' for row in selected)} "
                "title/lede anchors, "
                f"{sum(row.get('candidate_lane') == 'article_entity' for row in selected)} "
                "article-body entities, "
                f"{sum(row.get('candidate_lane') == 'event_ecosystem' for row in selected)} "
                f"event-ecosystem entities, {broad_count} broad, {theme_count} theme-taxonomy; "
                f"{sum(bool(row.get('guaranteed_article_anchor')) for row in selected)} guaranteed)"
            )
            return selected

        # Preserve the former broad/theme interleaving for direct callers that
        # intentionally exercise the legacy lane mechanics without a profile.
        interleaved: list[dict] = []
        broad_cursor = thematic_cursor = 0
        while broad_cursor < len(broad_rows) or thematic_cursor < len(thematic_rows):
            if broad_cursor < len(broad_rows):
                interleaved.append(broad_rows[broad_cursor])
                broad_cursor += 1
            for _ in range(2):
                if thematic_cursor < len(thematic_rows):
                    interleaved.append(thematic_rows[thematic_cursor])
                    thematic_cursor += 1
        _log(
            f"stocks: built {len(selected)} candidates from {len(universe)} liquid names "
            f"({broad_count} broad, {theme_count} theme-taxonomy; "
            f"{direct_count} reserved direct-pathway)"
        )
        return interleaved

    # -- stage 4 -------------------------------------------------------------
    def screen_until_target(self, rows, brief: dict, article: dict,
                            target: int, kind: str, cap: int | None = None,
                            rank_label: str = "rank") -> list[dict]:
        """Rank grounded public relationships separately from ETF theme evidence."""
        thr = float(self.opts["relevance_threshold"])
        if target < 0:
            raise ValueError("stock target cannot be negative")
        if target == 0:
            self._last_stock_evidence = []
            self._last_stock_public_reserves = []
            self._last_stock_membership_codes = ()
            return []
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
        candidates = list(rows)
        self._ensure_candidate_ids(candidates)
        scores = self.score_relevance(brief, candidates, article)
        qualified: list[dict] = []
        public_qualified: list[dict] = []
        rejection_counts: dict[str, int] = {}
        public_rejection_counts: dict[str, int] = {}
        min_confidence = float(self.opts["min_relevance_confidence"])
        theme_direction = _norm_theme_direction(brief.get("theme_direction"))
        for candidate in candidates:
            candidate.update(scores.get(
                candidate["code"],
                _normalise_relevance_object(None, status="missing"),
            ))
            if candidate.get("relevance_status") == "scored":
                candidate["semantic_score"] = _semantic_score(candidate)
            else:
                candidate["semantic_score"] = 0.0
                candidate["article_support"] = 0.0
            candidate["verified_article_support"] = _verified_article_support(
                candidate, article)
            candidate["selection_score"] = (
                THEME_SCORE_WEIGHT * _clamp_float(
                    candidate.get("semantic_score"), 0.0, 1.0, default=0.0)
                + ARTICLE_SCORE_WEIGHT * candidate["verified_article_support"]
            )
            candidate["public_semantic_score"] = (
                _clamp_float(
                    (float(candidate.get("public_relation_score") or 1.0) - 1.0) / 4.0,
                    0.0, 1.0, default=0.0,
                )
                * _clamp_float(
                    candidate.get("public_relation_confidence"),
                    0.0, 1.0, default=0.0,
                )
            )
            candidate["public_selection_score"] = (
                THEME_SCORE_WEIGHT * candidate["public_semantic_score"]
                + ARTICLE_SCORE_WEIGHT * candidate["verified_article_support"]
            )
            eligible = _is_semantically_eligible(
                candidate, threshold=thr, min_confidence=min_confidence)
            candidate["theme_eligible"] = eligible
            if eligible:
                qualified.append(candidate)
                candidate["rejection_reason"] = "eligible"
            else:
                if candidate.get("relevance_status") != "scored":
                    reason = str(candidate.get("relevance_status") or "missing")
                elif float(candidate.get("theme_relevance") or 0) < thr:
                    reason = "theme_relevance_below_threshold"
                elif float(candidate.get("confidence") or 0) < min_confidence:
                    reason = "confidence_below_threshold"
                elif candidate.get("exposure_type") in {
                    "factor_proxy", "diversified", "unclear",
                }:
                    reason = f"exposure_{candidate.get('exposure_type')}"
                else:
                    reason = "causal_evidence_gate"
                candidate["rejection_reason"] = reason
                rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
            public_eligible = _is_public_relation_eligible(
                candidate,
                theme_direction=theme_direction,
                min_confidence=min_confidence,
                article=article,
                brief=brief,
            )
            candidate["public_relation_eligible"] = public_eligible
            if public_eligible:
                public_qualified.append(candidate)
                candidate["public_rejection_reason"] = "eligible"
            else:
                if candidate.get("relevance_status") != "scored":
                    public_reason = str(candidate.get("relevance_status") or "missing")
                elif float(candidate.get("public_relation_score") or 0) < 3.0:
                    public_reason = "relation_score_below_threshold"
                elif float(candidate.get("public_relation_confidence") or 0) < min_confidence:
                    public_reason = "relation_confidence_below_threshold"
                elif candidate.get("relation_type") == "none":
                    public_reason = "no_business_relationship"
                elif candidate.get("directional_effect") != (
                    "negative" if theme_direction == "bearish" else "positive"
                ):
                    public_reason = "direction_mismatch"
                elif not _public_relation_evidence_is_grounded(
                        candidate, article, brief):
                    public_reason = "no_relation_evidence"
                else:
                    public_reason = "business_financial_pathway_gate"
                candidate["public_rejection_reason"] = public_reason
                public_rejection_counts[public_reason] = (
                    public_rejection_counts.get(public_reason, 0) + 1
                )
            _log(
                "stock diagnostic "
                f"id={candidate.get('candidate_id')} code={candidate.get('code')} "
                f"lane={candidate.get('candidate_lane') or 'unspecified'} "
                f"provenance={','.join(candidate.get('candidate_provenance') or []) or '-'} "
                f"resolution={candidate.get('resolution_match_kind') or '-'} "
                f"retry={candidate.get('relevance_retry') or '-'} "
                f"theme={float(candidate.get('theme_relevance') or 0):.2f} "
                f"public={float(candidate.get('public_relation_score') or 0):.2f} "
                f"article={float(candidate.get('article_support') or 0):.2f} "
                f"semantic={candidate['semantic_score']:.4f} "
                f"selection={candidate['selection_score']:.4f} "
                f"status={candidate.get('relevance_status')} "
                f"theme_decision={candidate.get('rejection_reason')} "
                f"public_decision={candidate.get('public_rejection_reason')}"
            )
        scanned = len(candidates)
        _log(f"{kind}: scored {scanned} by {rank_label}; "
             f"{len(qualified)} pass the structural theme gate and "
             f"{len(public_qualified)} pass the public relationship gate")
        if rejection_counts:
            _log("stock rejection reasons: " + "; ".join(
                f"{reason}={count}" for reason, count in sorted(rejection_counts.items())))
        if public_rejection_counts:
            _log("public relationship rejection reasons: " + "; ".join(
                f"{reason}={count}"
                for reason, count in sorted(public_rejection_counts.items())))

        exposure_priority = {
            "direct": 0, "enabler": 1, "supply_chain": 2,
            "beneficiary": 3, "diversified": 4, "factor_proxy": 5,
            "unclear": 6,
        }
        structural_rank_key = lambda candidate: (
            -candidate["selection_score"],
            -candidate["semantic_score"],
            exposure_priority.get(candidate.get("exposure_type"), 9),
            -float(candidate.get("theme_relevance") or 0.0),
            -float(candidate.get("confidence") or 0.0),
            int(candidate.get("rank") or 10**9),
            candidate["code"],
        )
        qualified.sort(key=structural_rank_key)

        relation_priority = {
            "direct": 0,
            "supplier": 1,
            "customer": 1,
            "partner": 1,
            "complementary": 2,
            "competitor": 2,
            "second_order": 3,
            "none": 9,
        }

        def dedupe(values: list[dict], limit: int | None = None) -> list[dict]:
            output: list[dict] = []
            issuers: set[str] = set()
            for candidate in values:
                issuer = _issuer_key(candidate) or candidate["code"].casefold()
                if issuer in issuers:
                    continue
                candidate["issuer_key"] = issuer
                issuers.add(issuer)
                output.append(candidate)
                if limit is not None and len(output) >= limit:
                    break
            return output

        # Phase one fixes public membership.  Every real candidate participates,
        # but strong, grounded relationships always outrank progressively weaker
        # evidence.  Phase two (narration) is not allowed to revisit these tiers.
        provenance_tier_sources = {
            "title_lede", "article_body", "event_ecosystem", "theme_profile",
            "theme_taxonomy",
        }
        provenance_tier_lanes = {
            "theme_entity", "article_anchor", "article_entity",
            "event_ecosystem", "theme_industry",
        }
        lane_priority = {
            "theme_entity": 0,
            "article_anchor": 1,
            "article_entity": 2,
            "event_ecosystem": 3,
            "theme_industry": 4,
            "broad_liquidity": 5,
            "broad_fallback": 6,
        }
        expected_effect = "negative" if theme_direction == "bearish" else "positive"

        def selection_tier(candidate: dict) -> int:
            if candidate.get("public_relation_eligible"):
                return 1
            if candidate.get("theme_eligible"):
                return 2
            scored = candidate.get("relevance_status") == "scored"
            concrete_relation_field = any(
                _normalised_text(candidate.get(field))
                for field in ("business_fact", "theme_connection", "financial_pathway")
            )
            if (
                scored
                and candidate.get("directional_effect") == expected_effect
                and concrete_relation_field
            ):
                return 3
            if scored and (
                float(candidate.get("public_relation_score") or 0.0) > 1.0
                or float(candidate.get("theme_relevance") or 0.0) > 1.0
            ):
                return 4
            raw_provenance = candidate.get("candidate_provenance") or []
            if isinstance(raw_provenance, str):
                raw_provenance = [raw_provenance]
            provenance = {
                str(value).strip().lower()
                for value in raw_provenance
                if str(value).strip()
            }
            if (
                provenance & provenance_tier_sources
                or str(candidate.get("candidate_lane") or "").strip().lower()
                in provenance_tier_lanes
            ):
                return 5
            return 6

        def original_rank(candidate: dict) -> int:
            try:
                return int(candidate.get("rank"))
            except (TypeError, ValueError):
                return int(candidate.get("_stock_selection_input_order") or 10**9)

        for input_order, candidate in enumerate(candidates, 1):
            candidate["_stock_selection_input_order"] = input_order
            candidate["stock_selection_tier"] = selection_tier(candidate)

        def public_rank_key(candidate: dict) -> tuple:
            lane = str(candidate.get("candidate_lane") or "").strip().lower()
            confidence = _clamp_float(
                candidate.get("public_relation_confidence"), 0.0, 1.0,
                default=_clamp_float(
                    candidate.get("confidence"), 0.0, 1.0, default=0.0),
            )
            return (
                int(candidate["stock_selection_tier"]),
                -float(candidate.get("public_relation_score") or 0.0),
                -float(candidate.get("verified_article_support") or 0.0),
                -float(candidate.get("selection_score") or 0.0),
                relation_priority.get(candidate.get("relation_type"), 9),
                -confidence,
                lane_priority.get(lane, 9),
                original_rank(candidate),
                _issuer_key(candidate) or candidate["code"].casefold(),
                candidate["code"],
            )

        public_ranked = dedupe(sorted(candidates, key=public_rank_key))
        public_ranked_codes = {candidate["code"] for candidate in public_ranked}
        self._last_stock_public_rejections = {
            candidate["code"]: str(
                candidate.get("public_rejection_reason") or "relationship_gate"
            )
            for candidate in candidates
            if not candidate.get("public_relation_eligible")
        }
        for candidate in candidates:
            if candidate["code"] not in public_ranked_codes:
                self._last_stock_public_rejections[candidate["code"]] = (
                    "duplicate_issuer"
                )
        if len(public_ranked) < target:
            sources: list[str] = []
            for candidate in candidates:
                provenance = candidate.get("candidate_provenance") or []
                if isinstance(provenance, str):
                    provenance = [provenance]
                sources.extend(str(value) for value in provenance if str(value).strip())
                lane = str(candidate.get("candidate_lane") or "").strip()
                if lane:
                    sources.append(lane)
                universe_source = str(
                    candidate.get("universe_source") or ""
                ).strip()
                if universe_source:
                    sources.append(universe_source)
            raise SelectionUniverseError(
                "stock", target, len(public_ranked), sources,
            )
        public_selected = public_ranked[:target]
        self._last_stock_public_reserves = public_ranked[target:]
        self._last_stock_membership_codes = tuple(
            candidate["code"] for candidate in public_selected
        )
        for selection_rank, candidate in enumerate(public_selected, 1):
            candidate["stock_selection_rank"] = selection_rank
            candidate["stock_membership_frozen"] = True

        tier_counts: dict[int, int] = {}
        for candidate in public_selected:
            tier = int(candidate["stock_selection_tier"])
            tier_counts[tier] = tier_counts.get(tier, 0) + 1

        evidence_limit = target
        if str(kind).strip().lower() in {"stock", "stocks"}:
            evidence_limit = max(
                target, int(self.opts.get("etf_evidence_stock_limit", 30)))
        evidence_selected = dedupe(qualified, evidence_limit)

        if str(kind).strip().lower() in {"stock", "stocks"}:
            self._last_stock_evidence = list(evidence_selected)
            public_codes = {candidate["code"] for candidate in public_selected}
            for rank, candidate in enumerate(self._last_stock_evidence, 1):
                candidate["etf_evidence_rank"] = rank
                candidate["is_public_theme_stock"] = candidate["code"] in public_codes

        boundary = (
            f"scan cap {scan_cap} reached" if scan_cap and scanned >= scan_cap
            else "universe exhausted"
        )
        evidence_note = (
            f"; retaining {len(self._last_stock_evidence)} ETF evidence stocks"
            if str(kind).strip().lower() in {"stock", "stocks"} else ""
        )
        _log(f"{kind}: {boundary}; froze exact membership "
             f"{len(public_selected)}/{target} after issuer dedupe "
             f"(tiers={','.join(f'{tier}:{count}' for tier, count in sorted(tier_counts.items())) or '-'}); "
             f"{len(self._last_stock_public_reserves)} ranked non-members remain"
             f"{evidence_note}")
        return public_selected

    def screen_stock_sets(
        self,
        rows,
        brief: dict,
        article: dict,
        target: int,
        *,
        cap: int | None = None,
        rank_label: str = "rank",
    ) -> tuple[list[dict], list[dict]]:
        """Return the published picks and the wider internal ETF evidence set."""
        published = self.screen_until_target(
            rows,
            brief,
            article,
            target,
            "stocks",
            cap=cap,
            rank_label=rank_label,
        )
        return published, list(self._last_stock_evidence)

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
            if (payload.get("market_code") is not None
                    or payload.get("candidate_id") is not None):
                return [payload]
        return []

    @staticmethod
    def _ensure_candidate_ids(rows: list[dict]) -> None:
        """Assign deterministic, application-owned identities to the complete set."""
        used: set[str] = set()
        next_number = 1
        for row in rows:
            candidate_id = str(row.get("candidate_id") or "").strip().upper()
            if not re.fullmatch(r"S\d{4,}", candidate_id) or candidate_id in used:
                while f"S{next_number:04d}" in used:
                    next_number += 1
                candidate_id = f"S{next_number:04d}"
                next_number += 1
            row["candidate_id"] = candidate_id
            used.add(candidate_id)

    @staticmethod
    def _candidate_line(row: dict, *, include_market: bool) -> str:
        """Compact factual row for the model; descriptions are untrusted data."""
        def clean(value, limit):
            return re.sub(r"\s+", " ", str(value or "")).replace("|", "/").strip()[:limit]

        parts = [str(row["code"])]
        if row.get("candidate_id"):
            parts.append(clean(row["candidate_id"], 24))
        parts.append(clean(row.get("name") or row["code"], 120))
        provenance = row.get("candidate_provenance") or []
        if isinstance(provenance, str):
            provenance = [provenance]
        if provenance:
            parts.append(f"discovery_provenance={clean(','.join(provenance), 100)}")
        if row.get("candidate_lane"):
            parts.append(f"candidate_lane={clean(row['candidate_lane'], 40)}")
        if row.get("entity_role"):
            parts.append(f"theme_profile_role={clean(row['entity_role'], 40)}")
        if row.get("entity_confidence") is not None:
            parts.append(
                f"theme_profile_confidence={clean(row['entity_confidence'], 16)}")
        if row.get("guaranteed_article_anchor"):
            parts.append("validated_title_lede_anchor=true")
        if row.get("sector"):
            parts.append(f"sector={clean(row['sector'], 100)}")
        if row.get("industry"):
            parts.append(f"industry={clean(row['industry'], 120)}")
        if row.get("company_introduction"):
            parts.append(f"business={clean(row['company_introduction'], 420)}")
        if row.get("article_anchor_evidence"):
            parts.append(
                f"title_lede_evidence={clean(row['article_anchor_evidence'], 300)}")
        if row.get("event_relation_hint"):
            parts.append(
                f"event_relationship_hint={clean(row['event_relation_hint'], 40)}")
        if row.get("verified_article_evidence"):
            parts.append(
                "verified_article_evidence="
                f"{clean(row['verified_article_evidence'], 420)}")
        if include_market:
            if all(row.get(key) is not None for key in (
                    "rvol_event", "chg_pct", "abnormal_return")):
                parts.extend((
                    f"rvol_event={row['rvol_event']:.2f}",
                    f"chg_pct={row['chg_pct']:.2f}",
                    f"abnormal_return={row['abnormal_return']:.2f}",
                ))
            else:
                parts.append("market data unavailable")
        return " | ".join(parts)

    @staticmethod
    def _join_relevance_rows(
        batch: list[dict],
        arr: list[dict],
        ticker_scope_counts: dict[str, int] | None = None,
        *,
        strict_identity: bool = False,
    ) -> dict[str, dict | None]:
        """Join model rows safely by candidate ID, full code, then unique ticker."""
        norm = lambda value: str(value or "").strip().upper()
        by_id = {norm(row["candidate_id"]): row for row in batch}
        by_code = {norm(row["code"]): row for row in batch}
        ticker_rows: dict[str, list[dict]] = {}
        for row in batch:
            ticker_rows.setdefault(norm(row["code"]).partition(":")[2], []).append(row)
        if ticker_scope_counts is None:
            ticker_scope_counts = {
                ticker: len(rows) for ticker, rows in ticker_rows.items()
            }

        response_id_counts: dict[str, int] = {}
        for obj in arr:
            candidate_id = norm(obj.get("candidate_id"))
            if candidate_id:
                response_id_counts[candidate_id] = response_id_counts.get(candidate_id, 0) + 1

        joined: dict[str, dict | None] = {}
        ownership_count: dict[str, int] = {}
        for obj in arr:
            candidate_id = norm(obj.get("candidate_id"))
            raw_code = norm(obj.get("market_code"))
            target = None

            if strict_identity and (not candidate_id or ":" not in raw_code):
                continue

            if candidate_id:
                # Candidate ID is authoritative. Never fall back to another code
                # when the model echoes an unknown, duplicate, or conflicting ID.
                target = by_id.get(candidate_id)
                if target is None or response_id_counts.get(candidate_id) != 1:
                    if target is not None:
                        joined[target["code"]] = None
                    continue
                if raw_code:
                    if ":" in raw_code:
                        if raw_code != norm(target["code"]):
                            joined[target["code"]] = None
                            continue
                    else:
                        ticker_matches = ticker_rows.get(raw_code, [])
                        if (ticker_scope_counts.get(raw_code) != 1
                                or len(ticker_matches) != 1
                                or ticker_matches[0] is not target):
                            joined[target["code"]] = None
                            continue
            elif raw_code:
                if ":" in raw_code:
                    target = by_code.get(raw_code)
                else:
                    ticker_matches = ticker_rows.get(raw_code, [])
                    target = (
                        ticker_matches[0]
                        if ticker_scope_counts.get(raw_code) == 1
                        and len(ticker_matches) == 1
                        else None
                    )
            if target is None:
                continue
            code = target["code"]
            ownership_count[code] = ownership_count.get(code, 0) + 1
            joined[code] = obj if ownership_count[code] == 1 else None
        return joined

    def _stock_relevance_call(
        self, brief: dict, batch: list[dict], article: dict,
        ticker_scope_counts: dict[str, int] | None = None,
    ) -> tuple[dict[str, dict | None], object]:
        article_txt = json.dumps({
            "title": article.get("title", ""),
            "url": article.get("url", ""),
            "excerpt": article.get("text", "")[:ARTICLE_BODY_TEXT_CHARS],
        }, ensure_ascii=False)
        profile = self._theme_profile or {
            "exact_theme": self._exact_theme,
            "canonical_name": self._exact_theme,
        }
        exact_theme = _normalised_text(
            profile.get("exact_theme") or self._exact_theme
            or brief.get("exact_theme") or brief.get("theme"))
        candidates = "\n".join(
            self._candidate_line(row, include_market=True) for row in batch)
        user = prompts.RELEVANCE_USER.format(
            theme=exact_theme,
            theme_profile=json.dumps(profile, ensure_ascii=False),
            article_context=prompts.STOCK_ARTICLE_CONTEXT.format(article=article_txt),
            brief=json.dumps(brief, ensure_ascii=False),
            candidates=candidates,
        )
        raw = self.llm.chat_json(
            prompts.STOCK_RELEVANCE_SYS,
            user,
            max_tokens=6000,
            response_schema=_relevance_response_schema(batch, stock=True),
            schema_name="stock_relevance_batch",
        )
        arr = self._coerce_relevance_rows(raw)
        return self._join_relevance_rows(
            batch, arr, ticker_scope_counts, strict_identity=True), raw

    def score_relevance(self, brief: dict, rows: list[dict], article: dict | None = None) -> dict:
        rows = list(rows)
        self._ensure_candidate_ids(rows)
        article = article or {}
        ticker_scope_counts: dict[str, int] = {}
        for row in rows:
            ticker = str(row.get("code") or "").strip().upper().partition(":")[2]
            ticker_scope_counts[ticker] = ticker_scope_counts.get(ticker, 0) + 1
        scores: dict[str, dict] = {}
        batches = list(_batched(rows, self.opts["relevance_batch"]))
        _log(
            f"stock relevance: {len(rows)} candidates in {len(batches)} "
            "serial LLM batches"
        )
        for batch_index, batch in enumerate(batches, 1):
            started = time.monotonic()
            _log(
                f"stock relevance batch {batch_index}/{len(batches)} started "
                f"({len(batch)} candidates)"
            )
            raw = None
            try:
                joined, raw = self._stock_relevance_call(
                    brief, batch, article, ticker_scope_counts)
            except Exception as exc:
                # Model adapters expose provider-specific request exceptions
                # (for example Anthropic API status errors) that do not inherit
                # from RuntimeError.  A failed batch is an evidence gap, not a
                # reason to discard scores from every other batch.
                _log(f"relevance batch failed ({exc}); marking {len(batch)} candidates unresolved")
                joined = {}
            if not joined:
                shape = (f"dict keys={list(raw)[:8]}" if isinstance(raw, dict)
                         else f"type={type(raw).__name__}")
                preview = repr(raw)[:500]
                _log(f"relevance batch returned no usable rows for {len(batch)} "
                     f"candidates (response {shape}); preview={preview}")
            _log(
                f"stock relevance batch {batch_index}/{len(batches)} finished "
                f"in {time.monotonic() - started:.1f}s"
            )
            for r in batch:
                o = joined.get(r["code"])
                if o is None:
                    # Missing from the LLM reply: mark unresolved, do NOT treat a
                    # transport/coverage gap as a confident "no exposure" verdict.
                    scores[r["code"]] = _normalise_relevance_object(
                        None, status="missing")
                    continue
                scores[r["code"]] = _normalise_relevance_object(
                    o, require_public_fields=True)
                scores[r["code"]]["relevance_retry"] = "not_needed"

        unresolved = [
            row for row in rows
            if scores.get(row["code"], {}).get("relevance_status") != "scored"
        ]
        if unresolved:
            _log(f"relevance: retrying {len(unresolved)} unresolved candidates once")
        retry_batches = list(_batched(unresolved, 5))
        for batch_index, batch in enumerate(retry_batches, 1):
            started = time.monotonic()
            _log(
                f"stock relevance retry {batch_index}/{len(retry_batches)} started "
                f"({len(batch)} candidates)"
            )
            try:
                joined, _ = self._stock_relevance_call(
                    brief, batch, article, ticker_scope_counts)
            except Exception as exc:
                _log(f"relevance retry failed ({exc}); {len(batch)} candidates remain unresolved")
                joined = {}
            for row in batch:
                obj = joined.get(row["code"])
                previous = scores.get(
                    row["code"], _normalise_relevance_object(None, status="missing"))
                retried = (
                    _normalise_relevance_object(obj, require_public_fields=True)
                    if obj is not None else dict(previous)
                )
                if retried.get("relevance_status") == "scored":
                    retried["relevance_retry"] = "recovered"
                    scores[row["code"]] = retried
                else:
                    retried["relevance_retry"] = "persistent_miss"
                    scores[row["code"]] = retried
            _log(
                f"stock relevance retry {batch_index}/{len(retry_batches)} finished "
                f"in {time.monotonic() - started:.1f}s"
            )

        n_missing = sum(
            1 for value in scores.values()
            if value.get("relevance_status") != "scored")
        if n_missing:
            _log(f"relevance: {n_missing}/{len(scores)} candidates unresolved after retry")
        return scores

    def score_etf_holding_relevance(
        self, brief: dict, rows: list[dict], article: dict | None = None,
        *, seed_scores: dict[str, dict] | None = None,
    ) -> dict[str, dict]:
        """Score ETF components from the frozen theme, reusing prior stock work."""
        rows = list(rows)
        self._ensure_candidate_ids(rows)
        article = article or {}
        article_txt = json.dumps({
            "title": article.get("title", ""),
            "url": article.get("url", ""),
            "excerpt": article.get("text", "")[:6000],
        }, ensure_ascii=False)
        brief_txt = json.dumps(brief, ensure_ascii=False)
        profile = self._theme_profile or {
            "exact_theme": self._exact_theme,
            "canonical_name": self._exact_theme,
        }
        exact_theme = _normalised_text(
            profile.get("exact_theme") or self._exact_theme
            or brief.get("exact_theme") or brief.get("theme")
        )
        scores: dict[str, dict] = {}
        seeds = seed_scores if isinstance(seed_scores, dict) else {}
        pending_rows: list[dict] = []
        for row in rows:
            raw_seed = seeds.get(row["code"])
            if not isinstance(raw_seed, dict) or raw_seed.get(
                    "relevance_status", "scored") != "scored":
                pending_rows.append(row)
                continue
            reused = _normalise_relevance_object(
                raw_seed, status=raw_seed.get("relevance_status", "scored"))
            if reused.get("relevance_status") != "scored":
                pending_rows.append(row)
                continue
            reused["relevance_retry"] = "reused_stock_score"
            reused["relevance_source"] = "stock_score_reuse"
            scores[row["code"]] = reused

        ticker_scope_counts: dict[str, int] = {}
        for row in rows:
            ticker = str(row.get("code") or "").strip().upper().partition(":")[2]
            ticker_scope_counts[ticker] = ticker_scope_counts.get(ticker, 0) + 1

        def score_batch(batch: list[dict]) -> tuple[dict[str, dict | None], object]:
            candidates = "\n".join(
                self._candidate_line(row, include_market=False) for row in batch)
            user = prompts.ETF_HOLDING_RELEVANCE_USER.format(
                theme=exact_theme,
                theme_profile=json.dumps(profile, ensure_ascii=False),
                article_context=prompts.STOCK_ARTICLE_CONTEXT.format(
                    article=article_txt),
                brief=brief_txt,
                candidates=candidates,
            )
            raw = self.llm.chat_json(
                prompts.ETF_HOLDING_RELEVANCE_SYS,
                user,
                max_tokens=8000,
                response_schema=_relevance_response_schema(batch, stock=False),
                schema_name="etf_holding_relevance_batch",
            )
            return self._join_relevance_rows(
                batch, self._coerce_relevance_rows(raw), ticker_scope_counts,
            ), raw

        batch_size = int(self.opts["etf_holding_relevance_batch"])
        batches = list(_batched(pending_rows, batch_size))
        recoverable_codes: set[str] = set()
        _log(
            f"ETF holdings relevance: {len(rows)} component companies; "
            f"{len(scores)} reused stock scores and {len(pending_rows)} companies in "
            f"{len(batches)} serial LLM batches"
        )
        for batch_index, batch in enumerate(batches, 1):
            started = time.monotonic()
            _log(
                f"ETF holdings relevance batch {batch_index}/{len(batches)} "
                f"started ({len(batch)} companies)"
            )
            raw = None
            batch_may_retry = False
            try:
                joined, raw = score_batch(batch)
                # A successful but incomplete/invalid response is safe to retry
                # by identity because it did not fail for configuration reasons.
                batch_may_retry = True
            except Exception as exc:
                # Keep component assessment fail-closed at the same request
                # boundary while allowing later batches and recovery to run.
                _log(f"ETF holding relevance batch failed ({exc}); "
                     f"marking {len(batch)} companies unresolved")
                joined = {}
                batch_may_retry = bool(
                    getattr(exc, "retryable", False)
                    or getattr(exc, "status_code", None) in {400, 413, 422}
                )
            for row in batch:
                obj = joined.get(row["code"])
                if obj is None:
                    scores[row["code"]] = _normalise_relevance_object(
                        None, status="missing")
                    continue
                scores[row["code"]] = _normalise_relevance_object(obj)
                scores[row["code"]]["relevance_retry"] = "not_needed"
            unresolved = sum(
                scores[row["code"]].get("relevance_status") != "scored"
                for row in batch
            )
            if unresolved:
                if batch_may_retry:
                    recoverable_codes.update(
                        row["code"] for row in batch
                        if scores[row["code"]].get("relevance_status") != "scored"
                    )
                shape = (
                    f"dict keys={list(raw)[:8]}" if isinstance(raw, dict)
                    else f"type={type(raw).__name__}"
                )
                _log(
                    f"ETF holdings relevance batch {batch_index}/{len(batches)} "
                    f"left {unresolved}/{len(batch)} unresolved ({shape})"
                )
            _log(
                f"ETF holdings relevance batch {batch_index}/{len(batches)} "
                f"finished in {time.monotonic() - started:.1f}s"
            )

        recoverable_rows = [
            row for row in pending_rows
            if scores.get(row["code"], {}).get("relevance_status") != "scored"
            and row["code"] in recoverable_codes
        ]
        recoverable_rows.sort(key=lambda row: (
            -float(row.get("_max_portfolio_weight_pct") or 0.0),
            -float(row.get("_aggregate_portfolio_weight_pct") or 0.0),
            row["code"],
        ))
        retry_budget = max(
            0, int(self.opts["etf_holding_relevance_retry_budget"])
        )
        unresolved_rows = recoverable_rows[:retry_budget]
        if len(recoverable_rows) > len(unresolved_rows):
            _log(
                "ETF holdings relevance: recovery budget capped at "
                f"{retry_budget}; {len(recoverable_rows) - len(unresolved_rows)} "
                "additional unresolved companies remain fail-closed"
            )
        if unresolved_rows:
            _log(
                "ETF holdings relevance: retrying "
                f"{len(unresolved_rows)} unresolved companies once"
            )
        retry_batches = list(_batched(unresolved_rows, 5))
        for batch_index, batch in enumerate(retry_batches, 1):
            started = time.monotonic()
            _log(
                f"ETF holdings relevance retry {batch_index}/{len(retry_batches)} "
                f"started ({len(batch)} companies)"
            )
            try:
                joined, _ = score_batch(batch)
            except Exception as exc:
                _log(
                    f"ETF holdings relevance retry failed ({exc}); "
                    f"{len(batch)} companies remain unresolved"
                )
                joined = {}
            for row in batch:
                obj = joined.get(row["code"])
                previous = scores.get(
                    row["code"], _normalise_relevance_object(None, status="missing")
                )
                retried = (
                    _normalise_relevance_object(obj)
                    if obj is not None else dict(previous)
                )
                retried["relevance_retry"] = (
                    "recovered"
                    if retried.get("relevance_status") == "scored"
                    else "persistent_miss"
                )
                scores[row["code"]] = retried
            _log(
                f"ETF holdings relevance retry {batch_index}/{len(retry_batches)} "
                f"finished in {time.monotonic() - started:.1f}s"
            )

        unresolved_count = sum(
            score.get("relevance_status") != "scored" for score in scores.values()
        )
        if unresolved_count:
            _log(
                f"ETF holdings relevance: {unresolved_count}/{len(scores)} "
                "companies unresolved after retry"
            )
        return scores

    def rerank_etfs_from_components(
        self, candidates: list[dict], brief: dict, article: dict,
        theme_direction: str, stock_scores: list[dict] | dict | None = None,
    ) -> list[dict]:
        """Assess a bounded ETF shortlist using complete component portfolios."""
        budget = max(0, int(self.opts["etf_holdings_unique_budget"]))
        portfolio_budget = max(
            0, int(self.opts.get("etf_holdings_portfolio_budget", 40)))
        holdings_by_etf: dict[str, list[dict]] = {}
        unique_components: dict[str, dict] = {}
        attempted_codes: set[str] = set()
        seed_scores: dict[str, dict] = {}
        if isinstance(stock_scores, dict):
            score_rows = stock_scores.values()
        else:
            score_rows = stock_scores or []
        for score in score_rows:
            if not isinstance(score, dict):
                continue
            code = str(score.get("code") or score.get("market_code") or "").strip()
            if code and score.get("relevance_status") == "scored":
                seed_scores[code] = score

        def admit(candidate: dict) -> bool:
            code = candidate["code"]
            if code in attempted_codes or not requires_component_holdings(candidate):
                return False
            attempted_codes.add(code)
            try:
                holdings = list(self.quotes.iter_etf_holdings(code))
            except (AttributeError, RuntimeError) as exc:
                _log(f"ETF holdings unavailable for {code}: {exc}")
                return False
            if not holdings:
                _log(f"ETF holdings unavailable for {code}: empty portfolio")
                return False

            normalized = []
            new_codes = set()
            for holding in holdings:
                component_code = str(holding.get("code") or "").strip()
                try:
                    weight = float(str(holding.get("weight_pct") or "").rstrip("%"))
                except (TypeError, ValueError):
                    continue
                if not component_code or not math.isfinite(weight) or weight <= 0:
                    continue
                row = {
                    "code": component_code,
                    "name": holding.get("name") or component_code,
                    "weight_pct": weight,
                }
                normalized.append(row)
                if component_code not in unique_components:
                    new_codes.add(component_code)
            if not normalized:
                _log(f"ETF holdings unavailable for {code}: no valid weighted components")
                return False
            if len(unique_components) + len(new_codes) > budget:
                _log(f"ETF holdings budget skipped semantic scoring for {code} "
                     f"({len(new_codes)} new companies)")
                return False

            holdings_by_etf[code] = normalized
            for row in normalized:
                component = unique_components.setdefault(row["code"], {
                    "code": row["code"], "name": row["name"],
                    "_max_portfolio_weight_pct": 0.0,
                    "_aggregate_portfolio_weight_pct": 0.0,
                })
                component["_max_portfolio_weight_pct"] = max(
                    float(component["_max_portfolio_weight_pct"]), row["weight_pct"])
                component["_aggregate_portfolio_weight_pct"] = (
                    float(component["_aggregate_portfolio_weight_pct"])
                    + row["weight_pct"]
                )
            return True

        holdings_candidates = [
            candidate for candidate in candidates
            if requires_component_holdings(candidate)
        ]
        lane_aware = any(
            candidate.get("selection_lane") or candidate.get("etf_lane")
            for candidate in holdings_candidates
        )

        def lane_of(candidate: dict) -> str:
            return str(
                candidate.get("selection_lane")
                or candidate.get("etf_lane")
                or ""
            ).strip().lower()

        def has_stock_evidence(candidate: dict) -> bool:
            return bool(
                candidate.get("anchor_weights")
                or candidate.get("related_stock_codes")
                or candidate.get("benchmark_match_codes")
            )

        def diversity_source_keys(candidate: dict) -> list[str]:
            """Stable curated-pool and exact concept-index provenance keys."""
            keys = [
                f"pool:{key}"
                for key in candidate.get("pool_matches", {})
            ]
            keys.extend(
                f"concept_index:{key}"
                for key in candidate.get("concept_index_matches", {})
            )
            return keys

        stock_evidence = [
            candidate for candidate in holdings_candidates
            if has_stock_evidence(candidate) or not lane_aware
        ]
        stock_cap = min(
            portfolio_budget,
            max(0, int(self.opts.get(
                "etf_stock_evidence_portfolio_budget", 30))),
        )
        assessment_order = list(stock_evidence[:stock_cap])
        primary_stock_count = len(assessment_order)
        selected_codes = {candidate["code"] for candidate in assessment_order}

        # Reserve a bounded diversity lane after the stock-evidence leaders. Pick
        # one portfolio per curated pool before taking a second fund from a pool.
        pool_target = min(
            max(0, portfolio_budget - len(assessment_order)),
            max(0, int(self.opts.get(
                "etf_pool_diversity_portfolio_budget", 10))),
        )
        pool_cap = pool_target
        pool_candidates = [
            candidate for candidate in holdings_candidates
            if candidate["code"] not in selected_codes
            and not has_stock_evidence(candidate)
            and diversity_source_keys(candidate)
        ]
        pool_keys: list[str] = []
        for candidate in pool_candidates:
            for key in diversity_source_keys(candidate):
                if key not in pool_keys:
                    pool_keys.append(key)
        for key in pool_keys:
            if pool_cap <= 0:
                break
            candidate = next((
                item for item in pool_candidates
                if item["code"] not in selected_codes
                and key in diversity_source_keys(item)
            ), None)
            if candidate is None:
                continue
            assessment_order.append(candidate)
            selected_codes.add(candidate["code"])
            pool_cap -= 1
        for candidate in pool_candidates:
            if pool_cap <= 0:
                break
            if candidate["code"] in selected_codes:
                continue
            assessment_order.append(candidate)
            selected_codes.add(candidate["code"])
            pool_cap -= 1

        # The 30/10 split is an initial evidence allocation, not a reason to
        # leave the overall assessment budget idle. If either lane lacks enough
        # candidates, backfill from the other lane in its existing ranked order.
        # This is especially important for narrow themes, where selected-stock
        # relations can be rich while the curated-pool catalog has little recall,
        # or vice versa.
        primary_pool_count = len(assessment_order) - primary_stock_count
        lane_underfilled = bool(
            primary_stock_count < stock_cap
            or primary_pool_count < pool_target
        )
        primary_capacity = (
            portfolio_budget
            if lane_underfilled
            else min(
                portfolio_budget,
                max(0, int(self.opts.get(
                    "etf_stock_evidence_portfolio_budget", 30)))
                + max(0, int(self.opts.get(
                    "etf_pool_diversity_portfolio_budget", 10))),
            )
        )
        if len(assessment_order) < primary_capacity:
            remaining_ranked = [
                candidate for candidate in holdings_candidates
                if candidate["code"] not in selected_codes
                and (
                    has_stock_evidence(candidate)
                    or diversity_source_keys(candidate)
                )
            ]
            for candidate in remaining_ranked:
                assessment_order.append(candidate)
                selected_codes.add(candidate["code"])
                if len(assessment_order) >= primary_capacity:
                    break

        # Legacy/mocked candidates do not carry explicit lane fields. Preserve
        # their prior ranked-budget behavior without weakening lane-aware runs.
        if not lane_aware and len(assessment_order) < portfolio_budget:
            for candidate in holdings_candidates:
                if candidate["code"] in selected_codes:
                    continue
                assessment_order.append(candidate)
                selected_codes.add(candidate["code"])
                if len(assessment_order) >= portfolio_budget:
                    break

        relevance: dict[str, dict] = {}
        scored_components: set[str] = set()

        def score_pending_components() -> None:
            pending = [row for code, row in unique_components.items()
                       if code not in scored_components]
            if not pending:
                return
            try:
                profiles = self.quotes.security_profiles(
                    [row["code"] for row in pending])
            except AttributeError:
                profiles = {}
            except RuntimeError as exc:
                _log(f"ETF component company facts unavailable: {exc}")
                profiles = {}
            for row in pending:
                facts = profiles.get(row["code"], {})
                for key in ("name", "company_introduction", "sector", "industry"):
                    if facts.get(key):
                        row[key] = facts[key]
            relevance.update(self.score_etf_holding_relevance(
                brief, pending, article, seed_scores=seed_scores))
            scored_components.update(row["code"] for row in pending)

        # Spend the explicit portfolio budget before final ranking. Reaching the
        # requested output count is not a valid stopping rule: a later portfolio
        # can have stronger verified thematic mass than the first eligible funds.
        for candidate_index, candidate in enumerate(assessment_order, 1):
            if len(attempted_codes) >= portfolio_budget:
                break
            started = time.monotonic()
            _log(
                f"ETF holdings fetch {candidate_index}/{len(assessment_order)} "
                f"started ({candidate['code']})"
            )
            admitted = admit(candidate)
            _log(
                f"ETF holdings fetch {candidate_index}/{len(assessment_order)} "
                f"finished in {time.monotonic() - started:.1f}s "
                f"status={'admitted' if admitted else 'skipped'}"
            )

        score_pending_components()
        rerank_with_component_holdings(
            candidates, holdings_by_etf, relevance,
            theme_direction=theme_direction,
            min_output_score=float(self.opts["etf_min_theme_score"]),
            min_holding_relevance=float(self.opts["relevance_threshold"]),
            min_holding_confidence=float(self.opts["min_relevance_confidence"]),
        )

        final_rejection_counts: dict[str, int] = {}
        final_rejection_examples: dict[str, list[str]] = {}
        for candidate in holdings_candidates:
            if candidate.get("output_eligible"):
                continue
            reasons = candidate.get("eligibility_rejection_reasons") or []
            if isinstance(reasons, str):
                reasons = [reasons]
            for reason in reasons:
                reason = str(reason or "").strip()
                if not reason:
                    continue
                final_rejection_counts[reason] = (
                    final_rejection_counts.get(reason, 0) + 1)
                examples = final_rejection_examples.setdefault(reason, [])
                code = str(candidate.get("code") or "").strip()
                if code and code not in examples and len(examples) < 3:
                    examples.append(code)
        rejection_summary = "; ".join(
            f"{reason}={count}"
            + (
                f" ({', '.join(final_rejection_examples.get(reason, []))})"
                if final_rejection_examples.get(reason) else ""
            )
            for reason, count in sorted(final_rejection_counts.items())
        ) or "none"
        _log(f"ETF final basket eligibility rejections: {rejection_summary}")

        bypassed_wrappers = sum(
            1 for candidate in candidates
            if lane_of(candidate) == "single_stock_leveraged"
            and not requires_component_holdings(candidate)
        )
        stock_attempts = sum(
            candidate["code"] in attempted_codes
            and has_stock_evidence(candidate)
            for candidate in holdings_candidates
        )
        _log(f"ETF assessment lanes: {stock_attempts} stock-evidence portfolios, "
             f"{len(attempted_codes) - stock_attempts} pool/concept-diversity portfolios; "
             f"{bypassed_wrappers} exact-underlying wrappers bypassed physical holdings")
        coverage_complete = sum(
            candidate.get("holdings_status") == "complete"
            for candidate in holdings_candidates
        )
        semantic_complete = sum(
            candidate.get("ranking_mode") == "full_holdings"
            for candidate in holdings_candidates
        )
        partial_lower_bound_codes = [
            candidate["code"] for candidate in holdings_candidates
            if candidate.get("partial_evidence_mode")
            and candidate.get("output_eligible")
        ]
        scored_component_count = sum(
            relevance.get(code, {}).get("relevance_status") == "scored"
            for code in unique_components
        )
        unresolved_component_count = len(unique_components) - scored_component_count
        _log(
            f"ETF component assessment: {len(attempted_codes)} portfolios attempted, "
            f"{len(holdings_by_etf)} fetched, {coverage_complete} met weight coverage, "
            f"{semantic_complete} met semantic completeness, "
            f"{len(partial_lower_bound_codes)} qualified on conservative partial-evidence "
            "lower bounds, and "
            f"{scored_component_count}/{len(unique_components)} unique companies scored "
            f"({unresolved_component_count} unresolved)"
        )
        if partial_lower_bound_codes:
            _log(
                "ETF partial-evidence lower-bound qualifiers (unresolved holdings "
                "counted as zero exposure): "
                + ", ".join(partial_lower_bound_codes)
            )
        return candidates

    @staticmethod
    def _mark_etf_liquidity_diagnostics(candidates: list[dict]) -> None:
        """Normalize the approved soft liquidity warning without excluding funds."""
        flagged: list[dict] = []
        missing = 0
        for candidate in candidates:
            reasons = candidate.get("low_liquidity_reasons")
            if reasons is None:
                reasons = candidate.get("liquidity_reasons")
            if isinstance(reasons, str):
                reasons = [reasons]
            elif not isinstance(reasons, list):
                reasons = []
            reasons = [str(reason).strip() for reason in reasons if str(reason).strip()]

            try:
                aum = float(candidate.get("aum"))
                if not math.isfinite(aum):
                    raise ValueError
            except (TypeError, ValueError):
                aum = None
            try:
                turnover = float(candidate.get("turnover"))
                if not math.isfinite(turnover):
                    raise ValueError
            except (TypeError, ValueError):
                turnover = None

            if (aum is not None and aum < 25_000_000
                    and "aum_below_25m" not in reasons):
                reasons.append("aum_below_25m")
            if (turnover is not None and turnover < 1_000_000
                    and "turnover_below_1m" not in reasons):
                reasons.append("turnover_below_1m")
            missing_fields = [
                label for label, value in (("aum", aum), ("turnover", turnover))
                if value is None
            ]
            candidate["liquidity_metrics_missing"] = missing_fields
            candidate["liquidity_data_missing"] = bool(missing_fields)
            if missing_fields:
                missing += 1
            candidate["low_liquidity"] = bool(
                candidate.get("low_liquidity") or reasons)
            candidate["low_liquidity_reasons"] = reasons
            if candidate["low_liquidity"]:
                flagged.append(candidate)

        _log(
            f"ETF liquidity diagnostics: {len(flagged)} low-liquidity flags "
            f"(AUM<$25m or turnover<$1m); {missing} candidates have incomplete metrics"
        )
        if flagged:
            preview = ", ".join(
                f"{candidate['code']} "
                f"({'; '.join(reason.replace('_', ' ') for reason in candidate['low_liquidity_reasons']) or 'flagged'})"
                for candidate in flagged[:8]
            )
            _log(f"ETF low-liquidity examples: {preview}")

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
        """Calibrate headline exposure without disturbing approved ETF selection."""
        is_etf_output = bool(chosen) and all(
            candidate.get("static_theme_exposure") is not None
            for candidate in chosen
        )
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
                semantic = _clamp_float(
                    c.get("public_semantic_score"), 0.0, 1.0,
                    default=_clamp_float(
                        c.get("semantic_score"), 0.0, 1.0,
                        default=_semantic_score(c),
                    ),
                )
                # Public stock exposure is the grounded relationship score.
                # Price and volume remain private diagnostics and cannot inflate
                # the public label.
                c["theme_exposure_raw"] = semantic
        # Stock labels communicate the already-frozen broker rank, not a second
        # eligibility judgment.  Keep the first member at 5.0 and distribute the
        # remainder evenly through 3.0 without consulting price/volume features.
        if not is_etf_output:
            count = len(chosen)
            for index, candidate in enumerate(chosen):
                score = (
                    5.0
                    if count <= 1
                    else round(5.0 - 2.0 * index / (count - 1), 1)
                )
                candidate["theme_exposure"] = score
                candidate["score"] = score
            return

        # ETF membership and order are fixed before market data is fetched.
        # Calibrate labels on a sorted copy, then map them back without reordering
        # the approved ETF composition.
        exposure_order = sorted(
            range(len(chosen)),
            key=lambda index: (
                -chosen[index]["theme_exposure_raw"],
                str(chosen[index].get("code") or ""),
            ),
        )
        display = scoring.calibrate_scores([
            chosen[index]["theme_exposure_raw"] for index in exposure_order
        ])
        for index, score in zip(exposure_order, display):
            chosen[index]["theme_exposure"] = score
            chosen[index]["score"] = score

    @staticmethod
    def _validated_rationale_text(value) -> str | None:
        """Return one safe language string, or ``None`` when details leaked."""
        if not isinstance(value, str):
            return None
        text = value.strip()
        if not text:
            return None
        if (_NARRATIVE_INTERNAL_RE.search(text) or _NARRATIVE_META_RE.search(text)
                or _NARRATIVE_BOILERPLATE_RE.search(text)
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

    @classmethod
    def _has_derivative_structure(cls, candidate: dict) -> bool:
        """Whether direction/leverage is material enough for public ETF prose."""
        if cls._is_inverse_candidate(candidate):
            return True
        try:
            leverage = float(candidate.get("leverage"))
        except (TypeError, ValueError):
            return False
        return math.isfinite(leverage) and not math.isclose(
            abs(leverage), 1.0, rel_tol=0.0, abs_tol=1e-9)

    @classmethod
    def _ordinary_etf_mentions_default_position(
        cls, candidate: dict, rationale: dict,
    ) -> bool:
        """Reject redundant 1x/long labels on conventional ETF rationales."""
        if (candidate.get("static_theme_exposure") is None
                or cls._has_derivative_structure(candidate)):
            return False
        en = unicodedata.normalize("NFKC", rationale["en"])
        zh = unicodedata.normalize("NFKC", rationale["zh"])
        en_pattern = re.compile(
            r"(?<![\d.])1(?:\.0+)?\s*(?:x|×|times?)\b|"
            r"\bone(?:[- ]times?)?\s+(?:long|exposure|leverage|multiple)\b|"
            r"\bunleveraged\b|"
            r"\blong(?:-only)?\s+(?:fund|etf|mandate|exposure|position|product|strategy)\b|"
            r"\b(?:fund|etf|mandate|product|strategy)\s+(?:is\s+|remains\s+|with\s+)?"
            r"(?:a\s+)?long(?:-only)?\b",
            re.IGNORECASE,
        )
        zh_pattern = re.compile(
            r"(?:1(?:\.0+)?|一)\s*(?:倍|x|×)|无杠杆|非杠杆|"
            r"(?:多头|做多)(?:基金|ETF|产品|敞口|策略|头寸)|"
            r"(?:基金|ETF|产品|策略).{0,8}(?:多头|做多)",
            re.IGNORECASE,
        )
        return bool(en_pattern.search(en) or zh_pattern.search(zh))

    @staticmethod
    def _bearish_basket_rationale_has_downside(
        candidate: dict, rationale: dict,
    ) -> bool:
        """Require an explicit holdings-to-fund-loss pathway for bearish baskets."""
        if not candidate.get("bearish_downside_exposure"):
            return True
        en = rationale["en"].lower()
        zh = rationale["zh"]
        return bool(
            re.search(r"\b(?:fund|portfolio|holdings?|nav|net asset value)\b", en)
            and re.search(
                r"\b(?:declin\w*|fall\w*|drop\w*|weak\w*|contract\w*|"
                r"deteriorat\w*|pressure\w*|downturn|slowdown|headwinds?)\b",
                en,
            )
            and re.search(
                r"\b(?:reduc\w*|lower\w*|pressure\w*|hurt\w*|weigh\w*|"
                r"drag\w*|erod\w*|lose|loss(?:es)?|declin\w*|fall\w*)\b",
                en,
            )
            and re.search(r"基金|净值|组合|持仓|资产价值", zh)
            and re.search(r"下跌|下降|走弱|收缩|疲软|恶化|承压|下行|放缓|逆风", zh)
            and re.search(r"压低|降低|拖累|损失|受损|承压|下跌|下降|侵蚀|减少", zh)
        )

    @staticmethod
    def _ordinary_etf_rationale_matches_direction(
        candidate: dict, rationale: dict, theme_direction: str | None,
    ) -> bool:
        """Require ordinary-fund EN/ZH copy to express the same theme stance."""
        if theme_direction is None:
            return True
        expected = (
            "negative"
            if _norm_theme_direction(theme_direction) == "bearish"
            else "positive"
        )
        en = rationale["en"]
        zh = rationale["zh"]
        if re.search(
            r"\b(?:fund|portfolio|holdings?|nav|net asset value|fund value)\b",
            en,
            re.IGNORECASE,
        ) is None or re.search(r"基金|净值|组合|持仓|资产价值", zh) is None:
            return False
        en_stance = en
        zh_stance = zh
        identity_values = [
            str(candidate.get(key) or "").strip()
            for key in ("name", "name_zh", "security_name_zh", "code")
        ]
        code = str(candidate.get("code") or "").strip()
        ticker = code.partition(":")[2] if code else ""
        if len(ticker) >= 2:
            identity_values.append(ticker)
        for identity in sorted(
            {value for value in identity_values if value}, key=len, reverse=True,
        ):
            en_stance = re.sub(
                re.escape(identity), " ", en_stance, flags=re.IGNORECASE
            )
            zh_stance = re.sub(
                re.escape(identity), " ", zh_stance, flags=re.IGNORECASE
            )
        positive_en = list(re.finditer(
            r"\b(?:appreciat|gain|grow|higher|improv|increas|lift|rais|rise|"
            r"strengthen|support)\w*\b",
            en_stance,
            re.IGNORECASE,
        ))
        negative_en = list(re.finditer(
            r"\b(?:declin|drop|erod|fall|loss|lower|pressure|reduc|weaken|weigh)\w*\b",
            en_stance,
            re.IGNORECASE,
        ))
        latest_positive = max((match.start() for match in positive_en), default=-1)
        latest_negative = max((match.start() for match in negative_en), default=-1)
        en_matches = (
            latest_negative >= 0 and latest_negative > latest_positive
            if expected == "negative" else
            latest_positive >= 0 and latest_positive > latest_negative
        )
        return en_matches and _directional_chinese_is_consistent(
            zh_stance, expected
        )

    @staticmethod
    def _derivative_concentration_label(candidate: dict) -> str:
        """Return a factual concentration context, or an empty string."""
        lane = str(
            candidate.get("selection_lane") or candidate.get("etf_lane") or ""
        ).strip().lower()
        if (
            candidate.get("single_stock_inverse")
            or candidate.get("single_stock_leveraged")
            or candidate.get("single_stock_wrapper")
            or lane == "single_stock_leveraged"
        ):
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
    def _derivative_reference_values(candidate: dict) -> list[str]:
        """Return only supplied benchmark/underlying names suitable for narration."""
        # Exact structured underlying evidence owns single-stock narration. A
        # free-form benchmark label can be broader or stale and must not replace
        # the selected stock that was actually verified for this product lane.
        underlying_code = str(candidate.get("underlying_code") or "").strip()
        benchmark_code = str(candidate.get("benchmark_code") or "").strip()
        exact_code = underlying_code or benchmark_code
        if exact_code:
            return [
                exact_code.partition(":")[2]
                if ":" in exact_code else exact_code
            ]
        benchmark = str(candidate.get("benchmark") or "").strip()
        if benchmark:
            return [benchmark.partition(":")[2] if ":" in benchmark else benchmark]
        tracked_index = str(candidate.get("index_tracked") or "").strip()
        if tracked_index:
            return [tracked_index]
        lane = str(
            candidate.get("selection_lane") or candidate.get("etf_lane") or ""
        ).strip().lower()
        if not (
            candidate.get("single_stock_inverse")
            or candidate.get("single_stock_leveraged")
            or candidate.get("single_stock_wrapper")
            or lane == "single_stock_leveraged"
        ):
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

    # Compatibility aliases retained for callers/tests that used the original
    # inverse-only helper names.
    _inverse_concentration_label = _derivative_concentration_label
    _inverse_reference_values = _derivative_reference_values

    @staticmethod
    def _liquidity_limitation(candidate: dict) -> tuple[str, str]:
        if not candidate.get("low_liquidity"):
            return "", ""
        return (
            " Limited assets under management or current trading turnover indicate low "
            "liquidity, which can increase spreads and trading impact.",
            "基金资产规模或当前成交额偏低，流动性有限，可能扩大买卖价差和交易冲击。",
        )

    @staticmethod
    def _multilingual(en: str, zh: str) -> dict:
        return {"type": "multilingual", "en": en, "zh": zh}

    @classmethod
    def _validated_candidate_rationale(
        cls,
        candidate: dict,
        value,
        theme_direction: str | None = None,
    ) -> dict | None:
        """Validate public prose, including derivative and liquidity risk facts."""
        rationale = cls._validated_theme_rationale(value)
        if rationale is None:
            return None
        if candidate.get("static_theme_exposure") is not None:
            supplied_fund_facts: list[str] = []
            for holding in candidate.get("matched_holdings", []):
                if not isinstance(holding, dict):
                    continue
                supplied_fund_facts.extend((
                    str(holding.get("ticker") or ""),
                    str(holding.get("code") or "").partition(":")[2],
                    str(holding.get("name") or ""),
                ))
            related = candidate.get("related_stock_codes") or []
            if isinstance(related, str):
                related = [related]
            supplied_fund_facts.extend(
                str(code or "").partition(":")[2] or str(code or "")
                for code in related
            )
            for key in (
                "underlying_code", "benchmark", "benchmark_code", "index_tracked", "mandate",
                "investment_objective", "fund_strategy", "fund_focus",
                "fund_niche", "fund_category", "asset_class",
            ):
                supplied_fund_facts.append(str(candidate.get(key) or ""))
            pool_labels = candidate.get("pool_labels") or []
            if isinstance(pool_labels, str):
                pool_labels = [pool_labels]
            supplied_fund_facts.extend(str(value or "") for value in pool_labels)
            supplied_fact_text = ". ".join(supplied_fund_facts)
            allowed_names = _external_proper_nouns(
                supplied_fact_text, candidate
            )
            allowed_names.update(
                _lane_stem(match.group(0).rstrip(".").casefold())
                for match in re.finditer(
                    r"(?<![A-Za-z0-9])[A-Z][A-Z0-9.]{1,}",
                    supplied_fact_text,
                )
                if _lane_stem(match.group(0).rstrip(".").casefold()) not in {
                    "ai", "etf", "etn", "nav", "ucits", "usd",
                }
            )
            def claimed_fund_names(text: str) -> set[str]:
                names: set[str] = set()
                ignored = {
                    "ai", "bear", "bearish", "bull", "bullish", "ceo", "cfo",
                    "cpu", "daily", "dram", "etf", "etn", "gpu", "hbm",
                    "inverse", "long", "nand", "nav", "positive", "short",
                    "ucits", "usd",
                }
                for identity in sorted(
                    {
                        str(candidate.get(key) or "").strip()
                        for key in (
                            "name", "name_zh", "security_name_zh", "code",
                        )
                        if str(candidate.get(key) or "").strip()
                    },
                    key=len,
                    reverse=True,
                ):
                    text = re.sub(
                        re.escape(identity), " ", text, flags=re.IGNORECASE
                    )
                for match in re.finditer(
                    r"(?<![A-Za-z0-9])[A-Z][A-Za-z0-9&.]{1,}", text,
                ):
                    token = match.group(0).rstrip(".")
                    stem = _lane_stem(token.casefold())
                    if stem in ignored:
                        continue
                    prefix = text[:match.start()].rstrip()
                    at_sentence_start = (
                        not prefix or prefix[-1:] in {".", "!", "?", ";", ":", "\n"}
                    )
                    # Uppercase ticker-like claims remain meaningful at sentence
                    # starts; ordinary title-case prose words do not.
                    if token.isupper() or not at_sentence_start:
                        names.add(stem)
                return names

            claimed_names = claimed_fund_names(
                f"{rationale['en']}. {rationale['zh']}"
            )
            if not claimed_names <= allowed_names:
                return None
        if cls._ordinary_etf_mentions_default_position(candidate, rationale):
            return None
        if not cls._bearish_basket_rationale_has_downside(candidate, rationale):
            return None
        en = rationale["en"].lower()
        zh = rationale["zh"]
        if candidate.get("low_liquidity"):
            if re.search(
                    r"\b(?:low|limited|thin)\s+(?:trading\s+)?liquidity\b|"
                    r"\b(?:low|limited)\s+(?:turnover|trading volume)\b|"
                    r"\bwide(?:r)?\s+(?:bid[- ]ask\s+)?spreads?\b",
                    en,
            ) is None or re.search(
                    r"流动性|成交额(?:偏低|较低|有限)|成交量(?:偏低|较低|有限)|买卖价差",
                    zh,
            ) is None:
                return None

        if not cls._has_derivative_structure(candidate):
            if (
                candidate.get("static_theme_exposure") is not None
                and not cls._ordinary_etf_rationale_matches_direction(
                    candidate, rationale, theme_direction
                )
            ):
                return None
            return rationale

        is_inverse = cls._is_inverse_candidate(candidate)
        required_en = (
            r"\b(?:daily|one[ -]day)\b",
            r"\breset\b",
            r"\bcompound(?:ing|ed)?\b",
            r"\bpath[ -]depend(?:ence|ent)\b",
        )
        if any(re.search(pattern, en) is None for pattern in required_en):
            return None
        if any(term not in zh for term in ("重置", "复利", "路径依赖")):
            return None
        if not ("单日" in zh or "每日" in zh):
            return None
        if is_inverse:
            if re.search(r"\b(?:inverse|short exposure)\b", en) is None or "反向" not in zh:
                return None
        elif (
            re.search(r"\b(?:long|bull(?:ish)?|positive)\b", en) is None
            or re.search(r"正向|做多|多头", zh) is None
        ):
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
            multiple = abs(float(candidate.get("leverage")))
            if not math.isfinite(multiple) or multiple <= 0:
                raise ValueError
        except (TypeError, ValueError):
            multiple = None
        if multiple is not None:
            multiple_text = f"{multiple:g}"
            if re.search(
                    rf"(?<![\d.]){re.escape(multiple_text)}\s*(?:x|×|times?)(?!\w)", en
            ) is None:
                return None
            if re.search(
                    rf"(?<![\d.]){re.escape(multiple_text)}\s*倍", zh
            ) is None:
                return None

        reference_values = cls._derivative_reference_values(candidate)
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

        if cls._derivative_concentration_label(candidate):
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
        cls,
        candidate: dict,
        theme_direction: str = "bullish",
        *,
        theme: str = "",
        rank: int | None = None,
    ) -> dict:
        """Build bilingual, factual prose when LLM output is absent or unsafe."""
        name = str(candidate.get("name") or candidate.get("code") or "This security").strip()
        direction_mode = _norm_theme_direction(theme_direction)
        is_etf = candidate.get("static_theme_exposure") is not None
        if not is_etf:
            def safe_fragment(
                value: object, *, limit: int = 220, strip_parties: bool = True,
            ) -> str:
                """Keep supplied wording but remove metrics and named counterparties."""
                text = _normalised_text(value)
                if not text:
                    return ""
                text = re.split(r"[\r\n]+", text, maxsplit=1)[0]
                # Screening diagnostics are never investment prose.  Reject the
                # entire source fragment instead of trying to disguise internal
                # mechanics inside a deterministic public fallback.
                if (
                    _NARRATIVE_INTERNAL_RE.search(text)
                    or _NARRATIVE_META_RE.search(text)
                    or _NARRATIVE_BOILERPLATE_RE.search(text)
                    or _SNAKE_CASE_RE.search(text)
                ):
                    return ""
                text = re.sub(
                    r"[$£€¥]\s*\d[\d,.]*\s*(?:million|billion|trillion)?|"
                    r"(?<![A-Za-z0-9])\d+(?:\.\d+)?\s*(?:%|percent|"
                    r"basis points?|bps|million|billion|trillion)(?![A-Za-z])",
                    "",
                    text,
                    flags=re.IGNORECASE,
                )
                text = re.sub(
                    r"\b(?:at|by|of)?\s*(?:million|billion|trillion)(?:[- ]dollar)?"
                    r"(?:\s+scale)?\b",
                    "",
                    text,
                    flags=re.IGNORECASE,
                )
                text = re.sub(r"\b(?:at|by)\s+scale\b", "", text, flags=re.IGNORECASE)
                text = _CURRENCY_MAGNITUDE_RE.sub("", text)
                text = _FINANCIAL_MAGNITUDE_RE.sub("", text)
                text = _WORD_MULTIPLE_RE.sub("", text)
                text = _WORD_QUANTITY_RE.sub("", text)
                # Remove every remaining standalone number.  Product/event
                # identifiers embedded in words (for example 5G) are retained,
                # while shipments, units, and financial quantities cannot leak.
                text = re.sub(
                    r"(?<![A-Za-z0-9])[-+]?\d+(?:[.,]\d+)*(?![A-Za-z0-9])",
                    "",
                    text,
                )
                # A deterministic fallback never needs a named commercial party.
                # Preserve the relationship category while removing the party name.
                text = re.sub(
                    r"\b(with|for|from|to)\s+"
                    r"(?:[A-Z][A-Za-z0-9&.'’-]*(?:\s+[A-Z][A-Za-z0-9&.'’-]*){0,3})",
                    lambda match: (
                        "with counterparties"
                        if match.group(1).lower() == "with"
                        else "for customers"
                        if match.group(1).lower() in {"for", "to"}
                        else "from suppliers"
                    ),
                    text,
                )
                if strip_parties:
                    for party in _external_proper_nouns(text, candidate):
                        text = re.sub(
                            rf"(?<![A-Za-z0-9]){re.escape(party)}(?![A-Za-z0-9])",
                            "counterparties",
                            text,
                            flags=re.IGNORECASE,
                        )
                text = re.sub(
                    r"\b(counterparties|customers|suppliers)(?:\s+\1)+\b",
                    r"\1",
                    text,
                )
                text = re.sub(r"\s{2,}", " ", text)
                text = re.sub(r"\s+([,;:.])", r"\1", text)
                text = text.strip(" \t,;:.")[:limit].rstrip(" \t,;:.")
                if (
                    _NARRATIVE_INTERNAL_RE.search(text)
                    or _NARRATIVE_META_RE.search(text)
                    or _NARRATIVE_BOILERPLATE_RE.search(text)
                    or _SNAKE_CASE_RE.search(text)
                ):
                    return ""
                return text

            def chinese_business_labels(value: str) -> list[tuple[str, str]]:
                """Return a conservative EN/ZH concept pair from supplied CJK."""
                labels: list[tuple[str, str]] = []
                for pattern, en_label, zh_label in (
                    (r"数字.{0,4}服务", "digital services", "数字服务"),
                    (r"广告", "advertising services", "广告业务"),
                    (r"数据中心", "data centers", "数据中心"),
                    (r"云", "cloud", "云计算"),
                    (r"智能手机|手机|终端|可穿戴|消费.{0,4}设备", "consumer devices", "消费电子设备"),
                    (r"DRAM|HBM|NAND|内存|存储", "memory", "存储"),
                    (r"封装|测试", "semiconductor packaging and testing", "半导体封装与测试"),
                    (r"芯片|半导体|晶圆|处理器|调制解调器", "semiconductors", "半导体"),
                    (r"硬件", "hardware", "硬件"),
                    (r"软件|应用", "software", "软件"),
                    (r"无线|通信|连接", "connectivity", "通信与连接"),
                    (r"银行|信贷|贷款|按揭|支付", "financial services", "金融服务"),
                    (r"电力|能源|天然气|石油|油气", "energy", "能源"),
                    (r"建筑|建造|住房|住宅", "housing", "住宅与建筑"),
                    (r"平台", "technology platforms", "技术平台"),
                    (r"服务", "services", "服务"),
                    (r"产品|解决方案|系统", "products", "产品"),
                    (r"设备", "equipment", "设备"),
                ):
                    pair = (en_label, zh_label)
                    if re.search(pattern, value, re.IGNORECASE) and pair not in labels:
                        labels.append(pair)
                if len(labels) > 1:
                    specific = [
                        pair for pair in labels
                        if pair[0] not in {"services", "products", "equipment"}
                    ]
                    labels = specific or labels
                return labels[:4]

            def english_business_from_chinese(value: str) -> str:
                """Translate only broad, observable business nouns for fallback copy."""
                labels = [label[0] for label in chinese_business_labels(value)]
                if not labels:
                    return ""
                return f"operates a business centered on {cls._human_join(labels)}"

            def concise_chinese_business(value: str) -> str:
                labels = [label[1] for label in chinese_business_labels(value)]
                if not labels:
                    return ""
                return f"业务涵盖{cls._human_join_zh(labels)}"

            def useful_business_fragment(value: str) -> bool:
                """Exclude identity-only and party-redaction remnants."""
                if not value or re.search(
                    r"\b(?:counterparties|customers|suppliers)\b",
                    value,
                    re.IGNORECASE,
                ):
                    return False
                if _CJK_RE.search(value):
                    return True
                identity = _candidate_identity_stems(candidate)
                generic = {
                    "business", "company", "corp", "corporation", "group",
                    "holding", "holdings", "inc", "limited", "listed", "ltd",
                    "plc", "security", "stock",
                }
                content = [
                    _lane_stem(token.casefold())
                    for token in re.findall(r"[A-Za-z0-9]+", value)
                    if _lane_stem(token.casefold()) not in identity | generic
                ]
                return bool(content)

            def broker_business_fragment(value: str) -> str:
                """Turn a supplied sector/industry label into a business clause."""
                value = re.sub(
                    r"\s+(?:I|II|III|IV|V)$", "", value, flags=re.IGNORECASE
                ).strip()
                if _CJK_RE.search(value) or re.search(
                    r"\b(?:builds?|designs?|develops?|focuses?|generates?|"
                    r"manufactures?|makes?|offers?|operates?|produces?|provides?|"
                    r"runs?|sells?|serves?|specializes?|supplies?)\b|"
                    r"\bis\s+(?:a|an)\b",
                    value,
                    re.IGNORECASE,
                ):
                    return value
                return f"operates a business focused on {value}"

            business = ""
            business_zh_source = ""
            for field in ("business_fact", "company_introduction", "industry", "sector"):
                fragment = safe_fragment(candidate.get(field), strip_parties=False)
                if not useful_business_fragment(fragment):
                    continue
                if _CJK_RE.search(fragment):
                    business_zh_source = fragment
                else:
                    business = broker_business_fragment(fragment)
                # Keep both languages anchored to the same highest-priority
                # fact. A lower-priority field can describe a different segment.
                break
            if business_zh_source and not business:
                business = english_business_from_chinese(business_zh_source)
                concise_business_zh = concise_chinese_business(business_zh_source)
                if business and concise_business_zh:
                    business_zh_source = concise_business_zh
                else:
                    # If the noun falls outside the safe translation vocabulary,
                    # use equivalent broad descriptions in both languages.
                    business_zh_source = "主营其核心业务"
            business = business or "operates its core business"
            connection = ""
            connection_zh_source = ""
            for field in ("theme_connection", "reason"):
                fragment = safe_fragment(candidate.get(field), strip_parties=True)
                if not fragment:
                    continue
                if _CJK_RE.search(fragment):
                    connection_zh_source = fragment
                else:
                    connection = fragment
                break
            has_supplied_connection = bool(connection or connection_zh_source)
            literal_theme = _normalised_text(
                theme or candidate.get("_fallback_theme")
            )
            if literal_theme and (
                _NARRATIVE_INTERNAL_RE.search(literal_theme)
                or _NARRATIVE_META_RE.search(literal_theme)
                or _NARRATIVE_BOILERPLATE_RE.search(literal_theme)
                or _SNAKE_CASE_RE.search(literal_theme)
            ):
                literal_theme = ""
            # ``self`` is intentionally unavailable in this classmethod.  The
            # finalizer always supplies the literal input theme; direct callers
            # receive an event-only conditional rather than an invented theme.
            if not connection:
                if literal_theme and not _CJK_RE.search(literal_theme):
                    connection = (
                        f"the {literal_theme} thesis can "
                        f"{'reduce' if direction_mode == 'bearish' else 'increase'} "
                        "demand for that business"
                    )
                else:
                    connection = (
                        "the event thesis can "
                        f"{'reduce' if direction_mode == 'bearish' else 'increase'} "
                        "demand for that business"
                    )

            financial_fragment = safe_fragment(candidate.get("financial_pathway"))
            financial_zh_source = (
                financial_fragment if _CJK_RE.search(financial_fragment) else ""
            )
            financial = "" if financial_zh_source else financial_fragment
            code = str(candidate.get("code") or "").strip()
            ticker = code.partition(":")[2].strip() if ":" in code else code
            display = name
            if ticker and ticker.casefold() not in name.casefold():
                display = f"{name} ({ticker})"
            zh_name = _normalised_text(
                candidate.get("name_zh") or candidate.get("security_name_zh")
            ) or display

            rank_seed = rank if rank is not None else candidate.get("stock_selection_rank")
            template_seed = f"{rank_seed or 0}|{code}"
            template_hash = int(
                hashlib.sha256(template_seed.encode("utf-8")).hexdigest()[:8], 16
            )
            template_index = template_hash % 3
            try:
                weak_template_index = (max(1, int(rank_seed)) - 1) % 8
            except (TypeError, ValueError):
                weak_template_index = template_hash % 8
            raw_selection_tier = candidate.get("stock_selection_tier")
            try:
                selection_tier = int(raw_selection_tier)
            except (TypeError, ValueError):
                selection_tier = None
            weak_mode = (
                not has_supplied_connection
                or (selection_tier is not None and selection_tier >= 3)
            )
            expected_effect = (
                "negative" if direction_mode == "bearish" else "positive"
            )
            supplied_effect = str(
                candidate.get("directional_effect") or ""
            ).strip().lower()
            supplied_pathway = "; ".join(
                value for value in (connection, financial) if value
            )
            supplied_pathway_zh = "；".join(
                value for value in (connection_zh_source, financial_zh_source)
                if value
            )
            if selection_tier is not None and (
                supplied_effect not in {"positive", "negative"}
                or supplied_effect != expected_effect
                or not _directional_pathway_is_consistent(
                    supplied_pathway, expected_effect
                )
                or (
                    supplied_pathway_zh
                    and not _directional_chinese_is_consistent(
                        supplied_pathway_zh, expected_effect
                    )
                )
            ):
                # Structural evidence can still point the opposite way from the
                # current theme.  Selection stays frozen, but narration switches
                # to a directionally correct core-business thesis.
                weak_mode = True
            if weak_mode:
                # Once screening is frozen, weak relationship evidence is not
                # narrated. Pitch the operating business on broad, defensible
                # execution levers instead of emitting a watchlist disclaimer or
                # reopening the selection decision.
                trusted_business = ""
                trusted_business_zh = ""
                for field in (
                    "business_fact", "company_introduction", "industry", "sector",
                ):
                    fragment = safe_fragment(candidate.get(field), strip_parties=False)
                    if not useful_business_fragment(fragment):
                        continue
                    if _CJK_RE.search(fragment):
                        trusted_business_zh = fragment
                    else:
                        trusted_business = broker_business_fragment(fragment)
                    break
                if trusted_business:
                    business = trusted_business
                if trusted_business_zh:
                    business_zh_source = trusted_business_zh
                    business = english_business_from_chinese(trusted_business_zh)
                    concise_business_zh = concise_chinese_business(
                        trusted_business_zh
                    )
                    if business and concise_business_zh:
                        business_zh_source = concise_business_zh
                    else:
                        business = "operates its core business"
                        business_zh_source = "主营其核心业务"
                literal_theme_en = (
                    literal_theme if literal_theme and not _CJK_RE.search(literal_theme)
                    else ""
                )
                backdrop = (
                    f"the {literal_theme_en} risk backdrop"
                    if literal_theme_en and direction_mode == "bearish" else
                    f"the {literal_theme_en} opportunity"
                    if literal_theme_en else "its market"
                )
                bullish_core_levers = (
                    f"within {backdrop}, disciplined execution against core demand is the key upside lever",
                    f"as investors focus on {backdrop}, operating scale and business mix can improve monetization",
                    f"{backdrop} puts a premium on sharper execution and competitive positioning",
                    f"the investable edge within {backdrop} is converting existing product demand into profitable growth",
                    f"against {backdrop}, sales execution across the core franchise is the key variable",
                    f"its route into {backdrop} is sustained adoption of the products and services it already sells",
                    f"within {backdrop}, the upside rests on turning core demand into higher-value sales",
                    f"{backdrop} rewards disciplined scaling of existing offerings and operating costs",
                )
                bearish_core_levers = (
                    f"within {backdrop}, softer core demand is the principal operating vulnerability",
                    f"as investors reassess {backdrop}, operating scale and business mix can amplify weaker monetization",
                    f"{backdrop} raises the cost of any slippage in execution or competitive positioning",
                    f"the downside lever within {backdrop} is weaker conversion of product demand into profitable growth",
                    f"against {backdrop}, slower sales execution across the core franchise is the key risk",
                    f"its exposure to {backdrop} is a slowdown in adoption of the products and services it already sells",
                    f"within {backdrop}, lower-value sales can weaken the quality of the revenue mix",
                    f"{backdrop} can expose fixed-cost pressure when existing offerings scale more slowly",
                )
                core_levers = (
                    bearish_core_levers
                    if direction_mode == "bearish" else bullish_core_levers
                )
                connection = core_levers[weak_template_index]
            positive_bridges = (
                "the channel can support demand, revenue, and earnings",
                "the exposure can support orders, margins, and earnings",
                "the linkage can translate into firmer sales and profits",
            )
            negative_bridges = (
                "the channel can pressure demand, revenue, and earnings",
                "the exposure can weigh on orders, margins, and earnings",
                "the linkage can translate into weaker sales and profits",
            )
            if weak_mode:
                bullish_financial = (
                    "better demand conversion can support revenue, margins, and earnings",
                    "improving monetization can lift sales, margins, and profits",
                    "deeper adoption can expand revenue and earnings power",
                    "profitable growth can widen margins and compound earnings",
                    "stronger sales conversion can support revenue and cash flow",
                    "continued adoption can build orders, revenue, and earnings",
                    "higher-value sales can strengthen revenue mix, margins, and profits",
                    "successful scaling can lift sales, margins, and earnings",
                )
                bearish_financial = (
                    "weaker demand conversion can pressure revenue, margins, and earnings",
                    "softer monetization can weigh on sales, margins, and profits",
                    "slower adoption can reduce revenue and earnings power",
                    "weaker profitable growth can compress margins and earnings",
                    "slower sales conversion can pressure revenue and cash flow",
                    "fading adoption can reduce orders, revenue, and earnings",
                    "lower-value sales can weaken revenue mix, margins, and profits",
                    "slower scaling can weigh on sales, margins, and earnings",
                )
                financial = (
                    bearish_financial
                    if direction_mode == "bearish" else bullish_financial
                )[weak_template_index]
            elif not financial:
                financial = (
                    negative_bridges if direction_mode == "bearish" else positive_bridges
                )[template_index]

            en_connectors = ("consequently", "on that basis", "as that thesis develops")
            en = (
                f"{display}: {business}; {connection}; "
                f"{en_connectors[template_index]}, {financial}."
            )

            if business_zh_source:
                business_zh = business_zh_source
            elif _CJK_RE.search(business):
                business_zh = business
            else:
                business_labels: list[str] = []
                business_basis = (
                    business
                    if weak_mode else
                    " ".join(str(candidate.get(field) or "") for field in (
                        "business_fact", "company_introduction", "industry", "sector",
                    )) or business
                )
                for pattern, label in (
                    (r"\blaunch\w*\b", "发射服务"),
                    (r"\bspacecraft\b", "航天器"),
                    (r"\bconnectiv\w*\b", "连接服务"),
                    (r"\badvertis\w*\b", "广告"),
                    (r"\bcloud\b", "云计算"),
                    (r"\bsubscriptions?\b", "订阅"),
                    (r"\bapplications?\b", "应用"),
                    (r"\bsmartphones?\b", "智能手机"),
                    (r"\bcomputers?\b", "计算机"),
                    (r"\btablets?\b", "平板电脑"),
                    (r"\bwearables?\b", "可穿戴设备"),
                    (r"\baccessories\b", "配件"),
                    (r"\bretail\b", "零售"),
                    (r"\bstorage\b", "存储"),
                    (r"\banalytics?\b", "数据分析"),
                    (r"\b(?:semiconductors?|chips?)\b", "半导体"),
                    (r"\bgpus?\b", "GPU"),
                    (r"\bprocessors?\b", "处理器"),
                    (r"\bwafers?\b", "晶圆制造"),
                    (r"\btest(?:ing)?\b", "测试"),
                    (r"\bassembl(?:y|ies)\b", "组装"),
                    (r"\bsoftware\b", "软件"),
                    (r"\bnetworking\b", "网络"),
                    (r"\bcommunications?\b", "通信"),
                    (r"\bplatforms?\b", "平台"),
                    (r"\bdevices?\b", "设备"),
                    (r"\bservices?\b", "服务"),
                ):
                    if re.search(pattern, business_basis, re.IGNORECASE) and label not in business_labels:
                        business_labels.append(label)
                business_zh = (
                    f"主营{'、'.join(business_labels[:6])}等业务"
                    if business_labels else "主营其核心产品与服务"
                )
            if weak_mode:
                zh_backdrop = literal_theme or "其核心市场"
                bullish_connection_zh = (
                    f"在{zh_backdrop}机会中，围绕核心需求稳健执行是主要上行抓手",
                    f"当投资者关注{zh_backdrop}时，经营规模与业务组合有望提升变现效率",
                    f"{zh_backdrop}更看重执行能力与竞争定位",
                    f"在{zh_backdrop}中，投资价值取决于将现有产品需求转化为盈利增长",
                    f"面对{zh_backdrop}，核心业务的销售执行是关键变量",
                    f"其切入{zh_backdrop}的路径是推动现有产品与服务持续采用",
                    f"在{zh_backdrop}中，上行空间取决于将核心需求转化为更高价值的销售",
                    f"{zh_backdrop}更有利于能够严控成本并扩大现有业务的公司",
                )
                bearish_connection_zh = (
                    f"在{zh_backdrop}中，核心需求走弱是主要经营风险",
                    f"当投资者重估{zh_backdrop}时，经营规模与业务组合可能放大变现压力",
                    f"{zh_backdrop}会放大执行失误与竞争定位转弱的影响",
                    f"在{zh_backdrop}中，下行风险来自产品需求向盈利增长的转化减弱",
                    f"面对{zh_backdrop}，核心业务销售执行放缓是关键风险",
                    f"其对{zh_backdrop}的风险在于现有产品与服务采用速度下降",
                    f"在{zh_backdrop}中，低价值销售可能削弱收入结构质量",
                    f"若现有业务扩张放缓，{zh_backdrop}可能暴露固定成本压力",
                )
                connection_zh = (
                    bearish_connection_zh
                    if direction_mode == "bearish" else bullish_connection_zh
                )[weak_template_index]
            elif connection_zh_source:
                connection_zh = connection_zh_source
            elif _CJK_RE.search(connection):
                connection_zh = connection
            elif literal_theme and connection.startswith(f"the {literal_theme} thesis"):
                connection_zh = (
                    f"{literal_theme}逻辑可能"
                    f"{'压低' if direction_mode == 'bearish' else '提升'}该业务需求"
                )
            else:
                connection_zh = f"主题联系在于“{connection}”"

            if not weak_mode and financial_zh_source:
                financial_zh = financial_zh_source
            else:
                outcome_terms: list[str] = []
                financial_lower = financial.casefold()
                for pattern, label in (
                    (r"\border", "订单"),
                    (r"\bdemand", "需求"),
                    (r"\b(?:revenue|sales?)", "收入"),
                    (r"\bcost", "成本"),
                    (r"\bmargin", "利润率"),
                    (r"\b(?:earnings|profits?|cash flow)", "盈利"),
                ):
                    if re.search(pattern, financial_lower) and label not in outcome_terms:
                        outcome_terms.append(label)
                if not outcome_terms:
                    outcome_terms = ["需求", "收入", "盈利"]
                outcomes = "、".join(outcome_terms[:3])
                financial_zh = (
                    f"该渠道可能压低{outcomes}"
                    if direction_mode == "bearish"
                    else f"该渠道可支撑{outcomes}"
                )
            zh_connectors = ("因此", "据此", "若该逻辑展开")
            zh = (
                f"{zh_name}：{business_zh}；{connection_zh}；"
                f"{zh_connectors[template_index]}，{financial_zh}。"
            )
            return cls._multilingual(en=en, zh=zh)

        if is_etf:
            is_inverse = cls._is_inverse_candidate(candidate)
            liquidity_en, liquidity_zh = cls._liquidity_limitation(candidate)

            def etf_multilingual(*, en: str, zh: str) -> dict:
                return cls._multilingual(
                    en=f"{en}{liquidity_en}",
                    zh=f"{zh}{liquidity_zh}",
                )

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
            benchmark_code = str(candidate.get("benchmark_code") or "").strip()
            benchmark_ticker = benchmark.partition(":")[2] if ":" in benchmark else benchmark
            if not benchmark_ticker and benchmark_code:
                benchmark_ticker = (
                    benchmark_code.partition(":")[2]
                    if ":" in benchmark_code else benchmark_code
                )
            tracked_index = str(candidate.get("index_tracked") or "").strip()

            if is_inverse:
                try:
                    leverage = abs(float(candidate.get("leverage")))
                    if not math.isfinite(leverage) or leverage <= 0:
                        raise ValueError
                except (TypeError, ValueError):
                    leverage = None
                multiple = f"{leverage:g}" if leverage is not None else ""
                reference_values = cls._derivative_reference_values(candidate)
                concentration_label = cls._derivative_concentration_label(candidate)
                if concentration_label == "single underlying":
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
                    if multiple:
                        objective_en = (
                            f"{name} offers tactical downside exposure by seeking approximately "
                            f"{multiple} times the inverse daily return of {reference}."
                        )
                        objective_zh = (
                            f"{name}力求实现{reference_zh}单日收益约{multiple}倍的反向表现，"
                            "为战术性下行配置提供工具。"
                        )
                    else:
                        objective_en = (
                            f"{name} offers tactical downside exposure through the inverse daily "
                            f"performance of {reference}."
                        )
                        objective_zh = (
                            f"{name}通过跟踪{reference_zh}的单日反向表现，为战术性下行配置提供工具。"
                        )
                else:
                    multiple_phrase = f"{multiple}x daily " if multiple else "daily "
                    objective_en = (
                        f"{name} offers a tactical way to express downside through its "
                        f"{multiple_phrase}inverse mandate; the reference asset was not supplied."
                    )
                    objective_zh = (
                        f"{name}凭借其{'约' + multiple + '倍' if multiple else ''}单日反向策略，"
                        "为战术性下行配置提供工具；参考标的未提供。"
                    )
                return etf_multilingual(
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

            if cls._has_derivative_structure(candidate):
                try:
                    leverage = abs(float(candidate.get("leverage")))
                    if not math.isfinite(leverage) or leverage <= 0:
                        raise ValueError
                except (TypeError, ValueError):
                    leverage = None
                multiple = f"{leverage:g}" if leverage is not None else ""
                reference_values = cls._derivative_reference_values(candidate)
                concentration_label = cls._derivative_concentration_label(candidate)
                if concentration_label == "single underlying":
                    concentration_en = (
                        " A single-underlying mandate also creates concentration risk."
                    )
                    concentration_zh = "单一标的结构还带来集中度风险。"
                elif concentration_label:
                    concentration_en = (
                        f" Its focus on {concentration_label} also creates concentration risk."
                    )
                    concentration_zh = (
                        f"其对{concentration_label}的集中配置还带来集中度风险。"
                    )
                else:
                    concentration_en = ""
                    concentration_zh = ""
                bearish_long = direction_mode == "bearish"
                if reference_values:
                    reference = cls._human_join(reference_values)
                    reference_zh = cls._human_join_zh(reference_values)
                    if bearish_long and multiple:
                        objective_en = (
                            f"{name} seeks approximately {multiple} times the positive daily "
                            f"return of {reference}; under the downside thesis, declines in that "
                            "reference can amplify daily losses and reduce the fund's value."
                        )
                        objective_zh = (
                            f"{name}力求实现{reference_zh}单日收益约{multiple}倍的正向表现；"
                            "若下行逻辑兑现，参考标的下跌会放大单日损失并压低基金价值。"
                        )
                    elif bearish_long:
                        objective_en = (
                            f"{name} provides positive daily exposure to {reference}; under the "
                            "downside thesis, declines in that reference can increase losses and "
                            "reduce the fund's value."
                        )
                        objective_zh = (
                            f"{name}提供对{reference_zh}的单日正向敞口；若下行逻辑兑现，"
                            "参考标的下跌会增加损失并压低基金价值。"
                        )
                    elif multiple:
                        objective_en = (
                            f"{name} amplifies a bullish view by seeking approximately {multiple} "
                            f"times the positive daily return of {reference}."
                        )
                        objective_zh = (
                            f"{name}力求实现{reference_zh}单日收益约{multiple}倍的正向表现，"
                            "可放大看多观点。"
                        )
                    else:
                        objective_en = (
                            f"{name} offers tactical bullish exposure to the daily performance "
                            f"of {reference}."
                        )
                        objective_zh = (
                            f"{name}跟踪{reference_zh}的单日正向表现，为战术性看多配置提供工具。"
                        )
                elif bearish_long:
                    multiple_phrase = f"{multiple}x daily " if multiple else "daily "
                    objective_en = (
                        f"{name} uses a {multiple_phrase}positive mandate; the reference asset "
                        "was not supplied, but under the downside thesis falling exposure can "
                        "increase losses and reduce the fund's value."
                    )
                    objective_zh = (
                        f"{name}采用{'约' + multiple + '倍' if multiple else ''}单日正向策略；"
                        "参考标的未提供，但在下行情景下，底层敞口下跌会增加损失并压低基金价值。"
                    )
                else:
                    multiple_phrase = f"{multiple}x daily " if multiple else "daily "
                    objective_en = (
                        f"{name} offers a tactical way to amplify upside through its "
                        f"{multiple_phrase}bullish mandate; the reference asset was not supplied."
                    )
                    objective_zh = (
                        f"{name}凭借其{'约' + multiple + '倍' if multiple else ''}单日正向策略，"
                        "为战术性看多配置提供工具；参考标的未提供。"
                    )
                return etf_multilingual(
                    en=(
                        f"{objective_en} Daily reset, compounding, and path dependence can make "
                        "multi-day performance differ from a simple long multiple of the reference "
                        f"asset's cumulative move.{concentration_en}"
                    ),
                    zh=(
                        f"{objective_zh}每日重置、复利和路径依赖可能使其多日表现偏离参考标的"
                        f"累计涨跌幅的简单正向倍数。{concentration_zh}"
                    ),
                )

            if holding_tickers:
                examples = cls._human_join(holding_tickers[:3])
                examples_zh = cls._human_join_zh(holding_tickers[:3])
                if direction_mode == "bearish":
                    return etf_multilingual(
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
                return etf_multilingual(
                    en=(
                        f"{name} packages holdings such as {examples} into one trade, giving "
                        "investors diversified access to their operating upside; stronger revenue "
                        "and earnings across the basket can lift the fund's net asset value."
                    ),
                    zh=(
                        f"{name}将{examples_zh}等持仓组合为一站式投资工具，分散获取其经营"
                        "上行机会；篮子内公司收入和盈利改善可推升基金净值。"
                    ),
                )
            if related_tickers:
                examples = cls._human_join(related_tickers[:3])
                examples_zh = cls._human_join_zh(related_tickers[:3])
                if direction_mode == "bearish":
                    return etf_multilingual(
                        en=(
                            f"{name} provides one-trade exposure to a strategy linked with "
                            f"securities such as {examples}; weakness across those underlying "
                            "positions can reduce portfolio value and pressure the fund's NAV."
                        ),
                        zh=(
                            f"{name}通过与{examples_zh}等证券相关的策略提供一站式配置；"
                            "相关底层头寸走弱会压低组合价值并拖累基金净值。"
                        ),
                    )
                return etf_multilingual(
                    en=(
                        f"{name} provides one-trade access to a strategy linked with securities such "
                        f"as {examples}; gains across those underlying exposures can translate into "
                        "fund-level upside."
                    ),
                    zh=(
                        f"{name}通过与{examples_zh}等证券相关的策略提供一站式配置；"
                        "相关底层敞口上涨可转化为基金层面的上行空间。"
                    ),
                )
            if benchmark_ticker or tracked_index:
                reference = benchmark_ticker or tracked_index
                if direction_mode == "bearish":
                    return etf_multilingual(
                        en=(
                            f"{name} references {reference}. Under the downside thesis, declines "
                            "in that reference would reduce the fund's value; tracking and "
                            "portfolio differences can change the magnitude."
                        ),
                        zh=(
                            f"{name}以{reference}为参考。若下行逻辑兑现，该基准下跌会压低"
                            "基金价值；跟踪方式与组合差异可能改变实际幅度。"
                        ),
                    )
                return etf_multilingual(
                    en=(
                        f"{name} references {reference}, offering efficient one-trade exposure to "
                        "that market segment; appreciation across the tracked portfolio can drive "
                        "net asset value higher."
                    ),
                    zh=(
                        f"{name}以{reference}为参考，可高效一站式配置相关市场板块；"
                        "跟踪组合上涨可推动基金净值走高。"
                    ),
                )
            raw_categories = candidate.get("pool_labels", [])
            if isinstance(raw_categories, str):
                raw_categories = [raw_categories]
            categories = [
                str(v).strip() for v in [
                    *raw_categories,
                    candidate.get("fund_category"), candidate.get("fund_focus"),
                    candidate.get("fund_niche"), candidate.get("asset_class"),
                ] if str(v or "").strip()
            ]
            if categories:
                category_text = cls._human_join(categories[:2])
                category_text_zh = cls._human_join_zh(categories[:2])
                if direction_mode == "bearish":
                    return etf_multilingual(
                        en=(
                            f"{name}'s fund categories include {category_text}. The fund can lose "
                            "value when assets affected by the downside thesis decline, while "
                            "current holdings determine the magnitude of that sensitivity."
                        ),
                        zh=(
                            f"{name}的基金类别包括{category_text_zh}。当受下行逻辑影响的资产"
                            "下跌时，基金可能损失价值，具体敏感度取决于最新持仓。"
                        ),
                    )
                return etf_multilingual(
                    en=(
                        f"{name} targets {category_text}, giving investors diversified one-trade "
                        "access to that opportunity set; improving demand and earnings across its "
                        "portfolio can support net asset value growth."
                    ),
                    zh=(
                        f"{name}聚焦{category_text_zh}，让投资者一站式分散配置相关机会；"
                        "组合内需求与盈利改善可支持基金净值增长。"
                    ),
                )
            if direction_mode == "bearish":
                return etf_multilingual(
                    en=(
                        f"{name} packages its underlying portfolio into one liquid trade; under "
                        "the downside thesis, weakness across those holdings can reduce portfolio "
                        "value and pressure the fund's net asset value."
                    ),
                    zh=(
                        f"{name}将底层组合整合为一项流动性工具；若下行逻辑兑现，持仓走弱"
                        "会压低组合价值并拖累基金净值。"
                    ),
                )
            return etf_multilingual(
                en=(
                    f"{name} offers an exchange-traded, one-ticket portfolio allocation; gains "
                    "across its underlying holdings can compound into higher net asset value while "
                    "spreading issuer-specific exposure."
                ),
                zh=(
                    f"{name}提供可交易的一站式组合配置；底层持仓上涨可汇聚为基金净值增长，"
                    "同时分散单一发行人的敞口。"
                ),
            )

        return cls._multilingual(
            en=(
                f"{name} provides a listed, liquid vehicle for portfolio exposure; operating "
                "improvement across its underlying assets can translate into capital appreciation."
            ),
            zh=(
                f"{name}提供上市、可交易的组合配置工具；底层资产经营改善可转化为资本增值。"
            ),
        )

    @staticmethod
    def _narrative_record(
        candidate: dict, kind: str, brief: dict | None = None,
    ) -> dict:
        """Expose only evidence needed for prose, never ranking or calculation inputs."""
        record = {
            "candidate_id": candidate.get("candidate_id") or candidate["code"],
            "market_code": candidate["code"],
            "name": candidate["name"],
        }
        if kind.lower() != "etf":
            for source_key, output_key in (
                ("name_zh", "company name in Chinese"),
                ("company_introduction", "company business"),
                ("sector", "sector"),
                ("industry", "industry"),
                ("business_fact", "business fact"),
                ("theme_connection", "event relationship"),
                ("financial_pathway", "financial pathway"),
                ("reason", "structural exposure evidence"),
                ("verified_article_evidence", "verified article evidence"),
            ):
                value = _normalised_text(candidate.get(source_key))
                if value:
                    record[output_key] = value
            relation_type = str(candidate.get("relation_type") or "").strip()
            if relation_type and relation_type != "none":
                record["relationship type"] = relation_type
            effect = str(candidate.get("directional_effect") or "").strip()
            brief = brief if isinstance(brief, dict) else {}
            required_effect = (
                "negative"
                if _norm_theme_direction(brief.get("theme_direction")) == "bearish"
                else "positive"
            )
            if effect == required_effect:
                record["directional effect"] = effect
            # The generated brief can guide discovery but cannot ground prose.
            # Only the user's literal theme and fetched article excerpts cross
            # this boundary; the validated relationship fields above carry the
            # candidate-specific catalyst and financial transmission.
            input_theme = _normalised_text(brief.get("input_theme"))
            if input_theme:
                record["event/theme"] = input_theme
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
        if candidate.get("bearish_downside_exposure"):
            record["required downside framing"] = (
                "Weakness or declines in the relevant holdings reduce or pressure "
                "the fund's portfolio value or NAV; present this basket as vulnerable, "
                "not as a bullish beneficiary."
            )

        if ThemeWorkflow._has_derivative_structure(candidate):
            direction = str(candidate.get("direction") or "").strip()
            if direction:
                record["fund direction"] = direction
            try:
                leverage = float(candidate.get("leverage"))
            except (TypeError, ValueError):
                leverage = None
            if leverage is not None and math.isfinite(leverage):
                record["daily leverage multiple"] = abs(leverage)

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

        lane = str(
            candidate.get("selection_lane") or candidate.get("etf_lane") or ""
        ).strip().lower()
        if candidate.get("single_stock_inverse"):
            record["single-underlying inverse mandate"] = True
        elif (
            candidate.get("single_stock_leveraged")
            or candidate.get("single_stock_wrapper")
            or lane == "single_stock_leveraged"
        ):
            record["single-underlying leveraged mandate"] = True

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
        benchmark_code = str(candidate.get("benchmark_code") or "").strip()
        if benchmark_code:
            record["reference underlying market code"] = benchmark_code
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
        if candidate.get("low_liquidity"):
            reasons = candidate.get("low_liquidity_reasons") or []
            if isinstance(reasons, str):
                reasons = [reasons]
            reason_labels = {
                "aum_below_25m": "assets under management below $25 million",
                "turnover_below_1m": "latest turnover below $1 million",
            }
            record["liquidity limitation"] = {
                "status": "low liquidity",
                "reasons": [
                    reason_labels.get(str(reason), str(reason).replace("_", " "))
                    for reason in reasons if str(reason).strip()
                ],
                "aum": candidate.get("aum"),
                "latest turnover": candidate.get("turnover"),
            }
        return record

    def _assemble(
        self, chosen, narr, event_date, theme_direction="bullish", *, theme="",
    ):
        self._assign_theme_exposure(chosen)
        items = []
        for c in chosen:
            n = narr.get(c["code"], {})
            rationale = self._validated_candidate_rationale(
                c, n.get("theme_rationale"), theme_direction)
            if rationale is None:
                rationale = self._fallback_theme_rationale(
                    c,
                    theme_direction,
                    theme=_normalised_text(theme or c.get("_fallback_theme")),
                    rank=c.get("stock_selection_rank"),
                )
                _log(
                    f"assembly rendered deterministic rationale fallback for "
                    f"{c.get('code') or 'unknown security'}"
                )
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
    @staticmethod
    def _narrative_fingerprint(
        candidate: dict, rationale: dict, language: str = "en",
    ) -> str:
        """Normalize company identity away so templated stock copy is detectable."""
        language = "zh" if language == "zh" else "en"
        text = _normalised_text(rationale.get(language)).casefold()
        identities = {
            _normalised_text(candidate.get("name")).casefold(),
            str(candidate.get("code") or "").casefold(),
            str(candidate.get("code") or "").partition(":")[2].casefold(),
            _normalised_text(candidate.get("name_zh")).casefold(),
            _normalised_text(candidate.get("security_name_zh")).casefold(),
        }
        for identity in sorted((v for v in identities if v), key=len, reverse=True):
            if language == "en" and re.fullmatch(r"[a-z0-9 .:&'-]+", identity):
                text = re.sub(
                    rf"(?<![a-z0-9]){re.escape(identity)}(?![a-z0-9])",
                    " company ", text,
                )
            else:
                text = text.replace(identity, " company ")
        text = re.sub(
            r"\b(?:inc|incorporated|corp|corporation|company|co|plc|ltd)\b",
            " company ", text)
        text = re.sub(r"\d+(?:\.\d+)?", "#", text)
        if language == "zh":
            text = re.sub(
                r"^.{1,60}?(?=(?:是一家|是一间|是|主营|生产|制造|销售|提供|经营|"
                r"运营|开发|设计|供应|从事))",
                "company",
                text,
            )
            return re.sub(r"[^a-z0-9#\u4e00-\u9fff]+", "", text).strip()
        return re.sub(r"[^a-z0-9#]+", " ", text).strip()

    @classmethod
    def _narrative_fingerprint_pair(
        cls, candidate: dict, rationale: dict,
    ) -> dict[str, str]:
        return {
            language: cls._narrative_fingerprint(
                candidate, rationale, language=language)
            for language in ("en", "zh")
        }

    @staticmethod
    def _narrative_is_near_duplicate(
        fingerprint: str, existing: list[str] | set[str] | tuple[str, ...],
        *, similarity_threshold: float = 0.86,
    ) -> bool:
        """Catch lightly reworded templates after company identity is removed."""
        if not fingerprint:
            return False
        tokens = set(fingerprint.split())
        for other in existing:
            if fingerprint == other:
                return True
            other_tokens = set(other.split())
            union = tokens | other_tokens
            jaccard = len(tokens & other_tokens) / len(union) if union else 0.0
            if (
                SequenceMatcher(None, fingerprint, other).ratio() >= similarity_threshold
                or (min(len(tokens), len(other_tokens)) >= 8 and jaccard >= 0.82)
            ):
                return True
        return False

    @classmethod
    def _narrative_pair_is_near_duplicate(
        cls,
        fingerprints: dict[str, str],
        existing: list[dict[str, str]],
    ) -> bool:
        """Reject a repeated template in either public language."""
        return any(
            cls._narrative_is_near_duplicate(
                fingerprints.get(language, ""),
                [pair.get(language, "") for pair in existing],
                similarity_threshold=0.82 if language == "zh" else 0.86,
            )
            for language in ("en", "zh")
        )

    @classmethod
    def _validate_stock_narrative(
        cls,
        candidate: dict,
        value: object,
        theme_direction: str | None = None,
    ) -> tuple[dict | None, str]:
        rationale = cls._validated_candidate_rationale(candidate, value)
        if rationale is None:
            return None, "invalid_or_forbidden_prose"
        en = rationale["en"]
        zh = rationale["zh"]
        word_count = len(re.findall(r"[A-Za-z0-9][A-Za-z0-9'/-]*", en))
        if word_count < 12 or word_count > 60:
            return None, "length_outside_broker_range"
        identity_tokens = _candidate_identity_stems(candidate)
        en_token_positions = [
            (_lane_stem(match.group(0).casefold()), match.start())
            for match in re.finditer(r"[A-Za-z0-9]+", en)
        ]
        identity_positions = [
            position for stem, position in en_token_positions
            if stem in identity_tokens
        ]
        if identity_tokens and not identity_positions:
            return None, "missing_company_identity"
        financial_en = re.compile(
            r"\b(?:orders?|sales|revenue|costs?|margins?|earnings|profits?|cash flow)\b",
            re.IGNORECASE,
        )
        financial_matches = list(financial_en.finditer(en))
        if not financial_matches:
            return None, "missing_financial_pathway"
        zh_financial = list(re.finditer(
            r"订单|销售额|销量|销售增长|销售下降|销售下滑|收入|成本|利润率|"
            r"盈利|利润|现金流",
            zh,
        ))
        if not zh_financial:
            return None, "missing_chinese_financial_pathway"
        financial_outcomes_en: set[str] = set()
        for label, pattern in (
            ("orders", r"\borders?\b"),
            ("revenue", r"\b(?:sales|revenue)\b"),
            ("costs", r"\bcosts?\b"),
            ("margins", r"\bmargins?\b"),
            ("earnings", r"\b(?:earnings|profits?)\b"),
            ("cash_flow", r"\bcash flow\b"),
        ):
            if re.search(pattern, en, re.IGNORECASE):
                financial_outcomes_en.add(label)
        financial_outcomes_zh: set[str] = set()
        for label, pattern in (
            ("orders", r"订单"),
            ("revenue", r"销售额|销量|销售增长|销售下降|销售下滑|收入"),
            ("costs", r"成本"),
            ("margins", r"利润率"),
            ("earnings", r"盈利|利润(?!率)"),
            ("cash_flow", r"现金流"),
        ):
            if re.search(pattern, zh):
                financial_outcomes_zh.add(label)
        if re.search(
            r"(?:提升|增加|支撑|带动|推动|压低|拖累|减少|削弱|降低)"
            r".{0,24}销售",
            zh,
        ):
            financial_outcomes_zh.add("revenue")
        if financial_outcomes_en != financial_outcomes_zh:
            return None, "bilingual_financial_outcome_mismatch"
        if (_NEGATED_DIRECTION_EN_RE.search(en)
                or _NEGATED_FINANCIAL_SUPPORT_RE.search(en)
                or _NEGATED_DIRECTION_ZH_RE.search(zh)):
            return None, "direction_mismatch"

        supplied_evidence = ". ".join(
            _normalised_text(candidate.get(field)) for field in (
                "company_introduction", "business_fact", "theme_connection",
                "financial_pathway", "verified_article_evidence",
            )
        )
        rationale_text = f"{en} {zh}"
        if _financial_magnitude_claims(rationale_text):
            return None, "unsupported_financial_magnitude"
        if not _commercial_claim_categories(rationale_text) <= (
                _affirmative_commercial_claim_categories(supplied_evidence)):
            return None, "unsupported_relationship_claim"
        en_commercial_categories = _commercial_claim_categories(en)
        zh_commercial_categories = _commercial_claim_categories(zh)
        if en_commercial_categories != zh_commercial_categories:
            return None, "bilingual_relationship_mismatch"
        if not _commercial_clauses_are_supported(
                candidate, en, supplied_evidence):
            return None, "unsupported_relationship_clause"
        relationship_parties = _external_proper_nouns(
            candidate.get("theme_connection"), candidate)
        business_parties = _external_proper_nouns(
            ". ".join(_normalised_text(candidate.get(field)) for field in (
                "company_introduction", "business_fact",
            )),
            candidate,
        )
        product_like_parties = business_parties - relationship_parties
        source_parties = (
            _external_proper_nouns(supplied_evidence, candidate)
            - product_like_parties
        )
        source_associations = _named_party_commercial_associations(
            supplied_evidence, candidate,
            known_parties=source_parties,
        )
        narrative_associations = _named_party_commercial_associations(
            en, candidate, known_parties=source_parties,
        )
        if any(
            categories and not categories <= source_associations.get(party, set())
            for party, categories in narrative_associations.items()
            if party not in product_like_parties
        ):
            return None, "unsupported_counterparty_relationship"
        rationale_named_parties = _external_proper_nouns(en, candidate)
        evidence_named_parties = _external_proper_nouns(
            supplied_evidence, candidate)
        if not rationale_named_parties <= evidence_named_parties:
            return None, "unsupported_named_counterparty"
        # Preserve supplied Latin proper names in Chinese rather than silently
        # accepting a newly invented counterparty or product alias.  This is
        # deliberately checked before the CJK translation grammar below,
        # whose ASCII stripping is intended only for punctuation and syntax.
        chinese_named_parties = _external_proper_nouns(zh, candidate)
        bilingual_named_evidence = _external_proper_nouns(
            f"{en}. {supplied_evidence}", candidate)
        if not chinese_named_parties <= bilingual_named_evidence:
            return None, "unsupported_chinese_factual_claim"
        # Also compare every Latin-script content token.  Proper-noun casing is
        # not a trust boundary: ``nvidia`` in otherwise Chinese prose is just as
        # capable of inventing a counterparty as ``NVIDIA``.  Exact supplied
        # product names and acronyms remain valid because they occur in the
        # English rationale or the application-owned evidence.
        chinese_latin_tokens = _grounding_tokens(
            zh, candidate, stopwords=set())
        bilingual_latin_tokens = _grounding_tokens(
            f"{en}. {supplied_evidence}", candidate, stopwords=set())
        if not chinese_latin_tokens <= bilingual_latin_tokens:
            return None, "unsupported_chinese_factual_claim"
        if _unsupported_english_factual_tokens(candidate, en, supplied_evidence):
            return None, "unsupported_factual_claim"

        direction_mode = (
            _norm_theme_direction(theme_direction)
            if theme_direction is not None else None
        )
        directional_effect = (
            "negative" if direction_mode == "bearish" else "positive"
            if direction_mode == "bullish" else
            str(candidate.get("directional_effect") or "").lower()
        )
        if directional_effect == "positive" and _BULLISH_ANTI_STANCE_RE.search(
                rationale_text):
            return None, "stance_mismatch"
        if directional_effect in {"positive", "negative"}:
            if (not _directional_pathway_is_consistent(en, directional_effect)
                    or not _directional_chinese_is_consistent(
                        zh, directional_effect)):
                return None, "direction_mismatch"
        business_fact = _normalised_text(candidate.get("business_fact"))
        business_tokens = _grounding_tokens(
            business_fact,
            candidate,
            stopwords=_GROUNDING_BUSINESS_STOPWORDS | {
                "cost", "costs", "demand", "earnings", "margin", "margins",
                "orders", "profit", "profits", "revenue", "sales",
            },
        )
        if not business_tokens:
            return None, "missing_business_fact"
        business_positions = [
            position for stem, position in en_token_positions
            if stem in business_tokens
        ]
        if not business_positions:
            return None, "missing_business_fact"

        theme_connection = _normalised_text(candidate.get("theme_connection"))
        connection_tokens = _grounding_tokens(
            theme_connection,
            candidate,
            stopwords=_GROUNDING_RELATION_STOPWORDS | {
                "cost", "costs", "earnings", "margin", "margins", "orders",
                "profit", "profits", "revenue", "sales",
            },
        ) - business_tokens
        if not connection_tokens:
            return None, "missing_event_relationship"
        business_position = min(business_positions)
        if financial_matches[0].start() < business_position:
            return None, "reasoning_order_mismatch"
        if identity_positions and min(identity_positions) > business_position:
            return None, "company_introduction_order_mismatch"
        connection_positions = [
            position for stem, position in en_token_positions
            if stem in connection_tokens
        ]
        if not connection_positions:
            return None, "missing_event_relationship"

        connection_after_business = [
            position for position in connection_positions
            if position > business_position
        ]
        if not connection_after_business:
            return None, "reasoning_order_mismatch"
        connection_position = min(connection_after_business)
        financial_position = financial_matches[-1].start()
        if not business_position < connection_position < financial_position:
            return None, "reasoning_order_mismatch"

        def concept_names(source: str) -> set[str]:
            return {
                name for name, (source_pattern, _) in _BILINGUAL_CONCEPT_PATTERNS.items()
                if source_pattern.search(source)
            }

        supplied_concepts = concept_names(supplied_evidence)
        chinese_claim_concepts = {
            name for name, (_, chinese_pattern) in _BILINGUAL_CONCEPT_PATTERNS.items()
            if chinese_pattern.search(zh)
        }
        if not chinese_claim_concepts <= supplied_concepts:
            return None, "unsupported_chinese_factual_claim"
        if _unsupported_chinese_factual_content(
                candidate, zh, supplied_evidence):
            return None, "unsupported_chinese_factual_claim"

        def chinese_concept_positions(names: set[str]) -> list[int]:
            positions = []
            for name in names:
                source_pattern, chinese_pattern = _BILINGUAL_CONCEPT_PATTERNS[name]
                match = chinese_pattern.search(zh) or source_pattern.search(zh)
                if match:
                    positions.append(match.start())
            return positions

        business_concepts = concept_names(business_fact)
        connection_concepts = concept_names(theme_connection) - business_concepts
        if not connection_concepts:
            connection_concepts = concept_names(theme_connection)
        zh_business_positions = chinese_concept_positions(business_concepts)
        zh_connection_positions = chinese_concept_positions(connection_concepts)

        def ascii_evidence_positions(source: str, stopwords: set[str]) -> list[int]:
            tokens = _grounding_tokens(source, candidate, stopwords=stopwords)
            return [
                match.start()
                for token in tokens
                for match in re.finditer(
                    rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])",
                    zh,
                    re.IGNORECASE,
                )
            ]

        if not business_concepts:
            zh_business_positions = ascii_evidence_positions(
                business_fact, _GROUNDING_BUSINESS_STOPWORDS)
        if not connection_concepts:
            zh_connection_positions = ascii_evidence_positions(
                theme_connection, _GROUNDING_RELATION_STOPWORDS)
        if not zh_business_positions:
            return None, "missing_chinese_business_fact"
        zh_business_position = min(zh_business_positions)
        if zh_financial[0].start() < zh_business_position:
            return None, "chinese_reasoning_order_mismatch"
        zh_connection_after_business = [
            position for position in zh_connection_positions
            if position > zh_business_position
        ]
        if not zh_connection_after_business:
            return None, "missing_chinese_event_relationship"
        zh_connection_position = min(zh_connection_after_business)
        if not zh_business_position < zh_connection_position < zh_financial[-1].start():
            return None, "chinese_reasoning_order_mismatch"

        # Both public languages must pitch the same operating business. Without
        # this comparison, English could sell devices while Chinese sells
        # services even though both facts independently occur in the source.
        material_business_concepts = {
            "advertising", "cloud", "connectivity", "devices", "energy",
            "finance", "housing", "memory", "packaging_test", "products",
            "semiconductors", "services", "software",
        }
        en_business_clause = en[business_position:connection_position]
        zh_business_clause = zh[zh_business_position:zh_connection_position]
        en_business_claims = {
            name for name, (source_pattern, _) in _BILINGUAL_CONCEPT_PATTERNS.items()
            if name in material_business_concepts
            and name in business_concepts
            and source_pattern.search(en_business_clause)
        }
        zh_business_claims = {
            name for name, (source_pattern, chinese_pattern) in _BILINGUAL_CONCEPT_PATTERNS.items()
            if name in material_business_concepts
            and name in business_concepts
            and (
                chinese_pattern.search(zh_business_clause)
                or source_pattern.search(zh_business_clause)
            )
        }
        def without_generic_business_concepts(values: set[str]) -> set[str]:
            specific = values - {"products", "services"}
            return specific or values

        normalized_en_business = without_generic_business_concepts(
            en_business_claims
        )
        normalized_zh_business = without_generic_business_concepts(
            zh_business_claims
        )
        if (
            normalized_en_business
            and normalized_zh_business
            and normalized_en_business.isdisjoint(normalized_zh_business)
        ):
            return None, "bilingual_business_mismatch"

        native_identity_values = [
            value for value in (
                _normalised_text(candidate.get("name_zh")),
                _normalised_text(candidate.get("security_name_zh")),
            ) if value
        ]
        core_name_tokens = [
            token for token in re.findall(
                r"[A-Za-z0-9]+", _normalised_text(candidate.get("name")))
            if len(token) >= 3
            and _lane_stem(token.casefold()) not in _IDENTITY_STOPWORDS
        ]
        core_name = " ".join(core_name_tokens)
        identity_values = (
            native_identity_values
            if native_identity_values else [
                _normalised_text(candidate.get("name")), core_name,
            ]
        )
        zh_casefold = zh.casefold()
        zh_identity_matches = [
            (zh_casefold.find(value.casefold()), len(value))
            for value in identity_values
            if value and value.casefold() in zh_casefold
        ]
        if not zh_identity_matches:
            return None, "missing_chinese_company_identity"
        zh_identity_position, zh_identity_length = min(zh_identity_matches)
        if zh_identity_position > zh_business_position:
            return None, "chinese_company_introduction_order_mismatch"
        intro_gap = zh[
            zh_identity_position + zh_identity_length:zh_business_position
        ]
        intro_gap = re.sub(
            r"是一家|是一间|旗下|核心|主要|主营|业务|包括|专注于|生产|制造|"
            r"销售|提供|经营|运营|开发|设计|供应|从事|通过|依靠|围绕|的|以",
            "",
            intro_gap,
        )
        intro_gap = re.sub(r"[A-Za-z0-9\s()（）,，;；:：·.'\"-]+", "", intro_gap)
        if _CJK_RE.search(intro_gap):
            return None, "unsupported_chinese_issuer_claim"
        return rationale, "accepted"

    def _narrative_call(
        self, inp: dict, brief: dict, chosen: list[dict], kind: str,
    ) -> tuple[dict[str, dict | None], object]:
        self._ensure_candidate_ids(chosen)
        records = [self._narrative_record(c, kind, brief) for c in chosen]
        user = prompts.NARRATIVE_USER.format(
            theme=inp["theme"], summary=brief.get("summary", ""),
            thesis=brief.get("thesis", ""), as_of=self.as_of,
            theme_direction=_norm_theme_direction(brief.get("theme_direction")),
            kind=kind, records=json.dumps(records, ensure_ascii=False))
        kwargs = {
            "max_tokens": 4000,
            "response_schema": _narrative_response_schema(chosen),
            "schema_name": (
                "etf_broker_narratives"
                if kind.lower() == "etf"
                else "stock_broker_narratives"
            ),
        }
        raw = self.llm.chat_json(prompts.NARRATIVE_SYS, user, **kwargs)
        arr = raw.get("items", []) if isinstance(raw, dict) else raw
        arr = arr if isinstance(arr, list) else []
        return self._join_relevance_rows(
            chosen, arr, strict_identity=True), raw

    def narrate(self, inp: dict, brief: dict, chosen: list[dict], kind: str) -> dict:
        if not chosen:
            return {}
        # Assign stable IDs across the complete frozen basket before slicing it
        # into bounded request schemas. Per-batch assignment would restart at
        # S0001 and break identity uniqueness for custom targets above 20.
        self._ensure_candidate_ids(chosen)
        self._last_stock_narrative_errors = {}
        self._last_stock_narrative_retried_codes = set()
        self._last_stock_narrative_transport_error = None
        # Bound each primary schema so positive custom targets cannot overflow a
        # single 4k response or turn a valid exact basket into malformed JSON.
        batch_size = max(
            1, min(20, int(self.opts.get("relevance_batch", 10) or 10))
        )
        joined: dict[str, dict | None] = {}
        for offset in range(0, len(chosen), batch_size):
            batch = chosen[offset:offset + batch_size]
            try:
                batch_joined, _ = self._narrative_call(inp, brief, batch, kind)
                joined.update(batch_joined)
            except Exception as exc:
                # Both baskets are already frozen. Treat a failed batch as one
                # missing row per member so each code gets its same-code retry.
                batch_codes = ",".join(
                    str(candidate.get("code") or "") for candidate in batch
                )
                _log(
                    f"{kind} narrative primary request failed for "
                    f"{batch_codes} ({exc}); retrying each frozen market code once"
                )
                if kind.lower() != "etf":
                    self._last_stock_narrative_transport_error = str(exc)

        output: dict[str, dict] = {}
        errors: dict[str, str] = {}
        accepted_fingerprints: list[dict[str, str]] = []
        candidate_by_code = {
            str(candidate.get("code") or ""): candidate for candidate in chosen
        }
        for code, candidate in candidate_by_code.items():
            obj = joined.get(code)
            if not isinstance(obj, dict):
                errors[code] = "missing_response_row"
                continue
            if kind.lower() == "etf":
                rationale = self._validated_candidate_rationale(
                    candidate,
                    obj.get("theme_rationale"),
                    brief.get("theme_direction"),
                )
                if rationale is None:
                    errors[code] = "invalid_or_forbidden_prose"
                    continue
            else:
                rationale, reason = self._validate_stock_narrative(
                    candidate,
                    obj.get("theme_rationale"),
                    brief.get("theme_direction"),
                )
                if rationale is None:
                    errors[code] = reason
                    continue
                fingerprint = self._narrative_fingerprint_pair(candidate, rationale)
                if self._narrative_pair_is_near_duplicate(
                        fingerprint, accepted_fingerprints):
                    errors[code] = "duplicate_template"
                    continue
                accepted_fingerprints.append(fingerprint)
            output[code] = {"theme_rationale": rationale}

        if kind.lower() == "etf":
            retry_candidates = [
                candidate for candidate in chosen
                if candidate["code"] in errors
            ]
            for offset in range(0, len(retry_candidates), batch_size):
                retry_batch = retry_candidates[offset:offset + batch_size]
                retry_codes = [candidate["code"] for candidate in retry_batch]
                _log(
                    "ETF narrative targeted retry for frozen codes "
                    + ",".join(retry_codes)
                )
                try:
                    retry_joined, _ = self._narrative_call(
                        inp, brief, retry_batch, kind)
                except Exception as exc:
                    for code in retry_codes:
                        errors[code] = f"retry_failed:{exc}"
                    _log(
                        "ETF narrative targeted retry failed for "
                        f"{','.join(retry_codes)}: {exc}"
                    )
                    continue
                for candidate in retry_batch:
                    code = candidate["code"]
                    retry_obj = retry_joined.get(code)
                    rationale = self._validated_candidate_rationale(
                        candidate,
                        retry_obj.get("theme_rationale")
                        if isinstance(retry_obj, dict) else None,
                        brief.get("theme_direction"),
                    )
                    if rationale is None:
                        errors[code] = "invalid_or_forbidden_prose_after_retry"
                        _log(
                            f"ETF narrative retry rejected for {code}; "
                            "using same-code fund fallback"
                        )
                        continue
                    output[code] = {"theme_rationale": rationale}
                    errors.pop(code, None)
                    _log(f"ETF narrative retry recovered {code}")
            missing = [
                candidate["code"] for candidate in chosen
                if candidate["code"] not in output
            ]
            _log(
                f"ETF narrative coverage expected={len(chosen)} "
                f"received={len(output)} missing={','.join(missing) or '-'}"
            )
            return output

        retry_candidates = [
            candidate for candidate in chosen
            if candidate["code"] in errors
        ]
        for offset in range(0, len(retry_candidates), batch_size):
            retry_batch = retry_candidates[offset:offset + batch_size]
            retry_codes = [candidate["code"] for candidate in retry_batch]
            self._last_stock_narrative_retried_codes.update(retry_codes)
            _log(
                "stock narrative targeted retry for frozen codes "
                + ",".join(retry_codes)
            )
            try:
                retry_joined, _ = self._narrative_call(
                    inp, brief, retry_batch, kind)
            except Exception as exc:
                for code in retry_codes:
                    errors[code] = f"retry_failed:{exc}"
                _log(
                    "stock narrative targeted retry failed for "
                    f"{','.join(retry_codes)}: {exc}"
                )
                continue
            for candidate in retry_batch:
                code = candidate["code"]
                obj = retry_joined.get(code)
                rationale, retry_reason = self._validate_stock_narrative(
                    candidate,
                    obj.get("theme_rationale") if isinstance(obj, dict) else None,
                    brief.get("theme_direction"),
                )
                if rationale is None:
                    errors[code] = retry_reason
                    _log(f"stock narrative retry rejected for {code}: {retry_reason}")
                    continue
                fingerprint = self._narrative_fingerprint_pair(candidate, rationale)
                if self._narrative_pair_is_near_duplicate(
                        fingerprint, accepted_fingerprints):
                    errors[code] = "duplicate_template_after_retry"
                    _log(f"stock narrative retry rejected for {code}: duplicate template")
                    continue
                accepted_fingerprints.append(fingerprint)
                output[code] = {"theme_rationale": rationale}
                errors.pop(code, None)
                _log(f"stock narrative retry recovered {code}")

        self._last_stock_narrative_errors = dict(errors)
        missing = [candidate["code"] for candidate in chosen if candidate["code"] not in output]
        _log(
            f"stock narrative coverage expected={len(chosen)} received={len(output)} "
            f"missing={','.join(missing) or '-'}"
        )
        return output

    def finalize_stock_rationales(
        self, inp: dict, brief: dict, chosen: list[dict], target: int,
    ) -> tuple[list[dict], dict]:
        """Render prose for frozen stock members without changing membership."""
        chosen_codes = tuple(str(candidate.get("code") or "") for candidate in chosen)
        valid_unique_codes = {
            code for code in chosen_codes
            if _SECURITY_CODE_RE.fullmatch(code)
        }
        if len(chosen_codes) != target or len(valid_unique_codes) != target:
            raise SelectionUniverseError(
                "stock", target, len(valid_unique_codes),
                ("frozen_stock_selection",),
            )
        if not self._last_stock_membership_codes:
            # Direct helper callers may bypass screen_stock_sets.  Freeze their
            # supplied order once; production freezes it during phase one.
            self._last_stock_membership_codes = chosen_codes
        if chosen_codes != self._last_stock_membership_codes:
            raise RuntimeError(
                "stock membership changed after selection: "
                f"frozen={self._last_stock_membership_codes!r} "
                f"received={chosen_codes!r}"
            )
        if target == 0:
            return chosen, {}

        for rank, candidate in enumerate(chosen, 1):
            candidate.setdefault("stock_selection_rank", rank)
            candidate["stock_membership_frozen"] = True
            candidate["_fallback_theme"] = _normalised_text(inp.get("theme"))

        narrative_map = self.narrate(inp, brief, chosen, "stock")
        fallback_count = 0
        for rank, candidate in enumerate(chosen, 1):
            code = candidate["code"]
            if code in narrative_map:
                continue
            reason = self._last_stock_narrative_errors.get(
                code, "missing_validated_rationale")
            rationale = self._fallback_theme_rationale(
                candidate,
                _norm_theme_direction(brief.get("theme_direction")),
                theme=_normalised_text(inp.get("theme")),
                rank=rank,
            )
            narrative_map[code] = {"theme_rationale": rationale}
            fallback_count += 1
            _log(
                f"stock narrative fallback rendered for frozen member {code}: {reason}"
            )

        narrative_map = {
            candidate["code"]: narrative_map[candidate["code"]]
            for candidate in chosen
        }
        returned_codes = tuple(candidate["code"] for candidate in chosen)
        if returned_codes != chosen_codes or returned_codes != self._last_stock_membership_codes:
            raise RuntimeError("stock membership changed while rendering narratives")
        if tuple(narrative_map) != chosen_codes:
            raise RuntimeError(
                "stock narrative map does not match frozen membership order: "
                f"expected={chosen_codes!r} received={tuple(narrative_map)!r}"
            )
        _log(
            f"stock narration complete for {len(chosen_codes)}/{target} frozen members; "
            f"deterministic_fallbacks={fallback_count}"
        )
        return chosen, narrative_map

    def faq(self, inp: dict, brief: dict, stocks, etfs) -> list[dict]:
        user = prompts.FAQ_USER.format(
            theme=inp["theme"], thesis=brief.get("thesis", ""),
            stocks=", ".join(s["name"] for s in stocks),
            etfs=", ".join(e["name"] for e in etfs),
            date=inp["date"], n_min=4, n_max=8)
        try:
            raw = self.llm.chat_json(
                prompts.FAQ_SYS, user, max_tokens=3000,
            )
        except Exception as exc:
            _log(f"FAQ request failed ({exc}); continuing with an empty FAQ")
            raw = []
        arr = raw.get("items", []) if isinstance(raw, dict) else raw
        if not isinstance(arr, list):
            arr = []
        out = [{"question": o.get("question", ""), "answer": o.get("answer", "")}
               for o in arr if isinstance(o, dict) and o.get("question")]
        return out[:8]

    # -- driver --------------------------------------------------------------
    def run(self, payload: dict) -> dict:
        """Screen both exact baskets first, then narrate frozen members only."""
        self._last_stock_evidence = []
        self._last_stock_public_reserves = []
        self._last_stock_public_rejections = {}
        self._last_stock_membership_codes = ()
        self._last_etf_membership_codes: tuple[str, ...] = ()
        inp = validate_input(payload)
        stock_target = int(self.opts.get("stock_target", 8))
        etf_target = int(self.opts.get("etf_target", 5))
        candidate_budget = int(self.opts.get("stock_candidate_budget", 120))
        stock_cap = int(self.opts.get("stock_universe", 500))
        etf_cap = min(
            int(self.opts.get("etf_universe", 500)),
            int(DEFAULTS["etf_universe"]),
        )
        safety_cap = int(self.opts.get("max_scan") or 0)
        if safety_cap > 0:
            stock_cap = min(stock_cap, safety_cap)
            etf_cap = min(etf_cap, safety_cap)
        if stock_target < 0 or etf_target < 0:
            raise ValueError("stock_target and etf_target cannot be negative")
        if stock_target > candidate_budget:
            raise ValueError("stock_target cannot exceed stock_candidate_budget")
        if stock_target > stock_cap:
            raise ValueError("stock_target cannot exceed the effective stock universe")
        if etf_target > etf_cap:
            raise ValueError("etf_target cannot exceed the effective ETF universe")
        ev_int = scoring.event_date_int(inp["date"])
        _log(f"theme={inp['theme']!r} date={inp['date']} scene={self.quotes.cfg.scene}")
        _log(
            "run budgets: "
            f"stock_universe={self.opts['stock_universe']}, "
            f"stock_candidates={self.opts['stock_candidate_budget']}, "
            f"etf_universe={self.opts['etf_universe']}, "
            f"etf_evidence_stocks={self.opts['etf_evidence_stock_limit']}, "
            f"etf_portfolios={self.opts['etf_holdings_portfolio_budget']}, "
            f"etf_component_companies={self.opts['etf_holdings_unique_budget']}, "
            "etf_component_recovery="
            f"{self.opts['etf_holding_relevance_retry_budget']}"
        )

        started = time.monotonic()
        _log("article fetch started")
        art = fetch_article(inp["url"])
        _log(
            f"article fetch finished in {time.monotonic() - started:.1f}s; "
            f"ok={art['ok']} title={art.get('title','')[:60]!r}"
        )

        started = time.monotonic()
        _log("theme profile LLM request started")
        profile = self.theme_profile(inp["theme"])
        _log(f"theme profile stage finished in {time.monotonic() - started:.1f}s")
        started = time.monotonic()
        _log("event brief LLM request started")
        brief = self.event_brief(inp, art, profile)
        theme_direction = _norm_theme_direction(brief.get("theme_direction"))
        _log(
            f"event brief finished in {time.monotonic() - started:.1f}s; "
            f"direction={theme_direction}"
        )

        # Phase 1A: choose stocks. No narrative request is permitted in this phase.
        if stock_target == 0:
            stock_candidates = []
            top_stocks = []
            strict_etf_evidence = []
            _log("stock_target=0; public stocks and stock-led ETF evidence disabled")
        else:
            ecosystem_entities = self.event_ecosystem(inp, brief, profile, art)
            stock_candidates = self.stock_candidates(
                brief, profile, art, ecosystem_entities=ecosystem_entities,
            )
            top_stocks, strict_etf_evidence = self.screen_stock_sets(
                stock_candidates,
                brief,
                art,
                stock_target,
                rank_label="candidate lane",
            )

        frozen_stock_codes = tuple(stock["code"] for stock in top_stocks)
        if len(frozen_stock_codes) != stock_target or len(set(frozen_stock_codes)) != stock_target:
            raise SelectionUniverseError(
                "stock", stock_target, len(set(frozen_stock_codes)),
                ("live_ranked", "offline_es"),
            )
        self._last_stock_membership_codes = frozen_stock_codes

        # All public members are discovery anchors. Strict structural qualifiers
        # remain separately identifiable and therefore retain ranking precedence.
        strict_by_code = {
            stock["code"]: stock for stock in strict_etf_evidence
        }
        etf_evidence_stocks: list[dict] = []
        evidence_seen: set[str] = set()
        for stock in [*top_stocks, *strict_etf_evidence]:
            code = stock["code"]
            if code in evidence_seen:
                continue
            evidence_seen.add(code)
            stock["is_public_theme_stock"] = code in frozen_stock_codes
            stock["strict_structural_etf_evidence"] = code in strict_by_code
            stock.setdefault("etf_evidence_rank", len(etf_evidence_stocks) + 1)
            etf_evidence_stocks.append(stock)
        _log(
            f"ETF stock anchors: {len(top_stocks)} frozen public stocks; "
            f"{len(strict_etf_evidence)} strict structural qualifiers"
        )

        # Phase 1B: complete ETF assessment and freeze exact membership. A full
        # live/offline CE expansion runs only when strict discovery is short.
        etf_source_failures: list[str] = []
        if etf_target == 0:
            etf_result = PreselectionResult([], [], 0, 0, [])
            deduped_etfs = []
            top_etfs = []
            _log("etf_target=0; ETF screening disabled")
        else:
            started = time.monotonic()
            _log(f"ETF preselection started (ordinary candidate limit={etf_cap})")
            try:
                etf_result = preselect_etfs(
                    self.quotes,
                    etf_evidence_stocks,
                    theme=inp["theme"],
                    brief=brief,
                    article_title=art.get("title", ""),
                    theme_direction=theme_direction,
                    limit=etf_cap,
                    max_pools=int(self.opts.get("etf_theme_pools", 4)),
                    log=_log,
                )
            except Exception as exc:
                etf_source_failures.append(f"strict_preselection:{exc}")
                _log(f"ETF strict preselection failed ({exc}); recovering from CE universe")
                etf_result = PreselectionResult(
                    candidates=[], pools=[], discovered=0, excluded=0,
                    failures=[str(exc)],
                )
            _log(
                f"ETF preselection finished in {time.monotonic() - started:.1f}s; "
                f"{len(etf_result.candidates)} ranked candidates"
            )
            self._mark_etf_liquidity_diagnostics(etf_result.candidates)
            if etf_result.candidates:
                started = time.monotonic()
                _log("ETF portfolio/component assessment started")
                try:
                    self.rerank_etfs_from_components(
                        etf_result.candidates, brief, art, theme_direction,
                        stock_scores=stock_candidates,
                    )
                except Exception as exc:
                    etf_source_failures.append(f"component_assessment:{exc}")
                    _log(
                        f"ETF component assessment failed ({exc}); "
                        "retaining deterministic preselection facts"
                    )
                _log(
                    "ETF portfolio/component assessment finished in "
                    f"{time.monotonic() - started:.1f}s"
                )

            for candidate in etf_result.candidates:
                candidate["fallback_direction_mismatch"] = bool(
                    theme_direction == "bullish"
                    and self._is_inverse_candidate(candidate)
                )
            deduped_etfs = select_output_etfs(etf_result.candidates, limit=None)
            top_etfs = compose_output_etfs(deduped_etfs, etf_target)
            if len(top_etfs) < etf_target:
                before = len(etf_result.candidates)
                _, recovery_failures = extend_etf_candidates(
                    self.quotes,
                    etf_result.candidates,
                    theme=inp["theme"],
                    brief=brief,
                    target=etf_target,
                    universe_limit=etf_cap,
                    log=_log,
                )
                etf_source_failures.extend(recovery_failures)
                self._mark_etf_liquidity_diagnostics(etf_result.candidates)
                for candidate in etf_result.candidates:
                    candidate["fallback_direction_mismatch"] = bool(
                        theme_direction == "bullish"
                        and self._is_inverse_candidate(candidate)
                    )
                _log(
                    f"ETF reserve expansion appended "
                    f"{len(etf_result.candidates) - before} CE candidates"
                )
                deduped_etfs = select_output_etfs(
                    etf_result.candidates, limit=None)
                top_etfs = compose_output_etfs(deduped_etfs, etf_target)
            if len(top_etfs) != etf_target:
                raise SelectionUniverseError(
                    "etf", etf_target, len(top_etfs),
                    (
                        "strict_theme_discovery", "live_ce", "offline_ce",
                        *etf_source_failures,
                    ),
                )

        dedupe_reasons: dict[str, int] = {}
        for candidate in etf_result.candidates:
            if not candidate.get("dedupe_excluded"):
                continue
            reason = str(candidate.get("dedupe_reason") or "unknown")
            dedupe_reasons[reason] = dedupe_reasons.get(reason, 0) + 1
        _log(
            "ETF deduplication: "
            f"{sum(bool(candidate.get('output_eligible')) for candidate in etf_result.candidates)} "
            f"strict eligible, {len(deduped_etfs)} total admissible unique; "
            + (
                ", ".join(
                    f"{reason}={count}"
                    for reason, count in sorted(dedupe_reasons.items())
                )
                if dedupe_reasons else "no duplicates removed"
            )
        )
        selected_lane_counts: dict[str, int] = {}
        for candidate in top_etfs:
            lane = str(
                candidate.get("selection_lane")
                or candidate.get("etf_lane")
                or "legacy"
            ).strip().lower()
            selected_lane_counts[lane] = selected_lane_counts.get(lane, 0) + 1
        _log(
            "ETF selected lanes: "
            + (
                ", ".join(
                    f"{lane}={count}"
                    for lane, count in sorted(selected_lane_counts.items())
                )
                or "none"
            )
        )
        low_liquidity_selected = [
            candidate["code"] for candidate in top_etfs
            if candidate.get("low_liquidity")
        ]
        if low_liquidity_selected:
            _log(
                "ETFs: soft low-liquidity warning on selected "
                + ", ".join(low_liquidity_selected)
            )
        frozen_etf_codes = tuple(etf["code"] for etf in top_etfs)
        if len(frozen_etf_codes) != etf_target or len(set(frozen_etf_codes)) != etf_target:
            raise SelectionUniverseError(
                "etf", etf_target, len(set(frozen_etf_codes)),
                ("strict_theme_discovery", "live_ce", "offline_ce"),
            )
        self._last_etf_membership_codes = frozen_etf_codes
        for etf in top_etfs:
            if theme_direction == "bearish" and not self._is_inverse_candidate(etf):
                # A conventional long basket is a downside expression in a
                # bearish theme, including CE rows recovered without holdings.
                # This is narration metadata only and cannot change membership.
                etf["bearish_downside_exposure"] = True
            etf["etf_membership_frozen"] = True
        _log(
            f"screening complete: stocks={len(top_stocks)}/{stock_target} "
            f"ETFs={len(top_etfs)}/{etf_target}; "
            f"stock_codes={','.join(frozen_stock_codes) or '-'}; "
            f"etf_codes={','.join(frozen_etf_codes) or '-'}"
        )

        # Market enrichment cannot reopen screening or change the frozen order.
        started = time.monotonic()
        _log("finalist market-data stage started")
        try:
            benchmark_bars = self.quotes.fetch_klines(
                [self.opts["benchmark"]], count=self.opts["kline_count"]
            ).get(self.opts["benchmark"], [])
            benchmark_return = scoring.window_return(benchmark_bars, ev_int)
            self.add_market_features(top_stocks, ev_int, benchmark_return)
            self.add_market_features(top_etfs, ev_int, benchmark_return)
        except Exception as exc:
            _log(f"finalist market-data stage failed ({exc}); continuing without market colour")
        self._mark_volume_confirmed(top_stocks)
        self._mark_volume_confirmed(top_etfs)
        _log(f"market features computed in {time.monotonic() - started:.1f}s")

        # Phase 2: sell the already-selected securities. These calls can only
        # produce code-keyed prose; they have no reserve or selection path.
        started = time.monotonic()
        _log(f"stock narrative stage started ({len(top_stocks)} frozen stocks)")
        top_stocks, stock_narr = self.finalize_stock_rationales(
            inp, brief, top_stocks, stock_target,
        )
        if tuple(stock["code"] for stock in top_stocks) != frozen_stock_codes:
            raise RuntimeError("stock membership changed during narration")
        _log(f"stock narrative stage finished in {time.monotonic() - started:.1f}s")

        started = time.monotonic()
        _log(f"ETF narrative LLM request started ({len(top_etfs)} ETFs)")
        etf_narr = self.narrate(inp, brief, top_etfs, "ETF")
        if tuple(etf["code"] for etf in top_etfs) != frozen_etf_codes:
            raise RuntimeError("ETF membership changed during narration")
        _log(f"ETF narrative stage finished in {time.monotonic() - started:.1f}s")
        started = time.monotonic()
        _log("FAQ LLM request started")
        faq = self.faq(inp, brief, top_stocks, top_etfs)
        _log(f"FAQ stage finished in {time.monotonic() - started:.1f}s")
        _log("narratives + FAQ ready")

        result = {
            "theme_cn": brief["theme_cn"],
            "ThemeStocks": self._assemble(
                top_stocks, stock_narr, inp["date"], theme_direction,
                theme=inp["theme"]),
            "ThemeEtfs": self._assemble(
                top_etfs, etf_narr, inp["date"], theme_direction,
                theme=inp["theme"]),
            "ThemeFAQ": faq,
        }
        if tuple(
            item.get("market_code") for item in result["ThemeStocks"]
        ) != frozen_stock_codes:
            raise RuntimeError("assembled stocks differ from frozen selection")
        if tuple(
            item.get("market_code") for item in result["ThemeEtfs"]
        ) != frozen_etf_codes:
            raise RuntimeError("assembled ETFs differ from frozen selection")
        return result
