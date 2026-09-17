"""Deterministic ETF candidate generation and ranking.

The ETF universe is theme-derived rather than globally ranked by AUM:

* theme stocks expand to related ETFs, including derivatives without physical weight;
* a static registry and exact concept-index matches supplement stock-led recall;
* factual direction filters and holdings arithmetic run in code;
* turnover, AUM, and expense contribute only through bounded investability.

Generic basket sources and the ordinary unique-candidate set are bounded by
``min(limit, 500)``. Exact selected-stock derivative probes are additive so a
broad basket cannot crowd out a verified wrapper on a selected public stock.
"""

from __future__ import annotations

import math
import re
from collections import deque
from dataclasses import dataclass, field
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


@dataclass(frozen=True)
class CanonicalThemeEvidence:
    """Validated exact names shared by mandate and concept-index discovery."""

    validated_terms: tuple[str, ...]
    routed_pool_keys: tuple[str, ...]
    exact_names: tuple[str, ...]
    normalized_names: frozenset[str]


@dataclass
class PreselectionResult:
    candidates: list[dict]
    pools: list[PoolMatch]
    discovered: int
    excluded: int
    failures: list[str]
    exclusion_reasons: dict[str, int] = field(default_factory=dict)
    exclusion_examples: dict[str, list[str]] = field(default_factory=dict)
    canonical_diagnostics: dict[str, object] = field(default_factory=dict)
    discovery_source_counts: dict[str, dict[str, int]] = field(default_factory=dict)


# Stable product prompt pools documented in
# Skills/ainvest-openapi-quote/references/legacy/id_dict.md.  Ambiguous one-word
# aliases are intentionally avoided where producer equities and direct-asset funds
# would otherwise be conflated.
_AMERICAN_CONSUMER_ALIASES = (
    "american consumer", "american consumers",
    "u s consumer", "u s consumers", "us consumer", "us consumers",
    "united states consumer", "united states consumers",
    "美国消费", "美国消费者",
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
    PoolSpec("consumer_discretionary", "Consumer Discretionary ETFs",
             "6908aebd8738843bb3ba864a",
             ("consumer discretionary", "housing related consumer discretionary",
              "home construction", "homebuilders", "home builders", "auto retail",
              "auto finance", "home improvement retail",
              *_AMERICAN_CONSUMER_ALIASES)),
    PoolSpec("consumer_staples", "Consumer Staples ETFs",
             "6908af018738843bb3ba864b",
             ("consumer staples", "consumer staple", "consumer defensive",
              "essential consumer goods", "food and household products",
              *_AMERICAN_CONSUMER_ALIASES)),
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
             ("real estate", "residential real estate", "residential reit",
              "residential reits", "mortgage reit", "mortgage reits", "reit", "reits")),
    PoolSpec("ecommerce", "Internet & E-Commerce ETFs", "6908b20d069a48065f15937b",
             ("ecommerce", "e commerce", "online retail", "digital marketplace")),
    PoolSpec("retail", "Retail ETFs", "6908b0308738843bb3ba8653",
             ("retail sector", "retailers", "retail stocks", "consumer retail",
              "home improvement retail")),
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
    "factor_proxy": 0.0,
    "diversified": 0.6,
    "unclear": 0.35,
}
_SPECIFICITY_FACTOR = {
    "company_specific": 1.0,
    "industry_specific": 0.9,
    "broad_factor": 0.0,
    "none": 0.0,
}
_MATERIALITY_FACTOR = {"high": 1.0, "medium": 0.85, "unknown": 0.55, "low": 0.35}
_EVIDENCE_FACTOR = {"explicit": 1.0, "derived": 0.85, "speculative": 0.3, "none": 0.0}

_DIRECT_ASSET_POOL_KEYS = {
    "physical_gold", "gold", "silver", "crude_oil", "energy_commodities",
    "broad_commodities", "agriculture", "precious_metals", "industrial_metals",
    "short_treasury", "investment_grade_bonds", "high_yield_bonds",
    "municipal_bonds", "inflation_bonds", "fixed_income", "bitcoin", "ethereum",
}


def _normalise(value: object) -> str:
    text = str(value or "").lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text)
    return " ".join(text.split())


def _holding_issuer_key(code: str, name: object) -> str:
    """Collapse share classes for ETF thematic-breadth counting."""
    text = str(name or code).strip()
    text = re.sub(
        r"\s+(?:(?:series|pref(?:erred)?(?:\s+(?:stock|shares?))?)\s*)"
        r"[a-z0-9.-]+(?:\s+(?:stock|shares?))?$", "", text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\s+(?:class\s+)?(?:a|b|c)(?:\s+(?:shares?|common stock))?$", "", text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"\s+(?:incorporated|inc\.?|corp(?:oration)?\.?|company|co\.?|plc|ltd\.?)$",
        "", text, flags=re.IGNORECASE,
    )
    return _normalise(text) or code.casefold()


def _contains_phrase(text: str, phrase: str) -> bool:
    phrase = _normalise(phrase)
    return bool(phrase) and f" {phrase} " in f" {text} "


def _brief_text(brief: dict, theme: str = "") -> str:
    # The dedicated ETF mandate field is the only model-authored routing input.
    # Beneficiary/pathway prose can validly mention broad factor winners (for
    # example big tech after lower yields) and must not activate a sector pool.
    return " ".join(_validated_etf_terms(brief, theme))


_MANDATE_GENERIC_TOKENS = {
    "active", "and", "etf", "fund", "index", "ishares", "invesco", "portfolio",
    "spdr", "state", "street", "the", "trust", "us", "vanguard",
}
_MANDATE_CONCEPT_REWRITES = (
    (r"\b(?:home\s*builders?|homebuilding|home construction|housing related)\b", "housing"),
    (r"\b(?:mortgage backed securities?|mortgage finance|mortgage reits?)\b", "mortgage"),
    (r"\b(?:residential real estate|residential reits?)\b", "residential"),
    (r"\b(?:real estate|reits?)\b", "real_estate"),
    (r"\bhome improvement(?: retail)?\b", "home_improvement"),
    (r"\bconsumer discretionary(?: retail)?\b", "consumer_discretionary"),
    (r"\b(?:building products?|building (?:and |&) construction)\b", "building_products"),
    (r"\b(?:auto retail|auto finance)\b", "auto_finance"),
    (r"\b(?:semiconductors?|chipmakers?|chip makers?|memory chips?|ai memory)\b", "semiconductor"),
    (r"\b(?:artificial intelligence|generative ai|machine learning)\b", "artificial_intelligence"),
    (r"\b(?:ecommerce|e commerce|online retail(?:ers?)?)\b", "ecommerce"),
)


def _mandate_tokens(value: object) -> set[str]:
    text = _normalise(value)
    for pattern, replacement in _MANDATE_CONCEPT_REWRITES:
        text = re.sub(pattern, replacement, text)
    return {
        token for token in text.split()
        if token not in _MANDATE_GENERIC_TOKENS and len(token) > 2
    }


_MANDATE_HIGH_SIGNAL = {
    "housing", "mortgage", "residential", "real_estate", "home_improvement",
    "consumer_discretionary", "building_products", "auto_finance",
}
_GENERIC_FACTOR_ETF_TERM_RE = re.compile(
    r"\b(?:big tech|(?:information )?technology(?: sector| stocks?)?|"
    r"growth stocks?|long[ -]?duration|"
    r"duration stocks?|risk[ -]?on|broad market|market beta)\b",
    re.IGNORECASE,
)
_MACRO_FACTOR_THEME_RE = re.compile(
    r"\b(?:inflation|disinflation|cpi|consumer prices?|interest rates?|rate cuts?|"
    r"rate hikes?|federal reserve|fed policy|monetary policy|bond yields?)\b",
    re.IGNORECASE,
)
_CJK_MACRO_FACTOR_THEME_RE = re.compile(
    r"通胀|通货膨胀|物价|消费者价格|利率|降息|加息|美联储|货币政策|债券收益率"
)
_MACRO_TECH_CONCEPTS = {
    "semiconductor", "artificial_intelligence", "robotics", "cybersecurity",
    "software", "internet_infrastructure", "technology", "digital_economy",
    "fintech", "blockchain", "ecommerce",
}

_ETF_CONCEPT_PATTERNS: tuple[tuple[str, re.Pattern], ...] = (
    ("gold_miners", re.compile(r"\bgold (?:miners?|mining)\b|黄金矿(?:业|商)?", re.I)),
    ("silver_miners", re.compile(r"\bsilver (?:miners?|mining)\b|白银矿(?:业|商)?", re.I)),
    ("bitcoin_miners", re.compile(r"\b(?:bitcoin|crypto) miners?\b|比特币矿(?:业|商)?", re.I)),
    ("housing", re.compile(
        r"\b(?:home\s*builders?|homebuilding|home construction|housing related)\b|"
        r"住宅建筑商?|住宅建造|住宅建设|房屋建筑商?|房屋建造|住房建设", re.I)),
    ("mortgage", re.compile(
        r"\b(?:mortgage(?: finance| lenders?| reits?| backed securities?)?)\b|"
        r"抵押贷款|按揭|房贷", re.I)),
    ("residential", re.compile(r"\bresidential(?: real estate| reits?)?\b|住宅地产|住宅房地产", re.I)),
    ("real_estate", re.compile(r"\b(?:real estate|reits?)\b|房地产|不动产", re.I)),
    ("home_improvement", re.compile(r"\bhome improvement(?: retail)?\b|家居建材零售|家装零售", re.I)),
    ("building_products", re.compile(r"\bbuilding products?\b|建筑产品|建筑材料|建材", re.I)),
    ("auto_finance", re.compile(r"\b(?:auto retail|auto finance)\b|汽车金融|汽车零售", re.I)),
    ("consumer_discretionary", re.compile(r"\bconsumer discretionary(?: retail)?\b|可选消费", re.I)),
    ("regional_banks", re.compile(r"\b(?:regional|community) banks?\b|区域银行|社区银行", re.I)),
    ("semiconductor", re.compile(
        r"\b(?:semiconductors?|chipmakers?|chip makers?|memory chips?|ai memory|hbm|dram|nand)\b|半导体", re.I)),
    ("artificial_intelligence", re.compile(
        r"\b(?:ai|artificial intelligence|generative ai|machine learning|ai companies?)\b|人工智能|生成式ai", re.I)),
    ("robotics", re.compile(r"\b(?:robotics?|industrial automation|factory automation)\b|机器人|工业自动化", re.I)),
    ("cybersecurity", re.compile(r"\b(?:cybersecurity|cyber security|information security)\b|网络安全|信息安全", re.I)),
    ("software", re.compile(r"\b(?:software|saas|cloud software)\b|软件|云软件", re.I)),
    ("internet_infrastructure", re.compile(
        r"\b(?:internet infrastructure|cloud infrastructure|data cent(?:er|re)s?)\b|互联网基础设施|云基础设施|数据中心", re.I)),
    ("technology", re.compile(
        r"\b(?:big tech|(?:information )?technology(?: sector| stocks?)?)\b|科技板块|信息技术板块", re.I)),
    ("digital_economy", re.compile(r"\b(?:digital economy|digital transformation)\b|数字经济|数字化转型", re.I)),
    ("fintech", re.compile(r"\b(?:fintech|financial technology|digital payments?|mobile payments?)\b|金融科技|数字支付|移动支付", re.I)),
    ("blockchain", re.compile(r"\b(?:blockchain|crypto exchanges?)\b|区块链|加密货币交易所", re.I)),
    ("ecommerce", re.compile(r"\b(?:ecommerce|e commerce|online retail(?:ers?)?|digital marketplace)\b|电子商务|电商|在线零售", re.I)),
    ("physical_gold", re.compile(r"\b(?:physical gold|gold bullion)\b|实物黄金|黄金现货", re.I)),
    ("gold", re.compile(r"\b(?:gold prices?|gold etfs?|gold)\b|黄金", re.I)),
    ("silver", re.compile(r"\b(?:silver prices?|silver etfs?|silver)\b|白银", re.I)),
    ("bitcoin", re.compile(r"\b(?:bitcoin|spot bitcoin|btc)\b|比特币", re.I)),
    ("ethereum", re.compile(r"\b(?:ethereum|spot eth|ether)\b|以太坊", re.I)),
    ("crude_oil", re.compile(
        r"\b(?:crude oil|oil prices?|wti|brent crude)\b|原油|油价", re.I)),
)

