"""Canonical JSON serialisation for the Supervisor foundation.

The reproducibility invariant depends entirely on this module: two structurally
equal values must produce byte-identical output on every run, on every host,
regardless of dictionary insertion order or float arithmetic history.

Serialisation rules (all enforced here, all tested):

1.  **Encoding** — UTF-8, no BOM, ``ensure_ascii=False`` so text is stored as
    itself rather than as escapes.
2.  **Object keys** — sorted lexicographically at every level by their Unicode
    code points. Non-string keys are rejected rather than coerced, because
    coercion makes ``1`` and ``"1"`` collide.
3.  **Whitespace** — none. Separators are ``(',', ':')``.
4.  **Floats** — rounded to ``FLOAT_PRECISION`` (4) decimal places, matching the
    rounding already applied by ``rule_engine`` when it writes
    ``factor_computation_evidence``. Negative zero normalises to ``0.0``.
    NaN and infinities are rejected: they are not valid JSON and their presence
    means an upstream computation failed.
5.  **Null** — preserved, and distinct from an absent key. A section that could
    not be read emits an explicit ``None``, never a dropped key.
6.  **Booleans** — emitted as ``true``/``false``. Checked before ``int``
    because ``isinstance(True, int)`` is true in Python.
7.  **Datetimes** — normalised to UTC and formatted ``%Y-%m-%dT%H:%M:%SZ``.
    A naive datetime is treated as UTC. Sub-second precision is dropped so that
    two reads of the same logical instant cannot differ.
8.  **Enums** — replaced by their ``.value``, so a renamed member name does not
    change a hash while the wire value is unchanged.
9.  **Decimal** — converted through float and normalised as in (4).
10. **Tuples** — serialised as arrays; list order is preserved exactly, because
    order is semantic and is the caller's responsibility to fix (see
    ``bundle.ORDERING_RULES``).
11. **Sets** — sorted by each element's own canonical encoding, which is a total
    order over any mix of types. Sets are accepted rather than rejected, but
    callers should prefer explicitly sorted lists so the ordering is visible at
    the call site.
12. **Bytes** — rejected. Binary has no canonical JSON form; callers should
    supply a hex digest instead.
"""

from __future__ import annotations

import json
import math
from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any

#: Decimal places retained for every float. Matches the rounding that
#: ``rule_engine`` already applies to stored risk evidence.
FLOAT_PRECISION = 4

#: Canonical datetime format. Deliberately second-resolution.
DATETIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


class CanonicalJSONError(TypeError):
    """A value has no canonical JSON representation."""


def _normalise_float(value: float) -> float:
    if math.isnan(value) or math.isinf(value):
        raise CanonicalJSONError(
            f"non-finite float {value!r} has no canonical JSON form; "
            "an upstream computation produced NaN or infinity"
        )
    rounded = round(float(value), FLOAT_PRECISION)
    # round() can return -0.0, which serialises as "-0.0" and would differ from
    # a structurally identical 0.0 produced by another code path.
    if rounded == 0.0:
        return 0.0
    return rounded


def _normalise_datetime(value: datetime) -> str:
    if value.tzinfo is None:
        aware = value.replace(tzinfo=timezone.utc)
    else:
        aware = value.astimezone(timezone.utc)
    return aware.strftime(DATETIME_FORMAT)


def canonicalise(value: Any) -> Any:
    """Return ``value`` rewritten into canonically-serialisable primitives."""
    # bool before int: isinstance(True, int) is True.
    if value is None or isinstance(value, (bool, str)):
        return value

    if isinstance(value, Enum):
        return canonicalise(value.value)

    if isinstance(value, int):
        return value

    if isinstance(value, float):
        return _normalise_float(value)

    if isinstance(value, Decimal):
        return _normalise_float(float(value))

    if isinstance(value, datetime):
        return _normalise_datetime(value)

    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")

    if isinstance(value, (bytes, bytearray, memoryview)):
        raise CanonicalJSONError(
            "bytes have no canonical JSON form; supply a hex digest instead"
        )

    if isinstance(value, dict):
        canonical: dict[str, Any] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise CanonicalJSONError(
                    f"object keys must be strings, got {type(key).__name__}: {key!r}"
                )
            canonical[key] = canonicalise(item)
        # Sorting happens at dump time via sort_keys, but sorting here too keeps
        # the returned structure inspectable in a stable order during debugging.
        return {key: canonical[key] for key in sorted(canonical)}

    if isinstance(value, (list, tuple)):
        return [canonicalise(item) for item in value]

    if isinstance(value, (set, frozenset)):
        # Total order over mixed types: sort by each element's own encoding.
        encoded = [(canonical_json(item), canonicalise(item)) for item in value]
        encoded.sort(key=lambda pair: pair[0])
        return [item for _, item in encoded]

    raise CanonicalJSONError(
        f"{type(value).__name__} has no canonical JSON form: {value!r}"
    )


def canonical_json(value: Any) -> str:
    """Serialise ``value`` to its canonical JSON string."""
    return json.dumps(
        canonicalise(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def canonical_bytes(value: Any) -> bytes:
    """Serialise ``value`` to canonical UTF-8 bytes — the input to every hash."""
    return canonical_json(value).encode("utf-8")
