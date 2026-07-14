#!/usr/bin/env python3
"""Search generated AInvest quote request metadata.

The script reads `references/generated/quote_request_lookup.csv` and returns
matching rows as JSON. Search terms may be metric IDs, source codes, standard
codes, Chinese names, English names, or aliases. It contains lightweight ranking
logic for interval phrases such as `5分钟区间涨幅`, where the time phrase should
map to `attr.time_period` instead of becoming part of the metric alias.

This is an offline lookup helper. It does not fetch market data and does not
need API keys, cookies, or network access.
"""

import argparse
import csv
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_LOOKUP = ROOT / "references" / "generated" / "quote_request_lookup.csv"

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


def load_rows(path):
    with Path(path).open("r", encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def searchable_text(row):
    fields = [
        row.get("query_key", ""),
        row.get("indicator_id", ""),
        row.get("req_unique_id", ""),
        row.get("metric_name", ""),
        row.get("english_name", ""),
        row.get("description", ""),
        row.get("aliases", ""),
        row.get("exchange", ""),
        row.get("access_guide", ""),
    ]
    return " ".join(fields).lower()


def query_has_interval_intent(query):
    return "\u533a\u95f4" in query


def query_period_value(query):
    normalized = query.lower()
    if any(token in normalized for token in ["5\u5206\u949f", "\u4e94\u5206\u949f", "5min", "min_5"]):
        return "MIN_5"
    if any(token in normalized for token in ["1\u5206\u949f", "\u4e00\u5206\u949f", "1min", "min_1"]):
        return "MIN_1"
    return ""


def row_period_values(row):
    try:
        parsed = json.loads(row.get("period_values_json") or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item).upper() for item in parsed]


def row_is_interval(row):
    return (row.get("is_interval_metric") or "").lower() == "true" or str(row.get("indicator_id", "")).startswith("inr-")


def query_tokens(query):
    tokens = []
    query = query.strip()
    if query:
        tokens.append(query.lower())
    for token in ["\u533a\u95f4", "\u6536\u76d8\u4ef7", "\u6da8\u5e45", "5\u5206\u949f", "\u4e94\u5206\u949f"]:
        if token in query:
            tokens.append(token.lower())
    return tokens


def semantic_query(query):
    value = query.strip()
    for token in ["5\u5206\u949f", "\u4e94\u5206\u949f", "5min", "MIN_5", "min_5"]:
        value = value.replace(token, "")
    return value.strip().lower()


def row_names(row):
    names = [row.get("metric_name", "")]
    names.extend((row.get("aliases", "") or "").replace(";", "|").split("|"))
    return [name.strip().lower() for name in names if name.strip()]


def score_row(row, query):
    query = query.strip()
    haystack = searchable_text(row)
    interval_intent = query_has_interval_intent(query)
    period = query_period_value(query)
    score = 0

    if query and query.lower() in haystack:
        score += 100
    for token in query_tokens(query):
        if token in haystack:
            score += 15

    semantic = semantic_query(query)
    if semantic and semantic in row_names(row):
        score += 90
    if semantic and "\u5dee" not in semantic and "\u6da8\u5e45\u5dee" in haystack:
        score -= 45

    if interval_intent:
        if row_is_interval(row):
            score += 120
        else:
            score -= 80
    elif period and not row_is_interval(row):
        score += 25

    if period and period.upper() in row_period_values(row):
        score += 40

    if row.get("indicator_id") and query.lower() == row["indicator_id"].lower():
        score += 200

    return score


def find_matches(path, query, limit=20):
    rows = load_rows(path)
    if not query.strip():
        return rows[:limit]

    scored = []
    for row in rows:
        score = score_row(row, query)
        if score > 0:
            scored.append((score, row))
    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored:
        return []
    top_category = scored[0][1].get("category", "")
    if top_category:
        scored = [item for item in scored if item[1].get("category", "") == top_category]
    return [row for _, row in scored[:limit]]


def parse_args():
    parser = argparse.ArgumentParser(description="Search generated AInvest quote request parameters.")
    parser.add_argument("--query", required=True, help="Metric id, source code, Chinese name, or English name")
    parser.add_argument("--lookup", default=str(DEFAULT_LOOKUP), help="Path to quote_request_lookup.csv")
    parser.add_argument("--limit", type=int, default=20, help="Maximum number of matches to return")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    return parser.parse_args()


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    args = parse_args()
    try:
        matches = find_matches(args.lookup, args.query, args.limit)
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(matches, ensure_ascii=False, indent=2 if args.pretty else None))
    return 0 if matches else 1


if __name__ == "__main__":
    sys.exit(main())