_CONCEPT_POOL_KEYS: dict[str, set[str]] = {
    "gold_miners": {"gold_miners"},
    "silver_miners": {"silver_miners"},
    "bitcoin_miners": {"blockchain"},
    "housing": {"consumer_discretionary"},
    "mortgage": {"real_estate"},
    "residential": {"real_estate"},
    "real_estate": {"real_estate"},
    "home_improvement": {"consumer_discretionary", "retail"},
    "building_products": {"consumer_discretionary", "infrastructure"},
    "auto_finance": {"consumer_discretionary"},
    "consumer_discretionary": {"consumer_discretionary"},
    "regional_banks": {"regional_banks"},
    "semiconductor": {"semiconductors"},
    "artificial_intelligence": {"artificial_intelligence"},
    "robotics": {"robotics", "ai_robotics"},
    "cybersecurity": {"cybersecurity"},
    "software": {"software"},
    "internet_infrastructure": {"internet_infrastructure"},
    "technology": {"technology"},
    "digital_economy": {"digital_economy"},
    "fintech": {"fintech"},
    "blockchain": {"blockchain"},
    "ecommerce": {"ecommerce"},
    "physical_gold": {"physical_gold"},
    "gold": {"gold"},
    "silver": {"silver"},
    "bitcoin": {"bitcoin"},
    "ethereum": {"ethereum"},
    "crude_oil": {"crude_oil"},
}


def _semantic_concepts(value: object) -> set[str]:
    text = str(value or "").casefold()
    concepts = {
        concept for concept, pattern in _ETF_CONCEPT_PATTERNS
        if pattern.search(text)
    }
    # More-specific producer/physical concepts suppress nested asset words.
    if "gold_miners" in concepts:
        concepts.discard("gold")
    if "silver_miners" in concepts:
        concepts.discard("silver")
    if "bitcoin_miners" in concepts:
        concepts.discard("bitcoin")
        concepts.discard("blockchain")
    if "physical_gold" in concepts:
        concepts.discard("gold")
    return concepts


def _concept_pool_keys(concepts: set[str]) -> set[str]:
    return set().union(*(
        _CONCEPT_POOL_KEYS.get(concept, set()) for concept in concepts
    )) if concepts else set()


def _pool_keys_in_text(value: object) -> set[str]:
    text = _normalise(value)
    return {
        spec.key for spec in THEME_POOLS
        if any(_contains_phrase(text, alias) for alias in spec.aliases)
    }


def _canonical_pool_keys(value: object) -> set[str]:
    """Resolve any supported spelling to canonical pool keys.

    Phrase aliases cover the entire catalog, while semantic concepts preserve
    the special producer-vs-direct-asset distinctions (for example gold miners
    must not silently become physical gold). This helper is the shared evidence
    vocabulary for term validation and pool routing.
    """
    concepts = _semantic_concepts(value)
    concept_keys = _concept_pool_keys(concepts)
    phrase_keys = _pool_keys_in_text(value)
    if concept_keys:
        phrase_keys -= {
            key for key in phrase_keys
            if key in _DIRECT_ASSET_POOL_KEYS and key not in concept_keys
        }
    return concept_keys | phrase_keys


def _validated_etf_terms(brief: dict | None, theme: str = "") -> list[str]:
    """Return ETF mandate terms corroborated by a primary causal pathway.

    The event model proposes terms, but a proposal is not allowed to route a
    sector pool by itself. Each term must agree with direct-beneficiary or
    picks-and-shovels descriptors, and false-positive/factor-proxy terms are
    rejected. This is deliberately fail closed because holdings and mandate
    verification happen downstream.
    """
    brief = brief or {}
    raw_terms = brief.get("etf_exposure_terms") or []
    if not isinstance(raw_terms, list):
        raw_terms = [raw_terms]
    support_values: list[str] = []
    for field in ("direct_beneficiaries", "picks_and_shovels"):
        value = brief.get(field) or []
        if not isinstance(value, list):
            value = [value]
        support_values.extend(str(item) for item in value if item)
    support_text = " ".join(support_values)
    support_norm = _normalise(support_text)
    support_tokens = _mandate_tokens(support_text)
    support_concepts = _semantic_concepts(support_text)

    false_values = brief.get("false_positives") or []
    if not isinstance(false_values, list):
        false_values = [false_values]
    false_text = " ".join(str(item) for item in false_values if item)
    false_norm = _normalise(false_text)
    false_concepts = _semantic_concepts(false_text)
    theme_concepts = _semantic_concepts(theme)
    theme_pool_keys = _canonical_pool_keys(theme)
    macro_tech_pool_keys = _concept_pool_keys(_MACRO_TECH_CONCEPTS)
    theme_text = str(theme or "")
    macro_factor_theme = bool(
        _MACRO_FACTOR_THEME_RE.search(theme_text)
        or _CJK_MACRO_FACTOR_THEME_RE.search(theme_text)
    )

    valid: list[str] = []
    for raw_term in raw_terms:
        term = str(raw_term.get("term") if isinstance(raw_term, dict) else raw_term or "").strip()
        if not term:
            continue
        term_norm = _normalise(term)
        term_tokens = _mandate_tokens(term)
        term_concepts = _semantic_concepts(term)
        term_pool_keys = _canonical_pool_keys(term)
        if term_concepts & false_concepts:
            continue
        if (term_norm and false_norm and (
                _contains_phrase(false_norm, term_norm)
                or _contains_phrase(term_norm, false_norm))):
            continue
        # For macro factor themes, model-authored tech-family terms are the
        # original false-positive path. A trusted user theme can explicitly opt
        # into one; event-driven CHIPS/cyber/ecommerce themes remain unaffected.
        if (macro_factor_theme
                and (
                    term_concepts & _MACRO_TECH_CONCEPTS
                    or term_pool_keys & macro_tech_pool_keys
                )
                and not (
                    term_concepts & theme_concepts
                    or term_pool_keys & theme_pool_keys
                )):
            continue
        if (_GENERIC_FACTOR_ETF_TERM_RE.search(term)
                and not term_concepts & theme_concepts):
            continue
        overlap = term_tokens & support_tokens
        corroborated = bool(
            term_concepts & theme_concepts
            or (
                support_norm
                and (
                _contains_phrase(support_norm, term_norm)
                or any(_contains_phrase(term_norm, _normalise(value))
                       for value in support_values)
                or bool(term_concepts & support_concepts)
                or len(overlap) >= 2
                or bool(overlap & _MANDATE_HIGH_SIGNAL)
                )
            )
        )
        if corroborated:
            valid.append(term)
    return valid


def canonical_theme_evidence(
    theme: str,
    brief: dict | None,
    pools: Iterable[PoolMatch],
) -> CanonicalThemeEvidence:
    """Build one fail-closed canonical name set for downstream ETF evidence.

    Model-proposed terms enter only after causal validation. Routed pools add
    their documented labels and aliases. The raw theme itself is included only
    when it resolves to one of those routed canonical pools; arbitrary text is
    never promoted into an index-name guess.
    """
    validated_terms = tuple(_validated_etf_terms(brief, theme))
    matched_pools = tuple(pools)
    routed_pool_keys = tuple(dict.fromkeys(
        match.spec.key for match in matched_pools
    ))
    names: list[str] = list(validated_terms)
    if set(routed_pool_keys) & _canonical_pool_keys(theme):
        names.append(str(theme or "").strip())
    for match in matched_pools:
        names.extend((match.spec.label, *match.spec.aliases))

    unique_by_normalized: dict[str, str] = {}
    for name in names:
        raw_name = str(name or "").strip()
        normalized = _normalise(raw_name)
        if normalized:
            unique_by_normalized.setdefault(normalized, raw_name)
    return CanonicalThemeEvidence(
        validated_terms=validated_terms,
        routed_pool_keys=routed_pool_keys,
        exact_names=tuple(unique_by_normalized.values()),
        normalized_names=frozenset(unique_by_normalized),
    )


def _mandate_match(
    candidate: dict,
    brief: dict | None,
    theme: str = "",
    pools: Iterable[PoolMatch] | None = None,
    canonical_evidence: CanonicalThemeEvidence | None = None,
) -> tuple[bool, list[str]]:
    """Verify a mandate against validated terms and their canonical pool aliases.

    Pool routing is the normalization boundary: once ``AI`` (for example) maps
    to the artificial-intelligence pool, mandate corroboration may use that
    pool's full alias vocabulary instead of requiring the fund to repeat the
    original free-form phrase.
    """
    matched_pools = list(pools) if pools is not None else match_theme_pools(
        theme,
        brief,
        max_pools=len(THEME_POOLS),
        theme_direction="bullish",
    )
    evidence = canonical_evidence or canonical_theme_evidence(
        theme, brief, matched_pools,
    )
    raw_terms = list(evidence.exact_names)
    candidate_text = " ".join(str(candidate.get(key) or "") for key in (
        "name", "benchmark", "fund_strategy", "fund_category", "fund_niche",
        "fund_focus", "selection_criteria", "index_tracked",
    ))
    candidate_tokens = _mandate_tokens(candidate_text)
    candidate_norm = _normalise(candidate_text)
    candidate_concepts = _semantic_concepts(candidate_text)
    matched: list[str] = []
    for raw_term in raw_terms:
        term = str(raw_term or "").strip()
        tokens = _mandate_tokens(term)
        overlap = candidate_tokens & tokens
        concept_overlap = candidate_concepts & _semantic_concepts(term)
        term_norm = _normalise(term)
        exact_phrase = bool(
            term_norm and len(term_norm) >= 5 and _contains_phrase(candidate_norm, term_norm)
        )
        if (exact_phrase or concept_overlap or len(overlap) >= 2
                or bool(overlap & _MANDATE_HIGH_SIGNAL)):
            matched.append(term)
    return bool(matched), matched


