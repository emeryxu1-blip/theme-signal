"""Ordered quote concurrency preserves ETF discovery and evidence semantics."""

from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import sys
import threading
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from etf_preselection import _discover, _enrich, preselect_etfs


def _stock(code, *, public=True):
    return {
        "code": code, "name": code.partition(":")[2],
        "is_public_theme_stock": public, "ai_relevance": 4.5,
        "relevance_status": "scored", "exposure_type": "direct",
        "confidence": 0.9, "impact_channel": "revenue_demand",
        "theme_specificity": "company_specific", "materiality": "high",
        "evidence_strength": "explicit", "reason": "Direct operating exposure",
    }


def _metadata(code, *, underlying=None):
    return {
        "name": code.partition(":")[2], "security_class": "CE",
        "aum": 100_000_000, "turnover": 2_000_000, "volume": 50_000,
        "expense_ratio": 0.3, "direction": "Long",
        "leverage": 2 if underlying else 1, "asset_class": "Equity",
        "benchmark_code": underlying, "fund_strategy": "Growth",
    }


class Quotes:
    names = {}

    def __init__(self, *, failures=(), recover=False):
        self.failures = set(failures)
        self.recover = recover
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.requests = []
        self.finished = []
        self.metadata_requests = []
        self.live_requests = 0

    @contextmanager
    def request(self, key):
        with self.lock:
            self.requests.append(key)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            # Make completion order differ from submission order with >1 worker.
            time.sleep(0.025 if key[-1] == "185:A" else 0.003)
            if key in self.failures:
                raise RuntimeError("fixture unavailable")
            yield
        finally:
            with self.lock:
                self.finished.append(key)
                self.active -= 1

    @staticmethod
    def name_of(code):
        return code.partition(":")[2]

    def iter_stock_etfs(self, code, relation="related", *, page_size=1000):
        assert page_size == (100 if relation == "related" else 1000)
        with self.request((relation, code)):
            if relation == "related":
                yield {"code": "185:SHARED", "holding_weight": 12,
                       **_metadata("185:SHARED")}
                own = f"185:ETF_{code.partition(':')[2]}"
                yield {"code": own, "holding_weight": 30, **_metadata(own)}
            elif not self.recover:
                wrapper = f"185:WRAP_{code.partition(':')[2]}"
                yield {"code": wrapper, "holding_weight": None,
                       "stock_relation_kind": relation,
                       **_metadata(wrapper, underlying=code)}

    def iter_prompt_etfs(self, prompt_id, *, page_size=100):
        yield {"code": "185:POOL", **_metadata("185:POOL")}

    def live_etf_codes(self, *, page_size=1000):
        self.live_requests += 1
        return ["185:WRAP_A", "185:SHARED"]

    def etf_metadata(self, codes):
        self.metadata_requests.append(list(codes))
        return {
            code: _metadata(
                code, underlying=f"185:{code.rsplit('_', 1)[1]}"
                if "WRAP_" in code else None,
            )
            for code in codes
        }

    def etf_holding_weights_for_stock(self, codes, stock_code):
        with self.request(("weights", stock_code)):
            return {code: 15 + index for index, code in enumerate(codes)}


def _run(quotes, workers):
    return preselect_etfs(
        quotes,
        [_stock("185:A"), _stock("185:B"), _stock("185:C", public=False)],
        theme="semiconductors", brief={}, article_title="", limit=8,
        quote_workers=workers,
    )


def test_parallel_discovery_and_weights_preserve_complete_result_and_source_order():
    serial_quotes, parallel_quotes = Quotes(), Quotes()
    serial = _run(serial_quotes, 1)
    parallel = _run(parallel_quotes, 4)

    assert parallel == serial
    assert serial_quotes.max_active == 1
    assert 1 < parallel_quotes.max_active <= 4
    assert sorted(parallel_quotes.requests) == sorted(serial_quotes.requests)
    assert parallel_quotes.finished != serial_quotes.finished
    assert parallel_quotes.metadata_requests == serial_quotes.metadata_requests
    assert not any(relation in {"leveraged", "long"} and code == "185:C"
                   for relation, code in parallel_quotes.requests)


def test_parallel_failures_keep_diagnostics_and_successful_evidence_in_source_order():
    failures = {("related", "185:B"), ("long", "185:A"),
                ("weights", "185:A"), ("weights", "185:C")}
    serial = _run(Quotes(failures=failures), 1)
    parallel = _run(Quotes(failures=failures), 4)
    assert parallel == serial
    errors = [item for item in parallel.failures if "fixture unavailable" in item]
    assert errors == [
        "stock-long:185:A: fixture unavailable",
        "stock:185:B: fixture unavailable",
        "holding-weight:185:A: fixture unavailable",
        "holding-weight:185:C: fixture unavailable",
    ]


