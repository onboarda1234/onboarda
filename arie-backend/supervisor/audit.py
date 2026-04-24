"""
ARIE Finance — AI Agent Supervisor: Audit Logger
==================================================
Append-only audit logging for the entire supervisor framework.

Logs:
  - Every agent run (start, complete, fail)
  - Every schema validation result
  - Every contradiction found
  - Every rule triggered
  - Every human decision
  - Every override
  - Every prompt/model/version used

Features:
  - Hash chain for tamper detection
  - Structured JSON data for every event
  - Severity classification
  - Actor tracking (system, agent, officer, admin)
  - Uses the shared production DB layer (get_db) for PostgreSQL/SQLite
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Any, Deque, Dict, List, Optional
from uuid import uuid4

from .schemas import (
    AuditEntry,
    AuditEventType,
    Severity,
)

# ---------------------------------------------------------------------------
# Standalone transactional helper — does NOT open its own connection
# ---------------------------------------------------------------------------

def append_verdict_chain_entry(
    db,
    application_id: str,
    verdict: str,
    contradiction_count: int,
    supervisor_confidence: float,
    memo_id: str,
    actor_id: str = "",
    actor_name: str = "",
    actor_role: str = "",
    ip_address: Optional[str] = None,
) -> str:
    """Append a hash-chained audit entry for a memo-supervisor verdict.

    This function operates on the *caller's* open DB connection so the
    insert participates in the same transaction as the verdict write.
    The caller must NOT commit before calling this; the function does not
    commit itself.  If the insert fails the exception propagates to the
    caller so the outer transaction is never committed (fail-closed).

    The hash payload is byte-for-byte identical to what
    ``AuditLogger.verify_chain_integrity()`` reconstructs, so entries
    written here are fully verifiable by the existing verification path.

    Returns:
        entry_hash (str) — the SHA-256 hex digest of the new entry.
    Raises:
        Exception — propagated from the INSERT on failure.
    """
    from datetime import datetime, timezone
    from uuid import uuid4

    # Retrieve the most recent entry_hash to link the chain.
    row = db.execute(
        "SELECT entry_hash FROM supervisor_audit_log ORDER BY timestamp DESC LIMIT 1"
    ).fetchone()
    previous_hash: Optional[str] = row["entry_hash"] if row else None

    audit_id = str(uuid4())
    # Timestamp format must stay identical to AuditEntry.timestamp (schemas.py line ~734)
    # so that verify_chain_integrity() can reconstruct the same hash.
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    event_type_val = AuditEventType.SUPERVISOR_VERDICT.value
    severity_val = Severity.INFO.value
    action = f"Supervisor verdict: {verdict}"
    detail = f"Contradictions: {contradiction_count}, Confidence: {supervisor_confidence:.3f}"
    data: Dict[str, Any] = {
        "verdict": verdict,
        "contradiction_count": contradiction_count,
        "supervisor_confidence": supervisor_confidence,
        "memo_id": memo_id,
    }

    # Canonical content — structure and key ordering must exactly match the
    # entry_data dict reconstructed in AuditLogger.verify_chain_integrity().
    # Rules:
    #   - Null optional fields are serialised as "" (empty string), not null.
    #   - previous_hash for the genesis entry is "" (no prior entry).
    #   - hash_version=2 is the current algorithm version.
    #   - actor_type is always "officer": memo-supervisor runs are always
    #     triggered by a human compliance officer via the backoffice UI.
    #   - sort_keys=True ensures deterministic JSON regardless of dict ordering.
    content = json.dumps({
        "audit_id": audit_id,
        "timestamp": timestamp,
        "event_type": event_type_val,
        "severity": severity_val,
        "pipeline_id": "",
        "application_id": application_id or "",
        "run_id": "",
        "agent_type": "",
        "actor_type": "officer",
        "actor_id": actor_id or "",
        "actor_name": actor_name or "",
        "actor_role": actor_role or "",
        "action": action,
        "detail": detail,
        "data": data,
        "previous_hash": previous_hash or "",
        "hash_version": 2,
    }, sort_keys=True)
    entry_hash = hashlib.sha256(content.encode()).hexdigest()

    db.execute(
        """
        INSERT INTO supervisor_audit_log (
            id, timestamp, event_type, severity,
            pipeline_id, application_id, run_id, agent_type,
            actor_type, actor_id, actor_name, actor_role,
            action, detail, data_json,
            ip_address, session_id,
            previous_hash, entry_hash
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            audit_id, timestamp, event_type_val, severity_val,
            None, application_id, None, None,
            "officer", actor_id or None, actor_name or None, actor_role or None,
            action, detail, json.dumps(data),
            ip_address, None,
            previous_hash, entry_hash,
        ),
    )

    logger.info(
        "AUDIT CHAIN: verdict=%s app=%s entry_hash=%.16s previous_hash=%.16s",
        verdict, application_id, entry_hash,
        previous_hash or "GENESIS",
    )
    return entry_hash

