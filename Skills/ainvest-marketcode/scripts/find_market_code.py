#!/usr/bin/env python3

import argparse
import csv
import json
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
CSV_PATH = ROOT / "references" / "security_config_V1.1.csv"
NAME_EN_PATH = ROOT / "references" / "ainvest_market_code_names_en.csv"
NAME_ZH_PATH = ROOT / "references" / "ainvest_market_code_names_zh.csv"
SECTOR_TREE_PATH = ROOT / "references" / "gics_sector_tree.json"
INDUSTRY_ALIASES_PATH = ROOT / "references" / "industry_aliases.json"
INDUSTRY_NAME_INDEX_PATH = ROOT / "references" / "industry_name_index.json"
CSV_UPDATE_URL = "https://cdn.ainvest.com/clientconfigs/common_config/security_config_V1.1.csv"

MARKET_ALIASES = {
    "169": {"169", "nyse", "arca", "nyse arca"},
    "170": {"170", "amex", "nyse amex", "american"},
    "171": {"171", "bats", "cboe", "cboe bzx"},
    "185": {"185", "nasdaq", "nasdaq global select", "nasdaq global"},
    "186": {"186", "nasdaq capital", "nasdaq capital market", "nasdaq small cap"},
    "89": {"89", "ths index", "tonghuashun index", "index"},
    "UAOS": {"uaos", "us option", "options", "opra"},
    "UDC": {"udc", "apex", "crypto"},
    "UBAX": {"ubax", "binance", "binance spot"},
    "UBAF": {"ubaf", "binance", "binance futures", "binance perpetual", "binance perp"},
    "U31X": {"u31x", "bybit", "bybit spot"},
    "U31F": {"u31f", "bybit", "bybit futures", "bybit perpetual", "bybit perp"},
    "U32X": {"u32x", "bitget", "bitget spot"},
    "U32F": {"u32f", "bitget", "bitget futures", "bitget perpetual", "bitget perp"},
}

ASSET_HINTS = {
    "stock": {"E"},
    "equity": {"E"},
    "equities": {"E"},
    "ordinary_stock": {"ES"},
    "ordinary": {"ES"},
    "etf": {"C"},
    "fund": {"C"},
    "bond": {"D"},
    "option": {"O"},
    "options": {"O"},
    "index": {"I"},
    "spot": {"T"},
    "crypto": {"T", "S", "F", "P"},
    "futures": {"F"},
    "future": {"F"},
    "perpetual": {"S"},
    "perp": {"S"},
}

LIST_ASSET_TYPE_PREFIXES = {
    "stock": ("ES",),
    "ordinary_stock": ("ES",),
    "ordinary": ("ES",),
    "equity": ("E",),
    "equities": ("E",),
    "etf": ("CE",),
    "fund": ("CE",),
    "bond": ("D",),
    "option": ("O",),
    "options": ("O",),
    "index": ("I",),
    "spot": ("T",),
    "perpetual": ("S",),
    "perp": ("S",),
    "futures": ("F", "S"),
    "future": ("F", "S"),
}

CRYPTO_SPOT_MARKETS = {"UBAX", "U31X", "U32X"}
CRYPTO_DERIVATIVE_MARKETS = {"UBAF", "U31F", "U32F"}
CRYPTO_MARKETS = {"UDC", *CRYPTO_SPOT_MARKETS, *CRYPTO_DERIVATIVE_MARKETS}

MARKET_PRIORITY = {
    "185": 100,
    "186": 90,
    "169": 80,
    "170": 70,
    "171": 60,
    "UAOS": 55,
    "UBAX": 50,
    "UBAF": 49,
    "U31X": 48,
    "U31F": 47,
    "U32X": 46,
    "U32F": 45,
    "89": 40,
    "UDC": 30,
}

MARKET_DEFAULT_LISTING_MARKET = {
    "UBAX": "BNB",
    "UBAF": "BNB",
    "U31X": "BY",
    "U31F": "BY",
    "U32X": "BGT",
    "U32F": "BGT",
    "169": "N",
    "170": "A",
    "171": "Z",
    "185": "Q",
    "186": "Q",
}

