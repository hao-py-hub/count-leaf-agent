"""Shared analysis-job state groups used by API and service boundaries."""

RESULT_READY_STATUSES = frozenset({"completed", "review_required"})
TERMINAL_ANALYSIS_STATUSES = RESULT_READY_STATUSES | frozenset({"failed", "cancelled"})
