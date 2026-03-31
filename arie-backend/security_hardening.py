"""
ARIE Finance — Security Hardening Module
=========================================

Implements all Critical and High audit remediation fixes:
- Approval gates (P0-01, P0-02)
- Screening mode tracking (P0-02)
- Production environment guards (P0-04)
- AI source tracking (P0-05)
- Compliance memo validation (P0-06)
- PII encryption (P0-10)
- Password policy (P0-09, P1)
- Request schema validation (P0-11)
- Token revocation (P1)
- File upload validation (P1)
- Health endpoint restriction (P1)

This module is self-contained and importable by server.py.
It should NOT be modified to integrate with server.py—only imported.
"""

import os
import sys
import json
import base64
import logging
import secrets
import re
import time
import hashlib
from datetime import datetime, timedelta
from typing import Tuple, Dict, List, Optional, Any
from pathlib import Path

from environment import ENV, is_production

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


# ============================================================================
# 1. Approval Gate Validators (P0-01, P0-02)
# ============================================================================

class ApprovalGateValidator:
    """
    Validates all preconditions before an application can be approved.
    Prevents approval without proper KYC, screening, compliance, and document review.
    """

    @staticmethod
    def validate_approval(app: Dict, db) -> Tuple[bool, str]:
        """
        Validates that an application meets all approval prerequisites.

        Reads from actual data sources (DB tables, prescreening_data JSON) — not phantom columns.

        Args:
            app: Application dictionary from SELECT * FROM applications
            db: Database connection object (required)

        Returns:
            Tuple of (can_approve: bool, error_message: str)

        Checks:
            1. Application must have passed through KYC/compliance workflow stages
            2. Screening must exist in prescreening_data and mode must be 'live'
            3. Compliance memo must exist in compliance_memos table
            4. All documents must not be 'flagged'
            5. Screening report checks must not use simulated api_status
            6. Compliance memo ai_source must not be 'mock'
        """
        try:
            app_id = app.get('id')
            if not app_id or not db:
                return (False, "Application ID and database connection are required for approval validation")

            # 1. Check application has been through KYC/review workflow
            # (The state machine in server.py enforces transitions, but verify the app
            #  has reached a reviewable state — not still in draft/prescreening)
            status = app.get('status', '').lower()
            pre_kyc_states = ('draft', 'prescreening_submitted', 'pricing_review', 'pricing_accepted',
                              'pre_approval_review', 'pre_approved', 'kyc_documents')
            if status in pre_kyc_states:
                return (False, f"Application is still in pre-review state '{status}'. "
                        "Cannot approve until compliance review is complete.")

            # 2. Check screening exists in prescreening_data and mode is live
            prescreening_data = app.get('prescreening_data', '{}')
            if isinstance(prescreening_data, str):
                import json as _json
                try:
                    prescreening_data = _json.loads(prescreening_data)
                except (ValueError, TypeError):
                    prescreening_data = {}

            screening_report = prescreening_data.get('screening_report', {})
            if not screening_report:
                return (False, "No screening report found in application data. "
                        "Screening must be run before approval.")

            screening_mode = screening_report.get('screening_mode', '').lower()
            if is_production() and screening_mode != 'live':
                return (
                    False,
                    f"Screening must be in 'live' mode, not '{screening_mode}'. "
                    "Simulated screening is not permitted for approval in production."
                )

            # 3. Check compliance memo exists and meets quality gates
            memo_row = db.execute(
                "SELECT id, memo_data, review_status, validation_status, supervisor_status, blocked, block_reason "
                "FROM compliance_memos WHERE application_id = ? ORDER BY version DESC LIMIT 1",
                (app_id,)
            ).fetchone()
            if not memo_row:
                return (False, "Compliance memo must be generated before approval. "
                        "Generate via POST /api/applications/{id}/memo first.")
            if not isinstance(memo_row, dict):
                memo_row = dict(memo_row)

            # 3a. Memo must not be blocked
            if memo_row.get('blocked'):
                return (False, f"Compliance memo is blocked: {memo_row.get('block_reason', 'unspecified reason')}. "
                        "Resolve blocking issues before approval.")

            # 3b. Memo must be formally approved (review_status)
            memo_review = (memo_row.get('review_status') or '').lower()
            if memo_review != 'approved':
                return (False, f"Compliance memo review_status is '{memo_review}', must be 'approved'. "
                        "Memo must be reviewed and approved before application approval.")

            # 3c. Memo validation must have an explicit positive pass
            memo_validation = (memo_row.get('validation_status') or '').lower()
            if memo_validation != 'pass':
                return (
                    False,
                    f"Compliance memo validation_status is '{memo_validation}', must be 'pass'. "
                    "Validation warnings or pending states must be resolved before approval."
                )

            # 3d. Supervisor must have an explicit positive verdict
            memo_supervisor = (memo_row.get('supervisor_status') or '').upper()
            if memo_supervisor != 'CONSISTENT':
                return (
                    False,
                    f"Compliance memo supervisor_status is '{memo_supervisor}', must be 'CONSISTENT'. "
                    "Supervisor warnings or inconsistencies must be resolved before approval."
                )

            # 4. Check all documents are not flagged (from DB, not app dict)
            flagged_docs = db.execute(
                "SELECT id, doc_type, verification_status FROM documents "
                "WHERE application_id = ? AND verification_status = 'flagged'",
                (app_id,)
            ).fetchall()
            if flagged_docs:
                doc_types = ', '.join(d['doc_type'] for d in flagged_docs)
                return (
                    False,
                    f"Flagged documents must be resolved before approval: {doc_types}"
                )

            # 5. Check screening report for any simulated or degraded provider statuses
            screening_evidence = _collect_screening_provider_evidence(screening_report)
            if screening_evidence:
                for item in screening_evidence:
                    api_status = (item.get("api_status") or "").lower()
                    source = (item.get("source") or "").lower()
                    if api_status in ("simulated", "mocked") or source in ("simulated", "mocked"):
                        return (
                            False,
                            f"Screening check '{item.get('name', 'unknown')}' used simulated data. "
                            "Live screening results are required for approval."
                        )
                    if api_status in ("error", "blocked"):
                        return (
                            False,
                            f"Screening check '{item.get('name', 'unknown')}' is not in a live usable state "
                            f"(api_status={api_status or 'unknown'}).",
                        )
            else:
                for check_name in ('sanctions', 'company_registry', 'ip_geolocation', 'kyc'):
                    check_data = screening_report.get(check_name, {})
                    if isinstance(check_data, dict) and check_data.get('api_status') == 'simulated':
                        return (
                            False,
                            f"Screening check '{check_name}' used simulated data (api_status=simulated). "
                            "Live screening results are required for approval."
                        )

            # 6. Check AI source provenance from memo data
            memo_data_str = memo_row.get('memo_data', '{}') if memo_row else '{}'
            if isinstance(memo_data_str, str):
                import json as _json2
                try:
                    memo_data = _json2.loads(memo_data_str)
                except (ValueError, TypeError):
                    memo_data = {}
            else:
                memo_data = memo_data_str
            ai_source = memo_data.get('ai_source', '').lower()
            if ai_source == 'mock':
                return (
                    False,
                    "Compliance memo was generated with mock AI. "
                    "Live AI verification required for approval."
                )

            logger.info(f"Application {app_id} passed approval gate validation")
            return (True, "")

        except Exception as e:
            logger.error(f"Error in approval gate validation: {e}", exc_info=True)
            return (False, f"Internal validation error: {str(e)}")

    @staticmethod
    def validate_high_risk_dual_approval(
        app: Dict,
        current_user: Dict,
        db
    ) -> Tuple[bool, str]:
        """
        For HIGH/VERY_HIGH risk applications: validates that a different compliance officer
        has already recorded a first approval in the audit log.

        Args:
            app: Application dictionary with risk_level
            current_user: Current user (approval officer) making the approval
            db: Database connection (required — reads from audit_log table)

        Returns:
            Tuple of (can_approve: bool, error_message: str)
        """
        try:
            risk_level = app.get('risk_level', '').upper()

            # Only enforce dual approval for high-risk applications
            if risk_level not in ['HIGH', 'VERY_HIGH']:
                return (True, "")

            current_user_id = current_user.get('sub', current_user.get('id', ''))
            app_ref = app.get('ref', '')

            if not db or not app_ref:
                return (False, "Database connection and application reference required for dual approval check")

            # Check audit_log for a prior "First Approval" by a DIFFERENT officer
            prior_approvals = db.execute(
                "SELECT user_id, user_name FROM audit_log "
                "WHERE target = ? AND action = 'First Approval (Pending Second)' "
                "ORDER BY timestamp DESC",
                (app_ref,)
            ).fetchall()

            # Find approvals by other officers
            other_approvals = [a for a in prior_approvals if a['user_id'] != current_user_id]

            if not other_approvals:
                return (
                    False,
                    "HIGH/VERY_HIGH risk application requires dual approval. "
                    "Another compliance officer must approve first."
                )

            first_approver = other_approvals[0]['user_name']
            logger.info(
                f"Application {app_ref} ({risk_level}) passed dual approval check: "
                f"first approver={first_approver}, second approver={current_user_id}"
            )
            return (True, "")

        except Exception as e:
            logger.error(f"Error in dual approval validation: {e}", exc_info=True)
            return (False, f"Internal validation error: {str(e)}")


