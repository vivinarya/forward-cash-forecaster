#!/usr/bin/env python3
"""Thin wrapper so the generator can be run without installing the package.

    python tools/generate_synthetic.py --out data/synthetic --scale medium --seed 20260905

Equivalent to `python -m cashpilot generate ...`. Writes the bank feed, AR/AP ledgers, remittance
advices, Razorpay exports AND the ground-truth files used by `cashpilot bench`.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cashpilot.cli import build_parser  # noqa: E402


def main() -> int:
    argv = sys.argv[1:]
    if not argv or argv[0] != "generate":
        argv = ["generate", *argv]
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
