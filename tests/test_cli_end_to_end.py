"""The panel path: from a clean checkout to reports, in the commands the README tells you to run.

Everything here goes through `cashpilot.cli.main`, because a unit test cannot catch the failure mode
that actually embarrasses people - a command that raises on the way to the report writer.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from cashpilot.cli import main

ARTIFACTS = [
    "matches.csv",
    "exceptions.csv",
    "settlements.csv",
    "recovery_batches.csv",
    "forecast.csv",
    "party_behaviour.csv",
    "aged_receivables.csv",
    "unresolved.csv",
    "run_manifest.json",
    "accuracy.json",
    "reconciliation.md",
    "settlements.md",
    "recovery.md",
    "forecast.md",
    "brief.md",
    "INDEX.md",
    "dashboard.html",
]


@pytest.fixture(scope="module")
def corpus(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("cli") / "data"
    rc = main(["generate", "--out", str(out), "--scale", "tiny", "--seed", "7", "--history-days", "70"])
    assert rc == 0
    return out


def test_generate_writes_a_complete_corpus(corpus):
    for name in [
        "bank_statement.csv",
        "invoices.csv",
        "bills.csv",
        "payment_advices.csv",
        "razorpay_settlements.csv",
        "razorpay_payments.csv",
        "razorpay_refunds.csv",
        "opening_balance.csv",
        "truth_matches.csv",
        "truth_future_cash.csv",
        "meta.json",
    ]:
        assert (corpus / name).exists(), f"{name} is missing - the generator and the loader have drifted apart"
    meta = json.loads((corpus / "meta.json").read_text())
    assert meta["bank_lines_history"] > 50, "the bar is 50+ records; the tiny corpus must clear it"


def test_run_produces_every_artifact_and_beats_the_naive_pass(corpus, tmp_path):
    out = tmp_path / "full"
    assert main(["run", "--data", str(corpus), "--out", str(out), "--llm", "off", "--runs", "200"]) == 0
    for name in ARTIFACTS:
        assert (out / name).exists(), f"{name} was not written"
        assert (out / name).stat().st_size > 0, f"{name} is empty"

    acc = json.loads((out / "accuracy.json").read_text())
    assert acc["recall"] > 0.9 and acc["precision"] > 0.95, acc
    assert acc["auto_post_precision"] == 1.0, "auto-posting may never trade precision for recall"
    manifest = json.loads((out / "run_manifest.json").read_text())
    assert manifest["llm"]["enabled"] is False and manifest["llm"]["calls"] == 0
    assert manifest["stages_ms"]["total_ms"] > 0

    exact = tmp_path / "exact"
    main(["run", "--data", str(corpus), "--out", str(exact), "--strategy", "exact", "--llm", "off", "--runs", "1"])
    acc_exact = json.loads((exact / "accuracy.json").read_text())
    assert acc["recall"] > acc_exact["recall"], "the fuzzy + inference tiers must buy recall over exact string matching"
    assert (out / "unresolved.csv").read_text().count("\n") >= 2, "the exception list must be written, not summarised away"


def test_dashboard_is_self_contained(corpus, tmp_path):
    out = tmp_path / "dash"
    main(["run", "--data", str(corpus), "--out", str(out), "--llm", "off", "--runs", "1"])
    html = (out / "dashboard.html").read_text()
    assert "<svg" in html
    for forbidden in ('src="http', "href=\"http", "<link", "@import", "cdn."):
        assert forbidden not in html, f"dashboard pulls in {forbidden} - it must render offline in a sandboxed iframe"


def test_forecast_command_reports_horizons_and_bands(corpus, capsys):
    assert main(["forecast", "--data", str(corpus), "--horizon", "21", "--runs", "150"]) == 0
    printed = capsys.readouterr().out
    assert "21d" in printed and "P10" in printed and "worst" in printed


def test_forecast_backtest_runs_from_the_cli(corpus, capsys):
    rc = main(["forecast", "--data", str(corpus), "--backtest", "--forecast-origins", "3", "--bt-runs", "60", "--runs", "1"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "rolling-origin backtest" in out and "cashpilot" in out and "seasonal_naive" in out


def test_bench_command_writes_both_report_files(corpus, tmp_path):
    out = tmp_path / "benchdir"
    rc = main(["bench", "--data", str(corpus), "--reps", "1", "--out-dir", str(out), "--llm", "off"])
    assert rc == 0
    assert (out / "bench.md").exists() and (out / "bench.json").exists()
    data = json.loads((out / "bench.json").read_text())
    assert data["summary"]["full"]["recall"] > data["summary"]["exact"]["recall"], data["summary"]
    assert "caveats" in data and data["caveats"]


def test_demo_command_is_the_one_liner_the_readme_promises(tmp_path):
    data, out = tmp_path / "sample", tmp_path / "demo"
    rc = main(["demo", "--data", str(data), "--out", str(out), "--seed", "4242"])
    assert rc == 0
    assert (out / "dashboard.html").exists() and (out / "bench.md").exists()
    bench = json.loads((out / "bench.json").read_text())
    # the demo corpus is small on purpose; it still has to clear the 50-record bar
    assert bench["records"] >= 50, bench["records"]
    assert bench["summary"]["full"]["lines"] == bench["records"]
    assert bench["summary"]["full"]["auto_post_precision"] == 1.0


def test_doctor_explains_a_broken_environment(corpus, tmp_path, capsys):
    assert main(["doctor", "--data", str(corpus)]) == 0
    assert "python" in capsys.readouterr().out.lower()
    # a missing directory must be a clean message from main(), not a traceback
    assert main(["forecast", "--data", str(tmp_path / "nowhere")]) == 2


def test_ingest_reports_rows_it_could_not_read(tmp_path):
    """A bad row in someone's CSV becomes an exception in the run, never a silent skip."""
    (tmp_path / "bank_statement.csv").write_text(
        "line_id,txn_date,narration,amount_in,amount_out,utr,account\n"
        "L1,2026-09-01,NEFT CR ACME,1000.00,,UTR1,HDFC\n"
        "L2,not-a-date,NEFT CR ACME,2000.00,,UTR2,HDFC\n"
        "L3,2026-09-02,NEFT CR ACME,not-an-amount,,UTR3,HDFC\n"
    )
    from cashpilot.ingest import load_dataset

    ds = load_dataset(tmp_path)
    assert len(ds.lines) == 1
    assert len(ds.parse_failures) == 2, ds.parse_failures
    assert {f["reason"] for f in ds.parse_failures} == {"bad date", "zero amount"}