def match_theme_pools(
    theme: str,
    brief: dict | None = None,
    article_title: str = "",
    *,
    max_pools: int = 4,
    theme_direction: str = "bullish",
) -> list[PoolMatch]:
    """Map text to curated pools with exact phrase matching, without an LLM."""
    _ = _theme_direction(theme_direction)
    pool_limit = max(0, max_pools)
    theme_pool_limit = pool_limit
    valid_terms = _validated_etf_terms(brief or {}, theme)
    sources = [
        ("theme", str(theme or ""), 3.0),
        *(("brief", term, 1.0) for term in valid_terms),
    ]
    matches: list[PoolMatch] = []
    for spec in THEME_POOLS:
        best: PoolMatch | None = None
        for source, raw_text, base in sources:
            text = _normalise(raw_text)
            source_pool_keys = _canonical_pool_keys(raw_text)
            concept_match = spec.key in source_pool_keys
            for alias in spec.aliases:
                phrase_match = _contains_phrase(text, alias)
                if not phrase_match:
                    continue
                # Do not let nested words in producer themes route spot assets
                # (gold miners -> gold, bitcoin miners -> spot bitcoin).
                if (spec.key in _DIRECT_ASSET_POOL_KEYS and source_pool_keys
                        and spec.key not in source_pool_keys):
                    continue
                specificity = min(len(_normalise(alias).split()) * 0.05, 0.25)
                candidate = PoolMatch(
                    spec, base + specificity, source,
                    str(raw_text) if concept_match and not phrase_match else alias,
                )
                if best is None or candidate.strength > best.strength:
                    best = candidate
            # A semantic/canonical match broadens recall but must not outrank a
            # more-specific literal alias. In particular, "AI memory" should
            # prefer the semiconductor pool over the broader one-word AI pool.
            if concept_match:
                candidate = PoolMatch(spec, base + 0.04, source, str(raw_text))
                if best is None or candidate.strength > best.strength:
                    best = candidate
        if best:
            matches.append(best)
    matches.sort(key=lambda m: (-m.strength, -len(_normalise(m.alias)), m.spec.key))
    selected = matches[:theme_pool_limit]
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
    exposure_type = str(stock.get("exposure_type") or "unclear")
    exposure = _EXPOSURE_FACTOR.get(exposure_type, 0.35)
    if exposure_type == "direct":
        default_specificity = "company_specific"
    elif exposure_type in {"supply_chain", "enabler", "beneficiary"}:
        default_specificity = "industry_specific"
    else:
        default_specificity = "none"
    specificity = _SPECIFICITY_FACTOR.get(
        str(stock.get("theme_specificity") or default_specificity), 0.0)
    materiality = _MATERIALITY_FACTOR.get(
        str(stock.get("materiality") or ("medium" if specificity else "unknown")), 0.0)
    evidence = _EVIDENCE_FACTOR.get(
        str(stock.get("evidence_strength") or ("derived" if specificity else "none")), 0.0)
    return rel * conf * exposure * specificity * materiality * evidence


def _eligible_holding(
    stock: dict, *, min_relevance: float, min_confidence: float
) -> bool:
    """Mirror the stock causal gate so weak components cannot aggregate upward."""
    relevance = _finite_float(stock.get("ai_relevance"))
    confidence = _finite_float(stock.get("confidence"))
    return bool(
        stock.get("relevance_status") == "scored"
        and relevance is not None and relevance >= min_relevance
        and confidence is not None and confidence >= min_confidence
        and stock.get("exposure_type")
        in {"direct", "enabler", "supply_chain", "beneficiary"}
        and stock.get("theme_specificity")
        in {"company_specific", "industry_specific"}
        and stock.get("materiality") in {"high", "medium"}
        and stock.get("evidence_strength") in {"explicit", "derived"}
        and stock.get("impact_channel") in {
            "revenue_demand", "input_cost_margin", "financing_sensitive_demand",
            "supply_chain_orders", "policy_or_regulatory",
        }
    )


_ALTERNATIVE_STRATEGY_RE = re.compile(
    r"(?:style\s+premia|market\s+neutral|long[\s/\-]*short|managed\s+futures|"
    r"buffer(?:ed)?|defined[\s\-]*outcome|\bhedged\b)",
    re.IGNORECASE,
)
_OPTION_INCOME_RE = re.compile(
    r"(?:yieldmax|\bweekly[\s_\-]*pay\b|"
    r"options?[\s\-]*income|covered[\s\-]*call|"
    r"premium[\s\-]*income|buy[\s\-]*write|"
    r"(?:daily|weekly|monthly)[\s\-]*income)",
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
_EXCHANGE_TRADED_NOTE_RE = re.compile(
    r"\b(?:etns?|exchange[\s_\-\u00a0\u2010-\u2015\u2212]*"
    r"traded[\s_\-\u00a0\u2010-\u2015\u2212]*notes?)\b",
    re.IGNORECASE,
)
_MARKET_CODE_RE = re.compile(r"\d+:[A-Za-z0-9.\-]+")

SINGLE_STOCK_LEVERAGED_LANE = "single_stock_leveraged"
MULTI_STOCK_BASKET_LANE = "multi_stock_basket"
DIRECT_ASSET_LANE = "direct_asset"
INELIGIBLE_LANE = "ineligible"

# Selection preference is independent of the public rank-based display score.
ETF_PREFERRED_EVIDENCE_MIN = 0.375

_LOW_LIQUIDITY_AUM = 25_000_000.0
_LOW_LIQUIDITY_TURNOVER = 1_000_000.0
_GENERIC_RELATED_SCAN_MAX = 100
_DIRECT_PROBE_SCAN_MAX = 1000
_CONCEPT_INDEX_SOURCE_SCAN_MAX = 100
_ORDINARY_BASKET_CANDIDATE_CAP = 500
_DISCOVERY_SOURCE_BUCKETS = (
    "generic_stock", "dedicated_direct", "curated_pool",
    "concept_index", "recovery",
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
        "name", "fund_strategy", "fund_category", "fund_niche",
        "fund_focus", "selection_criteria", "etf_type",
    ))
    if _ALTERNATIVE_STRATEGY_RE.search(strategy_text):
        return "alternative long/short strategy"
    if _OPTION_INCOME_RE.search(strategy_text):
        return "option-income strategy"
    if _EXCHANGE_TRADED_NOTE_RE.search(strategy_text):
        return "exchange-traded note"
    profile = _derivative_profile(candidate)
    if profile.conflict:
        return "conflicting direction/leverage metadata"
    if profile.leverage > 3.0:
        return f"leverage={profile.leverage:g}"

    # Discovery must retain both aligned long and inverse derivatives until the
    # exact benchmark metadata is available.  Direction-specific admission is
    # deliberately deferred to the explicit lane classifier below.
    if defer_ambiguous:
        return None

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
    for key in (
        "name", "aum", "leverage", "direction", "turnover", "expense_ratio",
        "benchmark", "benchmark_code", "index_etf_code", "selection_criteria",
        "fund_strategy", "fund_category", "fund_niche", "fund_focus",
        "index_tracked", "base_index_code", "asset_class", "security_class",
        "etf_type",
    ):
        if candidate.get(key) in (None, "") and row.get(key) not in (None, ""):
            candidate[key] = row[key]


def _exact_benchmark_code(candidate: dict) -> str | None:
    """Return a structured AInvest benchmark market code, never a name guess."""
    for key in (
        "benchmark_code", "benchmark_hq_code", "benchmark_market_code",
        "underlying_code",
    ):
        value = str(candidate.get(key) or "").strip()
        if _MARKET_CODE_RE.fullmatch(value):
            return value
    # Older clients exposed the benchmark code through ``benchmark`` itself.
    # Accept it only when it is already an exact market_code; free-form names
    # are intentionally not resolved by ticker substring.
    value = str(candidate.get("benchmark") or "").strip()
    return value if _MARKET_CODE_RE.fullmatch(value) else None


def _verified_single_stock_leverage(
    candidate: dict,
    selected_stock_codes: set[str],
    theme_direction: str,
) -> tuple[str | None, str | None]:
    """Verify a direction-aligned >1x-to-3x wrapper on one selected stock.

    Product names can help discovery, but eligibility requires exact structured
    benchmark, direction, and leverage metadata.  This keeps similarly named
    sector/index products out of the direct-selected-stock lane.
    """
    security_class = _normalise(candidate.get("security_class"))
    if security_class != "ce":
        return None, "security_class is not CE"
    benchmark_code = _exact_benchmark_code(candidate)
    if not benchmark_code or benchmark_code not in selected_stock_codes:
        return None, "benchmark is not an exact selected-stock market_code"

    direction = _structured_direction(candidate.get("direction"))
    raw_leverage = _finite_float(candidate.get("leverage"))
    if direction is None or raw_leverage is None:
        return None, "missing structured direction/leverage metadata"
    leverage = abs(raw_leverage)
    if not (1.0 < leverage <= 3.0):
        return None, f"single-stock leverage={leverage:g} outside (1, 3]"
    if direction == "long" and raw_leverage < 0:
        return None, "conflicting direction/leverage metadata"

    expected = "short" if _theme_direction(theme_direction) == "bearish" else "long"
    if direction != expected:
        return None, f"{direction} wrapper is not aligned with {theme_direction} theme"
    return benchmark_code, None


def _exact_concept_index_sources(
    quotes,
    evidence: CanonicalThemeEvidence | None,
) -> list[dict]:
    """Resolve canonical names to local concept indices by exact equality only."""
    if evidence is None or not evidence.normalized_names:
        return []
    names = getattr(quotes, "names", None)
    if not hasattr(names, "items"):
        return []
    evidence_by_name: dict[str, list[str]] = {}
    for exact_name in evidence.exact_names:
        normalized = _normalise(exact_name)
        if normalized:
            evidence_by_name.setdefault(normalized, []).append(exact_name)

    sources: list[dict] = []
    for raw_code, raw_name in names.items():
        index_code = str(raw_code or "").strip()
        normalized_name = _normalise(raw_name)
        if (not index_code.startswith("89:")
                or normalized_name not in evidence.normalized_names):
            continue
        sources.append({
            "index_code": index_code,
            "index_name": str(raw_name or "").strip(),
            "matched_evidence_names": tuple(
                evidence_by_name.get(normalized_name, ())),
        })
    sources.sort(key=lambda source: (
        _normalise(source["index_name"]), source["index_code"],
    ))
    return sources


