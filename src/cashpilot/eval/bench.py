"""The benchmark the track's bar asks for: throughput + measured accuracy + an honest exception list.

`cashpilot bench` runs the same corpus through three strategies, N times each, and reports:

  exact        document-number regex + UTR equality only. This is what "just use a script" means.
  fuzzy_only   skip the deterministic evidence, similarity-search amounts and names only.
  full         the shipped ladder.

Timings are wall-clock medians over `reps` (fresh ingest each rep, because loading the CSVs is part
of what a real nightly run pays for). Accuracy is scored against the generator's planted ground
truth, and everything the engine cannot resolve stays on the exception list with a reason.
"""

from __future__ import annotations

import statistics
import time
from pathlib import Path

from ..config import Settings
from ..ingest import load_dataset
from ..recon.engine import Reconciler
from .accuracy import load_truth, score


def _one(data_dir: Path, settings: Settings, strategy: str, with_truth: bool):
    t0 = time.perf_counter()
    ds = load_dataset(data_dir)
    load_ms = (time.perf_counter() - t0) * 1000
    t1 = time.perf_counter()
    recon = Reconciler(ds, settings, strategy=strategy).run()
    recon_ms = (time.perf_counter() - t1) * 1000
    acc = None
    if with_truth:
        truth_path = data_dir / "truth_matches.csv"
        if truth_path.exists():
            acc = score(recon.matches, load_truth(truth_path), stats={**recon.stats, "timing_ms": {"load": load_ms, "reconcile": recon_ms}}, strategy=strategy)
    return ds, recon, acc, load_ms, recon_ms