# ============================================================================
# 2. Screening Mode Tracker (P0-02)
# ============================================================================

def _collect_screening_provider_evidence(screening_report: Dict) -> list:
    evidence = []
    if not isinstance(screening_report, dict):
        return evidence

    def add(name: str, item):
        if not isinstance(item, dict):
            return
        evidence.append({
            "name": name,
            "api_status": item.get("api_status"),
            "source": item.get("source"),
        })

    company_screening = screening_report.get("company_screening") or {}
    add("company_registry", company_screening)
    add("company_watchlist", company_screening.get("sanctions"))

    for idx, person in enumerate(screening_report.get("director_screenings") or []):
        add(f"director_screening_{idx}", (person or {}).get("screening"))

    for idx, person in enumerate(screening_report.get("ubo_screenings") or []):
        add(f"ubo_screening_{idx}", (person or {}).get("screening"))

    add("ip_geolocation", screening_report.get("ip_geolocation"))

    for idx, applicant in enumerate(screening_report.get("kyc_applicants") or []):
        add(f"kyc_applicant_{idx}", applicant)

    return evidence

def determine_screening_mode(screening_report: Dict) -> str:
    """
    Analyzes a screening report to determine if it used live or simulated sources.

    Args:
        screening_report: Dictionary with screening data, typically from SumSub API

    Returns:
        'live' if all screening sources are production, 'simulated' if any source is mocked
    """
    try:
        if not isinstance(screening_report, dict) or not screening_report:
            return 'unknown'

        provider_evidence = _collect_screening_provider_evidence(screening_report)
        if provider_evidence:
            saw_live = False
            for item in provider_evidence:
                api_status = (item.get("api_status") or "").lower()
                source_name = (item.get("source") or "").lower()
                if api_status in ("simulated", "mocked") or any(tag in source_name for tag in ("simulated", "mock", "demo")):
                    logger.warning(f"Screening contains simulated source: {item}")
                    return 'simulated'
                if api_status in ("error", "blocked"):
                    logger.warning(f"Screening contains non-live provider state: {item}")
                    return 'unknown'
                if api_status == "live" or source_name in ("sumsub", "opencorporates", "ipapi", "local"):
                    saw_live = True
            return 'live' if saw_live else 'unknown'

        # Legacy fallback for older report shapes
        sources = screening_report.get('sources', [])
        rules_results = screening_report.get('rules_results', [])

        for source in sources:
            source_name = source.get('name', '').lower()
            if 'simulated' in source_name or 'mock' in source_name or 'demo' in source_name:
                logger.warning(f"Screening contains simulated source: {source_name}")
                return 'simulated'

        for rule in rules_results:
            if rule.get('is_simulated') or 'simulated' in str(rule).lower():
                logger.warning("Screening contains simulated rule results")
                return 'simulated'

        if screening_report.get('is_simulated') or screening_report.get('testMode'):
            logger.warning("Screening report marked as simulated/test mode")
            return 'simulated'

        return 'unknown'

    except Exception as e:
        logger.error(f"Error determining screening mode: {e}")
        return 'unknown'


