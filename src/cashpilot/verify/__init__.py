"""Settlement verification: recompute what the gateway owes us and diff it against the bank.

No AI here at all on purpose: this is arithmetic against a contracted fee schedule.
"""

from .settlements import verify_settlements  # noqa: F401
