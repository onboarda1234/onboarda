import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from test_periodic_review_attestation import _PeriodicReviewAttestationBase


class TestPeriodicReviewWorkspace(_PeriodicReviewAttestationBase):
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

    def test_workspace_overview_exposes_core_review_context(self):
        resp = self._get(f"/api/monitoring/reviews/{self._owned_review_id}", self.admin_token)
        assert resp.code == 200
        body = json.loads(resp.body)
        workspace = body["periodic_review_workspace"]
        overview = workspace["overview"]
        assert overview["review_reference"].startswith("PR-")
        assert overview["company_name"] == "Owned Co Ltd"
        assert overview["risk_level"] == "HIGH"
        assert overview["owner"] == "Compliance Officer"
        assert overview["due_date"] == "2026-06-20"

    def test_workspace_readiness_awaits_client_attestation_when_missing(self):
        resp = self._get(f"/api/monitoring/reviews/{self._owned_review_id}", self.admin_token)
        assert resp.code == 200
        body = json.loads(resp.body)
        assert body["status_label"] == "Awaiting client attestation"
        assert body["projection"]["status_label"] == "Awaiting client attestation"
        readiness = body["periodic_review_workspace"]["readiness"]
        assert readiness["state"] == "awaiting_client_attestation"
        assert "Client attestation has not been submitted" in readiness["blockers"]

        queue = self._get("/api/monitoring/reviews?queue=open", self.admin_token)
        assert queue.code == 200
        queue_row = next(row for row in json.loads(queue.body)["reviews"] if row["id"] == self._owned_review_id)
        assert queue_row["status_label"] == "Awaiting client attestation"
        assert queue_row["queue_status_label"] == "Awaiting client attestation"

        lifecycle = self._get("/api/lifecycle/applications/app-owned/summary", self.admin_token)
        assert lifecycle.code == 200
        lifecycle_body = json.loads(lifecycle.body)
        review_item = next(
            item for item in lifecycle_body["active"]["items"]
            if item["type"] == "review" and item["id"] == self._owned_review_id
        )
        assert review_item["status_label"] == "Awaiting client attestation"
        assert lifecycle_body["review_setup"]["status_label"] == "Awaiting client attestation"

    def test_workspace_readiness_awaits_documents_when_required_request_missing(self):
        submit = self._post(
            "/api/portal/applications/app-owned/periodic-review/submit",
            self._submit_payload({
                "shareholders_changed": {"answer": "yes", "comment": "Shareholding changed."},
            }),
            self.client_token,
        )
        assert submit.code == 200

        resp = self._get(f"/api/monitoring/reviews/{self._owned_review_id}", self.admin_token)
        assert resp.code == 200
        body = json.loads(resp.body)
        assert body["status_label"] == "Awaiting documents"
        assert body["projection"]["status_label"] == "Awaiting documents"
        readiness = body["periodic_review_workspace"]["readiness"]
        assert readiness["state"] == "awaiting_documents"
        assert readiness["blocker_count"] >= 1
        assert any("missing" in blocker.lower() for blocker in readiness["blockers"])

        detail = self._get("/api/applications/app-owned", self.admin_token)
        assert detail.code == 200
        detail_body = json.loads(detail.body)
        assert detail_body["periodic_review"]["status_label"] == "Awaiting documents"

        lifecycle = self._get("/api/lifecycle/applications/app-owned/summary", self.admin_token)
        assert lifecycle.code == 200
        lifecycle_body = json.loads(lifecycle.body)
        review_item = next(
            item for item in lifecycle_body["active"]["items"]
            if item["type"] == "review" and item["id"] == self._owned_review_id
        )
        assert review_item["status_label"] == "Awaiting documents"
        assert lifecycle_body["review_setup"]["status_label"] == "Awaiting documents"

    def test_workspace_readiness_becomes_ready_for_officer_findings_when_docs_are_uploaded(self):
        submit = self._post(
            "/api/portal/applications/app-owned/periodic-review/submit",
            self._submit_payload({
                "shareholders_changed": {"answer": "yes", "comment": "Shareholding changed."},
            }),
            self.client_token,
        )
        assert submit.code == 200
        requests = json.loads(submit.body)["document_requests"]
        for idx, requirement in enumerate(requests, start=1):
            doc_id = f"doc-prs4-ready-{idx}"
            self._conn.execute(
                """
                INSERT INTO documents
                (id, application_id, doc_type, doc_name, file_path, uploaded_at, verification_status, review_status)
                VALUES (?, ?, ?, ?, ?, datetime('now'), 'verified', 'accepted')
                """,
                (doc_id, "app-owned", "supporting_evidence", f"evidence-{idx}.pdf", f"/tmp/evidence-{idx}.pdf"),
            )
            self._conn.execute(
                """
                UPDATE application_enhanced_requirements
                SET linked_document_id = ?, status = 'uploaded'
                WHERE id = ?
                """,
                (doc_id, requirement["id"]),
            )
        self._conn.commit()

        resp = self._get(f"/api/monitoring/reviews/{self._owned_review_id}", self.admin_token)
        assert resp.code == 200
        body = json.loads(resp.body)
        assert body["status_label"] == "Officer review required"
        assert body["projection"]["status_label"] == "Officer review required"
        readiness = body["periodic_review_workspace"]["readiness"]
        assert readiness["state"] == "ready_for_officer_findings"

    def test_workspace_status_becomes_ready_for_decision_when_findings_exist(self):
        submit = self._post(
            "/api/portal/applications/app-owned/periodic-review/submit",
            self._submit_payload({
                "shareholders_changed": {"answer": "yes", "comment": "Shareholding changed."},
            }),
            self.client_token,
        )
        assert submit.code == 200
        requests = json.loads(submit.body)["document_requests"]
        for idx, requirement in enumerate(requests, start=1):
            doc_id = f"doc-prs4-decision-{idx}"
            self._conn.execute(
                """
                INSERT INTO documents
                (id, application_id, doc_type, doc_name, file_path, uploaded_at, verification_status, review_status)
                VALUES (?, ?, ?, ?, ?, datetime('now'), 'verified', 'accepted')
                """,
                (doc_id, "app-owned", "supporting_evidence", f"decision-{idx}.pdf", f"/tmp/decision-{idx}.pdf"),
            )
            self._conn.execute(
                """
                UPDATE application_enhanced_requirements
                SET linked_document_id = ?, status = 'uploaded'
                WHERE id = ?
                """,
                (doc_id, requirement["id"]),
            )
        self._conn.execute(
            """
            UPDATE periodic_reviews
            SET officer_findings_note = ?, officer_internal_review_note = ?
            WHERE id = ?
            """,
            ("Evidence reviewed.", "Prepare PRS-5 outcome.", self._owned_review_id),
        )
        self._conn.commit()

        resp = self._get(f"/api/monitoring/reviews/{self._owned_review_id}", self.admin_token)
        assert resp.code == 200
        body = json.loads(resp.body)
        assert body["status_label"] == "Ready for decision"
        assert body["projection"]["status_label"] == "Ready for decision"
        assert body["periodic_review_workspace"]["readiness"]["state"] == "ready_for_outcome_decision"

    def test_officer_can_save_workspace_findings_and_audit_event_is_created(self):
        resp = self._post(
            f"/api/monitoring/reviews/{self._owned_review_id}/findings",
            {
                "officer_findings_note": "Attestation and evidence reviewed.",
                "officer_deficiencies_note": "Awaiting officer review on one uploaded file.",
                "officer_internal_review_note": "Prepare PRS-5 outcome once the last document is cleared.",
            },
            self.admin_token,
        )
        assert resp.code == 200
        body = json.loads(resp.body)
        assert body["status"] == "findings_saved"

        row = self._conn.execute(
            "SELECT officer_findings_note, officer_deficiencies_note, officer_internal_review_note "
            "FROM periodic_reviews WHERE id = ?",
            (self._owned_review_id,),
        ).fetchone()
        assert row["officer_findings_note"] == "Attestation and evidence reviewed."
        assert row["officer_deficiencies_note"] == "Awaiting officer review on one uploaded file."
        assert row["officer_internal_review_note"] == "Prepare PRS-5 outcome once the last document is cleared."

        audit = self._conn.execute(
            "SELECT action, detail FROM audit_log WHERE action = 'periodic_review_findings_saved' ORDER BY id DESC LIMIT 1"
        ).fetchone()
        assert audit is not None
        detail = json.loads(audit["detail"])
        assert detail["periodic_review_id"] == self._owned_review_id
        assert detail["source_surface"] == "backoffice_periodic_review_workspace"

        audit_log_resp = self._get("/api/applications/app-owned/audit-log?limit=20", self.admin_token)
        assert audit_log_resp.code == 200
        audit_entries = json.loads(audit_log_resp.body)["entries"]
        matching = [entry for entry in audit_entries if entry["action"] == "periodic_review_findings_saved"]
        assert matching
        assert matching[0]["target"] == f"periodic_review:{self._owned_review_id}"
