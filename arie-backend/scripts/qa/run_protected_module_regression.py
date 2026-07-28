#!/usr/bin/env python3
"""Run the permanent protected-module regression group.

The manifest is data, not a shell fragment: every entry must be a repository
test file. This keeps the suite reusable from CI and later Monitoring PRs
without allowing per-PR pytest skips or selector weakening.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys


BACKEND_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = BACKEND_ROOT / "tests" / "protected_module_regression_manifest.json"
CONTRACT_TESTS = [
    "tests/test_protected_module_regression_contract.py",
    "tests/test_protected_module_staging_smoke_contract.py",
]
PYTEST_PLUGIN = "scripts.qa.protected_regression_pytest_plugin"


def load_manifest() -> dict:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def selected_tests(manifest: dict, groups: list[str]) -> list[str]:
    configured = manifest["groups"]
    selected_groups = groups or list(configured)
    unknown = sorted(set(selected_groups) - set(configured))
    if unknown:
        raise ValueError(
            "Unknown group(s): "
            + ", ".join(unknown)
            + ". Available: "
            + ", ".join(configured)
        )

    tests: list[str] = list(CONTRACT_TESTS)
    seen: set[str] = set(CONTRACT_TESTS)
    for group in selected_groups:
        for relative in configured[group]:
            path = Path(relative)
            if path.is_absolute() or ".." in path.parts or path.suffix != ".py":
                raise ValueError(f"Unsafe test path in manifest: {relative}")
            if not (BACKEND_ROOT / path).is_file():
                raise ValueError(f"Missing protected test: {relative}")
            if relative not in seen:
                seen.add(relative)
                tests.append(relative)
    return tests


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--group",
        action="append",
        default=[],
        help="Run one manifest group; repeat for more than one. Defaults to all.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="Print the resolved test paths without running pytest.",
    )
    parser.add_argument(
        "pytest_args",
        nargs=argparse.REMAINDER,
        help="Additional pytest arguments after '--'.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        tests = selected_tests(load_manifest(), args.group)
    except (KeyError, ValueError, json.JSONDecodeError) as exc:
        print(f"protected-module-regression manifest error: {exc}", file=sys.stderr)
        return 2

    if args.list:
        print("\n".join(tests))
        return 0

    extra = list(args.pytest_args)
    if extra[:1] == ["--"]:
        extra = extra[1:]
    env = dict(os.environ)
    env.setdefault("ENVIRONMENT", "testing")
    env.setdefault("SECRET_KEY", "protected-module-regression-test-key")
    env.setdefault("ADMIN_INITIAL_PASSWORD", "ProtectedRegression123")
    command = [
        sys.executable,
        "-m",
        "pytest",
        "-p",
        PYTEST_PLUGIN,
        *tests,
        *extra,
    ]
    print(
        f"protected-module-regression: {len(tests)} files "
        f"across {len(args.group or load_manifest()['groups'])} group(s)",
        flush=True,
    )
    return subprocess.run(command, cwd=BACKEND_ROOT, env=env, check=False).returncode


if __name__ == "__main__":
    raise SystemExit(main())
