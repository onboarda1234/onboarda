"""Browser regression coverage for canonical KYC party/document persistence.

The production portal HTML runs in Chromium while an in-memory API is supplied
through Playwright routing. No staging credentials or external network are used.
"""

import json
import re
from copy import deepcopy
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import pytest


playwright_api = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright_api.sync_playwright
PlaywrightError = playwright_api.Error
expect = playwright_api.expect


ROOT = Path(__file__).resolve().parents[2]
PORTAL_HTML = (ROOT / "arie-portal.html").read_text(encoding="utf-8")
BACKOFFICE_HTML = (ROOT / "arie-backoffice.html").read_text(encoding="utf-8")
APP_ID = "app-p0-browser"
APP_REF = "ARF-2026-P0-BROWSER"
CLIENT_ID = "client-p0-browser"
DIRECTOR_ID = "dir-server-001"
UBO_ID = "ubo-server-001"
SYNTHETIC_MARKER = (
    "SYNTHETIC E2E TEST DOCUMENT — NOT A REAL IDENTITY OR CORPORATE RECORD"
)


def _verified_document(doc_id, person_id, person_type, doc_type, doc_name, *, slot_key=None):
    return {
        "id": doc_id,
        "doc_id": doc_id,
        "application_id": APP_ID,
        "person_id": person_id,
        "person_type": person_type,
        "doc_type": doc_type,
        "doc_name": doc_name,
        "slot_key": slot_key or f"person:{person_type}:{person_id}:{doc_type}",
        "is_current": True,
        "version": 1,
        "verification_status": "verified",
        "verification_state": "verified",
        "verification_status_label": "Verified",
        "verification_status_tone": "success",
        "verification_success": True,
        "verification_terminal": True,
        "verification_results": {
            "overall": "verified",
            "checks": [
                {
                    "label": "Synthetic browser fixture",
                    "result": "pass",
                    "message": "Synthetic marker confirmed",
                }
            ],
        },
        "uploaded_at": "2026-07-24T12:00:00Z",
    }


def _verified_entity_document(doc_id, doc_type, doc_name):
    document = _verified_document(
        doc_id,
        None,
        None,
        doc_type,
        doc_name,
        slot_key=f"entity:{doc_type}",
    )
    document["person_id"] = None
    document["person_type"] = None
    return document


class PortalApiHarness:
    def __init__(
        self,
        *,
        status="pricing_review",
        documents=None,
        rmi_requests=None,
        enhanced_requirements=None,
        stale_prescreening_parties=None,
    ):
        self.status = status
        self.documents = list(documents or [])
        self.rmi_requests = list(rmi_requests or [])
        self.enhanced_requirements = list(enhanced_requirements or [])
        self.stale_prescreening_parties = deepcopy(
            stale_prescreening_parties or {}
        )
        self.profile_urls = {
            DIRECTOR_ID: "",
            UBO_ID: "",
        }
        self.calls = []
        self.profile_payloads = []
        self.applicant_payloads = []
        self.uploads = []
        self.accept_count = 0
        self.logout_count = 0
        self.submit_count = 0
        self.application_read_count = 0
        self.reverse_party_order_on_read = False
        self.saved_session = None
        self.save_resume_writes = 0
        self.officer_login_count = 0
        self.backoffice_detail_reads = 0
        self.parties = {
            "directors": [
                {
                    "id": DIRECTOR_ID,
                    "person_key": "dir1",
                    "first_name": "Amina",
                    "last_name": "Director",
                    "full_name": "Amina Director",
                    "nationality": "Mauritius",
                    "date_of_birth": "1985-02-03",
                    "country_of_residence": "Mauritius",
                    "residential_address": "Synthetic Director Address",
                    "date_of_appointment": "2024-01-01",
                    "is_pep": "No",
                    "professional_profile_url": "",
                }
            ],
            "ubos": [
                {
                    "id": UBO_ID,
                    "person_key": "ubo1",
                    "first_name": "Basil",
                    "last_name": "Owner",
                    "full_name": "Basil Owner",
                    "nationality": "Mauritius",
                    "date_of_birth": "1982-04-05",
                    "country_of_residence": "Mauritius",
                    "residential_address": "Synthetic Owner Address",
                    "ownership_pct": 40,
                    "is_pep": "No",
                    "professional_profile_url": "",
                }
            ],
            "intermediaries": [],
        }

    def application(self):
        parties = deepcopy(self.parties)
        for collection in ("directors", "ubos", "intermediaries"):
            for party in parties[collection]:
                party_id = party.get("id")
                if party_id in self.profile_urls:
                    party["professional_profile_url"] = self.profile_urls[party_id]
        if self.reverse_party_order_on_read and self.application_read_count % 2 == 0:
            parties["directors"].reverse()
            parties["ubos"].reverse()
            parties["intermediaries"].reverse()
        prescreening_data = {
            "registered_entity_name": "Synthetic P0 Browser Ltd",
            "country_of_incorporation": "Mauritius",
            "pricing": {
                "currency": "USD",
                "onboarding_fee": 500,
                "annual_monitoring_fee": 250,
            },
        }
        prescreening_data.update(deepcopy(self.stale_prescreening_parties))
        return {
            "id": APP_ID,
            "ref": APP_REF,
            "client_id": CLIENT_ID,
            "company_name": "Synthetic P0 Browser Ltd",
            "country": "Mauritius",
            "status": self.status,
            "final_risk_level": "LOW",
            "risk_level": "LOW",
            "risk_score": 1.2,
            "risk_dimensions": {"d1": 1, "d2": 1, "d3": 1, "d4": 1, "d5": 1},
            "prescreening_data": prescreening_data,
            "documents": deepcopy(self.documents),
            "rmi_requests": deepcopy(self.rmi_requests),
            **parties,
        }

    def summary(self):
        return {
            "id": APP_ID,
            "ref": APP_REF,
            "company_name": "Synthetic P0 Browser Ltd",
            "status": self.status,
            "updated_at": "2026-07-24T12:00:00Z",
        }

    def backoffice_application(self):
        detail = deepcopy(self.application())
        detail.update(
            {
                "assigned_to": "synthetic-officer",
                "assigned_name": "Synthetic Officer",
                "submitted_at": "2026-07-24T12:00:00Z",
                "document_history": [],
                "audit_history": [],
            }
        )
        return detail

    @staticmethod
    def _fulfill_json(route, payload, status=200):
        route.fulfill(
            status=status,
            content_type="application/json",
            body=json.dumps(payload),
        )

    def route(self, route):
        request = route.request
        parsed = urlparse(request.url)
        method = request.method.upper()
        path = parsed.path
        query = parse_qs(parsed.query)
        self.calls.append({"method": method, "path": path, "query": query})

        if path == "/portal":
            route.fulfill(status=200, content_type="text/html", body=PORTAL_HTML)
            return
        if path == "/backoffice":
            route.fulfill(status=200, content_type="text/html", body=BACKOFFICE_HTML)
            return
        if path == "/favicon.ico":
            route.fulfill(status=204, body="")
            return
        if path == "/api/auth/client/login" and method == "POST":
            self._fulfill_json(
                route,
                {
                    "token": "synthetic-browser-token",
                    "client": {
                        "id": CLIENT_ID,
                        "sub": CLIENT_ID,
                        "email": "synthetic@example.test",
                        "company_name": "Synthetic P0 Browser",
                    },
                },
            )
            return
        if path == "/api/auth/officer/login" and method == "POST":
            self.officer_login_count += 1
            self._fulfill_json(
                route,
                {
                    "token": "synthetic-officer-token",
                    "user": {
                        "id": "synthetic-officer",
                        "name": "Synthetic Officer",
                        "email": "officer@example.test",
                        "role": "admin",
                    },
                },
            )
            return
        if path == "/api/auth/logout" and method == "POST":
            self.logout_count += 1
            self._fulfill_json(route, {"success": True})
            return
        if path == "/api/portal/applications" and method == "GET":
            self._fulfill_json(route, {"applications": [self.summary()]})
            return
        if path == "/api/save-resume/active" and method == "GET":
            drafts = [self.summary()] if self.saved_session else []
            self._fulfill_json(route, {"drafts": drafts})
            return
        if path == "/api/save-resume" and method == "POST":
            payload = json.loads(request.post_data or "{}")
            self.saved_session = {
                "application_id": payload.get("application_id") or APP_ID,
                "application_ref": APP_REF,
                "form_data": deepcopy(payload.get("form_data") or {}),
                "last_step": payload.get("last_step", 0),
                "last_saved_at": "2026-07-24T12:34:56",
            }
            self.save_resume_writes += 1
            self._fulfill_json(route, self.saved_session)
            return
        if path == "/api/save-resume" and method == "GET":
            if self.saved_session:
                self._fulfill_json(route, deepcopy(self.saved_session))
            else:
                self._fulfill_json(route, {"error": "No saved session"}, 404)
            return
        if path == "/api/applications" and method == "GET":
            self._fulfill_json(
                route,
                {"applications": [self.backoffice_application()]},
            )
            return
        if (
            path
            in {
                f"/api/applications/{APP_ID}",
                f"/api/applications/{APP_REF}",
            }
            and method == "GET"
        ):
            authorization = request.headers.get("authorization", "")
            if authorization == "Bearer synthetic-officer-token":
                self.backoffice_detail_reads += 1
                self._fulfill_json(route, self.backoffice_application())
            else:
                self.application_read_count += 1
                self._fulfill_json(route, self.application())
            return
        if path == f"/api/applications/{APP_ID}/accept-pricing" and method == "POST":
            self.accept_count += 1
            self.status = "kyc_documents"
            self._fulfill_json(route, {"status": self.status})
            return
        party_profile_match = re.fullmatch(
            rf"/api/applications/{APP_ID}/kyc/parties/([^/]+)/profile",
            path,
        )
        if party_profile_match and method == "PATCH":
            party_id = party_profile_match.group(1)
            party_type = next(
                (
                    person_type
                    for person_type, collection in (
                        ("director", self.parties["directors"]),
                        ("ubo", self.parties["ubos"]),
                        ("intermediary", self.parties["intermediaries"]),
                    )
                    if any(item["id"] == party_id for item in collection)
                ),
                "",
            )
            if not party_type:
                self._fulfill_json(route, {"error": "Unknown synthetic party"}, 404)
                return
            payload = json.loads(request.post_data or "{}")
            self.profile_payloads.append(payload)
            self.profile_urls[party_id] = payload.get(
                "professional_profile_url",
                "",
            )
            self._fulfill_json(
                route,
                {
                    "application_id": APP_ID,
                    "person_id": party_id,
                    "person_type": party_type,
                    "professional_profile_url": self.profile_urls[party_id],
                },
            )
            return
        if path == "/api/kyc/applicant" and method == "POST":
            payload = json.loads(request.post_data or "{}")
            self.applicant_payloads.append(payload)
            self._fulfill_json(
                route,
                {
                    "applicant_id": "synthetic-sumsub-applicant",
                    "api_status": "success",
                },
            )
            return
        if path == f"/api/applications/{APP_ID}/documents" and method == "POST":
            body = request.post_data_buffer or b""
            filename_match = re.search(br'filename="([^"]+)"', body)
            filename = (
                filename_match.group(1).decode("utf-8", errors="replace")
                if filename_match
                else "synthetic-upload.txt"
            )
            person_id = query.get("person_id", [""])[0]
            person_type = query.get("person_type", [""])[0]
            doc_type = query.get("doc_type", [""])[0]
            document = _verified_document(
                f"doc-browser-{len(self.documents) + 1}",
                person_id,
                person_type,
                doc_type,
                filename,
            )
            self.documents.append(document)
            self.uploads.append(
                {
                    "person_id": person_id,
                    "person_type": person_type,
                    "doc_type": doc_type,
                    "filename": filename,
                    "body": body,
                }
            )
            self._fulfill_json(route, document)
            return
        if path == f"/api/applications/{APP_ID}/submit-kyc" and method == "POST":
            self.submit_count += 1
            self.status = "kyc_submitted"
            self._fulfill_json(route, {"status": self.status})
            return
        if (
            path
            == f"/api/portal/applications/{APP_ID}/enhanced-requirements"
            and method == "GET"
        ):
            self._fulfill_json(
                route,
                {"requirements": deepcopy(self.enhanced_requirements)},
            )
            return

        self._fulfill_json(
            route,
            {"error": f"Unhandled synthetic route: {method} {path}"},
            status=404,
        )


