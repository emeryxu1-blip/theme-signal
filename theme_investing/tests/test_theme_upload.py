"""Offline tests for exact theme-result uploads."""
from __future__ import annotations

import hashlib
import json
import os
import ssl
import sys
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import theme_upload


class FakeResponse:
    status = 201
    def read(self): return b'{"id":"theme_123","duplicate":false}'
    def __enter__(self): return self
    def __exit__(self, *args): return False


class ThemeUploadTests(unittest.TestCase):
    def test_upload_sends_exact_bytes_and_hash(self):
        serialized = '{"theme":"AI","future":{"x":1}}'
        captured = {}
        class FakeOpener:
            def open(self, request, timeout):
                captured["request"] = request
                captured["timeout"] = timeout
                return FakeResponse()
        with patch("theme_upload.urllib.request.build_opener",
                   return_value=FakeOpener()) as build_opener:
            result = theme_upload.upload_result(
                serialized, endpoint="https://example.test/api/themes", token="secret")
        request = captured["request"]
        digest = hashlib.sha256(serialized.encode()).hexdigest()
        self.assertEqual(result, "theme_123")
        self.assertEqual(request.data, serialized.encode())
        self.assertEqual(request.get_header("X-theme-upload-token"), "secret")
        self.assertEqual(request.get_header("X-content-sha256"), digest)
        self.assertEqual(request.get_header("User-agent"), "theme-workflow/1.0")
        self.assertEqual(json.loads(request.data), json.loads(serialized))
        self.assertEqual(captured["timeout"], 30.0)
        handlers = build_opener.call_args.args
        self.assertEqual(handlers[0].proxies, {})
        self.assertTrue(any(
            isinstance(handler, theme_upload._RejectRedirects)
            for handler in handlers
        ))

    def test_redirect_handler_never_forwards_upload_request(self):
        original = urllib.request.Request(
            "https://example.test/api/themes",
            headers={"X-Theme-Upload-Token": "secret"},
        )
        redirected = theme_upload._RejectRedirects().redirect_request(
            original, None, 302, "Found", {}, "https://attacker.test/collect",
        )
        self.assertIsNone(redirected)

    def test_upload_requires_https_except_loopback(self):
        with self.assertRaisesRegex(theme_upload.ThemeUploadError, "must use HTTPS"):
            theme_upload.upload_result(
                '{"theme":"AI"}',
                endpoint="http://example.test/api/themes",
                token="secret",
            )

    def test_upload_retries_timeout_with_same_idempotency_key(self):
        serialized = '{"theme":"AI","ThemeStocks":[{"market_code":"185:NVDA"}]}'
        requests = []

        class RetryOpener:
            def open(self, request, timeout):
                requests.append(request)
                if len(requests) == 1:
                    raise TimeoutError("timed out")
                return FakeResponse()

        with (
            patch("theme_upload.urllib.request.build_opener", return_value=RetryOpener()),
            patch("theme_upload.time.sleep") as sleep,
        ):
            result = theme_upload.upload_result(
                serialized,
                endpoint="https://example.test/api/themes",
                token="secret",
            )

        self.assertEqual(result, "theme_123")
        self.assertEqual(len(requests), 2)
        self.assertEqual(
            requests[0].get_header("X-idempotency-key"),
            requests[1].get_header("X-idempotency-key"),
        )
        sleep.assert_called_once_with(0.5)

    def test_upload_does_not_retry_permanent_http_error(self):
        class RejectingOpener:
            calls = 0
            def open(self, request, timeout):
                self.calls += 1
                raise urllib.error.HTTPError(
                    request.full_url, 401, "Unauthorized", {}, None,
                )

        opener = RejectingOpener()
        with (
            patch("theme_upload.urllib.request.build_opener", return_value=opener),
            patch("theme_upload.time.sleep") as sleep,
        ):
            with self.assertRaisesRegex(theme_upload.ThemeUploadError, "HTTP 401"):
                theme_upload.upload_result(
                    '{"theme":"AI"}',
                    endpoint="https://example.test/api/themes",
                    token="secret",
                )
        self.assertEqual(opener.calls, 1)
        sleep.assert_not_called()

    def test_upload_does_not_retry_tls_verification_failure(self):
        class TlsFailureOpener:
            calls = 0
            def open(self, request, timeout):
                self.calls += 1
                raise urllib.error.URLError(
                    ssl.SSLCertVerificationError("certificate verify failed")
                )

        opener = TlsFailureOpener()
        with (
            patch("theme_upload.urllib.request.build_opener", return_value=opener),
            patch("theme_upload.time.sleep") as sleep,
        ):
            with self.assertRaisesRegex(
                theme_upload.ThemeUploadError, "TLS certificate verification failed",
            ):
                theme_upload.upload_result(
                    '{"theme":"AI"}',
                    endpoint="https://example.test/api/themes",
                    token="secret",
                )
        self.assertEqual(opener.calls, 1)
        sleep.assert_not_called()

    def test_upload_error_does_not_echo_token(self):
        token = "do-not-log-this-token"

        class LeakyFailureOpener:
            def open(self, request, timeout):
                raise OSError(f"connection failed with {token}")

        with (
            patch(
                "theme_upload.urllib.request.build_opener",
                return_value=LeakyFailureOpener(),
            ),
            patch("theme_upload.time.sleep"),
        ):
            with self.assertRaises(theme_upload.ThemeUploadError) as caught:
                theme_upload.upload_result(
                    '{"theme":"AI"}',
                    endpoint="https://example.test/api/themes",
                    token=token,
                    attempts=1,
                )
        self.assertNotIn(token, str(caught.exception))
        self.assertIn("OSError", str(caught.exception))

    def test_upload_attempts_are_bounded(self):
        class TimeoutOpener:
            calls = 0
            def open(self, request, timeout):
                self.calls += 1
                raise TimeoutError("timed out")

        opener = TimeoutOpener()
        with (
            patch("theme_upload.urllib.request.build_opener", return_value=opener),
            patch("theme_upload.time.sleep") as sleep,
        ):
            with self.assertRaisesRegex(
                theme_upload.ThemeUploadError, "failed after 3 attempts",
            ):
                theme_upload.upload_result(
                    '{"theme":"AI"}',
                    endpoint="https://example.test/api/themes",
                    token="secret",
                )
        self.assertEqual(opener.calls, 3)
        self.assertEqual(sleep.call_count, 2)

    def test_configuration_is_optional_but_not_partial(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(theme_upload.configured_upload())
        with patch.dict(os.environ, {"THEME_SIGNAL_UPLOAD_URL": "https://x"}, clear=True):
            with self.assertRaises(theme_upload.ThemeUploadError):
                theme_upload.configured_upload()


if __name__ == "__main__":
    unittest.main()
