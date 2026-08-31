"""Deterministic stock-only adapter for the local AInvest market-code skill.

The skill's command-line search intentionally supports many asset classes and may
refresh its security CSV after a miss. Theme candidate generation needs a narrower
contract: resolve only supported US ordinary stocks from one frozen local snapshot,
without network access or reference-file mutation.
"""

from __future__ import annotations

import importlib.util
import re
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path
from types import ModuleType
from typing import Iterable, Literal, Mapping


SUPPORTED_STOCK_MARKETS = frozenset({"169", "170", "171", "185", "186"})

ResolutionStatus = Literal[
    "resolved", "not_found", "ambiguous", "conflict", "not_stock"
]
MatchKind = Literal["market_code", "ticker", "exact_name", "partial_name"]

_MARKET_ALIASES = {
    "169": frozenset({"169", "nyse", "arca", "nyse arca", "n"}),
    "170": frozenset({"170", "amex", "nyse amex", "american", "a"}),
    "171": frozenset({"171", "bats", "cboe", "cboe bzx", "z"}),
    "185": frozenset(
        {"185", "nasdaq", "nasdaq global select", "nasdaq global", "q"}
    ),
    "186": frozenset(
        {"186", "nasdaq capital", "nasdaq capital market", "nasdaq small cap", "q"}
    ),
}
_CORPORATE_SUFFIXES = frozenset(
    {
        "co",
        "company",
        "corp",
        "corporation",
        "group",
        "holding",
        "holdings",
        "inc",
        "incorporated",
        "limited",
        "ltd",
        "plc",
    }
)


@dataclass(frozen=True)
class SecurityResolution:
    """Outcome of one deterministic identifier or entity resolution."""

    status: ResolutionStatus
    query: str
    market_code: str | None = None
    security_name: str = ""
    security_name_zh: str = ""
    match_kind: MatchKind | None = None
    candidates: tuple[str, ...] = ()
    reason: str = ""

    @property
    def resolved(self) -> bool:
        return self.status == "resolved" and self.market_code is not None


@dataclass(frozen=True)
class _IndexedRow:
    market_code: str
    market: str
    ticker: str
    security_type: str
    security_name: str
    security_name_zh: str
    normalized_names: tuple[str, ...]
    compact_names: tuple[str, ...]

    @property
    def eligible(self) -> bool:
        return self.security_type == "ES" and self.market in SUPPORTED_STOCK_MARKETS


@dataclass(frozen=True)
class _Match:
    status: ResolutionStatus
    query: str
    kind: MatchKind | None = None
    rows: tuple[_IndexedRow, ...] = ()
    reason: str = ""


def _normalize_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().strip()
    text = text.replace("&", " and ")
    text = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", " ", text)
    return " ".join(text.split())


def _dedupe_rows(rows: Iterable[_IndexedRow]) -> tuple[_IndexedRow, ...]:
    by_code: dict[str, _IndexedRow] = {}
    for row in rows:
        by_code.setdefault(row.market_code, row)
    return tuple(by_code[code] for code in sorted(by_code))