@pytest.fixture(scope="module")
def chromium_browser():
    with sync_playwright() as playwright:
        try:
            browser = playwright.chromium.launch(headless=True)
        except PlaywrightError as exc:
            if "Executable doesn't exist" in str(exc):
                pytest.skip(f"Playwright Chromium is not installed: {exc}")
            raise
        try:
            yield browser
        finally:
            browser.close()


def _new_page(browser, harness):
    context = browser.new_context()
    context.route("**/*", harness.route)
    page = context.new_page()
    page.goto("http://regmind.test/portal", wait_until="domcontentloaded")
    return context, page


def _login(page):
    page.locator('button[onclick="showView(\'login\')"]').first.click()
    expect(page.locator("#view-login")).not_to_have_class(re.compile(r"\bhidden\b"))
    page.locator("#l-email").fill("synthetic@example.test")
    page.locator("#l-password").fill("Synthetic-Only-Password-123!")
    page.locator("#login-form").evaluate("(form) => form.requestSubmit()")
    expect(page.locator("#view-my-apps")).not_to_have_class(re.compile(r"\bhidden\b"))
    expect(page.get_by_text(APP_REF, exact=True)).to_be_visible()


def _open_application(page):
    page.get_by_text(APP_REF, exact=True).click()


def _resume_application(page, status):
    page.evaluate(
        "([ref, stage]) => resumeApplication(ref, stage)",
        [APP_REF, status],
    )


