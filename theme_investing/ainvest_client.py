"""AInvest OpenAPI quote client (snapshot pagination + multi_kline batching)."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable

from config import QuoteConfig

MULTI_KLINE_MAX = 16          # API hard limit: <=16 codes per multi_kline call
SNAPSHOT_PAGE_MAX = 1000      # API hard limit: page.count <= 1000
SNAPSHOT_CODE_MAX = 10000     # API hard limit: total explicit input codes <= 10000

RELATED_ETF_PROMPT_ID = "677251bbbc4823684c64145d"
RELATED_LEVERAGED_ETF_PROMPT_ID = "6762c178784e3a2b800f5bae"
RELATED_INVERSE_ETF_PROMPT_ID = "6762c196bc4823684c641404"
RELATED_LONG_ETF_PROMPT_ID = "6762c1ae784e3a2b800f5baf"
ALL_US_ETF_PROMPT_ID = "6809daea3ed15058a925c378"
CONCEPT_INDEX_RELATED_ETF_PROMPT_ID = "69285634069a48065f159442"

STOCK_ETF_PROMPT_IDS = {
    "related": RELATED_ETF_PROMPT_ID,
    "leveraged": RELATED_LEVERAGED_ETF_PROMPT_ID,
    "inverse": RELATED_INVERSE_ETF_PROMPT_ID,
    "long": RELATED_LONG_ETF_PROMPT_ID,
}

ETF_HOLDING_WEIGHT_ID = "国际北美etf@Holding Stock Weight(View)"
ETF_COMPONENT_HOLDING_RATIO_ID = "ext_etf_holding_ratio"
ETF_AUM_ID = "国际北美etf@Assets Under Management(Latest)"
ETF_LEVERAGE_ID = "国际北美etf@Leverage Ratio"
ETF_DIRECTION_ID = "国际北美etf@Investment Direction"
ETF_BENCHMARK_CODE_ID = "国际北美etf@Benchmark HQ Code(AInvest)"
ETF_SECURITY_CLASS_ID = "ext_metric_security_class"
ETF_TYPE_ID = "ext_metric_etf_type"
ETF_INDEX_ETF_CODE_ID = "ext_metric_index_etf_code"
ETF_INDEX_HOLDING_OVERLAP_ID = "block_etf_holdrate"
ETF_INDEX_RETURN_SIMILARITY_ID = "block_etf_risekline"

_REFS_DIR = (
    Path(__file__).resolve().parent.parent
    / "Skills" / "ainvest-marketcode" / "references"
)
_NAMES_CSV = _REFS_DIR / "ainvest_market_code_names_en.csv"
_SECURITY_CSV = _REFS_DIR / "security_config_V1.1.csv"


def _load_names() -> dict:
    names: dict[str, str] = {}
    if not _NAMES_CSV.exists():
        return names
    import csv
    with _NAMES_CSV.open(encoding="utf-8") as fh:
        for row in csv.reader(fh):
            if len(row) >= 2 and row[0] != "market_code":
                names[row[0]] = row[1]
    return names


class AInvestClient:
    def __init__(self, cfg: QuoteConfig, *, retries: int = 3, timeout: float = 30.0):
        self.cfg = cfg
        self.retries = retries
        self.timeout = timeout
        self.names = _load_names()

    # -- transport -----------------------------------------------------------
    def _post(self, url: str, body: dict) -> dict:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        last_err = None
        for attempt in range(self.retries):
            request_headers = self.cfg.headers
            # B-side quoteag endpoints use the quoteag application key; index_api
            # endpoints use the index-api key. C-side Cookie auth is unchanged.
            if (self.cfg.scene == "b" and self.cfg.endpoint_family_credentials
                    and url == self.cfg.multi_kline_url):
                request_headers = dict(self.cfg.headers)
                request_headers["apikey"] = self.cfg.endpoint_family_credentials["quoteag"]
            req = urllib.request.Request(url, data=data, headers=request_headers, method="POST")
            try:
                with urllib.request.urlopen(
                    req, timeout=self.timeout, context=self.cfg.ssl_context()
                ) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
                last_err = exc
                time.sleep(0.6 * (attempt + 1))
        raise RuntimeError(f"quote request failed after {self.retries} tries: {last_err}")

    # -- snapshot universe ---------------------------------------------------
    def iter_ranked(
        self,
        selector: dict,
        indicators: list[dict],
        *,
        sort_pos: int | None = 0,
        order: str = "desc",
        page_size: int = SNAPSHOT_PAGE_MAX,
        strict: bool = False,
    ):
        """Lazily yield ranked rows ``{"code","rank","values":{...}}`` in pool/sort
        order, paging the snapshot endpoint on demand until the pool is exhausted.

        No upper bound: the caller decides when to stop consuming. A page is only
        fetched from the network when the consumer asks for a row beyond the last
        one already yielded, so a scan that stops early costs only the pages it read.
        """
        page_size = min(page_size, SNAPSHOT_PAGE_MAX)
        seen: set[str] = set()
        begin = 0
        rank = 0
        while True:
            body = {
                "symbol": [selector],
                "indicator": indicators,
                "page": {"begin": begin, "count": page_size},
            }
            if sort_pos is not None:
                body["sort"] = [{"pos": sort_pos, "order": order}]
            try:
                resp = self._post(self.cfg.snapshot_url, body)
            except RuntimeError:
                if strict:
                    raise
                break
            status_code = resp.get("status_code")
            if status_code not in (None, 0):
                if strict:
                    status_msg = resp.get("status_msg") or "unknown snapshot error"
                    raise RuntimeError(
                        f"snapshot failed ({status_code}): {status_msg}"
                    )
                break
            data = resp.get("data") or {}
            ind_order = [i.get("req_unique_id") for i in (data.get("indicator") or [])]
            page_rows = data.get("data") or []
            if not page_rows:
                break
            new_rows = 0
            for r in page_rows:
                code = r.get("symbol_code")
                if not code or code in seen:
                    continue
                seen.add(code)
                new_rows += 1
                vals = {}
                for idx, cell in enumerate(r.get("value") or []):
                    if idx < len(ind_order):
                        vals[ind_order[idx]] = cell.get("v")
                rank += 1
                yield {"code": code, "rank": rank, "values": vals}
            # Some gateways can ignore ``page.begin`` and repeat the last full
            # page. Without this guard a bounded outer ``islice`` would still wait
            # forever for its next unique row.
            if new_rows == 0:
                break
            begin += len(page_rows)
            if len(page_rows) < page_size:
                break

    def rank_universe(
        self,
        selector: dict,
        indicators: list[dict],
        *,
        total: int,
        sort_pos: int | None = 0,
        order: str = "desc",
        page_size: int = SNAPSHOT_PAGE_MAX,
        strict: bool = False,
    ) -> list[dict]:
        """Return up to ``total`` ranked rows (eager wrapper around ``iter_ranked``)."""
        from itertools import islice
        return list(islice(
            self.iter_ranked(selector, indicators, sort_pos=sort_pos,
                             order=order, page_size=page_size, strict=strict),
            total))

    # -- ETF discovery / evidence -------------------------------------------
    def iter_stock_etfs(
        self,
        stock_code: str,
        relation: str = "related",
        *,
        page_size: int = SNAPSHOT_PAGE_MAX,
    ):
        """Yield ETFs related to one stock through a documented prompt relation.

        ``relation`` is one of ``related``, ``leveraged``, ``inverse``, or
        ``long``. Dedicated derivative relations matter because an inverse
        single-stock wrapper can have no physical holding weight and therefore
        appear very late in the generic holding-weight-sorted pool.

        Holding weights are percentage points; derivative effective exposure can
        exceed 100% or be null. Transport failures are surfaced so callers can
        record partial data instead of silently treating it as an empty pool.
        """
        relation = str(relation or "").strip().lower()
        try:
            prompt_id = STOCK_ETF_PROMPT_IDS[relation]
        except KeyError as exc:
            allowed = ", ".join(STOCK_ETF_PROMPT_IDS)
            raise ValueError(
                f"unsupported stock ETF relation {relation!r}; expected one of: {allowed}"
            ) from exc

        selector = {
            "type": "prompt_id",
            "value": [prompt_id],
            "attr": {"market_code": stock_code},
        }
        indicators = [
            {"id": ETF_HOLDING_WEIGHT_ID, "req_unique_id": "holding_weight",
             "attr": {"match_code": stock_code}},
            {"id": "55", "req_unique_id": "name"},
            {"id": ETF_AUM_ID, "req_unique_id": "aum"},
            {"id": ETF_LEVERAGE_ID, "req_unique_id": "leverage"},
            {"id": ETF_DIRECTION_ID, "req_unique_id": "direction"},
            {"id": ETF_BENCHMARK_CODE_ID, "req_unique_id": "benchmark_code"},
            {"id": ETF_SECURITY_CLASS_ID, "req_unique_id": "security_class"},
            {"id": ETF_TYPE_ID, "req_unique_id": "etf_type"},
            {"id": ETF_INDEX_ETF_CODE_ID, "req_unique_id": "index_etf_code"},
        ]
        for row in self.iter_ranked(
                selector, indicators, sort_pos=0, page_size=page_size, strict=True):
            values = row["values"]
            yield {
                **row,
                "name": values.get("name") or self.name_of(row["code"]),
                "holding_weight": values.get("holding_weight"),
                "aum": values.get("aum"),
                "leverage": values.get("leverage"),
                "direction": values.get("direction"),
                "benchmark_code": values.get("benchmark_code"),
                "security_class": values.get("security_class"),
                "etf_type": values.get("etf_type"),
                "index_etf_code": values.get("index_etf_code"),
                "stock_relation_kind": relation,
            }

    def iter_related_etfs(self, stock_code: str, *, page_size: int = 100):
        """Backward-compatible generic stock-to-ETF relation iterator."""
        yield from self.iter_stock_etfs(
            stock_code, "related", page_size=page_size
        )

    def iter_related_leveraged_etfs(
        self, stock_code: str, *, page_size: int = SNAPSHOT_PAGE_MAX
    ):
        """Yield leveraged ETFs related to ``stock_code``."""
        yield from self.iter_stock_etfs(
            stock_code, "leveraged", page_size=page_size
        )

    def iter_related_inverse_etfs(
        self, stock_code: str, *, page_size: int = SNAPSHOT_PAGE_MAX
    ):
        """Yield inverse ETFs related to ``stock_code``."""
        yield from self.iter_stock_etfs(
            stock_code, "inverse", page_size=page_size
        )

    def iter_related_long_etfs(
        self, stock_code: str, *, page_size: int = SNAPSHOT_PAGE_MAX
    ):
        """Yield long ETFs related to ``stock_code``."""
        yield from self.iter_stock_etfs(
            stock_code, "long", page_size=page_size
        )

    def iter_prompt_etfs(self, prompt_id: str, *, page_size: int = 100):
        """Yield a curated ETF prompt pool in descending AUM order."""
        selector = {"type": "prompt_id", "value": [prompt_id]}
        indicators = [
            {"id": ETF_AUM_ID, "req_unique_id": "aum"},
            {"id": "55", "req_unique_id": "name"},
            {"id": ETF_LEVERAGE_ID, "req_unique_id": "leverage"},
            {"id": ETF_DIRECTION_ID, "req_unique_id": "direction"},
            {"id": ETF_BENCHMARK_CODE_ID, "req_unique_id": "benchmark_code"},
            {"id": ETF_SECURITY_CLASS_ID, "req_unique_id": "security_class"},
            {"id": ETF_TYPE_ID, "req_unique_id": "etf_type"},
            {"id": ETF_INDEX_ETF_CODE_ID, "req_unique_id": "index_etf_code"},
        ]
        for row in self.iter_ranked(
                selector, indicators, sort_pos=0, page_size=page_size, strict=True):
            values = row["values"]
            yield {
                **row,
                "name": values.get("name") or self.name_of(row["code"]),
                "aum": values.get("aum"),
                "leverage": values.get("leverage"),
                "direction": values.get("direction"),
                "benchmark_code": values.get("benchmark_code"),
                "security_class": values.get("security_class"),
                "etf_type": values.get("etf_type"),
                "index_etf_code": values.get("index_etf_code"),
            }

    def iter_concept_index_etfs(
        self, index_code: str, *, page_size: int = 100
    ):
        """Yield ETFs linked to one exact AInvest concept index.

        The documented prompt defines relatedness from constituent overlap. The
        two index-relative metrics are retained as source evidence only; final
        basket eligibility still comes from independently fetched full holdings.
        """
        index_code = str(index_code or "").strip()
        if not index_code.startswith("89:"):
            raise ValueError("concept index code must use the 89: market prefix")
        selector = {
            "type": "prompt_id",
            "value": [CONCEPT_INDEX_RELATED_ETF_PROMPT_ID],
            "attr": {"market_code": index_code},
        }
        indicators = [
            {
                "id": ETF_INDEX_HOLDING_OVERLAP_ID,
                "req_unique_id": "block_etf_holdrate",
                "attr": {"match_code": index_code},
            },
            {
                "id": ETF_INDEX_RETURN_SIMILARITY_ID,
                "req_unique_id": "block_etf_risekline",
                "attr": {"match_code": index_code},
            },
            {"id": "55", "req_unique_id": "name"},
            {"id": ETF_AUM_ID, "req_unique_id": "aum"},
            {"id": ETF_LEVERAGE_ID, "req_unique_id": "leverage"},
            {"id": ETF_DIRECTION_ID, "req_unique_id": "direction"},
            {"id": ETF_BENCHMARK_CODE_ID, "req_unique_id": "benchmark_code"},
            {"id": ETF_SECURITY_CLASS_ID, "req_unique_id": "security_class"},
            {"id": ETF_TYPE_ID, "req_unique_id": "etf_type"},
            {"id": ETF_INDEX_ETF_CODE_ID, "req_unique_id": "index_etf_code"},
        ]
        for row in self.iter_ranked(
                selector, indicators, sort_pos=0, page_size=page_size, strict=True):
            values = row["values"]
            yield {
                **row,
                "name": values.get("name") or self.name_of(row["code"]),
                "block_etf_holdrate": values.get("block_etf_holdrate"),
                "block_etf_risekline": values.get("block_etf_risekline"),
                "aum": values.get("aum"),
                "leverage": values.get("leverage"),
                "direction": values.get("direction"),
                "benchmark_code": values.get("benchmark_code"),
                "security_class": values.get("security_class"),
                "etf_type": values.get("etf_type"),
                "index_etf_code": values.get("index_etf_code"),
            }

    def iter_etf_holdings(
        self, etf_code: str, *, page_size: int = SNAPSHOT_PAGE_MAX
    ):
        """Yield every reported component of one ETF in descending weight order.

        Holding weights are percentage points. Strict pagination is intentional:
        callers must be able to distinguish an unavailable portfolio from a real
        empty response so final eligibility can fail closed.
        """
        selector = {
            "type": "link_code",
            "value": [etf_code],
            "attr": {"link_type": "holding"},
        }
        indicators = [
            {
                "id": ETF_COMPONENT_HOLDING_RATIO_ID,
                "req_unique_id": "holding_weight",
                "attr": {"match_code": etf_code},
            },
            {"id": "55", "req_unique_id": "name"},
        ]
        for row in self.iter_ranked(
                selector, indicators, sort_pos=0, page_size=page_size, strict=True):
            values = row["values"]
            yield {
                "code": row["code"],
                "name": values.get("name") or self.name_of(row["code"]),
                "weight_pct": values.get("holding_weight"),
                "rank": row["rank"],
            }

    @staticmethod
    def _chunks(values: list[str], size: int):
        for start in range(0, len(values), size):
            yield values[start:start + size]

    def _explicit_snapshot(
        self, codes: Iterable[str], indicators: list[dict]
    ) -> list[dict]:
        """Fetch strict snapshot rows for explicit codes in bounded chunks.

        Chunking at 1,000 keeps every request to one response page and also works
        for caller limits larger than 1,000 without approaching the API's 10,000
        input-code ceiling.
        """
        ordered = list(dict.fromkeys(str(code) for code in codes if code))
        rows: list[dict] = []
        for chunk in self._chunks(ordered, SNAPSHOT_PAGE_MAX):
            rows.extend(self.rank_universe(
                {"type": "market_code", "value": chunk}, indicators,
                total=len(chunk), sort_pos=None, page_size=len(chunk), strict=True,
            ))
        return rows

    def etf_holding_weights_for_stock(
        self, etf_codes: Iterable[str], stock_code: str
    ) -> dict[str, float | None]:
        """Return exact ETF holding weights for one stock across explicit ETFs.

        The quote service does not reliably support repeating this indicator with
        several ``match_code`` attrs in one request, so callers intentionally make
        one request per selected stock.
        """
        indicators = [{
            "id": ETF_HOLDING_WEIGHT_ID,
            "req_unique_id": "holding_weight",
            "attr": {"match_code": stock_code},
        }]
        return {
            row["code"]: row["values"].get("holding_weight")
            for row in self._explicit_snapshot(etf_codes, indicators)
        }

    def security_profiles(self, codes: Iterable[str]) -> dict[str, dict]:
        """Return compact company facts used for semantic exposure scoring."""
        indicators = [
            {"id": "55", "req_unique_id": "name"},
            {"id": "company_introduction", "req_unique_id": "company_introduction"},
            {"id": "ext_metric_sector_1_name", "req_unique_id": "sector"},
            {"id": "ext_metric_sector_3_name", "req_unique_id": "industry"},
        ]
        out: dict[str, dict] = {}
        for row in self._explicit_snapshot(codes, indicators):
            values = dict(row["values"])
            values["name"] = values.get("name") or self.name_of(row["code"])
            out[row["code"]] = values
        return out

    def etf_metadata(self, etf_codes: Iterable[str]) -> dict[str, dict]:
        """Return deterministic ranking/filter metadata for explicit ETF codes."""
        indicators = [
            {"id": "55", "req_unique_id": "name"},
            {"id": ETF_LEVERAGE_ID, "req_unique_id": "leverage"},
            {"id": ETF_DIRECTION_ID, "req_unique_id": "direction"},
            {"id": ETF_AUM_ID, "req_unique_id": "aum"},
            {"id": "国际北美etf@Expense Ratio", "req_unique_id": "expense_ratio"},
            {"id": "国际北美etf@Latest Net Fund Flow", "req_unique_id": "net_flow"},
            {"id": "国际北美etf@Benchmark", "req_unique_id": "benchmark"},
            {"id": ETF_BENCHMARK_CODE_ID, "req_unique_id": "benchmark_code"},
            {"id": ETF_SECURITY_CLASS_ID, "req_unique_id": "security_class"},
            {"id": ETF_TYPE_ID, "req_unique_id": "etf_type"},
            {"id": ETF_INDEX_ETF_CODE_ID, "req_unique_id": "index_etf_code"},
            {"id": "国际北美etf@Asset Class", "req_unique_id": "asset_class"},
            {"id": "fundCategory", "req_unique_id": "fund_category"},
            {"id": "fundFocus", "req_unique_id": "fund_focus"},
            {"id": "fundNiche", "req_unique_id": "fund_niche"},
            {"id": "fundStrategy", "req_unique_id": "fund_strategy"},
            {"id": "fundIndexTracked", "req_unique_id": "index_tracked"},
            {"id": "fundSelectionCriteria", "req_unique_id": "selection_criteria"},
            {"id": "13", "req_unique_id": "volume",
             "attr": {"trade_class": "intraday"}},
            {"id": "19", "req_unique_id": "turnover",
             "attr": {"trade_class": "intraday"}},
        ]
        out: dict[str, dict] = {}
        for row in self._explicit_snapshot(etf_codes, indicators):
            values = dict(row["values"])
            values["name"] = values.get("name") or self.name_of(row["code"])
            out[row["code"]] = values
        return out

    def iter_relation_codes(
        self,
        relation: str,
        symbol: str,
        symbol_type: str,
        *,
        page_size: int = SNAPSHOT_PAGE_MAX,
    ):
        """Yield every code from a paged ``relation_list`` request.

        Live ETF universes, prompt/index components, and plain ETF holding-code
        lists all share this endpoint shape, so exposing the primitive also gives
        callers a deterministic fallback building block.
        """
        if relation not in {"holding", "component"}:
            raise ValueError("relation must be 'holding' or 'component'")
        if symbol_type not in {"market_code", "prompt_id", "block_id", "group_id"}:
            raise ValueError(
                "symbol_type must be market_code, prompt_id, block_id, or group_id"
            )
        relation_url = getattr(self.cfg, "relation_list_url", None)
        if not relation_url:
            raise RuntimeError("relation_list endpoint is not configured")

        page_size = min(max(int(page_size), 1), SNAPSHOT_PAGE_MAX)
        begin = 0
        seen: set[str] = set()
        while True:
            body = {
                "relation": relation,
                "symbol": symbol,
                "symbol_type": symbol_type,
                "page": {"begin": begin, "count": page_size},
            }
            resp = self._post(relation_url, body)
            status_code = resp.get("status_code")
            if status_code not in (None, 0):
                status_msg = resp.get("status_msg") or "unknown relation-list error"
                raise RuntimeError(
                    f"relation_list failed ({status_code}): {status_msg}"
                )

            data = resp.get("data") or {}
            page_rows = data.get("data") or []
            if not page_rows:
                break
            new_codes = 0
            for item in page_rows:
                code = str(item.get("v") or "").strip()
                if not code or code in seen:
                    continue
                seen.add(code)
                new_codes += 1
                yield code
            if new_codes == 0:
                break

            begin += len(page_rows)
            total = (data.get("page") or {}).get("total")
            if isinstance(total, (int, float)) and begin >= total:
                break
            if len(page_rows) < page_size:
                break

    def iter_live_etf_codes(self, *, page_size: int = SNAPSHOT_PAGE_MAX):
        """Yield the current live all-US-ETF universe from its prompt relation."""
        yield from self.iter_relation_codes(
            "component",
            ALL_US_ETF_PROMPT_ID,
            "prompt_id",
            page_size=page_size,
        )

    def live_etf_codes(self, *, page_size: int = SNAPSHOT_PAGE_MAX) -> list[str]:
        """Return the current live all-US-ETF universe, surfacing API failures."""
        return list(self.iter_live_etf_codes(page_size=page_size))

    def etf_universe_codes(
        self, *, prefer_live: bool = True, page_size: int = SNAPSHOT_PAGE_MAX
    ) -> list[str]:
        """Return ETF codes, falling back to the offline ``CE`` universe.

        Call ``live_etf_codes`` directly when a caller must distinguish a live
        failure from a genuinely empty universe. This convenience method is for
        paths where a stale local fallback is preferable to no candidates.
        """
        if prefer_live:
            try:
                live_codes = self.live_etf_codes(page_size=page_size)
            except RuntimeError:
                live_codes = []
            if live_codes:
                return live_codes
        return self.security_codes("CE")

    def security_codes(self, security_type: str) -> list[str]:
        """All local market_codes of a given security_type (e.g. ``"CE"`` = ETF).

        Sourced from the offline security-config reference for callers that need a
        local symbol list without live ranking metrics."""
        codes: list[str] = []
        if not _SECURITY_CSV.exists():
            return codes
        import csv
        with _SECURITY_CSV.open(encoding="utf-8") as fh:
            for row in csv.reader(fh):
                if len(row) >= 2 and row[0] != "key" and row[1] == security_type:
                    codes.append(row[0])
        return codes

    # -- daily k-lines -------------------------------------------------------
    def fetch_klines(
        self, codes: list[str], *, count: int = 60, time_period: str = "day_1", end_time: int = 0
    ) -> dict:
        """Return ``{market_code: [ {t, close, volume, date_int} ] }`` (oldest→newest)."""
        out: dict[str, list[dict]] = {}
        for start in range(0, len(codes), MULTI_KLINE_MAX):
            batch = codes[start:start + MULTI_KLINE_MAX]
            by_market: dict[str, list[str]] = {}
            for c in batch:
                market, _, sym = c.partition(":")
                by_market.setdefault(market, []).append(sym)
            body = {
                "code_list": [{"market": m, "codes": cs} for m, cs in by_market.items()],
                "trade_class": "intraday",
                "time_period": time_period,
                "time_range": {"count": count, "end_time": end_time},
            }
            try:
                resp = self._post(self.cfg.multi_kline_url, body)
            except RuntimeError:
                continue
            for qd in ((resp.get("data") or {}).get("quote_data") or []):
                market = qd.get("market")
                sym = qd.get("code")
                code = f"{market}:{sym}"
                bars = []
                for row in qd.get("value") or []:
                    # [time_ms, open, high, low, close, volume, turnover, yyyymmdd]
                    if len(row) >= 6:
                        bars.append({
                            "t": row[0],
                            "close": row[4],
                            "volume": row[5],
                            "date_int": int(row[7]) if len(row) > 7 else None,
                        })
                out[code] = bars
        return out

    def name_of(self, code: str) -> str:
        return self.names.get(code, code.split(":")[-1])