def _discover(
    quotes,
    stocks: list[dict],
    pools: list[PoolMatch],
    limit: int,
    failures: list[str],
    theme_direction: str = "bullish",
    exclusion_reasons: dict[str, int] | None = None,
    exclusion_examples: dict[str, list[str]] | None = None,
    canonical_evidence: CanonicalThemeEvidence | None = None,
    canonical_diagnostics: dict[str, object] | None = None,
    source_counts: dict[str, dict[str, int]] | None = None,
) -> tuple[dict[str, dict], int]:
    """Discover bounded stock-derived and curated ETF candidates.

    Selected-stock relations are always scanned independently of catalog
    matches.  A small priority probe lane retains derivative rows (including
    wrappers with no physical holding weight) before the global unique cap is
    filled; the remaining related and pool rows are consumed round-robin.
    """
    ordinary_limit = min(limit, _ORDINARY_BASKET_CANDIDATE_CAP)
    if source_counts is not None:
        for bucket in _DISCOVERY_SOURCE_BUCKETS:
            source_counts.setdefault(bucket, {"raw": 0, "retained": 0})

    def add_source_count(bucket: str, field_name: str, amount: int = 1) -> None:
        if source_counts is None or amount <= 0:
            return
        stats = source_counts.setdefault(bucket, {"raw": 0, "retained": 0})
        stats[field_name] = stats.get(field_name, 0) + amount

    concept_index_sources = _exact_concept_index_sources(
        quotes, canonical_evidence,
    )
    if canonical_diagnostics is not None:
        canonical_diagnostics["concept_indices"] = [
            {
                "index_code": source["index_code"],
                "index_name": source["index_name"],
                "matched_evidence_names": list(
                    source.get("matched_evidence_names", ())),
            }
            for source in concept_index_sources
        ]
    concept_index_iterator = getattr(quotes, "iter_concept_index_etfs", None)
    if canonical_evidence and canonical_evidence.normalized_names:
        if not concept_index_sources:
            failures.append("concept-index-source: no exact 89: name match")
        elif not callable(concept_index_iterator):
            failures.append("concept-index-source: client method unavailable")
            concept_index_sources = []
        else:
            failures.append(
                f"concept-index-source: exact_matches={len(concept_index_sources)}")

    source_count = len(stocks) + len(pools) + len(concept_index_sources)
    if source_count == 0:
        return {}, 0
    page_size = min(
        1000, max(20, math.ceil(ordinary_limit / source_count) * 2))
    active: deque = deque()
    has_public_flags = any("is_public_theme_stock" in stock for stock in stocks)
    stock_source_failed = False

    def is_public_stock(stock: dict) -> bool:
        return bool(stock.get("is_public_theme_stock")) if has_public_flags else True

    # Read each selected-stock relation deeply enough to reach single-stock
    # products whose holding weight is null and therefore sorts after ordinary
    # physical holders. The raw scan is bounded separately from retained unique
    # candidates, so it cannot make the final candidate universe unbounded.
    stock_rows: list[tuple[dict, list[dict], list[dict]]] = []
    for stock in stocks:
        code = stock["code"]
        try:
            if hasattr(quotes, "iter_stock_etfs"):
                iterator = quotes.iter_stock_etfs(
                    code, relation="related", page_size=_GENERIC_RELATED_SCAN_MAX)
            else:
                iterator = quotes.iter_related_etfs(
                    code, page_size=_GENERIC_RELATED_SCAN_MAX)
            rows = list(islice(
                iterator, _GENERIC_RELATED_SCAN_MAX,
            ))
        except RuntimeError as exc:
            stock_source_failed = True
            failures.append(f"stock:{code}: {exc}")
            rows = []
        add_source_count("generic_stock", "raw", len(rows))

        dedicated_rows: list[dict] = []
        if not is_public_stock(stock):
            dedicated_rows = []
        elif hasattr(quotes, "iter_stock_etfs"):
            dedicated_relations = (
                ("inverse", "leveraged")
                if _theme_direction(theme_direction) == "bearish"
                else ("leveraged", "long")
            )
            dedicated_by_code: dict[str, dict] = {}
            for dedicated_relation in dedicated_relations:
                try:
                    relation_rows = list(islice(
                        quotes.iter_stock_etfs(
                            code, relation=dedicated_relation,
                            page_size=_DIRECT_PROBE_SCAN_MAX,
                        ),
                        _DIRECT_PROBE_SCAN_MAX,
                    ))
                except RuntimeError as exc:
                    stock_source_failed = True
                    failures.append(
                        f"stock-{dedicated_relation}:{code}: {exc}")
                    continue
                add_source_count(
                    "dedicated_direct", "raw", len(relation_rows))
                for relation_row in relation_rows:
                    relation_code = str(relation_row.get("code") or "").strip()
                    if not relation_code:
                        continue
                    merged = dedicated_by_code.setdefault(
                        relation_code, {"code": relation_code})
                    _merge_row(merged, relation_row)
                    merged.setdefault("stock_relation_kinds", {})[
                        dedicated_relation] = True
                    merged["stock_relation_kind"] = dedicated_relation
            dedicated_rows = list(dedicated_by_code.values())
        else:
            # Compatibility clients have only the generic relation. Exact
            # benchmark verification still happens after metadata enrichment.
            dedicated_rows = rows
        stock_rows.append((stock, rows, dedicated_rows))

    for pool in pools:
        iterator = islice(
            quotes.iter_prompt_etfs(
                pool.spec.prompt_id, page_size=page_size), ordinary_limit)
        active.append(("pool", pool.spec.key, pool, iterator))
    for source in concept_index_sources:
        try:
            iterator = islice(
                concept_index_iterator(
                    source["index_code"],
                    page_size=_CONCEPT_INDEX_SOURCE_SCAN_MAX,
                ),
                _CONCEPT_INDEX_SOURCE_SCAN_MAX,
            )
        except RuntimeError as exc:
            failures.append(
                f"concept-index:{source['index_code']}: {exc}")
            continue
        active.append((
            "concept_index", source["index_code"], source, iterator,
        ))
    for stock, rows, _ in stock_rows:
        active.append(("stock", stock["code"], stock, iter(rows)))

    candidates: dict[str, dict] = {}
    excluded = 0

    def admit(
        kind: str,
        key: str,
        context: object,
        row: dict,
        *,
        source_bucket: str,
    ) -> None:
        nonlocal excluded
        code = str(row.get("code") or "").strip()
        if not code:
            return
        probe = dict(row)
        probe["name"] = probe.get("name") or quotes.name_of(code)
        hard_reason = _hard_filter_reason(
            probe, theme_direction, defer_ambiguous=True)
        if hard_reason:
            excluded += 1
            if exclusion_reasons is not None:
                exclusion_reasons[hard_reason] = (
                    exclusion_reasons.get(hard_reason, 0) + 1)
            if exclusion_examples is not None:
                example = f"{code} {probe['name']}".strip()
                examples = exclusion_examples.setdefault(hard_reason, [])
                if example not in examples and len(examples) < 3:
                    examples.append(example)
            return
        candidate = candidates.setdefault(code, {
            "code": code,
            "name": probe["name"],
            "anchor_weights": {},
            "related_stock_codes": {},
            "pool_matches": {},
            "concept_index_matches": {},
            "source_order": len(candidates) + 1,
        })
        _merge_row(candidate, probe)
        if kind == "stock":
            candidate["related_stock_codes"][key] = True
            relation_kind = str(row.get("stock_relation_kind") or "related")
            candidate.setdefault("stock_relation_kinds", {})[
                relation_kind] = True
            weight = _finite_float(row.get("holding_weight"))
            if weight is not None and weight > 0:
                candidate["anchor_weights"][key] = weight
        elif kind == "pool":
            candidate["pool_matches"][context.spec.key] = context
        else:
            candidate["concept_index_matches"][key] = {
                "index_code": context["index_code"],
                "index_name": context["index_name"],
                "matched_evidence_names": list(
                    context.get("matched_evidence_names", ())),
                "block_etf_holdrate": row.get("block_etf_holdrate"),
                "block_etf_risekline": row.get("block_etf_risekline"),
            }
        add_source_count(source_bucket, "retained")

    # Exact direction-aligned direct probes live outside the ordinary retained-
    # candidate cap. Dedicated relations surface wrappers with null physical
    # weights without allowing broad leveraged products to consume these slots.
    priority = deque()
    recorded_probe_exclusions: set[tuple[str, str, str]] = set()
    for stock, generic_rows, dedicated_rows in stock_rows:
        derivative_rows = []
        seen_derivative_codes: set[str] = set()
        stock_ticker = str(stock.get("code") or "").partition(":")[2]
        stock_name = _normalise(stock.get("name"))
        # Generic rows can occasionally expose an exact wrapper before the
        # dedicated relation index catches up. Include those already-fetched
        # rows in the same additive probe lane and avoid an unnecessary global
        # recovery scan when their structured metadata is sufficient.
        probe_rows = (
            [(row, True) for row in dedicated_rows]
            + [(row, False) for row in generic_rows]
            if is_public_stock(stock) else []
        )
        for row, from_dedicated_source in probe_rows:
            underlying_code, verification_reason = _verified_single_stock_leverage(
                row, {stock["code"]}, theme_direction)
            row_code = str(row.get("code") or "").strip()
            if underlying_code and row_code not in seen_derivative_codes:
                seen_derivative_codes.add(row_code)
                derivative_rows.append(row)
            elif (
                row_code
                and (
                    from_dedicated_source
                    or _exact_benchmark_code(row) == stock["code"]
                )
                and verification_reason
            ):
                diagnostic_key = (
                    stock["code"], row_code, verification_reason,
                )
                if diagnostic_key in recorded_probe_exclusions:
                    continue
                recorded_probe_exclusions.add(diagnostic_key)
                excluded += 1
                if exclusion_reasons is not None:
                    exclusion_reasons[verification_reason] = (
                        exclusion_reasons.get(verification_reason, 0) + 1)
                if exclusion_examples is not None:
                    example = f"{row_code} {row.get('name') or row_code}".strip()
                    examples = exclusion_examples.setdefault(
                        verification_reason, [])
                    if example not in examples and len(examples) < 3:
                        examples.append(example)
        if derivative_rows:
            # Null physical weight and an underlying token in the product name
            # are discovery hints only. They bring likely wrappers forward for
            # metadata enrichment; exact benchmark metadata still owns final
            # eligibility in ``_verified_single_stock_leverage``.
            def derivative_probe_order(row: dict) -> tuple:
                name = _normalise(row.get("name"))
                exact_benchmark = _exact_benchmark_code(row) == stock["code"]
                no_physical_weight = _finite_float(row.get("holding_weight")) is None
                named_underlying = bool(
                    (stock_ticker and _contains_phrase(name, stock_ticker))
                    or (len(stock_name) >= 4 and _contains_phrase(name, stock_name))
                )
                return (
                    -int(exact_benchmark), -int(no_physical_weight),
                    -int(named_underlying),
                    -(_finite_float(row.get("aum")) or 0.0),
                    str(row.get("code") or ""),
                )

            derivative_rows.sort(key=derivative_probe_order)
            priority.append((stock["code"], stock, iter(derivative_rows)))

    while priority:
        key, stock, iterator = priority.popleft()
        try:
            row = next(iterator)
        except StopIteration:
            continue
        admit(
            "stock", key, stock, row,
            source_bucket="dedicated_direct",
        )
        priority.append((key, stock, iterator))

    # Dedicated relations are the fast path, but availability differs across
    # deployments and newly listed wrappers can lag a relation index. Recover
    # once from the live ETF universe when any selected-stock source failed or
    # when no exact wrapper was verified at all. The recovery stays outside the
    # ordinary basket cap and still fails closed on structured CE, benchmark,
    # direction, and leverage metadata.
    public_stocks_by_code = {
        stock["code"]: stock for stock in stocks if is_public_stock(stock)
    }
    public_stock_codes = set(public_stocks_by_code)
    verified_direct_codes = {
        underlying
        for candidate in candidates.values()
        for underlying, _ in [
            _verified_single_stock_leverage(
                candidate, public_stock_codes, theme_direction)
        ]
        if underlying
    }
    needs_live_recovery = bool(public_stock_codes) and (
        stock_source_failed or not verified_direct_codes
    )
    if needs_live_recovery:
        recovery_codes: list[str] = []
        recovery_source = ""
        live_codes = getattr(quotes, "live_etf_codes", None)
        if callable(live_codes):
            try:
                recovery_codes = list(live_codes(
                    page_size=_DIRECT_PROBE_SCAN_MAX))
            except RuntimeError as exc:
                failures.append(f"direct-recovery-live-universe: {exc}")
            if recovery_codes:
                recovery_source = "live_etf_codes"
            else:
                failures.append("direct-recovery-live-universe: empty")

        # The local CE reference is deliberately only a live-universe fallback,
        # not a second scan after a successful live response.
        if not recovery_codes:
            offline_codes = getattr(quotes, "security_codes", None)
            if callable(offline_codes):
                recovery_codes = list(offline_codes("CE"))
                if recovery_codes:
                    recovery_source = "security_codes(CE)"

        matched_recovery = 0
        if recovery_codes:
            add_source_count("recovery", "raw", len(recovery_codes))
            try:
                recovery_metadata = quotes.etf_metadata(recovery_codes)
            except RuntimeError as exc:
                failures.append(f"direct-recovery-metadata: {exc}")
                recovery_metadata = {}
            for recovery_code, values in recovery_metadata.items():
                row = {"code": recovery_code, **dict(values or {})}
                underlying, _ = _verified_single_stock_leverage(
                    row, public_stock_codes, theme_direction)
                if not underlying:
                    continue
                row["stock_relation_kind"] = "live_universe_recovery"
                admit(
                    "stock",
                    underlying,
                    public_stocks_by_code[underlying],
                    row,
                    source_bucket="recovery",
                )
                matched_recovery += 1
            failures.append(
                f"direct-recovery-source:{recovery_source}: "
                f"scanned={len(recovery_codes)} matched={matched_recovery}"
            )
        else:
            failures.append("direct-recovery-source: unavailable")

    ordinary_candidate_target = len(candidates) + ordinary_limit
    while active and len(candidates) < ordinary_candidate_target:
        kind, key, context, iterator = active.popleft()
        try:
            row = next(iterator)
        except StopIteration:
            continue
        except RuntimeError as exc:
            failures.append(f"{kind}:{key}: {exc}")
            continue
        source_bucket = (
            "generic_stock" if kind == "stock" else
            "curated_pool" if kind == "pool" else
            "concept_index"
        )
        if kind != "stock":
            add_source_count(source_bucket, "raw")
        admit(
            kind, key, context, row, source_bucket=source_bucket,
        )
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
    benchmark = _exact_benchmark_code(candidate)
    selected = {str(stock.get("code") or "").strip() for stock in stocks}
    return [benchmark] if benchmark and benchmark in selected else []


