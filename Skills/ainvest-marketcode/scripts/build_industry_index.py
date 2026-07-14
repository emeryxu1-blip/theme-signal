#!/usr/bin/env python3

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
REFERENCES = ROOT / "references"
TREE_PATH = REFERENCES / "gics_sector_tree.json"
ALIASES_PATH = REFERENCES / "industry_aliases.json"
INDEX_PATH = REFERENCES / "industry_name_index.json"


def is_cn(text):
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def dedupe_keep_order(items):
    out = []
    seen = set()
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def load_aliases():
    if not ALIASES_PATH.exists():
        return {}
    return json.loads(ALIASES_PATH.read_text(encoding="utf-8")).get("alias_to_market_code", {})


def build_index():
    tree = json.loads(TREE_PATH.read_text(encoding="utf-8"))["data"]
    alias_to_market = load_aliases()
    market_to_aliases = {}
    for alias, market_code in alias_to_market.items():
        market_to_aliases.setdefault(market_code, []).append(alias)

    rows = []

    def walk(node, level=0, path_en=None):
        path_en = path_en or []
        block = node.get("blockVO") or {}
        name = (block.get("name") or "").strip()
        market = (block.get("indexMarket") or "").strip()
        code = (block.get("indexCode") or "").strip()
        next_path = path_en
        if name:
            next_path = path_en + [name]
            if market and code and 1 <= level <= 4:
                market_code = f"{market}:{code}"
                aliases = dedupe_keep_order(
                    sorted(market_to_aliases.get(market_code, []), key=lambda x: (not is_cn(x), x.lower()))
                )
                chinese_aliases = [a for a in aliases if is_cn(a)]
                english_aliases = [a for a in aliases if not is_cn(a)]
                rows.append(
                    {
                        "level": level,
                        "market_code": market_code,
                        "english_name": name,
                        "chinese_aliases": chinese_aliases,
                        "english_aliases": english_aliases,
                        "path_en": next_path,
                        "search_names": dedupe_keep_order([name, *english_aliases, *chinese_aliases, next_path[-1]]),
                    }
                )
        for child in node.get("childrenTrees", []):
            walk(child, level + 1, next_path)

    walk(tree)
    rows.sort(key=lambda row: (row["level"], row["market_code"]))
    return rows


def main():
    rows = build_index()
    INDEX_PATH.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"industry_name_index.json: {len(rows)} rows")


if __name__ == "__main__":
    main()
