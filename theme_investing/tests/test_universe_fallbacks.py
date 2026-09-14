"""Focused integration tests for exact-count ETF universe recovery."""

import os
import sys


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etf_preselection import compose_output_etfs, select_output_etfs  # noqa: E402
from universe_fallbacks import extend_etf_candidates  # noqa: E402


class RecoveryQuotes:
    def __init__(self, *, live, offline, names, metadata, live_error=None):
        self.live = list(live)
        self.offline = list(offline)
        self.names = dict(names)
        self.metadata = dict(metadata)
        self.live_error = live_error
        self.events = []
        self.metadata_codes = []

    def live_etf_codes(self):
        self.events.append("live")
        if self.live_error is not None:
            raise self.live_error
        return list(self.live)

    def security_codes(self, security_type):
        self.events.append(f"offline:{security_type}")
        assert security_type == "CE"
        return list(self.offline)

    def name_of(self, code):
        return self.names.get(code, code.partition(":")[2])

    def etf_metadata(self, codes):
        self.events.append("metadata")
        self.metadata_codes = list(codes)
        return {
            code: dict(self.metadata[code])
            for code in codes
            if code in self.metadata
        }


def _eligible(code="185:CORE"):
    return {
        "code": code,
        "name": "Strict Theme ETF",
        "security_class": "CE",
        "output_eligible": True,
        "unified_score": 0.80,
        "theme_evidence_score": 0.80,
        "static_theme_exposure": 0.80,
    }


def test_live_and_offline_ce_recovery_deduplicates_and_hydrates_metadata():
    existing = _eligible()
    quotes = RecoveryQuotes(
        live=["185:live", "185:dup", "185:CORE", "not-a-market-code"],
        offline=["185:DUP", "185:offline", "185:NOTE", "185:core"],
        names={
            "185:LIVE": "Solar Energy Leaders ETF",
            "185:DUP": "Solar Technology ETF",
            "185:OFFLINE": "Broad Market ETF",
            "185:NOTE": "Solar Income Product",
        },
        metadata={
            "185:LIVE": {
                "name": "Hydrated Solar Energy Leaders ETF",
                "security_class": "CE",
                "aum": "125000000",
                "fund_focus": "Solar power",
                "expense_ratio": 0.45,
            },
            "185:DUP": {
                "name": "Hydrated Solar Technology ETF",
                "security_class": "CE",
                "aum": 75_000_000,
            },
            "185:OFFLINE": {
                "name": "Hydrated Broad Market ETF",
                "security_class": "CE",
                "aum": 50_000_000,
            },
            "185:NOTE": {
                "name": "Hydrated Solar Income Note",
                "security_class": "CE",
                "etf_type": "ETN",
                "aum": 500_000_000,
            },
        },
    )

    candidates, failures = extend_etf_candidates(
        quotes,
        [existing],
        theme="solar energy",
        brief={"keywords": ["solar technology"]},
        target=4,
        universe_limit=20,
    )

    assert failures == ()
    assert quotes.events[:2] == ["live", "offline:CE"]
    assert quotes.events[-1] == "metadata"
    assert set(quotes.metadata_codes) == {
        "185:LIVE", "185:DUP", "185:OFFLINE", "185:NOTE",
        "185:CORE",
    }
    assert [row["code"] for row in candidates].count("185:DUP") == 1
    assert [row["code"] for row in candidates].count("185:CORE") == 1

    live = next(row for row in candidates if row["code"] == "185:LIVE")
    duplicate = next(row for row in candidates if row["code"] == "185:DUP")
    offline = next(row for row in candidates if row["code"] == "185:OFFLINE")
    note = next(row for row in candidates if row["code"] == "185:NOTE")
    assert live["name"] == "Hydrated Solar Energy Leaders ETF"
    assert live["fund_focus"] == "Solar power"
    assert live["expense_ratio"] == 0.45
    assert live["metric"] == 125_000_000.0
    assert duplicate["fallback_source"] == "live_ce"
    assert offline["fallback_source"] == "offline_ce"

    pool = select_output_etfs(candidates)
    selected = compose_output_etfs(pool, 4)

    assert len(selected) == 4
    assert selected[0] is existing
    assert note not in pool
    assert note["selection_basis"] == "explicit_etn_excluded"
    assert {row["code"] for row in selected} == {
        "185:CORE", "185:LIVE", "185:DUP", "185:OFFLINE",
    }


