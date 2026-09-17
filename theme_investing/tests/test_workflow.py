"""Unit + mocked end-to-end tests. Run: python3 tests/test_workflow.py"""

import argparse
import contextlib
import io
import json
import os
import re
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scoring
import workflow as workflow_module
from ainvest_client import AInvestClient
from cli import (_build_output, _positive_int, _serialize_output, build_parser,
                 workflow_options)
from etf_preselection import (apply_unified_etf_scores, compose_output_etfs,
                              _hard_filter_reason,
                              _mandate_match,
                              _validated_etf_terms, build_holdings_shortlist,
                              match_theme_pools, preselect_etfs,
                              rerank_with_component_holdings,
                              select_output_etfs)
from workflow import (SelectionUniverseError, StockRationaleError, ThemeWorkflow,
                      _taxonomy_match_score, validate_input)
from marketcode_resolver import MarketCodeResolver


def test_event_date_int():
    assert scoring.event_date_int("2026-07-09") == 20260709


def test_kline_features_event_window_and_abnormal_return():
    bars = [
        {"t": 1, "close": 86, "volume": 9, "date_int": 20260704},
        {"t": 2, "close": 88, "volume": 10, "date_int": 20260705},
        {"t": 3, "close": 90, "volume": 11, "date_int": 20260706},
        {"t": 4, "close": 95, "volume": 12, "date_int": 20260707},
        {"t": 5, "close": 97, "volume": 13, "date_int": 20260708},
        {"t": 6, "close": 100, "volume": 100, "date_int": 20260709},
        {"t": 7, "close": 110, "volume": 80, "date_int": 20260710},
    ]
    f = scoring.compute_kline_features(bars, 20260709, benchmark_return=5.0)
    assert round(f["chg_pct"], 1) == 10.0
    assert f["rvol_event"] == 100 / 11.0  # peak event volume / pre-event median
    assert f["active_days"] == 2
    assert round(f["abnormal_return"], 1) == 5.0
    assert f["has_history"] is True  # five pre-event bars form a baseline


def test_kline_features_preserve_negative_change_and_abnormal_return():
    bars = [
        {"close": 95, "volume": 10, "date_int": 20260707},
        {"close": 97, "volume": 11, "date_int": 20260708},
        {"close": 100, "volume": 30, "date_int": 20260709},
        {"close": 80, "volume": 35, "date_int": 20260710},
    ]
    features = scoring.compute_kline_features(
        bars, 20260709, benchmark_return=-5.0, min_history=2)
    assert round(features["chg_pct"], 1) == -20.0
    assert round(features["abnormal_return"], 1) == -15.0


def test_kline_features_requires_baseline():
    bars = [{"t": i, "close": 100+i, "volume": 10+i, "date_int": 20260700+i} for i in range(1, 12)]
    f = scoring.compute_kline_features(bars, 20260720)
    assert f["rvol_event"] is None
    assert f["quality"] < 1.0


def test_kline_features_configurable_robust_rvol_and_missing_volume():
    bars = [
        {"close": 100, "volume": volume, "date_int": 20260700 + i}
        for i, volume in enumerate((10, 11, 12, 13, 14), 1)
    ] + [
        {"close": 101, "volume": None, "date_int": 20260706},
        {"close": 102, "volume": 10000, "date_int": 20260707},
        {"close": 103, "volume": 24, "date_int": 20260708},
    ]
    f = scoring.compute_kline_features(
        bars, 20260706, baseline_lookback=3, min_history=3)
    # Only the trailing 12, 13, and 14 volumes establish the baseline.
    assert f["baseline_n"] == 3
    assert f["window_n"] == 2  # None volume is explicitly excluded.
    assert f["rvol_event"] == scoring.RVOL_WINSOR_CAP
    assert f["active_days"] == 2
    assert f["rvol_mean"] < scoring.RVOL_WINSOR_CAP


def test_kline_features_invalid_volume_has_no_baseline():
    bars = [
        {"close": 100, "volume": volume, "date_int": 20260700 + i}
        for i, volume in enumerate((0, -1, "bad", None, 10), 1)
    ] + [{"close": 102, "volume": 30, "date_int": 20260706}]
    f = scoring.compute_kline_features(bars, 20260706, min_history=2)
    assert f["baseline_n"] == 1
    assert f["has_history"] is False
    assert f["rvol_event"] is None


def test_percentiles():
    assert scoring.percentiles([1, 2, 3]) == [0.0, 0.5, 1.0]
    assert scoring.percentiles([None, 5]) == [0.0, 0.0]
    assert scoring.percentiles([None, 5, 9]) == [0.0, 0.0, 1.0]
    assert scoring.percentiles([1, 2, 2, 3]) == [0.0, 0.5, 0.5, 1.0]


def test_absolute_percentiles_are_sign_neutral_and_tie_aware():
    values = [-8.0, 8.0, 2.0, None]
    ranked = scoring.absolute_percentiles(values)
    assert ranked[0] == ranked[1]
    assert ranked[0] > ranked[2]
    assert ranked[3] == 0.0
    assert scoring.absolute_percentiles([-8.0, 8.0]) == [0.0, 0.0]
    assert scoring.absolute_percentiles([1.0, 2.0, float("nan")]) == [0.0, 1.0, 0.0]
    assert scoring.absolute_percentiles([1.0, float("inf")]) == [0.0, 0.0]


def test_composite_accounts_for_exposure_without_negative_price_penalty():
    direct = scoring.composite(5, 0.8, 0.8, 0.8, 1.0,
                               exposure_type="direct", confidence=1.0)
    diversified = scoring.composite(5, 0.8, 0.8, 0.8, 1.0,
                                    exposure_type="diversified", confidence=1.0)
    negative = scoring.composite(5, 0.8, 0.8, 0.8, 1.0,
                                 exposure_type="direct", confidence=1.0,
                                 neg_price_penalty=0.4)
    assert direct > diversified
    assert negative == direct


def test_calibrate_scores_preserve_absolute_quality_without_forced_five():
    scores = scoring.calibrate_scores([0.9, 0.88, 0.7, 0.5, 0.49])
    assert scores == [4.6, 4.5, 3.8, 3.0, 3.0]
    assert all(scores[i] >= scores[i + 1] for i in range(len(scores) - 1))
    assert all(1.0 <= s <= 5.0 for s in scores)


def test_calibrate_preserves_exact_evidence_ties_and_weak_singletons():
    scores = scoring.calibrate_scores([0.8, 0.8, 0.8])
    assert scores == [4.2, 4.2, 4.2]
    assert scoring.calibrate_scores([0.1]) == [1.4]


def test_theme_exposure_combines_llm_signals():
    strong = scoring.theme_exposure(5, "direct", 0.9)
    weak_exposure = scoring.theme_exposure(5, "diversified", 0.9)
    low_conf = scoring.theme_exposure(5, "direct", 0.2)
    low_rel = scoring.theme_exposure(2, "direct", 0.9)
    confirmed = scoring.theme_exposure(5, "direct", 0.9, market_confirm=True)
    half_strength = scoring.theme_exposure(5, "direct", 0.9, market_strength=0.5)
    assert strong > weak_exposure          # exposure type matters
    assert strong > low_conf               # confidence matters
    assert strong > low_rel                # relevance matters
    assert confirmed > strong              # market confirmation lifts the score
    assert strong < half_strength < confirmed
    assert 0.0 <= strong <= 1.0


def test_etf_market_strength_uplift_is_continuous_and_bounded():
    wf = ThemeWorkflow(FakeLLM(), FakeQuotes())
    unconfirmed = {"code": "185:A", "static_theme_exposure": 0.5,
                   "market_strength": 0.0}
    partial = {"code": "185:B", "static_theme_exposure": 0.5,
               "market_strength": 0.5}
    full = {"code": "185:C", "static_theme_exposure": 0.5,
            "market_strength": 1.0}
    wf._assign_theme_exposure([unconfirmed, partial, full])
    assert unconfirmed["theme_exposure_raw"] == 0.5
    assert partial["theme_exposure_raw"] == 0.525
    assert full["theme_exposure_raw"] == 0.55
    assert full["theme_exposure_raw"] <= 1.10 * unconfirmed["theme_exposure_raw"]
    assert [candidate["theme_exposure"] for candidate in (unconfirmed, partial, full)] == [
        5.0, 4.0, 3.0,
    ]


def test_etf_rank_ladder_preserves_raw_evidence_and_frozen_order():
    wf = ThemeWorkflow(FakeLLM(), FakeQuotes())
    raw_scores = [0.8, 0.0, 0.375, 0.25, 1.0, 0.3749, 0.5]
    chosen = [
        {"code": f"185:ETF{index}", "static_theme_exposure": evidence}
        for index, evidence in enumerate(raw_scores)
    ]
    frozen_codes = [candidate["code"] for candidate in chosen]

    wf._assign_theme_exposure(chosen)

    assert [candidate["theme_exposure"] for candidate in chosen] == [
        5.0, 4.7, 4.3, 4.0, 3.7, 3.3, 3.0,
    ]
    assert [candidate["score"] for candidate in chosen] == [
        candidate["theme_exposure"] for candidate in chosen
    ]
    assert [candidate["static_theme_exposure"] for candidate in chosen] == raw_scores
    assert [candidate["theme_exposure_raw"] for candidate in chosen] == raw_scores
    assert [candidate["code"] for candidate in chosen] == frozen_codes


def test_etf_rank_ladder_is_independent_of_equal_weak_or_missing_evidence():
    wf = ThemeWorkflow(FakeLLM(), FakeQuotes())
    for evidence in (0.0, 0.8, None):
        chosen = [
            {"code": f"185:ETF{index}", "security_class": "CE"}
            for index in range(5)
        ]
        if evidence is not None:
            for candidate in chosen:
                candidate["static_theme_exposure"] = evidence
        frozen_codes = [candidate["code"] for candidate in chosen]

        wf._assign_theme_exposure(chosen)

        assert [candidate["theme_exposure"] for candidate in chosen] == [
            5.0, 4.5, 4.0, 3.5, 3.0,
        ]
        assert [candidate["score"] for candidate in chosen] == [
            5.0, 4.5, 4.0, 3.5, 3.0,
        ]
        assert [candidate["code"] for candidate in chosen] == frozen_codes
        if evidence is not None:
            assert all(candidate["static_theme_exposure"] == evidence
                       and candidate["theme_exposure_raw"] == evidence
                       for candidate in chosen)
        else:
            assert all("static_theme_exposure" not in candidate
                       for candidate in chosen)


def test_etf_rank_ladder_handles_empty_singleton_and_two_selected_funds():
    wf = ThemeWorkflow(FakeLLM(), FakeQuotes())
    empty = []
    assert wf._assign_theme_exposure(empty) is None
    assert empty == []

    singleton = [{"code": "185:ONLY", "static_theme_exposure": 0.0}]
    wf._assign_theme_exposure(singleton)
    assert singleton[0]["theme_exposure"] == singleton[0]["score"] == 5.0
    assert singleton[0]["theme_exposure_raw"] == 0.0

    pair = [
        {"code": "185:WEAK", "static_theme_exposure": 0.0},
        {"code": "185:STRONG", "static_theme_exposure": 1.0},
    ]
    wf._assign_theme_exposure(pair)
    assert [candidate["code"] for candidate in pair] == ["185:WEAK", "185:STRONG"]
    assert [candidate["theme_exposure"] for candidate in pair] == [5.0, 3.0]
    assert [candidate["theme_exposure_raw"] for candidate in pair] == [0.0, 1.0]


def test_five_zero_evidence_etf_fallbacks_assemble_with_rank_ladder():
    wf = ThemeWorkflow(FakeLLM(), FakeQuotes())
    chosen = compose_output_etfs([
        {
            "code": f"185:FALLBACK{index}", "name": f"Fallback ETF {index}",
            "security_class": "CE", "output_eligible": False,
            "static_theme_exposure": 0.0,
        }
        for index in range(5)
    ], 5)
    frozen_codes = [candidate["code"] for candidate in chosen]

    output = wf._assemble(chosen, {}, "2026-09-16")

    assert len(output) == 5
    assert [item["market_code"] for item in output] == frozen_codes
    assert [item["Theme exposure"] for item in output] == [5.0, 4.5, 4.0, 3.5, 3.0]
    assert all(set(item) == {
        "market_code", "theme_rationale", "Theme exposure", "event_date",
    } for item in output)
    assert all(item["theme_rationale"]["en"] and item["theme_rationale"]["zh"]
               for item in output)
    assert all(candidate["static_theme_exposure"] == 0.0
               and candidate["theme_exposure_raw"] == 0.0 for candidate in chosen)


def test_validate_input_rejects_bad():
    for bad in ({"theme": "", "date": "2026-07-09", "url": "http://x"},
                {"theme": "x", "date": "07/09/2026", "url": "http://x"},
                {"theme": "x", "date": "2026-07-09", "url": "ftp://x"}):
        try:
            validate_input(bad)
            assert False
        except ValueError:
            pass


def test_event_brief_normalizes_internal_theme_direction():
    class BriefLLM:
        def __init__(self, direction):
            self.direction = direction
            self.system = ""
            self.user = ""

        def chat_json(self, system, user, **kw):
            self.system = system
            self.user = user
            return {"theme_cn": "  AI 泡沫  ", "summary": "s", "thesis": "t",
                    "theme_direction": self.direction}

    inp = {"theme": "AI Bubble", "date": "2026-08-05", "url": "https://example.com/x"}
    article = {"title": "Bubble warning", "text": "AI valuations may de-rate."}
    bearish_llm = BriefLLM("bearish")
    bearish = ThemeWorkflow(bearish_llm, FakeQuotes()).event_brief(inp, article)
    invalid = ThemeWorkflow(BriefLLM("mixed"), FakeQuotes()).event_brief(inp, article)

    assert bearish["theme_direction"] == "bearish"
    assert invalid["theme_direction"] == "bullish"
    assert bearish["theme_cn"] == "AI 泡沫"
    assert '"theme_cn"' in bearish_llm.user
    assert '"theme_direction"' in bearish_llm.user
    assert "dominant investable direction" in bearish_llm.system


def test_cli_output_includes_all_original_input_fields():
    payload = {
        "theme": "AI memory",
        "date": "2026-07-09",
        "url": "https://example.com/x",
        "cover": "https://example.com/cover.png",
        "future_metadata": {"source": "input"},
    }
    workflow_result = {
        "theme_cn": "AI 内存",
        "ThemeStocks": [{"market_code": "185:MU", "event_date": "2026-07-09"}],
        "ThemeEtfs": [{"market_code": "185:SMH", "event_date": "2026-07-09"}],
        "ThemeFAQ": [],
    }

    output = _build_output(payload, workflow_result)

    assert output["cover"] == payload["cover"]
    assert output["theme_cn"] == "AI 内存"
    assert output["future_metadata"] == payload["future_metadata"]
    assert output["ThemeStocks"] == [{
        "market_code": "185:MU", "event_date": "2026-07-09",
    }]
    assert output["ThemeEtfs"] == [{
        "market_code": "185:SMH", "event_date": "2026-07-09",
    }]
    assert workflow_result["ThemeStocks"][0]["event_date"] == "2026-07-09"

    serialized = _serialize_output(output)
    assert "\n" not in serialized
    assert '": ' not in serialized
    assert ', ' not in serialized
    assert json.loads(serialized) == output


def test_limit_cli_sets_independent_stock_and_etf_caps():
    for limit in (100, 1000):
        args = build_parser().parse_args(["--limit", str(limit), "{}"])
        opts = workflow_options(args)
        assert opts["stock_universe"] == limit
        assert opts["etf_universe"] == limit

    # A per-class option remains an intentional, explicit override.
    args = build_parser().parse_args(
        ["--limit", "100", "--stock-universe", "25", "{}"])
    opts = workflow_options(args)
    assert opts["stock_universe"] == 25
    assert opts["etf_universe"] == 100

    for invalid in ("0", "-1"):
        try:
            _positive_int(invalid)
            assert False, f"accepted invalid limit {invalid}"
        except argparse.ArgumentTypeError:
            pass


def _fake_relevance(code):
    """Deterministic mock relevance: memory leaders high, even-indexed names pass,
    odd-indexed names fail. Enough names clear 3.3 to fill the 8/5 targets."""
    if code.endswith("NVDA"):
        return 5.0
    if code.endswith("MU") or code.endswith("SMH") or code.endswith("SOXX"):
        return 4.0
    tail = "".join(ch for ch in code if ch.isdigit())
    if tail and int(tail) % 2 == 0:
        return 4.0
    return 1.0


_FAKE_BUSINESS_ROLES = (
    ("memory-controller silicon", "controller orders"),
    ("wafer-fabrication equipment for memory chips", "equipment orders"),
    ("advanced packaging tools for AI memory", "packaging-system orders"),
    ("data-center interconnects for AI accelerators", "interconnect sales"),
    ("memory-testing systems", "test-system orders"),
    ("semiconductor substrates for memory chips", "substrate shipments"),
    ("chip-design software for memory controllers", "design-software revenue"),
    ("server power systems for AI data centers", "power-system orders"),
    ("memory interconnect modules for AI accelerators", "interconnect-module sales"),
    ("thermal-management equipment for AI servers", "cooling-equipment orders"),
    ("storage-interface components", "interface-component sales"),
)

_FAKE_BROKER_PATTERNS = (
    "{business}; {connection}, so {pathway}.",
    "{business}; the event reaches this business as {connection}, with a financial result where {pathway}.",
    "{business}; its operating link is clear because {connection}, creating a route through which {pathway}.",
    "{business}; {connection} gives the company direct operating leverage, and {pathway}.",
    "{business}; when {connection}, the resulting product pull means {pathway}.",
    "{business}; its catalyst exposure runs through {connection}, turning customer activity into a setup where {pathway}.",
    "{business}; the catalyst matters because {connection}, which supports a financial outcome where {pathway}.",
    "{business}; demand transmission starts when {connection}; as a result, {pathway}.",
    "{business}; {connection} creates a distinct commercial channel, allowing the business to benefit as {pathway}.",
    "{business}; the earnings bridge begins with {connection}, after which {pathway}.",
    "{business}; this relationship is operational because {connection}, establishing conditions where {pathway}.",
)

_FAKE_BROKER_ZH_PATTERNS = (
    "{name}主营{business}；{connection}，因此{tail}。",
    "{name}经营{business}；由于{connection}，因此{tail}。",
    "{name}提供{business}；{connection}，从而{tail}。",
    "{name}专注于{business}；{connection}，将{tail}。",
    "{name}旗下核心业务包括{business}；{connection}，并可{tail}。",
    "{name}经营{business}；由于{connection}，同时{tail}。",
    "{name}主要经营{business}；随着{connection}，可{tail}。",
    "{name}从事{business}；{connection}，从而{tail}。",
    "{name}提供{business}；{connection}，直接{tail}。",
    "{name}运营{business}；{connection}，因此{tail}。",
    "{name}开发{business}；{connection}，从而{tail}。",
)


def _fake_candidate_evidence(code, name="", business="", *, bearish=False):
    """Ground one deterministic mock case so strict broker prose stays factual."""
    ticker = code.partition(":")[2]
    if ticker == "NVDA":
        role, driver = (
            "AI accelerator processors that use high-bandwidth memory",
            "accelerator orders",
        )
    elif ticker == "MU":
        role, driver = "high-bandwidth memory chips", "HBM orders"
    else:
        digits = "".join(char for char in ticker if char.isdigit())
        index = int(digits) if digits else sum(ord(char) for char in ticker)
        role, driver = _FAKE_BUSINESS_ROLES[index % len(_FAKE_BUSINESS_ROLES)]
    if business:
        # Keep the mocked relationship tied to the supplied live-profile fact;
        # random product roles would now (correctly) fail the grounding gate.
        role, driver = business, "product orders"
    company = name or ticker or code
    business_fact = (
        f"{company}'s business is {business}"
        if business and company.casefold() not in business.casefold()
        else business or f"{company} supplies {role}"
    )
    if bearish:
        connection = f"Lower event-driven spending reduces demand for {role}"
        pathway = f"Fewer {driver} can pressure revenue and earnings"
        effect = "negative"
    else:
        connection = f"Event-driven capacity growth increases demand for {role}"
        pathway = f"More {driver} can lift revenue and earnings"
        effect = "positive"
    return business_fact, connection, pathway, effect


def _fake_stock_score(line, score, *, bearish=False, event_phrase=""):
    parts = [part.strip() for part in line.split("|")]
    code = parts[0]
    candidate_id = parts[1] if len(parts) > 1 else ""
    name = parts[2] if len(parts) > 2 else code.partition(":")[2]
    business = next(
        (part.partition("=")[2] for part in parts if part.startswith("business=")),
        "",
    )
    business_fact, connection, pathway, effect = _fake_candidate_evidence(
        code, name, business, bearish=bearish,
    )
    if event_phrase:
        connection = (
            f"{event_phrase} reduces demand for {business_fact}"
            if bearish else
            f"{event_phrase} increases demand for {business_fact}"
        )
    return {
        "candidate_id": candidate_id,
        "market_code": code,
        "theme_relevance": score,
        "article_support": 0,
        "exposure_type": "direct",
        "confidence": 0.9,
        "impact_channel": "revenue_demand",
        "theme_specificity": "company_specific",
        "materiality": "high",
        "evidence_strength": "explicit",
        "reason": f"{business_fact}; {connection}",
        "article_reason": "not mentioned",
        "public_relation_score": score,
        "public_relation_confidence": 0.9,
        "relation_type": "direct",
        "directional_effect": effect,
        "business_fact": business_fact,
        "theme_connection": connection,
        "financial_pathway": pathway,
        "evidence_basis": "derived" if event_phrase or not business else "company_profile",
    }


def _fake_narrative_response(user):
    """Return distinct identity-preserving mock copy for stock and ETF records."""
    records = json.loads(
        user.split("Records (JSON):\n", 1)[1]
        .split("\n\nWriting requirements:", 1)[0]
    )
    bearish = "Internal theme direction: bearish" in user
    items = []
    for record in records:
        code = record["market_code"]
        name = record.get("name") or code.partition(":")[2]
        candidate_id = record.get("candidate_id") or code
        if not any(key in record for key in (
            "business fact", "company business", "event relationship",
            "financial pathway", "structural exposure evidence",
        )):
            # Preserve the existing ETF fallback coverage in broad E2E tests.
            continue
        business = record.get("business fact") or record.get("company business") or ""
        business_fact = business
        connection = record.get("event relationship") or ""
        pathway = record.get("financial pathway") or ""
        candidate_digits = "".join(
            char for char in str(candidate_id) if char.isdigit())
        pattern_index = (
            int(candidate_digits)
            if candidate_digits else sum(ord(char) for char in code)
        ) % len(_FAKE_BROKER_PATTERNS)
        en = _FAKE_BROKER_PATTERNS[pattern_index].format(
            business=business_fact,
            connection=connection.lower(),
            pathway=pathway.lower(),
        )
        tail = (
            "拖累订单、收入和盈利"
            if bearish else "提升订单、收入和盈利"
        )
        zh = _FAKE_BROKER_ZH_PATTERNS[pattern_index].format(
            name=name,
            business=business_fact,
            connection=connection,
            tail=tail,
        )
        items.append({
            "candidate_id": candidate_id,
            "market_code": code,
            "theme_rationale": {"type": "multilingual", "en": en, "zh": zh},
        })
    return {"items": items}


class FakeLLM:
    def chat_json(self, system, user, **kw):
        if system == workflow_module.prompts.EVENT_ECOSYSTEM_SYS:
            return {"entities": []}
        if system == workflow_module.prompts.NARRATIVE_SYS:
            return _fake_narrative_response(user)
        if "Return JSON with keys" in user:
            return {"theme_cn": "AI 内存", "summary": "s", "thesis": "t",
                    "theme_direction": "bullish",
                    "direct_beneficiaries": ["memory"],
                    "picks_and_shovels": ["semiconductor memory producers"],
                    "etf_exposure_terms": ["semiconductors", "AI memory"],
                    "second_order": [], "false_positives": [], "keywords": ["memory"]}
        if "Score each candidate" in user:
            input_theme_match = re.search(
                r'"input_theme"\s*:\s*"([^"]+)"', user)
            event_phrase = (
                input_theme_match.group(1)
                if input_theme_match else "AI memory expansion"
            )
            out = []
            for line in user.splitlines():
                if "|" in line and ":" in line.split("|")[0]:
                    code = line.split("|")[0].strip()
                    row = _fake_stock_score(
                        line, _fake_relevance(code),
                        bearish='"theme_direction": "bearish"' in user,
                        event_phrase=event_phrase,
                    )
                    out.append(row)
            return out
        if "FAQ" in user or "faq" in system.lower():
            return [{"question": f"Q{i}", "answer": f"A{i}"} for i in range(5)]
        return []


class FakeQuotes:
    class cfg: scene = "mock"
    _POOL = 500  # synthetic ranked pool depth
    _ETF_DATA = {
        "185:SMH": {
            "name": "VanEck Semiconductor ETF", "aum": 70_800_000_000,
            "leverage": 1, "direction": "Long", "fund_niche": "Semiconductors",
            "weights": {"185:NVDA": 19.99, "185:MU": 5.17, "185:S0": 5.1,
                        "185:S2": 4.9, "185:S4": 4.6, "185:S6": 4.5, "185:S8": 4.0},
        },
        "185:SOXX": {
            "name": "iShares Semiconductor ETF", "aum": 44_900_000_000,
            "leverage": 1, "direction": "Long", "fund_niche": "Semiconductors",
            "weights": {"185:NVDA": 8.13, "185:MU": 8.21, "185:S0": 5.4,
                        "185:S2": 4.9, "185:S4": 4.4, "185:S6": 4.2},
        },
        "171:DRAM": {
            "name": "Memory Technology ETF", "aum": 24_800_000_000,
            "leverage": 1, "direction": "Long", "fund_niche": "Semiconductors",
            "weights": {"185:MU": 8.0, "185:S0": 7.0, "185:S2": 6.0,
                        "185:S4": 5.0, "185:S6": 4.0},
        },
        "185:HBMX": {
            "name": "HBM Memory Supply Chain ETF", "aum": 36_000_000,
            "leverage": 1, "direction": "Long", "fund_niche": "Semiconductors",
            "weights": {"185:MU": 8.43, "185:S0": 6.0, "185:S2": 5.0,
                        "185:S4": 4.5, "185:S6": 4.3},
        },
        "185:AIQ": {
            "name": "Global X Artificial Intelligence ETF", "aum": 7_000_000_000,
            "leverage": 1, "direction": "Long", "fund_niche": "Artificial Intelligence",
            "weights": {"185:NVDA": 4.0, "185:MU": 6.4, "185:S0": 5.6,
                        "185:S2": 3.0},
        },
        "185:QQQ": {
            "name": "Invesco QQQ Trust", "aum": 300_000_000_000,
            "leverage": 1, "direction": "Long", "fund_niche": "Large Cap Growth",
            "weights": {"185:NVDA": 8.0, "185:MU": 0.8, "185:S0": 1.0},
        },
        "169:NVDY": {
            "name": "YieldMax NVDA Option Income Strategy ETF", "aum": 1_400_000_000,
            "leverage": 1, "direction": "Long", "benchmark": "185:NVDA",
            "weights": {},
        },
        "169:FLSP": {
            "name": "Franklin Systematic Style Premia ETF", "aum": 1_000_000_000,
            "leverage": 1, "direction": "Long", "fund_strategy": "Long/Short",
            "weights": {"185:MU": 29.0, "185:S0": 26.0},
        },
        "185:NVDL": {
            "name": "GraniteShares 2x Long NVDA", "aum": 5_000_000_000,
            "leverage": 2, "direction": "Long", "benchmark": "NVIDIA",
            "benchmark_code": "185:NVDA", "security_class": "CE",
            "etf_type": "Stock", "selection_criteria": "Single asset",
            "turnover": 400_000_000, "expense_ratio": 1.15,
            "weights": {"185:NVDA": 200.0},
        },
        "185:NVDQ": {
            "name": "Daily 2x Short NVDA ETF", "aum": 350_000_000,
            "leverage": -2, "direction": "Short", "benchmark": "NVIDIA",
            "benchmark_code": "185:NVDA", "security_class": "CE",
            "etf_type": "Stock", "selection_criteria": "Single asset",
            "turnover": 25_000_000, "expense_ratio": 1.15,
            "weights": {},
        },
        "185:SOXS": {
            "name": "Direxion Semiconductor Bear 3x", "aum": 2_000_000_000,
            "leverage": 3, "direction": "Short", "weights": {"185:NVDA": 10.0},
        },
    }
    def iter_ranked(self, selector, indicators, *, sort_pos=0, order="desc",
                    page_size=1000, strict=False):
        is_stock = selector.get("value") == ["C191"]
        pre = "185:S" if is_stock else "185:E"
        specials = ["185:NVDA", "185:MU"] if is_stock else ["185:SMH", "185:SOXX"]
        codes = specials + [f"{pre}{i}" for i in range(self._POOL)]
        for i, c in enumerate(codes):
            values = {indicators[0]["req_unique_id"]: 1e12 - i}
            if is_stock:
                name = self.name_of(c)
                business, _, _, _ = _fake_candidate_evidence(c, name)
                values.update({
                    "name": name,
                    "company_introduction": business,
                    "sector": "Technology",
                    "industry": "Theme Components",
                })
            yield {"code": c, "rank": i + 1, "values": values}
    def rank_universe(self, selector, indicators, *, total, sort_pos=0, **kw):
        import itertools
        return list(itertools.islice(
            self.iter_ranked(selector, indicators, sort_pos=sort_pos), total))
    def security_codes(self, security_type):
        return []  # mocked market: no offline expansion beyond the ranked pool
    def name_of(self, code): return code.split(":")[-1] + " Inc"
    def iter_prompt_etfs(self, prompt_id, *, page_size=100):
        if prompt_id == "6908afc3069a48065f159368":
            codes = ["185:SMH", "185:SOXX", "171:DRAM", "185:HBMX", "185:SOXS"]
        elif prompt_id == "6908b19b8738843bb3ba8657":
            codes = ["185:AIQ", "185:QQQ", "169:NVDY"]
        else:
            codes = []
        for rank, code in enumerate(codes, 1):
            data = self._ETF_DATA[code]
            yield {"code": code, "rank": rank, **{k: data.get(k) for k in
                   ("name", "aum", "leverage", "direction")}}
    def iter_related_etfs(self, stock_code, *, page_size=100):
        rows = []
        for code, data in self._ETF_DATA.items():
            weight = data.get("weights", {}).get(stock_code)
            if weight is not None:
                rows.append((weight, code, data))
        rows.sort(key=lambda item: (-item[0], item[1]))
        for rank, (weight, code, data) in enumerate(rows, 1):
            yield {"code": code, "rank": rank, "holding_weight": weight,
                   **{k: data.get(k) for k in ("name", "aum", "leverage", "direction")}}
    def iter_stock_etfs(self, stock_code, relation="related", *, page_size=1000):
        if relation == "related":
            yield from self.iter_related_etfs(stock_code, page_size=min(page_size, 100))
            return
        codes = []
        if stock_code == "185:NVDA":
            if relation == "long":
                codes = ["185:NVDL"]
            elif relation == "inverse":
                codes = ["185:NVDQ"]
            elif relation == "leveraged":
                codes = ["185:NVDL", "185:NVDQ"]
        for rank, code in enumerate(codes, 1):
            data = self._ETF_DATA[code]
            yield {"code": code, "rank": rank, "holding_weight": None,
                   "stock_relation_kind": relation,
                   **{k: data.get(k) for k in (
                       "name", "aum", "leverage", "direction", "benchmark_code",
                       "security_class", "etf_type", "selection_criteria",
                       "turnover", "expense_ratio")}}
    def etf_metadata(self, codes):
        out = {}
        for code in codes:
            if code not in self._ETF_DATA:
                continue
            values = {k: v for k, v in self._ETF_DATA[code].items() if k != "weights"}
            values.setdefault("security_class", "CE")
            values.setdefault("etf_type", "Stock")
            values.setdefault("turnover", max(1_000_000, values.get("aum", 0) / 100))
            values.setdefault("expense_ratio", 0.5)
            out[code] = values
        return out
    def etf_holding_weights_for_stock(self, codes, stock_code):
        return {code: self._ETF_DATA.get(code, {}).get("weights", {}).get(stock_code)
                for code in codes}
    def iter_etf_holdings(self, etf_code, *, page_size=1000):
        data = self._ETF_DATA.get(etf_code, {})
        weights = dict(data.get("weights", {}))
        residual = max(0.0, 100.0 - sum(weights.values()))
        if residual:
            weights[f"185:OTHER{etf_code.partition(':')[2]}"] = residual
        for rank, (code, weight) in enumerate(
                sorted(weights.items(), key=lambda item: (-item[1], item[0])), 1):
            yield {"code": code, "name": self.name_of(code),
                   "weight_pct": weight, "rank": rank}
    def fetch_klines(self, codes, *, count=60, **kw):
        out = {}
        for j, c in enumerate(codes):
            bars = []
            for k in range(90):
                # 60 pre-event bars, then 30 event-window bars
                di = 20260601 + k
                if k < 60: date_int = di
                else: date_int = 20260701 + (k-60)
                bars.append({"t": k, "close": 100+k+j, "volume": 1000+j if k < 60 else 2500+j, "date_int": date_int})
            out[c] = bars
        return out


class ScriptedLLM:
    """Relevance LLM whose score per code is looked up from a dict (default 1.0)."""
    def __init__(self, rel_by_code):
        self.rel_by_code = rel_by_code
        self.scored_codes = []
    def chat_json(self, system, user, **kw):
        if system == workflow_module.prompts.EVENT_ECOSYSTEM_SYS:
            return {"entities": []}
        if system == workflow_module.prompts.NARRATIVE_SYS:
            return _fake_narrative_response(user)
        if "Return JSON with keys" in user:
            return {"theme_cn": "AI 内存", "summary": "s", "thesis": "t",
                    "theme_direction": "bullish",
                    "direct_beneficiaries": [],
                    "picks_and_shovels": [], "second_order": [], "false_positives": [], "keywords": []}
        if "Score each candidate" in user:
            out = []
            for line in user.splitlines():
                if "|" in line and ":" in line.split("|")[0]:
                    code = line.split("|")[0].strip()
                    self.scored_codes.append(code)
                    out.append(_fake_stock_score(
                        line, self.rel_by_code.get(code, 1.0)))
            return out
        return []


def _rows(*codes):
    rows = []
    for index, code in enumerate(codes, 1):
        name = code.split(":")[-1]
        business, _, _, _ = _fake_candidate_evidence(code, name)
        rows.append({
            "code": code,
            "name": name,
            "rank": index,
            "company_introduction": business,
        })
    return rows


def test_screen_ranks_semantics_before_market_cap_order():
    # Threshold 3.3: later, stronger evidence must displace an earlier marginal pass.
    rel = {"169:A": 5.0, "169:B": 3.0, "169:C": 3.3, "169:D": 4.0, "169:E": 3.9}
    llm = ScriptedLLM(rel)
    wf = ThemeWorkflow(llm, FakeQuotes(), {
        "relevance_batch": 10,
        "relevance_threshold": 3.3,
        "etf_evidence_stock_limit": 3,
    })
    rows = _rows("169:A", "169:B", "169:C", "169:D", "169:E")
    chosen = wf.screen_until_target(rows, {}, {}, target=3, kind="stocks")
    assert [c["code"] for c in chosen] == ["169:A", "169:D", "169:E"]
    assert [c["code"] for c in wf._last_stock_evidence] == [
        "169:A", "169:D", "169:E",
    ]
    assert all(c["ai_relevance"] >= 3.3 for c in wf._last_stock_evidence)
    assert len(llm.scored_codes) == 5


def test_screen_excludes_momentum_from_priority_but_fills_public_stock_target():
    # Large moves and high RVOL cannot outrank relationship evidence, but the
    # frozen basket still fills from the remaining real candidates.
    rel = {"169:FMX": 1.0, "169:GGB": 3.0, "185:MU": 4.5}
    class GroundedRelationshipLLM(ScriptedLLM):
        def chat_json(self, system, user, **kw):
            if "Score each candidate" not in user:
                return super().chat_json(system, user, **kw)
            output = []
            for line in user.splitlines():
                if "|" not in line or ":" not in line.split("|")[0]:
                    continue
                code = line.split("|")[0].strip()
                self.scored_codes.append(code)
                output.append(_fake_stock_score(
                    line,
                    self.rel_by_code.get(code, 1.0),
                    event_phrase="AI memory component capacity growth",
                ))
            return output

    llm = GroundedRelationshipLLM(rel)
    wf = ThemeWorkflow(llm, FakeQuotes(), {"relevance_batch": 10, "relevance_threshold": 3.3})
    rows = _rows("169:FMX", "169:GGB", "185:MU")
    rows[0].update({"chg_pct": -40.0, "rvol_event": 8.0, "abnormal_return": -35.0})
    rows[1].update({"chg_pct": 35.0, "rvol_event": 7.0, "abnormal_return": 30.0})
    rows[2].update({"chg_pct": 1.0, "rvol_event": 1.0, "abnormal_return": 0.0})
    chosen = wf.screen_until_target(
        rows,
        {
            "theme_direction": "bullish",
            "input_theme": "AI memory component capacity growth",
        },
        {},
        target=3,
        kind="stocks",
    )
    assert [c["code"] for c in chosen] == ["185:MU", "169:GGB", "169:FMX"]
    assert all("weak_theme_fallback" not in candidate for candidate in chosen)
    assert wf._last_stock_evidence == [chosen[0]]


def test_screen_scans_deeper_until_target_met():
    # Only the deepest name qualifies; the scan must page past earlier batches.
    rel = {"169:Z": 4.0}
    llm = ScriptedLLM(rel)
    wf = ThemeWorkflow(llm, FakeQuotes(), {"relevance_batch": 2, "relevance_threshold": 3.3})
    rows = _rows("169:A", "169:B", "169:C", "169:D", "169:Z")
    chosen = wf.screen_until_target(rows, {}, {}, target=1, kind="stocks")
    assert [c["code"] for c in chosen] == ["169:Z"]
    assert [c["code"] for c in wf._last_stock_evidence] == ["169:Z"]
    assert len(llm.scored_codes) == 5  # scanned the whole universe to find it


