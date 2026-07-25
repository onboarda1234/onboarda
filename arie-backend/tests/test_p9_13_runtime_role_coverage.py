"""P9-13 open half — runtime role coverage + the APP-CONF-003 seed guard.

Two distinct gaps closed here.

**APP-CONF-003 — the cross-client probe was not cross-client.** The staging
harness (`scripts/qa/application_role_matrix_harness.py`) seeded every one of
its applications under a single `clients` row, then probed a *different
application of the same tenant* and called it
`cross_application_detail_denied`. Because the only client-side authorization
on that route is owner equality (`check_app_ownership`: client `sub` vs
`app.client_id`), the tenant-isolation branch was never reached and the check
passed vacuously — one of the harness's advertised 53 checks was a false
positive from the day #733 merged. `TestHarnessCrossTenantSeedInvariant`
pins the invariant so it cannot silently revert.

**Runtime coverage — what is and is NOT covered here.** The pre-existing
entrypoint test probes actions against a *non-existent* application id, so it
proves only that the `require_auth` decorator fires. This file adds probes
against REAL seeded rows, which reach in-handler rules the decorator cannot.
Adversarial review mutation-tested an earlier revision and proved two of its
tests passed with the very gate they named deleted; the honest ledger is:

  COVERED (mutation-verified to fail when the rule is removed):
    * decision authority — CO refused approve on HIGH. The probed row is
      assigned TO that CO deliberately: assigned elsewhere,
      authorize_signoff_ownership refuses first and the authority rule is
      never reached (that was the earlier false pass).
    * IDV senior-outcome escalation (`_idv_resolution_role_error`).
    * reassignment authority (senior-only `assigned_to` change).
    * cross-tenant isolation, both directions, with a positive control.

  NOT COVERED, deliberately and stated rather than implied:
    * dual control — see the note above `_approve_payload`.
    * memo approve, screening review admission, IDV read: these reach only
      the decorator, duplicating coverage that already exists in
      test_application_role_matrix.py. They are kept as cheap regression
      pins, not claimed as new in-handler coverage.
    * the two deepest screening role rules — pinned statically by
      TestScreeningRoleRulesArePinned below.

These are role-boundary assertions only: every probe asserts an authorization
outcome, never a workflow result, so nothing here depends on (or pins) the
frozen Application Review workflow behaviour.
"""

import json
import os
import sys
import tempfile
import uuid
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")

from tornado.testing import AsyncHTTPTestCase

from scripts.qa.application_role_matrix_harness import (
    CLIENT_ACTORS,
    build_seed_plan,
)

OFFICER_ROLES = ("admin", "sco", "co", "analyst")


