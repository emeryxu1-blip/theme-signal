"""Focused regression tests for exact two-phase stock selection/narration."""

import json
import os
import re
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import prompts
import workflow as workflow_module
from workflow import SelectionUniverseError, ThemeWorkflow


class _NoopQuotes:
    pass


class _ScoreWorkflow(ThemeWorkflow):
    def __init__(self, scores):
        super().__init__(object(), _NoopQuotes())
        self.scores = scores

    def score_relevance(self, brief, rows, article=None):
        return self.scores


def _score(*, status="scored", public=1.0, theme=1.0,
           effect="none", business="", connection="", pathway=""):
    return {
        "relevance_status": status,
        "theme_relevance": theme,
        "ai_relevance": theme,
        "article_support": 0.0,
        "confidence": 0.8,
        "exposure_type": "direct" if theme > 1 else "unclear",
        "impact_channel": "revenue_demand" if theme > 1 else "none",
        "theme_specificity": "company_specific" if theme > 1 else "none",
        "materiality": "high" if theme > 1 else "unknown",
        "evidence_strength": "derived" if theme > 1 else "none",
        "reason": connection,
        "public_relation_score": public,
        "public_relation_confidence": 0.8,
        "relation_type": "direct" if effect != "none" else "none",
        "directional_effect": effect,
        "business_fact": business,
        "theme_connection": connection,
        "financial_pathway": pathway,
        "evidence_basis": "derived" if business else "none",
    }


def test_tiered_selection_fills_exact_target_and_dedupes_issuer(monkeypatch):
    rows = [
        {"code": "185:PUBA", "name": "Public A Class A", "issuer_id": "public-a", "rank": 1},
        {"code": "185:PUBB", "name": "Public A Class B", "issuer_id": "public-a", "rank": 2},
        {"code": "185:T2", "name": "Theme", "rank": 3},
        {"code": "185:T3", "name": "Directional", "rank": 4},
        {"code": "185:T4", "name": "Scored", "rank": 5},
        {"code": "185:T5", "name": "Taxonomy", "rank": 6,
         "candidate_provenance": ["theme_taxonomy"]},
        {"code": "185:T6", "name": "Broad", "rank": 7,
         "candidate_lane": "broad_liquidity"},
    ]
    scores = {
        "185:PUBA": _score(public=4.5, theme=4.0, effect="positive", business="chips"),
        "185:PUBB": _score(public=4.9, theme=4.0, effect="positive", business="chips"),
        "185:T2": _score(public=1.0, theme=4.4),
        "185:T3": _score(public=1.0, theme=1.0, effect="positive", business="servers"),
        "185:T4": _score(public=2.0, theme=2.0),
        "185:T5": _score(status="missing"),
        "185:T6": _score(status="missing"),
    }
    monkeypatch.setattr(
        workflow_module, "_is_public_relation_eligible",
        lambda candidate, **kwargs: candidate["code"].startswith("185:PUB"),
    )
    monkeypatch.setattr(
        workflow_module, "_is_semantically_eligible",
        lambda candidate, **kwargs: candidate["code"] == "185:T2",
    )
    workflow = _ScoreWorkflow(scores)
    chosen = workflow.screen_until_target(
        rows, {"theme_direction": "bullish"}, {}, 6, "stocks",
    )

    assert [row["code"] for row in chosen] == [
        "185:PUBB", "185:T2", "185:T3", "185:T4", "185:T5", "185:T6",
    ]
    assert [row["stock_selection_tier"] for row in chosen] == [1, 2, 3, 4, 5, 6]
    assert workflow._last_stock_membership_codes == tuple(row["code"] for row in chosen)
    assert all(row["stock_membership_frozen"] for row in chosen)