def test_screen_scores_fixed_universe_before_truncation():
    rel = {c: 4.0 for c in [f"185:S{i}" for i in range(50)]}
    llm = ScriptedLLM(rel)
    wf = ThemeWorkflow(llm, FakeQuotes(), {
        "relevance_batch": 20,
        "relevance_threshold": 3.3,
        "etf_evidence_stock_limit": 8,
    })
    rows = _rows(*[f"185:S{i}" for i in range(50)])
    chosen = wf.screen_until_target(rows, {}, {}, target=8, kind="stocks")
    assert [c["code"] for c in chosen] == [f"185:S{i}" for i in range(8)]
    assert len(wf._last_stock_evidence) == 8
    assert len(llm.scored_codes) == 50


def test_screen_limit_is_an_exact_per_class_ceiling():
    # The scan consumes exactly N candidates and freezes a full basket even when
    # all scored relationships are weak.
    for limit in (100, 1000):
        for kind in ("stocks", "ETFs"):
            llm = ScriptedLLM({})
            wf = ThemeWorkflow(
                llm, FakeQuotes(),
                {"relevance_batch": 100, "relevance_threshold": 2.5},
            )
            rows = _rows(*[f"185:{kind[0]}{i}" for i in range(limit + 25)])
            chosen = wf.screen_until_target(
                rows, {}, {}, target=8, kind=kind, cap=limit)
            assert len(chosen) == 8
            assert [row["code"] for row in chosen] == [
                f"185:{kind[0]}{i}" for i in range(8)
            ]
            assert len(llm.scored_codes) == limit


def test_screen_stops_at_max_scan_and_fills_from_scanned_universe():
    # A bounded scan still freezes an exact basket from the real rows it saw.
    llm = ScriptedLLM({})  # everything defaults to relevance 1.0
    wf = ThemeWorkflow(llm, FakeQuotes(),
                       {"relevance_batch": 20, "relevance_threshold": 2.5, "max_scan": 40})
    rows = _rows(*[f"185:S{i}" for i in range(500)])
    chosen = wf.screen_until_target(rows, {}, {}, target=8, kind="stocks")
    assert [row["code"] for row in chosen] == [f"185:S{i}" for i in range(8)]
    assert len(llm.scored_codes) == 40  # bounded by max_scan, not the 500-name universe


def test_screen_max_scan_unbounded_when_zero():
    llm = ScriptedLLM({"185:S9": 4.0})  # only the deepest name qualifies
    wf = ThemeWorkflow(llm, FakeQuotes(),
                       {"relevance_batch": 5, "relevance_threshold": 2.5, "max_scan": 0})
    rows = _rows(*[f"185:S{i}" for i in range(10)])
    chosen = wf.screen_until_target(rows, {}, {}, target=1, kind="stocks")
    assert [c["code"] for c in chosen] == ["185:S9"]
    assert [c["code"] for c in wf._last_stock_evidence] == ["185:S9"]
    assert len(llm.scored_codes) == 10  # 0 disables the cap → scans until found


def test_relevance_prompt_keeps_theme_primary_and_article_secondary():
    class PromptTrackingLLM(FakeLLM):
        def __init__(self): self.user = ""
        def chat_json(self, system, user, **kw):
            self.user = user
            return super().chat_json(system, user, **kw)
    llm = PromptTrackingLLM()
    wf = ThemeWorkflow(llm, FakeQuotes())
    wf.score_relevance({"exact_theme": "AI memory", "thesis": "HBM demand"},
                       [{"code": "185:MU", "name": "Micron", "rvol_event": 1,
                         "chg_pct": 1, "abnormal_return": 1}],
                       {"title": "HBM catalyst", "url": "https://article", "text": "Specific HBM evidence"})
    assert "Specific HBM evidence" in llm.user
    assert "Exact trusted theme label (dominant): AI memory" in llm.user
    assert "Secondary article event evidence" in llm.user
    assert "primary reasoning material" not in llm.user


def test_bearish_relevance_prompt_targets_direct_downside_not_generic_hedges():
    class BearishPromptLLM:
        def chat_json(self, system, user, **kw):
            self.system = system
            self.user = user
            return [
                {"candidate_id": "S0001", "market_code": "185:NVDA",
                 "theme_relevance": 5, "article_support": 1,
                 "exposure_type": "direct", "confidence": 0.9,
                 "impact_channel": "revenue_demand",
                 "theme_specificity": "company_specific", "materiality": "high",
                 "evidence_strength": "explicit",
                 "reason": "AI demand slowdown pressures accelerator revenue",
                 "article_reason": "Article reports AI spending vulnerability"},
                {"candidate_id": "S0002", "market_code": "185:CME",
                 "theme_relevance": 1, "article_support": 0,
                 "exposure_type": "unclear", "confidence": 0.9,
                 "impact_channel": "none", "theme_specificity": "none",
                 "materiality": "low", "evidence_strength": "none",
                 "reason": "Generic volatility beneficiary lacks direct downside",
                 "article_reason": "not mentioned"},
            ]

    llm = BearishPromptLLM()
    scores = ThemeWorkflow(llm, FakeQuotes()).score_relevance(
        {"theme_direction": "bearish", "thesis": "AI valuations may de-rate."},
        [{"code": "185:NVDA", "name": "NVIDIA"},
         {"code": "185:CME", "name": "CME Group"}],
        {"title": "AI bubble warning", "text": "AI spending and valuations are vulnerable."},
    )

    assert scores["185:NVDA"]["ai_relevance"] == 5
    assert scores["185:CME"]["ai_relevance"] == 1
    assert "generic hedges, brokers, miners" in llm.system
    assert '"theme_direction": "bearish"' in llm.user


def test_score_relevance_handles_wrapped_json_and_code_variants():
    """The LLM sometimes wraps rows in an object or echoes codes with different
    whitespace/case; both must still resolve, not silently become relevance 1."""
    class WrappingLLM:
        def chat_json(self, system, user, **kw):
            line = next(line for line in user.splitlines()
                        if "|" in line and ":" in line.split("|")[0])
            row = _fake_stock_score(line, 5)
            row["market_code"] = " 185:mu "
            return {"results": [row]}
    wf = ThemeWorkflow(WrappingLLM(), FakeQuotes())
    scores = wf.score_relevance({"thesis": "HBM"},
                                [{"code": "185:MU", "name": "Micron", "rvol_event": 1,
                                  "chg_pct": 1, "abnormal_return": 1}])
    assert scores["185:MU"]["ai_relevance"] == 5
    assert scores["185:MU"]["relevance_status"] == "scored"


def test_score_relevance_empty_response_marks_missing():
    class EmptyLLM:
        def chat_json(self, system, user, **kw):
            return []
    wf = ThemeWorkflow(EmptyLLM(), FakeQuotes())
    rows = [{"code": f"185:X{i}", "name": f"X{i}"} for i in range(5)]
    scores = wf.score_relevance({}, rows)
    assert len(scores) == 5
    assert all(v["relevance_status"] == "missing" for v in scores.values())
    assert all(v["ai_relevance"] == 1.0 for v in scores.values())


def test_score_relevance_provider_error_fails_batch_closed_and_continues():
    class GatewayBadRequest(Exception):
        pass

    class FlakyLLM:
        def __init__(self):
            self.calls = 0

        def chat_json(self, system, user, **kw):
            self.calls += 1
            if self.calls == 1:
                raise GatewayBadRequest("request rejected by gateway")
            return [
                _fake_stock_score(line, 5)
                for line in user.splitlines()
                if "|" in line and ":" in line.split("|")[0]
            ]

    logs = []
    original_log = workflow_module._log
    workflow_module._log = logs.append
    try:
        llm = FlakyLLM()
        scores = ThemeWorkflow(
            llm, FakeQuotes(), {"relevance_batch": 2}
        ).score_relevance({}, _rows("185:A", "185:B", "185:C", "185:D"))
    finally:
        workflow_module._log = original_log

    assert llm.calls == 3
    assert all(scores[code]["relevance_status"] == "scored"
               for code in ("185:A", "185:B", "185:C", "185:D"))
    assert all(scores[code]["relevance_retry"] == "recovered"
               for code in ("185:A", "185:B"))
    assert any("relevance batch failed" in line and "2 candidates unresolved" in line
               for line in logs)


def test_coerce_relevance_rows_variants():
    assert ThemeWorkflow._coerce_relevance_rows([{"market_code": "x"}]) == [{"market_code": "x"}]
    assert ThemeWorkflow._coerce_relevance_rows({"scores": [{"market_code": "y"}]}) == [{"market_code": "y"}]
    assert ThemeWorkflow._coerce_relevance_rows({"market_code": "z"}) == [{"market_code": "z"}]
    assert ThemeWorkflow._coerce_relevance_rows("nonsense") == []


def _narrative_records(user_prompt):
    payload = user_prompt.split("Records (JSON):\n", 1)[1]
    payload = payload.split("\n\nWriting requirements:", 1)[0]
    return json.loads(payload)


def test_stock_narrative_excludes_internal_inputs_and_includes_evidence():
    class CapturingLLM:
        def chat_json(self, system, user, **kw):
            self.system = system
            self.user = user
            return {"items": [{
                "candidate_id": "S0001",
                "market_code": "185:MU",
                "theme_rationale": {
                    "type": "multilingual",
                    "en": (
                        "Micron sells HBM memory chips; HBM serves AI accelerator memory "
                        "demand and can lift memory revenue and earnings."
                    ),
                    "zh": (
                        "Micron主营HBM存储芯片；AI加速器需求增长可提升"
                        "存储业务收入和盈利。"
                    ),
                },
            }]}

    llm = CapturingLLM()
    wf = ThemeWorkflow(llm, FakeQuotes())
    chosen = [{"code": "185:MU", "name": "Micron", "exposure_type": "direct",
               "ai_relevance": 5, "confidence": 1.0,
               "company_introduction": "Micron sells DRAM and HBM memory chips",
               "business_fact": "Micron sells HBM memory chips",
               "theme_connection": "HBM serves AI accelerator memory demand",
               "financial_pathway": "Higher HBM demand can lift memory revenue and earnings",
               "reason": "HBM products serve AI accelerator memory demand",
               "rvol_event": 2.4, "abnormal_return": 4.1,
               "volume_confirmed": True, "chg_pct": 3.0}]
    result = wf.narrate(
        {"theme": "AI memory"},
        {"summary": "AI infrastructure demand is expanding.", "thesis": "HBM demand grows."},
        chosen, "stock")

    (record,) = _narrative_records(llm.user)
    assert set(record) == {
        "candidate_id", "market_code", "name", "company business",
        "business fact", "event relationship", "financial pathway",
        "structural exposure evidence",
    }
    assert record["structural exposure evidence"] == (
        "HBM products serve AI accelerator memory demand")
    serialized = json.dumps(record)
    for internal in ("ai_relevance", "exposure_type", "confidence", "rvol",
                     "abnormal_return", "volume_confirmed", "chg_pct", "score"):
        assert internal not in serialized
    assert "never expose or refer to internal scores" in llm.system
    assert "relevance 5.0" in llm.system
    assert "seasoned sell-side equity broker" in llm.system
    assert "20-45 English words" in llm.user
    assert "do not spend words saying it is" in llm.user
    assert "write a balanced theme rationale" not in llm.user
    assert "35-70 words" not in llm.user
    assert '"type": "multilingual"' in llm.user
    assert '"en"' in llm.user and '"zh"' in llm.user
    rationale = result["185:MU"]["theme_rationale"]
    assert rationale["type"] == "multilingual"
    assert rationale["en"].startswith("Micron sells HBM")
    assert rationale["zh"].startswith("Micron主营")


def test_stock_narrative_uses_only_literal_verified_article_evidence():
    candidate = {
        "code": "185:MU", "name": "Micron",
        "company_introduction": "Micron sells DRAM and HBM memory chips",
        "business_fact": "Micron sells HBM memory chips",
        "theme_connection": "HBM serves AI accelerator memory demand",
        "financial_pathway": "Higher HBM demand can lift revenue and earnings",
        "article_reason": "Invented scoring-model customer claim",
        "article_anchor_evidence": "Invented extraction-model contract claim",
        "event_operating_evidence": "Invented ecosystem-model partnership claim",
        "verified_article_evidence": "The article says Micron supplies HBM memory.",
    }
    record = ThemeWorkflow._narrative_record(candidate, "stock", {
        "input_theme": "AI memory demand",
        "catalyst": {"what_happened": "Invented catalyst claim"},
        "operating_evidence": [{
            "entity_name": "Micron", "fact": "Invented operating claim",
            "directional_pathway": "Invented pathway",
        }],
    })
    serialized = json.dumps(record)
    assert record["verified article evidence"] == (
        "The article says Micron supplies HBM memory.")
    assert record["event/theme"] == "AI memory demand"
    for unsafe in (
        "Invented scoring-model", "Invented extraction-model",
        "Invented ecosystem-model", "Invented catalyst",
        "Invented operating", "Invented pathway",
    ):
        assert unsafe not in serialized


def test_verified_candidate_article_evidence_returns_source_text_not_model_hint():
    candidate = {"code": "185:MU", "name": "Micron Technology"}
    entity = {
        "name": "Micron", "ticker": "MU",
        "operating_evidence": (
            "Micron has an invented exclusive customer contract worth $9 billion."),
    }
    evidence = workflow_module._verified_candidate_article_evidence(
        candidate,
        {
            "title": "Micron expands HBM memory output",
            "text": "Micron said AI accelerator demand is increasing HBM orders.",
        },
        entity,
    )
    assert "Micron expands HBM memory output" in evidence
    assert "AI accelerator demand is increasing HBM orders" in evidence
    assert "exclusive customer contract" not in evidence
    assert "$9 billion" not in evidence


def test_etf_narrative_uses_raw_holdings_without_aggregate_calculations():
    class CapturingLLM:
        def chat_json(self, system, user, **kw):
                self.user = user
                return [{
                    "candidate_id": "S0001",
                    "market_code": "185:SMH",
                "theme_rationale": {
                    "type": "multilingual",
                    "en": (
                        "The fund holds Micron and semiconductor-equipment suppliers tied to HBM capacity. "
                        "Its broader portfolio can dilute that connection, and holdings may change."
                    ),
                    "zh": (
                        "该基金持有美光及与HBM产能相关的半导体设备供应商。"
                        "其更广泛的投资组合可能稀释主题联系，且基金持仓可能变化。"
                    ),
                },
            }]

    llm = CapturingLLM()
    wf = ThemeWorkflow(llm, FakeQuotes())
    chosen = [{
        "code": "185:SMH", "name": "VanEck Semiconductor ETF",
        "static_theme_exposure": 0.8, "preselect_score": 0.8,
        "direction": "Long", "leverage": 1.0,
            "theme_weight_pct": 35.2, "theme_breadth": 3,
            "matched_holdings": [
                {"code": "185:MU", "ticker": "MU", "name": "Micron",
                 "weight_pct": 8.21},
                {"code": "185:AMAT", "ticker": "AMAT", "weight_pct": 6.45},
        ],
        "pool_labels": ["Semiconductor ETFs"],
        "fund_niche": "Semiconductors",
    }]
    result = wf.narrate(
        {"theme": "AI memory"},
        {"summary": "HBM capacity is expanding.", "thesis": "Equipment demand may rise."},
        chosen, "ETF")

    (record,) = _narrative_records(llm.user)
    assert record["relevant holdings"] == [
        "MU (8.21% of the fund)", "AMAT (6.45% of the fund)"]
    assert record["fund context"] == ["Semiconductors"]
    assert record["theme categories"] == ["Semiconductor ETFs"]
    assert "fund direction" not in record
    assert "daily leverage multiple" not in record
    assert "ordinary unleveraged, non-inverse ETF" in llm.user
    serialized = json.dumps(record)
    for internal in ("static_theme_exposure", "preselect_score", "theme_weight_pct",
                     "theme_breadth", "aggregate", "35.2"):
        assert internal not in serialized
    rationale = result["185:SMH"]["theme_rationale"]
    assert rationale["en"].startswith("The fund holds Micron")
    assert rationale["zh"].startswith("该基金持有")


def test_internal_narrative_is_rejected_and_uses_safe_stock_fallback():
    wf = ThemeWorkflow(FakeLLM(), FakeQuotes())
    chosen = [{
        "code": "185:MU", "name": "Micron", "ai_relevance": 5,
        "confidence": 0.9, "exposure_type": "direct",
        "reason": "HBM products serve AI accelerator memory demand",
    }]
    narrative = {"185:MU": {"theme_rationale": {
        "type": "multilingual",
        "en": "The model assigned relevance 5.0 because ai_relevance is high.",
        "zh": "内部评分显示相关性5.0，因此该股票值得关注。",
    }}}
    (item,) = wf._assemble(chosen, narrative, "2026-07-09")
    fallback = item["theme_rationale"]
    assert fallback != narrative["185:MU"]["theme_rationale"]
    assert "internal" not in fallback["en"].lower()
    assert "relevance" not in fallback["en"].lower()
    assert any(term in fallback["en"].lower() for term in (
        "revenue", "margin", "earnings", "sales", "profits",
    ))
    assert ThemeWorkflow._validated_rationale_text(
        "The fund has combined exposure of 35.2% across the selected holdings.") is None
    assert ThemeWorkflow._validated_theme_rationale({
        "type": "multilingual",
        "en": "Micron supplies HBM, while the financial contribution remains unquantified.",
        "zh": "美光供应HBM，但内部评分为5.0。",
    }) is None
    assert ThemeWorkflow._validated_theme_rationale({
        "type": "multilingual", "en": "Micron supplies HBM.", "cn": "美光供应HBM。"
    }) is None
    assert ThemeWorkflow._validated_theme_rationale({
        "type": "multilingual", "en": "美光供应高带宽存储。", "zh": "美光供应高带宽存储。"
    }) is None


def test_rationale_rejects_selection_fit_and_method_language():
    safe_en = "Micron supplies HBM used in AI accelerators, while financial materiality is unquantified."
    safe_zh = "美光供应用于人工智能加速器的高带宽存储，但其财务重要性尚未量化。"
    for text in (
        "Micron aligns with the theme through HBM.",
        "Micron aligns to the theme through HBM.",
        "Micron is a fit for the theme through HBM.",
        "After identifying Micron, the HBM business was reviewed.",
        "Micron is a qualifying name.",
        "Micron has strong alignment with the theme.",
        "Micron is a thematic match.",
        "Following identification, Micron's HBM business was reviewed.",
        "Micron was selected after evaluation of its HBM business.",
        "Micron matches the theme because it supplies HBM.",
    ):
        assert ThemeWorkflow._validated_theme_rationale({
            "type": "multilingual", "en": text, "zh": safe_zh}) is None
    for text in (
        "美光的HBM业务契合本主题。",
        "美光的HBM业务符合这一主题。",
        "美光的HBM业务与该主题契合。",
        "美光的HBM业务契合了本主题。",
        "美光经过筛选后入选。",
        "美光被识别为相关公司。",
    ):
        assert ThemeWorkflow._validated_theme_rationale({
            "type": "multilingual", "en": safe_en, "zh": text}) is None


def test_rationale_rejects_redundant_sensitivity_boilerplate():
    assert ThemeWorkflow._validated_theme_rationale({
        "type": "multilingual",
        "en": (
            "Micron has memory exposure, while available disclosures do not "
            "quantify the sensitivity."
        ),
        "zh": "美光拥有存储业务敞口，但现有资料尚未量化敏感度。",
    }) is None


def test_weak_theme_fallback_preserves_valid_broker_rationale():
    wf = ThemeWorkflow(FakeLLM(), FakeQuotes())
    candidate = {
        "code": "185:MU", "name": "Micron", "weak_theme_fallback": True,
        "ai_relevance": 3, "confidence": 0.6, "exposure_type": "direct",
        "reason": "HBM products supply AI accelerators and capture infrastructure demand",
    }
    supplied = {
        "type": "multilingual",
        "en": (
            "Micron's HBM products supply AI accelerators, positioning its memory "
            "business to turn stronger infrastructure orders into revenue and margin upside."
        ),
        "zh": (
            "美光的HBM产品用于AI加速器，有望承接基础设施需求增长，并将订单提升"
            "转化为收入和利润率上行。"
        ),
    }

    assert wf._validated_candidate_rationale(candidate, supplied) == supplied
    (item,) = wf._assemble(
        [candidate], {"185:MU": {"theme_rationale": supplied}},
        "2026-07-09", "bullish",
    )
    assert item["theme_rationale"] == supplied


def test_weak_theme_fallback_uses_same_code_broker_copy_when_narrative_is_unsafe():
    wf = ThemeWorkflow(FakeLLM(), FakeQuotes())
    candidate = {
        "code": "185:MU", "name": "Micron", "weak_theme_fallback": True,
        "ai_relevance": 1, "confidence": 0.3, "exposure_type": "unclear",
        "reason": "No clear earnings link to the theme",
    }
    unsafe = {
        "type": "multilingual",
        "en": "The model selected Micron because its internal relevance score is high.",
        "zh": "内部评分较高，因此模型筛选了美光。",
    }

    (item,) = wf._assemble(
        [candidate], {"185:MU": {"theme_rationale": unsafe}},
        "2026-07-09", "bullish",
    )
    rationale = item["theme_rationale"]
    assert "secondary watchlist" not in rationale["en"].lower()
    assert "high-conviction" not in rationale["en"].lower()
    assert "needs to emerge" not in rationale["en"].lower()
    assert any(term in rationale["en"].lower() for term in (
        "revenue", "margin", "earnings", "profits",
    ))


def test_stock_fallback_without_event_evidence_is_concise_broker_copy():
    rationale = ThemeWorkflow._fallback_theme_rationale({
        "code": "185:XYZ", "name": "Example Corp", "reason": "",
        "company_introduction": "Example Corp sells industrial equipment",
    }, "bullish", theme="industrial automation")
    assert rationale["en"].startswith(
        "Example Corp (XYZ): Example Corp sells industrial equipment"
    )
    assert "industrial automation" in rationale["en"]
    assert any(term in rationale["en"].lower() for term in (
        "revenue", "margin", "earnings", "profits",
    ))
    assert "watchlist" not in rationale["en"].lower()


def test_inverse_etf_record_and_fallback_are_objective_and_risk_explicit():
    candidate = {
        "code": "185:NVDQ",
        "name": "Daily 2X Short NVDA ETF",
        "static_theme_exposure": 0.8,
        "direction": "Short",
        "leverage": 2,
        "benchmark": "185:NVDA",
        "mandate": "Seek twice the inverse of NVDA's daily return",
        "selection_criteria": "Daily inverse exposure to the reference security",
        "related_stock_codes": {"185:NVDA"},
        "single_stock_inverse": True,
        "is_inverse": True,
        "matched_holdings": [],
    }
    record = ThemeWorkflow._narrative_record(candidate, "ETF")
    rationale = ThemeWorkflow._fallback_theme_rationale(candidate)

    assert record["fund direction"] == "Short"
    assert record["daily leverage multiple"] == 2
    assert record["related underlying securities"] == ["NVDA"]
    assert record["single-underlying inverse mandate"] is True
    assert record["fund mandate"] == "Seek twice the inverse of NVDA's daily return"
    assert record["reference benchmark"] == "185:NVDA"
    assert record["index construction facts"] == "Daily inverse exposure to the reference security"
    assert "daily reset" in rationale["en"].lower()
    assert "compounding" in rationale["en"].lower()
    assert "path dependence" in rationale["en"].lower()
    assert "concentration risk" in rationale["en"].lower()
    assert "每日重置" in rationale["zh"]
    assert "路径依赖" in rationale["zh"]
    assert ThemeWorkflow._validated_candidate_rationale(candidate, rationale) == rationale


def test_leveraged_long_etf_record_keeps_material_direction_and_multiple():
    candidate = {
        "code": "185:NVDL", "name": "GraniteShares 2x Long NVDA ETF",
        "static_theme_exposure": 0.8, "direction": "Long", "leverage": 2,
    }
    record = ThemeWorkflow._narrative_record(candidate, "ETF")
    assert record["fund direction"] == "Long"
    assert record["daily leverage multiple"] == 2

    one_x_inverse = ThemeWorkflow._narrative_record({
        "code": "185:SH", "name": "Short S&P 500 ETF",
        "static_theme_exposure": 0.8, "direction": "Short", "leverage": 1,
        "is_inverse": True,
    }, "ETF")
    assert one_x_inverse["fund direction"] == "Short"
    assert one_x_inverse["daily leverage multiple"] == 1


def test_low_liquidity_leveraged_public_fallback_keeps_all_bilingual_safeguards():
    candidate = {
        "code": "185:NVDL",
        "name": "Daily 2x Long NVDA ETF",
        "selection_lane": "single_stock_leveraged",
        "single_stock_leveraged": True,
        "underlying_code": "185:NVDA",
        "benchmark_code": "185:NVDA",
        "static_theme_exposure": 0.8,
        "direction": "Long",
        "leverage": 2,
        "low_liquidity": True,
        "low_liquidity_reasons": ["aum_below_25m", "turnover_below_1m"],
        "aum": 10_000_000,
        "turnover": 500_000,
        "matched_holdings": [],
    }
    (public_item,) = ThemeWorkflow(FakeLLM(), FakeQuotes())._assemble(
        [candidate], {}, "2026-07-09", "bullish",
    )
    rationale = public_item["theme_rationale"]
    en = rationale["en"].lower()
    zh = rationale["zh"]

    assert set(public_item) == {
        "market_code", "theme_rationale", "Theme exposure", "event_date",
    }
    assert "nvda" in en and "2 times" in en and "positive daily return" in en
    assert all(term in en for term in (
        "daily reset", "compounding", "path dependence", "concentration risk",
        "low liquidity", "spreads", "trading impact",
    ))
    assert "NVDA" in zh and re.search(r"2倍(?:的)?正向", zh)
    assert all(term in zh for term in (
        "每日重置", "复利", "路径依赖", "集中度风险", "流动性", "买卖价差", "交易冲击",
    ))
    assert ThemeWorkflow._validated_candidate_rationale(
        candidate, rationale) == rationale


def test_ordinary_etf_rationale_rejects_redundant_one_x_long_labels():
    candidate = {
        "code": "185:SMH", "name": "VanEck Semiconductor ETF",
        "static_theme_exposure": 0.8, "direction": "Long", "leverage": 1,
        "matched_holdings": [
            {"code": "185:MU", "ticker": "MU", "weight_pct": 8.2},
        ],
    }
    redundant = {
        "type": "multilingual",
        "en": (
            "This 1.0x long ETF holds MU, linking the portfolio to memory demand. "
            "Holdings and weights can change as the fund rebalances."
        ),
        "zh": (
            "这只1倍做多ETF持有MU，因此组合与存储需求相关。"
            "基金再平衡可能改变持仓及权重。"
        ),
    }
    assert ThemeWorkflow._validated_candidate_rationale(candidate, redundant) is None

    safe_en = (
        "The fund holds MU, linking the portfolio to memory demand. "
        "Holdings and weights can change as the fund rebalances."
    )
    safe_zh = "该基金持有MU，因此组合与存储需求相关；基金再平衡可能改变持仓及权重。"
    for fragment in (
        "1x Long ETF", "1.0× LONG fund", "1 times long product",
        "long at 1.0x exposure", "long allocation at one-times exposure",
    ):
        assert ThemeWorkflow._validated_candidate_rationale(candidate, {
            "type": "multilingual", "en": f"{fragment}. {safe_en}", "zh": safe_zh,
        }) is None
    for fragment in (
        "1.0倍做多ETF", "1倍多头基金", "一倍做多产品", "１．０倍做多ETF",
    ):
        assert ThemeWorkflow._validated_candidate_rationale(candidate, {
            "type": "multilingual", "en": safe_en,
            "zh": f"这只{fragment}持有MU。基金再平衡可能改变持仓及权重。",
        }) is None

    fallback = ThemeWorkflow._fallback_theme_rationale(candidate)
    assert re.search(r"1(?:\.0+)?\s*x|\blong\s+(?:fund|etf)\b",
                     fallback["en"], re.I) is None
    assert re.search(r"(?:1(?:\.0+)?|一)\s*倍|多头基金|做多ETF",
                     fallback["zh"], re.I) is None


def test_bearish_fallbacks_preserve_downside_pathways_in_both_languages():
    stock = {
        "code": "185:NVDA", "name": "NVIDIA",
        "reason": "Lower AI infrastructure budgets could reduce accelerator demand",
    }
    long_etf = {
        "code": "185:SMH", "name": "Semiconductor ETF",
        "static_theme_exposure": 0.4, "direction": "Long",
        "matched_holdings": [
            {"code": "185:NVDA", "ticker": "NVDA", "weight_pct": 10.0},
        ],
    }
    etf_rationale = ThemeWorkflow._fallback_theme_rationale(long_etf, "bearish")

    stock_rationale = ThemeWorkflow._fallback_theme_rationale(stock, "bearish")
    assert any(term in stock_rationale["en"].lower() for term in (
        "pressure", "weaker", "reduce", "weigh",
    ))
    assert any(term in stock_rationale["zh"] for term in (
        "压低", "承压", "下降", "减少",
    ))
    assert "declines" in etf_rationale["en"].lower()
    assert "下跌" in etf_rationale["zh"]
    assert ThemeWorkflow._validated_theme_rationale(etf_rationale) == etf_rationale


def test_bearish_long_basket_rejects_bullish_llm_prose_and_uses_downside_fallback():
    candidate = {
        "code": "185:VDC", "name": "Vanguard Consumer Staples ETF",
        "static_theme_exposure": 0.6, "direction": "Long", "leverage": 1,
        "selection_lane": "multi_stock_basket",
        "bearish_downside_exposure": True,
        "matched_holdings": [
            {"code": "185:WMT", "ticker": "WMT", "weight_pct": 12.0},
            {"code": "185:COST", "ticker": "COST", "weight_pct": 10.0},
        ],
    }
    record = ThemeWorkflow._narrative_record(candidate, "ETF")
    assert "required downside framing" in record
    assert "vulnerable" in record["required downside framing"]

    bullish = {
        "type": "multilingual",
        "en": (
            "The fund holds WMT and COST. Rising household demand could lift retailer "
            "revenue and increase the fund's value as consumer spending accelerates."
        ),
        "zh": (
            "该基金持有WMT和COST。家庭需求回升可能提振零售商收入，并在消费加速时"
            "提高基金净值。"
        ),
    }
    assert ThemeWorkflow._validated_candidate_rationale(candidate, bullish) is None

    fallback = ThemeWorkflow._fallback_theme_rationale(candidate, "bearish")
    assert ThemeWorkflow._validated_candidate_rationale(candidate, fallback) == fallback
    assert "declines" in fallback["en"].lower()
    assert "压低基金净值" in fallback["zh"]


def test_sector_inverse_fallback_discloses_concentration_when_supported():
    candidate = {
        "code": "185:SOXS", "name": "Semiconductor Bear 3X ETF",
        "static_theme_exposure": 0.7, "direction": "Short", "leverage": 3,
        "is_inverse": True, "related_stock_codes": {"185:NVDA"},
        "pool_labels": ["Semiconductor ETFs"], "matched_holdings": [],
    }
    rationale = ThemeWorkflow._fallback_theme_rationale(candidate, "bearish")
    assert "concentration risk" in rationale["en"].lower()
    assert "集中度风险" in rationale["zh"]
    assert "daily return of NVDA" not in rationale["en"]

    niche_only = dict(candidate, pool_labels=[], fund_niche="Semiconductors")
    niche_rationale = ThemeWorkflow._fallback_theme_rationale(niche_only, "bearish")
    assert "concentration risk" in niche_rationale["en"].lower()


def test_incomplete_inverse_llm_rationale_is_replaced_with_risk_fallback():
    wf = ThemeWorkflow(FakeLLM(), FakeQuotes())
    candidate = {
        "code": "185:NVDQ", "name": "Daily 2X Short NVDA ETF",
        "static_theme_exposure": 0.8, "direction": "Short", "leverage": 2,
        "benchmark": "185:NVDA", "single_stock_inverse": True,
        "is_inverse": True, "matched_holdings": [],
    }
    incomplete = {"185:NVDQ": {"theme_rationale": {
        "type": "multilingual",
        "en": "The fund seeks 2x daily inverse exposure to NVDA.",
        "zh": "该基金力求实现NVDA单日收益的2倍反向表现。",
    }}}
    (item,) = wf._assemble([candidate], incomplete, "2026-07-09", "bearish")
    rationale = item["theme_rationale"]
    assert "daily reset" in rationale["en"].lower()
    assert "compounding" in rationale["en"].lower()
    assert "concentration risk" in rationale["en"].lower()


def test_inverse_rationale_requires_reference_and_actual_risk_language():
    wf = ThemeWorkflow(FakeLLM(), FakeQuotes())
    candidate = {
        "code": "185:NVDQ", "name": "Daily 2X Short NVDA ETF",
        "static_theme_exposure": 0.8, "direction": "Short", "leverage": 2,
        "benchmark": "185:NVDA", "single_stock_inverse": True,
        "is_inverse": True, "matched_holdings": [],
    }
    missing_reference = {"type": "multilingual", "en": (
        "The fund seeks 2x daily inverse market exposure. Daily reset and compounding "
        "create path dependence that can make returns differ, while concentration adds risk."
    ), "zh": (
        "该基金力求实现2倍单日反向市场敞口。每日重置与复利带来路径依赖，可能使收益产生"
        "差异，而集中敞口会增加风险。"
    )}
    denying_risk = {"type": "multilingual", "en": (
        "The fund seeks 2x daily inverse exposure to NVDA. Daily reset and compounding "
        "eliminate path dependence, while concentrated exposure improves targeting without risk."
    ), "zh": (
        "该基金力求实现NVDA单日收益的2倍反向表现。每日重置与复利消除路径依赖，"
        "集中敞口提高针对性且没有风险。"
    )}

    assert ThemeWorkflow._validated_candidate_rationale(
        candidate, missing_reference) is None
    assert ThemeWorkflow._validated_candidate_rationale(candidate, denying_risk) is None

    for unsafe in (missing_reference, denying_risk):
        (item,) = wf._assemble(
            [dict(candidate)], {"185:NVDQ": {"theme_rationale": unsafe}},
            "2026-07-09", "bearish",
        )
        assert "NVDA" in item["theme_rationale"]["en"]
        assert "concentration risk" in item["theme_rationale"]["en"].lower()
        assert "eliminate" not in item["theme_rationale"]["en"].lower()


def test_inverse_fallback_states_when_reference_fact_is_unavailable():
    candidate = {
        "code": "185:SECT", "name": "Daily 3X Sector Bear ETF",
        "static_theme_exposure": 0.7, "direction": "Short", "leverage": 3,
        "is_inverse": True, "fund_niche": "Semiconductors",
        "matched_holdings": [],
    }
    rationale = ThemeWorkflow._fallback_theme_rationale(candidate, "bearish")
    assert "3x daily inverse mandate" in rationale["en"]
    assert "约3倍单日反向策略" in rationale["zh"]
    assert "daily reset" in rationale["en"].lower()
    assert "path dependence" in rationale["en"].lower()
    assert "S&P 500" not in rationale["en"]

    invented_reference = {"type": "multilingual", "en": (
        "The fund seeks 3x daily inverse exposure to the S&P 500. Daily reset and "
        "compounding create path dependence that can make returns differ, while "
        "sector concentration adds risk."
    ), "zh": (
        "该基金力求实现标普500单日收益的3倍反向表现。每日重置与复利带来路径依赖，"
        "可能使收益产生差异，而行业集中会增加风险。"
    )}
    assert ThemeWorkflow._validated_candidate_rationale(
        candidate, invented_reference) is None


def test_malformed_narrative_response_uses_safe_fallback():
    class MalformedLLM:
        def chat_json(self, system, user, **kw):
            return {"theme_rationale": "This is not the required array."}

    wf = ThemeWorkflow(MalformedLLM(), FakeQuotes())
    chosen = [{
        "code": "185:MU", "name": "Micron", "ai_relevance": 5,
        "confidence": 0.9, "exposure_type": "direct",
        "reason": "HBM products serve AI accelerator memory demand",
    }]
    narrative = wf.narrate(
        {"theme": "AI memory"}, {"summary": "", "thesis": ""}, chosen, "stock")
    assert narrative == {}
    (item,) = wf._assemble(
        chosen, narrative, "2026-07-09", theme="AI memory")
    assert item["market_code"] == "185:MU"
    assert "Micron" in item["theme_rationale"]["en"]
    assert any(term in item["theme_rationale"]["en"].lower() for term in (
        "revenue", "margin", "earnings", "profits",
    ))


def test_missing_etf_narrative_fallback_omits_aggregate_math():
    wf = ThemeWorkflow(FakeLLM(), FakeQuotes())
    chosen = [{
        "code": "185:SMH", "name": "VanEck Semiconductor ETF",
        "static_theme_exposure": 0.8, "theme_weight_pct": 35.2,
        "reason": "Holds 3 selected theme stocks (35.2% total)",
        "matched_holdings": [
            {"code": "185:MU", "ticker": "MU", "weight_pct": 8.21},
            {"code": "185:AMAT", "ticker": "AMAT", "weight_pct": 6.45},
        ],
    }]
    (item,) = wf._assemble(chosen, {}, "2026-07-09")
    rationale = item["theme_rationale"]
    assert set(rationale) == {"type", "en", "zh"}
    assert rationale["type"] == "multilingual"
    assert "MU" in rationale["en"] and "AMAT" in rationale["en"]
    assert "MU" in rationale["zh"] and "AMAT" in rationale["zh"]
    assert "35.2" not in rationale["en"] and "35.2" not in rationale["zh"]
    assert "total" not in rationale["en"].lower()
    assert "why_bullish" not in item


class CountingQuotes(FakeQuotes):
    """FakeQuotes that records which codes had klines fetched."""
    def __init__(self):
        self.kline_calls = []

    def fetch_klines(self, codes, *, count=60, **kw):
        self.kline_calls.append(list(codes))
        return super().fetch_klines(codes, count=count, **kw)


