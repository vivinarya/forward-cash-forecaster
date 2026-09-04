"""Shared fixtures.

The package is not installed during development, so `src/` is put on the path here instead of
requiring `pip install -e .` before the tests run. `PYTHONPATH=src python -m pytest` also works.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from cashpilot.config import load_settings  # noqa: E402
from cashpilot.synth.world import World  # noqa: E402


@pytest.fixture(scope="session")
def settings():
    return load_settings()


@pytest.fixture(scope="session")
def tiny_corpus(tmp_path_factory) -> Path:
    """A small but structurally complete corpus (gateway, refunds, duplicates, lumpsums...).

    Session-scoped: generating + reconciling it costs a couple of seconds, and nothing in the test
    suite mutates it.
    """
    out = tmp_path_factory.mktemp("corpus")
    w = World(
        seed=11,
        as_of=date(2026, 9, 5),
        history_days=75,
        horizon_days=21,
        n_customers=6,
        n_vendors=5,
        invoices_per_day=5.0,
        bills_per_day=4.0,
    )
    w.build()
    w.emit(out)
    return out


@pytest.fixture(scope="session")
def run_result(tiny_corpus, settings):
    from cashpilot.pipeline import run_books

    return run_books(tiny_corpus, settings=settings, strategy="full", horizon=21, runs=300, with_truth=True)
