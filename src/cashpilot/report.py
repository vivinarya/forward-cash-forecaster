"""Report layer: CSVs for machines, markdown for the repo, one self-contained HTML dashboard.

No template engine: this is string building over small dicts, and a dependency for that is the
kind of weight that makes a repo unrunnable in someone else's environment at 11pm.
"""

from __future__ import annotations

import csv
import json
from datetime import date
from html import escape
from pathlib import Path

from .money import fmt_inr

BANK_SIDE = {"bank_line", "input_row"}


def _write_csv(path: Path, rows: list[dict[str, object]]) -> None:
    if not rows:
        path.write_text("no_rows\n")
        return
    fields: list[str] = []
    for r in rows:
        for k in r:
            if k not in fields:
                fields.append(k)
    with path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)


def md_table(rows: list[dict[str, object]], cols: list[str] | None = None, *, limit: int = 25) -> str:
    if not rows:
        return "_none_\n"
    cols = cols or list(rows[0])
    head = "| " + " | ".join(cols) + " |"
    sep = "|" + "|".join("---" for _ in cols) + "|"
    body = []
    for r in rows[:limit]:
        body.append("| " + " | ".join(str(r.get(c, ""))[:70] for c in cols) + " |")
    tail = [f"\n_{len(rows) - limit} more rows in the CSV, not shown._"] if len(rows) > limit else []
    return "\n".join([head, sep, *body] + tail) + "\n"


def write_all(res, out_dir: str | Path) -> dict[str, str]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    written: dict[str, str] = {}

    # ---------------- CSVs ----------------
    match_rows = [
        {
            "line_id": m.line_id,
            "document_ids": ";".join(m.doc_ids),
            "tier": m.tier,
            "score": m.score,
            "confidence": m.confidence,
            "auto_post": "yes" if m.auto_post else "no",
            "amount_diff_paise": m.amount_diff_paise,
            "evidence": m.evidence,
            "n_docs": len(m.doc_ids),
        }
        for m in sorted(res.recon.matches, key=lambda x: (x.confidence, x.tier))
    ]
    _write_csv(out / "matches.csv", match_rows)
    written["matches.csv"] = f"{len(match_rows)} rows"

    line_by_id = {ln.line_id: ln for ln in res.dataset.lines}
    exc_rows = []
    for e in res.recon.exceptions:
        t = res.triage_table.get(e.ref_id, {})
        ln = line_by_id.get(e.ref_id)
        exc_rows.append(
            {
                **e.as_row(),
                "txn_date": ln.txn_date.isoformat() if ln else "",
                "narration": (ln.narration if ln else "")[:160],
                "amount": fmt_inr(e.amount_paise),
                "triage_category": t.get("category", ""),
                "triage_owner": t.get("owner", ""),
                "triage_action": t.get("action", ""),
                "triage_confidence": t.get("confidence", ""),
                "llm_status": t.get("llm_status", ""),
                "question_to_ask": t.get("question_to_ask", ""),
                "group_key": t.get("group_key", ""),
            }
        )
    _write_csv(out / "exceptions.csv", exc_rows)
    written["exceptions.csv"] = f"{len(exc_rows)} rows"

    _write_csv(out / "settlements.csv", [r.as_row() for r in res.verify_rows])
    written["settlements.csv"] = f"{len(res.verify_rows)} rows"
    _write_csv(out / "forecast.csv", res.cash.to_rows())
    written["forecast.csv"] = f"{len(res.cash.days)} rows"
    _write_csv(out / "party_behaviour.csv", res.cash.behaviour_table)
    written["party_behaviour.csv"] = f"{len(res.cash.behaviour_table)} rows"

    # The collections list a treasurer actually works from: what is overdue, by how much, and what
    # the model still expects from it. Sorted by money at risk, not by age.
    as_of = res.cash.as_of
    expect = res.cash.expected_by_doc
    aged = []
    for d in res.dataset.invoices:
        if d.outstanding_paise <= 0:
            continue
        days_overdue = (as_of - d.due_date).days
        if days_overdue < 0:
            continue
        aged.append(
            {
                "document_no": d.number,
                "counterparty": d.counterparty,
                "party_code": d.extra.get("counterparty_code", ""),
                "due_date": d.due_date.isoformat(),
                "days_overdue": days_overdue,
                "bucket": ("1-30" if days_overdue <= 30 else "31-60" if days_overdue <= 60 else "61-90" if days_overdue <= 90 else "91-180" if days_overdue <= 180 else "180+"),
                "outstanding_inr": round(d.outstanding_paise / 100, 2),
                "expected_in_window_inr": round(expect.get(d.number, 0) / 100, 2),
                "status": d.status,
            }
        )
    aged.sort(key=lambda r: -r["outstanding_inr"])
    _write_csv(out / "aged_receivables.csv", aged)
    written["aged_receivables.csv"] = f"{len(aged)} rows"

    unresolved = list(res.accuracy.unresolved) if res.accuracy else []
    _write_csv(out / "unresolved.csv", unresolved)
    written["unresolved.csv"] = f"{len(unresolved)} rows"

    (out / "run_manifest.json").write_text(json.dumps(res.manifest, indent=2, default=str))
    written["run_manifest.json"] = "stage timings, versions, llm usage"
    if res.accuracy:
        (out / "accuracy.json").write_text(json.dumps(res.accuracy.to_dict(), indent=2, default=str))
        written["accuracy.json"] = "measured score card"

    # ---------------- markdown ----------------
    (out / "reconciliation.md").write_text(recon_md(res))
    (out / "settlements.md").write_text(settlement_md(res))
    (out / "forecast.md").write_text(forecast_md(res))
    (out / "brief.md").write_text(brief_md(res))
    (out / "INDEX.md").write_text(index_md(res, written))
    for name in ("reconciliation.md", "settlements.md", "forecast.md", "brief.md", "INDEX.md"):
        written[name] = "report"

    (out / "dashboard.html").write_text(dashboard_html(res))
    written["dashboard.html"] = "self-contained view (no network, no CDN)"
    return written


