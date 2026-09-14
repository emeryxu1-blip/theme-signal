"""Offline contract tests for DeepSeek routing and compatible LLM calls."""

from __future__ import annotations

import copy
import http.client
import io
import json
import os
import ssl
import sys
import unittest
import urllib.error
import uuid
from contextlib import redirect_stderr, redirect_stdout
from email.message import Message
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cli
import config
import llm_client


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        if isinstance(self.payload, bytes):
            return self.payload
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def completion(content="ok", *, finish_reason="stop", refusal=None):
    message = {"role": "assistant", "content": content}
    if refusal is not None:
        message["refusal"] = refusal
    return FakeResponse({
        "choices": [{"finish_reason": finish_reason, "message": message}],
    })


def http_error(status, *, retry_after=None, detail="request rejected"):
    headers = Message()
    if retry_after is not None:
        headers["Retry-After"] = str(retry_after)

    class FakeHTTPError(urllib.error.HTTPError):
        def __init__(self):
            super().__init__(
                "https://example.test/litellm/v1/chat/completions",
                status,
                "error",
                headers,
                None,
            )

        def read(self):
            return detail.encode("utf-8")

    return FakeHTTPError()


def make_config(**overrides):
    values = {
        "base_url": "https://example.test/litellm/v1/chat/completions",
        "api_key": "unit-test-key",
        "model": "gpt-5.6-sol",
        "timeout": 600,
        "provider": "litellm_openai_compatible",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "trace_header": "X-Trace-Id",
        "reasoning_effort": "low",
        "max_completion_tokens": 8000,
        "verify_tls": False,
    }
    values.update(overrides)
    return config.LLMConfig(**values)


def request_headers(request):
    return {key.lower(): value for key, value in request.header_items()}


def make_deepseek_config(**overrides):
    values = {
        "base_url": "https://api.deepseek.com/chat/completions",
        "provider": "deepseek",
        "model": "deepseek-flash",
        "trace_header": None,
        "thinking": {"type": "enabled"},
        "verify_tls": True,
    }
    values.update(overrides)
    return make_config(**values)


def routing_env():
    return {
        "active_profiles": {"llm": "production", "quote": "production"},
        "llm_profiles": {
            "local": {
                "provider": "deepseek",
                "base_url": "https://api.deepseek.com",
                "api_key": "deepseek-test-key",
                "default_model": "deepseek-flash",
                "reasoning_effort": "low",
                "timeout_seconds": 600,
                "request": {"thinking": {"type": "enabled"}},
            },
            "claude": {
                "provider": "anthropic",
                "base_url": "https://claude.example.test/litellm",
                "api_key": "claude-test-key",
                "model": "claude-opus-4-8",
            },
            "production": {
                "provider": "litellm_openai_compatible",
                "active_environment": "wuchang_prod",
                "default_model": "gpt-5.6-sol",
                "reasoning_effort": "low",
                "api_key": "profile-test-key",
                "environments": {
                    "office_wifi": {
                        "base_url": "https://office.example.test/litellm",
                        "api_key": "office-test-key",
                    },
                    "wuchang_prod": {
                        "base_url": "https://prod.example.test/litellm",
                    },
                },
            },
        },
        "quote_profiles": {"local": {}, "production": {}},
    }


