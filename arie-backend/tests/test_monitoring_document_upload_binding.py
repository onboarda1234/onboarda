"""Fail-closed guards for the document-renewal upload-binding boundary.

These tests intentionally inspect structure as well as behaviour: future edits
must not turn a staged, identity-bound candidate into document verification,
canonical replacement, Agent 1 execution, or Monitoring Alert resolution.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re

import environment
import monitoring_document_renewal as renewal


BACKEND_ROOT = Path(__file__).resolve().parents[1]
RENEWAL_PATH = BACKEND_ROOT / "monitoring_document_renewal.py"
SERVER_PATH = BACKEND_ROOT / "server.py"


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )


def _class(tree: ast.Module, name: str) -> ast.ClassDef:
    return next(
        node
        for node in tree.body
        if isinstance(node, ast.ClassDef) and node.name == name
    )


def test_binding_contract_is_versioned_without_relabelling_request_contract():
    assert renewal.CONTRACT_VERSION == "monitoring_document_renewal_request_v1"
    assert renewal.ORIGINATING_PR == "PR-MON-DOC-RENEWAL-REQUEST-1"
    assert renewal.UPLOAD_BINDING_CONTRACT_VERSION == (
        "monitoring_document_renewal_upload_binding_v1"
    )
    assert renewal.UPLOAD_BINDING_ORIGINATING_PR == (
        "PR-MON-DOC-UPLOAD-BINDING-1"
    )
    assert renewal.UPLOADED_DOCUMENT_ID_PREFIX == "renewal-candidate:"
    assert "verifying" not in renewal.REQUEST_STATUSES
    assert "verified" not in renewal.REQUEST_STATUSES
    assert "resolved" not in renewal.REQUEST_STATUSES


def test_record_service_has_one_atomic_binding_path_and_no_protected_writes():
    source = RENEWAL_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = _function(tree, "record_renewal_upload")
    region = ast.get_source_segment(source, node)

    for required in (
        "_require_expected_upload_scope",
        "_require_request_accepts_upload",
        "_validate_request_linkage",
        "_validate_upload_binding",
        "monitoring_document_renewal_uploads",
        "monitoring_document_renewal_upload_bindings",
        "_audit_upload_bound",
        "db.commit()",
        "_rollback(db)",
    ):
        assert required in region

    audit_node = _function(tree, "_audit_upload_bound")
    audit_region = ast.get_source_segment(source, audit_node)
    assert "monitoring.document_renewal.upload_bound" in audit_region

    assert region.index("INSERT INTO monitoring_document_renewal_upload_bindings") < (
        region.index("_audit_upload_bound")
    )
    assert region.index("_audit_upload_bound") < region.rindex("db.commit()")
    assert "except Exception" in region
    assert "binding_missing" in region
    assert "upload_already_received" in region

    assert not re.search(
        r"\b(?:INSERT\s+INTO|UPDATE|DELETE\s+FROM)\s+"
        r"(?:documents|monitoring_alerts|agent_executions|"
        r"application_enhanced_requirements|screening_[a-z0-9_]+)\b",
        region,
        re.IGNORECASE,
    )
    for prohibited in (
        "transition_alert_status",
        "queue_verification_for_document",
        "invoke_agent",
        "_monitoring_document_refresh",
        "_prepare_document_slot_replacement",
        "verification_status",
        "review_status",
        "superseded_by_document_id",
    ):
        assert prohibited not in region


def test_preflight_rejection_auditor_preserves_alert_then_request_lock_order():
    source = RENEWAL_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = _function(tree, "audit_renewal_upload_preflight_rejection")
    region = ast.get_source_segment(source, node)
    transaction_region = region[region.index("_begin_write(db)") :]

    assert transaction_region.index("_lock_alert(") < transaction_region.index(
        "_load_request(db, normalized_request_id, lock=True)"
    )


def test_portal_handler_supplies_exact_server_owned_scope_only():
    source = SERVER_PATH.read_text(encoding="utf-8")
    tree = ast.parse(source)
    node = _class(tree, "PortalDocumentRenewalUploadHandler")
    region = ast.get_source_segment(source, node)

    call = next(
        item
        for item in ast.walk(node)
        if isinstance(item, ast.Call)
        and isinstance(item.func, ast.Attribute)
        and item.func.attr == "record_renewal_upload"
    )
    keywords = {item.arg: item.value for item in call.keywords}
    assert set(
        (
            "expected_application_id",
            "expected_customer_id",
            "expected_person_id",
            "expected_original_document_id",
            "expected_document_type",
        )
    ) <= set(keywords)
    for value in keywords.values():
        assert not (
            isinstance(value, ast.Call)
            and isinstance(value.func, ast.Attribute)
            and value.func.attr in {"get_argument", "get_body_argument"}
        )

    for prohibited in (
        "queue_verification_for_document",
        "invoke_agent",
        "transition_alert_status",
        "_prepare_document_slot_replacement",
        "_monitoring_document_refresh",
    ):
        assert prohibited not in region


def test_all_governed_monitoring_flags_still_default_off(monkeypatch):
    for flag in environment.MONITORING_FEATURE_FLAGS:
        monkeypatch.delenv(flag, raising=False)
    state = environment.get_monitoring_feature_state(
        environment.FeatureFlags("staging")
    )
    assert state == {
        flag: False for flag in environment.MONITORING_FEATURE_FLAGS
    }
