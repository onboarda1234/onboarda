"""Immutable persistence for canonical Compliance Memorandum PDF artefacts.

Preview and download are two presentations of one regulated document.  This
module owns the invariant that a memo row has at most one byte sequence for its
draft state and at most one byte sequence for its final state.  It never updates
or deletes an artefact and fails closed if stored bytes do not match their
persisted hash/length.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Optional, Tuple


MEMO_PDF_ARTIFACT_STATES = ("draft", "final")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


class MemoPDFArtifactError(RuntimeError):
    """Base class for canonical memo PDF persistence failures."""


class MemoPDFArtifactIntegrityError(MemoPDFArtifactError):
    """Stored bytes, length, or hash are invalid; never regenerate silently."""


class MemoPDFArtifactImmutableError(MemoPDFArtifactError):
    """A caller attempted to replace an already-persisted canonical artefact."""


def _artifact_state(value: str) -> str:
    state = str(value or "").strip().lower()
    if state not in MEMO_PDF_ARTIFACT_STATES:
        raise ValueError(f"Unsupported memo PDF artifact_state: {value!r}")
    return state


def _as_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, (bytearray, memoryview)):
        return bytes(value)
    raise MemoPDFArtifactIntegrityError("Stored memo PDF bytes have an unsupported database type")


def _validated_artifact(row: Any) -> Dict[str, Any]:
    data = dict(row or {})
    pdf_bytes = _as_bytes(data.get("pdf_bytes"))
    stored_hash = str(data.get("pdf_sha256") or "").strip().lower()
    stored_length = int(data.get("byte_length") or 0)
    observed_hash = hashlib.sha256(pdf_bytes).hexdigest()

    if not pdf_bytes.startswith(b"%PDF-"):
        raise MemoPDFArtifactIntegrityError("Stored memo PDF artefact is not a PDF")
    if not _SHA256_RE.fullmatch(stored_hash):
        raise MemoPDFArtifactIntegrityError("Stored memo PDF hash is invalid")
    if stored_length != len(pdf_bytes):
        raise MemoPDFArtifactIntegrityError("Stored memo PDF byte length does not match its metadata")
    if stored_hash != observed_hash:
        raise MemoPDFArtifactIntegrityError("Stored memo PDF bytes do not match their canonical hash")

    data["pdf_bytes"] = pdf_bytes
    data["pdf_sha256"] = stored_hash
    data["byte_length"] = stored_length
    data["artifact_state"] = _artifact_state(data.get("artifact_state"))
    return data


def load_memo_pdf_artifact(db, memo_id: int, artifact_state: str) -> Optional[Dict[str, Any]]:
    """Load and integrity-check the canonical artefact, if one exists."""
    state = _artifact_state(artifact_state)
    row = db.execute(
        """
        SELECT id, memo_id, artifact_state, pdf_bytes, pdf_sha256, byte_length,
               renderer_build_sha, created_at
          FROM compliance_memo_pdf_artifacts
         WHERE memo_id = ? AND artifact_state = ?
        """,
        (memo_id, state),
    ).fetchone()
    return _validated_artifact(row) if row else None


def persist_memo_pdf_artifact(
    db,
    *,
    memo_id: int,
    artifact_state: str,
    pdf_bytes: bytes,
    renderer_build_sha: str = "",
    reject_different_existing: bool = False,
) -> Tuple[Dict[str, Any], bool]:
    """Insert once and return the canonical stored artefact.

    Concurrent draft creators converge on the database winner.  Final creation
    is stricter: callers set ``reject_different_existing`` so a retry or race can
    never replace or reinterpret a locked document.
    """
    state = _artifact_state(artifact_state)
    candidate = _as_bytes(pdf_bytes)
    if not candidate.startswith(b"%PDF-"):
        raise MemoPDFArtifactIntegrityError("Generated memo PDF artefact is not a PDF")
    candidate_hash = hashlib.sha256(candidate).hexdigest()

    existing = load_memo_pdf_artifact(db, memo_id, state)
    if existing:
        if reject_different_existing and existing["pdf_sha256"] != candidate_hash:
            raise MemoPDFArtifactImmutableError(
                "Canonical final memo PDF artefact already exists with different bytes"
            )
        return existing, False

    db.execute(
        """
        INSERT INTO compliance_memo_pdf_artifacts (
            memo_id, artifact_state, pdf_bytes, pdf_sha256, byte_length,
            renderer_build_sha
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(memo_id, artifact_state) DO NOTHING
        """,
        (
            memo_id,
            state,
            candidate,
            candidate_hash,
            len(candidate),
            str(renderer_build_sha or ""),
        ),
    )
    inserted_rows = getattr(getattr(db, "_cursor", None), "rowcount", 0)
    stored = load_memo_pdf_artifact(db, memo_id, state)
    if not stored:
        raise MemoPDFArtifactError("Canonical memo PDF artefact was not persisted")
    if reject_different_existing and stored["pdf_sha256"] != candidate_hash:
        raise MemoPDFArtifactImmutableError(
            "Concurrent final memo PDF creation produced different bytes"
        )
    return stored, inserted_rows == 1
