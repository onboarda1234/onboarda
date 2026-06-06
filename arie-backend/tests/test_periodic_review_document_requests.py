import json
import os
import sys


TEST_DIR = os.path.dirname(os.path.abspath(__file__))
if TEST_DIR not in sys.path:
    sys.path.insert(0, TEST_DIR)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from periodic_review_document_requests import generate_periodic_review_document_requests
from test_periodic_review_attestation import _PeriodicReviewAttestationBase


QUESTION_CASES = {
    "directors_changed": {
        "answer": "yes",
        "comment": "One new director was appointed.",
        "expected_keys": {
            "updated_register_of_directors",
            "new_director_id_document",
            "new_director_proof_of_address",
        },
    },
    "shareholders_changed": {
        "answer": "yes",
        "comment": "Shareholding changed.",
        "expected_keys": {
            "updated_register_of_shareholders",
            "updated_cap_table",
            "share_transfer_or_allotment_evidence",
        },
    },
    "ubos_changed": {
        "answer": "yes",
        "comment": "Control structure changed.",
        "expected_keys": {
            "updated_ownership_chart",
            "ubo_identification_document",
            "ubo_proof_of_address",
            "proof_of_ownership_or_control",
        },
    },
    "business_activity_changed": {
        "answer": "yes",
        "comment": "New product line launched.",
        "expected_keys": {
            "updated_business_activity_description",
            "website_product_operating_evidence",
            "contracts_invoices_or_commercial_evidence",
            "regulated_activity_licence_or_approval",
        },
    },
    "jurisdictions_changed": {
        "answer": "yes",
        "comment": "Expanded target markets.",
        "expected_keys": {
            "jurisdiction_rationale",
            "operating_countries_target_markets_list",
            "market_operations_supporting_evidence",
        },
    },
    "transaction_volume_changed": {
        "answer": "yes",
        "comment": "Expected volumes increased.",
        "expected_keys": {
            "updated_transaction_volume_rationale",
            "expected_transaction_flow_explanation",
            "financials_bank_statements_or_projections",
        },
    },
    "licence_regulatory_status_changed": {
        "answer": "yes",
        "comment": "A new licence was issued.",
        "expected_keys": {
            "licence_or_registration_certificate",
            "regulator_approval_or_correspondence",
            "updated_regulatory_disclosure",
        },
    },
    "company_contact_details_correct": {
        "answer": "no",
        "comment": "Registered office and authorised contact changed.",
        "expected_keys": {
            "updated_company_extract",
            "updated_registered_office_proof",
            "updated_authorised_contact_confirmation",
        },
    },
}