LISTING_MARKET_NAMES = {
    "A": "NYSE AMEX",
    "BGT": "Bitget",
    "BNB": "Binance",
    "BY": "Bybit",
    "N": "NYSE",
    "P": "NYSE ARCA",
    "Q": "NASDAQ",
    "Z": "Cboe BZX",
}

MARKET_SECURITY_FAMILIES = {
    "UBAX": "TP",
    "UBAF": "FP",
    "U31X": "TP",
    "U31F": "FP",
    "U32X": "TP",
    "U32F": "FP",
    "169": "E",
    "170": "E",
    "171": "E",
    "185": "E",
    "186": "E",
    "UAOS": "O",
    "UDC": "P",
    "89": "I",
}

DEFAULT_INDUSTRY_ALIAS_TO_MARKET_CODE = {
    "石油": "89:861105",
    "石油行业": "89:861105",
    "石油和天然气": "89:861105",
    "石油天然气": "89:861105",
    "油气": "89:861105",
    "石油钻井": "89:861105",
    "oilgas": "89:861105",
    "oilandgas": "89:861105",
}

DEFAULT_INDUSTRY_CN_EN_REPLACEMENTS = {
    "石油和天然气": "oil gas",
    "石油天然气": "oil gas",
    "油气": "oil gas",
    "石油": "oil",
    "天然气": "gas",
    "钻井": "drilling",
    "设备": "equipment",
    "服务": "services",
    "勘探": "exploration",
    "开发": "production",
    "炼油": "refining",
    "营销": "marketing",
    "综合性": "integrated",
    "能源": "energy",
    "材料": "materials",
    "工业": "industrials",
    "可选消费": "consumer discretionary",
    "日常消费": "consumer staples",
    "医疗保健": "health care",
    "金融": "financials",
    "信息技术": "information technology",
    "通信服务": "communication services",
    "公用事业": "utilities",
    "房地产": "real estate",
    "化学": "chemicals",
    "建筑材料": "construction materials",
    "航空航天": "aerospace",
    "国防": "defense",
    "半导体": "semiconductors",
    "银行": "banks",
    "保险": "insurance",
    "软件": "software",
    "硬件": "hardware",
    "媒体": "media",
    "娱乐": "entertainment",
    "汽车": "automobiles",
    "零售": "retail",
    "饮料": "beverages",
    "食品": "food",
    "生物技术": "biotechnology",
    "制药": "pharmaceuticals",
}


def normalize(value):
    return (value or "").strip().lower()


def normalize_text(value):
    text = normalize(value)
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text)
    return " ".join(text.split())


def compact_text(value):
    return normalize_text(value).replace(" ", "")


def dedupe_keep_order(items):
    out = []
    seen = set()
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def load_industry_alias_config():
    if not INDUSTRY_ALIASES_PATH.exists():
        return DEFAULT_INDUSTRY_ALIAS_TO_MARKET_CODE, DEFAULT_INDUSTRY_CN_EN_REPLACEMENTS

    config = json.loads(INDUSTRY_ALIASES_PATH.read_text(encoding="utf-8"))
    alias_to_market_code = config.get("alias_to_market_code") or DEFAULT_INDUSTRY_ALIAS_TO_MARKET_CODE
    cn_en_replacements = config.get("cn_en_replacements") or DEFAULT_INDUSTRY_CN_EN_REPLACEMENTS
    return alias_to_market_code, cn_en_replacements


def tokenize_industry_query(value):
    _, cn_en_replacements = load_industry_alias_config()
    text = normalize_text(value)
    for source, target in sorted(cn_en_replacements.items(), key=lambda item: len(item[0]), reverse=True):
        text = text.replace(source, f" {target} ")
    text = re.sub(r"\b(and|the|of)\b", " ", text)
    text = text.replace("行业", " ").replace("板块", " ").replace("指数", " ")
    tokens = [token for token in text.split() if token]
    return tokens


def tokenize_name_query(value):
    return [token for token in normalize_text(value).split() if token]


