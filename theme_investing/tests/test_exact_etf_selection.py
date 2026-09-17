"""Focused tests for exact-count ETF output selection."""

import copy
import itertools
import os
import sys

import pytest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etf_preselection import (  # noqa: E402
    MULTI_STOCK_BASKET_LANE,
    SINGLE_STOCK_LEVERAGED_LANE,
    compose_output_etfs,
    select_output_etfs,
)


def _candidate(
    code,
    *,
    eligible=False,
    score=0.0,
    name=None,
    security_class="CE",
    **extra,
):
    return {
        "code": code,
        "name": name or code.partition(":")[2],
        "security_class": security_class,
        "output_eligible": eligible,
        "unified_score": score if eligible else 0.0,
        "theme_evidence_score": score,
        **extra,
    }


def _basket(code, *, eligible, score, holdings, **extra):
    return _candidate(
        code,
        eligible=eligible,
        score=score,
        selection_lane=MULTI_STOCK_BASKET_LANE,
        full_holdings=[
            {"code": holding_code, "name": holding_name, "weight_pct": weight}
            for holding_code, holding_name, weight in holdings
        ],
        **extra,
    )


def test_exact_composer_prefers_eligible_then_allows_ce_strategy_fillers():
    eligible = _candidate(
        "185:CORE", eligible=True, score=0.10, name="Core Theme ETF",
    )
    inverse = _candidate(
        "185:INVERSE", score=0.90, name="Inverse Theme ETF",
        exclusion_reason="inverse/short equity baskets are not admitted",
    )
    leveraged = _candidate(
        "185:LEV", score=0.80, name="Theme 2x Leveraged ETF",
        exclusion_reason="leveraged multi-stock baskets are not admitted",
    )
    option_income = _candidate(
        "185:CALL", score=0.70, name="Theme Covered Call ETF",
        exclusion_reason="option-income strategy",
    )
    hedged = _candidate(
        "185:HEDGE", score=0.60, name="Theme Hedged ETF",
        exclusion_reason="alternative long/short strategy",
    )
    alternative = _candidate(
        "185:ALT", score=0.50, name="Theme Managed Futures ETF",
        exclusion_reason="alternative long/short strategy",
        static_theme_exposure=0.42,
    )
    etn = _candidate(
        "185:NOTE", eligible=True, score=1.0,
        name="Theme Covered Call ETN", etf_type="ETN",
    )

    pool = select_output_etfs([
        alternative, etn, option_income, leveraged, eligible, hedged, inverse,
    ])
    selected = compose_output_etfs(pool, 6)

    assert [row["code"] for row in selected] == [
        "185:CORE", "185:INVERSE", "185:LEV", "185:CALL", "185:HEDGE",
        "185:ALT",
    ]
    assert len(selected) == 6
    assert all(row["selection_tier"] == "ce_fallback" for row in selected[1:])
    assert all(
        row["selection_basis"] == "ce_fallback_diverse"
        for row in selected[1:]
    )
    assert inverse["static_theme_exposure"] == 0.0
    assert alternative["static_theme_exposure"] == 0.42
    assert etn not in pool
    assert etn["selection_basis"] == "explicit_etn_excluded"
    assert etn["dedupe_reason"] == "exchange-traded note"


def test_market_code_uniqueness_is_hard_and_case_insensitive():
    preferred = _candidate("185:DUP", eligible=True, score=0.20)
    duplicate = _candidate("185:dup", eligible=False, score=0.99)

    pool = select_output_etfs([duplicate, preferred])

    assert pool == [preferred]
    assert duplicate["dedupe_excluded"] is True
    assert duplicate["dedupe_reason"] == "duplicate_market_code"
    assert duplicate["selection_basis"] == "duplicate_market_code_excluded"


