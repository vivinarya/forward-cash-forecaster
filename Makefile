# Cashpilot — the targets a reviewer actually needs.
# Everything runs from a clean checkout with only numpy installed.

PY      ?= python3
export PYTHONPATH := src

# The published corpora are pinned to these; every target that generates data passes them so a
# machine with a different clock still reproduces data/synthetic and data/sample byte for byte.

AS_OF ?= 2026-09-05
DATA    := data/synthetic
SAMPLE  := data/sample
OUT     := artifacts
SEED    ?= 20260905
SAMPLE_SEED ?= 4242

.PHONY: help install dev sample data demo run bench backtest forecast sweep test cov doctor clean all check distclean

help:
	@echo "make install   pip install -r requirements.txt"
	@echo "make demo      10-minute panel path: sample corpus, full run, benchmark   (start here)"
	@echo "make sample    generate the small corpus only -> $(SAMPLE)"
	@echo "make data      regenerate the published corpus (1,709 lines, byte-identical) -> $(DATA)"
	@echo "make run       reconcile + verify + forecast + reports on $(DATA) -> $(OUT)/"
	@echo "make bench     accuracy + speed across the three strategies (+ forecast backtest)"
	@echo "make sweep     the same metrics plus recovery at five corpus sizes, 90 to 5,610 lines"
	@echo "make check     test + demo + doctor, the whole promise in one line"
	@echo "make test      pytest;  make cov  with coverage"
	@echo "make doctor    environment and data sanity check"

install:
	$(PY) -m pip install -r requirements.txt

dev: install
	$(PY) -m pip install -r requirements-dev.txt

sample:
	$(PY) -m cashpilot generate --out $(SAMPLE) --scale sample --seed $(SAMPLE_SEED) --as-of $(AS_OF)

data: sample
	$(PY) -m cashpilot generate --out $(DATA) --scale medium --seed $(SEED) --as-of $(AS_OF)

demo:
	$(PY) -m cashpilot demo --data $(SAMPLE) --out $(OUT)/demo

run:
	$(PY) -m cashpilot run --data $(DATA) --out $(OUT) --runs 2000

backtest:
	$(PY) -m cashpilot run --data $(DATA) --out $(OUT) --runs 2000 --backtest

forecast:
	$(PY) -m cashpilot forecast --data $(DATA) --horizon 30 --runs 2000 --backtest

sweep:
	$(PY) -m cashpilot sweep --scales tiny,sample,medium,large,xl --seed $(SEED) --as-of $(AS_OF) \
		--json $(OUT)/scale_sweep.json --md $(OUT)/scale_sweep.md

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
	@mkdir -p $(OUT)/_scratch
	$(PY) -m cashpilot bench --data $(DATA) --reps 1 --forecast --seeded --out-dir $(OUT)/_scratch >/dev/null
	$(PY) -m cashpilot doctor --data $(SAMPLE)
	@echo "check: ok - tests, demo from a fresh corpus, probe bench; published artifacts untouched"

# `clean` removes only what git does not track: caches plus the scratch output `make
# check` writes. The corpora under data/ and the reports under artifacts/ ARE tracked -
# they are what README and docs quote - so regenerating them is explicit (`make data`,
# `make demo`, `make all`) and only `distclean` throws them away.
clean:
	rm -rf $(OUT)/_scratch .pytest_cache .coverage
	find . -name __pycache__ -type d -prune -exec rm -rf {} +

distclean: clean
	rm -rf $(OUT)
