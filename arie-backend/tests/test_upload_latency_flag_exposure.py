"""
Upload-latency feature flag exposure contract.

Backend-only remediation flags must not leak to frontend configuration payloads.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("ENVIRONMENT", "testing")
os.environ.setdefault("SECRET_KEY", "test-secret-key-for-testing-only")


def test_environment_info_upload_latency_allowlist_is_exact():
    from environment import (
        CLIENT_SAFE_UPLOAD_LATENCY_FLAGS,
        UPLOAD_LATENCY_FLAGS,
        get_environment_info,
    )

    body = get_environment_info()

    dedicated = body.get("upload_latency_flags")
    assert set(dedicated) == set(CLIENT_SAFE_UPLOAD_LATENCY_FLAGS)

    feature_upload_flags = {
        key for key in body.get("features", {})
        if key in UPLOAD_LATENCY_FLAGS
    }
    assert feature_upload_flags == set(CLIENT_SAFE_UPLOAD_LATENCY_FLAGS)
