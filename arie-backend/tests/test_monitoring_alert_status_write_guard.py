"""Static fail-closed guard for direct Monitoring Alert status writes.

The guard deliberately reasons over the Python AST and the structure of each
Monitoring SQL statement. It does not simply grep source text: f-string holes
are retained as explicit dynamic markers, bound table-name constants are
resolved, and INSERT status values are correlated with their parameter tuple.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re
import textwrap


BACKEND = Path(__file__).resolve().parents[1]
_DYNAMIC_SQL_FRAGMENT = "__MONITORING_DYNAMIC_SQL_FRAGMENT__"

_APPROVED_DIRECT_WRITERS = {
    (
        "monitoring_alert_state_machine.py",
        "transition_alert_status",
    ): "Canonical runtime transition service.",
    (
        "monitoring_status_backfill.py",
        "apply_backfill",
    ): "PR #902 exact-manifest controlled backfill.",
    (
        "monitoring_status_backfill.py",
        "apply_rollback",
    ): "PR #902 exact-row guarded rollback.",
    (
        "fixtures/seeder.py",
        "_upsert_alert",
    ): "Non-production deterministic fixture maintenance.",
}

# These are not runtime transition paths. They intentionally converge
# synthetic/demo records to non-open states, so they cannot satisfy the
# fixed-open initial-INSERT rule. Keeping the exception at function granularity
# prevents a whole fixtures directory from becoming an unreviewed bypass.
_APPROVED_NON_OPEN_FIXTURE_INSERTS = {
    (
        "db.py",
        "_seed_monitoring_demo_data",
    ): "Demo-only sample data with explicit canonical lifecycle states.",
    (
        "fixtures/seeder.py",
        "_upsert_alert",
    ): "Deterministic non-production scenario fixture.",
    (
        "fixtures/pilot_canonical_seeder.py",
        "_upsert_monitoring",
    ): "Deterministic pilot canonical dataset fixture.",
}

# Exact dynamic-SQL exceptions. Each entry documents why a dynamic table or
# assignment shape is unrelated to uncontrolled Monitoring lifecycle writes,
# plus the exact number of statements expected in that function. The guard
# still inspects fixture helper call sites that target monitoring_alerts.
_APPROVED_DYNAMIC_SQL = {
    (
        "fixtures/pilot_canonical_seeder.py",
        "_upsert_monitoring",
    ): (
        "Synthetic Monitoring fixture convergence from a fixed local column "
        "manifest.",
        1,
    ),
    (
        "db.py",
        "_insert_supervisor_audit_row",
    ): (
        "Supervisor audit schema repair inserts a fixed, status-free audit "
        "column manifest into one of two internal audit tables.",
        1,
    ),
    (
        "db.py",
        "migrate_sqlite_to_postgres",
    ): (
        "Explicit operator-run whole-database copy preserves every source "
        "table/column; it is not a runtime Monitoring mutation path.",
        1,
    ),
    (
        "fixtures/pilot_canonical_seeder.py",
        "_upsert_people",
    ): (
        "Synthetic director/UBO convergence selects only those two fixed "
        "party tables and has no status column.",
        1,
    ),
    (
        "fixtures/seeder.py",
        "_insert_returning_id",
    ): (
        "Generic fixture insert helper has separate PostgreSQL and SQLite "
        "execute statements; Monitoring call sites are inspected by "
        "_record_helper_insert.",
        2,
    ),
    (
        "change_management.py",
        "_apply_person_change",
    ): (
        "Person table and writable field are both selected from fixed "
        "non-Monitoring allowlists.",
        1,
    ),
    (
        "db.py",
        "_ensure_status_enum_constraints",
    ): (
        "Startup constraint repair iterates a fixed manifest that does not "
        "contain monitoring_alerts.",
        1,
    ),
    (
        "gdpr_erasure.py",
        "_anonymise_application",
    ): (
        "GDPR erasure tables and columns come from the fixed regulated "
        "erasure manifest, which excludes Monitoring lifecycle status.",
        1,
    ),
    (
        "server.py",
        "_apply_officer_correction",
    ): (
        "Officer-correction table and fields are selected from fixed "
        "director/UBO/intermediary manifests.",
        1,
    ),
}

# SQL passed through a helper/wrapper cannot be reconstructed from the local
# AST. Those calls fail closed unless the exact function and exact statement
# count have been reviewed here.
_APPROVED_UNRESOLVED_SQL = {
    ("db.py", "execute"): ("DBConnection driver adapter.", 1),
    ("db.py", "executescript"): ("DBConnection SQLite script adapter.", 2),
    (
        "db.py",
        "_execute_sqlite_script_with_add_column_if_not_exists",
    ): ("Migration-script compatibility executor.", 1),
    ("db.py", "init_db"): ("Repository-owned fixed schema bootstrap.", 1),
    ("db.py", "_run_migrations"): ("Repository-owned migration runner.", 3),
    ("evidence_pack_export.py", "_rows"): ("Read-only query helper.", 1),
    ("fixtures/seeder.py", "_fetch_id"): ("Fixture-only read helper.", 1),
    ("fixtures/tier0c_b.py", "execute"): ("Fixture connection adapter.", 1),
    (
        "fixtures/tier0c_b.py",
        "executescript",
    ): ("Fixture SQLite script adapter.", 1),
    (
        "migrations/runner.py",
        "ensure_schema_version_table",
    ): ("Migration-ledger bootstrap statements.", 2),
    (
        "migrations/runner.py",
        "run_migration",
    ): ("Repository-owned migration script executor.", 1),
    (
        "monitoring_alert_state_machine.py",
        "_query_all",
    ): ("Canonical service read-only query helper.", 1),
    (
        "monitoring_alert_state_machine.py",
        "_query_one",
    ): ("Canonical service read-only query helper.", 1),
    ("monitoring_automation.py", "_fetchall"): ("Read-only query helper.", 1),
    ("monitoring_automation.py", "_fetchone"): ("Read-only query helper.", 1),
    (
        "production_controls.py",
        "init_production_controls",
    ): ("Repository-owned fixed schema bootstrap.", 1),
    (
        "screening_adverse_truth.py",
        "_load_rows",
    ): ("Read-only screening query helper.", 1),
    (
        "screening_storage.py",
        "ensure_normalized_table",
    ): ("Repository-owned fixed screening DDL.", 1),
    (
        "screening_storage.py",
        "ensure_provider_comparisons_table",
    ): ("Repository-owned fixed screening DDL.", 1),
    (
        "scripts/diagnose_pii_tokens.py",
        "apply_null_invalid_tokens",
    ): ("Explicit operator repair scoped to PII tokens.", 1),
    (
        "scripts/qa/monitoring_alert_runtime_audit.py",
        "_fetch",
    ): ("Read-only Monitoring audit query helper.", 1),
    ("sumsub_idv_status.py", "_fetchall_optional"): ("Read-only query helper.", 1),
    (
        "supervisor/agent_executors.py",
        "execute",
    ): ("Supervisor execution adapter around fixed agent SQL.", 1),
    ("supervisor/audit.py", "_scalar_count"): ("Read-only audit count helper.", 1),
}

# Narrow SQL identifier grammar for the repository's PostgreSQL/SQLite
# dialects. It supports unquoted identifiers plus the quoting forms accepted by
# those engines without treating arbitrary text as a table token.
_SQL_IDENTIFIER = (
    r"(?:"
    r"[A-Za-z_][A-Za-z0-9_$]*"
    r'|"(?:""|[^"])+"'
    r"|`(?:``|[^`])+`"
    r"|\[(?:\]\]|[^\]])+\]"
    r")"
)
_MONITORING_TABLE_NAME = (
    r'(?:monitoring_alerts|"monitoring_alerts"|'
    r"`monitoring_alerts`|\[monitoring_alerts\])"
)
_MONITORING_TABLE = (
    rf"(?:(?:{_SQL_IDENTIFIER})\s*\.\s*)?"
    rf"{_MONITORING_TABLE_NAME}"
)
_UPDATE_ALIAS = (
    rf"(?:\s+AS\s+{_SQL_IDENTIFIER}"
    rf"|\s+(?!SET\b){_SQL_IDENTIFIER})?"
)
_INSERT_ALIAS = (
    rf"(?:\s+AS\s+{_SQL_IDENTIFIER}"
    rf"|\s+{_SQL_IDENTIFIER})?"
)
_MONITORING_UPDATE_PREFIX = (
    rf"\bUPDATE\s+(?:ONLY\s+)?"
    rf"{_MONITORING_TABLE}{_UPDATE_ALIAS}\s+SET\b"
)
_MONITORING_INSERT_PREFIX = (
    rf"\bINSERT(?:\s+OR\s+[A-Z_]+)?\s+INTO\s+"
    rf"{_MONITORING_TABLE}{_INSERT_ALIAS}"
)
_STATUS_ASSIGNMENT = (
    rf"(?<![A-Za-z0-9_$])"
    rf"(?:(?:{_SQL_IDENTIFIER})\s*\.\s*)?"
    rf'(?:status|"status"|`status`|\[status\])\s*='
)
_STATUS_TUPLE_ASSIGNMENT = (
    r"\([^)]*(?<![A-Za-z0-9_$])"
    r'(?:status|"status"|`status`|\[status\])'
    r"[^)]*\)\s*="
)
_STATUS_WRITE_ASSIGNMENT = (
    rf"(?:{_STATUS_ASSIGNMENT}|{_STATUS_TUPLE_ASSIGNMENT})"
)

_STATUS_UPDATE = re.compile(
    _MONITORING_UPDATE_PREFIX
    + r"(?:(?!\bWHERE\b).)*"
    + _STATUS_WRITE_ASSIGNMENT,
    re.IGNORECASE | re.DOTALL,
)
_UPSERT_STATUS_UPDATE = re.compile(
    _MONITORING_INSERT_PREFIX
    + r".*?"
    r"\bON\s+CONFLICT\b.*?\bDO\s+UPDATE\s+SET\b.*?"
    + _STATUS_WRITE_ASSIGNMENT,
    re.IGNORECASE | re.DOTALL,
)
_MONITORING_MERGE_STATUS_UPDATE = re.compile(
    rf"\bMERGE\s+INTO\s+{_MONITORING_TABLE}"
    rf"(?:\s+(?:AS\s+)?{_SQL_IDENTIFIER})?\s+USING\b"
    r".*?\bWHEN\s+MATCHED\b.*?\bTHEN\s+UPDATE\s+SET\b.*?"
    + _STATUS_WRITE_ASSIGNMENT,
    re.IGNORECASE | re.DOTALL,
)
_MONITORING_INSERT = re.compile(
    _MONITORING_INSERT_PREFIX,
    re.IGNORECASE,
)
_MONITORING_UPDATE = re.compile(
    _MONITORING_UPDATE_PREFIX,
    re.IGNORECASE,
)
_PLACEHOLDER = re.compile(r"^(?:\?|%s)$", re.IGNORECASE)
_SQL_STRING_LITERAL = re.compile(r"^'((?:''|[^'])*)'$", re.DOTALL)


def _extract_parenthesized(text, opening_index):
    """Return ``(contents, next_index)`` for one SQL parenthesized block."""
    if opening_index >= len(text) or text[opening_index] != "(":
        return None
    depth = 0
    in_quote = False
    index = opening_index
    while index < len(text):
        char = text[index]
        if in_quote:
            if char == "'" and index + 1 < len(text) and text[index + 1] == "'":
                index += 2
                continue
            if char == "'":
                in_quote = False
        elif char == "'":
            in_quote = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[opening_index + 1:index], index + 1
        index += 1
    return None


def _split_sql_csv(text):
    """Split a SQL column/value list without splitting nested expressions."""
    values = []
    current = []
    depth = 0
    in_quote = False
    index = 0
    while index < len(text):
        char = text[index]
        if in_quote:
            current.append(char)
            if char == "'" and index + 1 < len(text) and text[index + 1] == "'":
                current.append(text[index + 1])
                index += 2
                continue
            if char == "'":
                in_quote = False
        elif char == "'":
            in_quote = True
            current.append(char)
        elif char == "(":
            depth += 1
            current.append(char)
        elif char == ")":
            depth -= 1
            current.append(char)
        elif char == "," and depth == 0:
            values.append("".join(current).strip())
            current = []
        else:
            current.append(char)
        index += 1
    values.append("".join(current).strip())
    return values


def _monitoring_insert_parts(sql):
    """Return explicit Monitoring INSERT columns/value expressions, if any."""
    match = _MONITORING_INSERT.search(sql)
    if not match:
        return None
    index = match.end()
    while index < len(sql) and sql[index].isspace():
        index += 1
    columns_block = _extract_parenthesized(sql, index)
    if columns_block is None:
        return (None, None)
    columns_text, after_columns = columns_block
    values_match = re.search(r"\bVALUES\b", sql[after_columns:], re.IGNORECASE)
    if not values_match:
        return (None, None)
    values_open = after_columns + values_match.end()
    while values_open < len(sql) and sql[values_open].isspace():
        values_open += 1
    values_block = _extract_parenthesized(sql, values_open)
    if values_block is None:
        return (None, None)
    values_text, _after_values = values_block
    columns = [
        item.strip().strip('"`[]').lower()
        for item in _split_sql_csv(columns_text)
    ]
    return columns, _split_sql_csv(values_text)


def _dynamic_structural_monitoring_sql(sql):
    """True when interpolation could conceal a Monitoring status write."""
    if _DYNAMIC_SQL_FRAGMENT not in sql:
        return False

    # A fully dynamic table token cannot be matched against the literal
    # monitoring_alerts grammar. Treat it as Monitoring-capable whenever the
    # statement can write a status field; callers must use a fixed table token
    # (or receive a narrowly reviewed function exception) to prove otherwise.
    update_prefix = re.search(
        r"\bUPDATE\s+(?:ONLY\s+)?(?P<target>.*?)\bSET\b",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    if (
        update_prefix
        and _DYNAMIC_SQL_FRAGMENT in update_prefix.group("target")
    ):
        tail = sql[update_prefix.end():]
        boundary = re.search(
            r"\b(?:WHERE|RETURNING)\b",
            tail,
            re.IGNORECASE,
        )
        assignment_region = tail[:boundary.start()] if boundary else tail
        if (
            _DYNAMIC_SQL_FRAGMENT in assignment_region
            or re.search(
                _STATUS_WRITE_ASSIGNMENT,
                assignment_region,
                re.IGNORECASE,
            )
        ):
            return True

    insert_prefix = re.search(
        r"\bINSERT(?:\s+OR\s+[A-Z_]+)?\s+INTO\s+",
        sql,
        re.IGNORECASE,
    )
    if insert_prefix:
        statement = sql[insert_prefix.end():]
        structure = re.search(
            r"\(|\b(?:VALUES|SELECT|DEFAULT)\b",
            statement,
            re.IGNORECASE,
        )
        target_region = (
            statement[:structure.start()] if structure else statement
        )
        if _DYNAMIC_SQL_FRAGMENT in target_region:
            # Without an explicit column list the guard cannot prove which
            # positional field receives a value, so fail closed.
            if structure is None or structure.group(0) != "(":
                return True
            columns_block = _extract_parenthesized(
                statement,
                structure.start(),
            )
            if columns_block is None:
                return True
            columns_text, after_columns = columns_block
            columns = [
                item.strip().strip('"`[]').lower()
                for item in _split_sql_csv(columns_text)
            ]
            if (
                _DYNAMIC_SQL_FRAGMENT in columns_text
                or "status" in columns
            ):
                return True
            # A fixed insert column list without status is safe only when an
            # upsert clause cannot mutate status afterward.
            if re.search(
                r"\bON\s+CONFLICT\b.*?\bDO\s+UPDATE\s+SET\b.*?"
                + _STATUS_WRITE_ASSIGNMENT,
                statement[after_columns:],
                re.IGNORECASE | re.DOTALL,
            ):
                return True

    update = _MONITORING_UPDATE.search(sql)
    if update:
        tail = sql[update.end():]
        boundary = re.search(r"\b(?:WHERE|RETURNING)\b", tail, re.IGNORECASE)
        assignment_region = tail[:boundary.start()] if boundary else tail
        if _DYNAMIC_SQL_FRAGMENT in assignment_region:
            return True

    merge_prefix = re.search(
        r"\bMERGE\s+INTO\s+(?P<target>.*?)\bUSING\b",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    if (
        merge_prefix
        and _DYNAMIC_SQL_FRAGMENT in merge_prefix.group("target")
    ):
        merge_tail = sql[merge_prefix.end():]
        if (
            _DYNAMIC_SQL_FRAGMENT in merge_tail
            or re.search(
                r"\bWHEN\s+MATCHED\b.*?\bTHEN\s+UPDATE\s+SET\b.*?"
                + _STATUS_WRITE_ASSIGNMENT,
                merge_tail,
                re.IGNORECASE | re.DOTALL,
            )
        ):
            return True

    insert = _MONITORING_INSERT.search(sql)
    if insert:
        # A hole in the column/value or conflict-assignment structure can hide
        # either an explicit non-open initial status or an upsert overwrite.
        statement = sql[insert.start():]
        if _DYNAMIC_SQL_FRAGMENT in statement:
            return True
    return False


class _SqlWriteVisitor(ast.NodeVisitor):
    def __init__(self, relative_path):
        self.relative_path = relative_path
        self.functions = []
        self._string_bindings = [{}]
        self._expression_bindings = [{}]
        self.writes = []
        self.upserts = []
        self.dynamic_risks = []
        self.unresolved_risks = []
        self.invalid_inserts = []
        self.verified_open_inserts = []

    def _location(self, node):
        return (
            self.relative_path,
            self.functions[-1] if self.functions else "<module>",
            node.lineno,
        )

    def _lookup(self, scopes, name):
        for scope in reversed(scopes):
            if name in scope:
                return scope[name]
        return None

    def _shape(self, node):
        """Return ``(text, has_dynamic_hole)`` for a SQL string expression."""
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value, False
        if isinstance(node, ast.Name):
            return self._lookup(self._string_bindings, node.id)
        if isinstance(node, ast.JoinedStr):
            pieces = []
            dynamic = False
            for value in node.values:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    pieces.append(value.value)
                    continue
                if isinstance(value, ast.FormattedValue):
                    resolved = self._shape(value.value)
                    if resolved is not None and not resolved[1]:
                        pieces.append(resolved[0])
                    elif (
                        isinstance(value.value, ast.Constant)
                        and value.value.value is not None
                    ):
                        pieces.append(str(value.value.value))
                    else:
                        pieces.append(_DYNAMIC_SQL_FRAGMENT)
                        dynamic = True
                    continue
                pieces.append(_DYNAMIC_SQL_FRAGMENT)
                dynamic = True
            return "".join(pieces), dynamic
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self._shape(node.left)
            right = self._shape(node.right)
            if left is not None and right is not None:
                return left[0] + right[0], left[1] or right[1]
            if left is not None:
                return left[0] + _DYNAMIC_SQL_FRAGMENT, True
            if right is not None:
                return _DYNAMIC_SQL_FRAGMENT + right[0], True
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "format"
        ):
            base = self._shape(node.func.value)
            if base is not None:
                formatted = re.sub(
                    r"\{[^{}]*\}",
                    _DYNAMIC_SQL_FRAGMENT,
                    base[0],
                )
                return formatted, True
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod):
            base = self._shape(node.left)
            if base is not None:
                formatted = re.sub(
                    r"%(?:\([^)]+\))?[#0 +\-]?(?:\d+|\*)?(?:\.\d+)?[a-zA-Z]",
                    _DYNAMIC_SQL_FRAGMENT,
                    base[0],
                )
                return formatted, True
        return None

    def _resolve_expression(self, node):
        seen = set()
        while isinstance(node, ast.Name) and node.id not in seen:
            seen.add(node.id)
            resolved = self._lookup(self._expression_bindings, node.id)
            if resolved is None:
                break
            node = resolved
        return node

    def _literal_string(self, node):
        node = self._resolve_expression(node)
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        shape = self._shape(node)
        if shape is not None and not shape[1]:
            return shape[0]
        return None

    def _tuple_item(self, node, index):
        node = self._resolve_expression(node)
        if not isinstance(node, (ast.Tuple, ast.List)):
            return None
        position = 0
        for item in node.elts:
            if isinstance(item, ast.Starred):
                return None
            if position == index:
                return self._resolve_expression(item)
            position += 1
        return None

    def _status_parameter_is_fixed_open(self, call, values, status_index):
        token = values[status_index].strip()
        literal = _SQL_STRING_LITERAL.match(token)
        if literal:
            return literal.group(1).replace("''", "'") == "open"
        if not _PLACEHOLDER.match(token):
            return False

        placeholder_index = sum(
            bool(_PLACEHOLDER.match(value.strip()))
            for value in values[:status_index]
        )
        if len(call.args) < 2:
            return False
        status_argument = self._tuple_item(call.args[1], placeholder_index)
        return self._literal_string(status_argument) == "open"

    def _record_insert(self, call, sql, location):
        parts = _monitoring_insert_parts(sql)
        if parts is None:
            return
        columns, values = parts
        if columns is None or values is None:
            self.invalid_inserts.append(location)
            return
        if _DYNAMIC_SQL_FRAGMENT in ",".join(columns):
            self.invalid_inserts.append(location)
            return
        if "status" not in columns:
            # The database default is canonical open. No explicit lifecycle
            # state is being smuggled through this INSERT.
            return
        status_index = columns.index("status")
        if status_index >= len(values):
            self.invalid_inserts.append(location)
            return
        if self._status_parameter_is_fixed_open(call, values, status_index):
            self.verified_open_inserts.append(location)
        else:
            self.invalid_inserts.append(location)

    def _record_helper_insert(self, call):
        """Inspect fixture helper calls whose SQL is constructed indirectly."""
        name = getattr(call.func, "id", "")
        if name != "_insert_returning_id" or len(call.args) < 4:
            return
        table = self._literal_string(call.args[1])
        if str(table or "").strip().lower() != "monitoring_alerts":
            return
        location = self._location(call)
        columns_text = self._literal_string(call.args[2])
        if columns_text is None:
            self.invalid_inserts.append(location)
            return
        columns = [
            item.strip().strip('"`[]').lower()
            for item in _split_sql_csv(columns_text)
        ]
        if "status" not in columns:
            return
        status_argument = self._tuple_item(
            call.args[3],
            columns.index("status"),
        )
        if self._literal_string(status_argument) == "open":
            self.verified_open_inserts.append(location)
        else:
            self.invalid_inserts.append(location)

    def visit_FunctionDef(self, node):
        self.functions.append(node.name)
        self._string_bindings.append({})
        self._expression_bindings.append({})
        for statement in node.body:
            self.visit(statement)
        self._expression_bindings.pop()
        self._string_bindings.pop()
        self.functions.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_Assign(self, node):
        shape = self._shape(node.value)
        for target in node.targets:
            if isinstance(target, ast.Name):
                self._expression_bindings[-1][target.id] = node.value
                if shape is not None:
                    self._string_bindings[-1][target.id] = shape
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        if node.value is not None and isinstance(node.target, ast.Name):
            shape = self._shape(node.value)
            self._expression_bindings[-1][node.target.id] = node.value
            if shape is not None:
                self._string_bindings[-1][node.target.id] = shape
        self.generic_visit(node)

    def visit_AugAssign(self, node):
        if isinstance(node.target, ast.Name) and isinstance(node.op, ast.Add):
            previous = self._lookup(self._string_bindings, node.target.id)
            appended = self._shape(node.value)
            if previous is not None:
                if appended is None:
                    appended = (_DYNAMIC_SQL_FRAGMENT, True)
                self._string_bindings[-1][node.target.id] = (
                    previous[0] + appended[0],
                    previous[1] or appended[1],
                )
            else:
                self._string_bindings[-1].pop(node.target.id, None)
            self._expression_bindings[-1].pop(node.target.id, None)
        self.generic_visit(node)

    def visit_Call(self, node):
        self._record_helper_insert(node)
        method = getattr(node.func, "attr", "")
        if method in {"execute", "executemany", "executescript"} and node.args:
            shape = self._shape(node.args[0])
            if shape is None:
                self.unresolved_risks.append(self._location(node))
            else:
                sql, _dynamic = shape
                location = self._location(node)
                if _dynamic_structural_monitoring_sql(sql):
                    self.dynamic_risks.append(location)
                if (
                    _STATUS_UPDATE.search(sql)
                    or _UPSERT_STATUS_UPDATE.search(sql)
                    or _MONITORING_MERGE_STATUS_UPDATE.search(sql)
                ):
                    self.writes.append(location)
                if _UPSERT_STATUS_UPDATE.search(sql):
                    self.upserts.append(location)
                self._record_insert(node, sql, location)
        self.generic_visit(node)


def _production_python_files():
    for path in sorted(BACKEND.rglob("*.py")):
        relative = path.relative_to(BACKEND)
        if "tests" in relative.parts or "__pycache__" in relative.parts:
            continue
        yield path, relative.as_posix()


def _scan_production():
    combined = _SqlWriteVisitor("<combined>")
    for path, relative in _production_python_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = _SqlWriteVisitor(relative)
        visitor.visit(tree)
        for attribute in (
            "writes",
            "upserts",
            "dynamic_risks",
            "unresolved_risks",
            "invalid_inserts",
            "verified_open_inserts",
        ):
            getattr(combined, attribute).extend(getattr(visitor, attribute))
    return combined


def _scan_source(source, relative_path="synthetic_guard_probe.py"):
    visitor = _SqlWriteVisitor(relative_path)
    visitor.visit(ast.parse(textwrap.dedent(source), filename=relative_path))
    return visitor


def _unexpected(findings, allowlist):
    return [
        location
        for location in findings
        if location[:2] not in allowlist
    ]


def test_all_direct_runtime_status_writes_are_narrowly_allowlisted():
    visitor = _scan_production()
    discovered_locations = {
        (path, function)
        for path, function, _line in visitor.writes
    }
    unexpected = _unexpected(visitor.writes, _APPROVED_DIRECT_WRITERS)
    assert unexpected == [], (
        "Monitoring Alert status writes must use transition_alert_status; "
        f"unexpected direct writes: {unexpected}"
    )
    assert discovered_locations == set(_APPROVED_DIRECT_WRITERS), (
        "The direct-write allowlist must remain exact and reviewed. "
        f"discovered={sorted(discovered_locations)} "
        f"allowlisted={sorted(_APPROVED_DIRECT_WRITERS)}"
    )
    assert len(visitor.writes) == len(_APPROVED_DIRECT_WRITERS), (
        "Each direct-writer exception may contain exactly one status write; "
        f"discovered writes: {visitor.writes}"
    )


def test_runtime_upserts_never_overwrite_existing_lifecycle_status():
    assert _scan_production().upserts == []


def test_dynamic_status_capable_sql_is_rejected_except_exact_functions():
    visitor = _scan_production()
    unexpected = _unexpected(
        visitor.dynamic_risks,
        _APPROVED_DYNAMIC_SQL,
    )
    assert unexpected == [], (
        "Dynamic Monitoring INSERT/UPDATE structure can conceal a status "
        f"write; unexpected dynamic SQL: {unexpected}"
    )
    discovered = {
        (path, function)
        for path, function, _line in visitor.dynamic_risks
    }
    assert discovered == set(_APPROVED_DYNAMIC_SQL), (
        "Dynamic SQL exceptions must remain exact. "
        f"discovered={sorted(discovered)} "
        f"allowlisted={sorted(_APPROVED_DYNAMIC_SQL)}"
    )
    expected_count = sum(
        statement_count
        for _reason, statement_count in _APPROVED_DYNAMIC_SQL.values()
    )
    assert len(visitor.dynamic_risks) == expected_count, (
        "Each dynamic SQL exception must retain its reviewed statement count; "
        f"discovered risks: {visitor.dynamic_risks}"
    )


def test_unresolved_sql_is_rejected_except_exact_reviewed_functions():
    visitor = _scan_production()
    unexpected = _unexpected(
        visitor.unresolved_risks,
        _APPROVED_UNRESOLVED_SQL,
    )
    assert unexpected == [], (
        "Unresolved execute SQL must be statically reconstructed or receive "
        f"an exact reviewed exception: {unexpected}"
    )
    discovered = {
        (path, function)
        for path, function, _line in visitor.unresolved_risks
    }
    assert discovered == set(_APPROVED_UNRESOLVED_SQL), (
        "Unresolved SQL exceptions must remain exact. "
        f"discovered={sorted(discovered)} "
        f"allowlisted={sorted(_APPROVED_UNRESOLVED_SQL)}"
    )
    expected_count = sum(
        statement_count
        for _reason, statement_count in _APPROVED_UNRESOLVED_SQL.values()
    )
    assert len(visitor.unresolved_risks) == expected_count, (
        "Each unresolved SQL exception must retain its reviewed statement "
        f"count; discovered risks: {visitor.unresolved_risks}"
    )


def test_status_inserts_are_fixed_open_except_exact_demo_fixture_functions():
    visitor = _scan_production()
    unexpected = _unexpected(
        visitor.invalid_inserts,
        _APPROVED_NON_OPEN_FIXTURE_INSERTS,
    )
    assert unexpected == [], (
        "Monitoring Alert INSERT status must be a statically proven canonical "
        f"'open'; unexpected INSERTs: {unexpected}"
    )
    discovered = {
        (path, function)
        for path, function, _line in visitor.invalid_inserts
    }
    assert discovered == set(_APPROVED_NON_OPEN_FIXTURE_INSERTS), (
        "Demo/fixture INSERT exception must remain exact. "
        f"discovered={sorted(discovered)} "
        f"allowlisted={sorted(_APPROVED_NON_OPEN_FIXTURE_INSERTS)}"
    )
    assert len(visitor.invalid_inserts) == len(
        _APPROVED_NON_OPEN_FIXTURE_INSERTS
    ), (
        "Each demo/fixture exception may cover exactly one non-open INSERT; "
        f"discovered INSERTs: {visitor.invalid_inserts}"
    )
    assert visitor.verified_open_inserts, (
        "Guard did not inspect any fixed-open production Monitoring INSERT"
    )


def test_dynamic_fstring_update_cannot_erase_status_assignment_from_guard():
    visitor = _scan_source(
        """
        def malicious(db, assignment):
            table = "monitoring_alerts"
            db.execute(
                f"UPDATE {table} SET {assignment} WHERE id = ?",
                (17,),
            )
        """
    )
    assert {
        (path, function)
        for path, function, _line in visitor.dynamic_risks
    } == {("synthetic_guard_probe.py", "malicious")}


def test_fully_dynamic_table_target_cannot_hide_status_update_or_insert():
    visitor = _scan_source(
        """
        def malicious_update(db, table):
            db.execute(
                f"UPDATE {table} SET status = ? WHERE id = ?",
                ("resolved", 17),
            )

        def malicious_qualified_update(db, schema):
            db.execute(
                f"UPDATE {schema}.monitoring_alerts "
                "SET status = ? WHERE id = ?",
                ("resolved", 18),
            )

        def malicious_insert(db, table):
            db.execute(
                f"INSERT INTO {table} (id, status, summary) VALUES (?, ?, ?)",
                (19, "resolved", "bypass"),
            )

        def malicious_positional_insert(db, table):
            db.execute(
                f"INSERT INTO {table} VALUES (?, ?, ?)",
                (20, "resolved", "bypass"),
            )
        """
    )
    assert {
        (path, function)
        for path, function, _line in visitor.dynamic_risks
    } == {
        ("synthetic_guard_probe.py", "malicious_update"),
        ("synthetic_guard_probe.py", "malicious_qualified_update"),
        ("synthetic_guard_probe.py", "malicious_insert"),
        ("synthetic_guard_probe.py", "malicious_positional_insert"),
    }


def test_unresolved_concat_augassign_and_combined_dynamic_sql_fail_closed():
    visitor = _scan_source(
        """
        def built(db):
            sql = build_sql("monitoring_alerts", "status", "resolved")
            db.execute(sql)

        def concatenated(db, table):
            sql = "UPDATE " + table + " SET status = ? WHERE id = ?"
            db.execute(sql, ("resolved", 1))

        def augmented(db, assignment):
            sql = "UPDATE monitoring_alerts SET "
            sql += assignment
            db.execute(sql)

        def combined(db, table, assignment):
            db.execute(
                f"UPDATE {table} SET {assignment} WHERE id = ?",
                (1,),
            )
        """
    )
    assert {
        (path, function)
        for path, function, _line in visitor.unresolved_risks
    } == {("synthetic_guard_probe.py", "built")}
    assert {
        (path, function)
        for path, function, _line in visitor.dynamic_risks
    } == {
        ("synthetic_guard_probe.py", "concatenated"),
        ("synthetic_guard_probe.py", "augmented"),
        ("synthetic_guard_probe.py", "combined"),
    }


def test_merge_and_tuple_assignment_status_writes_are_detected():
    visitor = _scan_source(
        """
        def merged(db):
            db.execute(
                "MERGE INTO monitoring_alerts AS target "
                "USING staged_alerts AS source ON target.id = source.id "
                "WHEN MATCHED THEN UPDATE SET status = source.status"
            )

        def tuple_updated(db):
            db.execute(
                "UPDATE monitoring_alerts "
                "SET (status, summary) = (?, ?) WHERE id = ?",
                ("resolved", "bypass", 1),
            )
        """
    )
    assert {
        (path, function)
        for path, function, _line in visitor.writes
    } == {
        ("synthetic_guard_probe.py", "merged"),
        ("synthetic_guard_probe.py", "tuple_updated"),
    }


def test_dynamic_unrelated_table_sql_without_status_is_not_overclassified():
    visitor = _scan_source(
        """
        def permitted_update(db, table):
            db.execute(
                f"UPDATE {table} SET heartbeat_at = ? WHERE id = ?",
                ("2026-07-30", 17),
            )

        def permitted_insert(db, table):
            db.execute(
                f"INSERT INTO {table} (id, summary) VALUES (?, ?)",
                (18, "no lifecycle field"),
            )
        """
    )
    assert visitor.dynamic_risks == []


def test_non_open_insert_is_detected_while_fixed_open_insert_passes():
    visitor = _scan_source(
        """
        def malicious(db):
            db.execute(
                "INSERT INTO monitoring_alerts (status, summary) VALUES (?, ?)",
                ("resolved", "bypass"),
            )

        def permitted(db):
            db.execute(
                "INSERT INTO monitoring_alerts (status, summary) VALUES (?, ?)",
                ("open", "new signal"),
            )
        """
    )
    invalid = {
        (path, function)
        for path, function, _line in visitor.invalid_inserts
    }
    verified = {
        (path, function)
        for path, function, _line in visitor.verified_open_inserts
    }
    assert invalid == {("synthetic_guard_probe.py", "malicious")}
    assert verified == {("synthetic_guard_probe.py", "permitted")}


def test_aliased_quoted_and_schema_qualified_updates_are_detected():
    visitor = _scan_source(
        '''
        def aliased(db):
            db.execute(
                "UPDATE monitoring_alerts AS ma "
                "SET ma.status = ? WHERE ma.id = ?",
                ("resolved", 1),
            )

        def quoted(db):
            db.execute(
                'UPDATE "monitoring_alerts" '
                'SET "status" = ? WHERE id = ?',
                ("resolved", 2),
            )

        def qualified(db):
            db.execute(
                "UPDATE public.monitoring_alerts "
                "SET status = ? WHERE id = ?",
                ("resolved", 3),
            )

        def qualified_and_quoted(db):
            db.execute(
                'UPDATE "public"."monitoring_alerts" AS ma '
                'SET ma."status" = ? WHERE ma.id = ?',
                ("resolved", 4),
            )
        '''
    )
    assert {
        (path, function)
        for path, function, _line in visitor.writes
    } == {
        ("synthetic_guard_probe.py", "aliased"),
        ("synthetic_guard_probe.py", "quoted"),
        ("synthetic_guard_probe.py", "qualified"),
        ("synthetic_guard_probe.py", "qualified_and_quoted"),
    }


def test_quoted_and_schema_qualified_inserts_receive_the_same_open_check():
    visitor = _scan_source(
        '''
        def quoted_bad(db):
            db.execute(
                'INSERT INTO "monitoring_alerts" '
                '("status", summary) VALUES (?, ?)',
                ("resolved", "quoted bypass"),
            )

        def qualified_bad(db):
            db.execute(
                "INSERT INTO public.monitoring_alerts AS ma "
                "(status, summary) VALUES (?, ?)",
                ("dismissed", "qualified bypass"),
            )

        def qualified_quoted_good(db):
            db.execute(
                'INSERT INTO "public"."monitoring_alerts" '
                '("status", summary) VALUES (?, ?)',
                ("open", "canonical initial signal"),
            )
        '''
    )
    invalid = {
        (path, function)
        for path, function, _line in visitor.invalid_inserts
    }
    verified = {
        (path, function)
        for path, function, _line in visitor.verified_open_inserts
    }
    assert invalid == {
        ("synthetic_guard_probe.py", "quoted_bad"),
        ("synthetic_guard_probe.py", "qualified_bad"),
    }
    assert verified == {
        ("synthetic_guard_probe.py", "qualified_quoted_good"),
    }


def test_quoted_schema_qualified_upsert_cannot_overwrite_status():
    visitor = _scan_source(
        '''
        def malicious_upsert(db):
            db.execute(
                'INSERT INTO public."monitoring_alerts" AS ma '
                '("status", summary) VALUES (\\'open\\', ?) '
                'ON CONFLICT (id) DO UPDATE '
                'SET "status" = EXCLUDED."status"',
                ("upsert bypass",),
            )
        '''
    )
    expected = {
        ("synthetic_guard_probe.py", "malicious_upsert"),
    }
    assert {
        (path, function)
        for path, function, _line in visitor.upserts
    } == expected
    assert {
        (path, function)
        for path, function, _line in visitor.writes
    } == expected