def test_overlap_is_deferred_until_needed_for_the_exact_count():
    leader = _basket(
        "185:LEADER", eligible=True, score=0.90,
        holdings=[("185:A", "Alpha", 95), ("185:B", "Beta", 5)],
    )
    overlap = _basket(
        "185:OVERLAP", eligible=True, score=0.80,
        holdings=[("185:A", "Alpha", 96), ("185:B", "Beta", 4)],
    )
    distinct_fallback = _basket(
        "185:RESERVE", eligible=False, score=0.70,
        holdings=[("185:C", "Gamma", 60), ("185:D", "Delta", 40)],
    )

    pool = select_output_etfs([overlap, distinct_fallback, leader])

    assert [row["code"] for row in pool] == [
        "185:LEADER", "185:RESERVE", "185:OVERLAP",
    ]
    assert [row["code"] for row in compose_output_etfs(pool, 2)] == [
        "185:LEADER", "185:RESERVE",
    ]
    assert [row["code"] for row in compose_output_etfs(pool, 3)] == [
        "185:LEADER", "185:RESERVE", "185:OVERLAP",
    ]
    assert overlap["selection_tier"] == "eligible"
    assert overlap["selection_basis"] == "eligible_overlap_relaxed"
    assert overlap["overlap_relaxed"] is True
    assert overlap["dedupe_excluded"] is False
    assert overlap["dedupe_reason"] == "weighted_portfolio_overlap"


def test_composer_returns_short_only_when_admissible_input_is_exhausted():
    eligible = _candidate("185:ONLY", eligible=True, score=0.80)
    unknown_class = _candidate(
        "185:UNKNOWN", eligible=False, score=0.90, security_class="",
    )
    etn = _candidate(
        "185:ETNROW", eligible=False, score=1.0, etf_type="ETN",
    )

    selected = compose_output_etfs([etn, unknown_class, eligible], 4)

    assert selected == [eligible]


def test_invalid_market_code_is_never_admitted_even_when_marked_eligible():
    invalid = _candidate("NOT-A-MARKET-CODE", eligible=True, score=1.0)
    valid = _candidate("185:VALID", eligible=False, score=0.0)

    assert compose_output_etfs([invalid, valid], 2) == [valid]
    assert invalid["dedupe_excluded"] is True
    assert invalid["selection_basis"] == "invalid_market_code"
    assert invalid["dedupe_reason"] == "invalid_market_code"


def test_market_codes_are_canonicalised_before_freeze_and_dedupe():
    first = _candidate(" 185:abc ", eligible=False, security_class="CE")
    duplicate = _candidate("185:ABC", eligible=False, security_class="CE")

    selected = compose_output_etfs(select_output_etfs([first, duplicate]), 1)

    assert [row["code"] for row in selected] == ["185:ABC"]
    assert duplicate["dedupe_reason"] == "duplicate_market_code"


@pytest.mark.parametrize("etn_type", (
    "Note", "Exchange_Traded_Note", "Exchange\u2011Traded\u2011Note",
))
def test_structured_etn_type_variants_are_excluded(etn_type):
    note = _candidate("185:NOTE", eligible=False, security_class="CE")
    note["etf_type"] = etn_type

    assert select_output_etfs([note]) == []
    assert note["selection_basis"] == "explicit_etn_excluded"


@pytest.mark.parametrize(("evidence", "preferred"), [
    pytest.param({}, False, id="missing"),
    pytest.param({"static_theme_exposure": None}, False, id="null"),
    pytest.param({"static_theme_exposure": float("nan")}, False, id="nan"),
    pytest.param({"static_theme_exposure": float("inf")}, False, id="infinity"),
    pytest.param({"static_theme_exposure": -float("inf")}, False, id="negative-infinity"),
    pytest.param({"static_theme_exposure": "unavailable"}, False, id="invalid"),
    pytest.param({"static_theme_exposure": 0.374999}, False, id="below-threshold"),
    pytest.param({"static_theme_exposure": 0.375}, True, id="at-threshold"),
    pytest.param({"static_theme_exposure": 0.375001}, True, id="above-threshold"),
])
def test_evidence_preference_requires_current_finite_static_exposure(evidence, preferred):
    anchor = _candidate(
        "185:ANCHOR", eligible=True, score=0.10, static_theme_exposure=0.375,
    )
    provisional_leader = _candidate(
        "185:PROVISIONAL", eligible=True, score=0.99,
        base_theme_exposure=1.0, ai_relevance=5.0, preselect_score=1.0,
        **evidence,
    )

    selected = compose_output_etfs([provisional_leader, anchor], 1)

    assert selected == [provisional_leader if preferred else anchor]


