"""
ARIE Finance — AI Agent Supervisor: Schema Validator
=====================================================
Validates every agent output against strict Pydantic schemas.
Rejects or quarantines outputs that fail validation.

Checks:
  1. Mandatory fields present
  2. Field types correct
  3. Confidence score present and bounded
  4. Evidence present where required
  5. Status consistency
  6. Escalation flag/reason consistency
  7. Agent-specific field validation
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

from pydantic import ValidationError as PydanticValidationError

from .schemas import (
    AGENT_OUTPUT_MODELS,
    AgentOutputBase,
    AgentStatus,
    AgentType,
    Severity,
    ValidationError,
    ValidationResult,
)

logger = logging.getLogger("arie.supervisor.validator")

VALIDATOR_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0.0"


class SchemaValidator:
    """
    Validates agent outputs against Pydantic schemas.
    Returns structured ValidationResult for audit logging.
    """

    # Fields that are ALWAYS required in every agent output
    MANDATORY_FIELDS = [
        "agent_name", "agent_type", "agent_version", "prompt_version",
        "model_name", "run_id", "application_id", "status",
        "confidence_score", "findings", "evidence", "processed_at"
    ]

    # Status values that require non-empty evidence
    EVIDENCE_REQUIRED_STATUSES = [
        AgentStatus.ISSUES_FOUND,
    ]

    def __init__(self):
        self.validation_count = 0
        self.pass_count = 0
        self.fail_count = 0

    def validate(
        self,
        raw_output: Dict[str, Any],
        expected_agent_type: Optional[AgentType] = None
    ) -> Tuple[bool, ValidationResult, Optional[AgentOutputBase]]:
        """
        Validate an agent output dict against its schema.

        Args:
            raw_output: Raw JSON dict from agent
            expected_agent_type: If set, verify agent_type matches

        Returns:
            Tuple of (is_valid, validation_result, parsed_output_or_none)
        """
        self.validation_count += 1
        errors: List[ValidationError] = []
        missing_fields: List[str] = []
        type_errors: List[str] = []
        constraint_violations: List[str] = []
        warnings: List[str] = []

        run_id = raw_output.get("run_id", "unknown")
        app_id = raw_output.get("application_id", "unknown")
        agent_type_str = raw_output.get("agent_type", "unknown")

        # ── Step 1: Check mandatory fields ──
        for field in self.MANDATORY_FIELDS:
            if field not in raw_output or raw_output[field] is None:
                missing_fields.append(field)
                errors.append(ValidationError(
                    field=field,
                    error_type="missing",
                    message=f"Mandatory field '{field}' is missing or null",
                    severity=Severity.HIGH
                ))

        # ── Step 2: Validate agent_type matches expected ──
        if expected_agent_type and agent_type_str != expected_agent_type.value:
            constraint_violations.append(
                f"Expected agent_type '{expected_agent_type.value}', got '{agent_type_str}'"
            )
            errors.append(ValidationError(
                field="agent_type",
                error_type="constraint",
                message=f"Agent type mismatch: expected {expected_agent_type.value}",
                severity=Severity.HIGH
            ))

        # ── Step 3: Validate confidence_score bounds ──
        confidence = raw_output.get("confidence_score")
        if confidence is not None:
            if not isinstance(confidence, (int, float)):
                type_errors.append("confidence_score must be numeric")
                errors.append(ValidationError(
                    field="confidence_score",
                    error_type="type_error",
                    message=f"confidence_score must be float, got {type(confidence).__name__}",
                    severity=Severity.HIGH
                ))
            elif confidence < 0.0 or confidence > 1.0:
                constraint_violations.append("confidence_score must be between 0.0 and 1.0")
                errors.append(ValidationError(
                    field="confidence_score",
                    error_type="constraint",
                    message=f"confidence_score {confidence} out of range [0.0, 1.0]",
                    severity=Severity.HIGH
                ))

        # ── Step 4: Validate evidence for non-clean statuses ──
        status = raw_output.get("status")
        findings = raw_output.get("findings", [])
        evidence = raw_output.get("evidence", [])

        if status in [s.value for s in self.EVIDENCE_REQUIRED_STATUSES]:
            if findings and not evidence:
                constraint_violations.append(
                    f"Evidence required when status='{status}' and findings are present"
                )
                errors.append(ValidationError(
                    field="evidence",
                    error_type="constraint",
                    message="Evidence is empty but findings are present with non-clean status",
                    severity=Severity.HIGH
                ))

        # ── Step 5: Validate escalation consistency ──
        escalation_flag = raw_output.get("escalation_flag", False)
        escalation_reason = raw_output.get("escalation_reason")
        if escalation_flag and not escalation_reason:
            constraint_violations.append("escalation_reason required when escalation_flag is True")
            errors.append(ValidationError(
                field="escalation_reason",
                error_type="constraint",
                message="escalation_flag is True but escalation_reason is empty",
                severity=Severity.MEDIUM
            ))

        # ── Step 6: Full Pydantic validation ──
        parsed_output = None
        try:
            agent_type_enum = AgentType(agent_type_str) if agent_type_str != "unknown" else None
        except ValueError:
            agent_type_enum = None
            type_errors.append(f"Invalid agent_type: {agent_type_str}")

        if agent_type_enum and agent_type_enum in AGENT_OUTPUT_MODELS:
            model_class = AGENT_OUTPUT_MODELS[agent_type_enum]
            try:
                parsed_output = model_class(**raw_output)
            except PydanticValidationError as e:
                for err in e.errors():
                    field_path = " -> ".join(str(loc) for loc in err["loc"])
                    error_msg = err["msg"]
                    error_type = err["type"]

                    if "missing" in error_type:
                        missing_fields.append(field_path)
                    elif "type" in error_type:
                        type_errors.append(f"{field_path}: {error_msg}")
                    else:
                        constraint_violations.append(f"{field_path}: {error_msg}")

                    errors.append(ValidationError(
                        field=field_path,
                        error_type=error_type,
                        message=error_msg,
                        severity=Severity.HIGH
                    ))
        elif agent_type_enum is None:
            errors.append(ValidationError(
                field="agent_type",
                error_type="type_error",
                message=f"Cannot determine agent type from '{agent_type_str}'",
                severity=Severity.CRITICAL
            ))
        else:
            # Fall back to base model validation
            try:
                parsed_output = AgentOutputBase(**raw_output)
            except PydanticValidationError as e:
                for err in e.errors():
                    field_path = " -> ".join(str(loc) for loc in err["loc"])
                    errors.append(ValidationError(
                        field=field_path,
                        error_type=err["type"],
                        message=err["msg"],
                        severity=Severity.HIGH
                    ))

        # ── Step 7: Soft warnings (non-blocking) ──
        if not raw_output.get("processing_time_ms"):
            warnings.append("processing_time_ms not reported")
        if not raw_output.get("token_count"):
            warnings.append("token_count not reported")
        if status == AgentStatus.CLEAN.value and findings:
            warnings.append("Status is 'clean' but findings are present — may indicate inconsistency")

        # ── Build result ──
        is_valid = len(errors) == 0
        if is_valid:
            self.pass_count += 1
        else:
            self.fail_count += 1

        result = ValidationResult(
            validation_id=str(uuid4()),
            run_id=run_id,
            agent_type=agent_type_enum or AgentType.IDENTITY_DOCUMENT_INTEGRITY,
            application_id=app_id,
            is_valid=is_valid,
            errors=errors,
            missing_fields=list(set(missing_fields)),
            type_errors=list(set(type_errors)),
            constraint_violations=list(set(constraint_violations)),
            warnings=warnings,
            schema_version=SCHEMA_VERSION,
            validator_version=VALIDATOR_VERSION,
        )

        logger.info(
            "Validation %s for run=%s agent=%s app=%s | errors=%d warnings=%d",
            "PASSED" if is_valid else "FAILED",
            run_id, agent_type_str, app_id,
            len(errors), len(warnings)
        )

        return is_valid, result, parsed_output

    def get_stats(self) -> Dict[str, Any]:
        """Return validation statistics."""
        return {
            "total_validations": self.validation_count,
            "passed": self.pass_count,
            "failed": self.fail_count,
            "pass_rate": self.pass_count / max(self.validation_count, 1),
        }