def load_market_code_name_map():
    names_by_market_code = {}

    for path, name_key in ((NAME_EN_PATH, "security_name"), (NAME_ZH_PATH, "security_name_zh")):
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8-sig") as handle:
            reader = csv.DictReader(handle)
            for raw_row in reader:
                market_code = (raw_row.get("market_code") or "").strip()
                if not market_code:
                    continue
                row = names_by_market_code.setdefault(market_code, {})
                value = (raw_row.get(name_key) or "").strip()
                if value:
                    row[name_key] = value

    for row in names_by_market_code.values():
        search_names = dedupe_keep_order([row.get("security_name"), row.get("security_name_zh")])
        row["search_names"] = search_names
        row["normalized_search_names"] = [normalize_text(name) for name in search_names]
        row["compact_search_names"] = [compact_text(name) for name in search_names]
        row["token_sets"] = [set(tokenize_name_query(name)) for name in search_names]

    return names_by_market_code


def load_rows():
    names_by_market_code = load_market_code_name_map()
    rows = []
    with CSV_PATH.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            key = row["key"]
            market, code = key.split(":", 1)
            row["market"] = market
            row["code"] = code
            row["code_upper"] = code.upper()
            row["ths_code_upper"] = (row.get("ths_code") or "").upper()
            name_info = names_by_market_code.get(key, {})
            row["security_name"] = name_info.get("security_name", "")
            row["security_name_zh"] = name_info.get("security_name_zh", "")
            row["search_names"] = name_info.get("search_names", [])
            row["normalized_search_names"] = name_info.get("normalized_search_names", [])
            row["compact_search_names"] = name_info.get("compact_search_names", [])
            row["name_token_sets"] = name_info.get("token_sets", [])
            rows.append(row)
    return rows


def refresh_csv():
    CSV_PATH.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("wb", delete=False, dir=str(CSV_PATH.parent)) as handle:
        temp_path = Path(handle.name)
        try:
            with urllib.request.urlopen(CSV_UPDATE_URL, timeout=20) as response:
                shutil.copyfileobj(response, handle)
            if temp_path.stat().st_size == 0:
                raise ValueError("Downloaded CSV is empty.")
            temp_path.replace(CSV_PATH)
        except Exception:
            if temp_path.exists():
                temp_path.unlink()
            raise


def load_sector_rows():
    if INDUSTRY_NAME_INDEX_PATH.exists():
        index_rows = json.loads(INDUSTRY_NAME_INDEX_PATH.read_text(encoding="utf-8"))
        rows = []
        for row in index_rows:
            search_names = row.get("search_names") or [row["english_name"], *row.get("english_aliases", []), *row.get("chinese_aliases", [])]
            normalized_names = [normalize_text(name) for name in search_names if name]
            compact_names = [compact_text(name) for name in search_names if name]
            token_sets = [set(tokenize_industry_query(name)) for name in search_names if name]
            rows.append(
                {
                    "market_code": row["market_code"],
                    "market": row["market_code"].split(":", 1)[0],
                    "code": row["market_code"].split(":", 1)[1],
                    "name": row["english_name"],
                    "level": row["level"],
                    "path": row.get("path_en", []),
                    "normalized_names": normalized_names,
                    "compact_names": compact_names,
                    "token_sets": token_sets,
                }
            )
        return rows

    if not SECTOR_TREE_PATH.exists():
        return []

    root = json.loads(SECTOR_TREE_PATH.read_text(encoding="utf-8")).get("data", {})
    rows = []

    def walk(node, level=0, path_names=None):
        path_names = (path_names or [])
        block = node.get("blockVO") or {}
        name = (block.get("name") or "").strip()
        next_path = path_names
        if name:
            next_path = path_names + [name]
            index_market = (block.get("indexMarket") or "").strip()
            index_code = (block.get("indexCode") or "").strip()
            if index_market and index_code:
                rows.append(
                    {
                        "market_code": f"{index_market}:{index_code}",
                        "market": index_market,
                        "code": index_code,
                        "name": name,
                        "level": level,
                        "path": next_path,
                        "normalized_names": [normalize_text(name)],
                        "compact_names": [compact_text(name)],
                        "token_sets": [set(tokenize_industry_query(name))],
                    }
                )
        for child in node.get("childrenTrees", []):
            walk(child, level + 1, next_path)

    walk(root)
    return rows