def test_a_profile_persistence_across_ui_logout_and_clean_context(
    chromium_browser, tmp_path
):
    harness = PortalApiHarness(status="pricing_review")
    director_file = tmp_path / "synthetic-director-passport.pdf"
    ubo_file = tmp_path / "synthetic-ubo-address.pdf"
    director_file.write_text(
        f"{SYNTHETIC_MARKER}\nOWNER={DIRECTOR_ID}\nTYPE=passport\n",
        encoding="utf-8",
    )
    ubo_file.write_text(
        f"{SYNTHETIC_MARKER}\nOWNER={UBO_ID}\nTYPE=poa\n",
        encoding="utf-8",
    )

    first_context, first_page = _new_page(chromium_browser, harness)
    try:
        _login(first_page)
        _open_application(first_page)
        expect(first_page.locator("#view-pricing")).not_to_have_class(re.compile(r"\bhidden\b"))

        # Pricing-review hydration already uses canonical IDs, before acceptance.
        expect(first_page.locator(f"#kyc-person-{DIRECTOR_ID}")).to_have_attribute(
            "data-party-id", DIRECTOR_ID
        )
        expect(first_page.locator(f"#kyc-person-{UBO_ID}")).to_have_attribute(
            "data-party-id", UBO_ID
        )
        first_page.locator("#pricing-tc-accept").check()
        expect(first_page.locator("#btn-accept-pricing")).to_be_enabled()
        first_page.locator("#btn-accept-pricing").click()
        expect(first_page.locator("#view-onboarding")).not_to_have_class(
            re.compile(r"\bhidden\b")
        )
        assert harness.accept_count == 1

        profile_url = "https://profiles.example.test/amina-director"
        profile_input = first_page.locator(f"#kyc-linkedin-{DIRECTOR_ID}")
        profile_input.fill(profile_url)
        profile_input.dispatch_event("change")
        expect(profile_input).to_have_attribute("data-last-saved-value", profile_url)
        ubo_profile_url = "https://profiles.example.test/basil-owner"
        ubo_profile_input = first_page.locator(f"#kyc-linkedin-{UBO_ID}")
        ubo_profile_input.fill(ubo_profile_url)
        ubo_profile_input.dispatch_event("change")
        expect(ubo_profile_input).to_have_attribute(
            "data-last-saved-value",
            ubo_profile_url,
        )
        assert harness.profile_payloads == [
            {
                "person_type": "director",
                "professional_profile_url": profile_url,
            },
            {
                "person_type": "ubo",
                "professional_profile_url": ubo_profile_url,
            },
        ]

        first_page.locator(
            f"#kyc-person-{DIRECTOR_ID} .kyc-tab"
        ).nth(1).click()
        first_page.locator(f"#kyc-email-{DIRECTOR_ID}").fill(
            "amina.director@example.test"
        )
        first_page.locator(
            f"#kyc-person-{DIRECTOR_ID} .kyc-send-btn"
        ).click()
        expect(first_page.locator(f"#kyc-sent-{DIRECTOR_ID}")).to_be_visible()
        assert len(harness.applicant_payloads) == 1
        applicant_payload = harness.applicant_payloads[0]
        assert applicant_payload["external_user_id"] == DIRECTOR_ID
        assert applicant_payload["person_type"] == "director"
        assert applicant_payload["application_id"] == APP_ID
        assert applicant_payload["first_name"] == "Amina"
        assert applicant_payload["last_name"] == "Director"
        assert applicant_payload["email"] == "amina.director@example.test"

        first_page.evaluate(
            """
            (inputIds) => {
              window.__p0SelectedSyntheticFiles = {};
              inputIds.forEach((inputId) => {
                document.getElementById(inputId).addEventListener('change', (event) => {
                  const file = event.target.files[0];
                  if (!file) return;
                  file.text().then((text) => {
                    window.__p0SelectedSyntheticFiles[inputId] = {
                      name: file.name,
                      size: file.size,
                      text
                    };
                  });
                }, { capture: true, once: true });
              });
            }
            """,
            [
                f"kyc-passport-{DIRECTOR_ID}",
                f"kyc-poa-{UBO_ID}",
            ],
        )
        first_page.locator(f"#kyc-passport-{DIRECTOR_ID}").set_input_files(
            str(director_file)
        )
        expect(first_page.locator(f"#kyc-passport-lbl-{DIRECTOR_ID}")).to_have_text(
            director_file.name
        )
        first_page.locator(f"#kyc-poa-{UBO_ID}").set_input_files(str(ubo_file))
        expect(first_page.locator(f"#kyc-poa-lbl-{UBO_ID}")).to_have_text(ubo_file.name)

        assert [(item["person_id"], item["person_type"], item["doc_type"]) for item in harness.uploads] == [
            (DIRECTOR_ID, "director", "passport"),
            (UBO_ID, "ubo", "poa"),
        ]
        first_page.wait_for_function(
            """
            (marker) => {
              const files = window.__p0SelectedSyntheticFiles || {};
              return Object.keys(files).length === 2 &&
                Object.values(files).every((file) => file.size > 0 && file.text.includes(marker));
            }
            """,
            arg=SYNTHETIC_MARKER,
        )
        selected_files = first_page.evaluate("window.__p0SelectedSyntheticFiles")
        assert selected_files[f"kyc-passport-{DIRECTOR_ID}"]["name"] == director_file.name
        assert selected_files[f"kyc-poa-{UBO_ID}"]["name"] == ubo_file.name
        assert harness.parties["directors"][0]["id"] == DIRECTOR_ID
        assert harness.parties["ubos"][0]["id"] == UBO_ID

        first_page.evaluate(
            """
            localStorage.setItem('portal_token', 'must-be-cleared');
            localStorage.setItem('arie_draft_p0', 'must-be-cleared');
            sessionStorage.setItem('auth_token', 'must-be-cleared');
            sessionStorage.setItem('arie_draft_p0', 'must-be-cleared');
            """
        )
        first_page.get_by_role("button", name="Sign Out").click()
        expect(first_page.locator("#view-landing")).not_to_have_class(
            re.compile(r"\bhidden\b")
        )
        expect(first_page.locator("#global-client-sidebar")).not_to_have_class(
            re.compile(r"\bopen\b")
        )
        auth_state = first_page.evaluate(
            """
            ({
              token: AUTH_TOKEN,
              user: AUTH_USER,
              applicationId: currentApplicationId,
              localToken: localStorage.getItem('portal_token'),
              localDraft: localStorage.getItem('arie_draft_p0'),
              sessionToken: sessionStorage.getItem('auth_token'),
              sessionDraft: sessionStorage.getItem('arie_draft_p0')
            })
            """
        )
        assert auth_state == {
            "token": "",
            "user": None,
            "applicationId": "",
            "localToken": None,
            "localDraft": None,
            "sessionToken": None,
            "sessionDraft": None,
        }
        assert harness.logout_count == 1
    finally:
        first_context.close()

    # A brand-new context proves rehydration does not depend on cookies or storage.
    second_context, second_page = _new_page(chromium_browser, harness)
    try:
        assert second_context.storage_state()["origins"] == []
        _login(second_page)
        second_page.evaluate(
            """
            window.__p0HydrationEvents = [];
            const originalCardSync = window.syncDirectorsUBOsToKYC;
            const originalDocumentSync = window.syncPersistedApplicationDocuments;
            window.syncDirectorsUBOsToKYC = function() {
              window.__p0HydrationEvents.push('cards');
              return originalCardSync.apply(this, arguments);
            };
            window.syncPersistedApplicationDocuments = function() {
              window.__p0HydrationEvents.push('documents');
              return originalDocumentSync.apply(this, arguments);
            };
            """
        )
        _open_application(second_page)
        expect(second_page.locator("#view-onboarding")).not_to_have_class(
            re.compile(r"\bhidden\b")
        )

        events = second_page.evaluate("window.__p0HydrationEvents")
        assert events[-1] == "documents"
        assert events.count("cards") >= 1
        last_document_index = max(
            i for i, event in enumerate(events) if event == "documents"
        )
        assert max(i for i, event in enumerate(events) if event == "cards") < (
            last_document_index
        )

        director_card = second_page.locator(f"#kyc-person-{DIRECTOR_ID}")
        ubo_card = second_page.locator(f"#kyc-person-{UBO_ID}")
        expect(director_card).to_have_attribute("data-party-id", DIRECTOR_ID)
        expect(director_card).to_have_attribute("data-person-key", "dir1")
        expect(director_card).to_have_attribute("data-person-type", "director")
        expect(ubo_card).to_have_attribute("data-party-id", UBO_ID)
        expect(ubo_card).to_have_attribute("data-person-key", "ubo1")
        expect(ubo_card).to_have_attribute("data-person-type", "ubo")

        expect(second_page.locator(f"#kyc-passport-lbl-{DIRECTOR_ID}")).to_have_text(
            director_file.name
        )
        expect(second_page.locator(f"#kyc-poa-lbl-{DIRECTOR_ID}")).to_have_text(
            "Click to upload"
        )
        expect(second_page.locator(f"#kyc-poa-lbl-{UBO_ID}")).to_have_text(ubo_file.name)
        expect(second_page.locator(f"#kyc-passport-lbl-{UBO_ID}")).to_have_text(
            "Click to upload"
        )
        expect(second_page.locator(f"#kyc-linkedin-{DIRECTOR_ID}")).to_have_value(
            profile_url
        )
        expect(second_page.locator(f"#kyc-linkedin-{UBO_ID}")).to_have_value(
            ubo_profile_url
        )
        expect(second_page.locator("#portal-document-hydration-errors")).to_be_hidden()

        # Repeating the same resume is idempotent: cards and verification
        # results are rebuilt once rather than duplicated.
        second_page.evaluate(
            f"() => resumeApplication('{APP_REF}', '{harness.status}')"
        )
        expect(second_page.locator("#view-onboarding")).not_to_have_class(
            re.compile(r"\bhidden\b")
        )
        assert (
            second_page.locator(
                f'.kyc-person-card[data-party-id="{DIRECTOR_ID}"]'
            ).count()
            == 1
        )
        assert (
            second_page.locator(
                f'.kyc-person-card[data-party-id="{UBO_ID}"]'
            ).count()
            == 1
        )
        assert (
            second_page.locator(
                '.doc-verify-results[data-doc-id="doc-browser-1"]'
            ).count()
            == 1
        )
        assert (
            second_page.locator(
                '.doc-verify-results[data-doc-id="doc-browser-2"]'
            ).count()
            == 1
        )
        expect(second_page.locator(f"#kyc-passport-lbl-{DIRECTOR_ID}")).to_have_text(
            director_file.name
        )
        expect(second_page.locator(f"#kyc-poa-lbl-{UBO_ID}")).to_have_text(
            ubo_file.name
        )
    finally:
        second_context.close()


