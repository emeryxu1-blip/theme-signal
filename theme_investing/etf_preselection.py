"""Deterministic ETF candidate generation and ranking.

The ETF universe is theme-derived rather than globally ranked by AUM:

* theme stocks expand to related ETFs, including derivatives without physical weight;
* a static registry adds curated AInvest pools, including a bearish inverse source;
* factual direction filters and holdings arithmetic run in code;
* AUM is only the last investability tie-breaker.

All source iterators and the final unique-candidate set are bounded by ``limit``.
The same algorithm therefore applies to ``--limit 100`` and ``--limit 1000``.
"""

from __future__ import annotations

import math
import re
from collections import deque
from dataclasses import dataclass
from itertools import islice
from typing import Callable, Iterable


@dataclass(frozen=True)
class PoolSpec:
    key: str
    label: str
    prompt_id: str
    aliases: tuple[str, ...]


@dataclass(frozen=True)
class PoolMatch:
    spec: PoolSpec
    strength: float
    source: str
    alias: str


@dataclass
class PreselectionResult:
    candidates: list[dict]
    pools: list[PoolMatch]
    discovered: int
    excluded: int
    failures: list[str]


# Stable product prompt pools documented in
# Skills/ainvest-openapi-quote/references/legacy/id_dict.md.  Ambiguous one-word
# aliases are intentionally avoided where producer equities and direct-asset funds
# would otherwise be conflated.
INVERSE_SP500_POOL = PoolSpec(
    "inverse_sp500",
    "Inverse S&P 500 ETFs",
    "6908b49e8738843bb3ba8668",
    (),
)


