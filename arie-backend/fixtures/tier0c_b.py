"""Transactional orchestration for the controlled Tier 0C-B recomputation.

This module deliberately does not open a database connection.  The caller
supplies the connection that owns the complete recomputation transaction, the
standard audit writer, and the final validation callback.  There is exactly
one commit point: after every application has recomputed and validation has
passed.

Normal runtime recomputation remains unchanged.  Only this controlled path
opts out of the standard audit writer's autonomous commit behaviour.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from rule_engine import recompute_risk


class Tier0CBTransactionError(RuntimeError):
    """The controlled recomputation could not complete atomically."""


class Tier0CBTransactionBoundaryError(Tier0CBTransactionError):
    """A transaction participant attempted to control the outer transaction."""


class _NoCommitRawConnection:
    """Delegate raw-connection reads while denying transaction control."""

    def __init__(self, connection: Any):
        self._connection = connection

    def commit(self) -> None:
        raise Tier0CBTransactionBoundaryError(
            "Tier 0C-B callbacks cannot commit the caller-owned transaction"
        )

    def rollback(self) -> None:
        raise Tier0CBTransactionBoundaryError(
            "Tier 0C-B callbacks cannot roll back the caller-owned transaction"
        )

    def close(self) -> None:
        raise Tier0CBTransactionBoundaryError(
            "Tier 0C-B callbacks cannot close the caller-owned connection"
        )

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


class _TransactionParticipantConnection:
    """Connection view that can query/write but cannot end the transaction."""

    def __init__(self, db: Any):
        self._db = db
        self._raw_connection = None
        self._transaction_id = self._current_transaction_id()

    @property
    def is_postgres(self) -> bool:
        return bool(getattr(self._db, "is_postgres", False))

    @property
    def database_identity(self) -> Any:
        return getattr(self._db, "database_identity", None)

    @property
    def conn(self) -> _NoCommitRawConnection:
        if self._raw_connection is None:
            self._raw_connection = _NoCommitRawConnection(self._db.conn)
        return self._raw_connection

    def commit(self) -> None:
        raise Tier0CBTransactionBoundaryError(
            "Tier 0C-B callbacks cannot commit the caller-owned transaction"
        )

    def rollback(self) -> None:
        raise Tier0CBTransactionBoundaryError(
            "Tier 0C-B callbacks cannot roll back the caller-owned transaction"
        )

    def close(self) -> None:
        raise Tier0CBTransactionBoundaryError(
            "Tier 0C-B callbacks cannot close the caller-owned connection"
        )

    @property
    def rowcount(self) -> Any:
        return getattr(getattr(self._db, "_cursor", None), "rowcount", None)

    @property
    def lastrowid(self) -> Any:
        return getattr(self._db, "lastrowid", None)

    def execute(self, sql: str, params: tuple = ()) -> "_TransactionParticipantConnection":
        self._db.execute(sql, params)
        # DBConnection.execute() returns the original DBConnection. Returning
        # this restricted view instead prevents ``db.execute(...).commit()``
        # from escaping the transaction boundary.
        return self

    def executescript(self, sql: str) -> None:
        self._db.executescript(sql)

    def fetchone(self) -> Any:
        return self._db.fetchone()

    def fetchall(self) -> Any:
        return self._db.fetchall()

    def _current_transaction_id(self) -> Any:
        if not bool(getattr(self._db, "is_postgres", False)):
            return None
        self._db.execute("SELECT txid_current() AS tier0c_b_transaction_id")
        row = self._db.fetchone()
        return row["tier0c_b_transaction_id"]

    def assert_transaction_continuity(self) -> None:
        """Detect a swallowed PostgreSQL error/rollback before partial commit."""
        if self._transaction_id is None:
            return
        current = self._current_transaction_id()
        if current != self._transaction_id:
            raise Tier0CBTransactionBoundaryError(
                "Tier 0C-B transaction changed during a callback; "
                "the complete batch will be rolled back"
            )


class _TransactionBoundAuditWriter:
    """Force every callback write onto the caller's uncommitted connection."""

    def __init__(
        self,
        db: Any,
        participant_db: _TransactionParticipantConnection,
        writer: Callable[..., Any],
    ):
        self._db = db
        self._participant_db = participant_db
        self._writer = writer
        self._failures: list[BaseException] = []

    def __call__(
        self,
        user: Mapping[str, Any],
        action: str,
        target: str,
        detail: str,
        **kwargs: Any,
    ) -> Any:
        requested_db = kwargs.pop("db", self._participant_db)
        if requested_db is not self._db and requested_db is not self._participant_db:
            error = Tier0CBTransactionBoundaryError(
                "Tier 0C-B audit writes must use the caller-owned connection"
            )
            self._failures.append(error)
            raise error

        # BaseHandler.log_audit defaults to commit=True.  This adapter makes
        # the controlled contract explicit for the primary recompute audit and
        # every related audit callback.
        kwargs["db"] = self._participant_db
        kwargs["commit"] = False
        try:
            return self._writer(user, action, target, detail, **kwargs)
        except BaseException as exc:
            self._failures.append(exc)
            raise

    def raise_if_failed(self) -> None:
        if self._failures:
            raise Tier0CBTransactionError(
                "Tier 0C-B audit callback failed; transaction will be rolled back"
            ) from self._failures[0]