def load_sector_tree_index():
    if not SECTOR_TREE_PATH.exists():
        return {}, {}

    root = json.loads(SECTOR_TREE_PATH.read_text(encoding="utf-8")).get("data", {})
    nodes_by_market_code = {}
    components_by_market_code = {}

    def walk(node, level=0, path_names=None):
        path_names = path_names or []
        block = node.get("blockVO") or {}
        name = (block.get("name") or "").strip()
        next_path = path_names
        market_code = None
        if name:
            next_path = path_names + [name]
            index_market = (block.get("indexMarket") or "").strip()
            index_code = (block.get("indexCode") or "").strip()
            if index_market and index_code:
                market_code = f"{index_market}:{index_code}"
                nodes_by_market_code[market_code] = {
                    "market_code": market_code,
                    "market": index_market,
                    "code": index_code,
                    "name": name,
                    "level": level,
                    "path": next_path,
                }

        aggregated_components = []
        direct_components = []
        for item in block.get("codeList") or []:
            market_id = str(item.get("marketId") or "").strip()
            code = (item.get("code") or "").strip()
            if market_id and code:
                direct_components.append(f"{market_id}:{code}")
        aggregated_components.extend(direct_components)

        for child in node.get("childrenTrees", []):
            aggregated_components.extend(walk(child, level + 1, next_path))

        if market_code:
            components_by_market_code[market_code] = dedupe_keep_order(aggregated_components)
        return aggregated_components

    walk(root)
    return nodes_by_market_code, components_by_market_code


def search_sector_rows(query):
    sector_rows = load_sector_rows()
    if not sector_rows:
        return []

    normalized_query = normalize_text(query)
    compact_query = compact_text(query)
    alias_to_market_code, _ = load_industry_alias_config()
    alias_market_code = alias_to_market_code.get(compact_query)
    if alias_market_code:
        exact_alias = [row for row in sector_rows if row["market_code"] == alias_market_code]
        if exact_alias:
            return [(10_000, exact_alias[0])]

    query_tokens = set(tokenize_industry_query(query))
    ranked = []
    for row in sector_rows:
        score = 0
        if normalized_query and normalized_query in row["normalized_names"]:
            score += 5000
        if compact_query and compact_query in row["compact_names"]:
            score += 5000
        if compact_query and any(compact_query in name for name in row["compact_names"]):
            score += 800
        overlap = max((len(query_tokens & token_set) for token_set in row["token_sets"]), default=0)
        if overlap:
            score += overlap * 400
            if query_tokens and overlap == len(query_tokens):
                score += 1200
        if score:
            score += max(0, 50 - row["level"] * 10)
            ranked.append((score, row))

    ranked.sort(key=lambda item: (-item[0], item[1]["level"], item[1]["market_code"]))
    return ranked


def matches_market(market_hint, market_code):
    if not market_hint:
        return False
    hint = normalize(market_hint)
    aliases = MARKET_ALIASES.get(market_code, {market_code.lower()})
    return hint in aliases


def asset_match(asset_hint, security_type):
    if not asset_hint:
        return False
    families = ASSET_HINTS.get(normalize(asset_hint), set())
    return bool(security_type and security_type[:1] in families)


def list_asset_match(row, asset_hint):
    if not asset_hint:
        return True

    hint = normalize(asset_hint)
    security_type = row.get("security_type", "")
    market = row.get("market", "")

    if hint == "crypto":
        return market in CRYPTO_MARKETS or security_type[:1] in {"T", "S", "F", "P"}
    if hint == "spot":
        return market in CRYPTO_SPOT_MARKETS or security_type.startswith("T")
    if hint in {"futures", "future"}:
        return market in CRYPTO_DERIVATIVE_MARKETS or any(security_type.startswith(prefix) for prefix in LIST_ASSET_TYPE_PREFIXES[hint])
    if hint in {"perpetual", "perp"}:
        return market in CRYPTO_DERIVATIVE_MARKETS or security_type.startswith("S")

    prefixes = LIST_ASSET_TYPE_PREFIXES.get(hint)
    if not prefixes:
        return False
    return any(security_type.startswith(prefix) for prefix in prefixes)


