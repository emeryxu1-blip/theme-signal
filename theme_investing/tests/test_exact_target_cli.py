"""CLI contract tests for exact stock and ETF output targets."""

from __future__ import annotations

import argparse
import io
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cli
from workflow import SelectionUniverseError, StockUniverseError


def _options(*arguments: str) -> dict:
    args = cli.build_parser().parse_args([*arguments, "{}"])
    return cli.workflow_options(args)


class ExactTargetCliTests(unittest.TestCase):
    def _run_with_workflow_failure(
        self, failure: Exception
    ) -> tuple[int, str, str, object]:
        payload = (
            '{"theme":"AI","date":"2026-01-01",'
            '"url":"https://example.test/article"}'
        )

        def fail(_payload: dict) -> None:
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
                redirect_stdout(stdout), redirect_stderr(stderr):
            status = cli.main()

        return status, stdout.getvalue(), stderr.getvalue(), configured_upload

    def test_exact_targets_have_documented_defaults(self) -> None:
        parser = cli.build_parser()
        args = parser.parse_args(["{}"])

        self.assertEqual(args.stock_target, 8)
        self.assertEqual(args.etf_target, 5)
        self.assertEqual(cli.workflow_options(args)["stock_target"], 8)
        self.assertEqual(cli.workflow_options(args)["etf_target"], 5)

        help_text = " ".join(parser.format_help().split())
        self.assertIn("exact number of ranked stocks", help_text)
        self.assertIn("0 disables stocks (default: 8)", help_text)
        self.assertIn("exact number of ranked ETFs", help_text)
        self.assertIn("0 disables ETFs (default: 5)", help_text)

    def test_exact_targets_reject_negative_values(self) -> None:
        for option in ("--stock-target", "--etf-target"):
            with self.subTest(option=option), \
                    redirect_stderr(io.StringIO()) as stderr, \
                    self.assertRaises(SystemExit):
                cli.build_parser().parse_args([option, "-1", "{}"])
            self.assertIn("must be 0 or greater", stderr.getvalue())

    def test_zero_disables_each_exact_output_class(self) -> None:
        opts = _options("--stock-target", "0", "--etf-target", "0")

        self.assertEqual(opts["stock_target"], 0)
        self.assertEqual(opts["etf_target"], 0)

    def test_exact_targets_are_preflight_bounded(self) -> None:
        cases = [
            (
                ("--stock-target", "9", "--stock-candidate-budget", "8"),
                "--stock-target cannot exceed --stock-candidate-budget",
            ),
            (
                ("--stock-target", "9", "--stock-universe", "8"),
                "--stock-target cannot exceed the effective stock universe",
            ),
            (
                ("--limit", "7", "--etf-target", "0"),
                "--stock-target cannot exceed the effective stock universe",
            ),
            (
                ("--etf-target", "6", "--etf-universe", "5"),
                "--etf-target cannot exceed the effective ETF universe",
            ),
            (
                ("--limit", "4", "--stock-target", "4"),
                "--etf-target cannot exceed the effective ETF universe",
            ),
            (
                ("--etf-target", "501"),
                "--etf-target cannot exceed the effective ETF universe",
            ),
            (
                ("--max-scan", "7", "--etf-target", "0"),
                "--stock-target cannot exceed the effective stock universe",
            ),
            (
                ("--max-scan", "4", "--stock-target", "4"),
                "--etf-target cannot exceed the effective ETF universe",
            ),
        ]
        for arguments, message in cases:
            with self.subTest(arguments=arguments), \
                    self.assertRaisesRegex(ValueError, message):
                args = cli.build_parser().parse_args([*arguments, "{}"])
                cli.workflow_options(args)

    def test_per_asset_universe_overrides_drive_target_preflight(self) -> None:
        opts = _options(
            "--limit", "4",
            "--stock-universe", "8",
            "--etf-universe", "5",
        )

        self.assertEqual(opts["stock_universe"], 8)
        self.assertEqual(opts["etf_universe"], 5)

    def test_main_checks_target_bounds_before_reading_input_or_config(self) -> None:
        stderr = io.StringIO()
        with patch.object(
                sys, "argv", ["cli.py", "--limit", "4", "--stock-target", "4"]), \
                patch("cli._read_payload") as read_payload, \
                patch("cli.load_env") as load_env, \
                redirect_stderr(stderr), self.assertRaises(SystemExit) as raised:
            cli.main()

        self.assertEqual(raised.exception.code, 2)
        self.assertIn("--etf-target cannot exceed", stderr.getvalue())
        read_payload.assert_not_called()
        load_env.assert_not_called()

    def test_nonnegative_parser_preserves_argparse_error_type(self) -> None:
        with self.assertRaisesRegex(
                argparse.ArgumentTypeError, "must be an integer"):
            cli._nonnegative_int("not-an-integer")

    def test_selection_universe_failure_is_concise_and_skips_upload(self) -> None:
        failure = SelectionUniverseError(
            "etf", expected=5, available=3,
            sources=("curated-pools", "stock-relations"),
        )

        status, stdout, stderr, configured_upload = (
            self._run_with_workflow_failure(failure)
        )

        self.assertEqual(status, 2)
        self.assertEqual(stdout, "")
        self.assertIn(
            "error: etf selection universe exhausted: expected=5 available=3 "
            "sources=curated-pools,stock-relations",
            stderr,
        )
        configured_upload.assert_not_called()

    def test_legacy_stock_universe_failure_keeps_its_diagnostic(self) -> None:
        status, stdout, stderr, configured_upload = (
            self._run_with_workflow_failure(
                StockUniverseError("snapshot request failed")
            )
        )

        self.assertEqual(status, 2)
        self.assertEqual(stdout, "")
        self.assertIn(
            "error: stock universe retrieval failed: snapshot request failed",
            stderr,
        )
        configured_upload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
