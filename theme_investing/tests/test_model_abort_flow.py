"""Fatal model exhaustion must stop every workflow stage and publication."""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
import io
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import cli
from etf_preselection import PreselectionResult
from llm_errors import LLMWorkflowAbort
from parallel import ordered_map
from workflow import ThemeWorkflow


PAYLOAD = {
    "theme": "Consumer spending", "date": "2026-01-01",
    "url": "https://example.test/article",
}
ARTICLE = {"ok": True, "title": "Consumer spending", "text": "Retail demand"}
BRIEF = {"theme_direction": "bullish", "theme_cn": "消费", "summary": "Demand"}


class FatalLLM:
    def __init__(self):
        self.failure = LLMWorkflowAbort("model retry budget exhausted")
        self.calls = 0
        self.lock = threading.Lock()

    def chat_json(self, *_args, **_kwargs):
        with self.lock:
            self.calls += 1
        raise self.failure


def candidate(index=0):
    return {"code": f"185:C{index}", "name": f"Consumer Company {index}"}


@pytest.mark.parametrize("stage", (
    "theme", "event", "ecosystem", "stock_scores", "holding_scores",
    "stock_narrative", "etf_narrative", "faq",
))
def test_fatal_model_error_bypasses_stage_fallbacks(stage):
    llm = FatalLLM()
    workflow = ThemeWorkflow(llm, object(), {"etf_llm_workers": 1})
    calls = {
        "theme": lambda: workflow.theme_profile(PAYLOAD["theme"]),
        "event": lambda: workflow.event_brief(PAYLOAD, ARTICLE, {}),
        "ecosystem": lambda: workflow.event_ecosystem(PAYLOAD, BRIEF, {}, ARTICLE),
        "stock_scores": lambda: workflow.score_relevance(BRIEF, [candidate()], ARTICLE),
        "holding_scores": lambda: workflow.score_etf_holding_relevance(
            BRIEF, [candidate()], ARTICLE),
        "stock_narrative": lambda: workflow.narrate(PAYLOAD, BRIEF, [candidate()], "stock"),
        "etf_narrative": lambda: workflow.narrate(PAYLOAD, BRIEF, [candidate()], "ETF"),
        "faq": lambda: workflow.faq(PAYLOAD, BRIEF, [], []),
    }

    with pytest.raises(LLMWorkflowAbort) as failure:
        calls[stage]()

    assert failure.value is llm.failure
    assert llm.calls == 1


def test_concurrent_holding_fatal_does_not_enter_recovery_batches():
    llm = FatalLLM()
    workflow = ThemeWorkflow(llm, object(), {
        "etf_llm_workers": 2, "etf_holding_relevance_batch": 1,
    })

    with pytest.raises(LLMWorkflowAbort) as failure:
        workflow.score_etf_holding_relevance(BRIEF, [candidate(i) for i in range(8)])

    assert failure.value is llm.failure
    assert 1 <= llm.calls <= 2


def test_component_assessment_fatal_escapes_run_recovery_wrapper():
    workflow = ThemeWorkflow(object(), SimpleNamespace(cfg=SimpleNamespace(scene="test")), {
        "stock_target": 0, "etf_target": 1,
    })
    fatal = LLMWorkflowAbort("component model exhausted")
    discovery = PreselectionResult([candidate()], [], 1, 0, [])
    with patch("workflow.fetch_article", return_value=ARTICLE), \
            patch.object(workflow, "theme_profile", return_value={}), \
            patch.object(workflow, "event_brief", return_value=BRIEF), \
            patch("workflow.preselect_etfs", return_value=discovery), \
            patch.object(workflow, "_mark_etf_liquidity_diagnostics"), \
            patch.object(workflow, "rerank_etfs_from_components", side_effect=fatal), \
            patch("workflow.extend_etf_candidates") as recover_universe, \
            patch.object(workflow, "faq") as faq, \
            pytest.raises(LLMWorkflowAbort) as failure:
        workflow.run(PAYLOAD)

    assert failure.value is fatal
    recover_universe.assert_not_called()
    faq.assert_not_called()


def test_every_run_prepares_model_before_external_work():
    events = []
    llm = SimpleNamespace(begin_workflow=lambda: events.append("prepare"))
    workflow = ThemeWorkflow(llm, SimpleNamespace(cfg=SimpleNamespace(scene="test")))

    def article_boundary(_url):
        events.append("article")
        raise RuntimeError("test stops at first external request")

    with patch("workflow.fetch_article", side_effect=article_boundary):
        for _ in range(2):
            with pytest.raises(RuntimeError, match="first external request"):
                workflow.run(PAYLOAD)

    assert events == ["prepare", "article", "prepare", "article"]


