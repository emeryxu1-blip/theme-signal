"""Unit + mocked end-to-end tests. Run: python3 tests/test_workflow.py"""

import argparse
import json
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scoring
from ainvest_client import AInvestClient
from cli import _positive_int, build_parser, workflow_options
from etf_preselection import match_theme_pools, preselect_etfs
from workflow import ThemeWorkflow, validate_input


def test_event_date_int():
    assert scoring.event_date_int("2026-07-09") == 20260709


def test_kline_features_event_window_and_abnormal_return():
    bars = [
        {"t": 1, "close": 86, "volume": 9, "date_int": 20260704},
        {"t": 2, "close": 88, "volume": 10, "date_int": 20260705},
        {"t": 3, "close": 90, "volume": 11, "date_int": 20260706},
        {"t": 4, "close": 95, "volume": 12, "date_int": 20260707},
        {"t": 5, "close": 97, "volume": 13, "date_int": 20260708},
        {"t": 6, "close": 100, "volume": 100, "date_int": 20260709},
        {"t": 7, "close": 110, "volume": 80, "date_int": 20260710},
    ]
    f = scoring.compute_kline_features(bars, 20260709, benchmark_return=5.0)
    assert round(f["chg_pct"], 1) == 10.0
    assert f["rvol_event"] == 100 / 11.0  # peak event volume / pre-event median
    assert f["active_days"] == 2
    assert round(f["abnormal_return"], 1) == 5.0
    assert f["has_history"] is True  # five pre-event bars form a baseline


def test_kline_features_requires_baseline():
    bars = [{"t": i, "close": 100+i, "volume": 10+i, "date_int": 20260700+i} for i in range(1, 12)]
    f = scoring.compute_kline_features(bars, 20260720)
    assert f["rvol_event"] is None
    assert f["quality"] < 1.0


def test_kline_features_configurable_robust_rvol_and_missing_volume():
    bars = [
        {"close": 100, "volume": volume, "date_int": 20260700 + i}
        for i, volume in enumerate((10, 11, 12, 13, 14), 1)
    ] + [
        {"close": 101, "volume": None, "date_int": 20260706},
        {"close": 102, "volume": 10000, "date_int": 20260707},
        {"close": 103, "volume": 24, "date_int": 20260708},
    ]
    f = scoring.compute_kline_features(
        bars, 20260706, baseline_lookback=3, min_history=3)
    # Only the trailing 12, 13, and 14 volumes establish the baseline.
    assert f["baseline_n"] == 3
    assert f["window_n"] == 2  # None volume is explicitly excluded.
    assert f["rvol_event"] == scoring.RVOL_WINSOR_CAP
    assert f["active_days"] == 2
    assert f["rvol_mean"] < scoring.RVOL_WINSOR_CAP


def test_kline_features_invalid_volume_has_no_baseline():
    bars = [
        {"close": 100, "volume": volume, "date_int": 20260700 + i}
        for i, volume in enumerate((0, -1, "bad", None, 10), 1)
    ] + [{"close": 102, "volume": 30, "date_int": 20260706}]
    f = scoring.compute_kline_features(bars, 20260706, min_history=2)
    assert f["baseline_n"] == 1
    assert f["has_history"] is False
    assert f["rvol_event"] is None


def test_percentiles():
    assert scoring.percentiles([1, 2, 3]) == [0.0, 0.5, 1.0]
    assert scoring.percentiles([None, 5]) == [0.0, 0.0]
    assert scoring.percentiles([None, 5, 9]) == [0.0, 0.0, 1.0]


def test_composite_accounts_for_exposure_and_negative_price_action():
    direct = scoring.composite(5, 0.8, 0.8, 0.8, 1.0,
                               exposure_type="direct", confidence=1.0)
    diversified = scoring.composite(5, 0.8, 0.8, 0.8, 1.0,
                                    exposure_type="diversified", confidence=1.0)
    negative = scoring.composite(5, 0.8, 0.8, 0.8, 1.0,
                                 exposure_type="direct", confidence=1.0,
                                 neg_price_penalty=0.4)
    assert direct > diversified
    assert negative < direct


def test_calibrate_scores_are_diverse_without_forced_five():
    scores = scoring.calibrate_scores([0.9, 0.88, 0.7, 0.5, 0.49])
    assert scores[0] < 5.0
    assert len(set(scores)) == len(scores)
    assert all(scores[i] > scores[i + 1] for i in range(len(scores) - 1))
    assert all(1.0 <= s <= 5.0 for s in scores)


def test_calibrate_breaks_exact_ties():
    scores = scoring.calibrate_scores([0.8, 0.8, 0.8])
    assert len(set(scores)) == 3


def test_theme_exposure_combines_llm_signals():
    strong = scoring.theme_exposure(5, "direct", 0.9)
    weak_exposure = scoring.theme_exposure(5, "diversified", 0.9)
    low_conf = scoring.theme_exposure(5, "direct", 0.2)
    low_rel = scoring.theme_exposure(2, "direct", 0.9)
    confirmed = scoring.theme_exposure(5, "direct", 0.9, market_confirm=True)
    assert strong > weak_exposure          # exposure type matters
    assert strong > low_conf               # confidence matters
    assert strong > low_rel                # relevance matters
    assert confirmed > strong              # market confirmation lifts the score
    assert 0.0 <= strong <= 1.0