def test_b_document_persistence_keeps_distinct_files_under_intended_owners(
    chromium_browser,
    tmp_path,
):
    harness = PortalApiHarness(status="kyc_documents")
    director_file = tmp_path / "test-b-director-passport.pdf"
    ubo_file = tmp_path / "test-b-ubo-address.pdf"
    director_file.write_text(
        f"{SYNTHETIC_MARKER}\nOWNER={DIRECTOR_ID}\n",
        encoding="utf-8",
    )
    ubo_file.write_text(
        f"{SYNTHETIC_MARKER}\nOWNER={UBO_ID}\n",
        encoding="utf-8",
    )
    assert SYNTHETIC_MARKER in director_file.read_text(encoding="utf-8")
    assert SYNTHETIC_MARKER in ubo_file.read_text(encoding="utf-8")

    first_context, first_page = _new_page(chromium_browser, harness)
    try:
        _login(first_page)
        _open_application(first_page)
        first_page.locator(f"#kyc-passport-{DIRECTOR_ID}").set_input_files(
            str(director_file)
        )
        first_page.locator(f"#kyc-poa-{UBO_ID}").set_input_files(str(ubo_file))
        expect(first_page.locator(f"#kyc-passport-lbl-{DIRECTOR_ID}")).to_have_text(
            director_file.name
        )
        expect(first_page.locator(f"#kyc-poa-lbl-{UBO_ID}")).to_have_text(
            ubo_file.name
        )
        first_page.get_by_role("button", name="Sign Out").click()
        expect(first_page.locator("#view-landing")).not_to_have_class(
            re.compile(r"\bhidden\b")
        )
    finally:
        first_context.close()

    second_context, second_page = _new_page(chromium_browser, harness)
    try:
        _login(second_page)
        _open_application(second_page)
        expect(second_page.locator(f"#kyc-passport-lbl-{DIRECTOR_ID}")).to_have_text(
            director_file.name
        )
        expect(second_page.locator(f"#kyc-poa-lbl-{UBO_ID}")).to_have_text(
            ubo_file.name
        )
        expect(second_page.locator(f"#kyc-poa-lbl-{DIRECTOR_ID}")).to_have_text(
            "Click to upload"
        )
        expect(second_page.locator(f"#kyc-passport-lbl-{UBO_ID}")).to_have_text(
            "Click to upload"
        )
        assert [
            (doc["person_id"], doc["person_type"], doc["doc_type"])
            for doc in harness.documents
        ] == [
            (DIRECTOR_ID, "director", "passport"),
            (UBO_ID, "ubo", "poa"),
        ]
    finally:
        second_context.close()


def test_c_party_order_stability_cannot_displace_documents_on_repeated_reload(
    chromium_browser,
):
    second_director_id = "dir-server-002"
    documents = [
        _verified_document(
            "doc-order-first",
            DIRECTOR_ID,
            "director",
            "passport",
            "order-first-passport.pdf",
        ),
        _verified_document(
            "doc-order-second",
            second_director_id,
            "director",
            "passport",
            "order-second-passport.pdf",
        ),
    ]
    harness = PortalApiHarness(status="kyc_documents", documents=documents)
    second_director = deepcopy(harness.parties["directors"][0])
    second_director.update(
        {
            "id": second_director_id,
            "person_key": "dir2",
            "first_name": "Cora",
            "last_name": "Second",
            "full_name": "Cora Second",
        }
    )
    harness.parties["directors"].append(second_director)
    harness.profile_urls[second_director_id] = ""
    harness.reverse_party_order_on_read = True

    context, page = _new_page(chromium_browser, harness)
    try:
        _login(page)
        _open_application(page)
        observed_orders = set()
        for _ in range(4):
            expect(page.locator(f"#kyc-passport-lbl-{DIRECTOR_ID}")).to_have_text(
                "order-first-passport.pdf"
            )
            expect(
                page.locator(f"#kyc-passport-lbl-{second_director_id}")
            ).to_have_text("order-second-passport.pdf")
            observed_orders.add(
                tuple(
                    page.locator(
                        '#kyc-persons-list .kyc-person-card[data-person-type="director"]'
                    ).evaluate_all(
                        "(cards) => cards.map((card) => card.dataset.partyId)"
                    )
                )
            )
            _resume_application(page, harness.status)
        assert len(observed_orders) == 2
        assert (
            page.locator('.doc-verify-results[data-doc-id="doc-order-first"]').count()
            == 1
        )
        assert (
            page.locator('.doc-verify-results[data-doc-id="doc-order-second"]').count()
            == 1
        )
    finally:
        context.close()


def test_d_pricing_transition_changes_only_workflow_status(chromium_browser):
    documents = [
        _verified_document(
            "doc-pricing-director",
            DIRECTOR_ID,
            "director",
            "passport",
            "pricing-director-passport.pdf",
        ),
        _verified_document(
            "doc-pricing-ubo",
            UBO_ID,
            "ubo",
            "poa",
            "pricing-ubo-address.pdf",
        ),
    ]
    harness = PortalApiHarness(status="pricing_review", documents=documents)
    harness.profile_urls[DIRECTOR_ID] = "https://profiles.example.test/pricing-director"
    harness.profile_urls[UBO_ID] = "https://profiles.example.test/pricing-ubo"
    parties_before = deepcopy(harness.parties)
    profiles_before = deepcopy(harness.profile_urls)
    documents_before = deepcopy(harness.documents)

    context, page = _new_page(chromium_browser, harness)
    try:
        _login(page)
        _open_application(page)
        page.locator("#pricing-tc-accept").check()
        page.locator("#btn-accept-pricing").click()
        expect(page.locator("#view-onboarding")).not_to_have_class(
            re.compile(r"\bhidden\b")
        )
        expect(page.locator(f"#kyc-passport-lbl-{DIRECTOR_ID}")).to_have_text(
            "pricing-director-passport.pdf"
        )
        expect(page.locator(f"#kyc-poa-lbl-{UBO_ID}")).to_have_text(
            "pricing-ubo-address.pdf"
        )
        expect(page.locator(f"#kyc-linkedin-{DIRECTOR_ID}")).to_have_value(
            profiles_before[DIRECTOR_ID]
        )
        expect(page.locator(f"#kyc-linkedin-{UBO_ID}")).to_have_value(
            profiles_before[UBO_ID]
        )
        assert harness.status == "kyc_documents"
        assert harness.accept_count == 1
        assert harness.parties == parties_before
        assert harness.profile_urls == profiles_before
        assert harness.documents == documents_before
    finally:
        context.close()


