"""Offline equivalence and recovery contracts for concurrent ETF assessment."""

from __future__ import annotations

import copy
import json
import os
import sys
import threading
from collections import Counter
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from etf_preselection import compose_output_etfs
from llm_client import LLMClient, LLMRequestError
from test_llm_client import completion, make_config
from test_workflow import _candidate, _component_evidence, _component_score
from workflow import ThemeWorkflow


def _rows(count):
    return [{
        "code": f"185:C{index:02d}",
        "name": f"Consumer Company {index:02d}",
        "_max_portfolio_weight_pct": float(index + 1),
        "_aggregate_portfolio_weight_pct": float(100 - index),
    } for index in range(count)]


def _prompt_rows(user):
    rows = []
    for line in user.splitlines():
        parts = [part.strip() for part in line.split("|")]
        if len(parts) >= 3 and ":" in parts[0]:
            rows.append(parts[:3])
    return rows


def _valid_response(rows):
    return {"results": [
        _component_score(code, candidate_id)
        for code, candidate_id, _name in rows
    ]}


class RecordingLLM:
    """Record complete payloads and force overlap without timing assertions."""

    def __init__(self, *, workers=1, barrier=False, response=None):
        self.calls = []
        self.completed = []
        self.active = 0
        self.max_active = 0
        self.lock = threading.Lock()
        self.barrier = threading.Barrier(workers, timeout=5) if barrier else None
        self.workers = workers
        self.first_released = threading.Event()
        self.response = response

    def chat_json(self, system, user, **kwargs):
        rows = _prompt_rows(user)
        with self.lock:
            self.calls.append((system, user, copy.deepcopy(kwargs)))
            call_number = len(self.calls)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.barrier and call_number <= self.workers:
                self.barrier.wait()
                if call_number == 1:
                    assert self.first_released.wait(5), "No other batch completed"
            result = (
                self.response(rows, call_number)
                if self.response else _valid_response(rows)
            )
            with self.lock:
                self.completed.append(rows[0][0])
                if call_number != 1:
                    self.first_released.set()
            return result
        finally:
            with self.lock:
                self.active -= 1


def _workflow(llm, *, workers, quotes=None, **options):
    workflow = ThemeWorkflow(llm, quotes or object(), {
        "etf_llm_workers": workers,
        "etf_quote_workers": workers,
        **options,
    })
    workflow._exact_theme = "American Consumer"
    workflow._theme_profile = {
        "exact_theme": "American Consumer",
        "canonical_definition": "Businesses exposed to United States household demand",
        "exclusions": ["broad market exposure alone"],
    }
    return workflow


def _sorted_payloads(llm):
    return sorted(llm.calls, key=lambda call: _prompt_rows(call[1])[0][0])


def test_parallel_holding_batches_preserve_serial_scores_and_exact_payloads():
    serial = RecordingLLM()
    parallel = RecordingLLM(workers=4, barrier=True)
    results = []
    for workers, llm in ((1, serial), (4, parallel)):
        results.append(_workflow(
            llm, workers=workers, etf_holding_relevance_batch=2,
        ).score_etf_holding_relevance(
            {"theme_direction": "bearish"}, _rows(16),
            {"title": "Unrelated bond news", "text": "Article is optional evidence"},
        ))

    assert results[0] == results[1]
    assert all(row["relevance_status"] == "scored" for row in results[1].values())
    assert _sorted_payloads(serial) == _sorted_payloads(parallel)
    assert len(parallel.calls) == 8
    assert serial.max_active == 1
    assert parallel.max_active == 4
    assert parallel.completed.index("185:C00") > 0
    assert all(call[2]["max_tokens"] == 8000 for call in parallel.calls)


class PortfolioQuotes:
    def __init__(self, portfolios, *, workers):
        self.portfolios = portfolios
        self.workers = workers
        self.barrier = threading.Barrier(workers, timeout=5) if workers > 1 else None
        self.first_released = threading.Event()
        self.lock = threading.Lock()
        self.requested = []
        self.completed = []
        self.active = 0
        self.max_active = 0

    def iter_etf_holdings(self, code, *, page_size=1000):
        with self.lock:
            self.requested.append(code)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            if self.barrier:
                self.barrier.wait()
                if code == "185:A":
                    assert self.first_released.wait(5), "No other portfolio completed"
            yield from copy.deepcopy(self.portfolios[code])
            with self.lock:
                self.completed.append(code)
                if code != "185:A":
                    self.first_released.set()
        finally:
            with self.lock:
                self.active -= 1

    def security_profiles(self, codes):
        return {}


