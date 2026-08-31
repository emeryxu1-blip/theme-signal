"""Offline tests for retrying an existing result upload."""
from __future__ import annotations

import contextlib
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import upload_existing


class UploadExistingTests(unittest.TestCase):
    def test_uploads_exact_existing_bytes_without_running_workflow(self):
        serialized = '{"theme":"Bitcoin Surged","ThemeStocks":[{"market_code":"186:CLSK"}]}'
        with tempfile.TemporaryDirectory() as directory:
            result_path = Path(directory) / "result.json"
            result_path.write_text(serialized, encoding="utf-8")
            stdout, stderr = io.StringIO(), io.StringIO()
            with (
                patch.object(sys, "argv", ["upload_existing.py", str(result_path)]),
                patch(
                    "upload_existing.configured_upload",
                    return_value=("https://example.test/api/themes", "secret"),
                ),
                patch("upload_existing.upload_result", return_value="theme_existing") as upload,
                contextlib.redirect_stdout(stdout),
                contextlib.redirect_stderr(stderr),
            ):
                status = upload_existing.main()

        self.assertEqual(status, 0)
        upload.assert_called_once_with(
            serialized,
            endpoint="https://example.test/api/themes",
            token="secret",
        )
        self.assertEqual(stdout.getvalue().strip(), "theme_existing")
        self.assertIn("uploaded existing result", stderr.getvalue())

    def test_rejects_invalid_result_before_upload(self):
        with tempfile.TemporaryDirectory() as directory:
            result_path = Path(directory) / "result.json"
            result_path.write_text("{}", encoding="utf-8")
            stderr = io.StringIO()
            with (
                patch.object(sys, "argv", ["upload_existing.py", str(result_path)]),
                patch("upload_existing.upload_result") as upload,
                contextlib.redirect_stderr(stderr),
            ):
                status = upload_existing.main()

        self.assertEqual(status, 2)
        upload.assert_not_called()
        self.assertIn("non-empty theme", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
