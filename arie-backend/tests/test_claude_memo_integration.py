"""Tests for the H1 draft Claude-memo wrapper (claude_memo_integration.py).

Verifies the guard rails: OFF by default, risk-based model routing when enabled,
and fail-closed (returns None) so the caller keeps the deterministic memo.
The wrapper is a draft and is not wired into the live memo handler.
"""


class _FakeClaudeClient:
    ROUTING_MODELS = {"fast": "claude-sonnet-test", "thorough": "claude-opus-test"}

    def select_memo_model(self, risk_score, risk_level):
        level = (risk_level or "MEDIUM").upper()
        score = risk_score or 50
        if level in ("HIGH", "VERY_HIGH") or score >= 55:
            return self.ROUTING_MODELS["thorough"], "routed to opus"
        return self.ROUTING_MODELS["fast"], "routed to sonnet"

    def generate_compliance_memo(self, application_data, agent_results):
        return {"sections": {"executive_summary": {"content": "ok"}}, "metadata": {}}


class _RaisingClaudeClient(_FakeClaudeClient):
    def generate_compliance_memo(self, application_data, agent_results):
        raise RuntimeError("anthropic outage")


def test_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ENABLE_CLAUDE_MEMO", raising=False)
    import claude_memo_integration as cmi
    assert cmi.is_claude_memo_enabled() is False
    assert cmi.maybe_generate_claude_memo({}, {}) is None


def test_enabled_routes_high_risk_to_opus(monkeypatch):
    monkeypatch.setenv("ENABLE_CLAUDE_MEMO", "true")
    monkeypatch.setattr("claude_client.ClaudeClient", _FakeClaudeClient, raising=False)
    import claude_memo_integration as cmi
    memo = cmi.maybe_generate_claude_memo({}, {}, risk_score=70, risk_level="HIGH")
    assert memo is not None
    assert memo["ai_source"] == "claude"
    assert memo["metadata"]["memo_model"] == "claude-opus-test"


def test_enabled_routes_low_risk_to_sonnet(monkeypatch):
    monkeypatch.setenv("ENABLE_CLAUDE_MEMO", "on")
    monkeypatch.setattr("claude_client.ClaudeClient", _FakeClaudeClient, raising=False)
    import claude_memo_integration as cmi
    memo = cmi.maybe_generate_claude_memo({}, {}, risk_score=20, risk_level="LOW")
    assert memo["metadata"]["memo_model"] == "claude-sonnet-test"


def test_fail_closed_on_claude_error(monkeypatch):
    monkeypatch.setenv("ENABLE_CLAUDE_MEMO", "1")
    monkeypatch.setattr("claude_client.ClaudeClient", _RaisingClaudeClient, raising=False)
    import claude_memo_integration as cmi
    # Must return None (not raise) so the caller falls back to the deterministic memo.
    assert cmi.maybe_generate_claude_memo({}, {}, risk_score=70, risk_level="HIGH") is None