def test_validate_input_rejects_bad():
    for bad in ({"theme": "", "date": "2026-07-09", "url": "http://x"},
                {"theme": "x", "date": "07/09/2026", "url": "http://x"},
                {"theme": "x", "date": "2026-07-09", "url": "ftp://x"}):
        try:
            validate_input(bad)
            assert False
        except ValueError:
            pass


def test_limit_cli_sets_independent_stock_and_etf_caps():
    for limit in (100, 1000):
        args = build_parser().parse_args(["--limit", str(limit), "{}"])
        opts = workflow_options(args)
        assert opts["stock_universe"] == limit
        assert opts["etf_universe"] == limit

    # A per-class option remains an intentional, explicit override.
    args = build_parser().parse_args(
        ["--limit", "100", "--stock-universe", "25", "{}"])
    opts = workflow_options(args)
    assert opts["stock_universe"] == 25
    assert opts["etf_universe"] == 100

    for invalid in ("0", "-1"):
        try:
            _positive_int(invalid)
            assert False, f"accepted invalid limit {invalid}"
        except argparse.ArgumentTypeError:
            pass


def _fake_relevance(code):
    """Deterministic mock relevance: memory leaders high, even-indexed names pass,
    odd-indexed names fail. Enough names clear 3.3 to fill the 8/5 targets."""
    if code.endswith("NVDA"):
        return 5.0
    if code.endswith("MU") or code.endswith("SMH") or code.endswith("SOXX"):
        return 4.0
    tail = "".join(ch for ch in code if ch.isdigit())
    if tail and int(tail) % 2 == 0:
        return 4.0
    return 1.0


class FakeLLM:
    def chat_json(self, system, user, **kw):
        if "Return JSON with keys" in user:
            return {"summary": "s", "thesis": "t", "direct_beneficiaries": ["memory"],
                    "picks_and_shovels": [], "second_order": [], "false_positives": [], "keywords": ["memory"]}
        if "Score each candidate" in user:
            out = []
            for line in user.splitlines():
                if "|" in line and ":" in line.split("|")[0]:
                    code = line.split("|")[0].strip()
                    out.append({"market_code": code, "ai_relevance": _fake_relevance(code),
                                "exposure_type": "direct", "confidence": 0.9, "reason": "r"})
            return out
        if "FAQ" in user or "faq" in system.lower():
            return [{"question": f"Q{i}", "answer": f"A{i}"} for i in range(5)]
        return []