def test_selection_universe_error_reports_real_unique_availability(monkeypatch):
    rows = [
        {"code": "185:A", "name": "Same Class A", "issuer_id": "same"},
        {"code": "185:B", "name": "Same Class B", "issuer_id": "same"},
    ]
    scores = {row["code"]: _score(status="missing") for row in rows}
    monkeypatch.setattr(workflow_module, "_is_public_relation_eligible", lambda *a, **k: False)
    monkeypatch.setattr(workflow_module, "_is_semantically_eligible", lambda *a, **k: False)

    with pytest.raises(SelectionUniverseError) as caught:
        _ScoreWorkflow(scores).screen_until_target(
            rows, {"theme_direction": "bullish"}, {}, 2, "stocks",
        )
    assert caught.value.asset_class == "stock"
    assert caught.value.expected == 2
    assert caught.value.available == 1


def test_finalizer_retries_same_codes_then_falls_back_without_substitution():
    class _FailingNarrator:
        def __init__(self):
            self.calls = []

        def chat_json(self, system, user, **kwargs):
            records = json.loads(user.split("Records (JSON):\n", 1)[1].split(
                "\n\nWriting requirements:", 1,
            )[0])
            self.calls.append([record["market_code"] for record in records])
            raise RuntimeError("narrator unavailable")

    chosen = [{
        "candidate_id": f"S{index:04d}",
        "code": code,
        "name": name,
        "business_fact": business,
        "theme_connection": connection,
        "financial_pathway": pathway,
    } for index, (code, name, business, connection, pathway) in enumerate((
        ("185:A", "Alpha", "Alpha makes memory chips", "AI servers need memory chips",
         "Higher orders can lift revenue and earnings"),
        ("185:B", "Beta", "Beta builds cooling systems", "AI racks need liquid cooling",
         "Rack deployments can lift orders and margins"),
    ), 1)]
    llm = _FailingNarrator()
    workflow = ThemeWorkflow(llm, _NoopQuotes())
    workflow._last_stock_membership_codes = ("185:A", "185:B")
    workflow._last_stock_public_reserves = [{"code": "185:C", "name": "Reserve"}]

    returned, narratives = workflow.finalize_stock_rationales(
        {"theme": "AI infrastructure"}, {"theme_direction": "bullish"}, chosen, 2,
    )

    assert returned is chosen
    assert [row["code"] for row in returned] == ["185:A", "185:B"]
    assert list(narratives) == ["185:A", "185:B"]
    assert llm.calls == [["185:A", "185:B"], ["185:A", "185:B"]]
    alpha = narratives["185:A"]["theme_rationale"]
    assert alpha["en"].index("memory chips") < alpha["en"].index("AI servers")
    assert alpha["en"].index("AI servers") < alpha["en"].index("revenue")
    assert any("\u4e00" <= char <= "\u9fff" for char in alpha["zh"])


def test_assembly_never_raises_for_missing_stock_copy_and_uses_rank_scale():
    chosen = [{
        "code": f"185:S{index}", "name": f"Stock {index}",
        "industry": "Cloud infrastructure", "_fallback_theme": "AI buildout",
        "stock_selection_rank": index,
    } for index in range(1, 6)]
    workflow = ThemeWorkflow(object(), _NoopQuotes())
    output = workflow._assemble(chosen, {}, "2026-09-03", "bullish")

    assert [item["market_code"] for item in output] == [row["code"] for row in chosen]
    assert [item["Theme exposure"] for item in output] == [5.0, 4.5, 4.0, 3.5, 3.0]
    assert all(item["theme_rationale"]["en"] for item in output)
    assert all(item["theme_rationale"]["zh"] for item in output)

    singleton = [{"code": "185:ONE", "name": "One", "industry": "Software"}]
    workflow._assign_theme_exposure(singleton)
    assert singleton[0]["theme_exposure"] == 5.0


def test_stock_prompt_declares_frozen_membership_and_forbids_substitution():
    combined = prompts.NARRATIVE_SYS + prompts.NARRATIVE_USER
    assert "membership and order are already frozen" in combined
    assert "never substitute another" in combined
    assert "reject and replace" not in combined
    assert 'empty "en" and "zh"' not in combined


