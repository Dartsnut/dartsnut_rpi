"""Transport outcome vocabulary for a queued outbox ref.

These strings cross the Unix socket to the Rust bridge and are mirrored by the
bridge's ack/error wire kinds, so they are protocol values, not internal names.
They live here so every comparison and production site shares one spelling: a
typo in a single comparison (for instance in the Settings overlay's poll) would
otherwise read as "this ref never settles" and hang the overlay without an error.

`ref_status` reports OUTCOME_PENDING for a ref that is still queued, then the
most recent transport report. OUTCOME_ERROR is not final: the entry stays queued
for retry, so a later OUTCOME_ACKED supersedes it.
"""

OUTCOME_PENDING = "pending"
OUTCOME_ACKED = "acked"
OUTCOME_ERROR = "error"

__all__ = ["OUTCOME_PENDING", "OUTCOME_ACKED", "OUTCOME_ERROR"]
