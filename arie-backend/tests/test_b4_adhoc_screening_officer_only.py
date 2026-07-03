"""Ad-hoc screening endpoints must be officer-only (audit finding B4/M1).

The client portal used to call POST /api/screening/sanctions as an applicant
typed director/UBO names. The endpoint (and its /company and /ip siblings) used
a bare require_auth() with no role check and no rate limit, so any authenticated
client could burn paid screening quota and receive third-party sanctions data.
The client-side call has been removed and the endpoints are now officer-only.
"""
import io
import json
import unittest

import bcrypt


def _run(case_cls, method_name):
    suite = unittest.TestLoader().loadTestsFromName(method_name, case_cls)
    result = unittest.TextTestRunner(verbosity=0, stream=io.StringIO()).run(suite)
    assert result.wasSuccessful(), f"B4 gating regression failed: {result.failures + result.errors}"


def test_client_token_denied_on_adhoc_screening(db):
    from tornado.testing import AsyncHTTPTestCase
    from server import make_app, create_token

    pw = bcrypt.hashpw("ClientPass123!".encode(), bcrypt.gensalt()).decode()
    db.execute(
        "INSERT OR IGNORE INTO clients (id, email, password_hash, company_name, status) VALUES (?, ?, ?, ?, 'active')",
        ("client-b4", "b4@example.com", pw, "B4 Co"),
    )
    db.commit()
    client_token = create_token("client-b4", "client", "B4 Client", "client")

    class _App(AsyncHTTPTestCase):
        def get_app(self_inner):
            return make_app()

        def test_flow(self_inner):
            headers = {"Authorization": f"Bearer {client_token}", "Content-Type": "application/json"}
            # POST /api/screening/sanctions — must be denied for a client (was 200).
            r = self_inner.fetch(
                "/api/screening/sanctions", method="POST",
                body=json.dumps({"name": "Jane Doe", "entity_type": "Person"}),
                headers=headers,
            )
            assert r.code in (401, 403), f"sanctions: expected denial, got {r.code}: {r.body.decode()}"
            # POST /api/screening/company — must be denied for a client.
            r = self_inner.fetch(
                "/api/screening/company", method="POST",
                body=json.dumps({"company_name": "Acme Ltd"}),
                headers=headers,
            )
            assert r.code in (401, 403), f"company: expected denial, got {r.code}: {r.body.decode()}"
            # GET /api/screening/ip — must be denied for a client.
            r = self_inner.fetch("/api/screening/ip?ip=8.8.8.8", headers={"Authorization": f"Bearer {client_token}"})
            assert r.code in (401, 403), f"ip: expected denial, got {r.code}: {r.body.decode()}"

    _run(_App, "test_flow")
