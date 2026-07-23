"""PR-D — screening evidence must not fabricate ComplyAdvantage provenance.

Audit finding (PR-D reliability set): the provider reference summary hardcoded
``provider_display_name = "ComplyAdvantage Mesh"`` regardless of the evidence's
actual provider, so evidence with an unknown/absent provider silently read as CA
on the Screening Review surface. This violates the CLAUDE.md provider-source-of-
truth invariant ("unknown provider evidence must remain unknown and must not
default to CA").

Fix: derive ``provider_display_name`` from the resolved provider via
``screening_config.get_provider_display_name`` (docstring: "without fabricating
CA provenance"). ComplyAdvantage output is byte-identical; unknown stays unknown.
"""
import server


def test_unknown_provider_does_not_default_to_complyadvantage():
    refs = server._screening_evidence_reference_summary(
        [{"provider": "", "category": "adverse media"}],
        {"subject_type": "director", "subject_name": "Jane Doe"},
    )
    # Unknown provider resolves to empty and is dropped from the summary (the
    # function omits empty values) — it is never carried forward as CA.
    assert "provider" not in refs, "unknown provider should not resolve to any provider"
    assert refs["provider_display_name"] == "Unknown provider"
    assert "ComplyAdvantage" not in refs["provider_display_name"], (
        "unknown-provider evidence must not read as ComplyAdvantage"
    )


def test_complyadvantage_case_display_name_is_unchanged():
    # Byte-identical to the previous hardcoded value — the frozen-queue workflow
    # output for the normal CA path does not change.
    refs = server._screening_evidence_reference_summary(
        [{"provider": "complyadvantage"}],
        {},
    )
    assert refs["provider"] == "complyadvantage"
    assert refs["provider_display_name"] == "ComplyAdvantage Mesh"


def test_non_ca_known_provider_is_named_honestly_not_ca():
    # A row whose provenance is Sumsub must not be relabelled ComplyAdvantage.
    refs = server._screening_evidence_reference_summary(
        [{"provider": "sumsub"}],
        {"provider": "sumsub"},
    )
    assert "ComplyAdvantage" not in refs["provider_display_name"]
    assert refs["provider_display_name"] == "Sumsub IDV/KYC"
