#!/usr/bin/env python3
"""Build generated quote lookup CSVs for the ainvest-openapi-quote skill.

Input is the exported Tangram metric workbook, usually
`references/generated/export_metric_meta_new.xlsx` inside this skill. The script
writes two generated reference files under `references/generated/`:

- `id_dict_quote.csv`: compact id-dictionary rows derived from the workbook.
- `quote_request_lookup.csv`: request-oriented rows containing endpoint,
  template, attrs, category, and time_range hints.

Legacy rules in `references/legacy/id_dict.md` intentionally override workbook
attrs and time_range hints where that file has stronger request requirements.
The script only builds local metadata; it never performs network requests and
must not be given API keys, cookies, or other credentials.
"""

import argparse
import csv
import json
import re
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_INPUT_CANDIDATES = [
    ROOT / "references" / "generated" / "export_metric_meta_new.xlsx",
]
DEFAULT_INPUT = next((path for path in DEFAULT_INPUT_CANDIDATES if path.exists()), DEFAULT_INPUT_CANDIDATES[0])
DEFAULT_OUT_DIR = ROOT / "references" / "generated"
DEFAULT_ID_DICT_REFERENCE = ROOT.parent / "indexapi-id-config" / "references" / "id_dict.csv"
DEFAULT_ROUTER_CONFIG = ROOT.parent / "indexapi-id-config" / "references" / "id_router.yaml"
DEFAULT_LEGACY_ID_DICT = ROOT / "references" / "legacy" / "id_dict.md"
AUTO_MARKER = "[AUTO_ATTRS_FROM_ID_DICT]"

HEADER_INDICATOR_CODE = "*\u6307\u6807\u7f16\u7801"
HEADER_INDEXAPI_CODE = "*IndexAPI\u4ee3\u7801"
HEADER_SOURCE_CODE = "*\u6765\u6e90\u4ee3\u7801"
HEADER_STANDARD_CODE = "*\u6807\u51c6\u7f16\u7801"
HEADER_METRIC_NAME = "*\u6307\u6807\u540d\u79f0"
HEADER_ENGLISH_NAME = "\u82f1\u6587\u540d\u79f0"
HEADER_DESCRIPTION = "*\u6307\u6807\u63cf\u8ff0"
HEADER_SOURCE = "*\u6307\u6807\u6765\u6e90"
HEADER_PERIODS = "*\u652f\u6301\u5468\u671f"
HEADER_VALUE_TYPE = "\u663e\u793a\u503c\u7c7b\u578b"
HEADER_UNIT = "\u6307\u6807\u5355\u4f4d"
HEADER_SOURCE_ALIASES = "\u6765\u6e90\u6307\u6807\u4ee3\u7801\u522b\u540d"
HEADER_EXTENDED_ATTRS = "\u6269\u5c55\u5c5e\u6027"
HEADER_SORTABLE = "\u652f\u6301\u6392\u5e8f"
HEADER_ACCESS_GUIDE = "\u63a5\u5165\u6307\u5357\u6807\u7b7e"
HEADER_ENTITY_TYPE = "\u8bc1\u5238\u5b9e\u4f53\u7c7b\u578b"
HEADER_EXCHANGE = "\u4ea4\u6613\u6240"

HEADER_ALIASES = {
    HEADER_INDEXAPI_CODE: [HEADER_INDEXAPI_CODE],
    HEADER_SOURCE_CODE: [HEADER_SOURCE_CODE],
    HEADER_STANDARD_CODE: [HEADER_STANDARD_CODE],
    HEADER_METRIC_NAME: [HEADER_METRIC_NAME],
    HEADER_ENGLISH_NAME: [HEADER_ENGLISH_NAME],
    HEADER_DESCRIPTION: [HEADER_DESCRIPTION],
    HEADER_SOURCE: [HEADER_SOURCE],
    HEADER_PERIODS: [HEADER_PERIODS],
    HEADER_VALUE_TYPE: [HEADER_VALUE_TYPE],
    HEADER_UNIT: [HEADER_UNIT],
    HEADER_SOURCE_ALIASES: [HEADER_SOURCE_ALIASES],
    HEADER_EXTENDED_ATTRS: [HEADER_EXTENDED_ATTRS],
    HEADER_SORTABLE: [HEADER_SORTABLE],
    HEADER_ACCESS_GUIDE: [HEADER_ACCESS_GUIDE],
    HEADER_ENTITY_TYPE: [HEADER_ENTITY_TYPE],
    HEADER_EXCHANGE: [HEADER_EXCHANGE],
}