def test_add_market_features_skips_names_with_features():
    q = CountingQuotes()
    wf = ThemeWorkflow(FakeLLM(), q)
    cands = [{"code": "185:MU", "name": "Micron", "chg_pct": 1.0}]  # already featured
    wf.add_market_features(cands, 20260709, 0.0)   # should be a no-op
    assert q.kline_calls == []


def test_market_strength_uses_absolute_change_and_independent_volume():
    wf = ThemeWorkflow(FakeLLM(), FakeQuotes(), {"rvol_threshold": 1.5})
    cands = [
        {"code": "185:NEG", "chg_pct": -8.0, "rvol_event": 1.0},
        {"code": "185:POS", "chg_pct": 8.0, "rvol_event": 1.0},
        {"code": "185:SMALL", "chg_pct": 2.0, "rvol_event": 1.0},
        {"code": "185:MISSING", "chg_pct": None, "rvol_event": 2.0},
    ]
    wf._mark_volume_confirmed(cands)

    assert cands[0]["change_magnitude_percentile"] == cands[1]["change_magnitude_percentile"]
    assert cands[0]["market_strength"] == cands[1]["market_strength"]
    assert cands[0]["market_strength"] > cands[2]["market_strength"]
    assert cands[3]["change_magnitude_percentile"] == 0.0
    assert cands[3]["market_strength"] == 0.5  # volume contributes independently


def test_end_to_end_mocked():
    wf = ThemeWorkflow(FakeLLM(), FakeQuotes(), {"stock_universe": 20, "etf_universe": 20})

    res = wf.run({"theme": "AI memory", "date": "2026-07-09", "url": "https://example.com/x"})
    assert res["theme_cn"] == "AI 内存"
    assert len(res["ThemeStocks"]) == 8
    assert len(res["ThemeEtfs"]) == 5
    assert 4 <= len(res["ThemeFAQ"]) <= 8
    assert set(res["ThemeStocks"][0]) == {
        "market_code", "theme_rationale", "Theme exposure", "event_date"}
    for coll in ("ThemeStocks", "ThemeEtfs"):
        codes = [x["market_code"] for x in res[coll]]
        assert len(codes) == len(set(codes))
        scores = [x["Theme exposure"] for x in res[coll]]
        # Both public ladders describe each class's already-frozen output order.
        expected_scores = {
            "ThemeStocks": [5.0, 4.7, 4.4, 4.1, 3.9, 3.6, 3.3, 3.0],
            "ThemeEtfs": [5.0, 4.5, 4.0, 3.5, 3.0],
        }
        assert scores == expected_scores[coll]
        assert all("score" not in x and "score_components" not in x for x in res[coll])
        assert all("why_bullish" not in x for x in res[coll])
        assert all(set(x) == {"market_code", "theme_rationale", "Theme exposure", "event_date"}
                   for x in res[coll])
        assert all(x["theme_rationale"]["type"] == "multilingual" for x in res[coll])
        assert all(set(x["theme_rationale"]) == {"type", "en", "zh"} for x in res[coll])
        assert all(x["theme_rationale"]["en"] and x["theme_rationale"]["zh"] for x in res[coll])


class _StockLedThemeLLM:
    """Small deterministic LLM fixture for theme-agnostic E2E coverage."""

    def __init__(self, brief, stock_codes):
        self.brief = dict(brief)
        self.stock_codes = set(stock_codes)

    def chat_json(self, system, user, **kw):
        if system == workflow_module.prompts.EVENT_ECOSYSTEM_SYS:
            return {"entities": []}
        if system == workflow_module.prompts.NARRATIVE_SYS:
            return _fake_narrative_response(user)
        if "Return JSON with keys" in user:
            return dict(self.brief)
        if "Score each candidate" in user:
            keywords = self.brief.get("keywords") or []
            input_theme_match = re.search(
                r'"input_theme"\s*:\s*"([^"]+)"', user)
            event_phrase = (
                input_theme_match.group(1) if input_theme_match else
                str(keywords[0]) if keywords else
                str(self.brief.get("summary") or self.brief.get("thesis") or "")
            )
            return [
                _fake_stock_score(line, 5, event_phrase=event_phrase)
                for line in user.splitlines()
                if "|" in line
                and line.split("|")[0].strip() in self.stock_codes
            ]
        if "FAQ" in user or "faq" in system.lower():
            return [{"question": f"Q{i}", "answer": f"A{i}"}
                    for i in range(4)]
        return []


class _StockLedThemeQuotes:
    """Quote fixture with one exact wrapper and one selected-stock basket."""

    class cfg:
        scene = "mock-stock-led-theme"

    def __init__(self, stocks, wrapper_code, basket_code):
        self.stocks = [dict(stock) for stock in stocks]
        self.stock_by_code = {stock["code"]: stock for stock in self.stocks}
        self.wrapper_code = wrapper_code
        self.basket_code = basket_code
        self.underlying_code = self.stocks[0]["code"]
        self.stock_relation_calls = []
        self.prompt_calls = []
        underlying_ticker = self.underlying_code.partition(":")[2]
        self.metadata = {
            wrapper_code: {
                "name": f"Daily 2x Long {underlying_ticker} ETF",
                "aum": 500_000_000,
                "turnover": 20_000_000,
                "expense_ratio": 1.15,
                "leverage": 2,
                "direction": "Long",
                "benchmark_code": self.underlying_code,
                "security_class": "CE",
                "etf_type": "Stock",
                "selection_criteria": "Single asset",
            },
            basket_code: {
                "name": "Selected Theme Leaders ETF",
                "aum": 2_000_000_000,
                "turnover": 30_000_000,
                "expense_ratio": 0.45,
                "leverage": 1,
                "direction": "Long",
                "base_index_code": f"89:{basket_code.partition(':')[2]}IDX",
                "security_class": "CE",
                "etf_type": "Stock",
                "asset_class": "Equity",
                "selection_criteria": "Index basket",
            },
        }
        weight = 100.0 / len(self.stocks)
        self.basket_weights = {
            stock["code"]: weight for stock in self.stocks
        }

    def iter_ranked(self, selector, indicators, *, sort_pos=0, order="desc",
                    page_size=1000, strict=False):
        for rank, stock in enumerate(self.stocks, 1):
            values = {
                "mktcap": 10_000_000_000 - rank,
                "name": stock["name"],
                "company_introduction": stock["company_introduction"],
                "sector": stock["sector"],
                "industry": stock["industry"],
            }
            yield {"code": stock["code"], "rank": rank, "values": values}

    def name_of(self, code):
        if code in self.stock_by_code:
            return self.stock_by_code[code]["name"]
        return self.metadata.get(code, {}).get("name", code.partition(":")[2])

    def iter_prompt_etfs(self, prompt_id, *, page_size=100):
        self.prompt_calls.append(prompt_id)
        return iter(())

    def iter_related_etfs(self, stock_code, *, page_size=100):
        self.stock_relation_calls.append((stock_code, "related", page_size))
        if stock_code in self.basket_weights:
            yield {
                "code": self.basket_code,
                "name": self.metadata[self.basket_code]["name"],
                "holding_weight": self.basket_weights[stock_code],
                "aum": self.metadata[self.basket_code]["aum"],
                "leverage": 1,
                "direction": "Long",
            }

    def iter_stock_etfs(self, stock_code, relation="related", *, page_size=1000):
        if relation == "related":
            yield from self.iter_related_etfs(
                stock_code, page_size=min(page_size, 100))
            return
        self.stock_relation_calls.append((stock_code, relation, page_size))
        if stock_code == self.underlying_code and relation in {"leveraged", "long"}:
            yield {
                "code": self.wrapper_code,
                "holding_weight": None,
                "stock_relation_kind": relation,
                **self.metadata[self.wrapper_code],
            }

    def etf_metadata(self, codes):
        return {code: dict(self.metadata[code])
                for code in codes if code in self.metadata}

    def etf_holding_weights_for_stock(self, codes, stock_code):
        return {
            code: self.basket_weights.get(stock_code)
            if code == self.basket_code else None
            for code in codes
        }

    def iter_etf_holdings(self, etf_code, *, page_size=1000):
        if etf_code != self.basket_code:
            return
        for rank, stock in enumerate(self.stocks, 1):
            yield {
                "code": stock["code"],
                "name": stock["name"],
                "weight_pct": self.basket_weights[stock["code"]],
                "rank": rank,
            }

    def fetch_klines(self, codes, *, count=60, **kw):
        return FakeQuotes().fetch_klines(codes, count=count, **kw)


def _run_stock_led_theme_fixture(*, theme, brief, stocks, wrapper_code,
                                 basket_code, expect_catalog_pool):
    quotes = _StockLedThemeQuotes(stocks, wrapper_code, basket_code)
    llm = _StockLedThemeLLM(brief, [stock["code"] for stock in stocks])
    pools = match_theme_pools(theme, brief, "")
    assert bool(pools) is expect_catalog_pool

    original_fetch_article = workflow_module.fetch_article
    workflow_module.fetch_article = lambda url: {
        "ok": True,
        "title": theme,
        "text": f"{theme}. {brief.get('summary') or ''}",
        "url": url,
    }
    try:
        result = ThemeWorkflow(llm, quotes, {
            "stock_universe": len(stocks),
            "stock_candidate_budget": len(stocks),
            "stock_broad_lane": len(stocks),
            "stock_target": min(8, len(stocks)),
            "etf_universe": 20,
            # This fixture exposes exactly two ETF products; exact-count
            # behavior for the default five is covered with the full fake CE
            # universe elsewhere.
            "etf_target": 2,
        }).run({
            "theme": theme,
            "date": "2026-07-09",
            "url": "https://example.com/theme-fixture",
        })
    finally:
        workflow_module.fetch_article = original_fetch_article

    expected_item_keys = {
        "market_code", "theme_rationale", "Theme exposure", "event_date",
    }
    assert len(result["ThemeStocks"]) == min(8, len(stocks))
    assert len(result["ThemeEtfs"]) == 2
    assert {item["market_code"] for item in result["ThemeEtfs"]} == {
        wrapper_code, basket_code,
    }
    assert all(set(item) == expected_item_keys
               for section in ("ThemeStocks", "ThemeEtfs")
               for item in result[section])
    assert all(item["event_date"] == "2026-07-09"
               for section in ("ThemeStocks", "ThemeEtfs")
               for item in result[section])
    public_stock_codes = {
        item["market_code"] for item in result["ThemeStocks"]
    }
    related_probe_codes = {
        code for code, relation, _ in quotes.stock_relation_calls
        if relation == "related"
    }
    assert public_stock_codes <= related_probe_codes
    assert quotes.metadata[wrapper_code]["security_class"] == "CE"
    assert quotes.metadata[wrapper_code]["benchmark_code"] in public_stock_codes
    assert quotes.metadata[wrapper_code]["leverage"] == 2
    assert quotes.metadata[wrapper_code]["direction"] == "Long"


def _fixture_stocks(specs):
    return [{
        "code": code,
        "name": name,
        "company_introduction": description,
        "sector": sector,
        "industry": industry,
    } for code, name, description, sector, industry in specs]


def test_end_to_end_neocloud_stock_led_etf_discovery_uses_catalog_evidence():
    stocks = _fixture_stocks([
        ("185:CRWV", "CoreWeave", "GPU neocloud capacity provider", "Technology", "Cloud Infrastructure"),
        ("185:NBIS", "Nebius", "AI GPU cloud compute operator", "Technology", "Cloud Infrastructure"),
        ("185:VRT", "Vertiv", "Electrical power distribution and liquid cooling for GPU data centers", "Industrials", "Data Center Equipment"),
        ("185:DELL", "Dell", "GPU rack servers for neocloud compute clusters", "Technology", "Computer Hardware"),
    ])
    _run_stock_led_theme_fixture(
        theme="Neocloud GPU capacity",
        brief={
            "theme_cn": "新云算力",
            "summary": "GPU cloud capacity expands.",
            "thesis": "Neocloud operators and suppliers benefit.",
            "theme_direction": "bullish",
            "direct_beneficiaries": ["neocloud GPU providers"],
            "picks_and_shovels": ["data center infrastructure"],
            "etf_exposure_terms": ["cloud infrastructure"],
            "second_order": [], "false_positives": [],
            "keywords": ["neocloud", "data centers"],
        },
        stocks=stocks, wrapper_code="185:CRWL", basket_code="185:NCBK",
        expect_catalog_pool=True,
    )


def test_end_to_end_iran_war_defense_energy_stock_led_etf_discovery():
    stocks = _fixture_stocks([
        ("185:LMT", "Lockheed Martin", "Defense systems contractor", "Industrials", "Aerospace and Defense"),
        ("185:RTX", "RTX", "Missile and aerospace supplier", "Industrials", "Aerospace and Defense"),
        ("185:XOM", "Exxon Mobil", "Integrated oil producer with refining and chemicals", "Energy", "Oil and Gas"),
        ("185:CVX", "Chevron", "Integrated oil and LNG producer with upstream exposure", "Energy", "Oil and Gas"),
    ])
    _run_stock_led_theme_fixture(
        theme="Iran war defense and energy",
        brief={
            "theme_cn": "伊朗战争防务与能源",
            "summary": "Conflict lifts defense demand and oil risk premia.",
            "thesis": "Defense contractors and oil producers gain directionally.",
            "theme_direction": "bullish",
            "direct_beneficiaries": ["aerospace and defense", "oil producers"],
            "picks_and_shovels": ["defense spending"],
            "etf_exposure_terms": ["aerospace and defense", "energy sector"],
            "second_order": [], "false_positives": [],
            "keywords": ["defense spending", "crude oil"],
        },
        stocks=stocks, wrapper_code="185:LMTL", basket_code="185:IWDE",
        expect_catalog_pool=True,
    )


def test_american_consumer_unavailable_article_still_freezes_exact_baskets():
    stocks = _fixture_stocks([
        ("185:COTY", "Coty", "U.S. beauty and fragrance products", "Consumer Staples", "Personal Products"),
        ("185:WMT", "Walmart", "U.S. grocery and household retail", "Consumer Staples", "Discount Retail"),
        ("185:COST", "Costco", "U.S. membership warehouse retail", "Consumer Staples", "Discount Retail"),
        ("185:PG", "Procter & Gamble", "Household and personal care brands", "Consumer Staples", "Household Products"),
        ("185:AMZN", "Amazon", "U.S. online consumer retail", "Consumer Discretionary", "Internet Retail"),
        ("185:HD", "Home Depot", "U.S. home-improvement retail", "Consumer Discretionary", "Home Improvement"),
        ("185:TGT", "Target", "U.S. general merchandise retail", "Consumer Discretionary", "Discount Retail"),
    ])

    class ConsumerQuotes(_StockLedThemeQuotes):
        STAPLES_POOL = "6908af018738843bb3ba864b"
        DISCRETIONARY_POOL = "6908aebd8738843bb3ba864a"

        def __init__(self):
            super().__init__(stocks, "185:COTG", "185:VDC")
            self.basket_weights_by_code = {
                "185:VDC": {
                    "185:COTY": 20, "185:WMT": 30,
                    "185:COST": 25, "185:PG": 25,
                },
                "185:XLP": {
                    "185:COTY": 15, "185:WMT": 25,
                    "185:COST": 25, "185:PG": 35,
                },
                "185:XRT": {
                    "185:AMZN": 35, "185:HD": 30,
                    "185:TGT": 25, "185:COTY": 10,
                },
            }
            self.metadata.update({
                "185:VDC": {
                    "name": "Vanguard Consumer Staples ETF",
                    "aum": 8_000_000_000, "turnover": 20_000_000,
                    "expense_ratio": 0.10, "leverage": 1,
                    "direction": "Long", "base_index_code": "89:MSCIUSSTAPLES",
                    "security_class": "CE", "etf_type": "Stock",
                    "asset_class": "Equity", "fund_niche": "Consumer Staples",
                    "selection_criteria": "Index basket",
                },
                "185:XLP": {
                    "name": "Consumer Staples Select Sector SPDR Fund",
                    "aum": 18_000_000_000, "turnover": 900_000_000,
                    "expense_ratio": 0.08, "leverage": 1,
                    "direction": "Long", "base_index_code": "89:SPSTAPLES",
                    "security_class": "CE", "etf_type": "Stock",
                    "asset_class": "Equity", "fund_niche": "Consumer Staples",
                    "selection_criteria": "Index basket",
                },
                "185:XRT": {
                    "name": "SPDR S&P Retail ETF",
                    "aum": 500_000_000, "turnover": 50_000_000,
                    "expense_ratio": 0.35, "leverage": 1,
                    "direction": "Long", "base_index_code": "89:SPRETAIL",
                    "security_class": "CE", "etf_type": "Stock",
                    "asset_class": "Equity", "fund_niche": "Consumer Discretionary Retail",
                    "selection_criteria": "Index basket",
                },
            })

        def iter_prompt_etfs(self, prompt_id, *, page_size=100):
            self.prompt_calls.append(prompt_id)
            codes = (
                ("185:VDC", "185:XLP") if prompt_id == self.STAPLES_POOL else
                ("185:XRT",) if prompt_id == self.DISCRETIONARY_POOL else
                ()
            )
            for rank, code in enumerate(codes, 1):
                yield {"code": code, "rank": rank, **self.metadata[code]}

        def iter_related_etfs(self, stock_code, *, page_size=100):
            self.stock_relation_calls.append((stock_code, "related", page_size))
            return iter(())

        def etf_holding_weights_for_stock(self, codes, stock_code):
            return {
                code: self.basket_weights_by_code.get(code, {}).get(stock_code)
                for code in codes
            }

        def iter_etf_holdings(self, etf_code, *, page_size=1000):
            for rank, (code, weight) in enumerate(
                self.basket_weights_by_code.get(etf_code, {}).items(), 1,
            ):
                yield {
                    "code": code, "name": self.stock_by_code[code]["name"],
                    "weight_pct": weight, "rank": rank,
                }

    brief = {
        "theme_cn": "美国消费者",
        "summary": "U.S. household demand drives consumer-company results.",
        "thesis": "Staples and discretionary businesses have direct household-demand exposure.",
        "theme_direction": "bullish",
        "direct_beneficiaries": ["consumer staples", "consumer discretionary"],
        "picks_and_shovels": ["U.S. retailers"],
        "etf_exposure_terms": ["consumer staples", "consumer discretionary"],
        "second_order": [], "false_positives": ["broad market beta"],
        "keywords": ["American consumer", "U.S. household demand"],
    }
    quotes = ConsumerQuotes()
    llm = _StockLedThemeLLM(brief, [stock["code"] for stock in stocks])
    original_fetch_article = workflow_module.fetch_article
    workflow_module.fetch_article = lambda url: {
        "ok": False, "title": "", "text": "", "url": url,
        "error": "fixture article unavailable",
    }
    try:
        result = ThemeWorkflow(llm, quotes, {
                "stock_universe": len(stocks),
                "stock_candidate_budget": len(stocks),
                "stock_broad_lane": len(stocks),
                "stock_target": min(8, len(stocks)),
                "etf_universe": 20,
                "etf_target": 3,
            }).run({
                "theme": "American Consumer",
                "date": "2026-07-09",
                "url": "https://example.com/unavailable-consumer-article",
            })
    finally:
        workflow_module.fetch_article = original_fetch_article

    assert len(result["ThemeStocks"]) == 7
    assert len(result["ThemeEtfs"]) == 3
    assert len({item["market_code"] for item in result["ThemeStocks"]}) == 7
    assert len({item["market_code"] for item in result["ThemeEtfs"]}) == 3
    assert quotes.prompt_calls
    assert quotes.stock_relation_calls


def test_end_to_end_arbitrary_non_catalog_micro_segment_is_stock_led():
    stocks = _fixture_stocks([
        ("185:COHR", "Coherent", "Hollow-core fiber photonics components", "Technology", "Photonics Components"),
        ("185:IPGP", "IPG Photonics", "Specialized fiber laser source", "Technology", "Laser Systems"),
        ("185:LITE", "Lumentum", "Precision optical coupling modules", "Technology", "Optical Components"),
        (
            "185:LASR", "nLIGHT",
            "High-power laser modules for industrial materials processing",
            "Technology", "Laser Systems",
        ),
    ])
    _run_stock_led_theme_fixture(
        theme="subsea hollow-core fiber laser couplers",
        brief={
            "theme_cn": "海底空芯光纤激光耦合器",
            "summary": "A narrow photonics component cycle accelerates.",
            "thesis": "Specialist component suppliers gain order exposure.",
            "theme_direction": "bullish",
            "direct_beneficiaries": ["hollow-core fiber laser couplers"],
            "picks_and_shovels": ["precision photonics components"],
            "etf_exposure_terms": ["subsea optical coupling modules"],
            "second_order": [], "false_positives": [],
            "keywords": ["hollow-core fiber", "laser couplers"],
        },
        stocks=stocks, wrapper_code="185:COHL", basket_code="185:HCFB",
        expect_catalog_pool=False,
    )


def test_bearish_end_to_end_keeps_direction_and_derivative_facts_internal():
    class BearishLLM(FakeLLM):
        def chat_json(self, system, user, **kw):
            response = super().chat_json(system, user, **kw)
            if "Return JSON with keys" in user:
                response["theme_direction"] = "bearish"
                response["thesis"] = "AI spending and semiconductor valuations may contract."
            return response

    payload = {
        "theme": "AI bubble",
        "date": "2026-07-09",
        "url": "https://example.com/x",
        "source_tag": "preserved",
    }
    original_fetch_article = workflow_module.fetch_article
    workflow_module.fetch_article = lambda url: {
        "ok": True,
        "title": "AI bubble pressure",
        "text": (
            "AI bubble concerns point to lower accelerator spending. "
            "NVIDIA supplies AI accelerator processors."
        ),
        "url": url,
    }
    try:
        result = ThemeWorkflow(
            BearishLLM(), FakeQuotes(), {
                "stock_universe": 20,
                "stock_target": 1,
                "etf_universe": 100,
            }
        ).run(payload)
    finally:
        workflow_module.fetch_article = original_fetch_article
    public = _build_output(payload, result)
    serialized = _serialize_output(public)

    # The exact inverse wrapper remains first. Ordinary long baskets are retained
    # as downside-sensitive exposure, while a non-exact sector inverse such as
    # SOXS still cannot qualify from static-pool membership alone.
    assert result["ThemeEtfs"][0]["market_code"] == "185:NVDQ"
    downside_baskets = [
        item for item in result["ThemeEtfs"]
        if item["market_code"] != "185:NVDQ"
    ]
    assert downside_baskets
    assert any(
        "declines in these positions would reduce the fund's net asset value"
        in item["theme_rationale"]["en"]
        and "持仓下跌会压低基金净值" in item["theme_rationale"]["zh"]
        for item in downside_baskets
    )
    assert public["source_tag"] == "preserved"
    assert all(
        set(item) == {
            "market_code", "theme_rationale", "Theme exposure", "event_date",
        }
        for section in ("ThemeStocks", "ThemeEtfs")
        for item in public[section]
    )
    for internal in (
        "theme_direction", "chg_pct", "market_strength", "leverage",
        "direction", "verified_inverse",
    ):
        assert f'"{internal}"' not in serialized


def test_stock_source_uses_full_ranked_block_and_explicit_sorting():
    class RecordingQuotes(FakeQuotes):
        def __init__(self):
            self.calls = []
        def iter_ranked(self, selector, indicators, *, sort_pos=0, order="desc",
                        page_size=1000, strict=False):
            self.calls.append((selector, indicators, sort_pos, order))
            yield {"code": "185:X", "rank": 1,
                   "values": {indicators[0]["req_unique_id"]: 1}}

    quotes = RecordingQuotes()
    wf = ThemeWorkflow(FakeLLM(), quotes)
    list(wf.stock_source())

    (stock_call,) = quotes.calls
    assert stock_call[0] == {"type": "block_id", "value": ["C191"]}
    assert [indicator["id"] for indicator in stock_call[1]] == [
        "total_market_value", "55", "company_introduction",
        "ext_metric_sector_1_name", "ext_metric_sector_3_name",
    ]
    assert stock_call[2:] == (0, "desc")


def test_related_etf_helper_uses_stock_prompt_and_holding_weight_sort():
    client = object.__new__(AInvestClient)
    calls = []
    client.name_of = lambda code: code

    def rows(selector, indicators, *, sort_pos=0, order="desc", page_size=1000,
             strict=False):
        calls.append((selector, indicators, sort_pos, order, page_size, strict))
        yield {"code": "185:SMH", "rank": 1,
               "values": {"holding_weight": 19.99, "name": "SMH",
                          "aum": 70_000_000_000, "leverage": 1,
                          "direction": "Long"}}

    client.iter_ranked = rows
    result = list(client.iter_related_etfs("185:NVDA", page_size=37))
    selector, indicators, sort_pos, order, page_size, strict = calls[0]
    assert selector == {
        "type": "prompt_id", "value": ["677251bbbc4823684c64145d"],
        "attr": {"market_code": "185:NVDA"},
    }
    assert indicators[0]["id"] == "国际北美etf@Holding Stock Weight(View)"
    assert indicators[0]["attr"] == {"match_code": "185:NVDA"}
    assert (sort_pos, order, page_size, strict) == (0, "desc", 37, True)
    assert result[0]["holding_weight"] == 19.99


def test_stock_etf_helper_supports_dedicated_leverage_lane_and_exact_metadata():
    client = object.__new__(AInvestClient)
    calls = []
    client.name_of = lambda code: code

    def rows(selector, indicators, *, sort_pos=0, order="desc", page_size=1000,
             strict=False):
        calls.append((selector, indicators, sort_pos, order, page_size, strict))
        yield {"code": "185:NVDL", "rank": 1, "values": {
            "holding_weight": None, "name": "Daily 2x Long NVDA ETF",
            "aum": 4_000_000_000, "leverage": 2, "direction": "Long",
            "benchmark_code": "185:NVDA", "security_class": "CE",
            "etf_type": "Stock", "index_etf_code": None,
        }}

    client.iter_ranked = rows
    result = list(client.iter_stock_etfs(
        "185:NVDA", relation="leveraged", page_size=41))
    selector, indicators, sort_pos, order, page_size, strict = calls[0]

    assert selector == {
        "type": "prompt_id", "value": ["6762c178784e3a2b800f5bae"],
        "attr": {"market_code": "185:NVDA"},
    }
    assert {indicator["req_unique_id"] for indicator in indicators} >= {
        "benchmark_code", "security_class", "etf_type", "index_etf_code",
    }
    assert (sort_pos, order, page_size, strict) == (0, "desc", 41, True)
    assert result[0]["benchmark_code"] == "185:NVDA"
    assert result[0]["security_class"] == "CE"
    assert result[0]["stock_relation_kind"] == "leveraged"


def test_concept_index_etf_helper_uses_exact_index_attrs_and_maps_evidence():
    client = object.__new__(AInvestClient)
    calls = []
    client.name_of = lambda code: code

    def rows(selector, indicators, *, sort_pos=0, order="desc", page_size=1000,
             strict=False):
        calls.append((selector, indicators, sort_pos, order, page_size, strict))
        yield {"code": "185:CLOU", "rank": 1, "values": {
            "block_etf_holdrate": 64.5,
            "block_etf_risekline": 0.91,
            "name": "Cloud Infrastructure ETF",
            "aum": 800_000_000,
            "leverage": 1,
            "direction": "Long",
            "benchmark_code": "89:40001234",
            "security_class": "CE",
            "etf_type": "Stock",
            "index_etf_code": "89:40001234",
        }}

    client.iter_ranked = rows
    result = list(client.iter_concept_index_etfs(
        "89:40001234", page_size=43))
    selector, indicators, sort_pos, order, page_size, strict = calls[0]

    assert selector == {
        "type": "prompt_id", "value": ["69285634069a48065f159442"],
        "attr": {"market_code": "89:40001234"},
    }
    evidence_indicators = {
        indicator["req_unique_id"]: indicator
        for indicator in indicators[:2]
    }
    assert evidence_indicators["block_etf_holdrate"]["attr"] == {
        "match_code": "89:40001234",
    }
    assert evidence_indicators["block_etf_risekline"]["attr"] == {
        "match_code": "89:40001234",
    }
    assert (sort_pos, order, page_size, strict) == (0, "desc", 43, True)
    assert result[0]["block_etf_holdrate"] == 64.5
    assert result[0]["block_etf_risekline"] == 0.91
    assert result[0]["index_etf_code"] == "89:40001234"


def test_live_etf_universe_uses_relation_list_and_deduplicates_pages():
    client = object.__new__(AInvestClient)
    client.cfg = type("Cfg", (), {
        "relation_list_url": "https://example.test/relation",
    })()
    begins = []

    def relation(_url, body):
        begins.append(body["page"]["begin"])
        if body["page"]["begin"] == 0:
            return {"status_code": 0, "data": {
                "data": [{"v": "185:AAA"}, {"v": "185:BBB"}],
                "page": {"total": 3},
            }}
        return {"status_code": 0, "data": {
            "data": [{"v": "185:BBB"}, {"v": "185:CCC"}],
            "page": {"total": 3},
        }}

    client._post = relation
    assert list(client.iter_live_etf_codes(page_size=2)) == [
        "185:AAA", "185:BBB", "185:CCC",
    ]
    assert begins == [0, 2]


def test_preselection_recovers_exact_wrapper_live_first_then_local_ce_fallback():
    class RecoveryQuotes:
        _ROWS = {
            "185:LIVE2X": {
                "name": "Daily 2x Long NVDA Live ETF",
                "aum": 600_000_000, "turnover": 30_000_000,
                "expense_ratio": 1.15, "leverage": 2, "direction": "Long",
                "benchmark_code": "185:NVDA", "security_class": "CE",
                "etf_type": "Stock", "selection_criteria": "Single asset",
            },
            "185:LOCAL2X": {
                "name": "Daily 2x Long NVDA Local ETF",
                "aum": 300_000_000, "turnover": 10_000_000,
                "expense_ratio": 1.15, "leverage": 2, "direction": "Long",
                "benchmark_code": "185:NVDA", "security_class": "CE",
                "etf_type": "Stock", "selection_criteria": "Single asset",
            },
            "185:ORDINARY": {
                "name": "Ordinary Equity",
                "aum": 1_000_000_000, "turnover": 20_000_000,
                "expense_ratio": 0.5, "leverage": 1, "direction": "Long",
                "benchmark_code": "89:BROAD", "security_class": "CE",
                "etf_type": "Stock", "selection_criteria": "Index basket",
            },
        }

        def __init__(self, live_codes):
            self.live_codes_result = list(live_codes)
            self.live_calls = 0
            self.local_calls = 0

        def name_of(self, code):
            return self._ROWS.get(code, {}).get("name", code)

        def iter_stock_etfs(self, stock_code, relation="related", *, page_size=1000):
            return iter(())

        def iter_related_etfs(self, stock_code, *, page_size=100):
            return iter(())

        def iter_prompt_etfs(self, prompt_id, *, page_size=100):
            return iter(())

        def live_etf_codes(self, *, page_size=1000):
            self.live_calls += 1
            return list(self.live_codes_result)

        def security_codes(self, security_type):
            assert security_type == "CE"
            self.local_calls += 1
            return ["185:LOCAL2X"]

        def etf_metadata(self, codes):
            return {code: dict(self._ROWS[code])
                    for code in codes if code in self._ROWS}

        def etf_holding_weights_for_stock(self, codes, stock_code):
            return {code: None for code in codes}

    selected = [{
        "code": "185:NVDA", "name": "NVIDIA", "ai_relevance": 5,
        "confidence": 0.95, "exposure_type": "direct",
        "is_public_theme_stock": True,
    }]

    live_quotes = RecoveryQuotes(["185:ORDINARY", "185:LIVE2X"])
    live = preselect_etfs(
        live_quotes, selected, theme="arbitrary narrow capacity trade",
        brief={}, article_title="", limit=10,
    )
    assert [candidate["code"] for candidate in live.candidates] == ["185:LIVE2X"]
    assert live.candidates[0]["output_eligible"] is True
    assert live.candidates[0]["underlying_code"] == "185:NVDA"
    assert live.discovery_source_counts["recovery"] == {
        "raw": 2, "retained": 1,
    }
    assert live_quotes.live_calls == 1
    assert live_quotes.local_calls == 0

    local_quotes = RecoveryQuotes([])
    local = preselect_etfs(
        local_quotes, selected, theme="arbitrary narrow capacity trade",
        brief={}, article_title="", limit=10,
    )
    assert [candidate["code"] for candidate in local.candidates] == ["185:LOCAL2X"]
    assert local.candidates[0]["output_eligible"] is True
    assert local.candidates[0]["underlying_code"] == "185:NVDA"
    assert local_quotes.live_calls == 1
    assert local_quotes.local_calls == 1


def test_etf_holdings_helper_uses_component_selector_and_weight_sort():
    client = object.__new__(AInvestClient)
    client.name_of = lambda code: f"Name {code}"
    calls = []

    def rows(selector, indicators, *, sort_pos=0, order="desc", page_size=1000,
             strict=False):
        calls.append((selector, indicators, sort_pos, order, page_size, strict))
        yield {"code": "185:NVDA", "rank": 1,
               "values": {"holding_weight": 8.5, "name": "NVIDIA"}}

    client.iter_ranked = rows
    result = list(client.iter_etf_holdings("185:QQQ", page_size=37))
    selector, indicators, sort_pos, order, page_size, strict = calls[0]

    assert selector == {
        "type": "link_code", "value": ["185:QQQ"],
        "attr": {"link_type": "holding"},
    }
    assert indicators[0]["id"] == "ext_etf_holding_ratio"
    assert indicators[0]["attr"] == {"match_code": "185:QQQ"}
    assert indicators[1] == {"id": "55", "req_unique_id": "name"}
    assert (sort_pos, order, page_size, strict) == (0, "desc", 37, True)
    assert result == [{"code": "185:NVDA", "name": "NVIDIA",
                       "weight_pct": 8.5, "rank": 1}]


def test_etf_holdings_parses_live_gateway_indicator_order_fixture():
    client = object.__new__(AInvestClient)
    client.cfg = type("Cfg", (), {"snapshot_url": "https://example.test/snapshot"})()
    client.name_of = lambda code: code

    def snapshot(_url, body):
        assert body["indicator"][0]["id"] == "ext_etf_holding_ratio"
        return {"data": {
            # The gateway can return indicators in a different order from the
            # request; cells follow data.indicator rather than request position.
            "indicator": [
                {"id": "55", "req_unique_id": "name"},
                {"id": "ext_etf_holding_ratio", "req_unique_id": "holding_weight"},
            ],
            "data": [{
                "symbol_code": "185:NVDA",
                "value": [{"v": "NVIDIA"}, {"v": 8.37}],
            }],
        }}

    client._post = snapshot
    assert list(client.iter_etf_holdings("185:QQQ")) == [{
        "code": "185:NVDA", "name": "NVIDIA", "weight_pct": 8.37, "rank": 1,
    }]


def test_iter_ranked_stops_when_gateway_repeats_a_full_page():
    client = object.__new__(AInvestClient)
    client.cfg = type("Cfg", (), {"snapshot_url": "https://example.test/snapshot"})()
    begins = []

    def repeated_page(_url, body):
        begins.append(body["page"]["begin"])
        return {"data": {
            "indicator": [{"req_unique_id": "metric"}],
            "data": [
                {"symbol_code": "185:A", "value": [{"v": 2}]},
                {"symbol_code": "185:B", "value": [{"v": 1}]},
            ],
        }}

    client._post = repeated_page
    rows = list(client.iter_ranked(
        {"type": "block_id", "value": ["C191"]},
        [{"id": "total_market_value", "req_unique_id": "metric"}],
        page_size=2,
    ))
    assert [r["code"] for r in rows] == ["185:A", "185:B"]
    assert begins == [0, 2]


def test_iter_ranked_strict_surfaces_snapshot_application_errors():
    client = object.__new__(AInvestClient)
    client.cfg = type("Cfg", (), {
        "snapshot_url": "https://example.test/snapshot",
    })()
    client._post = lambda _url, _body: {
        "status_code": 40017,
        "status_msg": "invalid concept-index selector",
        "data": {},
    }

    try:
        list(client.iter_ranked(
            {"type": "prompt_id", "value": ["concept-etf-prompt"]},
            [{"id": "55", "req_unique_id": "name"}],
            strict=True,
        ))
    except RuntimeError as exc:
        assert str(exc) == (
            "snapshot failed (40017): invalid concept-index selector"
        )
    else:
        raise AssertionError("strict snapshot application failure was swallowed")

    assert list(client.iter_ranked(
        {"type": "prompt_id", "value": ["concept-etf-prompt"]},
        [{"id": "55", "req_unique_id": "name"}],
        strict=False,
    )) == []


def test_workflow_scores_the_whole_bounded_stock_candidate_set():
    """Output slots are assigned only after all 50 bounded candidates compete."""
    seen = {"n": 0}

    class TrackingLLM(FakeLLM):
        def chat_json(self, system, user, **kw):
            if "Candidates (market_code | candidate_id | name" in user:
                seen["n"] += sum(
                    1 for line in user.splitlines()
                    if "|" in line and ":" in line.split("|")[0])
            return super().chat_json(system, user)

    wf = ThemeWorkflow(TrackingLLM(), FakeQuotes(),
                       {"stock_universe": 50, "stock_target": 1,
                        "etf_universe": 50, "relevance_batch": 20})
    wf.run({"theme": "AI memory", "date": "2026-07-09", "url": "https://example.com/x"})
    assert seen["n"] == 50


def _theme_stocks():
    return [
        {"code": "185:NVDA", "name": "NVIDIA", "ai_relevance": 5,
         "confidence": 0.95, "exposure_type": "direct"},
        {"code": "185:MU", "name": "Micron", "ai_relevance": 5,
         "confidence": 0.95, "exposure_type": "direct"},
        *[
            {"code": f"185:S{i}", "name": f"Theme Stock {i}", "ai_relevance": 4,
             "confidence": 0.9, "exposure_type": "supply_chain"}
            for i in (0, 2, 4, 6, 8, 10)
        ],
    ]


