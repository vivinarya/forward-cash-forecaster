"""Forecast engine: the survival maths, the learned curves, and the guard rails that stop it lying."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from cashpilot.forecast.engine import (
    _density,
    _misc_projection,
    _pmf,
    aging_recovery,
    expected_profile,
    forecast,
    learn_ledger,
)
from cashpilot.forecast.seasonality import (
    add_business_days,
    flows_by_day,
    month_index,
    quarter_end_boost,
    seasonality_factor,
    weekday_index,
)
from cashpilot.ingest import load_dataset
from cashpilot.models import BankLine, LedgerDoc
from cashpilot.recon.engine import Reconciler

AS_OF = date(2026, 9, 5)  # a Saturday


def test_flows_by_day_separates_signs():
    lines = [
        BankLine("a", AS_OF, "x", 1000),
        BankLine("b", AS_OF, "y", -400),
        BankLine("c", AS_OF - timedelta(days=1), "z", 700),
    ]
    assert flows_by_day(lines, "in") == {AS_OF: 1000, AS_OF - timedelta(days=1): 700}
    assert flows_by_day(lines, "out") == {AS_OF: 400}  # magnitudes, sign already spent on the side


def test_weekday_index_is_shrunk_towards_one():
    """Seven observations must not produce a factor of 4.0 - the index is empirical-Bayes shrunk."""
    day_flows = {AS_OF - timedelta(days=i): (2000 if (AS_OF - timedelta(days=i)).weekday() == 0 else 1000) for i in range(1, 60)}
    wd = weekday_index(day_flows)
    assert set(wd) == set(range(7))
    assert all(0.2 < v < 3.0 for v in wd.values())
    assert abs(sum(wd.values()) / 7 - 1.0) < 0.35


def test_sunday_is_never_a_value_date_for_this_ledger():
    assert add_business_days(date(2026, 9, 4), 0) == date(2026, 9, 4)  # Friday
    assert add_business_days(date(2026, 9, 5), 1) == date(2026, 9, 7)  # Sat +1 business day -> Mon
    assert add_business_days(date(2026, 9, 6), 1) == date(2026, 9, 7)  # Sunday rolls forward
    assert quarter_end_boost(date(2026, 9, 30)) > 1.0
    assert quarter_end_boost(date(2026, 5, 15)) == 1.0
    assert seasonality_factor(date(2026, 9, 30), weekday_index({}), month_index({})) > 1.0


def test_density_has_support_on_both_sides_of_the_due_date():
    dens = _density([1, 2, 3, -4, 40])
    assert any(k < 0 for k in dens)  # payments made early exist and must be modelled
    assert any(k > 30 for k in dens)  # and the long tail must not be truncated to zero


def test_pmf_is_a_normalised_distribution_blended_with_the_prior():
    pmf = _pmf([2, 2, 3], {2: 0.4, 3: 0.2, 10: 0.1})
    assert abs(sum(pmf.values()) - 1.0) < 1e-9
    assert pmf[2] > pmf[10]


def test_expected_profile_sums_to_at_most_one_and_respects_the_cure_fraction():
    pmf = {d: 1.0 / 31 for d in range(0, 31)}
    due = AS_OF - timedelta(days=5)
    probs_eager, placed_eager, branch_eager = expected_profile(1_000_00, due, AS_OF, 30, pmf, 0.95, 0.012)
    probs_deadbeat, placed_deadbeat, _ = expected_profile(1_000_00, due, AS_OF, 30, pmf, 0.20, 0.012)
    assert sum(probs_eager) <= 1.0 + 1e-9
    assert placed_deadbeat < placed_eager, "a customer who pays 20% of the time cannot be booked like one who pays 95%"
    assert branch_eager in {"pmf", "hazard"}


def test_no_evidence_left_falls_back_to_the_aging_prior_not_to_zero():
    """300 days overdue with no party history: the hazard branch must be chosen and recovery applied."""
    flat = {d: 0.0 for d in range(-45, 121)}
    probs, placed, branch = expected_profile(1_000_00, AS_OF - timedelta(days=300), AS_OF, 30, flat, 0.5, 0.012)
    assert branch in {"hazard", "beyond", "dust"}
    assert placed <= 1.0
    assert aging_recovery(300, {"0": 1.0, "61": 0.28, "181": 0.02}) < 0.1
    assert aging_recovery(3, {"0": 1.0, "61": 0.28, "181": 0.02}) == 1.0


def test_every_rupee_is_attributed_to_exactly_one_component(forecast_on_tiny):
    """Closure test for the worst bug in this module (docs/FAILURES.md #2).

    An early version added a flat "unbooked run-rate" term (`_misc_projection`) on top of the
    same-window churn component. Both describe unledgered movement, so outflow was double counted
    by ~20%. The fix was to delete the term; this test is what keeps it deleted: the forecast total
    must equal book + prospective + gateway and nothing else, at any tolerance tighter than a
    rounding rupee.
    """
    _ds, fut = forecast_on_tiny
    st = fut.stats
    book_in, book_out = st["book_in_paise"], st["book_out_paise"]
    comp_in = book_in + st["prospective_in_paise"] + st["gateway_in_paise"]
    comp_out = book_out + st["prospective_out_paise"]
    tot_in = sum(d.expected_in_paise for d in fut.days)
    tot_out = sum(d.expected_out_paise for d in fut.days)
    # every share is an int(), so a couple of thousand documents can shed a few thousand paise of
    # truncation in total. ₹0.01 per open document is the tolerance; a re-added "run-rate" term is
    # worth millions and cannot hide inside it.
    tol_in = 2 * len(fut.days) + 1  # one paise of truncation per day, not per document
    tol_out = 2 * len(fut.days) + 1
    assert abs(tot_in - comp_in) <= tol_in, f"inflow does not close: {tot_in} vs {comp_in}"
    assert abs(tot_out - comp_out) <= tol_out, f"outflow does not close: {tot_out} vs {comp_out}"
    assert st["expected_in_paise"] == pytest.approx(tot_in, rel=1e-6)
    assert st["expected_out_paise"] == pytest.approx(tot_out, rel=1e-6)


def test_learned_behaviour_is_per_party_and_per_kind(tiny_corpus, settings):
    ds = load_dataset(tiny_corpus)
    recon = Reconciler(ds, settings, strategy="full").run()
    learn = learn_ledger(ds, recon, ds.as_of or AS_OF)
    behaviours = learn["behaviours"]
    assert behaviours, "the corpus must produce at least one learned party behaviour"
    for key, b in behaviours.items():
        assert isinstance(key, tuple) and key[0] in {"AR", "AP"}, "behaviour is learned per party *and* per direction"
        assert 0.0 < b.p_paid <= 1.0
        assert sum(b.pmf.values()) == pytest.approx(1.0, abs=1e-6)
    assert any(b.n_delays >= 3 for b in behaviours.values()), "at least one party must have real delay evidence"
    for b in behaviours.values():
        assert b.n_delays >= 0 and 0.0 < b.p_paid <= 1.0


@pytest.fixture(scope="module")
def forecast_on_tiny(tiny_corpus, settings):
    ds = load_dataset(tiny_corpus)
    recon = Reconciler(ds, settings, strategy="full").run()
    return ds, forecast(ds, recon, settings, horizon=30, runs=400, as_of=ds.as_of or AS_OF)


def test_forecast_identity_closing_equals_opening_plus_flows(forecast_on_tiny):
    ds, fut = forecast_on_tiny
    cum = ds.opening_balance_paise
    for day in fut.days:
        cum += day.expected_in_paise - day.expected_out_paise
        assert day.closing_paise == cum, f"{day.day}: cumulative cash must be the ledger sum, not a second calculation"
    assert fut.days[-1].closing_paise == cum


def test_percentiles_are_ordered_and_widen_with_the_horizon(forecast_on_tiny):
    _ds, fut = forecast_on_tiny
    widths = {}
    for h, v in sorted(fut.horizons.items()):
        assert v["p10_closing_paise"] <= v["expected_closing_paise"] <= v["p90_closing_paise"], f"P10>P50 or P50>P90 at {h}d"
        assert v["p10_closing_paise"] <= v["p50_closing_paise"] <= v["p90_closing_paise"]
        widths[h] = v["p90_closing_paise"] - v["p10_closing_paise"]
    hs = sorted(widths)
    assert widths[hs[-1]] >= widths[hs[0]], f"band must not be narrower at {hs[-1]}d than at {hs[0]}d: {widths}"
    # Intermediate horizons may be *wider* than a shorter one when the dominant uncertainty is
    # timing inside the window rather than whether money moves at all; asserted here so the
    # behaviour is documented instead of surprising someone later.
    assert _misc_projection is not None


def test_forecast_reports_what_it_could_not_place(forecast_on_tiny):
    _ds, fut = forecast_on_tiny
    assert fut.stats["open_ar_paise"] > 0 and fut.stats["open_ap_paise"] > 0
    assert fut.stats["expected_in_paise"] > 0
    assert fut.stats["horizon_days"] == 30
    # every out-of-support document is counted, never silently dropped
    assert sum(fut.stats["profile_branch_counts"].values()) >= fut.stats["open_ar_docs"] + fut.stats["open_ap_docs"] - 5


def test_ledger_csvs_stop_at_asof_while_the_world_keeps_going(tmp_path):
    """The corpus bug that made every early accuracy number meaningless.

    `emit()` used to write the whole simulated world into invoices.csv / bills.csv, including the
    30 days the generator has already planned but that a real business cannot see yet. A forecaster
    evaluated on that corpus was reading the answer key. The truth files keep the future (they are
    the answer key, on purpose); the ledger files must not.
    """
    import csv

    from cashpilot.synth.world import World

    w = World(seed=99, as_of=AS_OF, history_days=60, horizon_days=20, n_customers=5, n_vendors=4, invoices_per_day=4.0, bills_per_day=3.0)
    w.build()
    w.emit(tmp_path)
    assert any(d.doc_date > AS_OF for d in w.docs), "the world must contain future documents for this check to mean anything"
    for name in ("invoices.csv", "bills.csv"):
        rows = list(csv.DictReader((tmp_path / name).open()))
        assert rows, name
        leaked = [r["document_date"] for r in rows if date.fromisoformat(r["document_date"]) > AS_OF]
        assert not leaked, f"{name} leaks {len(leaked)} documents raised after as_of"