def _concept_direct_asset_pool_keys(candidate: dict) -> set[str]:
    """Map exact concept-index provenance back to canonical direct-asset pools."""
    keys: set[str] = set()
    for match in (candidate.get("concept_index_matches", {}) or {}).values():
        if not isinstance(match, dict):
            continue
        evidence_names = match.get("matched_evidence_names") or []
        if isinstance(evidence_names, str):
            evidence_names = [evidence_names]
        for value in [match.get("index_name"), *evidence_names]:
            keys.update(_canonical_pool_keys(value) & _DIRECT_ASSET_POOL_KEYS)
    return keys


def _percentile_ranks(values: list[object], *, inverse: bool = False) -> list[float]:
    """Tie-aware percentiles; missing and single-distinct samples score zero."""
    numeric = [_finite_float(value) for value in values]
    indices = [index for index, value in enumerate(numeric) if value is not None]
    out = [0.0] * len(values)
    if not indices or len({numeric[index] for index in indices}) < 2:
        return out
    order = sorted(
        indices,
        key=lambda index: -numeric[index] if inverse else numeric[index],
    )
    denominator = len(order) - 1
    start = 0
    while start < len(order):
        end = start
        while (
            end + 1 < len(order)
            and numeric[order[end + 1]] == numeric[order[start]]
        ):
            end += 1
        percentile = ((start + end) / 2.0) / denominator
        for position in range(start, end + 1):
            out[order[position]] = percentile
        start = end + 1
    return out


def _ranking_key(candidate: dict) -> tuple:
    """Approved unified ranking with deterministic investability tie-breaks."""
    turnover = _finite_float(candidate.get("turnover"))
    aum = _finite_float(candidate.get("aum"))
    expense = _finite_float(candidate.get("expense_ratio"))
    return (
        -int(bool(candidate.get("output_eligible"))),
        -float(candidate.get("unified_score") or 0.0),
        -float(candidate.get("theme_evidence_score") or 0.0),
        -(turnover if turnover is not None else -1.0),
        -(aum if aum is not None else -1.0),
        expense if expense is not None else math.inf,
        str(candidate.get("code") or ""),
    )


def apply_unified_etf_scores(
    candidates: list[dict], *, include_pending_baskets: bool = False,
) -> list[dict]:
    """Attach approved investability and unified ranking scores in place.

    Missing turnover, AUM, or expense values receive percentile zero. Low
    liquidity is a soft diagnostic and ranking consequence, never an exclusion.
    """
    scoreable = [
        candidate for candidate in candidates
        if candidate.get("output_eligible")
        or (
            include_pending_baskets
            and candidate.get("selection_lane") == MULTI_STOCK_BASKET_LANE
            and candidate.get("ranking_mode") == "pending_full_holdings"
        )
    ]
    turnover_pct = _percentile_ranks([
        candidate.get("turnover") for candidate in scoreable
    ])
    aum_pct = _percentile_ranks([
        candidate.get("aum") for candidate in scoreable
    ])
    inverse_expense_pct = _percentile_ranks([
        candidate.get("expense_ratio") for candidate in scoreable
    ], inverse=True)

    scoreable_ids = {id(candidate) for candidate in scoreable}
    for index, candidate in enumerate(scoreable):
        investability = (
            0.50 * turnover_pct[index]
            + 0.35 * aum_pct[index]
            + 0.15 * inverse_expense_pct[index]
        )
        evidence = max(0.0, min(
            1.0, float(candidate.get("theme_evidence_score") or 0.0)))
        direct_bonus = 1.0 if candidate.get(
            "selection_lane") == SINGLE_STOCK_LEVERAGED_LANE else 0.0
        candidate.update({
            "turnover_percentile": turnover_pct[index],
            "aum_percentile": aum_pct[index],
            "inverse_expense_percentile": inverse_expense_pct[index],
            "investability_score": investability,
            "exact_selected_stock_direct_bonus": direct_bonus,
            "unified_score": max(0.0, min(
                1.0,
                0.80 * evidence + 0.10 * investability + 0.10 * direct_bonus,
            )),
        })

    for candidate in candidates:
        if id(candidate) not in scoreable_ids:
            candidate.update({
                "turnover_percentile": 0.0,
                "aum_percentile": 0.0,
                "inverse_expense_percentile": 0.0,
                "investability_score": 0.0,
                "exact_selected_stock_direct_bonus": 0.0,
                "unified_score": 0.0,
            })
        aum = _finite_float(candidate.get("aum"))
        turnover = _finite_float(candidate.get("turnover"))
        candidate["low_liquidity"] = bool(
            (aum is not None and aum < _LOW_LIQUIDITY_AUM)
            or (turnover is not None and turnover < _LOW_LIQUIDITY_TURNOVER)
        )
        low_liquidity_reasons = []
        if aum is not None and aum < _LOW_LIQUIDITY_AUM:
            low_liquidity_reasons.append("aum_below_25m")
        if turnover is not None and turnover < _LOW_LIQUIDITY_TURNOVER:
            low_liquidity_reasons.append("turnover_below_1m")
        candidate["low_liquidity_reasons"] = low_liquidity_reasons
        candidate["liquidity_data_missing"] = aum is None or turnover is None
        candidate["preselect_score"] = candidate["unified_score"]
    candidates.sort(key=_ranking_key)
    return candidates