def test_static_theme_pool_routing_is_specific_and_llm_free():
    matches = match_theme_pools("AI memory", {"keywords": ["HBM", "DRAM"]}, "")
    keys = [match.spec.key for match in matches]
    assert keys[:2] == ["semiconductors", "artificial_intelligence"]
    assert "ai_robotics" not in keys
    assert "robotics" not in keys
    assert match_theme_pools("chairman said demand is strong", {}, "") == []
    bearish = match_theme_pools(
        "AI memory", {"keywords": ["HBM", "DRAM"]}, "",
        max_pools=4, theme_direction="bearish")
    assert 1 <= len(bearish) <= 4
    assert [match.spec.key for match in bearish] == keys
    assert "inverse_sp500" not in {match.spec.key for match in bearish}


def test_ai_infrastructure_uses_the_same_canonical_pool_and_mandate_vocabulary():
    brief = {
        "direct_beneficiaries": ["artificial intelligence companies"],
        "picks_and_shovels": ["AI model platforms"],
        "etf_exposure_terms": ["AI infrastructure"],
    }
    pools = match_theme_pools("AI infrastructure", brief, "")
    assert pools
    assert pools[0].spec.key == "artificial_intelligence"

    verified, terms = _mandate_match(
        {"name": "Artificial Intelligence Leaders ETF"},
        brief,
        "AI infrastructure",
        pools=pools,
    )
    assert verified is True
    assert terms


def test_pool_only_evidence_is_primary_only_for_direct_asset_funds():
    class PoolOnlyQuotes:
        _NAMES = {
            "185:GLD": "Gold Trust",
            "185:BIL": "Short Duration Treasury ETF",
            "185:GENAI": "Generic Artificial Intelligence ETF",
        }
        def name_of(self, code): return code.partition(":")[2]
        def iter_related_etfs(self, stock_code, *, page_size=100):
            return iter(())
        def iter_prompt_etfs(self, prompt_id, *, page_size=100):
            if prompt_id == "6908b3d38738843bb3ba8662":
                yield {"code": "185:GLD", "name": "Gold Trust", "aum": 1e11,
                       "leverage": 1, "direction": "Long"}
            elif prompt_id == "6908b4328738843bb3ba8664":
                yield {"code": "185:BIL", "name": "Short Duration Treasury ETF", "aum": 1e11,
                       "leverage": 1, "direction": "Long"}
            elif prompt_id == "6908b19b8738843bb3ba8657":
                yield {"code": "185:GENAI", "name": "Generic AI ETF", "aum": 1e10,
                       "leverage": 1, "direction": "Long"}
        def etf_metadata(self, codes):
            return {code: {"name": self._NAMES[code], "aum": 1e10,
                           "leverage": 1, "direction": "Long",
                           "security_class": "CE",
                           "asset_class": (
                               "Commodity" if code == "185:GLD" else
                               "Bonds" if code == "185:BIL" else "Equity")}
                    for code in codes}
        def etf_holding_weights_for_stock(self, codes, stock_code):
            return {code: None for code in codes}

    quotes = PoolOnlyQuotes()
    gold = preselect_etfs(
        quotes, _theme_stocks()[:1], theme="gold", brief={}, article_title="", limit=10)
    ai = preselect_etfs(
        quotes, _theme_stocks()[:1], theme="AI", brief={}, article_title="", limit=10)
    treasury = preselect_etfs(
        quotes, _theme_stocks()[:1], theme="short term treasury",
        brief={}, article_title="", limit=10)
    assert gold.candidates[0]["output_eligible"] is True
    assert treasury.candidates[0]["output_eligible"] is True
    assert ai.candidates[0]["selection_lane"] == "multi_stock_basket"
    assert ai.candidates[0]["output_eligible"] is False
    assert gold.candidates[0]["preselect_score"] > ai.candidates[0]["preselect_score"]


def test_exact_direct_asset_concept_survives_curated_pool_failure():
    class ConceptOnlyGoldQuotes:
        names = {"89:861123": "Gold"}

        def name_of(self, code):
            return "SPDR Gold Shares" if code == "185:GLD" else code.partition(":")[2]

        def iter_stock_etfs(self, stock_code, relation="related", *, page_size=1000):
            return iter(())

        def iter_prompt_etfs(self, prompt_id, *, page_size=100):
            raise RuntimeError("curated gold pool unavailable")
            yield  # pragma: no cover - keeps this a strict failing iterator

        def iter_concept_index_etfs(self, index_code, *, page_size=100):
            assert index_code == "89:861123"
            yield {
                "code": "185:GLD",
                "name": "SPDR Gold Shares",
                "security_class": "CE",
                "etf_type": "Commodity",
                "direction": "Long",
                "leverage": 1,
            }

        def etf_metadata(self, codes):
            return {
                "185:GLD": {
                    "name": "SPDR Gold Shares",
                    "security_class": "CE",
                    "etf_type": "Commodity",
                    "asset_class": "Commodity",
                    "direction": "Long",
                    "leverage": 1,
                    "benchmark": "Gold bullion",
                    "fund_strategy": "Tracks the price of gold bullion",
                },
            }

        def etf_holding_weights_for_stock(self, codes, stock_code):
            return {code: None for code in codes}

    selected = [{
        "code": "185:NEM",
        "name": "Newmont",
        "is_public_theme_stock": True,
    }]
    result = preselect_etfs(
        ConceptOnlyGoldQuotes(), selected, theme="gold",
        brief={}, article_title="", limit=20,
    )

    assert [candidate["code"] for candidate in result.candidates] == ["185:GLD"]
    gold = result.candidates[0]
    assert gold["selection_lane"] == "direct_asset"
    assert gold["direct_asset_source_keys"] == ["gold"]
    assert gold["pool_matches"] == {}
    assert gold["mandate_verified"] is True
    assert gold["output_eligible"] is True
    assert result.discovery_source_counts["curated_pool"]["retained"] == 0
    assert result.discovery_source_counts["concept_index"]["retained"] == 1
    assert any(failure.startswith("pool:gold:") for failure in result.failures)


def test_missing_structure_cannot_create_plain_long_lane_and_daily_income_is_excluded():
    class UnsettledMetadataQuotes:
        _ROWS = {
            "185:MISSINGBASKET": {
                "name": "Selected Holdings Basket ETF",
                "security_class": "CE", "etf_type": "Stock",
                "asset_class": "Equity", "selection_criteria": "Index basket",
                "aum": 500_000_000,
            },
            "185:MISSINGGOLD": {
                "name": "Physical Gold Trust",
                "security_class": "CE", "etf_type": "Commodity",
                "asset_class": "Commodity", "selection_criteria": "Physical gold",
                "aum": 2_000_000_000,
            },
            "185:DAYINC": {
                "name": "Daily Income ETF",
                "security_class": "CE", "etf_type": "Stock",
                "asset_class": "Equity", "selection_criteria": "Index basket",
                "aum": 1_000_000_000, "leverage": 1, "direction": "Long",
            },
        }

        def name_of(self, code): return self._ROWS[code]["name"]
        def iter_stock_etfs(self, stock_code, relation="related", *, page_size=1000):
            if relation == "related":
                yield {"code": "185:MISSINGBASKET", "holding_weight": 20.0,
                       **self._ROWS["185:MISSINGBASKET"]}
                yield {"code": "185:DAYINC", "holding_weight": 20.0,
                       **self._ROWS["185:DAYINC"]}
        def iter_related_etfs(self, stock_code, *, page_size=100):
            return self.iter_stock_etfs(stock_code, relation="related", page_size=page_size)
        def iter_prompt_etfs(self, prompt_id, *, page_size=100):
            yield {"code": "185:MISSINGGOLD", **self._ROWS["185:MISSINGGOLD"]}
        def etf_metadata(self, codes):
            return {code: dict(self._ROWS[code]) for code in codes
                    if code in self._ROWS}
        def etf_holding_weights_for_stock(self, codes, stock_code):
            return {code: 20.0 if code in {
                "185:MISSINGBASKET", "185:DAYINC",
            } else None for code in codes}

    selected = [{
        "code": "185:NVDA", "name": "NVIDIA", "ai_relevance": 5,
        "confidence": 0.95, "exposure_type": "direct",
        "is_public_theme_stock": True,
    }]
    result = preselect_etfs(
        UnsettledMetadataQuotes(), selected, theme="gold",
        brief={}, article_title="", limit=20,
    )

    codes = [candidate["code"] for candidate in result.candidates]
    assert "185:MISSINGBASKET" not in codes
    assert "185:MISSINGGOLD" not in codes
    assert "185:DAYINC" not in codes
    assert result.discovered >= 2  # both missing-structure rows reached classification
    assert result.exclusion_reasons["option-income strategy"] >= 1


def test_weeklypay_income_brand_is_excluded_before_every_product_lane():
    class WeeklyPayQuotes:
        _ROWS = {
            "171:AMDW": {
                "name": "Roundhill AMD WeeklyPay ETF",
                "security_class": "CE", "etf_type": "Stock",
                "asset_class": "Equity", "selection_criteria": "Single asset",
                "benchmark_code": "185:AMD", "direction": "Long",
                "leverage": 1.2, "aum": 80_000_000,
            },
            "185:WPBASKET": {
                "name": "Selected Leaders Weekly Pay ETF",
                "security_class": "CE", "etf_type": "Stock",
                "asset_class": "Equity", "selection_criteria": "Index basket",
                "direction": "Long", "leverage": 1, "aum": 500_000_000,
            },
            "185:WPGOLD": {
                "name": "Physical Gold Weekly-Pay ETF",
                "security_class": "CE", "etf_type": "Commodity",
                "asset_class": "Commodity", "selection_criteria": "Physical gold",
                "fund_strategy": "Tracks gold bullion", "direction": "Long",
                "leverage": 1, "aum": 500_000_000,
            },
        }

        def name_of(self, code):
            return self._ROWS[code]["name"]

        def iter_stock_etfs(self, stock_code, relation="related", *, page_size=1000):
            if relation == "related":
                yield {
                    "code": "185:WPBASKET", "holding_weight": 30.0,
                    **self._ROWS["185:WPBASKET"],
                }
            elif stock_code == "185:AMD" and relation in {"leveraged", "long"}:
                yield {"code": "171:AMDW", **self._ROWS["171:AMDW"]}

        def iter_prompt_etfs(self, prompt_id, *, page_size=100):
            yield {"code": "185:WPGOLD", **self._ROWS["185:WPGOLD"]}

        def etf_metadata(self, codes):
            return {code: dict(self._ROWS[code]) for code in codes
                    if code in self._ROWS}

        def etf_holding_weights_for_stock(self, codes, stock_code):
            return {code: 30.0 if code == "185:WPBASKET" else None
                    for code in codes}

    selected = [
        {"code": "185:AMD", "name": "AMD", "is_public_theme_stock": True},
        {"code": "185:NVDA", "name": "NVIDIA", "is_public_theme_stock": True},
    ]
    result = preselect_etfs(
        WeeklyPayQuotes(), selected, theme="gold",
        brief={}, article_title="", limit=20,
    )

    assert result.candidates == []
    assert result.discovered == 0
    assert result.exclusion_reasons["option-income strategy"] >= 3
    assert set(result.exclusion_examples["option-income strategy"]) == {
        "171:AMDW Roundhill AMD WeeklyPay ETF",
        "185:WPBASKET Selected Leaders Weekly Pay ETF",
        "185:WPGOLD Physical Gold Weekly-Pay ETF",
    }


def test_structured_etf_type_rejects_neutral_name_etn():
    candidate = {
        "name": "Issuer Daily NVDA Product",
        "security_class": "CE",
        "etf_type": "ETN",
        "benchmark_code": "185:NVDA",
        "direction": "Long",
        "leverage": 2,
    }
    assert _hard_filter_reason(
        candidate, "bullish", defer_ambiguous=True,
    ) == "exchange-traded note"


def test_dedicated_probe_wrong_benchmark_is_diagnosed_without_admission():
    class WrongBenchmarkQuotes:
        def name_of(self, code):
            return code.partition(":")[2]

        def iter_stock_etfs(self, stock_code, relation="related", *, page_size=1000):
            if relation == "leveraged":
                yield {
                    "code": "185:WRONG",
                    "name": "Issuer Daily Product",
                    "security_class": "CE",
                    "etf_type": "Stock",
                    "benchmark_code": "185:AMD",
                    "direction": "Long",
                    "leverage": 2,
                }

        def iter_prompt_etfs(self, prompt_id, *, page_size=100):
            return iter(())

        def etf_metadata(self, codes):
            return {}

        def etf_holding_weights_for_stock(self, codes, stock_code):
            return {}

    selected = [{
        "code": "185:NVDA",
        "name": "NVIDIA",
        "is_public_theme_stock": True,
    }]
    result = preselect_etfs(
        WrongBenchmarkQuotes(), selected, theme="microsegment",
        brief={}, article_title="", limit=20,
    )

    reason = "benchmark is not an exact selected-stock market_code"
    assert result.candidates == []
    assert result.exclusion_reasons[reason] == 1
    assert result.exclusion_examples[reason] == ["185:WRONG Issuer Daily Product"]


def test_etf_preselection_prioritizes_exact_wrapper_then_basket_evidence():
    result = preselect_etfs(
        FakeQuotes(), _theme_stocks(), theme="AI memory",
        brief={"keywords": ["HBM", "DRAM"]}, article_title="Memory demand", limit=100,
    )
    codes = [candidate["code"] for candidate in result.candidates]
    assert codes[0] == "185:NVDL"
    assert codes.index("185:SOXX") < codes.index("185:QQQ")
    assert codes.index("185:HBMX") < codes.index("185:QQQ")
    assert "185:SOXS" not in codes       # Short / 3x
    assert "169:NVDY" not in codes       # single-stock option-income wrapper
    assert "169:FLSP" not in codes       # alternative long/short strategy
    wrapper = result.candidates[0]
    assert wrapper["selection_lane"] == "single_stock_leveraged"
    assert wrapper["underlying_code"] == "185:NVDA"
    assert wrapper["output_eligible"] is True
    smh = next(candidate for candidate in result.candidates
               if candidate["code"] == "185:SMH")
    assert smh["theme_weight_pct"] > 40
    assert smh["theme_breadth"] >= 5
    assert smh["relevance_status"] == "deterministic"
    assert smh["selection_lane"] == "multi_stock_basket"


def test_bearish_etf_preselection_keeps_inverse_first_and_admits_long_baskets():
    bearish = preselect_etfs(
        FakeQuotes(), _theme_stocks(), theme="AI memory",
        brief={"keywords": ["HBM", "DRAM"]}, article_title="AI bubble warning",
        limit=100, theme_direction="bearish",
    )
    codes = [candidate["code"] for candidate in bearish.candidates]
    inverse = bearish.candidates[0]

    assert codes[0] == "185:NVDQ"
    assert {"185:SMH", "185:SOXX", "171:DRAM", "185:HBMX"} <= set(codes)
    assert inverse["selection_lane"] == "single_stock_leveraged"
    assert inverse["underlying_code"] == "185:NVDA"
    assert inverse["verified_inverse"] is True
    assert inverse["is_inverse"] is True
    assert inverse["leverage"] == 2
    assert inverse["output_eligible"] is True
    baskets = [candidate for candidate in bearish.candidates
               if candidate["selection_lane"] == "multi_stock_basket"]
    assert baskets
    assert all(candidate["direction"] == "Long" for candidate in baskets)
    assert all(candidate["leverage"] == 1 for candidate in baskets)
    assert all(candidate["bearish_downside_exposure"] is True
               for candidate in baskets)
    assert all("vulnerable" in candidate["reason"] for candidate in baskets)
    assert "185:SOXS" not in codes       # sector inverse lacks exact stock benchmark
    assert "185:NVDL" not in codes       # leveraged long remains excluded
    assert "169:NVDY" not in codes       # option-income remains excluded
    assert "169:FLSP" not in codes       # generic long/short remains excluded

    invalid = preselect_etfs(
        FakeQuotes(), _theme_stocks(), theme="AI memory",
        brief={"keywords": ["HBM"]}, article_title="",
        limit=100, theme_direction="mixed",
    )
    assert "185:SOXS" not in [candidate["code"] for candidate in invalid.candidates]


def test_bearish_exact_wrapper_is_eligible_with_null_physical_holding():
    class InverseQuotes:
        _DATA = {
            "185:NVDQ": {
                "name": "Tradr 2X Short NVDA Daily ETF", "aum": 2e9,
                "leverage": -2, "direction": "Short",
                "benchmark_code": "185:NVDA", "security_class": "CE",
                "etf_type": "Stock", "selection_criteria": "Single asset",
                "turnover": 25e6, "expense_ratio": 1.15,
            },
            "185:NVDL": {
                "name": "GraniteShares 2X Long NVDA Daily ETF", "aum": 5e9,
                "leverage": 2, "direction": "Long",
                "benchmark_code": "185:NVDA", "security_class": "CE",
                "etf_type": "Stock", "selection_criteria": "Single asset",
                "turnover": 400e6, "expense_ratio": 1.15,
            },
        }

        def name_of(self, code):
            return self._DATA[code]["name"]

        def iter_related_etfs(self, stock_code, *, page_size=100):
            if stock_code == "185:NVDA":
                data = self._DATA["185:NVDQ"]
                yield {"code": "185:NVDQ", "holding_weight": None, **data}

        def iter_stock_etfs(self, stock_code, relation="related", *, page_size=1000):
            if stock_code != "185:NVDA":
                return
            if relation == "related":
                yield from self.iter_related_etfs(stock_code, page_size=100)
                return
            codes = (
                ("185:NVDQ",) if relation == "inverse" else
                ("185:NVDQ", "185:NVDL") if relation == "leveraged" else ()
            )
            for code in codes:
                yield {"code": code, "holding_weight": None,
                       "stock_relation_kind": relation, **self._DATA[code]}

        def iter_prompt_etfs(self, prompt_id, *, page_size=100):
            return iter(())

        def etf_metadata(self, codes):
            return {code: dict(self._DATA[code]) for code in codes}

        def etf_holding_weights_for_stock(self, codes, stock_code):
            return {code: None for code in codes}

    selected = [dict(_theme_stocks()[0], is_public_theme_stock=True)]
    result = preselect_etfs(
        InverseQuotes(), selected, theme="AI bubble",
        brief={"keywords": ["artificial intelligence"]}, article_title="Bubble warning",
        limit=20, theme_direction="bearish",
    )
    assert [candidate["code"] for candidate in result.candidates] == ["185:NVDQ"]
    inverse = result.candidates[0]
    assert inverse["selection_lane"] == "single_stock_leveraged"
    assert inverse["single_stock_inverse"] is True
    assert inverse["underlying_code"] == "185:NVDA"
    assert inverse["theme_weight_pct"] == 0
    assert inverse["matched_holdings"] == []
    assert inverse["output_eligible"] is True


def test_bullish_exact_wrapper_is_eligible_with_null_physical_holding():
    class NullHoldingWrapperQuotes:
        _ROW = {
            "code": "185:NVDL",
            "name": "GraniteShares 2X Long NVDA Daily ETF",
            "aum": 5e9,
            "leverage": 2,
            "direction": "Long",
            "benchmark_code": "185:NVDA",
            "security_class": "CE",
            "etf_type": "Stock",
            "selection_criteria": "Single asset",
            "turnover": 400e6,
            "expense_ratio": 1.15,
        }

        def name_of(self, code): return code.partition(":")[2]
        def iter_related_etfs(self, stock_code, *, page_size=100):
            return iter(())
        def iter_stock_etfs(self, stock_code, relation="related", *, page_size=1000):
            if stock_code == "185:NVDA" and relation in {"leveraged", "long"}:
                yield {**self._ROW, "holding_weight": None,
                       "stock_relation_kind": relation}
        def iter_prompt_etfs(self, prompt_id, *, page_size=100):
            return iter(())
        def etf_metadata(self, codes):
            return {code: dict(self._ROW) for code in codes if code == "185:NVDL"}
        def etf_holding_weights_for_stock(self, codes, stock_code):
            return {code: None for code in codes}

    selected = [dict(_theme_stocks()[0], is_public_theme_stock=True)]
    result = preselect_etfs(
        NullHoldingWrapperQuotes(), selected,
        theme="neocloud capacity", brief={}, article_title="", limit=5,
        theme_direction="bullish",
    )

    assert [candidate["code"] for candidate in result.candidates] == ["185:NVDL"]
    wrapper = result.candidates[0]
    assert wrapper["selection_lane"] == "single_stock_leveraged"
    assert wrapper["underlying_code"] == "185:NVDA"
    assert wrapper["matched_holdings"] == []
    assert wrapper["theme_weight_pct"] == 0
    assert wrapper["output_eligible"] is True


def test_generic_related_etf_scan_is_capped_at_100_per_selected_stock():
    class ManyETFQuotes:
        class cfg: scene = "mock"
        def name_of(self, code): return code.partition(":")[2]
        def iter_prompt_etfs(self, prompt_id, *, page_size=100):
            return iter(())
        def iter_related_etfs(self, stock_code, *, page_size=100):
            for i in range(1200):
                yield {"code": f"185:F{i:04d}", "name": f"Fund {i}",
                       "holding_weight": max(0.01, 10 - i / 200),
                       "aum": 1_000_000_000 - i, "leverage": 1,
                       "direction": "Long"}
        def etf_metadata(self, codes):
            return {code: {"name": code.partition(":")[2], "aum": 1_000_000,
                           "leverage": 1, "direction": "Long",
                           "security_class": "CE", "etf_type": "Stock",
                           "asset_class": "Equity",
                           "selection_criteria": "Index basket"}
                    for code in codes}
        def etf_holding_weights_for_stock(self, codes, stock_code):
            return {code: 2.0 for code in codes}

    one_stock = [_theme_stocks()[0]]
    for limit in (100, 1000):
        result = preselect_etfs(
            ManyETFQuotes(), one_stock, theme="novel photonics packaging",
            brief={}, article_title="", limit=limit,
        )
        assert result.discovered == 100
        assert len(result.candidates) == 100
        assert result.candidates[-1]["rank"] == 100
        assert all(candidate["leverage"] == 1 for candidate in result.candidates)


def test_verified_direct_probe_survives_saturated_500_ordinary_candidate_cap():
    class SaturatedQuotes:
        _WRAPPER = {
            "code": "185:NVDL",
            "name": "Daily 2x Long NVDA ETF",
            "aum": 5_000_000_000,
            "turnover": 400_000_000,
            "expense_ratio": 1.15,
            "leverage": 2,
            "direction": "Long",
            "benchmark_code": "185:NVDA",
            "security_class": "CE",
            "etf_type": "Stock",
            "selection_criteria": "Single asset",
        }

        def name_of(self, code):
            if code == self._WRAPPER["code"]:
                return self._WRAPPER["name"]
            return f"Artificial Intelligence Basket {code.partition(':')[2]}"

        def iter_stock_etfs(self, stock_code, relation="related", *, page_size=1000):
            if stock_code == "185:NVDA" and relation in {"leveraged", "long"}:
                yield {**self._WRAPPER, "holding_weight": None,
                       "stock_relation_kind": relation}

        def iter_related_etfs(self, stock_code, *, page_size=100):
            return iter(())

        def iter_prompt_etfs(self, prompt_id, *, page_size=100):
            for index in range(700):
                code = f"185:AI{index:03d}"
                yield {
                    "code": code,
                    "name": f"Artificial Intelligence Basket {index:03d}",
                    "aum": 1_000_000_000 - index,
                    "turnover": 10_000_000,
                    "expense_ratio": 0.50,
                    "leverage": 1,
                    "direction": "Long",
                    "security_class": "CE",
                    "etf_type": "Stock",
                    "asset_class": "Equity",
                    "selection_criteria": "Index basket",
                }

        def etf_metadata(self, codes):
            metadata = {}
            for code in codes:
                if code == self._WRAPPER["code"]:
                    metadata[code] = dict(self._WRAPPER)
                elif code.startswith("185:AI"):
                    metadata[code] = {
                        "name": self.name_of(code),
                        "aum": 1_000_000_000,
                        "turnover": 10_000_000,
                        "expense_ratio": 0.50,
                        "leverage": 1,
                        "direction": "Long",
                        "security_class": "CE",
                        "etf_type": "Stock",
                        "asset_class": "Equity",
                        "selection_criteria": "Index basket",
                    }
            return metadata

        def etf_holding_weights_for_stock(self, codes, stock_code):
            return {code: None for code in codes}

    selected = [{
        "code": "185:NVDA", "name": "NVIDIA", "ai_relevance": 5,
        "confidence": 0.95, "exposure_type": "direct",
        "is_public_theme_stock": True,
    }]
    result = preselect_etfs(
        SaturatedQuotes(), selected, theme="artificial intelligence",
        brief={"etf_exposure_terms": ["artificial intelligence"]},
        article_title="", limit=10_000, max_pools=1,
    )

    codes = [candidate["code"] for candidate in result.candidates]
    ordinary_codes = [code for code in codes if code.startswith("185:AI")]
    assert len(ordinary_codes) == 500
    assert len(result.candidates) == 501
    assert result.discovered == 501
    assert "185:NVDL" in codes
    wrapper = next(candidate for candidate in result.candidates
                   if candidate["code"] == "185:NVDL")
    assert wrapper["selection_lane"] == "single_stock_leveraged"
    assert wrapper["underlying_code"] == "185:NVDA"
    assert wrapper["output_eligible"] is True


def test_low_liquidity_is_eligible_but_receives_an_investability_penalty():
    liquid = {
        "code": "185:LIQ", "selection_lane": "multi_stock_basket",
        "output_eligible": True, "theme_evidence_score": 0.70,
        "aum": 1_000_000_000, "turnover": 20_000_000, "expense_ratio": 0.50,
    }
    thin = {
        "code": "185:THIN", "selection_lane": "multi_stock_basket",
        "output_eligible": True, "theme_evidence_score": 0.70,
        "aum": 10_000_000, "turnover": 500_000, "expense_ratio": 0.50,
    }

    apply_unified_etf_scores([thin, liquid])

    assert thin["output_eligible"] is True
    assert thin["low_liquidity"] is True
    assert set(thin["low_liquidity_reasons"]) == {
        "aum_below_25m", "turnover_below_1m",
    }
    assert liquid["low_liquidity"] is False
    assert thin["investability_score"] < liquid["investability_score"]
    assert thin["unified_score"] < liquid["unified_score"]


def test_high_purity_basket_can_outrank_weak_exact_wrapper_despite_direct_bonus():
    weak_wrapper = {
        "code": "185:WEAK2X", "selection_lane": "single_stock_leveraged",
        "underlying_code": "185:WEAK", "output_eligible": True,
        "theme_evidence_score": 0.50, "aum": 10_000_000,
        "turnover": 100_000, "expense_ratio": 1.50,
    }
    high_purity_basket = {
        "code": "185:PURE", "selection_lane": "multi_stock_basket",
        "output_eligible": True, "theme_evidence_score": 0.95,
        "aum": 2_000_000_000, "turnover": 50_000_000,
        "expense_ratio": 0.25,
    }

    apply_unified_etf_scores([weak_wrapper, high_purity_basket])

    assert weak_wrapper["exact_selected_stock_direct_bonus"] == 1.0
    assert high_purity_basket["exact_selected_stock_direct_bonus"] == 0.0
    assert high_purity_basket["unified_score"] > weak_wrapper["unified_score"]
    deduped = select_output_etfs(
        [weak_wrapper, high_purity_basket], limit=2,
    )
    assert deduped == [high_purity_basket, weak_wrapper]
    assert compose_output_etfs(deduped, 1) == [weak_wrapper]
    assert compose_output_etfs(deduped, 2) == [weak_wrapper, high_purity_basket]


def test_output_composition_reserves_basket_without_wrapper_and_rejects_ineligible():
    direct_leader = {
        "code": "185:GOLD", "selection_lane": "direct_asset",
        "output_eligible": True, "unified_score": 0.95,
    }
    direct_second = {
        "code": "185:BTC", "selection_lane": "direct_asset",
        "output_eligible": True, "unified_score": 0.90,
    }
    eligible_basket = {
        "code": "185:VDC", "selection_lane": "multi_stock_basket",
        "output_eligible": True, "unified_score": 0.80,
    }
    ineligible_basket = {
        "code": "185:VTI", "selection_lane": "multi_stock_basket",
        "output_eligible": False, "unified_score": 1.00,
    }
    ranked = [ineligible_basket, direct_leader, direct_second, eligible_basket]

    assert compose_output_etfs(ranked, 1) == [direct_leader]
    assert compose_output_etfs(ranked, 2) == [direct_leader, eligible_basket]
    assert compose_output_etfs(ranked, 5) == [
        direct_leader, direct_second, eligible_basket,
    ]
    assert ineligible_basket not in compose_output_etfs(ranked, 5)


def test_output_composition_runs_after_wrapper_and_basket_economic_deduplication():
    def wrapper(code, underlying, score):
        return {
            "code": code, "selection_lane": "single_stock_leveraged",
            "underlying_code": underlying, "output_eligible": True,
            "unified_score": score, "theme_evidence_score": score,
        }

    def basket(code, identity, score):
        return {
            "code": code, "selection_lane": "multi_stock_basket",
            "base_index_code": identity, "output_eligible": True,
            "unified_score": score, "theme_evidence_score": score,
            "full_holdings": [
                {"code": "185:A", "name": "Alpha", "weight_pct": 60},
                {"code": "185:B", "name": "Beta", "weight_pct": 40},
            ],
        }

    preferred_wrapper = wrapper("185:COTG", "185:COTY", 0.70)
    duplicate_wrapper = wrapper("185:COT2", "185:COTY", 0.60)
    preferred_basket = basket("185:VDC", "89:CONS", 0.90)
    duplicate_basket = basket("185:XLP", "89:CONS", 0.80)
    other_basket = basket("185:XRT", "89:RETAIL", 0.75)
    other_basket["full_holdings"] = [
        {"code": "185:C", "name": "Gamma", "weight_pct": 50},
        {"code": "185:D", "name": "Delta", "weight_pct": 50},
    ]

    deduped = select_output_etfs([
        duplicate_wrapper, preferred_basket, duplicate_basket,
        preferred_wrapper, other_basket,
    ], limit=None)
    composed = compose_output_etfs(deduped, 5)

    assert [candidate["code"] for candidate in composed] == [
        "185:COTG", "185:VDC", "185:XRT",
    ]
    assert duplicate_wrapper["dedupe_reason"] == "duplicate_selected_stock_underlying"
    assert duplicate_basket["dedupe_reason"] == "duplicate_benchmark_or_base_index"


def test_direct_wrapper_output_dedupes_to_one_product_per_underlying():
    def wrapper(code, underlying, score, turnover):
        return {
            "code": code, "selection_lane": "single_stock_leveraged",
            "underlying_code": underlying, "output_eligible": True,
            "unified_score": score, "theme_evidence_score": 0.80,
            "turnover": turnover, "aum": 1_000_000_000,
            "expense_ratio": 1.15,
        }

    preferred = wrapper("185:NVDL", "185:NVDA", 0.90, 50_000_000)
    duplicate = wrapper("185:NVDU", "185:NVDA", 0.80, 20_000_000)
    other = wrapper("185:TSLL", "185:TSLA", 0.70, 10_000_000)

    selected = select_output_etfs([duplicate, other, preferred], limit=5)

    assert [candidate["code"] for candidate in selected] == ["185:NVDL", "185:TSLL"]
    assert duplicate["dedupe_excluded"] is True
    assert duplicate["dedupe_reason"] == "duplicate_selected_stock_underlying"


def test_basket_output_dedupes_by_structured_index_and_weighted_overlap():
    def basket(code, score, identity, holdings, *, identity_field="benchmark_code"):
        return {
            "code": code, "selection_lane": "multi_stock_basket",
            "output_eligible": True, "unified_score": score,
            "theme_evidence_score": score,
            identity_field: identity,
            "full_holdings": [
                {"code": holding_code, "name": holding_name,
                 "weight_pct": weight}
                for holding_code, holding_name, weight in holdings
            ],
        }

    leader = basket("185:LEAD", 0.90, "89:IDX1", [
        ("185:AAA", "Alpha Systems", 94),
        ("185:BBB", "Beta Systems", 6),
    ])
    same_index = basket(
        "185:SAMEIDX", 0.80, "89:IDX1",
        [("185:CCC", "Gamma Systems", 100)],
        identity_field="base_index_code",
    )
    overlapping = basket(
        "185:OVERLAP", 0.70, "89:IDX2", [
            ("185:AAA", "Alpha Systems", 95),
            ("185:BBB", "Beta Systems", 5),
        ], identity_field="index_etf_code",
    )
    distinct = basket("185:DISTINCT", 0.60, "89:IDX3", [
        ("185:DDD", "Delta Systems", 60),
        ("185:EEE", "Epsilon Systems", 40),
    ])

    selected = select_output_etfs(
        [distinct, overlapping, same_index, leader], limit=5,
    )

    assert [candidate["code"] for candidate in selected] == [
        "185:LEAD", "185:DISTINCT",
    ]
    assert same_index["dedupe_reason"] == "duplicate_benchmark_or_base_index"
    assert overlapping["dedupe_reason"] == "weighted_portfolio_overlap"


def test_etf_component_scoring_never_scores_fund_tickers():
    scored = []
    class TrackingLLM(FakeLLM):
        def chat_json(self, system, user, **kw):
            if "Score each candidate" in user:
                scored.extend(
                    line.split("|")[0].strip() for line in user.splitlines()
                    if "|" in line and ":" in line.split("|")[0]
                )
            return super().chat_json(system, user, **kw)

    result = ThemeWorkflow(
        TrackingLLM(), FakeQuotes(), {"stock_universe": 20, "etf_universe": 100}
    ).run({"theme": "AI memory", "date": "2026-07-09", "url": "https://example.com/x"})
    assert 1 <= len(result["ThemeEtfs"]) <= 5
    assert scored
    assert all(code.startswith(("185:S", "185:OTHER")) or code in ("185:NVDA", "185:MU")
               for code in scored)
    assert not any(code in FakeQuotes._ETF_DATA for code in scored)


def _candidate(code, legacy_score, *, aum=1_000_000_000, pool_strength=0.0):
    return {
        "code": code, "name": code.partition(":")[2], "base_theme_exposure": legacy_score,
        "static_theme_exposure": legacy_score, "preselect_score": legacy_score,
        "weighted_theme_exposure_pct": legacy_score * 50, "theme_breadth": 1,
        "pool_strength": pool_strength, "normalised_leverage": 0.0,
        "aum": aum, "direction": "Long", "leverage": 1,
        "pool_matches": {}, "matched_holdings": [], "mandate_verified": True,
    }


def _component_score(code, candidate_id, *, relevance=5,
                     exposure_type="direct", confidence=0.95):
    relevant = relevance >= 3.3 and exposure_type not in {
        "unclear", "diversified", "factor_proxy",
    }
    return {
        "candidate_id": candidate_id,
        "market_code": code,
        "ai_relevance": relevance,
        "exposure_type": exposure_type,
        "confidence": confidence,
        "impact_channel": "revenue_demand" if relevant else "none",
        "theme_specificity": "company_specific" if relevant else "none",
        "materiality": "high" if relevant else "unknown",
        "evidence_strength": "explicit" if relevant else "none",
        "reason": "Direct consumer revenue exposure" if relevant else "Unrelated",
    }


def _component_evidence(code, *, relevance=5, exposure_type="direct",
                        confidence=0.95):
    evidence = _component_score(
        code, "S0001", relevance=relevance,
        exposure_type=exposure_type, confidence=confidence,
    )
    evidence.pop("candidate_id")
    evidence.pop("market_code")
    evidence["relevance_status"] = "scored"
    return evidence


def test_etf_component_prompt_is_theme_first_when_article_is_empty_or_unrelated():
    class CapturingLLM:
        def __init__(self):
            self.calls = []

        def chat_json(self, system, user, **kw):
            self.calls.append((system, user))
            rows = []
            for line in user.splitlines():
                parts = [part.strip() for part in line.split("|")]
                if len(parts) >= 3 and ":" in parts[0]:
                    rows.append(_component_score(parts[0], parts[1]))
            return {"results": rows}

    llm = CapturingLLM()
    wf = ThemeWorkflow(llm, FakeQuotes())
    wf._exact_theme = "American Consumer"
    wf._theme_profile = {
        "exact_theme": "American Consumer",
        "canonical_name": "United States consumer economy",
        "canonical_definition": "Businesses materially exposed to U.S. household demand.",
        "exclusions": ["broad market exposure alone"],
    }
    for index, article in enumerate((
        {"ok": False, "title": "", "text": "", "url": ""},
        {"ok": True, "title": "Rate cuts", "text": "Unrelated bond-market news."},
    )):
        scores = wf.score_etf_holding_relevance(
            {"theme_direction": "bullish"},
            [{"code": f"185:C{index}", "name": f"Consumer {index}"}],
            article,
        )
        assert scores[f"185:C{index}"]["relevance_status"] == "scored"

    assert len(llm.calls) == 2
    for system, user in llm.calls:
        normalized_user = re.sub(r"\s+", " ", user)
        assert system == workflow_module.prompts.ETF_HOLDING_RELEVANCE_SYS
        assert "Exact trusted theme label (dominant): American Consumer" in normalized_user
        assert "Frozen theme profile (authoritative)" in normalized_user
        assert "United States consumer economy" in normalized_user
        assert "article is optional corroboration" in normalized_user
        assert "article-supported" not in normalized_user
    assert '"excerpt": ""' in llm.calls[0][1]
    assert "Unrelated bond-market news" in llm.calls[1][1]