def list_query_match(row, query):
    if not query:
        return True

    query_upper = query.upper()
    normalized_query = normalize_text(query)
    compact_query = compact_text(query)

    if query_upper in row["key"].upper():
        return True
    if query_upper in row["code_upper"]:
        return True
    if query_upper and query_upper in row["ths_code_upper"]:
        return True
    if normalized_query and any(normalized_query in name for name in row.get("normalized_search_names", [])):
        return True
    if compact_query and any(compact_query in name for name in row.get("compact_search_names", [])):
        return True
    return False


def list_rows(rows, market_hint=None, asset_hint=None, query=None, offset=0, limit=None):
    filtered = []
    for row in rows:
        if market_hint and not matches_market(market_hint, row["market"]):
            continue
        if asset_hint and not list_asset_match(row, asset_hint):
            continue
        if query and not list_query_match(row, query):
            continue
        filtered.append(row)

    filtered.sort(key=lambda row: (row["market"], row["code_upper"]))

    if offset > 0:
        filtered = filtered[offset:]
    if limit is not None:
        filtered = filtered[: max(limit, 0)]

    return [format_row(row, 0) for row in filtered]


def score_row(row, query, market_hint=None, asset_hint=None):
    score = 0
    query_upper = query.upper()
    key_upper = row["key"].upper()
    code_upper = row["code_upper"]
    ths_upper = row["ths_code_upper"]

    if query_upper == key_upper:
        score += 1000
    if query_upper == code_upper:
        score += 900
    elif code_upper.startswith(query_upper):
        score += 500
    elif query_upper in code_upper:
        score += 250

    if query_upper == ths_upper:
        score += 800
    elif ths_upper.startswith(query_upper + "."):
        score += 300

    if market_hint and matches_market(market_hint, row["market"]):
        score += 400

    if asset_hint and asset_match(asset_hint, row.get("security_type", "")):
        score += 120

    if row.get("main_code_flag") == "1":
        score += 60

    score += MARKET_PRIORITY.get(row["market"], 0)
    return score


def score_name_match(row, query, market_hint=None, asset_hint=None):
    score = 0
    normalized_query = normalize_text(query)
    compact_query = compact_text(query)
    query_tokens = set(tokenize_name_query(query))

    if normalized_query and normalized_query in row["normalized_search_names"]:
        score += 5000
    if compact_query and compact_query in row["compact_search_names"]:
        score += 5000
    if compact_query and any(compact_query in name for name in row["compact_search_names"]):
        score += 900

    overlap = max((len(query_tokens & token_set) for token_set in row["name_token_sets"]), default=0)
    if overlap:
        score += overlap * 350
        if query_tokens and overlap == len(query_tokens):
            score += 900

    if score == 0:
        return 0

    if market_hint and matches_market(market_hint, row["market"]):
        score += 400

    if asset_hint and asset_match(asset_hint, row.get("security_type", "")):
        score += 120

    if row.get("main_code_flag") == "1":
        score += 60

    score += MARKET_PRIORITY.get(row["market"], 0)
    return score


def exact_group(rows, query):
    query_upper = query.upper()
    exact_key = [row for row in rows if row["key"].upper() == query_upper]
    if exact_key:
        return exact_key, True

    exact_code = [row for row in rows if row["code_upper"] == query_upper]
    if exact_code:
        return exact_code, True

    exact_ths = [row for row in rows if row["ths_code_upper"] == query_upper or row["ths_code_upper"].startswith(query_upper + ".")]
    if exact_ths:
        return exact_ths, True

    return [], False


def search_named_rows(rows, query, market_hint=None, asset_hint=None):
    ranked = []
    for row in rows:
        if not row["search_names"]:
            continue
        score = score_name_match(row, query, market_hint, asset_hint)
        if score > 0:
            ranked.append((score, row))

    ranked.sort(key=lambda item: (-item[0], item[1]["key"]))
    return ranked