# --------------------------------------------------------------------------- markdown reports
def _pct(x: float) -> str:
    return f"{100.0 * float(x):.1f}%"


def recon_md(res) -> str:
    acc = res.accuracy
    st = res.recon.stats
    unresolved = list(acc.unresolved) if acc else []
    tier_rows = [
        {"tier": k, "matches": v.get("matches", 0), "correct": v.get("correct", 0), "wrong": v.get("wrong", 0)}
        for k, v in sorted((acc.by_tier if acc else {}).items(), key=lambda kv: -kv[1]["matches"])
    ]
    exc_counts: dict[str, int] = {}
    for e in res.recon.exceptions:
        exc_counts[e.code] = exc_counts.get(e.code, 0) + 1
    exc_rows = [{"code": k, "count": v} for k, v in sorted(exc_counts.items(), key=lambda kv: -kv[1])]
    tri = res.triage_stats
    lines = [
        "# Reconciliation run",
        "",
        f"_as of {res.dataset.as_of} - strategy `{st.get('strategy')}` - {st.get('bank_lines')} bank lines vs "
        f"{st.get('documents')} documents_",
        "",
        "## Measured result",
        "",
        md_table(
            [
                {
                    "metric": "records",
                    "value": st.get("bank_lines"),
                    "note": "bank statement lines in the run",
                },
                {"metric": "matched", "value": acc.lines_matched if acc else st.get("matched_lines"), "note": f"match rate {_pct(acc.match_rate) if acc else 'n/a'}"},
                {"metric": "correct (exact doc set)", "value": acc.correct if acc else "-", "note": f"precision {_pct(acc.precision)}, recall {_pct(acc.recall)}, F1 {acc.f1}" if acc else ""},
                {"metric": "partial / wrong", "value": f"{acc.partial} / {acc.wrong}" if acc else "-", "note": "partial = subset of the true doc set"},
                {"metric": "auto-posted", "value": st.get("auto_posted"), "note": f"auto-post precision {_pct(acc.auto_post_precision) if acc else 'n/a'}"},
                {"metric": "exceptions raised", "value": st.get("exceptions"), "note": "every unresolved line, typed"},
                {
                    "metric": "quarantine accuracy",
                    "value": _pct(acc.quarantine_accuracy) if acc else "-",
                    "note": "charges/interest/duplicates correctly left unposted",
                },
                {"metric": "rupee accuracy", "value": _pct(acc.rupee_accuracy) if acc else "-", "note": "share of matchable rupees posted to the right document"},
            ],
            ["metric", "value", "note"],
        ),
        "## Where the matches came from",
        "",
        md_table(tier_rows, ["tier", "matches", "correct", "wrong"]),
        "## Exception mix",
        "",
        md_table(exc_rows, ["code", "count"]),
        "## Triage (the only AI step)",
        "",
        f"- deterministic pre-classification: {tri.get('deterministic_classified')} exceptions",
        f"- LLM attempted / accepted / discarded: {tri.get('llm_attempted')} / {tri.get('llm_accepted')} / {tri.get('llm_discarded')}"
        + (f" _(skipped: {tri.get('skipped_reason')})_" if tri.get("skipped_reason") else ""),
        f"- duplicate-root-cause groupings: {tri.get('groupings')}",
        f"- LLM wall time: {tri.get('ms')} ms; usage: `{json.dumps(tri.get('llm_usage', {}), default=str)}`",
        "",
        "## Top unresolved bank lines",
        "",
        md_table(
            [
                {
                    "line": u["line_id"],
                    "why": u["why"],
                    "amount": fmt_inr(u.get("amount_paise", 0)),
                    "claimed": ",".join(u.get("claimed", []) or []) or "-",
                    "truth": ",".join(u.get("truth_docs", []) or []) or "-",
                    "narration": str(u.get("narration", ""))[:60],
                }
                for u in unresolved[:15]
            ],
            ["line", "why", "amount", "claimed", "truth", "narration"],
            limit=15,
        ),
        "",
        "Full list: `unresolved.csv` / `exceptions.csv`.",
        "",
    ]
    return "\n".join(lines)


