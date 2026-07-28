"""Static safety and coverage contracts for the staging semantic smoke."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "arie-backend" / "scripts" / "qa" / "protected_module_staging_smoke.py"
BASELINE = (
    ROOT
    / "docs"
    / "audits"
    / "evidence"
    / "PR-MON-PROTECTED-BASELINE-1"
    / "protected_module_semantic_baseline.json"
)


def _text():
    return SCRIPT.read_text(encoding="utf-8")


def test_staging_smoke_covers_all_canonical_protected_cases():
    text = _text()
    for reference in (
        "RM-PILOT-001",
        "RM-PILOT-024",
        "RM-PILOT-028",
        "RM-PILOT-039",
        "RM-PILOT-041",
        "ARF-QAFIX-001",
        "ARF-QAFIX-002",
        "ARF-QAFIX-004",
        "ARF-QAFIX-005",
        "ARF-QAFIX-006",
    ):
        assert reference in text
    for contract in (
        "canonical_applications_hidden_by_default",
        "screening_fixtures_hidden_by_default",
        "pending_failed_stale_never_clear",
        "four_eyes_fixture_remains_explicit",
        "risk_model_read_only",
        "risk_model_dimensions_d1_d5",
        "authoritative_risk_evidence_available",
        "edd_cases_link_to_canonical_applications",
        "change_management_fixture_hidden_by_default",
        "change_management_fixture_available_by_opt_in",
        "change_management_fixture_semantics_available",
        "monitoring_dashboard_flag_enabled",
    ):
        assert contract in text


def test_staging_smoke_has_only_one_explicit_post_for_authentication():
    text = _text()
    assert text.count('method="POST"') == 1
    auth_block = text.split("def authenticate(self):", 1)[1].split(
        "def get(self, path: str):", 1
    )[0]
    assert "/auth/officer/login" in auth_block
    assert 'method="POST"' in auth_block
    assert "resource_request_policy" in text
    assert "GET-only after authentication" in text
    for mutation_path in (
        "/approve",
        "/reject",
        "/implement",
        "/preconditions",
        "/documents",
        "/dispositions",
        "/rescreen",
    ):
        assert mutation_path not in text


def test_staging_smoke_never_emits_credentials_or_token():
    text = _text()
    assert "credential values are never emitted" in text
    assert '"password": self.password' in text
    assert '"token":' not in text
    assert "self.token =" in text
    result_block = text.split('result = {', 1)[1]
    assert '"password": self.password' not in result_block
    assert '"email": self.email' not in result_block


def test_staging_smoke_uses_central_brand_for_default_url():
    text = _text()
    assert "from branding import BRAND" in text
    assert "f\"https://staging.{BRAND['system_id']}.co\"" in text
    assert '"https://staging.regmind.co"' not in text


def test_semantic_hashes_exclude_volatile_timestamps_and_actor_fields():
    text = _text()
    assert "semantic_hash" in text
    assert "semantic_sha256" in text
    assert '"captured_at", "semantic_sha256"' in text
    assert "created_at" not in text
    assert "updated_at" not in text
    assert "reviewed_by" not in text


def test_compact_baseline_pins_every_protected_semantic_hash():
    import json

    baseline = json.loads(BASELINE.read_text(encoding="utf-8"))
    assert baseline["staging"]["backend_task_definition"] == "regmind-staging:980"
    assert (
        baseline["staging"]["worker_task_definition"]
        == "regmind-verification-worker:428"
    )
    assert "arn:aws" not in BASELINE.read_text(encoding="utf-8")
    expected = baseline["expected_semantics"]
    assert set(expected["applications"]) == {
        "RM-PILOT-001",
        "RM-PILOT-024",
        "RM-PILOT-028",
        "RM-PILOT-039",
        "RM-PILOT-041",
    }
    assert set(expected["screening"]) == {
        "ARF-QAFIX-001",
        "ARF-QAFIX-002",
        "ARF-QAFIX-004",
        "ARF-QAFIX-005",
        "ARF-QAFIX-006",
    }
    assert expected["fixture_visibility"] == {
        "change_management_fixture_application_id": (
            "qafix-confirmed-match-817"
        ),
        "change_management_fixture_available_by_opt_in": True,
        "change_management_fixture_hidden_by_default": True,
    }
    assert set(expected["edd_cases"]) == {
        "RM-PILOT-024",
        "RM-PILOT-028",
    }
    assert expected["screening_provider"]["rescreen_enabled"] is False
    assert expected["screening_provider"]["hydration_enabled"] is False
    assert (
        expected["environment_features"]["ENABLE_MONITORING_DASHBOARD"]
        is True
    )
    for digest in [
        expected["risk_model_semantic_sha256"],
        expected["change_management_semantic_sha256"],
        *expected["applications"].values(),
        *expected["screening"].values(),
        *expected["edd_cases"].values(),
    ]:
        assert len(digest) == 64
        int(digest, 16)


def test_semantic_snapshots_cover_decision_memo_and_screening_axes():
    text = _text()
    for contract in (
        '"decision_eligibility"',
        '"kyc_gates"',
        '"memo"',
        '"content_semantic_sha256"',
        '"supervisor_semantic_sha256"',
        '"agent3_advisory_semantic_sha256"',
        '"axes"',
        '"execution"',
        '"adjudication"',
        '"provenance"',
        '"provider_references"',
        '"evidence_items_semantic_sha256"',
        '"review_evidence_semantic_sha256"',
        '"baseline_environment_features_match"',
        '"baseline_change_management_matches"',
        '"baseline_edd_"',
    ):
        assert contract in text
