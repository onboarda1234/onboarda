"""
Targeted tests for dual-header Sumsub webhook signature verification.

Covers the fix for staging evidence showing X-Payload-Digest (not X-App-Access-Sig)
being sent by Sumsub in certain environments.

Scenarios tested:
  - Valid X-App-Access-Sig (primary path)
  - Valid X-Payload-Digest (fallback path)
  - Both headers present — primary (X-App-Access-Sig) wins
  - Missing both headers
  - Wrong signature (primary format)
  - Wrong signature (fallback format)
  - Missing secret in staging/production still rejects
"""
import hmac
import hashlib
import os
import pytest


SECRET = "test-webhook-secret-abc123"
BODY = b'{"type":"applicantReviewed","applicantId":"test-id-001"}'


def _hmac(secret, body):
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers: header selection logic (mirrors server.py SumsubWebhookHandler.post)
# ─────────────────────────────────────────────────────────────────────────────

def _pick_signature(headers: dict) -> tuple:
    """Replicate the header-selection logic from SumsubWebhookHandler.post."""
    primary = headers.get("X-App-Access-Sig", "")
    digest = headers.get("X-Payload-Digest", "")
    if primary:
        return primary, "X-App-Access-Sig"
    if digest:
        return digest, "X-Payload-Digest"
    return "", "none"


# ─────────────────────────────────────────────────────────────────────────────
# 1. verify_webhook (screening.py) — pure unit tests
# ─────────────────────────────────────────────────────────────────────────────

class TestSumsubVerifyWebhookUnit:
    """Unit tests for screening.sumsub_verify_webhook()."""

    def test_valid_signature_accepted(self, monkeypatch):
        """Valid HMAC-SHA256 signature must be accepted."""
        import screening
        monkeypatch.setattr(screening, "SUMSUB_WEBHOOK_SECRET", SECRET)
        monkeypatch.setattr(screening, "ENVIRONMENT", "staging")

        sig = _hmac(SECRET, BODY)
        assert screening.sumsub_verify_webhook(BODY, sig) is True

    def test_wrong_signature_rejected(self, monkeypatch):
        """Wrong HMAC value must be rejected."""
        import screening
        monkeypatch.setattr(screening, "SUMSUB_WEBHOOK_SECRET", SECRET)
        monkeypatch.setattr(screening, "ENVIRONMENT", "staging")

        assert screening.sumsub_verify_webhook(BODY, "deadbeefdeadbeef") is False

    def test_empty_signature_rejected_with_secret(self, monkeypatch):
        """Empty signature string must fail when secret is configured."""
        import screening
        monkeypatch.setattr(screening, "SUMSUB_WEBHOOK_SECRET", SECRET)
        monkeypatch.setattr(screening, "ENVIRONMENT", "staging")

        assert screening.sumsub_verify_webhook(BODY, "") is False

    def test_missing_secret_staging_rejects(self, monkeypatch):
        """Staging without secret must always reject — cannot bypass."""
        import screening
        monkeypatch.setattr(screening, "SUMSUB_WEBHOOK_SECRET", "")
        monkeypatch.setattr(screening, "ENVIRONMENT", "staging")

        assert screening.sumsub_verify_webhook(BODY, "any-sig") is False

    def test_missing_secret_production_rejects(self, monkeypatch):
        """Production without secret must always reject."""
        import screening
        monkeypatch.setattr(screening, "SUMSUB_WEBHOOK_SECRET", "")
        monkeypatch.setattr(screening, "ENVIRONMENT", "production")

        assert screening.sumsub_verify_webhook(BODY, "any-sig") is False

    def test_missing_secret_demo_accepts(self, monkeypatch):
        """Demo/dev without secret should accept (local testing convenience)."""
        import screening
        monkeypatch.setattr(screening, "SUMSUB_WEBHOOK_SECRET", "")
        monkeypatch.setattr(screening, "ENVIRONMENT", "demo")

        assert screening.sumsub_verify_webhook(BODY, "any-sig") is True

    def test_tampered_body_rejected(self, monkeypatch):
        """Signature valid for original body must fail against tampered body."""
        import screening
        monkeypatch.setattr(screening, "SUMSUB_WEBHOOK_SECRET", SECRET)
        monkeypatch.setattr(screening, "ENVIRONMENT", "staging")

        original_sig = _hmac(SECRET, BODY)
        tampered = BODY + b" extra"
        assert screening.sumsub_verify_webhook(tampered, original_sig) is False


# ─────────────────────────────────────────────────────────────────────────────
# 2. Header-selection logic tests
# ─────────────────────────────────────────────────────────────────────────────

class TestHeaderSelectionLogic:
    """Tests for the header-selection logic in SumsubWebhookHandler."""

    def test_primary_header_used_when_present(self):
        """X-App-Access-Sig takes precedence when present."""
        sig, source = _pick_signature({"X-App-Access-Sig": "sig-value-abc"})
        assert sig == "sig-value-abc"
        assert source == "X-App-Access-Sig"

    def test_fallback_header_used_when_primary_absent(self):
        """X-Payload-Digest is used when X-App-Access-Sig is missing."""
        sig, source = _pick_signature({"X-Payload-Digest": "digest-value-xyz"})
        assert sig == "digest-value-xyz"
        assert source == "X-Payload-Digest"

    def test_primary_wins_when_both_present(self):
        """When both headers are present, X-App-Access-Sig (primary) wins."""
        sig, source = _pick_signature({
            "X-App-Access-Sig": "primary-sig",
            "X-Payload-Digest": "fallback-sig",
        })
        assert sig == "primary-sig"
        assert source == "X-App-Access-Sig"

    def test_missing_both_headers_returns_empty(self):
        """When neither header is present, signature is empty string."""
        sig, source = _pick_signature({})
        assert sig == ""
        assert source == "none"

    def test_empty_primary_falls_back_to_digest(self):
        """Empty X-App-Access-Sig falls back to X-Payload-Digest."""
        sig, source = _pick_signature({
            "X-App-Access-Sig": "",
            "X-Payload-Digest": "digest-value-xyz",
        })
        assert sig == "digest-value-xyz"
        assert source == "X-Payload-Digest"