def test_etf_identifier_mismatch_retries_same_code_then_assembles_fallback():
    class _WrongIdentityNarrator:
        def __init__(self):
            self.calls = []

        def chat_json(self, system, user, **kwargs):
            records = json.loads(user.split("Records (JSON):\n", 1)[1].split(
                "\n\nWriting requirements:", 1,
            )[0])
            self.calls.append([record["market_code"] for record in records])
            return {"items": [{
                "candidate_id": "WRONG-ID",
                "market_code": records[0]["market_code"],
                "theme_rationale": {
                    "type": "multilingual",
                    "en": "The fund tracks a broad portfolio whose gains can lift net asset value.",
                    "zh": "该基金跟踪广泛组合，底层上涨可推动基金净值走高。",
                },
            }]}

    llm = _WrongIdentityNarrator()
    workflow = ThemeWorkflow(llm, _NoopQuotes())
    chosen = [{
        "code": "185:ETF1", "name": "Example ETF",
        "security_class": "CE", "static_theme_exposure": 0.0,
        "fund_category": "Broad equities",
    }]

    narratives = workflow.narrate(
        {"theme": "AI infrastructure"},
        {"theme_direction": "bullish"},
        chosen,
        "ETF",
    )
    assembled = workflow._assemble(
        chosen, narratives, "2026-09-03", "bullish",
        theme="AI infrastructure",
    )

    assert narratives == {}
    assert llm.calls == [["185:ETF1"], ["185:ETF1"]]
    assert [row["market_code"] for row in assembled] == ["185:ETF1"]
    assert assembled[0]["theme_rationale"]["en"]


def test_eight_weak_stock_fallbacks_are_distinct_business_led_broker_cases():
    business_facts = (
        "builds launch vehicles and spacecraft",
        "provides online advertising and cloud services",
        "develops GPUs and accelerated-computing platforms",
        "sells smartphones, computers, tablets, and services",
        "provides software and cloud platforms",
        "operates retail and cloud-computing businesses",
        "manufactures semiconductors and provides wafer fabrication",
        "develops semiconductors and infrastructure software",
    )
    rationales = []
    for rank, business in enumerate(business_facts, 1):
        rationale = ThemeWorkflow._fallback_theme_rationale({
            "code": f"185:S{rank}",
            "name": f"Issuer {rank}",
            "company_introduction": business,
            "stock_selection_tier": 6,
            "stock_selection_rank": rank,
            # Low-tier diagnostics must never leak back into narration.
            "theme_connection": "not summit operations and weak screening evidence",
            "financial_pathway": "available disclosures do not quantify sensitivity",
        }, "bullish", theme="G20 AI Summit", rank=rank)
        rationales.append(rationale)

    english = [rationale["en"] for rationale in rationales]
    tails = [re.sub(r"^Issuer \d+ \(S\d+\): [^;]+; ", "", text) for text in english]
    assert len(set(tails)) == 8
    assert all("G20 AI Summit" in text for text in english)
    assert all(any(term in text for term in (
        "revenue", "sales", "orders", "margins", "earnings", "profits",
    )) for text in english)
    assert all("weak screening" not in text for text in english)
    assert all("available disclosures" not in text for text in english)
    assert all(any("\u4e00" <= char <= "\u9fff" for char in rationale["zh"])
               for rationale in rationales)
    assert all("核心业务为" not in rationale["zh"] for rationale in rationales)


