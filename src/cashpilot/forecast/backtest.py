"""Forecast accuracy: rolling-origin backtest against both a naive baseline and the seeded future.

Two independent measurements, because each one alone can be argued with:

1. **Rolling-origin on history** (`cashpilot bench --forecast`): for several origins inside the
   180 days of history, rebuild the ledger exactly as it looked on that date - documents not yet
   raised are removed, documents cleared by bank lines up to that date are marked paid, delay
   distributions are re-learned from only that much history - forecast 7/14/30 days, and compare to
   what the bank feed actually shows happened. Same procedure a quant desk would use, and it is the
   only measurement here that never touches the generator's private plan.

2. **Seeded future** (against `truth_future_cash.csv`): the generator recorded what it *intended*
   to happen after the as-of date. This is a fair test of the model, not of data leakage, because the
   forecaster's inputs are truncated at as_of - but it does share the generator's assumptions, so it
   is reported as a secondary number and labelled that way in the README.

Baselines are the point. A forecaster that beats nothing is decoration.
  * `seasonal_naive`  : last week's same weekday repeats (standard, hard to beat on short series)
  * `due_date_sum`    : "cash = today + (AR due - AP due)", what a spreadsheet does today
  * `cashpilot`       : this model
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from ..config import Settings
from ..ingest import Dataset
from ..models import LedgerDoc
from ..recon.engine import Reconciler
from .engine import forecast, learn_ledger


@dataclass
class BacktestResult:
    origins: list[str] = field(default_factory=list)
    metrics: dict[str, float] = field(default_factory=dict)
    per_origin: list[dict[str, object]] = field(default_factory=list)
    horizons: list[int] = field(default_factory=list)
    coverage: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "origins": self.origins,
            "n_origins": len(self.origins),
            "metrics": self.metrics,
            "per_origin": self.per_origin,
            "band_coverage_pct": self.coverage,
            "notes": self.notes,
            "horizons": self.horizons,
        }


def _slice_dataset(ds: Dataset, as_of: date) -> Dataset:
    """Rebuild the world as it looked on `as_of`. This is the whole point of the exercise: any
    implementation that peeks one day forward produces a beautiful, worthless accuracy number.

    The status column in the CSVs describes *today*, not the origin date, so it is reset here:
    every document raised by the origin is treated as unposted (that is the state that needs
    reconciling), and the origin's own reconciliation run is what decides which of them were
    actually settled by then. Without this reset the backtest "leaks" the answer for every payment
    made between the origin and today - it showed up as a 4x under-call of outflows, and it is the
    reason the naive baseline initially looked impossibly good. See docs/FAILURES.md #5.
    """
    from dataclasses import replace

    lines = [ln for ln in ds.lines if ln.txn_date <= as_of]
    docs = [
        replace(d, status="open" if d.due_date > as_of else "overdue", paid_amount_paise=0)
        for d in ds.docs
        if d.doc_date <= as_of
    ]
    return Dataset(
        lines=lines,
        invoices=[d for d in docs if d.kind == "AR"],
        bills=[d for d in docs if d.kind == "AP"],
        advices=[a for a in ds.advices if a.notified_on <= as_of],
        settlements=[s for s in ds.settlements if s.settled_on <= as_of],
        payments=[p for p in ds.payments if p.captured_on <= as_of],
        refunds=[r for r in ds.refunds if getattr(r, "created_on", as_of) <= as_of],
        opening_balance_paise=ds.opening_balance_paise,
        as_of=as_of,
    )


def _actuals(ds: Dataset, start: date, end: date) -> dict[date, dict[str, int]]:
    out: dict[date, dict[str, int]] = {}
    for ln in ds.lines:
        if start <= ln.txn_date <= end:
            d = out.setdefault(ln.txn_date, {"in": 0, "out": 0})
            if ln.amount_paise > 0:
                d["in"] += ln.amount_paise
            else:
                d["out"] += -ln.amount_paise
    return out


def _err_share(pred: int, actual_in: int, actual_out: int) -> float:
    """Error as a share of total money movement in the window.

    Why not MAPE on net cash change: net change is a small difference between two large numbers, so
    the percentage is unstable by construction - a week where receipts and payments nearly cancel
    turns a correct forecast into "infinite % error". Dividing by gross movement keeps the same
    intuition (error per rupee that actually moved) with a denominator that cannot vanish.
    """
    gross = abs(actual_in) + abs(actual_out)
    return abs(pred) / gross * 100.0 if gross else 0.0


def doc_clear_dates(ds: Dataset, settings: Settings) -> dict[str, date]:
    """Ground truth for "when did this document really get paid", from the *full* ledger.

    Used only as the label when scoring the model's ranking of expected collections - never as an
    input to a forecast, which would be the classic look-ahead sin. The origin's forecast sees only
    documents that were still open at that date.
    """
    from ..recon.engine import Reconciler

    recon = Reconciler(ds, settings, strategy="full").run()
    out: dict[str, date] = {}
    by_id = {d.doc_id: d for d in ds.docs}
    line_date = {ln.line_id: ln.txn_date for ln in ds.lines}
    for m in recon.matches:
        when = line_date.get(m.line_id)
        if when is None:
            continue
        for did in m.doc_ids:
            d = by_id.get(did)
            if d and (d.number not in out or when > out[d.number]):
                out[d.number] = when
    return out


def backtest(
    ds: Dataset,
    settings: Settings,
    *,
    horizons: list[int] | None = None,
    n_origins: int = 8,
    warmup_days: int = 75,
    runs: int = 250,
    step_days: int = 10,
) -> BacktestResult:
    """Rolling-origin evaluation. At each origin the whole pipeline is re-run on data <= origin:
    reconcile, learn behaviour, forecast, then compare with what the feed shows happened."""
    horizons = horizons or [7, 14, 30]
    last = max(l.txn_date for l in ds.lines) if ds.lines else ds.as_of
    first = min((d.doc_date for d in ds.docs), default=last)
    span = max(horizons)
    res = BacktestResult(horizons=list(horizons))
    acc: dict[str, dict[str, list[float]]] = {}
    sign_hits: dict[str, int] = {}
    sign_total: dict[str, int] = {}
    cov: dict[int, list[float]] = {}
    rank: dict[str, dict[int, list[float]]] = {"cashpilot": defaultdict(list), "by_amount": defaultdict(list)}
    cleared_on = doc_clear_dates(ds, settings)
    n_used = 0
    for k in range(n_origins):
        # overlapping rolling origins (step < horizon) is the standard setup; using one origin per
        # horizon-length would leave 2-3 points in a 180-day series and the "accuracy" would be noise
        origin = last - timedelta(days=span + 1 + k * max(1, step_days))
        if (origin - first).days < warmup_days:
            continue
        h = _slice_dataset(ds, origin)
        rec = Reconciler(h, settings, strategy="full").run()
        fut = forecast(h, rec, settings, horizon=span, runs=runs, as_of=origin, use_monte_carlo=True)
        actual = _actuals(ds, origin + timedelta(days=1), origin + timedelta(days=span))
        a_in = {i: sum(v["in"] for d, v in actual.items() if (d - origin).days == i) for i in range(1, span + 1)}
        a_out = {i: sum(v["out"] for d, v in actual.items() if (d - origin).days == i) for i in range(1, span + 1)}
        # baselines on the same windows, from the same history
        hist = _actuals(h, origin - timedelta(days=56 * 7), origin)  # wide to have 8 weeks of each weekday
        naive_net: dict[int, float] = {}
        for i in range(1, span + 1):
            day = origin + timedelta(days=i)
            samples = [
                (v["in"] - v["out"])
                for d, v in hist.items()
                if d.weekday() == day.weekday() and (day - d).days in (7, 14, 21, 28, 35, 42, 49, 56)
            ]
            naive_net[i] = sum(samples) / len(samples) if samples else 0.0
        ma_net = 0.0
        recent = [d for d in hist if d > origin - timedelta(days=28)]
        if recent:
            ma_net = sum(hist[d]["in"] - hist[d]["out"] for d in recent) / max(1, len(recent))
        point_in: dict[int, int] = defaultdict(int)
        point_out: dict[int, int] = defaultdict(int)
        for d in h.docs:
            if d.outstanding_paise <= 0:
                continue
            i = (d.due_date - origin).days
            if 1 <= i <= span:
                (point_in if d.kind == "AR" else point_out)[i] += d.outstanding_paise

        res.origins.append(origin.isoformat())
        row: dict[str, object] = {"origin": origin.isoformat()}
        for hh in horizons:
            A_in = sum(a_in.get(i, 0) for i in range(1, hh + 1))
            A_out = sum(a_out.get(i, 0) for i in range(1, hh + 1))
            A_net = A_in - A_out
            P_in = sum(x.expected_in_paise for x in fut.days[:hh])
            P_out = sum(x.expected_out_paise for x in fut.days[:hh])
            preds = {
                "cashpilot": P_in - P_out,
                "seasonal_naive": int(sum(naive_net.get(i, 0.0) for i in range(1, hh + 1))),
                "moving_avg": int(ma_net * hh),
                "due_date_sum": int(sum(point_in.get(i, 0) for i in range(1, hh + 1)) - sum(point_out.get(i, 0) for i in range(1, hh + 1))),
            }
            for name, pred_net in preds.items():
                m = acc.setdefault((name, hh), {"mae_net": [], "mae_in": [], "mae_out": [], "bias_net": [], "share_gross": [], "mape_net": []})
                m["mae_net"].append(abs(pred_net - A_net))
                m["bias_net"].append(pred_net - A_net)
                m["share_gross"].append(_err_share(pred_net - A_net, A_in, A_out))
                m["mape_net"].append(abs(pred_net - A_net) / abs(A_net) * 100.0 if A_net else 0.0)
                sign_total[(name, hh)] = sign_total.get((name, hh), 0) + 1
                if (pred_net >= 0) == (A_net >= 0):
                    sign_hits[(name, hh)] = sign_hits.get((name, hh), 0) + 1
                if name == "cashpilot":
                    m["mae_in"].append(abs(P_in - A_in))
                    m["mae_out"].append(abs(P_out - A_out))
            if hh == span:
                row.update(
                    {
                        "actual_in_paise": A_in,
                        "actual_out_paise": A_out,
                        "actual_net_paise": A_net,
                        "predicted_net_paise": P_in - P_out,
                        "naive_net_paise": preds["seasonal_naive"],
                        "due_date_net_paise": preds["due_date_sum"],
                        "error_paise": (P_in - P_out) - A_net,
                        "error_share_of_gross_pct": round(_err_share((P_in - P_out) - A_net, A_in, A_out), 2),
                        "band_paise": fut.days[hh - 1].closing_hi_paise - fut.days[hh - 1].closing_lo_paise,
                    }
                )
        # "did the model know WHICH invoices would clear?" - rank the open book by the amount the
        # forecast expects inside the window, then check the top k against what actually settled.
        # The control is the same book ranked by size alone, which is what a treasurer with no
        # behavioural history would do. Both see exactly the same information at the origin.
        by_doc = {k: float(v) for k, v in fut.expected_by_doc.items()}
        open_at_origin = [d for d in h.docs if d.outstanding_paise > 0 and d.doc_date <= origin]
        for hh in (7, span):
            window_end = origin + timedelta(days=hh)
            for tag, order in (
                ("cashpilot", sorted(by_doc.items(), key=lambda kv: -kv[1])),
                ("by_amount", sorted(((d.number, d.outstanding_paise) for d in open_at_origin), key=lambda kv: -kv[1])),
            ):
                top = [num for num, _v in order[:25] if num]
                if not top:
                    continue
                hits = sum(1 for num in top if num in cleared_on and origin < cleared_on[num] <= window_end)
                rank[tag][hh].append(hits / len(top))
        res.per_origin.append(row)
        for hh in horizons:
            d = fut.days[hh - 1]
            lo = d.closing_lo_paise - fut.opening_paise
            hi = d.closing_hi_paise - fut.opening_paise
            A_in = sum(a_in.get(i, 0) for i in range(1, hh + 1))
            A_net = A_in - sum(a_out.get(i, 0) for i in range(1, hh + 1))
            cov.setdefault(hh, []).append(1.0 if lo <= A_net <= hi else 0.0)
        n_used += 1

    metrics: dict[str, float] = {}
    metrics["n_origins"] = float(n_used)
    for tag, per_h in rank.items():
        for hh, vals in per_h.items():
            if vals:
                metrics[f"{tag}_top25_hit_rate_pct_{hh}"] = round(100.0 * sum(vals) / len(vals), 1)
    for (name, hh), m in acc.items():
        for key, vals in m.items():
            if vals:
                metrics[f"{name}_{key}_{hh}"] = round(sum(vals) / len(vals), 1)
        base = acc.get(("seasonal_naive", hh), {}).get("mae_net")
        mine = m["mae_net"]
        if base and mine and sum(base) > 0:
            metrics[f"{name}_skill_vs_naive_pct_{hh}"] = round(100.0 * (1 - sum(mine) / sum(base)), 2)
        metrics[f"{name}_direction_accuracy_pct_{hh}"] = round(100.0 * sign_hits.get((name, hh), 0) / max(1, sign_total.get((name, hh), 1)), 1)
    res.metrics = metrics
    res.coverage = {f"band_hit_{hh}": round(100.0 * sum(v) / len(v), 1) for hh, v in cov.items() if v}
    res.notes = [
        f"{n_used} rolling origins, horizons {horizons}, warmup {warmup_days}d, {runs} MC paths per origin",
        "mae_net_* = mean absolute error of cumulative net cash movement over the horizon (paise)",
        "share_gross_* = |error| / total money that moved in the window, the headline accuracy number",
        "bias_net_* > 0 means the model is too optimistic about cash",
        "skill_vs_naive = 1 - MAE / MAE of the same-weekday seasonal naive baseline",
        "each origin re-runs reconciliation, so the ledger state and behaviour curves are as-of-date, not today's",
    ]
    return res


def seeded_future_check(ds: Dataset, settings: Settings, *, horizon: int = 30, runs: int = 1000) -> dict[str, object]:
    """Measurement 2: compare against the generator's own plan for the period after as_of."""
    import csv
    from pathlib import Path

    candidates = [Path(getattr(ds, "data_dir", "") or ".") / "truth_future_cash.csv", Path("data/synthetic/truth_future_cash.csv")]
    truth_path = next((c for c in candidates if c.exists()), None)
    if truth_path is None:
        return {"available": False, "reason": f"no truth_future_cash.csv next to the data ({candidates[0]}) - run `cashpilot generate`"}
    rec = Reconciler(ds, settings, strategy="full").run()
    fut = forecast(ds, rec, settings, horizon=horizon, runs=runs, use_monte_carlo=True)
    truth: dict[date, dict[str, int]] = {}
    with truth_path.open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            d = date.fromisoformat(row["day"])
            truth[d] = {"in": int(row["expected_in_paise"]), "out": int(row["expected_out_paise"]), "net": int(row["net_paise"])}
    start = (ds.as_of or date.today()) + timedelta(days=1)
    days = [start + timedelta(days=i) for i in range(horizon)]
    a_in = sum(truth.get(d, {}).get("in", 0) for d in days)
    a_out = sum(truth.get(d, {}).get("out", 0) for d in days)
    a_net = a_in - a_out
    p_in = sum(x.expected_in_paise for x in fut.days)
    p_out = sum(x.expected_out_paise for x in fut.days)
    p_net = p_in - p_out
    daily = [
        {
            "day": d.isoformat(),
            "actual_net_paise": truth.get(d, {}).get("net", 0),
            "predicted_net_paise": (fut.days[i].expected_in_paise - fut.days[i].expected_out_paise),
            "abs_err_pct": round(
                abs((fut.days[i].expected_in_paise - fut.days[i].expected_out_paise) - truth.get(d, {}).get("net", 0))
                / max(1, abs(truth.get(d, {}).get("net", 0)))
                * 100.0,
                2,
            ),
        }
        for i, d in enumerate(days)
    ]
    gross = a_in + a_out
    share = abs(p_net - a_net) / gross * 100.0 if gross else 0.0
    mae_daily = sum(abs(x["predicted_net_paise"] - x["actual_net_paise"]) for x in daily) / max(1, len(daily))
    days_ok = sum(1 for x in daily if x["abs_err_pct"] <= 20.0)
    return {
        "available": True,
        "horizon_days": horizon,
        "actual_in_paise": a_in,
        "actual_out_paise": a_out,
        "actual_net_paise": a_net,
        "predicted_in_paise": p_in,
        "predicted_out_paise": p_out,
        "predicted_net_paise": p_net,
        "cumulative_error_paise": p_net - a_net,
        "cumulative_error_pct": round(abs(p_net - a_net) / a_net * 100.0, 3) if a_net else None,
        "error_share_of_gross_pct": round(share, 2),
        "mean_daily_mae_paise": int(mae_daily),
        "days_within_20pct": days_ok,
        "daily": daily,
        "caveat": (
            "Shares structural assumptions with the generator, so this is an upper bound on real-world "
            "accuracy. Use the rolling-origin numbers as the headline."
        ),
    }
