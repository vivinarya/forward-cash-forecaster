"""Seasonality estimation: classical multiplicative index, shrunk toward 1 on thin data.

Why not a learned model (Prophet/ARIMA/transformer)? The signal we need from 180 days of a
small SMB's bank feed is two shapes: which weekdays move money, and which months are heavy
(quarter-end collections, festive advance). A shrunk multiplicative index is estimable from
that much history; a deep model is not, and it cannot be explained to a CFO. The index is
also what makes the Monte Carlo layer sane: it scales the volume distribution, not a point.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta


def flows_by_day(lines, sign: str = "both") -> dict[date, int]:
    out: dict[date, int] = defaultdict(int)
    for ln in lines:
        amt = ln.amount_paise
        if sign == "in" and amt <= 0:
            continue
        if sign == "out" and amt >= 0:
            continue
        out[ln.txn_date] += abs(amt) if sign != "both" else amt
    return dict(out)


def _shrink(index: dict[int, float], overall_mean: float, k: float = 8.0) -> dict[int, float]:
    """Normalise to mean 1.0 and shrink thin buckets toward the global level.

    n/(n+k) shrinkage matters: with 180 days of history a Sunday bucket has ~25 observations
    and a one-off festival refund can swing it by 40%. Uncorrected, that swing is then
    projected forward for every Sunday forever.
    """
    if overall_mean <= 0:
        return {key: 1.0 for key in index}
    out = {}
    for key, (mean, n) in index.items():
        raw = mean / overall_mean if overall_mean else 1.0
        w = n / (n + k)
        out[key] = 1.0 + w * (raw - 1.0)
    # renormalise so the factors average 1.0 over the observed buckets
    avg = sum(out.values()) / max(1, len(out))
    return {key: round(v / avg, 4) if avg else 1.0 for key, v in out.items()}


def weekday_index(day_flows: dict[date, int]) -> dict[int, float]:
    buckets: dict[int, list[int]] = defaultdict(list)
    for d, v in day_flows.items():
        buckets[d.weekday()].append(abs(v))
    total = [abs(v) for v in day_flows.values()]
    overall = sum(total) / max(1, len(total)) if total else 0.0
    idx = {wd: (sum(vs) / len(vs), len(vs)) for wd, vs in buckets.items()}
    for wd in range(7):
        idx.setdefault(wd, (overall, 0.0))
    return _shrink(idx, overall)


def month_index(day_flows: dict[date, int]) -> dict[int, float]:
    buckets: dict[int, list[int]] = defaultdict(list)
    for d, v in day_flows.items():
        buckets[d.month].append(abs(v))
    total = [abs(v) for v in day_flows.values()]
    overall = sum(total) / max(1, len(total)) if total else 0.0
    idx = {m: (sum(vs) / len(vs), len(vs)) for m, vs in buckets.items()}
    for m in range(1, 13):
        idx.setdefault(m, (overall, 0.0))
    return _shrink(idx, overall)


def quarter_end_boost(day: date) -> float:
    """Collections spike in the last week of an Indian financial-quarter month."""
    if day.month in (3, 6, 9, 12) and day.day >= 25:
        return 1.15
    return 1.0


def seasonality_factor(day: date, wd: dict[int, float], mo: dict[int, float]) -> float:
    return wd.get(day.weekday(), 1.0) * mo.get(day.month, 1.0) * quarter_end_boost(day)


def add_business_days(d: date, n: int, skip: set[int] = frozenset({6})) -> date:  # Sunday closed
    while n > 0:
        d += timedelta(days=1)
        while d.weekday() in skip:
            d += timedelta(days=1)
        n -= 1
    return d


def summary(day_flows: dict[date, int]) -> dict[str, object]:
    wd, mo = weekday_index(day_flows), month_index(day_flows)
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    return {
        "weekday_index": {names[k]: v for k, v in sorted(wd.items())},
        "month_index": {k: v for k, v in sorted(mo.items())},
        "observations": len(day_flows),
    }