REQUIRED_HEADER_KEYS = {HEADER_INDEXAPI_CODE, HEADER_EXTENDED_ATTRS}
LEGACY_REQUIRED_HEADER_KEYS = {HEADER_SOURCE_CODE, HEADER_STANDARD_CODE, HEADER_EXTENDED_ATTRS}

ID_DICT_FIELDS = [
    "id",
    "category",
    "periods",
    "sortable",
    "attrs",
    "value_type",
    "unit",
    "aliases",
    "from",
    "describe",
]

LOOKUP_FIELDS = [
    "query_key",
    "category",
    "scenario",
    "endpoint",
    "symbol_type",
    "indicator_id",
    "req_unique_id",
    "attrs_json",
    "time_range_json",
    "template",
    "periods",
    "metric_name",
    "english_name",
    "description",
    "aliases",
    "is_interval_metric",
    "period_values_json",
    "exchange",
    "access_guide",
]

SUPPLEMENTAL_ALIAS_MAP = {
    "inr-price_change_ratio_pct-sum": [
        "\u533a\u95f4\u6536\u76d8\u4ef7\u6da8\u5e45",
        "\u533a\u95f4\u6da8\u5e45",
    ],
    "inr-open_price_change_ratio_pct-sum": [
        "\u533a\u95f4\u5f00\u76d8\u4ef7\u6da8\u5e45",
        "\u533a\u95f4\u6da8\u5e45(\u5f00\u76d8\u4ef7\u8ba1\u7b97)",
    ],
    "inr-price_change-sum": [
        "\u533a\u95f4\u6536\u76d8\u4ef7\u6da8\u8dcc\u989d",
        "\u533a\u95f4\u6da8\u8dcc",
    ],
    "inr-open_price_change-sum": [
        "\u533a\u95f4\u5f00\u76d8\u4ef7\u6da8\u8dcc\u989d",
        "\u533a\u95f4\u6da8\u8dcc(\u5f00\u76d8\u4ef7\u8ba1\u7b97)",
    ],
}

KNOWN_ATTR_NAMES = ["time_period", "trade_class", "period_type", "match_code", "event_id", "tech_param"]


def recent_trading_day(today=None):
    today = today or date.today()
    candidate = today - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate


def recent_trading_day_begin_time_ms(today=None):
    trading_day = recent_trading_day(today)
    timestamp = datetime.combine(trading_day, time.min, tzinfo=timezone.utc)
    return int(timestamp.timestamp() * 1000)


@dataclass
class BuildResult:
    rows_read: int
    rows_written: int
    warnings: list[str]
    id_dict_path: Path
    lookup_path: Path


def import_openpyxl():
    try:
        import openpyxl
    except ImportError as exc:
        raise RuntimeError("openpyxl is required. Install it with: python -m pip install openpyxl") from exc
    return openpyxl


def clean_cell(value):
    if value is None:
        return ""
    return str(value).strip()


def load_rows(path):
    if not path.exists():
        raise FileNotFoundError(f"Input workbook not found: {path}")
    if path.suffix.lower() != ".xlsx":
        raise ValueError("Only .xlsx input is supported.")
    openpyxl = import_openpyxl()
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    sheet = workbook.active
    return [[clean_cell(cell) for cell in row] for row in sheet.iter_rows(values_only=True)]