class FakeQuotes:
    class cfg: scene = "mock"
    _POOL = 500  # synthetic ranked pool depth
    _ETF_DATA = {
        "185:SMH": {
            "name": "VanEck Semiconductor ETF", "aum": 70_800_000_000,
            "leverage": 1, "direction": "Long", "fund_niche": "Semiconductors",
            "weights": {"185:NVDA": 19.99, "185:MU": 5.17, "185:S0": 5.1,
                        "185:S2": 4.9, "185:S4": 4.6, "185:S6": 4.5, "185:S8": 4.0},
        },
        "185:SOXX": {
            "name": "iShares Semiconductor ETF", "aum": 44_900_000_000,
            "leverage": 1, "direction": "Long", "fund_niche": "Semiconductors",
            "weights": {"185:NVDA": 8.13, "185:MU": 8.21, "185:S0": 5.4,
                        "185:S2": 4.9, "185:S4": 4.4, "185:S6": 4.2},
        },
        "171:DRAM": {
            "name": "Memory Technology ETF", "aum": 24_800_000_000,
            "leverage": 1, "direction": "Long", "fund_niche": "Semiconductors",
            "weights": {"185:MU": 8.0, "185:S0": 7.0, "185:S2": 6.0,
                        "185:S4": 5.0, "185:S6": 4.0},
        },
        "185:HBMX": {
            "name": "HBM Memory Supply Chain ETF", "aum": 36_000_000,
            "leverage": 1, "direction": "Long", "fund_niche": "Semiconductors",
            "weights": {"185:MU": 8.43, "185:S0": 6.0, "185:S2": 5.0,
                        "185:S4": 4.5, "185:S6": 4.3},
        },
        "185:AIQ": {
            "name": "Global X Artificial Intelligence ETF", "aum": 7_000_000_000,
            "leverage": 1, "direction": "Long", "fund_niche": "Artificial Intelligence",
            "weights": {"185:NVDA": 4.0, "185:MU": 6.4, "185:S0": 5.6,
                        "185:S2": 3.0},
        },
        "185:QQQ": {
            "name": "Invesco QQQ Trust", "aum": 300_000_000_000,
            "leverage": 1, "direction": "Long", "fund_niche": "Large Cap Growth",
            "weights": {"185:NVDA": 8.0, "185:MU": 0.8, "185:S0": 1.0},
        },
        "169:NVDY": {
            "name": "YieldMax NVDA Option Income Strategy ETF", "aum": 1_400_000_000,
            "leverage": 1, "direction": "Long", "benchmark": "185:NVDA",
            "weights": {},
        },
        "169:FLSP": {
            "name": "Franklin Systematic Style Premia ETF", "aum": 1_000_000_000,
            "leverage": 1, "direction": "Long", "fund_strategy": "Long/Short",
            "weights": {"185:MU": 29.0, "185:S0": 26.0},
        },
        "185:NVDL": {
            "name": "GraniteShares 2x Long NVDA", "aum": 5_000_000_000,
            "leverage": 2, "direction": "Long", "benchmark": "185:NVDA",
            "weights": {"185:NVDA": 200.0},
        },
        "185:SOXS": {
            "name": "Direxion Semiconductor Bear 3x", "aum": 2_000_000_000,
            "leverage": 3, "direction": "Short", "weights": {"185:NVDA": 10.0},
        },
    }
    def iter_ranked(self, selector, indicators, *, sort_pos=0, order="desc", page_size=1000):
        is_stock = selector.get("value") == ["C191"]
        pre = "185:S" if is_stock else "185:E"
        specials = ["185:NVDA", "185:MU"] if is_stock else ["185:SMH", "185:SOXX"]
        codes = specials + [f"{pre}{i}" for i in range(self._POOL)]
        for i, c in enumerate(codes):
            yield {"code": c, "rank": i + 1,
                   "values": {indicators[0]["req_unique_id"]: 1e12 - i}}
    def rank_universe(self, selector, indicators, *, total, sort_pos=0, **kw):
        import itertools
        return list(itertools.islice(
            self.iter_ranked(selector, indicators, sort_pos=sort_pos), total))
    def security_codes(self, security_type):
        return []  # mocked market: no offline expansion beyond the ranked pool
    def name_of(self, code): return code.split(":")[-1] + " Inc"
    def iter_prompt_etfs(self, prompt_id, *, page_size=100):
        if prompt_id == "6908afc3069a48065f159368":
            codes = ["185:SMH", "185:SOXX", "171:DRAM", "185:HBMX", "185:SOXS"]
        elif prompt_id == "6908b19b8738843bb3ba8657":
            codes = ["185:AIQ", "185:QQQ", "169:NVDY"]
        else:
            codes = []
        for rank, code in enumerate(codes, 1):
            data = self._ETF_DATA[code]
            yield {"code": code, "rank": rank, **{k: data.get(k) for k in
                   ("name", "aum", "leverage", "direction")}}
    def iter_related_etfs(self, stock_code, *, page_size=100):
        rows = []
        for code, data in self._ETF_DATA.items():
            weight = data.get("weights", {}).get(stock_code)
            if weight is not None:
                rows.append((weight, code, data))
        rows.sort(key=lambda item: (-item[0], item[1]))
        for rank, (weight, code, data) in enumerate(rows, 1):
            yield {"code": code, "rank": rank, "holding_weight": weight,
                   **{k: data.get(k) for k in ("name", "aum", "leverage", "direction")}}
    def etf_metadata(self, codes):
        return {code: {k: v for k, v in self._ETF_DATA[code].items() if k != "weights"}
                for code in codes if code in self._ETF_DATA}
    def etf_holding_weights_for_stock(self, codes, stock_code):
        return {code: self._ETF_DATA.get(code, {}).get("weights", {}).get(stock_code)
                for code in codes}
    def fetch_klines(self, codes, *, count=60, **kw):
        out = {}
        for j, c in enumerate(codes):
            bars = []
            for k in range(90):
                # 60 pre-event bars, then 30 event-window bars
                di = 20260601 + k
                if k < 60: date_int = di
                else: date_int = 20260701 + (k-60)
                bars.append({"t": k, "close": 100+k+j, "volume": 1000+j if k < 60 else 2500+j, "date_int": date_int})
            out[c] = bars
        return out


class ScriptedLLM:
    """Relevance LLM whose score per code is looked up from a dict (default 1.0)."""
    def __init__(self, rel_by_code):
        self.rel_by_code = rel_by_code
        self.scored_codes = []
    def chat_json(self, system, user, **kw):
        if "Return JSON with keys" in user:
            return {"summary": "s", "thesis": "t", "direct_beneficiaries": [],
                    "picks_and_shovels": [], "second_order": [], "false_positives": [], "keywords": []}
        if "Score each candidate" in user:
            out = []
            for line in user.splitlines():
                if "|" in line and ":" in line.split("|")[0]:
                    code = line.split("|")[0].strip()
                    self.scored_codes.append(code)
                    out.append({"market_code": code,
                                "ai_relevance": self.rel_by_code.get(code, 1.0),
                                "exposure_type": "direct", "confidence": 0.9, "reason": "r"})
            return out
        return []


def _rows(*codes):
    return [{"code": c, "name": c.split(":")[-1], "rank": i + 1}
            for i, c in enumerate(codes)]


def test_screen_stops_at_target_in_market_cap_order():
    # Threshold 3.3: 3.0 fails, 3.3 and up pass. Membership follows market-cap order.
    rel = {"169:A": 5.0, "169:B": 3.0, "169:C": 3.3, "169:D": 4.0, "169:E": 3.9}
    llm = ScriptedLLM(rel)
    wf = ThemeWorkflow(llm, FakeQuotes(), {"relevance_batch": 10, "relevance_threshold": 3.3})
    rows = _rows("169:A", "169:B", "169:C", "169:D", "169:E")
    chosen = wf.screen_until_target(rows, {}, {}, target=3, kind="stocks")
    assert [c["code"] for c in chosen] == ["169:A", "169:C", "169:D"]  # B (3.0) excluded
    assert all(c["ai_relevance"] >= 3.3 for c in chosen)