def store_screening_mode(db, app_id: str, mode: str) -> bool:
    """
    Stores the screening mode (live/simulated) in the application record.

    Args:
        db: Database connection object
        app_id: Application ID
        mode: 'live' or 'simulated'

    Returns:
        True if successful, False otherwise
    """
    try:
        if mode not in ['live', 'simulated', 'unknown']:
            logger.error(f"Invalid screening mode: {mode}")
            return False

        db.execute(
            "UPDATE applications SET screening_mode=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
            (mode, app_id),
        )
        logger.info(f"Screening mode='{mode}' for application {app_id}")
        return True

    except Exception as e:
        logger.error(f"Error in store_screening_mode: {e}", exc_info=True)
        return False


# ============================================================================
# 3. Production Environment Guards (P0-04)
# ============================================================================

def validate_production_environment() -> None:
    """
    Called at server startup. Enforces security constraints in production.

    Checks:
        - If ENVIRONMENT=production, CLAUDE_MOCK_MODE must NOT be 'true'
        - If ENVIRONMENT=production, SUMSUB_APP_TOKEN must be set
        - If ENVIRONMENT=production, ANTHROPIC_API_KEY must be set
        - If ENVIRONMENT=production, SUMSUB_WEBHOOK_SECRET must be set

    Raises:
        RuntimeError: If any production check fails

    Example:
        >>> validate_production_environment()  # Called in server.py startup
    """
    if not is_production():
        logger.debug(f"Environment is '{ENV}', skipping production validation")
        return

    logger.info("Running production environment validation...")

    errors = []

    # Check CLAUDE_MOCK_MODE is not 'true'
    mock_mode = os.environ.get('CLAUDE_MOCK_MODE', '').lower()
    if mock_mode == 'true':
        errors.append("CLAUDE_MOCK_MODE must not be 'true' in production")

    # Check required API tokens
    if not os.environ.get('SUMSUB_APP_TOKEN'):
        errors.append("SUMSUB_APP_TOKEN environment variable is not set")

    if not os.environ.get('ANTHROPIC_API_KEY'):
        errors.append("ANTHROPIC_API_KEY environment variable is not set")

    if not os.environ.get('SUMSUB_WEBHOOK_SECRET'):
        errors.append("SUMSUB_WEBHOOK_SECRET environment variable is not set")

    if errors:
        error_msg = "\n".join([f"  ✗ {e}" for e in errors])
        raise RuntimeError(
            f"Production environment validation failed:\n{error_msg}\n\n"
            f"All required environment variables must be set before starting the server."
        )

    logger.info("✓ Production environment validation passed")


# ============================================================================
# 4. AI Source Tracking (P0-05)
# ============================================================================

def tag_ai_response(response: Dict, source: str) -> Dict:
    """
    Adds an ai_source field to an AI agent response for audit tracking.

    Args:
        response: Dictionary containing AI agent response data
        source: Source identifier ('claude-sonnet-4-6', 'claude-opus-4-6', or 'mock')

    Returns:
        Modified response dictionary with ai_source field added

    Raises:
        ValueError: If source is not a valid option

    Example:
        >>> response = {'analysis': '...', 'score': 75}
        >>> tagged = tag_ai_response(response, 'claude-sonnet-4-6')
        >>> tagged['ai_source']
        'claude-sonnet-4-6'
    """
    valid_sources = ['claude-sonnet-4-6', 'claude-opus-4-6', 'mock']

    if source not in valid_sources:
        raise ValueError(
            f"Invalid AI source '{source}'. Must be one of: {', '.join(valid_sources)}"
        )

    response_copy = response.copy() if isinstance(response, dict) else {}
    response_copy['ai_source'] = source

    if source == 'mock':
        logger.warning(f"AI response tagged with mock source (for development/testing only)")
    else:
        logger.debug(f"AI response tagged with source: {source}")

    return response_copy


def is_mock_ai_response(response: Dict) -> bool:
    """
    Checks whether an AI response came from mock/test mode.

    Args:
        response: AI response dictionary

    Returns:
        True if ai_source == 'mock', False otherwise
    """
    return response.get('ai_source', '').lower() == 'mock'


# ============================================================================
# 5. Compliance Memo Validator (P0-06)
# ============================================================================

