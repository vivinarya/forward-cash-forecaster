"""The AI layer, kept deliberately small: exception triage and the written brief.

Everything that decides a number lives outside this package. If the model is off, the pipeline still
produces the same matches, the same forecast and a filled-in template brief - see docs/AI_JUDGEMENT.md.
"""

from .triage import CATEGORIES, deterministic_triage, triage  # noqa: F401
