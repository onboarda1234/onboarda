"""P11-7 (BSA-008 / BSA-010, DCI-017) — document-download disposition +
webhook signature hygiene.

BSA-008: every document-download path must serve with an explicit, sanitized
Content-Disposition — including the S3 presigned path when no filename is
available (previously that branch silently dropped the disposition override
entirely), and the local-fallback paths which interpolated stored names into
the header raw. The sanitizer allows plain spaces only: the previous class
(``[^\\w\\s\\-.]``) readmitted CR/LF via ``\\s``.

BSA-010: webhook signature material must never reach logs. The Sumsub path
logged 8-char signature prefixes (computed + received) and evaluated a
non-constant-time ``==`` purely for the log line; the ComplyAdvantage handler
(which logs no signature material) is the reference pattern.
"""

import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def _read(rel):
    return (BACKEND / rel).read_text(encoding="utf-8")


def _region(text, start, end):
    i = text.index(start)
    j = text.index(end, i)
    return text[i:j]


# ── BSA-008: presigned URL always carries a disposition ──────────────


class _FakeBoto:
    def __init__(self):
        self.calls = []

    def generate_presigned_url(self, operation, Params=None, ExpiresIn=None):
        self.calls.append({"operation": operation, "params": Params, "expiry": ExpiresIn})
        return "https://example.invalid/presigned"


def _client_with_fake_boto():
    from s3_client import S3Client

    client = object.__new__(S3Client)
    client.bucket_name = "test-bucket"
    client.s3_client = _FakeBoto()
    return client


def test_presign_with_filename_emits_attachment_disposition():
    client = _client_with_fake_boto()
    ok, url = client.get_presigned_url("documents/app1/file.pdf", response_filename="file.pdf")
    assert ok and url.startswith("https://")
    params = client.s3_client.calls[0]["params"]
    assert params["ResponseContentDisposition"] == 'attachment; filename="file.pdf"'


def test_presign_without_filename_still_emits_attachment_disposition():
    """The falsy-filename branch previously dropped the override entirely."""
    client = _client_with_fake_boto()
    for empty in (None, ""):
        client.s3_client.calls.clear()
        ok, _ = client.get_presigned_url("documents/app1/file.pdf", response_filename=empty)
        assert ok
        params = client.s3_client.calls[0]["params"]
        assert params["ResponseContentDisposition"] == 'attachment; filename="document"'


def test_presign_inline_only_for_exact_inline_value():
    client = _client_with_fake_boto()
    ok, _ = client.get_presigned_url(
        "k", response_filename="a.pdf", content_disposition="inline"
    )
    assert ok
    assert client.s3_client.calls[0]["params"]["ResponseContentDisposition"].startswith("inline;")

    client.s3_client.calls.clear()
    ok, _ = client.get_presigned_url(
        "k", response_filename="a.pdf", content_disposition="INLINE-ish"
    )
    assert ok
    assert client.s3_client.calls[0]["params"]["ResponseContentDisposition"].startswith("attachment;")


def test_presign_sanitizes_hostile_filename():
    """CRLF, quotes, and separators must not survive into the header value.

    Regression guard for the ``\\s`` -> ``' '`` tightening: the old class
    treated CR/LF as allowed whitespace.
    """
    client = _client_with_fake_boto()
    hostile = 'evil"; filename="x.html\r\nSet-Cookie: a=b'
    ok, _ = client.get_presigned_url("k", response_filename=hostile)
    assert ok
    value = client.s3_client.calls[0]["params"]["ResponseContentDisposition"]
    assert "\r" not in value and "\n" not in value and "\t" not in value
    # Only one filename=" opener (ours) and no interior quote survives.
    assert value.count('filename="') == 1
    inner = value.split('filename="', 1)[1].rsplit('"', 1)[0]
    assert '"' not in inner and ";" not in inner


# ── BSA-008: local-fallback handlers sanitize stored names ───────────


def test_local_download_paths_sanitize_stored_filenames():
    server = _read("server.py")
    # (handler start marker, sanitized variable that must FEED the header —
    # audit follow-up: the regex merely existing in the region is not enough)
    for handler, safe_var in (
        ("class DocumentDownloadHandler", "safe_doc_name"),
        ("class ComplianceResourceDownloadHandler", "safe_resource_name"),
        ("class RegulatoryIntelligenceDownloadHandler", "safe_reg_name"),
    ):
        region = _region(server, handler, "\nclass ")
        assert "re.sub(r'[^\\w \\-.]'" in region, (
            f"{handler} must sanitize the stored filename before setting "
            "Content-Disposition (P11-7 / BSA-008)"
        )
        assert f'filename="{{{safe_var}}}"' in region, (
            f"{handler}: the sanitized name ({safe_var}) must be what the "
            "Content-Disposition header interpolates"
        )
    # The raw interpolations this PR removed must not come back.
    assert 'filename="{doc["doc_name"]}"' not in server
    assert 'filename="{resource["file_name"]}"' not in server
    assert 'filename="{row.get("file_name") or (document_id + ".bin")}"' not in server


def test_s3_branch_still_passes_stored_filename():
    """Audit follow-up: if a refactor drops response_filename=doc["doc_name"]
    from the S3 presign call, every S3 download silently becomes
    filename="document" and nothing else fails."""
    server = _read("server.py")
    region = _region(server, "class DocumentDownloadHandler", "\nclass ")
    assert 'response_filename=doc["doc_name"]' in region


def test_no_crlf_permissive_sanitizer_remains():
    """The header sanitizer must not use \\s (readmits CR/LF)."""
    for rel in ("server.py", "s3_client.py"):
        assert "[^\\w\\s\\-.]" not in _read(rel), (
            f"{rel}: CRLF-permissive filename sanitizer class reintroduced"
        )


# ── BSA-010: no signature material in logs ───────────────────────────


def test_server_webhook_log_carries_no_signature_material():
    server = _read("server.py")
    assert "sig prefix" not in server
    assert "signature[:8]" not in server


def test_screening_verifier_logs_no_signature_material():
    screening = _read("screening.py")
    verifier = _region(screening, "def sumsub_verify_webhook", "\ndef ")
    assert "computed_prefix" not in verifier
    assert "received_prefix" not in verifier
    assert "expected[:8]" not in verifier
    # The equality check must be the constant-time comparison only.
    assert 'hmac.compare_digest(expected, signature_header or "")' in verifier
    assert 'expected == (signature_header or "")' not in verifier


def test_screening_verifier_still_fail_closed():
    """Renaming/moving the return must not soften the allowlist gate."""
    screening = _read("screening.py")
    verifier = _region(screening, "def sumsub_verify_webhook", "\ndef ")
    assert "ALLOWED_DIGEST_ALGS" in verifier
    assert verifier.count("return False") >= 2  # missing secret (deployed) + unknown alg
