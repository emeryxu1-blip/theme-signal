"""Orchestration for the theme-investing agentic workflow.

Pipeline:
  1. validate input + fetch article
  2. freeze a theme-only profile, then analyze the article as secondary evidence
  3. resolve theme/title-lede entities, build a bounded theme-first stock set,
     then LLM-score theme relevance and article support separately
  4. deterministically derive ETFs from theme-stock relations and static pools;
     prioritize verified direction-aligned single-stock wrappers, then baskets
  5. fetch daily k-lines for finalists -> signed facts + |Chg %|/RVOL strength
  6. apply a bounded market-strength uplift and calibrate diverse 1-5 values
  7. LLM theme rationale + SEO FAQ
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import re
import sys
import time
import unicodedata
from collections import deque
from itertools import islice

import prompts
import scoring
from ainvest_client import AInvestClient
from article import fetch_article
from etf_preselection import (compose_output_etfs, preselect_etfs,
                              requires_component_holdings,
                              rerank_with_component_holdings,
                              select_output_etfs)
from llm_client import LLMClient

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
    stock_target=8,           # maximum qualifying stocks to emit
    etf_target=5,             # maximum independently verified ETFs to emit
    relevance_threshold=3.3,  # prompt rubric: <3 is marginal/speculative
    min_relevance_confidence=0.55,
    etf_min_theme_score=0.25, # permit fewer than five weak/unverified funds
    kline_count=90,           # enough daily bars for the PRE-event baseline window
    baseline_lookback=20,     # pre-event bars used to compute the median volume baseline
    min_history=5,            # minimum pre-event bars required before baseline is trusted
    rvol_threshold=1.5,       # event-window peak RVOL that earns a "volume-confirmed" badge
    benchmark="169:SPY",      # market benchmark for abnormal-return calculation
)

THEME_SCORE_WEIGHT = 0.80
ARTICLE_SCORE_WEIGHT = 0.20
ARTICLE_ANCHOR_TEXT_CHARS = 1500
_US_STOCK_MARKETS = {"169", "170", "171", "185", "186"}
_ENTITY_ROLES = {
    "pure_play_operator", "direct_operator", "enabler", "supply_chain",
    "beneficiary",
}


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
    r"valuation uplift|(?:lower|falling) yields? lift|broad financial conditions)\b",
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
    if ticker and re.search(
        rf"(?<![A-Za-z0-9]){re.escape(ticker)}(?![A-Za-z0-9])",
        normalised_region,
        re.IGNORECASE,
    ):
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
        r"\s+(?:group|holdings?|incorporated|inc\.?|corporation|corp\.?|"
        r"company|co\.?|plc|limited|ltd\.?)$",
        "", legal_name, flags=re.IGNORECASE,
    ).strip()
    if stripped and stripped.casefold() != legal_name.casefold():
        variants.append(stripped)
    haystack = re.sub(
        r"[^a-z0-9\u4e00-\u9fff]+", " ", normalised_region.casefold(),
    ).strip()
    for variant in variants:
        needle = re.sub(
            r"[^a-z0-9\u4e00-\u9fff]+", " ", variant.casefold(),
        ).strip()
        if not needle:
            continue
        if re.search(r"[a-z0-9]", needle):
            pattern = re.escape(needle).replace(r"\ ", r"\s+")
            if re.search(rf"(?<![a-z0-9]){pattern}(?![a-z0-9])", haystack):
                return True
        elif len(needle) >= 2 and needle in haystack:
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
    if token in {"building", "builder", "builders"}:
        return "build"
    if token in {"homebuilder", "homebuilders", "homebuilding"}:
        return "homebuilder"
    if token in {"house", "houses", "housing"}:
        return "housing"
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


def _normalise_relevance_object(value: dict | None, *, status: str = "scored") -> dict:
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
    required_schema_valid = bool(
        raw_exposure in _EXPOSURE_TYPES
        and str(value.get("impact_channel") or "").strip().lower() in _IMPACT_CHANNELS
        and str(value.get("theme_specificity") or "").strip().lower()
        in _THEME_SPECIFICITIES
        and str(value.get("materiality") or "").strip().lower() in _MATERIALITIES
        and str(value.get("evidence_strength") or "").strip().lower()
        in _EVIDENCE_STRENGTHS
    )
    if status == "scored" and not required_schema_valid:
        status = "invalid_schema"
    theme_relevance = _clamp_float(
        value.get("theme_relevance", value.get("ai_relevance")),
        1.0, 5.0, default=1.0,
    )
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
        "relevance_status": status,
    }


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
            url=inp["url"], lede=lede, excerpt=art.get("text", "")[:6000],
        )
        brief = self.llm.chat_json(
            prompts.EVENT_BRIEF_SYS, user, max_tokens=6000,
        )
        if not isinstance(brief, dict):
            brief = {}
        theme_cn = brief.get("theme_cn")
        frozen_theme_cn = _normalised_text(profile.get("theme_cn"))
        brief["theme_cn"] = (
            frozen_theme_cn
            or (theme_cn.strip() if isinstance(theme_cn, str) else "")
        )
        brief["theme_direction"] = _norm_theme_direction(brief.get("theme_direction"))
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

    # -- stage 3 -------------------------------------------------------------
    def _pool_rows(self, pool, indicator_id, req_id, sort_pos):
        """Lazily yield named rows from a ranked API pool (market cap / AUM order)."""
        for r in self.quotes.iter_ranked(
                pool, [{"id": indicator_id, "req_unique_id": req_id}], sort_pos=sort_pos):
            r["name"] = self.quotes.name_of(r["code"])
            r["metric"] = r["values"].get(req_id)
            yield r

    def stock_source(self):
        """Yield a liquidity-bounded universe with facts used to reduce fame bias."""
        indicators = [
            {"id": MKTCAP_ID, "req_unique_id": "mktcap"},
            {"id": "55", "req_unique_id": "name"},
            {"id": "company_introduction", "req_unique_id": "company_introduction"},
            {"id": "ext_metric_sector_1_name", "req_unique_id": "sector"},
            {"id": "ext_metric_sector_3_name", "req_unique_id": "industry"},
        ]
        for row in self.quotes.iter_ranked(STOCK_POOL, indicators, sort_pos=0):
            values = row.get("values") or {}
            row.update({
                "name": values.get("name") or self.quotes.name_of(row["code"]),
                "metric": values.get("mktcap"),
                "company_introduction": values.get("company_introduction") or "",
                "sector": values.get("sector") or "",
                "industry": values.get("industry") or "",
            })
            yield row

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
    ) -> list[dict]:
        """Build deterministic entity, broad-liquidity, and theme-only lanes."""
        universe_cap = int(self.opts["stock_universe"])
        if universe_cap <= 0:
            raise ValueError("stock_universe must be greater than 0")
        safety_cap = self.opts.get("max_scan")
        if safety_cap:
            universe_cap = min(universe_cap, int(safety_cap))
        universe = list(islice(self.stock_source(), universe_cap))
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
        resolved_theme = self._resolve_entity_hints(theme_hints)
        resolved_article = self._resolve_entity_hints(article_hints)

        resolved_codes = {
            entity["resolved_code"] for entity in [*resolved_theme, *resolved_article]
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
            if provenance == "title_lede":
                existing["guaranteed_article_anchor"] = True
                existing["article_anchor_evidence"] = (
                    (entity or {}).get("operating_evidence")
                    or "explicit title/lede mention"
                )

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
            candidate.setdefault("name", self.quotes.name_of(code))
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
            if provenance == "title_lede":
                candidate["guaranteed_article_anchor"] = True
                candidate["article_anchor_evidence"] = (
                    (entity or {}).get("operating_evidence") or "explicit title/lede mention"
                )
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
                f"article-only anchors, {broad_count} broad, {theme_count} theme-taxonomy; "
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
        """Score the fixed universe, rank on causal evidence, then take ``target``.

        Market cap may define the liquid universe and break an exact semantic tie;
        it never decides membership by letting an early passing row occupy a slot.
        Public stock output is completed from the best available scored candidates when
        the strict evidence gate yields fewer than ``target`` names. The strict gate
        remains authoritative for ETF evidence; public fallback names are disclosed as
        evidence-limited and never become ETF evidence.
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
        candidates = list(rows)
        self._ensure_candidate_ids(candidates)
        scores = self.score_relevance(brief, candidates, article)
        qualified: list[dict] = []
        rejection_counts: dict[str, int] = {}
        min_confidence = float(self.opts["min_relevance_confidence"])
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
            candidate["selection_score"] = _selection_score(candidate)
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
            _log(
                "stock diagnostic "
                f"id={candidate.get('candidate_id')} code={candidate.get('code')} "
                f"lane={candidate.get('candidate_lane') or 'unspecified'} "
                f"provenance={','.join(candidate.get('candidate_provenance') or []) or '-'} "
                f"resolution={candidate.get('resolution_match_kind') or '-'} "
                f"retry={candidate.get('relevance_retry') or '-'} "
                f"theme={float(candidate.get('theme_relevance') or 0):.2f} "
                f"article={float(candidate.get('article_support') or 0):.2f} "
                f"semantic={candidate['semantic_score']:.4f} "
                f"selection={candidate['selection_score']:.4f} "
                f"status={candidate.get('relevance_status')} "
                f"decision={candidate.get('rejection_reason')}"
            )
        scanned = len(candidates)
        _log(f"{kind}: scored {scanned} by {rank_label}; "
             f"{len(qualified)} pass the theme-only causal-evidence gate")
        if rejection_counts:
            _log("stock rejection reasons: " + "; ".join(
                f"{reason}={count}" for reason, count in sorted(rejection_counts.items())))

        exposure_priority = {
            "direct": 0, "enabler": 1, "supply_chain": 2,
            "beneficiary": 3, "diversified": 4, "factor_proxy": 5,
            "unclear": 6,
        }
        rank_key = lambda candidate: (
            -candidate["selection_score"],
            -candidate["semantic_score"],
            exposure_priority.get(candidate.get("exposure_type"), 9),
            -float(candidate.get("theme_relevance") or 0.0),
            -float(candidate.get("confidence") or 0.0),
            int(candidate.get("rank") or 10**9),
            candidate["code"],
        )
        qualified.sort(key=rank_key)

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

        anchors = dedupe([
            candidate for candidate in candidates
            if candidate.get("guaranteed_article_anchor")
        ])
        anchor_codes = {candidate["code"] for candidate in anchors}
        if len(anchors) > target:
            public_selected = sorted(anchors, key=rank_key)[:target]
            _log(
                f"stocks: {len(anchors)} validated title/lede anchors exceed {target}; "
                f"keeping the {target} highest 80/20 scores"
            )
        else:
            eligible_anchors = sorted(
                [candidate for candidate in anchors if candidate.get("theme_eligible")],
                key=rank_key,
            )
            weak_anchors = sorted(
                [candidate for candidate in anchors if not candidate.get("theme_eligible")],
                key=rank_key,
            )
            filler_limit = max(0, target - len(anchors))
            anchor_issuers = {
                _issuer_key(candidate) or candidate["code"].casefold()
                for candidate in anchors
            }
            fillers = dedupe([
                candidate for candidate in qualified
                if candidate["code"] not in anchor_codes
                and (_issuer_key(candidate) or candidate["code"].casefold())
                not in anchor_issuers
            ], filler_limit)
            # Strong anchors and ordinary qualifiers compete by the 80/20 score;
            # guaranteed weak anchors remain visible but always sit at the bottom.
            public_selected = sorted([*eligible_anchors, *fillers], key=rank_key)
            public_selected.extend(weak_anchors)

        # A weak guaranteed mention is inclusion-only evidence. Keep its public
        # rationale and displayed exposure conservative rather than presenting an
        # article mention as structural theme exposure.
        weak_anchors: list[dict] = []
        strong_public: list[dict] = []
        for candidate in public_selected:
            if (candidate.get("guaranteed_article_anchor")
                    and not candidate.get("theme_eligible")):
                candidate["weak_guaranteed_anchor"] = True
                candidate.update({
                    "reason": "",
                    "exposure_type": "unclear",
                    "theme_specificity": "none",
                    "materiality": "unknown",
                    "evidence_strength": "none",
                    "impact_channel": "none",
                    "semantic_score": 0.0,
                })
                weak_anchors.append(candidate)
            else:
                strong_public.append(candidate)

        # The public stock contract is a best-available list, not an evidence
        # threshold. Exact event labels can have no qualifying names (for example
        # a Bitcoin move), but the reader still needs the closest listed equities.
        # Fill only after every candidate has competed under the strict gate, and
        # keep this fallback completely separate from ETF evidence below.
        if (
            str(kind).strip().lower() in {"stock", "stocks"}
            and len(strong_public) + len(weak_anchors) < target
        ):
            public_codes = {candidate["code"] for candidate in public_selected}
            structural_types = {"direct", "enabler", "supply_chain", "beneficiary"}
            fallback_candidates = [
                candidate for candidate in candidates
                if candidate["code"] not in public_codes
            ]

            def fallback_key(candidate: dict):
                status = candidate.get("relevance_status")
                structural = candidate.get("exposure_type") in structural_types
                # Prefer scored structural names, then other scored names, and
                # finally unresolved rows whose evidence is unavailable.
                tier = (
                    0 if status == "scored" and structural else
                    1 if status == "scored" else 2
                )
                return (
                    tier,
                    -float(candidate.get("theme_relevance") or 0.0),
                    -float(candidate.get("confidence") or 0.0),
                    exposure_priority.get(candidate.get("exposure_type"), 9),
                    -float(candidate.get("article_support") or 0.0),
                    int(candidate.get("rank") or 10**9),
                    candidate["code"],
                )

            fallback_candidates.sort(key=fallback_key)
            fallback_limit = max(0, target - len(strong_public) - len(weak_anchors))
            fallbacks = dedupe(fallback_candidates, fallback_limit)
            for candidate in fallbacks:
                candidate["weak_theme_fallback"] = True
                candidate["fallback_tier"] = (
                    "scored_structural"
                    if candidate.get("relevance_status") == "scored"
                    and candidate.get("exposure_type") in structural_types
                    else "scored_or_unresolved"
                )
                if candidate.get("rejection_reason") in (None, "eligible"):
                    candidate["rejection_reason"] = "public_fallback"
            strong_public.extend(fallbacks)
            if fallbacks:
                _log(
                    f"stocks: filled {len(fallbacks)} public slots with best-available "
                    "fallback candidates; they remain excluded from ETF evidence"
                )

        public_selected = [*strong_public, *weak_anchors]

        evidence_limit = target
        if str(kind).strip().lower() in {"stock", "stocks"}:
            evidence_limit = max(
                target, int(self.opts.get("etf_evidence_stock_limit", 30)))
        evidence_candidates = [
            candidate for candidate in public_selected
            if candidate.get("theme_eligible")
            and not candidate.get("weak_theme_fallback")
            and not candidate.get("weak_guaranteed_anchor")
        ]
        evidence_codes = {candidate["code"] for candidate in evidence_candidates}
        evidence_candidates.extend(
            candidate for candidate in qualified
            if candidate["code"] not in evidence_codes)
        evidence_selected = dedupe(evidence_candidates, evidence_limit)

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
        _log(f"{kind}: {boundary}; {len(qualified)} qualify and {len(anchors)} "
             f"title/lede anchors are guaranteed; emitting {len(public_selected)}/{target} "
             "after 80/20 ranking and issuer dedupe"
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
            "excerpt": article.get("text", "")[:6000],
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
        return self._join_relevance_rows(batch, arr, ticker_scope_counts), raw

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
                scores[r["code"]] = _normalise_relevance_object(o)
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
                retried = _normalise_relevance_object(obj) if obj is not None else dict(previous)
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
        if chosen and not is_etf_output:
            # Membership was fixed before market data was fetched. Re-establish
            # only the semantic 80/20 order here; market strength never participates.
            chosen.sort(key=lambda candidate: (
                bool(candidate.get("weak_guaranteed_anchor")),
                -_clamp_float(
                    candidate.get("selection_score"), 0.0, 1.0,
                    default=_selection_score(candidate),
                ),
                -_clamp_float(
                    candidate.get("semantic_score"), 0.0, 1.0,
                    default=_semantic_score(candidate),
                ),
                str(candidate.get("code") or ""),
            ))
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
                    c.get("semantic_score"), 0.0, 1.0,
                    default=_semantic_score(c),
                )
                c["theme_exposure_raw"] = min(
                    1.0, semantic * (1.0 + 0.10 * market_strength))
        # ETF output order is already owned by the approved unified evidence /
        # investability score and economic-exposure dedupe. Calibrate its public
        # exposure labels on a sorted copy, then map them back without reordering
        # the selected funds. Stock output retains its existing exposure ordering.
        if not is_etf_output:
            # Uplifted values are capped monotonically in the already-approved
            # semantic order so market confirmation can alter the label without
            # inverting the public ranking.
            display_inputs: list[float] = []
            ceiling = 1.0
            for candidate in chosen:
                ceiling = min(ceiling, candidate["theme_exposure_raw"])
                display_inputs.append(ceiling)
            display = scoring.calibrate_scores(display_inputs)
            for c, score in zip(chosen, display):
                c["theme_exposure"] = score
                c["score"] = score
            return

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
    def _validated_candidate_rationale(cls, candidate: dict, value) -> dict | None:
        """Validate public prose, including derivative and liquidity risk facts."""
        rationale = cls._validated_theme_rationale(value)
        if rationale is None:
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
        cls, candidate: dict, theme_direction: str = "bullish"
    ) -> dict:
        """Build bilingual, factual prose when LLM output is absent or unsafe."""
        name = str(candidate.get("name") or candidate.get("code") or "This security").strip()
        direction_mode = _norm_theme_direction(theme_direction)
        is_etf = candidate.get("static_theme_exposure") is not None
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
                except (TypeError, ValueError):
                    leverage = 1.0
                leverage = max(1.0, min(3.0, leverage))
                multiple = f"{leverage:g}"
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
                except (TypeError, ValueError):
                    leverage = 1.0
                leverage = max(1.0, min(3.0, leverage))
                multiple = f"{leverage:g}"
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
                if reference_values:
                    reference = cls._human_join(reference_values)
                    reference_zh = cls._human_join_zh(reference_values)
                    objective_en = (
                        f"{name} seeks approximately {multiple} times the positive daily return "
                        f"of {reference}."
                    )
                    objective_zh = (
                        f"{name}力求实现{reference_zh}单日收益的约{multiple}倍正向表现。"
                    )
                else:
                    objective_en = (
                        f"{name} seeks approximately {multiple} times positive daily performance, "
                        "but the available fund facts do not name its reference benchmark or underlying."
                    )
                    objective_zh = (
                        f"{name}力求实现约{multiple}倍单日正向表现，但现有基金资料未列明其"
                        "参考基准或标的。"
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
                return etf_multilingual(
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
                return etf_multilingual(
                    en=(
                        f"Available fund data does not establish concentrated exposure for {name}. "
                        "Its downside sensitivity depends on the current mandate "
                        "and holdings, which remain the principal limitation in the available data."
                    ),
                    zh=(
                        f"现有基金资料无法确认{name}是否具有集中敞口。其下行敏感度取决于"
                        "最新投资范围与持仓，而现有资料在这两方面仍有限。"
                    ),
                )
            return etf_multilingual(
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

    def _assemble(self, chosen, narr, event_date, theme_direction="bullish"):
        self._assign_theme_exposure(chosen)
        items = []
        for c in chosen:
            n = narr.get(c["code"], {})
            rationale = None if (
                c.get("weak_guaranteed_anchor") or c.get("weak_theme_fallback")
            ) else (
                self._validated_candidate_rationale(
                    c, n.get("theme_rationale"))
            )
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
            raw = self.llm.chat_json(
                prompts.NARRATIVE_SYS, user, max_tokens=4000,
            )
        except (ValueError, RuntimeError):
            raw = []
        arr = raw.get("items", []) if isinstance(raw, dict) else raw
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
            raw = self.llm.chat_json(
                prompts.FAQ_SYS, user, max_tokens=3000,
            )
        except (ValueError, RuntimeError):
            raw = []
        arr = raw.get("items", []) if isinstance(raw, dict) else raw
        if not isinstance(arr, list):
            arr = []
        out = [{"question": o.get("question", ""), "answer": o.get("answer", "")}
               for o in arr if isinstance(o, dict) and o.get("question")]
        return out[:8]

    # -- driver --------------------------------------------------------------
    def run(self, payload: dict) -> dict:
        # A workflow instance may be reused by tests or callers. Never carry ETF
        # anchor evidence from a previous theme into the next run.
        self._last_stock_evidence = []
        inp = validate_input(payload)
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

        # Market cap bounds the cheap quote universe only. A broad lane and
        # theme-industry lanes form a fixed semantic work set; every row in that
        # set competes before output slots are assigned.
        stock_candidates = self.stock_candidates(brief, profile, art)
        top_stocks, etf_evidence_stocks = self.screen_stock_sets(
            stock_candidates,
            brief,
            art,
            self.opts["stock_target"],
            rank_label="candidate lane",
        )
        # Weak article-guaranteed stocks are public inclusion exceptions, not
        # causal evidence for admitting related ETFs.
        etf_evidence_stocks = list(etf_evidence_stocks)
        public_stock_codes = {stock["code"] for stock in top_stocks}
        # Public picks remain first so single-stock wrapper discovery respects the
        # visible stock ranking; additional qualified names only widen ETF evidence.
        etf_evidence_stocks.sort(key=lambda stock: (
            0 if stock["code"] in public_stock_codes else 1,
            int(stock.get("etf_evidence_rank") or 10**9),
            stock["code"],
        ))
        _log(
            f"ETF stock evidence: {sum(stock['code'] in public_stock_codes for stock in etf_evidence_stocks)} "
            f"eligible public anchors + {sum(stock['code'] not in public_stock_codes for stock in etf_evidence_stocks)} "
            "internal qualifiers"
        )

        # ETF facts that are universal (fund holdings, leverage/direction, source
        # membership, deduplication, and ranking) are implemented deterministically.
        # max_scan remains an optional lower safety ceiling for both asset classes.
        etf_cap = int(self.opts["etf_universe"])
        safety_cap = self.opts.get("max_scan")
        if safety_cap:
            etf_cap = min(etf_cap, int(safety_cap))
        started = time.monotonic()
        _log(f"ETF preselection started (ordinary candidate limit={etf_cap})")
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
        _log(
            f"ETF preselection finished in {time.monotonic() - started:.1f}s; "
            f"{len(etf_result.candidates)} ranked candidates"
        )
        self._mark_etf_liquidity_diagnostics(etf_result.candidates)
        started = time.monotonic()
        _log("ETF portfolio/component assessment started")
        self.rerank_etfs_from_components(
            etf_result.candidates, brief, art, theme_direction,
            stock_scores=stock_candidates,
        )
        _log(
            "ETF portfolio/component assessment finished in "
            f"{time.monotonic() - started:.1f}s"
        )
        lane_counts: dict[str, int] = {}
        eligible_lane_counts: dict[str, int] = {}
        for candidate in etf_result.candidates:
            lane = str(
                candidate.get("selection_lane")
                or candidate.get("etf_lane")
                or "legacy"
            ).strip().lower()
            lane_counts[lane] = lane_counts.get(lane, 0) + 1
            if candidate.get("output_eligible"):
                eligible_lane_counts[lane] = eligible_lane_counts.get(lane, 0) + 1
        _log(
            "ETF candidate lanes: "
            + (
                ", ".join(
                    f"{lane}={count} ({eligible_lane_counts.get(lane, 0)} eligible)"
                    for lane, count in sorted(lane_counts.items())
                )
                or "none"
            )
        )
        etf_target = int(self.opts["etf_target"])
        if etf_target < 0:
            raise ValueError("ETF output target cannot be negative")
        # Run deduplication across the full eligible set so diagnostics account
        # for every economic duplicate, then apply the public output boundary.
        deduped_etfs = select_output_etfs(etf_result.candidates, limit=None)
        top_etfs = compose_output_etfs(deduped_etfs, etf_target)
        dedupe_reasons: dict[str, int] = {}
        for candidate in etf_result.candidates:
            if not candidate.get("dedupe_excluded"):
                continue
            reason = str(candidate.get("dedupe_reason") or "unknown")
            dedupe_reasons[reason] = dedupe_reasons.get(reason, 0) + 1
        _log(
            "ETF deduplication: "
            f"{sum(bool(candidate.get('output_eligible')) for candidate in etf_result.candidates)} "
            f"eligible -> {len(deduped_etfs)} unique; "
            + (
                ", ".join(
                    f"{reason}={count}"
                    for reason, count in sorted(dedupe_reasons.items())
                )
                if dedupe_reasons else "no duplicates removed"
            )
        )
        if len(deduped_etfs) > etf_target:
            _log(
                f"ETF output boundary: composed {etf_target} of "
                f"{len(deduped_etfs)} deduplicated eligible products with "
                "leveraged-first and basket-reservation policy"
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
        if len(top_etfs) < etf_target:
            _log(f"ETFs: only {len(top_etfs)}/{self.opts['etf_target']} candidates "
                 "survive verified-evidence gates and economic-exposure dedupe")
        _log(f"selected {len(top_stocks)} stocks and {len(top_etfs)} ETFs")

        # Fetch SPY once to remove broad-market drift, then compute market features
        # for the finalists only. These remain internal scoring inputs and are not
        # passed to the narrative stage.
        started = time.monotonic()
        _log("finalist market-data stage started")
        benchmark_bars = self.quotes.fetch_klines(
            [self.opts["benchmark"]], count=self.opts["kline_count"]
        ).get(self.opts["benchmark"], [])
        benchmark_return = scoring.window_return(benchmark_bars, ev_int)
        self.add_market_features(top_stocks, ev_int, benchmark_return)
        self.add_market_features(top_etfs, ev_int, benchmark_return)
        self._mark_volume_confirmed(top_stocks)
        self._mark_volume_confirmed(top_etfs)
        _log(f"market features computed in {time.monotonic() - started:.1f}s")

        started = time.monotonic()
        _log(f"stock narrative LLM request started ({len(top_stocks)} stocks)")
        stock_narr = self.narrate(inp, brief, top_stocks, "stock")
        _log(f"stock narrative stage finished in {time.monotonic() - started:.1f}s")
        started = time.monotonic()
        _log(f"ETF narrative LLM request started ({len(top_etfs)} ETFs)")
        etf_narr = self.narrate(inp, brief, top_etfs, "ETF")
        _log(f"ETF narrative stage finished in {time.monotonic() - started:.1f}s")
        started = time.monotonic()
        _log("FAQ LLM request started")
        faq = self.faq(inp, brief, top_stocks, top_etfs)
        _log(f"FAQ stage finished in {time.monotonic() - started:.1f}s")
        _log("narratives + FAQ ready")

        return {
            "theme_cn": brief["theme_cn"],
            "ThemeStocks": self._assemble(
                top_stocks, stock_narr, inp["date"], theme_direction),
            "ThemeEtfs": self._assemble(
                top_etfs, etf_narr, inp["date"], theme_direction),
            "ThemeFAQ": faq,
        }