def format_row(row, score):
    listing_market = row.get("listing_market") or MARKET_DEFAULT_LISTING_MARKET.get(row["market"], "")
    security_type = row.get("security_type") or MARKET_SECURITY_FAMILIES.get(row["market"], "")
    return {
        "market_code": row["key"],
        "market": row["market"],
        "code": row["code"],
        "security_type": security_type,
        "listing_market": listing_market,
        "listing_market_name": LISTING_MARKET_NAMES.get(listing_market, ""),
        "ths_code": row.get("ths_code"),
        "security_name": row.get("security_name"),
        "security_name_zh": row.get("security_name_zh"),
        "score": score,
    }


def format_sector_row(row, score):
    return {
        "market_code": row["market_code"],
        "market": row["market"],
        "code": row["code"],
        "name": row["name"],
        "category": "industry_index",
        "level": row["level"],
        "path": row["path"],
        "score": score,
    }


def search_sector_identifier(query):
    sector_rows = load_sector_rows()
    if not query:
        return []

    query_upper = query.upper()
    exact = [row for row in sector_rows if row["market_code"].upper() == query_upper or row["code"].upper() == query_upper]
    if exact:
        return [(10_000, exact[0])]

    return search_sector_rows(query)


def list_sector_rows(level=None, query=None, offset=0, limit=None):
    rows = load_sector_rows()
    if level is not None:
        rows = [row for row in rows if row["level"] == level]
    if query:
        matches = search_sector_identifier(query)
        rows = [row for _, row in matches]

    rows.sort(key=lambda row: (row["level"], row["market_code"]))
    if offset > 0:
        rows = rows[offset:]
    if limit is not None:
        rows = rows[: max(limit, 0)]
    return [format_sector_row(row, 0) for row in rows]


def list_sector_components(query, offset=0, limit=None):
    matches = search_sector_identifier(query)
    if not matches:
        return []

    sector_row = matches[0][1]
    _, components_by_market_code = load_sector_tree_index()
    component_keys = components_by_market_code.get(sector_row["market_code"], [])
    security_rows = {row["key"]: row for row in load_rows()}

    results = []
    for key in component_keys:
        row = security_rows.get(key)
        if row:
            results.append(format_row(row, 0))
        else:
            market, code = key.split(":", 1)
            results.append(
                {
                    "market_code": key,
                    "market": market,
                    "code": code,
                    "security_type": "",
                    "listing_market": "",
                    "listing_market_name": "",
                    "ths_code": "",
                    "security_name": "",
                    "security_name_zh": "",
                    "score": 0,
                }
            )

    if offset > 0:
        results = results[offset:]
    if limit is not None:
        results = results[: max(limit, 0)]
    return results


