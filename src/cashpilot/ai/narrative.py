"""The cash brief: the second and last use of the LLM.

Turning a table of numbers into a sentence a founder can act on is genuinely a language task,
so this is where the model goes. Everything below it (the numbers themselves) is deterministic,
and the *fallback* is a filled-in template rather than a shrug: with no API key, `cashpilot run`
still prints a complete, correctly-rounded brief.

Guardrail: the prompt forbids introducing numbers, and the answer is checked for any digit
group that does not appear in the evidence block. A brief that invents a figure is worse than no
brief, so it is discarded rather than edited.
"""

from __future__ import annotations

import re

from ..config import Settings
from ..money import fmt_inr
from .llm import LlmClient

SYSTEM = (
    "You are writing a 4-sentence daily cash brief for the founder of an Indian SMB, based ONLY on "
    "the evidence block. Never introduce a number that is not in the evidence. No hedging, no "
    "disclaimers, no bullet points, no markdown. Plain sentences."
)

USER_TMPL = """Evidence (all figures are already computed by deterministic code):
- cash today: {today}
- expected cash in {h30} days: {h30c} (P10 {h30lo} / P90 {h30hi})
- expected cash in {h7} days: {h7c}
- operating minimum the business must not go below: {floor}
- first day the P10 path breaches that minimum: {breach}
- receivables not yet collected: {ar} across {ar_n} invoices; payables due: {ap} across {ap_n} bills
- gateway settlements pending verification: {pend}
- reconciliation: {matched} of {lines} bank lines matched, {auto} auto-posted, {exc} exceptions needing a human
- top exception themes: {themes}
- largest single-day net outflow in the window: {worst_day} at {worst}
- forecast accuracy from the last backtest (error as share of gross movement, 7/30 day): {mape7} / {mape30}

Write the brief: what is happening, the one number that matters, what to do today, and what to
watch. Under 90 words."""


def _mape(backtest: dict[str, object] | None, h: int) -> str:
    if not backtest:
        return "not measured"
    m = backtest.get("metrics", {}) if isinstance(backtest, dict) else {}
    val = m.get(f"cashpilot_share_gross_{h}")
    return f"{val}" if val is not None else "not measured"


def _themes(triage_stats: dict[str, object], n: int = 3) -> str:
    cats = triage_stats.get("categories", {}) if isinstance(triage_stats, dict) else {}
    top = sorted(cats.items(), key=lambda kv: -kv[1])[:n] if isinstance(cats, dict) else []
    return ", ".join(f"{k} ({v})" for k, v in top) or "none"


def build_brief(
    *,
    forecast,
    recon_stats: dict[str, object],
    triage_stats: dict[str, object],
    verify_summary: dict[str, object],
    settings: Settings,
    backtest: dict[str, object] | None = None,
    llm: LlmClient | None = None,
) -> dict[str, object]:
    """Return {"text": ..., "source": "llm"|"template", "evidence": {...}}."""
    H = int(forecast.stats.get("horizon_days", 30))
    hor = forecast.horizons
    h7 = hor.get(7) or next(iter(hor.values()))
    h30 = hor.get(H) or list(hor.values())[-1]
    ev = {
        "today": fmt_inr(forecast.opening_paise),
        "h7": h7["horizon_days"],
        "h30": h30["horizon_days"],
        "h7c": fmt_inr(h7["expected_closing_paise"]),
        "h30c": fmt_inr(h30["expected_closing_paise"]),
        "h30lo": fmt_inr(h30["p10_closing_paise"]),
        "h30hi": fmt_inr(h30["p90_closing_paise"]),
        "floor": fmt_inr(int(forecast.stats.get("operating_minimum_paise", 0))),
        "breach": h30.get("first_breach_day") or "none inside the window",
        "ar": fmt_inr(int(forecast.stats.get("open_ar_paise", 0))),
        "ar_n": forecast.stats.get("open_ar_docs", 0),
        "ap": fmt_inr(int(forecast.stats.get("open_ap_paise", 0))),
        "ap_n": forecast.stats.get("open_ap_docs", 0),
        "pend": fmt_inr(int(verify_summary.get("recoverable_paise", 0))) + " overbilling risk, " + str(verify_summary.get("batches_flagged", 0)) + " batches flagged",
        "matched": recon_stats.get("matched_lines", 0),
        "lines": recon_stats.get("bank_lines", 0),
        "auto": recon_stats.get("auto_posted", 0),
        "exc": recon_stats.get("exceptions", 0),
        "themes": _themes(triage_stats),
        "worst_day": h30.get("worst_day", "-"),
        "worst": fmt_inr(int(h30.get("worst_day_p10_closing_paise", 0))),
        "mape7": _mape(backtest, 7),
        "mape30": _mape(backtest, 30),
    }
    prompt = USER_TMPL.format(**ev)
    client = llm or LlmClient(settings)
    if client.enabled and client.budget_left() > 0:
        payload = client.complete_json(SYSTEM, prompt + '\n\nAnswer JSON only: {"brief": "..."}')
        text = (payload or {}).get("brief")
        if isinstance(text, str) and _numbers_allowed(text, prompt):
            return {"text": text.strip(), "source": "llm", "evidence": ev, "prompt": prompt}
    return {"text": template_brief(ev), "source": "template", "evidence": ev, "prompt": prompt}


def _numbers_allowed(text: str, evidence: str) -> bool:
    """Reject a brief that contains a digit group absent from the evidence block."""
    allowed = set(re.findall(r"\d[\d,]*\.?\d*", evidence))
    seen = set(re.findall(r"\d[\d,]*\.?\d*", text))
    if not seen:
        return False
    for num in seen:
        cand = {num, num.replace(",", ""), num.rstrip(".0")}
        if cand & allowed or any(a.replace(",", "").startswith(num.replace(",", "")) for a in allowed):
            continue
        return False
    return True


def template_brief(ev: dict[str, object]) -> str:
    breach = ev["breach"]
    if breach and breach != "none inside the window":
        head = (
            f"Cash is {ev['today']} today and the expected path ends at {ev['h30c']} in {ev['h30']} days, "
            f"but the P10 path crosses your {ev['floor']} operating minimum on {breach}."
        )
        act = f"Before that date either chase the {ev['ar']} receivables into that week or hold {ev['ap']} of payables."
    else:
        head = (
            f"Cash is {ev['today']} today and the expected path ends at {ev['h30c']} in {ev['h30']} days; "
            f"the P10 path of {ev['h30lo']} stays above your {ev['floor']} minimum."
        )
        act = "No funding action needed inside the window - use the slack to clear the exception queue."
    mid = (
        f"{ev['matched']} of {ev['lines']} bank lines cleared themselves ({ev['auto']} auto-posted) and {ev['exc']} "
        f"need a human, dominated by {ev['themes']}."
    )
    tail = (
        f"Gateway verification flagged {ev['pend']}; forecast MAPE on the last backtest was {ev['mape7']}% at 7 days and {ev['mape30']}% at 30."
        if str(ev["mape7"]) != "not measured"
        else f"Gateway verification flagged {ev['pend']}. Run `cashpilot bench --forecast` for measured forecast accuracy."
    )
    return f"{head} {mid} {act} {tail}"
