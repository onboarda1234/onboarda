import os
import sys
import tempfile
import uuid

from tornado.testing import AsyncHTTPTestCase


sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


def _sync_db_path(path):
    os.environ["DB_PATH"] = path
    for module_name in ("config", "db", "server"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "DB_PATH"):
            setattr(module, "DB_PATH", path)
        if module_name == "server" and module is not None and hasattr(module, "_CFG_DB_PATH"):
            setattr(module, "_CFG_DB_PATH", path)


def _policies_by_key():
    from document_policy_registry import get_canonical_document_policies

    return {policy["document_type"]: policy for policy in get_canonical_document_policies()}


def _workflows_by_key():
    from document_policy_registry import get_workflow_usage_mappings

    return {workflow["workflow"]: workflow for workflow in get_workflow_usage_mappings()}


def test_active_document_policies_have_backend_metadata_and_methods():
    policies = _policies_by_key()
    active = [policy for policy in policies.values() if policy["active_pilot_status"] == "Active"]

    assert active
    for policy in active:
        assert policy["backend_executable"] is True, policy["document_type"]
        assert policy["backend_sources"], policy["document_type"]
        assert policy["material_checks"], policy["document_type"]
        assert any(
            sum(policy["check_method_counts"].get(method, 0) for method in ("Rule", "Hybrid", "AI")) > 0
            for _ in [policy]
        ), policy["document_type"]
        assert policy["policy_id"].startswith("DOC-"), policy["document_type"]


def test_manual_and_future_policies_are_not_presented_as_runtime_verified():
    policies = _policies_by_key()

    for key, policy in policies.items():
        if policy["active_pilot_status"] in {"Manual review only", "Future / enterprise"}:
            assert policy["backend_executable"] is False, key
            assert policy["runtime_verified"] is False, key

    assert policies["sar_str_support"]["active_pilot_status"] == "Future / enterprise"
    assert policies["sar_str_support"]["backend_executable"] is False


def test_upload_allowlist_types_map_to_policy_or_manual_future_status():
    from document_policy_registry import get_policy_alias_map

    document_type_allowlist = {
        "aml_policy",
        "bank_statements",
        "bankref",
        "board_res",
        "cert_gs",
        "cert_inc",
        "contracts",
        "cv",
        "director_id",
        "drivers_license",
        "fin_stmt",
        "general",
        "id_card",
        "licence",
        "memarts",
        "national_id",
        "passport",
        "pep_declaration",
        "poa",
        "reg_dir",
        "reg_sh",
        "regulatory_intelligence",
        "sow",
        "source_funds",
        "source_wealth",
        "structure_chart",
        "supporting_document",
        "trust_deed",
        "ubo_id",
    }

    alias_map = get_policy_alias_map()
    policies = _policies_by_key()

    for doc_type in document_type_allowlist:
        canonical = alias_map.get(doc_type)
        assert canonical, f"{doc_type} is accepted for upload but invisible in the policy registry"
        status = policies[canonical]["active_pilot_status"]
        assert status in {"Active", "Manual review only", "Future / enterprise"}


def test_identity_allowlist_aliases_map_to_canonical_national_id_policy():
    from document_policy_registry import get_policy_alias_map

    alias_map = get_policy_alias_map()
    for alias in ("id_card", "drivers_license", "director_id", "ubo_id"):
        assert alias_map[alias] == "national_id"


def test_core_workflow_mappings_reuse_canonical_document_policies():
    workflows = _workflows_by_key()

    assert "reg_dir" in workflows["onboarding"]["required_documents"]
    assert "reg_dir" in workflows["director_change"]["required_documents"]
    assert "reg_dir" in workflows["periodic_review"]["required_documents"]

    for workflow in ("onboarding", "director_change", "ubo_change", "dob_correction", "nationality_correction", "passport_expiry", "periodic_review"):
        assert "passport" in workflows[workflow]["required_documents"]

    for workflow in ("onboarding", "address_change", "periodic_review"):
        assert "poa" in workflows[workflow]["required_documents"]

    assert workflows["company_name_change"]["required_documents"] == ["certificate_name_change"]
    assert any("re-screen" in trigger.lower() for trigger in workflows["director_change"]["re_screening_triggers"])
    assert any("re-screen" in trigger.lower() for trigger in workflows["ubo_change"]["re_screening_triggers"])
    assert any("risk" in trigger.lower() for trigger in workflows["ownership_percentage_change"]["risk_score_triggers"])


def test_edd_monitoring_and_regulatory_scope_is_honest_for_pilot():
    policies = _policies_by_key()
    workflows = _workflows_by_key()

    for doc_type in ("source_wealth", "source_funds", "bank_statements", "bankref"):
        assert doc_type in workflows["edd_basic"]["required_documents"]
        assert policies[doc_type]["active_pilot_status"] == "Active"

    for doc_type in ("tax_return", "payslip", "inheritance_evidence", "sale_agreement", "loan_agreement", "adverse_media_response"):
        assert policies[doc_type]["active_pilot_status"] == "Manual review only"

    assert policies["monitoring_support_evidence"]["active_pilot_status"] == "Manual review only"
    assert policies["regulatory_intelligence"]["active_pilot_status"] == "Manual review only"
    assert policies["sar_str_support"]["active_pilot_status"] == "Future / enterprise"


def test_policy_payload_summary_keeps_sar_inactive_and_unknown_review_enabled():
    from document_policy_registry import build_document_policy_payload

    payload = build_document_policy_payload()
    summary = payload["summary"]

    assert summary["active_policies"] > 0
    assert summary["manual_review_only_policies"] > 0
    assert summary["future_enterprise_policies"] == 1
    assert summary["sar_str_active"] is False
    assert summary["unknown_documents_require_review"] is True
    assert payload["unknown_unclassified_handling"]["automated_reliance_allowed"] is False


class DocumentPolicyConfigApiTest(AsyncHTTPTestCase):
    def get_app(self):
        self._db_path = os.path.join(
            tempfile.gettempdir(),
            f"onboarda_doc_policy_registry_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
        )
        try:
            os.unlink(self._db_path)
        except OSError:
            pass
        _sync_db_path(self._db_path)

        from db import get_db, init_db, seed_initial_data

        init_db()
        conn = get_db()
        seed_initial_data(conn)
        conn.commit()
        conn.close()

        from server import make_app

        return make_app()

    def setUp(self):
        super().setUp()
        from server import create_token

        self.admin_token = create_token("admin001", "admin", "Test Admin", "officer")

    def tearDown(self):
        super().tearDown()
        try:
            os.unlink(self._db_path)
        except OSError:
            pass

    def test_document_policy_config_endpoint_exposes_canonical_registry(self):
        response = self.fetch(
            "/api/config/document-policies",
            headers={"Authorization": f"Bearer {self.admin_token}"},
        )

        assert response.code == 200
        import json

        payload = json.loads(response.body.decode("utf-8"))
        assert payload["summary"]["registry_version"] == "DOC-POLICY-CANONICAL-v1"
        assert payload["summary"]["sar_str_active"] is False
        assert any(policy["document_type"] == "passport" for policy in payload["document_policies"])
        assert any(workflow["workflow"] == "director_change" for workflow in payload["workflow_usages"])