def test_etf_component_scoring_reuses_negative_stock_scores_and_only_calls_unknowns():
    class CapturingLLM:
        def __init__(self):
            self.calls = []

        def chat_json(self, system, user, **kw):
            parsed = []
            for line in user.splitlines():
                parts = [part.strip() for part in line.split("|")]
                if len(parts) >= 3 and ":" in parts[0]:
                    parsed.append((parts[0], parts[1]))
            self.calls.append([code for code, _ in parsed])
            return {"results": [
                _component_score(code, candidate_id)
                for code, candidate_id in parsed
            ]}

    negative_seed = {
        "ai_relevance": 1,
        "theme_relevance": 1,
        "exposure_type": "unclear",
        "confidence": 0.9,
        "impact_channel": "none",
        "theme_specificity": "none",
        "materiality": "unknown",
        "evidence_strength": "none",
        "reason": "No consumer exposure",
        "relevance_status": "scored",
    }
    positive_seed = {
        **_component_evidence("185:KNOWN_POSITIVE"),
        "theme_relevance": 5,
    }
    llm = CapturingLLM()
    scores = ThemeWorkflow(llm, FakeQuotes()).score_etf_holding_relevance(
        {"theme": "American Consumer"},
        _rows("185:KNOWN_POSITIVE", "185:KNOWN_NEGATIVE", "185:UNKNOWN"),
        seed_scores={
            "185:KNOWN_POSITIVE": positive_seed,
            "185:KNOWN_NEGATIVE": negative_seed,
        },
    )

    assert llm.calls == [["185:UNKNOWN"]]
    assert scores["185:KNOWN_POSITIVE"]["ai_relevance"] == 5
    assert scores["185:KNOWN_POSITIVE"]["relevance_retry"] == "reused_stock_score"
    assert scores["185:KNOWN_NEGATIVE"]["ai_relevance"] == 1
    assert scores["185:KNOWN_NEGATIVE"]["relevance_status"] == "scored"
    assert scores["185:KNOWN_NEGATIVE"]["relevance_retry"] == "reused_stock_score"
    assert scores["185:KNOWN_NEGATIVE"]["relevance_source"] == "stock_score_reuse"
    assert scores["185:UNKNOWN"]["relevance_status"] == "scored"


def test_etf_holding_payload_error_splits_twenty_rows_into_weight_first_groups_of_five():
    class PayloadError(Exception):
        retryable = False
        status_code = 400

    class SizeLimitedLLM:
        def __init__(self):
            self.calls = []

        def chat_json(self, system, user, **kw):
            parsed = []
            for line in user.splitlines():
                parts = [part.strip() for part in line.split("|")]
                if len(parts) >= 3 and ":" in parts[0]:
                    parsed.append((parts[0], parts[1]))
            self.calls.append([code for code, _ in parsed])
            if len(parsed) > 5:
                raise PayloadError("payload too large")
            return {"results": [
                _component_score(code, candidate_id)
                for code, candidate_id in parsed
            ]}

    rows = _rows(*[f"185:C{i:02d}" for i in range(20)])
    for index, row in enumerate(rows):
        row["_max_portfolio_weight_pct"] = float(index + 1)
        row["_aggregate_portfolio_weight_pct"] = float(100 - index)
    llm = SizeLimitedLLM()
    scores = ThemeWorkflow(llm, FakeQuotes(), {
        "etf_holding_relevance_batch": 20,
        "etf_holding_relevance_retry_budget": 20,
    }).score_etf_holding_relevance({}, rows)

    assert len(llm.calls) == 5
    assert len(llm.calls[0]) == 20
    assert all(len(batch) <= 5 for batch in llm.calls[1:])
    expected_retry_order = [f"185:C{i:02d}" for i in reversed(range(20))]
    assert [code for batch in llm.calls[1:] for code in batch] == expected_retry_order
    assert all(score["relevance_status"] == "scored" for score in scores.values())
    assert all(score["relevance_retry"] == "recovered" for score in scores.values())


def test_etf_holding_auth_failures_and_persistent_single_rows_stay_closed():
    class RequestError(Exception):
        def __init__(self, status_code, *, retryable=False):
            super().__init__(f"HTTP {status_code}")
            self.status_code = status_code
            self.retryable = retryable

    class BrokenLLM:
        def __init__(self, status_code, *, retryable=False):
            self.status_code = status_code
            self.retryable = retryable
            self.calls = 0

        def chat_json(self, system, user, **kw):
            self.calls += 1
            raise RequestError(self.status_code, retryable=self.retryable)

    for status_code in (401, 403):
        llm = BrokenLLM(status_code)
        scores = ThemeWorkflow(llm, FakeQuotes()).score_etf_holding_relevance(
            {}, _rows("185:AUTH"))
        assert llm.calls == 1
        assert scores["185:AUTH"]["relevance_status"] == "missing"

    transient = BrokenLLM(503, retryable=True)
    scores = ThemeWorkflow(transient, FakeQuotes()).score_etf_holding_relevance(
        {}, _rows("185:PERSISTENT"))
    assert transient.calls == 2
    assert scores["185:PERSISTENT"]["relevance_status"] == "missing"
    assert scores["185:PERSISTENT"]["relevance_retry"] == "persistent_miss"


def test_full_component_portfolio_demotes_broad_high_aum_fund():
    broad = _candidate("185:BROAD", 0.95, aum=300_000_000_000)
    focused = _candidate("185:FOCUS", 0.40, aum=100_000_000)
    candidates = [broad, focused]
    holdings = {
        "185:BROAD": [
            {"code": "185:ANCHOR", "name": "Anchor", "weight_pct": 30},
            {"code": "185:OTHER", "name": "Other", "weight_pct": 70},
        ],
        "185:FOCUS": [
            {"code": "185:T1", "name": "Theme One", "weight_pct": 45},
            {"code": "185:T2", "name": "Theme Two", "weight_pct": 45},
            {"code": "185:CASH", "name": "Cash", "weight_pct": 10},
        ],
    }
    relevant = {
        code: {"ai_relevance": 5, "exposure_type": "direct", "confidence": 1,
               "reason": "direct", "relevance_status": "scored",
               "impact_channel": "revenue_demand",
               "theme_specificity": "company_specific", "materiality": "high",
               "evidence_strength": "explicit"}
        for code in ("185:ANCHOR", "185:T1", "185:T2")
    }
    relevant.update({
        code: {"ai_relevance": 1, "exposure_type": "unclear", "confidence": 1,
               "reason": "unrelated", "relevance_status": "scored",
               "impact_channel": "none", "theme_specificity": "none",
               "materiality": "unknown", "evidence_strength": "none"}
        for code in ("185:OTHER", "185:CASH")
    })

    rerank_with_component_holdings(candidates, holdings, relevant)

    assert candidates[0]["code"] == "185:FOCUS"
    assert candidates[0]["ranking_mode"] == "full_holdings"
    assert candidates[1]["ranking_mode"] == "full_holdings"
    assert candidates[0]["holding_theme_mass"] > candidates[1]["holding_theme_mass"]


def test_component_assessment_deduplicates_shared_holdings_and_scores_nonanchors():
    class Quotes:
        def iter_etf_holdings(self, code, *, page_size=1000):
            rows = {
                "185:A": [("185:SHARED", 50), ("185:ONLYA", 50)],
                "185:B": [("185:SHARED", 50), ("185:ONLYB", 50)],
            }[code]
            for component, weight in rows:
                yield {"code": component, "name": component, "weight_pct": weight}

    class HoldingLLM:
        def __init__(self): self.codes = []
        def chat_json(self, system, user, **kw):
            for line in user.splitlines():
                if "|" not in line or ":" not in line.split("|")[0]:
                    continue
                code = line.split("|")[0].strip()
                self.codes.append(code)
            return [{"market_code": code, "ai_relevance": 5,
                     "exposure_type": "direct", "confidence": 1, "reason": "direct",
                     "impact_channel": "revenue_demand",
                     "theme_specificity": "company_specific", "materiality": "high",
                     "evidence_strength": "explicit"}
                    for code in self.codes[-3:]]

    llm = HoldingLLM()
    wf = ThemeWorkflow(llm, Quotes(), {
        "etf_target": 2, "etf_holdings_unique_budget": 10,
        "etf_holding_relevance_batch": 10,
    })
    candidates = [_candidate("185:A", 0.5), _candidate("185:B", 0.4)]
    wf.rerank_etfs_from_components(candidates, {}, {}, "bullish")

    assert sorted(llm.codes) == ["185:ONLYA", "185:ONLYB", "185:SHARED"]
    assert llm.codes.count("185:SHARED") == 1
    assert all(candidate["ranking_mode"] == "full_holdings" for candidate in candidates)


def test_etf_holding_provider_error_recovers_failed_batch_once_and_continues():
    class GatewayTransientError(Exception):
        retryable = True

    class FlakyLLM:
        def __init__(self):
            self.calls = 0

        def chat_json(self, system, user, **kw):
            self.calls += 1
            if self.calls == 1:
                raise GatewayTransientError("gateway temporarily unavailable")
            codes = [line.split("|")[0].strip() for line in user.splitlines()
                     if "|" in line and ":" in line.split("|")[0]]
            return [{
                "market_code": code, "ai_relevance": 5,
                "exposure_type": "direct", "confidence": 1,
                "impact_channel": "revenue_demand",
                "theme_specificity": "company_specific", "materiality": "high",
                "evidence_strength": "explicit", "reason": "direct",
            } for code in codes]

    logs = []
    original_log = workflow_module._log
    workflow_module._log = logs.append
    try:
        llm = FlakyLLM()
        scores = ThemeWorkflow(llm, FakeQuotes(), {
            "etf_holding_relevance_batch": 2,
        }).score_etf_holding_relevance(
            {}, _rows("185:A", "185:B", "185:C", "185:D"))
    finally:
        workflow_module._log = original_log

    assert llm.calls == 3
    assert all(scores[code]["relevance_status"] == "scored"
               for code in ("185:A", "185:B", "185:C", "185:D"))
    assert all(scores[code]["relevance_retry"] == "recovered"
               for code in ("185:A", "185:B"))
    assert all(scores[code]["relevance_retry"] == "not_needed"
               for code in ("185:C", "185:D"))
    assert any("ETF holding relevance batch failed" in line
               and "2 companies unresolved" in line for line in logs)
    assert any("retrying 2 unresolved companies once" in line for line in logs)


def test_etf_holding_partial_response_retries_only_unresolved_rows_with_schema():
    class PartialLLM:
        def __init__(self):
            self.calls = []
            self.schemas = []

        def chat_json(self, system, user, **kw):
            rows = []
            for line in user.splitlines():
                parts = [part.strip() for part in line.split("|")]
                if len(parts) >= 3 and ":" in parts[0]:
                    rows.append((parts[0], parts[1]))
            self.calls.append([code for code, _ in rows])
            self.schemas.append(kw.get("response_schema"))
            if len(self.calls) == 1:
                rows = rows[:1]
            return {"results": [{
                "candidate_id": candidate_id,
                "market_code": code,
                "ai_relevance": 1,
                "exposure_type": "unclear",
                "confidence": 0.2,
                "impact_channel": "none",
                "theme_specificity": "none",
                "materiality": "unknown",
                "evidence_strength": "none",
                "reason": "unrelated",
            } for code, candidate_id in rows]}

    llm = PartialLLM()
    scores = ThemeWorkflow(llm, FakeQuotes(), {
        "etf_holding_relevance_batch": 10,
    }).score_etf_holding_relevance(
        {}, _rows("185:A", "185:B", "185:C"))

    assert llm.calls == [["185:A", "185:B", "185:C"], ["185:B", "185:C"]]
    assert scores["185:A"]["relevance_retry"] == "not_needed"
    assert scores["185:B"]["relevance_retry"] == "recovered"
    assert scores["185:C"]["relevance_retry"] == "recovered"
    first_results = llm.schemas[0]["properties"]["results"]
    assert first_results["minItems"] == first_results["maxItems"] == 3
    required = set(first_results["items"]["required"])
    assert {"candidate_id", "market_code", "ai_relevance"} <= required


def test_etf_holding_permanent_provider_error_is_not_workflow_retried():
    class PermanentGatewayError(Exception):
        retryable = False

    class BrokenLLM:
        def __init__(self):
            self.calls = 0

        def chat_json(self, system, user, **kw):
            self.calls += 1
            raise PermanentGatewayError("invalid credentials")

    llm = BrokenLLM()
    scores = ThemeWorkflow(llm, FakeQuotes(), {
        "etf_holding_relevance_batch": 2,
    }).score_etf_holding_relevance(
        {}, _rows("185:A", "185:B", "185:C", "185:D"))

    assert llm.calls == 2
    assert all(score["relevance_status"] == "missing"
               for score in scores.values())


def test_etf_holding_recovery_budget_caps_retry_amplification():
    class SparseLLM:
        def __init__(self):
            self.calls = []

        def chat_json(self, system, user, **kw):
            rows = []
            for line in user.splitlines():
                parts = [part.strip() for part in line.split("|")]
                if len(parts) >= 3 and ":" in parts[0]:
                    rows.append((parts[0], parts[1]))
            self.calls.append([code for code, _ in rows])
            if len(self.calls) == 1:
                return {"results": []}
            return {"results": [{
                "candidate_id": candidate_id,
                "market_code": code,
                "ai_relevance": 1,
                "exposure_type": "unclear",
                "confidence": 0.2,
                "impact_channel": "none",
                "theme_specificity": "none",
                "materiality": "unknown",
                "evidence_strength": "none",
                "reason": "unrelated",
            } for code, candidate_id in rows]}

    llm = SparseLLM()
    scores = ThemeWorkflow(llm, FakeQuotes(), {
        "etf_holding_relevance_batch": 10,
        "etf_holding_relevance_retry_budget": 3,
    }).score_etf_holding_relevance(
        {}, _rows("185:A", "185:B", "185:C", "185:D", "185:E", "185:F"))

    assert llm.calls == [
        ["185:A", "185:B", "185:C", "185:D", "185:E", "185:F"],
        ["185:A", "185:B", "185:C"],
    ]
    assert sum(score["relevance_status"] == "scored"
               for score in scores.values()) == 3
    assert sum(score["relevance_status"] == "missing"
               for score in scores.values()) == 3


def test_incomplete_component_scores_fail_closed():
    candidate = _candidate("185:FUND", 0.7)
    holdings = {"185:FUND": [
        {"code": "185:A", "name": "A", "weight_pct": 70},
        {"code": "185:B", "name": "B", "weight_pct": 30},
    ]}
    scores = {
        "185:A": {"ai_relevance": 5, "exposure_type": "direct", "confidence": 1,
                   "reason": "direct", "relevance_status": "scored",
                   "impact_channel": "revenue_demand",
                   "theme_specificity": "company_specific", "materiality": "high",
                   "evidence_strength": "explicit"},
        "185:B": {"ai_relevance": 1, "exposure_type": "unclear", "confidence": 0,
                   "reason": "", "relevance_status": "missing"},
    }

    rerank_with_component_holdings([candidate], holdings, scores)

    assert candidate["ranking_mode"] == "holdings_incomplete"
    assert candidate["preselect_score"] == 0.0
    assert candidate["output_eligible"] is False
    assert candidate["semantic_scored_weight_pct"] == 70


def test_partial_component_lower_bound_qualifies_only_on_known_two_issuer_mass():
    candidate = _candidate("185:PARTIAL", 0.7)
    holdings = {"185:PARTIAL": [
        {"code": "185:A", "name": "Alpha Consumer", "weight_pct": 35},
        {"code": "185:B", "name": "Beta Consumer", "weight_pct": 35},
        {"code": "185:UNRESOLVED", "name": "Unknown Holding", "weight_pct": 30},
    ]}
    scores = {
        "185:A": _component_evidence("185:A"),
        "185:B": _component_evidence("185:B"),
        "185:UNRESOLVED": {
            "relevance_status": "missing", "ai_relevance": 1,
            "exposure_type": "unclear", "confidence": 0,
        },
    }

    rerank_with_component_holdings([candidate], holdings, scores)

    assert candidate["output_eligible"] is True
    assert candidate["ranking_mode"] == "partial_holdings_lower_bound"
    assert candidate["partial_evidence_mode"] is True
    assert candidate["semantic_coverage_status"] == "partial_lower_bound"
    assert candidate["semantic_scored_weight_pct"] == 70
    assert candidate["semantic_scored_share"] == 0.70
    assert candidate["holding_theme_breadth"] == 2
    assert candidate["theme_evidence_score"] >= 0.25
    assert "unresolved holdings count as zero exposure" in candidate["reason"]


def test_component_assessment_logs_partial_lower_bound_qualifiers():
    class Quotes:
        def iter_etf_holdings(self, code, *, page_size=1000):
            yield {"code": "185:A", "name": "Alpha Consumer", "weight_pct": 35}
            yield {"code": "185:B", "name": "Beta Consumer", "weight_pct": 35}
            yield {"code": "185:UNKNOWN", "name": "Unknown", "weight_pct": 30}

    class EmptyLLM:
        def chat_json(self, system, user, **kw):
            return {"results": []}

    stock_scores = []
    for code in ("185:A", "185:B"):
        stock_scores.append({"code": code, **_component_evidence(code)})
    candidate = _candidate("185:PARTIAL", 0.7)
    logs = []
    original_log = workflow_module._log
    workflow_module._log = logs.append
    try:
        ThemeWorkflow(EmptyLLM(), Quotes(), {
            "etf_target": 1,
            "etf_holdings_unique_budget": 10,
            "etf_holdings_portfolio_budget": 1,
            "etf_holding_relevance_batch": 10,
        }).rerank_etfs_from_components(
            [candidate], {}, {}, "bullish", stock_scores=stock_scores,
        )
    finally:
        workflow_module._log = original_log

    assert candidate["ranking_mode"] == "partial_holdings_lower_bound"
    assert candidate["output_eligible"] is True
    assert any(
        "partial-evidence lower-bound qualifiers" in line
        and "185:PARTIAL" in line
        and "zero exposure" in line
        for line in logs
    )


def test_partial_lower_bound_rejects_broad_market_incidental_exposure():
    candidate = _candidate("185:BROAD", 0.9)
    holdings = {"185:BROAD": [
        {"code": "185:A", "name": "Alpha Consumer", "weight_pct": 10},
        {"code": "185:B", "name": "Beta Consumer", "weight_pct": 10},
        {"code": "185:UNRESOLVED", "name": "Broad Market", "weight_pct": 80},
    ]}
    scores = {
        "185:A": _component_evidence("185:A"),
        "185:B": _component_evidence("185:B"),
        "185:UNRESOLVED": {
            "relevance_status": "missing", "ai_relevance": 1,
            "exposure_type": "unclear", "confidence": 0,
        },
    }

    rerank_with_component_holdings([candidate], holdings, scores)

    assert candidate["output_eligible"] is False
    assert candidate["ranking_mode"] == "holdings_incomplete"
    assert candidate["partial_evidence_mode"] is False
    assert candidate["theme_evidence_score"] < 0.25
    assert "semantic_coverage_below_minimum" in candidate[
        "eligibility_rejection_reasons"]
    assert "theme_score_below_minimum" in candidate[
        "eligibility_rejection_reasons"]


def test_holdings_lower_bound_keeps_reported_coverage_and_full_negative_gates():
    underreported = _candidate("185:UNDER60", 0.8)
    unavailable = _candidate("185:UNAVAILABLE", 0.8)
    fully_negative = _candidate("185:NEGATIVE", 0.8)
    holdings = {
        "185:UNDER60": [
            {"code": "185:A", "name": "Alpha", "weight_pct": 30},
            {"code": "185:B", "name": "Beta", "weight_pct": 20},
        ],
        "185:NEGATIVE": [
            {"code": "185:X", "name": "Unrelated X", "weight_pct": 50},
            {"code": "185:Y", "name": "Unrelated Y", "weight_pct": 50},
        ],
    }
    scores = {
        "185:A": _component_evidence("185:A"),
        "185:B": _component_evidence("185:B"),
        "185:X": _component_evidence(
            "185:X", relevance=1, exposure_type="unclear"),
        "185:Y": _component_evidence(
            "185:Y", relevance=1, exposure_type="unclear"),
    }

    rerank_with_component_holdings(
        [underreported, unavailable, fully_negative], holdings, scores)

    assert underreported["holdings_weight_coverage_pct"] == 50
    assert underreported["ranking_mode"] == "holdings_incomplete"
    assert underreported["output_eligible"] is False
    assert unavailable["ranking_mode"] == "holdings_unavailable"
    assert unavailable["output_eligible"] is False
    assert fully_negative["semantic_scored_share"] == 1.0
    assert fully_negative["ranking_mode"] == "full_holdings"
    assert fully_negative["holding_theme_breadth"] == 0
    assert fully_negative["output_eligible"] is False


def test_holdings_budget_is_strict_and_never_truncates_a_portfolio():
    class Quotes:
        def iter_etf_holdings(self, code, *, page_size=1000):
            for index in range(3):
                yield {"code": f"185:{code[-1]}{index}", "name": "Component",
                       "weight_pct": 100 / 3}

    class LLM:
        def __init__(self): self.codes = []
        def chat_json(self, system, user, **kw):
            batch = []
            for line in user.splitlines():
                if "|" in line and ":" in line.split("|")[0]:
                    code = line.split("|")[0].strip()
                    self.codes.append(code)
                    batch.append(code)
            return [{"market_code": code, "ai_relevance": 4,
                     "exposure_type": "direct", "confidence": 1, "reason": "direct",
                     "impact_channel": "revenue_demand",
                     "theme_specificity": "company_specific", "materiality": "high",
                     "evidence_strength": "explicit"}
                    for code in batch]

    llm = LLM()
    wf = ThemeWorkflow(llm, Quotes(), {
        "etf_target": 2, "etf_holdings_unique_budget": 4,
        "etf_holding_relevance_batch": 20,
    })
    candidates = [_candidate("185:A", 0.9), _candidate("185:B", 0.8),
                  _candidate("185:C", 0.7)]
    wf.rerank_etfs_from_components(candidates, {}, {}, "bullish")

    assert len(llm.codes) == 3
    assert all(code.startswith("185:A") for code in llm.codes)
    by_code = {candidate["code"]: candidate for candidate in candidates}
    assert by_code["185:A"]["ranking_mode"] == "full_holdings"
    assert by_code["185:B"]["ranking_mode"] == "holdings_unavailable"
    assert by_code["185:C"]["ranking_mode"] == "holdings_unavailable"


def test_promoted_candidate_is_component_assessed_before_output_boundary_stabilizes():
    class Quotes:
        def __init__(self): self.requested = []
        def iter_etf_holdings(self, code, *, page_size=1000):
            self.requested.append(code)
            if code == "185:LATE":
                rows = [("185:THEME", 100)]
            else:
                rows = [(f"185:UNRELATED{code[-2:]}", 100)]
            for component, weight in rows:
                yield {"code": component, "name": component, "weight_pct": weight}

    class LLM:
        def chat_json(self, system, user, **kw):
            batch = [line.split("|")[0].strip() for line in user.splitlines()
                     if "|" in line and ":" in line.split("|")[0]]
            return [{"market_code": code,
                     "ai_relevance": 5 if code == "185:THEME" else 1,
                     "exposure_type": "direct" if code == "185:THEME" else "unclear",
                     "confidence": 1, "reason": "evidence",
                     "impact_channel": "revenue_demand" if code == "185:THEME" else "none",
                     "theme_specificity": "company_specific" if code == "185:THEME" else "none",
                     "materiality": "high" if code == "185:THEME" else "unknown",
                     "evidence_strength": "explicit" if code == "185:THEME" else "none"}
                    for code in batch]

    quotes = Quotes()
    wf = ThemeWorkflow(LLM(), quotes, {
        "etf_target": 5, "etf_holdings_unique_budget": 100,
        "etf_holding_relevance_batch": 100,
    })
    candidates = [_candidate(f"185:F{i:02d}", 1 - i / 100) for i in range(20)]
    candidates.append(_candidate("185:LATE", 0.79))
    wf.rerank_etfs_from_components(candidates, {}, {}, "bullish")

    assert "185:LATE" in quotes.requested
    assert candidates[0]["code"] == "185:LATE"
    assert candidates[0]["ranking_mode"] == "full_holdings"


def test_holdings_shortlist_retains_leaders_and_pool_diversity():
    candidates = [_candidate(f"185:F{i}", 1 - i / 20) for i in range(25)]
    pool = type("Match", (), {"spec": type("Spec", (), {"key": "special"})()})()
    candidates[19]["pool_matches"] = {"special": pool}
    shortlisted = build_holdings_shortlist(candidates, etf_target=5)
    codes = [candidate["code"] for candidate in shortlisted]
    assert len(codes) == 20
    assert codes[:15] == [f"185:F{i}" for i in range(15)]
    assert "185:F19" in codes


def test_inflation_cooling_rejects_generic_tech_factor_beta():
    verdicts = {
        "185:MSFT": {
            "ai_relevance": 5, "exposure_type": "factor_proxy", "confidence": 0.95,
            "impact_channel": "valuation_only", "theme_specificity": "broad_factor",
            "materiality": "low", "evidence_strength": "derived",
            "reason": "Lower discount rates support long-duration valuation",
        },
        "185:HOME": {
            "ai_relevance": 4.8, "exposure_type": "direct", "confidence": 0.9,
            "impact_channel": "financing_sensitive_demand",
            "theme_specificity": "company_specific", "materiality": "high",
            "evidence_strength": "explicit",
            "reason": "Lower mortgage rates increase homebuyer affordability",
        },
        "185:REIT": {
            "ai_relevance": 4.3, "exposure_type": "beneficiary", "confidence": 0.85,
            "impact_channel": "input_cost_margin",
            "theme_specificity": "industry_specific", "materiality": "medium",
            "evidence_strength": "derived",
            "reason": "Cooling shelter costs reduce property operating pressure",
        },
    }

    class InflationLLM:
        def chat_json(self, system, user, **kw):
            output = []
            for line in user.splitlines():
                if "|" not in line or ":" not in line.split("|")[0]:
                    continue
                code = line.split("|")[0].strip()
                verdict = verdicts[code]
                row = _fake_stock_score(line, verdict["ai_relevance"])
                row.update({key: value for key, value in verdict.items()
                            if key != "ai_relevance"})
                row["theme_relevance"] = verdict["ai_relevance"]
                output.append(row)
            return output

    rows = _rows("185:MSFT", "185:HOME", "185:REIT")
    workflow = ThemeWorkflow(InflationLLM(), FakeQuotes())
    chosen = workflow.screen_until_target(
        rows, {"theme_direction": "bullish"}, {}, target=3, kind="stocks")

    assert [candidate["code"] for candidate in chosen] == [
        "185:HOME", "185:REIT", "185:MSFT",
    ]
    assert [candidate["code"] for candidate in workflow._last_stock_evidence] == [
        "185:HOME", "185:REIT",
    ]
    assert all(
        "weak_theme_fallback" not in candidate
        for candidate in workflow._last_stock_evidence
    )


def test_stock_membership_is_invariant_to_market_cap_order():
    scores = {f"185:S{i}": 3.4 + i / 10 for i in range(8)}

    def select(codes):
        return [candidate["code"] for candidate in ThemeWorkflow(
            ScriptedLLM(scores), FakeQuotes(), {"relevance_batch": 3}
        ).screen_until_target(_rows(*codes), {}, {}, target=4, kind="stocks")]

    forward = list(scores)
    assert select(forward) == select(list(reversed(forward)))


def test_share_classes_consume_one_stock_output_slot():
    scores = {"185:GOOGL": 5.0, "185:GOOG": 4.9, "185:HOME": 4.8}
    rows = _rows("185:GOOGL", "185:GOOG", "185:HOME")
    rows[0]["name"] = "Alphabet A"
    rows[1]["name"] = "Alphabet C"
    rows[2]["name"] = "Homebuilder Inc"
    workflow = ThemeWorkflow(ScriptedLLM(scores), FakeQuotes())
    chosen = workflow.screen_until_target(
        rows, {}, {}, target=2, kind="stocks")
    assert [candidate["code"] for candidate in chosen] == [
        "185:GOOGL", "185:HOME",
    ]
    assert [candidate["code"] for candidate in workflow._last_stock_evidence] == [
        "185:GOOGL", "185:HOME",
    ]


def test_secondary_tech_terms_do_not_route_technology_etfs():
    matches = match_theme_pools(
        "inflation cooling",
        {
            "direct_beneficiaries": ["regional banks", "real estate"],
            "second_order": ["big tech"],
            "keywords": ["technology stocks"],
            "etf_exposure_terms": ["regional banks", "real estate"],
        },
        "",
    )
    keys = {match.spec.key for match in matches}
    assert {"regional_banks", "real_estate"} <= keys
    assert "technology" not in keys


def test_etf_holding_ratios_preserve_documented_percentage_point_units():
    class PercentQuotes:
        def iter_etf_holdings(self, code, *, page_size=1000):
            yield {"code": "185:A", "name": "Alpha Systems", "weight_pct": 60}
            yield {"code": "185:B", "name": "Beta Memory", "weight_pct": 40}

    class DirectLLM:
        def chat_json(self, system, user, **kw):
            codes = [line.split("|")[0].strip() for line in user.splitlines()
                     if "|" in line and ":" in line.split("|")[0]]
            return [{
                "market_code": code, "ai_relevance": 5,
                "exposure_type": "direct", "confidence": 1,
                "impact_channel": "revenue_demand",
                "theme_specificity": "company_specific", "materiality": "high",
                "evidence_strength": "explicit", "reason": "direct",
            } for code in codes]

    candidate = _candidate("185:FUND", 0.8)
    workflow = ThemeWorkflow(DirectLLM(), PercentQuotes(), {
        "etf_target": 1, "etf_holdings_unique_budget": 10,
        "etf_holding_relevance_batch": 10,
    })
    workflow.rerank_etfs_from_components(
        [candidate], {"theme_direction": "bullish"}, {}, "bullish")
    assert candidate["holdings_weight_coverage_pct"] == 100.0
    assert candidate["holding_theme_breadth"] == 2
    assert candidate["ranking_mode"] == "full_holdings"
    assert candidate["output_eligible"] is True


def test_sparse_percent_etf_portfolio_cannot_be_scaled_into_complete_coverage():
    class SparseQuotes:
        def iter_etf_holdings(self, code, *, page_size=1000):
            yield {"code": "185:A", "name": "Company A", "weight_pct": "0.6%"}

    class DirectLLM:
        def chat_json(self, system, user, **kw):
            return [{
                "market_code": "185:A", "ai_relevance": 5,
                "exposure_type": "direct", "confidence": 1,
                "impact_channel": "revenue_demand",
                "theme_specificity": "company_specific", "materiality": "high",
                "evidence_strength": "explicit", "reason": "direct",
            }]

    candidate = _candidate("185:FUND", 0.8)
    ThemeWorkflow(DirectLLM(), SparseQuotes(), {
        "etf_target": 1, "etf_holdings_unique_budget": 10,
    }).rerank_etfs_from_components([candidate], {}, {}, "bullish")
    assert candidate["holdings_weight_coverage_pct"] == 0.6
    assert candidate["ranking_mode"] == "holdings_incomplete"
    assert candidate["output_eligible"] is False


def test_missing_structured_evidence_and_nonfinite_scores_fail_closed():
    class InvalidLLM:
        def chat_json(self, system, user, **kw):
            lines = [line for line in user.splitlines()
                     if "|" in line and ":" in line.split("|")[0]]
            first_parts = [part.strip() for part in lines[0].split("|")]
            nonfinite = _fake_stock_score(lines[1], 5)
            nonfinite["theme_relevance"] = float("nan")
            nonfinite["confidence"] = float("inf")
            return [{
                "candidate_id": first_parts[1],
                "market_code": first_parts[0],
                "theme_relevance": 5,
                "exposure_type": "direct",
                "confidence": 1,
                "reason": "operating link",
            }, nonfinite]

    rows = _rows("185:MISSING", "185:NONFINITE")
    wf = ThemeWorkflow(InvalidLLM(), FakeQuotes())
    scores = wf.score_relevance({}, rows)
    assert scores["185:MISSING"]["relevance_status"] == "invalid_schema"
    assert scores["185:NONFINITE"]["ai_relevance"] == 1.0
    assert scores["185:NONFINITE"]["confidence"] == 0.3
    chosen = wf.screen_until_target(rows, {}, {}, 2, "stocks")
    assert [candidate["code"] for candidate in chosen] == [
        "185:MISSING", "185:NONFINITE",
    ]
    assert wf._last_stock_evidence == []


def test_valuation_and_market_beta_channels_are_never_stock_eligible():
    class FactorLLM:
        def chat_json(self, system, user, **kw):
            rows = []
            lines = [line for line in user.splitlines()
                     if "|" in line and ":" in line.split("|")[0]]
            for index, line in enumerate(lines):
                row = _fake_stock_score(line, 5)
                row.update({
                    "impact_channel": (
                        "valuation_only" if index == 0 else "market_beta"),
                    "reason": "Lower discount rates lift technology multiples",
                })
                rows.append(row)
            return rows

    workflow = ThemeWorkflow(FactorLLM(), FakeQuotes())
    chosen = workflow.screen_until_target(
        _rows("185:MSFT", "185:GOOG"), {}, {}, 2, "stocks")
    assert [candidate["code"] for candidate in chosen] == [
        "185:MSFT", "185:GOOG",
    ]
    assert workflow._last_stock_evidence == []


def test_relevance_identity_must_match_and_never_recovers_by_position():
    class WrongCodeLLM:
        def chat_json(self, system, user, **kw):
            return [{
                "market_code": code, "ai_relevance": 5,
                "exposure_type": "direct", "confidence": 1,
                "impact_channel": "revenue_demand",
                "theme_specificity": "company_specific", "materiality": "high",
                "evidence_strength": "explicit", "reason": "direct",
            } for code in ("185:WRONG1", "185:WRONG2")]

    scores = ThemeWorkflow(WrongCodeLLM(), FakeQuotes()).score_relevance(
        {}, _rows("185:A", "185:B"))
    assert all(score["relevance_status"] == "missing" for score in scores.values())


def test_relevance_rejects_ticker_only_identity_even_when_unique():
    class TickerOnlyLLM:
        def chat_json(self, system, user, **kw):
            rows = []
            for line in user.splitlines():
                parts = [part.strip() for part in line.split("|")]
                if len(parts) < 3 or ":" not in parts[0]:
                    continue
                rows.append({
                "candidate_id": parts[1],
                "market_code": parts[0].partition(":")[2],
                "theme_relevance": 5, "article_support": 1,
                "exposure_type": "direct", "confidence": 1,
                "impact_channel": "revenue_demand",
                "theme_specificity": "company_specific", "materiality": "high",
                "evidence_strength": "explicit", "reason": "direct",
                "article_reason": "explicit mention",
            })
            return rows

    recovered = ThemeWorkflow(
        TickerOnlyLLM(), FakeQuotes(), {"relevance_batch": 10}
    ).score_relevance({}, _rows("185:CRWV", "185:NBIS"))
    assert all(score["relevance_status"] != "scored" for score in recovered.values())

    for batch_size in (1, 10):
        ambiguous = ThemeWorkflow(
            TickerOnlyLLM(), FakeQuotes(), {"relevance_batch": batch_size}
        ).score_relevance({}, _rows("169:NVR", "185:NVR"))
        assert all(
            score["relevance_status"] != "scored"
            for score in ambiguous.values()
        )


def test_public_stock_exposure_is_rank_relative_without_reordering():
    lower_quality = {
        "code": "185:A", "ai_relevance": 5, "exposure_type": "direct",
        "confidence": 1, "theme_specificity": "industry_specific",
        "materiality": "medium", "evidence_strength": "derived",
        "impact_channel": "revenue_demand", "semantic_score": 0.65,
        "market_strength": 0,
    }
    higher_quality = {
        "code": "185:B", "ai_relevance": 4.9, "exposure_type": "direct",
        "confidence": 0.9, "theme_specificity": "company_specific",
        "materiality": "high", "evidence_strength": "explicit",
        "impact_channel": "revenue_demand", "semantic_score": 0.88,
        "market_strength": 0,
    }
    rows = [lower_quality, higher_quality]
    ThemeWorkflow(FakeLLM(), FakeQuotes())._assign_theme_exposure(rows)
    assert [row["code"] for row in rows] == ["185:A", "185:B"]
    assert [row["theme_exposure"] for row in rows] == [5.0, 3.0]


def test_stock_candidate_lanes_reach_theme_industries_below_broad_mega_caps():
    class LaneQuotes(FakeQuotes):
        def iter_ranked(self, selector, indicators, *, sort_pos=0, order="desc",
                        page_size=1000, strict=False):
            for index in range(200):
                industry = "Regional Banks" if index >= 150 else "Technology Hardware"
                yield {
                    "code": f"185:S{index}", "rank": index + 1,
                    "values": {
                        "mktcap": 200 - index, "name": f"Company {index}",
                        "company_introduction": "",
                        "sector": "Financials" if index >= 150 else "Technology",
                        "industry": industry,
                    },
                }

    wf = ThemeWorkflow(FakeLLM(), LaneQuotes(), {
        "stock_universe": 200, "stock_candidate_budget": 50, "stock_broad_lane": 40,
    })
    candidates = wf.stock_candidates({"etf_exposure_terms": ["regional banks"]})
    assert len(candidates) == 50
    assert any(int(row["code"].removeprefix("185:S")) >= 150 for row in candidates)
    assert sum(row["candidate_lane"] == "broad_liquidity" for row in candidates) == 40


def test_stock_candidate_lane_recognizes_chinese_homebuilder_description():
    class BilingualQuotes(FakeQuotes):
        def iter_ranked(self, selector, indicators, *, sort_pos=0, order="desc",
                        page_size=1000, strict=False):
            for index in range(100):
                homebuilder = index == 99
                yield {
                    "code": "169:DHI" if homebuilder else f"185:T{index}",
                    "rank": index + 1,
                    "values": {
                        "mktcap": 100 - index,
                        "name": "D.R. Horton" if homebuilder else f"Tech {index}",
                        "company_introduction": (
                            "公司是美国最大的住宅建筑公司，建造和销售房屋。"
                            if homebuilder else "软件公司"
                        ),
                        "sector": "Industrials" if homebuilder else "Technology",
                        "industry": "Building & Decoration" if homebuilder else "Software",
                    },
                }

    candidates = ThemeWorkflow(FakeLLM(), BilingualQuotes(), {
        "stock_universe": 100, "stock_candidate_budget": 41, "stock_broad_lane": 40,
    }).stock_candidates({"direct_beneficiaries": ["Homebuilders"]})
    homebuilder = next(row for row in candidates if row["code"] == "169:DHI")
    assert homebuilder["candidate_lane"] == "theme_industry"


