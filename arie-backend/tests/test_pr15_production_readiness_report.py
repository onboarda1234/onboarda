from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / "docs" / "qa" / "PR15-production-readiness-hardening.md"


def _report_text():
    return REPORT.read_text(encoding="utf-8")


def test_pr15_report_records_scope_and_source_of_truth():
    text = _report_text()

    assert "PR15 Production Readiness Hardening" in text
    assert "cc1708bbb0851505b3239d47c3d8ae9a5e32e19d" in text
    assert "GitHub `main` is the code source of truth" in text
    assert "AWS staging is the runtime source of truth" in text
    assert "Render demo evidence is not acceptable" in text
    assert "does not touch protected UI files" in text


def test_pr15_report_pins_required_deploy_and_runtime_evidence():
    text = _report_text()

    for expected in [
        "GitHub main SHA",
        "Deployed SHA",
        "ECS task definition",
        "ECR image",
        "/api/version",
        "/api/health",
        "/api/liveness",
        "Targeted backend tests",
        "Authenticated browser smoke",
        "CloudWatch review",
        "Rollback target",
    ]:
        assert expected in text


def test_pr15_report_confirms_lifecycle_architecture_boundaries():
    text = _report_text()

    for expected in [
        "`periodic_reviews` remains the canonical periodic-review state owner",
        "Lifecycle Queue remains a launchpad, not an editor",
        "Case Management remains assigned work only",
        "Ongoing Monitoring remains monitoring signals and agents only",
        "Screening, EDD, Change Management, and KYC Documents remain owner workflows",
        "Evidence links use `periodic_review_evidence_links`",
        "Reports and analytics count each periodic review once",
    ]:
        assert expected in text


def test_pr15_report_requires_audit_security_and_agent_boundaries():
    text = _report_text()

    for expected in [
        "SCO/admin-only actions remain SCO/admin-only",
        "Agent outputs may surface signals but must not write officer-owned fields",
        "No bearer token, QA password, or staging credential is committed",
        "Legacy `/decision` completion remains fenced from modern `outcome`",
        "before/after state",
    ]:
        assert expected in text


def test_pr15_report_classifies_blockers_and_pilot_verdict():
    text = _report_text()

    for expected in [
        "P0",
        "P1",
        "P2",
        "P3",
        "Staging acceptance blockers",
        "Production blockers",
        "ready for a controlled AWS staging",
        "Broad production rollout is not approved",
    ]:
        assert expected in text


def test_pr15_report_documents_rollback_and_validation_commands():
    text = _report_text()

    assert "docs/DEPLOYMENT_RUNBOOK.md" in text
    assert "regmind-staging:<revision>" in text
    assert "previous known-good" in text
    assert "test_pr15_production_readiness_report.py" in text
    assert "staging_browser_smoke.js" in text
    assert "STAGING_QA_EMAIL" in text
    assert "STAGING_QA_PASSWORD" in text


def test_pr15_report_does_not_store_credentials_or_tokens():
    text = _report_text()

    assert "Authorization: Bearer" not in text
    assert "StagingQa2026" not in text
    assert "m.dubois@ariefinance.mu" not in text