def test_e_partial_save_logout_resume_complete_without_duplicates(
    chromium_browser,
    tmp_path,
):
    entity_types = [
        "cert_inc",
        "memarts",
        "reg_sh",
        "reg_dir",
        "fin_stmt",
        "poa",
        "board_res",
        "structure_chart",
    ]
    documents = [
        _verified_entity_document(
            f"doc-entity-{doc_type}",
            doc_type,
            f"entity-{doc_type}.pdf",
        )
        for doc_type in entity_types
    ]
    documents.extend(
        [
            _verified_document(
                "doc-e-dir-passport",
                DIRECTOR_ID,
                "director",
                "passport",
                "e-director-passport.pdf",
            ),
            _verified_document(
                "doc-e-dir-poa",
                DIRECTOR_ID,
                "director",
                "poa",
                "e-director-address.pdf",
            ),
            _verified_document(
                "doc-e-ubo-passport",
                UBO_ID,
                "ubo",
                "passport",
                "e-ubo-passport.pdf",
            ),
        ]
    )
    harness = PortalApiHarness(status="draft")
    final_file = tmp_path / "test-e-final-ubo-address.pdf"
    final_file.write_text(
        f"{SYNTHETIC_MARKER}\nOWNER={UBO_ID}\nFINAL_PARTIAL_STEP=1\n",
        encoding="utf-8",
    )

    first_context, first_page = _new_page(chromium_browser, harness)
    try:
        _login(first_page)
        _open_application(first_page)
        expect(first_page.locator("#view-prescreening")).not_to_have_class(
            re.compile(r"\bhidden\b")
        )
        partial_overview = (
            "Synthetic partial save/resume case — no real customer information"
        )
        first_page.locator("#f-biz-overview").fill(partial_overview)
        first_page.get_by_role("button", name=re.compile("Save Draft")).click()
        expect(first_page.locator("#save-status")).to_contain_text("Saved")
        assert harness.save_resume_writes == 1
        assert (
            harness.saved_session["form_data"]["prescreening"]["f-biz-overview"]
            == partial_overview
        )
        first_page.get_by_role("button", name="Sign Out").click()
        expect(first_page.locator("#view-landing")).not_to_have_class(
            re.compile(r"\bhidden\b")
        )
    finally:
        first_context.close()

    second_context, second_page = _new_page(chromium_browser, harness)
    try:
        _login(second_page)
        _open_application(second_page)
        expect(second_page.locator("#view-prescreening")).not_to_have_class(
            re.compile(r"\bhidden\b")
        )
        expect(second_page.locator("#f-biz-overview")).to_have_value(
            partial_overview
        )

        # The same backend state now advances to KYC. Completing the remaining
        # document proves the restored draft and post-pricing records do not
        # duplicate parties or evidence.
        harness.status = "kyc_documents"
        harness.documents = deepcopy(documents)
        harness.profile_urls[DIRECTOR_ID] = (
            "https://profiles.example.test/e-director"
        )
        harness.profile_urls[UBO_ID] = "https://profiles.example.test/e-ubo"
        _resume_application(second_page, harness.status)
        expect(second_page.locator("#view-onboarding")).not_to_have_class(
            re.compile(r"\bhidden\b")
        )
        expect(second_page.locator(f"#kyc-passport-lbl-{UBO_ID}")).to_have_text(
            "e-ubo-passport.pdf"
        )
        expect(second_page.locator(f"#kyc-poa-lbl-{UBO_ID}")).to_have_text(
            "Click to upload"
        )
        second_page.locator(f"#kyc-poa-{UBO_ID}").set_input_files(str(final_file))
        expect(second_page.locator(f"#kyc-poa-lbl-{UBO_ID}")).to_have_text(
            final_file.name
        )
        _resume_application(second_page, harness.status)
        assert len(harness.documents) == len(documents) + 1
        assert len({doc["id"] for doc in harness.documents}) == len(harness.documents)
        assert (
            second_page.locator(
                f'.kyc-person-card[data-party-id="{DIRECTOR_ID}"]'
            ).count()
            == 1
        )
        assert (
            second_page.locator(
                f'.kyc-person-card[data-party-id="{UBO_ID}"]'
            ).count()
            == 1
        )
        assert (
            second_page.locator(
                f'.doc-verify-results[data-doc-id="{harness.documents[-1]["id"]}"]'
            ).count()
            == 1
        )
        second_page.locator("#kyc-final-declaration").check()
        second_page.locator("#btn-submit-docs").click()
        expect(second_page.locator("#view-submission-review")).not_to_have_class(
            re.compile(r"\bhidden\b")
        )
        expect(second_page.locator("#btn-final-submit")).to_be_enabled()
        second_page.locator("#btn-final-submit").click()
        expect(second_page.locator("#view-docs-review")).not_to_have_class(
            re.compile(r"\bhidden\b")
        )
        assert harness.submit_count == 1
        assert harness.status == "kyc_submitted"
    finally:
        second_context.close()


def test_f_cross_surface_portal_and_backoffice_values_match_exactly(
    chromium_browser,
):
    documents = [
        _verified_document(
            "doc-f-director",
            DIRECTOR_ID,
            "director",
            "passport",
            "f-director-passport.pdf",
        ),
        _verified_document(
            "doc-f-ubo",
            UBO_ID,
            "ubo",
            "poa",
            "f-ubo-address.pdf",
        ),
    ]
    harness = PortalApiHarness(status="kyc_documents", documents=documents)
    harness.profile_urls[DIRECTOR_ID] = "https://profiles.example.test/f-director"
    harness.profile_urls[UBO_ID] = "https://profiles.example.test/f-ubo"

    portal_context, portal_page = _new_page(chromium_browser, harness)
    try:
        _login(portal_page)
        _open_application(portal_page)
        portal_values = {
            "director_name": portal_page.locator(
                f"#kyc-name-{DIRECTOR_ID}"
            ).text_content(),
            "ubo_name": portal_page.locator(f"#kyc-name-{UBO_ID}").text_content(),
            "director_profile": portal_page.locator(
                f"#kyc-linkedin-{DIRECTOR_ID}"
            ).input_value(),
            "ubo_profile": portal_page.locator(
                f"#kyc-linkedin-{UBO_ID}"
            ).input_value(),
            "director_document": portal_page.locator(
                f"#kyc-passport-lbl-{DIRECTOR_ID}"
            ).text_content(),
            "ubo_document": portal_page.locator(
                f"#kyc-poa-lbl-{UBO_ID}"
            ).text_content(),
        }
    finally:
        portal_context.close()

    backoffice_context = chromium_browser.new_context()
    backoffice_context.route("**/*", harness.route)
    backoffice_page = backoffice_context.new_page()
    try:
        backoffice_page.goto(
            "http://regmind.test/backoffice",
            wait_until="domcontentloaded",
        )
        backoffice_page.locator("#login-email").fill("officer@example.test")
        backoffice_page.locator("#login-password").fill(
            "Synthetic-Only-Officer-Password-123!"
        )
        backoffice_page.locator("#login-form").evaluate(
            "(form) => form.requestSubmit()"
        )
        expect(backoffice_page.locator("#login-overlay")).to_be_hidden()
        backoffice_values = backoffice_page.evaluate(
            """
            async (applicationRef) => {
              const mapped = await fetchApplicationDetail(applicationRef);
              currentApp = mapped;
              APPLICATION_ENHANCED_REQUIREMENTS = [];
              const host = document.createElement('section');
              host.id = 'p0-backoffice-cross-surface';
              host.innerHTML =
                renderPartyCard(mapped.directors[0], 'director', mapped) +
                renderPartyCard(mapped.ubos[0], 'ubo', mapped) +
                renderStandardKycDocumentTaxonomy(mapped, { includeIdv: false });
              document.body.appendChild(host);
              const orphanOwnerDoc = Object.assign({}, mapped._documents[0], {
                id: 'doc-f-orphan-owner',
                person_id: 'missing-director',
                slot_key: 'person:director:missing-director:passport'
              });
              const orphanOwnerApp = Object.assign({}, mapped, {
                _documents: [orphanOwnerDoc]
              });
              const orphanOwnerSummary = computeDocumentReadinessSummary(orphanOwnerApp);
              const orphanSpecialDoc = Object.assign({}, mapped._documents[0], {
                id: 'doc-f-orphan-special',
                person_id: '',
                person_type: '',
                doc_type: 'supporting_document',
                slot_key: 'rmi:missing-item'
              });
              const orphanSpecialApp = Object.assign({}, mapped, {
                _documents: [orphanSpecialDoc],
                rmiRequests: []
              });
              return {
                directorName: mapped.directors[0].name,
                uboName: mapped.ubos[0].name,
                directorProfile: mapped.directors[0].professional_profile_url,
                uboProfile: mapped.ubos[0].professional_profile_url,
                directorOwner: findDocumentOwnerLabelForApp(mapped, mapped._documents[0]),
                uboOwner: findDocumentOwnerLabelForApp(mapped, mapped._documents[1]),
                orphanOwnerBlocked:
                  orphanOwnerSummary.isIncomplete &&
                  orphanOwnerSummary.issueCount >= 1 &&
                  renderStandardKycDocumentTaxonomy(orphanOwnerApp, { includeIdv: false })
                    .includes('backoffice-document-integrity-error'),
                orphanSpecialBlocked:
                  computeDocumentReadinessSummary(orphanSpecialApp).issueCount >= 1 &&
                  renderStandardKycDocumentTaxonomy(orphanSpecialApp, { includeIdv: false })
                    .includes('backoffice-document-integrity-error')
              };
            }
            """,
            APP_REF,
        )
        host = backoffice_page.locator("#p0-backoffice-cross-surface")
        expect(host).to_contain_text(portal_values["director_name"])
        expect(host).to_contain_text(portal_values["ubo_name"])
        expect(host).to_contain_text(portal_values["director_profile"])
        expect(host).to_contain_text(portal_values["ubo_profile"])
        expect(host).to_contain_text(portal_values["director_document"])
        expect(host).to_contain_text(portal_values["ubo_document"])
        assert backoffice_values == {
            "directorName": portal_values["director_name"],
            "uboName": portal_values["ubo_name"],
            "directorProfile": portal_values["director_profile"],
            "uboProfile": portal_values["ubo_profile"],
            "directorOwner": portal_values["director_name"],
            "uboOwner": portal_values["ubo_name"],
            "orphanOwnerBlocked": True,
            "orphanSpecialBlocked": True,
        }
        assert harness.officer_login_count == 1
        assert harness.backoffice_detail_reads >= 1
    finally:
        backoffice_context.close()


