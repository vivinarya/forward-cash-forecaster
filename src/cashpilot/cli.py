"""`cashpilot` command line. Every subcommand is safe to run offline and is deterministic for a seed.

    cashpilot generate  --scale medium --seed 20260905      # synthetic books + ground truth
    cashpilot run       --data data/synthetic --out artifacts
    cashpilot bench     --data data/synthetic                # match-rate / speed / strategies
    cashpilot bench     --data data/synthetic --forecast     # + rolling-origin forecast accuracy
    cashpilot demo                                           # generate(sample) + run + bench in one go
    cashpilot doctor                                         # why is it not running
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from .config import REPO_ROOT, load_settings

SCALES = {
    "tiny": dict(history_days=45, n_customers=6, n_vendors=5, invoices_per_day=4.0, bills_per_day=3.0),
    "sample": dict(history_days=120, n_customers=14, n_vendors=10, invoices_per_day=5.0, bills_per_day=4.0),
    # medium is the published corpus: the numbers in README/docs reproduce from exactly these
    # parameters plus --seed 20260905 (byte-identical CSVs), so do not retune it casually
    "medium": dict(history_days=180, n_customers=30, n_vendors=22, invoices_per_day=7.0, bills_per_day=6.0),
    "large": dict(history_days=240, n_customers=55, n_vendors=40, invoices_per_day=9.0, bills_per_day=6.0),
}


def _paise(x: int) -> str:
    from .money import fmt_inr

    return fmt_inr(int(x))


def cmd_generate(args) -> int:
    from .synth.world import World

    out = Path(args.out)
    params = dict(SCALES[args.scale])
    if args.history_days:
        params["history_days"] = args.history_days
    w = World(seed=args.seed, as_of=date.fromisoformat(args.as_of) if args.as_of else date.today(), horizon_days=args.horizon, **params)
    w.build()
    meta = w.emit(out)
    print(
        f"generated {out}: {meta['bank_lines_history']} bank lines, {meta['documents']} documents, "
        f"{meta['customers']} customers / {meta['vendors']} vendors over {meta['history_start']} -> {meta['as_of']}"
    )
    print(f"ground truth for evaluation: {out / 'truth_matches.csv'} (never fed to the engine)")
    return 0


def cmd_run(args) -> int:
    from .report import write_all

    settings = load_settings(args.rules)
    if args.llm == "off":
        settings.llm_enabled = False
    res_data = _run(args, settings)
    written = write_all(res_data, args.out)
    m = res_data.manifest
    acc = res_data.accuracy
    print()
    print(f"cashpilot run  |  {m['counts']['bank_lines']} bank lines  |  {m['stages_ms']['total_ms']} ms  |  {args.out}/")
    if acc:
        print(
            f"  reconciled {acc.correct}/{acc.lines_matchable} matchable bank lines correctly "
            f"(precision {acc.precision:.3f}, recall {acc.recall:.3f}, F1 {acc.f1:.3f})"
        )
        print(
            f"  auto-posted {acc.auto_post_count} at {acc.auto_post_precision:.3f} precision; "
            f"{acc.unmatched_but_matchable} matchable lines refused + {acc.partial + acc.wrong} flagged as imperfect"
        )
    else:
        print(f"  {m['counts']['matches']} matches, {m['counts']['exceptions']} exceptions (no ground truth file: accuracy skipped)")
    rec = getattr(res_data, "recovery", None)
    if rec is not None:
        rt, bd, ar = rec.runtime, rec.batch_defects, rec.receivables
        print(
            f"  settlements: {rt['batches']} batches verified, {rt['batches_with_rupee_stake']} with money at stake; "
            f"claim value {rt['claim_value']}, credit recovery rate {rt['recovery_rate_pct']}%"
        )
        if bd.get("measured"):
            line = (
                f"    of {bd['planted_batches']} batches the generator corrupted: {bd['flagged_batches']} flagged "
                f"({bd['detection_rate_pct']}%), {bd['identified_value']} of {bd['planted_value']} identified "
                f"({bd['rupee_catch_rate_pct']}%)"
            )
            if ar.get("planted_docs"):
                line += (
                    f"; of {ar['planted_docs']} invoices paid short: {ar['surfaced_docs']} surfaced "
                    f"({ar['rupee_catch_rate_pct']}% of {ar['planted_value']})"
                )
            print(line)
        else:
            print("    defect catch rate: not measured - this corpus has no ground-truth ledger to divide by")
    if acc and acc.by_kind:
        parts = sorted(((float(v["correct_pct"]), k) for k, v in acc.by_kind.items()), reverse=True)
        print("  recall by class of line: " + ", ".join(f"{k} {pct}%" for pct, k in parts))
    h30 = res_data.cash.horizons.get(args.horizon or 30) or list(res_data.cash.horizons.values())[-1]
    print(
        f"  cash { _paise(res_data.cash.opening_paise)} today -> {_paise(h30['expected_closing_paise'])} in {h30['horizon_days']}d "
        f"(P10 {_paise(h30['p10_closing_paise'])})"
    )
    if h30["first_breach_day"]:
        print(f"  !! operating minimum breached on {h30['first_breach_day']} on the P10 path: need {_paise(h30['funding_need_p10_paise'])}")
    print(f"  llm: {m['llm']['calls']} calls, {m['llm']['ok']} accepted" + ("" if m['llm']['enabled'] else " (disabled: deterministic fallback used)"))
    print("\n  brief:\n")
    print("   " + res_data.brief["text"].replace("\n", "\n   "))
    print()
    if getattr(args, "backtest", False):
        from .forecast.backtest import backtest as rolling_backtest

        out_dir = Path(args.out)
        out_dir.mkdir(parents=True, exist_ok=True)
        bt = rolling_backtest(res_data.dataset, settings, n_origins=10, runs=600)
        (out_dir / "backtest.json").write_text(json.dumps(bt.to_dict(), indent=2, default=str))
        print("  rolling-origin forecast backtest (cumulative net cash error as a share of gross movement):")
        for name in ("cashpilot", "seasonal_naive", "moving_avg", "due_date_sum"):
            hs = bt.horizons or [7, 14, 30]
            last = hs[-1]
            shares = "  ".join("%dd %s%%" % (h, bt.metrics.get("%s_share_gross_%d" % (name, h))) for h in hs)
            skill = bt.metrics.get("%s_skill_vs_naive_pct_%d" % (name, last))
            hit = bt.metrics.get("%s_top25_hit_rate_pct_%d" % (name, last), "-")
            print(f"    {name:14s} {shares}   skill {skill}%  top-25 hit {hit}%")
        print(f"    written to {out_dir / 'backtest.json'}")
        print()
    for k, v in written.items():
        print(f"    {k:24s} {v}")
    return 0


def _run(args, settings):
    from .pipeline import run_books

    data = Path(args.data)
    if not data.exists() and args.autogen:
        print(f"[cashpilot] {data} not found - generating it first (seed {args.seed}, scale {args.scale})")
        ns = argparse.Namespace(
            out=str(data),
            scale=args.scale,
            seed=args.seed,
            as_of=getattr(args, "as_of", None),
            horizon=args.horizon or 30,
            history_days=0,
        )
        cmd_generate(ns)
    return run_books(
        data,
        settings=settings,
        strategy=args.strategy,
        horizon=args.horizon,
        runs=args.runs,
        with_truth=not args.no_truth,
        triage_limit=args.triage_limit,
    )


def cmd_bench(args) -> int:
    from .eval.bench import bench

    settings = load_settings(args.rules)
    if args.llm == "off":
        settings.llm_enabled = False
    out = bench(
        Path(args.data),
        settings,
        strategies=args.strategies.split(",") if args.strategies else None,
        reps=args.reps,
        with_forecast=args.forecast,
        forecast_origins=args.forecast_origins,
        forecast_runs=args.bt_runs,
        forecast_step=args.bt_step,
        horizons=[int(x) for x in args.horizons.split(",")] if args.horizons else None,
        seeded=args.seeded,
        out_dir=Path(args.out),
        autogen=args.autogen,
        scale=args.scale,
        seed=args.seed,
    )
    print(json.dumps(out["summary"], indent=2))
    if args.json:
        Path(args.json).write_text(json.dumps(out, indent=2, default=str))
        print(f"full benchmark written to {args.json}")
    return 0


def cmd_forecast(args) -> int:
    from .forecast.backtest import backtest, seeded_future_check
    from .ingest import load_dataset
    from .recon.engine import Reconciler
    from .forecast.engine import forecast as do_forecast

    settings = load_settings(args.rules)
    ds = load_dataset(args.data)
    recon = Reconciler(ds, settings, strategy="full").run()
    fut = do_forecast(ds, recon, settings, horizon=args.horizon, runs=args.runs)
    for h, v in sorted(fut.horizons.items()):
        print(
            f"{h:2d}d  expected {_paise(v['expected_closing_paise']):>16}  P10 {_paise(v['p10_closing_paise']):>16}  "
            f"P90 {_paise(v['p90_closing_paise']):>16}  worst {v['worst_day']} {_paise(v['worst_day_p10_closing_paise']):>16}  "
            f"breach {v['days_below_operating_minimum']}d"
        )
    if args.backtest:
        print("\nrolling-origin backtest (this is the honest number):")
        bt = backtest(ds, settings, n_origins=args.forecast_origins, runs=args.bt_runs, step_days=args.bt_step)
        print(f"  {'model':16s} {'err % of gross movement':>23s}   {'MAE of net change':>19s}   {'bias':>12s}  skill vs naive  direction")
        for name in ("cashpilot", "seasonal_naive", "moving_avg", "due_date_sum"):
            hs = bt.horizons or [7, 14, 30]
            share = {h: bt.metrics.get(f"{name}_share_gross_{h}") for h in hs}
            mae = {h: bt.metrics.get(f"{name}_mae_net_{h}") for h in hs}
            bias = bt.metrics.get(f"{name}_bias_net_30")
            skill = bt.metrics.get(f"{name}_skill_vs_naive_pct_30")
            direction = bt.metrics.get(f"{name}_direction_accuracy_pct_30")
            print(
                f"  {name:16s} {json.dumps(share):>23s}   "
                f"{json.dumps({k: _paise(int(v)) for k, v in mae.items() if v is not None}):>19s}   "
                f"{(_paise(int(bias)) if bias is not None else '-'):>12s}  {str(skill) + '%':>12s}  {str(direction) + '%':>9s}"
            )
        print(f"  origins used: {len(bt.origins)}   P10-P90 band hit rate: {json.dumps(bt.coverage)}")
    if args.seeded:
        print("\nseeded-future check (shares assumptions with the generator - secondary):")
        print(json.dumps(seeded_future_check(ds, settings, horizon=args.horizon), indent=2)[:1200])
    return 0


def cmd_sweep(args) -> int:
    from .eval.sweep import main as sweep_main

    return sweep_main(
        [
            "--scales",
            args.scales,
            "--seed",
            str(args.seed),
            "--as-of",
            args.as_of,
            "--json",
            args.json,
            "--md",
            args.md,
        ]
        + (["--keep"] if args.keep else [])
    )


def cmd_demo(args) -> int:
    """The 10-minute panel path: sample corpus -> whole run -> benchmark, one command.

    Sub-command namespaces are produced by the real parser rather than hand-built, so a new flag
    can never silently break the demo (it did, once).
    """
    data = Path(args.data)
    if not (data / "bank_statement.csv").exists() or args.regenerate:
        gen = ["generate", "--out", str(data), "--scale", "sample", "--seed", str(args.seed)]
        if getattr(args, "as_of", None):
            gen += ["--as-of", str(args.as_of)]
        cmd_generate(build_parser().parse_args(gen))
    run_ns = build_parser().parse_args(["run", "--data", str(data), "--out", args.out, "--llm", "off", "--runs", "1200"])
    cmd_run(run_ns)
    bench_ns = build_parser().parse_args(
        ["bench", "--data", str(data), "--out-dir", args.out, "--llm", "off", "--reps", "1", "--forecast", "--seeded", "--forecast-origins", "4", "--bt-runs", "600"]
    )
    cmd_bench(bench_ns)
    print(f"\ndemo done. open {args.out}/dashboard.html in a browser, and {args.out}/bench.md for the numbers.")
    return 0


def cmd_doctor(args) -> int:
    import importlib.util
    import platform

    ok = True
    print(f"python {sys.version.split()[0]} on {platform.platform()}")
    if sys.version_info < (3, 10):
        print("  !! needs python >= 3.10 (uses match-free but `X | None` typing and dataclass slots)")
        ok = False
    for mod in ("numpy", "pytest"):
        found = importlib.util.find_spec(mod) is not None
        print(f"  {'ok ' if found else 'MISSING'} {mod}")
        if mod == "numpy":
            ok = ok and found
    for rel in ("data/synthetic/bank_statement.csv", "data/sample/bank_statement.csv", "config/recon_rules.json", "config/fee_schedule.json"):
        p = REPO_ROOT / rel
        print(f"  {'ok ' if p.exists() else 'absent '} {rel}" + (f" ({len(p.read_text().splitlines()) - 1} rows)" if p.exists() and p.suffix == ".csv" else ""))
    data = Path(args.data)
    if data.exists():
        from .ingest import load_dataset

        ds = load_dataset(data)
        print(f"  dataset {data}: {len(ds.lines)} lines, {len(ds.docs)} docs, {len(ds.settlements)} settlements, {len(ds.parse_failures)} parse failures")
    else:
        print(f"  dataset {data}: not generated yet -> `python -m cashpilot generate --out {data}` or `make sample`")
    env = load_settings()
    print(f"  llm: {'on' if env.llm_enabled else 'off'} ({'key present' if env.llm_api_key else 'no key - deterministic fallback'})")
    for w in env.warnings:
        print(f"  note: {w}")
    print("doctor:", "ready" if ok else "problems above")
    return 0 if ok else 1


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="cashpilot", description="Finance back-office agent: reconcile, verify, forecast.")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p, data=True):
        if data:
            p.add_argument("--data", default=str(REPO_ROOT / "data" / "synthetic"))
        p.add_argument("--rules", default=None, help="path to recon rules JSON (default config/recon_rules.json)")
        p.add_argument("--llm", choices=["auto", "off"], default="auto")
        return p

    g = sub.add_parser("generate", help="write the synthetic books + ground truth")
    g.add_argument("--out", default=str(REPO_ROOT / "data" / "synthetic"))
    g.add_argument("--scale", choices=list(SCALES), default="medium")
    g.add_argument("--seed", type=int, default=20260905)
    g.add_argument("--as-of", dest="as_of", default=None, help="ISO date, default today")
    g.add_argument("--horizon", type=int, default=30)
    g.add_argument("--history-days", dest="history_days", type=int, default=0)
    g.set_defaults(fn=cmd_generate)

    r = common(sub.add_parser("run", help="the whole back-office run: reconcile, verify, triage, forecast, report"))
    r.add_argument("--out", default=str(REPO_ROOT / "artifacts"))
    r.add_argument("--strategy", choices=["exact", "fuzzy_only", "full"], default="full")
    r.add_argument("--horizon", type=int, default=None)
    r.add_argument("--runs", type=int, default=None)
    r.add_argument("--triage-limit", dest="triage_limit", type=int, default=60)
    r.add_argument("--no-truth", dest="no_truth", action="store_true", help="ignore truth_matches.csv")
    r.add_argument("--backtest", dest="backtest", action="store_true", help="also run the rolling-origin forecast backtest (slower)")
    r.add_argument("--autogen", action="store_true", default=True, help="generate the dataset if missing (default on)")
    r.add_argument("--scale", choices=list(SCALES), default="medium")
    r.add_argument("--seed", type=int, default=20260905)
    r.set_defaults(fn=cmd_run)

    b = common(sub.add_parser("bench", help="measured accuracy / speed across strategies"))
    b.add_argument("--strategies", default="exact,fuzzy_only,full")
    b.add_argument("--reps", type=int, default=3)
    b.add_argument("--forecast", action="store_true", help="also run the rolling-origin forecast backtest")
    b.add_argument("--forecast-origins", dest="forecast_origins", type=int, default=10)
    b.add_argument("--bt-runs", dest="bt_runs", type=int, default=1500, help="Monte Carlo paths per origin")
    b.add_argument("--bt-step", dest="bt_step", type=int, default=10, help="days between rolling origins")
    b.add_argument("--horizons", dest="horizons", default="", help="comma list for the forecast backtest")
    b.add_argument("--seeded", dest="seeded", action="store_true", help="also score against the generator's own future plan")
    b.add_argument("--json", default=None, help="write full benchmark json here")
    b.add_argument("--out-dir", dest="out", default=str(REPO_ROOT / "artifacts"), help="directory for bench.md / bench.json")
    b.add_argument("--autogen", action="store_true", default=True)
    b.add_argument("--scale", choices=list(SCALES), default="medium")
    b.add_argument("--seed", type=int, default=20260905)
    b.set_defaults(fn=cmd_bench)

    f = common(sub.add_parser("forecast", help="forward cash only"))
    f.add_argument("--horizon", type=int, default=30)
    f.add_argument("--runs", type=int, default=None)
    f.add_argument("--backtest", action="store_true")
    f.add_argument("--seeded", action="store_true", help="also compare against the generator's plan")
    f.add_argument("--forecast-origins", dest="forecast_origins", type=int, default=6)
    f.add_argument("--bt-runs", dest="bt_runs", type=int, default=200)
    f.add_argument("--bt-step", dest="bt_step", type=int, default=10, help="days between rolling origins")
    f.set_defaults(fn=cmd_forecast)

    sw = common(sub.add_parser("sweep", help="accuracy / speed / recovery across five corpus sizes"))
    sw.add_argument("--scales", default="tiny,sample,medium,large,xl")
    sw.add_argument("--seed", type=int, default=20260905)
    sw.add_argument("--as-of", dest="as_of", default="2026-09-05")
    sw.add_argument("--json", default=str(REPO_ROOT / "artifacts" / "scale_sweep.json"))
    sw.add_argument("--md", default=str(REPO_ROOT / "artifacts" / "scale_sweep.md"))
    sw.add_argument("--keep", action="store_true", help="leave the generated corpora in artifacts/_sweep")
    sw.set_defaults(fn=cmd_sweep)

    d = sub.add_parser("demo", help="sample data + full run + bench, from a clean checkout")
    d.add_argument("--data", default=str(REPO_ROOT / "data" / "sample"))
    d.add_argument("--out", default=str(REPO_ROOT / "artifacts" / "demo"))
    d.add_argument("--seed", type=int, default=4242)
    d.add_argument(
        "--as-of",
        dest="as_of",
        default="2026-09-05",
        help="as-of date used if the sample corpus has to be generated (default: the date the "
        "committed data/sample was built on, so a clean checkout reproduces the documented numbers)",
    )
    d.add_argument("--regenerate", action="store_true")
    d.set_defaults(fn=cmd_demo)

    dd = sub.add_parser("doctor", help="environment + data sanity check")
    dd.add_argument("--data", default=str(REPO_ROOT / "data" / "synthetic"))
    dd.set_defaults(fn=cmd_doctor)
    return ap


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except FileNotFoundError as exc:
        print(f"cashpilot: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("interrupted", file=sys.stderr)
        return 130


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
