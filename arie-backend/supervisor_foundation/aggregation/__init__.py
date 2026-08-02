"""Review aggregation — probe findings into one Senior Compliance Officer review.

The probe layer answers "what is wrong with this case?" one check at a time.
This layer answers the questions an officer actually opens the file with:

1. What materially concerns the Supervisor?
2. Which controls could not be evaluated?
3. Which findings are the same problem seen more than once?
4. What must be done next?
5. What evidence would close each issue?
6. What would a regulator challenge?
7. How complete and defensible is this review?

Pure, deterministic, read-only, advisory, non-authoritative, and free of any
model call. No authoritative module imports this package; it has no write path,
no API, no persistence and no scheduled execution.
"""

from .checks import CHECK_REGISTER, CONTRADICTION_TYPES, MISSING_EVIDENCE_CLASSES
from .engine import (
    MATERIAL_CONCERN_LIMIT,
    OVERALL_STATUSES,
    AggregationInputError,
    aggregate_review,
)
from .limitations import LIMITATION_KINDS, LIMITATIONS

__all__ = [
    "CHECK_REGISTER",
    "CONTRADICTION_TYPES",
    "LIMITATIONS",
    "LIMITATION_KINDS",
    "MATERIAL_CONCERN_LIMIT",
    "MISSING_EVIDENCE_CLASSES",
    "OVERALL_STATUSES",
    "AggregationInputError",
    "aggregate_review",
]