def test_pricing_binding_rejects_canonical_id_person_key_conflict(chromium_browser):
    harness = PortalApiHarness(status="pricing_review")
    context, page = _new_page(chromium_browser, harness)
    try:
        _login(page)
        _open_application(page)
        page.locator(
            f'#directors-body tr[data-party-id="{DIRECTOR_ID}"]'
        ).evaluate(
            "(row) => { row.dataset.personKey = 'dir-conflicting-alias'; }"
        )
        page.locator("#pricing-tc-accept").check()
        page.locator("#btn-accept-pricing").click()

        expect(page.locator("#pricing-party-integrity-error")).to_be_visible()
        expect(page.locator("#pricing-party-integrity-error")).to_contain_text(
            "ownership parties could not be linked"
        )
        expect(page.locator("#view-pricing")).not_to_have_class(re.compile(r"\bhidden\b"))
        assert harness.accept_count == 0
    finally:
        context.close()


def test_pricing_binding_rejects_authoritative_id_key_namespace_collision(
    chromium_browser,
):
    harness = PortalApiHarness(status="pricing_review")
    colliding_director = deepcopy(harness.parties["directors"][0])
    colliding_director.update(
        {
            "id": "dir-server-002",
            "person_key": DIRECTOR_ID,
            "first_name": "Cora",
            "last_name": "Collision",
            "full_name": "Cora Collision",
        }
    )
    harness.parties["directors"].append(colliding_director)
    context, page = _new_page(chromium_browser, harness)
    try:
        _login(page)
        _open_application(page)
        page.locator("#pricing-tc-accept").check()
        page.locator("#btn-accept-pricing").click()
        expect(page.locator("#pricing-party-integrity-error")).to_be_visible()
        expect(page.locator("#view-pricing")).not_to_have_class(
            re.compile(r"\bhidden\b")
        )
        assert harness.accept_count == 0
    finally:
        context.close()


def test_conflicting_persisted_slot_metadata_fails_visibly_closed(chromium_browser):
    conflicting = _verified_document(
        "doc-conflicting-slot",
        DIRECTOR_ID,
        "director",
        "passport",
        "must-not-render-under-director.txt",
        slot_key=f"person:ubo:{DIRECTOR_ID}:passport",
    )
    harness = PortalApiHarness(status="kyc_documents", documents=[conflicting])
    context, page = _new_page(chromium_browser, harness)
    try:
        _login(page)
        _open_application(page)
        expect(page.locator("#view-onboarding")).not_to_have_class(
            re.compile(r"\bhidden\b")
        )
        expect(page.locator("#portal-document-hydration-errors")).to_be_visible()
        expect(page.locator("#portal-document-hydration-errors")).to_contain_text(
            "could not be matched safely"
        )
        expect(page.locator(f"#kyc-passport-lbl-{DIRECTOR_ID}")).to_have_text(
            "Click to upload"
        )
        page.locator("#kyc-final-declaration").check()
        expect(page.locator("#btn-submit-docs")).to_be_disabled()
    finally:
        context.close()


def test_special_evidence_namespaces_are_not_reinterpreted_as_base_kyc_slots(
    chromium_browser,
):
    rmi_document = _verified_document(
        "doc-rmi-special",
        None,
        None,
        "supporting_document",
        "rmi-evidence.txt",
        slot_key="rmi:rmi-item-001",
    )
    enhanced_document = _verified_document(
        "doc-enhanced-special",
        None,
        None,
        "enhanced_requirement",
        "enhanced-evidence.txt",
        slot_key="enhanced_requirement:requirement-001",
    )
    rmi_requests = [
        {
            "id": "rmi-request-001",
            "application_id": APP_ID,
            "status": "open",
            "reason": "Synthetic linked evidence check",
            "deadline": "2026-08-01",
            "items": [
                {
                    "id": "rmi-item-001",
                    "document_id": rmi_document["id"],
                    "doc_type": rmi_document["doc_type"],
                    "label": "Synthetic linked RMI evidence",
                    "description": "Synthetic only",
                    "status": "uploaded",
                }
            ],
        }
    ]
    enhanced_requirements = [
        {
            "id": "requirement-001",
            "requirement_type": "document",
            "label": "Synthetic enhanced evidence",
            "status": "submitted",
            "status_label": "Submitted",
            "subject_scope": "application",
            "linked_document_id": enhanced_document["id"],
            "linked_document": deepcopy(enhanced_document),
        }
    ]
    harness = PortalApiHarness(
        status="kyc_documents",
        documents=[rmi_document, enhanced_document],
        rmi_requests=rmi_requests,
        enhanced_requirements=enhanced_requirements,
    )
    context, page = _new_page(chromium_browser, harness)
    try:
        _login(page)
        _open_application(page)
        expect(page.locator("#view-onboarding")).not_to_have_class(
            re.compile(r"\bhidden\b")
        )
        expect(page.locator("#portal-document-hydration-errors")).to_be_hidden()
        expect(page.locator("#rmi-requests-container")).to_contain_text(
            "Synthetic linked RMI evidence"
        )
        expect(
            page.locator(
                '[data-enhanced-requirement-id="requirement-001"]'
            ).first
        ).to_contain_text(enhanced_document["doc_name"])
        assert (
            page.locator(
                '.doc-verify-results[data-doc-id="doc-rmi-special"]'
            ).count()
            == 0
        )
        assert (
            page.locator(
                '.doc-verify-results[data-doc-id="doc-enhanced-special"]'
            ).count()
            == 0
        )
    finally:
        context.close()


def test_orphan_special_evidence_namespace_fails_visibly_closed(chromium_browser):
    orphan = _verified_document(
        "doc-orphan-rmi",
        None,
        None,
        "supporting_document",
        "orphan-rmi-evidence.txt",
        slot_key="rmi:missing-item",
    )
    harness = PortalApiHarness(status="kyc_documents", documents=[orphan])
    context, page = _new_page(chromium_browser, harness)
    try:
        _login(page)
        _open_application(page)
        expect(page.locator("#portal-document-hydration-errors")).to_be_visible()
        expect(page.locator("#portal-document-hydration-errors")).to_contain_text(
            "could not be matched safely"
        )
        assert (
            page.locator(
                '.doc-verify-results[data-doc-id="doc-orphan-rmi"]'
            ).count()
            == 0
        )
    finally:
        context.close()


def test_linked_special_evidence_with_unresolved_typed_owner_fails_closed(
    chromium_browser,
):
    document = _verified_document(
        "doc-special-missing-owner",
        "missing-director",
        "director",
        "supporting_document",
        "linked-but-owner-missing.pdf",
        slot_key="rmi:rmi-item-missing-owner",
    )
    rmi_requests = [
        {
            "id": "rmi-request-missing-owner",
            "application_id": APP_ID,
            "status": "open",
            "items": [
                {
                    "id": "rmi-item-missing-owner",
                    "document_id": document["id"],
                    "doc_type": document["doc_type"],
                    "label": "Linked evidence with missing owner",
                    "status": "uploaded",
                }
            ],
        }
    ]
    harness = PortalApiHarness(
        status="kyc_documents",
        documents=[document],
        rmi_requests=rmi_requests,
    )
    context, page = _new_page(chromium_browser, harness)
    try:
        _login(page)
        _open_application(page)
        expect(page.locator("#portal-document-hydration-errors")).to_be_visible()
        assert (
            page.locator(
                '.doc-verify-results[data-doc-id="doc-special-missing-owner"]'
            ).count()
            == 0
        )
    finally:
        context.close()


