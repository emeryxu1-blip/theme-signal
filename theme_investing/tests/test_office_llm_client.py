"""Offline checks for fatal office-workflow retries and shared cancellation."""

from __future__ import annotations

import http.client
import io
import json
import os
import sys
import threading
import traceback
import unittest
import urllib.error
from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import llm_client
from llm_errors import LLMWorkflowAbort
from test_llm_client import FakeResponse, completion, http_error, make_config


def office_client(*, workflow_retries=200, auto_select_model=False, retries=3):
    return llm_client.LLMClient(
        make_config(
            workflow_retries=workflow_retries,
            auto_select_model=auto_select_model,
        ),
        retries=retries,
    )


class OfficeWorkflowRetryTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(redirect_stderr(io.StringIO()))
        # All ordinary retry tests advance the policy without sleeping.
        self.waiting = self.enterContext(patch.object(
            llm_client.LLMClient, "_wait_for_retry", return_value=None,
        ))

    def test_exactly_201_http_attempts_and_200_waits_before_fatal_exhaustion(self):
        client = office_client(retries=50)
        with patch("llm_client.urllib.request.urlopen", side_effect=http_error(401)) as opening:
            with self.assertRaises(LLMWorkflowAbort):
                client.chat("instruction", "input")
        self.assertEqual(opening.call_count, 201)
        self.assertEqual(self.waiting.call_count, 200)
        self.assertTrue(all(0 <= call.args[-1] <= 30 for call in self.waiting.call_args_list))

    def test_success_on_attempt_201_is_accepted_without_switching_models(self):
        client = office_client()
        responses = [http_error(400) for _ in range(200)] + [completion("recovered")]
        with patch("llm_client.urllib.request.urlopen", side_effect=responses) as opening:
            self.assertEqual(client.chat("instruction", "input"), "recovered")
        self.assertEqual(opening.call_count, 201)
        self.assertEqual(self.waiting.call_count, 200)
        models = {json.loads(call.args[0].data)["model"] for call in opening.call_args_list}
        self.assertEqual(models, {"gpt-5.6-sol"})

    def test_retry_budget_resets_for_each_distinct_logical_request(self):
        client = office_client(workflow_retries=1)
        with patch(
            "llm_client.urllib.request.urlopen",
            side_effect=[http_error(401), completion("first"),
                         http_error(400), completion("second")],
        ) as opening:
            self.assertEqual(client.chat("instruction", "first input"), "first")
            self.assertEqual(client.chat("instruction", "second input"), "second")
        self.assertEqual(opening.call_count, 4)

    def test_office_policy_retries_transport_and_malformed_completion_failures(self):
        failures = {
            "permanent HTTP error": http_error(400),
            "authentication HTTP error": http_error(401),
            "transient HTTP error": http_error(503),
            "URL error": urllib.error.URLError("temporary"),
            "timeout": TimeoutError("temporary"),
            "connection reset": ConnectionResetError("temporary"),
            "remote disconnect": http.client.RemoteDisconnected("temporary"),
            "invalid response JSON": FakeResponse(b"not-json"),
            "non-object response": FakeResponse([]),
            "missing choices": FakeResponse({}),
            "empty choices": FakeResponse({"choices": []}),
            "empty message": completion(""),
            "whitespace message": completion("   "),
            "null message": completion(None),
            "refusal": completion(None, refusal="cannot comply"),
            "truncation": completion("partial", finish_reason="length"),
            "content filter": completion(None, finish_reason="content_filter"),
        }
        for name, failure in failures.items():
            with self.subTest(failure=name):
                client = office_client(workflow_retries=1)
                with patch(
                    "llm_client.urllib.request.urlopen",
                    side_effect=[failure, completion("recovered")],
                ) as opening:
                    self.assertEqual(client.chat("instruction", "input"), "recovered")
                self.assertEqual(opening.call_count, 2)

    def test_json_repairs_and_transport_errors_share_one_logical_budget(self):
        client = office_client(workflow_retries=3, retries=10)
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
        with patch(
            "llm_client.urllib.request.urlopen",
            side_effect=[http_error(401), completion("not json"),
                         completion("[]"), completion('{"ok":true}')],
        ) as opening:
            parsed = client.chat_json("Return JSON.", "input", response_schema=schema)
        self.assertEqual(parsed, {"ok": True})
        self.assertEqual(opening.call_count, 4)
        self.assertEqual(self.waiting.call_count, 3)

    def test_invalid_json_cannot_multiply_201_attempt_budget_even_with_strict_schema(self):
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
        for response_schema in (None, schema):
            with self.subTest(response_schema=response_schema):
                self.waiting.reset_mock()
                client = office_client(retries=10)
                with patch(
                    "llm_client.urllib.request.urlopen", return_value=completion("not json"),
                ) as opening:
                    with self.assertRaises(LLMWorkflowAbort):
                        client.chat_json("Return JSON.", "input", response_schema=response_schema)
                self.assertEqual(opening.call_count, 201)
                self.assertEqual(self.waiting.call_count, 200)

    def test_exhaustion_bypasses_exception_fallback_and_blocks_later_requests(self):
        client = office_client(workflow_retries=0)
        fallback_used = False
        with patch("llm_client.urllib.request.urlopen", side_effect=http_error(400)) as opening:
            with self.assertRaises(LLMWorkflowAbort):
                try:
                    client.chat("instruction", "first")
                except Exception:
                    fallback_used = True
            with self.assertRaises(LLMWorkflowAbort):
                client.chat("instruction", "second")
            with self.assertRaises(LLMWorkflowAbort):
                client.chat_json("Return JSON.", "third")
        self.assertFalse(fallback_used)
        self.assertEqual(opening.call_count, 1)
        self.waiting.assert_not_called()

    def test_fatal_error_and_logs_do_not_expose_key_headers_or_raw_request_data(self):
        private_prompt = "private-report-content-unique-marker"
        key = "unit-test-key"
        failures = (
            urllib.error.HTTPError(
                "https://example.test/litellm/v1/chat/completions", 401,
                f"Authorization: Bearer {key}; {private_prompt}", {},
                io.BytesIO(f'{key} {private_prompt}'.encode()),
            ),
            urllib.error.URLError(f"Authorization: Bearer {key}; {private_prompt}"),
            FakeResponse(f"{key} {private_prompt}".encode()),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                client = office_client(workflow_retries=1)
                output = io.StringIO()
                with redirect_stdout(output), redirect_stderr(output), patch(
                    "llm_client.urllib.request.urlopen", side_effect=[failure, failure],
                ):
                    with self.assertRaises(LLMWorkflowAbort) as raised:
                        client.chat(private_prompt, private_prompt)
                visible_error = "".join(traceback.format_exception(raised.exception))
                logged = output.getvalue() + visible_error
                self.assertNotIn(key, logged)
                self.assertNotIn(private_prompt, logged)
                self.assertNotIn("Authorization", logged)
                self.assertNotIn("Bearer", logged)

    def test_begin_workflow_resets_exhausted_state_without_auto_selection(self):
        client = office_client(workflow_retries=0)
        with patch("llm_client.urllib.request.urlopen", side_effect=http_error(401)):
            with self.assertRaises(LLMWorkflowAbort):
                client.chat("instruction", "old run")
        with patch("model_selection.select_workflow_model") as selecting, patch(
            "llm_client.urllib.request.urlopen", return_value=completion("fresh run"),
        ) as opening:
            client.begin_workflow()
            self.assertEqual(client.chat("instruction", "new run"), "fresh run")
        selecting.assert_not_called()
        opening.assert_called_once()

    def test_auto_selection_occurs_once_at_each_workflow_start_and_model_stays_fixed(self):
        client = office_client(workflow_retries=1, auto_select_model=True)
        first_cfg = replace(client.cfg, model="selected-first")
        second_cfg = replace(client.cfg, model="selected-next")
        with patch(
            "model_selection.select_workflow_model", side_effect=[first_cfg, second_cfg],
        ) as selecting, patch(
            "llm_client.urllib.request.urlopen",
            side_effect=[http_error(400), completion("first"),
                         completion("second"), completion("next run")],
        ) as opening:
            client.begin_workflow()
            self.assertEqual(client.chat("instruction", "first"), "first")
            self.assertEqual(client.chat("instruction", "second"), "second")
            self.assertEqual(selecting.call_count, 1)
            client.begin_workflow()
            self.assertEqual(client.chat("instruction", "next"), "next run")
        self.assertEqual(selecting.call_count, 2)
        models = [json.loads(call.args[0].data)["model"] for call in opening.call_args_list]
        self.assertEqual(models, ["selected-first"] * 3 + ["selected-next"])


class OfficeWorkflowCancellationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(redirect_stderr(io.StringIO()))

    def test_inflight_success_is_rejected_after_another_request_exhausts(self):
        client = office_client(workflow_retries=0)
        in_flight = threading.Event()
        release_response = threading.Event()

        def respond(request, **_kwargs):
            if json.loads(request.data)["messages"][1]["content"] == "slow":
                in_flight.set()
                if not release_response.wait(5):
                    raise TimeoutError("test response was not released")
                return completion("late response")
            raise http_error(401)

        with patch("llm_client.urllib.request.urlopen", side_effect=respond) as opening:
            with ThreadPoolExecutor(max_workers=1) as pool:
                slow = pool.submit(client.chat, "instruction", "slow")
                self.assertTrue(in_flight.wait(2))
                try:
                    with self.assertRaises(LLMWorkflowAbort):
                        client.chat("instruction", "failing")
                finally:
                    release_response.set()
                with self.assertRaises(LLMWorkflowAbort):
                    slow.result(timeout=2)
            with self.assertRaises(LLMWorkflowAbort):
                client.chat("instruction", "later")
        self.assertEqual(opening.call_count, 2)

    def test_cancellation_interrupts_another_requests_retry_wait(self):
        client = office_client(workflow_retries=1)
        waiting = threading.Event()
        original_wait = llm_client.LLMClient._wait_for_retry

        def wait_for_retry(state, _delay):
            if threading.current_thread().name.startswith("office-waiter"):
                waiting.set()
                return original_wait(state, 5)
            return original_wait(state, 0)

        with patch.object(llm_client.LLMClient, "_wait_for_retry", new=staticmethod(wait_for_retry)), patch(
            "llm_client.urllib.request.urlopen", side_effect=urllib.error.URLError("temporary"),
        ) as opening:
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix="office-waiter") as pool:
                sleeping = pool.submit(client.chat, "instruction", "sleeping")
                self.assertTrue(waiting.wait(2))
                with self.assertRaises(LLMWorkflowAbort):
                    client.chat("instruction", "exhausting")
                with self.assertRaises(LLMWorkflowAbort):
                    sleeping.result(timeout=1)
        self.assertEqual(opening.call_count, 3)


if __name__ == "__main__":
    unittest.main()
