"""Fail-closed pytest policy for the permanent protected-module suite."""

from __future__ import annotations

from pathlib import Path

import pytest


_POLICY_VIOLATIONS: list[str] = []


def _requested_test_files(config: pytest.Config) -> set[Path]:
    requested: set[Path] = set()
    invocation_dir = Path(str(config.invocation_params.dir))
    for argument in config.args:
        test_path = Path(str(argument).split("::", 1)[0])
        if test_path.suffix == ".py":
            requested.add((invocation_dir / test_path).resolve())
    return requested


def pytest_collection_finish(session: pytest.Session) -> None:
    requested = _requested_test_files(session.config)
    collected = {Path(str(item.path)).resolve() for item in session.items}
    missing = sorted(str(path) for path in requested - collected)
    if missing:
        raise pytest.UsageError(
            "Protected regression requested test file(s) with zero collected tests: "
            + ", ".join(missing)
        )


def pytest_runtest_logreport(report: pytest.TestReport) -> None:
    was_xfail = getattr(report, "wasxfail", None)
    if report.skipped:
        _POLICY_VIOLATIONS.append(f"skip: {report.nodeid}")
    if was_xfail:
        outcome = "xpass" if report.passed else "xfail"
        _POLICY_VIOLATIONS.append(f"{outcome}: {report.nodeid}")


def pytest_sessionfinish(
    session: pytest.Session, exitstatus: int
) -> None:
    del exitstatus
    if not _POLICY_VIOLATIONS:
        return
    terminal = session.config.pluginmanager.get_plugin("terminalreporter")
    if terminal is not None:
        terminal.write_sep(
            "!",
            "protected-module-regression forbids skip/xfail/xpass",
            red=True,
        )
        for violation in _POLICY_VIOLATIONS:
            terminal.write_line(violation, red=True)
    session.exitstatus = pytest.ExitCode.TESTS_FAILED
