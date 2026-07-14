#!/usr/bin/env python3
"""Validate quote request templates in this skill.

The validator checks static template structure, including indicator
`req_unique_id`, symbol attrs, page/sort bounds, basic quote code limits, and
supported time_range combinations. It also dry-runs representative templates
through `fetch_quote.py` to ensure endpoint inference still works.

Run this after changing files under `assets/request-templates/`. The validation
is local and uses `fetch_quote.py --dry-run`, so it does not need credentials and
does not send quote requests.
"""

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = ROOT / "assets" / "request-templates"
FETCH_SCRIPT = ROOT / "scripts" / "fetch_quote.py"

KNOWN_INDICATORS = {
    "10": "latest_price",
    "13": "volume",
    "19": "turnover",
    "55": "security_name",
    "199112": "change_pct",
    "65": "open_interest",
}

SNAPSHOT_SYMBOL_TYPES = {
    "market_code",
    "ths_code",
    "market",
    "block_id",
    "prompt_id",
    "link_code",
    "group_id",
    "prompt_id_self",
    "group_id_self",
    "macro_region",
    "macro_metric",
}


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def count_codes(code_list):
    total = 0
    for item in code_list:
        if isinstance(item, dict):
            codes = item.get("codes", [])
            if isinstance(codes, list):
                total += len(codes)
    return total


def is_snapshot_template(payload):
    return isinstance(payload.get("symbol"), list) or "page" in payload or "sort" in payload or "filter" in payload


def is_basic_quote_template(payload):
    return isinstance(payload.get("code_list"), list) and "indicator" not in payload


def is_relation_template(payload):
    return "relation" in payload and "symbol" in payload and "symbol_type" in payload and "indicator" not in payload


def validate_code_list(payload, errors):
    code_list = payload.get("code_list")
    if not isinstance(code_list, list) or not code_list:
        errors.append("missing non-empty code_list")
        return 0

    for idx, item in enumerate(code_list):
        if not isinstance(item, dict):
            errors.append(f"code_list[{idx}] must be an object")
            continue
        market = item.get("market")
        codes = item.get("codes")
        if not market:
            errors.append(f"code_list[{idx}] missing market")
        if not isinstance(codes, list) or not codes:
            errors.append(f"code_list[{idx}] missing non-empty codes")
            continue
        for code_idx, code in enumerate(codes):
            if not isinstance(code, str) or not code:
                errors.append(f"code_list[{idx}].codes[{code_idx}] must be a non-empty string")

    return count_codes(code_list)


def validate_multi_kline_template(payload):
    errors = []
    code_count = validate_code_list(payload, errors)
    if code_count > 16:
        errors.append("multi_kline supports at most 16 codes per request")

    trade_class = payload.get("trade_class")
    if not isinstance(trade_class, str) or not trade_class:
        errors.append("multi_kline missing trade_class")

    time_period = payload.get("time_period")
    if not isinstance(time_period, str) or not time_period:
        errors.append("multi_kline missing time_period")

    time_range = payload.get("time_range")
    if not isinstance(time_range, dict) or not time_range:
        errors.append("multi_kline missing time_range")
        return errors

    has_trade_date = "trade_date" in time_range
    has_count = "count" in time_range and isinstance(time_range.get("count"), int) and time_range["count"] > 0
    has_begin_time = "begin_time" in time_range
    has_end_time = "end_time" in time_range

    valid_combo = (
        has_trade_date
        or (has_count and has_end_time)
        or (has_begin_time and has_count)
        or (has_begin_time and has_end_time)
    )
    if not valid_combo:
        errors.append("multi_kline time_range must match a supported single_kline combination")

    if has_begin_time and has_end_time and time_range.get("begin_time") == 0 and time_range.get("end_time") == 0:
        errors.append("multi_kline begin_time and end_time cannot both be 0")

    return errors