def test_stock_candidate_lane_recognizes_exact_nonhousing_chinese_concept():
    semiconductor = {
        "sector": "Industrials", "industry": "",
        "company_introduction": "公司生产半导体设备及关键零部件",
    }
    assert _taxonomy_match_score(semiconductor, ["半导体设备"]) > 0
    assert _taxonomy_match_score(semiconductor, ["物流服务"]) == 0


def test_direct_stock_lane_keeps_multiple_pure_plays_from_one_industry():
    class DenseLaneQuotes(FakeQuotes):
        def iter_ranked(self, selector, indicators, *, sort_pos=0, order="desc",
                        page_size=1000, strict=False):
            for index in range(200):
                homebuilder = 150 <= index < 160
                yield {
                    "code": f"185:H{index}" if homebuilder else f"185:S{index}",
                    "rank": index + 1,
                    "values": {
                        "mktcap": 200 - index, "name": f"Company {index}",
                        "company_introduction": (
                            "Builds and sells single-family homes"
                            if homebuilder else f"Provides generic industrial service {index}"
                        ),
                        "sector": "Consumer Discretionary" if homebuilder else "Industrials",
                        "industry": "Homebuilding" if homebuilder else f"Industry {index % 80}",
                    },
                }

    candidates = ThemeWorkflow(FakeLLM(), DenseLaneQuotes(), {
        "stock_universe": 200, "stock_candidate_budget": 50, "stock_broad_lane": 40,
    }).stock_candidates({
        "primary_shock": "broad disinflation affects industrial services",
        "direct_beneficiaries": ["homebuilders"],
        "etf_exposure_terms": ["home construction"],
    })
    direct_homebuilders = [
        row for row in candidates
        if row.get("candidate_lane_priority") == "direct_pathway"
        and row.get("industry") == "Homebuilding"
    ]
    assert len(direct_homebuilders) >= 4


def test_equal_thematic_matches_are_sampled_independently_of_market_cap_order():
    class EqualThemeQuotes(FakeQuotes):
        def __init__(self, reverse=False): self.reverse = reverse
        def iter_ranked(self, selector, indicators, *, sort_pos=0, order="desc",
                        page_size=1000, strict=False):
            broad = list(range(40))
            thematic = list(range(20))
            if self.reverse:
                thematic.reverse()
            for rank, (lane, index) in enumerate(
                    [("B", item) for item in broad]
                    + [("H", item) for item in thematic], 1):
                homebuilder = lane == "H"
                yield {
                    "code": f"185:{lane}{index}", "rank": rank,
                    "values": {
                        "mktcap": 1000 - rank, "name": f"{lane}{index}",
                        "company_introduction": (
                            "Builds and sells single-family homes"
                            if homebuilder else "Technology hardware"
                        ),
                        "sector": "Consumer Discretionary" if homebuilder else "Technology",
                        "industry": "Homebuilding" if homebuilder else "Hardware",
                    },
                }

    def themed_codes(reverse):
        rows = ThemeWorkflow(FakeLLM(), EqualThemeQuotes(reverse), {
            "stock_universe": 60, "stock_candidate_budget": 50,
            "stock_broad_lane": 40,
        }).stock_candidates({"direct_beneficiaries": ["homebuilders"]})
        return {row["code"] for row in rows if row["code"].startswith("185:H")}

    assert themed_codes(False) == themed_codes(True)


def test_unvalidated_etf_terms_do_not_consume_reserved_stock_pathway_slots():
    class MixedTermsQuotes(FakeQuotes):
        def iter_ranked(self, selector, indicators, *, sort_pos=0, order="desc",
                        page_size=1000, strict=False):
            labels = ["homebuilding"] * 10 + [
                "artificial intelligence", "semiconductors", "cybersecurity",
                "robotics", "fintech", "ecommerce", "regional banks",
            ]
            for index in range(40 + len(labels)):
                label = labels[index - 40] if index >= 40 else "technology hardware"
                yield {
                    "code": f"185:S{index}", "rank": index + 1,
                    "values": {
                        "mktcap": 1000 - index, "name": f"Company {index}",
                        "company_introduction": label,
                        "sector": "Consumer Discretionary" if label == "homebuilding" else "Technology",
                        "industry": "Homebuilding" if label == "homebuilding" else label,
                    },
                }

    rows = ThemeWorkflow(FakeLLM(), MixedTermsQuotes(), {
        "stock_universe": 57, "stock_candidate_budget": 50, "stock_broad_lane": 40,
    }).stock_candidates({
        "direct_beneficiaries": ["homebuilders"],
        "etf_exposure_terms": [
            "artificial intelligence", "semiconductors", "cybersecurity",
            "robotics", "fintech", "ecommerce", "regional banks",
        ],
    })
    reserved_terms = {
        row.get("candidate_lane_term") for row in rows
        if row.get("candidate_lane_priority") == "direct_pathway"
    }
    assert reserved_terms == {"homebuilders"}
    assert sum(row.get("industry") == "Homebuilding" for row in rows) >= 4


def test_stock_candidate_scoring_context_interleaves_broad_and_theme_lanes():
    class LaneQuotes(FakeQuotes):
        def iter_ranked(self, selector, indicators, *, sort_pos=0, order="desc",
                        page_size=1000, strict=False):
            for index in range(12):
                thematic = index >= 6
                yield {
                    "code": f"185:S{index}", "rank": index + 1,
                    "values": {
                        "mktcap": 12 - index, "name": f"Company {index}",
                        "company_introduction": "",
                        "sector": "Financials" if thematic else "Technology",
                        "industry": "Regional Banks" if thematic else "Software",
                    },
                }

    rows = ThemeWorkflow(FakeLLM(), LaneQuotes(), {
        "stock_universe": 12, "stock_candidate_budget": 9, "stock_broad_lane": 3,
    }).stock_candidates({"etf_exposure_terms": ["regional banks"]})
    assert [row["candidate_lane"] for row in rows[:6]] == [
        "broad_liquidity", "theme_industry", "theme_industry",
        "broad_liquidity", "theme_industry", "theme_industry",
    ]


def test_article_title_and_beneficiary_factor_proxies_cannot_route_tech_pool():
    matches = match_theme_pools(
        "inflation cooling",
        {"direct_beneficiaries": ["big tech"],
         "picks_and_shovels": ["regional banks"],
         "primary_transmission_channels": ["technology multiples"],
         "etf_exposure_terms": ["regional banks"]},
        "Big Tech rallies as inflation cools",
    )
    keys = {match.spec.key for match in matches}
    assert "regional_banks" in keys
    assert "technology" not in keys


def test_inflation_cooling_primary_mandates_route_housing_not_technology():
    matches = match_theme_pools(
        "inflation cooling",
        {
            "direct_beneficiaries": [
                "U.S. homebuilders", "residential real estate",
                "mortgage finance and mortgage REITs", "home improvement retail",
            ],
            "picks_and_shovels": ["building products", "consumer discretionary retail"],
            "etf_exposure_terms": [
                "U.S. homebuilders", "residential real estate", "building products",
                "mortgage finance and mortgage REITs", "home improvement retail",
                "consumer discretionary retail",
            ],
        },
        "Big Tech rallies as inflation cools",
    )
    keys = {match.spec.key for match in matches}
    assert {"consumer_discretionary", "real_estate", "retail"} <= keys
    assert "technology" not in keys


def test_uncorroborated_model_etf_term_cannot_route_technology_pool():
    brief = {
        "direct_beneficiaries": ["homebuilders", "mortgage lenders"],
        "picks_and_shovels": ["building products"],
        "etf_exposure_terms": ["technology stocks", "U.S. homebuilders"],
        "false_positives": ["big tech and generic duration stocks"],
    }
    matches = match_theme_pools(
        "inflation cooling", brief,
        "Big Tech rallies as inflation cools",
    )
    keys = {match.spec.key for match in matches}
    assert "consumer_discretionary" in keys
    assert "technology" not in keys
    assert _mandate_match(
        {"name": "Technology Select Sector SPDR ETF"}, brief
    ) == (False, [])

    # Cross-field agreement from the same model is not independent evidence:
    # generic technology remains invalid unless the user's theme itself is tech.
    correlated_error = {
        "direct_beneficiaries": ["big tech"],
        "etf_exposure_terms": ["technology stocks"],
        "false_positives": ["broad market beta"],
    }
    assert "technology" not in {
        match.spec.key for match in match_theme_pools(
            "inflation cooling", correlated_error, "")
    }
    for generic_tech_term in (
        "technology", "technology sector", "information technology sector",
        "artificial intelligence", "software", "digital economy",
    ):
        wording_bypass = {
            "direct_beneficiaries": [generic_tech_term],
            "etf_exposure_terms": [generic_tech_term],
            "false_positives": ["broad market beta and generic duration stocks"],
        }
        assert "technology" not in {
            match.spec.key for match in match_theme_pools(
                "inflation cooling", wording_bypass, "")
        }
        assert _mandate_match(
            {"name": "Technology Select Sector SPDR ETF"}, wording_bypass
        ) == (False, [])
    for term, fund_name in (
        ("artificial intelligence", "Global X Artificial Intelligence ETF"),
        ("software", "iShares Expanded Tech-Software Sector ETF"),
        ("digital economy", "Digital Economy ETF"),
    ):
        model_only = {
            "direct_beneficiaries": [term], "etf_exposure_terms": [term],
        }
        assert _mandate_match(
            {"name": fund_name}, model_only, "inflation cooling"
        ) == (False, [])

    # The same terms remain usable when they are explicitly part of the user's
    # theme rather than introduced only by the event model.
    explicit_ai = {
        "direct_beneficiaries": ["artificial intelligence companies"],
        "etf_exposure_terms": ["artificial intelligence"],
    }
    assert "artificial_intelligence" in {
        match.spec.key for match in match_theme_pools(
            "artificial intelligence", explicit_ai, "")
    }


def test_event_sector_etf_terms_remain_valid_outside_macro_factor_themes():
    cases = (
        ("CHIPS Act funding approved", "chipmakers", "semiconductors",
         "iShares Semiconductor ETF", "semiconductors"),
        ("Major ransomware attack", "cybersecurity vendors", "cybersecurity",
         "First Trust Nasdaq Cybersecurity ETF", "cybersecurity"),
        ("Online sales tax repealed", "online retailers", "ecommerce",
         "Online Retail ETF", "ecommerce"),
    )
    for theme, support, term, fund, pool_key in cases:
        brief = {"direct_beneficiaries": [support], "etf_exposure_terms": [term]}
        assert _validated_etf_terms(brief, theme) == [term]
        assert pool_key in {
            match.spec.key for match in match_theme_pools(theme, brief)
        }
        assert _mandate_match({"name": fund}, brief, theme)[0] is True


def test_etf_term_corroboration_uses_precise_concepts_not_broad_pool_keys():
    mismatched = {
        "direct_beneficiaries": ["homebuilders"],
        "etf_exposure_terms": ["auto finance"],
    }
    assert _validated_etf_terms(mismatched, "inflation cooling") == []

    valid = {
        "direct_beneficiaries": ["homebuilders"],
        "etf_exposure_terms": ["home construction"],
        "false_positives": ["auto finance"],
    }
    assert _validated_etf_terms(valid, "inflation cooling") == ["home construction"]
    assert "consumer_discretionary" in {
        match.spec.key for match in match_theme_pools("inflation cooling", valid)
    }


def test_american_consumer_aliases_route_to_staples_and_discretionary_pools():
    expected = {"consumer_staples", "consumer_discretionary"}
    for theme in (
        "American Consumer", "U.S. consumer", "US consumers", "美国消费者",
    ):
        matches = match_theme_pools(theme, {}, max_pools=4)
        keys = {match.spec.key for match in matches}
        assert expected <= keys
        prompt_ids = {match.spec.prompt_id for match in matches}
        assert "6908af018738843bb3ba864b" in prompt_ids
        assert "6908aebd8738843bb3ba864a" in prompt_ids


def test_chinese_etf_terms_route_and_verify_chinese_housing_mandate():
    brief = {
        "direct_beneficiaries": ["住宅建筑商"],
        "picks_and_shovels": ["建筑材料"],
        "etf_exposure_terms": ["住宅建筑", "建筑材料"],
    }
    assert _validated_etf_terms(brief, "通胀降温") == ["住宅建筑", "建筑材料"]
    assert "consumer_discretionary" in {
        match.spec.key for match in match_theme_pools("通胀降温", brief)
    }
    assert _mandate_match({"name": "美国住宅建筑ETF"}, brief, "通胀降温")[0] is True


def test_chinese_macro_themes_reject_model_authored_tech_factor_etfs():
    cases = (
        ("通胀降温", "科技板块", "中国科技ETF"),
        ("利率下降", "人工智能", "人工智能ETF"),
        ("美联储降息", "软件", "软件行业ETF"),
    )
    for theme, term, fund in cases:
        brief = {
            "direct_beneficiaries": [term], "etf_exposure_terms": [term],
        }
        assert _validated_etf_terms(brief, theme) == []
        assert match_theme_pools(theme, brief) == []
        assert _mandate_match({"name": fund}, brief, theme) == (False, [])


def test_producer_themes_do_not_route_nested_spot_asset_pools():
    assert {match.spec.key for match in match_theme_pools("gold miners", {})} == {
        "gold_miners"
    }
    assert {match.spec.key for match in match_theme_pools("silver miners", {})} == {
        "silver_miners"
    }
    assert {match.spec.key for match in match_theme_pools("bitcoin miners", {})} == {
        "blockchain"
    }


def test_explicit_housing_fund_mandates_verify_but_generic_tech_does_not():
    brief = {
        "direct_beneficiaries": [
            "U.S. homebuilders", "residential real estate", "mortgage finance",
        ],
        "etf_exposure_terms": [
            "U.S. homebuilders", "residential real estate", "mortgage finance",
        ],
    }
    for name in (
        "iShares U.S. Home Construction ETF",
        "State Street SPDR S&P Homebuilders ETF",
        "Hoya Capital Housing ETF",
        "VanEck Mortgage REIT Income ETF",
    ):
        verified, terms = _mandate_match({"name": name}, brief)
        assert verified and terms
    verified, terms = _mandate_match(
        {"name": "Global X Artificial Intelligence ETF"}, brief)
    assert verified is False and terms == []


def test_below_threshold_holdings_cannot_aggregate_into_an_etf_pick():
    candidate = _candidate("185:FUND", 0.9)
    holdings = {"185:FUND": [
        {"code": f"185:H{i}", "name": f"Holding {i}", "weight_pct": 10}
        for i in range(10)
    ]}
    scores = {f"185:H{i}": {
        "ai_relevance": 3.2, "exposure_type": "beneficiary", "confidence": 1,
        "impact_channel": "revenue_demand", "theme_specificity": "industry_specific",
        "materiality": "medium", "evidence_strength": "derived",
        "reason": "weak", "relevance_status": "scored",
    } for i in range(10)}
    rerank_with_component_holdings([candidate], holdings, scores)
    assert candidate["holding_theme_mass"] == 0
    assert candidate["output_eligible"] is False


def test_basket_mandate_mismatch_does_not_block_two_issuer_holdings_evidence():
    candidate = _candidate("185:TECH", 0.9)
    candidate["mandate_verified"] = False
    holdings = {"185:TECH": [
        {"code": "185:A", "name": "Alpha Systems", "weight_pct": 60},
        {"code": "185:B", "name": "Beta Memory", "weight_pct": 40},
    ]}
    scores = {code: {
        "ai_relevance": 5, "exposure_type": "direct", "confidence": 1,
        "impact_channel": "revenue_demand", "theme_specificity": "company_specific",
        "materiality": "high", "evidence_strength": "explicit",
        "reason": "direct", "relevance_status": "scored",
    } for code in ("185:A", "185:B")}
    rerank_with_component_holdings([candidate], holdings, scores)
    assert candidate["full_holdings_score"] > 0.25
    assert candidate["mandate_verified"] is False
    assert candidate["mandate_corroboration"] == 0.0
    assert candidate["holding_theme_breadth"] == 2
    assert candidate["output_eligible"] is True


def test_pool_provenance_is_corroboration_not_an_equity_mandate_gate():
    candidate = _candidate("185:AIETF", 0.9)
    candidate["name"] = "Generic Artificial Intelligence ETF"
    candidate["mandate_verified"] = False
    candidate["pool_matches"] = {
        "consumer_discretionary": type(
            "Match", (), {"spec": type("Spec", (), {"key": "consumer_discretionary"})()}
        )(),
    }
    holdings = {"185:AIETF": [
        {"code": "185:DHI", "name": "D.R. Horton", "weight_pct": 60},
        {"code": "185:PHM", "name": "PulteGroup", "weight_pct": 40},
    ]}
    scores = {code: {
        "ai_relevance": 5, "exposure_type": "direct", "confidence": 1,
        "impact_channel": "financing_sensitive_demand",
        "theme_specificity": "company_specific", "materiality": "high",
        "evidence_strength": "explicit", "reason": "direct",
        "relevance_status": "scored",
    } for code in ("185:DHI", "185:PHM")}
    rerank_with_component_holdings([candidate], holdings, scores)
    assert candidate["full_holdings_score"] >= 0.25
    assert candidate["mandate_verified"] is False
    assert candidate["holding_theme_breadth"] == 2
    assert candidate["output_eligible"] is True


def test_direct_asset_pool_provenance_also_requires_intended_mandate():
    candidate = _candidate("185:GLD", 0.8)
    candidate["pool_matches"] = {"gold": object()}
    candidate["mandate_verified"] = False
    candidate["asset_class"] = "commodity"
    rerank_with_component_holdings([candidate], {}, {})
    assert candidate["ranking_mode"] == "direct_asset"
    assert candidate["output_eligible"] is False


def test_single_issuer_basket_fails_even_with_high_holdings_score():
    candidate = _candidate("185:LONG", 0.9)
    holdings = {"185:LONG": [
        {"code": "185:A", "name": "Vulnerable Company", "weight_pct": 100},
    ]}
    scores = {"185:A": {
        "ai_relevance": 4.0, "exposure_type": "beneficiary", "confidence": 0.9,
        "impact_channel": "revenue_demand", "theme_specificity": "industry_specific",
        "materiality": "medium", "evidence_strength": "derived",
        "reason": "downside", "relevance_status": "scored",
    }}
    rerank_with_component_holdings(
        [candidate], holdings, scores, theme_direction="bearish", min_output_score=0.25)
    assert candidate["full_holdings_score"] >= 0.25
    assert candidate["holding_theme_breadth"] == 1
    assert candidate["output_eligible"] is False


def test_failed_etf_leaders_do_not_block_later_complete_candidate():
    class Quotes:
        def __init__(self): self.requested = []
        def iter_etf_holdings(self, code, *, page_size=1000):
            self.requested.append(code)
            if code == "185:LATE":
                yield {"code": "185:THEMEA", "name": "Alpha Theme", "weight_pct": 60}
                yield {"code": "185:THEMEB", "name": "Beta Theme", "weight_pct": 40}

    class LLM:
        def chat_json(self, system, user, **kw):
            codes = [line.split("|")[0].strip() for line in user.splitlines()
                     if "|" in line and ":" in line.split("|")[0]]
            return [{
                "market_code": code, "ai_relevance": 5,
                "exposure_type": "direct", "confidence": 1,
                "impact_channel": "revenue_demand",
                "theme_specificity": "company_specific", "materiality": "high",
                "evidence_strength": "explicit", "reason": "direct",
            } for code in codes]

    quotes = Quotes()
    candidates = [_candidate(f"185:F{i:02d}", 1 - i / 100) for i in range(20)]
    candidates.append(_candidate("185:LATE", 0.79))
    ThemeWorkflow(LLM(), quotes, {
        "etf_target": 1, "etf_holdings_unique_budget": 10,
        "etf_holdings_portfolio_budget": 25,
    }).rerank_etfs_from_components(candidates, {}, {}, "bullish")
    assert "185:LATE" in quotes.requested
    assert candidates[0]["code"] == "185:LATE"
    assert candidates[0]["output_eligible"] is True


def test_etf_assessment_spends_budget_before_ranking_passing_leaders():
    class Quotes:
        def __init__(self): self.requested = []
        def iter_etf_holdings(self, code, *, page_size=1000):
            self.requested.append(code)
            suffix = code.partition(":")[2]
            yield {"code": f"185:H{suffix}", "name": f"Holding {suffix}",
                   "weight_pct": 100}

    class LLM:
        def chat_json(self, system, user, **kw):
            codes = [line.split("|")[0].strip() for line in user.splitlines()
                     if "|" in line and ":" in line.split("|")[0]]
            return [{
                "market_code": code,
                "ai_relevance": 5 if code == "185:HLATE" else 4,
                "exposure_type": "direct", "confidence": 1,
                "impact_channel": "revenue_demand",
                "theme_specificity": "company_specific", "materiality": "high",
                "evidence_strength": "explicit", "reason": "direct",
            } for code in codes]

    quotes = Quotes()
    candidates = [_candidate(f"185:F{i:02d}", 1 - i / 100) for i in range(20)]
    candidates.append(_candidate("185:LATE", 0.79))
    ThemeWorkflow(LLM(), quotes, {
        "etf_target": 5, "etf_holdings_unique_budget": 50,
        "etf_holdings_portfolio_budget": 21,
    }).rerank_etfs_from_components(candidates, {}, {}, "bullish")
    assert len(quotes.requested) == 21
    assert candidates[0]["code"] == "185:LATE"


def test_etf_portfolio_budget_does_not_skip_baskets_on_mandate_mismatch():
    class Quotes:
        def __init__(self): self.requested = []
        def iter_etf_holdings(self, code, *, page_size=1000):
            self.requested.append(code)
            yield {"code": "185:THEMEA", "name": "Alpha Theme", "weight_pct": 60}
            yield {"code": "185:THEMEB", "name": "Beta Theme", "weight_pct": 40}

    class LLM:
        def chat_json(self, system, user, **kw):
            codes = [line.split("|")[0].strip() for line in user.splitlines()
                     if "|" in line and ":" in line.split("|")[0]]
            return [{
                "market_code": code, "ai_relevance": 5,
                "exposure_type": "direct", "confidence": 1,
                "impact_channel": "revenue_demand",
                "theme_specificity": "company_specific", "materiality": "high",
                "evidence_strength": "explicit", "reason": "direct",
            } for code in codes]

    candidates = [_candidate(f"185:BAD{i:02d}", 1 - i / 100) for i in range(40)]
    for candidate in candidates:
        candidate["mandate_verified"] = False
    quotes = Quotes()
    ThemeWorkflow(LLM(), quotes, {
        "etf_target": 1, "etf_holdings_portfolio_budget": 1,
        "etf_holdings_unique_budget": 10,
    }).rerank_etfs_from_components(candidates, {}, {}, "bullish")
    assert quotes.requested == ["185:BAD00"]
    assert candidates[0]["code"] == "185:BAD00"
    assert candidates[0]["mandate_verified"] is False
    assert candidates[0]["output_eligible"] is True


def test_empty_etf_output_logs_each_final_basket_rejection_reason():
    class RejectionQuotes:
        def iter_etf_holdings(self, code, *, page_size=1000):
            if code == "185:INCOMPLETE":
                yield {"code": "185:I", "name": "Incomplete Holding", "weight_pct": 50}
            elif code == "185:WEAK":
                yield {"code": "185:W1", "name": "Weak One", "weight_pct": 50}
                yield {"code": "185:W2", "name": "Weak Two", "weight_pct": 50}
            elif code == "185:SINGLE":
                yield {"code": "185:S1", "name": "Single Issuer", "weight_pct": 100}

        def security_profiles(self, codes):
            return {}

    class RejectionLLM:
        def chat_json(self, system, user, **kw):
            codes = [line.split("|")[0].strip() for line in user.splitlines()
                     if "|" in line and ":" in line.split("|")[0]]
            rows = []
            for code in codes:
                weak = code in {"185:W1", "185:W2"}
                rows.append({
                    "market_code": code,
                    "ai_relevance": 3.3 if weak else 5,
                    "exposure_type": "beneficiary" if weak else "direct",
                    "confidence": 0.55 if weak else 1,
                    "impact_channel": "revenue_demand",
                    "theme_specificity": (
                        "industry_specific" if weak else "company_specific"),
                    "materiality": "medium" if weak else "high",
                    "evidence_strength": "derived" if weak else "explicit",
                    "reason": "causal operating exposure",
                })
            return rows

    candidates = [
        _candidate("185:UNAVAILABLE", 0.9),
        _candidate("185:INCOMPLETE", 0.8),
        _candidate("185:WEAK", 0.7),
        _candidate("185:SINGLE", 0.6),
    ]
    logs = []
    original_log = workflow_module._log
    workflow_module._log = logs.append
    try:
        ThemeWorkflow(RejectionLLM(), RejectionQuotes(), {
            "etf_holdings_portfolio_budget": 4,
            "etf_holdings_unique_budget": 10,
        }).rerank_etfs_from_components(candidates, {}, {}, "bullish")
    finally:
        workflow_module._log = original_log

    assert select_output_etfs(candidates) == []
    rejection_log = next(
        line for line in logs
        if line.startswith("ETF final basket eligibility rejections:"))
    assert "holdings_unavailable=1 (185:UNAVAILABLE)" in rejection_log
    assert "holdings_incomplete=1 (185:INCOMPLETE)" in rejection_log
    assert "theme_score_below_minimum=1 (185:WEAK)" in rejection_log
    assert "relevant_issuer_breadth_below_2=1 (185:SINGLE)" in rejection_log


def test_preferred_share_classes_consume_one_issuer_slot_and_best_class_wins():
    scores = {"185:APO.PA": 4.5, "185:APO": 5.0, "185:HOME": 4.7}
    rows = _rows("185:APO.PA", "185:APO", "185:HOME")
    rows[0]["name"] = "Apollo Global Preferred Stock A"
    rows[1]["name"] = "Apollo Global"
    rows[2]["name"] = "Homebuilder"
    workflow = ThemeWorkflow(ScriptedLLM(scores), FakeQuotes())
    chosen = workflow.screen_until_target(rows, {}, {}, 2, "stocks")
    assert [candidate["code"] for candidate in chosen] == [
        "185:APO", "185:HOME",
    ]
    assert [candidate["code"] for candidate in workflow._last_stock_evidence] == [
        "185:APO", "185:HOME",
    ]


def test_etf_breadth_deduplicates_issuer_share_classes():
    candidate = _candidate("185:FUND", 0.9)
    holdings = {"185:FUND": [
        {"code": "185:GOOGL", "name": "Alphabet Class A", "weight_pct": 50},
        {"code": "185:GOOG", "name": "Alphabet Class C", "weight_pct": 50},
    ]}
    scores = {code: {
        "ai_relevance": 5, "exposure_type": "direct", "confidence": 1,
        "impact_channel": "revenue_demand", "theme_specificity": "company_specific",
        "materiality": "high", "evidence_strength": "explicit",
        "reason": "direct", "relevance_status": "scored",
    } for code in ("185:GOOGL", "185:GOOG")}
    rerank_with_component_holdings([candidate], holdings, scores)
    assert candidate["holding_theme_breadth"] == 1
    assert candidate["output_eligible"] is False


def test_run_keeps_exact_wrapper_when_all_basket_holdings_are_unavailable():
    class EmptyHoldingsQuotes(FakeQuotes):
        def iter_etf_holdings(self, code, *, page_size=1000):
            return iter(())

    result = ThemeWorkflow(FakeLLM(), EmptyHoldingsQuotes(), {
        "stock_universe": 20, "etf_universe": 100,
    }).run({"theme": "AI memory", "date": "2026-07-09",
           "url": "https://example.com/x"})
    codes = [item["market_code"] for item in result["ThemeEtfs"]]
    assert len(codes) == 5
    assert len(set(codes)) == 5
    assert codes[0] == "185:NVDL"


def _neocloud_profile():
    return {
        "exact_theme": "Neocloud",
        "theme_cn": "新云",
        "canonical_name": "Neocloud",
        "canonical_definition": (
            "Specialized cloud operators selling GPU compute capacity for AI workloads."
        ),
        "aliases": ["GPU cloud", "AI compute cloud"],
        "direct_business_models": ["GPU compute capacity rental"],
        "pure_play_descriptors": ["specialized GPU cloud operator"],
        "enablers": ["GPU suppliers", "data-center power infrastructure"],
        "exclusions": ["generic software", "diversified hyperscalers"],
        "core_entities": [
            {"name": "CoreWeave", "ticker": "CRWV", "market_code": None,
             "role": "pure_play_operator", "confidence": 0.99},
            {"name": "Nebius Group", "ticker": "NBIS", "market_code": None,
             "role": "pure_play_operator", "confidence": 0.98},
        ],
    }


def _neocloud_resolver():
    return MarketCodeResolver(rows=[
        {"key": "185:CRWV", "market": "185", "code": "CRWV",
         "security_type": "ES", "security_name": "CoreWeave"},
        {"key": "185:NBIS", "market": "185", "code": "NBIS",
         "security_type": "ES", "security_name": "Nebius Group"},
        {"key": "185:NVDA", "market": "185", "code": "NVDA",
         "security_type": "ES", "security_name": "NVIDIA"},
        {"key": "185:MU", "market": "185", "code": "MU",
         "security_type": "ES", "security_name": "Micron Technology"},
        {"key": "185:QQQ", "market": "185", "code": "QQQ",
         "security_type": "CE", "security_name": "Invesco QQQ Trust"},
    ])


class _NeocloudQuotes:
    class cfg:
        scene = "neocloud-fixture"

    def __init__(self, count=300):
        self.count = count
        self.profile_requests = []

    def iter_ranked(self, selector, indicators, *, sort_pos=0, order="desc",
                    page_size=1000, strict=False):
        for index in range(self.count):
            code = f"185:S{index}"
            name = f"Generic Company {index}"
            intro = "Diversified software and services"
            sector, industry = "Technology", "Software"
            if index == 0:
                code, name = "185:NVDA", "NVIDIA"
                intro, industry = "Supplies GPUs for AI data centers", "Semiconductors"
            elif index == 1:
                code, name = "185:MU", "Micron Technology"
                intro, industry = "Supplies memory for AI servers", "Semiconductors"
            elif index == 258:
                code, name = "185:NBIS", "Nebius Group"
                intro = "Operates the Yandex search portal and online advertising services"
                sector, industry = "Communications", "Interactive Media & Services"
            elif index == 298:
                code, name = "185:CRWV", "CoreWeave"
                intro = "Operates specialized cloud infrastructure for AI workloads"
                sector, industry = "Technology", "Software"
            yield {
                "code": code, "rank": index + 1,
                "values": {
                    "mktcap": self.count - index, "name": name,
                    "company_introduction": intro,
                    "sector": sector, "industry": industry,
                },
            }

    def name_of(self, code):
        return code.partition(":")[2]

    def security_profiles(self, codes):
        self.profile_requests.append(list(codes))
        profiles = {
            "185:CRWV": {"name": "CoreWeave", "company_introduction": "GPU cloud"},
            "185:NBIS": {"name": "Nebius Group", "company_introduction": "AI cloud"},
        }
        return {code: profiles[code] for code in codes if code in profiles}


def _neocloud_article_and_brief():
    article = {
        "title": "CoreWeave and Nebius lead the Neocloud buildout",
        "text": "CoreWeave and Nebius reported demand for GPU cloud capacity. "
                "NVIDIA and Micron supply important components.",
        "url": "https://example.com/neocloud",
    }
    brief = {
        "theme_direction": "bullish",
        "title_lede_entities": [
            {"name": "CoreWeave", "ticker": None, "market_code": None,
             "article_role": "subject", "operating_evidence": "Reported demand growth."},
            {"name": "Nebius Group", "ticker": None, "market_code": None,
             "article_role": "subject", "operating_evidence": "Reported capacity demand."},
            {"name": "Invesco QQQ Trust", "ticker": "QQQ", "market_code": None,
             "article_role": "other", "operating_evidence": ""},
        ],
        "body_entities": [
            {"name": "NVIDIA", "ticker": "NVDA", "article_role": "supplier"},
            {"name": "Micron Technology", "ticker": "MU", "article_role": "supplier"},
        ],
    }
    return article, brief


def test_neocloud_resolved_entities_bypass_broad_lane_and_stale_metadata():
    quotes = _NeocloudQuotes()
    article, brief = _neocloud_article_and_brief()
    workflow = ThemeWorkflow(
        FakeLLM(), quotes,
        {"stock_universe": 300, "stock_candidate_budget": 50,
         "stock_broad_lane": 20},
        marketcode_resolver=_neocloud_resolver(),
    )
    candidates = workflow.stock_candidates(brief, _neocloud_profile(), article)
    by_code = {candidate["code"]: candidate for candidate in candidates}

    assert len(candidates) == 50
    assert [candidate["code"] for candidate in candidates[:2]] == [
        "185:CRWV", "185:NBIS",
    ]
    assert by_code["185:CRWV"]["candidate_lane"] == "theme_entity"
    assert by_code["185:NBIS"]["candidate_lane"] == "theme_entity"
    assert by_code["185:NBIS"]["company_introduction"].startswith("Operates the Yandex")
    assert by_code["185:CRWV"]["guaranteed_article_anchor"] is True
    assert by_code["185:NBIS"]["guaranteed_article_anchor"] is True
    assert by_code["185:CRWV"]["candidate_provenance"] == [
        "theme_profile", "title_lede",
    ]
    assert by_code["185:NVDA"]["candidate_provenance"] == [
        "article_body", "broad_liquidity",
    ]
    assert by_code["185:MU"]["candidate_provenance"] == [
        "article_body", "broad_liquidity",
    ]
    assert "185:QQQ" not in by_code
    assert len({candidate["candidate_id"] for candidate in candidates}) == 50


def test_entity_resolver_injection_accepts_mapping_results_and_resolve_only_adapter():
    by_ticker = {"CRWV": "185:CRWV", "NBIS": "185:NBIS"}

    class DictResolver:
        def resolve_entity(self, **entity):
            ticker = entity.get("ticker")
            return {
                "status": "resolved", "market_code": by_ticker[ticker],
                "security_name": entity.get("name"), "match_kind": "ticker",
            }

    class ResolveOnlyResolver:
        def resolve(self, entity):
            ticker = entity.get("ticker")
            return {
                "status": "resolved", "market_code": by_ticker[ticker],
                "name": entity.get("name"), "match_kind": "ticker",
            }

    for resolver in (DictResolver(), ResolveOnlyResolver()):
        candidates = ThemeWorkflow(
            FakeLLM(), _NeocloudQuotes(),
            {"stock_universe": 300, "stock_candidate_budget": 2},
            marketcode_resolver=resolver,
        ).stock_candidates({}, _neocloud_profile(), {"title": "", "text": ""})
        assert [candidate["code"] for candidate in candidates] == [
            "185:CRWV", "185:NBIS",
        ]


def test_title_lede_provenance_merges_after_entity_lane_fills_budget():
    quotes = _NeocloudQuotes()
    article, brief = _neocloud_article_and_brief()
    candidates = ThemeWorkflow(
        FakeLLM(), quotes,
        {"stock_universe": 300, "stock_candidate_budget": 2,
         "stock_broad_lane": 20},
        marketcode_resolver=_neocloud_resolver(),
    ).stock_candidates(brief, _neocloud_profile(), article)

    assert [candidate["code"] for candidate in candidates] == [
        "185:CRWV", "185:NBIS",
    ]
    assert all(candidate["guaranteed_article_anchor"] for candidate in candidates)
    assert all(candidate["candidate_provenance"] == [
        "theme_profile", "title_lede",
    ] for candidate in candidates)


def test_alternate_share_class_merges_article_guarantee_into_retained_issuer():
    class AlphabetQuotes:
        class cfg:
            scene = "alphabet-fixture"

        def iter_ranked(self, selector, indicators, *, sort_pos=0, order="desc",
                        page_size=1000, strict=False):
            for index, (code, name) in enumerate((
                ("185:GOOGL", "Alphabet Inc Class A"),
                ("185:GOOG", "Alphabet Inc Class C"),
            )):
                yield {
                    "code": code, "rank": index + 1,
                    "values": {
                        "mktcap": 2 - index, "name": name,
                        "company_introduction": "Internet and cloud services",
                        "sector": "Technology", "industry": "Internet",
                    },
                }

        def name_of(self, code):
            return code.partition(":")[2]

        def security_profiles(self, codes):
            return {}

    resolver = MarketCodeResolver(rows=[
        {"key": "185:GOOGL", "market": "185", "code": "GOOGL",
         "security_type": "ES", "security_name": "Alphabet Inc Class A"},
        {"key": "185:GOOG", "market": "185", "code": "GOOG",
         "security_type": "ES", "security_name": "Alphabet Inc Class C"},
    ])
    profile = {
        "exact_theme": "Internet platforms",
        "core_entities": [{
            "name": "Alphabet Inc Class A", "ticker": "GOOGL",
            "role": "direct_operator", "confidence": 0.95,
        }],
    }
    brief = {"title_lede_entities": [{
        "name": "Alphabet Inc Class C", "ticker": "GOOG",
        "article_role": "subject", "operating_evidence": "Named in the title.",
    }]}
    candidates = ThemeWorkflow(
        FakeLLM(), AlphabetQuotes(),
        {"stock_universe": 2, "stock_candidate_budget": 1},
        marketcode_resolver=resolver,
    ).stock_candidates(
        brief, profile,
        {"title": "Alphabet (NASDAQ: GOOG) expands its platform", "text": ""},
    )

    assert len(candidates) == 1
    assert candidates[0]["code"] == "185:GOOGL"
    assert candidates[0]["candidate_provenance"] == [
        "theme_profile", "title_lede",
    ]
    assert candidates[0]["guaranteed_article_anchor"] is True


