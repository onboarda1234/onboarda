"""Proof that the Supervisor foundation cannot write to any authoritative store.

Four independent proofs, because each has a different escape route:

1. **Write-blocking connection proxy** — every statement issued during a full
   assembly is inspected; anything other than ``SELECT`` raises, as does
   ``commit``, ``executemany`` and ``executescript``.
2. **Whole-database mutation diff** — a content checksum of every table is
   taken before and after a full assemble-and-review and must be unchanged.
3. **Static source scan** — no module in the package contains a write verb.
4. **Import-boundary scan** — no authoritative module imports the package, so
   authoritative runtime behaviour cannot depend on it.
"""

from __future__ import annotations

import ast
import hashlib
import pathlib
import re

import pytest

import supervisor_foundation
from supervisor_foundation import assemble_application_bundle, run_review
from supervisor_foundation.bundle import (
    BundleAssemblyError,
    _fetchall,
    _REVIEWED_READ_ONLY_SELECTS,
)
from supervisor_foundation_fixtures import AS_OF, SYNTHETIC_PROBES, complete_standard

PACKAGE_ROOT = pathlib.Path(supervisor_foundation.__file__).parent
BACKEND_ROOT = PACKAGE_ROOT.parent

#: Tables the foundation must never mutate.
PROTECTED_TABLES = (
    "applications",
    "documents",
    "compliance_memos",
    "decision_records",
    "edd_cases",
    "periodic_reviews",
    "monitoring_alerts",
    "screening_reviews",
    "screening_jobs",
    "audit_log",
    "supervisor_audit_log",
    "risk_config",
    "directors",
    "ubos",
    "intermediaries",
)

WRITE_STATEMENT = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|REPLACE|CREATE|DROP|ALTER|TRUNCATE|GRANT|VACUUM|PRAGMA)\b",
    re.IGNORECASE,
)

REVIEWED_READ_TABLES = {
    "applications",
    "decision_records",
    "directors",
    "documents",
    "edd_cases",
    "intermediaries",
    "periodic_reviews",
    "screening_reviews",
    "ubos",
}


class WriteAttempted(AssertionError):
    """The foundation attempted a mutating operation."""


class WriteBlockingConnection:
    """A connection proxy that permits ``SELECT`` and nothing else."""

    def __init__(self, connection):
        self._connection = connection
        self.statements: list[str] = []

    def execute(self, sql, params=()):
        self.statements.append(sql)
        if WRITE_STATEMENT.match(sql):
            raise WriteAttempted(f"mutating statement issued by the foundation: {sql!r}")
        return self._connection.execute(sql, params)

    def executemany(self, *args, **kwargs):
        raise WriteAttempted("executemany() is not available to the foundation")

    def executescript(self, *args, **kwargs):
        raise WriteAttempted("executescript() is not available to the foundation")

    def commit(self):
        raise WriteAttempted("commit() is not available to the foundation")

    def rollback(self):
        raise WriteAttempted("rollback() is not available to the foundation")

    def __getattr__(self, name):
        raise WriteAttempted(
            f"the foundation reached for connection attribute {name!r}; only "
            "execute() is permitted"
        )


def _database_checksum(db) -> str:
    """Content checksum across every protected table."""
    digest = hashlib.sha256()
    for table in PROTECTED_TABLES:
        try:
            rows = db.execute(f"SELECT * FROM {table}").fetchall()
        except Exception:  # table absent in this schema build
            digest.update(f"{table}:absent".encode("utf-8"))
            continue
        digest.update(f"{table}:{len(rows)}".encode("utf-8"))
        for row in sorted(repr(tuple(row)) for row in rows):
            digest.update(row.encode("utf-8"))
    return digest.hexdigest()


# ── 1. Write-blocking proxy ──────────────────────────────────────────