def find_header(rows):
    for index, row in enumerate(rows):
        header = {}
        values = {name: row_index for row_index, name in enumerate(row) if name}
        for canonical, aliases in HEADER_ALIASES.items():
            for alias in aliases:
                if alias in values:
                    header[canonical] = values[alias]
                    break
        if REQUIRED_HEADER_KEYS.issubset(header) or LEGACY_REQUIRED_HEADER_KEYS.issubset(header):
            return index, header
    required = ", ".join(sorted(REQUIRED_HEADER_KEYS))
    raise ValueError(f"Cannot find Excel header row containing: {required}")


def value_at(row, header, name):
    index = header.get(name)
    if index is None or index >= len(row):
        return ""
    return row[index]


def parse_json_array(text, source):
    text = (text or "").strip()
    if not text:
        return []
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{source} is not valid JSON: {exc}") from exc
    if not isinstance(parsed, list):
        raise ValueError(f"{source} must be a JSON array")
    return parsed


def parse_extended_attrs(raw):
    raw = (raw or "").strip()
    if not raw:
        return [], []
    if AUTO_MARKER not in raw:
        return parse_json_array(raw, HEADER_EXTENDED_ATTRS), []

    main_text, auto_text = raw.split(AUTO_MARKER, 1)
    main_attrs = parse_json_array(main_text, HEADER_EXTENDED_ATTRS) if main_text.strip() else []
    auto_attrs = parse_json_array(auto_text, "AUTO_ATTRS_FROM_ID_DICT") if auto_text.strip() else []
    return main_attrs, auto_attrs


def default_from_attr(attr):
    if "default_value" in attr:
        return attr.get("default_value")
    if "default" in attr:
        return attr.get("default")
    return None


def normalize_time_period_value(value):
    if value in (None, ""):
        return value
    return str(value).strip().lower()


def normalize_time_period_fields(name, attr):
    if name != "time_period":
        return attr
    normalized = dict(attr)
    for key in ["default", "example"]:
        if normalized.get(key) not in (None, ""):
            normalized[key] = normalize_time_period_value(normalized[key])
    enum_options = []
    for option in normalized.get("enum_options") or []:
        if isinstance(option, dict):
            option = dict(option)
            if option.get("value") not in (None, ""):
                option["value"] = normalize_time_period_value(option["value"])
        enum_options.append(option)
    if enum_options:
        normalized["enum_options"] = enum_options
    return normalized


def normalize_attr(attr, source):
    name = clean_cell(attr.get("name"))
    if not name:
        return None
    normalized = {
        "name": name,
        "required": clean_cell(attr.get("required")),
        "query_type": attr.get("query_type"),
        "default": default_from_attr(attr),
        "example": attr.get("example"),
        "enum_options": attr.get("enum_options"),
        "children": attr.get("children"),
        "source": source,
    }
    return normalize_time_period_fields(name, normalized)


def attrs_to_lookup(main_attrs, auto_attrs):
    attrs = {}
    for attr in main_attrs:
        normalized = normalize_attr(attr, "table")
        if normalized:
            attrs[normalized["name"]] = normalized
    for attr in auto_attrs:
        normalized = normalize_attr(attr, "auto")
        if normalized and normalized["name"] not in attrs:
            attrs[normalized["name"]] = normalized
    return attrs


def split_markdown_row(line):
    line = line.strip()
    if not line.startswith("|"):
        return []
    return [cell.strip() for cell in line.strip("|").split("|")]


def parse_legacy_required(value):
    return "1" if any(token in value for token in ["必选", "required", "must"]) else ""


def parse_legacy_time_period_default(value):
    match = re.search(r"\b((?:min|day|week|month|year|quarter|hour)_\d+)\b", value, flags=re.IGNORECASE)
    if match:
        return normalize_time_period_value(match.group(1))
    return None


def attrs_from_legacy_text(value):
    text = clean_cell(value)
    if not text:
        return {}
    attrs = {}
    for name in KNOWN_ATTR_NAMES:
        if name not in text:
            continue
        attr = {
            "name": name,
            "required": parse_legacy_required(text),
            "query_type": None,
            "default": None,
            "example": None,
            "enum_options": None,
            "children": None,
            "source": "legacy",
        }
        if name == "time_period":
            attr["default"] = parse_legacy_time_period_default(text)
        attrs[name] = normalize_time_period_fields(name, attr)
    return attrs