def test_parallel_portfolios_keep_budget_admission_names_and_shared_seed_scores():
    def holding(code, name, weight):
        return {"code": code, "name": name, "weight_pct": weight}

    portfolios = {
        "185:A": [holding("185:SHARED", "Primary Consumer Name", 40),
                  holding("185:POSITIVE", "Known Consumer", 30),
                  holding("185:NEGATIVE", "Unrelated Business", 30)],
        "185:B": [holding("185:SHARED", "Later Consumer Name", 40),
                  holding("185:BONLY", "Second Consumer", 60)],
        # This entire portfolio must be skipped at the four-company boundary.
        "185:C": [holding("185:SHARED", "Third Consumer Name", 40),
                  holding("185:CONLY", "New Consumer", 60)],
        # An overlapping portfolio can still fit after the preceding skip.
        "185:D": [holding("185:SHARED", "Last Consumer Name", 50),
                  holding("185:POSITIVE", "Known Consumer", 50)],
    }
    seeds = [
        {"code": "185:POSITIVE", **_component_evidence("185:POSITIVE")},
        {"code": "185:NEGATIVE", **_component_evidence(
            "185:NEGATIVE", relevance=1, exposure_type="unclear")},
    ]
    results, llms, quote_clients = [], [], []
    for workers in (1, 4):
        quotes = PortfolioQuotes(portfolios, workers=workers)
        llm = RecordingLLM()
        candidates = [_candidate(f"185:{code}", 0.9 - index / 10)
                      for index, code in enumerate("ABCD")]
        for candidate in candidates:
            candidate["security_class"] = "CE"
        _workflow(
            llm, workers=workers, quotes=quotes, etf_target=3,
            etf_holdings_unique_budget=4, etf_holdings_portfolio_budget=4,
            etf_holding_relevance_batch=1,
        ).rerank_etfs_from_components(
            candidates, {}, {}, "bullish", stock_scores=copy.deepcopy(seeds),
        )
        results.append(candidates)
        llms.append(llm)
        quote_clients.append(quotes)

    assert results[0] == results[1]
    assert compose_output_etfs(copy.deepcopy(results[0]), 3) == compose_output_etfs(
        copy.deepcopy(results[1]), 3)
    assert _sorted_payloads(llms[0]) == _sorted_payloads(llms[1])
    scored_rows = [row for call in llms[1].calls for row in _prompt_rows(call[1])]
    assert Counter(row[0] for row in scored_rows) == {"185:SHARED": 1, "185:BONLY": 1}
    assert next(row[2] for row in scored_rows if row[0] == "185:SHARED") == "Primary Consumer Name"
    by_code = {candidate["code"]: candidate for candidate in results[1]}
    assert by_code["185:C"]["ranking_mode"] == "holdings_unavailable"
    assert by_code["185:C"]["output_eligible"] is False
    assert by_code["185:D"]["ranking_mode"] == "full_holdings"
    negative = next(row for row in by_code["185:A"]["full_holdings"]
                    if row["code"] == "185:NEGATIVE")
    assert negative["relevance_status"] == "scored"
    assert negative["ai_relevance"] == 1
    assert negative["theme_eligible"] is False
    assert quote_clients[0].max_active == 1
    assert quote_clients[1].max_active == 4
    assert quote_clients[1].completed.index("185:A") > 0
    assert sorted(quote_clients[1].requested) == list(portfolios)