def test_canonical_id_legacy_alias_collision_fails_visibly_closed(chromium_browser):
    document = _verified_document(
        "doc-alias-collision",
        DIRECTOR_ID,
        "director",
        "passport",
        "must-not-render-through-ambiguous-alias.txt",
    )
    harness = PortalApiHarness(status="kyc_documents", documents=[document])
    colliding_director = deepcopy(harness.parties["directors"][0])
    colliding_director.update(
        {
            "id": "dir-server-002",
            "person_key": DIRECTOR_ID,
            "first_name": "Cora",
            "last_name": "Collision",
            "full_name": "Cora Collision",
        }
    )
    harness.parties["directors"].append(colliding_director)

    context, page = _new_page(chromium_browser, harness)
    try:
        _login(page)
        _open_application(page)
        expect(page.locator("#view-onboarding")).not_to_have_class(
            re.compile(r"\bhidden\b")
        )
        expect(page.locator("#portal-document-hydration-errors")).to_be_visible()
        expect(page.locator(f"#kyc-passport-lbl-{DIRECTOR_ID}")).to_have_text(
            "Click to upload"
        )
        assert (
            page.locator(
                '.doc-verify-results[data-doc-id="doc-alias-collision"]'
            ).count()
            == 0
        )
    finally:
        context.close()


def test_cross_type_canonical_party_id_collision_renders_no_cards_or_documents(
    chromium_browser,
):
    director_document = _verified_document(
        "doc-cross-type-director",
        DIRECTOR_ID,
        "director",
        "passport",
        "must-not-render-director-passport.pdf",
    )
    ubo_document = _verified_document(
        "doc-cross-type-ubo",
        DIRECTOR_ID,
        "ubo",
        "poa",
        "must-not-render-ubo-address.pdf",
    )
    harness = PortalApiHarness(
        status="kyc_documents",
        documents=[director_document, ubo_document],
    )
    harness.parties["ubos"][0]["id"] = DIRECTOR_ID

    context, page = _new_page(chromium_browser, harness)
    try:
        _login(page)
        _open_application(page)
        expect(page.locator("#view-onboarding")).not_to_have_class(
            re.compile(r"\bhidden\b")
        )
        expect(page.locator("#portal-party-hydration-errors")).to_be_visible()
        expect(page.locator("#portal-party-hydration-errors")).to_contain_text(
            "reuse the same stable ID across different party types"
        )
        assert page.locator(".kyc-person-card").count() == 0
        assert (
            page.locator(
                '.doc-verify-results[data-doc-id="doc-cross-type-director"]'
            ).count()
            == 0
        )
        assert (
            page.locator(
                '.doc-verify-results[data-doc-id="doc-cross-type-ubo"]'
            ).count()
            == 0
        )
        page.locator("#kyc-final-declaration").check()
        expect(page.locator("#btn-submit-docs")).to_be_disabled()

        page.evaluate("() => acceptPricing()")
        assert harness.accept_count == 0
        assert page.evaluate(
            """
            () => ({
              pricingBlocked: pricingPartyIntegrityErrors.some(
                (error) =>
                  error.reason === 'canonical_party_id_cross_type_collision'
              ),
              pricingBannerDisplayed:
                document.getElementById('pricing-party-integrity-error').style.display
            })
            """
        ) == {
            "pricingBlocked": True,
            "pricingBannerDisplayed": "block",
        }
    finally:
        context.close()


def test_canonical_empty_party_collections_never_revive_stale_prescreening_rows(
    chromium_browser,
):
    stale_director_name = "Stale Prescreening Director"
    stale_ubo_name = "Stale Prescreening Owner"
    harness = PortalApiHarness(
        status="kyc_documents",
        stale_prescreening_parties={
            "directors": [
                {
                    "id": "stale-director",
                    "person_key": "dir-stale",
                    "full_name": stale_director_name,
                    "first_name": "Stale",
                    "last_name": "Director",
                }
            ],
            "ubos": [
                {
                    "id": "stale-ubo",
                    "person_key": "ubo-stale",
                    "full_name": stale_ubo_name,
                    "first_name": "Stale",
                    "last_name": "Owner",
                    "ownership_pct": 100,
                }
            ],
            "intermediaries": [],
        },
    )
    harness.parties = {"directors": [], "ubos": [], "intermediaries": []}

    portal_context, portal_page = _new_page(chromium_browser, harness)
    try:
        _login(portal_page)
        _open_application(portal_page)
        expect(portal_page.locator("#view-onboarding")).not_to_have_class(
            re.compile(r"\bhidden\b")
        )
        expect(portal_page.locator("#portal-party-hydration-errors")).to_be_visible()
        expect(portal_page.locator("#portal-party-hydration-errors")).to_contain_text(
            "authoritative Director or UBO records are missing or empty"
        )
        assert portal_page.locator(".kyc-person-card").count() == 0
        expect(portal_page.locator("body")).not_to_contain_text(stale_director_name)
        expect(portal_page.locator("body")).not_to_contain_text(stale_ubo_name)
        portal_page.locator("#kyc-final-declaration").check()
        expect(portal_page.locator("#btn-submit-docs")).to_be_disabled()
    finally:
        portal_context.close()

    backoffice_context = chromium_browser.new_context()
    backoffice_context.route("**/*", harness.route)
    backoffice_page = backoffice_context.new_page()
    try:
        backoffice_page.goto(
            "http://regmind.test/backoffice",
            wait_until="domcontentloaded",
        )
        backoffice_page.locator("#login-email").fill("officer@example.test")
        backoffice_page.locator("#login-password").fill(
            "Synthetic-Only-Officer-Password-123!"
        )
        backoffice_page.locator("#login-form").evaluate(
            "(form) => form.requestSubmit()"
        )
        expect(backoffice_page.locator("#login-overlay")).to_be_hidden()
        result = backoffice_page.evaluate(
            """
            async (applicationRef) => {
              const mapped = await fetchApplicationDetail(applicationRef);
              currentApp = mapped;
              APPLICATION_ENHANCED_REQUIREMENTS = [];
              const html = renderStandardKycDocumentTaxonomy(
                mapped,
                { includeIdv: false }
              );
              const summary = computeDocumentReadinessSummary(mapped);
              return {
                directorCount: mapped.directors.length,
                uboCount: mapped.ubos.length,
                intermediaryCount: mapped.intermediaries.length,
                partyIntegrityCount: summary.partyIntegrityCount,
                isIncomplete: summary.isIncomplete,
                hasBanner: html.includes('backoffice-party-integrity-error'),
                hasStaleDirector: html.includes('Stale Prescreening Director'),
                hasStaleUbo: html.includes('Stale Prescreening Owner')
              };
            }
            """,
            APP_REF,
        )
        assert result == {
            "directorCount": 0,
            "uboCount": 0,
            "intermediaryCount": 0,
            "partyIntegrityCount": 2,
            "isIncomplete": True,
            "hasBanner": True,
            "hasStaleDirector": False,
            "hasStaleUbo": False,
        }
    finally:
        backoffice_context.close()


def test_unknown_entity_document_category_is_visible_and_submission_blocking(
    chromium_browser,
):
    document = _verified_entity_document(
        "doc-entity-passport-invalid",
        "passport",
        "entity-passport-must-not-disappear.pdf",
    )
    harness = PortalApiHarness(status="kyc_documents", documents=[document])
    context, page = _new_page(chromium_browser, harness)
    try:
        _login(page)
        _open_application(page)
        expect(page.locator("#portal-document-hydration-errors")).to_be_visible()
        expect(page.locator("#portal-document-hydration-errors")).to_contain_text(
            "could not be matched safely"
        )
        assert (
            page.locator(
                '.doc-verify-results[data-doc-id="doc-entity-passport-invalid"]'
            ).count()
            == 0
        )
        page.locator("#kyc-final-declaration").check()
        expect(page.locator("#btn-submit-docs")).to_be_disabled()
    finally:
        context.close()