def main():
    parser = argparse.ArgumentParser(description="Find AInvest market_code matches.")
    parser.add_argument("query", nargs="?", help="Ticker, code, security name, industry name, or list filter text")
    parser.add_argument("--market", help="Market or exchange hint such as nasdaq or binance")
    parser.add_argument("--asset", help="Asset hint such as stock, etf, spot, futures, perpetual, option")
    parser.add_argument("--list", action="store_true", help="List all matching securities for the market/asset filters")
    parser.add_argument("--industry-level", type=int, choices=[1, 2, 3, 4], help="List industry index codes for a specific GICS level")
    parser.add_argument("--components", action="store_true", help="List component securities for an industry index code or name")
    parser.add_argument("--limit", type=int, help="Maximum matches to return")
    parser.add_argument("--offset", type=int, default=0, help="Skip the first N rows in list mode")
    parser.add_argument("--best", action="store_true", help="Return only the best match")
    parser.add_argument("--count-only", action="store_true", help="Emit only the number of matches")
    parser.add_argument("--codes-only", action="store_true", help="Emit only market_code values")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    parser.add_argument("--no-refresh", action="store_true", help="Do not refresh the CSV when no match is found")
    args = parser.parse_args()

    if args.offset < 0:
        parser.error("--offset must be >= 0")

    if args.components:
        if not args.query:
            parser.error("query is required when --components is used")
        limit = args.limit
        results = list_sector_components(args.query, args.offset, limit)
        if args.count_only:
            count = len(results)
            if args.json:
                print(json.dumps(count, ensure_ascii=False))
            else:
                print(count)
            return
        if args.codes_only:
            codes = [item["market_code"] for item in results]
            if args.json:
                print(json.dumps(codes, ensure_ascii=False, indent=2))
            else:
                for code in codes:
                    print(code)
            return
        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=2))
            return
        if not results:
            print("No matches found.")
            return
        for index, item in enumerate(results, start=1 + args.offset):
            print(
                f"{index}. {item['market_code']} "
                f"(security_type={item['security_type']}, listing_market={item['listing_market']}, "
                f"ths_code={item['ths_code']}, security_name={item['security_name']}, security_name_zh={item['security_name_zh']})"
            )
        return

    if args.list or args.industry_level is not None:
        limit = args.limit
        if args.industry_level is not None:
            results = list_sector_rows(args.industry_level, args.query, args.offset, limit)
        else:
            rows = load_rows()
            results = list_rows(rows, args.market, args.asset, args.query, args.offset, limit)
        if args.best and results:
            results = [results[0]]

        if args.count_only:
            count = len(results)
            if args.json:
                print(json.dumps(count, ensure_ascii=False))
            else:
                print(count)
            return

        if args.codes_only:
            codes = [item["market_code"] for item in results]
            if args.json:
                print(json.dumps(codes, ensure_ascii=False, indent=2))
            else:
                for code in codes:
                    print(code)
            return

        if args.json:
            print(json.dumps(results, ensure_ascii=False, indent=2))
            return

        if not results:
            print("No matches found.")
            return

        for index, item in enumerate(results, start=1 + args.offset):
            prefix = "" if args.best else f"{index}. "
            if "category" in item:
                print(
                    f"{prefix}{item['market_code']} "
                    f"(name={item['name']}, category={item['category']}, level={item['level']}, path={item['path']})"
                )
            else:
                print(
                    f"{prefix}{item['market_code']} "
                    f"(security_type={item['security_type']}, listing_market={item['listing_market']}, "
                    f"ths_code={item['ths_code']}, security_name={item['security_name']}, security_name_zh={item['security_name_zh']})"
                )
        return

    if not args.query:
        parser.error("query is required unless --list or --industry-level is used")

    limit = args.limit if args.limit is not None else 5

    refreshed = False
    results = []
    for attempt in range(2):
        rows = load_rows()
        candidate_rows, found_exact = exact_group(rows, args.query)
        if not found_exact:
            named_ranked = search_named_rows(rows, args.query, args.market, args.asset)
            if named_ranked:
                results = [format_row(row, score) for score, row in named_ranked[: max(limit, 1)]]
                break
            sector_ranked = search_sector_rows(args.query)
            if sector_ranked:
                results = [format_sector_row(row, score) for score, row in sector_ranked[: max(limit, 1)]]
                break
            if args.no_refresh or attempt == 1:
                results = []
                break
            try:
                refresh_csv()
                refreshed = True
                continue
            except (urllib.error.URLError, OSError, ValueError) as exc:
                print(f"Refresh failed: {exc}", file=sys.stderr)
                results = []
                break

        ranked = []
        for row in candidate_rows:
            score = score_row(row, args.query, args.market, args.asset)
            if score > 0:
                ranked.append((score, row))

        ranked.sort(key=lambda item: (-item[0], item[1]["key"]))
        results = [format_row(row, score) for score, row in ranked[: max(limit, 1)]]
        break

    if args.best and results:
        results = [results[0]]

    if args.count_only:
        count = len(results)
        if args.json:
            print(json.dumps(count, ensure_ascii=False))
        else:
            print(count)
        return

    if args.codes_only:
        codes = [item["market_code"] for item in results]
        if args.json:
            print(json.dumps(codes, ensure_ascii=False, indent=2))
        else:
            for code in codes:
                print(code)
        return

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return

    if not results:
        if refreshed:
            print("No matches found after refreshing security_config_V1.1.csv.")
            return
        print("No matches found.")
        return

    for index, item in enumerate(results, start=1):
        prefix = "" if args.best else f"{index}. "
        print(
            f"{prefix}{item['market_code']} "
            f"(security_type={item['security_type']}, listing_market={item['listing_market']}, ths_code={item['ths_code']}, score={item['score']})"
        )


if __name__ == "__main__":
    main()
