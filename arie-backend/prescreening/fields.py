"""
Prescreening field catalog and legacy alias definitions.

Phase 1 scope:
- canonical field names
- legacy/frontend alias maps
- minimal compatibility helpers for normalization
"""

from __future__ import annotations

from copy import deepcopy


CURRENT_SCHEMA_VERSION = "1.0"


SESSION_PRESCREENING_FIELD_MAP = {
    "f-reg-name": "registered_entity_name",
    "f-trade-name": "trading_name",
    "f-reg-address": "registered_address",
    "f-hq-address": "headquarters_address",
    "f-contact-first": "entity_contact_first",
    "f-contact-last": "entity_contact_last",
    "f-email": "entity_contact_email",
    "f-phone-code": "entity_contact_phone_code",
    "f-mobile": "entity_contact_mobile",
    "f-website": "website",
    "f-is-licensed": "has_licence",
    "f-licences": "regulatory_licences",
    "f-licence-number": "licence_number",
    "f-licence-authority": "licence_authority",
    "f-licence-type": "licence_type",
    "f-inc-country": "country_of_incorporation",
    "f-inc-date": "incorporation_date",
    "f-brn": "brn",
    "f-financial-year-end": "financial_year_end",
    "f-annual-turnover": "annual_turnover",
    "f-sector": "sector",
    "f-entity-type": "entity_type",
    "f-ownership-structure": "ownership_structure",
    "f-monthly-volume": "monthly_volume",
    "f-txn-complexity": "transaction_complexity",
    "f-biz-overview": "business_overview",
    "f-source-wealth-type": "source_of_wealth_type",
    "f-source-wealth": "source_of_wealth_detail",
    "f-source-init-type": "source_of_funds_initial_type",
    "f-source-init": "source_of_funds_initial_detail",
    "f-source-ongoing-type": "source_of_funds_ongoing_type",
    "f-source-ongoing": "source_of_funds_ongoing_detail",
    "f-mgmt": "management_overview",
    "f-intro-method": "introduction_method",
    "f-referrer-name": "referrer_name",
}


LEGACY_SESSION_PRESCREENING_FIELD_MAP = {
    "regName": "registered_entity_name",
    "tradeName": "trading_name",
    "regAddress": "registered_address",
    "hqAddress": "headquarters_address",
    "contactFirst": "entity_contact_first",
    "contactLast": "entity_contact_last",
    "contactEmail": "entity_contact_email",
    "phoneCode": "entity_contact_phone_code",
    "mobile": "entity_contact_mobile",
    "website": "website",
    "isLicensed": "has_licence",
    "licences": "regulatory_licences",
    "licenceNumber": "licence_number",
    "licenceAuthority": "licence_authority",
    "licenceType": "licence_type",
    "incCountry": "country_of_incorporation",
    "incDate": "incorporation_date",
    "brn": "brn",
    "financialYearEnd": "financial_year_end",
    "annualTurnover": "annual_turnover",
    "sector": "sector",
    "entityType": "entity_type",
    "ownershipStructure": "ownership_structure",
    "monthlyVolume": "monthly_volume",
    "expectedVolume": "expected_volume",
    "txnComplexity": "transaction_complexity",
    "bizOverview": "business_overview",
    "businessOverview": "business_overview",
    "servicesRequired": "services_required",
    "countriesOfOperation": "countries_of_operation",
    "targetMarkets": "target_markets",
    "accountPurposes": "account_purposes",
    "hasBank": "existing_bank_account",
    "bankName": "existing_bank_name",
    "currencies": "currencies",
    "sourceWealthType": "source_of_wealth_type",
    "sourceWealth": "source_of_wealth_detail",
    "sourceInitType": "source_of_funds_initial_type",
    "sourceInit": "source_of_funds_initial_detail",
    "sourceOngoingType": "source_of_funds_ongoing_type",
    "sourceOngoing": "source_of_funds_ongoing_detail",
    "mgmt": "management_overview",
    "managementOverview": "management_overview",
    "introMethod": "introduction_method",
    "referrerName": "referrer_name",
}


CANONICAL_TEMPLATE = {
    "entity": {
        "legal_name": "",
        "trading_name": "",
        "type": "",
        "incorporation_country": "",
        "registration_number": "",
        "incorporation_date": "",
        "website": "",
        "registered_address": {"full_text": ""},
        "headquarters_address": {"full_text": ""},
        "contact": {
            "first_name": "",
            "last_name": "",
            "email": "",
            "phone_code": "",
            "mobile": "",
        },
    },
    "business": {
        "sector": "",
        "activity_description": "",
        "management_overview": "",
        "services": {"primary_services": []},
        "account_purposes": [],
    },
    "ownership": {
        "structure_type": "",
        "no_ubo_reason": "",
        "total_declared_pct": None,
    },
    "parties": {
        "directors": [],
        "ubos": [],
        "intermediary_shareholders": [],
    },
    "transaction": {
        "operating_countries": [],
        "target_markets": [],
        "currencies": [],
        "corridor_complexity": "",
        "cross_border_expected": False,
        "expected_monthly_volume": {
            "band_legacy": "",
        },
        "estimated_activity": {
            "inflows": {},
            "outflows": {},
        },
        "expected_average_transaction": None,
        "expected_highest_transaction": None,
    },
    "wealth": {
        "source_of_wealth": {
            "type": "",
            "detail": "",
            "summary": "",
        }
    },
    "funds": {
        "initial_source": {},
        "ongoing_source": {},
        "summary": "",
    },
    "banking": {
        "existing_account": "",
        "bank_name": "",
    },
    "licensing": {
        "legacy_text": "",
        "has_licence": None,
        "regulated_activity_declared": None,
        "licences": [],
    },
    "delivery_channel": {
        "introduction_method": "",
        "referrer_name": "",
    },
    "submission": {
        "schema_version": CURRENT_SCHEMA_VERSION,
        "consents": {},
    },
}


def new_canonical_payload():
    return deepcopy(CANONICAL_TEMPLATE)