def test_static_evidence_cannot_qualify_an_ineligible_ce_fallback():
    eligible = _candidate(
        "185:QUALIFIED", eligible=True, score=0.10, static_theme_exposure=0.375,
    )
    ineligible = _candidate(
        "185:INELIGIBLE", eligible=False, score=0.99, static_theme_exposure=0.99,
    )

    assert compose_output_etfs([ineligible, eligible], 1) == [eligible]


def test_evidence_preference_precedes_market_code_deduplication():
    qualified = _candidate(
        "185:DUP", eligible=True, score=0.10, static_theme_exposure=0.375,
    )
    weak = _candidate(
        "185:dup", eligible=True, score=0.99, static_theme_exposure=0.374999,
    )

    assert select_output_etfs([weak, qualified]) == [qualified]
    assert weak["dedupe_reason"] == "duplicate_market_code"
    assert weak["dedupe_excluded"] is True


def test_evidence_preference_precedes_economic_overlap_classification():
    holdings = [("185:A", "Alpha", 95), ("185:B", "Beta", 5)]
    qualified = _basket(
        "185:QUALIFIED", eligible=True, score=0.10,
        static_theme_exposure=0.375, holdings=holdings,
    )
    weak = _basket(
        "185:WEAK", eligible=True, score=0.99,
        static_theme_exposure=0.10, holdings=holdings,
    )

    assert select_output_etfs([weak, qualified]) == [qualified, weak]
    assert qualified["overlap_relaxed"] is False
    assert weak["overlap_relaxed"] is True
    assert weak["dedupe_reason"] == "weighted_portfolio_overlap"


def test_qualified_overlap_reserve_precedes_all_weak_diverse_rows():
    leader = _basket(
        "185:LEADER", eligible=True, score=0.90,
        static_theme_exposure=0.60,
        holdings=[("185:A", "Alpha", 95), ("185:B", "Beta", 5)],
    )
    overlap = _basket(
        "185:OVERLAP", eligible=True, score=0.80,
        static_theme_exposure=0.50,
        holdings=[("185:A", "Alpha", 96), ("185:B", "Beta", 4)],
    )
    weak_eligible = _basket(
        "185:WEAK", eligible=True, score=0.99,
        static_theme_exposure=0.20,
        holdings=[("185:C", "Gamma", 60), ("185:D", "Delta", 40)],
    )
    fallback = _candidate("185:FALLBACK", score=0.70)
    pool = select_output_etfs([fallback, weak_eligible, overlap, leader])

    assert pool == [leader, overlap, weak_eligible, fallback]
    assert compose_output_etfs(pool, 2) == [leader, overlap]
    assert overlap["overlap_relaxed"] is True
    assert overlap["dedupe_excluded"] is False


@pytest.mark.parametrize("weak_lane", [
    SINGLE_STOCK_LEVERAGED_LANE, MULTI_STOCK_BASKET_LANE,
])
def test_weak_lane_reservation_cannot_displace_qualified_products(weak_lane):
    strong = [
        _candidate(
            f"185:STRONG{index}", eligible=True, score=0.8 - index * 0.1,
            static_theme_exposure=0.375, selection_lane="direct_asset",
        )
        for index in range(2)
    ]
    weak = _candidate(
        "185:WEAK", eligible=True, score=0.99,
        static_theme_exposure=0.10, selection_lane=weak_lane,
        underlying_code="185:UNDERLYING",
    )

    assert compose_output_etfs([weak, *strong], 1) == strong[:1]
    assert compose_output_etfs([weak, *strong], 2) == strong


