"""
Onboarda Platform — Centralised Branding Configuration
Sprint 4: Config-driven branding for client portal, back office, and PDF output.

Usage:
    from branding import BRAND

    print(BRAND["portal_name"])       # "Onboarda"
    print(BRAND["backoffice_name"])   # "RegMind"
    print(BRAND["pdf_header"])        # "Onboarda Compliance Report"
    print(BRAND["pdf_footer"])        # "Powered by RegMind"

All external (client-facing) surfaces use "Onboarda".
All internal (back-office, compliance officer) surfaces use "RegMind".
System-level logs and metrics use "regmind" as the technical identifier.
"""

import os

# ═══════════════════════════════════════════════════════════
# BRAND CONFIGURATION — Single source of truth
# ═══════════════════════════════════════════════════════════

BRAND = {
    # ── Client-facing (external) ──
    "portal_name": os.environ.get("BRAND_PORTAL_NAME", "Onboarda"),
    "portal_tagline": os.environ.get("BRAND_PORTAL_TAGLINE", "Compliance. Automated. Explained."),
    "portal_description": "AI-powered compliance onboarding platform for regulated institutions",

    # ── Back office (internal) ──
    "backoffice_name": os.environ.get("BRAND_BACKOFFICE_NAME", "RegMind"),
    "backoffice_tagline": "Intelligent compliance operations",

    # ── Combined / system ──
    "platform_name": "Onboarda",
    "powered_by": "Powered by RegMind",
    "company_legal": "Onboarda Ltd",
    "copyright": f"\u00a9 2026 Onboarda. All rights reserved.",
    "support_email": os.environ.get("BRAND_SUPPORT_EMAIL", "support@onboarda.com"),
    "website": os.environ.get("BRAND_WEBSITE", "https://onboarda.com"),

    # ── PDF output ──
    "pdf_header": "Onboarda Compliance Report",
    "pdf_footer": "Powered by RegMind",
    "pdf_classification": "CONFIDENTIAL",
    "pdf_watermark": None,  # Set to string to enable watermark

    # ── Technical identifiers (logs, metrics, cookies) ──
    "system_id": "regmind",
    "logger_name": "regmind",
    "cookie_prefix": "arie",  # Keep existing cookie name for backward compatibility
    "metric_prefix": "arie",  # Keep existing metric prefix for Prometheus continuity

    # ── Email ──
    "email_from_name": "Onboarda",
    "email_from_address": os.environ.get("BRAND_EMAIL_FROM", "noreply@onboarda.com"),
}


# ═══════════════════════════════════════════════════════════
# CONVENIENCE ACCESSORS
# ═══════════════════════════════════════════════════════════

def portal_name() -> str:
    """Client-facing product name."""
    return BRAND["portal_name"]

def backoffice_name() -> str:
    """Internal compliance officer product name."""
    return BRAND["backoffice_name"]

def powered_by() -> str:
    """Attribution line for footers and generated content."""
    return BRAND["powered_by"]

def pdf_header() -> str:
    """Title for PDF compliance report header."""
    return BRAND["pdf_header"]

def pdf_footer() -> str:
    """Footer text for PDF compliance reports."""
    return BRAND["pdf_footer"]

def system_id() -> str:
    """Technical system identifier for logs and metrics."""
    return BRAND["system_id"]