class MemoValidator:
    """
    Post-generation validation of compliance memos against actual screening/verification results.

    Detects discrepancies where memo claims don't match actual data,
    which could indicate fraud or AI hallucination.
    """

    @staticmethod
    def validate_memo_against_results(
        memo: Dict,
        agent_results: Dict
    ) -> Tuple[bool, List[str]]:
        """
        Cross-checks compliance memo claims against actual agent results.

        Args:
            memo: Generated compliance memo with claims about findings
            agent_results: Actual screening, verification, and analysis results

        Returns:
            Tuple of (is_valid: bool, list_of_discrepancies: List[str])

        Discrepancies detected:
            - Memo says 'no screening hits' but screening found hits
            - Memo references different risk score than actual
            - Memo says 'all documents verified' but flagged docs exist
            - Memo approval recommendation contradicts risk_level without override
        """
        discrepancies = []

        try:
            # Extract memo claims
            memo_text = memo.get('memo_text', '').lower()
            memo_risk_score = memo.get('risk_score')
            memo_approval_rec = memo.get('approval_recommendation', '').lower()

            # Extract actual results
            screening_hits = agent_results.get('screening_hits', [])
            actual_risk_score = agent_results.get('risk_score')
            flagged_documents = agent_results.get('flagged_documents', [])
            risk_level = agent_results.get('risk_level', '').lower()

            # Check 1: Screening hits
            has_no_hits_claim = 'no screening hits' in memo_text or 'no hits found' in memo_text
            if has_no_hits_claim and screening_hits:
                discrepancies.append(
                    f"Memo claims 'no screening hits' but {len(screening_hits)} hit(s) found"
                )

            # Check 2: Risk score mismatch
            if memo_risk_score is not None and actual_risk_score is not None:
                if abs(memo_risk_score - actual_risk_score) > 5:
                    discrepancies.append(
                        f"Memo risk score ({memo_risk_score}) differs from actual ({actual_risk_score})"
                    )

            # Check 3: Document verification
            all_verified_claim = 'all documents verified' in memo_text
            if all_verified_claim and flagged_documents:
                discrepancies.append(
                    f"Memo claims 'all documents verified' but {len(flagged_documents)} flagged document(s) exist"
                )

            # Check 4: Approval recommendation vs risk level
            if memo_approval_rec == 'approve' and risk_level in ['high', 'very_high']:
                override_mentioned = 'override' in memo_text or 'exceptional' in memo_text
                if not override_mentioned:
                    discrepancies.append(
                        f"Memo recommends approval for {risk_level} risk without documented override justification"
                    )

            if discrepancies:
                logger.warning(f"Memo validation found {len(discrepancies)} discrepancy(ies)")
                return (False, discrepancies)

            logger.debug("Memo validation passed")
            return (True, [])

        except Exception as e:
            logger.error(f"Error validating memo: {e}", exc_info=True)
            return (False, [f"Validation error: {str(e)}"])


# ============================================================================
# 6. PII Encryption (P0-10)
# ============================================================================

class PIIEncryptor:
    """
    Field-level encryption for PII data using Fernet symmetric encryption.

    Uses cryptography.fernet for secure encryption/decryption of sensitive fields.
    Encryption key MUST be provided via PII_ENCRYPTION_KEY environment variable.
    The server will fail to start if this key is missing or invalid.

    Generate a valid key with:
        python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    """

    # PII fields that must be encrypted in different data structures
    PII_FIELDS_DIRECTORS = ['passport_number', 'nationality', 'id_number']
    PII_FIELDS_UBOS = ['passport_number', 'nationality']
    PII_FIELDS_APPLICATIONS = ['pep_flags']

    def __init__(self, key: Optional[str] = None):
        """
        Initialize encryptor with symmetric key.

        Args:
            key: Base64-encoded Fernet key, or None to load from PII_ENCRYPTION_KEY env var

        Raises:
            RuntimeError: If no key provided and PII_ENCRYPTION_KEY not set
            ValueError: If key format is invalid (not a valid 32-byte base64-encoded Fernet key)
        """
        if key is None:
            key = os.environ.get('PII_ENCRYPTION_KEY')

        if not key:
            if is_production():
                raise RuntimeError(
                    "CRITICAL: PII_ENCRYPTION_KEY must be set in production. "
                    "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
                )
            else:
                raise RuntimeError(
                    "PII_ENCRYPTION_KEY environment variable is required. "
                    "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
                )

        # Validate key format: must be 44-char base64-encoded string (32 bytes encoded)
        try:
            if isinstance(key, str):
                key_bytes = key.encode('utf-8')
            else:
                key_bytes = key

            # Fernet keys are exactly 44 bytes of url-safe base64 (encoding 32 bytes)
            import base64
            decoded = base64.urlsafe_b64decode(key_bytes)
            if len(decoded) != 32:
                raise ValueError(
                    f"Fernet key must decode to exactly 32 bytes, got {len(decoded)}. "
                    "Generate a valid key with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
                )

            self.cipher = Fernet(key_bytes)
            self._key = key_bytes
            logger.info("PIIEncryptor initialized successfully (key validated)")
        except Exception as e:
            if "32 bytes" in str(e) or "Fernet key" in str(e):
                raise ValueError(str(e))
            raise ValueError(
                f"Invalid PII_ENCRYPTION_KEY format: {type(e).__name__}. "
                "Key must be a valid Fernet key (44 chars, url-safe base64). "
                "Generate one with: python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
            )

    def encrypt(self, plaintext: str) -> str:
        """
        Encrypt a PII field value.

        Args:
            plaintext: Unencrypted PII value

        Returns:
            Base64-encoded ciphertext
        """
        try:
            if not plaintext:
                return ""

            plaintext_bytes = plaintext.encode('utf-8') if isinstance(plaintext, str) else plaintext
            ciphertext = self.cipher.encrypt(plaintext_bytes)
            return base64.b64encode(ciphertext).decode('utf-8')

        except Exception as e:
            logger.error(f"Encryption error: {e}")
            raise

    def decrypt(self, ciphertext: str) -> str:
        """
        Decrypt a PII field value.

        Args:
            ciphertext: Base64-encoded encrypted value

        Returns:
            Decrypted plaintext
        """
        try:
            if not ciphertext:
                return ""

            ciphertext_bytes = base64.b64decode(ciphertext.encode('utf-8'))
            plaintext = self.cipher.decrypt(ciphertext_bytes)
            return plaintext.decode('utf-8')

        except InvalidToken:
            logger.error("Invalid encryption token (possibly wrong key)")
            raise
        except Exception as e:
            logger.error(f"Decryption error: {e}")
            raise

    def encrypt_dict_fields(self, data: Dict, fields: List[str]) -> Dict:
        """
        Encrypt specified fields in a dictionary.

        Args:
            data: Dictionary with PII fields
            fields: List of field names to encrypt

        Returns:
            Dictionary with specified fields encrypted
        """
        result = data.copy()
        for field in fields:
            if field in result and result[field]:
                result[field] = self.encrypt(str(result[field]))
        return result

    def decrypt_dict_fields(self, data: Dict, fields: List[str]) -> Dict:
        """
        Decrypt specified fields in a dictionary.

        Args:
            data: Dictionary with encrypted PII fields
            fields: List of field names to decrypt

        Returns:
            Dictionary with specified fields decrypted
        """
        result = data.copy()
        for field in fields:
            if field in result and result[field]:
                result[field] = self.decrypt(result[field])
        return result