def test_title_lede_literal_validation_uses_name_boundaries_and_legal_suffixes():
    is_mentioned = workflow_module._entity_is_mentioned
    assert is_mentioned({"name": "Meta"}, "Meta announced new capacity")
    assert not is_mentioned({"name": "Meta"}, "The metadata layer was upgraded")
    assert is_mentioned({"name": "IBM"}, "IBM announced new capacity")
    assert not is_mentioned({"name": "Apple"}, "Pineapple prices increased")
    assert is_mentioned(
        {"name": "CoreWeave, Inc."}, "CoreWeave reported stronger demand")
    assert not is_mentioned(
        {"name": "Strategy", "ticker": "MSTR"},
        "Apple strategy lifts its software roadmap",
    )
    assert not is_mentioned(
        {"name": "Strategy", "ticker": "MSTR"},
        "Strategy lifts Apple software roadmap",
    )
    assert is_mentioned(
        {"name": "Strategy", "ticker": "MSTR"},
        "Strategy Inc. updates its software roadmap",
    )
    assert is_mentioned(
        {"name": "Strategy", "ticker": "MSTR"},
        "$MSTR updates its software roadmap",
    )
    assert not is_mentioned(
        {"name": "ServiceNow", "ticker": "NOW"},
        "Apple says its software roadmap matters NOW",
    )
    assert is_mentioned(
        {"name": "ServiceNow", "ticker": "NOW"},
        "ServiceNow expanded its software roadmap",
    )
    assert is_mentioned(
        {"name": "ServiceNow", "ticker": "NOW"},
        "Shares of $NOW moved after the update",
    )
    assert not is_mentioned(
        {"name": "Toast", "ticker": "TOST"},
        "Toast the success of AI cloud launches.",
    )
    assert is_mentioned(
        {"name": "Toast", "ticker": "TOST"},
        "Toast reported stronger restaurant demand.",
    )


def test_structured_relationship_binds_resolved_coordinated_entity_stems_only():
    is_structured_reference = (
        workflow_module._structured_entity_reference_is_mentioned
    )
    relationship = "CoreWeave and Nebius expansion increases GPU demand"
    assert is_structured_reference({"name": "CoreWeave"}, relationship)
    assert is_structured_reference({"name": "Nebius Group"}, relationship)
    assert not is_structured_reference(
        {"name": "Toast", "ticker": "TOST"},
        "Toast the success of AI cloud launches.",
    )


def test_outside_universe_theme_entity_requires_one_live_confirmation_batch():
    quotes = _NeocloudQuotes(count=10)
    workflow = ThemeWorkflow(
        FakeLLM(), quotes,
        {"stock_universe": 10, "stock_candidate_budget": 5,
         "stock_broad_lane": 2},
        marketcode_resolver=_neocloud_resolver(),
    )
    candidates = workflow.stock_candidates({}, _neocloud_profile(), {"title": "", "text": ""})
    by_code = {candidate["code"]: candidate for candidate in candidates}

    assert set(quotes.profile_requests[0]) == {"185:CRWV", "185:NBIS"}
    assert len(quotes.profile_requests) == 1
    assert by_code["185:CRWV"]["outside_liquidity_universe"] is True
    assert by_code["185:NBIS"]["outside_liquidity_universe"] is True
    assert len(candidates) == 5


def _new_stock_score(candidate_id, code, *, theme, article, exposure="direct"):
    direct = exposure == "direct"
    business_fact = (
        "Operates GPU cloud capacity" if direct
        else "Supplies AI infrastructure components"
    )
    theme_connection = (
        "Neocloud demand increases utilization of GPU cloud capacity" if direct
        else "Neocloud expansion increases demand for AI infrastructure components"
    )
    return {
        "candidate_id": candidate_id,
        "market_code": code,
        "theme_relevance": theme,
        "article_support": article,
        "exposure_type": exposure,
        "confidence": 0.95,
        "impact_channel": "revenue_demand" if direct else "supply_chain_orders",
        "theme_specificity": "company_specific" if direct else "industry_specific",
        "materiality": "high" if direct else "medium",
        "evidence_strength": "explicit" if direct else "derived",
        "reason": "GPU cloud capacity is core revenue" if direct else "Supplies AI infrastructure",
        "article_reason": "Explicit operating evidence" if article else "not mentioned",
        "public_relation_score": theme,
        "public_relation_confidence": 0.95,
        "relation_type": "direct" if direct else "supplier",
        "directional_effect": "positive",
        "business_fact": business_fact,
        "theme_connection": theme_connection,
        "financial_pathway": "Higher demand can lift orders, revenue, and earnings",
        "evidence_basis": "combined" if article else "company_profile",
    }


def test_stock_scoring_prompt_preserves_trusted_role_beside_stale_company_profile():
    class CaptureLLM:
        def __init__(self):
            self.calls = []

        def chat_json(self, system, user, **kw):
            self.calls.append((system, user))
            return [_new_stock_score(
                "S0001", "185:NBIS", theme=4.9, article=1.0,
            )]

    llm = CaptureLLM()
    workflow = ThemeWorkflow(llm, FakeQuotes())
    workflow._theme_profile = _neocloud_profile()
    workflow._exact_theme = "Neocloud"
    rows = [{
        "code": "185:NBIS", "name": "Nebius Group",
        "company_introduction": "Operates the Yandex search portal and advertising",
        "candidate_lane": "theme_entity",
        "candidate_provenance": ["theme_profile", "title_lede"],
        "entity_role": "pure_play_operator", "entity_confidence": 0.98,
        "guaranteed_article_anchor": True,
    }]
    scores = workflow.score_relevance(
        {"theme_direction": "bullish"}, rows,
        {"title": "Nebius leads the Neocloud buildout", "text": "Nebius demand rose."},
    )

    assert scores["185:NBIS"]["relevance_status"] == "scored"
    system, user = llm.calls[0]
    assert "cached business description is stale" in system
    assert "discovery_provenance=theme_profile,title_lede" in user
    assert "theme_profile_role=pure_play_operator" in user
    assert "business=Operates the Yandex search portal" in user


def test_golden_neocloud_public_stocks_put_coreweave_and_nebius_first():
    class NeocloudScoringLLM:
        def chat_json(self, system, user, **kw):
            output = []
            for line in user.splitlines():
                parts = [part.strip() for part in line.split("|")]
                if len(parts) < 3 or ":" not in parts[0]:
                    continue
                code, candidate_id = parts[:2]
                ticker = code.partition(":")[2]
                score, exposure = {
                    "CRWV": (5.0, "direct"), "NBIS": (4.9, "direct"),
                    "NVDA": (3.7, "supply_chain"), "MU": (3.6, "supply_chain"),
                }.get(ticker, (1.0, "unclear"))
                if exposure == "unclear":
                    row = _new_stock_score(
                        candidate_id, code, theme=score, article=0, exposure="direct")
                    row.update({
                        "exposure_type": "unclear", "confidence": 0.3,
                        "impact_channel": "none", "theme_specificity": "none",
                        "materiality": "low", "evidence_strength": "none",
                    })
                else:
                    row = _new_stock_score(
                        candidate_id, code, theme=score, article=1, exposure=exposure)
                    if exposure == "supply_chain":
                        row["theme_connection"] = (
                            "CoreWeave and Nebius expansion increases demand for "
                            "AI infrastructure components"
                        )
                output.append(row)
            return output

    quotes = _NeocloudQuotes()
    article, brief = _neocloud_article_and_brief()
    article = dict(article, text=(
        "CoreWeave operates GPU cloud capacity, and Neocloud demand increases "
        "utilization of CoreWeave GPU cloud capacity. "
        "Nebius Group operates GPU cloud capacity, and Neocloud demand increases "
        "utilization of Nebius Group GPU cloud capacity. "
        "NVIDIA supplies AI infrastructure components as CoreWeave and Nebius "
        "expansion increases demand for AI infrastructure components. "
        "Micron Technology supplies AI infrastructure components as CoreWeave "
        "and Nebius expansion increases demand for AI infrastructure components."
    ))
    brief = dict(brief, input_theme="Neocloud")
    workflow = ThemeWorkflow(
        NeocloudScoringLLM(), quotes,
        {"stock_universe": 300, "stock_candidate_budget": 50,
         "stock_broad_lane": 20, "relevance_batch": 10},
        marketcode_resolver=_neocloud_resolver(),
    )
    profile = _neocloud_profile()
    workflow._theme_profile = profile
    workflow._exact_theme = "Neocloud"
    candidates = workflow.stock_candidates(brief, profile, article)
    chosen = workflow.screen_until_target(candidates, brief, article, 8, "stocks")

    assert [item["code"] for item in chosen[:2]] == ["185:CRWV", "185:NBIS"]
    assert [item["code"] for item in chosen[2:4]] == ["185:NVDA", "185:MU"]
    assert len(chosen) == 8
    assert all(item["stock_membership_frozen"] for item in chosen)


def test_eighty_twenty_weight_cannot_admit_off_theme_article_name():
    scores = {
        "185:PURE": (5.0, 0.0, "direct"),
        "185:SUP": (4.3, 1.0, "supply_chain"),
        "185:OFF": (2.9, 1.0, "direct"),
    }

    class WeightLLM:
        def chat_json(self, system, user, **kw):
            output = []
            for line in user.splitlines():
                parts = [part.strip() for part in line.split("|")]
                if len(parts) >= 3 and ":" in parts[0]:
                    theme, article, exposure = scores[parts[0]]
                    row = _new_stock_score(
                        parts[1], parts[0], theme=theme,
                        article=article, exposure=exposure)
                    business = next(
                        (part.partition("=")[2] for part in parts
                         if part.startswith("business=")),
                        "",
                    )
                    row.update({
                        "business_fact": business,
                        "theme_connection": (
                            f"Neocloud demand increases orders for {business}"
                        ),
                        "evidence_basis": "company_profile",
                    })
                    output.append(row)
            return output

    workflow = ThemeWorkflow(WeightLLM(), FakeQuotes())
    chosen = workflow.screen_until_target(
        _rows(*scores), {"exact_theme": "Neocloud"}, {}, 3, "stocks")
    assert [candidate["code"] for candidate in chosen] == [
        "185:PURE", "185:SUP", "185:OFF",
    ]
    structural = workflow._last_stock_evidence
    assert [candidate["code"] for candidate in structural] == [
        "185:PURE", "185:SUP",
    ]
    assert structural[0]["article_support"] == 0.0
    assert all("weak_theme_fallback" not in candidate for candidate in structural)


def test_public_stock_fallback_fills_exact_basket_but_keeps_strict_etf_evidence():
    class BitcoinLLM(ScriptedLLM):
        def chat_json(self, system, user, **kw):
            if "Score each candidate" not in user:
                return super().chat_json(system, user, **kw)
            output = []
            for line in user.splitlines():
                parts = [part.strip() for part in line.split("|")]
                if len(parts) < 3 or ":" not in parts[0]:
                    continue
                code = parts[0]
                self.scored_codes.append(code)
                if code == "185:MARA":
                    score, exposure = 4.0, "direct"
                elif code in {"185:RIOT", "185:CLSK"}:
                    score, exposure = 3.1, "direct"
                else:
                    score, exposure = 1.5, "factor_proxy"
                row = _fake_stock_score(line, score)
                if exposure == "factor_proxy":
                    row.update({
                        "exposure_type": "factor_proxy",
                        "confidence": 0.2,
                        "impact_channel": "market_beta",
                        "theme_specificity": "broad_factor",
                        "materiality": "low",
                        "evidence_strength": "none",
                        "reason": "broad market beta",
                    })
                output.append(row)
            return {"results": output}

    rows = _rows(*[
        "185:MARA", "185:RIOT", "185:CLSK", "185:COIN", "185:MSTR",
        "185:WULF", "185:IREN", "185:BTDR", "185:NVDA", "185:AMD",
    ])
    workflow = ThemeWorkflow(
        BitcoinLLM({}), FakeQuotes(),
        {"relevance_batch": 20, "relevance_threshold": 3.3},
    )
    chosen = workflow.screen_until_target(
        rows, {"exact_theme": "Bitcoin Surged"}, {}, target=8, kind="stocks")

    assert len(chosen) == 8
    assert len({candidate["code"] for candidate in chosen}) == 8
    assert chosen[0]["code"] == "185:MARA"
    assert all("weak_theme_fallback" not in candidate for candidate in chosen)
    assert {candidate["code"] for candidate in workflow._last_stock_evidence} == {
        "185:MARA",
    }
    assert all(candidate["code"] not in {
        "185:CLSK", "185:COIN", "185:MSTR", "185:WULF", "185:IREN", "185:BTDR", "185:NVDA", "185:AMD",
    } for candidate in workflow._last_stock_evidence)
    accepted, narratives = workflow.finalize_stock_rationales(
        {"theme": "Bitcoin Surged"}, {"theme_direction": "bullish"},
        chosen, 8,
    )
    assert [candidate["code"] for candidate in accepted] == [
        candidate["code"] for candidate in chosen
    ]
    assert set(narratives) == {candidate["code"] for candidate in chosen}


def test_title_lede_anchors_are_not_guaranteed_into_public_output():
    class GuaranteeLLM:
        def chat_json(self, system, user, **kw):
            output = []
            for line in user.splitlines():
                parts = [part.strip() for part in line.split("|")]
                if len(parts) < 3 or ":" not in parts[0]:
                    continue
                index = int(re.search(r"(\d+)$", parts[0]).group(1))
                anchor = parts[0].startswith("185:A")
                theme = max(1.0, 5.0 - 0.3 * index) if anchor else 4.5
                row = _new_stock_score(
                    parts[1], parts[0], theme=theme,
                    article=1.0 if anchor else 0.0)
                business = next(
                    (part.partition("=")[2] for part in parts
                     if part.startswith("business=")),
                    "",
                )
                row.update({
                    "business_fact": business,
                    "theme_connection": (
                        f"Neocloud demand increases orders for {business}"
                    ),
                    "evidence_basis": "company_profile",
                })
                output.append(row)
            return output

    few = _rows("185:A12", "185:A13", "185:A14", "185:N0", "185:N1", "185:N2")
    for candidate in few[:3]:
        candidate["guaranteed_article_anchor"] = True
    workflow = ThemeWorkflow(GuaranteeLLM(), FakeQuotes())
    chosen = workflow.screen_until_target(
        few, {"exact_theme": "Neocloud"}, {}, 6, "stocks")
    assert len(chosen) == 6
    assert all(
        "weak_guaranteed_anchor" not in candidate
        for candidate in workflow._last_stock_evidence
    )
    assert {candidate["code"] for candidate in workflow._last_stock_evidence} == {
        "185:N0", "185:N1", "185:N2",
    }

    overflow = _rows(*[f"185:A{i}" for i in range(10)])
    for candidate in overflow:
        candidate["guaranteed_article_anchor"] = True
    overflow_workflow = ThemeWorkflow(GuaranteeLLM(), FakeQuotes())
    capped = overflow_workflow.screen_until_target(
        overflow, {"exact_theme": "Neocloud"}, {}, 8, "stocks")
    assert len(capped) == 8
    assert all(
        "weak_guaranteed_anchor" not in candidate
        for candidate in overflow_workflow._last_stock_evidence
    )


def test_partial_relevance_response_retries_only_missing_candidates():
    calls = []

    class PartialLLM:
        def chat_json(self, system, user, **kw):
            parsed = []
            for line in user.splitlines():
                parts = [part.strip() for part in line.split("|")]
                if len(parts) >= 3 and ":" in parts[0]:
                    parsed.append((parts[1], parts[0]))
            calls.append([code for _, code in parsed])
            selected = parsed[:1] if len(calls) == 1 else parsed
            return [
                _new_stock_score(candidate_id, code, theme=5, article=0)
                for candidate_id, code in selected
            ]

    scores = ThemeWorkflow(
        PartialLLM(), FakeQuotes(), {"relevance_batch": 10}
    ).score_relevance({}, _rows("185:A", "185:B"))
    assert calls == [["185:A", "185:B"], ["185:B"]]
    assert scores["185:A"]["relevance_retry"] == "not_needed"
    assert scores["185:B"]["relevance_retry"] == "recovered"


def test_duplicate_candidate_ids_fail_closed_after_single_retry():
    class DuplicateIdLLM:
        def chat_json(self, system, user, **kw):
            parsed = []
            for line in user.splitlines():
                parts = [part.strip() for part in line.split("|")]
                if len(parts) >= 3 and ":" in parts[0]:
                    parsed.append((parts[1], parts[0]))
            if not parsed:
                return []
            duplicate_id = parsed[0][0]
            return [
                _new_stock_score(duplicate_id, code, theme=5, article=1)
                for _, code in parsed
            ]

    scores = ThemeWorkflow(
        DuplicateIdLLM(), FakeQuotes(), {"relevance_batch": 10}
    ).score_relevance({}, _rows("185:A", "185:B"))
    assert all(score["relevance_status"] != "scored" for score in scores.values())
    assert all(score["relevance_retry"] == "persistent_miss" for score in scores.values())


def test_market_strength_cannot_inflate_public_stock_theme_exposure():
    rows = [
        {"code": "185:STRONG", "public_semantic_score": 0.80,
         "market_strength": 0.0},
        {"code": "185:MOVER", "public_semantic_score": 0.75,
         "market_strength": 1.0},
    ]
    ThemeWorkflow(FakeLLM(), FakeQuotes())._assign_theme_exposure(rows)
    assert [row["code"] for row in rows] == ["185:STRONG", "185:MOVER"]
    assert rows[0]["theme_exposure_raw"] == 0.80
    assert rows[1]["theme_exposure_raw"] == 0.75
    assert rows[0]["theme_exposure"] > rows[1]["theme_exposure"]


def test_theme_profile_is_frozen_before_article_analysis():
    class SeparatingLLM:
        def __init__(self):
            self.calls = []

        def chat_json(self, system, user, **kw):
            self.calls.append((system, user))
            if system == workflow_module.prompts.THEME_PROFILE_SYS:
                assert "Generic AI headline" not in user
                assert "https://example.com" not in user
                assert user.count("Neocloud") >= 1
                return _neocloud_profile()
            return {
                "theme_cn": "ARTICLE OVERRIDE", "summary": "event", "thesis": "thesis",
                "theme_direction": "bullish", "title_lede_entities": [],
                "body_entities": [], "direct_beneficiaries": ["generic AI"],
                "picks_and_shovels": [], "false_positives": [], "keywords": [],
            }

    llm = SeparatingLLM()
    workflow = ThemeWorkflow(llm, FakeQuotes())
    profile = workflow.theme_profile("Neocloud")
    brief = workflow.event_brief(
        {"theme": "Neocloud", "date": "2026-08-14", "url": "https://example.com"},
        {"title": "Generic AI headline", "text": "Article tries to broaden the label."},
        profile,
    )
    assert profile["canonical_definition"].startswith("Specialized cloud operators")
    assert workflow._theme_profile == profile
    assert brief["theme_cn"] == profile["theme_cn"]
    assert len(llm.calls) == 2


def _grounded_public_relation_score(
    candidate_id, code, *, business_fact, theme_connection,
    financial_pathway, relation_type="supplier", theme_relevance=2.4,
    public_relation_score=4.5, exposure_type="beneficiary",
    article_support=0.5,
):
    """Complete score fixture for a broker-valid public relationship."""
    return {
        "candidate_id": candidate_id,
        "market_code": code,
        "theme_relevance": theme_relevance,
        "article_support": article_support,
        "exposure_type": exposure_type,
        "confidence": 0.92,
        "impact_channel": (
            "supply_chain_orders"
            if relation_type in {"supplier", "customer"}
            else "revenue_demand"
        ),
        "theme_specificity": "company_specific",
        "materiality": "high",
        "evidence_strength": "explicit",
        "reason": theme_connection,
        "article_reason": "The article supplies company-specific operating evidence.",
        "public_relation_score": public_relation_score,
        "public_relation_confidence": 0.92,
        "relation_type": relation_type,
        "directional_effect": "positive",
        "business_fact": business_fact,
        "theme_connection": theme_connection,
        "financial_pathway": financial_pathway,
        "evidence_basis": "combined",
    }


def _unrelated_public_score(candidate_id, code):
    return {
        "candidate_id": candidate_id,
        "market_code": code,
        "theme_relevance": 1.0,
        "article_support": 0.0,
        "exposure_type": "factor_proxy",
        "confidence": 0.2,
        "impact_channel": "market_beta",
        "theme_specificity": "broad_factor",
        "materiality": "low",
        "evidence_strength": "none",
        "reason": "Generic market exposure does not establish an operating relationship.",
        "article_reason": "not mentioned",
        "public_relation_score": 1.0,
        "public_relation_confidence": 0.2,
        "relation_type": "none",
        "directional_effect": "none",
        "business_fact": "",
        "theme_connection": "",
        "financial_pathway": "",
        "evidence_basis": "none",
    }


def test_eight_grounded_public_relationships_do_not_expand_etf_evidence():
    names = {
        "185:AAPL": "Apple",
        "185:TSM": "TSMC",
        "185:MU": "Micron",
        "185:AMAT": "Applied Materials",
        "185:GOOGL": "Alphabet",
        "185:QCOM": "Qualcomm",
        "185:AMKR": "Amkor",
        "185:TER": "Teradyne",
        "185:MSTR": "Strategy",
    }
    structural_codes = {"185:AAPL", "185:TSM", "185:MU", "185:AMAT"}
    public_codes = set(names) - {"185:MSTR"}

    class RelationshipLLM:
        def chat_json(self, system, user, **kwargs):
            assert kwargs.get("schema_name") == "stock_relevance_batch"
            results = []
            for line in user.splitlines():
                parts = [part.strip() for part in line.split("|")]
                if len(parts) < 3 or parts[0] not in names:
                    continue
                code, candidate_id = parts[:2]
                if code == "185:MSTR":
                    results.append(_unrelated_public_score(candidate_id, code))
                    continue
                structural = code in structural_codes
                results.append(_grounded_public_relation_score(
                    candidate_id,
                    code,
                    business_fact=f"{names[code]} sells event-linked products and services.",
                    theme_connection=(
                        f"{names[code]}'s operations have a supplied company-specific event link."
                    ),
                    financial_pathway="The event can lift product orders and revenue.",
                    relation_type="direct" if code == "185:AAPL" else "supplier",
                    theme_relevance=4.3 if structural else 2.4,
                    public_relation_score=4.8 if structural else 4.2,
                    exposure_type="direct" if code == "185:AAPL" else (
                        "supply_chain" if structural else "beneficiary"
                    ),
                ))
            return {"results": results}

    workflow = ThemeWorkflow(
        RelationshipLLM(), FakeQuotes(), {"relevance_batch": 20},
    )
    candidates = [
        {
            "code": code,
            "name": name,
            "rank": rank,
            "candidate_provenance": ["article_body"],
            "company_introduction": (
                f"{name} sells event-linked products and services."
                if code != "185:MSTR"
                else "Strategy holds bitcoin and sells enterprise software."
            ),
        }
        for rank, (code, name) in enumerate(names.items(), 1)
    ]
    public, etf_evidence = workflow.screen_stock_sets(
        candidates,
        {"theme_direction": "bullish"},
        {
            "title": "Apple leadership event",
            "text": " ".join(
                f"{name}'s operations have a supplied company-specific event link."
                for code, name in names.items() if code != "185:MSTR"
            ),
        },
        8,
    )

    assert len(public) == 8
    assert {candidate["code"] for candidate in public} == public_codes
    assert {candidate["code"] for candidate in etf_evidence} == structural_codes
    assert public_codes - structural_codes
    assert (public_codes - structural_codes).isdisjoint(
        candidate["code"] for candidate in etf_evidence
    )
    assert "185:MSTR" not in {candidate["code"] for candidate in public}


def test_stock_finalizer_rejects_membership_count_mismatch_as_universe_exhaustion():
    workflow = ThemeWorkflow(FakeLLM(), FakeQuotes())
    candidates = [
        {"code": f"185:GROUNDED{i}", "name": f"Grounded Issuer {i}"}
        for i in range(7)
    ]

    try:
        workflow.finalize_stock_rationales(
            {"theme": "Apple leadership"},
            {"theme_direction": "bullish"},
            candidates,
            8,
        )
        assert False, "accepted fewer grounded candidates than stock_target"
    except SelectionUniverseError as exc:
        assert exc.asset_class == "stock"
        assert exc.expected == 8
        assert exc.available == 7


def test_invalid_stock_rationale_uses_same_full_market_code_fallback():
    class InvalidNarrator:
        def chat_json(self, system, user, **kwargs):
            return {"items": [{
                "candidate_id": "S0001",
                "market_code": "185:MU",
                "theme_rationale": {
                    "type": "multilingual", "en": "", "zh": "",
                },
            }]}

    candidate = {
        "candidate_id": "S0001", "code": "185:MU", "name": "Micron",
        "company_introduction": "Micron sells HBM memory chips",
        "business_fact": "Micron sells HBM memory chips",
        "theme_connection": "AI accelerators require HBM memory chips",
        "financial_pathway": "More HBM orders can lift revenue and earnings",
        "directional_effect": "positive",
    }
    workflow = ThemeWorkflow(InvalidNarrator(), FakeQuotes())
    accepted, narratives = workflow.finalize_stock_rationales(
        {"theme": "AI memory"},
        {"theme_direction": "bullish"},
        [candidate],
        1,
    )
    assert accepted == [candidate]
    assert set(narratives) == {"185:MU"}
    rationale = narratives["185:MU"]["theme_rationale"]
    assert rationale["en"] and rationale["zh"]
    assert "Micron sells HBM memory chips" in rationale["en"]


def test_all_quota_filler_stock_boilerplate_is_rejected():
    safe_en = (
        "Apple designs consumer devices and services, and product execution can lift "
        "device sales and services earnings."
    )
    safe_zh = "苹果设计消费电子设备并运营服务业务，产品执行有望推动设备销售与服务业务盈利。"
    for bad_en in (
        (
            "Apple is a secondary watchlist name, not a high-conviction theme trade; "
            "a clear earnings catalyst still needs to emerge."
        ),
        (
            "Apple is the sole company directly governed by the event, while stronger "
            "theme demand could affect revenue."
        ),
        (
            "Micron has memory exposure, while available disclosures do not quantify "
            "the sensitivity."
        ),
    ):
        assert ThemeWorkflow._validated_theme_rationale({
            "type": "multilingual", "en": bad_en, "zh": safe_zh,
        }) is None
    for bad_zh in (
        "苹果是次级观察标的，并非高确信度主题交易，明确的盈利催化剂仍需出现。",
        "苹果是唯一直接受影响的公司，更强的主题需求可能推动收入。",
    ):
        assert ThemeWorkflow._validated_theme_rationale({
            "type": "multilingual", "en": safe_en, "zh": bad_zh,
        }) is None


def test_apple_ceo_ecosystem_produces_distinct_grounded_rationales_without_mstr():
    cases = {
        "185:AAPL": {
            "name": "Apple",
            "business": (
                "Apple designs and sells devices including iPhones, Macs, wearables, "
                "and digital services."
            ),
            "connection": "Leadership continuity supports Apple's product and AI roadmap.",
            "pathway": "Sustained execution can lift device sales and services earnings.",
            "relation": "direct",
            "theme": 5.0,
            "exposure": "direct",
            "en": (
                "Apple designs and sells iPhones, Macs, wearables, and digital services; leadership "
                "continuity supports Apple's product and AI roadmap, so sustained execution can "
                "lift device sales and services earnings."
            ),
            "zh": (
                "Apple销售iPhone、Mac、可穿戴设备和数字服务；领导层延续性支撑Apple的产品"
                "与人工智能路线图，持续执行可提升设备销售和服务业务盈利。"
            ),
        },
        "185:TSM": {
            "name": "TSMC",
            "business": "TSMC fabricates advanced processors for Apple devices.",
            "connection": "Apple's continuing silicon roadmap requires advanced-node foundry capacity.",
            "pathway": "Apple devices can raise processor orders and foundry revenue.",
            "relation": "supplier",
            "theme": 3.8,
            "exposure": "supply_chain",
            "en": (
                "TSMC fabricates advanced processors for Apple devices; Apple's continuing silicon "
                "roadmap requires advanced-node foundry capacity, so Apple devices can raise "
                "processor orders and foundry revenue."
            ),
            "zh": (
                "TSMC制造处理器供Apple设备使用；Apple持续推进的硅路线图需要先进制程代工"
                "产能，Apple设备可提升处理器订单和代工收入。"
            ),
        },
        "185:GOOGL": {
            "name": "Alphabet",
            "business": "Alphabet operates Google Search, Cloud, and Gemini.",
            "connection": "Alphabet has an Apple AI-distribution partnership in the supplied evidence.",
            "pathway": "Broader Gemini distribution can increase usage and cloud revenue.",
            "relation": "partner",
            "theme": 2.6,
            "exposure": "beneficiary",
            "en": (
                "Alphabet operates Google Search, Cloud, and Gemini; its Apple AI-distribution "
                "partnership can broaden Gemini distribution, increasing usage and cloud revenue."
            ),
            "zh": (
                "Alphabet运营云和Gemini；其与Apple的人工智能分发合作可扩大Gemini分发，"
                "提高使用量并增加云业务收入。"
            ),
        },
        "185:MU": {
            "name": "Micron",
            "business": "Micron sells DRAM and NAND memory used in consumer devices.",
            "connection": "New Apple devices can carry higher memory content.",
            "pathway": "Higher memory content can raise component demand, revenue, and margins.",
            "relation": "supplier",
            "theme": 3.7,
            "exposure": "supply_chain",
            "en": (
                "Micron sells DRAM and NAND memory used in consumer devices; higher memory content "
                "in new Apple devices can raise component demand, memory revenue, and margins."
            ),
            "zh": (
                "Micron销售DRAM和NAND存储，用于消费设备；Apple新设备的存储容量提升可"
                "增加元件需求、存储收入和利润率。"
            ),
        },
        "185:AVGO": {
            "name": "Broadcom",
            "business": "Broadcom supplies wireless connectivity chips used in Apple devices.",
            "connection": "Apple device programs require Broadcom connectivity components.",
            "pathway": "A steady product cycle can raise component orders and chip revenue.",
            "relation": "supplier",
            "theme": 3.5,
            "exposure": "supply_chain",
            "en": (
                "Broadcom supplies wireless connectivity chips used in Apple devices; Apple device "
                "programs require those components, so a steady product cycle can raise component "
                "orders and chip revenue."
            ),
            "zh": (
                "Broadcom供应无线连接芯片，用于Apple设备；Apple设备项目需要这些元件，"
                "稳定的产品周期可提升元件订单和芯片收入。"
            ),
        },
        "185:QCOM": {
            "name": "Qualcomm",
            "business": "Qualcomm sells cellular modems and licenses wireless technology.",
            "connection": "Apple premium devices use cellular connectivity technology.",
            "pathway": "Apple device demand can support modem shipments, licensing revenue, and earnings.",
            "relation": "supplier",
            "theme": 3.0,
            "exposure": "supply_chain",
            "en": (
                "Qualcomm sells cellular modems and licenses wireless technology; Apple premium "
                "devices use cellular connectivity, so device demand can support modem shipments, "
                "licensing revenue, and earnings."
            ),
            "zh": (
                "Qualcomm销售蜂窝调制解调器并授权无线技术；Apple高端设备使用蜂窝连接，"
                "设备需求可支撑调制解调器出货、授权收入和盈利。"
            ),
        },
        "185:AMKR": {
            "name": "Amkor",
            "business": "Amkor provides outsourced semiconductor packaging and testing.",
            "connection": "Apple supplier volumes flow through packaging and testing capacity.",
            "pathway": "Higher supplier volumes can increase packaging orders, utilization, and revenue.",
            "relation": "supplier",
            "theme": 3.4,
            "exposure": "supply_chain",
            "en": (
                "Amkor provides outsourced semiconductor packaging and testing; Apple supplier "
                "volumes flow through that capacity, so higher volumes can increase packaging "
                "orders, utilization, and revenue."
            ),
            "zh": (
                "Amkor提供半导体封装与测试；Apple供应商产量流经这些产能，产量提升"
                "可增加封装订单、利用率和收入。"
            ),
        },
        "185:TER": {
            "name": "Teradyne",
            "business": "Teradyne sells automated semiconductor test systems.",
            "connection": "New Apple chip programs require suppliers to expand test capacity.",
            "pathway": "Expanded test capacity can boost test-system orders and revenue.",
            "relation": "second_order",
            "theme": 3.2,
            "exposure": "enabler",
            "en": (
                "Teradyne sells automated semiconductor test systems; new Apple chip programs "
                "require suppliers to expand test capacity, which can boost test-system orders "
                "and revenue."
            ),
            "zh": (
                "Teradyne销售半导体自动化测试系统；Apple新芯片项目要求供应商扩大测试产能，"
                "从而提升测试系统订单和收入。"
            ),
        },
    }

    class AppleEcosystemLLM:
        def chat_json(self, system, user, **kwargs):
            if kwargs.get("schema_name") == "stock_relevance_batch":
                results = []
                for line in user.splitlines():
                    parts = [part.strip() for part in line.split("|")]
                    if len(parts) < 3 or not parts[0].startswith("185:"):
                        continue
                    code, candidate_id = parts[:2]
                    if code == "185:MSTR":
                        results.append(_unrelated_public_score(candidate_id, code))
                        continue
                    case = cases[code]
                    results.append(_grounded_public_relation_score(
                        candidate_id,
                        code,
                        business_fact=case["business"],
                        theme_connection=case["connection"],
                        financial_pathway=case["pathway"],
                        relation_type=case["relation"],
                        theme_relevance=case["theme"],
                        public_relation_score=4.9,
                        exposure_type=case["exposure"],
                        article_support=0.8,
                    ))
                return {"results": results}
            if kwargs.get("schema_name") == "stock_broker_narratives":
                records_json = user.split("Records (JSON):\n", 1)[1].split(
                    "\n\nWriting requirements:", 1,
                )[0]
                records = json.loads(records_json)
                return {"items": [{
                    "candidate_id": record["candidate_id"],
                    "market_code": record["market_code"],
                    "theme_rationale": {
                        "type": "multilingual",
                        "en": cases[record["market_code"]]["en"],
                        "zh": cases[record["market_code"]]["zh"],
                    },
                } for record in records]}
            raise AssertionError(f"unexpected LLM call: {kwargs.get('schema_name')}")

    candidates = [
        {
            "code": code,
            "name": case["name"],
            "company_introduction": case["business"],
            "candidate_lane": "event_ecosystem",
            "candidate_provenance": ["article_body", "event_ecosystem"],
            "rank": rank,
        }
        for rank, (code, case) in enumerate(cases.items(), 1)
    ] + [{
        "code": "185:MSTR",
        "name": "Strategy",
        "company_introduction": "Strategy holds bitcoin and sells enterprise software.",
        "candidate_lane": "broad_liquidity",
        "candidate_provenance": ["broad_liquidity"],
        "rank": 99,
    }]
    workflow = ThemeWorkflow(
        AppleEcosystemLLM(), FakeQuotes(), {"relevance_batch": 20},
    )
    selected, etf_evidence = workflow.screen_stock_sets(
        candidates,
        {"theme_direction": "bullish", "summary": "Apple CEO succession planning."},
        {
            "title": "Apple leadership transition",
            "text": " ".join(
                f"{case['name']} reports that {case['business']} "
                f"{case['name']} reports that {case['connection']}"
                for case in cases.values()
            ),
        },
        8,
    )
    accepted, narrative = workflow.finalize_stock_rationales(
        {"theme": "Apple CEO succession"},
        {"theme_direction": "bullish", "summary": "Apple CEO succession planning."},
        selected,
        8,
    )

    accepted_codes = {candidate["code"] for candidate in accepted}
    assert len(accepted) == 8
    assert {"185:AAPL", "185:TSM", "185:GOOGL", "185:MU"} <= accepted_codes
    assert "185:MSTR" not in accepted_codes
    assert "185:MSTR" not in narrative
    assert narrative["185:AAPL"]["theme_rationale"]["en"].startswith("Apple designs")
    assert narrative["185:TSM"]["theme_rationale"]["en"].startswith("TSMC fabricates")
    assert narrative["185:GOOGL"]["theme_rationale"]["en"].startswith("Alphabet operates")
    assert narrative["185:MU"]["theme_rationale"]["en"].startswith("Micron sells")
    fingerprints = {
        workflow._narrative_fingerprint(
            candidate, narrative[candidate["code"]]["theme_rationale"],
        )
        for candidate in accepted
    }
    assert len(fingerprints) == 8
    public = workflow._assemble(
        accepted, narrative, "2026-09-02", "bullish",
    )
    assert len(public) == 8
    assert all(item["theme_rationale"]["en"] for item in public)
    assert all(item["theme_rationale"]["zh"] for item in public)
    assert {candidate["code"] for candidate in etf_evidence} < accepted_codes


