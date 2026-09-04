"""The pipeline the track asks for: run the books, verify settlement, forecast cash.

`cashpilot run` executes this. It is an *agent loop* in the practical sense - ingest, act, check
what is left unresolved, ask the model only about the residue, then report - and it is a plain
function rather than a framework because there is exactly one plan and the plan is auditable.

Each stage records its wall time and its own failure list into `RunResult.artifacts["manifest"]`,
so a run can be defended or debugged after the fact.
"""

from __future__ import annotations

import json
import platform
import sys
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .ai.llm import LlmClient
from .ai.narrative import build_brief
from .ai.triage import triage
from .config import Settings, load_settings
from .eval.accuracy import load_truth, score
from .eval.recovery import recovery_report
from .forecast.engine import forecast
from .ingest import Dataset, load_dataset
from .recon.engine import Reconciler
from .verify.settlements import FeeSchedule, verify_settlements


@dataclass
class RunResult:
    dataset: Dataset
    recon: object = None
    verify_rows: list = field(default_factory=list)
    verify_summary: dict = field(default_factory=dict)
    triage_table: dict = field(default_factory=dict)
    triage_stats: dict = field(default_factory=dict)
    cash: object = None
    brief: dict = field(default_factory=dict)
    accuracy: object = None
    recovery: object = None
    manifest: dict = field(default_factory=dict)

    @property
    def exceptions(self) -> list:
        return list(self.recon.exceptions) if self.recon else []


def run_books(
    data_dir: str | Path,
    *,
    settings: Settings | None = None,
    strategy: str = "full",
    horizon: int | None = None,
    runs: int | None = None,
    triage_limit: int = 60,
    with_truth: bool = True,
) -> RunResult:
    """Reconcile -> verify -> triage residue -> forecast. Never raises on missing optional inputs."""
    settings = settings or load_settings()
    stages: dict[str, float] = {}
    notes: list[str] = []
    t0 = time.perf_counter()
    ds = load_dataset(data_dir)
    stages["ingest_ms"] = round((time.perf_counter() - t0) * 1000, 2)

    # Unparseable input rows become first-class exceptions. A row we could not read is a finding
    # for the finance team, not a line in a log file nobody opens.
    from .models import ReconException

    input_exceptions = [
        ReconException(
            ref_id=f"{f['file']}#{f['row']}",
            ref_type="input_row",
            code="PARSE_FAILURE",
            severity="medium",
            amount_paise=0,
            detail=f"{f['reason']}: {f['raw'][:160]}",
            suggested_action="fix_the_source_file",
        )
        for f in ds.parse_failures
    ]

    t1 = time.perf_counter()
    recon = Reconciler(ds, settings, strategy=strategy).run()
    recon.exceptions = input_exceptions + recon.exceptions
    recon.stats["exceptions"] = len(recon.exceptions)
    stages["reconcile_ms"] = recon.stats.get("stage_ms", {})
    stages["reconcile_total_ms"] = round((time.perf_counter() - t1) * 1000, 2)

    t1 = time.perf_counter()
    settlement_link = {}
    for m in recon.matches:
        for doc_id in m.doc_ids:
            if doc_id.startswith("SETL-"):
                settlement_link[doc_id[5:]] = m.line_id
    rows, verify_excs, vsummary = verify_settlements(ds, FeeSchedule.load(), settlement_link)
    recon.exceptions.extend(verify_excs)
    stages["settlement_verify_ms"] = round((time.perf_counter() - t1) * 1000, 2)

    t1 = time.perf_counter()
    llm = LlmClient(settings)
    tres = triage(recon.exceptions, ds, settings, limit=triage_limit, llm=llm)
    stages["triage_ms"] = round((time.perf_counter() - t1) * 1000, 2)

    t1 = time.perf_counter()
    cash = forecast(ds, recon, settings, horizon=horizon, runs=runs)
    stages["forecast_ms"] = round((time.perf_counter() - t1) * 1000, 2)

    t1 = time.perf_counter()
    recovery = recovery_report(data_dir, recon, rows, vsummary, cash=cash)
    stages["recovery_ms"] = round((time.perf_counter() - t1) * 1000, 2)

    accuracy = None
    truth_path = Path(data_dir) / "truth_matches.csv"
    if with_truth and truth_path.exists():
        accuracy = score(recon.matches, load_truth(truth_path), stats=recon.stats, strategy=strategy)

    brief = build_brief(
        forecast=cash,
        recon_stats=recon.stats,
        triage_stats=tres["stats"],
        verify_summary=vsummary,
        settings=settings,
        llm=llm,
    )

    stages["total_ms"] = round((time.perf_counter() - t0) * 1000, 2)
    manifest = {
        "tool": "cashpilot",
        "version": __import__("cashpilot").__version__,
        "ran_at": date.today().isoformat(),
        "as_of": ds.as_of.isoformat() if ds.as_of else None,
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "strategy": strategy,
        "stages_ms": stages,
        "inputs": ds.source_files,
        "warnings": list(ds.warnings),
        "config_warnings": list(settings.warnings),
        "rules_path": str(settings.rules_path) if settings.rules_path else None,
        "llm": llm.usage(),
        "counts": {
            "bank_lines": len(ds.lines),
            "documents": len(ds.docs),
            "advice_rows": len(ds.advices),
            "settlements": len(ds.settlements),
            "payments": len(ds.payments),
            "refunds": len(ds.refunds),
            "parse_failures": len(ds.parse_failures),
            "matches": len(recon.matches),
            "auto_posted": len(recon.auto_posted),
            "exceptions": len(recon.exceptions),
        },
        "accuracy": accuracy.to_dict() if accuracy else None,
        "recovery": recovery.to_dict(),
        "settlements": vsummary,
        "forecast_stats": {k: v for k, v in cash.stats.items() if k != "learnt"},
        "notes": notes,
    }
    ds.warnings.extend(notes)
    return RunResult(
        dataset=ds,
        recon=recon,
        verify_rows=rows,
        verify_summary=vsummary,
        triage_table=tres["table"],
        triage_stats=tres["stats"],
        cash=cash,
        brief=brief,
        accuracy=accuracy,
        recovery=recovery,
        manifest=manifest,
    )
