"""Scenario registry (Path A: real-schema-adapted).

Each ``ScenarioDef`` is a *declarative* description of a staging
fixture. The seeder consumes these and is responsible for upserting
rows idempotently against the *real* current schema declared in
``arie-backend/db.py``.

Conventions enforced here (so the seeder stays generic):

- ``application.id``      : 16-char lowercase hex from a reserved
                            ``f1xed...`` namespace (visually obvious in
                            logs; cannot collide with real or demo IDs)
- ``application.ref``     : reserved range ``ARF-2026-9xxxxx``
- ``application.risk_level``: UPPERCASE (LOW/MEDIUM/HIGH/VERY_HIGH) to
                              satisfy the schema CHECK constraint
- ``company_name`` prefix : ``FIX-SCENxx ...``
- ``alert.source_reference``: ``FIX_SCENxx_ALERT`` (idempotency key)
- ``review.fixture_marker`` : ``FIX_SCENxx_REVIEW`` (embedded into
                              ``periodic_reviews.trigger_reason``)
- ``edd.fixture_marker``    : ``FIX_SCENxx_EDD`` (embedded into
                              ``edd_cases.trigger_notes``)
- ``document.fixture_marker``: ``FIX_SCENxx_DOC_<purpose>``
                              (embedded into ``documents.file_path``
                              as ``fixture://<marker>``)

These markers go into existing TEXT columns. No schema changes.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Dict, Any


# ---------------------------------------------------------------------
# Deterministic application ids (16-char lowercase hex). Picked from a
# reserved "f1xed..." namespace so they are visually obvious in logs
# and cannot collide with existing real or demo IDs.
# ---------------------------------------------------------------------
APP_ID = {
    "SCEN-01": "f1xed00000000001",
    "SCEN-02": "f1xed00000000002",
    "SCEN-03": "f1xed00000000003",
    "SCEN-04": "f1xed00000000004",
    # SCEN-05 intentionally omitted - covered by existing legacy reviews.
    "SCEN-06": "f1xed00000000006",
    "SCEN-07": "f1xed00000000007",
    "SCEN-08": "f1xed00000000008",
    "SCEN-09": "f1xed00000000009",
    "SCEN-10": "f1xed00000000010",
    "SCEN-11": "f1xed00000000011",
}

APP_REF = {
    "SCEN-01": "ARF-2026-900001",
    "SCEN-02": "ARF-2026-900002",
    "SCEN-03": "ARF-2026-900003",
    "SCEN-04": "ARF-2026-900004",
    "SCEN-06": "ARF-2026-900006",
    "SCEN-07": "ARF-2026-900007",
    "SCEN-08": "ARF-2026-900008",
    "SCEN-09": "ARF-2026-900009",
    "SCEN-10": "ARF-2026-900010",
    "SCEN-11": "ARF-2026-900011",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _days_ago_iso(n: int) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=n)).isoformat()


@dataclass
class DocumentSpec:
    """Maps onto ``documents`` (TEXT pk, doc_type/doc_name/file_path NOT NULL)."""
    purpose: str          # written into documents.doc_type and documents.doc_name
    uploaded_at_iso: str  # documents.uploaded_at
    verification_status: str = "verified"   # documents.verification_status
    fixture_marker: str = ""  # idempotency key, stored in documents.file_path


@dataclass
class AlertSpec:
    """Maps onto ``monitoring_alerts``.

    The drafted ``dismissal_payload`` column does NOT exist. Structured
    dismissal JSON is serialized into ``officer_notes`` with a leading
    ``FIX_PAYLOAD_JSON:`` sentinel so the renderer (or any tooling) can
    unambiguously re-parse it.
    """
    severity: str
    summary: str
    status: str
    source_reference: str    # idempotency key, stored as-is in source_reference
    alert_type: str = "fixture"   # monitoring_alerts.alert_type
    officer_action: Optional[str] = None
    officer_notes: Optional[str] = None
    dismissal_payload: Optional[Dict[str, Any]] = None
    link_to_review: bool = False
    link_to_edd: bool = False


@dataclass
class ReviewSpec:
    """Maps onto ``periodic_reviews``.

    PG schema for ``periodic_reviews`` does NOT have ``status``,
    ``review_memo``, ``outcome``, ``source_alert_id`` or ``updated_at``
    columns. Their semantics are folded into existing columns:

    - ``status``        -> ``trigger_type`` (e.g. 'fixture_completed',
                          'fixture_in_progress')
    - ``review_memo``   -> appended to ``trigger_reason`` as
                           ``...; memo=<text>``
    - ``outcome``       -> ``decision``
    - ``source_alert_id`` -> appended to ``trigger_reason`` as
                             ``; source_alert_id=N``
    """
    status: str                              # mapped to trigger_type
    fixture_marker: str                      # leading token in trigger_reason
    review_memo: Optional[str] = None        # appended to trigger_reason
    outcome: Optional[str] = None            # written to decision
    completed_at_iso: Optional[str] = None   # completed_at
    escalated_to_edd: bool = False


@dataclass
class EddSpec:
    """Maps onto ``edd_cases``.

    The drafted ``kind``, ``status``, ``memo_id``, ``source_review_id``,
    ``source_alert_id`` columns do NOT exist. Mapping:

    - ``kind``        -> ``trigger_source`` ('onboarding' or 'periodic_review')
    - ``status``      -> ``stage`` (must be in CHECK enum: triggered,
                         information_gathering, analysis,
                         pending_senior_review, edd_approved, edd_rejected)
    - ``memo_id``     -> NOT stored; memo lookup is by application_id only
    - ``source_review_id`` -> appended to ``trigger_notes`` as
                              ``; source_review_id=N``
    - ``source_alert_id``  -> appended to ``trigger_notes`` as
                              ``; source_alert_id=N``
    """
    kind: str                          # mapped to trigger_source
    risk_level: str                    # UPPERCASE
    fixture_marker: str                # leading token in trigger_notes
    stage: str = "information_gathering"   # edd_cases.stage
    seed_compliance_memo: bool = True


@dataclass
class ScenarioDef:
    code: str
    purpose: str
    company_name: str
    risk_level: str   # UPPERCASE
    country: str
    sector: str
    entity_type: str = "company"
    ownership_structure: str = "simple"
    documents: List[DocumentSpec] = field(default_factory=list)
    alert_spec: Optional[AlertSpec] = None
    review_spec: Optional[ReviewSpec] = None
    edd_spec: Optional[EddSpec] = None
    proves: List[str] = field(default_factory=list)


SCENARIOS: List[ScenarioDef] = [
    ScenarioDef(
        code="SCEN-01",
        purpose="Monitoring alert -> periodic review -> completed review -> memo generated",
        company_name="FIX-SCEN01 Alert-to-Memo Holdings Ltd",
        risk_level="MEDIUM",
        country="UAE",
        sector="financial_services",
        alert_spec=AlertSpec(
            severity="medium",
            summary="FIX-SCEN01 monitoring trigger: adverse media match",
            status="in_review",
            source_reference="FIX_SCEN01_ALERT",
            officer_action="create_review",
            link_to_review=True,
        ),
        review_spec=ReviewSpec(
            status="fixture_completed",
            fixture_marker="FIX_SCEN01_REVIEW",
            review_memo=(
                "FIXTURE MEMO (SCEN-01): Periodic review completed following "
                "monitoring alert. No further action required."
            ),
            outcome="no_action_required",
            completed_at_iso=_now_iso(),
        ),
        proves=[
            "Monitoring alert can drive a periodic review",
            "Completed review with non-null review memo (embedded in trigger_reason) is recoverable",
        ],
    ),
    ScenarioDef(
        code="SCEN-02",
        purpose="Monitoring alert -> direct EDD (no review)",
        company_name="FIX-SCEN02 Alert-to-EDD Trading Ltd",
        risk_level="HIGH",
        country="BVI",
        sector="crypto",
        alert_spec=AlertSpec(
            severity="high",
            summary="FIX-SCEN02 monitoring trigger: sanctions proximity",
            status="in_review",
            source_reference="FIX_SCEN02_ALERT",
            officer_action="create_edd",
            link_to_edd=True,
        ),
        edd_spec=EddSpec(
            kind="onboarding",
            risk_level="HIGH",
            fixture_marker="FIX_SCEN02_EDD",
        ),
        proves=[
            "Monitoring alert can route directly to EDD (no review hop)",
            "EDD created from alert appears in Lifecycle Queue and EDD Pipeline",
        ],
    ),
    ScenarioDef(
        code="SCEN-03",
        purpose="Periodic review -> escalated to EDD",
        company_name="FIX-SCEN03 Review-to-EDD Capital Ltd",
        risk_level="HIGH",
        country="Cayman Islands",
        sector="investment",
        review_spec=ReviewSpec(
            status="fixture_completed",
            fixture_marker="FIX_SCEN03_REVIEW",
            outcome="edd_required",
            completed_at_iso=_now_iso(),
            escalated_to_edd=True,
        ),
        edd_spec=EddSpec(
            kind="periodic_review",
            risk_level="HIGH",
            fixture_marker="FIX_SCEN03_EDD",
        ),
        proves=[
            "Completed review with outcome=edd_required produces EDD",
            "EDD trace links back to originating review (via trigger_notes marker)",
        ],
    ),
    ScenarioDef(
        code="SCEN-04",
        purpose="Completed review with generated periodic review memo (memo-positive case)",
        company_name="FIX-SCEN04 MemoPositive Ventures Ltd",
        risk_level="MEDIUM",
        country="Singapore",
        sector="fintech",
        review_spec=ReviewSpec(
            status="fixture_completed",
            fixture_marker="FIX_SCEN04_REVIEW",
            review_memo=(
                "FIXTURE MEMO (SCEN-04): Comprehensive periodic review memo. "
                "Customer profile reviewed. Risk dimensions reassessed. "
                "Documentation current. Outcome: continue monitoring."
            ),
            outcome="continue_monitoring",
            completed_at_iso=_now_iso(),
        ),
        proves=[
            "Completed periodic review with non-null memo (embedded) is the "
            "positive control for SCEN-05",
        ],
    ),
    # SCEN-05 intentionally not seeded - covered by existing legacy reviews.
    ScenarioDef(
        code="SCEN-06",
        purpose="Dismissed alert with structured dismissal JSON",
        company_name="FIX-SCEN06 StructuredDismiss Holdings Ltd",
        risk_level="LOW",
        country="UAE",
        sector="real_estate",
        alert_spec=AlertSpec(
            severity="low",
            summary="FIX-SCEN06 dismissed alert (structured payload)",
            status="dismissed",
            source_reference="FIX_SCEN06_ALERT",
            officer_action="dismiss",
            dismissal_payload={
                "reason_code": "false_positive",
                "reason_label": "False positive - name collision",
                "evidence_refs": ["FIX_SCEN06_EVIDENCE_001"],
                "dismissed_by": "fixture_seed",
                "dismissed_at": _now_iso(),
            },
        ),
        proves=[
            "Dismissed alert with structured payload (folded into officer_notes "
            "with FIX_PAYLOAD_JSON: sentinel) is recoverable",
        ],
    ),
    ScenarioDef(
        code="SCEN-07",
        purpose="Dismissed alert with legacy free-text dismissal notes",
        company_name="FIX-SCEN07 LegacyDismiss Trading Ltd",
        risk_level="LOW",
        country="UAE",
        sector="trading",
        alert_spec=AlertSpec(
            severity="low",
            summary="FIX-SCEN07 dismissed alert (legacy free-text)",
            status="dismissed",
            source_reference="FIX_SCEN07_ALERT",
            officer_action="dismiss",
            officer_notes=(
                "Reviewed and dismissed. Customer is not the listed party; "
                "DOB and nationality differ. No further action."
            ),
        ),
        proves=[
            "Dismissed alert with only free-text notes renders legacy view",
            "Renderer falls back gracefully when no FIX_PAYLOAD_JSON: sentinel is present",
        ],
    ),
    ScenarioDef(
        code="SCEN-08",
        purpose="EDD with no compliance memo (onboarding_attachment_confirmed = false)",
        company_name="FIX-SCEN08 NoAttach Holdings Ltd",
        risk_level="HIGH",
        country="BVI",
        sector="crypto",
        alert_spec=AlertSpec(
            severity="high",
            summary="FIX-SCEN08 onboarding flag (no compliance memo expected)",
            status="in_review",
            source_reference="FIX_SCEN08_ALERT",
            officer_action="create_edd",
            link_to_edd=True,
        ),
        edd_spec=EddSpec(
            kind="onboarding",
            risk_level="HIGH",
            fixture_marker="FIX_SCEN08_EDD",
            seed_compliance_memo=False,
        ),
        proves=[
            "EDD where no compliance memo exists yields "
            "onboarding_attachment_confirmed=false at runtime",
            "Lifecycle UI shows attachment-warning state",
        ],
    ),
    ScenarioDef(
        code="SCEN-09",
        purpose="New EDD created from routing (fresh case)",
        company_name="FIX-SCEN09 FreshRouting Ltd",
        risk_level="HIGH",
        country="Cayman Islands",
        sector="financial_services",
        edd_spec=EddSpec(
            kind="onboarding",
            risk_level="HIGH",
            fixture_marker="FIX_SCEN09_EDD",
        ),
        proves=[
            "Routing to EDD on a clean application creates a brand-new EDD case",
            "Lifecycle Queue shows the new case immediately",
        ],
    ),
    ScenarioDef(
        code="SCEN-10",
        purpose="Existing active EDD reused from routing (no duplicate created)",
        company_name="FIX-SCEN10 ReuseRouting Ltd",
        risk_level="HIGH",
        country="Cayman Islands",
        sector="financial_services",
        alert_spec=AlertSpec(
            severity="high",
            summary="FIX-SCEN10 fresh adverse-media trigger on app with existing EDD",
            status="in_review",
            source_reference="FIX_SCEN10_ALERT",
            officer_action="create_edd",
            link_to_edd=True,
        ),
        edd_spec=EddSpec(
            kind="onboarding",
            risk_level="HIGH",
            fixture_marker="FIX_SCEN10_EDD",
        ),
        proves=[
            "Application starts with exactly one active EDD",
            "Re-routing action does NOT create a second EDD; existing case is reused",
            "EDD count on the application remains 1 after the routing test",
        ],
    ),
    ScenarioDef(
        code="SCEN-11",
        purpose=(
            "Agent 6 rich review-prep covering risk-tier, jurisdiction, sector, "
            "screening staleness, document expiry, ownership-related items, plus "
            "an outstanding alert visible in the alert pane (NOT as a required-item)"
        ),
        company_name="FIX-SCEN11 Agent6 RichPrep Holdings Ltd",
        risk_level="VERY_HIGH",
        country="Mauritius",
        sector="crypto",
        ownership_structure="complex_multi_tier",
        documents=[
            DocumentSpec(
                purpose="passport",
                uploaded_at_iso=_days_ago_iso(400),
                fixture_marker="FIX_SCEN11_DOC_passport",
            ),
            DocumentSpec(
                purpose="ownership_chart",
                uploaded_at_iso=_days_ago_iso(420),
                fixture_marker="FIX_SCEN11_DOC_ownership_chart",
            ),
            DocumentSpec(
                purpose="license",
                uploaded_at_iso=_days_ago_iso(380),
                fixture_marker="FIX_SCEN11_DOC_license",
            ),
        ],
        alert_spec=AlertSpec(
            severity="high",
            summary="FIX-SCEN11 outstanding alert (rendered in alert pane only)",
            status="open",
            source_reference="FIX_SCEN11_ALERT",
        ),
        review_spec=ReviewSpec(
            status="fixture_in_progress",
            fixture_marker="FIX_SCEN11_REVIEW",
        ),
        proves=[
            "Risk-tier=VERY_HIGH triggers licensing_refresh required-item",
            "Jurisdiction=Mauritius triggers high-risk-jurisdiction required-item",
            "Sector=crypto triggers sector-specific required-item",
            "Document(s) older than 365 days trigger document_expiry_refresh",
            "Stale screening triggers screening_refresh",
            "Complex ownership triggers ownership-related required-item",
            "Outstanding alert visible in Agent 6 alert pane "
            "(NOT a generated required-item - see Known gaps in REGISTER.md)",
        ],
    ),
]


def by_code(code: str) -> ScenarioDef:
    for s in SCENARIOS:
        if s.code == code:
            return s
    raise KeyError(code)
