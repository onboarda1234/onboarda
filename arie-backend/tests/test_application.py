"""
Tests for application workflow.
"""
import pytest
import json


class TestApplicationWorkflow:
    def test_create_application(self, db):
        """Application can be created with required fields."""
        db.execute("""
            INSERT INTO applications (id, ref, client_id, company_name, country, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("app_test_1", "ARF-2026-999", "testclient001", "Test Corp", "Mauritius", "draft"))
        db.commit()

        row = db.execute("SELECT * FROM applications WHERE id='app_test_1'").fetchone()
        assert row is not None
        assert row["status"] == "draft"
        assert row["company_name"] == "Test Corp"

    def test_application_status_transitions(self, db):
        """Application status transitions are valid."""
        db.execute("""
            INSERT INTO applications (id, ref, client_id, company_name, status)
            VALUES (?, ?, ?, ?, ?)
        """, ("app_trans_1", "ARF-2026-TR1", "testclient001", "Trans Corp", "draft"))
        db.commit()

        # Draft -> prescreening_submitted (valid status per CHECK constraint)
        db.execute("UPDATE applications SET status='prescreening_submitted' WHERE id='app_trans_1'")
        db.commit()
        row = db.execute("SELECT status FROM applications WHERE id='app_trans_1'").fetchone()
        assert row["status"] == "prescreening_submitted"

    def test_directors_linked_to_application(self, db, sample_application):
        """Directors are properly linked to applications."""
        db.execute("""
            INSERT INTO directors (id, application_id, full_name, nationality)
            VALUES (?, ?, ?, ?)
        """, ("dir001", sample_application, "John Smith", "Mauritius"))
        db.commit()

        dirs = db.execute(
            "SELECT * FROM directors WHERE application_id=?", (sample_application,)
        ).fetchall()
        assert len(dirs) == 1
        assert dirs[0]["full_name"] == "John Smith"

    def test_ubos_linked_to_application(self, db, sample_application):
        """UBOs are properly linked to applications."""
        db.execute("""
            INSERT INTO ubos (id, application_id, full_name, nationality, ownership_pct)
            VALUES (?, ?, ?, ?, ?)
        """, ("ubo001", sample_application, "Jane Doe", "UK", 75.0))
        db.commit()

        ubos = db.execute(
            "SELECT * FROM ubos WHERE application_id=?", (sample_application,)
        ).fetchall()
        assert len(ubos) == 1
        assert ubos[0]["ownership_pct"] == 75.0

    def test_audit_trail_created(self, db):
        """Audit log entries are created properly."""
        db.execute("""
            INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("admin001", "Test Admin", "admin", "Test Action", "test_target", "Test detail"))
        db.commit()

        logs = db.execute("SELECT * FROM audit_log WHERE action='Test Action'").fetchall()
        assert len(logs) == 1
        assert logs[0]["user_name"] == "Test Admin"

    def test_batch_a_material_prescreening_persists_authoritatively(self, db):
        """Material intake fields should resolve to an authoritative application record."""
        from server import normalize_prescreening_data, resolve_application_company_name

        payload = {
            "company_name": "",
            "entity_name": "Acme Holdings Ltd",
            "brn": "C123456",
            "country": "Mauritius",
            "sector": "Technology",
            "entity_type": "SME / Private Company",
            "ownership_structure": "Simple — direct identifiable UBOs",
            "prescreening_data": {
                "registered_entity_name": "Acme Holdings Ltd",
                "trading_name": "Acme Pay",
                "registered_address": "10 Harbour Front, Port Louis",
                "headquarters_address": "10 Harbour Front, Port Louis",
                "entity_contact_first": "Jane",
                "entity_contact_last": "Doe",
                "entity_contact_email": "jane@acme.test",
                "entity_contact_phone_code": "+230",
                "entity_contact_mobile": "57550000",
                "website": "https://acme.test",
                "regulatory_licences": "None",
                "services_required": ["Multi-currency corporate accounts"],
                "monthly_volume": "USD 50,000 to USD 500,000 per month",
                "transaction_complexity": "Standard — multi-currency, low-risk corridors",
                "countries_of_operation": ["Mauritius", "United Kingdom"],
                "business_overview": "B2B treasury platform for export merchants.",
                "target_markets": ["United Kingdom", "United Arab Emirates"],
                "account_purposes": ["Receiving payments from clients", "International transfers / FX"],
                "existing_bank_account": "Yes",
                "existing_bank_name": "Barclays, UK",
                "currencies": ["USD", "EUR"],
                "source_of_wealth_type": "Business revenue / trading profits",
                "source_of_wealth_detail": "Bootstrapped from software revenues.",
                "source_of_funds_initial_type": "Transfer from company bank account",
                "source_of_funds_initial_detail": "Initial treasury transfer from Barclays.",
                "source_of_funds_ongoing_type": "Client payments / receivables",
                "source_of_funds_ongoing_detail": "Export merchant settlements.",
                "management_overview": "Founder-led management team with in-house engineering.",
                "introduction_method": "Direct application — client initiated",
                "consent_declaration": True
            }
        }

        prescreening = normalize_prescreening_data(payload)
        company_name = resolve_application_company_name(payload, prescreening)

        db.execute("""
            INSERT INTO applications (
                id, ref, client_id, company_name, brn, country, sector,
                entity_type, ownership_structure, prescreening_data, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            "app_batch_a",
            "ARF-2026-BA1",
            "testclient001",
            company_name,
            payload["brn"],
            payload["country"],
            payload["sector"],
            payload["entity_type"],
            payload["ownership_structure"],
            json.dumps(prescreening),
            "draft"
        ))
        db.commit()

        row = db.execute("SELECT company_name, prescreening_data FROM applications WHERE id='app_batch_a'").fetchone()
        stored = json.loads(row["prescreening_data"])

        assert row["company_name"] == "Acme Holdings Ltd"
        assert stored["registered_entity_name"] == "Acme Holdings Ltd"
        assert stored["trading_name"] == "Acme Pay"
        assert stored["registered_address"] == "10 Harbour Front, Port Louis"
        assert stored["services_required"] == ["Multi-currency corporate accounts"]
        assert stored["countries_of_operation"] == ["Mauritius", "United Kingdom"]
        assert stored["business_overview"] == "B2B treasury platform for export merchants."
        assert stored["target_markets"] == ["United Kingdom", "United Arab Emirates"]
        assert stored["account_purposes"] == ["Receiving payments from clients", "International transfers / FX"]
        assert stored["expected_volume"] == "USD 50,000 to USD 500,000 per month"
        assert stored["source_of_funds"] == (
            "Initial: Transfer from company bank account; Initial treasury transfer from Barclays.; "
            "Ongoing: Client payments / receivables; Export merchant settlements."
        )
        assert stored["consent_declaration"] is True

    def test_batch_a_blank_legal_entity_name_is_not_resolved(self):
        """Helper should not resolve an authoritative company name from empty input."""
        from server import normalize_prescreening_data, resolve_application_company_name

        payload = {
            "country": "Mauritius",
            "prescreening_data": {"monthly_volume": "Under USD 50,000 per month"}
        }
        prescreening = normalize_prescreening_data(payload)
        assert resolve_application_company_name(payload, prescreening) == ""

    def test_batch_b_stores_directors_ubos_and_intermediaries_with_stable_keys(self, db, sample_application):
        """Ownership parties should persist stable person keys and declaration data."""
        from server import store_application_parties

        store_application_parties(
            db,
            sample_application,
            directors=[{
                "person_key": "dir7",
                "first_name": "John",
                "last_name": "Smith",
                "nationality": "Mauritius",
                "is_pep": "Yes",
                "pep_declaration": {
                    "public_function": "Former minister",
                    "source_of_wealth_categories": ["Business operations / profits"]
                }
            }],
            ubos=[{
                "person_key": "ubo4",
                "first_name": "Jane",
                "last_name": "Doe",
                "nationality": "United Kingdom",
                "ownership_pct": 62.5,
                "is_pep": "No",
                "pep_declaration": {}
            }],
            intermediaries=[{
                "person_key": "int2",
                "entity_name": "North HoldCo Ltd",
                "jurisdiction": "BVI",
                "ownership_pct": 100
            }]
        )
        db.commit()

        director = db.execute(
            "SELECT person_key, first_name, last_name, full_name, is_pep, pep_declaration FROM directors WHERE application_id=?",
            (sample_application,)
        ).fetchone()
        ubo = db.execute(
            "SELECT person_key, first_name, last_name, full_name, ownership_pct FROM ubos WHERE application_id=?",
            (sample_application,)
        ).fetchone()
        intermediary = db.execute(
            "SELECT person_key, entity_name, jurisdiction, ownership_pct FROM intermediaries WHERE application_id=?",
            (sample_application,)
        ).fetchone()

        assert director["person_key"] == "dir7"
        assert director["first_name"] == "John"
        assert director["last_name"] == "Smith"
        assert director["full_name"] == "John Smith"
        assert director["is_pep"] == "Yes"
        assert json.loads(director["pep_declaration"])["public_function"] == "Former minister"

        assert ubo["person_key"] == "ubo4"
        assert ubo["full_name"] == "Jane Doe"
        assert ubo["ownership_pct"] == 62.5

        assert intermediary["person_key"] == "int2"
        assert intermediary["entity_name"] == "North HoldCo Ltd"
        assert intermediary["jurisdiction"] == "BVI"
        assert intermediary["ownership_pct"] == 100

    def test_batch_b_resolves_person_references_by_person_key(self, db, sample_application):
        """Document linkage helpers should resolve stored person keys without row-order fallbacks."""
        from server import resolve_application_person, store_application_parties

        store_application_parties(
            db,
            sample_application,
            directors=[{
                "person_key": "dir11",
                "first_name": "Amina",
                "last_name": "Khan",
                "nationality": "UAE",
                "is_pep": "No",
                "pep_declaration": {}
            }],
            ubos=[{
                "person_key": "ubo12",
                "first_name": "Omar",
                "last_name": "Ali",
                "nationality": "Mauritius",
                "ownership_pct": 40,
                "is_pep": "Yes",
                "pep_declaration": {"public_function": "MP"}
            }],
            intermediaries=[{
                "person_key": "int13",
                "entity_name": "Layered SPV Ltd",
                "jurisdiction": "Cayman Islands",
                "ownership_pct": 40
            }]
        )
        db.commit()

        director = resolve_application_person(db, sample_application, "dir11")
        ubo = resolve_application_person(db, sample_application, "ubo12")
        intermediary = resolve_application_person(db, sample_application, "int13")

        assert director["full_name"] == "Amina Khan"
        assert director["person_type"] == "director"
        assert ubo["full_name"] == "Omar Ali"
        assert ubo["person_type"] == "ubo"
        assert ubo["pep_declaration"]["public_function"] == "MP"
        assert intermediary["full_name"] == "Layered SPV Ltd"
        assert intermediary["person_type"] == "intermediary"


class TestDocuments:
    def test_document_record_creation(self, db, sample_application):
        """Document records can be created."""
        db.execute("""
            INSERT INTO documents (id, application_id, doc_type, doc_name, file_path)
            VALUES (?, ?, ?, ?, ?)
        """, ("doc001", sample_application, "passport", "passport.pdf", "/uploads/test.pdf"))
        db.commit()

        docs = db.execute(
            "SELECT * FROM documents WHERE application_id=?", (sample_application,)
        ).fetchall()
        assert len(docs) == 1
        assert docs[0]["verification_status"] == "pending"


class TestLegacyNormalization:
    def test_base64_wrapped_fernet_values_are_decrypted(self):
        """Legacy base64-wrapped Fernet ciphertext must not leak into detail, risk, or memo views."""
        from server import encrypt_pii_fields, decrypt_pii_fields

        encrypted = encrypt_pii_fields({"nationality": "Mauritius"}, ["nationality"])
        legacy_wrapped = json.loads(json.dumps({"nationality": encrypted["nationality"]}))
        legacy_wrapped["nationality"] = __import__("base64").b64encode(
            str(legacy_wrapped["nationality"]).encode("utf-8")
        ).decode("utf-8")

        decrypted = decrypt_pii_fields(legacy_wrapped, ["nationality"])
        assert decrypted["nationality"] == "Mauritius"

    def test_saved_session_prescreening_backfills_sparse_application_records(self):
        """Legacy applications should surface material prescreening fields from saved portal sessions when DB JSON is sparse."""
        from server import merge_prescreening_sources, normalize_saved_session_prescreening

        form_data = {
            "prescreening": {
                "f-trade-name": "Legacy Trade",
                "f-source-wealth-type": "Business revenue / trading profits",
                "f-source-wealth": "Generated from software subscriptions.",
                "f-intro-method": "Introduced by partner",
                "f-mgmt": "Founder-led operations"
            }
        }
        session_backfill = normalize_saved_session_prescreening(form_data)
        merged = merge_prescreening_sources({}, session_backfill)

        assert merged["trading_name"] == "Legacy Trade"
        assert merged["source_of_wealth_type"] == "Business revenue / trading profits"
        assert merged["source_of_wealth_detail"] == "Generated from software subscriptions."
        assert merged["introduction_method"] == "Introduced by partner"
        assert merged["management_overview"] == "Founder-led operations"

    def test_legacy_camelcase_session_shape_is_backfilled(self):
        """Older saved-session payloads with camelCase keys should still populate prescreening detail."""
        from server import normalize_saved_session_prescreening

        form_data = {
            "regName": "Legacy Camel Ltd",
            "tradeName": "Legacy Trade",
            "regAddress": "10 Harbour Street",
            "monthlyVolume": "USD 50,000 to USD 500,000 per month",
            "sourceWealthType": "Business revenue / trading profits",
            "sourceWealth": "Generated from operating revenues.",
            "introMethod": "Introduced by partner",
            "referrerName": "Legacy Referrer",
            "managementOverview": "Founder-led management team"
        }

        normalized = normalize_saved_session_prescreening(form_data)
        assert normalized["registered_entity_name"] == "Legacy Camel Ltd"
        assert normalized["trading_name"] == "Legacy Trade"
        assert normalized["registered_address"] == "10 Harbour Street"
        assert normalized["expected_volume"] == "USD 50,000 to USD 500,000 per month"
        assert normalized["source_of_wealth_type"] == "Business revenue / trading profits"
        assert normalized["source_of_wealth_detail"] == "Generated from operating revenues."
        assert normalized["introduction_method"] == "Introduced by partner"
        assert normalized["referrer_name"] == "Legacy Referrer"
        assert normalized["management_overview"] == "Founder-led management team"

    def test_zero_documents_do_not_generate_false_mitigant(self):
        """Memo generation must not claim documents were received when none exist."""
        from memo_handler import build_compliance_memo

        app = {
            "ref": "ARF-2026-NODOCS",
            "company_name": "No Docs Ltd",
            "brn": "C100",
            "country": "Mauritius",
            "sector": "Technology",
            "entity_type": "SME",
            "source_of_funds": "Operating revenue",
            "expected_volume": "USD 50,000",
            "ownership_structure": "Simple",
            "risk_level": "LOW",
            "risk_score": 22,
            "assigned_to": "admin001",
        }

        memo, _, supervisor_result, _ = build_compliance_memo(
            app,
            [{"full_name": "Test Director", "nationality": "Mauritius", "is_pep": "No"}],
            [{"full_name": "Test UBO", "nationality": "Mauritius", "ownership_pct": 100, "is_pep": "No"}],
            []
        )

        mitigants = memo["sections"]["red_flags_and_mitigants"]["mitigants"]
        red_flags = memo["sections"]["red_flags_and_mitigants"]["red_flags"]
        assert not any("All required documents received" in item for item in mitigants)
        assert any("no uploaded documents" in item.lower() for item in red_flags)
        assert memo["metadata"]["document_count"] == 0
        assert memo["metadata"]["documentation_complete"] is False
        assert supervisor_result["can_approve"] is False

    def test_save_resume_session_preserves_checkbox_and_multiselect_fields(self):
        """Saved session data with list fields (services, countries, currencies)
        should roundtrip through normalization and appear in merged prescreening."""
        from server import normalize_saved_session_prescreening, merge_prescreening_sources

        form_data = {
            "prescreening": {
                "f-reg-name": "Test Corp",
                "f-trade-name": "Test Trade",
                "f-inc-date": "2020-06-15",
                "f-inc-country": "Mauritius",
                "f-brn": "C12345",
                "f-source-wealth-type": "Business revenue",
                "f-source-init-type": "Bank transfer",
                "f-source-init": "From main operating account",
                "f-source-ongoing-type": "Revenue collection",
                "f-source-ongoing": "Monthly client payments",
            },
            "servicesRequired": ["Multi-currency accounts", "FX services"],
            "accountPurposes": ["Receive client payments", "Pay suppliers"],
            "countriesOfOperation": ["Mauritius", "South Africa"],
            "targetMarkets": ["Europe", "Asia"],
            "currencies": ["USD", "EUR", "GBP"],
        }
        session_backfill = normalize_saved_session_prescreening(form_data)
        merged = merge_prescreening_sources({}, session_backfill)

        assert merged["registered_entity_name"] == "Test Corp"
        assert merged["trading_name"] == "Test Trade"
        assert merged["incorporation_date"] == "2020-06-15"
        assert merged["country_of_incorporation"] == "Mauritius"
        assert merged["brn"] == "C12345"
        assert merged["services_required"] == ["Multi-currency accounts", "FX services"]
        assert merged["account_purposes"] == ["Receive client payments", "Pay suppliers"]
        assert merged["countries_of_operation"] == ["Mauritius", "South Africa"]
        assert merged["target_markets"] == ["Europe", "Asia"]
        assert merged["currencies"] == ["USD", "EUR", "GBP"]
        assert merged["source_of_wealth_type"] == "Business revenue"
        assert merged["source_of_funds_initial_type"] == "Bank transfer"
        assert merged["source_of_funds_initial_detail"] == "From main operating account"
        assert merged["source_of_funds_ongoing_type"] == "Revenue collection"
        assert merged["source_of_funds_ongoing_detail"] == "Monthly client payments"
        assert "source_of_funds" in merged
        assert "Bank transfer" in merged["source_of_funds"]

    def test_save_resume_director_ubo_intermediary_keys_are_stable(self):
        """Director/UBO/intermediary rows saved with semantic keys should
        persist and retrieve correctly via store_application_parties."""
        from server import store_application_parties, get_application_parties

        app_id = "app_save_resume_party_test"
        db = self._make_db(app_id)

        directors = [
            {"person_key": "dir1", "first_name": "Alice", "last_name": "Doe",
             "nationality": "Mauritius", "is_pep": "No"},
            {"person_key": "dir2", "first_name": "Bob", "last_name": "Smith",
             "nationality": "South Africa", "is_pep": "Yes",
             "pep_declaration": {"public_function": "Member of Parliament"}},
        ]
        ubos = [
            {"person_key": "ubo1", "first_name": "Charlie", "last_name": "Brown",
             "nationality": "UK", "ownership_pct": 60, "is_pep": "No"},
        ]
        intermediaries = [
            {"person_key": "int1", "entity_name": "HoldCo Ltd",
             "jurisdiction": "BVI", "ownership_pct": 40},
        ]
        store_application_parties(db, app_id,
                                 directors=directors, ubos=ubos,
                                 intermediaries=intermediaries)
        dirs_out, ubos_out, ints_out = get_application_parties(db, app_id)

        assert len(dirs_out) == 2
        assert dirs_out[0]["first_name"] == "Alice"
        assert dirs_out[0]["last_name"] == "Doe"
        assert dirs_out[0]["full_name"] == "Alice Doe"
        assert dirs_out[1]["first_name"] == "Bob"
        assert dirs_out[1]["pep_declaration"]["public_function"] == "Member of Parliament"

        assert len(ubos_out) == 1
        assert ubos_out[0]["first_name"] == "Charlie"
        assert ubos_out[0]["ownership_pct"] == 60

        assert len(ints_out) == 1
        assert ints_out[0]["entity_name"] == "HoldCo Ltd"
        assert ints_out[0]["jurisdiction"] == "BVI"
        assert ints_out[0]["ownership_pct"] == 40

        db.close()

    def _make_db(self, app_id):
        """Helper: create an in-memory DB with the application tables and a test row."""
        import sqlite3, os, sys
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from db import init_db, get_db
        import tempfile
        db_path = os.path.join(tempfile.gettempdir(), f"onboarda_test_party_{os.getpid()}.db")
        os.environ["DB_PATH"] = db_path
        init_db()
        db = get_db()
        db.execute("""
            INSERT OR IGNORE INTO applications (id, ref, client_id, company_name, status)
            VALUES (?, ?, ?, ?, ?)
        """, (app_id, "ARF-TEST-" + app_id, "testclient", "Test Corp", "draft"))
        db.commit()
        return db

    def test_detail_api_returns_estimated_activity_and_forecast(self):
        """The prescreening_data returned by detail API should include
        estimated_monthly_activity and financial_forecast when stored."""
        from server import normalize_prescreening_data

        data = {
            "company_name": "Forecast Corp",
            "country": "Mauritius",
            "prescreening_data": {
                "registered_entity_name": "Forecast Corp",
                "estimated_monthly_activity": {
                    "inflows": {"transactions": 50, "min_amount_usd": 1000},
                    "outflows": {"transactions": 30, "min_amount_usd": 500}
                },
                "financial_forecast": {
                    "revenue": {"year_1": 100000, "year_2": 200000, "year_3": 300000},
                    "cost_of_sales": {"year_1": 50000, "year_2": 80000, "year_3": 100000},
                    "profit": {"year_1": 50000, "year_2": 120000, "year_3": 200000}
                },
                "incorporation_date": "2020-01-15",
                "source_of_funds_initial_type": "Bank transfer",
                "source_of_funds_initial_detail": "Seed investment",
                "source_of_funds_ongoing_type": "Client revenue",
                "source_of_funds_ongoing_detail": "Monthly SaaS subscriptions"
            }
        }
        normalized = normalize_prescreening_data(data)

        # estimated_monthly_activity should survive normalization
        assert "estimated_monthly_activity" in normalized
        activity = normalized["estimated_monthly_activity"]
        assert isinstance(activity, dict)
        assert "inflows" in activity

        # financial_forecast should survive normalization
        assert "financial_forecast" in normalized
        forecast = normalized["financial_forecast"]
        assert isinstance(forecast, dict)

        # incorporation_date should survive
        assert normalized.get("incorporation_date") == "2020-01-15"

        # source_of_funds summary should be composed
        assert "source_of_funds" in normalized
        assert "Bank transfer" in normalized["source_of_funds"]
        assert "Client revenue" in normalized["source_of_funds"]
