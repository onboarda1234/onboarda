"""Backend contract for the party-field correction expansion (option a, 2026-07-17).

The party card displays country_of_residence, residential_address and (directors)
date_of_appointment; these are now offered by the correction UI. The backend
whitelists and columns already existed — these tests pin the decided semantics:

- country_of_residence is treated like nationality: tier2, risk-relevant,
  memo-visible, and validated against the controlled country option set
  (fail-closed: correcting it recomputes risk and stales the memo).
- residential_address and date_of_appointment stay tier3 (audit-only): they
  feed neither the risk engine nor the memo.
"""


def test_new_fields_remain_whitelisted_per_target(temp_db):
    from server import OFFICER_CORRECTION_DIRECTOR_FIELDS, OFFICER_CORRECTION_UBO_FIELDS

    for field in ("country_of_residence", "residential_address", "date_of_appointment"):
        assert field in OFFICER_CORRECTION_DIRECTOR_FIELDS
    assert "country_of_residence" in OFFICER_CORRECTION_UBO_FIELDS
    assert "residential_address" in OFFICER_CORRECTION_UBO_FIELDS
    assert "date_of_appointment" not in OFFICER_CORRECTION_UBO_FIELDS


def test_country_of_residence_mirrors_nationality_materiality(temp_db):
    from server import (
        OFFICER_CORRECTION_MEMO_VISIBLE_FIELDS,
        OFFICER_CORRECTION_RISK_RELEVANT_FIELDS,
        OFFICER_CORRECTION_TIER1_FIELDS,
        OFFICER_CORRECTION_TIER2_FIELDS,
    )

    assert "country_of_residence" in OFFICER_CORRECTION_TIER2_FIELDS
    assert "country_of_residence" in OFFICER_CORRECTION_RISK_RELEVANT_FIELDS
    assert "country_of_residence" in OFFICER_CORRECTION_MEMO_VISIBLE_FIELDS
    assert "country_of_residence" not in OFFICER_CORRECTION_TIER1_FIELDS


def test_address_and_appointment_stay_tier3_audit_only(temp_db):
    from server import (
        OFFICER_CORRECTION_MEMO_VISIBLE_FIELDS,
        OFFICER_CORRECTION_RISK_RELEVANT_FIELDS,
        OFFICER_CORRECTION_TIER1_FIELDS,
        OFFICER_CORRECTION_TIER2_FIELDS,
    )

    for field in ("residential_address", "date_of_appointment"):
        assert field not in OFFICER_CORRECTION_TIER1_FIELDS, field
        assert field not in OFFICER_CORRECTION_TIER2_FIELDS, field
        assert field not in OFFICER_CORRECTION_RISK_RELEVANT_FIELDS, field
        assert field not in OFFICER_CORRECTION_MEMO_VISIBLE_FIELDS, field


def test_country_of_residence_is_controlled_and_canonicalized(temp_db):
    from server import (
        CONTROLLED_CORRECTION_OPTION_SETS,
        PORTAL_COUNTRY_OPTIONS,
        _canonicalize_controlled_option,
    )

    assert CONTROLLED_CORRECTION_OPTION_SETS["country_of_residence"] is PORTAL_COUNTRY_OPTIONS
    # Same alias handling as the other country fields.
    assert _canonicalize_controlled_option("country_of_residence", "uk") == "United Kingdom"
    assert _canonicalize_controlled_option("country_of_residence", "United Kingdom") == "United Kingdom"


def test_materiality_derivation_for_new_fields(temp_db):
    from server import _system_derived_officer_correction_materiality

    assert (
        _system_derived_officer_correction_materiality(
            "director", {"country_of_residence": "United Kingdom"}
        )
        == "tier2"
    )
    for field in ("residential_address", "date_of_appointment"):
        assert (
            _system_derived_officer_correction_materiality("director", {field: "x"})
            == "tier3"
        ), field