@pytest.mark.parametrize("workers", (1, 4))
def test_profiles_skip_valid_seeds_but_refresh_invalid_evidence_without_changing_issuer_names(workers):
    original_names = {
        "185:ACMEA": "Acme Holdings Class A",
        "185:UNRELATED": "Unrelated Company",
        "185:ACMEC": "Acme Holdings Class C",
        "185:ACMEB": "Acme Holdings Class B",
    }
    hydrated_names = {
        "185:ACMEC": "Fresh Profile For Invalid Evidence",
        "185:ACMEB": "Fresh Profile For Unknown Evidence",
    }

    class Quotes:
        def __init__(self):
            self.profile_calls = []

        def iter_etf_holdings(self, code, *, page_size=1000):
            for (component, name), weight in zip(original_names.items(), (40, 10, 25, 25)):
                yield {"code": component, "name": name, "weight_pct": weight}

        def security_profiles(self, codes):
            self.profile_calls.append(list(codes))
            return {code: {
                "name": hydrated_names[code],
                "company_introduction": "Makes products for American consumers",
                "sector": "Consumer Staples",
                "industry": "Household Products",
            } for code in codes}

    seeds = [
        {"code": "185:ACMEA", **_component_evidence("185:ACMEA")},
        {"code": "185:UNRELATED", **_component_evidence(
            "185:UNRELATED", relevance=1, exposure_type="unclear")},
        {"code": "185:ACMEC", **_component_evidence("185:ACMEC"),
         "confidence": float("nan")},
    ]
    quotes, llm = Quotes(), RecordingLLM()
    candidate = _candidate("185:FUND", 0.9)
    _workflow(
        llm, workers=workers, quotes=quotes,
        etf_holdings_portfolio_budget=1, etf_holding_relevance_batch=1,
    ).rerank_etfs_from_components(
        [candidate], {}, {}, "bullish", stock_scores=seeds,
    )

    assert quotes.profile_calls == [["185:ACMEC", "185:ACMEB"]]
    scored_rows = [row for call in llm.calls for row in _prompt_rows(call[1])]
    assert {row[0]: row[2] for row in scored_rows} == hydrated_names
    holdings = {row["code"]: row for row in candidate["full_holdings"]}
    assert {code: row["name"] for code, row in holdings.items()} == original_names
    assert all(row["relevance_status"] == "scored" for row in holdings.values())
    assert holdings["185:ACMEC"]["confidence"] == 0.95
    assert holdings["185:UNRELATED"]["ai_relevance"] == 1
    assert holdings["185:UNRELATED"]["theme_eligible"] is False
    assert candidate["theme_evidence_score"] >= 0.25
    assert candidate["holding_theme_breadth"] == 1
    assert candidate["output_eligible"] is False
    assert candidate["eligibility_rejection_reasons"] == ["relevant_issuer_breadth_below_2"]


def test_parallel_single_row_batches_keep_global_ticker_ambiguity():
    def ticker_only(rows, _call_number):
        response = _valid_response(rows)
        for row in response["results"]:
            row.pop("candidate_id")
            row["market_code"] = row["market_code"].partition(":")[2]
        return response

    llm = RecordingLLM(response=ticker_only)
    scores = _workflow(
        llm, workers=4, etf_holding_relevance_batch=1,
        etf_holding_relevance_retry_budget=0,
    ).score_etf_holding_relevance({}, [
        {"code": code, "name": code}
        for code in ("185:DUP", "169:DUP", "185:UNIQUE")
    ])

    assert scores["185:UNIQUE"]["relevance_status"] == "scored"
    assert scores["185:DUP"]["relevance_status"] == "missing"
    assert scores["169:DUP"]["relevance_status"] == "missing"
    ids = [row[1] for call in llm.calls for row in _prompt_rows(call[1])]
    assert len(set(ids)) == 3


def test_parallel_batches_reject_foreign_candidate_ids_without_cross_assignment():
    def foreign_id(rows, _call_number):
        response = _valid_response(rows)
        for row in response["results"]:
            row["candidate_id"] = "S0002" if row["market_code"] == "185:C00" else "S0001"
        return response

    scores = _workflow(
        RecordingLLM(response=foreign_id), workers=4,
        etf_holding_relevance_batch=1, etf_holding_relevance_retry_budget=0,
    ).score_etf_holding_relevance({}, _rows(2))

    assert all(row["relevance_status"] == "missing" for row in scores.values())


@pytest.mark.parametrize("workers", (1, 4))
def test_holding_retries_share_one_global_weight_prioritized_budget(workers):
    llm = RecordingLLM(
        workers=workers, barrier=workers > 1,
        response=lambda rows, number: {"results": []} if number <= 4 else _valid_response(rows),
    )
    scores = _workflow(
        llm, workers=workers, etf_holding_relevance_batch=5,
        etf_holding_relevance_retry_budget=7,
    ).score_etf_holding_relevance({}, _rows(20))

    assert len(llm.calls) == 6
    retry_batches = [_prompt_rows(call[1]) for call in llm.calls[4:]]
    assert sorted([row[0] for row in batch] for batch in retry_batches) == sorted([
        [f"185:C{index:02d}" for index in range(19, 14, -1)],
        ["185:C14", "185:C13"],
    ])
    assert {code for code, score in scores.items() if score["relevance_status"] == "scored"} == {
        f"185:C{index:02d}" for index in range(13, 20)
    }
    assert sum(score["relevance_status"] == "missing" for score in scores.values()) == 13
    assert all(len(batch) <= 5 for batch in retry_batches)


