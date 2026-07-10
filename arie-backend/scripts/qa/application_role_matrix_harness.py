#!/usr/bin/env python3
"""Staging-only Applications role-matrix seed and validation harness.

The harness creates clearly labelled fixture actors and applications, writes
credentials only to a local 0600 artifact, validates the deployed role matrix,
and can disable all synthetic accounts after the validation window.

It deliberately has no production mode and no committed credentials.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional
from urllib.error import HTTPError
from urllib.parse import quote, urlparse
from urllib.request import Request, urlopen


CONFIRM_TOKEN = "I-UNDERSTAND-STAGING-ROLE-AUDIT-WRITES"
ALLOW_ENV = "ALLOW_APPLICATION_ROLE_SEED"
ALLOWED_HOST_ENV = "ROLE_AUDIT_ALLOWED_DB_HOST"
OFFICER_ROLES = ("admin", "sco", "co", "analyst")
ALL_ROLES = (*OFFICER_ROLES, "client")
RUN_ID_RE = re.compile(r"^\d{8}T\d{6}Z-[0-9a-f]{6}$")
PRODUCTION_MARKERS = ("production", "prod-db", "prod_", "-prod", ".prod.")


def new_run_id() -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{stamp}-{secrets.token_hex(3)}"


def _stable_id(run_id: str, label: str, prefix: str) -> str:
    digest = hashlib.sha256(f"{run_id}:{label}".encode("utf-8")).hexdigest()[:16]
    return f"{prefix}_{digest}"


def _database_identity(database_url: str) -> tuple[str, str, str]:
    parsed = urlparse(database_url or "")
    return parsed.scheme.lower(), (parsed.hostname or "").lower(), parsed.path.lstrip("/").lower()


def enforce_staging_seed_guard(
    *,
    environment: Optional[str] = None,
    database_url: Optional[str] = None,
    allow_value: Optional[str] = None,
    confirm: Optional[str] = None,
    allowed_host: Optional[str] = None,
) -> None:
    """Fail closed unless the operator has identified one exact staging DB."""
    environment = str(environment if environment is not None else os.environ.get("ENVIRONMENT", "")).lower()
    database_url = database_url if database_url is not None else os.environ.get("DATABASE_URL", "")
    allow_value = allow_value if allow_value is not None else os.environ.get(ALLOW_ENV, "")
    allowed_host = allowed_host if allowed_host is not None else os.environ.get(ALLOWED_HOST_ENV, "")
    if environment != "staging":
        raise RuntimeError(f"REFUSED: ENVIRONMENT must be staging (got {environment or 'unset'}).")
    if allow_value != "1":
        raise RuntimeError(f"REFUSED: {ALLOW_ENV}=1 is required.")
    if confirm != CONFIRM_TOKEN:
        raise RuntimeError(f"REFUSED: --confirm must equal {CONFIRM_TOKEN!r}.")

    scheme, host, database_name = _database_identity(database_url)
    if scheme not in {"postgres", "postgresql"} or not host or not database_name:
        raise RuntimeError("REFUSED: DATABASE_URL must identify a PostgreSQL staging database.")
    identity = f"{host}/{database_name}"
    if any(marker in identity for marker in PRODUCTION_MARKERS):
        raise RuntimeError("REFUSED: DATABASE_URL contains a production marker.")
    if not allowed_host or host != allowed_host.strip().lower():
        raise RuntimeError(
            f"REFUSED: {ALLOWED_HOST_ENV} must exactly match DATABASE_URL host {host!r}."
        )


def enforce_staging_base_url(base_url: str) -> str:
    parsed = urlparse(base_url or "")
    host = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or not host or "staging" not in host:
        raise RuntimeError("REFUSED: validation base URL must be an HTTPS staging host.")
    if any(marker in host for marker in PRODUCTION_MARKERS):
        raise RuntimeError("REFUSED: validation host contains a production marker.")
    return base_url.rstrip("/")


def build_seed_plan(run_id: str) -> Dict[str, Any]:
    if not RUN_ID_RE.fullmatch(run_id):
        raise ValueError("run_id must match YYYYMMDDTHHMMSSZ-xxxxxx")

    actors: Dict[str, Dict[str, str]] = {}
    for role in OFFICER_ROLES:
        actors[role] = {
            "id": _stable_id(run_id, role, "ra_user"),
            "email": f"appaudit_role_{role}_{run_id.lower()}@example.test",
            "name": f"APPAUDIT_ROLE_{role.upper()}_{run_id}",
            "role": role,
            "type": "officer",
        }
    actors["client"] = {
        "id": _stable_id(run_id, "client", "ra_client"),
        "email": f"appaudit_role_client_{run_id.lower()}@example.test",
        "name": f"APPAUDIT_ROLE_CLIENT_{run_id}",
        "role": "client",
        "type": "client",
    }

    scenario_specs = (
        ("assigned_sco", "compliance_review", "MEDIUM", "sco"),
        ("assigned_co", "compliance_review", "LOW", "co"),
        ("assigned_analyst", "kyc_documents", "MEDIUM", "analyst"),
        ("unassigned", "compliance_review", "LOW", None),
        ("blocked_admin", "kyc_documents", "MEDIUM", "admin"),
        ("blocked_sco", "kyc_documents", "MEDIUM", "sco"),
        ("blocked_co", "kyc_documents", "MEDIUM", "co"),
        ("submitted_compliance", "submitted_to_compliance", "HIGH", "sco"),
        ("wrong_stage", "draft", "LOW", "co"),
        ("terminal_approved", "approved", "LOW", "sco"),
        ("client_owned", "draft", "LOW", None),
    )
    apps: Dict[str, Dict[str, Any]] = {}
    compact_stamp = run_id.split("-")[0]
    for index, (scenario, status_value, risk_level, assigned_role) in enumerate(scenario_specs, start=1):
        apps[scenario] = {
            "id": _stable_id(run_id, scenario, "ra_app"),
            "ref": f"ROLEAUDIT-{compact_stamp}-{index:02d}",
            "company_name": f"ROLEAUDIT-{run_id}-{scenario}",
            "status": status_value,
            "risk_level": risk_level,
            "risk_score": {"LOW": 20, "MEDIUM": 50, "HIGH": 78}[risk_level],
            "assigned_to": actors[assigned_role]["id"] if assigned_role else None,
            "assigned_role": assigned_role,
            "client_id": actors["client"]["id"],
            "is_fixture": True,
        }
    return {"run_id": run_id, "actors": actors, "applications": apps}


def _passwords_for(plan: Mapping[str, Any]) -> Dict[str, str]:
    return {role: f"{secrets.token_urlsafe(20)}!Aa7" for role in plan["actors"]}


def _secure_write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    fd = os.open(path, flags, stat.S_IRUSR | stat.S_IWUSR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
    except Exception:
        try:
            path.unlink()
        except OSError:
            pass
        raise


def _artifact_paths(out_dir: Optional[str], run_id: str) -> tuple[Path, Path]:
    root = Path(out_dir or tempfile.gettempdir()) / f"onboarda-role-audit-{run_id}"
    return root / "credentials.json", root / "seed-evidence.json"


def _insert_seed_rows(db, plan: Mapping[str, Any], passwords: Mapping[str, str]) -> None:
    import bcrypt

    for role in OFFICER_ROLES:
        actor = plan["actors"][role]
        password_hash = bcrypt.hashpw(passwords[role].encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        db.execute(
            "INSERT INTO users (id, email, password_hash, full_name, role, status) VALUES (?,?,?,?,?,'active')",
            (actor["id"], actor["email"], password_hash, actor["name"], role),
        )

    client = plan["actors"]["client"]
    client_hash = bcrypt.hashpw(passwords["client"].encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    db.execute(
        "INSERT INTO clients (id, email, password_hash, company_name, status) VALUES (?,?,?,?,'active')",
        (client["id"], client["email"], client_hash, client["name"]),
    )

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for scenario, app in plan["applications"].items():
        prescreening = json.dumps({
            "synthetic_test": True,
            "role_audit_run_id": plan["run_id"],
            "scenario": scenario,
        }, sort_keys=True)
        db.execute(
            """
            INSERT INTO applications
                (id, ref, client_id, company_name, country, sector, entity_type,
                 prescreening_data, risk_score, risk_level, final_risk_level,
                 onboarding_lane, status, assigned_to, screening_mode, is_fixture,
                 created_at, updated_at, inputs_updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                app["id"], app["ref"], app["client_id"], app["company_name"],
                "Mauritius", "Synthetic role audit", "company", prescreening,
                app["risk_score"], app["risk_level"], app["risk_level"],
                "standard", app["status"], app["assigned_to"], "sandbox", True,
                now, now, now,
            ),
        )
        db.execute(
            """
            INSERT INTO audit_log
                (user_id, user_name, user_role, action, target, application_id,
                 detail, ip_address, request_id)
            VALUES (?,?,?,?,?,?,?,?,?)
            """,
            (
                "role-audit-harness", "Application Role Matrix Harness", "system",
                "Role Audit Harness Seed", f"application:{app['id']}", app["id"],
                json.dumps({"run_id": plan["run_id"], "scenario": scenario, "synthetic": True}, sort_keys=True),
                "local-operator", f"role-audit-seed:{plan['run_id']}",
            ),
        )


