"""Deterministic live/offline universe recovery for exact output contracts."""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable


_MARKET_CODE_RE = re.compile(r"^\d+:[A-Za-z0-9.\-]+$")
_EXCHANGE_TRADED_NOTE_RE = re.compile(
    r"\b(?:etns?|exchange[\s_\-\u00a0\u2010-\u2015\u2212]*"
    r"traded[\s_\-\u00a0\u2010-\u2015\u2212]*notes?)\b",
    re.IGNORECASE,
)
_WORD_RE = re.compile(r"[a-z0-9]+", re.IGNORECASE)
_STOPWORDS = {
    "a", "an", "and", "at", "by", "for", "from", "in", "into", "of",
    "on", "or", "the", "to", "with", "event", "summit", "theme", "stock",
    "stocks", "fund", "funds", "etf", "etfs",
}


def _normalised_words(value: object) -> set[str]:
    return {
        word.casefold()
        for word in _WORD_RE.findall(str(value or ""))
        if word.casefold() not in _STOPWORDS
    }


def _theme_values(theme: str, brief: dict | None) -> list[str]:
    values = [str(theme or "").strip()]
    brief = brief if isinstance(brief, dict) else {}
    for key in (
        "theme_cn", "input_theme", "summary", "thesis", "event_catalyst",
        "direct_beneficiaries", "picks_and_shovels", "second_order",
        "etf_exposure_terms", "keywords",
    ):
        value = brief.get(key)
        if isinstance(value, (list, tuple, set)):
            values.extend(str(item).strip() for item in value if str(item).strip())
        elif str(value or "").strip():
            values.append(str(value).strip())
    return [value for value in values if value]


def _theme_similarity(name: str, theme: str, brief: dict | None) -> float:
    """Return a weak, deterministic text score used only within fallback tiers."""
    name_words = _normalised_words(name)
    if not name_words:
        return 0.0
    values = _theme_values(theme, brief)
    theme_words: set[str] = set()
    for value in values:
        theme_words.update(_normalised_words(value))
    overlap = name_words & theme_words
    score = float(len(overlap))
    normalised_name = " ".join(_WORD_RE.findall(str(name or "").casefold()))
    for value in values:
        phrase = " ".join(_WORD_RE.findall(value.casefold()))
        if len(phrase) >= 3 and phrase in normalised_name:
            score += 2.0
    return score


def _finite(value: object) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _is_explicit_etn(facts: dict, local_name: str = "") -> bool:
    """Reject notes even when providers vary separators or use type=Note."""
    for key in ("security_class", "etf_type"):
        value = str(facts.get(key) or "").strip()
        normalised = re.sub(
            r"[\s_\-\u00a0\u2010-\u2015\u2212]+", " ", value
        ).casefold()
        if normalised in {"etn", "etns", "note", "exchange traded note"}:
            return True
        if _EXCHANGE_TRADED_NOTE_RE.search(value):
            return True
    for key in (
        "name", "fund_strategy", "fund_category", "fund_niche", "fund_focus",
        "selection_criteria", "asset_class",
    ):
        if _EXCHANGE_TRADED_NOTE_RE.search(str(facts.get(key) or "")):
            return True
    return bool(_EXCHANGE_TRADED_NOTE_RE.search(str(local_name or "")))


