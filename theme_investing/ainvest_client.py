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

from config import QuoteConfig

_SSL_CTX = ssl._create_unverified_context()

MULTI_KLINE_MAX = 16          # API hard limit: <=16 codes per multi_kline call
SNAPSHOT_PAGE_MAX = 1000      # API hard limit: page.count <= 1000

_NAMES_CSV = (
    Path(__file__).resolve().parent.parent
    / "Skills" / "ainvest-marketcode" / "references" / "ainvest_market_code_names_en.csv"
)


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
    def rank_universe(
        self,
        selector: dict,
        indicators: list[dict],
        *,
        total: int,
        sort_pos: int | None = 0,
        order: str = "desc",
        page_size: int = SNAPSHOT_PAGE_MAX,
    ) -> list[dict]:
        """Return up to ``total`` ranked rows as dicts:
        ``{"code","rank","values":{req_unique_id:v}}`` in pool/sort order."""
        page_size = min(page_size, SNAPSHOT_PAGE_MAX)
        rows: list[dict] = []
        seen: set[str] = set()
        begin = 0
        while len(rows) < total:
            body = {
                "symbol": [selector],
                "indicator": indicators,
                "page": {"begin": begin, "count": min(page_size, total - len(rows))},
            }
            if sort_pos is not None:
                body["sort"] = [{"pos": sort_pos, "order": order}]
            resp = self._post(self.cfg.snapshot_url, body)
            data = resp.get("data") or {}
            ind_order = [i.get("req_unique_id") for i in (data.get("indicator") or [])]
            page_rows = data.get("data") or []
            if not page_rows:
                break
            for r in page_rows:
                code = r.get("symbol_code")
                if not code or code in seen:
                    continue
                seen.add(code)
                vals = {}
                for idx, cell in enumerate(r.get("value") or []):
                    if idx < len(ind_order):
                        vals[ind_order[idx]] = cell.get("v")
                rows.append({"code": code, "rank": len(rows) + 1, "values": vals})
            begin += len(page_rows)
            if len(page_rows) < body["page"]["count"]:
                break
        return rows[:total]

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