def test_event_brief_and_faq_provider_failures_are_nonfatal():
    class _UnavailableLLM:
        def chat_json(self, *args, **kwargs):
            raise OSError("provider unavailable")

    workflow = ThemeWorkflow(_UnavailableLLM(), _NoopQuotes())
    profile = {
        "exact_theme": "AI infrastructure",
        "theme_cn": "人工智能基础设施",
        "canonical_name": "AI infrastructure",
        "canonical_definition": "",
        "aliases": [],
        "direct_business_models": [],
        "pure_play_descriptors": [],
        "enablers": [],
        "exclusions": [],
        "core_entities": [],
    }
    brief = workflow.event_brief(
        {
            "theme": "AI infrastructure", "date": "2026-09-03",
            "url": "https://example.test/article",
        },
        {"title": "", "text": "", "url": "https://example.test/article"},
        profile,
    )

    assert brief["theme_cn"] == "人工智能基础设施"
    assert brief["theme_direction"] == "bullish"
    assert brief["input_theme"] == "AI infrastructure"
    assert brief["title_lede_entities"] == []
    assert brief["body_entities"] == []
    assert workflow.faq(
        {"theme": "AI infrastructure", "date": "2026-09-03"},
        brief, [], [],
    ) == []


@pytest.mark.parametrize("boilerplate", (
    "The fund has no direct summit operating role.",
    "The fund is not tied to summit operations.",
    "This supports earnings rather than summit-related revenue.",
    "The basket acts without creating direct event-service exposure.",
    "该基金并不参与峰会运营。",
    "该基金不会形成直接的峰会服务业务敞口。",
    "这将支持盈利，而非带来峰会相关收入。",
))
def test_event_operations_disclaimers_are_rejected_as_boilerplate(boilerplate):
    assert ThemeWorkflow._validated_rationale_text(boilerplate) is None


def test_bearish_weak_fallback_is_directional_and_sanitises_source_claims():
    candidate = {
        "code": "185:ALPHA",
        "name": "Alpha",
        "business_fact": "ships 500 AI servers to Meta",
        "company_introduction": "builds data-center systems",
        "theme_connection": "candidate was selected by relevance_score",
        "financial_pathway": "收入和盈利将增长",
        "directional_effect": "positive",
        "stock_selection_tier": 2,
        "stock_selection_rank": 1,
    }

    rationale = ThemeWorkflow._fallback_theme_rationale(
        candidate, "bearish", theme="AI spending downturn", rank=1,
    )

    assert "500" not in rationale["en"]
    assert "Meta" not in rationale["en"]
    assert "candidate" not in rationale["en"].lower()
    assert "relevance_score" not in rationale["en"]
    assert re.search(r"pressure|weigh|reduce|weaker|compress", rationale["en"])
    assert re.search(r"压低|拖累|减少|承压|压缩|削弱", rationale["zh"])
    assert "增长" not in rationale["zh"]


def test_direct_finalizer_rejects_duplicate_or_invalid_frozen_codes():
    workflow = ThemeWorkflow(object(), _NoopQuotes())
    duplicates = [
        {"code": "185:A", "name": "Alpha"},
        {"code": "185:A", "name": "Alpha duplicate"},
    ]
    with pytest.raises(SelectionUniverseError) as caught:
        workflow.finalize_stock_rationales(
            {"theme": "AI"}, {"theme_direction": "bullish"}, duplicates, 2,
        )
    assert caught.value.available == 1

    with pytest.raises(SelectionUniverseError):
        ThemeWorkflow(object(), _NoopQuotes()).finalize_stock_rationales(
            {"theme": "AI"}, {"theme_direction": "bullish"},
            [{"code": "", "name": "Missing code"}], 1,
        )


def test_zero_target_short_circuits_before_scoring():
    class NeverScore(_ScoreWorkflow):
        def score_relevance(self, *args, **kwargs):
            raise AssertionError("zero target must not score")

    assert NeverScore({}).screen_until_target(
        iter([{"code": "185:A", "name": "Alpha"}]),
        {"theme_direction": "bullish"}, {}, 0, "stocks",
    ) == []


