"""Forward cash forecast (7/14/30 days) built on top of the reconciled ledger.

The model, in words:

    cash(t) = cash(as_of)
            + open AR, weighted by each customer's *own* historical payment-delay distribution
              and their empirical propensity to ever pay
            + open AP, same treatment on the vendor side
            + gateway collections, projected from trailing capture volume through the observed
              net-of-deductions ratio and the learned T+n settlement lag
            + unbooked residual flow (bank charges, interest, odd credits) from lines that
              matched no document

Then the identical structure is sampled `monte_carlo_runs` times for P10/P50/P90 paths, because
the treasury decision is "does the bad path still clear my minimum balance", not "what is the mean".

`expected_profile()` is the single source of truth for both views: the deterministic path is the
expectation of that distribution and the Monte Carlo draws from it, so the two cannot disagree
(the most common bug in hand-rolled forecasting dashboards, including my first attempt here -
docs/FAILURES.md #4).

Two properties worth stating plainly:
 * it is a dated-contract model: unbilled revenue and ad-hoc spend are outside it by construction;
 * delay distributions are learned from the same synthetic world that generates the evaluation
   data, so absolute errors read optimistic in a way a real deployment will not. The baseline
   skill comparison in `bench forecast` is the number that means something.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

from ..config import Settings
from ..ingest import Dataset
from ..models import DayFlow, ReconResult
from .seasonality import (
    add_business_days,
    flows_by_day,
    month_index,
    seasonality_factor,
    weekday_index,
)

DUST_PAISE = 100  # ignore sub-rupee rounding dust everywhere below


@dataclass(slots=True)
class PartyBehaviour:
    code: str
    kind: str
    pmf: dict[int, float]
    p_paid: float
    n_delays: int
    median_delay: int
    share_uncleared: float


@dataclass
class CashForecast:
    as_of: date
    opening_paise: int
    days: list[DayFlow] = field(default_factory=list)
    horizons: dict[int, dict[str, object]] = field(default_factory=dict)
    stats: dict[str, object] = field(default_factory=dict)
    drivers: list[dict[str, object]] = field(default_factory=list)
    expected_by_doc: dict[str, int] = field(default_factory=dict)
    behaviour_table: list[dict[str, object]] = field(default_factory=list)

    def expected_net_change(self, horizon: int) -> int:
        return sum(d.expected_in_paise - d.expected_out_paise for d in self.days[:horizon])

    def to_rows(self) -> list[dict[str, object]]:
        return [
            {
                "day": d.day.isoformat(),
                "expected_in_paise": d.expected_in_paise,
                "expected_out_paise": d.expected_out_paise,
                "expected_net_paise": d.expected_in_paise - d.expected_out_paise,
                "closing_paise": d.closing_paise,
                "closing_p10_paise": d.closing_lo_paise,
            "closing_p50_paise": d.closing_p50_paise,
                "closing_p50_paise": d.closing_p50_paise or d.closing_paise,
                "closing_p90_paise": d.closing_hi_paise,
                "band_width_paise": d.closing_hi_paise - d.closing_lo_paise,
                "below_operating_minimum": "",
                "top_drivers": d.note,
            }
            for d in self.days
        ]


def aging_recovery(days_overdue: int, table: dict[str, float]) -> float:
    """Multiplicative haircut on expected collection by age bucket (see config `ar_aging_recovery`)."""
    if not table or days_overdue <= 0:
        return 1.0
    factor = 1.0
    for start in sorted((int(k) for k in table if str(k).isdigit())):
        if days_overdue >= start:
            factor = float(table[str(start)] if str(start) in table else table[start])
    return factor


def _weekday_weighted(probs: list[float], as_of: date, horizon: int, wd: dict[int, float]) -> list[float]:
    """Re-spread a document's in-window probabilities over the weekday pattern of the book.

    The delay distribution says *how late*; the weekday index says *which days this business's
    money actually moves*. Combining them keeps the total probability of each invoice identical
    (renormalised) and only moves it across days - which is why this is safe to apply to the
    expectation and to the Monte Carlo alike.
    """
    out = [0.0] * (horizon + 1)
    tot = 0.0
    for i in range(1, horizon + 1):
        f = wd.get((as_of + timedelta(days=i)).weekday(), 1.0)
        out[i] = probs[i] * max(0.2, min(3.0, f))
        tot += out[i]
    src = sum(probs[1:])
    if tot > 0 and src > 0:
        scale = src / tot
        for i in range(1, horizon + 1):
            out[i] *= scale
    return out


def _median(values: list[int]) -> int:
    if not values:
        return 0
    s = sorted(values)
    return s[len(s) // 2]


def _density(delays: list[int], *, lo: int = -45, hi: int = 120, kernel: float = 5.0) -> dict[int, float]:
    """Kernel-smoothed density of observed payment delays over a bounded support.

    Kernel (not histogram) because the observation counts per counterparty are single digits:
    one payment at "due +2" is evidence about +1, +2 and +3, not a spike at +2 with zeros
    everywhere else. The support is deliberately asymmetric - a business can pay 45 days early,
    but "120 days late" is already a collection case, not a payment-timing observation.
    """
    import math

    bins = list(range(lo, hi + 1))
    dens = {b: 0.0 for b in bins}
    sigma = max(1.6, kernel)
    for x in delays:
        c = max(lo, min(hi, int(round(x))))
        span = int(math.ceil(3 * sigma))
        for off in range(-span, span + 1):
            b = c + off
            if lo <= b <= hi:
                dens[b] += math.exp(-0.5 * (off / sigma) ** 2)
    total = sum(dens.values())
    if total <= 0:
        # no evidence at all: assume "on terms, a little late", which is the SMB median
        for b in bins:
            dens[b] = math.exp(-0.5 * ((b - 3) / 14.0) ** 2)
        total = sum(dens.values())
    return {b: v / total for b, v in dens.items()}


def _pmf(delays: list[int], global_dens: dict[int, float] | None = None, *, shrink_k: float = 8.0, floor: float = 0.0015) -> dict[int, float]:
    """Payment-delay pmf: shrunk toward the book-wide curve, then floored so no day is impossible.

    Two failure modes this shape avoids, both of which I hit:
      * Laplace-smoothing a 256-bin histogram with an alpha proportional to n makes the prior a
        constant ~2/3 of the weight, so *every* 30-day window looked like ~12% of the money -
        the forecast under-called cash by 2x (docs/FAILURES.md #3).
      * Raw counts on 4 observations produce spikes that put real weight on exactly the four days
        a customer happened to pay on. Empirical-Bayes shrinkage with n/(n+8) fixes that.
    """
    obs = _density(delays)
    if global_dens is None:
        out = dict(obs)
    else:
        w = len(delays) / (len(delays) + shrink_k) if delays else 0.0
        out = {b: w * obs.get(b, 0.0) + (1 - w) * global_dens.get(b, 0.0) for b in obs}
    n = len(out)
    out = {b: v + floor / n for b, v in out.items()}
    total = sum(out.values())
    return {b: v / total for b, v in out.items()}


def learn_ledger(ds: Dataset, recon: ReconResult, as_of: date, *, aged_floor_days: int = 45) -> dict[str, object]:
    """Learn, for every counterparty, when they pay and whether they pay - from the reconciled book.

    Everything here is derived from data at or before `as_of`: the matches this reconciliation run
    produced. No future information, and the same function is used by the backtest so the
    reported skill numbers come from the code path that ships.
    """
    lines_by_id = {ln.line_id: ln for ln in ds.lines if ln.txn_date <= as_of}
    cleared_on: dict[str, date] = {}
    for m in recon.matches:
        ln = lines_by_id.get(m.line_id)
        if ln is None:
            continue
        for doc_id in m.doc_ids:
            cleared_on.setdefault(doc_id, ln.txn_date)

    groups: dict[tuple[str, str], dict[str, list]] = defaultdict(lambda: {"delays": [], "cleared": 0, "aged_open": 0, "open": 0})
    for d in ds.docs:
        if d.doc_date > as_of:
            continue
        code = str(d.extra.get("customer_code") or d.extra.get("vendor_code") or "GENERIC")
        g = groups[(d.kind, code)]
        if d.doc_id in cleared_on:
            g["cleared"] += 1
            g["delays"].append((cleared_on[d.doc_id] - d.due_date).days)
        else:
            g["open"] += 1
            if d.outstanding_paise > DUST_PAISE and d.due_date <= as_of - timedelta(days=aged_floor_days):
                g["aged_open"] += 1

    all_delays = [v for g in groups.values() for v in g["delays"]]
    global_dens = _density(all_delays)
    global_pmf = _pmf(all_delays, None)
    behaviours: dict[tuple[str, str], PartyBehaviour] = {}
    for (kind, code), g in groups.items():
        # always blended toward the book-wide curve; a 3-invoice customer gets a wide, dull prior
        pmf = _pmf(g["delays"], global_dens)
        # p_paid: of this counterparty's invoices that were due long enough ago to have been paid,
        # what share actually got paid? Laplace-smoothed so a 3-invoice customer is not 0% or 100%.
        aged = g["cleared"] + g["aged_open"]
        p_paid = (g["cleared"] + 1) / (aged + 2) if aged else 0.95
        behaviours[(kind, code)] = PartyBehaviour(
            code=code,
            kind=kind,
            pmf=pmf,
            p_paid=round(min(0.995, p_paid), 4),
            n_delays=len(g["delays"]),
            median_delay=_median(g["delays"]),
            share_uncleared=round(g["open"] / max(1, g["open"] + g["cleared"]), 3),
        )
    return {
        "behaviours": behaviours,
        "global_pmf": global_pmf,
        "cleared_docs": set(cleared_on),
        "median_delay_ar": _median([v for (k, _c), g in groups.items() if k == "AR" for v in g["delays"]]),
        "median_delay_ap": _median([v for (k, _c), g in groups.items() if k == "AP" for v in g["delays"]]),
        "n_cleared": len(cleared_on),
    }


def expected_profile(
    amount_paise: int,
    due: date,
    as_of: date,
    horizon: int,
    pmf: dict[int, float],
    p_paid: float,
    aged_daily_hazard: float,
) -> tuple[list[float], float, str]:
    """Probability a document's money lands on each day 1..horizon, given it has not arrived yet.

    A discrete-time survival model with a *cure fraction* (p_paid = the share of this
    counterparty's invoices that ever get paid). For a document already `age` days past its due
    date, being unpaid is itself evidence:

        P(paid in window | unpaid at age) = p_paid * q(window) / (p_paid * S(age) + 1 - p_paid)

    where q is the learned delay density and S(age) the mass beyond `age`. That denominator is the
    whole point. Without the `1 - p_paid` term the model conditions on "it will be paid" and then
    reads the near-zero tail of the density as "so it must be paid in the next few days" - which
    made an early version of this forecaster predict ₹18M of receipts from invoices that were
    already written off, and swing to a 2x under-call when I tried to fix it with a blunt aging
    haircut instead. Both attempts, and the numbers, are in docs/FAILURES.md #3.

    Three branches:
      `pmf`     - inside the observed delay support: the formula above.
      `hazard`  - past the end of the support (no evidence left): flat collection hazard, which the
                  caller further discounts by the AR aging recovery curve.
      `beyond`  - due date is after the horizon: nothing expected inside the window.
    """
    probs = [0.0] * (horizon + 1)
    if amount_paise <= DUST_PAISE:
        return probs, 0.0, "dust"
    support_lo = min(pmf)
    support_hi = max(pmf)
    age = (as_of - due).days
    if due + timedelta(days=support_lo) > as_of + timedelta(days=horizon):
        return probs, 0.0, "beyond"
    if age >= support_hi:  # nothing in the learned distribution reaches this far back
        alive = 1.0
        for i in range(1, horizon + 1):
            day = as_of + timedelta(days=i)
            if day.weekday() == 6:
                continue
            hit = alive * aged_daily_hazard
            probs[i] = hit * p_paid
            alive -= hit
        return probs, 1.0 - alive, "hazard"
    tail = sum(v for k, v in pmf.items() if k > age)
    denom = p_paid * tail + (1.0 - p_paid)
    if denom <= 1e-9:
        return probs, 0.0, "starved"
    placed = 0.0
    for i in range(1, horizon + 1):
        q = pmf.get(age + i, 0.0)
        if q <= 0:
            continue
        probs[i] = p_paid * q / denom
        placed += probs[i]
    return probs, min(1.0, placed), "pmf"


@dataclass
class DayContrib:
    day: date
    inflow: int = 0
    outflow: int = 0
    top: list[tuple[int, str]] = field(default_factory=list)


def _doc_items(ds: Dataset, cleared: set[str], as_of: date):
    for d in ds.docs:
        if d.doc_id in cleared or d.outstanding_paise <= DUST_PAISE or d.doc_date > as_of:
            continue
        key = (d.kind, str(d.extra.get("customer_code") or d.extra.get("vendor_code") or "GENERIC"))
        yield d, key


def forecast(
    ds: Dataset,
    recon: ReconResult,
    settings: Settings,
    *,
    horizon: int | None = None,
    runs: int | None = None,
    as_of: date | None = None,
    seed: int = 7,
    use_monte_carlo: bool = True,
) -> CashForecast:
    """Produce the 7/14/30-day forward cash position with P10/P50/P90 bands."""
    import numpy as np

    as_of = as_of or ds.as_of or date.today()
    policy = settings.rules["cash_policy"]
    H = int(horizon or max(policy["forecast_horizons"]))
    runs = int(runs or policy["monte_carlo_runs"])
    learn = learn_ledger(ds, recon, as_of)
    behaviours = learn["behaviours"]  # type: ignore[assignment]
    global_pmf: dict[int, float] = learn["global_pmf"]  # type: ignore[assignment]
    cleared: set[str] = learn["cleared_docs"]  # type: ignore[assignment]
    hazard = float(settings.rules.get("aged_daily_hazard", 0.012))
    redistribute = bool(policy.get("weekday_redistribute", True))
    aging_table = {k: v for k, v in settings.rules.get("ar_aging_recovery", {}).items() if not str(k).startswith("_")}

    hist_lines = [ln for ln in ds.lines if ln.txn_date <= as_of]
    wd = weekday_index(flows_by_day(hist_lines, "in"))
    mo = month_index(flows_by_day(hist_lines, "in"))

    expected_in: dict[date, int] = defaultdict(int)
    expected_out: dict[date, int] = defaultdict(int)
    contrib: dict[date, list[tuple[int, str]]] = defaultdict(list)
    # expected settlement per document over the whole window - the ranking the backtest scores and
    # the dashboard lists, i.e. "which invoice", not just "how much"
    doc_expect: dict[str, int] = defaultdict(int)
    book_in = 0.0
    book_out = 0.0
    stats: dict[str, object] = {
        "as_of": as_of.isoformat(),
        "horizon_days": H,
        "opening_paise": ds.opening_balance_paise,
        "open_ar_docs": 0,
        "open_ar_paise": 0,
        "open_ap_docs": 0,
        "open_ap_paise": 0,
        "aged_docs_out_of_support": 0,
        "recovery_discounted_paise": 0,
    }
    branch_counts: dict[str, int] = {}

    for d, key in _doc_items(ds, cleared, as_of):
        bhv = behaviours.get(key)  # type: ignore[union-attr]
        pmf = bhv.pmf if bhv else global_pmf
        p_paid = bhv.p_paid if bhv else 0.9
        age_days = (as_of - d.due_date).days
        probs, placed, branch = expected_profile(d.outstanding_paise, d.due_date, as_of, H, pmf, p_paid, hazard)
        # the recovery curve is a prior for documents with *no* evidence left, not a haircut on the
        # learned delay distribution, which already contains the late payers
        recovery = aging_recovery(age_days, aging_table) if (d.kind == "AR" and branch == "hazard") else 1.0
        branch_counts[branch] = branch_counts.get(branch, 0) + 1
        if placed <= 0.0:
            stats["aged_docs_out_of_support"] = int(stats["aged_docs_out_of_support"]) + 1
        if redistribute:
            probs = _weekday_weighted(probs, as_of, H, wd)
        for i in range(1, H + 1):
            share = d.outstanding_paise * probs[i] * recovery  # p_paid already inside probs
            if share <= 0:
                continue
            day = as_of + timedelta(days=i)
            (expected_in if d.kind == "AR" else expected_out)[day] += share
            # kept as floats and rounded once, so the component totals close against the per-day
            # sums to within a few paise no matter how many documents are in the book
            if d.kind == "AR":
                book_in += share
            else:
                book_out += share
            contrib[day].append((int(share), f"{d.number}·{d.counterparty.split()[0]}·p{p_paid * recovery:.2f}"))
            doc_expect[d.number] += int(share)
        if recovery < 1.0:
            stats["recovery_discounted_paise"] = int(stats["recovery_discounted_paise"]) + int(d.outstanding_paise * (1 - recovery))
        if d.kind == "AR":
            stats["open_ar_docs"] = int(stats["open_ar_docs"]) + 1
            stats["open_ar_paise"] = int(stats["open_ar_paise"]) + d.outstanding_paise
        else:
            stats["open_ap_docs"] = int(stats["open_ap_docs"]) + 1
            stats["open_ap_paise"] = int(stats["open_ap_paise"]) + d.outstanding_paise

    stats["book_in_paise"] = int(round(book_in))
    stats["book_out_paise"] = int(round(book_out))

    prospective = _prospective_component(ds, recon, as_of, H, wd, mo)
    _ = recon
    for day, amt in prospective["in"].items():
        expected_in[day] += amt
    for day, amt in prospective["out"].items():
        expected_out[day] += amt
    stats["prospective_in_paise"] = prospective["in_total"]
    stats["prospective_out_paise"] = prospective["out_total"]
    stats["ledger_coverage_in"] = prospective["coverage_in"]
    stats["same_window_churn_share"] = prospective["same_window_churn_share"]
    stats["churn_reference_windows"] = prospective["n_reference_windows"]

    gw = _gateway_projection(ds, as_of, H, wd, mo)
    for day, amt in gw["inflow"].items():
        expected_in[day] += amt
    stats["gateway_in_paise"] = int(gw.get("total", 0) or sum(gw["inflow"].values()))
    # NOTE: there is deliberately no separate "unbooked residual flow" term. An earlier version
    # added one on top of the same-window churn component and both describe unledgered movement -
    # the double count was worth ~20% of forecast outflow (docs/FAILURES.md #3).
    misc = {"in": {}, "out": {}, "mean_in": 0, "mean_out": 0}

    days: list[DayFlow] = []
    cum = ds.opening_balance_paise
    for i in range(1, H + 1):
        day = as_of + timedelta(days=i)
        ei, eo = int(expected_in.get(day, 0)), int(expected_out.get(day, 0))
        cum += ei - eo
        top = sorted(contrib.get(day, []), reverse=True)[:3]
        days.append(
            DayFlow(
                day=day,
                expected_in_paise=ei,
                expected_out_paise=eo,
                band_lo_paise=cum,
                band_hi_paise=cum,
                closing_paise=cum,
                closing_lo_paise=cum,
                closing_hi_paise=cum,
                note=" | ".join(f"{lbl} ₹{amt / 100:,.0f}" for amt, lbl in top),
            )
        )

    matrix_arr = None
    if use_monte_carlo and runs > 0:
        import time

        t0 = time.perf_counter()
        matrix, daily_mc = _monte_carlo(ds, learn, as_of, H, gw, misc, prospective, seed, hazard, runs, aging_table)
        stats["mc_ms"] = round((time.perf_counter() - t0) * 1000, 2)
        lo_q, mid_q, hi_q = (list(policy["percentiles"]) + [10, 50, 90])[:3]
        p_lo = _percentile(matrix, lo_q)
        p_mid = _percentile(matrix, mid_q)
        p_hi = _percentile(matrix, hi_q)
        for i, dd in enumerate(days):
            dd.closing_lo_paise = int(p_lo[i])
            dd.closing_hi_paise = int(p_hi[i])
            dd.closing_p50_paise = int(p_mid[i])
            dd.band_lo_paise = int(p_mid[i])
            dd.band_hi_paise = int(p_hi[i])
        stats["mc_daily_sd_paise"] = int(daily_mc.std(axis=0).mean())
        matrix_arr = np.asarray(matrix)
    else:
        p_lo = [d.closing_lo_paise for d in days]
        p_mid = [d.closing_paise for d in days]
        p_hi = [d.closing_hi_paise for d in days]

    floor_paise = int(policy["minimum_cash_paise"])
    horizons: dict[int, dict[str, object]] = {}
    # the caller's horizon is always reported, even when it is not one of the configured steps,
    # so `--horizon 21` cannot silently show only 7 and 14
    for h in sorted({int(x) for x in policy["forecast_horizons"]} | {int(H)}):
        if h > H:
            continue
        window = days[:h]
        worst = min(window, key=lambda x: x.closing_lo_paise)
        below = [x for x in window if x.closing_lo_paise < floor_paise]
        horizons[int(h)] = {
            "horizon_days": int(h),
            "expected_net_change_paise": sum(x.expected_in_paise - x.expected_out_paise for x in window),
            "expected_closing_paise": window[-1].closing_paise,
            "p50_closing_paise": int(p_mid[h - 1]),
            "expected_minus_p50_paise": int(window[-1].closing_paise - p_mid[h - 1]),
            "p10_closing_paise": int(p_lo[h - 1]),
            "p90_closing_paise": int(p_hi[h - 1]),
            "worst_day": worst.day.isoformat(),
            "worst_day_expected_closing_paise": worst.closing_paise,
            "worst_day_p10_closing_paise": worst.closing_lo_paise,
            "days_below_operating_minimum": len(below),
            "first_breach_day": below[0].day.isoformat() if below else None,
            "probability_below_operating_minimum": (
                round(float((matrix_arr[:, h - 1] < floor_paise).mean()), 4)
                if matrix_arr is not None
                else (1.0 if window[-1].closing_paise < floor_paise else 0.0)
            ),
            "funding_need_p10_paise": max(0, floor_paise - worst.closing_lo_paise),
        }
    stats.update(
        {
            "mc_runs": runs,
            "expected_in_paise": int(sum(expected_in.values())),
            "expected_out_paise": int(sum(expected_out.values())),
            "gateway_projected_in_paise": gw["total"],
            "gateway_net_ratio": gw["ratio"],
            "gateway_lag_days": gw["lag"],
            "gateway_daily_gross_paise": gw["daily_gross"],
            "misc_daily_mean_in_paise": misc["mean_in"],
            "misc_daily_mean_out_paise": misc["mean_out"],
            "operating_minimum_paise": floor_paise,
            "ar_aging_applied": bool(aging_table),
            "profile_branch_counts": dict(branch_counts),
            "min_expected_closing_paise": min((d.closing_paise for d in days), default=0),
            "min_p10_closing_paise": min(p_lo, default=0),
            "learnt": {
                "n_cleared_docs": learn["n_cleared"],
                "median_delay_ar_days": learn["median_delay_ar"],
                "median_delay_ap_days": learn["median_delay_ap"],
                "parties_profiled": len(behaviours),
                "weekday_index": {str(k): v for k, v in sorted(wd.items())},
                "month_index": {str(k): v for k, v in sorted(mo.items())},
            },
        }
    )
    behaviour_table = [
        {
            "kind": b.kind,
            "party_code": b.code,
            "median_delay_days": b.median_delay,
            "p_paid": b.p_paid,
            "observations": b.n_delays,
            "share_uncleared": b.share_uncleared,
        }
        for b in sorted(behaviours.values(), key=lambda x: (x.kind, -x.n_delays))  # type: ignore[arg-type]
    ]
    return CashForecast(
        as_of=as_of,
        opening_paise=ds.opening_balance_paise,
        days=days,
        horizons=horizons,
        stats=stats,
        drivers=[{"day": k.isoformat(), "top": sorted(v, reverse=True)[:5]} for k, v in sorted(contrib.items())],
        expected_by_doc=dict(doc_expect),
        behaviour_table=behaviour_table,
    )


def _percentile(rows, q: float) -> list[float]:
    import numpy as np

    arr = np.asarray(rows, dtype=float)
    if arr.ndim == 1:
        return [float(arr[0])] * len(arr)
    return [float(x) for x in np.percentile(arr, q, axis=0)]


def _gateway_projection(ds: Dataset, as_of: date, horizon: int, wd: dict[int, float], mo: dict[int, float]) -> dict[str, object]:
    """Project gateway collections: trailing capture volume -> net credit after the T+n lag."""
    look = timedelta(days=28)
    recent = [p for p in ds.payments if p.captured_on <= as_of and p.captured_on > as_of - look]
    settled = [s for s in ds.settlements if s.settled_on <= as_of and s.settled_on > as_of - look]
    gross = sum(p.amount_paise for p in recent) or sum(s.gross_paise for s in settled)
    net = sum(s.net_paise for s in settled)
    ratio = round(net / gross, 4) if gross and net else 0.965
    days_with_capture = len({p.captured_on for p in recent}) or max(1, len(settled))
    daily_gross = int(gross / max(1, days_with_capture))
    lag = 2
    if settled:
        by_sid = {s.settlement_id: s for s in settled}
        lags = [(by_sid[p.settlement_id].settled_on - p.captured_on).days for p in recent if p.settlement_id in by_sid]
        lags = [x for x in lags if x >= 0]
        if lags:
            lag = _median(lags)
    inflow: dict[date, int] = defaultdict(int)
    for i in range(1, horizon + 1):
        capture_day = as_of + timedelta(days=i - lag)
        if capture_day.weekday() == 6 and lag >= 1:
            capture_day = add_business_days(capture_day, 0)
        credit_day = as_of + timedelta(days=i)
        if credit_day.weekday() == 6:
            continue
        f = seasonality_factor(capture_day, wd, mo)
        inflow[credit_day] += int(daily_gross * f * ratio)
    return {"inflow": dict(inflow), "total": int(sum(inflow.values())), "lag": lag, "ratio": ratio, "daily_gross": daily_gross, "sd": int(daily_gross * 0.22)}


def _prospective_component(ds: Dataset, recon: ReconResult, as_of: date, horizon: int, wd: dict[int, float], mo: dict[int, float]) -> dict[str, object]:
    """Cash that will move on documents that do not exist yet - measured, not assumed.

    An open-items forecast is structurally blind to an invoice raised *and* collected inside the
    horizon, which on 0-15 day terms is a big share of traffic at an Indian SMB. Guessing it with a
    flat run-rate double counts (the bills raised since the last reference date are already in
    today's ledger), so instead the component is measured the same way it will be missed: over past
    windows of the same length, how much of the movement came from documents raised *inside* that
    window. Averaged, then projected forward through the seasonality index.

    Reported as `prospective_in/out_paise` plus `same_window_churn_share` in every output, so it is
    visible and attackable, not a fudge factor inside the model.
    """
    lines_by_id = {ln.line_id: ln for ln in ds.lines}
    docs_by_id = {d.doc_id: d for d in ds.docs}
    cleared: dict[str, date] = {}
    for m in recon.matches:
        ln = lines_by_id.get(m.line_id)
        if ln is None:
            continue
        for doc_id in m.doc_ids:
            cleared.setdefault(doc_id, ln.txn_date)

    def window_stats(r_end: date) -> tuple[int, int, int, int]:
        r_start = r_end - timedelta(days=horizon)
        churn_in = churn_out = moved_in = moved_out = 0
        for doc_id, when in cleared.items():
            d = docs_by_id.get(doc_id)
            if d is None or not (r_start < when <= r_end):
                continue
            moved_in if d.kind == "AR" else moved_out
            amt = abs(d.outstanding_paise) or d.amount_paise
            if r_start < d.doc_date <= r_end:  # raised AND settled inside the window: invisible at origin
                if d.kind == "AR":
                    churn_in += amt
                else:
                    churn_out += amt
            else:
                if d.kind == "AR":
                    moved_in += amt
                else:
                    moved_out += amt
        return churn_in, churn_out, moved_in, moved_out

    tot_in = tot_out = tot_book = 0.0
    wsum = 0.0
    for k, wt in ((1, 0.50), (2, 0.25), (3, 0.15), (4, 0.10)):
        ci, co, mi, mo_ = window_stats(as_of - timedelta(days=horizon * (k - 1)))
        if mi + mo_ <= 0 and ci + co <= 0:
            continue
        # recency weighted: the most recent window reflects today's run-rate; older windows drag the
        # estimate toward a business that no longer exists (and the world here is still growing)
        tot_in += wt * ci
        tot_out += wt * co
        tot_book += wt * (mi + mo_)
        wsum += wt
    churn_in = int(tot_in / wsum) if wsum else 0
    churn_out = int(tot_out / wsum) if wsum else 0
    weights: list[tuple[date, float]] = []
    for i in range(1, horizon + 1):
        day = as_of + timedelta(days=i)
        if day.weekday() == 6:
            continue
        weights.append((day, seasonality_factor(day, wd, mo)))
    wsum = sum(w for _, w in weights) or 1.0
    tin: dict[date, int] = {}
    tout: dict[date, int] = {}
    for day, w in weights:
        tin[day] = int(churn_in * w / wsum)
        tout[day] = int(churn_out * w / wsum)
    return {
        "in": tin,
        "out": tout,
        "in_total": int(sum(tin.values())),
        "out_total": int(sum(tout.values())),
        "coverage_in": round(tot_book / max(1, tot_book + tot_in), 3),
        "same_window_churn_share": round((tot_in + tot_out) / max(1, tot_in + tot_out + tot_book), 3),
        "n_reference_windows": 4 if wsum else 0,
    }


def _misc_projection(ds: Dataset, recon: ReconResult, as_of: date, horizon: int, wd: dict[int, float]) -> dict[str, object]:
    """Unbooked residual flow: average per-weekday movement of bank lines that matched no document."""
    unresolved = [ln for ln in ds.lines if ln.line_id not in recon.matched_line_ids and ln.txn_date <= as_of and ln.txn_date > as_of - timedelta(days=60)]
    per_wd_in: dict[int, list[int]] = defaultdict(list)
    per_wd_out: dict[int, list[int]] = defaultdict(list)
    by_day: dict[date, int] = defaultdict(int)
    for ln in unresolved:
        by_day[ln.txn_date] += ln.amount_paise
        (per_wd_in if ln.amount_paise > 0 else per_wd_out)[ln.txn_date.weekday()].append(abs(ln.amount_paise))
    n_days = max(1, len(by_day))
    mean_in = int(sum(sum(v) for v in per_wd_in.values()) / n_days)
    mean_out = int(sum(sum(v) for v in per_wd_out.values()) / n_days)
    tin: dict[date, int] = defaultdict(int)
    tout: dict[date, int] = defaultdict(int)
    for i in range(1, horizon + 1):
        day = as_of + timedelta(days=i)
        if day.weekday() == 6:
            continue
        f = wd.get(day.weekday(), 1.0)
        tin[day] += int(mean_in * f)
        tout[day] += int(mean_out * f)
    return {"in": dict(tin), "out": dict(tout), "mean_in": mean_in, "mean_out": mean_out, "n_days": n_days}


def _monte_carlo(ds, learn, as_of: date, H: int, gw: dict, misc: dict, prospective: dict, seed: int, hazard: float, runs: int, aging_table: dict[str, float]):
    """Sample the exact distribution `expected_profile` describes. Returns (closing_paths, daily_paths).

    Party-level correlation is the point: a customer who stops paying stops paying on *all* of
    their invoices at once, and that is what generates the fat left tail a treasury cares about.
    Sampling invoice timings independently instead would understate the P10 by a wide margin.
    """
    import numpy as np

    rng = np.random.default_rng(seed)
    behaviours = learn["behaviours"]  # type: ignore[assignment]
    global_pmf: dict[int, float] = learn["global_pmf"]  # type: ignore[assignment]
    cleared: set[str] = learn["cleared_docs"]  # type: ignore[assignment]

    day_index = {as_of + timedelta(days=i): i - 1 for i in range(1, H + 1)}
    gw_arr = np.zeros(H, dtype=np.int64)
    misc_arr = np.zeros(H, dtype=np.int64)
    for day, amt in gw["inflow"].items():
        if day in day_index:
            gw_arr[day_index[day]] += int(amt)
    for day, amt in misc["in"].items():
        if day in day_index:
            misc_arr[day_index[day]] += int(amt)
    for day, amt in misc["out"].items():
        if day in day_index:
            misc_arr[day_index[day]] -= int(amt)

    # gateway volume is the volatile part: scale it per run, leave the contracted book alone
    cv = 0.22
    mult = np.clip(rng.normal(1.0, cv, size=(runs, H)), 0.2, 2.5)
    daily = (gw_arr[None, :] * mult).round().astype(np.int64) + np.tile(misc_arr, (runs, 1))

    groups: dict[tuple, list[tuple[int, date, float]]] = defaultdict(list)
    for d, key in _doc_items(ds, cleared, as_of):
        age = (as_of - d.due_date).days
        # sign matters: an AP outflow sampled as an inflow shifted the whole simulated band up by
        # 2x payables (found by comparing P50 with the expected path - tests/test_forecast.py)
        sign = 1 if d.kind == "AR" else -1
        bhv = behaviours.get(key)  # type: ignore[union-attr]
        pmf = bhv.pmf if bhv else global_pmf
        p_paid = bhv.p_paid if bhv else 0.9
        _probs, _placed, branch = expected_profile(d.outstanding_paise, d.due_date, as_of, H, pmf, p_paid, hazard)
        recovery = aging_recovery(age, aging_table) if (d.kind == "AR" and branch == "hazard") else 1.0
        groups[key].append((d.outstanding_paise * sign, d.due_date, recovery))

    sampled_docs = 0
    for key, entries in groups.items():
        bhv = behaviours.get(key)  # type: ignore[union-attr]
        pmf = bhv.pmf if bhv else global_pmf
        p_paid = bhv.p_paid if bhv else 0.9  # already folded into probs; kept for the log line
        assert p_paid > 0
        for signed_amount, due, recovery in entries:
            amount = abs(signed_amount)
            probs, placed, _branch = expected_profile(amount, due, as_of, H, pmf, p_paid, hazard)
            if placed <= 0:
                continue
            # Sampling distribution: day 1..H, plus a "does not land in this window" slot.
            # Dropping that last slot is how the bands came out inverted (P10 above the expected
            # path): it silently conditions every invoice on being paid, so the simulated mean
            # sits far above the deterministic one. tests/test_forecast.py pins the two together.
            dist = np.clip(np.array(probs[1:], dtype=float), 0.0, 1.0)
            total = float(dist.sum())
            if total <= 0:
                continue
            dist = np.append(dist / max(total, 1.0), max(0.0, 1.0 - total))  # p_paid is inside probs
            if dist.sum() <= 0:
                continue
            dist = dist / dist.sum()
            draw = rng.choice(H + 1, size=runs, p=dist)
            # party_pays must not be applied twice: probs already carry the cure fraction, so the
            # only extra gate is the aging-recovery discount on the no-evidence branch.
            will_pay = np.ones(runs, dtype=bool) if recovery >= 0.999 else (rng.random(runs) < recovery)
            rows = np.nonzero((draw < H) & will_pay)[0]
            if rows.size:
                np.add.at(daily, (rows, draw[rows]), signed_amount)
                sampled_docs += 1

    closing = ds.opening_balance_paise + np.cumsum(daily, axis=1)
    return closing, daily