def test_near_duplicate_stock_copy_retries_only_duplicate_without_substitution():
    def candidate(code, name, business, connection, pathway, rank):
        return {
            "candidate_id": f"S{rank:04d}",
            "code": code,
            "name": name,
            "company_introduction": business,
            "business_fact": business,
            "theme_connection": connection,
            "financial_pathway": pathway,
            "relation_type": "supplier",
            "directional_effect": "positive",
            "rank": rank,
        }

    alpha = candidate(
        "185:AAA", "Alpha", "Alpha makes HBM modules",
        "AI accelerator programs require HBM modules",
        "More accelerator deployments can lift HBM orders, revenue, and earnings", 1,
    )
    beta = candidate(
        "185:BBB", "Beta", "Beta makes HBM modules",
        "AI accelerator programs require HBM modules",
        "More accelerator deployments can lift HBM orders, revenue, and earnings", 2,
    )
    gamma = candidate(
        "185:CCC", "Gamma", "Gamma builds liquid cooling systems",
        "Dense AI data centers require liquid cooling systems",
        "Rack deployments can raise cooling-equipment orders, revenue, and earnings", 3,
    )
    calls = []
    first_alpha = (
        "Alpha makes HBM modules for AI accelerator programs, so increasing "
        "accelerator deployments lift HBM orders, revenue, and earnings."
    )

    class DuplicateThenReserveLLM:
        def chat_json(self, system, user, **kwargs):
            records = _narrative_records(user)
            items = []
            for record in records:
                code = record["market_code"]
                calls.append(code)
                if code == "185:AAA":
                    en = first_alpha
                    zh = (
                        "Alpha主营HBM modules；AI accelerator programs require "
                        "HBM modules，accelerator deployments可提升HBM订单、"
                        "收入和盈利。"
                    )
                elif code == "185:BBB":
                    # One-word variation remains a near-duplicate after identity removal.
                    en = (
                        "Beta makes HBM modules for AI accelerator programs, so rising "
                        "accelerator deployments lift HBM orders, revenue, and earnings."
                    )
                    zh = (
                        "Beta主营HBM modules；AI accelerator programs require "
                        "HBM modules，accelerator deployments可提升HBM订单、"
                        "收入和盈利。"
                    )
                else:
                    en = (
                        "Gamma builds liquid cooling systems for dense AI data centers, so "
                        "rack deployments raise cooling-equipment orders, revenue, and earnings."
                    )
                    zh = (
                        "Gamma主营liquid cooling systems；dense AI data centers"
                        "需要liquid cooling systems，rack deployments可提升"
                        "订单、收入和盈利。"
                    )
                items.append({
                    "candidate_id": record["candidate_id"],
                    "market_code": code,
                    "theme_rationale": {
                        "type": "multilingual", "en": en, "zh": zh,
                    },
                })
            return {"items": items}

    workflow = ThemeWorkflow(DuplicateThenReserveLLM(), FakeQuotes())
    workflow._last_stock_public_reserves = [gamma]
    accepted, narratives = workflow.finalize_stock_rationales(
        {"theme": "AI accelerator buildout"},
        {"theme_direction": "bullish"},
        [alpha, beta], 2,
    )

    assert [row["code"] for row in accepted] == ["185:AAA", "185:BBB"]
    assert narratives["185:AAA"]["theme_rationale"]["en"] == first_alpha
    assert calls.count("185:AAA") == 1
    assert calls.count("185:BBB") == 2
    assert calls.count("185:CCC") == 0
    assert narratives["185:BBB"]["theme_rationale"]["en"] != first_alpha


def test_stock_narrative_requires_exact_candidate_id_and_full_market_code():
    candidate = {
        "candidate_id": "S0001", "code": "185:MU", "name": "Micron",
        "company_introduction": "Micron sells HBM memory",
        "business_fact": "Micron sells HBM memory",
        "theme_connection": "AI accelerators require HBM memory",
        "financial_pathway": "More HBM demand can lift revenue and earnings",
    }
    calls = 0

    class TickerOnlyNarrator:
        def chat_json(self, system, user, **kwargs):
            nonlocal calls
            calls += 1
            return {"items": [{
                "candidate_id": "S0001",
                "market_code": "MU",
                "theme_rationale": {
                    "type": "multilingual",
                    "en": (
                        "Micron sells HBM memory for AI accelerators, so rising demand "
                        "can lift memory revenue and earnings."
                    ),
                    "zh": "美光销售用于AI加速器的HBM；需求增长可提升存储收入和盈利。",
                },
            }]}

    workflow = ThemeWorkflow(TickerOnlyNarrator(), FakeQuotes())
    assert workflow.narrate(
        {"theme": "AI memory"}, {"theme_direction": "bullish"},
        [candidate], "stock",
    ) == {}
    assert calls == 2
    assert candidate["code"] in workflow._last_stock_narrative_errors


def test_stock_narrative_rejects_directionally_mismatched_earnings_pathway():
    candidate = {
        "code": "185:NVDA", "name": "NVIDIA",
        "business_fact": "NVIDIA sells AI accelerator processors",
        "theme_connection": "Lower AI budgets reduce accelerator demand",
        "financial_pathway": "Fewer orders can pressure revenue and earnings",
        "directional_effect": "negative",
    }
    bullish_copy = {
        "type": "multilingual",
        "en": (
            "NVIDIA sells AI accelerator processors; lower AI budgets reduce "
            "accelerator demand, but more orders can lift revenue and earnings."
        ),
        "zh": (
            "NVIDIA主营AI加速器处理器；AI预算下降会减少加速器"
            "需求，但更多订单可提升收入和盈利。"
        ),
    }

    rationale, reason = ThemeWorkflow._validate_stock_narrative(
        candidate, bullish_copy)
    assert rationale is None
    assert reason == "direction_mismatch"


def test_stock_relevance_rejects_wrong_numeric_types_and_out_of_range_values():
    valid = _fake_stock_score(
        "185:MU | S0001 | Micron | business=Micron sells HBM memory chips",
        4.5,
    )
    assert workflow_module._normalise_relevance_object(
        valid, require_public_fields=True,
    )["relevance_status"] == "scored"

    invalid_values = (
        ("theme_relevance", "4.5"),
        ("theme_relevance", 0.9),
        ("theme_relevance", 5.1),
        ("confidence", True),
        ("confidence", -0.1),
        ("confidence", 1.1),
        ("article_support", "0.5"),
        ("article_support", -0.1),
        ("article_support", 1.1),
        ("public_relation_score", "4.5"),
        ("public_relation_score", 0.9),
        ("public_relation_score", 5.1),
        ("public_relation_confidence", "0.9"),
        ("public_relation_confidence", -0.1),
        ("public_relation_confidence", 1.1),
    )
    for field, invalid in invalid_values:
        payload = dict(valid)
        payload[field] = invalid
        normalized = workflow_module._normalise_relevance_object(
            payload, require_public_fields=True,
        )
        assert normalized["relevance_status"] == "invalid_schema", (
            f"{field}={invalid!r} was coerced or clamped instead of rejected"
        )


def test_stock_narrative_rejects_list_valued_language_fields():
    candidate = {
        "code": "185:MU", "name": "Micron",
        "business_fact": "Micron sells HBM memory chips",
        "theme_connection": "AI accelerators require HBM memory chips",
        "financial_pathway": "More HBM orders can lift revenue and earnings",
    }
    en = (
        "Micron sells HBM memory chips for AI accelerators, so rising HBM "
        "orders can lift memory revenue and earnings."
    )
    zh = "美光销售用于AI加速器的HBM存储芯片；HBM订单增长可提升存储收入和盈利。"
    for field, invalid in (("en", [en]), ("zh", [zh])):
        rationale = {"type": "multilingual", "en": en, "zh": zh}
        rationale[field] = invalid
        accepted, reason = ThemeWorkflow._validate_stock_narrative(
            candidate, rationale,
        )
        assert accepted is None, f"list-valued {field} was stringified and accepted"
        assert reason == "invalid_or_forbidden_prose"


def test_stock_narrative_rejects_vacuous_business_fact_tokens():
    candidate = {
        "code": "185:MU", "name": "Micron",
        "business_fact": "The company provides services with their business",
        "theme_connection": "AI accelerators require HBM memory chips",
        "financial_pathway": "More HBM orders can lift revenue and earnings",
    }
    rationale = {
        "type": "multilingual",
        "en": (
            "Micron sells HBM memory chips for AI accelerators, so rising HBM "
            "orders can lift memory revenue and earnings."
        ),
        "zh": "美光销售用于AI加速器的HBM存储芯片；HBM订单增长可提升存储收入和盈利。",
    }

    accepted, reason = ThemeWorkflow._validate_stock_narrative(
        candidate, rationale,
    )
    assert accepted is None
    assert reason == "missing_business_fact"


def test_stock_narrative_rejects_vacuous_theme_connection_tokens():
    candidate = {
        "code": "185:MU", "name": "Micron",
        "business_fact": "Micron sells HBM memory chips",
        "theme_connection": "This event relationship with the exact theme",
        "financial_pathway": "More HBM orders can lift revenue and earnings",
    }
    rationale = {
        "type": "multilingual",
        "en": (
            "Micron sells HBM memory chips; more HBM orders can lift revenue "
            "and earnings."
        ),
        "zh": "Micron主营HBM存储芯片；更多HBM订单可提升收入和盈利。",
    }

    accepted, reason = ThemeWorkflow._validate_stock_narrative(
        candidate, rationale,
    )
    assert accepted is None
    assert reason == "missing_event_relationship"


def test_exhausted_stock_narrative_service_uses_same_code_fallback():
    candidate = {
        "candidate_id": "S0001", "code": "185:MU", "name": "Micron",
        "company_introduction": "Micron sells HBM memory",
        "business_fact": "Micron sells HBM memory",
        "theme_connection": "AI accelerators require HBM memory",
        "financial_pathway": "More HBM demand can lift revenue and earnings",
    }

    class ExhaustedLLM:
        calls = 0

        def chat_json(self, system, user, **kwargs):
            self.calls += 1
            raise RuntimeError("429 retry budget exhausted")

    llm = ExhaustedLLM()
    workflow = ThemeWorkflow(llm, FakeQuotes())
    diagnostics = io.StringIO()
    with contextlib.redirect_stderr(diagnostics):
        accepted, narratives = workflow.finalize_stock_rationales(
            {"theme": "AI memory"}, {"theme_direction": "bullish"},
            [candidate], 1,
        )
    assert accepted == [candidate]
    assert set(narratives) == {"185:MU"}
    assert narratives["185:MU"]["theme_rationale"]["en"]
    assert "expected=1 received=0 missing=185:MU" in diagnostics.getvalue()
    assert llm.calls == 2


def test_security_selection_and_etf_scoring_finish_before_stock_narration():
    stages = []

    class OrderedWorkflow(ThemeWorkflow):
        def finalize_stock_rationales(self, *args, **kwargs):
            stages.append("stock_narration")
            return super().finalize_stock_rationales(*args, **kwargs)

        def rerank_etfs_from_components(self, *args, **kwargs):
            stages.append("etf_components")
            return super().rerank_etfs_from_components(*args, **kwargs)

    OrderedWorkflow(
        FakeLLM(), FakeQuotes(),
        {"stock_universe": 20, "etf_universe": 20},
    ).run({
        "theme": "AI memory", "date": "2026-07-09",
        "url": "https://example.com/x",
    })

    assert stages.index("etf_components") < stages.index("stock_narration")


def _apple_broker_validation_candidate():
    return {
        "candidate_id": "S0001",
        "code": "185:AAPL",
        "name": "Apple",
        "company_introduction": (
            "Apple designs and sells smartphones and digital services"
        ),
        "business_fact": (
            "Apple designs and sells smartphones and digital services"
        ),
        "theme_connection": (
            "CEO succession affects Apple product execution and device roadmap"
        ),
        "financial_pathway": (
            "Execution can lift device sales, services revenue, and earnings"
        ),
        "directional_effect": "positive",
    }


def test_public_relation_gate_rejects_invented_partner_contract():
    candidate = {
        "code": "185:MSTR",
        "name": "Strategy",
        "company_introduction": "Strategy sells software and holds bitcoin",
        "relevance_status": "scored",
        "public_relation_score": 5.0,
        "public_relation_confidence": 0.95,
        "relation_type": "partner",
        "directional_effect": "positive",
        "business_fact": "Strategy sells enterprise software and holds bitcoin",
        "theme_connection": (
            "Strategy advises Apple under a CEO-services contract"
        ),
        "financial_pathway": (
            "The contract can lift advisory revenue and earnings"
        ),
        "evidence_basis": "company_profile",
        "article_support": 0.0,
        "candidate_provenance": ["broad_liquidity"],
        "impact_channel": "revenue_demand",
        "exposure_type": "beneficiary",
    }
    assert not workflow_module._is_public_relation_eligible(
        candidate,
        theme_direction="bullish",
        min_confidence=0.55,
        article={"title": "Apple CEO", "text": "Apple succession planning"},
    )


def test_public_relation_gate_rejects_lowercase_invented_partner_counterparty():
    candidate = {
        "code": "185:MSTR",
        "name": "Strategy",
        "company_introduction": (
            "Strategy sells enterprise software and holds bitcoin"
        ),
        "relevance_status": "scored",
        "public_relation_score": 5.0,
        "public_relation_confidence": 0.95,
        "relation_type": "partner",
        "directional_effect": "positive",
        "business_fact": (
            "Strategy sells enterprise software and holds bitcoin"
        ),
        "theme_connection": (
            "Enterprise software integration with apple accelerates its CEO transition"
        ),
        "financial_pathway": (
            "The integration can lift software revenue and earnings"
        ),
        "evidence_basis": "company_profile",
        "article_support": 0.0,
        "candidate_provenance": ["broad_liquidity"],
        "impact_channel": "revenue_demand",
        "exposure_type": "beneficiary",
    }
    assert not workflow_module._is_public_relation_eligible(
        candidate,
        theme_direction="bullish",
        min_confidence=0.55,
        article={"title": "Apple CEO", "text": "Apple succession planning"},
    )


def test_public_relation_gate_rejects_direct_alien_mining_from_one_generic_overlap():
    candidate = {
        "code": "185:MSTR",
        "name": "Strategy",
        "company_introduction": (
            "Strategy sells enterprise software and holds bitcoin"
        ),
        "relevance_status": "scored",
        "public_relation_score": 5.0,
        "public_relation_confidence": 0.95,
        "relation_type": "direct",
        "directional_effect": "positive",
        "business_fact": (
            "Strategy sells enterprise software and holds bitcoin"
        ),
        "theme_connection": (
            "Software enables alien-mining launches and captures new demand"
        ),
        "financial_pathway": (
            "Alien-mining demand can lift software revenue and earnings"
        ),
        "evidence_basis": "company_profile",
        "article_support": 0.0,
        "candidate_provenance": ["broad_liquidity"],
        "impact_channel": "revenue_demand",
        "exposure_type": "direct",
    }
    assert not workflow_module._is_public_relation_eligible(
        candidate,
        theme_direction="bullish",
        min_confidence=0.55,
        article={"title": "Alien mining", "text": "Alien-mining launches"},
    )


def test_generated_event_brief_cannot_self_ground_an_unrelated_stock():
    candidate = {
        "code": "185:MSTR",
        "name": "Strategy",
        "company_introduction": (
            "Strategy sells enterprise analytics software and holds bitcoin"
        ),
        "relevance_status": "scored",
        "public_relation_score": 5.0,
        "public_relation_confidence": 0.95,
        "relation_type": "direct",
        "directional_effect": "positive",
        "business_fact": "Strategy sells enterprise analytics software",
        "theme_connection": (
            "Strategy analytics software shapes Apple device analytics roadmap"
        ),
        "financial_pathway": (
            "Software demand can lift revenue and earnings"
        ),
        "evidence_basis": "derived",
        "article_support": 0.0,
        "candidate_provenance": ["broad_liquidity"],
        "impact_channel": "revenue_demand",
        "exposure_type": "direct",
    }
    hallucinated_brief = {
        "input_theme": "Apple CEO succession",
        "theme_direction": "bullish",
        "summary": (
            "Strategy analytics and bitcoin systems drive Apple's device roadmap"
        ),
        "keywords": ["enterprise analytics", "bitcoin systems"],
        "direct_beneficiaries": ["Strategy"],
        "title_lede_entities": [{"name": "Apple"}],
    }
    assert not workflow_module._is_public_relation_eligible(
        candidate,
        theme_direction="bullish",
        min_confidence=0.55,
        article={
            "title": "Apple CEO transition",
            "text": "Apple named a CEO to oversee its device roadmap.",
        },
        brief=hallucinated_brief,
    )


def test_single_broad_concept_cannot_bridge_unrelated_company_to_ceo_event():
    candidate = {
        "code": "185:MSTR", "name": "Strategy",
        "company_introduction": "Strategy sells enterprise software",
        "relevance_status": "scored",
        "public_relation_score": 5.0,
        "public_relation_confidence": 0.95,
        "relation_type": "complementary",
        "directional_effect": "positive",
        "business_fact": "Strategy sells enterprise software",
        "theme_connection": "Enterprise software and Apple CEO succession",
        "financial_pathway": "Software sales can lift revenue and earnings",
        "evidence_basis": "derived",
        "article_support": 0.0,
        "candidate_provenance": ["event_ecosystem"],
        "impact_channel": "revenue_demand",
        "exposure_type": "beneficiary",
    }
    article = {
        "title": "Apple names its next CEO",
        "text": "Apple names its next CEO.",
    }
    brief = {"input_theme": "Apple software CEO succession"}
    assert not workflow_module._public_relation_evidence_is_grounded(
        candidate, article, brief)
    assert not workflow_module._is_public_relation_eligible(
        candidate,
        theme_direction="bullish",
        min_confidence=0.55,
        article=article,
        brief=brief,
    )


def test_short_common_ticker_is_not_a_literal_article_identity():
    assert not workflow_module._entity_is_mentioned(
        {"name": "onsemi", "ticker": "ON"},
        "Apple focuses on manufacturing devices.",
    )
    assert workflow_module._entity_is_mentioned(
        {"name": "onsemi", "ticker": "ON"},
        "Shares of $ON rose after the announcement.",
    )
    assert not workflow_module._entity_is_mentioned(
        {"name": "C3.ai", "ticker": "AI"},
        "Artificial intelligence (AI) spending is rising.",
    )
    assert not workflow_module._entity_is_mentioned(
        {"name": "Gartner", "ticker": "IT"},
        "Information technology (IT) budgets are expanding.",
    )
    assert workflow_module._entity_is_mentioned(
        {"name": "C3.ai", "ticker": "AI"},
        "C3.ai (NYSE: AI) reported results.",
    )
    candidate = {
        "code": "185:ON", "name": "onsemi",
        "company_introduction": "onsemi manufactures semiconductor chips",
        "relevance_status": "scored",
        "public_relation_score": 5.0,
        "public_relation_confidence": 0.95,
        "relation_type": "supplier",
        "directional_effect": "positive",
        "business_fact": "onsemi manufactures semiconductor chips",
        "theme_connection": "Apple manufacturing devices",
        "financial_pathway": "Device orders can lift chip revenue and earnings",
        "evidence_basis": "combined",
        "article_support": 1.0,
        "candidate_provenance": ["article_body"],
        "impact_channel": "supply_chain_orders",
        "exposure_type": "supply_chain",
    }
    article = {
        "title": "Apple manufacturing update",
        "text": "Apple focuses on manufacturing devices.",
    }
    assert not workflow_module._is_public_relation_eligible(
        candidate,
        theme_direction="bullish",
        min_confidence=0.55,
        article=article,
        brief={"input_theme": "Apple manufacturing devices"},
    )


def test_common_word_issuer_cannot_self_ground_from_an_ordinary_imperative():
    candidate = {
        "code": "185:TOST", "name": "Toast",
        "company_introduction": "Toast provides restaurant payment software",
        "relevance_status": "scored",
        "public_relation_score": 5.0,
        "public_relation_confidence": 0.95,
        "relation_type": "direct",
        "directional_effect": "positive",
        "business_fact": "Toast provides restaurant payment software",
        "theme_connection": "AI cloud launches increase restaurant software demand",
        "financial_pathway": "Software demand can lift revenue and earnings",
        "evidence_basis": "combined",
        "article_support": 1.0,
        "candidate_provenance": ["article_body"],
        "impact_channel": "revenue_demand",
        "exposure_type": "direct",
    }
    article = {
        "title": "AI cloud launch celebration",
        "text": "Toast the success of AI cloud launches.",
    }
    assert not workflow_module._is_public_relation_eligible(
        candidate,
        theme_direction="bullish",
        min_confidence=0.55,
        article=article,
        brief={"input_theme": "AI cloud launches"},
    )


def test_adjacent_third_party_relation_is_not_attributed_to_candidate():
    candidate = {
        "code": "185:TSM", "name": "TSMC",
        "company_introduction": "TSMC manufactures semiconductor chips",
        "relevance_status": "scored",
        "public_relation_score": 5.0,
        "public_relation_confidence": 0.95,
        "relation_type": "partner",
        "directional_effect": "positive",
        "business_fact": "TSMC manufactures semiconductor chips",
        "theme_connection": "Apple partnership covers devices",
        "financial_pathway": "The partnership can lift chip revenue and earnings",
        "evidence_basis": "combined",
        "article_support": 1.0,
        "candidate_provenance": ["article_body"],
        "impact_channel": "revenue_demand",
        "exposure_type": "beneficiary",
    }
    article = {
        "title": "Apple and TSMC update",
        "text": (
            "TSMC manufactures semiconductor chips. "
            "Apple partnership with Acme covers devices."
        ),
    }
    evidence = workflow_module._article_entity_evidence_text(
        candidate, f"{article['title']}. {article['text']}")
    assert "TSMC manufactures semiconductor chips" in evidence
    assert "partnership with Acme" not in evidence
    assert not workflow_module._is_public_relation_eligible(
        candidate,
        theme_direction="bullish",
        min_confidence=0.55,
        article=article,
        brief={"input_theme": "Apple partnership devices"},
    )


def test_adversative_neighbor_clause_cannot_launder_a_direct_relationship():
    candidate = {
        "code": "185:TSM", "name": "TSMC",
        "company_introduction": "TSMC manufactures semiconductor chips",
        "relevance_status": "scored",
        "public_relation_score": 5.0,
        "public_relation_confidence": 0.95,
        "relation_type": "direct",
        "directional_effect": "positive",
        "business_fact": "TSMC manufactures semiconductor chips",
        "theme_connection": "Apple CEO advances its AI roadmap",
        "financial_pathway": "AI chip demand can lift revenue and earnings",
        "evidence_basis": "combined",
        "article_support": 1.0,
        "candidate_provenance": ["article_body"],
        "impact_channel": "revenue_demand",
        "exposure_type": "direct",
    }
    article = {
        "title": "Semiconductor and leadership update",
        "text": (
            "TSMC manufactures semiconductor chips, while Apple CEO advances "
            "its AI roadmap."
        ),
    }
    evidence = workflow_module._article_entity_evidence_text(
        candidate, f"{article['title']}. {article['text']}")
    assert "TSMC manufactures semiconductor chips" in evidence
    assert "Apple CEO advances" not in evidence
    assert not workflow_module._is_public_relation_eligible(
        candidate,
        theme_direction="bullish",
        min_confidence=0.55,
        article=article,
        brief={"input_theme": "Apple CEO AI roadmap"},
    )


def test_and_neighbor_clause_cannot_launder_a_direct_relationship():
    candidate = {
        "code": "185:TSM", "name": "TSMC",
        "company_introduction": "TSMC manufactures semiconductor chips",
        "relevance_status": "scored",
        "public_relation_score": 5.0,
        "public_relation_confidence": 0.95,
        "relation_type": "direct",
        "directional_effect": "positive",
        "business_fact": "TSMC manufactures semiconductor chips",
        "theme_connection": "Apple CEO advances its AI roadmap",
        "financial_pathway": "AI chip demand can lift revenue and earnings",
        "evidence_basis": "combined",
        "article_support": 1.0,
        "candidate_provenance": ["article_body"],
        "impact_channel": "revenue_demand",
        "exposure_type": "direct",
    }
    for separator in (" and ", ", and "):
        article = {
            "title": "Semiconductor and leadership update",
            "text": (
                f"TSMC manufactures semiconductor chips{separator}"
                "Apple CEO advances its AI roadmap."
            ),
        }
        clauses = workflow_module._relationship_source_clauses(article["text"])
        assert clauses == [
            "TSMC manufactures semiconductor chips",
            "Apple CEO advances its AI roadmap",
        ]
        assert not workflow_module._is_public_relation_eligible(
            candidate,
            theme_direction="bullish",
            min_confidence=0.55,
            article=article,
            brief={"input_theme": "Apple CEO AI roadmap"},
        )


def test_relation_clause_parser_preserves_coordinated_parties_but_splits_negation():
    supplier = {
        "code": "185:NVDA",
        "name": "NVIDIA",
        "relation_type": "supplier",
        "theme_connection": (
            "CoreWeave and Nebius expansion increases demand for "
            "AI infrastructure components"
        ),
    }
    supplier_tokens = workflow_module._grounding_tokens(
        supplier["theme_connection"],
        supplier,
        stopwords=(
            workflow_module._GROUNDING_RELATION_STOPWORDS
            | workflow_module._SOURCE_RELATION_GENERIC_STOPWORDS
        ),
    )
    assert workflow_module._source_supports_relation(
        supplier,
        supplier_tokens,
        "NVIDIA supplies AI infrastructure components as CoreWeave and "
        "Nebius expansion increases demand for AI infrastructure components.",
    )

    denied_partner = {
        "code": "185:MSTR",
        "name": "Strategy",
        "relation_type": "partner",
        "theme_connection": "Strategy has an Apple partnership for software",
    }
    denied_tokens = workflow_module._grounding_tokens(
        denied_partner["theme_connection"],
        denied_partner,
        stopwords=(
            workflow_module._GROUNDING_RELATION_STOPWORDS
            | workflow_module._SOURCE_RELATION_GENERIC_STOPWORDS
        ),
    )
    assert not workflow_module._source_supports_relation(
        denied_partner,
        denied_tokens,
        "Strategy partners with IBM and has no Apple partnership for software.",
    )


def test_adjacent_shared_noun_cannot_launder_an_event_relationship():
    candidate = {
        "code": "185:MSTR",
        "name": "Strategy",
        "company_introduction": "Strategy sells enterprise analytics software",
        "relevance_status": "scored",
        "public_relation_score": 5.0,
        "public_relation_confidence": 0.95,
        "relation_type": "direct",
        "directional_effect": "positive",
        "business_fact": "Strategy sells enterprise analytics software",
        "theme_connection": (
            "Strategy enterprise analytics software shapes Apple device analytics roadmap"
        ),
        "financial_pathway": "Software demand can lift revenue and earnings",
        "evidence_basis": "derived",
        "article_support": 1.0,
        "candidate_provenance": ["article_body"],
        "impact_channel": "revenue_demand",
        "exposure_type": "direct",
    }
    assert not workflow_module._is_public_relation_eligible(
        candidate,
        theme_direction="bullish",
        min_confidence=0.55,
        article={
            "title": "Apple CEO transition",
            "text": (
                "Strategy sells enterprise analytics software. "
                "Apple named a CEO to oversee its device analytics roadmap."
            ),
        },
        brief={"input_theme": "Apple CEO succession"},
    )


def test_public_relation_gate_rejects_an_invented_financial_product_pathway():
    candidate = {
        "code": "185:AAPL",
        "name": "Apple",
        "company_introduction": (
            "Apple designs smartphones and operates digital services"
        ),
        "relevance_status": "scored",
        "public_relation_score": 5.0,
        "public_relation_confidence": 0.95,
        "relation_type": "direct",
        "directional_effect": "positive",
        "business_fact": "Apple designs smartphones and operates digital services",
        "theme_connection": (
            "CEO succession affects Apple product execution and device roadmap"
        ),
        "financial_pathway": "Device sales can lift revenue and earnings",
        "evidence_basis": "combined",
        "article_support": 1.0,
        "candidate_provenance": ["article_body"],
        "impact_channel": "revenue_demand",
        "exposure_type": "direct",
    }
    article = {
        "title": "Apple CEO succession",
        "text": (
            "Apple designs smartphones and operates digital services. "
            "CEO succession affects Apple product execution and device roadmap."
        ),
    }
    brief = {
        "input_theme": "Apple CEO succession",
        "title_lede_entities": [{"name": "Apple"}],
    }
    assert workflow_module._is_public_relation_eligible(
        candidate,
        theme_direction="bullish",
        min_confidence=0.55,
        article=article,
        brief=brief,
    )
    candidate["financial_pathway"] = (
        "Reactor orders can lift revenue and earnings"
    )
    assert not workflow_module._is_public_relation_eligible(
        candidate,
        theme_direction="bullish",
        min_confidence=0.55,
        article=article,
        brief=brief,
    )


def test_stock_narrative_requires_business_event_financial_order():
    candidate = _apple_broker_validation_candidate()
    reversed_copy = {
        "type": "multilingual",
        "en": (
            "CEO succession affects Apple product execution and device roadmap, "
            "which can lift device sales, services revenue, and earnings; Apple "
            "designs and sells smartphones and digital services."
        ),
        "zh": (
            "首席执行官继任影响Apple产品执行和设备路线图，可提升"
            "设备销售、服务收入和盈利；Apple设计并经营智能手机"
            "和数字服务。"
        ),
    }
    rationale, reason = ThemeWorkflow._validate_stock_narrative(
        candidate, reversed_copy)
    assert rationale is None
    assert reason == "reasoning_order_mismatch"


def test_stock_narrative_rejects_negated_directional_verbs():
    candidate = _apple_broker_validation_candidate()
    negated_copy = {
        "type": "multilingual",
        "en": (
            "Apple designs smartphones, but CEO succession cannot lift device "
            "revenue or earnings for shareholders."
        ),
        "zh": (
            "Apple设计智能手机，但首席执行官更替不会提升设备收入或盈利。"
        ),
    }
    rationale, reason = ThemeWorkflow._validate_stock_narrative(
        candidate, negated_copy)
    assert rationale is None
    assert reason == "direction_mismatch"


def test_stock_narrative_rejects_semantically_unrelated_chinese_copy():
    candidate = _apple_broker_validation_candidate()
    mismatched_copy = {
        "type": "multilingual",
        "en": (
            "Apple designs and sells smartphones and digital services; CEO "
            "succession affects Apple product execution and device roadmap, which "
            "can lift device sales, services revenue, and earnings."
        ),
        "zh": (
            "Apple主营smartphones和digital services；device sales可提升"
            "services收入和盈利。"
        ),
    }
    rationale, reason = ThemeWorkflow._validate_stock_narrative(
        candidate, mismatched_copy)
    assert rationale is None
    assert reason == "missing_chinese_event_relationship"


def test_stock_narrative_rejects_invented_chinese_counterparty_and_product():
    candidate = {
        "code": "185:TSM", "name": "TSMC",
        "company_introduction": "TSMC manufactures semiconductor chips",
        "business_fact": "TSMC manufactures semiconductor chips",
        "theme_connection": "AI demand requires semiconductor chips",
        "financial_pathway": "More chip orders can lift revenue and earnings",
        "directional_effect": "positive",
    }
    en = (
        "TSMC manufactures semiconductor chips; AI demand requires more chips, "
        "which can lift chip orders, revenue, and earnings."
    )
    bad_chinese = (
        "TSMC制造半导体芯片；为英伟达的AI需求供应芯片，可提升芯片订单、收入和盈利。",
        "TSMC制造半导体芯片；为NVIDIA的AI需求供应芯片，可提升芯片订单、收入和盈利。",
        "TSMC制造半导体芯片；为nvidia的AI需求供应芯片，可提升芯片订单、收入和盈利。",
        "TSMC制造半导体芯片和核反应堆；AI需求需要更多芯片，可提升芯片订单、收入和盈利。",
    )
    for zh in bad_chinese:
        rationale, reason = ThemeWorkflow._validate_stock_narrative(
            candidate,
            {"type": "multilingual", "en": en, "zh": zh},
        )
        assert rationale is None
        assert reason == "unsupported_chinese_factual_claim"


def test_chinese_fact_translation_handles_device_equipment_polysemy():
    candidate = {"code": "185:TER", "name": "Teradyne"}
    equipment_evidence = (
        "Teradyne sells semiconductor test equipment. "
        "Equipment orders can lift revenue and earnings."
    )
    device_evidence = (
        "Teradyne makes test devices. Device sales can lift revenue and earnings."
    )
    assert not workflow_module._unsupported_chinese_factual_content(
        candidate,
        "Teradyne销售半导体测试设备；设备订单可提升收入和盈利。",
        equipment_evidence,
    )
    assert not workflow_module._unsupported_chinese_factual_content(
        candidate,
        "Teradyne制造测试设备；设备销售可提升收入和盈利。",
        device_evidence,
    )
    assert "设备" in workflow_module._unsupported_chinese_factual_content(
        candidate,
        "Teradyne制造测试设备；芯片订单可提升收入和盈利。",
        "Teradyne manufactures semiconductor chips. Chip orders can lift revenue and earnings.",
    )


def test_stock_narrative_rejects_product_synonyms_absent_from_evidence():
    candidate = {
        "code": "185:TSM", "name": "TSMC",
        "company_introduction": "TSMC manufactures semiconductor chips",
        "business_fact": "TSMC manufactures semiconductor chips",
        "theme_connection": "AI demand requires semiconductor chips",
        "financial_pathway": "More chip orders can lift revenue and earnings",
        "directional_effect": "positive",
    }
    rationale, reason = ThemeWorkflow._validate_stock_narrative(
        candidate,
        {
            "type": "multilingual",
            "en": (
                "TSMC manufactures semiconductor chips and modems; AI demand "
                "requires more chips, which can lift chip orders, revenue, and earnings."
            ),
            "zh": (
                "TSMC制造半导体芯片；人工智能需求需要更多芯片，可提升芯片订单、收入和盈利。"
            ),
        },
    )
    assert rationale is None
    assert reason == "unsupported_factual_claim"


def test_stock_narrative_rejects_wrong_chinese_issuer_despite_matching_concepts():
    candidate = {
        **_apple_broker_validation_candidate(),
        "name_zh": "苹果",
    }
    wrong_issuer_copy = {
        "type": "multilingual",
        "en": (
            "Apple designs smartphones and digital services; CEO succession shapes "
            "product execution, which can lift device sales, revenue, and earnings."
        ),
        "zh": (
            "谷歌设计智能手机并销售数字服务；首席执行官继任影响产品执行和设备路线图，"
            "可提升设备销售、服务收入和盈利。"
        ),
    }
    rationale, _ = ThemeWorkflow._validate_stock_narrative(
        candidate, wrong_issuer_copy)
    assert rationale is None


def test_stock_narrative_rejects_common_legal_token_as_english_identity():
    candidate = {
        "candidate_id": "S0002",
        "code": "185:TTD",
        "name": "The Trade Desk Inc",
        "name_zh": "萃弈",
        "company_introduction": (
            "The Trade Desk Inc operates a programmatic advertising platform"
        ),
        "business_fact": (
            "The Trade Desk Inc operates a programmatic advertising platform"
        ),
        "theme_connection": (
            "AI bidding automation increases advertiser adoption of its platform"
        ),
        "financial_pathway": (
            "Higher platform usage can lift revenue and earnings"
        ),
        "directional_effect": "positive",
    }
    common_token_copy = {
        "type": "multilingual",
        "en": (
            "The programmatic advertising platform supports media buying; AI bidding "
            "automation increases advertiser adoption, which can lift revenue and earnings."
        ),
        "zh": (
            "萃弈运营程序化广告平台；AI竞价自动化增加广告主采用率，可提升收入和盈利。"
        ),
    }
    rationale, reason = ThemeWorkflow._validate_stock_narrative(
        candidate, common_token_copy)
    assert rationale is None
    assert reason == "missing_company_identity"


def test_stock_narrative_rejects_soft_and_absolute_negations_in_both_languages():
    candidate = _apple_broker_validation_candidate()
    cases = (
        {
            "type": "multilingual",
            "en": (
                "Apple designs smartphones; CEO succession is unlikely to lift device "
                "sales, services revenue, or earnings."
            ),
            "zh": (
                "Apple设计智能手机；首席执行官继任可提升设备销售、服务收入和盈利。"
            ),
        },
        {
            "type": "multilingual",
            "en": (
                "Apple designs smartphones; CEO succession will never lift device "
                "sales, services revenue, or earnings."
            ),
            "zh": (
                "Apple设计智能手机；首席执行官继任可提升设备销售、服务收入和盈利。"
            ),
        },
        {
            "type": "multilingual",
            "en": (
                "Apple designs smartphones; CEO succession shapes product execution, "
                "which can lift device sales, services revenue, and earnings."
            ),
            "zh": (
                "Apple设计智能手机；首席执行官继任不太可能提升设备销售、服务收入和盈利。"
            ),
        },
        {
            "type": "multilingual",
            "en": (
                "Apple designs smartphones; CEO succession shapes product execution, "
                "which can lift device sales, services revenue, and earnings."
            ),
            "zh": (
                "Apple设计智能手机；首席执行官继任从未提升设备销售、服务收入和盈利。"
            ),
        },
    )
    for negated_copy in cases:
        rationale, reason = ThemeWorkflow._validate_stock_narrative(
            candidate, negated_copy)
        assert rationale is None, negated_copy
        assert reason == "direction_mismatch"


def test_stock_narrative_rejects_unsupported_numerical_financial_magnitude():
    candidate = _apple_broker_validation_candidate()
    unsupported_magnitude_copy = {
        "type": "multilingual",
        "en": (
            "Apple designs smartphones; CEO succession shapes product execution, "
            "which can lift device revenue and earnings by 500%."
        ),
        "zh": (
            "Apple设计智能手机；首席执行官继任影响产品执行，可提升设备收入和盈利500%。"
        ),
    }
    rationale, _ = ThemeWorkflow._validate_stock_narrative(
        candidate, unsupported_magnitude_copy)
    assert rationale is None


def test_worded_financial_magnitudes_are_detected():
    for value in (
        "several million dollars of revenue",
        "a few million dollars of orders",
        "dozens of millions in sales",
        "roughly one billion dollars of earnings",
        "one hundred million dollars of revenue",
        "约数千万元收入",
        "几亿美元订单",
    ):
        assert workflow_module._financial_magnitude_claims(value), value


def test_article_body_discovery_prompts_include_full_fetched_body():
    marker = "LateBodyIssuer manufactures advanced optical components"
    article = {"title": "Theme update", "text": "x" * 7000 + marker}

    class CapturingBodyLLM:
        def __init__(self):
            self.calls = []

        def chat_json(self, system, user, **kwargs):
            self.calls.append((system, user))
            assert marker in user
            if system == workflow_module.prompts.EVENT_ECOSYSTEM_SYS:
                return {"entities": []}
            return {
                "theme_cn": "测试主题",
                "theme_direction": "bullish",
                "title_lede_entities": [],
                "body_entities": [],
            }

    llm = CapturingBodyLLM()
    workflow = ThemeWorkflow(llm, FakeQuotes())
    profile = {"exact_theme": "Optical components", "theme_cn": "光学元件"}
    brief = workflow.event_brief(
        {
            "theme": "Optical components",
            "date": "2026-07-09",
            "url": "https://example.com/article",
        },
        article,
        profile,
    )
    workflow.event_ecosystem(
        {"theme": "Optical components"}, brief, profile, article)
    assert len(llm.calls) == 2


if __name__ == "__main__":
    fns = [v for k,v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} tests passed")