def test_screen_excludes_momentum_movers_below_threshold():
    # FMX/GGB style: unrelated names never enter output regardless of order.
    rel = {"169:FMX": 1.0, "169:GGB": 3.0, "185:MU": 4.5}
    llm = ScriptedLLM(rel)
    wf = ThemeWorkflow(llm, FakeQuotes(), {"relevance_batch": 10, "relevance_threshold": 3.3})
    rows = _rows("169:FMX", "169:GGB", "185:MU")
    chosen = wf.screen_until_target(rows, {}, {}, target=8, kind="stocks")
    assert [c["code"] for c in chosen] == ["185:MU"]


def test_screen_scans_deeper_until_target_met():
    # Only the deepest name qualifies; the scan must page past earlier batches.
    rel = {"169:Z": 4.0}
    llm = ScriptedLLM(rel)
    wf = ThemeWorkflow(llm, FakeQuotes(), {"relevance_batch": 2, "relevance_threshold": 3.3})
    rows = _rows("169:A", "169:B", "169:C", "169:D", "169:Z")
    chosen = wf.screen_until_target(rows, {}, {}, target=1, kind="stocks")
    assert [c["code"] for c in chosen] == ["169:Z"]
    assert len(llm.scored_codes) == 5  # scanned the whole universe to find it


def test_screen_stops_early_and_bounds_llm_calls():
    rel = {c: 4.0 for c in [f"185:S{i}" for i in range(50)]}
    llm = ScriptedLLM(rel)
    wf = ThemeWorkflow(llm, FakeQuotes(), {"relevance_batch": 20, "relevance_threshold": 3.3})
    rows = _rows(*[f"185:S{i}" for i in range(50)])
    chosen = wf.screen_until_target(rows, {}, {}, target=8, kind="stocks")
    assert len(chosen) == 8
    assert len(llm.scored_codes) <= 20  # stopped after the first batch


def test_screen_limit_is_an_exact_per_class_ceiling():
    # With no qualifying rows, the scan must consume exactly N candidates and stop.
    for limit in (100, 1000):
        for kind in ("stocks", "ETFs"):
            llm = ScriptedLLM({})
            wf = ThemeWorkflow(
                llm, FakeQuotes(),
                {"relevance_batch": 100, "relevance_threshold": 2.5},
            )
            rows = _rows(*[f"185:{kind[0]}{i}" for i in range(limit + 25)])
            chosen = wf.screen_until_target(
                rows, {}, {}, target=8, kind=kind, cap=limit)
            assert chosen == []
            assert len(llm.scored_codes) == limit


def test_screen_stops_at_max_scan_and_returns_partial():
    # No candidate qualifies; the safety cap must halt the scan and return what it has.
    llm = ScriptedLLM({})  # everything defaults to relevance 1.0
    wf = ThemeWorkflow(llm, FakeQuotes(),
                       {"relevance_batch": 20, "relevance_threshold": 2.5, "max_scan": 40})
    rows = _rows(*[f"185:S{i}" for i in range(500)])
    chosen = wf.screen_until_target(rows, {}, {}, target=8, kind="stocks")
    assert chosen == []
    assert len(llm.scored_codes) == 40  # bounded by max_scan, not the 500-name universe


def test_screen_max_scan_unbounded_when_zero():
    llm = ScriptedLLM({"185:S9": 4.0})  # only the deepest name qualifies
    wf = ThemeWorkflow(llm, FakeQuotes(),
                       {"relevance_batch": 5, "relevance_threshold": 2.5, "max_scan": 0})
    rows = _rows(*[f"185:S{i}" for i in range(10)])
    chosen = wf.screen_until_target(rows, {}, {}, target=1, kind="stocks")
    assert [c["code"] for c in chosen] == ["185:S9"]
    assert len(llm.scored_codes) == 10  # 0 disables the cap → scans until found


def test_relevance_prompt_contains_article_context():
    class PromptTrackingLLM(FakeLLM):
        def __init__(self): self.user = ""
        def chat_json(self, system, user, **kw):
            self.user = user
            return super().chat_json(system, user, **kw)
    llm = PromptTrackingLLM()
    wf = ThemeWorkflow(llm, FakeQuotes())
    wf.score_relevance({"thesis": "HBM demand"},
                       [{"code": "185:MU", "name": "Micron", "rvol_event": 1,
                         "chg_pct": 1, "abnormal_return": 1}],
                       {"title": "HBM catalyst", "url": "https://article", "text": "Specific HBM evidence"})
    assert "Specific HBM evidence" in llm.user
    assert "primary reasoning material" in llm.user


def test_score_relevance_handles_wrapped_json_and_code_variants():
    """The LLM sometimes wraps rows in an object or echoes codes with different
    whitespace/case; both must still resolve, not silently become relevance 1."""
    class WrappingLLM:
        def chat_json(self, system, user, **kw):
            return {"results": [
                {"market_code": " 185:mu ", "ai_relevance": 5,
                 "exposure_type": "direct", "confidence": 0.9, "reason": "HBM"},
            ]}
    wf = ThemeWorkflow(WrappingLLM(), FakeQuotes())
    scores = wf.score_relevance({"thesis": "HBM"},
                                [{"code": "185:MU", "name": "Micron", "rvol_event": 1,
                                  "chg_pct": 1, "abnormal_return": 1}])
    assert scores["185:MU"]["ai_relevance"] == 5
    assert scores["185:MU"]["relevance_status"] == "scored"


