"""Regression tests for the convenience runner."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class RunThemeTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
