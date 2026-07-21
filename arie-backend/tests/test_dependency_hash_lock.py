"""R2-BSA-019: hash-pinned dependency lockfiles.

The production image installs requirements.lock with --require-hashes, so every
dependency is verified against a reviewed SHA-256. These guards keep the locks
honest without a network/install (CI has a separate step that actually installs
them under --require-hashes):

  * both lockfiles exist and every entry carries a --hash=sha256 pin;
  * every DIRECT pinned dependency in requirements.txt / requirements-dev.txt
    appears in the matching lock at the SAME version (drift guard — catches
    "bumped requirements.txt, forgot to regenerate the lock").
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _normalize(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _direct_pins(path):
    pins = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)==([0-9][^\s;]*)", line)
        if m:
            pins[_normalize(m.group(1))] = m.group(2)
    return pins


def _lock_pins(path):
    pins = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+)==([0-9][^\s\\]*)", line.strip())
        if m:
            pins[_normalize(m.group(1))] = m.group(2)
    return pins


def test_lockfiles_exist_and_are_hash_pinned():
    for name in ("requirements.lock", "requirements-dev.lock"):
        lock = ROOT / name
        assert lock.exists(), f"{name} missing"
        text = lock.read_text(encoding="utf-8")
        assert "--hash=sha256:" in text, f"{name} has no sha256 hashes"
        # Every pinned package line must be followed by at least one hash.
        assert text.count("--hash=sha256:") >= text.count("==")


def test_runtime_lock_covers_direct_runtime_pins():
    direct = _direct_pins(ROOT / "requirements.txt")
    lock = _lock_pins(ROOT / "requirements.lock")
    for pkg, ver in direct.items():
        assert pkg in lock, f"{pkg} pinned in requirements.txt but absent from requirements.lock — regenerate the lock"
        assert lock[pkg] == ver, f"{pkg} version drift: requirements.txt={ver} lock={lock[pkg]} — regenerate the lock"


def test_dev_lock_covers_direct_runtime_and_dev_pins():
    direct = _direct_pins(ROOT / "requirements.txt")
    direct.update(_direct_pins(ROOT / "requirements-dev.txt"))
    lock = _lock_pins(ROOT / "requirements-dev.lock")
    for pkg, ver in direct.items():
        assert pkg in lock, f"{pkg} pinned but absent from requirements-dev.lock — regenerate the lock"
        assert lock[pkg] == ver, f"{pkg} version drift vs requirements-dev.lock — regenerate the lock"