def test_backoffice_filters_and_blocks_arbitrary_enhanced_document_links(
    chromium_browser,
):
    base_document = _verified_entity_document(
        "doc-base-source-wealth",
        "source_wealth",
        "base-source-wealth.pdf",
    )
    harness = PortalApiHarness(
        status="kyc_documents",
        documents=[base_document],
    )
    context = chromium_browser.new_context()
    context.route("**/*", harness.route)
    page = context.new_page()
    try:
        page.goto("http://regmind.test/backoffice", wait_until="domcontentloaded")
        page.locator("#login-email").fill("officer@example.test")
        page.locator("#login-password").fill(
            "Synthetic-Only-Officer-Password-123!"
        )
        page.locator("#login-form").evaluate("(form) => form.requestSubmit()")
        expect(page.locator("#login-overlay")).to_be_hidden()
        result = page.evaluate(
            """
            async (applicationRef) => {
              const mapped = await fetchApplicationDetail(applicationRef);
              const linked = mapped._documents.find(
                (doc) => doc.id === 'doc-base-source-wealth'
              );
              const requirement = {
                id: 'enhanced-source-wealth-001',
                active: true,
                audience: 'both',
                requirement_type: 'document',
                requirement_display_type: 'evidence',
                requirement_key: 'enhanced_source_of_wealth',
                requirement_label: 'Enhanced source-of-wealth evidence',
                status: 'accepted',
                linked_document_id: linked.id,
                linked_document: linked,
                canonical_doc_type: 'source_wealth',
                document_policy: { document_type: 'source_wealth' }
              };
              currentApp = mapped;
              APPLICATION_ENHANCED_REQUIREMENTS = [requirement];
              const integrity = enhancedRequirementDocumentLinkIntegrity(
                requirement,
                linked
              );
              const options = enhancedRequirementDocumentOptions(requirement);
              const summary = computeDocumentReadinessSummary(mapped);
              const html = renderStandardKycDocumentTaxonomy(
                mapped,
                { includeIdv: false }
              );
              return {
                integrityValid: integrity.valid,
                integrityReason: integrity.reason,
                optionsContainBaseDocument:
                  options.includes('doc-base-source-wealth'),
                enhancedIntegrityCount:
                  summary.enhancedDocumentLinkIntegrityCount,
                isIncomplete: summary.isIncomplete,
                hasBanner: html.includes(
                  'backoffice-enhanced-document-link-integrity-error'
                )
              };
            }
            """,
            APP_REF,
        )
        assert result == {
            "integrityValid": False,
            "integrityReason": "document_slot_mismatch",
            "optionsContainBaseDocument": False,
            "enhancedIntegrityCount": 1,
            "isIncomplete": True,
            "hasBanner": True,
        }
    finally:
        context.close()


def test_backoffice_accepted_document_without_link_is_named_red_blocker(
    chromium_browser,
):
    harness = PortalApiHarness(status="kyc_documents")
    context = chromium_browser.new_context()
    context.route("**/*", harness.route)
    page = context.new_page()
    try:
        page.goto("http://regmind.test/backoffice", wait_until="domcontentloaded")
        page.locator("#login-email").fill("officer@example.test")
        page.locator("#login-password").fill(
            "Synthetic-Only-Officer-Password-123!"
        )
        page.locator("#login-form").evaluate("(form) => form.requestSubmit()")
        expect(page.locator("#login-overlay")).to_be_hidden()
        result = page.evaluate(
            """
            async (applicationRef) => {
              const mapped = await fetchApplicationDetail(applicationRef);
              const requirement = {
                id: 'accepted-missing-document-001',
                active: true,
                audience: 'both',
                requirement_type: 'document',
                requirement_display_type: 'evidence',
                requirement_key: 'company_bank_reference',
                requirement_label: 'Company bank reference',
                status: 'accepted',
                linked_document_id: null,
                canonical_doc_type: 'bankref',
                document_policy: { document_type: 'bankref' },
                mandatory: true,
                blocking_approval: true
              };
              const summary = {
                enhanced_review_active: true,
                total: 1,
                unresolved_count: 1,
                mandatory_unresolved_count: 1,
                blocking_unresolved_count: 1,
                pending_client_count: 0,
                submitted_awaiting_review_count: 0,
                rejected_count: 0,
                accepted_count: 1,
                waived_count: 0,
                document_integrity_error_count: 1,
                approval_blocked: true,
                next_action: 'Repair invalid enhanced-evidence document links',
                next_action_code: 'repair_document_links',
                status_label: 'Approval blocked',
                trigger_labels: ['P0 accepted missing evidence'],
                type_counts: {
                  evidence: 1,
                  portal_disclosure: 0,
                  internal_control: 0
                },
                invalid_document_links: [{
                  id: requirement.id,
                  requirement_label: requirement.requirement_label,
                  linked_document_id: '',
                  action_needed:
                    'Accepted document requirements must retain valid linked evidence.',
                  document_integrity: {
                    valid: false,
                    reason: 'linked_document_missing',
                    message:
                      'Accepted document requirements must retain valid linked evidence.'
                  }
                }]
              };
              currentApp = mapped;
              renderApplicationEnhancedRequirements(
                [requirement],
                null,
                summary
              );
              const mappedRequirement = APPLICATION_ENHANCED_REQUIREMENTS[0];
              const errors = enhancedRequirementDocumentIntegrityErrors(
                APPLICATION_ENHANCED_REQUIREMENTS
              );
              const readiness = computeDocumentReadinessSummary(mapped);
              const taxonomyHtml = renderStandardKycDocumentTaxonomy(
                mapped,
                { includeIdv: false }
              );
              const rowHtml = renderEnhancedEvidenceDocumentsGroupHtml(
                APPLICATION_ENHANCED_REQUIREMENTS
              );
              return {
                serverInvalid:
                  mappedRequirement._server_document_integrity_invalid,
                integrityReason:
                  mappedRequirement.linked_document_integrity.reason,
                errorCount: errors.length,
                errorRequirementId: errors[0].requirement.id,
                redStatus:
                  enhancedRequirementStatusBadge(
                    mappedRequirement.status,
                    mappedRequirement
                  ).includes('Accepted — evidence missing'),
                rowNamed: rowHtml.includes('Company bank reference'),
                rowInvalid: rowHtml.includes(
                  'Accepted document requirements must retain valid linked evidence.'
                ),
                globalBanner: taxonomyHtml.includes(
                  'backoffice-enhanced-document-link-integrity-error'
                ),
                readinessBlocked:
                  readiness.isIncomplete &&
                  readiness.enhancedDocumentLinkIntegrityCount === 1,
                namedReadinessBlocker: readiness.blockerDescriptions.some(
                  (description) =>
                    description.includes('Company bank reference')
                )
              };
            }
            """,
            APP_REF,
        )
        assert result == {
            "serverInvalid": True,
            "integrityReason": "linked_document_missing",
            "errorCount": 1,
            "errorRequirementId": "accepted-missing-document-001",
            "redStatus": True,
            "rowNamed": True,
            "rowInvalid": True,
            "globalBanner": True,
            "readinessBlocked": True,
            "namedReadinessBlocker": True,
        }
    finally:
        context.close()


def test_portal_invalid_enhanced_link_never_double_renders_base_document(
    chromium_browser,
):
    base_document = _verified_document(
        "doc-base-passport-invalid-enhanced-link",
        DIRECTOR_ID,
        "director",
        "passport",
        "director-base-passport-only.pdf",
    )
    invalid_requirement = {
        "id": "enhanced-historical-invalid-link",
        "label": "Enhanced source-of-wealth evidence",
        "description": "Provide separate enhanced evidence.",
        "requirement_type": "document",
        "status": "required",
        "status_label": "Action required",
        "subject_scope": "application",
        "linked_document_id": base_document["id"],
        # Even if a stale API or intermediary accidentally includes the
        # invalid document payload, the portal must not render it as enhanced.
        "linked_document": deepcopy(base_document),
        "linked_document_integrity_valid": False,
        "linked_document_integrity_error": (
            "Previously submitted evidence could not be matched safely. "
            "Please upload the requested document again or contact support."
        ),
    }
    harness = PortalApiHarness(
        status="kyc_documents",
        documents=[base_document],
        enhanced_requirements=[invalid_requirement],
    )
    context, page = _new_page(chromium_browser, harness)
    try:
        _login(page)
        _open_application(page)
        expect(
            page.locator("#portal-enhanced-document-link-integrity-error")
        ).to_be_visible()
        enhanced_card = page.locator(
            '[data-enhanced-requirement-id="enhanced-historical-invalid-link"]'
        )
        expect(enhanced_card).to_be_visible()
        expect(enhanced_card).to_contain_text("Evidence matching issue")
        expect(enhanced_card).not_to_contain_text(base_document["doc_name"])
        expect(page.locator(f"#kyc-passport-lbl-{DIRECTOR_ID}")).to_have_text(
            base_document["doc_name"]
        )
        assert (
            page.locator(
                '.doc-verify-results[data-doc-id="doc-base-passport-invalid-enhanced-link"]'
            ).count()
            == 1
        )
        page.locator("#kyc-final-declaration").check()
        expect(page.locator("#btn-submit-docs")).to_be_disabled()
    finally:
        context.close()