def test_theme_text_orders_recovery_rows_before_generic_source_priority():
    quotes = RecoveryQuotes(
        live=["185:LGEN", "185:LTHEME"],
        offline=["185:OTHEME", "185:OGEN"],
        names={
            "185:LGEN": "Live Generic Fund",
            "185:LTHEME": "Live Specialist Fund",
            "185:OTHEME": "Offline Specialist Fund",
            "185:OGEN": "Offline Generic Fund",
        },
        metadata={
            "185:LGEN": {
                "name": "Total Market ETF", "security_class": "CE",
                "aum": 5_000_000_000,
            },
            "185:LTHEME": {
                "name": "Solar Energy Transition ETF", "security_class": "CE",
                "aum": 50_000_000,
            },
            "185:OTHEME": {
                "name": "Photovoltaic Infrastructure ETF",
                "security_class": "CE", "aum": 25_000_000,
            },
            "185:OGEN": {
                "name": "Broad Allocation ETF", "security_class": "CE",
                "aum": 10_000_000_000,
            },
        },
    )

    candidates, failures = extend_etf_candidates(
        quotes,
        [],
        theme="solar energy",
        brief={"keywords": ["photovoltaic"]},
        target=4,
        universe_limit=10,
    )
    pool = select_output_etfs(candidates)
    selected = compose_output_etfs(pool, 4)

    assert failures == ()
    assert [row["code"] for row in candidates] == [
        "185:LGEN", "185:LTHEME", "185:OTHEME", "185:OGEN",
    ]
    assert [row["code"] for row in selected] == [
        "185:LTHEME", "185:OTHEME", "185:LGEN", "185:OGEN",
    ]
    assert selected[0]["fallback_theme_similarity"] > selected[1][
        "fallback_theme_similarity"
    ] > selected[2]["fallback_theme_similarity"]
    # Equal generic matches preserve live-before-offline source order even when
    # the offline fund has more AUM.
    assert selected[2]["fallback_source"] == "live_ce"
    assert selected[3]["fallback_source"] == "offline_ce"


def test_recovery_hydrates_an_existing_weak_code_instead_of_false_exhaustion():
    existing = {
        "code": "185:WEAK",
        "name": "Unclassified Solar Fund",
        "output_eligible": False,
        "static_theme_exposure": 0.0,
    }
    quotes = RecoveryQuotes(
        live=["185:WEAK"],
        offline=[],
        names={"185:WEAK": "Solar Recovery ETF"},
        metadata={
            "185:WEAK": {
                "name": "Hydrated Solar Recovery ETF",
                "security_class": "CE",
                "aum": 42_000_000,
            },
        },
    )

    candidates, failures = extend_etf_candidates(
        quotes,
        [existing],
        theme="solar energy",
        brief={},
        target=1,
        universe_limit=10,
    )
    selected = compose_output_etfs(select_output_etfs(candidates), 1)

    assert failures == ()
    assert candidates == [existing]
    assert existing["name"] == "Hydrated Solar Recovery ETF"
    assert existing["security_class"] == "CE"
    assert existing["metric"] == 42_000_000.0
    assert selected == [existing]