def settlement_md(res) -> str:
    v = res.verify_summary
    flagged = [r.as_row() for r in res.verify_rows if r.flags]
    return "\n".join(
        [
            "# Razorpay settlement verification",
            "",
            f"- batches checked: **{v['batches']}**, flagged: **{v['batches_flagged']}** ({_pct(v['flag_rate'])})",
            f"- gross captured: {fmt_inr(v['gross_paise'])}, commission billed: {fmt_inr(v['declared_fee_paise'])}"
            f" (effective MDR {v['effective_mdr_pct']}%)",
            f"- commission re-derived from the rate card: {fmt_inr(v['expected_fee_paise'])}",
            f"- **recoverable overbilling: {fmt_inr(v['recoverable_paise'])}**",
            f"- unexplained credit gaps vs bank feed: {fmt_inr(v['credit_gap_paise'])}",
            f"- refunds matched into batches: {fmt_inr(v['refunds_matched_paise'])}",
            f"- payments older than the settlement window with no batch: {v['unsettled_payments']}",
            "",
            "## Flagged batches",
            "",
            md_table(
                [
                    {
                        "settlement": r["settlement_id"],
                        "date": r["settled_on"],
                        "gross": fmt_inr(r["gross_paise"]),
                        "fee_diff": fmt_inr(r["fee_diff_paise"]),
                        "credit_gap": fmt_inr(r["credit_gap_paise"]),
                        "flags": r["flags"],
                    }
                    for r in flagged
                ],
                ["settlement", "date", "gross", "fee_diff", "credit_gap", "flags"],
            ),
            "",
            "Arithmetic only: gross - MDR - TMN - GST on fees - TDS - refunds = net credited, per batch,",
            "against `config/fee_schedule.json`. No model involvement, by design.",
            "",
        ]
    )


