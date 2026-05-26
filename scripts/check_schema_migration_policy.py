#!/usr/bin/env python3
import os
import re
import subprocess
import sys
from pathlib import Path

MSG = "This PR modifies schema inside `db.py`'s init_db functions but does not add a new migration file under `arie-backend/migrations/scripts/`. See ADR 0008 — every schema change requires both an `init_db` update AND a migration file."
SCHEMA_RE = re.compile(r"\b(CREATE TABLE|ALTER TABLE|ADD COLUMN|ADD CONSTRAINT|CREATE INDEX|DROP TABLE|DROP COLUMN)\b", re.I)
FUNC_RE = re.compile(r"^def (_get_postgres_schema|_get_sqlite_schema)\(")
HUNK_RE = re.compile(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def run(*args):
    return subprocess.run(args, text=True, capture_output=True, check=False)


def base_ref():
    base = os.environ.get("GITHUB_BASE_REF", "main")
    run("git", "fetch", "origin", f"{base}:refs/remotes/origin/{base}")
    return f"origin/{base}...HEAD"


def function_ranges(path):
    ranges, current = [], None
    lines = Path(path).read_text().splitlines()
    for i, line in enumerate(lines, 1):
        if not line.startswith("def "):
            continue
        m = FUNC_RE.match(line)
        if current:
            ranges.append((start, i - 1))
            current = None
        if m:
            current, start = m.group(1), i
    if current:
        ranges.append((start, len(lines)))
    return ranges


def in_ranges(line_no, ranges):
    return any(start <= line_no <= end for start, end in ranges)


def added_migration(compare):
    out = run("git", "diff", "--name-status", compare).stdout.splitlines()
    return any(re.match(r"^A\s+arie-backend/migrations/scripts/migration_.*\.sql$", l) for l in out)


def db_schema_changed(compare):
    diff = run("git", "diff", "--unified=0", compare, "--", "arie-backend/db.py").stdout
    if not diff:
        return False
    ranges, line_no = function_ranges("arie-backend/db.py"), None
    for line in diff.splitlines():
        m = HUNK_RE.match(line)
        if m:
            line_no = int(m.group(1))
            continue
        if line.startswith("+") and not line.startswith("+++"):
            if line_no and in_ranges(line_no, ranges) and SCHEMA_RE.search(line[1:]):
                return True
            line_no = (line_no or 0) + 1
        elif not line.startswith("-") and line_no:
            line_no += 1
    return False


def main():
    compare = base_ref()
    if db_schema_changed(compare) and not added_migration(compare):
        print(MSG, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
