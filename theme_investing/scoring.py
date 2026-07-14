"""Feature normalization, composite scoring, and score calibration."""

from __future__ import annotations

from datetime import date

# composite weights (see README): relevance-led, with market confirmation
W_AI = 0.45     # LLM semantic exposure to the theme
W_RVOL = 0.25   # event-window relative volume (participation spike)
W_ABR = 0.20    # abnormal return vs benchmark (theme-specific move)
W_CHG = 0.10    # raw event->today price change (direction/magnitude)


def event_date_int(iso_date: str) -> int:
    y, m, d = iso_date.split("-")
    return int(f"{y}{m}{d}")


def _median(xs: list) -> float | None:
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    return xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2


def window_return(bars: list[dict], ev_int: int) -> float | None:
    """Percentage return from the event bar close to the latest close."""
    if not bars:
        return None
    latest = bars[-1]
    ev = next((b for b in bars if (b.get("date_int") or 0) >= ev_int), None)
    if ev and ev.get("close") and latest.get("close"):
        return (latest["close"] / ev["close"] - 1.0) * 100.0
    return None


def compute_kline_features(bars: list[dict], ev_int: int,
                           benchmark_return: float | None = None) -> dict:
    """Event-study features from daily bars (oldest→newest):

    - ``chg_pct``: event close -> latest close return %.
    - ``rvol_event``: PEAK daily volume in [event, latest] / median of ~20 days
      of *pre-event* volume. Captures the participation spike the event caused,
      not a decayed latest-bar reading against a spike-contaminated window.
    - ``rvol_mean`` / ``active_days``: mean event-window RVOL and count of days
      trading above their pre-event norm (persistence).
    - ``abnormal_return``: ``chg_pct`` minus the benchmark's window return —
      isolates the theme-specific move from broad-market drift.
    - ``has_history``: whether a pre-event baseline could be formed (new listings
      cannot, so they can't be volume-confirmed).
    """
    out = {"chg_pct": None, "rvol_event": None, "rvol_mean": None, "active_days": 0,
           "abnormal_return": None, "event_close": None, "latest_close": None,
           "latest_volume": None, "has_history": False, "quality": 1.0}
    if not bars:
        out["quality"] = 0.0
        return out
    latest = bars[-1]
    out["latest_close"] = latest.get("close")
    out["latest_volume"] = latest.get("volume")

    window = [b for b in bars if (b.get("date_int") or 0) >= ev_int]
    pre_vol = [b.get("volume") for b in bars
               if (b.get("date_int") or 0) < ev_int and b.get("volume")]
    baseline = _median(pre_vol[-20:]) if len(pre_vol) >= 5 else None
    out["has_history"] = baseline is not None

    # event close & change %
    event_bar = window[0] if window else None
    if event_bar and event_bar.get("close"):
        out["event_close"] = event_bar["close"]
        if latest.get("close"):
            out["chg_pct"] = (latest["close"] / event_bar["close"] - 1.0) * 100.0
    else:
        out["quality"] *= 0.5  # event bar missing (future/holiday/no data)

    # event-window relative volume vs pre-event baseline
    if baseline:
        rvols = [b["volume"] / baseline for b in window if b.get("volume")]
        if rvols:
            out["rvol_event"] = max(rvols)
            out["rvol_mean"] = sum(rvols) / len(rvols)
            out["active_days"] = sum(1 for r in rvols if r > 1.0)
    else:
        out["quality"] *= 0.6  # cannot confirm participation without history

    # abnormal return vs benchmark (theme-specific excess move)
    if out["chg_pct"] is not None and benchmark_return is not None:
        out["abnormal_return"] = out["chg_pct"] - benchmark_return
    return out


def percentiles(values: list) -> list:
    """Rank-based percentile in [0,1]; None values map to 0.0."""
    idx = [i for i, v in enumerate(values) if v is not None]
    out = [0.0] * len(values)
    if not idx:
        return out
    order = sorted(idx, key=lambda i: values[i])
    denom = max(len(order) - 1, 1)
    for rank, i in enumerate(order):
        out[i] = rank / denom
    return out


def composite(ai_relevance, rvol_pct, abret_pct, chg_pct_pct, quality) -> float:
    ai_norm = (max(1, min(5, ai_relevance)) - 1) / 4.0
    raw = (W_AI * ai_norm + W_RVOL * rvol_pct
           + W_ABR * abret_pct + W_CHG * chg_pct_pct)
    return raw * (0.6 + 0.4 * quality)  # data-quality penalty, capped at 40% of score


def calibrate_scores(sorted_composites: list[float], *, top: float = 5.0,
                     min_gap: float = 0.1, max_gap: float = 0.5) -> list[float]:
    """Map an ordered (desc) list of composites to differentiated display scores.

    The best candidate gets ``top`` (5.0); each subsequent score steps down by a
    gap that grows with the composite drop to its predecessor, bounded to
    ``[min_gap, max_gap]``. This guarantees strictly decreasing, distinct scores
    (min 0.1 apart after rounding) while staying within [1, 5] for up to ~9 names
    — so a set never collapses to eight identical 4.9s.
    """
    n = len(sorted_composites)
    if n == 0:
        return []
    if n == 1:
        return [round(top, 1)]
    diffs = [sorted_composites[i - 1] - sorted_composites[i] for i in range(1, n)]
    max_diff = max(diffs) if diffs else 0.0
    scores = [top]
    for d in diffs:
        frac = (d / max_diff) if max_diff > 1e-9 else 0.0
        gap = min_gap + (max_gap - min_gap) * frac
        scores.append(scores[-1] - gap)
    return [round(max(1.0, min(5.0, s)), 1) for s in scores]
