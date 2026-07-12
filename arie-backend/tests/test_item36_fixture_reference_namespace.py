"""Item 36 collision-resistant synthetic-reference and cleanup coverage."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import db as db_module
from fixtures.cleanup import cleanup_registered_fixture
from fixtures.registry import APP_REF, NEGATIVE_PATH_FIXTURES
from fixtures.seeder import FixtureReferenceCollision, seed_all


BACKEND = Path(__file__).resolve().parents[1]
ITEM36_CODES = tuple(manifest["scenario_code"] for manifest in NEGATIVE_PATH_FIXTURES.values())


def _identity(manifest, role="root"):
    return json.dumps({
        "fixture": manifest["scenario_code"],
        "fixture_key": manifest["fixture_key"],
        "fixture_marker": manifest["marker"],
        "fixture_role": role,
        "source": "fixtures.seeder",
    })


def _insert_application(db, *, app_id, ref, is_fixture, prescreening_data="{}", company="Collision Sentinel Ltd"):
    db.execute(
        "INSERT INTO applications (id,ref,company_name,status,risk_level,is_fixture,prescreening_data) "
        "VALUES (?,?,?,?,?,?,?)",
        (app_id, ref, company, "in_review", "MEDIUM", is_fixture, prescreening_data),
    )
    db.commit()


def _count(db, sql, params=()):
    return int(db.execute(sql, params).fetchone()["n"])


def _item36_audit_count(db):
    predicates = " OR ".join("detail LIKE ?" for _ in ITEM36_CODES)
    return _count(
        db,
        f"SELECT count(*) AS n FROM audit_log WHERE user_id='fixture_seed' AND ({predicates})",
        tuple(f"%{code}%" for code in ITEM36_CODES),
    )


def test_reserved_namespace_is_exact_stable_and_outside_normal_generator():
    expected = {
        "SCEN-12": "FX-ITEM36-SCEN12-BLOCKERS",
        "SCEN-13": "FX-ITEM36-SCEN13-STALE-MEMO",
        "SCEN-14": "FX-ITEM36-SCEN14-STALE-RISK",
        "SCEN-15": "FX-ITEM36-SCEN15-MISSING-IDV",
        "SCEN-16": "FX-ITEM36-SCEN16-MISSING-DOCS",
        "SCEN-17": "FX-ITEM36-SCEN17-PENDING-RMI",
        "SCEN-18": "FX-ITEM36-SCEN18-SANCTIONS",
        "SCEN-19": "FX-ITEM36-SCEN19-PEP",
        "SCEN-20": "FX-ITEM36-SCEN20-PR-BLOCKED",
        "SCEN-21": "FX-ITEM36-SCEN21-PR-COMPLETED",
        "SCEN-22": "FX-ITEM36-SCEN22A-XCLIENT",
        "SCEN-23": "FX-ITEM36-SCEN23-REPLAY",
    }
    assert {code: APP_REF[code] for code in ITEM36_CODES} == expected
    all_refs = {manifest["synthetic_ref"] for manifest in NEGATIVE_PATH_FIXTURES.values()}
    all_refs.add(NEGATIVE_PATH_FIXTURES["similar-reference-cross-client"]["paired_synthetic_ref"])
    assert len(all_refs) == 13
    assert all(ref.startswith("FX-ITEM36-") for ref in all_refs)

    # Normal application generation is deliberately ARF-only. The synthetic
    # namespace does not require or widen any business ref validator.
    server_source = (BACKEND / "server.py").read_text(encoding="utf-8")
    assert 'f"ARF-{year}-' in server_source
    assert "FX-ITEM36" not in server_source
    assert "WHERE id = ? OR ref = ?" in server_source


def test_nonfixture_reference_collision_fails_before_any_mutation(temp_db):
    manifest = NEGATIVE_PATH_FIXTURES["active-approval-blockers"]
    db = db_module.get_db()
    _insert_application(
        db,
        app_id="real-collision-001",
        ref=manifest["synthetic_ref"],
        is_fixture=False,
        company="Real Record Must Remain Ltd",
    )
    before = dict(db.execute("SELECT * FROM applications WHERE id='real-collision-001'").fetchone())
    db.close()

    with pytest.raises(FixtureReferenceCollision, match="non-fixture application real-collision-001"):
        seed_all(dry_run=False, only=[manifest["scenario_code"]])

    db = db_module.get_db()
    after = dict(db.execute("SELECT * FROM applications WHERE id='real-collision-001'").fetchone())
    assert after == before
    assert _count(db, "SELECT count(*) AS n FROM applications WHERE is_fixture=?", (True,)) == 0
    assert _item36_audit_count(db) == 0
    db.execute("DELETE FROM applications WHERE id='real-collision-001'")
    db.commit()
    db.close()


def test_matching_fixture_identity_reseeds_without_fixed_primary_key(temp_db):
    manifest = NEGATIVE_PATH_FIXTURES["active-approval-blockers"]
    db = db_module.get_db()
    _insert_application(
        db,
        app_id="existing-random01",
        ref=manifest["synthetic_ref"],
        is_fixture=True,
        prescreening_data=_identity(manifest),
    )
    db.close()

    first = seed_all(dry_run=False, only=[manifest["scenario_code"]])[0]
    second = seed_all(dry_run=False, only=[manifest["scenario_code"]])[0]
    assert first["application_id"] == second["application_id"] == "existing-random01"
    db = db_module.get_db()
    assert _count(db, "SELECT count(*) AS n FROM applications WHERE ref=?", (manifest["synthetic_ref"],)) == 1
    cleanup_registered_fixture(db, manifest["fixture_key"], actor_id="pytest:item36-hotfix")
    db.close()


def test_fixture_key_or_marker_mismatch_fails_closed(temp_db):
    manifest = NEGATIVE_PATH_FIXTURES["active-approval-blockers"]
    wrong = json.loads(_identity(manifest))
    wrong["fixture_key"] = "different-fixture"
    db = db_module.get_db()
    _insert_application(
        db,
        app_id="fixture-mismatch1",
        ref=manifest["synthetic_ref"],
        is_fixture=True,
        prescreening_data=json.dumps(wrong),
    )
    db.close()

    with pytest.raises(FixtureReferenceCollision, match="different fixture identity"):
        seed_all(dry_run=False, only=[manifest["scenario_code"]])

    db = db_module.get_db()
    assert _count(db, "SELECT count(*) AS n FROM applications WHERE ref=?", (manifest["synthetic_ref"],)) == 1
    assert _item36_audit_count(db) == 0
    db.execute("DELETE FROM applications WHERE id='fixture-mismatch1'")
    db.commit()
    db.close()


def test_seed_twice_child_refs_and_sanctioned_cleanup_leave_zero_residue(temp_db):
    # This computed legacy ref represents the staging collision class. It must
    # remain untouched through seed, re-seed and cleanup.
    occupied_ref = f"ARF-{2026}-{900012}"
    db = db_module.get_db()
    _insert_application(
        db,
        app_id="preexisting-real1",
        ref=occupied_ref,
        is_fixture=False,
        company="Pre-existing Runtime Validation Ltd",
    )
    db.close()

    first = seed_all(dry_run=False, only=list(ITEM36_CODES))
    db = db_module.get_db()
    refs = [result["application_ref"] for result in first]
    refs.append(NEGATIVE_PATH_FIXTURES["similar-reference-cross-client"]["paired_synthetic_ref"])
    root_ids = [result["application_id"] for result in first]
    counts_before = {
        "applications": _count(db, "SELECT count(*) AS n FROM applications WHERE ref LIKE 'FX-ITEM36-%'"),
        "directors": _count(db, f"SELECT count(*) AS n FROM directors WHERE application_id IN ({','.join('?' for _ in root_ids)})", tuple(root_ids)),
        "rmi_requests": _count(db, f"SELECT count(*) AS n FROM rmi_requests WHERE application_id IN ({','.join('?' for _ in root_ids)})", tuple(root_ids)),
        "periodic_reviews": _count(db, f"SELECT count(*) AS n FROM periodic_reviews WHERE application_id IN ({','.join('?' for _ in root_ids)})", tuple(root_ids)),
        "decisions": _count(db, "SELECT count(*) AS n FROM decision_records WHERE application_ref LIKE 'FX-ITEM36-%'"),
        "audit": _item36_audit_count(db),
    }
    db.close()

    second = seed_all(dry_run=False, only=list(ITEM36_CODES))
    assert [row["application_id"] for row in second] == root_ids
    db = db_module.get_db()
    counts_after = {
        "applications": _count(db, "SELECT count(*) AS n FROM applications WHERE ref LIKE 'FX-ITEM36-%'"),
        "directors": _count(db, f"SELECT count(*) AS n FROM directors WHERE application_id IN ({','.join('?' for _ in root_ids)})", tuple(root_ids)),
        "rmi_requests": _count(db, f"SELECT count(*) AS n FROM rmi_requests WHERE application_id IN ({','.join('?' for _ in root_ids)})", tuple(root_ids)),
        "periodic_reviews": _count(db, f"SELECT count(*) AS n FROM periodic_reviews WHERE application_id IN ({','.join('?' for _ in root_ids)})", tuple(root_ids)),
        "decisions": _count(db, "SELECT count(*) AS n FROM decision_records WHERE application_ref LIKE 'FX-ITEM36-%'"),
        "audit": _item36_audit_count(db),
    }
    assert counts_after == counts_before
    assert counts_after["applications"] == 13
    decision = db.execute(
        "SELECT application_ref, key_flags FROM decision_records WHERE actor_user_id='fixture_seed'"
    ).fetchone()
    assert decision["application_ref"] == NEGATIVE_PATH_FIXTURES["already-consumed-approval"]["synthetic_ref"]
    assert NEGATIVE_PATH_FIXTURES["already-consumed-approval"]["marker"] in decision["key_flags"]

    for fixture_key in NEGATIVE_PATH_FIXTURES:
        cleanup_registered_fixture(db, fixture_key, actor_id="pytest:item36-hotfix")
    assert _count(db, "SELECT count(*) AS n FROM applications WHERE ref LIKE 'FX-ITEM36-%'") == 0
    assert _count(db, "SELECT count(*) AS n FROM clients WHERE email LIKE 'fix-scen22-%@fixture.invalid'") == 0
    assert _item36_audit_count(db) == 0
    survivor = db.execute("SELECT company_name, is_fixture FROM applications WHERE id='preexisting-real1'").fetchone()
    assert survivor["company_name"] == "Pre-existing Runtime Validation Ltd"
    assert not bool(survivor["is_fixture"])
    db.close()


def test_item36_sources_contain_no_legacy_reserved_refs():
    legacy_refs = {f"ARF-{2026}-{900000 + number}" for number in range(12, 24)}
    paths = (
        BACKEND / "fixtures" / "registry.py",
        BACKEND / "fixtures" / "seeder.py",
        BACKEND / "fixtures" / "cleanup.py",
        BACKEND / "tests" / "test_negative_path_fixtures.py",
    )
    for path in paths:
        source = path.read_text(encoding="utf-8")
        assert not any(ref in source for ref in legacy_refs), path
