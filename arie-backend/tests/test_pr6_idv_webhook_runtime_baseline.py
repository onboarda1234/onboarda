"""PR-6 IDV webhook and runtime-baseline guards."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests._migration_idempotency_helpers import fresh_migration_db


def _load_script(script_name: str):
    path = Path(__file__).resolve().parents[1] / "scripts" / script_name
    spec = importlib.util.spec_from_file_location(script_name.replace(".py", ""), path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_runtime_baseline_alignment_detects_stale_worker_image():
    runtime = _load_script("staging_runtime_baseline.py")
    expected = "b061c52f147b6fa42398629bb2b5dd2502682f3d"
    summary = {
        "backend_service_name": "regmind-backend",
        "worker_service_name": "regmind-verification-worker",
        "services": [
            {
                "serviceName": "regmind-backend",
                "status": "ACTIVE",
                "desiredCount": 2,
                "runningCount": 2,
                "primaryRolloutState": "COMPLETED",
            },
            {
                "serviceName": "regmind-verification-worker",
                "status": "ACTIVE",
                "desiredCount": 6,
                "runningCount": 6,
                "primaryRolloutState": "COMPLETED",
            },
        ],
        "task_definitions": {
            "backend": {
                "containers": [{
                    "image_tag": expected,
                    "env_provenance": {"GIT_SHA": expected, "IMAGE_TAG": expected},
                }],
            },
            "worker": {
                "containers": [{
                    "image_tag": "15b281fa620d19c8a475f5d3e94e78edcf976f5a",
                    "env_provenance": {
                        "GIT_SHA": "15b281fa620d19c8a475f5d3e94e78edcf976f5a",
                        "IMAGE_TAG": "15b281fa620d19c8a475f5d3e94e78edcf976f5a",
                    },
                }],
            },
        },
    }

    alignment = runtime.evaluate_alignment(summary, expected)

    assert alignment["backend_service_healthy"] is True
    assert alignment["worker_service_healthy"] is True
    assert alignment["backend_image_matches_expected"] is True
    assert alignment["worker_image_matches_expected"] is False
    assert alignment["aligned"] is False


def test_runtime_baseline_redacts_secret_values_from_container_summary():
    runtime = _load_script("staging_runtime_baseline.py")
    summary = runtime.summarize_container({
        "name": "regmind-backend",
        "image": "example.dkr.ecr/regmind-backend:abc123",
        "environment": [
            {"name": "GIT_SHA", "value": "abc123"},
            {"name": "IMAGE_TAG", "value": "abc123"},
            {"name": "DATABASE_URL", "value": "postgres://secret"},
            {"name": "SUMSUB_SECRET_KEY", "value": "secret"},
        ],
        "secrets": [
            {"name": "DATABASE_URL", "valueFrom": "arn:secret:database"},
            {"name": "SUMSUB_SECRET_KEY", "valueFrom": "arn:secret:sumsub"},
        ],
        "logConfiguration": {
            "options": {
                "awslogs-group": "/ecs/regmind-staging",
                "awslogs-stream-prefix": "worker",
            },
        },
    })

    assert summary["image_tag"] == "abc123"
    assert summary["env_provenance"] == {"GIT_SHA": "abc123", "IMAGE_TAG": "abc123"}
    assert summary["secret_names"] == ["DATABASE_URL", "SUMSUB_SECRET_KEY"]
    rendered = json.dumps(summary)
    assert "postgres://secret" not in rendered
    assert "SUMSUB_SECRET_KEY" in rendered


def test_deploy_workflow_updates_verification_worker_with_same_sha_pinned_image():
    workflow = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "deploy-staging.yml"
    source = workflow.read_text(encoding="utf-8")

    assert "ECS_WORKER_SERVICE: regmind-verification-worker" in source
    assert "ECS_WORKER_TASK_FAMILY: regmind-verification-worker" in source
    assert "Register verification worker task definition with SHA-pinned image" in source
    assert "Deploy verification worker to ECS (rolling update)" in source
    assert 'upsert_env("GIT_SHA", os.environ["GIT_SHA"])' in source
    assert 'upsert_env("IMAGE_TAG", os.environ["IMAGE_TAG"])' in source
    assert "--service $ECS_WORKER_SERVICE" in source
    assert "steps.register-worker-task-def.outputs.task-definition-arn" in source


def test_verification_worker_smoke_processes_synthetic_job_without_provider_calls(tmp_path, monkeypatch):
    smoke = _load_script("verification_worker_smoke.py")

    with fresh_migration_db(tmp_path, monkeypatch) as db:
        result = smoke.run_smoke(
            db=db,
            run_id="unit01",
            worker_id="pr6-smoke-unit",
            cleanup=True,
        )

        assert result["status"] == "passed"
        assert result["provider_calls"] == "none"
        assert result["result"]["outcome"] == "succeeded"
        assert result["document_status"] == "verified"
        assert result["locked_by"] == "pr6-smoke-unit"
        assert result["cleanup"] == "completed"

        assert db.execute(
            "SELECT COUNT(*) AS c FROM applications WHERE id=?",
            (result["application_id"],),
        ).fetchone()["c"] == 0
        assert db.execute(
            "SELECT COUNT(*) AS c FROM clients WHERE id=?",
            (f"pr6_smoke_client_{result['run_id']}",),
        ).fetchone()["c"] == 0
        assert db.execute(
            "SELECT COUNT(*) AS c FROM verification_jobs WHERE id=?",
            (result["job_id"],),
        ).fetchone()["c"] == 0


def test_verification_worker_smoke_binds_document_is_current_as_boolean():
    smoke = _load_script("verification_worker_smoke.py")

    class _Cursor:
        def fetchone(self):
            return {}

    class _FakeDB:
        def __init__(self):
            self.calls = []

        def execute(self, sql, params=()):
            self.calls.append((sql, params))
            return _Cursor()

    db = _FakeDB()
    smoke._seed_smoke_records(db, "unitbool")

    document_insert = next(
        params for sql, params in db.calls
        if "INSERT INTO documents" in sql
    )
    assert document_insert[8] is True


def test_webhook_renorm_prescreening_loader_accepts_postgres_jsonb_dict():
    from screening_storage import _load_prescreening_data

    payload = {
        "screening_report": {
            "overall_flags": [],
            "sumsub_webhook": {"reviewResult": {"reviewAnswer": "GREEN"}},
        }
    }

    assert _load_prescreening_data(payload) == payload


def test_webhook_renorm_prescreening_loader_accepts_sqlite_json_text():
    from screening_storage import _load_prescreening_data

    payload = {
        "screening_report": {
            "overall_flags": [],
            "sumsub_webhook": {"reviewResult": {"reviewAnswer": "GREEN"}},
        }
    }

    assert _load_prescreening_data(json.dumps(payload)) == payload
