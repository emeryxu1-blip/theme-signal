"""AInvest OpenAPI quote client (snapshot pagination + multi_kline batching).

Uses only the standard library. TLS verification is disabled because the
corporate proxy in front of the c-side gateway presents a self-signed chain
(the same reason ``curl -k`` is required on this network).
"""

from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterable

from config import QuoteConfig

_SSL_CTX = ssl._create_unverified_context()

MULTI_KLINE_MAX = 16          # API hard limit: <=16 codes per multi_kline call
SNAPSHOT_PAGE_MAX = 1000      # API hard limit: page.count <= 1000
SNAPSHOT_CODE_MAX = 10000     # API hard limit: total explicit input codes <= 10000

RELATED_ETF_PROMPT_ID = "677251bbbc4823684c64145d"
ETF_HOLDING_WEIGHT_ID = "国际北美etf@Holding Stock Weight(View)"
ETF_AUM_ID = "国际北美etf@Assets Under Management(Latest)"
ETF_LEVERAGE_ID = "国际北美etf@Leverage Ratio"
ETF_DIRECTION_ID = "国际北美etf@Investment Direction"

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
                with urllib.request.urlopen(req, timeout=self.timeout, context=_SSL_CTX) as resp:
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
    def iter_related_etfs(self, stock_code: str, *, page_size: int = 100):
        """Yield ETFs containing ``stock_code``, ordered by its portfolio weight.

        Holding weights are percentage points (for example ``19.99`` means
        19.99%). Transport failures are surfaced so callers can log an explicit
        partial-data warning instead of silently treating it as an empty pool.
        """
        selector = {
            "type": "prompt_id",
            "value": [RELATED_ETF_PROMPT_ID],
            "attr": {"market_code": stock_code},
        }
        indicators = [
            {"id": ETF_HOLDING_WEIGHT_ID, "req_unique_id": "holding_weight",
             "attr": {"match_code": stock_code}},
            {"id": "55", "req_unique_id": "name"},
            {"id": ETF_AUM_ID, "req_unique_id": "aum"},
            {"id": ETF_LEVERAGE_ID, "req_unique_id": "leverage"},
            {"id": ETF_DIRECTION_ID, "req_unique_id": "direction"},
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
            }

    def iter_prompt_etfs(self, prompt_id: str, *, page_size: int = 100):
        """Yield a curated ETF prompt pool in descending AUM order."""
        selector = {"type": "prompt_id", "value": [prompt_id]}
        indicators = [
            {"id": ETF_AUM_ID, "req_unique_id": "aum"},
            {"id": "55", "req_unique_id": "name"},
            {"id": ETF_LEVERAGE_ID, "req_unique_id": "leverage"},
            {"id": ETF_DIRECTION_ID, "req_unique_id": "direction"},
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
