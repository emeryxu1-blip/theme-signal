"""Focused tests for the stock-only market-code resolver adapter."""

import os
import sys
import urllib.request
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from marketcode_resolver import MarketCodeResolver


def _row(
    market_code,
    ticker,
    name,
    *,
    security_type="ES",
    name_zh="",
):
    market = market_code.split(":", 1)[0]
    return {
        "key": market_code,
        "market": market,
        "code": ticker,
        "security_type": security_type,
        "security_name": name,
        "security_name_zh": name_zh,
        "search_names": [name, name_zh],
    }


def _synthetic_rows():
    return [
        _row("185:CRWV", "CRWV", "CoreWeave", name_zh="CoreWeave"),
        _row("185:NBIS", "NBIS", "Nebius Group", name_zh="Nebius 集团"),
        _row("169:ALFA", "ALFA", "Alpha Systems", name_zh="阿尔法系统"),
        _row("185:BETA", "BETA", "Beta Systems"),
        _row("185:QQQ", "QQQ", "Invesco QQQ Trust", security_type="CE"),
        _row("999:FOREIGN", "FOREIGN", "Foreign Ordinary", security_type="ES"),
    ]


def test_real_skill_references_resolve_neocloud_pure_plays_without_network():
    def unexpected_network(*args, **kwargs):
        raise AssertionError("market-code resolution must not refresh over the network")

    original_urlopen = urllib.request.urlopen
    try:
        urllib.request.urlopen = unexpected_network
        resolver = MarketCodeResolver()

        expected = {
            "CRWV": "185:CRWV",
            "CoreWeave": "185:CRWV",
            "NBIS": "185:NBIS",
            "Nebius": "185:NBIS",
            "Nebius Group": "185:NBIS",
        }
        for query, market_code in expected.items():
            resolution = resolver.resolve(query)
            assert resolution.resolved
            assert resolution.market_code == market_code

        assert resolver.resolve("A_SECURITY_THAT_DOES_NOT_EXIST").status == "not_found"
    finally:
        urllib.request.urlopen = original_urlopen


def test_skill_module_and_rows_are_loaded_once_per_resolver():
    calls = []
    fake_module = SimpleNamespace(
        load_rows=lambda: calls.append("load_rows") or _synthetic_rows()
    )

    def fake_loader(path):
        calls.append("load_module")
        return fake_module

    original_loader = MarketCodeResolver._load_skill_module
    try:
        MarketCodeResolver._load_skill_module = staticmethod(fake_loader)
        resolver = MarketCodeResolver(script_path="unused-by-test.py")
        assert resolver.resolve("CRWV").market_code == "185:CRWV"
        assert resolver.resolve("NBIS").market_code == "185:NBIS"
        assert calls == ["load_module", "load_rows"]
    finally:
        MarketCodeResolver._load_skill_module = staticmethod(original_loader)


def test_injected_rows_bypass_skill_loading():
    def unexpected_loader(path):
        raise AssertionError("injected rows must bypass skill loading")

    original_loader = MarketCodeResolver._load_skill_module
    try:
        MarketCodeResolver._load_skill_module = staticmethod(unexpected_loader)
        resolver = MarketCodeResolver(rows=_synthetic_rows())
        assert resolver.resolve("CRWV").market_code == "185:CRWV"
    finally:
        MarketCodeResolver._load_skill_module = staticmethod(original_loader)


def test_resolution_precedence_is_full_code_then_ticker_then_name():
    rows = [
        *_synthetic_rows(),
        _row("169:NEBIUS", "NEBIUS", "Unrelated Issuer"),
    ]
    resolver = MarketCodeResolver(rows=rows)

    by_code = resolver.resolve("185:nbis")
    by_ticker = resolver.resolve("NEBIUS")
    by_name = resolver.resolve("Nebius Group")

    assert (by_code.market_code, by_code.match_kind) == ("185:NBIS", "market_code")
    assert (by_ticker.market_code, by_ticker.match_kind) == ("169:NEBIUS", "ticker")
    assert (by_name.market_code, by_name.match_kind) == ("185:NBIS", "exact_name")