class LLMConfigRoutingTests(unittest.TestCase):
    def _run_cli_and_capture_env(self, *arguments):
        captured = {}

        def fake_load_llm_config(env):
            captured["env"] = copy.deepcopy(env)
            return make_config()

        workflow = SimpleNamespace(run=lambda _payload: {})
        argv = ["cli.py", *arguments, '{"theme":"AI","date":"2026-01-01",'
                '"url":"https://example.test/article"}']
        with patch.object(sys, "argv", argv), \
                patch("cli.load_env", return_value=routing_env()), \
                patch("cli.load_llm_config", side_effect=fake_load_llm_config), \
                patch("cli.load_quote_config", return_value=SimpleNamespace(profile="local", scene="c")), \
                patch("cli.build_llm_client", return_value=object()), \
                patch("cli.AInvestClient", return_value=object()), \
                patch("cli.ThemeWorkflow", return_value=workflow), \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            status = cli.main()
        self.assertEqual(status, 0)
        return captured["env"]

    def test_local_target_routes_to_deepseek_profile_and_local_quotes(self):
        env = self._run_cli_and_capture_env("--target", "local", "--no-upload")
        self.assertEqual(env["active_profiles"], {
            "llm": "local", "quote": "local",
        })
        self.assertEqual(env["llm_profiles"]["local"]["provider"],
                         "deepseek")
        self.assertEqual(env["llm_profiles"]["local"]["default_model"],
                         "deepseek-flash")

    def test_explicit_llm_profile_can_still_select_claude(self):
        env = self._run_cli_and_capture_env(
            "--target", "local", "--llm-profile", "claude", "--no-upload",
        )
        self.assertEqual(env["active_profiles"]["llm"], "claude")
        self.assertEqual(env["llm_profiles"]["claude"]["provider"], "anthropic")

    def test_local_config_defaults_to_flash_and_official_deepseek_endpoint(self):
        env = routing_env()
        env["active_profiles"]["llm"] = "local"
        env["llm_profiles"]["local"].pop("default_model")
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": ""}):
            cfg = config.load_llm_config(env)
        self.assertEqual(cfg.base_url, "https://api.deepseek.com/chat/completions")
        self.assertEqual(cfg.api_key, "deepseek-test-key")
        self.assertEqual(cfg.model, "deepseek-flash")
        self.assertEqual(cfg.reasoning_effort, "low")
        self.assertEqual(cfg.timeout, 600)
        self.assertEqual(cfg.thinking, {"type": "enabled"})
        self.assertIsNone(cfg.trace_header)
        self.assertTrue(cfg.verify_tls)

    def test_checked_in_example_defaults_to_local_deepseek_and_is_loadable(self):
        example_path = Path(__file__).resolve().parents[2] / "Skills" / "env.example.json"
        env = json.loads(example_path.read_text(encoding="utf-8"))
        cfg = config.load_llm_config(env)
        self.assertEqual(env["active_profiles"]["llm"], "local")
        self.assertEqual(cfg.provider, "deepseek")
        self.assertEqual(cfg.model, "deepseek-flash")
        self.assertEqual(cfg.reasoning_effort, "low")
        self.assertEqual(cfg.max_completion_tokens, 8000)
        self.assertEqual(cfg.thinking, {"type": "enabled"})
        self.assertIsNone(cfg.trace_header)
        self.assertTrue(cfg.verify_tls)
        self.assertEqual(
            cfg.base_url,
            "https://api.deepseek.com/chat/completions",
        )
        local_quotes = env["quote_profiles"]["local"]
        self.assertNotIn("login", local_quotes["auth"])
        self.assertEqual(local_quotes["sessionid"], "<MANUAL_SESSION_ID>")
        self.assertEqual(local_quotes["userid"], "<MANUAL_USER_ID>")

    def test_deepseek_environment_key_overrides_the_profile_key(self):
        env = routing_env()
        env["active_profiles"]["llm"] = "local"
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "environment-test-key"}):
            cfg = config.load_llm_config(env)
        self.assertEqual(cfg.api_key, "environment-test-key")

    def test_deepseek_request_thinking_configuration_is_preserved(self):
        env = routing_env()
        env["active_profiles"]["llm"] = "local"
        env["llm_profiles"]["local"]["request"]["thinking"] = {"type": "disabled"}
        cfg = config.load_llm_config(env)
        self.assertEqual(cfg.thinking, {"type": "disabled"})

    def test_explicit_production_profile_preserves_litellm_routing(self):
        with patch.dict(os.environ, {"DEEPSEEK_API_KEY": "environment-test-key"}):
            cfg = config.load_llm_config(routing_env())
        self.assertEqual(cfg.provider, "litellm_openai_compatible")
        self.assertEqual(cfg.model, "gpt-5.6-sol")
        self.assertEqual(cfg.base_url, "https://prod.example.test/litellm/v1/chat/completions")
        self.assertEqual(cfg.api_key, "profile-test-key")

    def test_unknown_environment_has_a_clear_configuration_error(self):
        env = routing_env()
        env["active_profiles"]["llm"] = "production"
        env["llm_profiles"]["production"]["active_environment"] = "missing"
        with self.assertRaisesRegex(ValueError, "missing"):
            config.load_llm_config(env)


