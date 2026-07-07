"""SW-3 / ops-enforce-staging-sha-alignment-gate (code half).

The deploy-staging workflow must, before any validation counts, prove staging is
running the SHA just built: each service points at the task definition registered
for this commit, whose image tag + GIT_SHA + IMAGE_TAG env equal github.sha.

These tests (a) assert the gate step exists and is ordered before the health/
validation steps, and (b) extract the embedded SHA-comparison Python and run it
against aligned + every drift case so the logic itself is regression-locked.
"""
import json
import os
import re
import subprocess
import sys
import textwrap

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_WF = os.path.join(_REPO, ".github", "workflows", "deploy-staging.yml")

yaml = pytest.importorskip("yaml")


def _wf_text():
    with open(_WF, encoding="utf-8") as fh:
        return fh.read()


def _deploy_steps():
    doc = yaml.safe_load(_wf_text())
    return [s.get("name") for s in doc["jobs"]["deploy"]["steps"]]


def test_gate_step_present_and_ordered_before_validation():
    steps = _deploy_steps()
    assert "Enforce staging SHA alignment" in steps
    gate_i = steps.index("Enforce staging SHA alignment")
    # Must run after both service deploys and before health/portal validation.
    assert gate_i > steps.index("Deploy verification worker to ECS (rolling update)")
    assert gate_i < steps.index("Verify deployment health")
    assert gate_i < steps.index("Verify portal and backoffice")


def test_gate_checks_both_services_and_registered_task_defs():
    text = _wf_text()
    gate = text.split("Enforce staging SHA alignment", 1)[1].split("- name:", 1)[0]
    # Compares the live service task def against the ARN we registered this run.
    assert "register-task-def.outputs.task-definition-arn" in gate
    assert "register-worker-task-def.outputs.task-definition-arn" in gate
    assert 'assert_service_sha "$ECS_SERVICE"' in gate
    assert 'assert_service_sha "$ECS_WORKER_SERVICE"' in gate
    # Fails the run on drift.
    assert "exit 1" in gate


def test_optional_runtime_check_skips_without_secret():
    text = _wf_text()
    runtime = text.split("Confirm runtime /api/version SHA (optional)", 1)[1].split("\n      - name:", 1)[0]
    assert "STAGING_VERSION_BEARER_TOKEN" in runtime
    # Absent secret -> clean skip (exit 0), so the code half ships before ops
    # provisions the token.
    assert "skipping runtime /api/version check" in runtime
    assert "exit 0" in runtime


def _extract_gate_python():
    m = re.search(r"python3 - \"\$container_json\" <<'PY'\n(.*?)\n          PY\n",
                  _wf_text(), re.S)
    assert m, "embedded gate python heredoc not found"
    return textwrap.dedent(m.group(1))


@pytest.fixture()
def gate_script(tmp_path):
    p = tmp_path / "gate_check.py"
    p.write_text(_extract_gate_python())
    return str(p)


_EXP = "abc123def4567890"


def _run(gate_script, container):
    return subprocess.run(
        [sys.executable, gate_script, json.dumps(container)],
        env={"EXPECTED_SHA": _EXP, "SVC": "regmind-backend",
             "PATH": os.environ.get("PATH", "/usr/bin:/bin")},
        capture_output=True, text=True)


def _aligned():
    return {
        "image": f"acct.dkr.ecr.af-south-1.amazonaws.com/regmind-backend:{_EXP}",
        "environment": [
            {"name": "GIT_SHA", "value": _EXP},
            {"name": "IMAGE_TAG", "value": _EXP},
            {"name": "ENVIRONMENT", "value": "staging"},
        ],
    }


def test_gate_logic_passes_when_aligned(gate_script):
    r = _run(gate_script, _aligned())
    assert r.returncode == 0, r.stdout + r.stderr
    assert "OK" in r.stdout


def test_gate_logic_fails_on_stale_image_tag(gate_script):
    c = _aligned()
    c["image"] = c["image"].replace(_EXP, "OLDSHA")
    r = _run(gate_script, c)
    assert r.returncode == 1
    assert "image tag" in r.stdout


def test_gate_logic_fails_on_stale_git_sha_env(gate_script):
    c = _aligned()
    c["environment"][0]["value"] = "OLDSHA"
    r = _run(gate_script, c)
    assert r.returncode == 1
    assert "GIT_SHA env" in r.stdout


def test_gate_logic_fails_on_missing_env(gate_script):
    r = _run(gate_script, {"image": _aligned()["image"], "environment": []})
    assert r.returncode == 1