def forecast_md(res) -> str:
    c = res.cash
    rows = [
        {
            "day": d.day.isoformat(),
            "wd": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][d.day.weekday()],
            "in": fmt_inr(d.expected_in_paise),
            "out": fmt_inr(d.expected_out_paise),
            "closing": fmt_inr(d.closing_paise),
            "p10": fmt_inr(d.closing_lo_paise),
            "p90": fmt_inr(d.closing_hi_paise),
        }
        for d in c.days
    ]
    hor = [
        {
            "horizon": f"{h}d",
            "expected_closing": fmt_inr(v["expected_closing_paise"]),
            "p10": fmt_inr(v["p10_closing_paise"]),
            "p90": fmt_inr(v["p90_closing_paise"]),
            "worst_day": v["worst_day"],
            "worst_p10": fmt_inr(v["worst_day_p10_closing_paise"]),
            "below_min_days": v["days_below_operating_minimum"],
            "funding_need": fmt_inr(v["funding_need_p10_paise"]),
            "prob_below_min": f"{100.0 * float(v['probability_below_operating_minimum']):.1f}%",
        }
        for h, v in sorted(c.horizons.items())
    ]
    st = c.stats
    return "\n".join(
        [
            f"# Forward cash forecast - {st['horizon_days']} days from {st['as_of']}",
            "",
            "## Decision view",
            "",
            md_table(hor, ["horizon", "expected_closing", "p10", "p90", "worst_day", "worst_p10", "below_min_days", "funding_need", "prob_below_min"]),
            "## Components",
            "",
            md_table(
                [
                    {"component": "open receivables (AR)", "value": f"{st['open_ar_docs']} docs, {fmt_inr(st['open_ar_paise'])}"},
                    {"component": "open payables (AP)", "value": f"{st['open_ap_docs']} docs, {fmt_inr(st['open_ap_paise'])}"},
                    {"component": "expected in-window receipts", "value": fmt_inr(st["expected_in_paise"])},
                    {"component": "expected out-window payments", "value": fmt_inr(st["expected_out_paise"])},
                    {"component": "gateway collections projected", "value": f"{fmt_inr(st['gateway_projected_in_paise'])} at net ratio {st['gateway_net_ratio']}, T+{st['gateway_lag_days']}"},
                    {"component": "unbooked residual/day", "value": f"+{fmt_inr(st['misc_daily_mean_in_paise'])} / -{fmt_inr(st['misc_daily_mean_out_paise'])}"},
                    {"component": "MC paths", "value": f"{st['mc_runs']} in {st['mc_ms']} ms"},
                    {
                        "component": "learned behaviour",
                        "value": f"{st['learnt']['parties_profiled']} counterparties profiled; median AR delay {st['learnt']['median_delay_ar_days']}d, AP {st['learnt']['median_delay_ap_days']}d",
                    },
                    {"component": "aged docs outside delay support", "value": st.get("aged_docs_out_of_support", 0)},
                ],
                ["component", "value"],
            ),
            "## Day by day",
            "",
            md_table(rows, ["day", "wd", "in", "out", "closing", "p10", "p90"], limit=31),
            "",
            f"Operating minimum assumed: {fmt_inr(st['operating_minimum_paise'])} (config `cash_policy.minimum_cash_paise`).",
            "",
        ]
    )


def brief_md(res) -> str:
    b = res.brief
    return "\n".join(
        [
            "# Daily cash brief",
            "",
            f"_generated {date.today().isoformat}; source: {b.get('source')} (deterministic numbers, language model used only to phrase them)_",
            "",
            f"> {b.get('text','')}",
            "",
            "## Evidence behind the sentences",
            "",
            md_table([{"figure": k, "value": v} for k, v in b.get("evidence", {}).items()], ["figure", "value"]),
            "",
            "The prompt and the validation rule (no digit may appear that is not in the evidence block) are",
            "in `src/cashpilot/ai/narrative.py`; the brief is discarded, not edited, if that check fails.",
            "",
        ]
    )


