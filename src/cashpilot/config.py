"""Configuration loading: repo defaults + optional user override file + env vars.

Precedence (lowest to highest): built-in defaults -> config/recon_rules.json ->
CASHPILOT_RULES=<path> -> explicit CLI overrides.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_RULES: dict[str, Any] = {
    # A document reference is <prefix><4-10 digits, separators optional>, which covers INV-4711,
    # INV-2026-00123, VB991122 and BILL/471122. The previous default demanded a 3-6 digit tail, so
    # short invoice numbers in a narration were never extracted and those lines fell through to the
    # fuzzy tiers - see docs/FAILURES.md #4.
    "invoice_number_patterns": [
        "\\b(?:INV|SINV|SALES|RT|PO|BILL|VB|SUP)[-/]?\\d{4}(?:[-/]?\\d{1,6})?\\b",
    ],
    "name_similarity": {"auto_post_min": 0.86, "review_min": 0.70, "max_candidates_per_line": 6},
    "amount_tolerance": {
        "exact": 0,
        "rounding_paise": 100,
        "pct_of_invoice": 0.005,
        "absolute_max_paise": 500000,
    },
    "date_windows_days": {
        "tier_utr": {"early": 60, "late": 90},
        "tier_doc_number": {"early": 45, "late": 150},
        "tier_amount_exact": {"early": 20, "late": 25},
        "tier_amount_plus_name": {"early": 25, "late": 40},
        "tier_fuzzy": {"early": 30, "late": 60},
        "tier_lumpsum": {"early": 30, "late": 60},
        "_doc_date_floor": 20,
    },
    "lumpsum": {"enabled": True, "max_candidates": 12, "min_parts": 2},
    "duplicate_detection": {"same_utr": True, "same_amount_name_within_days": 3},
    "auto_post": {"min_confidence": 0.90, "max_amount_paise_for_auto": 2_000_000_000},
    "cash_policy": {
        "minimum_cash_paise": 300_000_000,
        "warning_days": 5,
        "forecast_horizons": [7, 14, 30],
        "monte_carlo_runs": 2000,
        "percentiles": [10, 50, 90],
        "weekday_redistribute": True,
        "aged_daily_hazard": 0.012,
    },
}

REPO_ROOT = Path(__file__).resolve().parents[2]


@dataclass(slots=True)
class Settings:
    rules: dict[str, Any] = field(default_factory=lambda: json.loads(json.dumps(DEFAULT_RULES)))
    rules_path: Path | None = None
    llm_enabled: bool = False
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    llm_api_key: str | None = None
    llm_max_calls: int = 200
    llm_timeout_s: int = 20
    warnings: tuple[str, ...] = ()

    # ---- rule shorthands ----
    @property
    def patterns(self) -> list[str]:
        return list(self.rules.get("invoice_number_patterns", []))

    def window(self, key: str) -> tuple[int, int, int]:
        """(early_days, late_days, doc_date_floor) for a tier - days vs the due date."""
        w = self.rules["date_windows_days"].get(key, {})
        if isinstance(w, (int, float)):
            return int(w), int(w), int(self.rules["date_windows_days"].get("_doc_date_floor", 20))
        return (
            int(w.get("early", 30)),
            int(w.get("late", 30)),
            int(self.rules["date_windows_days"].get("_doc_date_floor", 20)),
        )

    def tolerance_paise(self, amount_paise: int) -> int:
        tol = self.rules["amount_tolerance"]
        pct = int(abs(amount_paise) * float(tol.get("pct_of_invoice", 0.0)))
        return max(int(tol.get("rounding_paise", 0)), min(pct, int(tol.get("absolute_max_paise", pct))))

    @property
    def auto_post_min_confidence(self) -> float:
        return float(self.rules["auto_post"]["min_confidence"])

    def sim_thresholds(self) -> tuple[float, float]:
        ns = self.rules["name_similarity"]
        return float(ns["auto_post_min"]), float(ns["review_min"])


def _deep_merge(base: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for k, v in extra.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def load_settings(rules_path: str | os.PathLike[str] | None = None) -> Settings:
    warnings: list[str] = []
    path = Path(rules_path) if rules_path else None
    if path is None:
        env_path = os.environ.get("CASHPILOT_RULES")
        candidate = REPO_ROOT / "config" / "recon_rules.json"
        path = Path(env_path) if env_path else (candidate if candidate.exists() else None)

    rules = json.loads(json.dumps(DEFAULT_RULES))
    if path is not None:
        try:
            rules = _deep_merge(rules, json.loads(Path(path).read_text()))
        except FileNotFoundError:
            warnings.append(f"rules file {path} not found; using built-in defaults")
        except json.JSONDecodeError as exc:
            warnings.append(f"rules file {path} is not valid JSON ({exc}); using built-in defaults")

    enabled = os.environ.get("CASHPILOT_LLM_ENABLED", "0").strip().lower() in {"1", "true", "yes", "on"}
    key = os.environ.get("CASHPILOT_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    if enabled and not key:
        warnings.append("CASHPILOT_LLM_ENABLED=1 but no API key found -> falling back to heuristic judge")
        enabled = False

    return Settings(
        rules=rules,
        rules_path=path,
        llm_enabled=enabled,
        llm_base_url=os.environ.get("CASHPILOT_LLM_BASE_URL", "https://api.openai.com/v1"),
        llm_model=os.environ.get("CASHPILOT_LLM_MODEL", "gpt-4o-mini"),
        llm_api_key=key,
        llm_max_calls=int(os.environ.get("CASHPILOT_LLM_MAX_CALLS", "200")),
        llm_timeout_s=int(os.environ.get("CASHPILOT_LLM_TIMEOUT_S", "20")),
        warnings=tuple(warnings),
    )