def test_assembly_issues_only_select_statements(db):
    application_id = complete_standard(db)
    proxy = WriteBlockingConnection(db)

    bundle = assemble_application_bundle(proxy, application_id, as_of=AS_OF)
    run_review(bundle, probes=SYNTHETIC_PROBES)

    assert proxy.statements, "no statements were captured — proxy was bypassed"
    for statement in proxy.statements:
        assert statement.lstrip().upper().startswith("SELECT"), statement


def test_proxy_actually_blocks_a_write(db):
    """The proxy is not vacuous: a write through it raises."""
    proxy = WriteBlockingConnection(db)
    with pytest.raises(WriteAttempted):
        proxy.execute("UPDATE applications SET status = 'approved' WHERE id = 'x'")
    with pytest.raises(WriteAttempted):
        proxy.commit()


def test_fetchall_manifest_contains_only_single_reviewed_selects():
    """Every driver-reachable query is one SELECT over an audited table."""

    assert len(_REVIEWED_READ_ONLY_SELECTS) == len(REVIEWED_READ_TABLES)
    discovered_tables = set()
    for query in _REVIEWED_READ_ONLY_SELECTS:
        assert ";" not in query
        assert query == " ".join(query.split())
        match = re.match(r"^SELECT \* FROM ([a-z_]+) WHERE ", query)
        assert match, query
        discovered_tables.add(match.group(1))
        assert not WRITE_STATEMENT.match(query)
    assert discovered_tables == REVIEWED_READ_TABLES


@pytest.mark.parametrize(
    "query",
    (
        "SELECT 1",
        "SELECT 1; UPDATE monitoring_alerts SET status = 'resolved'",
        "WITH candidate AS (SELECT 1) SELECT * FROM candidate",
    ),
)
def test_fetchall_rejects_unreviewed_or_multi_statement_sql(db, query):
    with pytest.raises(BundleAssemblyError, match="reviewed read-only manifest"):
        _fetchall(db, query, ())


# ── 2. Whole-database mutation diff ──────────────────────────────────


def test_full_run_leaves_every_protected_table_unchanged(db):
    application_id = complete_standard(db)

    before = _database_checksum(db)
    bundle = assemble_application_bundle(db, application_id, as_of=AS_OF)
    run_review(bundle, probes=SYNTHETIC_PROBES)
    after = _database_checksum(db)

    assert before == after, "the foundation mutated a protected table"


def test_checksum_detects_a_real_mutation(db):
    """The checksum is not vacuous: a real write moves it."""
    application_id = complete_standard(db)
    before = _database_checksum(db)
    db.execute(
        "UPDATE applications SET status = ? WHERE id = ?", ("approved", application_id)
    )
    db.commit()
    assert _database_checksum(db) != before


# ── 3. Static source scan ────────────────────────────────────────────


@pytest.mark.parametrize(
    "path", sorted(PACKAGE_ROOT.rglob("*.py")), ids=lambda p: p.name
)
def test_no_write_verb_in_source(path: pathlib.Path):
    """No module contains a mutating SQL verb or a commit call."""
    source = path.read_text(encoding="utf-8")

    # Strip docstrings and comments: the audit notes in adapters.py legitimately
    # discuss writes in prose.
    tree = ast.parse(source, filename=str(path))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                docstrings.add(doc)

    code_lines = []
    for line in source.splitlines():
        stripped = line.split("#", 1)[0]
        if any(fragment in stripped for fragment in ("INSERT INTO", "UPDATE ", "DELETE FROM")):
            if not any(stripped.strip() in doc for doc in docstrings):
                code_lines.append(stripped)

    offending = [
        line
        for line in code_lines
        if re.search(r"\b(INSERT INTO|UPDATE\s+\w+\s+SET|DELETE FROM)\b", line, re.I)
    ]
    assert not offending, f"{path.name} contains a write statement: {offending}"

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            assert node.func.attr not in {
                "commit",
                "executescript",
                "executemany",
            }, f"{path.name} calls {node.func.attr}() at line {node.lineno}"


# ── 4. Import boundary ───────────────────────────────────────────────