def index_md(res, written: dict[str, str]) -> str:
    m = res.manifest
    acc = res.accuracy
    return "\n".join(
        [
            "# Cashpilot run - " + str(m["as_of"]),
            "",
            "Finance back-office agent: reconcile the bank feed against AR/AP, verify gateway settlements",
            "to the paisa, forecast cash 7-30 days ahead, and hand a human a typed exception list.",
            "",
            "## Headline",
            "",
            md_table(
                [
                    {"result": "records reconciled", "value": m["counts"]["bank_lines"]},
                    {"result": "matched correctly", "value": f"{acc.correct} ({_pct(acc.precision)} of everything matched)" if acc else m["counts"]["matches"]},
                    {"result": "auto-posted without a human", "value": f"{m['counts']['auto_posted']} at {_pct(acc.auto_post_precision)} precision" if acc else m["counts"]["auto_posted"]},
                    {"result": "exceptions needing a human", "value": m["counts"]["exceptions"]},
                    {"result": "settlement batches flagged", "value": res.verify_summary.get("batches_flagged")},
                    {"result": "forecast horizon", "value": f"{res.cash.stats['horizon_days']} days, P10/P50/P90 over {res.cash.stats['mc_runs']} paths"},
                    {"result": "end-to-end wall time", "value": f"{m['stages_ms']['total_ms']} ms"},
                    {"result": "LLM", "value": "enabled" if m["llm"]["enabled"] else f"off ({m['llm'].get('errors') or 'no key'}) - deterministic fallback used"},
                ],
                ["result", "value"],
            ),
            "## Outputs",
            "",
            md_table([{"file": k, "contents": v} for k, v in written.items()], ["file", "contents"]),
            "",
            "## Stage timings",
            "",
            md_table(
                [{"stage": k, "ms": v} for k, v in m["stages_ms"].items()],
                ["stage", "ms"],
            ),
            "",
            "Reproduce with `make demo` (or `python -m cashpilot run --data data/synthetic --out artifacts`).",
            "",
        ]
    )


