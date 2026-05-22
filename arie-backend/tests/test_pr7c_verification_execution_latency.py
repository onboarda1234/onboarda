"""PR7C verification execution-latency instrumentation guards."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import claude_client as claude_module
from claude_client import ClaudeClient
from document_verification import to_legacy_result, verify_document_layered


def _png(path):
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"test-image")


def test_layered_verification_records_execution_subspans(tmp_path):
    png = tmp_path / "certificate.png"
    _png(png)

    class FakeClaude:
        def __init__(self):
            self.last_provider_failure = None
            self.last_field_extraction_timing_ms = {}
            self.last_document_verification_timing_ms = {}

        def extract_document_fields(self, **_kwargs):
            self.last_provider_failure = None
            self.last_field_extraction_timing_ms = {
                "vision_read_ms": 2,
                "provider_round_trip_ms": 11,
                "response_parse_ms": 3,
            }
            return {"entity_name": "ACME LTD"}

        def verify_document(self, **_kwargs):
            self.last_provider_failure = None
            self.last_document_verification_timing_ms = {
                "vision_read_ms": 5,
                "prompt_build_ms": 7,
                "provider_round_trip_ms": 13,
                "response_parse_ms": 4,
            }
            return {
                "checks": [{
                    "id": "AI-TEST",
                    "label": "AI Test",
                    "classification": "ai",
                    "type": "validity",
                    "result": "pass",
                    "message": "ok",
                }],
                "overall": "verified",
                "confidence": 0.9,
            }

    result = verify_document_layered(
        doc_type="cert_inc",
        category="entity",
        file_path=str(png),
        file_size=png.stat().st_size,
        mime_type="image/png",
        prescreening_data={"company_name": "ACME LTD"},
        risk_level="MEDIUM",
        existing_hashes=[],
        claude_client=FakeClaude(),
        entity_name="ACME LTD",
        check_overrides=[{
            "id": "AI-TEST",
            "label": "AI Test",
            "classification": "ai",
            "type": "validity",
            "rule": "Use provider for test",
        }],
        file_name="certificate.png",
    )

    timing = result["execution_timing_ms"]
    assert timing["file_format_check_ms"] >= 0
    assert timing["duplicate_check_ms"] >= 0
    assert timing["field_extraction_ms"] >= 0
    assert timing["field_extraction_provider_round_trip_ms"] == 11
    assert timing["rule_checks_ms"] >= 0
    assert timing["ai_verification_ms"] >= 0
    assert timing["ai_verification_provider_round_trip_ms"] == 13
    assert timing["provider_round_trip_ms"] == 24
    assert timing["total_engine_ms"] >= 0


def test_legacy_verification_result_preserves_execution_timing():
    legacy = to_legacy_result({
        "checks": [],
        "overall": "flagged",
        "execution_timing_ms": {"provider_round_trip_ms": 123},
    })

    assert legacy["execution_timing_ms"] == {"provider_round_trip_ms": 123}


def test_claude_document_token_budget_is_bounded():
    client = ClaudeClient.__new__(ClaudeClient)

    assert client._document_verification_max_tokens(0) == ClaudeClient.DOCUMENT_VERIFICATION_MIN_TOKENS
    assert client._document_verification_max_tokens(2) < 4096
    assert client._document_verification_max_tokens(20) == ClaudeClient.DOCUMENT_VERIFICATION_MAX_TOKENS


def test_call_claude_honors_explicit_max_tokens(monkeypatch):
    captured = {}

    class FakeUsage:
        def log_usage(self, **_kwargs):
            return None

    class FakeMessages:
        def create(self, **kwargs):
            captured.update(kwargs)

            class Resp:
                content = [type("Item", (), {"text": "ok"})()]
                usage = type("Usage", (), {"input_tokens": 1, "output_tokens": 1})()

            return Resp()

    monkeypatch.setattr(claude_module, "_check_persistent_budget", lambda: True)
    monkeypatch.setattr(claude_module, "_record_persistent_usage", lambda *args, **kwargs: None)

    client = ClaudeClient.__new__(ClaudeClient)
    client.client = type("FakeClient", (), {"messages": FakeMessages()})()
    client.usage_tracker = FakeUsage()
    client.max_retries = 1
    client.last_call_timing_ms = {}

    assert client._call_claude("sys", "user", max_tokens=1200) == "ok"
    assert captured["max_tokens"] == 1200
    assert client.last_call_timing_ms["max_tokens"] == 1200
    assert client.last_call_timing_ms["provider_round_trip_ms"] >= 0