class MarketCodeResolver:
    """Resolve full codes, tickers, and EN/ZH names to supported US stocks.

    When ``rows`` is omitted, the resolver dynamically loads the market-code skill
    exactly once for this instance and calls only its local ``load_rows`` helper.
    Supplying rows bypasses module loading and makes ambiguity behavior easy to test.
    """

    DEFAULT_SCRIPT_PATH = (
        Path(__file__).resolve().parent.parent
        / "Skills"
        / "ainvest-marketcode"
        / "scripts"
        / "find_market_code.py"
    )

    def __init__(
        self,
        rows: Iterable[Mapping[str, object]] | None = None,
        *,
        script_path: str | Path | None = None,
    ):
        self.script_path = Path(script_path or self.DEFAULT_SCRIPT_PATH).resolve()
        self._skill_module: ModuleType | None = None
        if rows is None:
            self._skill_module = self._load_skill_module(self.script_path)
            # ``load_rows`` reads local CSV/JSON references only. Deliberately do
            # not call the skill's ``refresh_csv`` or CLI ``main`` path.
            rows = self._skill_module.load_rows()

        self._rows = tuple(self._coerce_row(row) for row in rows)
        self._rows = tuple(row for row in self._rows if row is not None)
        self._build_indexes()

    @staticmethod
    def _load_skill_module(path: Path) -> ModuleType:
        if not path.is_file():
            raise FileNotFoundError(f"AInvest market-code skill not found: {path}")
        spec = importlib.util.spec_from_file_location("_ainvest_marketcode_find", path)
        if spec is None or spec.loader is None:
            raise ImportError(f"Cannot load AInvest market-code skill: {path}")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    @staticmethod
    def _coerce_row(raw: Mapping[str, object]) -> _IndexedRow | None:
        market_code = str(raw.get("key") or raw.get("market_code") or "").strip()
        market = str(raw.get("market") or "").strip()
        ticker = str(raw.get("code") or "").strip()
        if market_code and ":" in market_code:
            key_market, key_ticker = market_code.split(":", 1)
            market = market or key_market
            ticker = ticker or key_ticker
        if not market or not ticker:
            return None
        market = market.upper()
        ticker = ticker.upper()
        market_code = f"{market}:{ticker}"

        security_name = str(raw.get("security_name") or "").strip()
        security_name_zh = str(raw.get("security_name_zh") or "").strip()
        search_names = [security_name, security_name_zh]
        supplied_names = raw.get("search_names")
        if isinstance(supplied_names, (list, tuple)):
            search_names.extend(str(value).strip() for value in supplied_names)

        normalized_names = tuple(
            dict.fromkeys(
                name for name in (_normalize_text(value) for value in search_names) if name
            )
        )
        compact_names = tuple(
            dict.fromkeys(name.replace(" ", "") for name in normalized_names if name)
        )
        return _IndexedRow(
            market_code=market_code,
            market=market,
            ticker=ticker,
            security_type=str(raw.get("security_type") or "").strip().upper(),
            security_name=security_name,
            security_name_zh=security_name_zh,
            normalized_names=normalized_names,
            compact_names=compact_names,
        )

    def _build_indexes(self) -> None:
        self._all_by_code: dict[str, list[_IndexedRow]] = {}
        self._all_by_ticker: dict[str, list[_IndexedRow]] = {}
        self._all_by_name: dict[str, list[_IndexedRow]] = {}
        self._all_by_compact_name: dict[str, list[_IndexedRow]] = {}
        for row in self._rows:
            self._all_by_code.setdefault(row.market_code, []).append(row)
            self._all_by_ticker.setdefault(row.ticker, []).append(row)
            for name in row.normalized_names:
                self._all_by_name.setdefault(name, []).append(row)
            for name in row.compact_names:
                self._all_by_compact_name.setdefault(name, []).append(row)

    @staticmethod
    def _market_matches(row: _IndexedRow, market_hint: str | None) -> bool:
        if not market_hint:
            return True
        hint = _normalize_text(market_hint)
        return hint in _MARKET_ALIASES.get(row.market, frozenset({row.market.casefold()}))

    def _classify_matches(
        self,
        query: str,
        kind: MatchKind,
        rows: Iterable[_IndexedRow],
        market_hint: str | None,
    ) -> _Match:
        all_rows = _dedupe_rows(rows)
        hinted_rows = _dedupe_rows(
            row for row in all_rows if self._market_matches(row, market_hint)
        )
        eligible = _dedupe_rows(row for row in hinted_rows if row.eligible)
        if len(eligible) == 1:
            return _Match("resolved", query, kind, eligible)
        if len(eligible) > 1:
            return _Match(
                "ambiguous", query, kind, eligible, "multiple supported stocks match"
            )
        if hinted_rows:
            return _Match(
                "not_stock",
                query,
                kind,
                hinted_rows,
                "identifier exists but is not a supported US ordinary stock",
            )
        return _Match("not_found", query, kind, (), "no matching security")

    def _match_market_code(
        self, market_code: str, market_hint: str | None = None
    ) -> _Match:
        query = str(market_code or "").strip()
        normalized = query.upper()
        if ":" not in normalized:
            return _Match("not_found", query, "market_code", (), "invalid market_code")
        return self._classify_matches(
            query,
            "market_code",
            self._all_by_code.get(normalized, ()),
            market_hint,
        )

    def _match_ticker(self, ticker: str, market_hint: str | None = None) -> _Match:
        query = str(ticker or "").strip()
        return self._classify_matches(
            query,
            "ticker",
            self._all_by_ticker.get(query.upper(), ()),
            market_hint,
        )

    @staticmethod
    def _partial_query_is_safe(normalized: str, compact: str) -> bool:
        if not compact:
            return False
        if re.search(r"[\u4e00-\u9fff]", normalized):
            return len(compact) >= 3
        tokens = normalized.split()
        distinctive = [
            token
            for token in tokens
            if len(token) >= 5 and token not in _CORPORATE_SUFFIXES
        ]
        return bool(distinctive)

    @staticmethod
    def _is_conservative_partial(
        row: _IndexedRow, normalized: str, compact: str
    ) -> bool:
        has_cjk = bool(re.search(r"[\u4e00-\u9fff]", normalized))
        for candidate, candidate_compact in zip(
            row.normalized_names, row.compact_names
        ):
            if has_cjk:
                if compact in candidate_compact:
                    return True
                continue

            # Prefix-only matching recovers canonical short forms such as
            # "Nebius" -> "Nebius Group" without accepting arbitrary internal
            # fragments such as "Cloud" -> "Alpha Cloud Holdings".
            if candidate == normalized or candidate.startswith(normalized + " "):
                return True
            candidate_tokens = candidate.split()
            while candidate_tokens and candidate_tokens[-1] in _CORPORATE_SUFFIXES:
                candidate_tokens.pop()
            if " ".join(candidate_tokens) == normalized:
                return True
        return False

    def _match_name(self, name: str, market_hint: str | None = None) -> _Match:
        query = str(name or "").strip()
        normalized = _normalize_text(query)
        compact = normalized.replace(" ", "")
        if not normalized:
            return _Match("not_found", query, "exact_name", (), "empty name")

        exact_rows = [
            *self._all_by_name.get(normalized, ()),
            *self._all_by_compact_name.get(compact, ()),
        ]
        if exact_rows:
            return self._classify_matches(
                query, "exact_name", exact_rows, market_hint
            )

        if not self._partial_query_is_safe(normalized, compact):
            return _Match(
                "not_found",
                query,
                "partial_name",
                (),
                "name is too broad for partial matching",
            )
        partial_rows = [
            row
            for row in self._rows
            if self._market_matches(row, market_hint)
            and self._is_conservative_partial(row, normalized, compact)
        ]
        return self._classify_matches(
            query, "partial_name", partial_rows, market_hint
        )

    @staticmethod
    def _to_resolution(match: _Match) -> SecurityResolution:
        candidates = tuple(row.market_code for row in match.rows)
        row = match.rows[0] if match.status == "resolved" else None
        return SecurityResolution(
            status=match.status,
            query=match.query,
            market_code=row.market_code if row else None,
            security_name=row.security_name if row else "",
            security_name_zh=row.security_name_zh if row else "",
            match_kind=match.kind,
            candidates=candidates,
            reason=match.reason,
        )

    def resolve(
        self, query: str, *, market_hint: str | None = None
    ) -> SecurityResolution:
        """Resolve one free-form identifier using the documented precedence."""
        value = str(query or "").strip()
        if not value:
            return SecurityResolution(
                "not_found", value, reason="query is empty"
            )
        if ":" in value:
            return self._to_resolution(self._match_market_code(value, market_hint))

        ticker_match = self._match_ticker(value, market_hint)
        if ticker_match.status != "not_found":
            return self._to_resolution(ticker_match)

        return self._to_resolution(self._match_name(value, market_hint))

    def resolve_entity(
        self,
        *,
        market_code: str | None = None,
        ticker: str | None = None,
        name: str | None = None,
        market_hint: str | None = None,
    ) -> SecurityResolution:
        """Resolve structured entity fields and reject contradictory identities.

        An explicit full code is authoritative and must itself be valid. A ticker
        or exact/partial name may disambiguate the other field, but two disjoint
        candidate sets are a conflict.
        """
        supplied_query = " | ".join(
            value for value in (market_code, ticker, name) if value
        )
        if market_code:
            code_match = self._match_market_code(market_code, market_hint)
            if code_match.status != "resolved":
                return self._to_resolution(code_match)
            matches = [code_match]
        else:
            matches = []

        if ticker:
            matches.append(self._match_ticker(ticker, market_hint))
        if name:
            matches.append(self._match_name(name, market_hint))
        if not matches:
            return SecurityResolution(
                "not_found", supplied_query, reason="no entity identifier supplied"
            )

        non_stock = next((match for match in matches if match.status == "not_stock"), None)
        if non_stock is not None:
            return self._to_resolution(non_stock)

        resolved_matches = [match for match in matches if match.status == "resolved"]
        resolved_codes = {
            match.rows[0].market_code for match in resolved_matches if match.rows
        }
        if len(resolved_codes) > 1:
            return SecurityResolution(
                "conflict",
                supplied_query,
                candidates=tuple(sorted(resolved_codes)),
                reason="entity identifiers resolve to different securities",
            )

        if resolved_codes:
            resolved_code = next(iter(resolved_codes))
            for match in matches:
                if match.status != "ambiguous":
                    continue
                candidate_codes = {row.market_code for row in match.rows}
                if resolved_code not in candidate_codes:
                    return SecurityResolution(
                        "conflict",
                        supplied_query,
                        candidates=tuple(sorted(candidate_codes | {resolved_code})),
                        reason="entity identifiers have disjoint candidate sets",
                    )
            # Preserve identifier precedence in the reported match kind.
            for kind in ("market_code", "ticker", "exact_name", "partial_name"):
                chosen = next(
                    (
                        match
                        for match in resolved_matches
                        if match.kind == kind and match.rows[0].market_code == resolved_code
                    ),
                    None,
                )
                if chosen is not None:
                    resolution = self._to_resolution(chosen)
                    return replace(resolution, query=supplied_query)

        ambiguous = next((match for match in matches if match.status == "ambiguous"), None)
        if ambiguous is not None:
            return self._to_resolution(ambiguous)
        missing = next((match for match in matches if match.status == "not_found"), None)
        if missing is not None:
            return self._to_resolution(missing)
        return SecurityResolution(
            "not_found", supplied_query, reason="entity could not be resolved"
        )


__all__ = [
    "MarketCodeResolver",
    "SecurityResolution",
    "SUPPORTED_STOCK_MARKETS",
]
