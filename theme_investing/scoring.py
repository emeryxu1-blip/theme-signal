"""Feature normalization, composite scoring, and score calibration."""

from __future__ import annotations

from datetime import date

# ---------------------------------------------------------------------------
# Composite weights (relevance-led, with market confirmation).
# Changing these requires updating the README and test assertions that depend
# on specific numeric outcomes.
# ---------------------------------------------------------------------------
W_AI = 0.45     # LLM semantic exposure to the theme
W_RVOL = 0.25   # event-window relative volume (participation spike)
W_ABR = 0.20    # abnormal return vs benchmark (theme-specific move)
W_CHG = 0.10    # raw event->today price change (direction/magnitude)

# ---------------------------------------------------------------------------
# Exposure-type multipliers applied to the AI-relevance weight component.
# "direct" pure-plays get full weight; diversified/unclear conglomerates get
# a discount so a high LLM score for an off-theme name has less impact.
# ---------------------------------------------------------------------------
EXPOSURE_WEIGHT = {
    "direct":       1.00,
    "enabler":      0.90,
    "supply_chain": 0.80,
    "beneficiary":  0.70,
    "diversified":  0.50,
    "unclear":      0.40,
}

# Default RVOL computation parameters (can be overridden via compute_kline_features).
DEFAULT_BASELINE_LOOKBACK: int = 20   # max number of pre-event bars for the baseline
DEFAULT_MIN_HISTORY: int = 5          # minimum pre-event bars required to form a baseline
RVOL_WINSOR_CAP: float = 10.0        # cap individual bar RVOL at this multiple before aggregating


def event_date_int(iso_date: str) -> int:
    y, m, d = iso_date.split("-")
    return int(f"{y}{m}{d}")


def _valid_volume(v) -> float | None:
    """Return v if it is a positive finite number, otherwise None."""
    try:
        f = float(v)
        return f if f > 0 else None
    except (TypeError, ValueError):
        return None


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


def compute_kline_features(
    bars: list[dict],
    ev_int: int,
    benchmark_return: float | None = None,
    *,
    baseline_lookback: int = DEFAULT_BASELINE_LOOKBACK,
    min_history: int = DEFAULT_MIN_HISTORY,
) -> dict:
    """Event-study features from daily bars (oldest→newest).

    Returns a dict with keys:

    - ``chg_pct``: event close -> latest close return %.
    - ``rvol_event``: PEAK winsorized daily volume in [event, latest] /
      pre-event baseline. Captures the participation spike caused by the event,
      not a decayed latest-bar reading against a spike-contaminated window.
      Individual bar RVOLs are capped at ``RVOL_WINSOR_CAP`` before taking the
      max so a single outlier print cannot inflate the headline figure.
    - ``rvol_mean``: mean winsorized event-window RVOL (persistence measure).
    - ``active_days``: count of event-window days trading above the baseline.
    - ``abnormal_return``: ``chg_pct`` minus the benchmark's window return.
    - ``has_history``: whether a stable pre-event baseline was formed.
    - ``quality``: [0, 1] data-quality score; lower means more imputation.
    - ``baseline_n``: number of pre-event bars actually used for the baseline.
    - ``window_n``: number of event-window bars with valid volume.

    Parameters
    ----------
    bars:
        Daily OHLCV dicts, oldest first.  Must contain ``date_int`` (int,
        YYYYMMDD), ``close`` (number), and ``volume`` (number).
    ev_int:
        Event date as an integer YYYYMMDD.
    benchmark_return:
        Benchmark window return (%), for abnormal-return calculation.
    baseline_lookback:
        Maximum number of pre-event bars to use when computing the baseline.
    min_history:
        Minimum pre-event bars required to declare ``has_history = True``.
    """
    out = {
        "chg_pct": None, "rvol_event": None, "rvol_mean": None, "active_days": 0,
        "abnormal_return": None, "event_close": None, "latest_close": None,
        "latest_volume": None, "has_history": False, "quality": 1.0,
        "baseline_n": 0, "window_n": 0,
    }
    if not bars:
        out["quality"] = 0.0
        return out

    latest = bars[-1]
    out["latest_close"] = latest.get("close")
    out["latest_volume"] = _valid_volume(latest.get("volume"))

    # Split into pre-event and event-window bars.
    window = [b for b in bars if (b.get("date_int") or 0) >= ev_int]
    pre = [b for b in bars if (b.get("date_int") or 0) < ev_int]

    # Build the pre-event volume baseline using only the most recent bars up to
    # baseline_lookback, filtering out non-positive or non-numeric values.
    pre_vols = [_valid_volume(b.get("volume")) for b in pre[-baseline_lookback:]]
    pre_vols = [v for v in pre_vols if v is not None]
    out["baseline_n"] = len(pre_vols)
    baseline = _median(pre_vols) if len(pre_vols) >= min_history else None
    out["has_history"] = baseline is not None

    # Event bar close & price change.
    event_bar = window[0] if window else None
    if event_bar and event_bar.get("close"):
        out["event_close"] = event_bar["close"]
        if latest.get("close"):
            out["chg_pct"] = (latest["close"] / event_bar["close"] - 1.0) * 100.0
    else:
        out["quality"] *= 0.5  # event bar missing (future/holiday/no data)

    # Robust event-window RVOL vs pre-event baseline.
    if baseline:
        raw_rvols = [
            _valid_volume(b.get("volume")) / baseline
            for b in window
            if _valid_volume(b.get("volume")) is not None
        ]
        out["window_n"] = len(raw_rvols)
        if raw_rvols:
            # Winsorise individual bar RVOL before aggregating so a single
            # extreme print doesn't inflate the headline figure.
            capped = [min(r, RVOL_WINSOR_CAP) for r in raw_rvols]
            out["rvol_event"] = max(capped)
            out["rvol_mean"] = sum(capped) / len(capped)
            out["active_days"] = sum(1 for r in raw_rvols if r > 1.0)
    else:
        out["quality"] *= 0.6  # cannot confirm participation without history

    # Abnormal return vs benchmark.
    if out["chg_pct"] is not None and benchmark_return is not None:
        out["abnormal_return"] = out["chg_pct"] - benchmark_return
    return out


