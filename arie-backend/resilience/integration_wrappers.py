#!/usr/bin/env python3
"""
Wrapped versions of external API clients with resilience integration.
Handles failures appropriately based on service type (blocking vs. non-blocking).
"""

import logging
import aiosqlite
from datetime import datetime, timezone
from typing import Optional, Dict, Any

from .resilient_client import ResilientAPIClient

logger = logging.getLogger(__name__)


class ApplicationStatusUpdater:
    """Helper to update application status in the database."""

    def __init__(self, db_path: str):
        self.db_path = db_path

    async def set_status(
        self,
        application_id: str,
        status: str,
        notes: Optional[str] = None
    ) -> None:
        """
        Update application status.

        Args:
            application_id: Application ID
            status: New status
            notes: Optional status notes
        """
        try:
            async with aiosqlite.connect(self.db_path) as db:
                now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

                await db.execute(
                    """
                    UPDATE applications
                    SET status = ?, notes = ?, updated_at = ?
                    WHERE id = ?
                    """,
                    (status, notes, now, application_id)
                )
                await db.commit()

                logger.info(f"Application {application_id} status updated to {status}")

        except Exception as e:
            logger.error(f"Failed to update application status: {e}", exc_info=True)


class ResilientSumsubClient:
    """
    Wraps Sumsub KYC client with resilience patterns.
    KYC failures are BLOCKING - cannot proceed without successful verification.
    """

    def __init__(self, db_path: str, sumsub_client=None):
        """
        Initialize Sumsub wrapper.

        Args:
            db_path: Path to database
            sumsub_client: Existing SumsubClient instance
        """
        self.db_path = db_path
        self.sumsub_client = sumsub_client
        self.resilient_client = ResilientAPIClient(db_path)
        self.status_updater = ApplicationStatusUpdater(db_path)

    async def verify_kyc(
        self,
        application_id: str,
        applicant_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Verify KYC with resilience.

        Args:
            application_id: Application ID
            applicant_data: Applicant information

        Returns:
            Result dict with verification status
        """
        result = await self.resilient_client.call(
            provider="sumsub",
            endpoint="/kyc/verify",
            func=self._do_verify_kyc,
            application_id=application_id,
            task_type="kyc_verification",
            method="POST",
            applicant_data=applicant_data
        )

        if not result["success"]:
            # Mark application as pending external retry (blocking)
            await self.status_updater.set_status(
                application_id,
                "pending_external_retry",
                f"Sumsub KYC verification failed: {result['error']}"
            )

            logger.warning(f"KYC verification failed for {application_id}: {result['error']}")

        return result

    async def _do_verify_kyc(self, applicant_data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute actual KYC verification."""
        if self.sumsub_client is None:
            raise RuntimeError("Sumsub client not configured")

        # Call the actual Sumsub client
        return await self.sumsub_client.verify_applicant(applicant_data)


class ResilientOpenSanctionsClient:
    """
    Wraps Open Sanctions client with resilience patterns.
    Sanctions screening is NON-BLOCKING but MUST NOT be skipped.
    On failure, application goes to manual review.
    """

    def __init__(self, db_path: str, sanctions_client=None):
        """
        Initialize Open Sanctions wrapper.

        Args:
            db_path: Path to database
            sanctions_client: Existing sanctions client instance
        """
        self.db_path = db_path
        self.sanctions_client = sanctions_client
        self.resilient_client = ResilientAPIClient(db_path)
        self.status_updater = ApplicationStatusUpdater(db_path)

    async def screen_entity(
        self,
        application_id: str,
        name: str,
        country: Optional[str] = None,
        entity_type: str = "company"
    ) -> Dict[str, Any]:
        """
        Screen entity against sanctions lists.

        Args:
            application_id: Application ID
            name: Entity name
            country: Country code
            entity_type: Type of entity (company, person)

        Returns:
            Result dict with screening results
        """
        result = await self.resilient_client.call(
            provider="open_sanctions",
            endpoint="/screen/entity",
            func=self._do_screen_entity,
            application_id=application_id,
            task_type="sanctions_screening",
            method="GET",
            name=name,
            country=country,
            entity_type=entity_type
        )

        if not result["success"]:
            # Mark application as pending manual review (non-blocking)
            await self.status_updater.set_status(
                application_id,
                "pending_manual_review",
                f"Sanctions screening failed and requires manual review: {result['error']}"
            )

            logger.warning(f"Sanctions screening failed for {application_id}: {result['error']}")

        return result

    async def screen_person(
        self,
        application_id: str,
        first_name: str,
        last_name: str,
        country: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Screen person against sanctions lists.

        Args:
            application_id: Application ID
            first_name: First name
            last_name: Last name
            country: Country code

        Returns:
            Result dict with screening results
        """
        result = await self.resilient_client.call(
            provider="open_sanctions",
            endpoint="/screen/person",
            func=self._do_screen_person,
            application_id=application_id,
            task_type="sanctions_screening",
            method="GET",
            first_name=first_name,
            last_name=last_name,
            country=country
        )

        if not result["success"]:
            await self.status_updater.set_status(
                application_id,
                "pending_manual_review",
                f"Person sanctions screening failed: {result['error']}"
            )

        return result

    async def _do_screen_entity(
        self,
        name: str,
        country: Optional[str],
        entity_type: str
    ) -> Dict[str, Any]:
        """Execute actual entity screening."""
        if self.sanctions_client is None:
            raise RuntimeError("Sanctions client not configured")

        return await self.sanctions_client.screen_entity(name, country, entity_type)

    async def _do_screen_person(
        self,
        first_name: str,
        last_name: str,
        country: Optional[str]
    ) -> Dict[str, Any]:
        """Execute actual person screening."""
        if self.sanctions_client is None:
            raise RuntimeError("Sanctions client not configured")

        return await self.sanctions_client.screen_person(first_name, last_name, country)


class ResilientOpenCorporatesClient:
    """
    Wraps OpenCorporates client with resilience patterns.
    Company verification failures are NON-BLOCKING.
    On failure, application can still proceed but is marked for manual review.
    """

    def __init__(self, db_path: str, corporates_client=None):
        """
        Initialize OpenCorporates wrapper.

        Args:
            db_path: Path to database
            corporates_client: Existing OpenCorporates client instance
        """
        self.db_path = db_path
        self.corporates_client = corporates_client
        self.resilient_client = ResilientAPIClient(db_path)
        self.status_updater = ApplicationStatusUpdater(db_path)

    async def lookup_company(
        self,
        application_id: str,
        jurisdiction: str,
        company_number: str
    ) -> Dict[str, Any]:
        """
        Look up company by jurisdiction and company number.

        Args:
            application_id: Application ID
            jurisdiction: Company jurisdiction
            company_number: Company registration number

        Returns:
            Result dict with company data
        """
        result = await self.resilient_client.call(
            provider="opencorporates",
            endpoint="/lookup",
            func=self._do_lookup_company,
            application_id=application_id,
            task_type="company_verification",
            method="GET",
            jurisdiction=jurisdiction,
            company_number=company_number
        )

        if not result["success"]:
            # Non-blocking, but mark as pending external retry
            await self.status_updater.set_status(
                application_id,
                "pending_external_retry",
                f"Company lookup failed: {result['error']}"
            )

            logger.warning(f"Company lookup failed for {application_id}: {result['error']}")

        return result

    async def search_companies(
        self,
        application_id: str,
        query: str
    ) -> Dict[str, Any]:
        """
        Search for companies.

        Args:
            application_id: Application ID
            query: Search query

        Returns:
            Result dict with search results
        """
        result = await self.resilient_client.call(
            provider="opencorporates",
            endpoint="/search",
            func=self._do_search_companies,
            application_id=application_id,
            task_type="company_verification",
            method="GET",
            query=query
        )

        if not result["success"]:
            await self.status_updater.set_status(
                application_id,
                "pending_external_retry",
                f"Company search failed: {result['error']}"
            )

        return result

    async def _do_lookup_company(
        self,
        jurisdiction: str,
        company_number: str
    ) -> Dict[str, Any]:
        """Execute actual company lookup."""
        if self.corporates_client is None:
            raise RuntimeError("OpenCorporates client not configured")

        return await self.corporates_client.lookup_company(jurisdiction, company_number)

    async def _do_search_companies(self, query: str) -> Dict[str, Any]:
        """Execute actual company search."""
        if self.corporates_client is None:
            raise RuntimeError("OpenCorporates client not configured")

        return await self.corporates_client.search_companies(query)


class ResilientClaudeClient:
    """
    Wraps Claude AI client with resilience patterns.
    Memo generation failures are BLOCKING for approval.
    On failure, application is marked as pending manual review.
    """

    def __init__(self, db_path: str, claude_client=None):
        """
        Initialize Claude wrapper.

        Args:
            db_path: Path to database
            claude_client: Existing ClaudeClient instance
        """
        self.db_path = db_path
        self.claude_client = claude_client
        self.resilient_client = ResilientAPIClient(db_path)
        self.status_updater = ApplicationStatusUpdater(db_path)

    async def generate_compliance_memo(
        self,
        application_id: str,
        application_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Generate compliance memo with AI.

        Args:
            application_id: Application ID
            application_data: Application information

        Returns:
            Result dict with memo
        """
        result = await self.resilient_client.call(
            provider="claude",
            endpoint="/generate_memo",
            func=self._do_generate_memo,
            application_id=application_id,
            task_type="memo_generation",
            method="POST",
            application_data=application_data
        )

        if not result["success"]:
            # Blocking failure - memo generation required for approval
            await self.status_updater.set_status(
                application_id,
                "pending_manual_review",
                f"Compliance memo generation failed: {result['error']}"
            )

            logger.warning(f"Memo generation failed for {application_id}: {result['error']}")

        return result

    async def score_risk(
        self,
        application_id: str,
        application_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Score risk with AI.

        Args:
            application_id: Application ID
            application_data: Application information

        Returns:
            Result dict with risk score
        """
        result = await self.resilient_client.call(
            provider="claude",
            endpoint="/score_risk",
            func=self._do_score_risk,
            application_id=application_id,
            task_type="risk_scoring",
            method="POST",
            application_data=application_data
        )

        if not result["success"]:
            await self.status_updater.set_status(
                application_id,
                "pending_manual_review",
                f"Risk scoring failed: {result['error']}"
            )

        return result

    async def _do_generate_memo(self, application_data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute actual memo generation."""
        if self.claude_client is None:
            raise RuntimeError("Claude client not configured")

        return await self.claude_client.generate_compliance_memo(application_data)

    async def _do_score_risk(self, application_data: Dict[str, Any]) -> Dict[str, Any]:
        """Execute actual risk scoring."""
        if self.claude_client is None:
            raise RuntimeError("Claude client not configured")

        return await self.claude_client.score_risk(application_data)