@pytest.mark.parametrize("workers", [1, 4])
def test_recovery_metadata_is_reused_only_inside_one_preselection(workers):
    quotes = Quotes(recover=True)
    result = _run(quotes, workers)
    assert quotes.metadata_requests[0] == ["185:WRAP_A", "185:SHARED"]
    assert set(quotes.metadata_requests[1]) == {
        "185:POOL", "185:ETF_A", "185:ETF_B", "185:ETF_C",
    }
    assert len(quotes.metadata_requests) == 2

    repeated = _run(quotes, workers)
    assert repeated == result
    assert quotes.live_requests == 2
    assert quotes.metadata_requests[2:] == quotes.metadata_requests[:2]


def test_enrichment_fetches_only_missing_metadata_and_keeps_cached_success_on_failure():
    class MissingQuotes(Quotes):
        def etf_metadata(self, codes):
            self.metadata_requests.append(list(codes))
            raise RuntimeError("metadata unavailable")

    quotes = MissingQuotes()
    candidates = {
        code: {"code": code, "anchor_weights": {}}
        for code in ("185:CACHED", "185:MISSING")
    }
    cache = {"185:CACHED": _metadata("185:CACHED")}
    before = deepcopy(cache)
    failures = []
    _enrich(quotes, candidates, [], failures, quote_workers=4, metadata_cache=cache)
    assert quotes.metadata_requests == [["185:MISSING"]]
    assert candidates["185:CACHED"]["security_class"] == "CE"
    assert "security_class" not in candidates["185:MISSING"]
    assert failures == ["metadata: metadata unavailable"]
    assert cache == before


def test_failed_recovery_metadata_is_not_cached_and_enrichment_can_retry():
    class RetryQuotes(Quotes):
        def etf_metadata(self, codes):
            if not self.metadata_requests:
                self.metadata_requests.append(list(codes))
                raise RuntimeError("first snapshot unavailable")
            return super().etf_metadata(codes)

    quotes = RetryQuotes(recover=True)
    stocks, failures, cache = [_stock("185:A")], [], {}
    candidates, _ = _discover(
        quotes, stocks, [], 2, failures, quote_workers=4, metadata_cache=cache,
    )
    assert cache == {}
    assert "direct-recovery-metadata: first snapshot unavailable" in failures
    _enrich(
        quotes, candidates, stocks, failures, quote_workers=4, metadata_cache=cache,
    )
    assert quotes.metadata_requests == [
        ["185:WRAP_A", "185:SHARED"], ["185:SHARED", "185:ETF_A"],
    ]
    assert set(cache) == {"185:SHARED", "185:ETF_A"}


def test_fully_cached_enrichment_does_not_issue_an_empty_metadata_request():
    quotes = Quotes()
    candidates = {"185:A": {"code": "185:A", "anchor_weights": {}}}
    _enrich(
        quotes, candidates, [], [], quote_workers=4,
        metadata_cache={"185:A": _metadata("185:A")},
    )
    assert quotes.metadata_requests == []
    assert candidates["185:A"]["security_class"] == "CE"


@pytest.mark.parametrize("empty_row", [
    None, {}, {"name": "Fallback name"},
    {"name": "Fallback name", "aum": None, "direction": ""},
])
def test_empty_recovery_metadata_does_not_mask_enrichment_retry(empty_row):
    class SparseRecoveryQuotes(Quotes):
        def etf_metadata(self, codes):
            rows = super().etf_metadata(codes)
            if len(self.metadata_requests) == 1:
                rows["185:SHARED"] = deepcopy(empty_row)
            return rows

    quotes = SparseRecoveryQuotes(recover=True)
    _run(quotes, 4)
    assert "185:SHARED" in quotes.metadata_requests[1]
    assert "185:WRAP_A" not in quotes.metadata_requests[1]


@pytest.mark.parametrize("empty_row", [None, {}, {"name": "Fallback name"}])
def test_unusable_provided_cache_rows_do_not_mask_enrichment_retry(empty_row):
    quotes = Quotes()
    candidates = {"185:A": {"code": "185:A", "anchor_weights": {}}}
    cache = {"185:A": empty_row}
    _enrich(quotes, candidates, [], [], quote_workers=4, metadata_cache=cache)
    assert quotes.metadata_requests == [["185:A"]]
    assert cache["185:A"]["security_class"] == "CE"