def _backend_modules() -> list[pathlib.Path]:
    return sorted(
        path
        for path in BACKEND_ROOT.glob("*.py")
        if path.name not in {"conftest.py"}
    )


def test_no_authoritative_module_imports_the_foundation():
    """Dependency direction is one-way: authoritative code must not depend on us.

    If an authoritative module imported the foundation, its runtime behaviour
    could change with a Supervisor edit — which the product boundary forbids.
    The regex covers ``supervisor_foundation.probes`` as a prefix match, so an
    authoritative module reaching for a probe fails here too.
    """
    importers = []
    for path in _backend_modules():
        source = path.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"^\s*(from|import)\s+supervisor_foundation", source, re.M):
            importers.append(path.name)
    assert not importers, (
        f"authoritative modules import supervisor_foundation: {importers}"
    )


def test_supervisor_package_does_not_import_the_foundation():
    """The gated ``supervisor/`` package must not depend on the foundation either."""
    importers = []
    for path in sorted((BACKEND_ROOT / "supervisor").glob("*.py")):
        source = path.read_text(encoding="utf-8", errors="ignore")
        if re.search(r"^\s*(from|import)\s+supervisor_foundation", source, re.M):
            importers.append(path.name)
    assert not importers, importers


def test_foundation_imports_only_read_only_authoritative_helpers():
    """Pin the authoritative modules the foundation is allowed to touch.

    Widening this set is a deliberate act: each addition must be re-audited for
    clock use and side effects.
    """
    # Each entry was audited for wall-clock use and side effects before being
    # admitted. Widening this set is a deliberate act requiring the same audit.
    #
    # ``screening_state`` (added for bundle v2): the module has exactly two
    # wall-clock call sites, ``derive_screening_truth`` and
    # ``build_screening_truth_summary``, and the assembler calls neither. The
    # four helpers it does use — ``provider_mode_from_record``,
    # ``derive_screening_state``, ``_entry_has_material_hit`` and
    # ``_entry_has_granular_material_fields`` — are clock-free and side-effect
    # free, which ``test_supervisor_foundation_bundle_v2`` re-asserts by AST
    # scan rather than by assertion here.
    #
    # Added for the Phase 0B-2 probes, all pure and clock-free:
    #   ``rule_engine`` — ``_is_valid_risk_config_version`` (string check) and
    #   two module constants. ``edd_routing_policy`` — ``evaluate_edd_routing``,
    #   a pure function of its fact dict. ``periodic_review_policy`` — the
    #   ``RISK_FREQUENCY_MONTHS`` table and ``frequency_months_for_risk``; the
    #   module's ``parse_review_date`` reads a clock and is prohibited, which
    #   ``test_supervisor_probe_p06_monitoring_requirement`` asserts by AST.
    #
    # ``branding`` is deliberately absent, and its absence is enforced rather
    # than incidental. Finding text is hashed into ``review_hash``, so reading a
    # deployment-configured brand name into a finding would make the hash a
    # function of configuration instead of a function of the canonical bundle
    # and policy version. Findings are product-neutral instead; see
    # ``test_supervisor_probe_determinism_under_branding``.
    permitted = {
        "company_registry",
        "document_reliance_gate",
        "edd_routing_policy",
        "environment",
        "memo_governance",
        "periodic_review_policy",
        "rule_engine",
        "screening_state",
        "supervisor_engine",
    }
    seen: set[str] = set()
    for path in sorted(PACKAGE_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                seen.add(node.module.split(".")[0])
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    seen.add(alias.name.split(".")[0])

    stdlib = {
        "__future__", "ast", "calendar", "dataclasses", "datetime", "decimal",
        "enum", "hashlib", "json", "math", "pathlib", "sys", "types", "typing",
    }
    authoritative = seen - stdlib - {"supervisor_foundation"}
    unexpected = sorted(authoritative - permitted)
    assert not unexpected, (
        f"foundation imports unaudited authoritative modules: {unexpected}"
    )
