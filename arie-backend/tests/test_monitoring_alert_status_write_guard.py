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
_SQL_EXECUTOR_METHODS = {"execute", "executemany", "executescript"}
_SQL_ARGUMENT_NAMES = {
    "command",
    "operation",
    "query",
    "script",
    "sql",
    "sql_script",
    "statement",
}
_SQL_PARAMETER_NAMES = {
    "bindings",
    "parameters",
    "params",
    "vars",
    "vars_list",
    "values",
}

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
_SQLITE_CONFLICT_ACTION = r"(?:ROLLBACK|ABORT|REPLACE|FAIL|IGNORE)"
_MONITORING_UPDATE_PREFIX = (
    rf"\bUPDATE(?:\s+OR\s+{_SQLITE_CONFLICT_ACTION})?\s+"
    rf"(?:ONLY\s+(?:\(\s*{_MONITORING_TABLE}\s*\)|"
    rf"{_MONITORING_TABLE})|{_MONITORING_TABLE})"
    rf"\s*\*?{_UPDATE_ALIAS}\s+SET\b"
)
_MONITORING_INSERT_PREFIX = (
    rf"\b(?:INSERT(?:\s+OR\s+{_SQLITE_CONFLICT_ACTION})?|REPLACE)"
    rf"\s+INTO\s+"
    rf"{_MONITORING_TABLE}{_INSERT_ALIAS}"
)
_REPLACING_MONITORING_INSERT = re.compile(
    rf"\b(?:INSERT\s+OR\s+REPLACE|REPLACE)\s+INTO\s+"
    rf"{_MONITORING_TABLE}",
    re.IGNORECASE,
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


def _mask_sql_literals_and_comments(sql):
    """Mask SQL data literals/comments while preserving character offsets."""
    masked = list(sql)
    index = 0
    state = "code"
    dollar_delimiter = None
    while index < len(sql):
        if state == "code":
            if sql.startswith("--", index):
                masked[index:index + 2] = "  "
                state = "line_comment"
                index += 2
                continue
            if sql.startswith("/*", index):
                masked[index:index + 2] = "  "
                state = "block_comment"
                index += 2
                continue
            if sql[index] == "'":
                masked[index] = " "
                state = "single_quote"
                index += 1
                continue
            dollar = re.match(r"\$(?:[A-Za-z_][A-Za-z0-9_]*)?\$", sql[index:])
            if dollar:
                dollar_delimiter = dollar.group(0)
                masked[index:index + len(dollar_delimiter)] = (
                    " " * len(dollar_delimiter)
                )
                state = "dollar_quote"
                index += len(dollar_delimiter)
                continue
            index += 1
            continue
        if state == "single_quote":
            masked[index] = " "
            if sql[index] == "'" and index + 1 < len(sql) and sql[index + 1] == "'":
                masked[index + 1] = " "
                index += 2
                continue
            if sql[index] == "'":
                state = "code"
            index += 1
            continue
        if state == "line_comment":
            if sql[index] == "\n":
                state = "code"
            else:
                masked[index] = " "
            index += 1
            continue
        if state == "block_comment":
            masked[index] = " "
            if sql.startswith("*/", index):
                if index + 1 < len(sql):
                    masked[index + 1] = " "
                index += 2
                state = "code"
            else:
                index += 1
            continue
        if state == "dollar_quote":
            if sql.startswith(dollar_delimiter, index):
                masked[index:index + len(dollar_delimiter)] = (
                    " " * len(dollar_delimiter)
                )
                index += len(dollar_delimiter)
                state = "code"
                dollar_delimiter = None
            else:
                masked[index] = " "
                index += 1
    return "".join(masked)


def _sql_boundary_index(sql, keywords=("WHERE", "RETURNING")):
    """Return the first top-level SQL keyword outside literals/comments."""
    masked = _mask_sql_literals_and_comments(sql)
    index = 0
    quote_end = None
    depth = 0
    while index < len(masked):
        char = masked[index]
        if quote_end is not None:
            if char == quote_end:
                if index + 1 < len(masked) and masked[index + 1] == quote_end:
                    index += 2
                    continue
                quote_end = None
            index += 1
            continue
        if char == '"':
            quote_end = '"'
            index += 1
            continue
        if char == "`":
            quote_end = "`"
            index += 1
            continue
        if char == "[":
            quote_end = "]"
            index += 1
            continue
        if char == "(":
            depth += 1
            index += 1
            continue
        if char == ")":
            depth = max(0, depth - 1)
            index += 1
            continue
        token = re.match(r"[A-Za-z_][A-Za-z0-9_$]*", masked[index:])
        if token:
            if depth == 0 and token.group(0).upper() in keywords:
                return index
            index += len(token.group(0))
            continue
        index += 1
    return None


def _monitoring_update_writes_status(sql):
    """Detect a status assignment in the actual UPDATE SET clause."""
    executable = _mask_sql_literals_and_comments(sql)
    for update in _MONITORING_UPDATE.finditer(executable):
        tail = sql[update.end():]
        boundary = _sql_boundary_index(tail)
        assignment_region = (
            tail[:boundary] if boundary is not None else tail
        )
        executable_assignments = _mask_sql_literals_and_comments(
            assignment_region
        )
        if re.search(
            _STATUS_WRITE_ASSIGNMENT,
            executable_assignments,
            re.IGNORECASE | re.DOTALL,
        ):
            return True
    return False


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
        rf"\bUPDATE(?:\s+OR\s+{_SQLITE_CONFLICT_ACTION})?\s+"
        r"(?:ONLY\s+)?(?P<target>.*?)\bSET\b",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    if (
        update_prefix
        and _DYNAMIC_SQL_FRAGMENT in update_prefix.group("target")
    ):
        tail = sql[update_prefix.end():]
        boundary = _sql_boundary_index(tail)
        assignment_region = (
            tail[:boundary] if boundary is not None else tail
        )
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
        rf"\b(?P<verb>INSERT(?:\s+OR\s+{_SQLITE_CONFLICT_ACTION})?"
        r"|REPLACE)\s+INTO\s+",
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
            if "REPLACE" in insert_prefix.group("verb").upper():
                return True
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
        boundary = _sql_boundary_index(tail)
        assignment_region = (
            tail[:boundary] if boundary is not None else tail
        )
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
        self._exception_path_trackers = []
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
            and node.func.attr == "replace"
            and len(node.args) == 2
            and not node.keywords
        ):
            base = self._shape(node.func.value)
            old = self._literal_string(node.args[0])
            new = self._literal_string(node.args[1])
            if base is not None and old is not None and new is not None:
                return base[0].replace(old, new), base[1]
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

    def _call_argument(self, call, position, keyword_names):
        candidates = []
        if len(call.args) > position:
            candidates.append(call.args[position])
        candidates.extend(
            keyword.value
            for keyword in call.keywords
            if keyword.arg in keyword_names
        )
        if len(candidates) != 1:
            return None
        return candidates[0]

    def _executor_method(self, callee):
        callee = self._resolve_expression(callee)
        method = (
            getattr(callee, "attr", "")
            or getattr(callee, "id", "")
        )
        if method in _SQL_EXECUTOR_METHODS:
            return method
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
        parameters = self._call_argument(
            call,
            1,
            _SQL_PARAMETER_NAMES,
        )
        if parameters is None:
            return False
        status_argument = self._tuple_item(parameters, placeholder_index)
        return self._literal_string(status_argument) == "open"

    def _record_insert(self, call, sql, location):
        if _REPLACING_MONITORING_INSERT.search(sql):
            # SQLite REPLACE deletes/reinserts an existing row on conflict and
            # can therefore reset or overwrite lifecycle state even when an
            # explicit status value is omitted or happens to be ``open``.
            self.invalid_inserts.append(location)
            return
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

    def _binding_state(self):
        return (
            dict(self._string_bindings[-1]),
            dict(self._expression_bindings[-1]),
        )

    def _visit_branch(self, statements, state):
        self._string_bindings[-1] = dict(state[0])
        self._expression_bindings[-1] = dict(state[1])
        for statement in statements:
            self.visit(statement)
        return self._binding_state()

    @staticmethod
    def _shape_risk(value):
        if value is None:
            return -1
        sql = value[0]
        if (
            _monitoring_update_writes_status(sql)
            or _UPSERT_STATUS_UPDATE.search(sql)
            or _MONITORING_MERGE_STATUS_UPDATE.search(sql)
        ):
            return 4
        if _dynamic_structural_monitoring_sql(sql):
            return 3
        if (
            _MONITORING_UPDATE.search(sql)
            or _MONITORING_INSERT.search(sql)
            or re.search(
                rf"\bMERGE\s+INTO\s+{_MONITORING_TABLE}",
                sql,
                re.IGNORECASE,
            )
        ):
            return 2
        return 0

    @classmethod
    def _merge_string_binding(cls, values):
        first = values[0]
        if all(value == first for value in values[1:]):
            return first
        known = [value for value in values if value is not None]
        if any(value is None for value in values) and not any(
            cls._shape_risk(value) >= 3 for value in known
        ):
            return None
        return max(
            known,
            key=cls._shape_risk,
        )

    @staticmethod
    def _expression_key(value):
        if value is None:
            return None
        return ast.dump(value, include_attributes=False)

    @staticmethod
    def _binding_executor_method(value, bindings):
        seen = set()
        while isinstance(value, ast.Name) and value.id not in seen:
            seen.add(value.id)
            if value.id not in bindings:
                break
            value = bindings[value.id]
        method = getattr(value, "attr", "") or getattr(value, "id", "")
        return method if method in _SQL_EXECUTOR_METHODS else None

    def _merge_branch_states(self, states):
        missing = object()
        string_names = set().union(*(state[0] for state in states))
        expression_names = set().union(*(state[1] for state in states))
        merged_strings = {}
        merged_expressions = {}

        for name in string_names:
            values = [state[0].get(name, missing) for state in states]
            if any(value is missing for value in values):
                known = [value for value in values if value is not missing]
                risky = [
                    value
                    for value in known
                    if self._shape_risk(value) >= 3
                ]
                merged_strings[name] = (
                    max(risky, key=self._shape_risk)
                    if risky
                    else None
                )
            else:
                merged_strings[name] = self._merge_string_binding(values)

        for name in expression_names:
            values = [state[1].get(name, missing) for state in states]
            executor_methods = {
                self._binding_executor_method(value, state[1])
                for value, state in zip(values, states)
                if value is not missing
            } - {None}
            if executor_methods:
                merged_expressions[name] = ast.Name(
                    id=sorted(executor_methods)[0],
                    ctx=ast.Load(),
                )
                continue
            if any(value is missing for value in values):
                continue
            keys = [self._expression_key(value) for value in values]
            if all(key == keys[0] for key in keys[1:]):
                merged_expressions[name] = values[0]

        self._string_bindings[-1] = merged_strings
        self._expression_bindings[-1] = merged_expressions

    def _start_exception_path_tracker(self):
        tracker = {
            "scope": len(self._string_bindings) - 1,
            "strings": {
                name: value
                for name, value in self._string_bindings[-1].items()
                if self._shape_risk(value) >= 3
            },
            "executors": {},
        }
        for name, value in self._expression_bindings[-1].items():
            method = self._binding_executor_method(
                value,
                self._expression_bindings[-1],
            )
            if method:
                tracker["executors"][name] = method
        self._exception_path_trackers.append(tracker)
        return tracker

    def _record_exception_path_binding(self, name, shape, expression):
        scope = len(self._string_bindings) - 1
        for tracker in self._exception_path_trackers:
            if tracker["scope"] != scope:
                continue
            existing = tracker["strings"].get(name)
            if shape is None:
                if existing is None:
                    tracker["strings"][name] = None
            elif self._shape_risk(shape) >= 3 and (
                existing is None
                or self._shape_risk(shape) > self._shape_risk(existing)
            ):
                tracker["strings"][name] = shape
            method = self._binding_executor_method(
                expression,
                self._expression_bindings[-1],
            )
            if method:
                tracker["executors"][name] = method

    def _finish_exception_path_tracker(self, tracker):
        popped = self._exception_path_trackers.pop()
        assert popped is tracker
        for name, risky in tracker["strings"].items():
            current = self._string_bindings[-1].get(name)
            if risky is None:
                if self._shape_risk(current) < 3:
                    self._string_bindings[-1][name] = None
            elif self._shape_risk(risky) > self._shape_risk(current):
                self._string_bindings[-1][name] = risky
        for name, method in tracker["executors"].items():
            self._expression_bindings[-1][name] = ast.Name(
                id=method,
                ctx=ast.Load(),
            )

    def visit_If(self, node):
        self.visit(node.test)
        initial = self._binding_state()
        body_state = self._visit_branch(node.body, initial)
        else_state = (
            self._visit_branch(node.orelse, initial)
            if node.orelse
            else initial
        )
        self._merge_branch_states((body_state, else_state))

    def _visit_loop(self, node):
        self.visit(node.iter if isinstance(node, (ast.For, ast.AsyncFor)) else node.test)
        initial = self._binding_state()
        body_state = self._visit_branch(node.body, initial)
        states = (body_state, initial)
        if node.orelse:
            states = tuple(
                self._visit_branch(node.orelse, state)
                for state in states
            )
        self._merge_branch_states(states)

    visit_For = _visit_loop
    visit_AsyncFor = _visit_loop
    visit_While = _visit_loop

    def visit_Try(self, node):
        initial = self._binding_state()
        tracker = self._start_exception_path_tracker()
        body_state = self._visit_branch(node.body, initial)
        states = [
            self._visit_branch(node.orelse, body_state)
            if node.orelse
            else body_state
        ]
        for handler in node.handlers:
            if handler.type is not None:
                self.visit(handler.type)
            states.append(self._visit_branch(handler.body, initial))
            states.append(self._visit_branch(handler.body, body_state))
        if node.finalbody:
            states = [
                self._visit_branch(node.finalbody, state)
                for state in states
            ]
        self._merge_branch_states(tuple(states))
        self._finish_exception_path_tracker(tracker)

    visit_TryStar = visit_Try

    def visit_With(self, node):
        for item in node.items:
            self.visit(item.context_expr)
        tracker = self._start_exception_path_tracker()
        for statement in node.body:
            self.visit(statement)
        self._finish_exception_path_tracker(tracker)

    visit_AsyncWith = visit_With

    def visit_Match(self, node):
        self.visit(node.subject)
        initial = self._binding_state()
        states = [initial]
        for case in node.cases:
            self._string_bindings[-1] = dict(initial[0])
            self._expression_bindings[-1] = dict(initial[1])
            if case.guard is not None:
                self.visit(case.guard)
            states.append(
                self._visit_branch(case.body, self._binding_state())
            )
        self._merge_branch_states(tuple(states))

    def visit_Assign(self, node):
        shape = self._shape(node.value)
        for target in node.targets:
            if isinstance(target, ast.Name):
                self._expression_bindings[-1][target.id] = node.value
                self._string_bindings[-1][target.id] = shape
                self._record_exception_path_binding(
                    target.id,
                    shape,
                    node.value,
                )
        self.generic_visit(node)

    def visit_AnnAssign(self, node):
        if node.value is not None and isinstance(node.target, ast.Name):
            shape = self._shape(node.value)
            self._expression_bindings[-1][node.target.id] = node.value
            self._string_bindings[-1][node.target.id] = shape
            self._record_exception_path_binding(
                node.target.id,
                shape,
                node.value,
            )
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
                self._string_bindings[-1][node.target.id] = None
            self._expression_bindings[-1].pop(node.target.id, None)
            self._record_exception_path_binding(
                node.target.id,
                self._string_bindings[-1].get(node.target.id),
                node.target,
            )
        self.generic_visit(node)

    def visit_Call(self, node):
        self._record_helper_insert(node)
        method = self._executor_method(node.func)
        if method:
            sql_argument = self._call_argument(
                node,
                0,
                _SQL_ARGUMENT_NAMES,
            )
            if sql_argument is None:
                self.unresolved_risks.append(self._location(node))
                self.generic_visit(node)
                return
            shape = self._shape(sql_argument)
            if shape is None:
                self.unresolved_risks.append(self._location(node))
            else:
                sql, _dynamic = shape
                location = self._location(node)
                if _dynamic_structural_monitoring_sql(sql):
                    self.dynamic_risks.append(location)
                if (
                    _monitoring_update_writes_status(sql)
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

        def malicious_replace(db, table):
            db.execute(
                f"REPLACE INTO {table} (id, summary) VALUES (?, ?)",
                (21, "implicit status reset"),
            )

        def malicious_insert_or_replace(db, table):
            db.execute(
                f"INSERT OR REPLACE INTO {table} "
                "(id, status, summary) VALUES (?, ?, ?)",
                (22, "open", "destructive replacement"),
            )

        def malicious_conflict_update(db, table):
            db.execute(
                f"UPDATE OR REPLACE {table} SET status = ? WHERE id = ?",
                ("resolved", 23),
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
        ("synthetic_guard_probe.py", "malicious_replace"),
        ("synthetic_guard_probe.py", "malicious_insert_or_replace"),
        ("synthetic_guard_probe.py", "malicious_conflict_update"),
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


def test_bare_keyword_and_aliased_execute_callees_fail_closed():
    visitor = _scan_source(
        """
        def static_bypass(execute):
            execute(
                "UPDATE monitoring_alerts SET status = ? WHERE id = ?",
                ("resolved", 1),
            )

        def unresolved_bypass(execute, sql):
            execute(sql, ("resolved", 2))

        def keyword_bypass(db):
            db.execute(
                sql="UPDATE monitoring_alerts SET status = ? WHERE id = ?",
                params=("resolved", 3),
            )

        def alias_bypass(db):
            run_sql = db.execute
            run_sql(
                "UPDATE monitoring_alerts SET status = ? WHERE id = ?",
                ("resolved", 4),
            )

        def chained_alias_bypass(db):
            first_alias = db.execute
            second_alias = first_alias
            second_alias(
                sql="UPDATE monitoring_alerts SET status = ? WHERE id = ?",
                params=("resolved", 5),
            )

        def unresolved_alias_bypass(db, sql):
            run_sql = db.execute
            run_sql(sql=sql, params=("resolved", 6))
        """
    )
    assert {
        (path, function)
        for path, function, _line in visitor.writes
    } == {
        ("synthetic_guard_probe.py", "alias_bypass"),
        ("synthetic_guard_probe.py", "chained_alias_bypass"),
        ("synthetic_guard_probe.py", "keyword_bypass"),
        ("synthetic_guard_probe.py", "static_bypass"),
    }
    assert {
        (path, function)
        for path, function, _line in visitor.unresolved_risks
    } == {
        ("synthetic_guard_probe.py", "unresolved_alias_bypass"),
        ("synthetic_guard_probe.py", "unresolved_bypass"),
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


def test_where_inside_literal_or_comment_cannot_hide_later_status_write():
    visitor = _scan_source(
        """
        def literal_bypass(db):
            db.execute(
                "UPDATE monitoring_alerts "
                "SET summary = 'WHERE', status = ? WHERE id = ?",
                ("resolved", 1),
            )

        def block_comment_bypass(db):
            db.execute(
                "UPDATE monitoring_alerts "
                "SET summary = ?, /* WHERE id = 0 */ status = ? WHERE id = ?",
                ("note", "resolved", 2),
            )

        def line_comment_bypass(db):
            db.execute(
                "UPDATE monitoring_alerts SET summary = ? -- WHERE fake\\n"
                ", status = ? WHERE id = ?",
                ("note", "resolved", 3),
            )
        """
    )
    assert {
        (path, function)
        for path, function, _line in visitor.writes
    } == {
        ("synthetic_guard_probe.py", "block_comment_bypass"),
        ("synthetic_guard_probe.py", "line_comment_bypass"),
        ("synthetic_guard_probe.py", "literal_bypass"),
    }


def test_where_inside_set_subquery_cannot_hide_later_status_write():
    visitor = _scan_source(
        """
        def subquery_bypass(db):
            db.execute(
                "UPDATE monitoring_alerts "
                "SET summary = ("
                "SELECT note FROM audit_log WHERE audit_log.id = 1"
                "), status = ? WHERE id = ?",
                ("resolved", 1),
            )
        """
    )
    assert {
        (path, function)
        for path, function, _line in visitor.writes
    } == {
        ("synthetic_guard_probe.py", "subquery_bypass"),
    }


def test_sqlite_conflict_updates_and_replace_inserts_cannot_bypass_guard():
    visitor = _scan_source(
        """
        def update_or_rollback(db):
            db.execute(
                "UPDATE OR ROLLBACK monitoring_alerts "
                "SET status = ? WHERE id = ?",
                ("resolved", 1),
            )

        def update_or_abort(db):
            db.execute(
                "UPDATE OR ABORT monitoring_alerts SET status = ? WHERE id = ?",
                ("resolved", 2),
            )

        def update_or_replace(db):
            db.execute(
                "UPDATE OR REPLACE monitoring_alerts "
                "SET status = ? WHERE id = ?",
                ("resolved", 3),
            )

        def update_or_fail(db):
            db.execute(
                "UPDATE OR FAIL monitoring_alerts SET status = ? WHERE id = ?",
                ("resolved", 4),
            )

        def update_or_ignore(db):
            db.execute(
                "UPDATE OR IGNORE monitoring_alerts SET status = ? WHERE id = ?",
                ("resolved", 5),
            )

        def replace_noncanonical(db):
            db.execute(
                "REPLACE INTO monitoring_alerts (id, status, summary) "
                "VALUES (?, ?, ?)",
                (6, "resolved", "replacement"),
            )

        def replace_implicit_default(db):
            db.execute(
                "REPLACE INTO monitoring_alerts (id, summary) VALUES (?, ?)",
                (7, "implicit reset"),
            )

        def insert_or_replace_open(db):
            db.execute(
                "INSERT OR REPLACE INTO monitoring_alerts "
                "(id, status, summary) VALUES (?, ?, ?)",
                (8, "open", "destructive open replacement"),
            )
        """
    )
    assert {
        (path, function)
        for path, function, _line in visitor.writes
    } == {
        ("synthetic_guard_probe.py", "update_or_abort"),
        ("synthetic_guard_probe.py", "update_or_fail"),
        ("synthetic_guard_probe.py", "update_or_ignore"),
        ("synthetic_guard_probe.py", "update_or_replace"),
        ("synthetic_guard_probe.py", "update_or_rollback"),
    }
    assert {
        (path, function)
        for path, function, _line in visitor.invalid_inserts
    } == {
        ("synthetic_guard_probe.py", "insert_or_replace_open"),
        ("synthetic_guard_probe.py", "replace_implicit_default"),
        ("synthetic_guard_probe.py", "replace_noncanonical"),
    }


def test_postgresql_inheritance_update_forms_cannot_bypass_guard():
    visitor = _scan_source(
        """
        def inherited_table(db):
            db.execute(
                "UPDATE monitoring_alerts * SET status = ? WHERE id = ?",
                ("resolved", 1),
            )

        def only_inherited_table(db):
            db.execute(
                "UPDATE ONLY monitoring_alerts * AS ma "
                "SET status = ? WHERE ma.id = ?",
                ("resolved", 2),
            )

        def parenthesized_only_table(db):
            db.execute(
                "UPDATE ONLY (monitoring_alerts) AS ma "
                "SET status = ? WHERE ma.id = ?",
                ("resolved", 3),
            )
        """
    )
    assert {
        (path, function)
        for path, function, _line in visitor.writes
    } == {
        ("synthetic_guard_probe.py", "inherited_table"),
        ("synthetic_guard_probe.py", "only_inherited_table"),
        ("synthetic_guard_probe.py", "parenthesized_only_table"),
    }


def test_non_string_rebinding_cannot_reuse_stale_sql_shape():
    visitor = _scan_source(
        """
        sql = "UPDATE monitoring_alerts SET status = ? WHERE id = ?"

        def rebound(db, builder):
            sql = "UPDATE monitoring_alerts SET status = ? WHERE id = ?"
            sql = builder()
            db.execute(sql, ("resolved", 1))

        def shadows_module_binding(db, builder):
            sql = builder()
            db.execute(sql, ("resolved", 2))
        """
    )
    assert {
        (path, function)
        for path, function, _line in visitor.unresolved_risks
    } == {
        ("synthetic_guard_probe.py", "rebound"),
        ("synthetic_guard_probe.py", "shadows_module_binding"),
    }
    assert visitor.writes == []


def test_conditional_and_loop_rebinding_preserve_every_possible_sql_path():
    visitor = _scan_source(
        """
        def conditional_overwrite(db, use_safe):
            sql = "UPDATE monitoring_alerts SET status = ? WHERE id = ?"
            if use_safe:
                sql = "UPDATE monitoring_alerts SET summary = ? WHERE id = ?"
            db.execute(sql, ("value", 1))

        def branch_assignment(db, use_safe):
            if use_safe:
                sql = "UPDATE monitoring_alerts SET summary = ? WHERE id = ?"
            else:
                sql = "UPDATE monitoring_alerts SET status = ? WHERE id = ?"
            db.execute(sql, ("value", 2))

        def loop_may_not_run(db, rows):
            sql = "UPDATE monitoring_alerts SET status = ? WHERE id = ?"
            for _row in rows:
                sql = "UPDATE monitoring_alerts SET summary = ? WHERE id = ?"
            db.execute(sql, ("value", 3))

        def conditional_executor_alias(db, callback, use_db):
            if use_db:
                run_sql = db.execute
            else:
                run_sql = callback
            run_sql(
                "UPDATE monitoring_alerts SET status = ? WHERE id = ?",
                ("resolved", 4),
            )
        """
    )
    assert {
        (path, function)
        for path, function, _line in visitor.writes
    } == {
        ("synthetic_guard_probe.py", "branch_assignment"),
        ("synthetic_guard_probe.py", "conditional_overwrite"),
        ("synthetic_guard_probe.py", "conditional_executor_alias"),
        ("synthetic_guard_probe.py", "loop_may_not_run"),
    }


def test_try_and_match_paths_cannot_hide_a_possible_status_write():
    visitor = _scan_source(
        """
        def try_overwrite(db, may_fail):
            sql = "UPDATE monitoring_alerts SET summary = ? WHERE id = ?"
            try:
                sql = "UPDATE monitoring_alerts SET status = ? WHERE id = ?"
                may_fail()
                sql = "UPDATE monitoring_alerts SET summary = ? WHERE id = ?"
            except RuntimeError:
                pass
            db.execute(sql, ("value", 1))

        def suppressed_with_overwrite(db, suppressor, may_fail):
            sql = "UPDATE monitoring_alerts SET summary = ? WHERE id = ?"
            with suppressor():
                sql = "UPDATE monitoring_alerts SET status = ? WHERE id = ?"
                may_fail()
                sql = "UPDATE monitoring_alerts SET summary = ? WHERE id = ?"
            db.execute(sql, ("value", 2))

        def match_assignment(db, decision):
            match decision:
                case "safe":
                    sql = "UPDATE monitoring_alerts SET summary = ? WHERE id = ?"
                case _:
                    sql = "UPDATE monitoring_alerts SET status = ? WHERE id = ?"
            db.execute(sql, ("value", 3))
        """
    )
    assert {
        (path, function)
        for path, function, _line in visitor.writes
    } == {
        ("synthetic_guard_probe.py", "match_assignment"),
        ("synthetic_guard_probe.py", "suppressed_with_overwrite"),
        ("synthetic_guard_probe.py", "try_overwrite"),
    }


def test_every_monitoring_update_in_an_executescript_is_inspected():
    visitor = _scan_source(
        """
        def later_statement_bypass(db):
            db.executescript(
                "UPDATE monitoring_alerts "
                "SET summary = 'safe first statement' WHERE id = 1; "
                "WITH selected AS (SELECT 2 AS id) "
                "UPDATE monitoring_alerts SET status = 'resolved' "
                "WHERE id IN (SELECT id FROM selected);"
            )
        """
    )
    assert {
        (path, function)
        for path, function, _line in visitor.writes
    } == {
        ("synthetic_guard_probe.py", "later_statement_bypass"),
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