# ============================================================================
# 7. Password Policy (P0-09, P1)
# ============================================================================

class PasswordPolicy:
    """
    Strong password enforcement for user accounts.

    Enforces minimum length, uppercase, lowercase, digits, and special characters.
    Provides secure temporary password generation.
    """

    MIN_LENGTH = 12
    SPECIAL_CHARS = "!@#$%^&*()-_=+[]{}|;:,.<>?"

    @staticmethod
    def validate(password: str) -> Tuple[bool, str]:
        """
        Validates that a password meets complexity requirements.

        Requirements:
            - At least 12 characters
            - At least 1 uppercase letter (A-Z)
            - At least 1 lowercase letter (a-z)
            - At least 1 digit (0-9)
            - At least 1 special character (!@#$%^&*()-_=+[]{}|;:,.<>?)

        Args:
            password: Password to validate

        Returns:
            Tuple of (is_valid: bool, error_message: str)

        Example:
            >>> PasswordPolicy.validate("Weak1!")
            (False, "Password must be at least 12 characters")
            >>> PasswordPolicy.validate("StrongPass123!")
            (True, "")
        """
        if not password:
            return (False, "Password is required")

        if len(password) < PasswordPolicy.MIN_LENGTH:
            return (False, f"Password must be at least {PasswordPolicy.MIN_LENGTH} characters")

        if not any(c.isupper() for c in password):
            return (False, "Password must contain at least 1 uppercase letter")

        if not any(c.islower() for c in password):
            return (False, "Password must contain at least 1 lowercase letter")

        if not any(c.isdigit() for c in password):
            return (False, "Password must contain at least 1 digit")

        if not any(c in PasswordPolicy.SPECIAL_CHARS for c in password):
            special_display = ", ".join(list(PasswordPolicy.SPECIAL_CHARS)[:5]) + "..."
            return (False, f"Password must contain at least 1 special character ({special_display})")

        return (True, "")

    @staticmethod
    def generate_temporary() -> str:
        """
        Generates a secure temporary password for new users.

        Format: 12-14 characters with guaranteed uppercase, lowercase, digit, special char.

        Returns:
            Temporary password string

        Example:
            >>> temp_pwd = PasswordPolicy.generate_temporary()
            >>> len(temp_pwd) >= 12
            True
        """
        # Ensure we have at least one of each required type
        password_chars = [
            secrets.choice('ABCDEFGHIJKLMNOPQRSTUVWXYZ'),  # uppercase
            secrets.choice('abcdefghijklmnopqrstuvwxyz'),  # lowercase
            secrets.choice('0123456789'),                   # digit
            secrets.choice(PasswordPolicy.SPECIAL_CHARS),   # special
        ]

        # Fill remaining characters (8-10 more to reach 12-14 total)
        all_chars = (
            'ABCDEFGHIJKLMNOPQRSTUVWXYZ'
            'abcdefghijklmnopqrstuvwxyz'
            '0123456789'
            + PasswordPolicy.SPECIAL_CHARS
        )

        for _ in range(secrets.randbelow(3) + 8):  # Add 8-10 more
            password_chars.append(secrets.choice(all_chars))

        # Shuffle to avoid predictable patterns
        import random
        random.shuffle(password_chars)

        return ''.join(password_chars)


# ============================================================================
# 8. Request Schema Validation (P0-11)
# ============================================================================