def validate_single_tick_template(payload):
    errors = []
    code_count = validate_code_list(payload, errors)
    if code_count != 1:
        errors.append("single_tick requires exactly one code")

    trade_class = payload.get("trade_class")
    if not isinstance(trade_class, str) or not trade_class:
        errors.append("single_tick missing trade_class")

    time_range = payload.get("time_range")
    if not isinstance(time_range, dict):
        errors.append("single_tick missing time_range")
        return errors

    if time_range.get("trade_date") != 0:
        errors.append("single_tick time_range.trade_date must be 0")
    if not isinstance(time_range.get("count"), int) or time_range["count"] <= 0:
        errors.append("single_tick time_range.count must be a positive integer")
    if "end_time" not in time_range:
        errors.append("single_tick time_range missing end_time")

    return errors


def validate_relation_template(payload):
    errors = []

    if payload.get("relation") not in {"holding", "component"}:
        errors.append("relation must be holding or component")
    if not isinstance(payload.get("symbol"), str) or not payload["symbol"]:
        errors.append("relation symbol must be a non-empty string")
    if payload.get("symbol_type") not in {"market_code", "prompt_id", "block_id", "group_id"}:
        errors.append("relation symbol_type must be market_code, prompt_id, block_id, or group_id")

    page = payload.get("page")
    if page is not None:
        if not isinstance(page, dict):
            errors.append("relation page must be an object when provided")
        else:
            if not isinstance(page.get("begin"), int) or page["begin"] < 0:
                errors.append("relation page.begin must be a non-negative integer")
            if not isinstance(page.get("count"), int) or page["count"] <= 0:
                errors.append("relation page.count must be a positive integer")

    for unsupported in ("indicator", "sort", "filter"):
        if unsupported in payload:
            errors.append(f"relation templates do not support {unsupported}")

    return errors


def validate_indicator_template(path: Path, payload):
    errors = []
    is_related_template = path.name.startswith("related-")
    indicators = payload.get("indicator")
    if not isinstance(indicators, list) or not indicators:
        errors.append("missing non-empty indicator array")
        return errors

    req_ids = set()
    for idx, indicator in enumerate(indicators):
        req_unique_id = indicator.get("req_unique_id")
        if not req_unique_id:
            errors.append(f"indicator[{idx}] missing req_unique_id")
        elif req_unique_id in req_ids:
            errors.append(f"duplicate req_unique_id: {req_unique_id}")
        else:
            req_ids.add(req_unique_id)

        indicator_id = indicator.get("id")
        expected = KNOWN_INDICATORS.get(indicator_id)
        if expected and req_unique_id and req_unique_id != expected:
            errors.append(
                f"indicator[{idx}] id {indicator_id} expects req_unique_id '{expected}', got '{req_unique_id}'"
            )

    symbols = payload.get("symbol")
    if isinstance(symbols, list):
        seen_macro_type = None
        seen_non_macro = False
        for idx, symbol in enumerate(symbols):
            symbol_type = symbol.get("type")
            attr = symbol.get("attr", {})
            values = symbol.get("value", [])
            if symbol_type not in SNAPSHOT_SYMBOL_TYPES:
                errors.append(f"symbol[{idx}] unsupported snapshot type {symbol_type}")
            if not isinstance(values, list) or not values:
                errors.append(f"symbol[{idx}] missing non-empty value array")
            if symbol_type in {"macro_region", "macro_metric"}:
                if seen_non_macro:
                    errors.append("snapshot macro symbol types must not be mixed with other symbol types")
                if seen_macro_type and seen_macro_type != symbol_type:
                    errors.append("snapshot macro_region and macro_metric must not be mixed")
                seen_macro_type = symbol_type
                for value_idx, value in enumerate(values):
                    if not isinstance(value, str) or not value or ":" in value:
                        errors.append(f"symbol[{idx}].value[{value_idx}] macro value must be non-empty and omit ':'")
            else:
                seen_non_macro = True
                if seen_macro_type:
                    errors.append("snapshot macro symbol types must not be mixed with other symbol types")
            if symbol_type == "prompt_id" and is_related_template and "market_code" not in attr:
                errors.append(f"symbol[{idx}] prompt_id missing attr.market_code")
            if symbol_type == "prompt_id":
                for key in attr:
                    if key not in {"market_code", "min", "max", "value"}:
                        errors.append(f"symbol[{idx}] prompt_id unsupported attr.{key}")
            if symbol_type == "link_code" and "link_type" not in attr:
                errors.append(f"symbol[{idx}] link_code missing attr.link_type")
            if symbol_type == "link_code" and attr.get("link_type") == "holding" and len(values) != 1:
                errors.append(f"symbol[{idx}] holding requests must contain exactly one code")
            if symbol_type == "market":
                if len(values) != 1 or values[0] not in {"UAOS", "UDC"}:
                    errors.append(f"symbol[{idx}] market value must be exactly one of UAOS or UDC")
    elif isinstance(symbols, dict):
        if symbols.get("type") == "prompt_id" and "market_code" not in symbols.get("attr", {}):
            errors.append("series symbol prompt_id missing attr.market_code")

    sort_items = payload.get("sort", [])
    for idx, sort_item in enumerate(sort_items):
        pos = sort_item.get("pos")
        if not isinstance(pos, int) or pos < 0 or pos >= len(indicators):
            errors.append(f"sort[{idx}] pos {pos} is out of range for {len(indicators)} indicators")

    if is_snapshot_template(payload):
        page = payload.get("page")
        if page is None:
            errors.append("snapshot template missing page")
        elif not isinstance(page.get("count"), int) or page["count"] <= 0:
            errors.append("snapshot page.count must be a positive integer")
        res_symbol_type = payload.get("res_symbol_type")
        if res_symbol_type is not None and res_symbol_type not in {"market_code", "ths_code"}:
            errors.append("snapshot res_symbol_type must be market_code or ths_code")
        if "full_symbols" in payload and not isinstance(payload["full_symbols"], bool):
            errors.append("snapshot full_symbols must be boolean when provided")

    return errors