@pytest.mark.parametrize("row", [None, {"name": "Current name"}])
def test_current_name_only_metadata_is_applied_but_not_cached(row):
    class NameQuotes(Quotes):
        def etf_metadata(self, codes):
            self.metadata_requests.append(list(codes))
            return {code: deepcopy(row) for code in codes}

    quotes = NameQuotes()
    candidates = {
        "185:A": {"code": "185:A", "name": "Original name", "anchor_weights": {}},
    }
    cache = {}
    for _ in range(2):
        _enrich(quotes, candidates, [], [], quote_workers=4, metadata_cache=cache)
    assert quotes.metadata_requests == [["185:A"], ["185:A"]]
    assert candidates["185:A"]["name"] == (
        "Current name" if row is not None else "Original name")
    assert cache == {}


def test_zero_metadata_values_are_cacheable_and_preserved():
    class ZeroQuotes(Quotes):
        def etf_metadata(self, codes):
            self.metadata_requests.append(list(codes))
            return {code: {"name": "Zero turnover", "turnover": 0} for code in codes}

    quotes = ZeroQuotes()
    candidates = {"185:A": {"code": "185:A", "anchor_weights": {}}}
    cache = {}
    for _ in range(2):
        _enrich(quotes, candidates, [], [], quote_workers=4, metadata_cache=cache)
    assert quotes.metadata_requests == [["185:A"]]
    assert candidates["185:A"]["turnover"] == 0
    assert cache["185:A"]["turnover"] == 0


@pytest.mark.parametrize("workers", [0, -1, True, False, 1.5, "4", None])
@pytest.mark.parametrize("has_sources", [False, True])
def test_invalid_quote_workers_fail_at_public_entry_before_quote_requests(workers, has_sources):
    quotes = Quotes()
    with pytest.raises(ValueError, match="quote_workers must be a positive integer"):
        preselect_etfs(
            quotes, [_stock("185:A")] if has_sources else [],
            theme="semiconductors" if has_sources else "uncatalogued event",
            brief={}, article_title="", limit=5, quote_workers=workers,
        )
    assert quotes.requests == []
    assert quotes.metadata_requests == []
    assert quotes.live_requests == 0


@pytest.mark.parametrize("workers", [1, 4])
def test_discovery_keeps_per_relation_scan_caps_and_additive_wrappers(workers):
    class BoundedQuotes(Quotes):
        def __init__(self):
            super().__init__()
            self.consumed = {}

        def iter_stock_etfs(self, code, relation="related", *, page_size=1000):
            limit = 100 if relation == "related" else 1000
            assert page_size == limit
            for index in range(limit + 1):
                self.consumed[relation] = index + 1
                if index == limit:
                    raise AssertionError("relation consumed beyond its scan cap")
                if relation == "related":
                    row_code = f"185:BASKET{index}"
                    yield {"code": row_code, "holding_weight": 1,
                           **_metadata(row_code)}
                elif index == limit - 1:
                    yield {"code": "185:WRAP_A", "holding_weight": None,
                           **_metadata("185:WRAP_A", underlying=code)}
                else:
                    yield {"code": f"185:UNRELATED{index}",
                           **_metadata(f"185:UNRELATED{index}")}

    quotes = BoundedQuotes()
    failures, sources = [], {}
    candidates, _ = _discover(
        quotes, [_stock("185:A")], [], 2, failures,
        quote_workers=workers, source_counts=sources,
    )
    assert quotes.consumed == {"related": 100, "leveraged": 1000, "long": 1000}
    assert list(candidates) == ["185:WRAP_A", "185:BASKET0", "185:BASKET1"]
    assert sources["generic_stock"]["raw"] == 100
    assert sources["dedicated_direct"]["raw"] == 2000
    assert quotes.live_requests == 0


def test_legacy_generic_only_client_keeps_parallel_compatibility():
    class LegacyQuotes:
        names = {}

        @staticmethod
        def name_of(code):
            return code

        @staticmethod
        def iter_related_etfs(code, *, page_size=100):
            assert page_size == 100
            yield {"code": "185:WRAP_A", "holding_weight": None,
                   **_metadata("185:WRAP_A", underlying=code)}

    serial = _discover(LegacyQuotes(), [_stock("185:A")], [], 2, [], quote_workers=1)
    parallel = _discover(LegacyQuotes(), [_stock("185:A")], [], 2, [], quote_workers=4)
    assert parallel == serial
    assert list(parallel[0]) == ["185:WRAP_A"]