class TestHarnessCrossTenantSeedInvariant:
    """The APP-CONF-003 regression guard — asserts the seed plan can actually
    exercise tenant isolation."""

    PLAN_RUN_ID = "20260725T090000Z-a1b2c3"

    def _plan(self):
        return build_seed_plan(self.PLAN_RUN_ID)

    def test_seed_plan_declares_two_distinct_tenants(self):
        plan = self._plan()
        assert len(CLIENT_ACTORS) == 2
        ids = {plan["actors"][key]["id"] for key in CLIENT_ACTORS}
        assert len(ids) == 2, "both client actors must be distinct rows"
        emails = {plan["actors"][key]["email"] for key in CLIENT_ACTORS}
        assert len(emails) == 2

    def test_cross_tenant_probe_target_is_owned_by_the_other_tenant(self):
        """The exact defect: the probe target must NOT share the probing
        client's client_id, or the ownership branch is unreachable."""
        plan = self._plan()
        own = plan["applications"]["client_owned"]
        cross = plan["applications"]["other_client_owned"]
        assert own["client_id"] == plan["actors"]["client"]["id"]
        assert cross["client_id"] == plan["actors"]["other_client"]["id"]
        assert own["client_id"] != cross["client_id"], (
            "cross-tenant probe target shares the probing client's tenant — "
            "check_app_ownership can never fire (APP-CONF-003)"
        )

    def test_every_application_names_its_owning_tenant(self):
        plan = self._plan()
        for scenario, app in plan["applications"].items():
            assert app["owner_actor"] in CLIENT_ACTORS, scenario
            assert app["client_id"] == plan["actors"][app["owner_actor"]]["id"], scenario

    def test_at_least_one_application_per_tenant(self):
        plan = self._plan()
        owners = {app["owner_actor"] for app in plan["applications"].values()}
        assert owners == set(CLIENT_ACTORS), (
            f"every declared tenant needs at least one application; got {owners}"
        )

    def test_harness_probes_the_second_tenant_and_both_directions(self):
        """Source guard: the validation block must actually use the second
        tenant. A seed fix with an unchanged probe would still be vacuous."""
        from pathlib import Path

        source = Path(__file__).resolve().parents[1].joinpath(
            "scripts", "qa", "application_role_matrix_harness.py").read_text(encoding="utf-8")
        assert 'cross_app = applications["other_client_owned"]' in source
        assert '"cross_tenant_detail_denied"' in source
        assert '"reciprocal_cross_tenant_detail_denied"' in source
        # The positive control keeps the probe honest: if the second tenant
        # could not read its OWN application, the 403s above would prove
        # nothing (a broken login would also produce them).
        assert '"own_detail_visible"' in source


def _sync_test_db_path(path):
    os.environ["DB_PATH"] = path
    for module_name in ("config", "db", "server"):
        module = sys.modules.get(module_name)
        if module is not None and hasattr(module, "DB_PATH"):
            setattr(module, "DB_PATH", path)
        if module_name == "server" and module is not None and hasattr(module, "_CFG_DB_PATH"):
            setattr(module, "_CFG_DB_PATH", path)


def _capture_db_path_state():
    state = {"env": os.environ.get("DB_PATH"), "modules": {}}
    for module_name in ("config", "db", "server"):
        module = sys.modules.get(module_name)
        attrs = {}
        if module is not None:
            for attr in ("DB_PATH", "_CFG_DB_PATH"):
                attrs[attr] = (hasattr(module, attr), getattr(module, attr, None))
        state["modules"][module_name] = attrs
    return state


def _restore_db_path_state(state):
    if state.get("env") is None:
        os.environ.pop("DB_PATH", None)
    else:
        os.environ["DB_PATH"] = state["env"]
    for module_name, attrs in state.get("modules", {}).items():
        module = sys.modules.get(module_name)
        if module is None:
            continue
        for attr, (existed, value) in attrs.items():
            if existed:
                setattr(module, attr, value)
            elif hasattr(module, attr):
                delattr(module, attr)