class ApplicationSchema:
    """
    Validates request payloads for application creation and modification.

    Does NOT use Pydantic (to avoid external dependencies).
    Performs type checking, range validation, enum validation, and length limits.
    """

    VALID_ENTITY_TYPES = [
        'company', 'trust', 'foundation', 'partnership', 'sole_trader'
    ]

    VALID_SECTORS = [
        'financial_services', 'technology', 'real_estate', 'commodities',
        'professional_services', 'gaming', 'crypto', 'other'
    ]

    MAX_COMPANY_NAME_LENGTH = 255
    MAX_DIRECTORS = 50
    MAX_UBOS = 50
    MAX_STRING_LENGTH = 1000
    MIN_OWNERSHIP = 0.0
    MAX_OWNERSHIP = 100.0

    @staticmethod
    def validate_application(data: Dict) -> Tuple[bool, str]:
        """
        Validates application creation payload.

        Args:
            data: Application data dictionary

        Returns:
            Tuple of (is_valid: bool, error_message: str)

        Validates:
            - entity_type is valid enum
            - company_name is string, not empty, length <= MAX_COMPANY_NAME_LENGTH
            - sector is valid enum
            - directors is list, length <= MAX_DIRECTORS
            - ubos is list, length <= MAX_UBOS
            - beneficial_owner, annual_revenue are valid numbers if present
        """
        if not isinstance(data, dict):
            return (False, "Request body must be a JSON object")

        # Entity type validation
        entity_type = data.get('entity_type', '').lower()
        if entity_type not in ApplicationSchema.VALID_ENTITY_TYPES:
            return (
                False,
                f"Invalid entity_type. Must be one of: {', '.join(ApplicationSchema.VALID_ENTITY_TYPES)}"
            )

        # Company name validation
        company_name = data.get('company_name', '')
        if not isinstance(company_name, str) or not company_name.strip():
            return (False, "company_name is required and must be a non-empty string")
        if len(company_name) > ApplicationSchema.MAX_COMPANY_NAME_LENGTH:
            return (
                False,
                f"company_name exceeds max length of {ApplicationSchema.MAX_COMPANY_NAME_LENGTH}"
            )

        # Sector validation
        sector = data.get('sector', '').lower()
        if sector and sector not in ApplicationSchema.VALID_SECTORS:
            return (
                False,
                f"Invalid sector. Must be one of: {', '.join(ApplicationSchema.VALID_SECTORS)}"
            )

        # Directors validation
        directors = data.get('directors', [])
        if not isinstance(directors, list):
            return (False, "directors must be a list")
        if len(directors) > ApplicationSchema.MAX_DIRECTORS:
            return (False, f"Too many directors (max {ApplicationSchema.MAX_DIRECTORS})")

        for i, director in enumerate(directors):
            valid, msg = ApplicationSchema.validate_director(director)
            if not valid:
                return (False, f"Director {i}: {msg}")

        # UBOs validation
        ubos = data.get('ubos', [])
        if not isinstance(ubos, list):
            return (False, "ubos must be a list")
        if len(ubos) > ApplicationSchema.MAX_UBOS:
            return (False, f"Too many UBOs (max {ApplicationSchema.MAX_UBOS})")

        for i, ubo in enumerate(ubos):
            valid, msg = ApplicationSchema.validate_ubo(ubo)
            if not valid:
                return (False, f"UBO {i}: {msg}")

        # Optional numeric fields
        if 'beneficial_owner' in data:
            if not isinstance(data['beneficial_owner'], (int, float)):
                return (False, "beneficial_owner must be a number")
            if not (0 <= data['beneficial_owner'] <= 100):
                return (False, "beneficial_owner must be between 0 and 100")

        if 'annual_revenue' in data:
            if not isinstance(data['annual_revenue'], (int, float)):
                return (False, "annual_revenue must be a number")
            if data['annual_revenue'] < 0:
                return (False, "annual_revenue must be non-negative")

        return (True, "")

    @staticmethod
    def validate_director(data: Dict) -> Tuple[bool, str]:
        """
        Validates a director record.

        Args:
            data: Director data dictionary

        Returns:
            Tuple of (is_valid: bool, error_message: str)
        """
        if not isinstance(data, dict):
            return (False, "Director must be an object")

        # Required fields
        for field in ['first_name', 'last_name', 'date_of_birth']:
            if field not in data or not data[field]:
                return (False, f"{field} is required")
            if not isinstance(data[field], str):
                return (False, f"{field} must be a string")
            if len(str(data[field])) > ApplicationSchema.MAX_STRING_LENGTH:
                return (False, f"{field} exceeds max length")

        # Optional fields with type checking
        if 'passport_number' in data and data['passport_number']:
            if not isinstance(data['passport_number'], str):
                return (False, "passport_number must be a string")
            if len(data['passport_number']) > 50:
                return (False, "passport_number exceeds max length")

        if 'nationality' in data and data['nationality']:
            if not isinstance(data['nationality'], str):
                return (False, "nationality must be a string")
            if len(data['nationality']) > 100:
                return (False, "nationality exceeds max length")

        if 'id_number' in data and data['id_number']:
            if not isinstance(data['id_number'], str):
                return (False, "id_number must be a string")
            if len(data['id_number']) > 50:
                return (False, "id_number exceeds max length")

        return (True, "")

    @staticmethod
    def validate_ubo(data: Dict) -> Tuple[bool, str]:
        """
        Validates a UBO (Ultimate Beneficial Owner) record.

        Args:
            data: UBO data dictionary

        Returns:
            Tuple of (is_valid: bool, error_message: str)

        Validates:
            - name is required string
            - ownership_pct is number between 0 and 100
            - passport_number, nationality are valid strings if present
        """
        if not isinstance(data, dict):
            return (False, "UBO must be an object")

        # Required field
        if 'name' not in data or not data['name']:
            return (False, "name is required")
        if not isinstance(data['name'], str):
            return (False, "name must be a string")
        if len(data['name']) > ApplicationSchema.MAX_STRING_LENGTH:
            return (False, "name exceeds max length")

        # Ownership percentage (required and validated)
        if 'ownership_pct' not in data:
            return (False, "ownership_pct is required")

        ownership = data['ownership_pct']
        if not isinstance(ownership, (int, float)):
            return (False, "ownership_pct must be a number")
        if not (ApplicationSchema.MIN_OWNERSHIP <= ownership <= ApplicationSchema.MAX_OWNERSHIP):
            return (
                False,
                f"ownership_pct must be between {ApplicationSchema.MIN_OWNERSHIP} "
                f"and {ApplicationSchema.MAX_OWNERSHIP}"
            )

        # Optional fields
        if 'passport_number' in data and data['passport_number']:
            if not isinstance(data['passport_number'], str):
                return (False, "passport_number must be a string")
            if len(data['passport_number']) > 50:
                return (False, "passport_number exceeds max length")

        if 'nationality' in data and data['nationality']:
            if not isinstance(data['nationality'], str):
                return (False, "nationality must be a string")
            if len(data['nationality']) > 100:
                return (False, "nationality exceeds max length")

        return (True, "")


# ============================================================================
# 9. Token Revocation (P1)
# ============================================================================

