from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
RUNBOOK = ROOT / "docs" / "DAY6_CLOSING_RUNBOOK.md"


def _read_runbook():
    return RUNBOOK.read_text()


def test_day6_closing_runbook_exists_and_keeps_pr210_out_of_scope():
    text = _read_runbook()

    assert "Day 6 Closing Runbook" in text
    assert "PR #210 remains open and out of scope" in text
    assert "do not merge, close, rebase, or retarget it" in text


def test_day6_closing_runbook_pins_merge_order_and_ci_gate():
    text = _read_runbook()

    assert "Temp DB import-order isolation" in text
    assert "Staging smoke workflow" in text
    assert "Browser KPI/export runtime validation" in text
    assert "Deployment observability closure" in text
    assert "Day 6 closing runbook" in text
    assert "all required checks are green" in text


def test_day6_closing_runbook_pins_staging_smoke_command_and_counts():
    text = _read_runbook()

    assert "arie-backend/scripts/qa/day5_closing_smoke.py" in text
    assert "--api-base https://staging.regmind.co/api" in text
    assert "--expected-sha" in text
    assert "--expected-total 22" in text
    assert "--expected-pending 21" in text
    assert "--expected-edd 1" in text
    assert 'BACKOFFICE_TOKEN="$STAGING_BACKOFFICE_TOKEN"' in text
    assert "--token-env" in text


def test_day6_closing_runbook_pins_observability_and_rollback_evidence():
    text = _read_runbook()

    assert "/api/version" in text
    assert "git_sha" in text
    assert "image_tag" in text
    assert "regmind-staging" in text
    assert "regmind-backend" in text
    assert "/ecs/regmind-staging" in text
    assert "regmind-staging:<REVISION>" in text
    assert "connection pool exhausted" in text
    assert "falling back to mock mode" in text


def test_day6_closing_runbook_pins_browser_and_export_gates():
    text = _read_runbook()

    assert 'Dashboard "In Progress" tile' in text
    assert 'KPI "In Progress Applications"' in text
    assert 'KPI "EDD Routing Rate"' in text
    assert "/api/reports/generate?format=csv" in text
    assert "X-Report-Record-Count" in text
    assert "X-Report-Field-List" in text
    assert "X-Report-Filename" in text
    assert "X-Report-Canonical-View" in text
