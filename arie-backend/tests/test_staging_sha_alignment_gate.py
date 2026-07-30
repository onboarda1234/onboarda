"""Release-control guards for the staging SHA/digest alignment gate."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "deploy-staging.yml"
RUNTIME_CONTROL = (
    ROOT / "arie-backend" / "scripts" / "qa" / "staging_deployment_control.py"
)
def _workflow_text():
    return WORKFLOW.read_text(encoding="utf-8")


def _deploy_steps():
    import re

    return re.findall(
        r"^      - name: (.+)$",
        _workflow_text(),
        flags=re.MULTILINE,
    )


def test_gate_step_present_and_ordered_before_authenticated_validation():
    steps = _deploy_steps()
    gate = steps.index("Enforce staging SHA alignment")
    assert gate > steps.index("Deploy verification worker to ECS (rolling update)")
    assert gate < steps.index(
        "Collect mandatory authenticated staging release evidence"
    )
    assert gate < steps.index("Collect mandatory CloudWatch error review")


def test_gate_checks_both_services_exact_digest_counts_and_alb():
    workflow = _workflow_text()
    helper = RUNTIME_CONTROL.read_text(encoding="utf-8")
    gate = workflow.split("Enforce staging SHA alignment", 1)[1].split(
        "\n      - name:",
        1,
    )[0]
    assert "--backend-task-definition" in gate
    assert "--worker-task-definition" in gate
    assert '--digest "${{ steps.image.outputs.image_digest }}"' in gate
    assert "imageDigest" in helper
    assert "desired_count" in helper
    assert "pending_count" in helper
    assert "describe-target-health" in helper


def test_runtime_version_check_is_mandatory_and_cannot_skip_without_token():
    text = _workflow_text()
    block = text.split(
        "Collect mandatory authenticated staging release evidence",
        1,
    )[1].split("\n      - name:", 1)[0]
    assert "staging_release_evidence.py" in block
    assert "--strict" in block
    assert "--expected-environment staging" in block
    assert "exit 0" not in block
    assert "skipping" not in block.lower()
    assert "STAGING_VERSION_BEARER_TOKEN" not in block


def test_task_definitions_are_sourced_from_live_services():
    text = _workflow_text()
    assert "capture-predeploy" in text
    assert "backend_source_task_definition" in text
    assert "worker_source_task_definition" in text
    assert "--task-definition $ECS_TASK_FAMILY" not in text
    assert "--task-definition $ECS_WORKER_TASK_FAMILY" not in text