def _rank_candidates(
    candidates: dict[str, dict],
    stocks: list[dict],
    limit: int,
    theme_direction: str = "bullish",
) -> tuple[list[dict], int]:
    """Classify candidates into explicit direct, basket, and asset lanes."""
    direction_mode = _theme_direction(theme_direction)
    stock_by_code = {stock["code"]: stock for stock in stocks}
    quality = {code: _stock_quality(stock) for code, stock in stock_by_code.items()}
    has_public_flags = any("is_public_theme_stock" in stock for stock in stocks)
    public_stock_codes = {
        stock["code"] for stock in stocks
        if (bool(stock.get("is_public_theme_stock")) if has_public_flags else True)
    }
    ranked: list[dict] = []
    excluded = 0

    for candidate in candidates.values():
        # Product-wide risk filters run here, while lane classification below
        # owns direction and leverage alignment.
        hard_reason = _hard_filter_reason(
            candidate, direction_mode, defer_ambiguous=True)
        if hard_reason:
            candidate.update({
                "selection_lane": INELIGIBLE_LANE,
                "output_eligible": False,
                "exclusion_reason": hard_reason,
            })
            excluded += 1
            continue
        security_class = _normalise(candidate.get("security_class"))
        if security_class and security_class != "ce":
            candidate.update({
                "selection_lane": INELIGIBLE_LANE,
                "output_eligible": False,
                "exclusion_reason": "security_class is not CE",
            })
            excluded += 1
            continue
        derivative = _derivative_profile(candidate)
        # Preserve the original structured fields for lane admission. The
        # derivative profile may infer presentation defaults from a name, but a
        # missing direction/leverage response must not become synthetic Long/1x
        # evidence for a conventional basket or direct-asset product.
        structured_direction = _structured_direction(candidate.get("direction"))
        structured_leverage = _finite_float(candidate.get("leverage"))
        # Verify exact wrappers before the presentation fields below normalize
        # direction/leverage. Name-inferred defaults must never masquerade as
        # the structured metadata required by the direct-wrapper lane.
        underlying_code, wrapper_reason = _verified_single_stock_leverage(
            candidate, public_stock_codes, direction_mode)
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
        concept_index_matches = candidate.get("concept_index_matches", {})
        concept_index_labels = sorted({
            str(match.get("index_name") or "").strip()
            for match in concept_index_matches.values()
            if str(match.get("index_name") or "").strip()
        })
        related_codes = set(candidate.get("related_stock_codes", {})) | set(
            candidate.get("anchor_weights", {}))
        related_codes &= set(stock_by_code)
        benchmark_codes = set(_benchmark_stock_matches(candidate, stocks))
        pool_strength = min(max((m.strength for m in pool_matches), default=0.0) / 3.25, 1.0)
        selected_stock_coverage = max(
            0.0, min(1.0, weighted_exposure / 100.0))
        breadth_factor = 1.0 - math.exp(-breadth / 5.0)
        mandate_corroboration = 1.0 if candidate.get("mandate_verified") else 0.0
        normalised_leverage = max(0.0, min(1.0, (derivative.leverage - 1.0) / 2.0))
        aum = _finite_float(candidate.get("aum"))
        common = {
            "matched_holdings": holdings,
            "theme_weight_pct": raw_weight,
            "weighted_theme_exposure_pct": weighted_exposure,
            "theme_breadth": breadth,
            "pool_labels": sorted(m.spec.label for m in pool_matches),
            "concept_index_labels": concept_index_labels,
            "pool_strength": pool_strength,
            "direction": "Short" if derivative.direction == "short" else "Long",
            "leverage": derivative.leverage,
            "direction_source": derivative.direction_source,
            "leverage_source": derivative.leverage_source,
            "benchmark_match_codes": sorted(benchmark_codes),
            "normalised_leverage": normalised_leverage,
            "selected_stock_coverage": selected_stock_coverage,
            "mandate_corroboration": mandate_corroboration,
            "metric": aum,
            "aum": aum,
            "relevance_status": "deterministic",
        }
        candidate.update(common)

        if underlying_code:
            evidence_score = max(0.0, min(1.0, quality.get(underlying_code, 0.0)))
            inverse = derivative.direction == "short"
            candidate.update({
                "selection_lane": SINGLE_STOCK_LEVERAGED_LANE,
                "ranking_mode": SINGLE_STOCK_LEVERAGED_LANE,
                "underlying_code": underlying_code,
                # Stock-related ratios on derivative products can be effective
                # exposure rather than physical portfolio weight. The exact
                # benchmark owns this lane, so do not present those ratios as
                # ordinary fund holdings.
                "matched_holdings": [],
                "theme_weight_pct": 0.0,
                "weighted_theme_exposure_pct": 0.0,
                "theme_breadth": 0,
                "theme_evidence_score": evidence_score,
                "base_theme_exposure": evidence_score,
                "static_theme_exposure": evidence_score,
                "output_eligible": True,
                "is_inverse": inverse,
                "verified_inverse": inverse,
                "single_stock_inverse": inverse,
                "single_stock_leveraged": True,
                "inverse_evidence": (
                    ["selected_stock_benchmark"] if inverse else []),
                "ai_relevance": round(1.0 + 4.0 * evidence_score, 1),
                "confidence": 0.98,
                "exposure_type": "direct",
                "reason": (
                    f"Targets {derivative.leverage:g}x "
                    f"{'inverse ' if inverse else ''}exposure to "
                    f"{underlying_code.partition(':')[2]}."
                ),
            })
            ranked.append(candidate)
            continue

        pool_direct_asset_keys = pool_keys & _DIRECT_ASSET_POOL_KEYS
        concept_direct_asset_keys = _concept_direct_asset_pool_keys(candidate)
        direct_asset_source_keys = pool_direct_asset_keys | concept_direct_asset_keys
        direct_asset = bool(direct_asset_source_keys)
        raw_direction = structured_direction
        raw_leverage = structured_leverage
        plain_long = bool(
            raw_direction == "long" and raw_leverage is not None
            and math.isclose(abs(raw_leverage), 1.0,
                             rel_tol=0.0, abs_tol=1e-9)
        )
        expected_direction = "short" if direction_mode == "bearish" else "long"
        aligned_direct_asset = bool(
            raw_direction == expected_direction and raw_leverage is not None
            and 1.0 <= abs(raw_leverage) <= 3.0
        )
        if direct_asset and aligned_direct_asset:
            source_specificity = max(
                pool_strength,
                1.0 if concept_direct_asset_keys else 0.0,
            )
            evidence_score = max(
                0.0, min(1.0, 0.60 + 0.20 * source_specificity))
            output_eligible = bool(
                candidate.get("mandate_verified") and evidence_score >= 0.25)
            inverse = raw_direction == "short"
            source_labels = sorted(set(
                candidate["pool_labels"] + concept_index_labels
            ))
            candidate.update({
                "selection_lane": DIRECT_ASSET_LANE,
                "ranking_mode": DIRECT_ASSET_LANE,
                "direct_asset_source_keys": sorted(direct_asset_source_keys),
                "direct_asset_source_labels": source_labels,
                "theme_evidence_score": evidence_score,
                "base_theme_exposure": evidence_score,
                "static_theme_exposure": evidence_score,
                "output_eligible": output_eligible,
                "is_inverse": inverse,
                "verified_inverse": inverse,
                "single_stock_inverse": False,
                "single_stock_leveraged": False,
                "ai_relevance": round(1.0 + 4.0 * evidence_score, 1),
                "confidence": 0.90,
                "exposure_type": "direct",
                "reason": (
                    f"Direction-aligned member of "
                    f"{', '.join(source_labels)}."
                ),
            })
            ranked.append(candidate)
            continue

        selection_text = _normalise(" ".join(str(candidate.get(key) or "") for key in (
            "selection_criteria", "fund_category", "fund_niche")))
        single_asset_metadata = bool(
            "single asset" in selection_text or "single stock" in selection_text)
        exact_selected_benchmark = _exact_benchmark_code(candidate) in stock_by_code
        basket_source_evidence = bool(
            weighted_exposure > 0 or pool_matches or related_codes
            or concept_index_matches)
        plain_equity_basket = bool(
            plain_long
            and not single_asset_metadata and not exact_selected_benchmark
            and basket_source_evidence
        )
        if plain_equity_basket:
            # Provisional evidence is used only to prioritize the bounded full-
            # holdings assessment. The final approved formula replaces it after
            # every component is scored.
            evidence_score = max(0.0, min(
                1.0,
                0.70 * selected_stock_coverage
                + 0.15 * breadth_factor
                + 0.10 * selected_stock_coverage
                + 0.05 * mandate_corroboration,
            ))
            candidate.update({
                "selection_lane": MULTI_STOCK_BASKET_LANE,
                "ranking_mode": "pending_full_holdings",
                "theme_evidence_score": evidence_score,
                "base_theme_exposure": evidence_score,
                "static_theme_exposure": evidence_score,
                "output_eligible": False,
                "is_inverse": False,
                "verified_inverse": False,
                "single_stock_inverse": False,
                "single_stock_leveraged": False,
                "bearish_downside_exposure": direction_mode == "bearish",
                "ai_relevance": round(1.0 + 4.0 * evidence_score, 1),
                "confidence": 0.80 if selected_stock_coverage > 0 else 0.70,
                "exposure_type": (
                    "beneficiary" if selected_stock_coverage > 0 else "diversified"),
                "reason": (
                    (
                        f"Conventional long basket links to {breadth} selected "
                        "theme companies that are vulnerable under the bearish theme."
                    )
                    if direction_mode == "bearish" and breadth else
                    f"Candidate basket links to {breadth} selected theme companies."
                    if breadth else
                    (
                        "Conventional long basket is exposed to downside in "
                        f"{', '.join(candidate['pool_labels'] or concept_index_labels)}."
                    )
                    if direction_mode == "bearish" else
                    f"Candidate from {', '.join(candidate['pool_labels'] or concept_index_labels)}."
                ),
            })
            ranked.append(candidate)
            continue

        asset_class = _normalise(candidate.get("asset_class"))
        conventional_equity = bool(
            not direct_asset
            and not single_asset_metadata
            and not exact_selected_benchmark
            and (
                not asset_class
                or any(token in asset_class for token in (
                    "equity", "stock", "index", "allocation",
                ))
            )
        )
        if direct_asset:
            if raw_direction is None or raw_leverage is None:
                exclusion_reason = (
                    "missing structured direction/leverage metadata for direct asset"
                )
            else:
                exclusion_reason = (
                    "direct-asset direction/leverage is not aligned with theme"
                )
        elif conventional_equity:
            if raw_direction is None or raw_leverage is None:
                exclusion_reason = (
                    "missing structured direction/leverage metadata for equity basket"
                )
            elif direction_mode == "bearish":
                exclusion_reason = (
                    "non-exact inverse/leveraged baskets are not admitted for bearish themes"
                )
            elif raw_direction != "long":
                exclusion_reason = (
                    "inverse/short equity baskets are not admitted for bullish themes"
                )
            else:
                exclusion_reason = (
                    "leveraged multi-stock baskets are not admitted"
                )
        else:
            exclusion_reason = (
                wrapper_reason or "does not meet an approved ETF lane")
        candidate.update({
            "selection_lane": INELIGIBLE_LANE,
            "ranking_mode": INELIGIBLE_LANE,
            "output_eligible": False,
            "exclusion_reason": exclusion_reason,
        })
        excluded += 1

    apply_unified_etf_scores(ranked, include_pending_baskets=True)
    for rank, candidate in enumerate(ranked, 1):
        candidate["rank"] = rank
    # Ordinary basket/pool/concept-index retention is capped at 500 in
    # discovery. Verified direct probes remain additive so no ordinary source
    # can evict an exact selected-stock wrapper.
    return ranked, excluded


def requires_component_holdings(candidate: dict) -> bool:
    """Whether physical company holdings are meaningful ranking evidence."""
    lane = candidate.get("selection_lane")
    if lane:
        return lane == MULTI_STOCK_BASKET_LANE
    # Compatibility for callers constructing legacy candidate dictionaries.
    if candidate.get("verified_inverse") or candidate.get("is_inverse"):
        return False
    pool_keys = set(candidate.get("pool_matches", {}))
    if pool_keys & _DIRECT_ASSET_POOL_KEYS:
        return False
    asset_class = _normalise(candidate.get("asset_class"))
    if asset_class and not any(token in asset_class for token in (
            "equity", "stock", "index", "allocation")):
        return False
    return True


def build_holdings_shortlist(
    candidates: list[dict], etf_target: int, *, multiplier: int = 4, minimum: int = 20
) -> list[dict]:
    """Combine legacy leaders with a curated-pool diversity lane."""
    if etf_target <= 0 or not candidates:
        return []
    shortlist_size = min(len(candidates), max(multiplier * etf_target, minimum))
    lead_count = min(shortlist_size, 3 * etf_target)
    selected = list(candidates[:lead_count])
    seen = {candidate["code"] for candidate in selected}

    pool_order: list[str] = []
    for candidate in candidates:
        for key in candidate.get("pool_matches", {}):
            if key not in pool_order:
                pool_order.append(key)
    pool_lane = min(etf_target, shortlist_size - len(selected))
    while pool_lane > 0:
        added = False
        for key in pool_order:
            candidate = next((item for item in candidates
                              if item["code"] not in seen
                              and key in item.get("pool_matches", {})), None)
            if candidate is None:
                continue
            selected.append(candidate)
            seen.add(candidate["code"])
            pool_lane -= 1
            added = True
            if pool_lane == 0 or len(selected) == shortlist_size:
                break
        if not added:
            break

    for candidate in candidates:
        if len(selected) >= shortlist_size:
            break
        if candidate["code"] not in seen:
            selected.append(candidate)
            seen.add(candidate["code"])
    return selected