def test_score_relevance_empty_response_marks_missing():
    class EmptyLLM:
        def chat_json(self, system, user, **kw):
            return []
    wf = ThemeWorkflow(EmptyLLM(), FakeQuotes())
    rows = [{"code": f"185:X{i}", "name": f"X{i}"} for i in range(5)]
    scores = wf.score_relevance({}, rows)
    assert len(scores) == 5
    assert all(v["relevance_status"] == "missing" for v in scores.values())
    assert all(v["ai_relevance"] == 1.0 for v in scores.values())


def test_coerce_relevance_rows_variants():
    assert ThemeWorkflow._coerce_relevance_rows([{"market_code": "x"}]) == [{"market_code": "x"}]
    assert ThemeWorkflow._coerce_relevance_rows({"scores": [{"market_code": "y"}]}) == [{"market_code": "y"}]
    assert ThemeWorkflow._coerce_relevance_rows({"market_code": "z"}) == [{"market_code": "z"}]
    assert ThemeWorkflow._coerce_relevance_rows("nonsense") == []


def _narrative_records(user_prompt):
    payload = user_prompt.split("Records (JSON):\n", 1)[1]
    payload = payload.split("\n\nWriting requirements:", 1)[0]
    return json.loads(payload)


def test_stock_narrative_excludes_internal_inputs_and_includes_evidence():
    class CapturingLLM:
        def chat_json(self, system, user, **kw):
            self.system = system
            self.user = user
            return [{
                "market_code": "185:MU",
                "theme_rationale": {
                    "type": "multilingual",
                    "en": (
                        "Micron sells HBM used in AI accelerators, linking demand to memory revenue. "
                        "The key watchpoint is whether HBM growth becomes material in segment results."
                    ),
                    "zh": (
                        "美光销售用于AI加速器的HBM，其主题传导路径主要体现在存储业务收入。"
                        "关键观察点是HBM增长能否在分部业绩中形成实质性贡献。"
                    ),
                },
            }]

    llm = CapturingLLM()
    wf = ThemeWorkflow(llm, FakeQuotes())
    chosen = [{"code": "185:MU", "name": "Micron", "exposure_type": "direct",
               "ai_relevance": 5, "confidence": 1.0,
               "reason": "HBM products serve AI accelerator memory demand",
               "rvol_event": 2.4, "abnormal_return": 4.1,
               "volume_confirmed": True, "chg_pct": 3.0}]
    result = wf.narrate(
        {"theme": "AI memory"},
        {"summary": "AI infrastructure demand is expanding.", "thesis": "HBM demand grows."},
        chosen, "stock")

    (record,) = _narrative_records(llm.user)
    assert set(record) == {"market_code", "name", "exposure evidence"}
    assert record["exposure evidence"] == "HBM products serve AI accelerator memory demand"
    serialized = json.dumps(record)
    for internal in ("ai_relevance", "exposure_type", "confidence", "rvol",
                     "abnormal_return", "volume_confirmed", "chg_pct", "score"):
        assert internal not in serialized
    assert "never expose or refer to internal scores" in llm.system
    assert "relevance 5.0" in llm.system
    assert '"type": "multilingual"' in llm.user
    assert '"en"' in llm.user and '"zh"' in llm.user
    rationale = result["185:MU"]["theme_rationale"]
    assert rationale["type"] == "multilingual"
    assert rationale["en"].startswith("Micron sells HBM")
    assert rationale["zh"].startswith("美光销售")


def test_etf_narrative_uses_raw_holdings_without_aggregate_calculations():
    class CapturingLLM:
        def chat_json(self, system, user, **kw):
            self.user = user
            return [{
                "market_code": "185:SMH",
                "theme_rationale": {
                    "type": "multilingual",
                    "en": (
                        "The fund holds Micron and semiconductor-equipment suppliers tied to HBM capacity. "
                        "Its broader portfolio can dilute that connection, and holdings may change."
                    ),
                    "zh": (
                        "该基金持有美光及与HBM产能相关的半导体设备供应商。"
                        "其更广泛的投资组合可能稀释主题联系，且基金持仓可能变化。"
                    ),
                },
            }]

    llm = CapturingLLM()
    wf = ThemeWorkflow(llm, FakeQuotes())
    chosen = [{
        "code": "185:SMH", "name": "VanEck Semiconductor ETF",
        "static_theme_exposure": 0.8, "preselect_score": 0.8,
        "theme_weight_pct": 35.2, "theme_breadth": 3,
        "matched_holdings": [
            {"code": "185:MU", "ticker": "MU", "weight_pct": 8.21},
            {"code": "185:AMAT", "ticker": "AMAT", "weight_pct": 6.45},
        ],
        "pool_labels": ["Semiconductor ETFs"],
        "fund_niche": "Semiconductors",
    }]
    result = wf.narrate(
        {"theme": "AI memory"},
        {"summary": "HBM capacity is expanding.", "thesis": "Equipment demand may rise."},
        chosen, "ETF")

    (record,) = _narrative_records(llm.user)
    assert record["relevant holdings"] == [
        "MU (8.21% of the fund)", "AMAT (6.45% of the fund)"]
    assert record["fund context"] == ["Semiconductors"]
    assert record["theme categories"] == ["Semiconductor ETFs"]
    serialized = json.dumps(record)
    for internal in ("static_theme_exposure", "preselect_score", "theme_weight_pct",
                     "theme_breadth", "aggregate", "35.2"):
        assert internal not in serialized
    rationale = result["185:SMH"]["theme_rationale"]
    assert rationale["en"].startswith("The fund holds Micron")
    assert rationale["zh"].startswith("该基金持有")