def test_exact_names_normalize_punctuation_spacing_and_chinese():
    resolver = MarketCodeResolver(rows=_synthetic_rows())

    assert resolver.resolve("Core Weave").market_code == "185:CRWV"
    assert resolver.resolve("NEBIUS   GROUP").market_code == "185:NBIS"
    assert resolver.resolve("阿尔法，系统").market_code == "169:ALFA"


def test_conservative_partial_name_requires_a_unique_distinctive_prefix():
    resolver = MarketCodeResolver(rows=_synthetic_rows())

    nebius = resolver.resolve("Nebius")
    assert nebius.market_code == "185:NBIS"
    assert nebius.match_kind == "partial_name"

    # Short and internal fragments must not become company identities.
    assert resolver.resolve("Core").status == "not_found"
    assert resolver.resolve("Systems").status == "not_found"


def test_partial_and_exact_ambiguity_fail_closed():
    rows = [
        _row("185:NBIA", "NBIA", "Nebius Alpha"),
        _row("169:NBIB", "NBIB", "Nebius Beta"),
        _row("169:ONE", "ONE", "Universal"),
        _row("185:TWO", "TWO", "Universal"),
    ]
    resolver = MarketCodeResolver(rows=rows)

    partial = resolver.resolve("Nebius")
    exact = resolver.resolve("Universal")
    assert partial.status == "ambiguous"
    assert partial.candidates == ("169:NBIB", "185:NBIA")
    assert exact.status == "ambiguous"
    assert exact.candidates == ("169:ONE", "185:TWO")


def test_ticker_ambiguity_can_be_disambiguated_by_market_hint():
    resolver = MarketCodeResolver(
        rows=[
            _row("169:DUP", "DUP", "Duplicate NYSE"),
            _row("185:DUP", "DUP", "Duplicate Nasdaq"),
        ]
    )

    assert resolver.resolve("DUP").status == "ambiguous"
    assert resolver.resolve("DUP", market_hint="nyse").market_code == "169:DUP"
    assert resolver.resolve("DUP", market_hint="nasdaq").market_code == "185:DUP"


def test_non_stock_and_unsupported_market_rows_are_rejected():
    resolver = MarketCodeResolver(rows=_synthetic_rows())

    for query in ("QQQ", "185:QQQ", "Invesco QQQ Trust", "FOREIGN", "999:FOREIGN"):
        resolution = resolver.resolve(query)
        assert resolution.status == "not_stock"
        assert not resolution.resolved


def test_structured_entity_rejects_conflicts_and_invalid_explicit_codes():
    resolver = MarketCodeResolver(rows=_synthetic_rows())

    conflict = resolver.resolve_entity(ticker="ALFA", name="Beta Systems")
    assert conflict.status == "conflict"
    assert conflict.candidates == ("169:ALFA", "185:BETA")

    invalid_code = resolver.resolve_entity(
        market_code="185:MISSING", ticker="CRWV", name="CoreWeave"
    )
    assert invalid_code.status == "not_found"
    assert not invalid_code.resolved


def test_structured_entity_can_use_one_identifier_to_disambiguate_another():
    resolver = MarketCodeResolver(
        rows=[
            _row("169:ONE", "ONE", "Universal"),
            _row("185:TWO", "TWO", "Universal"),
        ]
    )

    resolution = resolver.resolve_entity(ticker="TWO", name="Universal")
    assert resolution.resolved
    assert resolution.market_code == "185:TWO"
    assert resolution.match_kind == "ticker"


if __name__ == "__main__":
    tests = sorted(
        (name, value)
        for name, value in globals().items()
        if name.startswith("test_") and callable(value)
    )
    for _, test in tests:
        test()
    print(f"{len(tests)} market-code resolver tests passed")
