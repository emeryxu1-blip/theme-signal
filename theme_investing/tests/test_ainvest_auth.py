"""Offline tests for automatic AInvest C-side authentication."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
THEME_DIR = ROOT / "theme_investing"
SKILL_ROOT = ROOT / "Skills" / "ainvest-openapi-quote"
FETCH_QUOTE = SKILL_ROOT / "scripts" / "fetch_quote.py"
sys.path.insert(0, str(THEME_DIR))

import ainvest_auth
import config


def example_env() -> dict:
    return {
        "active_profiles": {"quote": "local"},
        "quote_profiles": {
            "local": {
                "scene": "c",
                "auth": {
                    "header": "Cookie",
                    "cookie_value_template": "sessionid={sessionid}; userid={userid}",
                    "login": {
                        "auto_login": True,
                        "email": "person@example.test",
                        "password": "not-a-real-password",
                    },
                },
                "endpoints": {
                    "snapshot": "https://example.test/snapshot",
                    "multi_kline": "https://example.test/kline",
                },
            }
        },
        "default_headers": {"X-Auth-ProgId": "7080"},
    }


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload

    def read(self):
        return json.dumps(self.payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeOpener:
    def __init__(self, responses, cookie_jar):
        self.responses = iter(responses)
        self.cookie_jar = cookie_jar
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        payload, cookies = next(self.responses)
        for name, value in cookies.items():
            self.cookie_jar.set_cookie(
                ainvest_auth.http.cookiejar.Cookie(
                    version=0, name=name, value=value, port=None, port_specified=False,
                    domain=".ainvest.com", domain_specified=True, domain_initial_dot=True,
                    path="/", path_specified=True, secure=True, expires=None,
                    discard=True, comment=None, comment_url=None, rest={}, rfc2109=False,
                )
            )
        return FakeResponse(payload)


class AuthTests(unittest.TestCase):
    def test_password_signing_key_path_accepts_explicit_local_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            key_path = Path(tmp) / "signing-key.pem"
            key_path.write_text("local test key")
            resolved = ainvest_auth._password_signing_key_path(str(key_path))
        self.assertEqual(resolved, key_path)

    def test_password_signing_key_path_rejects_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.pem"
            with self.assertRaisesRegex(ainvest_auth.AInvestAuthError, "signing key is missing"):
                ainvest_auth._password_signing_key_path(str(missing))

    def test_login_uses_visitor_check_and_signed_password(self):
        env = example_env()
        holder = {}

        def build_opener(cookie_processor, *_handlers):
            opener = FakeOpener([
                ({"data": {"userId": "visitor", "token": "visitor-token"}}, {}),
                ({"data": True}, {}),
                ({"data": {"userId": "user"}}, {"sessionid": "session", "userid": "user"}),
            ], cookie_processor.cookiejar)
            holder["opener"] = opener
            return opener

        with patch("ainvest_auth.urllib.request.build_opener", side_effect=build_opener), \
                patch("ainvest_auth._sign_password", return_value="signed") as signer:
            sessionid, userid = ainvest_auth.login_c_side(env)

        self.assertEqual((sessionid, userid), ("session", "user"))
        requests = holder["opener"].requests
        self.assertEqual(len(requests), 3)
        self.assertIn(b"clientType=WEB", requests[0].data)
        self.assertEqual(json.loads(requests[1].data), {"type": "EMAIL", "email": "person@example.test"})
        login = json.loads(requests[2].data)
        self.assertEqual(login["loginType"], "ACCOUNT_PWD")
        self.assertEqual(login["signedPwd"], "signed")
        self.assertEqual(login["visitorId"], "visitor")
        signer.assert_called_once()

    def test_missing_credentials_fail_before_network(self):
        env = example_env()
        del env["quote_profiles"]["local"]["auth"]["login"]["password"]
        with patch("ainvest_auth.urllib.request.build_opener") as build:
            with self.assertRaisesRegex(ainvest_auth.AInvestAuthError, "email and password"):
                ainvest_auth.login_c_side(env)
        build.assert_not_called()

    def test_captcha_is_not_bypassed(self):
        with self.assertRaises(ainvest_auth.AInvestChallengeError):
            ainvest_auth._raise_if_challenged({"errorCode": "-100007", "i18nMsg": "captcha"})

    def test_persist_updates_profile_and_legacy_cookie_fields(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "env.json"
            env = example_env()
            path.write_text(json.dumps(env))
            updated = ainvest_auth.persist_c_session(path, env, "new-session", "new-user")
            stored = json.loads(path.read_text())
        self.assertEqual(stored["quote_profiles"]["local"]["sessionid"], "new-session")
        self.assertEqual(stored["sessionid"], "new-session")
        self.assertEqual(updated["userid"], "new-user")

    def test_config_uses_selected_profile_cookie(self):
        env = example_env()
        env["sessionid"] = "legacy-session"
        env["userid"] = "legacy-user"
        env["quote_profiles"]["local"].update(sessionid="profile-session", userid="profile-user")
        cfg = config.load_quote_config(env)
        self.assertEqual(cfg.headers["Cookie"], "sessionid=profile-session; userid=profile-user")

    def test_quote_dry_run_does_not_login_and_explicit_cookie_wins(self):
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
