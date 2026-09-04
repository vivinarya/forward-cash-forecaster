import pathlib, ast
c = pathlib.Path("src/cashpilot/cli.py"); s = c.read_text()

# --- demo: build namespaces through the parser so defaults can never drift out of sync ---
old_demo_start = s.index("def cmd_demo(args) -> int:")
old_demo_end = s.index("def cmd_doctor(args) -> int:")
new_demo = '''def cmd_demo(args) -> int:
    """The 10-minute panel path: sample corpus -> whole run -> benchmark, one command.

    Sub-command namespaces are produced by the real parser rather than hand-built, so a new flag
    can never silently break the demo (it did, once).
    """
    data = Path(args.data)
    if not (data / "bank_statement.csv").exists() or args.regenerate:
        cmd_generate(build_parser().parse_args(["generate", "--out", str(data), "--scale", "sample", "--seed", str(args.seed)]))
    run_ns = build_parser().parse_args(["run", "--data", str(data), "--out", args.out, "--llm", "off", "--runs", "1200"])
    cmd_run(run_ns)
    bench_ns = build_parser().parse_args(
        ["bench", "--data", str(data), "--out-dir", args.out, "--llm", "off", "--reps", "1", "--forecast", "--seeded", "--forecast-origins", "4", "--bt-runs", "600"]
    )
    cmd_bench(bench_ns)
    print(f"\\ndemo done. open {args.out}/dashboard.html in a browser, and {args.out}/bench.md for the numbers.")
    return 0


'''
s = s[:old_demo_start] + new_demo + s[old_demo_end:]

# --- bench: --out-dir semantics ---
s = s.replace('''    b.add_argument("--json", default=None, help="write full benchmark json here")
    b.add_argument("--out", default=None, help="write benchmark markdown here")''',
'''    b.add_argument("--json", default=None, help="write full benchmark json here")
    b.add_argument("--out-dir", dest="out", default=str(REPO_ROOT / "artifacts"), help="directory for bench.md / bench.json")''')
s = s.replace('''        out_dir=Path(args.out) if args.out else Path("artifacts"),''', '''        out_dir=Path(args.out),''')

# --- run: optional rolling-origin backtest written next to the reports ---
s = s.replace('''    r.add_argument("--no-truth", dest="no_truth", action="store_true", help="ignore truth_matches.csv")''',
'''    r.add_argument("--no-truth", dest="no_truth", action="store_true", help="ignore truth_matches.csv")
    r.add_argument("--backtest", dest="backtest", action="store_true", help="also run the rolling-origin forecast backtest (slower)")''')
s = s.replace('''    print("\\n  brief:\\n")
    print("   " + res_data.brief["text"].replace("\\n", "\\n   "))
    print()''',
'''    print("\\n  brief:\\n")
    print("   " + res_data.brief["text"].replace("\\n", "\\n   "))
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
            print(
                f"    {name:14s} "
                + "  ".join(f"{h}d {bt.metrics.get(f'{name}_share_gross_{h}')}%" for h in hs)
                + f"   skill {bt.metrics.get(f'{name}_skill_vs_naive_pct_{hs[-1]')}%  "
                f"top-25 hit {bt.metrics.get(f'{name}_top25_hit_rate_pct_{hs[-1]}', '-')}%"
            )
        print(f"    written to {out_dir / 'backtest.json'}")
        print()''')
# cmd_run writes artifacts before the backtest; make sure backtest.json is listed
s = s.replace('''    for k, v in written.items():
        print(f"    {k:24s} {v}")
    return 0''', '''    for k, v in written.items():
        print(f"    {k:24s} {v}")
    return 0''')
c.write_text(s)
ast.parse(s)

# report/RunResult needs `dataset` for the backtest - check
p2 = pathlib.Path("src/cashpilot/pipeline.py"); t = p2.read_text()
print("has dataset field:", "dataset" in t.split("class RunResult")[1][:800])
print(t[t.index("@dataclass"):t.index("def run_books")])