def seed_staging(run_id: str, out_dir: Optional[str]) -> Dict[str, Any]:
    plan = build_seed_plan(run_id)
    passwords = _passwords_for(plan)
    credential_path, evidence_path = _artifact_paths(out_dir, run_id)
    credentials = {
        "run_id": run_id,
        "actors": {
            role: {"email": actor["email"], "password": passwords[role]}
            for role, actor in plan["actors"].items()
        },
    }
    evidence = {
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "environment": "staging",
        "synthetic_only": True,
        "credential_artifact": str(credential_path),
        "actors": list(plan["actors"].values()),
        "applications": list(plan["applications"].values()),
    }
    # Artifacts are durably and privately recorded before any database write.
    # If the transaction fails, both are removed so no false "seeded" evidence
    # or orphan credential artifact remains.
    _secure_write_json(credential_path, credentials)
    try:
        _secure_write_json(evidence_path, evidence)
    except Exception:
        credential_path.unlink(missing_ok=True)
        raise

    from db import get_db
    db = get_db()
    try:
        _insert_seed_rows(db, plan, passwords)
        db.commit()
    except Exception:
        db.rollback()
        credential_path.unlink(missing_ok=True)
        evidence_path.unlink(missing_ok=True)
        raise
    finally:
        db.close()

    return {"credentials": str(credential_path), "evidence": str(evidence_path), **evidence}


