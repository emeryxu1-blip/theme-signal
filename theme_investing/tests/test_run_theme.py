"""Regression tests for the convenience runner."""

from __future__ import annotations

import copy
import io
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cli
from workflow import StockRationaleError


class RunThemeTests(unittest.TestCase):
    def test_cli_always_uses_manual_cookie_and_ignores_obsolete_login_settings(self):
        payload = (
            '{"theme":"AI","date":"2026-01-01",'
            '"url":"https://example.test/article"}'
        )
        env = {
            "active_profiles": {"llm": "test", "quote": "local"},
            "quote_profiles": {
                "local": {
                    "scene": "c",
                    "auth": {
                        "header": "Cookie",
                        "cookie_value_template": "sessionid={sessionid}; userid={userid}",
                        # Old local files may retain this inert object. It must
                        # never re-enable account login or session refresh.
                        "login": {"auto_login": True},
                    },
                    "sessionid": "manual-session",
                    "userid": "manual-user",
                    "endpoints": {
                        "snapshot": "https://example.test/snapshot",
                        "multi_kline": "https://example.test/kline",
                    },
                }
            },
        }
        original_env = copy.deepcopy(env)
        captured = {}
        workflow = SimpleNamespace(run=lambda _payload: {})
        llm_cfg = SimpleNamespace(
            provider="openai",
            model="test-model",
            reasoning_effort=None,
            max_completion_tokens=1000,
            base_url="https://example.test/llm",
        )

        def capture_quote_config(quote_cfg):
            captured["cookie"] = quote_cfg.headers["Cookie"]
            return object()

        with patch.object(
                sys, "argv", ["cli.py", "--target", "local", "--no-upload", payload]), \
                patch("cli.load_env", return_value=env), \
                patch("cli.load_llm_config", return_value=llm_cfg), \
                patch("cli.build_llm_client", return_value=object()), \
                patch("cli.AInvestClient", side_effect=capture_quote_config), \
                patch("cli.ThemeWorkflow", return_value=workflow), \
                patch("urllib.request.build_opener",
                      side_effect=AssertionError("automatic login attempted")) as opener, \
                redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
            status = cli.main()

        self.assertEqual(status, 0)
        self.assertEqual(captured["cookie"],
                         "sessionid=manual-session; userid=manual-user")
        self.assertEqual(env["quote_profiles"], original_env["quote_profiles"])
        opener.assert_not_called()

    def test_stock_rationale_error_exposes_missing_and_rejected_codes(self):
        rejection_reasons = {
            "185:GOOGL": "duplicate_template_after_retry",
            "185:MU": "missing_event_relationship",
        }
        error = StockRationaleError(
            "validated 6/8 stock rationales",
            missing_codes=("185:TSM", "185:AMKR"),
            rejections=rejection_reasons,
        )
        rejection_reasons["185:AAPL"] = "mutated_after_construction"

        self.assertEqual(error.missing_codes, ("185:TSM", "185:AMKR"))
        self.assertEqual(error.rejected_codes, ("185:GOOGL", "185:MU"))
        self.assertEqual(error.rejections, {
            "185:GOOGL": "duplicate_template_after_retry",
            "185:MU": "missing_event_relationship",
        })
        self.assertEqual(str(error), "validated 6/8 stock rationales")

    def test_cli_stock_rationale_failure_emits_no_json_or_upload(self):
        payload = (
            '{"theme":"AI","date":"2026-01-01",'
            '"url":"https://example.test/article"}'
        )
        failure = StockRationaleError(
            "expected 8 grounded stock rationales",
            missing_codes=("185:TSM",),
            rejections={"185:GOOGL": "duplicate_template_after_retry"},
        )

        def fail(_payload):
            raise failure

        workflow = SimpleNamespace(run=fail)
        llm_cfg = SimpleNamespace(
            provider="openai",
            model="test-model",
            reasoning_effort=None,
            max_completion_tokens=1000,
            base_url="https://example.test/llm",
        )
        env = {"active_profiles": {"llm": "test", "quote": "test"}}
        stdout = io.StringIO()
        stderr = io.StringIO()

        with patch.object(sys, "argv", ["cli.py", payload]), \
                patch("cli.load_env", return_value=env), \
                patch("cli.load_llm_config", return_value=llm_cfg), \
                patch("cli.load_quote_config", return_value=SimpleNamespace(
                    profile="test", scene="test")), \
                patch("cli.build_llm_client", return_value=object()), \
                patch("cli.AInvestClient", return_value=object()), \
                patch("cli.ThemeWorkflow", return_value=workflow), \
                patch("cli.configured_upload") as configured_upload, \
                patch("cli.upload_result") as upload_result, \
                redirect_stdout(stdout), redirect_stderr(stderr):
            status = cli.main()

        self.assertEqual(status, 4)
        self.assertEqual(stdout.getvalue(), "")
        self.assertIn("stock rationale generation failed", stderr.getvalue())
        self.assertIn("missing=185:TSM", stderr.getvalue())
        self.assertIn(
            "rejected=185:GOOGL:duplicate_template_after_retry",
            stderr.getvalue(),
        )
        configured_upload.assert_not_called()
        upload_result.assert_not_called()

    def test_stock_target_is_exact_nonnegative_and_cannot_exceed_budget(self):
        args = cli.build_parser().parse_args(["--stock-target", "0", "{}"])
        self.assertEqual(cli.workflow_options(args)["stock_target"], 0)

        with redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cli.build_parser().parse_args(["--stock-target", "-1", "{}"])

        args = cli.build_parser().parse_args([
            "--stock-target", "9", "--stock-candidate-budget", "8", "{}",
        ])
        with self.assertRaisesRegex(
                ValueError, "cannot exceed --stock-candidate-budget"):
            cli.workflow_options(args)

        args = cli.build_parser().parse_args(["--stock-target", "121", "{}"])
        with self.assertRaisesRegex(
                ValueError, "cannot exceed --stock-candidate-budget"):
            cli.workflow_options(args)

    def test_forwards_cli_args_and_streams_progress(self):
        source = Path(__file__).resolve().parents[1] / "run_theme.sh"
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            runner = workdir / "run_theme.sh"
            shutil.copy2(source, runner)
            (workdir / ".env.upload").write_text("", encoding="utf-8")

            bindir = workdir / "bin"
            bindir.mkdir()
            fake_python = bindir / "python3"
            fake_python.write_text(
                "#!/bin/sh\n"
                "printf '%s\\n' \"$*\" > argv.txt\n"
                "printf '{\"ok\":true}'\n"
                "printf 'fake progress\\n' >&2\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)

            env = dict(os.environ)
            env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
            completed = subprocess.run(
                [str(runner), "--limit", "200", "--no-upload"],
                cwd=workdir,
                env=env,
                check=True,
                capture_output=True,
                text=True,
            )

            argv = (workdir / "argv.txt").read_text(encoding="utf-8")
            self.assertIn("--target local", argv)
            self.assertIn("--limit 200 --no-upload", argv)
            self.assertNotIn("--limit 2000", argv)
            self.assertEqual(
                (workdir / "result.json").read_text(encoding="utf-8"),
                '{"ok":true}',
            )
            self.assertEqual(
                (workdir / "run.log").read_text(encoding="utf-8"),
                "fake progress\n",
            )
            self.assertIn("fake progress", completed.stderr)
            self.assertIn("complete: result.json", completed.stderr)
            self.assertEqual(list(workdir.glob("result.json.tmp.*")), [])

    def test_failure_preserves_existing_result_and_cleans_temporary_output(self):
        source = Path(__file__).resolve().parents[1] / "run_theme.sh"
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            runner = workdir / "run_theme.sh"
            shutil.copy2(source, runner)
            (workdir / ".env.upload").write_text("", encoding="utf-8")
            (workdir / "result.json").write_text(
                '{"previous":true}', encoding="utf-8"
            )

            bindir = workdir / "bin"
            bindir.mkdir()
            fake_python = bindir / "python3"
            fake_python.write_text(
                "#!/bin/sh\n"
                "printf '{\"partial\":true}'\n"
                "printf 'stock rationale failure\\n' >&2\n"
                "exit 4\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)

            env = dict(os.environ)
            env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
            completed = subprocess.run(
                [str(runner), "--no-upload"],
                cwd=workdir,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.returncode, 4)
            self.assertEqual(
                (workdir / "result.json").read_text(encoding="utf-8"),
                '{"previous":true}',
            )
            self.assertEqual(
                (workdir / "run.log").read_text(encoding="utf-8"),
                "stock rationale failure\n",
            )
            self.assertIn("failed with status 4", completed.stderr)
            self.assertEqual(list(workdir.glob("result.json.tmp.*")), [])

    def test_empty_success_preserves_existing_result_and_is_not_complete(self):
        source = Path(__file__).resolve().parents[1] / "run_theme.sh"
        with tempfile.TemporaryDirectory() as tmp:
            workdir = Path(tmp)
            runner = workdir / "run_theme.sh"
            shutil.copy2(source, runner)
            (workdir / ".env.upload").write_text("", encoding="utf-8")
            (workdir / "result.json").write_text(
                '{"previous":true}', encoding="utf-8"
            )

            bindir = workdir / "bin"
            bindir.mkdir()
            fake_python = bindir / "python3"
            fake_python.write_text(
                "#!/bin/sh\n"
                "printf 'dry run completed without JSON\\n' >&2\n"
                "exit 0\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)

            env = dict(os.environ)
            env["PATH"] = f"{bindir}{os.pathsep}{env.get('PATH', '')}"
            completed = subprocess.run(
                [str(runner), "--dry-run"],
                cwd=workdir,
                env=env,
                check=False,
                capture_output=True,
                text=True,
            )

            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(
                (workdir / "result.json").read_text(encoding="utf-8"),
                '{"previous":true}',
            )
            self.assertEqual(
                (workdir / "run.log").read_text(encoding="utf-8"),
                "dry run completed without JSON\n",
            )
            self.assertNotIn("complete: result.json", completed.stderr)
            self.assertIn("empty", completed.stderr.lower())
            self.assertEqual(list(workdir.glob("result.json.tmp.*")), [])


if __name__ == "__main__":
    unittest.main()