def test_startup_failure_prevents_article_and_quote_work():
    llm = SimpleNamespace(begin_workflow=Mock(side_effect=LLMWorkflowAbort("no usable model")))
    quotes = Mock()
    workflow = ThemeWorkflow(llm, quotes)
    with patch("workflow.fetch_article") as article, pytest.raises(LLMWorkflowAbort):
        workflow.run(PAYLOAD)
    llm.begin_workflow.assert_called_once_with()
    article.assert_not_called()
    assert quotes.mock_calls == []


def test_invalid_target_fails_before_model_startup():
    llm = SimpleNamespace(begin_workflow=Mock())
    workflow = ThemeWorkflow(llm, object(), {"stock_target": 121})
    with pytest.raises(ValueError, match="stock_candidate_budget"):
        workflow.run(PAYLOAD)
    llm.begin_workflow.assert_not_called()


@pytest.mark.parametrize("llm", (object(), SimpleNamespace(begin_workflow=None)))
def test_clients_without_startup_hook_remain_supported(llm):
    workflow = ThemeWorkflow(llm, SimpleNamespace(cfg=SimpleNamespace(scene="test")))
    with patch("workflow.fetch_article", side_effect=RuntimeError("article reached")), \
            pytest.raises(RuntimeError, match="article reached"):
        workflow.run(PAYLOAD)


def test_cli_fatal_returns_nonzero_without_serialization_or_upload():
    fatal = LLMWorkflowAbort("model failed after 200 retries")
    cfg = SimpleNamespace(
        provider="litellm_openai_compatible", model="test-model",
        reasoning_effort="low", max_completion_tokens=1000,
        base_url="https://example.test/completions",
    )
    stdout, stderr = io.StringIO(), io.StringIO()
    with patch.object(sys, "argv", ["cli.py"]), \
            patch("cli._read_payload", return_value=PAYLOAD), \
            patch("cli.load_env", return_value={"active_profiles": {"llm": "local"}}), \
            patch("cli.load_llm_config", return_value=cfg), \
            patch("cli.load_quote_config", return_value=SimpleNamespace(profile="local", scene="c")), \
            patch("cli.build_llm_client", return_value=object()), \
            patch("cli.AInvestClient", return_value=object()), \
            patch("cli.ThemeWorkflow", return_value=SimpleNamespace(run=Mock(side_effect=fatal))), \
            patch("cli._serialize_output") as serialize, \
            patch("cli.configured_upload") as configured_upload, \
            patch("cli.upload_result") as upload, \
            redirect_stdout(stdout), redirect_stderr(stderr):
        status = cli.main()

    assert status == 6
    assert stdout.getvalue() == ""
    assert "workflow aborted: model failed after 200 retries" in stderr.getvalue()
    serialize.assert_not_called()
    configured_upload.assert_not_called()
    upload.assert_not_called()


def test_parallel_fatal_bypasses_slow_earlier_item_and_stops_queued_work():
    slow_started, release_slow = threading.Event(), threading.Event()
    slow_finished, coordinator_finished = threading.Event(), threading.Event()
    calls, failures = [], []
    fatal = LLMWorkflowAbort("later item exhausted")

    def work(index):
        calls.append(index)
        if index == 0:
            slow_started.set()
            try:
                assert release_slow.wait(5), "test never released the first worker"
                return index
            finally:
                slow_finished.set()
        assert slow_started.wait(5)
        if index == 1:
            raise fatal
        return index

    def consume():
        try:
            list(ordered_map(work, range(20), workers=2))
        except BaseException as exc:
            failures.append(exc)
        finally:
            coordinator_finished.set()

    coordinator = threading.Thread(target=consume)
    coordinator.start()
    try:
        assert coordinator_finished.wait(3), "fatal waited behind an earlier slow result"
        assert not slow_finished.is_set()
        assert failures == [fatal]
        assert sorted(calls) == [0, 1]
    finally:
        release_slow.set()
        coordinator.join(5)
        assert slow_finished.wait(5)


def test_parallel_ordinary_errors_keep_input_order():
    later_failed = threading.Event()
    ordinary = RuntimeError("ordinary recoverable failure")

    def work(index):
        if index == 0:
            assert later_failed.wait(5)
            return "first"
        later_failed.set()
        raise ordinary

    results = ordered_map(work, range(2), workers=2)
    assert next(results) == "first"
    with pytest.raises(RuntimeError) as failure:
        next(results)
    assert failure.value is ordinary