def test_genuinely_short_universe_stays_short_after_etn_and_duplicate_removal():
    quotes = RecoveryQuotes(
        live=[],
        offline=["185:ONLY", "185:only", "185:NOTE", "invalid"],
        names={
            "185:ONLY": "Only Available ETF",
            "185:NOTE": "Unavailable ETN",
        },
        metadata={
            "185:ONLY": {"security_class": "CE", "aum": 1_000_000},
            "185:NOTE": {"security_class": "CE", "etf_type": "ETN"},
        },
        live_error=RuntimeError("live endpoint unavailable"),
    )

    candidates, failures = extend_etf_candidates(
        quotes,
        [],
        theme="specialized theme",
        brief={},
        target=3,
        universe_limit=10,
    )
    pool = select_output_etfs(candidates)
    selected = compose_output_etfs(pool, 3)

    assert failures == ("live:live endpoint unavailable",)
    assert [row["code"] for row in candidates] == ["185:ONLY", "185:NOTE"]
    assert [row["code"] for row in pool] == ["185:ONLY"]
    assert [row["code"] for row in selected] == ["185:ONLY"]
    assert len(selected) < 3


def test_invalid_and_etn_rows_do_not_consume_valid_recovery_boundary():
    quotes = RecoveryQuotes(
        live=["bad code", "185:NOTE1", "185:NOTE2", "185:GOOD"],
        offline=[],
        names={
            "185:NOTE1": "First Exchange-Traded Note",
            "185:NOTE2": "Second Exchange\u2011Traded\u2011Note",
            "185:GOOD": "Actual Equity ETF",
        },
        metadata={
            "185:NOTE1": {"security_class": "CE", "etf_type": "ETN"},
            "185:NOTE2": {
                "security_class": "CE", "etf_type": "Exchange_Traded_Note",
            },
            "185:GOOD": {"security_class": "CE", "fund_focus": "Equity"},
        },
    )

    candidates, failures = extend_etf_candidates(
        quotes, [], theme="specialized allocation", brief={}, target=1,
        universe_limit=1,
    )
    selected = compose_output_etfs(select_output_etfs(candidates), 1)

    assert failures == ()
    assert [row["code"] for row in selected] == ["185:GOOD"]
    assert {row["code"] for row in candidates} == {
        "185:NOTE1", "185:NOTE2", "185:GOOD",
    }


def test_recovery_normalises_metadata_keys_before_etn_admission():
    class LowercaseMetadataQuotes(RecoveryQuotes):
        def etf_metadata(self, codes):
            self.events.append("metadata")
            self.metadata_codes = list(codes)
            return {
                "185:note": {
                    "security_class": "CE", "etf_type": "Note",
                },
                "185:good": {
                    "security_class": "CE", "name": "Valid ETF",
                },
            }

    quotes = LowercaseMetadataQuotes(
        live=["185:NOTE", "185:GOOD"], offline=[],
        names={"185:NOTE": "Income Product", "185:GOOD": "Valid ETF"},
        metadata={},
    )
    candidates, _ = extend_etf_candidates(
        quotes, [], theme="equity", brief={}, target=1, universe_limit=1,
    )

    assert [row["code"] for row in compose_output_etfs(
        select_output_etfs(candidates), 1,
    )] == ["185:GOOD"]


def test_recovery_rehydrates_existing_ce_row_and_excludes_stale_etn_label():
    stale = {
        "code": "185:NOTE", "name": "Income Product",
        "security_class": "CE", "output_eligible": True,
        "static_theme_exposure": 0.9,
    }
    quotes = RecoveryQuotes(
        live=["185:NOTE", "185:GOOD"], offline=[],
        names={"185:NOTE": "Income Product", "185:GOOD": "Valid ETF"},
        metadata={
            "185:NOTE": {
                "security_class": "CE", "etf_type": "Exchange-Traded Note",
            },
            "185:GOOD": {"security_class": "CE", "name": "Valid ETF"},
        },
    )

    candidates, _ = extend_etf_candidates(
        quotes, [stale], theme="allocation", brief={}, target=1,
        universe_limit=2,
    )
    selected = compose_output_etfs(select_output_etfs(candidates), 1)

    assert stale["etf_type"] == "Exchange-Traded Note"
    assert stale["selection_basis"] == "explicit_etn_excluded"
    assert [row["code"] for row in selected] == ["185:GOOD"]