class RuntimeRoleCoverageTest(AsyncHTTPTestCase):
    """Drives the five named cells against REAL seeded applications."""

    def get_app(self):
        self._db_path_state = _capture_db_path_state()
        self._db_path = os.path.join(
            tempfile.gettempdir(),
            f"p9_13_runtime_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
        )
        try:
            os.unlink(self._db_path)
        except OSError:
            pass
        _sync_test_db_path(self._db_path)
        from db import get_db, init_db, seed_initial_data
        from server import make_app

        init_db()
        db = get_db()
        seed_initial_data(db)
        db.commit()
        db.close()
        return make_app()

    def setUp(self):
        super().setUp()
        from db import get_db
        from server import create_token

        self.db = get_db()
        self.actors = {
            "admin": ("p13_admin", "admin"),
            "sco": ("p13_sco", "sco"),
            "sco_peer": ("p13_sco_peer", "sco"),
            "co": ("p13_co", "co"),
            "analyst": ("p13_analyst", "analyst"),
        }
        for label, (actor_id, role) in self.actors.items():
            self.db.execute(
                "INSERT OR REPLACE INTO users (id, email, password_hash, full_name, role, status) "
                "VALUES (?, ?, 'test-only', ?, ?, 'active')",
                (actor_id, f"{label}@p13.test", f"P913 {label}", role),
            )
        for client_id in ("p13_client", "p13_other_client"):
            self.db.execute(
                "INSERT OR REPLACE INTO clients (id, email, password_hash, company_name, status) "
                "VALUES (?, ?, 'test-only', ?, 'active')",
                (client_id, f"{client_id}@p13.test", f"P913 {client_id}"),
            )
        self.db.commit()
        self.tokens = {
            label: create_token(actor_id, role, f"P913 {label}", "officer")
            for label, (actor_id, role) in self.actors.items()
        }
        self.tokens["client"] = create_token("p13_client", "client", "P913 Client", "client")
        self.tokens["other_client"] = create_token(
            "p13_other_client", "client", "P913 Other Client", "client")

        self.apps = {}
        # Real rows, in the stage each cell actually runs in.
        # Assigned to the CO we probe with: otherwise authorize_signoff_ownership
        # fires FIRST and the test passes even with the authority gate deleted
        # (proven by mutation testing in review).
        self._seed_app("high_risk", status="compliance_review", risk="HIGH", assigned_to="p13_co")
        self._seed_app("memo_stage", status="compliance_review", assigned_to="p13_sco")
        self._seed_app("idv_stage", status="kyc_documents", risk="HIGH", assigned_to="p13_sco")
        self._seed_app("assign_stage", status="compliance_review", assigned_to="p13_co")
        self._seed_app("other_tenant", status="draft", client_id="p13_other_client")

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass
        try:
            _restore_db_path_state(self._db_path_state)
        except Exception:
            pass
        try:
            os.unlink(self._db_path)
        except OSError:
            pass
        super().tearDown()

    def _seed_app(self, label, *, status, risk="LOW", assigned_to=None, client_id="p13_client"):
        suffix = uuid.uuid4().hex[:10]
        app_id = f"p13_{label}_{suffix}"
        ref = f"P913-{label.upper()}-{suffix}"
        now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        score = {"LOW": 20, "MEDIUM": 50, "HIGH": 78, "VERY_HIGH": 92}[risk]
        self.db.execute(
            """
            INSERT INTO applications
                (id, ref, client_id, company_name, country, sector, entity_type,
                 status, risk_level, final_risk_level, risk_score, assigned_to,
                 is_fixture, created_at, updated_at, inputs_updated_at)
            VALUES (?, ?, ?, ?, 'Mauritius', 'Synthetic role audit', 'company',
                    ?, ?, ?, ?, ?, 0, ?, ?, ?)
            """,
            (app_id, ref, client_id, f"P913 {label}", status, risk, risk, score,
             assigned_to, now, now, now),
        )
        self.db.commit()
        self.apps[label] = {"id": app_id, "ref": ref, "status": status}
        return self.apps[label]

    def _request(self, role, path, method="GET", payload=None):
        headers = {
            "Authorization": f"Bearer {self.tokens[role]}",
            "Content-Type": "application/json",
        }
        kwargs = {"method": method, "headers": headers, "raise_error": False}
        if method in ("POST", "PUT", "PATCH"):
            kwargs["body"] = json.dumps(payload or {})
        return self.fetch(path, **kwargs)

    # ── Cell 1: approval authority ─────────────────────────────────────────
    def test_co_cannot_approve_high_risk_on_its_own_case(self):
        """The HIGH/VERY_HIGH authority rule in can_decide_application.

        The probed application is assigned to this CO on purpose: with it
        assigned elsewhere, authorize_signoff_ownership refuses first and the
        test would pass even with the authority gate removed.
        """
        app_id = self.apps["high_risk"]["id"]
        resp = self._request("co", f"/api/applications/{app_id}/decision",
                             method="POST", payload=self._approve_payload())
        assert resp.code == 403, (resp.code, resp.body[:300])
        body = resp.body.decode()
        assert "HIGH" in body and "approve" in body.lower(), body
        # Not the ownership refusal — that would mean the authority rule was
        # never reached.
        assert "assigned to another officer" not in body, body

    def test_analyst_and_client_are_refused_the_decision_route(self):
        app_id = self.apps["high_risk"]["id"]
        for role in ("analyst", "client"):
            resp = self._request(role, f"/api/applications/{app_id}/decision",
                                 method="POST", payload=self._approve_payload())
            assert resp.code == 403, (role, resp.code, resp.body[:200])

    def _approve_payload(self):
        return {
            "decision": "approve",
            "decision_reason": "Synthetic role-boundary probe: authority check only.",
            "officer_signoff": {"acknowledged": True, "scope": "decision",
                                "source_context": "ai_advisory"},
        }

    # NOTE — dual control is NOT covered here. Reaching the second-leg pairing
    # rule requires passing risk-staleness, memo-exists, memo-freshness,
    # validate_approval and the document-reliance gate first; against a bare
    # fixture every attempt dies at risk staleness (409), so a "status never
    # became approved" assertion holds even with dual control deleted. Review
    # mutation-tested exactly that and proved the assertion tautological, so
    # the test was removed rather than left as false coverage. The dual-control
    # state machine is exercised by test_dual_approval_race.py and
    # test_e2e_authority_matrix.py, which seed the full precondition chain.

    # ── Cell 2: memo approve ───────────────────────────────────────────────
    def test_memo_approve_role_boundary_on_a_real_application(self):
        app_id = self.apps["memo_stage"]["id"]
        for role in ("co", "analyst", "client"):
            resp = self._request(role, f"/api/applications/{app_id}/memo/approve",
                                 method="POST", payload={})
            assert resp.code == 403, (role, resp.code, resp.body[:200])

    # ── Cell 3: screening second review ────────────────────────────────────
    def test_screening_review_denies_client_and_admits_officers(self):
        payload = {
            "application_id": self.apps["memo_stage"]["id"],
            "subject_type": "company",
            "subject_name": "P913 Runtime Probe",
            "disposition": "follow_up_required",
            "disposition_code": "pending_further_review",
            "rationale": "Synthetic role-boundary probe only; no workflow assertion.",
        }
        assert self._request("client", "/api/screening/review", method="POST",
                             payload=payload).code == 403
        for role in OFFICER_ROLES:
            resp = self._request(role, "/api/screening/review", method="POST",
                                 payload=payload)
            assert resp.code != 403, (role, resp.code, resp.body[:200])

    # NOTE: the two deepest screening role rules — the second-review
    # escalation and the clearance-disposition escalation — sit BEHIND a
    # subject lookup that 404s without a seeded screening report, so they
    # cannot be reached from a bare application fixture. They are pinned
    # statically by TestScreeningRoleRulesArePinned below; driving them at
    # runtime needs a screening fixture and is recorded as the residual on
    # the P9-13 row rather than claimed here.

    # ── Cell 4: IDV ────────────────────────────────────────────────────────
    def test_idv_read_denies_client_and_resolution_denies_analyst(self):
        app_id = self.apps["idv_stage"]["id"]
        assert self._request(
            "client", f"/api/applications/{app_id}/kyc/identity-verifications"
        ).code == 403
        for role in ("analyst", "client"):
            resp = self._request(
                role, f"/api/applications/{app_id}/kyc/identity-verifications/resolve",
                method="POST",
                payload={"outcome": "manual_verification_completed",
                         "reason": "passport_unreadable",
                         "evidence_reviewed": ["passport"],
                         "rationale": "Synthetic role-boundary probe; authority check only.",
                         "confirmation": True})
            assert resp.code == 403, (role, resp.code, resp.body[:200])

    def test_idv_senior_outcomes_are_refused_for_co_on_high_risk(self):
        """HIGH-risk IDV resolution escalates to sco|admin — a real-row-only
        branch (`_idv_resolution_role_error`)."""
        app_id = self.apps["idv_stage"]["id"]
        resp = self._request(
            "co", f"/api/applications/{app_id}/kyc/identity-verifications/resolve",
            method="POST",
            payload={"outcome": "senior_exception_approved",
                     "reason": "passport_unreadable",
                     "evidence_reviewed": ["passport"],
                     "rationale": "Synthetic role-boundary probe; authority check only.",
                     "confirmation": True})
        assert resp.code == 403, (resp.code, resp.body[:300])

    # ── Cell 5: reassignment ───────────────────────────────────────────────
    def test_reassignment_authority_is_senior_only(self):
        app_id = self.apps["assign_stage"]["id"]
        for role in ("co", "analyst", "client"):
            resp = self._request(role, f"/api/applications/{app_id}", method="PATCH",
                                 payload={"assigned_to": "p13_sco",
                                          "reassignment_reason": "probe"})
            assert resp.code == 403, (role, resp.code, resp.body[:200])

    # ── Tenant isolation, runtime ──────────────────────────────────────────
    def test_cross_tenant_isolation_both_directions(self):
        """The in-process twin of the harness probe fixed by APP-CONF-003."""
        own = self.apps["memo_stage"]["id"]
        other = self.apps["other_tenant"]["id"]
        assert self._request("client", f"/api/applications/{other}").code == 403
        assert self._request("client", f"/api/applications/{other}/documents").code == 403
        assert self._request("other_client", f"/api/applications/{own}").code == 403
        # Positive control: the second tenant CAN see its own row, so the 403s
        # above are isolation and not a broken token.
        assert self._request("other_client", f"/api/applications/{other}").code == 200


