"""Offline startup catalog, model probe, and selection configuration contracts."""

from __future__ import annotations

import io
import json
import os
import sys
import unittest
import urllib.error
from dataclasses import replace
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import model_selection


class Response:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def catalog(*models):
    return Response({"data": [{"id": model} for model in models]})


def configuration(**kwargs):
    values = {
        "base_url": "https://office.example.test/ai-gateway/v1/chat/completions",
        "models_url": "https://office.example.test/ai-gateway/models",
        "api_key": "example-test-key",
        "model": "default-unused",
        "timeout": 600,
        "auto_select_model": True,
        "workflow_retries": 200,
        "probe_timeout_seconds": 12,
        "trace_header": "X-Trace-Id",
        "reasoning_effort": "low",
    }
    values.update(kwargs)
    return config.LLMConfig(**values)


class ModelSelectionTests(unittest.TestCase):
    def test_prefers_known_families_and_skips_non_text_models(self):
        models = [
            "gpt-5.6-sol-capacity-parked", "gpt-5.6-terra", "unknown-z",
            "private-claude-opus-5", "bm-gpt-6-astra", "gpt-5.6-sol",
            "data-bm-gpt-6-astra", "unknown-a", "gpt-image-1", "tts-1",
            "text-embedding-3-large", "gpt-audio", "dall-e-3", "gpt-5.6-sol",
        ]
        self.assertEqual(model_selection.ordered_models(models), [
            "bm-gpt-6-astra", "data-bm-gpt-6-astra", "private-claude-opus-5",
            "gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-sol-capacity-parked",
            "unknown-a", "unknown-z",
        ])

    def test_configured_priority_promotes_family_and_exact_route(self):
        self.assertEqual(model_selection.ordered_models([
            "bm-gpt-6-astra", "bm-gpt-5.6-sol", "private-gpt-5.6-sol",
        ], ("private-gpt-5.6-sol",)), [
            "private-gpt-5.6-sol", "bm-gpt-5.6-sol", "bm-gpt-6-astra",
        ])

    def test_deepseek_routes_precede_other_families_despite_configured_priority(self):
        deepseek = ["deepseek-flash", "mirror-deepseek-flash", "bm-deepseek-v4-flash"]
        ordered = model_selection.ordered_models(
            ["claude-opus-5", *reversed(deepseek), "gpt-6-astra"],
            ("gpt-6-astra", "claude-opus-5"),
        )
        self.assertCountEqual(ordered[:len(deepseek)], deepseek)
        self.assertEqual(ordered[len(deepseek):], ["gpt-6-astra", "claude-opus-5"])

    def test_unknown_and_parked_deepseek_routes_still_precede_other_families(self):
        deepseek = [
            "deepseek-v99-reasoner", "data-bm-DeepSeek-v99-pro",
            "mirror-deepseek-flash-capacity-parked",
        ]
        ordered = model_selection.ordered_models(
            ["gpt-6-astra", "claude-opus-5", *deepseek],
            ("claude-opus-5", "gpt-6-astra"),
        )
        self.assertCountEqual(ordered[:len(deepseek)], deepseek)
        self.assertEqual(ordered[len(deepseek):], ["claude-opus-5", "gpt-6-astra"])

    def test_working_deepseek_stops_probes_before_other_families(self):
        deepseek = ["deepseek-flash", "mirror-deepseek-flash", "bm-deepseek-v4-flash"]
        for failures_before_success in (0, 1):
            with self.subTest(failures_before_success=failures_before_success):
                seen = []

                def probe(client, system, user, **kwargs):
                    seen.append(client.cfg.model)
                    if len(seen) <= failures_before_success:
                        raise TimeoutError("unavailable")
                    return json.loads(user)

                with redirect_stderr(io.StringIO()), patch(
                    "model_selection.urllib.request.urlopen",
                    return_value=catalog("gpt-6-astra", "claude-opus-5", *deepseek),
                ), patch.object(
                    model_selection.LLMClient, "chat_json", autospec=True, side_effect=probe,
                ):
                    selected = model_selection.select_workflow_model(configuration(
                        model_priority=("gpt-6-astra", "claude-opus-5"),
                    ))
                self.assertEqual(len(seen), failures_before_success + 1)
                self.assertTrue(all(model in deepseek for model in seen))
                self.assertEqual(selected.model, seen[-1])

    def test_gpt_fallback_is_probed_only_after_every_available_deepseek_fails(self):
        deepseek = [
            "deepseek-flash", "bm-deepseek-v4-flash", "mirror-deepseek-v99-pro",
            "deepseek-flash-capacity-parked",
        ]
        seen = []

        def probe(client, system, user, **kwargs):
            seen.append(client.cfg.model)
            if client.cfg.model in deepseek:
                raise TimeoutError("unavailable")
            return json.loads(user)

        with redirect_stderr(io.StringIO()), patch(
            "model_selection.urllib.request.urlopen",
            return_value=catalog("gpt-6-astra", "claude-opus-5", *deepseek),
        ), patch.object(
            model_selection.LLMClient, "chat_json", autospec=True, side_effect=probe,
        ):
            selected = model_selection.select_workflow_model(configuration(
                model_priority=("gpt-6-astra", "claude-opus-5"),
            ))
        self.assertCountEqual(seen[:-1], deepseek)
        self.assertEqual(seen[-1], "gpt-6-astra")
        self.assertEqual(selected.model, "gpt-6-astra")

    def test_skips_listed_but_unusable_models_and_keeps_selected_configuration(self):
        cfg = configuration()
        seen = []

        def probe(client, system, user, **kwargs):
            seen.append(client.cfg.model)
            self.assertEqual(client.retries, 1)
            self.assertIsNone(client.cfg.workflow_retries)
            self.assertFalse(client.cfg.auto_select_model)
            self.assertEqual(client.cfg.timeout, 12)
            self.assertEqual(kwargs["max_tokens"], cfg.max_completion_tokens)
            expected = json.loads(user)
            self.assertEqual(kwargs["response_schema"]["properties"]["nonce"]["const"], expected["nonce"])
            if client.cfg.model == "bm-gpt-6-astra":
                raise TimeoutError("sensitive upstream detail")
            return expected

        with patch("model_selection.urllib.request.urlopen", return_value=catalog(
            "gpt-5.6-sol", "bm-gpt-6-astra", "private-claude-opus-5",
        )) as fetch, patch.object(model_selection.LLMClient, "chat_json", autospec=True, side_effect=probe):
            selected = model_selection.select_workflow_model(cfg)
        self.assertEqual(seen, ["bm-gpt-6-astra", "private-claude-opus-5"])
        self.assertEqual(selected, replace(cfg, model="private-claude-opus-5"))
        self.assertEqual(cfg.model, "default-unused")
        request = fetch.call_args.args[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.full_url, cfg.models_url)
        self.assertEqual(request.get_header("Authorization"), "Bearer example-test-key")
        self.assertTrue(request.get_header("X-trace-id"))
        self.assertEqual(fetch.call_args.kwargs["timeout"], 12)

    def test_every_run_fetches_catalog_again_and_uses_new_nonce(self):
        nonces = []

        def probe(client, system, user, **kwargs):
            expected = json.loads(user)
            nonces.append(expected["nonce"])
            return expected

        with patch("model_selection.urllib.request.urlopen", side_effect=[
            catalog("gpt-5.6-sol"), catalog("bm-gpt-6-astra"),
        ]) as fetch, patch.object(model_selection.LLMClient, "chat_json", autospec=True, side_effect=probe):
            first = model_selection.select_workflow_model(configuration())
            second = model_selection.select_workflow_model(configuration())
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(first.model, "gpt-5.6-sol")
        self.assertEqual(second.model, "bm-gpt-6-astra")
        self.assertNotEqual(*nonces)

    def test_empty_or_incorrect_probe_replies_do_not_pass(self):
        for reply in [None, "", {}, {"nonce": "wrong"}, []]:
            with self.subTest(reply=reply), patch(
                "model_selection.urllib.request.urlopen", return_value=catalog("gpt-5.6-sol"),
            ), patch.object(model_selection.LLMClient, "chat_json", return_value=reply):
                with self.assertRaisesRegex(RuntimeError, "No usable workflow model"):
                    model_selection.select_workflow_model(configuration())

    def test_empty_and_non_text_catalogs_fail_before_probe(self):
        for models in [(), ("gpt-image-1", "text-embedding-3-large")]:
            with self.subTest(models=models), patch(
                "model_selection.urllib.request.urlopen", return_value=catalog(*models),
            ), patch.object(model_selection.LLMClient, "chat_json") as probe:
                with self.assertRaisesRegex(RuntimeError, "no usable text models"):
                    model_selection.select_workflow_model(configuration())
                probe.assert_not_called()

    def test_discovery_failure_is_sanitized_and_does_not_probe(self):
        error = urllib.error.HTTPError(
            "https://example.test/private", 401, "example-test-key secret detail", {},
            io.BytesIO(b"secret response body"),
        )
        with patch("model_selection.urllib.request.urlopen", side_effect=error), patch.object(
            model_selection.LLMClient, "chat_json",
        ) as probe:
            with self.assertRaisesRegex(RuntimeError, "discovery failed \\(HTTP 401\\)") as raised:
                model_selection.select_workflow_model(configuration())
            probe.assert_not_called()
        self.assertNotIn("secret", str(raised.exception))
        self.assertNotIn("example-test-key", str(raised.exception))
        self.assertTrue(raised.exception.__suppress_context__)

    def test_probe_failure_messages_are_not_in_final_diagnostics(self):
        diagnostics = io.StringIO()
        with redirect_stderr(diagnostics), patch("model_selection.urllib.request.urlopen", return_value=catalog("gpt-5.6-sol")), patch.object(
            model_selection.LLMClient, "chat_json", side_effect=RuntimeError("example-test-key secret"),
        ):
            with self.assertRaisesRegex(RuntimeError, "RuntimeError: 1") as raised:
                model_selection.select_workflow_model(configuration())
        self.assertNotIn("secret", str(raised.exception))
        self.assertIn("model probe 1/1 model=gpt-5.6-sol", diagnostics.getvalue())
        self.assertIn("model probe rejected model=gpt-5.6-sol reason=RuntimeError", diagnostics.getvalue())
        self.assertNotIn("example-test-key", diagnostics.getvalue())
        self.assertNotIn("secret", diagnostics.getvalue())

    def test_malformed_catalog_and_missing_endpoint_fail_cleanly(self):
        with patch("model_selection.urllib.request.urlopen", return_value=Response({"models": []})):
            with self.assertRaisesRegex(RuntimeError, "discovery failed \\(ValueError\\)"):
                model_selection.select_workflow_model(configuration())
        with self.assertRaisesRegex(RuntimeError, "configured models_url"):
            model_selection.select_workflow_model(configuration(models_url=None))