def bench(
    data_dir: str | Path,
    settings: Settings | None = None,
    *,
    strategies: list[str] | None = None,
    reps: int = 3,
    with_forecast: bool = False,
    forecast_origins: int = 10,
    forecast_runs: int = 1500,
    forecast_step: int = 10,
    horizons: list[int] | None = None,
    seeded: bool = False,
    out_dir: Path | None = None,
    autogen: bool = False,
    scale: str = "medium",
    seed: int = 20260905,
) -> dict[str, object]:
    settings = settings or load_settings()
    data = Path(data_dir)
    if autogen and not (data / "bank_statement.csv").exists():
        from ..cli import cmd_generate
        import argparse

        print(f"[bench] {data} missing - generating (scale={scale}, seed={seed})")
        cmd_generate(argparse.Namespace(out=str(data), scale=scale, seed=seed, as_of=None, horizon=30, history_days=0))
    strategies = strategies or ["exact", "fuzzy_only", "full"]
    with_truth = (data / "truth_matches.csv").exists()
    result: dict[str, object] = {
        "data_dir": str(data),
        "reps": reps,
        "records": None,
        "strategies": {},
        "summary": {},
        "caveats": [
            "accuracy is measured on synthetic data whose messiness (truncated narrations, missing refs, "
            "short deductions, duplicates, lumpsums) was planted deliberately by src/cashpilot/synth/world.py; "
            "tools/generate_synthetic.py is a thin wrapper around it",
            "the forecaster is scored on rolling origins of the same synthetic world it was tuned on, so treat "
            "absolute errors as optimistic: skill vs the seasonal naive baseline, the top-25 settlement ranking "
            "and the band hit rate are the numbers that should survive contact with a real ledger",
            "daily MAPE on net movement is not a headline metric here - net change is a small difference of two "
            "large numbers and is zero on many days, which once made it read 3.3e9%",
            "timings are one core of a shared CI box; treat them as ratios, not as SLAs",
        ],
    }
    per: dict[str, dict[str, list[float]]] = {}
    cards: dict[str, dict[str, object]] = {}
    for strat in strategies:
        accs, times, loads, engines = [], [], [], []
        for _ in range(max(1, reps)):
            ds, recon, acc, load_ms, recon_ms = _one(data, settings, strat, with_truth)
            times.append(load_ms + recon_ms)
            loads.append(load_ms)
            engines.append(recon_ms)
            if acc:
                accs.append(acc)
        result["records"] = len(ds.lines)
        entry: dict[str, object] = {
            "matches": len(recon.matches),
            "exceptions": len(recon.exceptions),
            "auto_posted": len(recon.auto_posted),
            "ms_median": round(statistics.median(times), 2),
            "ms_min": round(min(times), 2),
            "ms_max": round(max(times), 2),
            "ingest_ms_median": round(statistics.median(loads), 2),
            "engine_ms_median": round(statistics.median(engines), 2),
            "lines_per_s_median": round(len(ds.lines) / (statistics.median(times) / 1000.0), 1),
            "lines_per_s_engine": round(len(ds.lines) / (statistics.median(engines) / 1000.0), 1),
        }
        if accs:
            def mean(attr: str) -> float:
                return round(statistics.fmean(getattr(a, attr) for a in accs), 4)

            entry.update(
                {
                    "lines": accs[0].lines_total,
                    "matchable": accs[0].lines_matchable,
                    "correct": round(statistics.fmean(a.correct for a in accs), 1),
                    "partial": round(statistics.fmean(a.partial for a in accs), 1),
                    "wrong": round(statistics.fmean(a.wrong for a in accs), 1),
                    "unmatched_but_matchable": round(statistics.fmean(a.unmatched_but_matchable for a in accs), 1),
                    "match_rate": mean("match_rate"),
                    "precision": mean("precision"),
                    "recall": mean("recall"),
                    "f1": mean("f1"),
                    "auto_post_precision": mean("auto_post_precision"),
                    "quarantine_accuracy": mean("quarantine_accuracy"),
                    "rupee_accuracy": mean("rupee_accuracy"),
                    "false_doc_assignments": round(statistics.fmean(a.false_doc_assignments for a in accs), 1),
                    "by_tier": accs[0].by_tier,
                }
            )
            cards[strat] = accs[0].to_dict()
        per[strat] = {"times": times}
        result["strategies"][strat] = entry
        result["summary"][strat] = {
            k: v
            for k, v in entry.items()
            if k
            in {
                "lines",
                "matchable",
                "matches",
                "correct",
                "partial",
                "wrong",
                "unmatched_but_matchable",
                "match_rate",
                "precision",
                "recall",
                "f1",
                "auto_posted",
                "auto_post_precision",
                "quarantine_accuracy",
                "rupee_accuracy",
                "ms_median",
                "ingest_ms_median",
                "engine_ms_median",
                "lines_per_s_median",
                "lines_per_s_engine",
            }
        }

    if with_truth:
        full = result["strategies"].get("full", {})
        exact = result["strategies"].get("exact", {})
        if full and exact and exact.get("recall"):
            result["summary"]["uplift"] = {
                "recall_full_minus_exact": round(float(full["recall"]) - float(exact["recall"]), 4),
                "correct_full_minus_exact": round(float(full["correct"]) - float(exact["correct"]), 1),
                "ms_ratio_vs_exact": round(float(full["ms_median"]) / max(1e-9, float(exact["ms_median"])), 3),
            }

    if with_forecast:
        from ..forecast.backtest import backtest, seeded_future_check

        ds = load_dataset(data)
        t0 = time.perf_counter()
        hz = horizons or [int(x) for x in settings.rules["cash_policy"]["forecast_horizons"]]
        bt = backtest(ds, settings, horizons=hz, n_origins=forecast_origins, runs=forecast_runs, step_days=forecast_step)
        bt_ms = round((time.perf_counter() - t0) * 1000, 1)
        fc: dict[str, object] = {**bt.to_dict(), "backtest_ms": bt_ms}
        if seeded:
            fc["seeded_future"] = {h: seeded_future_check(ds, settings, horizon=h) for h in hz}
        result["forecast"] = fc
        fm = fc["metrics"]  # type: ignore[index]
        hmax = max(hz)
        result["summary"]["forecast"] = {
            k: v
            for k, v in fm.items()  # type: ignore[union-attr]
            if k.endswith(f"_{hmax}") and ("skill" in k or "share_gross" in k or "bias_net" in k or "direction" in k)
        }

    if out_dir:
        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "bench.md").write_text(bench_md(result))
        (out_dir / "bench.json").write_text(__import__("json").dumps(result, indent=2, default=str))
    result["accuracy_cards"] = cards
    return result