def extend_etf_candidates(
    quotes,
    candidates: list[dict],
    *,
    theme: str,
    brief: dict | None,
    target: int,
    universe_limit: int,
    log: Callable[[str], None] | None = None,
) -> tuple[list[dict], tuple[str, ...]]:
    """Hydrate or append bounded live/offline ``CE`` rows for exact selection.

    The returned rows remain below evidence-qualified candidates.  Local security
    type is authoritative when live metadata is absent; no synthetic code is ever
    manufactured.
    """
    if target <= 0:
        return candidates, ()
    if universe_limit <= 0:
        return candidates, ()

    existing_by_code: dict[str, list[dict]] = {}
    for candidate in candidates:
        code = str(candidate.get("code") or "").strip().upper()
        if code:
            existing_by_code.setdefault(code, []).append(candidate)
    source_failures: list[str] = []
    source_rows: list[tuple[int, int, str, str]] = []
    seen: set[str] = set()

    def add_source(codes: Iterable[object], source_priority: int, source: str) -> int:
        retained = 0
        for source_rank, raw_code in enumerate(codes, 1):
            code = str(raw_code or "").strip().upper()
            if not _MARKET_CODE_RE.fullmatch(code) or code in seen:
                continue
            seen.add(code)
            # Existing rows still need authoritative metadata hydration. A
            # stale row labelled CE can in fact be an ETN; skipping it here
            # would let that note survive the downstream exclusion gate.
            retained += 1
            try:
                name = str(quotes.name_of(code) or code.partition(":")[2]).strip()
            except Exception:
                name = code.partition(":")[2]
            source_rows.append((source_priority, source_rank, code, name))
        return retained

    live_codes: list[str] = []
    try:
        live_loader = getattr(quotes, "live_etf_codes")
        live_codes = list(live_loader())
    except Exception as exc:
        source_failures.append(f"live:{exc}")
    live_count = add_source(live_codes, 0, "live_ce")

    # Keep an offline lane available even when the live endpoint returned a short
    # or stale list. Duplicate codes collapse into the higher-priority live lane.
    offline_codes: list[str] = []
    try:
        offline_loader = getattr(quotes, "security_codes")
        offline_codes = list(offline_loader("CE"))
    except Exception as exc:
        source_failures.append(f"offline:{exc}")
    offline_count = add_source(offline_codes, 1, "offline_ce")

    # Text relevance is a weak fallback ordering signal, followed by source
    # freshness and original source order. The boundary counts valid ETF rows,
    # not raw codes: malformed symbols and explicit ETNs must not hide a valid
    # CE immediately behind them and trigger a false universe-exhaustion error.
    ranked_source = sorted(
        source_rows,
        key=lambda item: (
            -_theme_similarity(item[3], theme, brief),
            item[0],
            item[1],
            item[2],
        ),
    )
    metadata: dict[str, dict] = {}
    ranked: list[tuple[int, int, str, str]] = []
    valid_ranked_count = 0
    rejected_etns = 0
    metadata_batch_size = 100
    for offset in range(0, len(ranked_source), metadata_batch_size):
        if valid_ranked_count >= universe_limit:
            break
        batch = ranked_source[offset:offset + metadata_batch_size]
        batch_codes = [item[2] for item in batch]
        batch_metadata: dict[str, dict] = {}
        try:
            raw_metadata = quotes.etf_metadata(batch_codes)
            if isinstance(raw_metadata, dict):
                batch_metadata = {
                    str(raw_code or "").strip().upper(): facts
                    for raw_code, facts in raw_metadata.items()
                    if isinstance(facts, dict)
                }
                metadata.update(batch_metadata)
        except Exception as exc:
            source_failures.append(f"metadata:{exc}")
        for item in batch:
            if valid_ranked_count >= universe_limit:
                break
            code = item[2]
            facts = batch_metadata.get(code, {})
            if _is_explicit_etn(facts, item[3]):
                rejected_etns += 1
                # Preserve authoritative ETN metadata on an existing row so the
                # downstream selector also rejects it explicitly.
                for candidate in existing_by_code.get(code, []):
                    for key, value in facts.items():
                        if value not in (None, ""):
                            candidate[key] = value
                # Retain the real row for explicit downstream diagnostics, but
                # do not let it consume one of the valid-universe slots.
                ranked.append(item)
                continue
            ranked.append(item)
            valid_ranked_count += 1

    codes = [item[2] for item in ranked]

    appended_count = 0
    refreshed_count = 0
    by_code = {item[2]: item for item in ranked}
    for fallback_rank, code in enumerate(codes, 1):
        source_priority, source_rank, _, local_name = by_code[code]
        facts = metadata.get(code)
        facts = facts if isinstance(facts, dict) else {}
        security_class = str(facts.get("security_class") or "CE").strip()
        metadata_text = " ".join(str(facts.get(key) or "") for key in (
                "name", "fund_category", "fund_focus", "fund_niche",
                "fund_strategy", "index_tracked", "benchmark",
            )).strip()
        similarity = _theme_similarity(
            metadata_text or local_name,
            theme,
            brief,
        )
        existing_rows = existing_by_code.get(code, [])
        if existing_rows:
            weak_score = min(0.099, 0.01 * similarity)
            for candidate in existing_rows:
                for key, value in facts.items():
                    if value not in (None, ""):
                        candidate[key] = value
                candidate["code"] = code
                if not candidate.get("name"):
                    candidate["name"] = local_name or code.partition(":")[2]
                if not candidate.get("security_class"):
                    candidate["security_class"] = "CE"
                candidate.setdefault("selection_lane", "ce_fallback")
                candidate.setdefault("ranking_mode", "ce_fallback")
                candidate.setdefault("output_eligible", False)
                candidate.setdefault("static_theme_exposure", 0.0)
                candidate.setdefault("theme_evidence_score", 0.0)
                candidate.setdefault("base_theme_exposure", 0.0)
                candidate["fallback_theme_similarity"] = similarity
                candidate["fallback_source"] = (
                    "live_ce" if source_priority == 0 else "offline_ce"
                )
                candidate["fallback_source_rank"] = source_rank
                candidate["fallback_rank"] = fallback_rank
                candidate["unified_score"] = max(
                    _finite(candidate.get("unified_score")) or 0.0,
                    weak_score,
                )
                candidate["preselect_score"] = max(
                    _finite(candidate.get("preselect_score")) or 0.0,
                    weak_score,
                )
                candidate.setdefault("reason", "ETF-universe fallback")
                candidate.setdefault("relevance_status", "universe_fallback")
                candidate["metric"] = _finite(candidate.get("aum"))
            refreshed_count += 1
            continue
        candidate = {
            "code": code,
            "name": str(facts.get("name") or local_name or code.partition(":")[2]),
            "security_class": security_class,
            "selection_lane": "ce_fallback",
            "ranking_mode": "ce_fallback",
            "output_eligible": False,
            "static_theme_exposure": 0.0,
            "theme_evidence_score": 0.0,
            "base_theme_exposure": 0.0,
            "fallback_theme_similarity": similarity,
            "fallback_source": "live_ce" if source_priority == 0 else "offline_ce",
            "fallback_source_rank": source_rank,
            "fallback_rank": fallback_rank,
            "rank": fallback_rank,
            "source_order": fallback_rank,
            # Keep all fallback rows below evidence-qualified candidates while
            # ordering theme-text matches above generic universe fillers.
            "unified_score": min(0.099, 0.01 * similarity),
            "preselect_score": min(0.099, 0.01 * similarity),
            "reason": "ETF-universe fallback",
            "relevance_status": "universe_fallback",
        }
        for key, value in facts.items():
            if value not in (None, ""):
                candidate[key] = value
        candidate["code"] = code
        candidate.setdefault("name", local_name or code.partition(":")[2])
        candidate.setdefault("security_class", "CE")
        candidate.setdefault("static_theme_exposure", 0.0)
        candidate.setdefault("theme_evidence_score", 0.0)
        candidate.setdefault("output_eligible", False)
        candidate["metric"] = _finite(candidate.get("aum"))
        candidates.append(candidate)
        appended_count += 1

    if log:
        hydrated_selected = sum(code in metadata for code in codes)
        log(
            "ETF universe recovery: "
            f"live={live_count} offline={offline_count} "
            f"hydrated={hydrated_selected}/{len(codes)} "
            f"explicit_etns_skipped={rejected_etns} "
            f"appended={appended_count} refreshed={refreshed_count}"
        )
        for failure in source_failures:
            log(f"ETF universe recovery source failure: {failure}")
    return candidates, tuple(source_failures)
