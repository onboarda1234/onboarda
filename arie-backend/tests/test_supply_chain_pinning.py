"""P11-8 / BSA-016 + BSA-017 + BSA-019 (= DCI-022/024) — supply-chain pinning.

BSA-016: GitHub Actions must be pinned to full commit SHAs, not mutable tags —
a compromised action release or tag mutation must not reach CI/deploy.
BSA-017: test/dev dependencies must not ship in production installs.
BSA-019: the Docker base image must be pinned by digest, and the build context
must exclude secrets/local data.

These are guard tests: they scan the actual workflow/build files so a future
edit cannot silently reintroduce a mutable reference.
"""
import os
import re

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WORKFLOWS_DIR = os.path.join(REPO_ROOT, ".github", "workflows")
BACKEND_DIR = os.path.join(REPO_ROOT, "arie-backend")

_SHA_PINNED = re.compile(r"@[0-9a-f]{40}(\s|$)")


def _workflow_files():
    return sorted(
        os.path.join(WORKFLOWS_DIR, f)
        for f in os.listdir(WORKFLOWS_DIR)
        if f.endswith((".yml", ".yaml"))
    )


def _uses_lines(path):
    out = []
    for n, line in enumerate(open(path, encoding="utf-8"), 1):
        stripped = line.strip()
        if stripped.startswith("#"):
            continue
        m = re.search(r"\buses:\s*(\S+)", stripped)
        if m:
            out.append((n, m.group(1)))
    return out


# ══════════════════════════════════════════════════════════
# BSA-016 — every external action SHA-pinned in every workflow
# ══════════════════════════════════════════════════════════

class TestActionsShaPinned:
    def test_every_external_action_is_sha_pinned(self):
        offenders = []
        for wf in _workflow_files():
            for n, ref in _uses_lines(wf):
                if ref.startswith("./"):
                    continue  # local reusable workflow — pinned by the repo SHA itself
                if not _SHA_PINNED.search(ref + " "):
                    offenders.append(f"{os.path.basename(wf)}:{n}: {ref}")
        assert not offenders, (
            "BSA-016: external actions must be pinned to a full 40-char commit "
            "SHA (uses: owner/action@<sha> # vN), found mutable refs:\n"
            + "\n".join(offenders)
        )

    def test_workflows_present(self):
        # The guard above must actually be scanning something.
        assert len(_workflow_files()) >= 3


# ══════════════════════════════════════════════════════════
# BSA-017 — prod requirements carry no test/dev packages
# ══════════════════════════════════════════════════════════

class TestRequirementsSplit:
    DEV_ONLY = ("pytest", "pytest-cov", "pytest-asyncio", "flake8")

    def _pkgs(self, path):
        pkgs = set()
        for line in open(path, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith(("#", "-r ")):
                continue
            pkgs.add(re.split(r"[=<>!\[]", line)[0].strip().lower())
        return pkgs

    def test_prod_requirements_have_no_dev_packages(self):
        pkgs = self._pkgs(os.path.join(BACKEND_DIR, "requirements.txt"))
        leaked = [d for d in self.DEV_ONLY if d in pkgs]
        assert not leaked, f"BSA-017: dev/test packages leaked into prod requirements: {leaked}"

    def test_dev_requirements_exist_and_are_pinned(self):
        path = os.path.join(BACKEND_DIR, "requirements-dev.txt")
        assert os.path.exists(path), "requirements-dev.txt must exist (BSA-017)"
        content = open(path, encoding="utf-8").read()
        for pkg in self.DEV_ONLY:
            assert re.search(rf"^{re.escape(pkg)}==", content, re.M), (
                f"{pkg} must be present and ==-pinned in requirements-dev.txt"
            )

    def test_ci_installs_dev_requirements(self):
        """CI must install the dev file or pytest/flake8 stop existing there."""
        ci = open(os.path.join(WORKFLOWS_DIR, "ci.yml"), encoding="utf-8").read()
        assert "requirements-dev.txt" in ci, (
            "ci.yml must install requirements-dev.txt now that test deps left requirements.txt"
        )

    def test_dockerfile_installs_only_prod_requirements(self):
        """Only instruction lines matter — a COMMENT mentioning the dev file is
        fine; a COPY or pip install of it is not."""
        instructions = [
            l.strip() for l in open(os.path.join(BACKEND_DIR, "Dockerfile"), encoding="utf-8")
            if l.strip() and not l.strip().startswith("#")
        ]
        offenders = [l for l in instructions if "requirements-dev.txt" in l]
        assert not offenders, (
            f"production image must not touch requirements-dev.txt: {offenders}"
        )
        assert any(re.search(r"pip install .*-r requirements\.txt", l) for l in instructions)


# ══════════════════════════════════════════════════════════
# BSA-019 — base image digest-pinned; context excludes local data
# ══════════════════════════════════════════════════════════

class TestDockerBasePinning:
    def test_every_from_is_digest_pinned(self):
        df_path = os.path.join(BACKEND_DIR, "Dockerfile")
        offenders = []
        for n, line in enumerate(open(df_path, encoding="utf-8"), 1):
            stripped = line.strip()
            if stripped.upper().startswith("FROM "):
                image = stripped.split()[1]
                # Stage references (FROM base AS ...) have no registry image.
                if image.lower() in ("base", "builder", "runtime"):
                    continue
                if "@sha256:" not in image:
                    offenders.append(f"Dockerfile:{n}: {stripped}")
        assert not offenders, (
            "BSA-019: base images must be digest-pinned:\n" + "\n".join(offenders)
        )

    def test_dockerignore_excludes_sensitive_paths(self):
        di = open(os.path.join(BACKEND_DIR, ".dockerignore"), encoding="utf-8").read()
        entries = {l.strip() for l in di.splitlines() if l.strip() and not l.strip().startswith("#")}
        for required in (".env", "*.db", "uploads/", "data/", "logs/", "tests/"):
            assert required in entries, (
                f"BSA-019: .dockerignore must exclude {required!r} from the build context"
            )
