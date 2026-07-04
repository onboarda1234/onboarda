"""PR-15 (audit H10) — the container healthcheck must be able to succeed.

The Dockerfile HEALTHCHECK previously probed /api/readiness, which requires
admin/sco authentication (ReadinessHandler.require_auth) — an unauthenticated
in-container probe therefore always got 401 and the container was reported
permanently unhealthy. The healthcheck must target the public liveness probe.
These are static guards so the regression is caught without building an image.
"""
import os
import re

BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCKERFILE = os.path.join(BACKEND, "Dockerfile")


def _dockerfile():
    with open(DOCKERFILE, encoding="utf-8") as fh:
        return fh.read()


def _healthcheck_cmd(text):
    """Return the HEALTHCHECK instruction (with continuation lines joined)."""
    joined = text.replace("\\\n", " ")
    for line in joined.splitlines():
        if line.strip().upper().startswith("HEALTHCHECK"):
            return line
    raise AssertionError("No HEALTHCHECK instruction found in Dockerfile")


def test_healthcheck_targets_public_liveness_probe():
    cmd = _healthcheck_cmd(_dockerfile())
    assert "/api/liveness" in cmd, (
        "Dockerfile HEALTHCHECK must probe the public /api/liveness endpoint; "
        f"got: {cmd.strip()}"
    )


def test_healthcheck_does_not_target_auth_gated_endpoints():
    cmd = _healthcheck_cmd(_dockerfile())
    for gated in ("/api/readiness", "/api/version"):
        assert gated not in cmd, (
            f"Dockerfile HEALTHCHECK probes {gated}, which requires "
            "authentication — an unauthenticated container probe would always "
            "fail (audit H10)"
        )


def test_healthcheck_port_matches_exposed_port():
    text = _dockerfile()
    cmd = _healthcheck_cmd(text)
    m = re.search(r"localhost:(\d+)", cmd)
    assert m, f"HEALTHCHECK has no localhost:<port> target: {cmd.strip()}"
    exposed = re.findall(r"^EXPOSE\s+(\d+)", text, re.MULTILINE)
    assert m.group(1) in exposed, (
        f"HEALTHCHECK probes port {m.group(1)} but Dockerfile exposes {exposed}"
    )


def test_liveness_route_is_public_in_server():
    """The probe target must remain unauthenticated: LivenessHandler.get must
    not call require_auth (ReadinessHandler, by contrast, must)."""
    with open(os.path.join(BACKEND, "server.py"), encoding="utf-8") as fh:
        src = fh.read()
    m = re.search(
        r"class LivenessHandler\(BaseHandler\):.*?(?=\nclass )", src, re.DOTALL
    )
    assert m, "LivenessHandler not found in server.py"
    assert "require_auth" not in m.group(0), (
        "LivenessHandler must stay public — the container healthcheck and "
        "ALB/ECS probes are unauthenticated"
    )