def rerank_with_component_holdings(
    candidates: list[dict],
    holdings_by_etf: dict[str, list[dict]],
    relevance_by_code: dict[str, dict],
    *,
    theme_direction: str = "bullish",
    min_weight_coverage: float = 60.0,
    min_scored_share: float = 0.80,
    min_output_score: float = 0.25,
    min_holding_relevance: float = 3.3,
    min_holding_confidence: float = 0.55,
) -> list[dict]:
    """Rerank assessed equity ETFs by whole-portfolio thematic mass.

    Equity baskets need at least the minimum reported holdings coverage. When
    semantic coverage is below its target, unresolved holdings count as zero
    exposure and the scored subset must independently clear both eligibility
    gates. Exact selected-stock wrappers and canonical direct-asset funds retain
    their deterministic evidence and never enter the physical-holdings gate.
    """
    _ = _theme_direction(theme_direction)  # lane direction was fixed upstream
    assessed_codes = set(holdings_by_etf)
    for candidate in candidates:
        candidate.setdefault("legacy_anchor_score", candidate["base_theme_exposure"])
        needs_holdings = requires_component_holdings(candidate)
        if not needs_holdings:
            pool_keys = set(candidate.get("pool_matches", {}))
            direct_asset_source_keys = (
                pool_keys
                | set(candidate.get("direct_asset_source_keys", []))
            ) & _DIRECT_ASSET_POOL_KEYS
            independently_verified = bool(
                candidate.get("selection_lane") == SINGLE_STOCK_LEVERAGED_LANE
                or (
                    candidate.get("selection_lane") == DIRECT_ASSET_LANE
                    and direct_asset_source_keys
                    and candidate.get("mandate_verified")
                )
                # Legacy compatibility for pre-lane inverse candidates only.
                or (
                    not candidate.get("selection_lane")
                    and (candidate.get("verified_inverse")
                         or candidate.get("is_inverse"))
                )
            )
            candidate.setdefault("ranking_mode", (
                SINGLE_STOCK_LEVERAGED_LANE
                if candidate.get("selection_lane") == SINGLE_STOCK_LEVERAGED_LANE
                else DIRECT_ASSET_LANE
                if direct_asset_source_keys
                else "unverified_non_equity"
            ))
            candidate["output_eligible"] = bool(
                independently_verified
                and candidate.get("theme_evidence_score",
                                  candidate.get("base_theme_exposure", 0.0)) > 0.0)
            continue

        # Fail closed before examining the response. A later complete assessment
        # may explicitly re-enable the fund.
        candidate.update({
            "ranking_mode": "holdings_unavailable",
            "holdings_status": "unavailable",
            "output_eligible": False,
            "eligibility_rejection_reasons": ["holdings_unavailable"],
            "preselect_score": 0.0,
            "static_theme_exposure": 0.0,
        })
        if candidate["code"] not in assessed_codes:
            continue

        holdings = []
        reported_weight = 0.0
        scored_weight = 0.0
        thematic_mass = 0.0
        thematic_issuers: set[str] = set()
        for raw in holdings_by_etf[candidate["code"]]:
            weight = _finite_float(raw.get("weight_pct"))
            code = str(raw.get("code") or "").strip()
            if not code or weight is None or weight <= 0:
                continue
            score = relevance_by_code.get(code, {})
            quality = _stock_quality(score)
            status = score.get("relevance_status", "missing")
            eligible_holding = _eligible_holding(
                score,
                min_relevance=min_holding_relevance,
                min_confidence=min_holding_confidence,
            )
            holding = {
                "code": code,
                "ticker": code.partition(":")[2],
                "name": raw.get("name") or code,
                "weight_pct": weight,
                "quality": quality,
                "ai_relevance": score.get("ai_relevance", 1.0),
                "exposure_type": score.get("exposure_type", "unclear"),
                "confidence": score.get("confidence", 0.0),
                "reason": score.get("reason", ""),
                "relevance_status": status,
                "impact_channel": score.get("impact_channel", "none"),
                "theme_specificity": score.get("theme_specificity", "none"),
                "materiality": score.get("materiality", "unknown"),
                "evidence_strength": score.get("evidence_strength", "none"),
                "theme_eligible": eligible_holding,
            }
            holdings.append(holding)
            reported_weight += weight
            if status == "scored":
                scored_weight += weight
                if eligible_holding:
                    thematic_mass += (weight / 100.0) * quality
                if eligible_holding and weight >= 0.25:
                    thematic_issuers.add(_holding_issuer_key(
                        code, raw.get("name") or code))

        holdings.sort(key=lambda item: (-item["weight_pct"], item["code"]))
        thematic_breadth = len(thematic_issuers)
        scored_share = scored_weight / reported_weight if reported_weight > 0 else 0.0
        semantic_coverage_complete = scored_share >= min_scored_share
        candidate.update({
            "full_holdings": holdings,
            "holdings_status": (
                "complete" if reported_weight >= min_weight_coverage else
                "incomplete"
            ),
            "holdings_weight_coverage_pct": reported_weight,
            "semantic_scored_weight_pct": scored_weight,
            "semantic_scored_share": scored_share,
            "holding_theme_mass": thematic_mass,
            "holding_theme_breadth": thematic_breadth,
        })
        if reported_weight < min_weight_coverage:
            candidate["ranking_mode"] = "holdings_incomplete"
            candidate["eligibility_rejection_reasons"] = ["holdings_incomplete"]
            continue

        breadth_factor = 1.0 - math.exp(-thematic_breadth / 5.0)
        selected_stock_coverage = max(0.0, min(
            1.0, float(candidate.get("selected_stock_coverage") or 0.0)))
        mandate_corroboration = (
            1.0 if candidate.get("mandate_verified") else 0.0)
        theme_evidence_score = max(0.0, min(
            1.0,
            0.70 * thematic_mass
            + 0.15 * breadth_factor
            + 0.10 * selected_stock_coverage
            + 0.05 * mandate_corroboration,
        ))
        relevant = [
            holding for holding in holdings
            if holding["theme_eligible"]
        ]
        # Holdings evidence owns basket eligibility. Canonical mandate evidence
        # is corroboration in the approved score, not a free-form hard gate.
        output_eligible = bool(
            theme_evidence_score >= min_output_score
            and thematic_breadth >= 2
        )
        eligibility_rejection_reasons = []
        if theme_evidence_score < min_output_score:
            eligibility_rejection_reasons.append("theme_score_below_minimum")
        if thematic_breadth < 2:
            eligibility_rejection_reasons.append(
                "relevant_issuer_breadth_below_2")
        partial_lower_bound = not semantic_coverage_complete
        if partial_lower_bound and not output_eligible:
            eligibility_rejection_reasons.insert(
                0, "semantic_coverage_below_minimum")
        ranking_mode = (
            "partial_holdings_lower_bound"
            if partial_lower_bound and output_eligible else
            "holdings_incomplete"
            if partial_lower_bound else
            "full_holdings"
        )
        bearish_downside = bool(candidate.get("bearish_downside_exposure"))
        if thematic_breadth:
            if partial_lower_bound and output_eligible:
                reason = (
                    f"Scored portfolio holdings alone contain {thematic_breadth} "
                    f"materially {'downside-sensitive ' if bearish_downside else ''}"
                    "theme-exposed companies; unresolved holdings count as zero exposure."
                )
            elif bearish_downside:
                reason = (
                    f"Portfolio contains {thematic_breadth} materially theme-exposed "
                    "companies vulnerable under the bearish theme."
                )
            else:
                reason = (
                    f"Portfolio contains {thematic_breadth} materially "
                    "theme-exposed companies."
                )
        else:
            reason = "Reported portfolio has limited theme-supported company exposure."
        candidate.update({
            "holding_theme_purity": thematic_mass / max(reported_weight / 100.0, 0.01),
            "holding_breadth_factor": breadth_factor,
            "full_holdings_score": theme_evidence_score,
            "theme_evidence_score": theme_evidence_score,
            "base_theme_exposure": theme_evidence_score,
            "static_theme_exposure": theme_evidence_score,
            "ranking_mode": ranking_mode,
            "partial_evidence_mode": bool(
                partial_lower_bound and output_eligible),
            "semantic_coverage_status": (
                "partial_lower_bound" if partial_lower_bound and output_eligible else
                "insufficient" if partial_lower_bound else
                "complete"
            ),
            "output_eligible": output_eligible,
            "eligibility_rejection_reasons": eligibility_rejection_reasons,
            "mandate_corroboration": mandate_corroboration,
            "matched_holdings": relevant,
            "theme_weight_pct": sum(item["weight_pct"] for item in relevant),
            "weighted_theme_exposure_pct": 100.0 * thematic_mass,
            "theme_breadth": thematic_breadth,
            "ai_relevance": round(1.0 + 4.0 * theme_evidence_score, 1),
            "confidence": min(0.98, scored_share),
            "exposure_type": (
                "direct" if theme_evidence_score >= 0.50 else "beneficiary"),
            "reason": reason,
        })

    apply_unified_etf_scores(candidates)
    for rank, candidate in enumerate(candidates, 1):
        candidate["rank"] = rank
    return candidates


def _basket_identity(candidate: dict) -> tuple[str, str] | None:
    """Return a structured benchmark/base-index code identity when present."""
    for key in ("index_etf_code", "base_index_code", "benchmark_code"):
        value = str(candidate.get(key) or "").strip()
        if value:
            return "benchmark_or_base_index_code", value.casefold()
    return None


def _normalised_portfolio(candidate: dict) -> dict[str, float]:
    weights: dict[str, float] = {}
    for holding in candidate.get("full_holdings", []) or []:
        weight = _finite_float(holding.get("weight_pct"))
        code = str(holding.get("code") or "").strip()
        if not code or weight is None or weight <= 0:
            continue
        issuer = _holding_issuer_key(code, holding.get("name") or code)
        weights[issuer] = weights.get(issuer, 0.0) + weight
    total = sum(weights.values())
    if total <= 0:
        return {}
    return {issuer: weight / total for issuer, weight in weights.items()}


def basket_weighted_overlap(left: dict, right: dict) -> float:
    """Return normalized weighted holdings overlap in ``[0, 1]``."""
    left_weights = _normalised_portfolio(left)
    right_weights = _normalised_portfolio(right)
    if not left_weights or not right_weights:
        return 0.0
    return sum(
        min(weight, right_weights.get(issuer, 0.0))
        for issuer, weight in left_weights.items()
    )


def _is_explicit_ce(candidate: dict) -> bool:
    """Return whether metadata explicitly identifies an exchange-traded fund."""
    return _normalise(candidate.get("security_class")) == "ce"


def _is_explicit_etn(candidate: dict) -> bool:
    """Fail closed when any structured or descriptive field identifies an ETN.

    This check intentionally runs before the relaxed CE tier.  Some data rows
    use ``security_class=CE`` for both funds and notes, leaving ``etf_type`` or
    the product name as the only explicit ETN signal.
    """
    if str(candidate.get("exclusion_reason") or "").strip().casefold() in {
        "exchange-traded note", "exchange traded note",
    }:
        return True
    for key in ("security_class", "etf_type"):
        structured_type = re.sub(
            r"[\s_\-\u00a0\u2010-\u2015\u2212]+", " ",
            str(candidate.get(key) or "").strip(),
        ).casefold()
        if structured_type in {"etn", "etns", "note", "exchange traded note"}:
            return True
    for key in (
        "security_class", "etf_type", "name", "fund_strategy",
        "fund_category", "fund_niche", "fund_focus", "selection_criteria",
        "asset_class",
    ):
        if _EXCHANGE_TRADED_NOTE_RE.search(str(candidate.get(key) or "")):
            return True
    return False


def _fallback_ranking_key(candidate: dict) -> tuple:
    """Rank weak/discovered CE rows deterministically without inventing fit."""
    evidence_values = [
        _finite_float(candidate.get(key))
        for key in (
            "unified_score", "theme_evidence_score", "static_theme_exposure",
            "base_theme_exposure", "preselect_score",
        )
    ]
    evidence = max((value for value in evidence_values if value is not None),
                   default=0.0)
    turnover = _finite_float(candidate.get("turnover"))
    aum = _finite_float(candidate.get("aum"))
    expense = _finite_float(candidate.get("expense_ratio"))
    rank = _finite_float(candidate.get("rank"))
    source_order = _finite_float(candidate.get("source_order"))
    fallback_rank = _finite_float(candidate.get("fallback_rank"))
    return (
        -evidence,
        int(bool(candidate.get("fallback_direction_mismatch"))),
        -int(bool(candidate.get("mandate_verified"))),
        rank if rank is not None else math.inf,
        source_order if source_order is not None else math.inf,
        fallback_rank if fallback_rank is not None else math.inf,
        -(turnover if turnover is not None else -1.0),
        -(aum if aum is not None else -1.0),
        expense if expense is not None else math.inf,
        str(candidate.get("code") or ""),
    )


def _is_basket_like(candidate: dict) -> bool:
    return bool(
        candidate.get("selection_lane") == MULTI_STOCK_BASKET_LANE
        or _normalised_portfolio(candidate)
    )


def _economic_overlap_reason(
    candidate: dict,
    accepted: list[dict],
    overlap_threshold: float,
) -> str | None:
    """Return the existing economic-dedupe reason against diverse rows."""
    lane = candidate.get("selection_lane")
    if lane == SINGLE_STOCK_LEVERAGED_LANE:
        underlying = str(candidate.get("underlying_code") or "").strip()
        if underlying and any(
            other.get("selection_lane") == SINGLE_STOCK_LEVERAGED_LANE
            and str(other.get("underlying_code") or "").strip() == underlying
            for other in accepted
        ):
            return "duplicate_selected_stock_underlying"

    if not _is_basket_like(candidate):
        return None
    identity = _basket_identity(candidate)
    if identity and any(
        _is_basket_like(other) and _basket_identity(other) == identity
        for other in accepted
    ):
        return "duplicate_benchmark_or_base_index"
    if any(
        _is_basket_like(other)
        and basket_weighted_overlap(candidate, other) >= overlap_threshold
        for other in accepted
    ):
        return "weighted_portfolio_overlap"
    return None


def _meets_etf_exposure_preference(candidate: dict) -> bool:
    """Prefer verified evidence, never provisional scores or public rank labels."""
    evidence = _finite_float(candidate.get("static_theme_exposure"))
    return bool(
        candidate.get("output_eligible")
        and evidence is not None
        and evidence >= ETF_PREFERRED_EVIDENCE_MIN
    )