def test_parallel_batch_errors_isolate_failures_and_only_retry_transient_rows():
    def response(rows, number):
        if rows[0][0] == "185:C00":
            raise LLMRequestError("unauthorized", retryable=False, status_code=401)
        if rows[0][0] == "185:C02" and number <= 4:
            raise LLMRequestError("temporarily unavailable", retryable=True, status_code=503)
        return _valid_response(rows)

    llm = RecordingLLM(response=response)
    scores = _workflow(
        llm, workers=4, etf_holding_relevance_batch=2,
    ).score_etf_holding_relevance({}, _rows(8))

    assert len(llm.calls) == 5
    assert {code for code, row in scores.items() if row["relevance_status"] == "missing"} == {
        "185:C00", "185:C01",
    }
    assert scores["185:C02"]["relevance_retry"] == "recovered"
    assert scores["185:C03"]["relevance_retry"] == "recovered"
    assert all(scores[f"185:C{index:02d}"]["relevance_retry"] == "not_needed"
               for index in range(4, 8))


@pytest.mark.parametrize("retry_budget", (7, 20))
def test_real_client_truncation_recovers_smaller_structured_batches(retry_budget):
    calls = []
    lock = threading.Lock()

    def opening(request, **_kwargs):
        body = json.loads(request.data)
        rows = _prompt_rows(body["messages"][-1]["content"])
        with lock:
            calls.append((body, rows))
        if len(rows) == 20:
            # Even parseable partial content cannot establish scored evidence.
            partial = {"results": [
                _component_score(code, candidate_id, relevance=1, exposure_type="unclear")
                for code, candidate_id, _name in rows
            ]}
            return completion(json.dumps(partial), finish_reason="length")
        assert len(rows) <= 5
        return completion(json.dumps(_valid_response(rows)))

    with patch("llm_client.urllib.request.urlopen", side_effect=opening), \
            patch("llm_client.time.sleep") as sleeping:
        scores = _workflow(
            LLMClient(make_config(), retries=3), workers=4,
            etf_holding_relevance_batch=20,
            etf_holding_relevance_retry_budget=retry_budget,
        ).score_etf_holding_relevance({}, _rows(20))

    assert len(calls) == 1 + (retry_budget + 4) // 5
    sleeping.assert_not_called()
    assert len(calls[0][1]) == 20
    expected = {f"185:C{index:02d}" for index in range(20 - retry_budget, 20)}
    assert {code for code, row in scores.items() if row["relevance_status"] == "scored"} == expected
    assert all(scores[code]["ai_relevance"] == 5 for code in expected)
    assert all(scores[code]["relevance_retry"] == "recovered" for code in expected)
    original_ids = {code: candidate_id for code, candidate_id, _name in calls[0][1]}
    assert all(candidate_id == original_ids[code]
               for _body, rows in calls[1:] for code, candidate_id, _name in rows)
    for body, rows in calls:
        assert body["model"] == make_config().model
        assert body["max_completion_tokens"] == 8000
        response_rows = body["response_format"]["json_schema"]["schema"]["properties"]["results"]
        assert response_rows["minItems"] == response_rows["maxItems"] == len(rows)


def test_real_client_persistent_single_row_truncation_stops_after_one_recovery():
    with patch("llm_client.urllib.request.urlopen", return_value=completion(
            '{"results": []}', finish_reason="length")) as opening, \
            patch("llm_client.time.sleep") as sleeping:
        scores = _workflow(
            LLMClient(make_config(), retries=3), workers=4,
        ).score_etf_holding_relevance({}, _rows(1))

    assert opening.call_count == 2
    sleeping.assert_not_called()
    assert scores["185:C00"]["relevance_status"] == "missing"
    assert scores["185:C00"]["relevance_retry"] == "persistent_miss"


@pytest.mark.parametrize("failure", ("refusal", "content_filter"))
def test_real_client_refusal_and_filter_do_not_enter_truncation_recovery(failure):
    response = (completion(None, refusal="cannot comply") if failure == "refusal"
                else completion(None, finish_reason="content_filter"))
    with patch("llm_client.urllib.request.urlopen", return_value=response) as opening:
        scores = _workflow(
            LLMClient(make_config(), retries=3), workers=4,
        ).score_etf_holding_relevance({}, _rows(1))

    assert opening.call_count == 1
    assert scores["185:C00"]["relevance_status"] == "missing"