THEME_POOLS: tuple[PoolSpec, ...] = (
    PoolSpec("semiconductors", "Semiconductor ETFs", "6908afc3069a48065f159368",
             ("semiconductor", "semiconductors", "chipmaker", "chipmakers", "memory chip",
              "memory chips", "ai memory", "high bandwidth memory", "hbm", "dram", "nand")),
    PoolSpec("artificial_intelligence", "Artificial Intelligence ETFs", "6908b19b8738843bb3ba8657",
             ("ai", "artificial intelligence", "machine learning", "generative ai",
              "large language model", "large language models")),
    PoolSpec("ai_robotics", "Artificial Intelligence & Robotics ETFs", "6908b1dd069a48065f159378",
             ("ai robotics", "ai and robotics", "artificial intelligence robotics",
              "artificial intelligence and robotics")),
    PoolSpec("robotics", "Robotics & Automation ETFs", "6908b1ec069a48065f159379",
             ("robotics", "industrial automation", "factory automation", "automation")),
    PoolSpec("cybersecurity", "Cybersecurity ETFs", "6908b1b28738843bb3ba8658",
             ("cybersecurity", "cyber security", "information security")),
    PoolSpec("software", "Software ETFs", "6908b043069a48065f15936a",
             ("software", "saas", "cloud software")),
    PoolSpec("internet_infrastructure", "Internet Infrastructure ETFs", "6908b01e8738843bb3ba8652",
             ("internet infrastructure", "cloud infrastructure", "data center", "data centers")),
    PoolSpec("technology", "Technology Sector ETFs", "6908ae7c8738843bb3ba8649",
             ("technology sector", "big tech", "technology stocks")),
    PoolSpec("digital_economy", "Digital Economy ETFs", "6908b249069a48065f15937c",
             ("digital economy", "digital transformation")),
    PoolSpec("fintech", "FinTech ETFs", "6908b26a069a48065f15937d",
             ("fintech", "financial technology", "digital payments", "mobile payments")),
    PoolSpec("blockchain", "Blockchain Infrastructure ETFs", "6908b4da069a48065f159389",
             ("blockchain infrastructure", "crypto miners", "bitcoin miners", "crypto exchanges")),
    PoolSpec("ev_supply_chain", "EV Supply-Chain ETFs", "6908b1fc069a48065f15937a",
             ("electric vehicle", "electric vehicles", "ev supply chain", "battery technology",
              "lithium battery", "lithium batteries")),
    PoolSpec("clean_energy", "Clean Energy & Solar ETFs", "6908b21e8738843bb3ba8659",
             ("clean energy", "renewable energy", "solar energy", "wind energy", "hydrogen energy")),
    PoolSpec("nuclear", "Nuclear Energy ETFs", "6908b27c8738843bb3ba865c",
             ("nuclear energy", "nuclear power", "uranium")),
    PoolSpec("water", "Water Resources ETFs", "6908b2b8069a48065f15937f",
             ("water infrastructure", "water resources", "water utilities", "water treatment")),
    PoolSpec("infrastructure", "Infrastructure & Construction ETFs", "6908b290069a48065f15937e",
             ("infrastructure spending", "infrastructure construction", "public infrastructure")),
    PoolSpec("defense", "Aerospace & Defense ETFs", "6908b08c069a48065f15936d",
             ("aerospace and defense", "aerospace defense", "defense spending", "defence spending")),
    PoolSpec("space", "Space Exploration ETFs", "6908b1ca069a48065f159377",
             ("space exploration", "space economy", "satellite industry", "rocket launch")),
    PoolSpec("biotech", "Biotech ETFs", "6908afef069a48065f159369",
             ("biotech", "biotechnology", "gene therapy", "genomics")),
    PoolSpec("pharma", "Pharmaceutical ETFs", "6908b189069a48065f159376",
             ("pharmaceutical", "pharmaceuticals", "drugmakers", "drug makers")),
    PoolSpec("medical_devices", "Health-Care Equipment ETFs", "6908b12f069a48065f159374",
             ("medical device", "medical devices", "health care equipment", "healthcare equipment")),
    PoolSpec("healthcare", "Healthcare Innovator ETFs", "6908ae92069a48065f159364",
             ("healthcare innovation", "health care innovation")),
    PoolSpec("regional_banks", "Regional Bank ETFs", "6908b063069a48065f15936b",
             ("regional bank", "regional banks", "community banks")),
    PoolSpec("banks", "Bank ETFs", "6908afda8738843bb3ba8650",
             ("banking sector", "bank stocks", "banks")),
    PoolSpec("financial_services", "Financial Services ETFs", "6908b0bb069a48065f15936f",
             ("financial services", "asset managers", "stock exchanges")),
    PoolSpec("insurance", "Insurance ETFs", "6908b109069a48065f159372",
             ("insurance sector", "insurers", "insurance companies")),
    PoolSpec("real_estate", "Real-Estate ETFs", "6908aea6069a48065f159365",
             ("real estate", "reit", "reits")),
    PoolSpec("ecommerce", "Internet & E-Commerce ETFs", "6908b20d069a48065f15937b",
             ("ecommerce", "e commerce", "online retail", "digital marketplace")),
    PoolSpec("retail", "Retail ETFs", "6908b0308738843bb3ba8653",
             ("retail sector", "retailers", "retail stocks")),
    PoolSpec("transportation", "Transportation ETFs", "6908b0ce8738843bb3ba8654",
             ("transportation sector", "freight", "railroads", "shipping industry")),
    PoolSpec("airlines", "Airline ETFs", "6908b158069a48065f159375",
             ("airline", "airlines", "air travel")),
    PoolSpec("media", "Media ETFs", "6908b0e1069a48065f159370",
             ("media sector", "streaming media", "broadcasting")),
    PoolSpec("gaming", "Video Games & ESports ETFs", "6908b2a08738843bb3ba865d",
             ("video game", "video games", "esports", "e sports", "gaming industry")),
    PoolSpec("gold_miners", "Gold Miner ETFs", "6908b0048738843bb3ba8651",
             ("gold miner", "gold miners", "gold mining")),
    PoolSpec("silver_miners", "Silver Miner ETFs", "6908b1748738843bb3ba8656",
             ("silver miner", "silver miners", "silver mining")),
    PoolSpec("metals_miners", "Metals & Mining ETFs", "6908b077069a48065f15936c",
             ("metals and mining", "metal miners", "mining equities")),
    PoolSpec("oil_producers", "Oil & Gas Producer ETFs", "6908b0a0069a48065f15936e",
             ("oil exploration", "gas exploration", "oil producers", "oil drillers", "upstream oil")),
    PoolSpec("natural_resources", "Natural Resource Equity ETFs", "6908b2338738843bb3ba865a",
             ("natural resource stocks", "natural resource companies")),
    PoolSpec("physical_gold", "Physical Gold ETFs", "6908b312069a48065f159381",
             ("physical gold", "gold bullion")),
    PoolSpec("gold", "Gold ETFs", "6908b3d38738843bb3ba8662",
             ("gold price", "gold prices", "gold etf", "gold")),
    PoolSpec("silver", "Silver ETFs", "6908b3fd069a48065f159384",
             ("silver price", "silver prices", "silver etf", "silver")),
    PoolSpec("crude_oil", "Crude Oil ETFs", "6908b3ed069a48065f159383",
             ("crude oil", "oil price", "oil prices", "wti", "brent crude")),
    PoolSpec("energy_commodities", "Energy Commodity ETFs", "6908b3b38738843bb3ba8661",
             ("energy commodity", "energy commodities", "natural gas price", "natural gas prices")),
    PoolSpec("broad_commodities", "Broad Commodity ETFs", "6908b3258738843bb3ba8660",
             ("broad commodities", "commodity basket", "commodities basket")),
    PoolSpec("agriculture", "Agriculture Commodity ETFs", "6908b3a0069a48065f159382",
             ("agricultural commodities", "agriculture commodities", "corn price", "wheat price",
              "soybean price", "crop prices")),
    PoolSpec("precious_metals", "Precious Metals ETFs", "6908b40e8738843bb3ba8663",
             ("precious metals", "platinum", "palladium")),
    PoolSpec("industrial_metals", "Industrial Metals ETFs", "6908b420069a48065f159385",
             ("industrial metals", "base metals", "copper price", "aluminum price", "nickel price")),
    PoolSpec("short_treasury", "Short-Duration Treasury ETFs", "6908b4328738843bb3ba8664",
             ("short term treasury", "short duration treasury", "treasury bills", "t bills")),
    PoolSpec("investment_grade_bonds", "Investment-Grade Corporate Bond ETFs", "6908b4428738843bb3ba8665",
             ("investment grade bond", "investment grade bonds", "corporate bonds")),
    PoolSpec("high_yield_bonds", "High-Yield Bond ETFs", "6908b4558738843bb3ba8666",
             ("high yield bond", "high yield bonds", "junk bond", "junk bonds")),
    PoolSpec("municipal_bonds", "Municipal Bond ETFs", "6908b468069a48065f159386",
             ("municipal bond", "municipal bonds", "muni bond", "muni bonds")),
    PoolSpec("inflation_bonds", "Inflation-Linked Bond ETFs", "6908b47a8738843bb3ba8667",
             ("inflation linked bond", "inflation linked bonds", "inflation protected bond", "tips bonds")),
    PoolSpec("fixed_income", "Broad Fixed-Income ETFs", "67c181f75517966594fb947a",
             ("fixed income", "bond market", "bond etf", "bond etfs")),
    PoolSpec("bitcoin", "Bitcoin ETFs", "67c182365517966594fb947b",
             ("bitcoin", "btc", "spot bitcoin")),
    PoolSpec("ethereum", "Ethereum ETFs", "6908b4e98738843bb3ba8669",
             ("ethereum", "ether", "eth etf", "spot eth")),
    PoolSpec("high_dividend", "High-Dividend ETFs", "6908ae268738843bb3ba8648",
             ("high dividend", "dividend yield", "income stocks")),
    PoolSpec("covered_call", "Covered-Call ETFs", "6908ae62069a48065f159363",
             ("covered call", "option income", "options income")),
    PoolSpec("low_volatility", "Low-Volatility ETFs", "6908b2e68738843bb3ba865f",
             ("low volatility", "minimum volatility", "defensive factor")),
    PoolSpec("momentum", "Momentum-Factor ETFs", "6908b2fd069a48065f159380",
             ("momentum factor", "price momentum", "momentum stocks")),
    PoolSpec("low_carbon", "Low-Carbon ESG ETFs", "6908b4b9069a48065f159388",
             ("low carbon", "decarbonization", "decarbonisation", "climate transition")),
)


