"""Offline tests for manually configured AInvest C-side authentication."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[2]
THEME_DIR = ROOT / "theme_investing"
SKILL_ROOT = ROOT / "Skills" / "ainvest-openapi-quote"
FETCH_QUOTE = SKILL_ROOT / "scripts" / "fetch_quote.py"
sys.path.insert(0, str(THEME_DIR))

import config
from workflow import StockUniverseError, ThemeWorkflow


def example_env() -> dict:
    return {
        "active_profiles": {"quote": "local"},
        "quote_profiles": {
            "local": {
                "scene": "c",
                "auth": {
                    "header": "Cookie",
                    "cookie_value_template": "sessionid={sessionid}; userid={userid}",
                    "verify_tls": False,
                },
                "sessionid": "manual-session",
                "userid": "manual-user",
                "endpoints": {
                    "snapshot": "https://example.test/snapshot",
                    "multi_kline": "https://example.test/kline",
                },
            }
        },
        "default_headers": {"X-Auth-ProgId": "7080"},
    }


class ManualAuthTests(unittest.TestCase):
    def test_quote_tls_supplements_missing_python_ca_store_with_certifi(self):
        context = Mock()
        quote_cfg = config.QuoteConfig(
            scene="c",
            snapshot_url="https://example.test/snapshot",
            multi_kline_url="https://example.test/kline",
            headers={},
        )
        with patch(
                "config.ssl.create_default_context", return_value=context) as create, \
                patch("certifi.where", return_value="/test/certifi.pem"):
            result = quote_cfg.ssl_context()

        self.assertIs(result, context)
        create.assert_called_once_with(cafile=None)
        context.load_verify_locations.assert_called_once_with(
            cafile="/test/certifi.pem"
        )

    def test_stock_source_surfaces_initial_quote_failure(self):
        class FailingQuotes:
            strict = None

            def iter_ranked(self, *_args, **kwargs):
                self.strict = kwargs.get("strict")
                raise RuntimeError("TLS certificate verification failed")

        workflow = object.__new__(ThemeWorkflow)
        workflow.quotes = FailingQuotes()

        with self.assertRaisesRegex(
                StockUniverseError, "TLS certificate verification failed"):
            list(workflow.stock_source())

        self.assertIs(workflow.quotes.strict, True)

    def test_stock_source_rejects_an_empty_stock_snapshot(self):
        class EmptyQuotes:
            def iter_ranked(self, *_args, **_kwargs):
                return iter(())

        workflow = object.__new__(ThemeWorkflow)
        workflow.quotes = EmptyQuotes()

        with self.assertRaisesRegex(
                StockUniverseError, "stock snapshot returned an empty universe"):
            list(workflow.stock_source())

    def test_config_uses_selected_profile_cookie(self):
        env = example_env()
        env["sessionid"] = "legacy-session"
        env["userid"] = "legacy-user"
        cfg = config.load_quote_config(env)
        self.assertEqual(
            cfg.headers["Cookie"],
            "sessionid=manual-session; userid=manual-user",
        )

    def test_config_requires_both_manual_cookie_values(self):
        for missing_key in ("sessionid", "userid"):
            with self.subTest(missing_key=missing_key):
                env = example_env()
                del env["quote_profiles"]["local"][missing_key]
                with self.assertRaisesRegex(
                        ValueError, "manually configured sessionid and userid"):
                    config.load_quote_config(env)

    def test_obsolete_auto_login_setting_is_ignored(self):
        env = example_env()
        env["quote_profiles"]["local"]["auth"]["login"] = {
            "auto_login": True,
            "email": "unused@example.test",
            "password": "unused",
        }
        cfg = config.load_quote_config(env)
        self.assertEqual(
            cfg.headers["Cookie"],
            "sessionid=manual-session; userid=manual-user",
        )

    def test_quote_script_resolves_saved_manual_cookie(self):
        spec = importlib.util.spec_from_file_location("fetch_quote_test", FETCH_QUOTE)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader)
        fetch_quote = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(fetch_quote)
        args = SimpleNamespace(
            auth_value=None,
            auth_env=None,
            index_api_apikey=None,
            quoteag_apikey=None,
        )
        env = example_env()
        env["quote_profiles"]["local"]["auth"]["login"] = {"auto_login": True}

        value, source = fetch_quote.resolve_auth_value(
            args, "snapshot", "c", env
        )

        self.assertEqual(value, "sessionid=manual-session; userid=manual-user")
        self.assertEqual(source, "env.json c sessionid/userid")
        self.assertFalse(hasattr(fetch_quote, "refresh_c_auth_if_needed"))

    def test_quote_dry_run_explicit_cookie_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            env_path = Path(tmp) / "env.json"
            env_path.write_text(json.dumps(example_env()))
            body = json.dumps({"indicator": [], "page": {"page": 1, "size": 1}})
            completed = subprocess.run([
                sys.executable, str(FETCH_QUOTE), "--scene", "c",
                "--env-file", str(env_path), "--body-json", body,
                "--auth-value", "sessionid=explicit; userid=explicit-user", "--dry-run",
            ], cwd=SKILL_ROOT, capture_output=True, text=True, check=False)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        output = json.loads(completed.stdout)
        self.assertEqual(output["auth_source"], "caller-provided cookie")
        self.assertEqual(output["headers"]["Cookie"], "<CALLER_PROVIDED_COOKIE>")


if __name__ == "__main__":
    unittest.main()
