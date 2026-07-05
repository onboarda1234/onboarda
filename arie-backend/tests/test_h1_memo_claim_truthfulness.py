"""H1 (PR-10): memo-claim truthfulness — static guards.

The live compliance-memo path is the deterministic builder in memo_handler.py.
The Claude memo integration (claude_memo_integration.py) is an off-by-default
draft that no live runtime module may import, and ENABLE_CLAUDE_MEMO must not
be set or defaulted by any runtime config, container build, or deploy manifest.
These tests keep the documentation claims (README.md, CLAUDE.md) and the code
from drifting apart again.
"""
import os

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REPO_ROOT = os.path.dirname(BACKEND)

# Modules that constitute the live memo decision path.
LIVE_MEMO_PATH_MODULES = (
    "server.py",
    "memo_handler.py",
    "validation_engine.py",
    "supervisor_engine.py",
)

# Runtime config / build / deploy surfaces that must not enable the draft.
CONFIG_SURFACES = (
    (BACKEND, "config_loader.py"),
    (BACKEND, "config.py"),
    (BACKEND, "environment.py"),
    (BACKEND, "Dockerfile"),
    (BACKEND, "docker-compose.yml"),
    (REPO_ROOT, "render.yaml"),
)


def _read(*parts):
    with open(os.path.join(*parts), encoding="utf-8") as fh:
        return fh.read()


def test_live_memo_path_never_imports_claude_memo_wrapper():
    """No live-route module may import or call the draft Claude memo wrapper."""
    for module in LIVE_MEMO_PATH_MODULES:
        src = _read(BACKEND, module)
        assert "claude_memo_integration" not in src, (
            f"{module} references claude_memo_integration — the draft must stay "
            "unwired from the live memo path (audit H1 / PC-4)"
        )
        assert "maybe_generate_claude_memo" not in src, (
            f"{module} calls maybe_generate_claude_memo — the draft must stay "
            "unwired from the live memo path (audit H1 / PC-4)"
        )


def test_memo_handler_is_llm_free():
    """The deterministic memo builder must contain no Claude/Anthropic call."""
    src = _read(BACKEND, "memo_handler.py")
    for marker in ("claude", "Claude", "anthropic", "Anthropic"):
        assert marker not in src, (
            f"memo_handler.py contains '{marker}' — the live memo builder is "
            "documented as deterministic and must stay LLM-free"
        )


def test_enable_claude_memo_not_defaulted_anywhere():
    """No runtime config, container build, or deploy manifest may set the flag.

    The surfaces are required to EXIST — a rename must fail this test rather
    than silently vaporising the guard — and the GitHub workflow files are
    scanned too (deploy-staging.yml injects ECS task environment).
    """
    for parts in CONFIG_SURFACES:
        path = os.path.join(*parts)
        assert os.path.exists(path), (
            f"{path} is missing — if it was renamed, update CONFIG_SURFACES in "
            "this test so the ENABLE_CLAUDE_MEMO guard follows it"
        )
        assert "ENABLE_CLAUDE_MEMO" not in _read(*parts), (
            f"{path} sets or references ENABLE_CLAUDE_MEMO — the Claude memo "
            "integration must remain off by default everywhere"
        )

    workflows_dir = os.path.join(REPO_ROOT, ".github", "workflows")
    assert os.path.isdir(workflows_dir), ".github/workflows missing"
    for name in sorted(os.listdir(workflows_dir)):
        if name.endswith((".yml", ".yaml")):
            assert "ENABLE_CLAUDE_MEMO" not in _read(workflows_dir, name), (
                f".github/workflows/{name} sets ENABLE_CLAUDE_MEMO — CI/CD must "
                "not enable the Claude memo integration"
            )