def test_stock_universe_boundary_counts_unique_issuers_not_share_classes():
    class OfflineQuotes:
        cfg = type("Config", (), {"scene": "test"})()

        def iter_ranked(self, *args, **kwargs):
            raise RuntimeError("live unavailable")

        def security_codes(self, security_type):
            assert security_type == "ES"
            return ["185:SAMEA", "185:SAMEB", "185:OTHER"]

        def security_profiles(self, codes):
            profiles = {
                "185:SAMEA": {"name": "Same Class A", "issuer_id": "same"},
                "185:SAMEB": {"name": "Same Class B", "issuer_id": "same"},
                "185:OTHER": {"name": "Other Corp", "issuer_id": "other"},
            }
            return {code: profiles[code] for code in codes}

        def name_of(self, code):
            raise RuntimeError("name endpoint unavailable")

    workflow = ThemeWorkflow(
        object(), OfflineQuotes(), {
            "stock_target": 2,
            "stock_candidate_budget": 2,
            "stock_universe": 2,
            "max_scan": 2,
            "stock_broad_lane": 2,
        },
    )
    candidates = workflow.stock_candidates({}, {}, None, [])

    assert [row["code"] for row in candidates] == ["185:SAMEA", "185:OTHER"]


@pytest.mark.parametrize("mechanics", (
    "Alpha's membership was frozen before narration.",
    "Alpha was included in the final basket.",
    "Alpha was picked for the selected list.",
    "Alpha was shortlisted for the final portfolio.",
    "Alpha made the final cut.",
    "Alpha belongs in the final basket.",
    "Alpha is one of our picks.",
    "该股被纳入最终组合。",
    "该股是最终候选股并成功上榜。",
))
def test_membership_mechanics_are_rejected_from_public_copy(mechanics):
    assert ThemeWorkflow._validated_rationale_text(mechanics) is None


def test_cjk_sources_do_not_leak_into_english_deterministic_fallback():
    rationale = ThemeWorkflow._fallback_theme_rationale({
        "code": "185:X", "name": "X Corp",
        "business_fact": "生产芯片",
        "theme_connection": "人工智能需要芯片",
        "financial_pathway": "需求增加将提升收入和盈利",
        "stock_selection_tier": 2,
        "directional_effect": "negative",
    }, "bearish", theme="人工智能", rank=1)

    assert not re.search(r"[\u4e00-\u9fff]", rationale["en"])
    assert "半导体" in rationale["zh"]
    assert re.search(r"pressure|weaker|reduce|weigh", rationale["en"])
    assert re.search(r"压低|拖累|承压|削弱", rationale["zh"])


@pytest.mark.parametrize("mechanics", (
    "The required investment direction is positive.",
    "Its relationship type is supplier.",
    "The directional effect is negative.",
    "The issuer passed the public relationship gate.",
    "该股关系类型为供应商，所需投资方向为正向。",
))
def test_narrative_record_labels_are_rejected_from_public_copy(mechanics):
    assert ThemeWorkflow._validated_rationale_text(mechanics) is None


def test_large_custom_target_batches_primary_and_targeted_retry_requests():
    class UnavailableNarrator:
        def __init__(self):
            self.calls = []
            self.candidate_ids = []

        def chat_json(self, system, user, **kwargs):
            records = json.loads(user.split("Records (JSON):\n", 1)[1].split(
                "\n\nWriting requirements:", 1,
            )[0])
            self.calls.append([record["market_code"] for record in records])
            self.candidate_ids.append([
                record["candidate_id"] for record in records
            ])
            raise RuntimeError("service unavailable")

    candidates = [{
        "code": f"185:S{index}", "name": f"Issuer {index}",
    } for index in range(1, 46)]
    llm = UnavailableNarrator()
    workflow = ThemeWorkflow(llm, _NoopQuotes(), {"relevance_batch": 100})

    assert workflow.narrate(
        {"theme": "AI"}, {"theme_direction": "bullish"},
        candidates, "stock",
    ) == {}
    assert [len(batch) for batch in llm.calls] == [20, 20, 5, 20, 20, 5]
    flattened = [code for batch in llm.calls for code in batch]
    assert all(flattened.count(candidate["code"]) == 2 for candidate in candidates)
    primary_ids = [value for batch in llm.candidate_ids[:3] for value in batch]
    retry_ids = [value for batch in llm.candidate_ids[3:] for value in batch]
    assert len(primary_ids) == len(set(primary_ids)) == 45
    assert retry_ids == primary_ids