def validate_template(path: Path):
    payload = load_json(path)

    if is_relation_template(payload):
        return validate_relation_template(payload)

    if is_basic_quote_template(payload):
        if path.name == "multi-kline-minute.json":
            return validate_multi_kline_template(payload)
        if path.name == "single-tick.json":
            return validate_single_tick_template(payload)
        return ["unknown basic quote template; add validator coverage"]

    return validate_indicator_template(path, payload)


def main():
    failures = []
    for path in sorted(TEMPLATE_DIR.glob("*.json")):
        errors = validate_template(path)
        if errors:
            failures.append((path, errors))

    dry_run_templates = {
        "stock-detail.json": "snapshot",
        "macro-region-list.json": "snapshot",
        "macro-metric-snapshot.json": "snapshot",
        "series-etf-30d.json": "series",
        "series-macro-metric-history.json": "series",
        "relation-etf-holding.json": "relation_list",
        "multi-kline-minute.json": "multi_kline",
        "single-tick.json": "single_tick",
    }
    for template_name, expected_endpoint in dry_run_templates.items():
        result = subprocess.run(
            [
                sys.executable,
                str(FETCH_SCRIPT),
                "--template",
                template_name,
                "--dry-run",
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            failures.append((FETCH_SCRIPT, [f"fetch_quote dry-run failed for {template_name}: {result.stderr.strip()}"]))
            continue
        try:
            output = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            failures.append((FETCH_SCRIPT, [f"fetch_quote dry-run output is not JSON for {template_name}: {exc}"]))
            continue
        if output.get("endpoint_name") != expected_endpoint:
            failures.append(
                (
                    FETCH_SCRIPT,
                    [
                        f"fetch_quote inferred {output.get('endpoint_name')} for {template_name}, expected {expected_endpoint}"
                    ],
                )
            )

    if failures:
        for path, errors in failures:
            print(f"{path.relative_to(ROOT)}")
            for error in errors:
                print(f"  - {error}")
        return 1

    print(f"Validated {len(list(TEMPLATE_DIR.glob('*.json')))} templates.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
