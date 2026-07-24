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

    def _link_verified_documents(self, requests, prefix):
        from enhanced_requirements import enhanced_requirement_document_policy

        for idx, requirement in enumerate(requests, start=1):
            doc_id = f"{prefix}-{idx}"
            stored_requirement = self._conn.execute(
                """
                SELECT requirement_key
                  FROM application_enhanced_requirements
                 WHERE id=?
                """,
                (requirement["id"],),
            ).fetchone()
            assert stored_requirement is not None
            doc_type = enhanced_requirement_document_policy(
                stored_requirement["requirement_key"]
            )["document_type"]
            self._conn.execute(
                """
                INSERT INTO documents
                (id, application_id, doc_type, doc_name, file_path, slot_key,
                 is_current, version, uploaded_at, verification_status, review_status)
                VALUES (?, ?, ?, ?, ?, ?, 1, 1, datetime('now'), 'verified', 'accepted')
                """,
                (
                    doc_id,
                    "app-owned",
                    doc_type,
                    f"{prefix}-{idx}.pdf",
                    f"/tmp/{prefix}-{idx}.pdf",
                    f"enhanced_requirement:{requirement['id']}",
                ),
            )
            self._conn.execute(
                """
                UPDATE application_enhanced_requirements
                SET linked_document_id = ?, status = 'uploaded'
                WHERE id = ?
                """,
                (doc_id, requirement["id"]),
            )

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

    def test_workspace_decision_payload_exposes_current_review_gates(self):
        resp = self._get(f"/api/monitoring/reviews/{self._owned_review_id}", self.admin_token)
        assert resp.code == 200
        body = json.loads(resp.body)
        workspace = body["periodic_review_workspace"]
        decision = workspace["decision"]

        assert decision["periodic_review_id"] == self._owned_review_id
        assert decision["review_reference"].startswith("PR-")
        assert decision["status_label"] == "Awaiting client attestation"
        assert decision["owner"] == "Compliance Officer"
        assert decision["risk_level"] == "HIGH"
        assert decision["attestation_status"] == "not_started"
        assert "Client attestation has not been submitted" in decision["readiness_blockers"]
        assert workspace["future_actions"] == []

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

    def test_workspace_readiness_becomes_ready_for_outcome_decision_when_docs_are_uploaded(self):
        submit = self._post(
            "/api/portal/applications/app-owned/periodic-review/submit",
            self._submit_payload({
                "shareholders_changed": {"answer": "yes", "comment": "Shareholding changed."},
            }),
            self.client_token,
        )
        assert submit.code == 200
        requests = json.loads(submit.body)["document_requests"]
        self._link_verified_documents(requests, "doc-prs4-ready")
        self._conn.commit()

        resp = self._get(f"/api/monitoring/reviews/{self._owned_review_id}", self.admin_token)
        assert resp.code == 200
        body = json.loads(resp.body)
        assert body["status_label"] == "Officer review required"
        assert body["projection"]["status_label"] == "Officer review required"
        readiness = body["periodic_review_workspace"]["readiness"]
        assert readiness["state"] == "ready_for_outcome_decision"

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
        self._link_verified_documents(requests, "doc-prs4-decision")
        self._conn.execute(
            """
            UPDATE periodic_reviews
            SET officer_findings_note = ?, officer_internal_review_note = ?
            WHERE id = ?
            """,
            ("Evidence reviewed.", "Prepare closure outcome.", self._owned_review_id),
        )
        self._conn.commit()

        resp = self._get(f"/api/monitoring/reviews/{self._owned_review_id}", self.admin_token)
        assert resp.code == 200
        body = json.loads(resp.body)
        assert body["status_label"] == "Ready for decision"
        assert body["projection"]["status_label"] == "Ready for decision"
        assert body["periodic_review_workspace"]["readiness"]["state"] == "ready_for_outcome_decision"

    def test_legacy_draft_findings_are_surfaced_in_decision_payload(self):
        self._conn.execute(
            """
            UPDATE periodic_reviews
            SET officer_findings_note = ?,
                officer_deficiencies_note = ?,
                officer_internal_review_note = ?
            WHERE id = ?
            """,
            (
                "Legacy PRS-4 findings retained.",
                "Legacy follow-up point retained.",
                "Legacy senior note retained.",
                self._owned_review_id,
            ),
        )
        self._conn.commit()

        resp = self._get(f"/api/monitoring/reviews/{self._owned_review_id}", self.admin_token)
        assert resp.code == 200
        body = json.loads(resp.body)
        decision = body["periodic_review_workspace"]["decision"]
        draft = body["periodic_review_workspace"]["findings_draft"]
        assert decision["findings_summary"] == "Legacy PRS-4 findings retained."
        assert decision["follow_up_notes"] == "Legacy follow-up point retained."
        assert decision["senior_review_note"] == "Legacy senior note retained."
        assert draft["officer_findings_note"] == "Legacy PRS-4 findings retained."

    def test_completed_historical_review_does_not_override_new_active_review(self):
        self._conn.execute(
            """
            UPDATE periodic_reviews
            SET status = 'completed',
                outcome = 'no_material_change',
                outcome_reason = 'Historical review completed.',
                completed_at = datetime('now')
            WHERE id = ?
            """,
            (self._owned_review_id,),
        )
        self._conn.execute(
            """
            INSERT INTO periodic_reviews
            (application_id, client_name, risk_level, status, due_date, assigned_officer, baseline_status, client_attestation_status, created_at)
            VALUES
            ('app-owned', 'Owned Co Ltd', 'HIGH', 'in_progress', '2026-07-20', 'co001', 'not_applicable', 'submitted', datetime('now'))
            """
        )
        self._conn.commit()
        new_review_id = self._conn.execute(
            "SELECT id FROM periodic_reviews WHERE application_id = 'app-owned' AND status = 'in_progress' ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]

        lifecycle = self._get("/api/lifecycle/applications/app-owned/summary", self.admin_token)
        assert lifecycle.code == 200
        body = json.loads(lifecycle.body)
        active_review_ids = [
            item["id"] for item in body["active"]["items"]
            if item["type"] == "review"
        ]
        historical_review_ids = [
            item["id"] for item in body["historical"]["items"]
            if item["type"] == "review"
        ]
        assert new_review_id in active_review_ids
        assert self._owned_review_id not in active_review_ids
        assert self._owned_review_id in historical_review_ids
        assert body["review_setup"]["review_id"] == new_review_id

    def test_officer_can_save_workspace_findings_and_audit_event_is_created(self):
        resp = self._post(
            f"/api/monitoring/reviews/{self._owned_review_id}/findings",
            {
                "officer_findings_note": "Attestation and evidence reviewed.",
                "officer_deficiencies_note": "Awaiting officer review on one uploaded file.",
                "officer_internal_review_note": "Prepare closure outcome once the last document is cleared.",
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
        assert row["officer_internal_review_note"] == "Prepare closure outcome once the last document is cleared."

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