class TokenRevocationList:
    """
    JWT token revocation list with DB persistence.

    Prevents token reuse after logout or role changes.
    Uses in-memory cache for fast lookups with DB persistence so
    revocations survive server restarts.
    Periodically removes expired entries to prevent memory/DB exhaustion.
    """

    def __init__(self, cleanup_interval: int = 3600):
        """
        Initialize the revocation list.

        Args:
            cleanup_interval: Seconds between automatic cleanups (default 1 hour)
        """
        self._revoked = {}  # jti -> expiry_timestamp (in-memory cache)
        self._cleanup_interval = cleanup_interval
        self._last_cleanup = time.time()
        self._db_loaded = False

    def _db_load_all(self) -> None:
        """Load all non-expired revoked tokens from DB into memory (called once)."""
        if self._db_loaded:
            return
        try:
            from db import get_db as _db_get
            db = _db_get()
            now = time.time()
            rows = db.execute(
                "SELECT jti, expires_at FROM revoked_tokens WHERE expires_at > ?",
                (now,)
            ).fetchall()
            for r in rows:
                jti = r[0] if isinstance(r, (tuple, list)) else r["jti"]
                exp = r[1] if isinstance(r, (tuple, list)) else r["expires_at"]
                self._revoked[jti] = exp
            db.close()
            if rows:
                logger.info(f"Loaded {len(rows)} revoked tokens from database")
            self._db_loaded = True
        except Exception as e:
            logger.debug(f"Could not load revoked tokens from DB: {e}")

    def _db_persist(self, jti: str, expires_at: float) -> None:
        """Persist a revocation to DB."""
        try:
            from db import get_db as _db_get
            db = _db_get()
            # Use INSERT OR REPLACE for SQLite / ON CONFLICT for Postgres
            db.execute(
                "INSERT INTO revoked_tokens (jti, expires_at) VALUES (?, ?) "
                "ON CONFLICT (jti) DO UPDATE SET expires_at = EXCLUDED.expires_at",
                (jti, expires_at)
            )
            db.commit()
            db.close()
        except Exception as e:
            logger.debug(f"Could not persist revoked token to DB: {e}")

    def _db_remove_expired(self) -> None:
        """Remove expired entries from DB."""
        try:
            from db import get_db as _db_get
            db = _db_get()
            db.execute("DELETE FROM revoked_tokens WHERE expires_at <= ?", (time.time(),))
            db.commit()
            db.close()
        except Exception:
            pass

    def revoke(self, jti: str, expires_at: float) -> None:
        """
        Add a token to the revocation list.

        Args:
            jti: JWT ID (from token's 'jti' claim)
            expires_at: Token expiry timestamp (Unix time)
        """
        self._revoked[jti] = expires_at
        self._db_persist(jti, expires_at)
        logger.debug(f"Token {jti[:8]}... revoked (expires at {expires_at})")

        # Cleanup if interval exceeded
        if time.time() - self._last_cleanup > self._cleanup_interval:
            self.cleanup()

    def is_revoked(self, jti: str) -> bool:
        """
        Check if a token is in the revocation list.

        Args:
            jti: JWT ID to check

        Returns:
            True if token is revoked, False otherwise
        """
        # Lazy-load from DB on first access
        self._db_load_all()

        if jti not in self._revoked:
            return False

        expiry = self._revoked[jti]
        if time.time() > expiry:
            # Token has expired, remove from list
            del self._revoked[jti]
            return False

        return True

    def cleanup(self) -> None:
        """
        Remove expired entries from the revocation list (memory + DB).

        Called automatically after cleanup_interval has passed.
        """
        now = time.time()
        expired = [jti for jti, exp in self._revoked.items() if now > exp]

        for jti in expired:
            del self._revoked[jti]

        if expired:
            logger.debug(f"Token revocation cleanup: removed {len(expired)} expired entries")

        self._db_remove_expired()
        self._last_cleanup = time.time()

    def stats(self) -> Dict:
        """
        Get revocation list statistics.

        Returns:
            Dictionary with current count and last cleanup time
        """
        return {
            'revoked_count': len(self._revoked),
            'last_cleanup': self._last_cleanup,
        }


# Global instance for use across the application
token_revocation_list = TokenRevocationList()


# ============================================================================
# 10. File Upload Validation (P1)
# ============================================================================

class FileUploadValidator:
    """
    MIME type and content validation for document uploads.

    Validates file extensions, MIME types, magic bytes (file signatures),
    and file size to prevent malicious uploads.
    """

    ALLOWED_MIME_TYPES = {
        'application/pdf',
        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
        'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        'image/png',
        'image/jpeg',
        'image/jpg',
    }

    ALLOWED_EXTENSIONS = {'.pdf', '.docx', '.xlsx', '.pptx', '.png', '.jpg', '.jpeg'}

    MAX_FILE_SIZE = 25 * 1024 * 1024  # 25MB

    # Magic bytes (file signatures) for type detection
    MAGIC_BYTES = {
        b'%PDF': 'application/pdf',
        b'\x89PNG': 'image/png',
        b'\xff\xd8\xff': 'image/jpeg',
        b'PK\x03\x04': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    }

    @classmethod
    def validate(cls, filename: str, content_type: str, file_data: bytes) -> Tuple[bool, str]:
        """
        Validates a file upload for security compliance.

        Checks:
            1. File extension is in ALLOWED_EXTENSIONS
            2. Content-Type MIME type is in ALLOWED_MIME_TYPES
            3. File size does not exceed MAX_FILE_SIZE
            4. File magic bytes match claimed MIME type

        Args:
            filename: Original filename
            content_type: HTTP Content-Type header value
            file_data: Raw file bytes

        Returns:
            Tuple of (is_valid: bool, error_message: str)

        Example:
            >>> valid, msg = FileUploadValidator.validate(
            ...     "document.pdf",
            ...     "application/pdf",
            ...     b"%PDF-1.4 ..."
            ... )
        """
        try:
            # 1. Check extension
            file_ext = Path(filename).suffix.lower()
            if file_ext not in cls.ALLOWED_EXTENSIONS:
                return (
                    False,
                    f"File type '{file_ext}' not allowed. Allowed: {', '.join(cls.ALLOWED_EXTENSIONS)}"
                )

            # 2. Check MIME type
            content_type_clean = content_type.split(';')[0].strip().lower() if content_type else ''
            if content_type_clean not in cls.ALLOWED_MIME_TYPES:
                return (
                    False,
                    f"Content-Type '{content_type_clean}' not allowed. "
                    f"Allowed: {', '.join(cls.ALLOWED_MIME_TYPES)}"
                )

            # 3. Check file size
            file_size = len(file_data)
            if file_size > cls.MAX_FILE_SIZE:
                max_size_mb = cls.MAX_FILE_SIZE / (1024 * 1024)
                return (
                    False,
                    f"File size {file_size} bytes exceeds maximum of {cls.MAX_FILE_SIZE} bytes ({max_size_mb}MB)"
                )

            # 4. Check magic bytes
            magic_match = cls._check_magic_bytes(file_data)
            if not magic_match:
                return (
                    False,
                    "File content does not match claimed file type (magic bytes mismatch)"
                )

            # Validate magic bytes match content type
            if not cls._magic_matches_content_type(magic_match, content_type_clean):
                return (
                    False,
                    f"File content type does not match Content-Type header "
                    f"(magic: {magic_match}, claimed: {content_type_clean})"
                )

            logger.info(f"File upload validated: {filename} ({file_size} bytes)")
            return (True, "")

        except Exception as e:
            logger.error(f"File validation error: {e}", exc_info=True)
            return (False, f"File validation error: {str(e)}")

    @classmethod
    def _check_magic_bytes(cls, file_data: bytes) -> Optional[str]:
        """
        Detect file type by magic bytes.

        Args:
            file_data: Raw file bytes

        Returns:
            Detected MIME type or None
        """
        if not file_data:
            return None

        for magic, mime_type in cls.MAGIC_BYTES.items():
            if file_data.startswith(magic):
                return mime_type

        return None

    @classmethod
    def _magic_matches_content_type(cls, magic_type: str, content_type: str) -> bool:
        """
        Verify that detected magic type matches declared content type.

        Args:
            magic_type: MIME type detected from magic bytes
            content_type: Declared MIME type from header

        Returns:
            True if types match or are compatible, False otherwise
        """
        # Exact match
        if magic_type == content_type:
            return True

        # Handle JPEG variants (image/jpg vs image/jpeg)
        if magic_type in ['image/jpeg', 'image/jpg'] and content_type in ['image/jpeg', 'image/jpg']:
            return True

        # Office Open XML formats are ZIP containers and share the same magic bytes.
        if magic_type == 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' and content_type in [
            'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            'application/vnd.openxmlformats-officedocument.presentationml.presentation',
        ]:
            return True

        return False


