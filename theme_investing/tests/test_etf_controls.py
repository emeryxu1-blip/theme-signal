"""Offline ETF concurrency controls and completion-recovery transport contracts."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import redirect_stderr
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
import urllib.error

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ainvest_client import AInvestClient
import cli
from config import LLMConfig
from llm_client import LLMClient, LLMCompletionLimitError, LLMRequestError
from workflow import DEFAULTS, ThemeWorkflow


class Response:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def completion(*, reason="stop", content='{"results": []}', refusal=None):
    return Response({"choices": [{
        "finish_reason": reason,
        "message": {"content": content, "refusal": refusal},
    }]})


def llm_config(provider="litellm_openai_compatible"):
    return LLMConfig(
        base_url="https://example.test/completions", api_key="test-key",
        model="test-model", timeout=1, provider=provider,
        auth_header="Authorization", auth_prefix="Bearer ",
        reasoning_effort="low", max_completion_tokens=1000, verify_tls=False,
    )


class ETFWorkerControlsTests(unittest.TestCase):
    def test_worker_defaults_are_four_and_reach_workflow(self):
        args = cli.build_parser().parse_args(["{}"])
        opts = cli.workflow_options(args)
        for key in ("etf_llm_workers", "etf_quote_workers"):
            self.assertEqual(DEFAULTS[key], 4)
            self.assertEqual(getattr(args, key), 4)
            self.assertEqual(opts[key], 4)

    def test_workers_can_be_serial_or_independently_overridden(self):
        for llm_workers, quote_workers in ((1, 1), (2, 7)):
            with self.subTest(llm=llm_workers, quotes=quote_workers):
                args = cli.build_parser().parse_args([
                    "--etf-llm-workers", str(llm_workers),
                    "--etf-quote-workers", str(quote_workers), "{}",
                ])
                opts = cli.workflow_options(args)
                self.assertEqual(opts["etf_llm_workers"], llm_workers)
                self.assertEqual(opts["etf_quote_workers"], quote_workers)

    def test_worker_cli_rejects_zero_negative_and_nonintegers(self):
        for option in ("--etf-llm-workers", "--etf-quote-workers"):
            for invalid in ("0", "-1", "1.5", "bad"):
                with self.subTest(option=option, value=invalid), \
                        redirect_stderr(io.StringIO()), \
                        self.assertRaises(SystemExit):
                    cli.build_parser().parse_args([option, invalid, "{}"])

    def test_invalid_worker_cli_fails_before_input_or_config(self):
        for option in ("--etf-llm-workers", "--etf-quote-workers"):
            with self.subTest(option=option), \
                    patch.object(sys, "argv", ["cli.py", option, "0"]), \
                    patch("cli._read_payload") as read_payload, \
                    patch("cli.load_env") as load_env, \
                    redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                cli.main()
            read_payload.assert_not_called()
            load_env.assert_not_called()

    def test_direct_workflow_options_reject_invalid_workers(self):
        for key in ("etf_llm_workers", "etf_quote_workers"):
            for invalid in (0, -1, 1.5, True, "bad"):
                with self.subTest(key=key, value=invalid), \
                        self.assertRaises(ValueError):
                    ThemeWorkflow(object(), object(), opts={key: invalid})


class CompletionLimitTransportTests(unittest.TestCase):
    def test_length_is_typed_and_never_retries_same_payload(self):
        for provider in ("litellm_openai_compatible", "deepseek"):
            with self.subTest(provider=provider):
                client = LLMClient(llm_config(provider), retries=3)
                # Even syntactically complete JSON is not evidence when the
                # provider marks the entire completion as truncated.
                with patch("llm_client.urllib.request.urlopen",
                           return_value=completion(reason="length")) as opening, \
                        patch("llm_client.time.sleep") as sleep, \
                        self.assertRaises(LLMCompletionLimitError) as failure:
                    client.chat_json("Return JSON", "20 companies")
                self.assertIsInstance(failure.exception, RuntimeError)
                self.assertNotIsInstance(failure.exception, LLMRequestError)
                self.assertEqual(opening.call_count, 1)
                sleep.assert_not_called()

    def test_smaller_request_can_succeed_after_truncation(self):
        client = LLMClient(llm_config(), retries=3)
        with patch("llm_client.urllib.request.urlopen", side_effect=[
            completion(reason="length", content='{"results": ['),
            completion(content='{"results": [{"candidate_id": "h001"}]}'),
        ]) as opening:
            with self.assertRaises(LLMCompletionLimitError):
                client.chat_json("Return JSON", "20 companies")
            result = client.chat_json("Return JSON", "5 companies")
        self.assertEqual(result, {"results": [{"candidate_id": "h001"}]})
        self.assertEqual(opening.call_count, 2)
        sent = [json.loads(call.args[0].data) for call in opening.call_args_list]
        self.assertEqual(sent[0]["messages"][1]["content"], "20 companies")
        self.assertEqual(sent[1]["messages"][1]["content"], "5 companies")

    def test_refusal_and_content_filter_are_not_completion_limits(self):
        for response in (
            completion(content=None, refusal="cannot comply"),
            completion(reason="content_filter", content=None),
        ):
            with self.subTest(payload=response.payload):
                client = LLMClient(llm_config(), retries=3)
                with patch("llm_client.urllib.request.urlopen",
                           return_value=response) as opening, \
                        self.assertRaises(RuntimeError) as failure:
                    client.chat_json("Return JSON", "input")
                self.assertNotIsInstance(failure.exception, LLMCompletionLimitError)
                self.assertEqual(opening.call_count, 1)

    def test_authentication_and_configuration_failures_remain_distinct(self):
        client = LLMClient(llm_config(), retries=3)
        error = urllib.error.HTTPError(
            "https://example.test/completions", 401, "unauthorized", {}, None,
        )
        with patch("llm_client.urllib.request.urlopen", side_effect=error) as opening, \
                self.assertRaises(LLMRequestError) as failure:
            client.chat_json("Return JSON", "input")
        self.assertFalse(failure.exception.retryable)
        self.assertEqual(failure.exception.status_code, 401)
        self.assertNotIsInstance(failure.exception, LLMCompletionLimitError)
        self.assertEqual(opening.call_count, 1)
        with patch("llm_client.urllib.request.urlopen") as opening, \
                self.assertRaises(ValueError):
            client.chat_json("Return JSON", "input", max_tokens=0)
        opening.assert_not_called()


class QuoteTransportReuseTests(unittest.TestCase):
    def make_client(self, *, retries=3):
        context = object()
        cfg = SimpleNamespace(
            scene="c", headers={}, endpoint_family_credentials=None,
            ssl_context=Mock(return_value=context),
        )
        with patch("ainvest_client._load_names", return_value={}):
            client = AInvestClient(cfg, retries=retries)
        return client, cfg, context

    def test_tls_is_initialized_once_and_shared_by_parallel_requests(self):
        client, cfg, context = self.make_client()
        cfg.ssl_context.assert_called_once_with()
        with patch("ainvest_client.urllib.request.urlopen",
                   return_value=Response({"ok": True})) as opening:
            with ThreadPoolExecutor(max_workers=4) as executor:
                results = list(executor.map(
                    lambda i: client._post("https://example.test/snapshot", {"i": i}),
                    range(8),
                ))
        self.assertEqual(results, [{"ok": True}] * 8)
        cfg.ssl_context.assert_called_once_with()
        self.assertEqual(opening.call_count, 8)
        self.assertTrue(all(call.kwargs["context"] is context
                            for call in opening.call_args_list))

    def test_retry_exhaustion_has_no_final_sleep_or_new_tls_context(self):
        client, cfg, context = self.make_client(retries=3)
        with patch("ainvest_client.urllib.request.urlopen",
                   side_effect=urllib.error.URLError("offline")) as opening, \
                patch("ainvest_client.time.sleep") as sleep, \
                self.assertRaisesRegex(RuntimeError, "after 3 tries"):
            client._post("https://example.test/snapshot", {})
        self.assertEqual(opening.call_count, 3)
        self.assertEqual([call.args[0] for call in sleep.call_args_list], [0.6, 1.2])
        cfg.ssl_context.assert_called_once_with()
        self.assertTrue(all(call.kwargs["context"] is context
                            for call in opening.call_args_list))

    def test_single_failed_attempt_does_not_sleep(self):
        client, _cfg, _context = self.make_client(retries=1)
        with patch("ainvest_client.urllib.request.urlopen",
                   side_effect=TimeoutError("offline")), \
                patch("ainvest_client.time.sleep") as sleep, \
                self.assertRaises(RuntimeError):
            client._post("https://example.test/snapshot", {})
        sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