def test_internal_narrative_is_rejected_and_uses_safe_stock_fallback():
    wf = ThemeWorkflow(FakeLLM(), FakeQuotes())
    chosen = [{
        "code": "185:MU", "name": "Micron", "ai_relevance": 5,
        "confidence": 0.9, "exposure_type": "direct",
        "reason": "HBM products serve AI accelerator memory demand",
    }]
    narrative = {"185:MU": {"theme_rationale": {
        "type": "multilingual",
        "en": "The model assigned relevance 5.0 because ai_relevance is high.",
        "zh": "内部评分显示相关性5.0，因此该股票值得关注。",
    }}}
    (item,) = wf._assemble(chosen, narrative, "2026-07-09")
    rationale = item["theme_rationale"]
    assert rationale["type"] == "multilingual"
    assert "relevance" not in rationale["en"].lower()
    assert "ai_relevance" not in rationale["en"]
    assert "HBM products" in rationale["en"]
    assert "内部评分" not in rationale["zh"]
    assert "现有资料" in rationale["zh"]
    assert ThemeWorkflow._validated_rationale_text(
        "The fund has combined exposure of 35.2% across the selected holdings.") is None
    assert ThemeWorkflow._validated_theme_rationale({
        "type": "multilingual",
        "en": "Micron supplies HBM, while the financial contribution remains unquantified.",
        "zh": "美光供应HBM，但内部评分为5.0。",
    }) is None
    assert ThemeWorkflow._validated_theme_rationale({
        "type": "multilingual", "en": "Micron supplies HBM.", "cn": "美光供应HBM。"
    }) is None
    assert ThemeWorkflow._validated_theme_rationale({
        "type": "multilingual", "en": "美光供应高带宽存储。", "zh": "美光供应高带宽存储。"
    }) is None


def test_malformed_narrative_response_uses_safe_fallback():
    class MalformedLLM:
        def chat_json(self, system, user, **kw):
            return {"theme_rationale": "This is not the required array."}

    wf = ThemeWorkflow(MalformedLLM(), FakeQuotes())
    chosen = [{
        "code": "185:MU", "name": "Micron", "ai_relevance": 5,
        "confidence": 0.9, "exposure_type": "direct",
        "reason": "HBM products serve AI accelerator memory demand",
    }]
    narrative = wf.narrate(
        {"theme": "AI memory"}, {"summary": "", "thesis": ""}, chosen, "stock")
    assert narrative == {}
    (item,) = wf._assemble(chosen, narrative, "2026-07-09")
    assert "HBM products" in item["theme_rationale"]["en"]
    assert "现有资料" in item["theme_rationale"]["zh"]
    assert "why_bullish" not in item


def test_missing_etf_narrative_fallback_omits_aggregate_math():
    wf = ThemeWorkflow(FakeLLM(), FakeQuotes())
    chosen = [{
        "code": "185:SMH", "name": "VanEck Semiconductor ETF",
        "static_theme_exposure": 0.8, "theme_weight_pct": 35.2,
        "reason": "Holds 3 selected theme stocks (35.2% total)",
        "matched_holdings": [
            {"code": "185:MU", "ticker": "MU", "weight_pct": 8.21},
            {"code": "185:AMAT", "ticker": "AMAT", "weight_pct": 6.45},
        ],
    }]
    (item,) = wf._assemble(chosen, {}, "2026-07-09")
    rationale = item["theme_rationale"]
    assert set(rationale) == {"type", "en", "zh"}
    assert rationale["type"] == "multilingual"
    assert "MU" in rationale["en"] and "AMAT" in rationale["en"]
    assert "MU" in rationale["zh"] and "AMAT" in rationale["zh"]
    assert "35.2" not in rationale["en"] and "35.2" not in rationale["zh"]
    assert "total" not in rationale["en"].lower()
    assert "why_bullish" not in item


class CountingQuotes(FakeQuotes):
    """FakeQuotes that records which codes had klines fetched."""
    def __init__(self):
        self.kline_calls = []

    def fetch_klines(self, codes, *, count=60, **kw):
        self.kline_calls.append(list(codes))
        return super().fetch_klines(codes, count=count, **kw)


def test_add_market_features_skips_names_with_features():
    q = CountingQuotes()
    wf = ThemeWorkflow(FakeLLM(), q)
    cands = [{"code": "185:MU", "name": "Micron", "chg_pct": 1.0}]  # already featured
    wf.add_market_features(cands, 20260709, 0.0)   # should be a no-op
    assert q.kline_calls == []


def test_end_to_end_mocked():
    wf = ThemeWorkflow(FakeLLM(), FakeQuotes(), {"stock_universe": 20, "etf_universe": 20})

    res = wf.run({"theme": "AI memory", "date": "2026-07-09", "url": "https://example.com/x"})
    assert len(res["ThemeStocks"]) == 8
    assert len(res["ThemeEtfs"]) == 5
    assert 4 <= len(res["ThemeFAQ"]) <= 8
    assert set(res["ThemeStocks"][0]) == {
        "market_code", "theme_rationale", "Theme exposure", "event_date"}
    for coll in ("ThemeStocks", "ThemeEtfs"):
        codes = [x["market_code"] for x in res[coll]]
        assert len(codes) == len(set(codes))
        scores = [x["Theme exposure"] for x in res[coll]]
        assert len(set(scores)) == len(scores)
        assert all(1.0 <= s <= 5.0 for s in scores)
        assert all("score" not in x and "score_components" not in x for x in res[coll])
        assert all("why_bullish" not in x for x in res[coll])
        assert all(set(x) == {"market_code", "theme_rationale", "Theme exposure", "event_date"}
                   for x in res[coll])
        assert all(x["theme_rationale"]["type"] == "multilingual" for x in res[coll])
        assert all(set(x["theme_rationale"]) == {"type", "en", "zh"} for x in res[coll])
        assert all(x["theme_rationale"]["en"] and x["theme_rationale"]["zh"] for x in res[coll])