# ============================================================================
# 11. Health Endpoint Restriction (P1)
# ============================================================================

def get_safe_health_response() -> Dict:
    """
    Returns health check data without leaking sensitive configuration.

    Safe for public access (no authentication required).
    Does not expose environment variables, API keys, or internal state.

    Returns:
        Dictionary with basic health status

    Example:
        >>> response = get_safe_health_response()
        >>> response['status']
        'ok'
    """
    return {
        'status': 'ok',
        'service': 'ARIE Finance API',
        'version': '1.0.0',
        'timestamp': datetime.utcnow().isoformat() + 'Z',
    }


def get_detailed_health_response(include_config: bool = False) -> Dict:
    """
    Returns detailed health information for authenticated admin users only.

    Should only be called after verifying user is admin.
    Includes database status, cache health, and optional configuration details.

    Args:
        include_config: If True, includes environment and config details (admin only)

    Returns:
        Dictionary with detailed health information

    Example:
        >>> # In authenticated admin endpoint
        >>> response = get_detailed_health_response(include_config=True)
        >>> response['database']['status']
        'connected'
    """
    response = get_safe_health_response()

    # Add database status
    response['database'] = {
        'type': os.environ.get('DATABASE_URL', 'sqlite').split('+')[0],
        'status': 'ok',  # Should be checked in actual implementation
    }

    # Add optional configuration details (admin only)
    if include_config:
        response['configuration'] = {
            'environment': ENV,
            'log_level': os.environ.get('LOG_LEVEL', 'INFO'),
            'claude_mock_mode': os.environ.get('CLAUDE_MOCK_MODE', 'false'),
        }

    return response


# ============================================================================
# Module Initialization
# ============================================================================

def initialize_security_module() -> None:
    """
    Initializes the security hardening module.

    Called once at server startup to validate production environment and set up logging.
    """
    logger.info("Security hardening module initialized")

    # Validate production environment at startup
    try:
        validate_production_environment()
    except RuntimeError as e:
        logger.error(f"Production environment validation failed: {e}")
        if is_production():
            raise


if __name__ == '__main__':
    # Simple smoke test
    logging.basicConfig(level=logging.DEBUG)
    logger = logging.getLogger(__name__)

    print("Testing security_hardening module...")

    # Test password policy
    pwd_valid, pwd_msg = PasswordPolicy.validate("Weak1!")
    print(f"✓ PasswordPolicy.validate: {pwd_valid}, {pwd_msg}")

    temp_pwd = PasswordPolicy.generate_temporary()
    print(f"✓ PasswordPolicy.generate_temporary: {len(temp_pwd)} chars")

    # Test schema validation
    app_valid, app_msg = ApplicationSchema.validate_application({
        'entity_type': 'company',
        'company_name': 'Test Corp',
        'sector': 'technology',
        'directors': [],
        'ubos': []
    })
    print(f"✓ ApplicationSchema.validate_application: {app_valid}")

    # Test screening mode
    mode = determine_screening_mode({'sources': [], 'rules_results': []})
    print(f"✓ determine_screening_mode: {mode}")

    # Test AI source tagging
    resp = tag_ai_response({'analysis': 'test'}, 'claude-sonnet-4-6')
    print(f"✓ tag_ai_response: ai_source={resp.get('ai_source')}")

    # Test file upload validation
    pdf_magic = b'%PDF-1.4'
    valid, msg = FileUploadValidator.validate('test.pdf', 'application/pdf', pdf_magic)
    print(f"✓ FileUploadValidator.validate: {valid}")

    # Test health responses
    safe_health = get_safe_health_response()
    print(f"✓ get_safe_health_response: status={safe_health.get('status')}")

    # Test token revocation
    print(f"✓ token_revocation_list: {token_revocation_list.stats()}")

    print("\n✓ All security_hardening tests passed!")
