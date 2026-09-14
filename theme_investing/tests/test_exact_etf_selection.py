"""Focused tests for exact-count ETF output selection."""

import os
import sys

import pytest


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etf_preselection import (  # noqa: E402
    MULTI_STOCK_BASKET_LANE,
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