def run_tier0c_b_recomputation_transaction(
    db: Any,
    application_ids: Sequence[Any],
    *,
    reason: str,
    user: Mapping[str, Any],
    log_audit_fn: Callable[..., Any],
    validator_fn: Callable[..., Any],
) -> dict[str, Any]:
    """Recompute and validate a Tier 0C-B batch in one transaction.

    ``validator_fn`` is invoked as ``validator_fn(db=..., recomputations=...,
    audit_writer=...)``.  It must raise on validation failure and return
    ``True`` or a mapping with ``valid=True`` on success. The existing
    canonical approval-route validator's two explicit ``*_valid`` flags are
    also accepted when both are true. The supplied connection and audit
    writer are transaction participants: neither can commit, roll back,
    close, or replace the caller-owned connection.

    The function owns the sole commit/rollback decision for this operation.
    It never opens a second connection.
    """

    ids = tuple(application_ids)
    if not ids:
        raise Tier0CBTransactionError(
            "Tier 0C-B requires at least one explicitly scoped application"
        )
    if len(ids) != len(set(ids)):
        raise Tier0CBTransactionError(
            "Tier 0C-B application scope contains duplicate IDs"
        )
    if not callable(log_audit_fn):
        raise Tier0CBTransactionError("Tier 0C-B requires an audit writer")
    if not callable(validator_fn):
        raise Tier0CBTransactionError("Tier 0C-B requires a validation callback")

    participant_db = _TransactionParticipantConnection(db)
    audit_writer = _TransactionBoundAuditWriter(
        db,
        participant_db,
        log_audit_fn,
    )
    recomputations: list[dict[str, Any]] = []

    try:
        for position, application_id in enumerate(ids, start=1):
            result = recompute_risk(
                participant_db,
                application_id,
                reason,
                user=user,
                log_audit_fn=audit_writer,
                apply_routing_policy=True,
                audit_commit=False,
            )
            audit_writer.raise_if_failed()
            participant_db.assert_transaction_continuity()
            if not result.get("recomputed"):
                raise Tier0CBTransactionError(
                    "Tier 0C-B recomputation failed for application "
                    f"{application_id!r} at batch position {position}"
                )
            recomputations.append({"application_id": application_id, **result})

        validation = validator_fn(
            db=participant_db,
            recomputations=tuple(recomputations),
            audit_writer=audit_writer,
        )
        audit_writer.raise_if_failed()
        participant_db.assert_transaction_continuity()
        validation_passed = validation is True or (
            isinstance(validation, Mapping) and validation.get("valid") is True
        ) or (
            isinstance(validation, Mapping)
            and validation.get("approval_routes_valid") is True
            and validation.get("decision_eligibility_valid") is True
        )
        if not validation_passed:
            raise Tier0CBTransactionError(
                "Tier 0C-B validation callback did not return explicit success"
            )

        result = {
            "applications_recomputed": len(recomputations),
            "recomputations": recomputations,
            "validation": validation,
        }
        db.commit()
        return result
    except BaseException:
        db.rollback()
        raise


__all__ = [
    "Tier0CBTransactionBoundaryError",
    "Tier0CBTransactionError",
    "run_tier0c_b_recomputation_transaction",
]
