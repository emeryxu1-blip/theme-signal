"""Retry uploading an existing workflow result without rerunning the workflow."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from theme_upload import ThemeUploadError, configured_upload, upload_result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Upload an existing ThemeSignal result JSON file",
    )
    parser.add_argument(
        "result",
        nargs="?",
        default="result.json",
        help="existing workflow result (default: result.json)",
    )
    return parser


def main() -> int:
    args = build_parser().parse_args()
    path = Path(args.result)
    try:
        serialized = path.read_text(encoding="utf-8")
        parsed = json.loads(serialized)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        print(f"error: cannot read a valid result JSON file: {exc}", file=sys.stderr)
        return 2
    if not isinstance(parsed, dict) or not str(parsed.get("theme") or "").strip():
        print("error: result JSON must contain a non-empty theme", file=sys.stderr)
        return 2
    try:
        upload = configured_upload()
        if upload is None:
            raise ThemeUploadError("theme archive upload is not configured")
        archive_id = upload_result(
            serialized,
            endpoint=upload[0],
            token=upload[1],
        )
    except ThemeUploadError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 3
    print(archive_id)
    print(f"[theme-upload] uploaded existing result archive_id={archive_id}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
