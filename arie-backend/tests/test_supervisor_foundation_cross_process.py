"""Cross-process determinism: the same stored evidence in a fresh interpreter.

The reproducibility harness repeats assembly *within* one process, which cannot
catch nondeterminism that varies per interpreter — hash randomisation, iteration
order of a set built from process-dependent values, or a module-level cache
seeded at import.

This assembles the same row from a second, independent Python process and
requires byte-identical canonical JSON.

**Why a column is pinned first.** ``applications.inputs_updated_at`` carries a
database ``CURRENT_TIMESTAMP`` default, so two fixture *creations* seconds apart
produce genuinely different evidence. That is correct behaviour — the column is
evidence the memo-staleness gate reads — but it means a fair comparison must
hold the stored row constant and vary only the interpreter. The test therefore
seeds once, pins that column, and reads the same row twice.
"""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

from supervisor_foundation import assemble_application_bundle, canonical_json
from supervisor_foundation_fixtures import AS_OF, complete_standard

BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS_ROOT = os.path.join(BACKEND_ROOT, "tests")

_CHILD = textwrap.dedent(
    """
    import sqlite3, sys
    sys.path.insert(0, {backend!r})
    sys.path.insert(0, {tests!r})
    from supervisor_foundation import assemble_application_bundle, canonical_json

    conn = sqlite3.connect({db_path!r})
    conn.row_factory = sqlite3.Row
    bundle = assemble_application_bundle(conn, {app_id!r}, as_of={as_of!r})
    sys.stdout.write(canonical_json(bundle))
    """
)


def _pin_default_timestamp(db, application_id: str) -> None:
    """Hold the DB-default timestamp constant so only the process varies."""
    db.execute(
        "UPDATE applications SET inputs_updated_at = ? WHERE id = ?",
        ("2026-07-01 00:00:00", application_id),
    )
    db.commit()


def _assemble_in_subprocess(db_path: str, application_id: str) -> str:
    script = _CHILD.format(
        backend=BACKEND_ROOT,
        tests=TESTS_ROOT,
        db_path=db_path,
        app_id=application_id,
        as_of=AS_OF,
    )
    env = dict(os.environ, ENVIRONMENT="testing")
    env.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        cwd=BACKEND_ROOT,
        timeout=180,
    )
    if result.returncode != 0:
        pytest.fail(f"child assembly failed:\n{result.stderr[-2000:]}")
    return result.stdout


def test_bundle_is_byte_identical_in_a_separate_interpreter(db, temp_db):
    """Same row, second process, byte-identical canonical JSON."""
    application_id = complete_standard(db)
    _pin_default_timestamp(db, application_id)

    in_process = canonical_json(
        assemble_application_bundle(db, application_id, as_of=AS_OF)
    )
    child = _assemble_in_subprocess(temp_db, application_id)

    assert child, "child produced no output"
    assert child == in_process, (
        "the bundle differs between processes; something in the assembler "
        "depends on per-interpreter state"
    )


def test_two_child_processes_agree(db, temp_db):
    """Two independent interpreters, neither of them the seeding one."""
    application_id = complete_standard(db)
    _pin_default_timestamp(db, application_id)

    first = _assemble_in_subprocess(temp_db, application_id)
    second = _assemble_in_subprocess(temp_db, application_id)

    assert first == second


def test_hash_randomisation_does_not_change_the_bundle(db, temp_db):
    """Explicitly vary PYTHONHASHSEED, which differs per process by default.

    Set-iteration and dict-iteration order are the classic sources of
    per-process drift; this pins that they cannot reach the output.
    """
    application_id = complete_standard(db)
    _pin_default_timestamp(db, application_id)

    outputs = set()
    for seed in ("0", "1", "12345"):
        script = _CHILD.format(
            backend=BACKEND_ROOT,
            tests=TESTS_ROOT,
            db_path=temp_db,
            app_id=application_id,
            as_of=AS_OF,
        )
        env = dict(os.environ, ENVIRONMENT="testing", PYTHONHASHSEED=seed)
        env.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")
        result = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            env=env,
            cwd=BACKEND_ROOT,
            timeout=180,
        )
        if result.returncode != 0:
            pytest.fail(f"child failed with PYTHONHASHSEED={seed}:\n{result.stderr[-2000:]}")
        outputs.add(result.stdout)

    assert len(outputs) == 1, "bundle output varies with PYTHONHASHSEED"