_EXPOSURE_FACTOR = {
    "direct": 1.0,
    "supply_chain": 0.9,
    "enabler": 0.85,
    "beneficiary": 0.8,
    "diversified": 0.6,
    "unclear": 0.35,
}

_DIRECT_ASSET_POOL_KEYS = {
    "physical_gold", "gold", "silver", "crude_oil", "energy_commodities",
    "broad_commodities", "agriculture", "precious_metals", "industrial_metals",
    "short_treasury", "investment_grade_bonds", "high_yield_bonds",
    "municipal_bonds", "inflation_bonds", "fixed_income", "bitcoin", "ethereum",
}


def _normalise(value: object) -> str:
    text = str(value or "").lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def _contains_phrase(text: str, phrase: str) -> bool:
    phrase = _normalise(phrase)
    return bool(phrase) and f" {phrase} " in f" {text} "


def _brief_text(brief: dict) -> str:
    # false_positives is deliberately excluded: a rejected theme term must never
    # become a deterministic routing signal.
    fields = (
        "summary", "thesis", "direct_beneficiaries", "picks_and_shovels",
        "second_order", "keywords",
    )
    values: list[str] = []
    for field in fields:
        value = brief.get(field)
        if isinstance(value, list):
            values.extend(str(item) for item in value)
        elif value:
            values.append(str(value))
    return " ".join(values)


def match_theme_pools(
    theme: str,
    brief: dict | None = None,
    article_title: str = "",
    *,
    max_pools: int = 4,
    theme_direction: str = "bullish",
) -> list[PoolMatch]:
    """Map text to curated pools with exact phrase matching, without an LLM."""
    direction = _theme_direction(theme_direction)
    pool_limit = max(0, max_pools)
    theme_pool_limit = pool_limit - 1 if direction == "bearish" and pool_limit else pool_limit
    sources = (
        ("theme", _normalise(theme), 3.0),
        ("article", _normalise(article_title), 2.0),
        ("brief", _normalise(_brief_text(brief or {})), 1.0),
    )
    matches: list[PoolMatch] = []
    for spec in THEME_POOLS:
        best: PoolMatch | None = None
        for source, text, base in sources:
            for alias in spec.aliases:
                if not _contains_phrase(text, alias):
                    continue
                specificity = min(len(_normalise(alias).split()) * 0.05, 0.25)
                candidate = PoolMatch(spec, base + specificity, source, alias)
                if best is None or candidate.strength > best.strength:
                    best = candidate
        if best:
            matches.append(best)
    matches.sort(key=lambda m: (-m.strength, -len(_normalise(m.alias)), m.spec.key))
    selected = matches[:theme_pool_limit]
    if direction == "bearish" and pool_limit:
        # The inverse pool is a direction-specific discovery source, not a text
        # match.  Keeping it inside max_pools preserves the existing source cap.
        selected.append(PoolMatch(
            INVERSE_SP500_POOL, 1.0, "theme_direction", "bearish",
        ))
    return selected