def test_bilingual_fallback_uses_one_business_source_not_conflicting_fields():
    rationale = ThemeWorkflow._fallback_theme_rationale({
        "code": "185:X", "name": "X Corp",
        "business_fact": "生产半导体芯片",
        "company_introduction": "sells footwear",
        "stock_selection_tier": 6,
    }, "bullish", theme="人工智能", rank=1)

    assert "footwear" not in rationale["en"].lower()
    assert "semiconductor" in rationale["en"].lower()
    assert "半导体" in rationale["zh"]
    assert not re.search(r"[\u4e00-\u9fff]", rationale["en"])


def test_stock_narrative_rejects_different_supported_business_by_language():
    candidate = {
        "code": "185:AAPL", "name": "Apple",
        "business_fact": "Apple designs smartphones and operates digital services",
        "company_introduction": (
            "Apple designs smartphones and operates digital services"
        ),
        "theme_connection": (
            "CEO succession affects product execution and device roadmap"
        ),
        "financial_pathway": "Device sales can lift revenue and earnings",
        "directional_effect": "positive",
    }
    mismatched = {
        "type": "multilingual",
        "en": (
            "Apple designs smartphones; CEO succession affects product execution "
            "and device roadmap, which can lift device sales, revenue, and earnings."
        ),
        "zh": (
            "Apple主营digital services；CEO succession影响product execution和"
            "device roadmap；device sales可提升收入和盈利。"
        ),
    }

    rationale, reason = ThemeWorkflow._validate_stock_narrative(
        candidate, mismatched, "bullish",
    )

    assert rationale is None
    assert reason == "bilingual_business_mismatch"


def test_stock_narrative_accepts_specific_business_with_generic_translation():
    candidate = {
        "code": "185:CLD", "name": "CloudCo",
        "business_fact": "CloudCo provides cloud services and advertising",
        "company_introduction": "CloudCo provides cloud services and advertising",
        "theme_connection": "AI adoption increases cloud demand",
        "financial_pathway": "Cloud demand can lift revenue and earnings",
        "directional_effect": "positive",
    }
    natural_pair = {
        "type": "multilingual",
        "en": (
            "CloudCo provides cloud services; AI adoption increases cloud demand, "
            "which can lift revenue and earnings."
        ),
        "zh": "CloudCo提供云业务；人工智能采用提升云需求，可增加收入和盈利。",
    }

    rationale, reason = ThemeWorkflow._validate_stock_narrative(
        candidate, natural_pair, "bullish",
    )

    assert rationale == natural_pair
    assert reason == "accepted"


def test_unknown_cjk_business_uses_symmetric_neutral_fallback():
    rationale = ThemeWorkflow._fallback_theme_rationale({
        "code": "185:X", "name": "X Corp",
        "business_fact": "制造光刻胶",
        "company_introduction": "sells footwear",
        "stock_selection_tier": 6,
    }, "bullish", theme="AI", rank=1)

    assert "footwear" not in rationale["en"].lower()
    assert "operates its core business" in rationale["en"]
    assert "主营其核心业务" in rationale["zh"]
    assert "光刻胶" not in rationale["zh"]


def test_fallback_skips_party_redaction_remnant_for_real_industry():
    rationale = ThemeWorkflow._fallback_theme_rationale({
        "code": "185:X", "name": "X Corp",
        "business_fact": "operates counterparties",
        "company_introduction": "X Corp",
        "industry": "Consumer Electronics",
        "stock_selection_tier": 6,
    }, "bullish", theme="AI", rank=1)

    assert "counterparties" not in rationale["en"].lower()
    assert "consumer electronics" in rationale["en"].lower()