def load_legacy_attrs(path=DEFAULT_LEGACY_ID_DICT):
    path = Path(path)
    if not path.exists():
        return {}
    attrs_by_id = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    for line in lines:
        cells = split_markdown_row(line)
        if len(cells) < 4:
            continue
        metric_id = clean_cell(cells[0])
        attr_text = cells[3]
        if not metric_id or metric_id in {"id", ":------------------------------------------------"} or set(metric_id) <= {":", "-"}:
            continue
        attrs = attrs_from_legacy_text(attr_text)
        if attrs:
            attrs_by_id[metric_id] = attrs
    return attrs_by_id


def markdown_separator_cell(value):
    cleaned = clean_cell(value)
    return bool(cleaned) and set(cleaned) <= {":", "-"}


def normalize_header_name(value):
    return clean_cell(value).lower().replace(" ", "")


def load_legacy_time_ranges(path=DEFAULT_LEGACY_ID_DICT):
    path = Path(path)
    if not path.exists():
        return {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except UnicodeDecodeError:
        lines = path.read_text(encoding="utf-8-sig").splitlines()

    header = None
    rules = {}
    for line in lines:
        cells = split_markdown_row(line)
        if len(cells) < 3:
            continue
        normalized = [normalize_header_name(cell) for cell in cells]
        if "id" in normalized and any("time_range" in cell for cell in normalized):
            header = {name: index for index, name in enumerate(normalized)}
            continue
        if header is None:
            continue
        metric_id_index = header.get("id", 0)
        if metric_id_index >= len(cells):
            continue
        metric_id = clean_cell(cells[metric_id_index])
        if not metric_id or markdown_separator_cell(metric_id):
            continue
        time_range_index = next((index for name, index in header.items() if "time_range" in name), None)
        if time_range_index is None or time_range_index >= len(cells):
            continue
        period_index = next((index for name, index in header.items() if name in {"time_period", "支持周期(time_period)"}), None)
        period_text = clean_cell(cells[period_index]) if period_index is not None and period_index < len(cells) else ""
        time_range_text = clean_cell(cells[time_range_index])
        if time_range_text:
            rules[metric_id] = {"time_period": normalize_periods(period_text), "time_range": time_range_text}
    return rules


def merge_legacy_attrs(attrs, legacy_attrs):
    if not legacy_attrs:
        return attrs
    merged = {}
    for name, legacy_attr in legacy_attrs.items():
        base = dict(attrs.get(name) or {})
        for key, value in legacy_attr.items():
            if value not in (None, "", []):
                base[key] = value
        base["name"] = name
        base["source"] = "legacy"
        merged[name] = normalize_time_period_fields(name, base)
    for name, attr in attrs.items():
        if name not in merged:
            merged[name] = attr
    return merged


def attrs_to_id_dict(attrs):
    parts = []
    for name, attr in attrs.items():
        default = attr.get("default")
        if attr.get("source") == "auto" and default not in (None, ""):
            parts.append(f"{name}:{default}")
        else:
            parts.append(name)
    return ";".join(parts)


def dedupe_keep_order(values):
    out = []
    seen = set()
    for value in values:
        value = clean_cell(value)
        if not value or value in seen:
            continue
        seen.add(value)
        out.append(value)
    return out


def split_aliases(value):
    normalized = clean_cell(value).replace(",", ";").replace("|", ";")
    return dedupe_keep_order(normalized.split(";"))


def supplemental_aliases(indicator_id, metric_name):
    aliases = list(SUPPLEMENTAL_ALIAS_MAP.get(indicator_id, []))
    if indicator_id.startswith("inr-") and metric_name:
        aliases.append(metric_name)
    return dedupe_keep_order(aliases)


def is_interval_metric(indicator_id, attrs):
    return str(indicator_id).startswith("inr-") or ("time_period" in attrs and "period_type" in attrs)


def period_values(attrs):
    time_period = attrs.get("time_period") or {}
    values = []
    for option in time_period.get("enum_options") or []:
        if isinstance(option, dict):
            values.append(normalize_time_period_value(option.get("value")))
    return dedupe_keep_order(values)


def periods_from_attrs(attrs):
    return ";".join(value for value in period_values(attrs) if value)


def normalize_periods(value):
    values = []
    for item in clean_cell(value).replace(",", ";").split(";"):
        item = item.strip()
        if item:
            values.append(item.lower())
    return ";".join(values)


def sortable_value(value):
    normalized = clean_cell(value).lower()
    return "TRUE" if normalized in {"1", "true", "yes", "y"} else "FALSE"


def split_tags(value):
    text = clean_cell(value)
    return dedupe_keep_order(re.split(r"[;,\s]+", text)) if text else []


def choose_endpoint_from_access_guide(access_guide, periods):
    tags = set(split_tags(access_guide))
    if any(tag.startswith("STANDARD_SERIES") for tag in tags):
        return "series"
    if any(tag.startswith("STANDARD_SNAPSHOT") for tag in tags):
        return "snapshot"
    return choose_endpoint(periods)


def build_time_range_from_access_guide(endpoint, periods, access_guide, begin_time_ms=None):
    if endpoint != "series":
        return None
    tags = set(split_tags(access_guide))
    first_period = first_period_from_rule(periods)
    if "STANDARD_SERIES_TIME_RANGE_BEGIN_END" in tags:
        return {
            "type": "begin_end",
            "begin_time": begin_time_ms or recent_trading_day_begin_time_ms(),
            "end_time": 0,
            "time_period": first_period,
        }
    if "STANDARD_SERIES_TIME_RANGE_ALL" in tags:
        return {
            "type": "end_count",
            "end_time": 0,
            "count": 30,
            "time_period": first_period,
            "supported_types": ["begin_end", "end_count"],
        }
    if "STANDARD_SERIES_TIME_RANGE_END_COUNT" in tags or "STANDARD_SERIES_MARKET_ENV" in tags:
        return {"type": "end_count", "end_time": 0, "count": 30, "time_period": first_period}
    return None


def choose_endpoint(periods):
    period_values = [item for item in periods.split(";") if item]
    if period_values and all(item != "snapshot" for item in period_values):
        return "series"
    return "snapshot"


def choose_template(endpoint):
    if endpoint == "series":
        return "series-etf-30d.json"
    return "stock-detail.json"


def first_period_from_rule(periods, legacy_rule=None):
    for source in [clean_cell((legacy_rule or {}).get("time_period")), periods]:
        for item in clean_cell(source).replace(",", ";").split(";"):
            item = item.strip().lower()
            if item and item != "snapshot":
                return item
    return "day_1"


def build_time_range_from_legacy_rule(period, legacy_rule, begin_time_ms=None):
    text = clean_cell((legacy_rule or {}).get("time_range")).lower()
    if not text:
        return None
    if "trade_date" in text:
        return {"type": "trade_date", "trade_date": 0, "time_period": period}
    if "begin_end" in text:
        return {
            "type": "begin_end",
            "begin_time": begin_time_ms or recent_trading_day_begin_time_ms(),
            "end_time": 0,
            "time_period": period,
        }
    if "end_count" in text:
        count = 30
        count_match = re.search(r"count\s*[:=]\s*(\d+)", text)
        if count_match:
            count = int(count_match.group(1))
        return {"type": "end_count", "end_time": 0, "count": count, "time_period": period}
    return None


def build_time_range_json(endpoint, periods, legacy_rule=None, begin_time_ms=None):
    if endpoint != "series":
        return "{}"
    first_period = first_period_from_rule(periods, legacy_rule)
    legacy_time_range = build_time_range_from_legacy_rule(first_period, legacy_rule, begin_time_ms)
    if legacy_time_range:
        return json.dumps(legacy_time_range, ensure_ascii=False, separators=(",", ":"))
    return json.dumps(
        {"type": "end_count", "end_time": 0, "count": 30, "time_period": first_period},
        ensure_ascii=False,
        separators=(",", ":"),
    )


def build_time_range_json_for_row(endpoint, periods, access_guide, legacy_rule=None, begin_time_ms=None):
    guide_time_range = build_time_range_from_access_guide(endpoint, periods, access_guide, begin_time_ms)
    if guide_time_range:
        return json.dumps(guide_time_range, ensure_ascii=False, separators=(",", ":"))
    return build_time_range_json(endpoint, periods, legacy_rule, begin_time_ms)


def load_category_map(path=DEFAULT_ID_DICT_REFERENCE):
    path = Path(path)
    if not path.exists():
        return {}
    categories = {}
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        for row in csv.DictReader(handle):
            indicator_id = clean_cell(row.get("id"))
            category = clean_cell(row.get("category"))
            if indicator_id and category:
                categories[indicator_id] = category
    return categories


def import_yaml():
    try:
        import yaml
    except ImportError:
        return None
    return yaml


def parse_simple_yaml_value(value):
    value = value.strip()
    if value in {"", "null", "Null", "NULL", "~"}:
        return None
    if value in {"true", "True", "TRUE"}:
        return True
    if value in {"false", "False", "FALSE"}:
        return False
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        if not inner:
            return []
        return [parse_simple_yaml_value(item.strip()) for item in inner.split(",")]
    if (
        (value.startswith('"') and value.endswith('"'))
        or (value.startswith("'") and value.endswith("'"))
    ):
        return value[1:-1]
    return value


def split_simple_yaml_pair(text):
    if ":" not in text:
        return text, None
    key, value = text.split(":", 1)
    return key.strip(), parse_simple_yaml_value(value)


def load_router_config_without_yaml(path):
    config = {"router": []}
    current_service = None
    current_route = None

    with path.open("r", encoding="utf-8") as handle:
        for raw_line in handle:
            line = raw_line.split("#", 1)[0].rstrip()
            if not line.strip():
                continue
            indent = len(line) - len(line.lstrip(" "))
            text = line.strip()

            if indent == 0 and text == "router:":
                continue
            if indent == 2 and text.startswith("- "):
                current_service = {}
                current_route = None
                config["router"].append(current_service)
                rest = text[2:].strip()
                if rest:
                    key, value = split_simple_yaml_pair(rest)
                    current_service[key] = value
                continue
            if current_service is None:
                continue
            if indent == 4 and text == "routes:":
                current_service.setdefault("routes", [])
                current_route = None
                continue
            if indent == 4 and not text.startswith("- "):
                key, value = split_simple_yaml_pair(text)
                current_service[key] = value
                continue
            if indent == 6 and text.startswith("- "):
                current_route = {}
                current_service.setdefault("routes", []).append(current_route)
                rest = text[2:].strip()
                if rest:
                    key, value = split_simple_yaml_pair(rest)
                    current_route[key] = value
                continue
            if indent == 8 and current_route is not None:
                key, value = split_simple_yaml_pair(text)
                current_route[key] = value

    return config


def load_router_map(path=DEFAULT_ROUTER_CONFIG):
    path = Path(path)
    if not path.exists():
        return {}
    yaml = import_yaml()
    if yaml:
        with path.open("r", encoding="utf-8") as handle:
            config = yaml.safe_load(handle) or {}
    else:
        config = load_router_config_without_yaml(path)
    router_map = {}
    for service in config.get("router") or []:
        markets = [str(market) for market in service.get("markets") or []]
        for route in service.get("routes") or []:
            periods = [normalize_time_period_value(period) for period in route.get("periods") or []]
            sortable = bool(route.get("sortable"))
            for indicator_id in route.get("ids") or []:
                key = str(indicator_id)
                entry = router_map.setdefault(key, {"periods": [], "sortable": False, "markets": []})
                entry["periods"].extend(periods)
                entry["sortable"] = entry["sortable"] or sortable
                entry["markets"].extend(markets)
    for entry in router_map.values():
        entry["periods"] = dedupe_keep_order(entry["periods"])
        entry["markets"] = dedupe_keep_order(entry["markets"])
    return router_map


def resolve_category(indicator_id, category_map):
    return category_map.get(indicator_id, "security")


def symbol_type_for_category(category):
    if category == "market_env":
        return ""
    if category == "security":
        return "market_code"
    return "market_code"


def symbol_type_for_entity_type(entity_type):
    entity_type = clean_cell(entity_type)
    if not entity_type:
        return symbol_type_for_category("security")
    if entity_type == "market_env":
        return ""
    if entity_type == "security":
        return "market_code"
    return entity_type


def template_for_category(endpoint, category):
    if category == "market_env" and endpoint == "snapshot":
        return "market-env-snapshot.json"
    if category == "market_env":
        return ""
    return choose_template(endpoint)


def row_to_outputs(
    row,
    header,
    warnings,
    row_number,
    category_map=None,
    router_map=None,
    legacy_attrs_map=None,
    legacy_time_range_map=None,
    begin_time_ms=None,
):
    category_map = category_map or {}
    router_map = router_map or {}
    indicator_id = value_at(row, header, HEADER_INDEXAPI_CODE)
    if not indicator_id and HEADER_INDEXAPI_CODE not in header:
        indicator_id = value_at(row, header, HEADER_SOURCE_CODE) or value_at(row, header, HEADER_STANDARD_CODE)
    if not indicator_id:
        warnings.append(f"row {row_number}: missing IndexAPI code; skipped")
        return None

    main_attrs, auto_attrs = [], []
    try:
        main_attrs, auto_attrs = parse_extended_attrs(value_at(row, header, HEADER_EXTENDED_ATTRS))
    except ValueError as exc:
        warnings.append(f"row {row_number} {indicator_id}: {exc}; attrs_json set to empty")
    attrs = attrs_to_lookup(main_attrs, auto_attrs)
    attrs = merge_legacy_attrs(attrs, (legacy_attrs_map or {}).get(indicator_id) or {})
    router_entry = router_map.get(indicator_id) or {}
    periods = periods_from_attrs(attrs)
    if not periods:
        periods = normalize_periods(value_at(row, header, HEADER_PERIODS))
    if not periods:
        periods = ";".join(router_entry.get("periods") or [])
    access_guide = value_at(row, header, HEADER_ACCESS_GUIDE)
    endpoint = choose_endpoint_from_access_guide(access_guide, periods)
    entity_type = value_at(row, header, HEADER_ENTITY_TYPE)
    category = entity_type or resolve_category(indicator_id, category_map)
    exchange = value_at(row, header, HEADER_EXCHANGE) or ",".join(router_entry.get("markets") or [])

    metric_name = value_at(row, header, HEADER_METRIC_NAME)
    english_name = value_at(row, header, HEADER_ENGLISH_NAME)
    description = value_at(row, header, HEADER_DESCRIPTION)
    query_key = "|".join(part for part in [indicator_id, metric_name, english_name] if part)
    aliases = dedupe_keep_order(
        split_aliases(value_at(row, header, HEADER_SOURCE_ALIASES)) + supplemental_aliases(indicator_id, metric_name)
    )
    attrs_json = json.dumps(attrs, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    id_row = {
        "id": indicator_id,
        "category": category,
        "periods": periods,
        "sortable": sortable_value(value_at(row, header, HEADER_SORTABLE) or router_entry.get("sortable")),
        "attrs": attrs_to_id_dict(attrs),
        "value_type": value_at(row, header, HEADER_VALUE_TYPE),
        "unit": value_at(row, header, HEADER_UNIT),
        "aliases": ";".join(aliases),
        "from": value_at(row, header, HEADER_SOURCE),
        "describe": metric_name,
    }
    lookup_row = {
        "query_key": query_key,
        "category": category,
        "scenario": "metric_lookup",
        "endpoint": endpoint,
        "symbol_type": symbol_type_for_entity_type(category),
        "indicator_id": indicator_id,
        "req_unique_id": indicator_id,
        "attrs_json": attrs_json,
        "time_range_json": build_time_range_json_for_row(
            endpoint,
            periods,
            access_guide,
            (legacy_time_range_map or {}).get(indicator_id),
            begin_time_ms,
        ),
        "template": template_for_category(endpoint, category),
        "periods": periods,
        "metric_name": metric_name,
        "english_name": english_name,
        "description": description,
        "aliases": "|".join(aliases),
        "is_interval_metric": "true" if is_interval_metric(indicator_id, attrs) else "false",
        "period_values_json": json.dumps(period_values(attrs), ensure_ascii=False, separators=(",", ":")),
        "exchange": exchange,
        "access_guide": access_guide,
    }
    return id_row, lookup_row


def write_csv(path, fieldnames, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def build_csvs(
    input_path=DEFAULT_INPUT,
    out_dir=DEFAULT_OUT_DIR,
    legacy_id_dict_path=DEFAULT_LEGACY_ID_DICT,
    category_map_path=DEFAULT_ID_DICT_REFERENCE,
    router_path=DEFAULT_ROUTER_CONFIG,
):
    input_path = Path(input_path)
    out_dir = Path(out_dir)
    rows = load_rows(input_path)
    header_index, header = find_header(rows)
    category_map = load_category_map(category_map_path) if category_map_path else {}
    router_map = load_router_map(router_path) if router_path else {}
    legacy_attrs_map = load_legacy_attrs(legacy_id_dict_path) if legacy_id_dict_path else {}
    legacy_time_range_map = load_legacy_time_ranges(legacy_id_dict_path) if legacy_id_dict_path else {}
    id_rows = []
    lookup_rows = []
    warnings = []
    begin_time_ms = recent_trading_day_begin_time_ms()

    for offset, row in enumerate(rows[header_index + 1 :], start=header_index + 2):
        if not any(row):
            continue
        output = row_to_outputs(
            row,
            header,
            warnings,
            offset,
            category_map,
            router_map,
            legacy_attrs_map,
            legacy_time_range_map,
            begin_time_ms,
        )
        if output:
            id_row, lookup_row = output
            id_rows.append(id_row)
            lookup_rows.append(lookup_row)

    id_dict_path = out_dir / "id_dict_quote.csv"
    lookup_path = out_dir / "quote_request_lookup.csv"
    write_csv(id_dict_path, ID_DICT_FIELDS, id_rows)
    write_csv(lookup_path, LOOKUP_FIELDS, lookup_rows)
    return BuildResult(
        rows_read=max(0, len(rows) - header_index - 1),
        rows_written=len(id_rows),
        warnings=warnings,
        id_dict_path=id_dict_path,
        lookup_path=lookup_path,
    )


def parse_args():
    parser = argparse.ArgumentParser(description="Build AInvest quote CSV indexes from an exported metric .xlsx file.")
    parser.add_argument("--input", default=str(DEFAULT_INPUT), help="Path to export_metric_meta_new.xlsx")
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR), help="Directory for generated CSV files")
    parser.add_argument("--legacy-id-dict", default=str(DEFAULT_LEGACY_ID_DICT), help="Legacy id_dict.md whose attr requirements override Excel attrs")
    parser.add_argument(
        "--category-map",
        default=str(DEFAULT_ID_DICT_REFERENCE),
        help="Optional id_dict.csv category map. Missing file is allowed; categories then default to security.",
    )
    parser.add_argument(
        "--router",
        default=str(DEFAULT_ROUTER_CONFIG),
        help="Optional id_router.yaml used as a fallback period/market/sortable source.",
    )
    return parser.parse_args()


def main():
    args = parse_args()
    try:
        result = build_csvs(args.input, args.out_dir, args.legacy_id_dict, args.category_map, args.router)
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    for warning in result.warnings:
        print(f"warning: {warning}", file=sys.stderr)
    print(f"Wrote {result.rows_written} rows to {result.id_dict_path}")
    print(f"Wrote {result.rows_written} rows to {result.lookup_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