# ─────────────────────────────────────────────────────────────────────────────
# 3. End-to-end: simulate the full webhook flow with each header
# ─────────────────────────────────────────────────────────────────────────────

class TestWebhookEndToEnd:
    """Simulate full path: header selection → verify_webhook()."""

    def _verify_from_headers(self, headers: dict, body: bytes, monkeypatch):
        import screening
        monkeypatch.setattr(screening, "SUMSUB_WEBHOOK_SECRET", SECRET)
        monkeypatch.setattr(screening, "ENVIRONMENT", "staging")
        sig, _ = _pick_signature(headers)
        return screening.sumsub_verify_webhook(body, sig)

    def test_x_app_access_sig_valid_flow(self, monkeypatch):
        """Full path: X-App-Access-Sig with correct HMAC → accepted."""
        headers = {"X-App-Access-Sig": _hmac(SECRET, BODY)}
        assert self._verify_from_headers(headers, BODY, monkeypatch) is True

    def test_x_payload_digest_valid_flow(self, monkeypatch):
        """Full path: X-Payload-Digest with correct HMAC → accepted (staging fix)."""
        headers = {
            "X-Payload-Digest": _hmac(SECRET, BODY),
            "X-Payload-Digest-Alg": "SHA256-HMAC",
        }
        assert self._verify_from_headers(headers, BODY, monkeypatch) is True

    def test_x_app_access_sig_wrong_sig_rejected(self, monkeypatch):
        """X-App-Access-Sig with wrong value → rejected."""
        headers = {"X-App-Access-Sig": "wrongsignaturevalue"}
        assert self._verify_from_headers(headers, BODY, monkeypatch) is False

    def test_x_payload_digest_wrong_sig_rejected(self, monkeypatch):
        """X-Payload-Digest with wrong value → rejected."""
        headers = {"X-Payload-Digest": "wrongdigestvalue"}
        assert self._verify_from_headers(headers, BODY, monkeypatch) is False

    def test_missing_both_headers_rejected_in_staging(self, monkeypatch):
        """No signature headers → signature is empty → rejected in staging."""
        assert self._verify_from_headers({}, BODY, monkeypatch) is False

    def test_primary_with_valid_sig_wins_over_fallback_with_invalid(self, monkeypatch):
        """Primary valid sig wins over bad fallback — correct HMAC accepted."""
        headers = {
            "X-App-Access-Sig": _hmac(SECRET, BODY),
            "X-Payload-Digest": "badfallback",
        }
        assert self._verify_from_headers(headers, BODY, monkeypatch) is True

    def test_valid_digest_but_tampered_body_rejected(self, monkeypatch):
        """Digest valid for original body must fail against different body."""
        good_body = b'{"type":"test"}'
        bad_body = b'{"type":"test","injected":true}'
        headers = {"X-Payload-Digest": _hmac(SECRET, good_body)}
        import screening
        monkeypatch.setattr(screening, "SUMSUB_WEBHOOK_SECRET", SECRET)
        monkeypatch.setattr(screening, "ENVIRONMENT", "staging")
        sig, _ = _pick_signature(headers)
        assert screening.sumsub_verify_webhook(bad_body, sig) is False


# ─────────────────────────────────────────────────────────────────────────────
# 4. Code-structure assertions
# ─────────────────────────────────────────────────────────────────────────────

class TestServerCodeStructure:
    """Verify server.py SumsubWebhookHandler code structure for dual-header support."""

    def _get_webhook_handler_code(self):
        server_path = os.path.join(os.path.dirname(__file__), "..", "server.py")
        with open(server_path, encoding="utf-8") as f:
            src = f.read()
        wh_start = src.find("class SumsubWebhookHandler")
        assert wh_start != -1, "SumsubWebhookHandler class not found in server.py"
        wh_end = src.find("\nclass ", wh_start + 10)
        return src[wh_start:wh_end]

    def test_primary_header_present_in_handler(self):
        """X-App-Access-Sig must appear in SumsubWebhookHandler."""
        assert "X-App-Access-Sig" in self._get_webhook_handler_code()

    def test_fallback_header_present_in_handler(self):
        """X-Payload-Digest must appear in SumsubWebhookHandler as fallback."""
        assert "X-Payload-Digest" in self._get_webhook_handler_code()

    def test_both_headers_come_before_verify_call(self):
        """Both header reads must precede the sumsub_verify_webhook() call."""
        code = self._get_webhook_handler_code()
        primary_pos = code.find("X-App-Access-Sig")
        digest_pos = code.find("X-Payload-Digest")
        verify_pos = code.find("sumsub_verify_webhook")
        assert primary_pos < verify_pos, "X-App-Access-Sig must be read before verify call"
        assert digest_pos < verify_pos, "X-Payload-Digest must be read before verify call"

    def test_sig_source_logging_present(self):
        """Diagnostic log must include the sig_source field."""
        assert "sig_source" in self._get_webhook_handler_code()

    def test_no_signature_bypass(self):
        """Handler must always call sumsub_verify_webhook — no bypass path."""
        code = self._get_webhook_handler_code()
        assert "sumsub_verify_webhook" in code, "sumsub_verify_webhook must be called"
        # Must not have an unconditional 'return True' that skips verification
        assert "return True" not in code
