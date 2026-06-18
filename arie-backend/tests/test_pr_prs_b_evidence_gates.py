"""PR-PRS-B: periodic review document-request + Agent 1 evidence gate tests.

Covers:
- P0-A1 / P1-EV2: periodic review document keys resolve to canonical Agent 1
  policies (runtime-executable) instead of falling through to supporting_document.
- P2-EV4: subject_scope is constrained to the allowed set.
- P0-EV1 / P1-A2 / P1-EV3: officer "accepted" no longer bypasses verification;
  only verified, or a senior (admin/sco) acceptance with a reason on a
  non-failed/non-pending document, satisfies completion; stale documents do not.
"""

from enhanced_requirements import enhanced_requirement_document_policy
from periodic_review_document_requests import _coerce_subject_scope
from periodic_review_blockers import _document_request_ready


# --- P0-A1 / P1-EV2: canonical policy mapping ---------------------------------

def test_pr_document_keys_resolve_to_runtime_executable_policies():
    mapped = {
        "updated_register_of_directors": "reg_dir",
        "new_director_id_document": "passport",
        "new_director_proof_of_address": "poa",
        "updated_register_of_shareholders": "reg_sh",
        "updated_ownership_chart": "structure_chart",
        "licence_or_registration_certificate": "licence",
        "financials_bank_statements_or_projections": "bank_statements",
        "updated_company_extract": "cert_inc",
        "updated_authorised_contact_confirmation": "board_res",
    }
    for key, doc_type in mapped.items():
        policy = enhanced_requirement_document_policy(key)
        assert policy["document_type"] == doc_type, (key, policy["document_type"])
        assert policy["runtime_executable"] is True, key
        assert policy["manual_review_only"] is False, key


def test_disclosure_keys_remain_manual_pending_section_e_split():
    # Narrative/disclosure keys are intentionally NOT mapped yet.
    for key in (
        "jurisdiction_rationale",
        "operating_countries_target_markets_list",
        "updated_business_activity_description",
    ):
        policy = enhanced_requirement_document_policy(key)
        assert policy["runtime_executable"] is False, key
        assert policy["document_type"] == "supporting_document", key


# --- P2-EV4: subject_scope guard ----------------------------------------------

def test_subject_scope_allowed_values_pass_through():
    for scope in ("company", "director", "ubo", "controller", "application", "screening_subject"):
        assert _coerce_subject_scope(scope) == scope


def test_subject_scope_unknown_defaults_to_application():
    assert _coerce_subject_scope("weird_scope") == "application"
    assert _coerce_subject_scope("") == "application"
    assert _coerce_subject_scope(None) == "application"
    assert _coerce_subject_scope("DIRECTOR") == "director"  # normalised


# --- P0-EV1 / P1-A2 / P1-EV3: tightened completion gate ------------------------

def _row(**kw):
    base = {
        "status": "accepted",
        "workflow_test_accepted": 0,
        "linked_document_id": "doc-1",
        "document_verification_status": "skipped",
        "document_review_status": "",
        "document_reviewer_role": "",
        "document_review_comment": "",
        "document_is_current": 1,
    }
    base.update(kw)
    return base


def test_accepted_over_unverified_document_does_not_satisfy_completion():
    # P0-EV1: requirement marked accepted, document never verified, plain CO.
    row = _row(status="accepted", document_verification_status="skipped",
               document_review_status="accepted", document_reviewer_role="co",
               document_review_comment="looks fine")
    assert _document_request_ready(row) is False


def test_accepted_with_no_linked_document_blocks():
    row = _row(status="accepted", linked_document_id="")
    assert _document_request_ready(row) is False


def test_verified_document_satisfies_completion():
    row = _row(document_verification_status="verified")
    assert _document_request_ready(row) is True


def test_senior_accept_with_reason_on_manual_doc_satisfies():
    # P1-A2: controlled senior exception for a manual/skipped document.
    row = _row(document_verification_status="skipped",
               document_review_status="accepted", document_reviewer_role="sco",
               document_review_comment="Verified manually against source register")
    assert _document_request_ready(row) is True


def test_co_accept_with_reason_does_not_satisfy():
    # P1-A2: a plain officer cannot self-clear unverified evidence.
    row = _row(document_verification_status="skipped",
               document_review_status="accepted", document_reviewer_role="co",
               document_review_comment="ok")
    assert _document_request_ready(row) is False


def test_senior_accept_without_comment_does_not_satisfy():
    row = _row(document_verification_status="skipped",
               document_review_status="accepted", document_reviewer_role="sco",
               document_review_comment="")
    assert _document_request_ready(row) is False


def test_failed_verification_not_senior_overridable():
    # Hard failure must not be senior-overridden.
    row = _row(document_verification_status="failed",
               document_review_status="accepted", document_reviewer_role="admin",
               document_review_comment="override")
    assert _document_request_ready(row) is False


def test_pending_verification_not_ready():
    row = _row(document_verification_status="pending",
               document_review_status="accepted", document_reviewer_role="sco",
               document_review_comment="wait")
    assert _document_request_ready(row) is False


def test_stale_document_does_not_satisfy_completion():
    # P1-EV3: even a verified document that is no longer current is not ready.
    row = _row(document_verification_status="verified", document_is_current=0)
    assert _document_request_ready(row) is False


def test_waived_remains_terminal():
    row = _row(status="waived", linked_document_id="")
    assert _document_request_ready(row) is True