class LLMClientTests(unittest.TestCase):
    def test_deepseek_chat_sends_official_thinking_payload_with_verified_tls(self):
        client = llm_client.LLMClient(make_deepseek_config(), retries=1)
        with patch("llm_client.urllib.request.urlopen", return_value=completion("done")) as opening:
            result = client.chat("stable instruction", "dynamic input",
                                 temperature=0.9, max_tokens=321)
        self.assertEqual(result, "done")
        request = opening.call_args.args[0]
        self.assertEqual(json.loads(request.data), {
            "model": "deepseek-flash",
            "stream": False,
            "thinking": {"type": "enabled"},
            "reasoning_effort": "low",
            "max_tokens": 321,
            "messages": [
                {"role": "system", "content": "stable instruction"},
                {"role": "user", "content": "dynamic input"},
            ],
        })
        self.assertEqual(request.full_url, "https://api.deepseek.com/chat/completions")
        headers = request_headers(request)
        self.assertEqual(headers["authorization"], "Bearer unit-test-key")
        self.assertNotIn("x-trace-id", headers)
        context = opening.call_args.kwargs["context"]
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(context.check_hostname)

    def test_deepseek_defaults_to_enabled_thinking_and_configured_token_limit(self):
        client = llm_client.LLMClient(make_deepseek_config(thinking=None), retries=1)
        with patch("llm_client.urllib.request.urlopen", return_value=completion()) as opening:
            client.chat("instruction", "input")
        body = json.loads(opening.call_args.args[0].data)
        self.assertEqual(body["thinking"], {"type": "enabled"})
        self.assertEqual(body["reasoning_effort"], "low")
        self.assertEqual(body["max_tokens"], 8000)
        self.assertNotIn("max_completion_tokens", body)
        self.assertNotIn("temperature", body)

    def test_deepseek_without_thinking_sends_temperature_and_omits_reasoning_effort(self):
        client = llm_client.LLMClient(
            make_deepseek_config(thinking={"type": "disabled"}), retries=1,
        )
        with patch("llm_client.urllib.request.urlopen", return_value=completion()) as opening:
            client.chat("instruction", "input", temperature=0.4)
        body = json.loads(opening.call_args.args[0].data)
        self.assertEqual(body["thinking"], {"type": "disabled"})
        self.assertEqual(body["temperature"], 0.4)
        self.assertNotIn("reasoning_effort", body)

    def test_deepseek_schema_uses_json_object_mode_and_embeds_schema_in_system_prompt(self):
        client = llm_client.LLMClient(make_deepseek_config(), retries=1)
        schema = {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        }
        with patch(
            "llm_client.urllib.request.urlopen", return_value=completion('{"ok":true}'),
        ) as opening:
            parsed = client.chat_json("Return JSON.", "input", response_schema=schema)
        self.assertEqual(parsed, {"ok": True})
        body = json.loads(opening.call_args.args[0].data)
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["messages"][0]["role"], "system")
        system_prompt = body["messages"][0]["content"]
        self.assertIn("Return JSON.", system_prompt)
        embedded_schema, _ = json.JSONDecoder().raw_decode(system_prompt[system_prompt.index("{"):])
        self.assertEqual(embedded_schema, schema)

    def test_deepseek_schema_allows_one_json_repair(self):
        schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}
        for second_reply in ('{"ok":true}', "still not json"):
            with self.subTest(second_reply=second_reply):
                client = llm_client.LLMClient(make_deepseek_config(), retries=1)
                with patch(
                    "llm_client.urllib.request.urlopen",
                    side_effect=[completion("not json"), completion(second_reply)],
                ) as opening:
                    if second_reply.startswith("{"):
                        self.assertEqual(
                            client.chat_json("Return JSON.", "input", response_schema=schema),
                            {"ok": True},
                        )
                    else:
                        with self.assertRaisesRegex(ValueError, "JSON object"):
                            client.chat_json("Return JSON.", "input", response_schema=schema)
                self.assertEqual(opening.call_count, 2)
                repaired_request = json.loads(opening.call_args_list[1].args[0].data)
                self.assertEqual(repaired_request["response_format"], {"type": "json_object"})
                self.assertIn("previous reply was invalid",
                              repaired_request["messages"][1]["content"].lower())

    def test_nonpositive_completion_limit_is_rejected_before_network(self):
        client = llm_client.LLMClient(make_config(), retries=1)
        with patch("llm_client.urllib.request.urlopen") as opening:
            with self.assertRaisesRegex(ValueError, "greater than zero"):
                client.chat("instruction", "input", max_tokens=0)
        opening.assert_not_called()

    def test_chat_sends_exact_sol_payload_and_configured_headers(self):
        client = llm_client.LLMClient(make_config(), retries=1)
        with patch("llm_client.urllib.request.urlopen", return_value=completion("done")) as opening:
            result = client.chat("stable instruction", "dynamic input",
                                 temperature=0.9, max_tokens=321)

        self.assertEqual(result, "done")
        request = opening.call_args.args[0]
        body = json.loads(request.data)
        self.assertEqual(body, {
            "model": "gpt-5.6-sol",
            "stream": False,
            "reasoning_effort": "low",
            "max_completion_tokens": 321,
            "messages": [
                {"role": "developer", "content": "stable instruction"},
                {"role": "user", "content": "dynamic input"},
            ],
        })
        self.assertNotIn("temperature", body)
        self.assertNotIn("max_tokens", body)
        self.assertNotIn("response_format", body)
        self.assertEqual(request.get_method(), "POST")
        self.assertEqual(request.full_url, make_config().base_url)
        headers = request_headers(request)
        self.assertEqual(headers["authorization"], "Bearer unit-test-key")
        self.assertEqual(headers["content-type"], "application/json")
        self.assertTrue(headers["x-trace-id"])
        self.assertEqual(opening.call_args.kwargs["timeout"], 600)

    def test_custom_auth_header_and_prefix_are_not_hardcoded(self):
        cfg = make_config(auth_header="X-Api-Key", auth_prefix="Token ", trace_header=None)
        client = llm_client.LLMClient(cfg, retries=1)
        with patch("llm_client.urllib.request.urlopen", return_value=completion()) as opening:
            client.chat("instruction", "input")
        headers = request_headers(opening.call_args.args[0])
        self.assertEqual(headers["x-api-key"], "Token unit-test-key")
        self.assertNotIn("authorization", headers)

    def test_chat_json_enables_json_object_mode_and_returns_an_object(self):
        client = llm_client.LLMClient(make_config(), retries=1)
        raw = '{"results":[{"id":1}]}'
        with patch("llm_client.urllib.request.urlopen", return_value=completion(raw)) as opening:
            parsed = client.chat_json("Return JSON.", "input", max_tokens=456)
        self.assertEqual(parsed, {"results": [{"id": 1}]})
        body = json.loads(opening.call_args.args[0].data)
        self.assertEqual(body["response_format"], {"type": "json_object"})
        self.assertEqual(body["max_completion_tokens"], 456)

    def test_chat_json_can_request_strict_json_schema_output(self):
        client = llm_client.LLMClient(make_config(), retries=1)
        schema = {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        }
        with patch(
            "llm_client.urllib.request.urlopen",
            return_value=completion('{"ok":true}'),
        ) as opening:
            parsed = client.chat_json(
                "Return JSON.",
                "input",
                response_schema=schema,
                schema_name="health_check",
            )
        self.assertEqual(parsed, {"ok": True})
        body = json.loads(opening.call_args.args[0].data)
        self.assertEqual(body["response_format"], {
            "type": "json_schema",
            "json_schema": {
                "name": "health_check",
                "strict": True,
                "schema": schema,
            },
        })

    def test_strict_schema_parse_failure_does_not_repeat_full_request(self):
        client = llm_client.LLMClient(make_config(), retries=1)
        schema = {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        }
        with patch(
            "llm_client.urllib.request.urlopen",
            return_value=completion("not json"),
        ) as opening:
            with self.assertRaisesRegex(ValueError, "JSON object"):
                client.chat_json(
                    "Return JSON.", "input", response_schema=schema,
                )
        self.assertEqual(opening.call_count, 1)

    def test_chat_json_repairs_invalid_json_at_most_once(self):
        client = llm_client.LLMClient(make_config(), retries=1)
        responses = [completion("not json"), completion('{"items":[]}')]
        with patch("llm_client.urllib.request.urlopen", side_effect=responses) as opening:
            parsed = client.chat_json("Return JSON.", "input")
        self.assertEqual(parsed, {"items": []})
        self.assertEqual(opening.call_count, 2)
        second = json.loads(opening.call_args_list[1].args[0].data)
        self.assertIn("previous reply was invalid", second["messages"][1]["content"].lower())

    def test_chat_json_rejects_arrays_and_surrounding_prose_after_one_repair(self):
        for invalid in ('[{"id":1}]', 'prefix {"id":1} suffix'):
            with self.subTest(invalid=invalid):
                client = llm_client.LLMClient(make_config(), retries=1)
                with patch(
                    "llm_client.urllib.request.urlopen",
                    side_effect=[completion(invalid), completion(invalid)],
                ) as opening:
                    with self.assertRaisesRegex(ValueError, "JSON object"):
                        client.chat_json("Return JSON.", "input")
                self.assertEqual(opening.call_count, 2)

    def test_transient_http_statuses_retry(self):
        for status in (408, 409, 425, 429, 500, 502, 503, 504):
            with self.subTest(status=status):
                client = llm_client.LLMClient(make_config(), retries=2)
                with patch(
                    "llm_client.urllib.request.urlopen",
                    side_effect=[http_error(status), completion("recovered")],
                ) as opening, patch("llm_client.time.sleep") as sleeping:
                    self.assertEqual(client.chat("instruction", "input"), "recovered")
                self.assertEqual(opening.call_count, 2)
                sleeping.assert_called_once()

    def test_permanent_http_statuses_fail_fast_and_redact_credentials(self):
        for status in (400, 401, 403, 404, 413, 422):
            with self.subTest(status=status):
                client = llm_client.LLMClient(make_config(), retries=3)
                rejection = http_error(
                    status, detail="unit-test-key private detail",
                )
                with patch("llm_client.urllib.request.urlopen", side_effect=rejection) as opening, \
                        patch("llm_client.time.sleep") as sleeping:
                    with self.assertRaises(RuntimeError) as raised:
                        client.chat("instruction", "input")
                rejection.close()
                self.assertEqual(opening.call_count, 1)
                sleeping.assert_not_called()
                self.assertNotIn("unit-test-key", str(raised.exception))
                self.assertFalse(raised.exception.retryable)
                self.assertEqual(raised.exception.status_code, status)

    def test_connection_failures_retry(self):
        failures = (
            urllib.error.URLError("temporary"),
            TimeoutError("timed out"),
            ConnectionResetError("reset"),
            http.client.RemoteDisconnected("closed"),
        )
        for failure in failures:
            with self.subTest(failure=type(failure).__name__):
                client = llm_client.LLMClient(make_config(), retries=2)
                with patch(
                    "llm_client.urllib.request.urlopen",
                    side_effect=[failure, completion("recovered")],
                ) as opening, patch("llm_client.time.sleep") as sleeping:
                    self.assertEqual(client.chat("instruction", "input"), "recovered")
                self.assertEqual(opening.call_count, 2)
                sleeping.assert_called_once()

    def test_retry_after_is_honored_and_each_attempt_gets_a_trace_id(self):
        client = llm_client.LLMClient(make_config(), retries=2)
        trace_ids = [uuid.UUID(int=1), uuid.UUID(int=2)]
        with patch(
            "llm_client.urllib.request.urlopen",
            side_effect=[http_error(429, retry_after=7), completion("recovered")],
        ) as opening, patch("llm_client.time.sleep") as sleeping, \
                patch("llm_client.uuid.uuid4", side_effect=trace_ids):
            self.assertEqual(client.chat("instruction", "input"), "recovered")
        sleeping.assert_called_once_with(7.0)
        traces = [
            request_headers(call.args[0])["x-trace-id"]
            for call in opening.call_args_list
        ]
        self.assertEqual(traces, [str(value) for value in trace_ids])

    def test_retry_exhaustion_uses_n_attempts_and_only_n_minus_one_sleeps(self):
        client = llm_client.LLMClient(make_config(), retries=3)

        def unavailable(*_args, **_kwargs):
            raise urllib.error.URLError("temporary")

        with patch("llm_client.urllib.request.urlopen", side_effect=unavailable) as opening, \
                patch("llm_client.time.sleep") as sleeping:
            with self.assertRaisesRegex(RuntimeError, "3 attempts") as raised:
                client.chat("instruction", "input")
        self.assertEqual(opening.call_count, 3)
        self.assertEqual(sleeping.call_count, 2)
        self.assertTrue(raised.exception.retryable)
        self.assertIsNone(raised.exception.status_code)
        delays = [call.args[0] for call in sleeping.call_args_list]
        self.assertGreater(delays[1], delays[0])

    def test_transient_http_retry_exhaustion_preserves_final_status(self):
        client = llm_client.LLMClient(make_config(), retries=2)
        failures = [http_error(502), http_error(503)]
        with patch(
            "llm_client.urllib.request.urlopen", side_effect=failures,
        ) as opening, patch("llm_client.time.sleep") as sleeping:
            with self.assertRaises(llm_client.LLMRequestError) as raised:
                client.chat("instruction", "input")
        for failure in failures:
            failure.close()
        self.assertEqual(opening.call_count, 2)
        sleeping.assert_called_once()
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.status_code, 503)

    def test_malformed_success_envelopes_fail_without_transport_retry(self):
        malformed = (
            {},
            {"choices": []},
            {"choices": [{"finish_reason": "stop", "message": {"content": None}}]},
            b"not-json",
        )
        for payload in malformed:
            with self.subTest(payload=payload):
                client = llm_client.LLMClient(make_config(), retries=3)
                with patch(
                    "llm_client.urllib.request.urlopen", return_value=FakeResponse(payload),
                ) as opening, patch("llm_client.time.sleep") as sleeping:
                    with self.assertRaises((RuntimeError, ValueError)):
                        client.chat("instruction", "input")
                self.assertEqual(opening.call_count, 1)
                sleeping.assert_not_called()

    def test_refusal_and_truncation_fail_without_retry(self):
        failures = (
            completion(None, refusal="cannot comply"),
            completion("partial", finish_reason="length"),
            completion(None, finish_reason="content_filter"),
        )
        for response in failures:
            with self.subTest(payload=response.payload):
                client = llm_client.LLMClient(make_config(), retries=3)
                with patch(
                    "llm_client.urllib.request.urlopen", return_value=response,
                ) as opening, patch("llm_client.time.sleep") as sleeping:
                    with self.assertRaises((RuntimeError, ValueError)):
                        client.chat("instruction", "input")
                self.assertEqual(opening.call_count, 1)
                sleeping.assert_not_called()


if __name__ == "__main__":
    unittest.main()