def _read_json(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as handle:
        value = json.load(handle)
    if not isinstance(value, dict):
        raise ValueError(f"Expected JSON object in {path}")
    return value


def disable_staging_users(manifest_path: str) -> Dict[str, Any]:
    manifest = _read_json(manifest_path)
    run_id = str(manifest.get("run_id") or "")
    if not RUN_ID_RE.fullmatch(run_id):
        raise RuntimeError("REFUSED: invalid or missing role-audit run_id in manifest.")
    actor_rows = manifest.get("actors") or []
    expected_names = {f"APPAUDIT_ROLE_{role.upper()}_{run_id}" for role in ALL_ROLES}
    if {str(row.get("name")) for row in actor_rows} != expected_names:
        raise RuntimeError("REFUSED: manifest actor set is not an exact role-audit harness set.")

    officer_ids = [row["id"] for row in actor_rows if row.get("type") == "officer"]
    client_ids = [row["id"] for row in actor_rows if row.get("type") == "client"]
    from db import get_db
    db = get_db()
    try:
        for actor_id in officer_ids:
            db.execute("UPDATE users SET status='inactive', updated_at=datetime('now') WHERE id=?", (actor_id,))
        for actor_id in client_ids:
            db.execute("UPDATE clients SET status='inactive' WHERE id=?", (actor_id,))
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
    return {"run_id": run_id, "disabled_officers": officer_ids, "disabled_clients": client_ids}


def _http_json(
    base_url: str,
    method: str,
    path: str,
    *,
    token: Optional[str] = None,
    payload: Optional[Mapping[str, Any]] = None,
) -> tuple[int, Any]:
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    headers = {"Accept": "application/json"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = Request(base_url + path, data=body, headers=headers, method=method)
    try:
        with urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else {}
    except HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            parsed = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            parsed = {"raw": raw[:500]}
        return exc.code, parsed


def _login(base_url: str, actor: Mapping[str, str], role: str) -> str:
    kind = "client" if role == "client" else "officer"
    code, payload = _http_json(
        base_url,
        "POST",
        f"/api/auth/{kind}/login",
        payload={"email": actor["email"], "password": actor["password"]},
    )
    if code != 200 or not payload.get("token"):
        raise RuntimeError(f"{role} login failed with HTTP {code}: {payload}")
    return payload["token"]


def validate_staging(base_url: str, manifest_path: str, credentials_path: str, out_path: str) -> Dict[str, Any]:
    base_url = enforce_staging_base_url(base_url)
    manifest = _read_json(manifest_path)
    credentials = _read_json(credentials_path)
    if manifest.get("run_id") != credentials.get("run_id"):
        raise RuntimeError("Manifest and credential artifacts belong to different runs.")

    applications = {row["company_name"].rsplit("-", 1)[-1]: row for row in manifest["applications"]}
    actor_credentials = credentials["actors"]
    tokens = {role: _login(base_url, actor_credentials[role], role) for role in ALL_ROLES}
    checks = []

    def check(role: str, name: str, method: str, path: str, expected: Iterable[int], payload=None):
        code, response = _http_json(base_url, method, path, token=tokens[role], payload=payload)
        passed = code in set(expected)
        checks.append({"role": role, "check": name, "method": method, "path": path, "status": code, "passed": passed})
        if not passed:
            raise RuntimeError(f"{role} {name} returned {code}: {response}")
        return code, response

    def assert_check(role: str, name: str, passed: bool, detail: str) -> None:
        checks.append({"role": role, "check": name, "status": "assertion", "passed": bool(passed), "detail": detail})
        if not passed:
            raise RuntimeError(f"{role} {name} failed: {detail}")

    prefix = quote(f"ROLEAUDIT-{manifest['run_id']}")
    for role in OFFICER_ROLES:
        app = applications["assigned_" + role] if role in {"sco", "co", "analyst"} else applications["unassigned"]
        app_id = app["id"]
        _, list_payload = check(
            role, "applications_list", "GET",
            f"/api/applications?q={prefix}&show_fixtures=true", {200},
        )
        listed_ids = {row.get("id") for row in list_payload.get("applications", [])}
        assert_check(role, "assigned_fixture_visible", app_id in listed_ids, f"expected {app_id} in synthetic list")
        check(role, "application_detail", "GET", f"/api/applications/{app_id}", {200})
        check(role, "documents", "GET", f"/api/applications/{app_id}/documents", {200})
        _, audit_payload = check(role, "activity_log", "GET", f"/api/applications/{app_id}/audit-log", {200})
        audit_ids = {row.get("application_id") for row in audit_payload.get("entries", [])}
        assert_check(
            role,
            "activity_log_immutable_scope",
            bool(audit_ids) and audit_ids == {app_id},
            f"observed application ids: {sorted(str(value) for value in audit_ids)}",
        )
        _, evidence_payload = check(role, "evidence_pack", "GET", f"/api/applications/{app_id}/evidence-pack", {200})
        evidence_app_id = (evidence_payload.get("scope") or {}).get("application_id")
        assert_check(
            role,
            "evidence_pack_immutable_scope",
            evidence_app_id == app_id,
            f"observed application id: {evidence_app_id}",
        )

    own_app = applications["client_owned"]
    cross_app = applications["assigned_co"]
    check("client", "backoffice_list_denied", "GET", "/api/applications", {403})
    _, client_detail = check("client", "own_shared_detail_safe", "GET", f"/api/applications/{own_app['id']}", {200})
    forbidden_detail = {"gate_blockers", "screening_reviews", "latest_memo_data", "decision_basis"}
    assert_check(
        "client", "own_detail_projection_excludes_officer_fields",
        not forbidden_detail.intersection(client_detail),
        "officer-only detail keys are absent",
    )
    _, client_documents = check(
        "client", "own_shared_documents_safe", "GET",
        f"/api/applications/{own_app['id']}/documents", {200},
    )
    forbidden_document = {"file_path", "verification_results", "review_comment", "evidence_class"}
    unsafe_doc_keys = sorted({
        key for row in client_documents if isinstance(row, dict)
        for key in forbidden_document.intersection(row)
    })
    assert_check(
        "client", "own_document_projection_excludes_officer_fields",
        not unsafe_doc_keys,
        f"unexpected keys: {unsafe_doc_keys}",
    )
    check("client", "cross_application_detail_denied", "GET", f"/api/applications/{cross_app['id']}", {403})
    check("client", "activity_log_denied", "GET", f"/api/applications/{own_app['id']}/audit-log", {403})
    check("client", "evidence_pack_denied", "GET", f"/api/applications/{own_app['id']}/evidence-pack", {403})

    signoff = {"acknowledged": True, "scope": "decision", "source_context": "ai_advisory"}
    for role in ("admin", "sco", "co"):
        blocked = applications[f"blocked_{role}"]
        _, before = check(
            role, "blocked_approval_before_state", "GET",
            f"/api/applications/{blocked['id']}", {200},
        )
        check(
            role,
            "blocked_approval_denied",
            "POST",
            f"/api/applications/{blocked['id']}/decision",
            {400, 403, 409},
            payload={"decision": "approve", "decision_reason": "Synthetic role harness blocked approval probe", "officer_signoff": signoff},
        )
        _, after = check(
            role, "blocked_approval_after_state", "GET",
            f"/api/applications/{blocked['id']}", {200},
        )
        unchanged = (
            before.get("status") == after.get("status")
            and before.get("decision_by") == after.get("decision_by")
            and before.get("decided_at") == after.get("decided_at")
        )
        assert_check(
            role,
            "blocked_approval_no_partial_mutation",
            unchanged,
            f"before status={before.get('status')} after status={after.get('status')}",
        )
    check(
        "analyst",
        "terminal_decision_denied",
        "POST",
        f"/api/applications/{applications['blocked_co']['id']}/decision",
        {403},
        payload={"decision": "approve", "decision_reason": "Synthetic role harness denial probe", "officer_signoff": signoff},
    )

    report = {
        "run_id": manifest["run_id"],
        "base_url": base_url,
        "validated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "synthetic_only": True,
        "credential_values_recorded": False,
        "checks": checks,
        "passed": all(item["passed"] for item in checks),
    }
    output = Path(out_path)
    _secure_write_json(output, report)
    return report


def run_browser_smoke(base_url: str, manifest_path: str, credentials_path: str, out_dir: str) -> Dict[str, Any]:
    """Run the existing authenticated browser smoke for SCO, CO, and analyst."""
    base_url = enforce_staging_base_url(base_url)
    manifest = _read_json(manifest_path)
    credentials = _read_json(credentials_path)
    if manifest.get("run_id") != credentials.get("run_id"):
        raise RuntimeError("Manifest and credential artifacts belong to different runs.")

    applications = {row["company_name"].rsplit("-", 1)[-1]: row for row in manifest["applications"]}
    script = Path(__file__).with_name("staging_browser_smoke.js")
    if not script.is_file():
        raise RuntimeError(f"Browser smoke script not found: {script}")

    root = Path(out_dir)
    results = []
    for role in ("sco", "co", "analyst"):
        actor = credentials["actors"][role]
        role_out = root / role
        env = os.environ.copy()
        env.update({
            "STAGING_BASE_URL": base_url,
            "STAGING_QA_EMAIL": actor["email"],
            "STAGING_QA_PASSWORD": actor["password"],
            "STAGING_SMOKE_APP_ID": applications[f"assigned_{role}"]["id"],
            "STAGING_SMOKE_OUT_DIR": str(role_out),
        })
        completed = subprocess.run(
            ["node", str(script)],
            env=env,
            check=False,
        )
        result = {
            "role": role,
            "exit_code": completed.returncode,
            "report": str(role_out / "report.json"),
            "application_id": applications[f"assigned_{role}"]["id"],
        }
        results.append(result)
        if completed.returncode != 0:
            raise RuntimeError(f"{role} browser smoke failed with exit code {completed.returncode}")

    summary = {
        "run_id": manifest["run_id"],
        "base_url": base_url,
        "credential_values_recorded": False,
        "roles": results,
        "passed": True,
    }
    _secure_write_json(root / "summary.json", summary)
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    seed = sub.add_parser("seed", help="create one staging-only synthetic role harness")
    seed.add_argument("--run-id", default=None)
    seed.add_argument("--out-dir", default=None)
    seed.add_argument("--confirm", required=True)

    disable = sub.add_parser("disable", help="disable every synthetic account in a seed manifest")
    disable.add_argument("--manifest", required=True)
    disable.add_argument("--confirm", required=True)

    validate = sub.add_parser("validate", help="run API role-matrix checks against staging")
    validate.add_argument("--base-url", default=os.environ.get("STAGING_BASE_URL", "https://staging.regmind.co"))
    validate.add_argument("--manifest", required=True)
    validate.add_argument("--credentials", required=True)
    validate.add_argument("--out", required=True)

    browser = sub.add_parser("browser", help="run SCO/CO/analyst authenticated browser smoke")
    browser.add_argument("--base-url", default=os.environ.get("STAGING_BASE_URL", "https://staging.regmind.co"))
    browser.add_argument("--manifest", required=True)
    browser.add_argument("--credentials", required=True)
    browser.add_argument("--out-dir", required=True)
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command in {"seed", "disable"}:
            enforce_staging_seed_guard(confirm=args.confirm)
        if args.command == "seed":
            result = seed_staging(args.run_id or new_run_id(), args.out_dir)
            print(json.dumps({
                "run_id": result["run_id"],
                "evidence": result["evidence"],
                "credentials": result["credentials"],
                "created_actor_count": len(result["actors"]),
                "created_application_count": len(result["applications"]),
            }, indent=2))
        elif args.command == "disable":
            print(json.dumps(disable_staging_users(args.manifest), indent=2))
        elif args.command == "validate":
            result = validate_staging(args.base_url, args.manifest, args.credentials, args.out)
            print(json.dumps({"run_id": result["run_id"], "passed": result["passed"], "evidence": args.out}, indent=2))
        else:
            result = run_browser_smoke(args.base_url, args.manifest, args.credentials, args.out_dir)
            print(json.dumps({
                "run_id": result["run_id"],
                "passed": result["passed"],
                "evidence": str(Path(args.out_dir) / "summary.json"),
            }, indent=2))
    except Exception as exc:
        print(f"application_role_matrix_harness: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
