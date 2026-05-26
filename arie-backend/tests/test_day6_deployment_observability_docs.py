from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_day6_deployment_observability_ledger_is_documented():
    runbook = (ROOT / "docs" / "DEPLOYMENT_RUNBOOK.md").read_text()

    assert "Day 6 deployment evidence ledger" in runbook
    assert "curl https://staging.regmind.co/api/version" in runbook
    assert "git_sha" in runbook
    assert "image_tag" in runbook
    assert "deploy-staging.yml" in runbook
    assert "regmind-staging" in runbook
    assert "regmind-backend" in runbook
    assert "/ecs/regmind-staging" in runbook
    assert "arie-backend/scripts/qa/day5_closing_smoke.py" in runbook
    assert "applications_report_v1" in runbook
    assert "regmind-staging:<REVISION>" in runbook


def test_day6_smoke_command_uses_token_env_not_literal_token():
    runbook = (ROOT / "docs" / "DEPLOYMENT_RUNBOOK.md").read_text()
    section = runbook.split("### Day 6 deployment evidence ledger", 1)[1].split("## 6. Rollback Procedure", 1)[0]

    assert 'BACKOFFICE_TOKEN="$STAGING_BACKOFFICE_TOKEN"' in section
    assert "--expected-sha" in section
    assert "--expected-total 22" in section
    assert "--expected-pending 21" in section
    assert "--expected-edd 1" in section
    assert "Do not paste bearer tokens" in section
    assert "--token " not in section