@pytest.mark.parametrize("static_exposure", [0.20, 0.375])
def test_wrapper_and_basket_reservations_remain_within_each_evidence_band(static_exposure):
    def product(code, score, lane):
        return _candidate(
            code, eligible=True, score=score,
            static_theme_exposure=static_exposure, selection_lane=lane,
        )

    leader = product("185:LEADER", 0.95, "direct_asset")
    runner_up = product("185:RUNNERUP", 0.90, "direct_asset")
    wrapper = product("185:WRAPPER", 0.30, SINGLE_STOCK_LEVERAGED_LANE)
    basket = product("185:BASKET", 0.20, MULTI_STOCK_BASKET_LANE)
    pool = [runner_up, basket, leader, wrapper]

    assert compose_output_etfs(pool, 1) == [wrapper]
    assert compose_output_etfs(pool, 2) == [wrapper, basket]
    assert compose_output_etfs(pool, 3) == [wrapper, leader, basket]
    assert compose_output_etfs(pool, 4) == [wrapper, leader, runner_up, basket]


def test_each_evidence_band_composes_only_its_remaining_capacity():
    strong_direct = _candidate(
        "185:STRONG", eligible=True, score=0.90,
        static_theme_exposure=0.50, selection_lane="direct_asset",
    )
    strong_basket = _candidate(
        "185:STRONGBASKET", eligible=True, score=0.10,
        static_theme_exposure=0.375, selection_lane=MULTI_STOCK_BASKET_LANE,
    )
    weak_direct = _candidate(
        "185:WEAKDIRECT", eligible=True, score=0.99,
        static_theme_exposure=0.10, selection_lane="direct_asset",
    )
    weak_wrapper = _candidate(
        "185:WEAKWRAPPER", eligible=True, score=0.90,
        static_theme_exposure=0.10, selection_lane=SINGLE_STOCK_LEVERAGED_LANE,
    )
    weak_basket = _candidate(
        "185:WEAKBASKET", eligible=True, score=0.10,
        static_theme_exposure=0.10, selection_lane=MULTI_STOCK_BASKET_LANE,
    )
    pool = [weak_direct, weak_wrapper, strong_direct, weak_basket, strong_basket]

    assert compose_output_etfs(pool, 3) == [
        strong_direct, strong_basket, weak_wrapper,
    ]
    assert compose_output_etfs(pool, 4) == [
        strong_direct, strong_basket, weak_wrapper, weak_basket,
    ]
    assert compose_output_etfs(pool, 5) == [
        strong_direct, strong_basket, weak_wrapper, weak_direct, weak_basket,
    ]


def test_direct_and_select_then_compose_are_deterministic_for_custom_targets():
    candidates = [
        _candidate("185:WEAK", eligible=True, score=0.99, static_theme_exposure=0.20),
        _candidate("185:STRONG", eligible=True, score=0.10, static_theme_exposure=0.375),
        _candidate("185:FALLBACK", score=0.80),
    ]
    expected = ["185:STRONG", "185:WEAK", "185:FALLBACK"]

    for permutation in itertools.permutations(candidates):
        for target in (0, 1, 2, 3, 7):
            direct = compose_output_etfs(copy.deepcopy(list(permutation)), target)
            selected = compose_output_etfs(
                select_output_etfs(copy.deepcopy(list(permutation))), target,
            )
            assert [row["code"] for row in direct] == expected[:target]
            assert selected == direct

    assert select_output_etfs(copy.deepcopy(candidates), limit=0) == []
    assert [row["code"] for row in select_output_etfs(
        copy.deepcopy(candidates), limit=2,
    )] == expected[:2]
