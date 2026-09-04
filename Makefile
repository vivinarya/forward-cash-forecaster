# Cashpilot — the targets a reviewer actually needs.
# Everything runs from a clean checkout with only numpy installed.

PY      ?= python3
export PYTHONPATH := src
DATA    := data/synthetic
SAMPLE  := data/sample
OUT     := artifacts
SEED    ?= 20260905

.PHONY: help install dev sample demo run bench backtest forecast test cov doctor clean all check

help:
	@echo "make install   pip install -r requirements.txt"
	@echo "make demo      10-minute panel path: sample corpus, full run, benchmark   (start here)"
	@echo "make sample    generate the small corpus only -> $(SAMPLE)"
	@echo "make data      regenerate the published corpus (1,709 lines, byte-identical) -> $(DATA)"
	@echo "make run       reconcile + verify + forecast + reports on $(DATA) -> $(OUT)/"
	@echo "make bench     accuracy + speed across the three strategies (+ forecast backtest)"
	@echo "make check     test + demo + doctor, the whole promise in one line"
	@echo "make test      pytest;  make cov  with coverage"
	@echo "make doctor    environment and data sanity check"

install:
	$(PY) -m pip install -r requirements.txt

dev: install
	$(PY) -m pip install -r requirements-dev.txt

sample:
	$(PY) -m cashpilot generate --out $(SAMPLE) --scale sample --seed $(SEED)

data:
	$(PY) -m cashpilot generate --out $(DATA) --scale medium --seed $(SEED) --as-of 2026-09-05

demo:
	$(PY) -m cashpilot demo --data $(SAMPLE) --out $(OUT)/demo

run:
	$(PY) -m cashpilot run --data $(DATA) --out $(OUT) --runs 2000

backtest:
	$(PY) -m cashpilot run --data $(DATA) --out $(OUT) --runs 2000 --backtest

forecast:
	$(PY) -m cashpilot forecast --data $(DATA) --horizon 30 --runs 2000 --backtest

bench:
	$(PY) -m cashpilot bench --data $(DATA) --reps 3 --forecast --seeded --json $(OUT)/bench.json

test:
	$(PY) -m pytest -q

cov:
	$(PY) -m pytest -q --cov=cashpilot --cov-report=term-missing:skip-covered

doctor:
	$(PY) -m cashpilot doctor --data $(DATA)

# what CI runs: tests, then a demo from a regenerated corpus, so the shipped artifacts are the ones
# the commands reproduce.
check all: test demo
	$(PY) -m cashpilot bench --data $(DATA) --reps 1 --forecast --seeded --out-dir $(OUT) >/dev/null
	$(PY) -m cashpilot doctor --data $(SAMPLE)

clean:
	rm -rf $(OUT) .pytest_cache .coverage
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