def bench_md(result: dict[str, object]) -> str:
    from ..report import md_table

    rows = []
    for strat, e in result["strategies"].items():
        rows.append(
            {
                "strategy": strat,
                "records": e.get("lines", "-"),
                "matched": e.get("matches"),
                "correct": e.get("correct", "-"),
                "partial": e.get("partial", "-"),
                "wrong": e.get("wrong", "-"),
                "precision": e.get("precision", "-"),
                "recall": e.get("recall", "-"),
                "f1": e.get("f1", "-"),
                "auto_post": e.get("auto_posted"),
                "auto_precision": e.get("auto_post_precision", "-"),
                "rupee_acc": e.get("rupee_accuracy", "-"),
                "quarantine": e.get("quarantine_accuracy", "-"),
                "ingest ms": e.get("ingest_ms_median"),
                "engine ms": e.get("engine_ms_median"),
                "lines/s (engine)": e.get("lines_per_s_engine"),
            }
        )
    cols = [
        "strategy",
        "records",
        "matched",
        "correct",
        "partial",
        "wrong",
        "unmatched_but_matchable",
        "precision",
        "recall",
        "f1",
        "auto_post",
        "auto_precision",
        "rupee_acc",
        "quarantine",
        "ingest ms",
        "engine ms",
        "lines/s (engine)",
    ]
    fc = result.get("forecast") or {}
    out = [
        f"# Benchmark - {result['data_dir']}",
        "",
        f"_{result['reps']} repetitions per strategy; times include CSV ingest._",
        "",
        md_table(rows, cols),
    ]
    up = result["summary"].get("uplift")
    if up:
        out += [
            "## What the extra tiers buy",
            "",
            f"- recall {up['recall_full_minus_exact']:+.4f} and {up['correct_full_minus_exact']:+.1f} more correct lines vs the regex-only baseline",
            f"- cost: {up['ms_ratio_vs_exact']:.2f}x the wall time of the naive pass",
            "",
        ]
    if fc:
        m = fc.get("metrics", {})
        sf = fc.get("seeded_future", {})
        hz = fc.get("horizons", [7, 14, 30])
        hmax = max(hz)
        cols = ["model"] + [f"err% gross @{h}" for h in hz] + [f"MAE net @{hmax} (INR)", f"bias @{hmax} (INR)", "skill vs naive", "direction right"]
        rows = []
        for name in ("cashpilot", "seasonal_naive", "moving_avg", "due_date_sum"):
            row = {"model": name}
            for h in hz:
                row[f"err% gross @{h}"] = m.get(f"{name}_share_gross_{h}", "-")
            row[f"MAE net @{hmax} (INR)"] = f"{float(m.get(f'{name}_mae_net_{hmax}', 0) or 0) / 100:,.0f}"
            row[f"bias @{hmax} (INR)"] = f"{float(m.get(f'{name}_bias_net_{hmax}', 0) or 0) / 100:,.0f}"
            row["skill vs naive"] = f"{m.get(f'{name}_skill_vs_naive_pct_{hmax}', '-')}%"
            row["direction right"] = f"{m.get(f'{name}_direction_accuracy_pct_{hmax}', '-')}%"
            rows.append(row)
        out += [
            "## Forecast accuracy (rolling-origin backtest)",
            "",
            "Error of the **cumulative net cash movement** over the horizon, expressed as a share of the money that",
            "actually moved in the window (a percentage of *net* change explodes on weeks where inflow and outflow",
            "nearly cancel, which is why it is not the headline). Lower is better; the baselines run on the same",
            "origins and the same truncated history.",
            "",
            f"- {len(fc.get('origins', []))} origins ending {', '.join(str(x)[:10] for x in fc.get('origins', []))}",
            f"- backtest wall time {fc.get('backtest_ms')} ms (includes re-running reconciliation at every origin)",
            "",
            md_table(rows, cols),
            "",
            f"- P10-P90 band hit rate: " + ", ".join(f"{h}d {v}%" for h, v in (fc.get("band_coverage_pct") or {}).items()),
            f"- and the part no naive baseline can do at all: of the 25 documents the model expects to "
            f"settle inside a {hmax}-day window, {m.get(f'cashpilot_top25_hit_rate_pct_{hmax}', '-')}% really did, "
            f"against {m.get(f'by_amount_top25_hit_rate_pct_{hmax}', '-')}% when the same open book is ranked by size alone "
            f"({int(m.get('n_origins', 0))} origins)",
            "",
            
            "",
        ]
        if isinstance(sf, dict) and sf:
            out += [
                "### Secondary check against the generator's own plan",
                "",
                "The seeded world knows what it intends to happen next. This is an upper bound, not the headline:",
                "it shares assumptions with the generator, so it flatters the model.",
                "",
                md_table(
                    [
                        {
                            "horizon": f"{h}d",
                            "cumulative error": f"{v.get('cumulative_error_pct', '-')}%",
                            "err as share of gross": f"{v.get('error_share_of_gross_pct', '-')}%",
                            "mean daily MAE (INR)": f"{float(v.get('mean_daily_mae_paise', 0) or 0) / 100:,.0f}",
                            "days within 20% of plan (daily path)": f"{v.get('days_within_20pct', '-')}/{v.get('horizon_days', h)}",
                            "inflow, pred vs actual (INR)": f"{v.get('predicted_in_paise', 0) / 100:,.0f} vs {v.get('actual_in_paise', 0) / 100:,.0f}",
                        }
                        for h, v in sf.items()
                        if isinstance(v, dict)
                    ],
                    ["horizon", "cumulative error", "err as share of gross", "mean daily MAE (INR)", "days within 20% of plan (daily path)", "inflow, pred vs actual (INR)"],
                ),
                "",
                f"- caveat: {(sf.get(hz[0]) or {}).get('caveat', '')}" if sf.get(hz[0]) else "",
                "",
            ]
    out += [
        "",
        "## Caveats, before anyone asks",
        "",
        *[f"- {c}" for c in result.get("caveats", [])],
        "",
    ]
    return "\n".join(out)
