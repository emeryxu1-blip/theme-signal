"""Unit + mocked end-to-end tests. Run: python3 tests/test_workflow.py"""

import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import scoring
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


def test_percentiles():
    assert scoring.percentiles([1, 2, 3]) == [0.0, 0.5, 1.0]
    assert scoring.percentiles([None, 5]) == [0.0, 0.0]
    assert scoring.percentiles([None, 5, 9]) == [0.0, 0.0, 1.0]


def test_calibrate_scores_are_diverse():
    scores = scoring.calibrate_scores([0.9, 0.88, 0.7, 0.5, 0.49])
    assert scores[0] == 5.0
    assert len(set(scores)) == len(scores)
    assert all(scores[i] > scores[i + 1] for i in range(len(scores) - 1))
    assert all(1.0 <= s <= 5.0 for s in scores)


def test_calibrate_breaks_exact_ties():
    scores = scoring.calibrate_scores([0.8, 0.8, 0.8])
    assert len(set(scores)) == 3


def test_validate_input_rejects_bad():
    for bad in ({"theme": "", "date": "2026-07-09", "url": "http://x"},
                {"theme": "x", "date": "07/09/2026", "url": "http://x"},
                {"theme": "x", "date": "2026-07-09", "url": "ftp://x"}):
        try:
            validate_input(bad)
            assert False
        except ValueError:
            pass


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
                    rel = 5 if code.endswith("NVDA") else 4 if code.endswith("MU") else 3
                    out.append({"market_code": code, "ai_relevance": rel, "exposure_type": "direct", "confidence": 0.9, "reason": "r"})
            return out
        if "FAQ" in user or "faq" in system.lower():
            return [{"question": f"Q{i}", "answer": f"A{i}"} for i in range(5)]
        return []


class FakeQuotes:
    class cfg: scene = "mock"
    def rank_universe(self, selector, indicators, *, total, sort_pos=0, **kw):
        pre = "185:S" if selector["type"] == "block_id" else "185:E"
        specials = ["185:NVDA", "185:MU"] if selector["type"] == "block_id" else ["185:SMH", "185:SOXX"]
        codes = (specials + [f"{pre}{i}" for i in range(total)])[:total]
        return [{"code": c, "rank": i+1, "values": {indicators[0]["req_unique_id"]: 1e12-i}} for i,c in enumerate(codes)]
    def name_of(self, code): return code.split(":")[-1] + " Inc"
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


def test_end_to_end_mocked():
    wf = ThemeWorkflow(FakeLLM(), FakeQuotes(), {"stock_universe": 20, "etf_universe": 20, "stock_shortlist": 12, "etf_shortlist": 10})
    res = wf.run({"theme": "AI memory", "date": "2026-07-09", "url": "https://example.com/x"})
    assert len(res["ThemeStocks"]) == 8
    assert len(res["ThemeEtfs"]) == 5
    assert 4 <= len(res["ThemeFAQ"]) <= 8
    assert set(res["ThemeStocks"][0]) == {"market_code", "why_bullish", "score", "score_components", "event_date"}
    for coll in ("ThemeStocks", "ThemeEtfs"):
        codes = [x["market_code"] for x in res[coll]]
        assert len(codes) == len(set(codes))
        scores = [x["score"] for x in res[coll]]
        assert len(set(scores)) == len(scores)
        assert all(1.0 <= s <= 5.0 for s in scores)


if __name__ == "__main__":
    fns = [v for k,v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn(); print(f"  ok  {fn.__name__}")
    print(f"\n{len(fns)}/{len(fns)} tests passed")