def select_output_etfs(
    candidates: list[dict],
    limit: int | None = None,
    *,
    basket_overlap_threshold: float = 0.90,
) -> list[dict]:
    """Build the deterministic output pool, including bounded CE reserves.

    Eligible products with authoritative evidence of at least 0.375 are
    preferred, including permitted economic-overlap reserves.
    Remaining products retain their existing eligibility and diversity order.
    A non-eligible reserve must be explicitly identified as ``security_class=CE``;
    explicit exchange-traded notes are always excluded. Duplicate market codes
    are also a hard exclusion. Within each evidence band, economic duplicates
    follow diverse rows so :func:`compose_output_etfs` relaxes overlap only
    after the band's diverse candidates are exhausted.

    Legacy hand-built candidates without explicit CE metadata retain the old
    hard economic-dedupe behavior. This preserves compatibility while ensuring
    that relaxation is never applied to a product whose security class is
    unknown.
    """
    if limit is not None and limit < 0:
        raise ValueError("ETF output limit cannot be negative")
    if limit == 0:
        return []
    overlap_threshold = max(0.0, min(1.0, basket_overlap_threshold))
    eligible: list[dict] = []
    ce_fallback: list[dict] = []
    for candidate in candidates:
        code = str(candidate.get("code") or "").strip().upper()
        if not _MARKET_CODE_RE.fullmatch(code):
            candidate.update({
                "selection_tier": "excluded",
                "selection_basis": "invalid_market_code",
                "dedupe_excluded": True,
                "dedupe_reason": "invalid_market_code",
            })
            continue
        candidate["code"] = code
        if _is_explicit_etn(candidate):
            candidate.update({
                "selection_tier": "excluded",
                "selection_basis": "explicit_etn_excluded",
                "dedupe_excluded": True,
                "dedupe_reason": "exchange-traded note",
            })
            continue
        if candidate.get("output_eligible"):
            candidate.update({
                "selection_tier": "eligible",
                "selection_basis": "eligible_diverse",
            })
            eligible.append(candidate)
            continue
        if _is_explicit_ce(candidate):
            if candidate.get("static_theme_exposure") is None:
                candidate["static_theme_exposure"] = 0.0
            candidate.update({
                "selection_tier": "ce_fallback",
                "selection_basis": "ce_fallback_diverse",
            })
            ce_fallback.append(candidate)
            continue
        candidate.update({
            "selection_tier": "excluded",
            "selection_basis": "not_eligible_or_explicit_ce",
        })

    # Choose the stronger evidence row before code or economic deduplication
    # can let a higher-ranked but weaker row consume its place.
    eligible.sort(key=lambda candidate: (
        not _meets_etf_exposure_preference(candidate),
        *_ranking_key(candidate),
    ))
    ce_fallback.sort(key=_fallback_ranking_key)

    diverse: dict[str, list[dict]] = {
        "eligible": [], "ce_fallback": [],
    }
    overlapping: dict[str, list[dict]] = {
        "eligible": [], "ce_fallback": [],
    }
    accepted_diverse: list[dict] = []
    seen_codes: set[str] = set()

    for tier, ordered in (("eligible", eligible), ("ce_fallback", ce_fallback)):
        for candidate in ordered:
            code = str(candidate.get("code") or "").strip()
            canonical_code = code.casefold()
            if not code or canonical_code in seen_codes:
                candidate.update({
                    "selection_basis": "duplicate_market_code_excluded",
                    "dedupe_excluded": True,
                    "dedupe_reason": "duplicate_market_code",
                    "overlap_relaxed": False,
                })
                continue
            seen_codes.add(canonical_code)

            overlap_reason = _economic_overlap_reason(
                candidate, accepted_diverse, overlap_threshold,
            )
            if overlap_reason:
                # Economic overlap is relaxable only for explicit CE rows. A
                # legacy candidate with no structured class retains the prior
                # hard-dedupe contract.
                if not _is_explicit_ce(candidate):
                    candidate.update({
                        "dedupe_excluded": True,
                        "dedupe_reason": overlap_reason,
                        "overlap_relaxed": False,
                    })
                    continue
                if candidate.get("static_theme_exposure") is None:
                    candidate["static_theme_exposure"] = 0.0
                candidate.update({
                    "selection_basis": f"{tier}_overlap_relaxed",
                    "dedupe_excluded": False,
                    "dedupe_reason": overlap_reason,
                    "overlap_relaxed": True,
                })
                overlapping[tier].append(candidate)
                continue

            candidate.update({
                "selection_basis": f"{tier}_diverse",
                "dedupe_excluded": False,
                "dedupe_reason": None,
                "overlap_relaxed": False,
            })
            diverse[tier].append(candidate)
            accepted_diverse.append(candidate)

    selected = [
        *diverse["eligible"],
        *diverse["ce_fallback"],
        *overlapping["eligible"],
        *overlapping["ce_fallback"],
    ]
    # Stable partition: supported overlap reserves precede weak diverse funds,
    # while the established ranking inside each evidence band is preserved.
    selected.sort(
        key=lambda candidate: not _meets_etf_exposure_preference(candidate),
    )
    if limit is not None:
        return selected[:limit]
    return selected


def compose_output_etfs(
    deduped_candidates: list[dict], target: int,
) -> list[dict]:
    """Compose exactly ``target`` ETFs whenever the supplied pool permits it.

    Eligible evidence of at least 0.375 takes priority over weaker rows.
    The historical best-wrapper and conventional-basket reservations
    apply inside each evidence band, so a weaker reservation cannot displace
    stronger evidence. A genuinely exhausted input pool returns short so the
    workflow can attempt live/offline CE expansion before deciding whether to fail.
    """
    if target < 0:
        raise ValueError("ETF output target cannot be negative")
    if target == 0:
        return []

    # Reapplying selection makes direct callers safe and deterministic while
    # remaining idempotent for the normal select-then-compose workflow.
    pool = select_output_etfs(deduped_candidates, limit=None)
    if not pool:
        return []

    strong = [
        candidate for candidate in pool if _meets_etf_exposure_preference(candidate)
    ]
    weaker = [
        candidate for candidate in pool if not _meets_etf_exposure_preference(candidate)
    ]
    selected = _compose_etf_band(strong, target)
    if len(selected) < target:
        selected.extend(_compose_etf_band(weaker, target - len(selected)))
    return selected


def _compose_etf_band(pool: list[dict], target: int) -> list[dict]:
    """Apply product reservations within one already-deduplicated evidence band."""
    preferred = [
        candidate for candidate in pool
        if not candidate.get("overlap_relaxed")
    ]
    best_wrapper = next((
        candidate for candidate in preferred
        if candidate.get("selection_tier") == "eligible"
        and candidate.get("selection_lane") == SINGLE_STOCK_LEVERAGED_LANE
    ), None)
    best_basket = next((
        candidate for candidate in preferred
        if candidate.get("selection_tier") == "eligible"
        and candidate.get("selection_lane") == MULTI_STOCK_BASKET_LANE
    ), None)

    selected = [best_wrapper] if best_wrapper is not None else []
    selected_ids = {id(candidate) for candidate in selected}
    remaining = [
        candidate for candidate in pool if id(candidate) not in selected_ids
    ]
    capacity = max(0, target - len(selected))
    tail = remaining[:capacity]

    # Reserve one conventional eligible basket without otherwise disturbing
    # tier/ranking order. An overlap reserve is never promoted for diversity.
    if (
        target >= 2
        and best_basket is not None
        and id(best_basket) not in selected_ids
        and best_basket not in tail
        and capacity > 0
    ):
        tail = [*tail[:capacity - 1], best_basket]
    return [*selected, *tail]


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
    """Rank bounded ordinary candidates plus additive exact direct probes."""
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
    canonical_evidence = canonical_theme_evidence(theme, brief, pools)
    canonical_diagnostics: dict[str, object] = {
        "validated_terms": list(canonical_evidence.validated_terms),
        "routed_pool_keys": list(canonical_evidence.routed_pool_keys),
        "exact_names": list(canonical_evidence.exact_names),
        "concept_indices": [],
    }
    discovery_source_counts = {
        bucket: {"raw": 0, "retained": 0}
        for bucket in _DISCOVERY_SOURCE_BUCKETS
    }
    failures: list[str] = []
    exclusion_reasons: dict[str, int] = {}
    exclusion_examples: dict[str, list[str]] = {}
    candidates, discovery_excluded = _discover(
        quotes,
        stocks,
        pools,
        limit,
        failures,
        direction_mode,
        exclusion_reasons=exclusion_reasons,
        exclusion_examples=exclusion_examples,
        canonical_evidence=canonical_evidence,
        canonical_diagnostics=canonical_diagnostics,
        source_counts=discovery_source_counts,
    )
    _enrich(quotes, candidates, stocks, failures)
    for candidate in candidates.values():
        mandate_verified, mandate_terms = _mandate_match(
            candidate,
            brief,
            theme,
            pools=pools,
            canonical_evidence=canonical_evidence,
        )
        candidate["mandate_verified"] = bool(mandate_verified)
        candidate["mandate_terms"] = mandate_terms
    ranked, ranking_excluded = _rank_candidates(
        candidates, stocks, limit, direction_mode,
    )
    for candidate in candidates.values():
        reason = str(candidate.get("exclusion_reason") or "").strip()
        if not reason:
            continue
        exclusion_reasons[reason] = exclusion_reasons.get(reason, 0) + 1
        example = f"{candidate.get('code', '')} {candidate.get('name', '')}".strip()
        examples = exclusion_examples.setdefault(reason, [])
        if example and example not in examples and len(examples) < 3:
            examples.append(example)
    result = PreselectionResult(
        candidates=ranked,
        pools=pools,
        discovered=len(candidates),
        excluded=discovery_excluded + ranking_excluded,
        failures=failures,
        exclusion_reasons=exclusion_reasons,
        exclusion_examples=exclusion_examples,
        canonical_diagnostics=canonical_diagnostics,
        discovery_source_counts=discovery_source_counts,
    )
    if log:
        pool_names = ", ".join(match.spec.label for match in pools) or "none"
        log(f"ETF static pools ({direction_mode}): {pool_names}")
        canonical_terms = ", ".join(
            canonical_diagnostics["validated_terms"]) or "none"
        canonical_pool_keys = ", ".join(
            canonical_diagnostics["routed_pool_keys"]) or "none"
        concept_indices = ", ".join(
            f"{item['index_code']}={item['index_name']}"
            for item in canonical_diagnostics["concept_indices"]
        ) or "none"
        log(
            f"ETF canonical evidence: terms=[{canonical_terms}]; "
            f"pools=[{canonical_pool_keys}]; "
            f"exact concept indices=[{concept_indices}]"
        )
        source_summary = "; ".join(
            f"{bucket} raw={discovery_source_counts[bucket]['raw']} "
            f"retained={discovery_source_counts[bucket]['retained']}"
            for bucket in _DISCOVERY_SOURCE_BUCKETS
        )
        log(f"ETF discovery source counts: {source_summary}")
        log(f"ETF preselection: {result.discovered} unique candidates, "
            f"{len(ranked)} ranked (ordinary limit="
            f"{min(limit, _ORDINARY_BASKET_CANDIDATE_CAP)}; "
            f"direct probes additive); "
            f"{result.excluded} risky source/candidate rows excluded")
        if result.exclusion_reasons:
            reason_summary = "; ".join(
                f"{reason}={count}"
                for reason, count in sorted(
                    result.exclusion_reasons.items(),
                    key=lambda item: (-item[1], item[0]),
                )
            )
            log(f"ETF exclusion reasons: {reason_summary}")
            for reason, examples in sorted(result.exclusion_examples.items()):
                if examples:
                    log(f"ETF exclusion examples [{reason}]: {', '.join(examples)}")
        for failure in failures:
            if failure.startswith((
                    "direct-recovery-source:", "concept-index-source:")):
                log(f"ETF preselection diagnostic: {failure}")
            else:
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
