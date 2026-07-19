"""P12-10 / DCI-016: the connection-level request-body ceiling is configurable
and coupled to the per-file upload limit.

max_body_size is the only genuine pre-buffer guard in Tornado's non-streaming
mode (it rejects oversized bodies before buffering them into memory). This
test pins that it derives from MAX_REQUEST_BODY_MB rather than a disconnected
magic constant, and that the ceiling stays >= the per-file MAX_UPLOAD_MB.
"""
import os
from pathlib import Path


def test_request_body_ceiling_defaults():
    import server

    assert server.MAX_UPLOAD_MB == int(os.getenv("MAX_UPLOAD_MB", "10"))
    assert server.MAX_REQUEST_BODY_MB == int(os.getenv("MAX_REQUEST_BODY_MB", "20"))


def test_request_body_ceiling_not_below_per_file_limit():
    import server

    # Multipart/form overhead means the request ceiling must never be below the
    # per-file limit, or legitimate max-size files would be rejected pre-buffer.
    assert server.MAX_REQUEST_BODY_MB >= server.MAX_UPLOAD_MB


def test_max_body_size_is_coupled_to_config():
    source = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    # The pre-buffer ceiling must be derived from the configurable constant,
    # not a hardcoded literal.
    assert "max_body_size=MAX_REQUEST_BODY_MB * 1024 * 1024" in source
    assert 'MAX_REQUEST_BODY_MB = int(os.getenv("MAX_REQUEST_BODY_MB"' in source
    assert 'MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB"' in source