class SelectionConfigTests(unittest.TestCase):
    def env(self, **profile):
        return {
            "active_profiles": {"llm": "local"},
            "llm_profiles": {"local": {
                "provider": "litellm_openai_compatible",
                "base_url": "https://office.example.test/ai-gateway",
                "api_key": "profile-example-key",
                **profile,
            }},
        }

    def test_defaults_and_environment_overrides(self):
        cfg = config.load_llm_config(self.env())
        self.assertFalse(cfg.auto_select_model)
        self.assertIsNone(cfg.workflow_retries)
        self.assertEqual(cfg.probe_timeout_seconds, 60)
        self.assertEqual(cfg.model_priority, ())
        env = self.env(auto_select_model=False, workflow_retries=4,
                       probe_timeout_seconds=30, model_priority=["gpt-5.6-sol"])
        env["llm_profiles"]["local"].update({
            "active_environment": "office", "environments": {"office": {
                "base_url": "https://new-office.example.test/ai-gateway",
                "auto_select_model": True, "workflow_retries": 200,
                "probe_timeout_seconds": 10, "model_priority": ["claude-opus-5"],
                "protocols": {"models": "models"},
            }},
        })
        with patch.dict(os.environ, {"OFFICE_API_KEY": "environment-example-key"}):
            cfg = config.load_llm_config(env)
        self.assertTrue(cfg.auto_select_model)
        self.assertEqual(cfg.workflow_retries, 200)
        self.assertEqual(cfg.probe_timeout_seconds, 10)
        self.assertEqual(cfg.model_priority, ("claude-opus-5",))
        self.assertEqual(cfg.api_key, "environment-example-key")

    def test_office_environment_key_does_not_override_other_profiles(self):
        for selected, provider, auto in [("production", "litellm_openai_compatible", True),
                                         ("local", "litellm_openai_compatible", False),
                                         ("local", "deepseek", True)]:
            with self.subTest(selected=selected, provider=provider, auto=auto):
                env = self.env(auto_select_model=auto, provider=provider)
                env["llm_profiles"][selected] = env["llm_profiles"].pop("local")
                env["active_profiles"]["llm"] = selected
                with patch.dict(os.environ, {"OFFICE_API_KEY": "office-key", "DEEPSEEK_API_KEY": ""}):
                    cfg = config.load_llm_config(env)
                self.assertEqual(cfg.api_key, "profile-example-key")

    def test_invalid_selection_limits_are_rejected(self):
        for options in [{"workflow_retries": -1}, {"workflow_retries": True},
                        {"probe_timeout_seconds": 0}, {"probe_timeout_seconds": float("nan")},
                        {"model_priority": "gpt-6-astra"}, {"model_priority": [""]}]:
            with self.subTest(options=options), self.assertRaises(ValueError):
                config.load_llm_config(self.env(**options))


if __name__ == "__main__":
    unittest.main()
