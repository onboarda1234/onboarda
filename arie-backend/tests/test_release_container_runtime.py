"""Static guards for the release-controls production container."""

from pathlib import Path


BACKEND = Path(__file__).resolve().parents[1]
DOCKERFILE = BACKEND / "Dockerfile"
ALPINE_BASE = (
    "python:3.11-alpine@"
    "sha256:25976e9d34a0fab1f278cae931f34c8303d97bf0c0d7f85b6b4dcf641d7702a4"
)


def _instructions() -> list[str]:
    joined = DOCKERFILE.read_text(encoding="utf-8").replace("\\\n", " ")
    return [
        line.strip()
        for line in joined.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]


def test_release_runtime_uses_reviewed_alpine_digest():
    from_lines = [line for line in _instructions() if line.upper().startswith("FROM ")]
    assert from_lines == [f"FROM {ALPINE_BASE} AS base"]


def test_runtime_os_layer_excludes_prior_vulnerable_toolchains():
    install = next(
        line for line in _instructions() if line.startswith("RUN apk upgrade --no-cache")
    )
    assert "apk add --no-cache" in install
    for required in (
        "cairo",
        "pango",
        "gdk-pixbuf",
        "libffi",
        "shared-mime-info",
        "font-liberation",
        "font-dejavu",
    ):
        assert required in install
    for forbidden in (
        "perl",
        "build-base",
        "linux-headers",
        "gcc",
        "musl-dev",
        "apt-get",
    ):
        assert forbidden not in install


def test_runtime_packaging_toolchain_is_removed_after_hashed_install():
    install = next(
        line
        for line in _instructions()
        if line.startswith("RUN pip install --no-cache-dir --require-hashes")
    )
    assert "-r requirements.lock" in install
    assert "python -m pip uninstall --yes setuptools pip" in install


def test_runtime_is_non_root_and_carries_explicit_release_metadata():
    instructions = _instructions()
    assert any(line.startswith("RUN addgroup -S arie") for line in instructions)
    assert "USER arie" in instructions
    text = DOCKERFILE.read_text(encoding="utf-8")
    for value in (
        "ARG GIT_SHA=unknown",
        "ARG BUILD_TIME=unknown",
        "ARG IMAGE_TAG=${GIT_SHA}",
        "GIT_SHA=${GIT_SHA}",
        "IMAGE_TAG=${IMAGE_TAG}",
    ):
        assert value in text