def percentiles(values: list) -> list:
    """Rank-based percentile in [0, 1].

    Missing (None) values are kept at 0.0 to indicate unavailability, which is
    weaker than any real observation.  When *all* values are missing or there is
    only one distinct value, the result is all zeros.
    """
    idx = [i for i, v in enumerate(values) if v is not None]
    out = [0.0] * len(values)
    if not idx:
        return out
    order = sorted(idx, key=lambda i: values[i])
    denom = max(len(order) - 1, 1)
    for rank, i in enumerate(order):
        out[i] = rank / denom
    return out


def composite(
    ai_relevance: int,
    rvol_pct: float,
    abret_pct: float,
    chg_pct_pct: float,
    quality: float,
    *,
    exposure_type: str = "unclear",
    confidence: float = 0.5,
    neg_price_penalty: float = 0.0,
) -> float:
    """Compute a [0, 1) composite score blending AI relevance with market signals.

    Parameters
    ----------
    ai_relevance:
        LLM integer rating 1–5 (clamped internally).
    rvol_pct, abret_pct, chg_pct_pct:
        Cross-sectional percentile ranks in [0, 1] for event-window peak RVOL,
        abnormal return, and raw price change respectively.
    quality:
        Data-quality scalar in [0, 1] from ``compute_kline_features``.
    exposure_type:
        One of the keys in ``EXPOSURE_WEIGHT``; scales the AI-relevance component.
    confidence:
        LLM confidence in [0, 1]; downscales the AI-relevance component slightly
        when the model is uncertain.
    neg_price_penalty:
        Extra penalty in [0, 1) to apply when price action is unambiguously
        negative (e.g. the stock fell into a clearly declining event window).
        Caller is responsible for computing this; defaults to no penalty.
    """
    ai_norm = (max(1, min(5, ai_relevance)) - 1) / 4.0
    # Scale AI weight by exposure quality and LLM confidence.
    exposure_scale = EXPOSURE_WEIGHT.get(exposure_type, EXPOSURE_WEIGHT["unclear"])
    ai_component = W_AI * ai_norm * exposure_scale * max(0.5, min(1.0, confidence))
    market_component = W_RVOL * rvol_pct + W_ABR * abret_pct + W_CHG * chg_pct_pct
    raw = ai_component + market_component
    # Apply data-quality penalty (capped: a name with poor data can score at most
    # 60% of its raw composite).
    quality_adj = raw * (0.6 + 0.4 * quality)
    # Apply negative price-action penalty after quality adjustment.
    return quality_adj * (1.0 - max(0.0, min(0.5, neg_price_penalty)))


def calibrate_scores(
    sorted_composites: list[float],
    *,
    min_gap: float = 0.1,
    max_gap: float = 0.5,
    absolute_ceiling: float = 5.0,
    quality_floor: float = 1.0,
    top_cap: float = 4.8,
) -> list[float]:
    """Map a descending list of composite scores to differentiated display scores.

    Unlike the previous implementation this function does NOT blindly assign 5.0
    (or any fixed value) to the top candidate.  Its initial display score derives
    from the absolute best composite, then is capped at ``top_cap`` (default 4.8)
    to reserve 5.0 for an explicit future override backed by stronger evidence.
    Subsequent gaps scale proportionally to the composite spread.

    Guarantees:
    - Strictly decreasing (each step ≥ ``min_gap`` after rounding).
    - All values in [``quality_floor``, ``absolute_ceiling``].
    - The top value never exceeds ``top_cap``.
    - Exactly ``len(sorted_composites)`` values returned.

    Parameters
    ----------
    sorted_composites:
        Composite values in descending order (already sorted by caller).
    min_gap:
        Minimum display-score step between consecutive candidates.
    max_gap:
        Maximum display-score step between consecutive candidates.
    absolute_ceiling:
        Hard upper bound for any display score (5.0).
    quality_floor:
        Hard lower bound for any display score (1.0).
    top_cap:
        The top candidate receives at most this score.  Set to 5.0 if you want
        the old forced-top behaviour.
    """
    n = len(sorted_composites)
    if n == 0:
        return []
    if n == 1:
        return [round(min(top_cap, absolute_ceiling), 1)]

    diffs = [sorted_composites[i - 1] - sorted_composites[i] for i in range(1, n)]
    max_diff = max(diffs) if diffs else 0.0

    top = min(top_cap, absolute_ceiling)
    scores = [top]
    for d in diffs:
        frac = (d / max_diff) if max_diff > 1e-9 else 0.0
        gap = min_gap + (max_gap - min_gap) * frac
        scores.append(scores[-1] - gap)

    return [round(max(quality_floor, min(absolute_ceiling, s)), 1) for s in scores]