def _finite_float(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _theme_direction(value: object) -> str:
    """Return the one direction that may enable inverse products."""
    return "bearish" if str(value or "").strip().lower() == "bearish" else "bullish"


def _stock_quality(stock: dict) -> float:
    relevance = _finite_float(stock.get("ai_relevance"))
    confidence = _finite_float(stock.get("confidence"))
    rel = max(0.0, min(1.0, ((relevance if relevance is not None else 1.0) - 1.0) / 4.0))
    conf = max(0.0, min(1.0, confidence if confidence is not None else 0.5))
    exposure = _EXPOSURE_FACTOR.get(str(stock.get("exposure_type") or "unclear"), 0.35)
    return rel * conf * exposure


_ALTERNATIVE_STRATEGY_RE = re.compile(
    r"(?:style\s+premia|market\s+neutral|long[\s/\-]*short|managed\s+futures)",
    re.IGNORECASE,
)
_OPTION_INCOME_RE = re.compile(
    r"(?:yieldmax|option\s+income|options\s+income|covered[\s\-]*call)",
    re.IGNORECASE,
)
_NAME_MULTIPLE_RE = re.compile(
    r"(?P<sign>[-\N{MINUS SIGN}])?\s*(?P<multiple>[1-9](?:\.\d+)?)\s*[x\N{MULTIPLICATION SIGN}](?![a-z0-9])",
    re.IGNORECASE,
)
_INVERSE_NAME_RE = re.compile(r"\b(?:inverse|bear|ultrashort)\b", re.IGNORECASE)
_NON_DIRECTIONAL_SHORT_RE = re.compile(
    r"\b(?:ultra[\s-]+)?short[\s-]+(?:duration|term|maturity|dated|bond|treasury|credit|income)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class _DerivativeProfile:
    direction: str
    leverage: float
    direction_source: str
    leverage_source: str
    ambiguous_leverage: bool = False
    conflict: bool = False


def _structured_direction(value: object) -> str | None:
    text = _normalise(value)
    if not text:
        return None
    if text in {"short", "inverse", "bear", "bearish", "negative"}:
        return "short"
    if text in {"long", "bull", "bullish", "positive"}:
        return "long"
    return None


def _name_direction(name: object) -> str | None:
    raw = str(name or "")
    if not raw:
        return None
    if any(match.group("sign") for match in _NAME_MULTIPLE_RE.finditer(raw)):
        return "short"
    if _INVERSE_NAME_RE.search(raw):
        return "short"
    # "Short Duration Treasury" describes maturity, not inverse exposure.
    without_duration = _NON_DIRECTIONAL_SHORT_RE.sub(" ", raw)
    if re.search(r"\bshort\b", without_duration, re.IGNORECASE):
        return "short"
    return None


def _name_leverage(name: object) -> float | None:
    raw = str(name or "")
    multiples = [float(match.group("multiple")) for match in _NAME_MULTIPLE_RE.finditer(raw)]
    if multiples:
        return max(multiples)
    normalised = _normalise(raw)
    if any(phrase in normalised for phrase in (
            "quadruple", "four times", "four time", "4 times", "4 time")):
        return 4.0
    if any(phrase in normalised for phrase in (
            "ultrapro", "triple short", "three times", "three time", "3 times", "3 time")):
        return 3.0
    if any(phrase in normalised for phrase in (
            "ultrashort", "double short", "two times", "two time", "2 times", "2 time")):
        return 2.0
    return None


def _derivative_profile(candidate: dict) -> _DerivativeProfile:
    raw_direction = _structured_direction(candidate.get("direction"))
    raw_leverage = _finite_float(candidate.get("leverage"))
    name_direction = _name_direction(candidate.get("name")) if raw_direction is None else None
    name_leverage = _name_leverage(candidate.get("name")) if raw_leverage is None else None

    direction = raw_direction or name_direction or ("short" if raw_leverage is not None and raw_leverage < 0 else "long")
    leverage = abs(raw_leverage) if raw_leverage is not None else (name_leverage or 1.0)
    conflict = raw_direction == "long" and raw_leverage is not None and raw_leverage < 0
    return _DerivativeProfile(
        direction=direction,
        leverage=leverage,
        direction_source="metadata" if raw_direction is not None else (
            "name" if name_direction is not None else
            "leverage" if raw_leverage is not None and raw_leverage < 0 else "default"
        ),
        leverage_source="metadata" if raw_leverage is not None else (
            "name" if name_leverage is not None else "default"
        ),
        ambiguous_leverage=(
            raw_direction is None and name_direction is None and
            raw_leverage is not None and
            not math.isclose(abs(raw_leverage), 1.0, rel_tol=0.0, abs_tol=1e-9)
        ),
        conflict=conflict,
    )


def _hard_filter_reason(
    candidate: dict,
    theme_direction: str = "bullish",
    *,
    defer_ambiguous: bool = False,
) -> str | None:
    strategy_text = " ".join(str(candidate.get(key) or "") for key in (
        "name", "fund_strategy", "fund_category", "fund_niche"))
    if _ALTERNATIVE_STRATEGY_RE.search(strategy_text):
        return "alternative long/short strategy"
    profile = _derivative_profile(candidate)
    if profile.conflict:
        return "conflicting direction/leverage metadata"
    if profile.leverage > 3.0:
        return f"leverage={profile.leverage:g}"

    if _theme_direction(theme_direction) == "bearish":
        if profile.direction == "short":
            if profile.leverage < 1.0:
                return f"inverse leverage={profile.leverage:g}"
            return None
        if defer_ambiguous and profile.ambiguous_leverage:
            return None
        if not math.isclose(profile.leverage, 1.0, rel_tol=0.0, abs_tol=1e-9):
            return f"leveraged-long={profile.leverage:g}"
        return None

    if not math.isclose(profile.leverage, 1.0, rel_tol=0.0, abs_tol=1e-9):
        return f"leverage={profile.leverage:g}"
    if profile.direction == "short":
        return "direction=Short"
    return None


def _merge_row(candidate: dict, row: dict) -> None:
    for key in ("name", "aum", "leverage", "direction"):
        if candidate.get(key) in (None, "") and row.get(key) not in (None, ""):
            candidate[key] = row[key]


def _discover(
    quotes,
    stocks: list[dict],
    pools: list[PoolMatch],
    limit: int,
    failures: list[str],
    theme_direction: str = "bullish",
) -> tuple[dict[str, dict], int]:
    """Round-robin all finite theme sources until ``limit`` unique ETFs exist."""
    source_count = len(stocks) + len(pools)
    if source_count == 0:
        return {}, 0
    page_size = min(1000, max(20, math.ceil(limit / source_count) * 2))
    active = deque()
    for pool in pools:
        iterator = islice(quotes.iter_prompt_etfs(pool.spec.prompt_id, page_size=page_size), limit)
        active.append(("pool", pool.spec.key, pool, iterator))
    for stock in stocks:
        code = stock["code"]
        iterator = islice(quotes.iter_related_etfs(code, page_size=page_size), limit)
        active.append(("stock", code, stock, iterator))

    candidates: dict[str, dict] = {}
    excluded = 0
    while active and len(candidates) < limit:
        kind, key, context, iterator = active.popleft()
        try:
            row = next(iterator)
        except StopIteration:
            continue
        except RuntimeError as exc:
            failures.append(f"{kind}:{key}: {exc}")
            continue

        code = str(row.get("code") or "").strip()
        if code:
            probe = dict(row)
            probe["name"] = probe.get("name") or quotes.name_of(code)
            if _hard_filter_reason(
                    probe, theme_direction, defer_ambiguous=True):
                excluded += 1
            else:
                candidate = candidates.setdefault(code, {
                    "code": code,
                    "name": probe["name"],
                    "anchor_weights": {},
                    "related_stock_codes": {},
                    "pool_matches": {},
                    "source_order": len(candidates) + 1,
                })
                _merge_row(candidate, probe)
                if kind == "stock":
                    # The related-ETF endpoint is itself relationship evidence.
                    # Derivative wrappers frequently have no physical holding
                    # weight, so retain the source link independently of weight.
                    candidate["related_stock_codes"][key] = True
                    weight = _finite_float(row.get("holding_weight"))
                    if weight is not None and weight > 0:
                        candidate["anchor_weights"][key] = weight
                else:
                    candidate["pool_matches"][context.spec.key] = context
        active.append((kind, key, context, iterator))
    return candidates, excluded


def _enrich(quotes, candidates: dict[str, dict], stocks: list[dict], failures: list[str]) -> None:
    codes = list(candidates)
    if not codes:
        return
    try:
        metadata = quotes.etf_metadata(codes)
    except RuntimeError as exc:
        failures.append(f"metadata: {exc}")
        metadata = {}
    for code, values in metadata.items():
        candidate = candidates.get(code)
        if not candidate:
            continue
        for key, value in values.items():
            if value not in (None, ""):
                candidate[key] = value

    # One indicator / request is intentional. The quote API does not reliably
    # return repeated holding-weight indicator ids with different match_code attrs.
    for stock in stocks:
        stock_code = stock["code"]
        try:
            weights = quotes.etf_holding_weights_for_stock(codes, stock_code)
        except RuntimeError as exc:
            failures.append(f"holding-weight:{stock_code}: {exc}")
            continue
        for code, raw_weight in weights.items():
            weight = _finite_float(raw_weight)
            if code in candidates and weight is not None and weight > 0:
                candidates[code]["anchor_weights"][stock_code] = weight
                candidates[code].setdefault("related_stock_codes", {})[stock_code] = True


def _is_single_stock_wrapper(
    candidate: dict,
    breadth: int,
    max_weight: float,
    stocks: list[dict] | None = None,
) -> bool:
    niche = _normalise(candidate.get("fund_niche"))
    category = _normalise(candidate.get("fund_category"))
    name = str(candidate.get("name") or "")
    normalised_name = _normalise(name)
    if (
        "single stock" in niche or "single stock" in category or
        "single stock" in normalised_name
    ):
        return True
    benchmark = str(candidate.get("benchmark") or "").strip()
    if breadth <= 1 and re.fullmatch(r"\d+:[A-Za-z0-9.\-]+", benchmark):
        return True
    # Sparse quote rows can omit niche and benchmark metadata.  An all-caps
    # symbol embedded in a derivative-style product name still identifies a
    # single-stock wrapper; exclude the fund's own ticker and product acronyms.
    own_ticker = str(candidate.get("code") or "").partition(":")[2].upper()
    symbol_patterns = (
        r"\b(?:Short|Long|Inverse)\s+([A-Z][A-Z0-9.\-]{1,5})\b",
        r"\b([A-Z][A-Z0-9.\-]{1,5})\s+(?:Bull|Bear)\b",
    )
    name_symbols = {
        match.group(1).upper()
        for pattern in symbol_patterns
        for match in re.finditer(pattern, name)
    }
    name_symbols -= {
        own_ticker, "ETF", "ETN", "UCITS", "USD", "DAILY", "SHORT", "LONG",
        "BULL", "BEAR", "ULTRA", "SHARES", "TRADR", "T-REX", "S&P",
    }
    reference_patterns = (
        r"\b(?:Short|SHORT|Long|LONG|Inverse|INVERSE)\s+"
        r"([A-Z][A-Za-z0-9.&'\-]*(?:\s+[A-Z][A-Za-z0-9.&'\-]*){0,2}?)"
        r"\s+(?:Daily|DAILY|ETF|ETN|Fund|$)",
        r"\b([A-Z][A-Za-z0-9.&'\-]*(?:\s+[A-Z][A-Za-z0-9.&'\-]*){0,2}?)"
        r"\s+(?:Bull|BULL|Bear|BEAR)\b",
    )
    name_references = [
        _normalise(match.group(1))
        for pattern in reference_patterns
        for match in re.finditer(pattern, name)
    ]
    non_single_reference_re = re.compile(
        r"\b(?:index|market|sector|semiconductor|technology|financial|energy|"
        r"biotech|healthcare|industrial|materials|utilities|consumer|real estate|"
        r"communication|artificial intelligence|cybersecurity|clean energy|s&p|"
        r"nasdaq|dow|russell|treasury|bond|gold|silver|oil|bitcoin|ethereum|"
        r"dollar|volatility|vix|duration|term|income)\b",
        re.IGNORECASE,
    )
    company_name_reference = any(
        reference and not non_single_reference_re.search(reference)
        for reference in name_references
    )
    if breadth <= 1 and name_symbols and (
            "daily" in normalised_name or _name_direction(name) is not None or
            _name_leverage(name) is not None):
        return True
    if breadth <= 1 and company_name_reference and (
            "daily" in normalised_name or _name_direction(name) is not None or
            _name_leverage(name) is not None):
        return True
    if breadth <= 1 and stocks and (
        "daily" in normalised_name or _name_direction(name) is not None or
        _name_leverage(name) is not None
    ):
        for stock in stocks:
            code = str(stock.get("code") or "").strip()
            ticker = code.partition(":")[2] or code
            stock_name = _normalise(stock.get("name"))
            if (
                (len(ticker) >= 2 and _contains_phrase(normalised_name, ticker)) or
                (len(stock_name) >= 4 and _contains_phrase(normalised_name, stock_name))
            ):
                return True
    return breadth <= 1 and max_weight > 80.0


def _benchmark_stock_matches(candidate: dict, stocks: list[dict]) -> list[str]:
    benchmark = str(candidate.get("benchmark") or "").strip()
    if not benchmark:
        return []
    benchmark_normalised = _normalise(benchmark)
    matched: list[str] = []
    for stock in stocks:
        code = str(stock.get("code") or "").strip()
        ticker = code.partition(":")[2] or code
        if not code:
            continue
        if benchmark.casefold() == code.casefold() or _contains_phrase(
                benchmark_normalised, ticker):
            matched.append(code)
    return matched


def _rank_candidates(
    candidates: dict[str, dict],
    stocks: list[dict],
    limit: int,
    theme_direction: str = "bullish",
) -> tuple[list[dict], int]:
    direction_mode = _theme_direction(theme_direction)
    stock_by_code = {stock["code"]: stock for stock in stocks}
    quality = {code: _stock_quality(stock) for code, stock in stock_by_code.items()}
    ranked: list[dict] = []
    excluded = 0

    for candidate in candidates.values():
        hard_reason = _hard_filter_reason(candidate, direction_mode)
        if hard_reason:
            excluded += 1
            continue
        derivative = _derivative_profile(candidate)
        holdings = []
        for stock_code, raw_weight in candidate.get("anchor_weights", {}).items():
            weight = _finite_float(raw_weight)
            if weight is None or weight <= 0:
                continue
            stock = stock_by_code.get(stock_code, {"code": stock_code, "name": stock_code})
            holdings.append({
                "code": stock_code,
                "ticker": stock_code.partition(":")[2],
                "name": stock.get("name") or stock_code,
                "weight_pct": weight,
                "quality": quality.get(stock_code, 0.0),
            })
        holdings.sort(key=lambda item: (-item["weight_pct"], item["code"]))
        raw_weight = sum(item["weight_pct"] for item in holdings)
        weighted_exposure = sum(item["weight_pct"] * item["quality"] for item in holdings)
        breadth = sum(1 for item in holdings if item["weight_pct"] >= 0.25)
        max_weight = max((item["weight_pct"] for item in holdings), default=0.0)

        pool_matches = list(candidate.get("pool_matches", {}).values())
        pool_keys = {match.spec.key for match in pool_matches}
        strategy_text = " ".join(str(candidate.get(key) or "") for key in (
            "name", "fund_strategy", "fund_category", "fund_niche"))
        if _OPTION_INCOME_RE.search(strategy_text) and (
                direction_mode == "bearish" or "covered_call" not in pool_keys):
            excluded += 1
            continue

        related_codes = set(candidate.get("related_stock_codes", {})) | set(
            candidate.get("anchor_weights", {}))
        related_codes &= set(stock_by_code)
        benchmark_codes = set(_benchmark_stock_matches(candidate, stocks))
        inverse_pool_evidence = INVERSE_SP500_POOL.key in pool_keys
        thematic_benchmark_evidence = bool(
            candidate.get("benchmark") and (pool_keys - {INVERSE_SP500_POOL.key})
        )
        inverse_evidence: list[str] = []
        if related_codes:
            inverse_evidence.append("related_stock")
        if benchmark_codes:
            inverse_evidence.append("selected_stock_benchmark")
        elif thematic_benchmark_evidence:
            inverse_evidence.append("thematic_benchmark")
        if inverse_pool_evidence:
            inverse_evidence.append("inverse_sp500_pool")
        verified_inverse = (
            direction_mode == "bearish" and derivative.direction == "short" and
            bool(inverse_evidence)
        )

        single_stock_wrapper = _is_single_stock_wrapper(
            candidate, breadth, max_weight, stocks,
        )
        if single_stock_wrapper and not (
                verified_inverse and bool(related_codes or benchmark_codes)):
            excluded += 1
            continue
        if derivative.direction == "short" and not verified_inverse:
            excluded += 1
            continue
        pool_strength = min(max((m.strength for m in pool_matches), default=0.0) / 3.25, 1.0)
        if weighted_exposure <= 0 and not pool_matches and not verified_inverse:
            excluded += 1
            continue

        inverse_base_floor = 0.0
        if verified_inverse and (related_codes or benchmark_codes):
            # A related-underlying or exact selected-stock benchmark remains
            # strong exposure evidence even when a derivative holds no shares.
            inverse_base_floor = 0.30 + 0.15 * pool_strength
        elif verified_inverse and thematic_benchmark_evidence:
            inverse_base_floor = 0.20 + 0.15 * pool_strength

        if weighted_exposure > 0:
            holding_component = min(weighted_exposure / 50.0, 1.0)
            breadth_component = min(breadth / max(1, min(len(stocks), 5)), 1.0)
            static_score = 0.80 * holding_component + 0.15 * breadth_component + 0.05 * pool_strength
            static_score = max(static_score, inverse_base_floor)
        elif inverse_base_floor:
            static_score = inverse_base_floor
        elif pool_keys & _DIRECT_ASSET_POOL_KEYS or not stocks:
            # Curated-pool membership is deterministic evidence for asset themes
            # (gold, bonds, Bitcoin, etc.) where stock holdings are inapplicable.
            static_score = 0.60 + 0.20 * pool_strength
        else:
            # Equity pool membership is a candidate-generation signal, not proof
            # of exposure to this specific event. It must rank below verified
            # holdings overlap when selected stock anchors are available.
            static_score = 0.10 + 0.15 * pool_strength
        static_score = max(0.05, min(1.0, static_score))

        normalised_leverage = max(0.0, min(1.0, (derivative.leverage - 1.0) / 2.0))
        if direction_mode == "bearish":
            directional_score = (
                0.50 + 0.45 * static_score + 0.05 * normalised_leverage
                if verified_inverse else
                0.45 * static_score
            )
        else:
            directional_score = static_score

        aum = _finite_float(candidate.get("aum"))
        candidate.update({
            "matched_holdings": holdings,
            "theme_weight_pct": raw_weight,
            "weighted_theme_exposure_pct": weighted_exposure,
            "theme_breadth": breadth,
            "pool_labels": sorted(m.spec.label for m in pool_matches),
            "pool_strength": pool_strength,
            "preselect_score": directional_score,
            "base_theme_exposure": static_score,
            "static_theme_exposure": directional_score,
            "ai_relevance": round(1.0 + 4.0 * static_score, 1),
            "confidence": 0.95 if weighted_exposure >= 10 else (0.85 if weighted_exposure > 0 else 0.75),
            "exposure_type": (
                "direct" if verified_inverse or raw_weight >= 35 else
                "beneficiary" if weighted_exposure > 0 else
                "direct" if str(candidate.get("asset_class") or "").lower() not in ("equity", "stock", "index") else
                "diversified"
            ),
            "direction": "Short" if derivative.direction == "short" else "Long",
            "leverage": derivative.leverage,
            "direction_source": derivative.direction_source,
            "leverage_source": derivative.leverage_source,
            "is_inverse": verified_inverse,
            "verified_inverse": verified_inverse,
            "single_stock_inverse": single_stock_wrapper and verified_inverse,
            "inverse_evidence": inverse_evidence,
            "benchmark_match_codes": sorted(benchmark_codes),
            "normalised_leverage": normalised_leverage,
            "metric": aum,
            "aum": aum,
            "relevance_status": "deterministic",
        })
        if verified_inverse:
            linked = sorted(related_codes | benchmark_codes)
            linked_text = ", ".join(code.partition(":")[2] for code in linked[:4])
            evidence_text = (
                f" linked to {linked_text}" if linked_text else
                f" in {', '.join(candidate['pool_labels'])}" if candidate["pool_labels"] else ""
            )
            candidate["reason"] = (
                f"Targets {derivative.leverage:g}x daily inverse exposure{evidence_text}."
            )
        elif holdings:
            detail = ", ".join(
                f"{item['ticker']} {item['weight_pct']:.1f}%" for item in holdings[:4])
            candidate["reason"] = (
                f"Holds {breadth} theme-linked companies ({raw_weight:.1f}% total): {detail}."
            )
        else:
            candidate["reason"] = f"Member of {', '.join(candidate['pool_labels'])}."
        ranked.append(candidate)

    ranked.sort(key=lambda candidate: (
        -candidate["preselect_score"],
        -candidate["weighted_theme_exposure_pct"],
        -candidate["theme_breadth"],
        -candidate["pool_strength"],
        -candidate["normalised_leverage"],
        -(candidate.get("aum") if candidate.get("aum") is not None else -1.0),
        candidate["code"],
    ))
    for rank, candidate in enumerate(ranked[:limit], 1):
        candidate["rank"] = rank
    return ranked[:limit], excluded


def preselect_etfs(
    quotes,
    stocks: list[dict],
    *,
    theme: str,
    brief: dict | None,
    article_title: str,
    limit: int,
    max_pools: int = 4,
    theme_direction: str = "bullish",
    log: Callable[[str], None] | None = None,
) -> PreselectionResult:
    """Build and deterministically rank at most ``limit`` thematic ETF candidates."""
    if limit <= 0:
        raise ValueError("ETF candidate limit must be greater than 0")
    direction_mode = _theme_direction(theme_direction)
    pools = match_theme_pools(
        theme,
        brief,
        article_title,
        max_pools=max_pools,
        theme_direction=direction_mode,
    )
    failures: list[str] = []
    candidates, discovery_excluded = _discover(
        quotes, stocks, pools, limit, failures, direction_mode,
    )
    _enrich(quotes, candidates, stocks, failures)
    ranked, ranking_excluded = _rank_candidates(
        candidates, stocks, limit, direction_mode,
    )
    result = PreselectionResult(
        candidates=ranked,
        pools=pools,
        discovered=len(candidates),
        excluded=discovery_excluded + ranking_excluded,
        failures=failures,
    )
    if log:
        pool_names = ", ".join(match.spec.label for match in pools) or "none"
        log(f"ETF static pools ({direction_mode}): {pool_names}")
        log(f"ETF preselection: {result.discovered} unique candidates, "
            f"{len(ranked)} ranked (limit={limit}); "
            f"{result.excluded} risky source/candidate rows excluded")
        for failure in failures:
            log(f"ETF preselection partial source failure: {failure}")
        for candidate in ranked[:10]:
            log(
                f"ETF rank {candidate['rank']}: {candidate['code']} {candidate['name']} | "
                f"score={candidate['preselect_score']:.3f} | "
                f"theme holdings={candidate['theme_weight_pct']:.1f}% | "
                f"breadth={candidate['theme_breadth']} | "
                f"AUM={candidate.get('aum')} | {candidate['reason']}"
            )
    return result