def test_stock_source_uses_full_ranked_block_and_explicit_sorting():
    class RecordingQuotes(FakeQuotes):
        def __init__(self):
            self.calls = []
        def iter_ranked(self, selector, indicators, *, sort_pos=0, order="desc", page_size=1000):
            self.calls.append((selector, indicators, sort_pos, order))
            yield {"code": "185:X", "rank": 1,
                   "values": {indicators[0]["req_unique_id"]: 1}}

    quotes = RecordingQuotes()
    wf = ThemeWorkflow(FakeLLM(), quotes)
    list(wf.stock_source())

    (stock_call,) = quotes.calls
    assert stock_call[0] == {"type": "block_id", "value": ["C191"]}
    assert stock_call[1][0]["id"] == "total_market_value"
    assert stock_call[2:] == (0, "desc")


def test_related_etf_helper_uses_stock_prompt_and_holding_weight_sort():
    client = object.__new__(AInvestClient)
    calls = []
    client.name_of = lambda code: code

    def rows(selector, indicators, *, sort_pos=0, order="desc", page_size=1000,
             strict=False):
        calls.append((selector, indicators, sort_pos, order, page_size, strict))
        yield {"code": "185:SMH", "rank": 1,
               "values": {"holding_weight": 19.99, "name": "SMH",
                          "aum": 70_000_000_000, "leverage": 1,
                          "direction": "Long"}}

    client.iter_ranked = rows
    result = list(client.iter_related_etfs("185:NVDA", page_size=37))
    selector, indicators, sort_pos, order, page_size, strict = calls[0]
    assert selector == {
        "type": "prompt_id", "value": ["677251bbbc4823684c64145d"],
        "attr": {"market_code": "185:NVDA"},
    }
    assert indicators[0]["id"] == "国际北美etf@Holding Stock Weight(View)"
    assert indicators[0]["attr"] == {"match_code": "185:NVDA"}
    assert (sort_pos, order, page_size, strict) == (0, "desc", 37, True)
    assert result[0]["holding_weight"] == 19.99


def test_iter_ranked_stops_when_gateway_repeats_a_full_page():
    client = object.__new__(AInvestClient)
    client.cfg = type("Cfg", (), {"snapshot_url": "https://example.test/snapshot"})()
    begins = []

    def repeated_page(_url, body):
        begins.append(body["page"]["begin"])
        return {"data": {
            "indicator": [{"req_unique_id": "metric"}],
            "data": [
                {"symbol_code": "185:A", "value": [{"v": 2}]},
                {"symbol_code": "185:B", "value": [{"v": 1}]},
            ],
        }}

    client._post = repeated_page
    rows = list(client.iter_ranked(
        {"type": "block_id", "value": ["C191"]},
        [{"id": "total_market_value", "req_unique_id": "metric"}],
        page_size=2,
    ))
    assert [r["code"] for r in rows] == ["185:A", "185:B"]
    assert begins == [0, 2]


def test_screen_stops_early_without_scanning_whole_universe():
    """Once the targets are met the scan stops — it must not score all 50+50."""
    seen = {"n": 0}

    class TrackingLLM(FakeLLM):
        def chat_json(self, system, user, **kw):
            if "Score each candidate" in user:
                seen["n"] += sum(
                    1 for line in user.splitlines()
                    if "|" in line and ":" in line.split("|")[0])
            return super().chat_json(system, user)

    wf = ThemeWorkflow(TrackingLLM(), FakeQuotes(),
                       {"stock_universe": 50, "etf_universe": 50, "relevance_batch": 20})
    wf.run({"theme": "AI memory", "date": "2026-07-09", "url": "https://example.com/x"})
    assert seen["n"] <= 20               # only the first stock batch was LLM-scored


def _theme_stocks():
    return [
        {"code": "185:NVDA", "name": "NVIDIA", "ai_relevance": 5,
         "confidence": 0.95, "exposure_type": "direct"},
        {"code": "185:MU", "name": "Micron", "ai_relevance": 5,
         "confidence": 0.95, "exposure_type": "direct"},
        *[
            {"code": f"185:S{i}", "name": f"Theme Stock {i}", "ai_relevance": 4,
             "confidence": 0.9, "exposure_type": "supply_chain"}
            for i in (0, 2, 4, 6, 8, 10)
        ],
    ]


def test_static_theme_pool_routing_is_specific_and_llm_free():
    matches = match_theme_pools("AI memory", {"keywords": ["HBM", "DRAM"]}, "")
    keys = [match.spec.key for match in matches]
    assert keys[:2] == ["semiconductors", "artificial_intelligence"]
    assert "ai_robotics" not in keys
    assert "robotics" not in keys
    assert match_theme_pools("chairman said demand is strong", {}, "") == []


