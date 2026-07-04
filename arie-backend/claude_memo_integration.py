"""Claude-generated compliance memo — DRAFT, off by default (audit finding H1).

Today the live compliance memo is produced by the deterministic Python builder
in ``memo_handler.build_compliance_memo()``. The Claude memo path
(``ClaudeClient.generate_compliance_memo`` / ``select_memo_model``) exists but is
dead code, so the documented "risk-based Sonnet/Opus routing for memo
generation" never actually runs.

This module is a bounded, reviewable first cut that wraps the existing Claude
memo path behind an explicit flag. It is intentionally NOT wired into
``ComplianceMemoHandler`` — the deterministic builder remains the default and
only live path.

Guard rails that MUST hold before/when this is integrated:
  * OFF by default (``ENABLE_CLAUDE_MEMO`` unset ⇒ deterministic path only).
  * The Claude memo must still flow through the existing validation + supervisor
    gates (fail-closed); it is not a shortcut around them.
  * A Claude failure MUST fall back to the deterministic memo / requires_review —
    it must never block decisioning or auto-approve. ``maybe_generate_claude_memo``
    therefore returns ``None`` (not an exception) on any failure so the caller
    keeps the deterministic memo.
"""
import logging
import os
from typing import Any, Dict, Optional

logger = logging.getLogger("arie")


def is_claude_memo_enabled() -> bool:
    """True only when explicitly enabled. Defaults OFF."""
    return os.environ.get("ENABLE_CLAUDE_MEMO", "").strip().lower() in {"1", "true", "yes", "on"}


def maybe_generate_claude_memo(
    application_data: Dict[str, Any],
    agent_results: Dict[str, Any],
    *,
    risk_score: Optional[float] = None,
    risk_level: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """Return a Claude-generated memo dict, or ``None`` to fall back to deterministic.

    Never raises: returns ``None`` when the flag is off or on any error, so the
    caller can always fall back to the deterministic builder (fail-closed).
    """
    if not is_claude_memo_enabled():
        return None
    try:
        from claude_client import ClaudeClient

        client = ClaudeClient()
        score = risk_score if risk_score is not None else application_data.get("risk_score", 50)
        level = risk_level or application_data.get("risk_level") or "MEDIUM"
        selected_model, routing_reason = client.select_memo_model(score, level)

        memo = client.generate_compliance_memo(application_data, agent_results)
        if not isinstance(memo, dict) or not memo.get("sections"):
            logger.warning("claude memo: empty/invalid result; falling back to deterministic builder")
            return None

        memo.setdefault("metadata", {})
        memo["ai_source"] = "claude"
        memo["metadata"]["memo_model"] = selected_model
        memo["metadata"]["memo_model_routing_reason"] = routing_reason
        return memo
    except Exception as exc:  # fail-closed: caller falls back to deterministic memo
        logger.warning("claude memo generation failed (%s); falling back to deterministic builder", exc)
        return None
