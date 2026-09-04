"""Synthetic world generator (data + ground truth).

Nothing in this package is imported by the reconciler or the forecaster at runtime:
the generator is a *development/evaluation* dependency, kept out of the production path.
"""

from .world import World  # noqa: F401