class TestScreeningRoleRulesArePinned:
    """Static guard for the two screening role escalations that a bare
    application fixture cannot reach (see the note in RuntimeRoleCoverageTest).

    These are the in-handler rules an entrypoint probe cannot see: the
    decorator admits all four officer roles, and only these checks distinguish
    who may complete a second review or clear a hit. Pinning them keeps a
    silent removal from passing CI; it does not substitute for a runtime run.
    """

    def _handler_source(self):
        from pathlib import Path
        import re

        source = Path(__file__).resolve().parents[1].joinpath(
            "server.py").read_text(encoding="utf-8")
        # Terminate at the next COLUMN-0 statement, not the next `class`: the
        # loose pattern captured ~1900 extra lines of module-level helpers, so
        # the guard could bind to a string or a db.commit() outside the class
        # (review demonstrated both false-pass modes).
        match = re.search(
            r"^class ScreeningReviewHandler\b.*?(?=^\S)", source, re.S | re.M)
        assert match, "ScreeningReviewHandler not found"
        body = match.group(0)
        # Sanity-bound the window so a future refactor cannot silently widen it.
        assert len(body.splitlines()) < 900, (
            f"class window looks over-captured: {len(body.splitlines())} lines")
        return body

    def test_second_review_is_restricted_to_senior_roles(self):
        body = self._handler_source()
        assert 'if is_second_review and user.get("role") not in ("admin", "sco"):' in body

    def test_clearance_disposition_is_restricted(self):
        body = self._handler_source()
        assert (
            'if canonical_disposition == "false_positive_cleared" '
            'and user.get("role") not in ("admin", "sco", "co"):'
        ) in body

    def test_both_role_checks_precede_the_commit(self):
        """A role check after the write would authorize nothing. The write
        itself is upsert_screening_review(); the commit is the durable point
        and is what this measures."""
        body = self._handler_source()
        second_review = body.index('if is_second_review and user.get("role")')
        clearance = body.index('if canonical_disposition == "false_positive_cleared"')
        commit = body.index("db.commit()")
        assert second_review < commit, "second-review role check runs after the commit"
        assert clearance < commit, "clearance role check runs after the commit"