def test_cjk_company_profile_is_condensed_to_broker_business_clause():
    rationale = ThemeWorkflow._fallback_theme_rationale({
        "code": "185:NVDA", "name": "Nvidia",
        "business_fact": (
            "该公司于多年前在加利福尼亚州注册成立。该公司的主营业务"
            "包括GPU、处理器、软件、硬件系统和技术平台。"
        ),
        "stock_selection_tier": 6,
    }, "bullish", theme="AI", rank=1)

    assert "注册成立" not in rationale["zh"]
    assert "硬件" in rationale["zh"]
    assert "semiconductor" in rationale["en"].lower()
    assert len(rationale["zh"]) < 150


def test_opposite_cjk_pathway_is_replaced_by_directional_same_code_fallback():
    rationale = ThemeWorkflow._fallback_theme_rationale({
        "code": "185:X", "name": "X Corp",
        "business_fact": "makes semiconductor chips",
        "theme_connection": "AI demand can increase chip orders",
        "financial_pathway": "收入下降并压缩利润率",
        "directional_effect": "positive",
        "stock_selection_tier": 1,
    }, "bullish", theme="AI infrastructure", rank=1)

    assert re.search(r"support|lift|increase|improve", rationale["en"])
    assert re.search(r"支撑|提升|增加|改善", rationale["zh"])
    assert "收入下降" not in rationale["zh"]


def test_ordinary_etf_rejects_wrong_stance_and_unsupported_named_holding():
    candidate = {
        "code": "185:GROW", "name": "Growth ETF",
        "static_theme_exposure": 0.5,
        "matched_holdings": [
            {"code": "185:NVDA", "ticker": "NVDA", "name": "NVIDIA"},
        ],
    }
    wrong_stance = {
        "type": "multilingual",
        "en": "Falling demand can reduce the fund NAV for Growth ETF.",
        "zh": "需求下降会拖累基金净值，影响Growth ETF。",
    }
    invented_holding = {
        "type": "multilingual",
        "en": "The fund holds TSLA, whose gains can lift the portfolio NAV.",
        "zh": "该基金持有TSLA，其上涨可提升组合净值。",
    }

    assert ThemeWorkflow._validated_candidate_rationale(
        candidate, wrong_stance, "bullish",
    ) is None
    assert ThemeWorkflow._validated_candidate_rationale(
        candidate, invented_holding, "bullish",
    ) is None


def test_bearish_long_leveraged_fallback_keeps_mandate_and_downside_stance():
    candidate = {
        "code": "185:UP", "name": "2x Long Market ETF",
        "security_class": "CE", "static_theme_exposure": 0.0,
        "direction": "Long", "leverage": 2,
        "benchmark_code": "185:SPY",
        "bearish_downside_exposure": True,
    }

    rationale = ThemeWorkflow._fallback_theme_rationale(candidate, "bearish")

    assert "positive daily return" in rationale["en"]
    assert re.search(r"loss|reduce|decline", rationale["en"])
    assert re.search(r"损失|压低|下跌", rationale["zh"])
    assert ThemeWorkflow._validated_candidate_rationale(
        candidate, rationale, "bearish",
    ) == rationale


def test_live_stock_without_name_uses_ticker_without_losing_the_code():
    class Quotes:
        def iter_ranked(self, *args, **kwargs):
            yield {"code": "185:AAA", "rank": 1, "values": {"mktcap": 1}}

        def name_of(self, code):
            raise RuntimeError("name endpoint unavailable")

        def security_codes(self, security_type):
            return []

    (row,) = list(ThemeWorkflow(object(), Quotes()).stock_source())
    assert row["code"] == "185:AAA"
    assert row["name"] == "AAA"