def test_pool_only_evidence_is_primary_only_for_direct_asset_funds():
    class PoolOnlyQuotes:
        def name_of(self, code): return code.partition(":")[2]
        def iter_related_etfs(self, stock_code, *, page_size=100):
            return iter(())
        def iter_prompt_etfs(self, prompt_id, *, page_size=100):
            if prompt_id == "6908b3d38738843bb3ba8662":
                yield {"code": "185:GLD", "name": "Gold Trust", "aum": 1e11,
                       "leverage": 1, "direction": "Long"}
            elif prompt_id == "6908b4328738843bb3ba8664":
                yield {"code": "185:BIL", "name": "Short Duration Treasury ETF", "aum": 1e11,
                       "leverage": 1, "direction": "Long"}
            elif prompt_id == "6908b19b8738843bb3ba8657":
                yield {"code": "185:GENAI", "name": "Generic AI ETF", "aum": 1e10,
                       "leverage": 1, "direction": "Long"}
        def etf_metadata(self, codes):
            return {code: {"name": self.name_of(code), "aum": 1e10,
                           "leverage": 1, "direction": "Long",
                           "asset_class": (
                               "Commodity" if code == "185:GLD" else
                               "Bonds" if code == "185:BIL" else "Equity")}
                    for code in codes}
        def etf_holding_weights_for_stock(self, codes, stock_code):
            return {code: None for code in codes}

    quotes = PoolOnlyQuotes()
    gold = preselect_etfs(
        quotes, _theme_stocks()[:1], theme="gold", brief={}, article_title="", limit=10)
    ai = preselect_etfs(
        quotes, _theme_stocks()[:1], theme="AI", brief={}, article_title="", limit=10)
    treasury = preselect_etfs(
        quotes, _theme_stocks()[:1], theme="short term treasury",
        brief={}, article_title="", limit=10)
    assert gold.candidates[0]["preselect_score"] >= 0.7
    assert ai.candidates[0]["preselect_score"] <= 0.25
    assert treasury.candidates[0]["code"] == "185:BIL"


def test_etf_preselection_ranks_holdings_before_aum_and_filters_risk():
    result = preselect_etfs(
        FakeQuotes(), _theme_stocks(), theme="AI memory",
        brief={"keywords": ["HBM", "DRAM"]}, article_title="Memory demand", limit=100,
    )
    codes = [candidate["code"] for candidate in result.candidates]
    assert codes[0] == "185:SMH"
    assert codes.index("185:SOXX") < codes.index("185:QQQ")
    assert codes.index("185:HBMX") < codes.index("185:QQQ")
    assert "185:NVDL" not in codes       # leverage=2
    assert "185:SOXS" not in codes       # Short / 3x
    assert "169:NVDY" not in codes       # single-stock option-income wrapper
    assert "169:FLSP" not in codes       # alternative long/short strategy
    smh = result.candidates[0]
    assert smh["theme_weight_pct"] > 40
    assert smh["theme_breadth"] >= 5
    assert smh["relevance_status"] == "deterministic"
    assert "NVDA" in smh["reason"]


def test_etf_preselection_global_cap_is_identical_for_100_and_1000():
    class ManyETFQuotes:
        class cfg: scene = "mock"
        def name_of(self, code): return code.partition(":")[2]
        def iter_prompt_etfs(self, prompt_id, *, page_size=100):
            return iter(())
        def iter_related_etfs(self, stock_code, *, page_size=100):
            for i in range(1200):
                yield {"code": f"185:F{i:04d}", "name": f"Fund {i}",
                       "holding_weight": max(0.01, 10 - i / 200),
                       "aum": 1_000_000_000 - i, "leverage": 1,
                       "direction": "Long"}
        def etf_metadata(self, codes):
            return {code: {"name": code.partition(":")[2], "aum": 1_000_000,
                           "leverage": 1, "direction": "Long"} for code in codes}
        def etf_holding_weights_for_stock(self, codes, stock_code):
            return {code: 2.0 for code in codes}

    one_stock = [_theme_stocks()[0]]
    for limit in (100, 1000):
        result = preselect_etfs(
            ManyETFQuotes(), one_stock, theme="novel photonics packaging",
            brief={}, article_title="", limit=limit,
        )
        assert result.discovered == limit
        assert len(result.candidates) == limit
        assert result.candidates[-1]["rank"] == limit
        assert all(candidate["leverage"] == 1 for candidate in result.candidates)


def test_etf_preselection_does_not_need_llm_etf_scores():
    scored = []
    class TrackingLLM(FakeLLM):
        def chat_json(self, system, user, **kw):
            if "Score each candidate" in user:
                scored.extend(
                    line.split("|")[0].strip() for line in user.splitlines()
                    if "|" in line and ":" in line.split("|")[0]
                )
            return super().chat_json(system, user, **kw)

    result = ThemeWorkflow(
        TrackingLLM(), FakeQuotes(), {"stock_universe": 20, "etf_universe": 100}
    ).run({"theme": "AI memory", "date": "2026-07-09", "url": "https://example.com/x"})
    assert len(result["ThemeEtfs"]) == 5
    assert scored
    assert all(code.startswith("185:S") or code in ("185:NVDA", "185:MU")
               for code in scored)
    assert not any(code in FakeQuotes._ETF_DATA for code in scored)


if __name__ == "__main__":
    fns = [v for k,v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} tests passed")
