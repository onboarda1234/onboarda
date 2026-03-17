#!/usr/bin/env python3
"""
Workflow enforcement rules for the onboarding process.
Ensures external dependencies are met before allowing approval.
"""

import logging
import aiosqlite
from datetime import datetime
from typing import Tuple, List, Dict, Any, Optional

logger = logging.getLogger(__name__)


class WorkflowEnforcer:
    """
    Enforces application approval rules based on external dependency status.
    Prevents approval when critical external services are unavailable.
    """

    def __init__(self, db_path: str):
        """
        Initialize the workflow enforcer.

        Args:
            db_path: Path to SQLite database
        """
        self.db_path = db_path

    async def can_approve(self, application_id: str) -> Tuple[bool, List[str]]:
        """
        Check if an application can be approved.

        Args:
            application_id: Application ID

        Returns:
            Tuple of (can_approve: bool, blockers: list of blocking reasons)
        """
        blockers = []

        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Get application and verification statuses
                cursor = await db.execute(
                    "SELECT * FROM applications WHERE id = ?",
                    (application_id,)
                )
                app_row = await cursor.fetchone()

                if not app_row:
                    return False, ["Application not found"]

                # Extract relevant columns (adjust indices based on your schema)
                # Assuming: id, user_id, status, kyc_status, sanctions_status, company_status, memo_status, ...
                app_status = app_row[2] if len(app_row) > 2 else None
                kyc_status = app_row[3] if len(app_row) > 3 else None
                sanctions_status = app_row[4] if len(app_row) > 4 else None
                company_status = app_row[5] if len(app_row) > 5 else None
                memo_status = app_row[6] if len(app_row) > 6 else None

                # Check KYC status
                if kyc_status in ["pending_external_retry", "api_failure"]:
                    blockers.append(
                        f"KYC verification pending external retry (status: {kyc_status})"
                    )
                elif kyc_status is None or kyc_status == "":
                    blockers.append("KYC verification not completed")

                # Check sanctions screening
                # Must be completed (not simulated) and not failed
                if sanctions_status in ["simulated", "failed", "pending_external_retry"]:
                    blockers.append(
                        f"Sanctions screening not completed (status: {sanctions_status})"
                    )
                elif sanctions_status is None or sanctions_status == "":
                    blockers.append("Sanctions screening not completed")

                # Check company verification (if required)
                if company_status in ["pending_external_retry", "failed"]:
                    blockers.append(
                        f"Company verification failed (status: {company_status})"
                    )
                # Note: company verification can be empty if not applicable

                # Check compliance memo
                if memo_status in ["failed", "pending_external_retry"]:
                    blockers.append(
                        f"Compliance memo generation failed (status: {memo_status})"
                    )
                elif memo_status is None or memo_status == "":
                    blockers.append("Compliance memo not generated")

                # Check overall application status
                if app_status in ["pending_external_retry", "pending_manual_review", "blocked_external_dependency"]:
                    blockers.append(
                        f"Application in non-approvable state (status: {app_status})"
                    )

        except Exception as e:
            logger.error(f"Error checking approval eligibility: {e}", exc_info=True)
            blockers.append(f"Error checking eligibility: {str(e)}")

        can_approve = len(blockers) == 0
        return can_approve, blockers

    async def get_application_blockers(self, application_id: str) -> List[Dict[str, Any]]:
        """
        Get detailed blockers for an application.

        Args:
            application_id: Application ID

        Returns:
            List of blocker dicts with details
        """
        blockers = []

        try:
            async with aiosqlite.connect(self.db_path) as db:
                cursor = await db.execute(
                    "SELECT * FROM applications WHERE id = ?",
                    (application_id,)
                )
                app_row = await cursor.fetchone()

                if not app_row:
                    return [{"type": "not_found", "reason": "Application not found"}]

                # Build blocker details
                app_status = app_row[2] if len(app_row) > 2 else None
                kyc_status = app_row[3] if len(app_row) > 3 else None
                sanctions_status = app_row[4] if len(app_row) > 4 else None
                company_status = app_row[5] if len(app_row) > 5 else None
                memo_status = app_row[6] if len(app_row) > 6 else None

                # KYC blockers
                if kyc_status in ["pending_external_retry", "api_failure"]:
                    blockers.append({
                        "type": "kyc_external_retry",
                        "reason": "KYC verification pending external retry",
                        "status": kyc_status,
                        "action": "Retry external KYC provider"
                    })
                elif not kyc_status or kyc_status == "":
                    blockers.append({
                        "type": "kyc_incomplete",
                        "reason": "KYC verification not completed",
                        "action": "Complete KYC verification"
                    })

                # Sanctions screening blockers
                if sanctions_status in ["simulated", "failed", "pending_external_retry"]:
                    blockers.append({
                        "type": "sanctions_not_complete",
                        "reason": "Sanctions screening not completed with live provider",
                        "status": sanctions_status,
                        "action": "Complete live sanctions screening"
                    })
                elif not sanctions_status or sanctions_status == "":
                    blockers.append({
                        "type": "sanctions_incomplete",
                        "reason": "Sanctions screening not completed",
                        "action": "Complete sanctions screening"
                    })

                # Company verification blockers
                if company_status in ["pending_external_retry", "failed"]:
                    blockers.append({
                        "type": "company_verification_failed",
                        "reason": "Company verification failed",
                        "status": company_status,
                        "action": "Retry company verification or proceed manually"
                    })

                # Compliance memo blockers
                if memo_status in ["failed", "pending_external_retry"]:
                    blockers.append({
                        "type": "memo_generation_failed",
                        "reason": "Compliance memo generation failed",
                        "status": memo_status,
                        "action": "Retry memo generation or generate manually"
                    })
                elif not memo_status or memo_status == "":
                    blockers.append({
                        "type": "memo_incomplete",
                        "reason": "Compliance memo not generated",
                        "action": "Generate compliance memo"
                    })

                # Application status blockers
                if app_status in ["pending_external_retry", "pending_manual_review", "blocked_external_dependency"]:
                    blockers.append({
                        "type": "application_status_blocked",
                        "reason": f"Application in {app_status} state",
                        "status": app_status,
                        "action": "Resolve external dependencies or route to manual review"
                    })

        except Exception as e:
            logger.error(f"Error getting blockers: {e}", exc_info=True)
            blockers.append({
                "type": "error",
                "reason": f"Error checking blockers: {str(e)}"
            })

        return blockers

    async def route_to_manual_review(
        self,
        application_id: str,
        reason: str
    ) -> None:
        """
        Route an application to manual review.

        Args:
            application_id: Application ID
            reason: Reason for manual review
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                now = datetime.utcnow().isoformat() + "Z"

                await db.execute(
                    """
                    UPDATE applications
                    SET status = ?, notes = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    ("pending_manual_review", reason, now, application_id)
                )
                await db.commit()

                logger.info(f"Application {application_id} routed to manual review: {reason}")

        except Exception as e:
            logger.error(f"Failed to route to manual review: {e}", exc_info=True)
            raise

    async def mark_external_dependency_blocked(
        self,
        application_id: str,
        provider: str,
        reason: str
    ) -> None:
        """
        Mark an application as blocked due to external dependency.

        Args:
            application_id: Application ID
            provider: Provider name
            reason: Blocking reason
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                now = datetime.utcnow().isoformat() + "Z"

                msg = f"Blocked: {provider} unavailable - {reason}"
                await db.execute(
                    """
                    UPDATE applications
                    SET status = ?, notes = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    ("blocked_external_dependency", msg, now, application_id)
                )
                await db.commit()

                logger.warning(f"Application {application_id} blocked by {provider}: {reason}")

        except Exception as e:
            logger.error(f"Failed to mark as external dependency blocked: {e}", exc_info=True)
            raise

    async def update_verification_status(
        self,
        application_id: str,
        verification_type: str,
        status: str,
        details: Optional[Dict[str, Any]] = None
    ) -> None:
        """
        Update specific verification status.

        Args:
            application_id: Application ID
            verification_type: Type of verification (kyc, sanctions, company, memo)
            status: New status
            details: Optional details to store
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                now = datetime.utcnow().isoformat() + "Z"

                if verification_type == "kyc":
                    column = "kyc_status"
                elif verification_type == "sanctions":
                    column = "sanctions_status"
                elif verification_type == "company":
                    column = "company_status"
                elif verification_type == "memo":
                    column = "memo_status"
                else:
                    raise ValueError(f"Unknown verification type: {verification_type}")

                await db.execute(
                    f"""
                    UPDATE applications
                    SET {column} = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (status, now, application_id)
                )
                await db.commit()

                logger.info(f"Application {application_id} {verification_type} status updated to {status}")

        except Exception as e:
            logger.error(f"Failed to update verification status: {e}", exc_info=True)
            raise

    # New status values to be added to the application model
    VALID_STATUSES = [
        "draft",
        "submitted",
        "under_review",
        "pending_kyc",
        "pending_sanctions_screening",
        "pending_company_verification",
        "pending_memo_generation",
        "pending_external_retry",      # NEW: Waiting for external API retry
        "pending_manual_review",         # NEW: Needs manual officer review
        "blocked_external_dependency",   # NEW: Blocked by external service failure
        "api_failure",                   # NEW: External API failed
        "approved",
        "rejected",
        "dormant",
    ]

    # New verification status values
    VALID_VERIFICATION_STATUSES = {
        "kyc": [
            "not_started",
            "in_progress",
            "completed",
            "failed",
            "pending_external_retry",
            "api_failure",
        ],
        "sanctions": [
            "not_started",
            "simulated",
            "in_progress",
            "completed",
            "failed",
            "pending_external_retry",
        ],
        "company": [
            "not_required",
            "not_started",
            "in_progress",
            "completed",
            "failed",
            "pending_external_retry",
        ],
        "memo": [
            "not_started",
            "in_progress",
            "completed",
            "failed",
            "pending_external_retry",
        ],
    }