class TestPeriodicReviewDocumentRequests(_PeriodicReviewAttestationBase):
    def _submit_payload(self, overrides=None):
        answers = {
            "directors_changed": {"answer": "no", "comment": ""},
            "shareholders_changed": {"answer": "no", "comment": ""},
            "ubos_changed": {"answer": "no", "comment": ""},
            "business_activity_changed": {"answer": "no", "comment": ""},
            "jurisdictions_changed": {"answer": "no", "comment": ""},
            "transaction_volume_changed": {"answer": "no", "comment": ""},
            "licence_regulatory_status_changed": {"answer": "no", "comment": ""},
            "company_contact_details_correct": {"answer": "yes", "comment": ""},
        }
        for key, value in (overrides or {}).items():
            answers[key] = value
        return {"answers": answers, "declaration_accepted": True}

    def _generated_rows(self):
        return self._conn.execute(
            """
            SELECT *
            FROM application_enhanced_requirements
            WHERE application_id = 'app-owned'
            ORDER BY id
            """
        ).fetchall()

    def test_submit_with_no_material_changes_generates_no_conditional_document_requests(self):
        resp = self._post(
            "/api/portal/applications/app-owned/periodic-review/submit",
            self._submit_payload(),
            self.client_token,
        )
        assert resp.code == 200
        body = json.loads(resp.body)
        assert body["document_request_count"] == 0
        assert body["document_requests"] == []
        assert self._generated_rows() == []

        audit = self._conn.execute(
            """
            SELECT detail
            FROM audit_log
            WHERE action = 'periodic_review_document_requests_generated'
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        assert audit is not None
        detail = json.loads(audit["detail"])
        assert detail["periodic_review_id"] == self._owned_review_id
        assert detail["generated_count"] == 0
        assert detail["triggering_question_keys"] == []

    def test_duplicate_open_requests_are_not_recreated(self):
        payload = self._submit_payload({
            "directors_changed": {"answer": "yes", "comment": "One new director was appointed."},
        })
        submit_resp = self._post(
            "/api/portal/applications/app-owned/periodic-review/submit",
            payload,
            self.client_token,
        )
        assert submit_resp.code == 200
        body = json.loads(submit_resp.body)
        original_ids = [item["id"] for item in body["document_requests"]]
        assert len(original_ids) == 3

        review = self._conn.execute(
            "SELECT * FROM periodic_reviews WHERE id = ?",
            (self._owned_review_id,),
        ).fetchone()
        app = self._conn.execute(
            "SELECT * FROM applications WHERE id = 'app-owned'"
        ).fetchone()
        generation = generate_periodic_review_document_requests(
            self._conn,
            review,
            app,
            body["attestation"],
            actor={"sub": "admin001", "name": "Admin User", "role": "admin"},
            generation_source="backoffice_periodic_review_documents",
        )
        self._conn.commit()

        rows = self._generated_rows()
        assert len(rows) == 3
        assert generation["generated_count"] == 0
        assert generation["deduped_count"] == 3
        assert sorted(generation["deduped_request_ids"]) == sorted(original_ids)

    def test_client_can_only_see_own_periodic_review_document_requests(self):
        self._post(
            "/api/portal/applications/app-owned/periodic-review/submit",
            self._submit_payload({
                "shareholders_changed": {"answer": "yes", "comment": "Shareholding changed."},
            }),
            self.client_token,
        )

        owned = self._get("/api/portal/applications/app-owned/enhanced-requirements", self.client_token)
        assert owned.code == 200
        owned_body = json.loads(owned.body)
        assert owned_body["total"] == 3
        assert all("risk_level" not in item for item in owned_body["requirements"])

        forbidden = self._get("/api/portal/applications/app-owned/enhanced-requirements", self.other_client_token)
        assert forbidden.code == 403

    def test_backoffice_review_detail_surfaces_review_linked_document_requests_and_triggering_question(self):
        self._post(
            "/api/portal/applications/app-owned/periodic-review/submit",
            self._submit_payload({
                "jurisdictions_changed": {"answer": "yes", "comment": "Expanded target markets."},
            }),
            self.client_token,
        )
        resp = self._get(f"/api/monitoring/reviews/{self._owned_review_id}", self.admin_token)
        assert resp.code == 200
        body = json.loads(resp.body)
        requests = body["periodic_review_document_requests"]
        assert len(requests) == 3
        assert all(item["linked_periodic_review_id"] == self._owned_review_id for item in requests)
        assert all(item["trigger_category"] == "periodic_review_attestation" for item in requests)
        assert any(item["trigger_question_key"] == "jurisdictions_changed" for item in requests)

    def test_audit_events_are_emitted_for_created_and_deduped_requests(self):
        self._post(
            "/api/portal/applications/app-owned/periodic-review/submit",
            self._submit_payload({
                "company_contact_details_correct": {
                    "answer": "no",
                    "comment": "Registered office and authorised contact changed.",
                },
            }),
            self.client_token,
        )
        created = self._conn.execute(
            "SELECT COUNT(*) AS c FROM audit_log WHERE action = 'periodic_review_document_request_created'"
        ).fetchone()["c"]
        generated = self._conn.execute(
            "SELECT COUNT(*) AS c FROM audit_log WHERE action = 'periodic_review_document_requests_generated'"
        ).fetchone()["c"]
        assert created == 3
        assert generated == 1


def _make_generation_test(question_key, case):
    def test(self):
        resp = self._post(
            "/api/portal/applications/app-owned/periodic-review/submit",
            self._submit_payload({
                question_key: {"answer": case["answer"], "comment": case["comment"]},
            }),
            self.client_token,
        )
        assert resp.code == 200
        body = json.loads(resp.body)
        rows = self._generated_rows()
        requirement_keys = {row["requirement_key"] for row in rows}
        assert requirement_keys == set(case["expected_keys"])
        assert body["document_request_count"] == len(case["expected_keys"])
        assert {item["label"] for item in body["document_requests"]}
        assert all(row["linked_periodic_review_id"] == self._owned_review_id for row in rows)
        assert all(row["trigger_category"] == "periodic_review_attestation" for row in rows)
    return test


for _question_key, _case in QUESTION_CASES.items():
    setattr(
        TestPeriodicReviewDocumentRequests,
        f"test_{_question_key}_generates_expected_document_requests",
        _make_generation_test(_question_key, _case),
    )
