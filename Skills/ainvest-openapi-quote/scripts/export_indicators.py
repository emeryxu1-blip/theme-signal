#!/usr/bin/env python3
"""Export current AInvest indicator metadata from Tangram.

The exported workbook is the input for build_quote_csvs.py. By default this
script writes inside this skill under references/generated/ so the source
workbook and derived lookup CSVs stay self-contained.
"""

import argparse
import json
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from datetime import timezone
from pathlib import Path
from urllib import error
from urllib import request


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_ROOT = SCRIPT_DIR.parent
URL = "https://fuyao.myhexin.com/tangram-console-plus-backend/api/indicator/aggregate/v1/export_excel"
DEFAULT_OUTPUT = SKILL_ROOT / "references" / "generated" / "export_metric_meta_new.xlsx"
DEFAULT_PARAMS = {
    "keyword": "",
    "query_source": "ELASTICSEARCH",
    "page_num": 1,
    "page_size": 10000,
    "dir_full_path": [],
    "sub_dir": True,
    "online_status": [40],
    "support_api": True,
    "tenant_id": "ainvest",
    "export_all_data": True,
}


@dataclass
class RebuildResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass
class HttpResponse:
    status_code: int
    content: bytes
    text: str


def default_timestamped_output():
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return SCRIPT_DIR / f"indicators_{timestamp}.xlsx"


def age_text(age_seconds):
    if age_seconds is None:
        return "not available"
    if age_seconds < 60:
        return "less than 1 minute"

    minutes = age_seconds // 60
    hours = minutes // 60
    days = hours // 24
    if days:
        remaining_hours = hours % 24
        if remaining_hours:
            return f"{days} days {remaining_hours} hours"
        return f"{days} days"
    if hours:
        remaining_minutes = minutes % 60
        if remaining_minutes:
            return f"{hours} hours {remaining_minutes} minutes"
        return f"{hours} hours"
    return f"{minutes} minutes"


def local_data_status(path=DEFAULT_OUTPUT, now=None):
    path = Path(path)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    if not path.exists():
        return {
            "path": str(path),
            "exists": False,
            "updated_at": None,
            "age_seconds": None,
            "age_text": "not available",
            "size_bytes": 0,
        }

    stat = path.stat()
    updated_at = datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
    age_seconds = max(0, int((now - updated_at).total_seconds()))
    return {
        "path": str(path),
        "exists": True,
        "updated_at": updated_at.isoformat(timespec="seconds"),
        "age_seconds": age_seconds,
        "age_text": age_text(age_seconds),
        "size_bytes": stat.st_size,
    }


def format_status(status):
    if not status["exists"]:
        return f"Local Tangram workbook is missing: {status['path']}"
    return (
        "Local Tangram workbook last updated "
        f"{status['age_text']} ago at {status['updated_at']}: {status['path']}"
    )


def default_http_post(url, headers, json, timeout, urlopen=None):
    urlopen = urlopen or request.urlopen
    payload = globals()["json"].dumps(json, ensure_ascii=False).encode("utf-8")
    req = request.Request(url, data=payload, headers=headers, method="POST")
    try:
        handle = urlopen(req, timeout=timeout)
        content = handle.read()
        return HttpResponse(getattr(handle, "status", 200), content, "")
    except error.HTTPError as exc:
        content = exc.read()
        return HttpResponse(exc.code, content, content.decode("utf-8", errors="replace"))


def export_indicators(output_path=DEFAULT_OUTPUT, keyword="", page_size=10000, timeout=300, http_post=None):
    params = dict(DEFAULT_PARAMS)
    params["keyword"] = keyword
    params["page_size"] = page_size
    headers = {
        "Content-Type": "application/json",
    }

    http_post = http_post or default_http_post

    response = http_post(URL, headers=headers, json=params, timeout=timeout)
    if response.status_code != 200:
        raise RuntimeError(f"Tangram export failed ({response.status_code}): {response.text}")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(response.content)
    return output_path, len(response.content), params


def rebuild_generated_csvs(workbook_path=DEFAULT_OUTPUT, command_runner=None):
    command = [
        sys.executable,
        str(SCRIPT_DIR / "build_quote_csvs.py"),
        "--input",
        str(workbook_path),
    ]
    if command_runner is None:
        completed = subprocess.run(command, cwd=SKILL_ROOT, check=False, text=True, capture_output=True)
        return RebuildResult(completed.returncode, completed.stdout, completed.stderr)
    return command_runner(command, cwd=SKILL_ROOT, check=False, text=True, capture_output=True)


def update_indicators(
    output_path=DEFAULT_OUTPUT,
    keyword="",
    page_size=10000,
    timeout=300,
    http_post=None,
    rebuild_csvs=False,
    command_runner=None,
):
    path, byte_count, _params = export_indicators(
        output_path=output_path,
        keyword=keyword,
        page_size=page_size,
        timeout=timeout,
        http_post=http_post,
    )
    rebuild_result = None
    if rebuild_csvs:
        rebuild_result = rebuild_generated_csvs(path, command_runner=command_runner)
        if rebuild_result.returncode != 0:
            raise RuntimeError(
                "CSV rebuild failed "
                f"({rebuild_result.returncode}): {rebuild_result.stderr or rebuild_result.stdout}"
            )
    return path, byte_count, rebuild_result


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Export current AInvest indicator metadata from Tangram.")
    parser.add_argument(
        "output_file",
        nargs="?",
        help="Optional output .xlsx path. Defaults to references/generated/export_metric_meta_new.xlsx.",
    )
    parser.add_argument("--output", help="Output .xlsx path. Overrides the default path.")
    parser.add_argument("--keyword", default="", help="Optional Tangram keyword filter.")
    parser.add_argument("--page-size", type=int, default=10000, help="Tangram export page size.")
    parser.add_argument("--timeout", type=int, default=300, help="Request timeout in seconds.")
    parser.add_argument("--status", action="store_true", help="Show local workbook freshness without network access.")
    parser.add_argument("--status-json", action="store_true", help="Print local workbook freshness as JSON.")
    parser.add_argument("--rebuild-csvs", action="store_true", help="Rebuild generated lookup CSVs after export.")
    parser.add_argument(
        "--timestamped",
        action="store_true",
        help="Write indicators_<timestamp>.xlsx under scripts/ instead of the default generated path.",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    if args.output and args.output_file:
        print("Use either positional output_file or --output, not both.", file=sys.stderr)
        return 2

    output_file = args.output or args.output_file
    if output_file:
        output_path = Path(output_file)
    elif args.timestamped:
        output_path = default_timestamped_output()
    else:
        output_path = DEFAULT_OUTPUT

    if args.status:
        status = local_data_status(output_path)
        if args.status_json:
            print(json.dumps(status, ensure_ascii=False, indent=2))
        else:
            print(format_status(status))
        return 0

    try:
        path, byte_count, rebuild_result = update_indicators(
            output_path=output_path,
            keyword=args.keyword,
            page_size=args.page_size,
            timeout=args.timeout,
            rebuild_csvs=args.rebuild_csvs,
        )
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1

    status = local_data_status(path)
    params = dict(DEFAULT_PARAMS)
    params["keyword"] = args.keyword
    params["page_size"] = args.page_size
    print(f"Request params: {json.dumps(params, ensure_ascii=False)}")
    print(f"Exported {byte_count} bytes to: {path}")
    print(format_status(status))
    if rebuild_result:
        if rebuild_result.stdout:
            print(rebuild_result.stdout.strip())
        print("Rebuilt generated lookup CSVs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