logger = logging.getLogger("arie.supervisor.audit")


def _get_db():
    """Import get_db lazily to avoid circular imports at module load time."""
    import sys
    db_mod = sys.modules.get("db")
    if db_mod and hasattr(db_mod, "get_db"):
        return db_mod.get_db()
    try:
        from db import get_db
        return get_db()
    except ImportError:
        return None


class AuditLogger:
    """
    Append-only audit logger with hash chain integrity.

    Uses the shared production DB layer (get_db()) which transparently
    handles both PostgreSQL (staging/production) and SQLite (dev/test).
    Table creation is handled by db.py schema + migrations.
    """

    def __init__(self, db_path: Optional[str] = None):
        """
        Args:
            db_path: Retained for backward compat but NOT used for connections.
                     All DB access goes through get_db().
        """
        self.db_path = db_path  # kept for API compat / stats reporting
        self._last_hash: Optional[str] = None
        self._buffer: Deque[AuditEntry] = deque(maxlen=10000)
        self._total_entries = 0

        # Recover last hash from DB for chain continuity
        if db_path:
            self._recover_last_hash()

    def _recover_last_hash(self):
        """Recover the last entry hash from the database for chain continuity."""
        db = None
        try:
            db = _get_db()
            if db is None:
                return
            row = db.execute(
                "SELECT entry_hash FROM supervisor_audit_log ORDER BY timestamp DESC LIMIT 1"
            ).fetchone()
            if row:
                self._last_hash = row["entry_hash"] if isinstance(row, dict) else row[0]
        except Exception as e:
            logger.warning("Could not recover last audit hash: %s", e)
        finally:
            if db:
                try:
                    db.close()
                except Exception:
                    pass

    def log(
        self,
        event_type: AuditEventType,
        action: str,
        detail: Optional[str] = None,
        severity: Severity = Severity.INFO,
        pipeline_id: Optional[str] = None,
        application_id: Optional[str] = None,
        run_id: Optional[str] = None,
        agent_type: Optional[str] = None,
        actor_type: str = "system",
        actor_id: Optional[str] = None,
        actor_name: Optional[str] = None,
        actor_role: Optional[str] = None,
        data: Optional[Dict[str, Any]] = None,
        ip_address: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> AuditEntry:
        """
        Create and persist an audit log entry.

        Returns the created AuditEntry for reference.
        """
        entry = AuditEntry(
            audit_id=str(uuid4()),
            timestamp=datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            event_type=event_type,
            severity=severity,
            pipeline_id=pipeline_id,
            application_id=application_id,
            run_id=run_id,
            agent_type=agent_type,
            actor_type=actor_type,
            actor_id=actor_id,
            actor_name=actor_name,
            actor_role=actor_role,
            action=action,
            detail=detail,
            data=data or {},
            ip_address=ip_address,
            session_id=session_id,
            previous_hash=self._last_hash,
        )

        # Compute hash chain
        entry.entry_hash = entry.compute_hash(self._last_hash)
        self._last_hash = entry.entry_hash

        # Buffer in memory
        self._buffer.append(entry)
        self._total_entries += 1

        # Persist to DB via shared layer
        if self.db_path:
            self._persist(entry)

        logger.info(
            "AUDIT: [%s] %s | %s | app=%s run=%s",
            event_type.value, action,
            detail or "",
            application_id or "-",
            run_id or "-"
        )

        return entry

    def _persist(self, entry: AuditEntry):
        """Write entry to database via the shared get_db() layer."""
        db = None
        try:
            db = _get_db()
            if db is None:
                logger.warning("Cannot persist audit entry: DB not available")
                return
            db.execute("""
                INSERT INTO supervisor_audit_log (
                    id, timestamp, event_type, severity,
                    pipeline_id, application_id, run_id, agent_type,
                    actor_type, actor_id, actor_name, actor_role,
                    action, detail, data_json,
                    ip_address, session_id,
                    previous_hash, entry_hash
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                entry.audit_id, entry.timestamp, entry.event_type.value,
                entry.severity.value,
                entry.pipeline_id, entry.application_id,
                entry.run_id, entry.agent_type,
                entry.actor_type, entry.actor_id,
                entry.actor_name, entry.actor_role,
                entry.action, entry.detail,
                json.dumps(entry.data),
                entry.ip_address, entry.session_id,
                entry.previous_hash, entry.entry_hash,
            ))
            db.commit()
        except Exception as e:
            logger.error("Failed to persist audit entry %s: %s", entry.audit_id, e)
        finally:
            if db:
                try:
                    db.close()
                except Exception:
                    pass

    # ─── Convenience methods ──────────────────────────────

    def log_agent_run_started(
        self, run_id: str, agent_type: str, application_id: str,
        pipeline_id: str, **kwargs
    ) -> AuditEntry:
        return self.log(
            event_type=AuditEventType.AGENT_RUN_STARTED,
            action=f"Agent {agent_type} run started",
            detail=f"Run {run_id} for application {application_id}",
            pipeline_id=pipeline_id,
            application_id=application_id,
            run_id=run_id,
            agent_type=agent_type,
            **kwargs,
        )

    def log_agent_run_completed(
        self, run_id: str, agent_type: str, application_id: str,
        pipeline_id: str, confidence: float, status: str, **kwargs
    ) -> AuditEntry:
        return self.log(
            event_type=AuditEventType.AGENT_RUN_COMPLETED,
            action=f"Agent {agent_type} run completed",
            detail=f"Status: {status}, Confidence: {confidence:.3f}",
            pipeline_id=pipeline_id,
            application_id=application_id,
            run_id=run_id,
            agent_type=agent_type,
            data={"confidence": confidence, "status": status},
            **kwargs,
        )

    def log_agent_run_failed(
        self, run_id: str, agent_type: str, application_id: str,
        pipeline_id: str, error: str, **kwargs
    ) -> AuditEntry:
        return self.log(
            event_type=AuditEventType.AGENT_RUN_FAILED,
            action=f"Agent {agent_type} run failed",
            detail=error,
            severity=Severity.HIGH,
            pipeline_id=pipeline_id,
            application_id=application_id,
            run_id=run_id,
            agent_type=agent_type,
            data={"error": error},
            **kwargs,
        )

    def log_validation(
        self, run_id: str, agent_type: str, application_id: str,
        is_valid: bool, errors: List[str], **kwargs
    ) -> AuditEntry:
        event_type = (
            AuditEventType.SCHEMA_VALIDATION_PASSED if is_valid
            else AuditEventType.SCHEMA_VALIDATION_FAILED
        )
        return self.log(
            event_type=event_type,
            action=f"Schema validation {'passed' if is_valid else 'failed'}",
            detail=f"Agent {agent_type}, errors: {len(errors)}",
            severity=Severity.INFO if is_valid else Severity.WARNING,
            application_id=application_id,
            run_id=run_id,
            agent_type=agent_type,
            data={"is_valid": is_valid, "error_count": len(errors), "errors": errors[:10]},
            **kwargs,
        )

    def log_contradiction(
        self, contradiction_id: str, application_id: str,
        pipeline_id: str, category: str, severity: str,
        agent_a: str, agent_b: str, description: str, **kwargs
    ) -> AuditEntry:
        return self.log(
            event_type=AuditEventType.CONTRADICTION_DETECTED,
            action=f"Contradiction detected: {category}",
            detail=description[:200],
            severity=Severity(severity) if severity in [s.value for s in Severity] else Severity.WARNING,
            pipeline_id=pipeline_id,
            application_id=application_id,
            data={
                "contradiction_id": contradiction_id,
                "category": category,
                "agent_a": agent_a,
                "agent_b": agent_b,
            },
            **kwargs,
        )

    def log_rule_triggered(
        self, rule_name: str, application_id: str,
        pipeline_id: str, action: str, severity: str,
        trigger_data: Optional[str] = None, **kwargs
    ) -> AuditEntry:
        return self.log(
            event_type=AuditEventType.RULE_TRIGGERED,
            action=f"Rule triggered: {rule_name}",
            detail=f"Action: {action}, Trigger: {trigger_data or 'N/A'}",
            severity=Severity(severity) if severity in [s.value for s in Severity] else Severity.WARNING,
            pipeline_id=pipeline_id,
            application_id=application_id,
            data={"rule_name": rule_name, "action": action, "trigger_data": trigger_data},
            **kwargs,
        )

    def log_human_review(
        self, review_id: str, application_id: str,
        pipeline_id: str, reviewer_name: str, reviewer_role: str,
        decision: str, is_override: bool, **kwargs
    ) -> AuditEntry:
        return self.log(
            event_type=AuditEventType.HUMAN_REVIEW_COMPLETED,
            action=f"Human review completed: {decision}",
            detail=f"Reviewer: {reviewer_name} ({reviewer_role}), Override: {is_override}",
            severity=Severity.WARNING if is_override else Severity.INFO,
            pipeline_id=pipeline_id,
            application_id=application_id,
            actor_type="officer",
            actor_name=reviewer_name,
            actor_role=reviewer_role,
            data={
                "review_id": review_id,
                "decision": decision,
                "is_override": is_override,
            },
            **kwargs,
        )

    def log_override(
        self, override_id: str, application_id: str,
        officer_name: str, officer_role: str,
        override_type: str, original_value: str,
        override_value: str, reason: str, **kwargs
    ) -> AuditEntry:
        return self.log(
            event_type=AuditEventType.AI_OVERRIDE,
            action=f"AI override: {override_type}",
            detail=f"{original_value} → {override_value} by {officer_name}. Reason: {reason[:100]}",
            severity=Severity.WARNING,
            application_id=application_id,
            actor_type="officer",
            actor_name=officer_name,
            actor_role=officer_role,
            data={
                "override_id": override_id,
                "override_type": override_type,
                "original_value": original_value,
                "override_value": override_value,
                "reason": reason,
            },
            **kwargs,
        )

    # ─── Query / Verification ─────────────────────────────

    def verify_chain_integrity(self, limit: int = 1000) -> Dict[str, Any]:
        """
        Verify the hash chain integrity of the last N entries.
        Detects tampered or deleted entries.
        """

        db = None
        try:
            db = _get_db()
            if db is None:
                return {"verified": False, "reason": "DB connection not available"}

            rows = db.execute(
                "SELECT * FROM supervisor_audit_log ORDER BY timestamp ASC LIMIT ?",
                (limit,)
            ).fetchall()

            if not rows:
                return {
                    "verified": False,
                    "status": "no_entries",
                    "entries_checked": 0,
                    "reason": "Audit chain is empty — no entries to verify.",
                }

            broken_links = []
            prev_hash = None

            for row in rows:
                # v2 hash covers all material fields
                entry_data = {
                    "audit_id": row["id"],
                    "timestamp": row["timestamp"],
                    "event_type": row["event_type"],
                    "severity": row["severity"] or "info",
                    "pipeline_id": row["pipeline_id"] or "",
                    "application_id": row["application_id"] or "",
                    "run_id": row["run_id"] or "",
                    "agent_type": row["agent_type"] or "",
                    "actor_type": row["actor_type"] or "system",
                    "actor_id": row["actor_id"] or "",
                    "actor_name": row["actor_name"] or "",
                    "actor_role": row["actor_role"] or "",
                    "action": row["action"],
                    "detail": row["detail"] or "",
                    "data": json.loads(row["data_json"] or "{}"),
                    "previous_hash": prev_hash or "",
                    "hash_version": 2,
                }
                expected_hash = hashlib.sha256(
                    json.dumps(entry_data, sort_keys=True).encode()
                ).hexdigest()

                if row["entry_hash"] != expected_hash:
                    broken_links.append({
                        "entry_id": row["id"],
                        "expected_hash": expected_hash,
                        "actual_hash": row["entry_hash"],
                    })

                # Also check chain linkage
                if prev_hash and row["previous_hash"] != prev_hash:
                    broken_links.append({
                        "entry_id": row["id"],
                        "issue": "previous_hash mismatch",
                        "expected_previous": prev_hash,
                        "actual_previous": row["previous_hash"],
                    })

                prev_hash = row["entry_hash"]

            return {
                "verified": len(broken_links) == 0,
                "entries_checked": len(rows),
                "broken_links": broken_links,
                "chain_intact": len(broken_links) == 0,
            }

        except Exception as e:
            return {"verified": False, "reason": str(e)}
        finally:
            if db:
                try:
                    db.close()
                except Exception:
                    pass

    def get_entries(
        self,
        application_id: Optional[str] = None,
        event_type: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Query audit entries (read-only)."""
        if not self.db_path:
            # Fallback: return from in-memory buffer
            entries = list(self._buffer)
            if application_id:
                entries = [e for e in entries if e.application_id == application_id]
            if event_type:
                entries = [e for e in entries if e.event_type.value == event_type]
            return [e.model_dump() for e in entries[-limit:]]

        db = None
        try:
            db = _get_db()
            if db is None:
                return []

            query = "SELECT * FROM supervisor_audit_log WHERE 1=1"
            params = []

            if application_id:
                query += " AND application_id = ?"
                params.append(application_id)
            if event_type:
                query += " AND event_type = ?"
                params.append(event_type)

            query += " ORDER BY timestamp DESC LIMIT ?"
            params.append(limit)

            rows = db.execute(query, tuple(params)).fetchall()

            return [dict(row) for row in rows]
        except Exception as e:
            logger.error("Failed to query audit entries: %s", e)
            return []
        finally:
            if db:
                try:
                    db.close()
                except Exception:
                    pass

    def get_stats(self) -> Dict[str, Any]:
        return {
            "total_entries": self._total_entries,
            "buffer_size": len(self._buffer),
            "last_hash": self._last_hash,
            "db_configured": self.db_path is not None,
        }