# --------------------------------------------------------------------------- HTML dashboard
def _sparkline(days, *, width=860, height=250) -> str:
    if not days:
        return ""
    lo = min(d.closing_lo_paise for d in days)
    hi = max(d.closing_hi_paise for d in days)
    rng = (hi - lo) or 1
    pad = 28

    def x(i: int) -> float:
        return pad + (width - 2 * pad) * (i / max(1, len(days) - 1))

    def y(v: int) -> float:
        return height - pad - (height - 2 * pad) * ((v - lo) / rng)

    band = " ".join(f"{x(i):.1f},{y(d.closing_hi_paise):.1f}" for i, d in enumerate(days))
    band2 = " ".join(f"{x(i):.1f},{y(d.closing_lo_paise):.1f}" for i, d in reversed(list(enumerate(days))))
    line = " ".join(f"{x(i):.1f},{y(d.closing_paise):.1f}" for i, d in enumerate(days))
    p50 = " ".join(f"{x(i):.1f},{y(d.closing_p50_paise or d.closing_paise):.1f}" for i, d in enumerate(days))
    ticks = "".join(
        f'<text x="{x(i):.1f}" y="{height - 6}" font-size="10" fill="#7d8794" text-anchor="middle">{days[i].day:%d %b}</text>'
        for i in range(0, len(days), max(1, len(days) // 7))
    )
    grid = "".join(
        f'<line x1="{pad}" x2="{width - pad}" y1="{y(v):.1f}" y2="{y(v):.1f}" stroke="#eef1f5"/>'
        for v in (lo, lo + rng * 0.5, hi)
    )
    return f"""<svg viewBox="0 0 {width} {height}" role="img" aria-label="forecast cash path">
  {grid}
  <polygon points="{band} {band2}" fill="#2563eb" opacity="0.13"/>
  <polyline points="{line}" fill="none" stroke="#1d4ed8" stroke-width="2.2"/>
  <polyline points="{p50}" fill="none" stroke="#0f9d58" stroke-width="1.2" stroke-dasharray="4 3"/>
  {ticks}
  <text x="{pad}" y="16" font-size="11" fill="#7d8794">P10-P90 band (shaded) - expected path (solid) - MC median (dashed)</text>
  <text x="{pad}" y="{height - 62}" font-size="10" fill="#7d8794">{fmt_inr(hi)}</text>
  <text x="{pad}" y="{height - 24}" font-size="10" fill="#7d8794">{fmt_inr(lo)}</text>
</svg>"""


def dashboard_html(res) -> str:
    m = res.manifest
    acc = res.accuracy
    c = res.cash
    hor = sorted(c.horizons.items())
    cards = []
    if acc:
        cards += [
            ("records", f"{m['counts']['bank_lines']}", "bank lines in the run"),
            ("matched correctly", f"{acc.correct}", f"precision {_pct(acc.precision)} / recall {_pct(acc.recall)}"),
            ("auto-posted", f"{m['counts']['auto_posted']}", f"precision {_pct(acc.auto_post_precision)}"),
            ("for a human", f"{m['counts']['exceptions']}", "typed exceptions, 0 silent drops"),
        ]
    cards += [
        ("cash today", fmt_inr(c.opening_paise), f"as of {m['as_of']}"),
        ("in 30 days", fmt_inr(hor[-1][1]["expected_closing_paise"]) if hor else "-", f"P10 {fmt_inr(hor[-1][1]['p10_closing_paise'])}" if hor else ""),
        ("settlement flags", f"{res.verify_summary.get('batches_flagged', 0)}", f"{fmt_inr(res.verify_summary.get('recoverable_paise', 0))} recoverable"),
        ("wall time", f"{m['stages_ms']['total_ms']} ms", "ingest + reconcile + verify + forecast"),
    ]
    card_html = "".join(
        f'<div class="card"><div class="k">{escape(str(k))}</div><div class="v">{escape(str(v))}</div><div class="s">{escape(str(s))}</div></div>'
        for k, v, s in cards
    )
    rows = "".join(
        f"<tr><td>{escape(d.day.isoformat())}</td><td class='r'>{escape(fmt_inr(d.expected_in_paise))}</td>"
        f"<td class='r'>{escape(fmt_inr(d.expected_out_paise))}</td><td class='r b'>{escape(fmt_inr(d.closing_paise))}</td>"
        f"<td class='r dim'>{escape(fmt_inr(d.closing_lo_paise))}</td><td class='r dim'>{escape(fmt_inr(d.closing_hi_paise))}</td>"
        f"<td class='drv'>{escape(d.note[:90])}</td></tr>"
        for d in c.days
    )
    exc_rows = "".join(
        f"<tr><td>{escape(e.ref_id)}</td><td><span class='pill {('hi' if e.severity=='high' else 'md') if e.severity in ('high','medium') else 'lo'}'>{escape(e.code)}</span></td>"
        f"<td class='r'>{escape(fmt_inr(e.amount_paise))}</td><td>{escape((res.triage_table.get(e.ref_id, {}) or {}).get('category',''))}</td>"
        f"<td>{escape((res.triage_table.get(e.ref_id, {}) or {}).get('owner',''))}</td>"
        f"<td>{escape((res.triage_table.get(e.ref_id, {}) or {}).get('action',''))}</td>"
        f"<td class='dim'>{escape(e.detail[:150])}</td></tr>"
        for e in res.recon.exceptions[:80]
    )
    tier_rows = "".join(
        f"<tr><td>{escape(k)}</td><td class='r'>{v['matches']}</td><td class='r'>{v['correct']}</td><td class='r'>{v['wrong']}</td></tr>"
        for k, v in sorted((acc.by_tier if acc else {}).items(), key=lambda kv: -kv[1]["matches"])
    )
    svg = _sparkline(c.days)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"/>
<meta name="viewport" content="width=device-width,initial-scale=1"/>
<title>Cashpilot - {escape(str(m['as_of']))}</title>
<style>
:root{{--ink:#111827;--dim:#6b7280;--line:#e5e7eb;--brand:#1d4ed8}}
*{{box-sizing:border-box}}
body{{margin:0;background:#f6f7f9;color:var(--ink);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,sans-serif}}
.wrap{{max-width:1080px;margin:0 auto;padding:28px 20px 60px}}
header{{display:flex;justify-content:space-between;align-items:flex-end;gap:16px;flex-wrap:wrap}}
h1{{font-size:22px;margin:0 0 4px}}
h2{{font-size:15px;margin:28px 0 10px;color:var(--dim);text-transform:uppercase;letter-spacing:.06em}}
.sub{{color:var(--dim);font-size:13px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:12px;margin:18px 0 6px}}
.card{{background:#fff;border:1px solid var(--line);border-radius:12px;padding:14px 16px}}
.card .k{{font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--dim)}}
.card .v{{font-size:20px;font-weight:650;margin:4px 0 2px}}
.card .s{{font-size:12px;color:var(--dim)}}
.panel{{background:#fff;border:1px solid var(--line);border-radius:12px;padding:16px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;color:var(--dim);font-weight:600;border-bottom:1px solid var(--line);padding:6px 8px;position:sticky;top:0;background:#fff}}
td{{border-bottom:1px solid #f1f3f6;padding:6px 8px;vertical-align:top}}
td.r{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
td.b{{font-weight:600}} td.dim{{color:var(--dim)}} td.drv{{color:var(--dim);font-size:12px}}
.pill{{display:inline-block;padding:1px 8px;border-radius:99px;font-size:11px;font-weight:600}}
.pill.hi{{background:#fde8e8;color:#9b1c1c}} .pill.md{{background:#fff4e0;color:#8a5a00}} .pill.lo{{background:#e8f0fe;color:#1a4fbf}}
.brief{{background:#0b1220;color:#e7edf7;border-radius:12px;padding:16px 18px;font-size:14.5px;line-height:1.6}}
.brief .src{{color:#8fa3c4;font-size:12px;margin-top:8px}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:12px}}
@media(max-width:800px){{.two{{grid-template-columns:1fr}}}}
footer{{color:var(--dim);font-size:12px;margin-top:26px}}
</style></head><body><div class="wrap">
<header>
  <div><h1>Cashpilot - books, settlements and cash position</h1>
  <div class="sub">reconciled {m['counts']['bank_lines']} bank lines against {m['counts']['documents']} documents as of {escape(str(m['as_of']))}</div></div>
  <div class="sub">generated by <code>python -m cashpilot run</code> - no network calls</div>
</header>
<div class="cards">{card_html}</div>
<h2>Forward cash path</h2>
<div class="panel">{svg}</div>
<div class="two">
  <div class="panel"><h2 style="margin-top:0">Match quality by tier</h2>
    <table><thead><tr><th>tier</th><th class="r">matches</th><th class="r">correct</th><th class="r">wrong</th></tr></thead>
    <tbody>{tier_rows or '<tr><td colspan=4 class=dim>no ground truth available</td></tr>'}</tbody></table></div>
  <div class="panel"><h2 style="margin-top:0">Today's brief</h2>
    <div class="brief">{escape(res.brief.get('text',''))}<div class="src">phrasing: {escape(res.brief.get('source',''))}; every figure computed by deterministic code</div></div></div>
</div>
<h2>Forecast, day by day</h2>
<div class="panel"><table><thead><tr><th>day</th><th class="r">expected in</th><th class="r">expected out</th><th class="r">closing</th><th class="r">P10</th><th class="r">P90</th><th>top drivers</th></tr></thead><tbody>{rows}</tbody></table></div>
<h2>Exception queue ({m['counts']['exceptions']} items, first 80)</h2>
<div class="panel"><table><thead><tr><th>ref</th><th>code</th><th class="r">amount</th><th>triage</th><th>owner</th><th>action</th><th>detail</th></tr></thead><tbody>{exc_rows}</tbody></table></div>
<footer>Deterministic engine (regex + amount/date blocking + fuzzy fallback), {m['llm']['calls']} LLM calls this run,
{m['llm']['ok']} accepted. Reproduce: <code>make demo</code>.</footer>
</div></body></html>"""
