"""
ARIE Finance — Screening & API Integrations
Extracted from server.py during Sprint 2 monolith decomposition.

Provides:
    - Sumsub AML screening (screen_sumsub_aml)
    - OpenCorporates company lookup (lookup_opencorporates)
    - IP geolocation (geolocate_ip)
    - Sumsub KYC integration (create applicant, tokens, status, documents, webhooks)
    - Full screening pipeline (run_full_screening)
"""
import os
import json
import time
import hmac
import hashlib
import base64
import random
import logging
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor

import requests

from config import (
    OPENCORPORATES_API_URL,
    IP_GEOLOCATION_API_URL,
    SUMSUB_WEBHOOK_SECRET,
)
from rule_engine import SANCTIONED, FATF_BLACK, FATF_GREY
from sumsub_client import get_sumsub_client
from environment import (
    ENV, is_production, is_staging, is_demo,
    get_sumsub_base_url, get_sumsub_app_token, get_sumsub_secret_key,
    get_sumsub_level_name, get_sumsub_individual_level_name,
    get_sumsub_company_level_name, get_sumsub_aml_level_name,
    get_opencorporates_api_key, get_ip_geolocation_api_key,
)

logger = logging.getLogger("arie")

ENVIRONMENT = ENV

# Sprint 2.5: Centralized config — delegated to environment.py getters.
# Module-level aliases for backward compatibility with existing function bodies.
OPENCORPORATES_API_KEY = get_opencorporates_api_key()
IP_GEOLOCATION_API_KEY = get_ip_geolocation_api_key()
SUMSUB_APP_TOKEN = get_sumsub_app_token()
SUMSUB_SECRET_KEY = get_sumsub_secret_key()
SUMSUB_BASE_URL = get_sumsub_base_url()
SUMSUB_LEVEL_NAME = get_sumsub_level_name()
SUMSUB_INDIVIDUAL_LEVEL_NAME = get_sumsub_individual_level_name()
SUMSUB_COMPANY_LEVEL_NAME = get_sumsub_company_level_name()
SUMSUB_AML_LEVEL_NAME = get_sumsub_aml_level_name()

def screen_sumsub_aml(name, birth_date=None, nationality=None, entity_type="Person"):
    """
    Screen a person or entity against Sumsub AML (sanctions, PEP, watchlists).

    **Person AML** (directors / UBOs):
        Uses the ``aml-screening`` level (``SUMSUB_AML_LEVEL_NAME``).
        1. Create applicant with fixedInfo (firstName, lastName, dob).
        2. Trigger the check:  ``POST /resources/applicants/{id}/status/pending``
        3. Poll the review:    ``GET  /resources/applicants/{id}/one``
        4. Map ``reviewAnswer``:
           - GREEN  → ``api_status=live, matched=false``
           - RED    → ``api_status=live, matched=true``
           - still running → ``api_status=pending``
           - API failure   → ``api_status=error``

    **Company screening**: unchanged — short-circuits to ``not_configured``
    when no company KYB level is provisioned.

    Returns: { matched: bool, results: [...], source: str, api_status: str }
    """
    # ── Company screening: short-circuit when no company/KYB level exists ──
    company_level = get_sumsub_company_level_name() if entity_type == "Company" else ""
    if entity_type == "Company" and not company_level:
        logger.info(
            "Sumsub company KYB level not configured — returning not_configured for '%s'", name
        )
        return {
            "matched": False,
            "results": [],
            "source": "sumsub",
            "api_status": "not_configured",
            "reason": "Sumsub company KYB level not configured",
            "screened_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        }

    try:
        import hashlib as _hashlib
        ext_id = f"{entity_type}_{_hashlib.md5(name.encode()).hexdigest()[:12]}"
        parts = name.strip().split(" ", 1)
        first = parts[0] if parts else ""
        last = parts[1] if len(parts) > 1 else ""

        # ── Choose the Sumsub level ──
        if entity_type == "Company":
            level = company_level
        else:
            # Person AML: use the dedicated AML-only level
            level = get_sumsub_aml_level_name()

        # Step 1: Create / retrieve applicant on the AML level
        applicant_result = sumsub_create_applicant(
            external_user_id=ext_id,
            first_name=first,
            last_name=last,
            dob=birth_date,
            country=nationality,
            level_name=level,
        )

        if not applicant_result.get("applicant_id"):
            logger.warning("Sumsub AML: Failed to create/retrieve applicant for '%s'", name)
            # Always return error — never coerce missing applicant into pending
            return {
                "matched": False,
                "results": [],
                "source": "sumsub" if applicant_result.get("api_status") == "error" else "simulated",
                "api_status": "error" if applicant_result.get("api_status") == "error" else "simulated",
                "error": applicant_result.get("error", "Applicant creation failed — no applicant_id"),
                "screened_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            }

        applicant_id = applicant_result["applicant_id"]

        client = get_sumsub_client()

        # Step 2: Trigger the check (POST .../status/pending)
        trigger_result = client.request_check(applicant_id)
        if trigger_result.get("api_status") == "error":
            return {
                "matched": False,
                "results": [],
                "source": "sumsub",
                "api_status": "error",
                "error": trigger_result.get("error", "request_check failed"),
                "screened_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            }

        # Step 3: Poll the review status (GET .../one)
        review = client.get_applicant_review_status(applicant_id)

        if review.get("api_status") == "error":
            return {
                "matched": False,
                "results": [],
                "source": "sumsub",
                "api_status": "error",
                "error": review.get("error", "get_applicant_review_status failed"),
                "screened_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            }

        if review.get("source") == "simulated":
            return _simulate_aml_screen(name)

        # Step 4: Map the review answer
        review_answer = (review.get("review_answer") or "").upper()
        review_api_status = review.get("api_status", "pending")

        if review_api_status == "pending":
            return {
                "matched": False,
                "results": [],
                "source": "sumsub",
                "api_status": "pending",
                "screened_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            }

        # Completed review
        if review_answer == "GREEN":
            return {
                "matched": False,
                "results": [],
                "source": "sumsub",
                "api_status": "live",
                "screened_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            }
        elif review_answer == "RED":
            return {
                "matched": True,
                "results": [{
                    "match_score": 100.0,
                    "matched_name": name,
                    "datasets": ["AML"],
                    "schema": entity_type,
                    "topics": [],
                    "countries": [],
                    "sanctions_list": "",
                    "is_pep": False,
                    "is_sanctioned": False,
                }],
                "source": "sumsub",
                "api_status": "live",
                "screened_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            }
        else:
            # Unknown review answer — treat as pending
            return {
                "matched": False,
                "results": [],
                "source": "sumsub",
                "api_status": "pending",
                "screened_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            }

    except Exception as e:
        logger.error("Sumsub AML screening error: %s", e)
        client = None
        try:
            client = get_sumsub_client()
        except Exception:
            pass
        if client and client.is_configured:
            return {
                "matched": False,
                "results": [],
                "source": "sumsub",
                "api_status": "error",
                "error": f"Sumsub AML screening failed: {str(e)[:200]}",
                "screened_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
            }
        return _simulate_aml_screen(name)


def _simulate_aml_screen(name, note="No Sumsub credentials configured — simulated result"):
    """Fallback: realistic simulation when Sumsub not configured."""
    if is_production():
        logger.error("BLOCKED: Mock screening attempted in production")
        return {"matched": False, "results": [], "source": "blocked", "api_status": "error", "note": "Mock fallback blocked in production", "screened_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")}
    import random
    is_hit = random.random() < 0.08  # 8% simulated hit rate
    results = []
    if is_hit:
        results.append({
            "match_score": round(random.uniform(68, 95), 1),
            "matched_name": name,
            "datasets": ["aml-simulated"],
            "schema": "Person",
            "topics": ["pep"] if random.random() < 0.5 else ["sanction"],
            "countries": [],
            "sanctions_list": "Simulated AML List",
            "is_pep": random.random() < 0.5,
            "is_sanctioned": random.random() < 0.3,
        })
    return {
        "matched": is_hit,
        "results": results,
        "source": "simulated",
        "api_status": "simulated",
        "note": note,
        "screened_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    }


def lookup_opencorporates(company_name, jurisdiction=None):
    """
    Look up a company via Open Corporates registry.
    Returns: { found: bool, companies: [...], source: "opencorporates"|"simulated" }
    """
    if not OPENCORPORATES_API_KEY:
        logger.info(f"OpenCorporates: No API key — simulating lookup for '{company_name}'")
        return _simulate_company_lookup(company_name)

    try:
        params = {"q": company_name, "api_token": OPENCORPORATES_API_KEY}
        if jurisdiction:
            params["jurisdiction_code"] = jurisdiction.lower()

        resp = requests.get(
            f"{OPENCORPORATES_API_URL}/companies/search",
            params=params,
            timeout=15
        )

        if resp.status_code == 200:
            data = resp.json()
            companies_raw = data.get("results", {}).get("companies", [])
            companies = []
            for c_wrap in companies_raw[:5]:  # Top 5 matches
                c = c_wrap.get("company", {})
                companies.append({
                    "name": c.get("name", ""),
                    "company_number": c.get("company_number", ""),
                    "jurisdiction": c.get("jurisdiction_code", ""),
                    "incorporation_date": c.get("incorporation_date", ""),
                    "dissolution_date": c.get("dissolution_date"),
                    "company_type": c.get("company_type", ""),
                    "registry_url": c.get("registry_url", ""),
                    "status": c.get("current_status", ""),
                    "registered_address": c.get("registered_address_in_full", ""),
                    "opencorporates_url": c.get("opencorporates_url", ""),
                })

            return {
                "found": len(companies) > 0,
                "companies": companies,
                "total_results": data.get("results", {}).get("total_count", 0),
                "source": "opencorporates",
                "api_status": "live",
                "searched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            }
        elif resp.status_code == 401:
            logger.warning("OpenCorporates: Invalid API key — falling back to simulation")
            return _simulate_company_lookup(company_name, note="API key invalid — simulated result")
        else:
            logger.warning(f"OpenCorporates: HTTP {resp.status_code}")
            return _simulate_company_lookup(company_name, note=f"API returned {resp.status_code} — simulated")

    except Exception as e:
        logger.error(f"OpenCorporates error: {e}")
        return _simulate_company_lookup(company_name, note=f"API error — simulated result")


def _simulate_company_lookup(company_name, note="No API key configured — simulated result"):
    """Fallback: simulated company registry lookup. C-04: BLOCKED in production."""
    if is_production():
        logger.error("BLOCKED: Mock company lookup attempted in production")
        return {"found": False, "companies": [], "source": "blocked", "api_status": "error", "note": "Mock fallback blocked in production"}
    import random
    found = random.random() < 0.85  # 85% chance found
    companies = []
    if found:
        companies.append({
            "name": company_name,
            "company_number": f"C{random.randint(10000,99999)}",
            "jurisdiction": "mu",
            "incorporation_date": f"20{random.randint(10,24)}-{random.randint(1,12):02d}-{random.randint(1,28):02d}",
            "dissolution_date": None,
            "company_type": random.choice(["Private Company Limited by Shares", "Global Business Company"]),
            "registry_url": "",
            "status": random.choice(["Active", "Active"]),
            "registered_address": "Port Louis, Mauritius",
            "opencorporates_url": "",
        })
    return {
        "found": found,
        "companies": companies,
        "total_results": 1 if found else 0,
        "source": "simulated",
        "api_status": "simulated",
        "note": note,
        "searched_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    }


def geolocate_ip(ip_address):
    """
    Look up geolocation and risk data for an IP address.
    Returns: { country, city, is_vpn, is_proxy, risk_level, source }
    """
    if not ip_address or ip_address in ("127.0.0.1", "::1", "0.0.0.0"):
        return {
            "country": "Local",
            "country_code": "XX",
            "city": "Localhost",
            "region": "",
            "is_vpn": False,
            "is_proxy": False,
            "is_tor": False,
            "risk_level": "LOW",
            "source": "local",
            "api_status": "skipped",
            "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
        }

    try:
        # Use ipapi.co (free tier: 1000 req/day, no key needed for basic)
        url = f"{IP_GEOLOCATION_API_URL}/{ip_address}/json/"
        if IP_GEOLOCATION_API_KEY:
            url += f"?key={IP_GEOLOCATION_API_KEY}"

        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("error"):
                return _simulate_ip_geolocation(ip_address, note=data.get("reason", "API error"))

            country_code = (data.get("country_code") or "").upper()
            # Determine IP risk level
            ip_risk = "LOW"
            if country_code in [c.upper() for c in SANCTIONED]:
                ip_risk = "VERY_HIGH"
            elif country_code in [c.upper() for c in FATF_BLACK]:
                ip_risk = "HIGH"
            elif country_code in [c.upper() for c in FATF_GREY]:
                ip_risk = "MEDIUM"

            return {
                "country": data.get("country_name", "Unknown"),
                "country_code": country_code,
                "city": data.get("city", ""),
                "region": data.get("region", ""),
                "latitude": data.get("latitude"),
                "longitude": data.get("longitude"),
                "org": data.get("org", ""),
                "asn": data.get("asn", ""),
                "is_vpn": False,  # Basic API doesn't detect VPN
                "is_proxy": False,
                "is_tor": False,
                "risk_level": ip_risk,
                "source": "ipapi",
                "api_status": "live",
                "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
            }
        else:
            return _simulate_ip_geolocation(ip_address, note=f"API returned {resp.status_code}")

    except Exception as e:
        logger.error(f"IP Geolocation error: {e}")
        return _simulate_ip_geolocation(ip_address, note=f"API error — simulated")


def _simulate_ip_geolocation(ip_address, note="Simulated result"):
    """Fallback: simulated IP geolocation. C-04: BLOCKED in production."""
    if is_production():
        logger.error("BLOCKED: Mock IP geolocation attempted in production")
        return {"country": "Unknown", "country_code": "XX", "source": "blocked", "api_status": "error", "note": "Mock fallback blocked in production"}
    import random
    countries = [
        ("Mauritius", "MU"), ("United Kingdom", "GB"), ("France", "FR"),
        ("India", "IN"), ("South Africa", "ZA"), ("Singapore", "SG")
    ]
    country, code = random.choice(countries)
    return {
        "country": country,
        "country_code": code,
        "city": "Simulated City",
        "region": "",
        "is_vpn": random.random() < 0.05,
        "is_proxy": random.random() < 0.03,
        "is_tor": False,
        "risk_level": "LOW",
        "source": "simulated",
        "api_status": "simulated",
        "note": note,
        "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    }


# ══════════════════════════════════════════════════════════
# SUMSUB KYC / IDENTITY VERIFICATION
# ══════════════════════════════════════════════════════════

def _sumsub_sign(method, url_path, body=b""):
    """
    Create HMAC-SHA256 signature for Sumsub API requests.
    Returns headers dict with X-App-Token, X-App-Access-Ts, X-App-Access-Sig.
    """
    ts = str(int(time.time()))
    # Build signature payload: ts + method + path + body
    sig_payload = ts.encode("utf-8") + method.upper().encode("utf-8") + url_path.encode("utf-8")
    if body:
        sig_payload += body if isinstance(body, bytes) else body.encode("utf-8")
    sig = hmac.new(
        SUMSUB_SECRET_KEY.encode("utf-8"),
        sig_payload,
        hashlib.sha256
    ).hexdigest()
    return {
        "X-App-Token": SUMSUB_APP_TOKEN,
        "X-App-Access-Ts": ts,
        "X-App-Access-Sig": sig,
    }


def sumsub_create_applicant(external_user_id, first_name=None, last_name=None,
                            email=None, phone=None, dob=None, country=None,
                            level_name=None):
    """
    Create a Sumsub applicant for KYC verification.
    Thin wrapper — delegates to SumsubClient for retry, cost tracking, and consistent error handling.
    Returns: { applicant_id, external_user_id, status, source }
    """
    client = get_sumsub_client()
    info = {}
    if first_name or last_name or dob or country:
        info = {}
        if first_name: info["firstName"] = first_name
        if last_name: info["lastName"] = last_name
        if dob: info["dob"] = dob
        if country: info["country"] = country
    return client.create_applicant(
        external_user_id=external_user_id,
        first_name=first_name,
        last_name=last_name,
        level_name=level_name,
        dob=dob,
        country=country,
        info=info if info else None,
    )


def sumsub_get_applicant_by_external_id(external_user_id):
    """Retrieve an existing Sumsub applicant by external user ID.
    Thin wrapper — delegates to SumsubClient."""
    client = get_sumsub_client()
    return client.get_applicant_by_external_id(external_user_id)


def sumsub_generate_access_token(external_user_id, level_name=None):
    """Generate an access token for the Sumsub WebSDK.
    Thin wrapper — delegates to SumsubClient."""
    client = get_sumsub_client()
    return client.generate_access_token(external_user_id, level_name=level_name)


def sumsub_get_applicant_status(applicant_id):
    """Get the verification status of a Sumsub applicant.
    Thin wrapper — delegates to SumsubClient."""
    client = get_sumsub_client()
    return client.get_applicant_status(applicant_id)


def sumsub_add_document(applicant_id, doc_type, country, file_path=None, file_data=None, file_name="document.pdf"):
    """Add an identity document to a Sumsub applicant.
    Thin wrapper — resolves file content then delegates to SumsubClient."""
    # Resolve file content from path or base64 data
    content = None
    if file_path and os.path.exists(file_path):
        with open(file_path, "rb") as f:
            content = f.read()
    elif file_data:
        content = base64.b64decode(file_data) if isinstance(file_data, str) else file_data

    if not content:
        return {"status": "error", "message": "No document content provided", "source": "sumsub"}

    client = get_sumsub_client()
    return client.add_document(
        applicant_id=applicant_id,
        doc_type=doc_type,
        file_data=content,
        filename=file_name,
        country=country,
    )


def sumsub_verify_webhook(body_bytes, signature_header, digest_alg=None):
    """Verify a Sumsub webhook signature against an algorithm allowlist.

    PR 14 (F-2): Sumsub signs webhooks with one of a known set of HMAC
    algorithms and advertises which via the ``X-Payload-Digest-Alg`` header
    (e.g. ``HMAC_SHA256_HEX`` or ``HMAC_SHA512_HEX``). The caller (webhook
    handler) is expected to pass this header value as ``digest_alg``. We
    hard-gate to an allowlist so that:

      1. Unknown or missing algorithms are rejected fail-closed rather than
         silently falling back to SHA256 — an attacker cannot downgrade or
         strip the header to force a weaker comparison.
      2. Adding a new algorithm is an explicit, reviewable code change — not
         an implicit fallthrough.

    For backward compatibility with deliveries that do not carry the header
    (older Sumsub payloads, internal test fixtures), ``digest_alg=None`` is
    treated as ``HMAC_SHA256_HEX``. This preserves existing behaviour for
    legacy callers while enabling strict gating for new ones.
    """
    # Lazy import to avoid a circular dependency at module-import time.
    from utils.sumsub_validation import ALLOWED_DIGEST_ALGS

    if not SUMSUB_WEBHOOK_SECRET:
        if ENVIRONMENT in ("production", "staging"):
            logger.error(
                f"Sumsub webhook secret not configured in {ENVIRONMENT} — REJECTING webhook. "
                "Set SUMSUB_WEBHOOK_SECRET environment variable."
            )
            return False
        logger.warning("Sumsub webhook secret not configured — accepting in dev/demo mode only")
        return True

    # Resolve the hash constructor from the allowlist. Default to SHA256 for
    # deliveries with no ``X-Payload-Digest-Alg`` header (preserves legacy
    # behaviour). Unknown algorithms are rejected fail-closed.
    _alg_key = digest_alg or "HMAC_SHA256_HEX"
    _hash_ctor = ALLOWED_DIGEST_ALGS.get(_alg_key)
    if _hash_ctor is None:
        logger.warning(
            "Sumsub webhook: unknown digest algorithm %r — rejecting fail-closed. "
            "Allowed: %s",
            _alg_key,
            sorted(ALLOWED_DIGEST_ALGS.keys()),
        )
        return False

    expected = hmac.new(
        SUMSUB_WEBHOOK_SECRET.encode("utf-8"),
        body_bytes,
        _hash_ctor,
    ).hexdigest()

    # Staging-safe diagnostic — partial values only, never full secrets
    logger.info(
        "Sumsub webhook HMAC: body_len=%d alg=%s computed_prefix=%s received_prefix=%s match=%s",
        len(body_bytes),
        _alg_key,
        expected[:8],
        (signature_header or "")[:8],
        expected == (signature_header or ""),
    )

    return hmac.compare_digest(expected, signature_header or "")


# ── Sumsub simulation fallbacks ──────────────────────────

def _simulate_sumsub_applicant(external_user_id, first_name=None, last_name=None, note="No Sumsub credentials configured"):
    """Fallback: simulated applicant creation. C-04: BLOCKED in production."""
    if is_production():
        logger.error("BLOCKED: Mock Sumsub applicant creation attempted in production")
        return {"applicant_id": None, "source": "blocked", "api_status": "error", "note": "Mock fallback blocked in production"}
    sim_id = f"sim_{hashlib.md5(external_user_id.encode()).hexdigest()[:16]}"
    return {
        "applicant_id": sim_id,
        "external_user_id": external_user_id,
        "status": "init",
        "inspection_id": f"insp_{sim_id[:12]}",
        "level_name": SUMSUB_LEVEL_NAME,
        "source": "simulated",
        "api_status": "simulated",
        "note": note,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
    }


def _simulate_sumsub_token(external_user_id, note="No Sumsub credentials configured"):
    """Fallback: simulated access token. C-04: BLOCKED in production."""
    if is_production():
        logger.error("BLOCKED: Mock Sumsub token generation attempted in production")
        return {"token": None, "source": "blocked", "api_status": "error", "note": "Mock fallback blocked in production"}
    token = base64.b64encode(f"sim_token_{external_user_id}_{int(time.time())}".encode()).decode()
    return {
        "token": token,
        "user_id": external_user_id,
        "level_name": SUMSUB_LEVEL_NAME,
        "source": "simulated",
        "api_status": "simulated",
        "note": note,
    }


def _simulate_sumsub_status(applicant_id, note="No Sumsub credentials configured"):
    """Fallback: simulated verification status. C-04: BLOCKED in production."""
    if is_production():
        logger.error("BLOCKED: Mock Sumsub status check attempted in production")
        return {"applicant_id": applicant_id, "status": "blocked", "source": "blocked", "api_status": "error", "note": "Mock fallback blocked in production"}
    import random
    statuses = ["init", "pending", "completed"]
    answers = ["", "", "GREEN"]
    idx = random.randint(0, 2)
    return {
        "applicant_id": applicant_id,
        "external_user_id": "",
        "status": statuses[idx],
        "review_answer": answers[idx],
        "rejection_labels": [],
        "moderation_comment": "",
        "verification_steps": {
            "IDENTITY": {"reviewResult": {"reviewAnswer": answers[idx]} if idx == 2 else {}},
            "SELFIE": {"reviewResult": {"reviewAnswer": answers[idx]} if idx == 2 else {}},
        },
        "source": "simulated",
        "api_status": "simulated",
        "note": note,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
    }


class ScreeningProviderError(Exception):
    """Raised when a critical screening provider fails and the submission cannot proceed."""
    pass


def _safe_future_result(future, timeout, source_label, company_name=""):
    """
    Safely collect a future result with bounded timeout.
    Returns (result, error_info) — error_info is None on success.
    On failure returns a degraded marker dict and the error string.
    """
    try:
        result = future.result(timeout=timeout)
        return result, None
    except Exception as e:
        logger.error(
            "Screening source '%s' failed: %s (company=%s)",
            source_label, str(e)[:300], company_name,
        )
        degraded = {
            "source": source_label,
            "api_status": "unavailable",
            "error": f"Provider temporarily unavailable: {str(e)[:200]}",
            "degraded": True,
        }
        return degraded, str(e)[:300]


def run_full_screening(application_data, directors, ubos, client_ip=None):
    """
    Run all screening agents in parallel against an application.
    Uses ThreadPoolExecutor for concurrent HTTP API calls.

    Resilience: individual provider failures are caught per-source and
    recorded as degraded markers.  Only a complete inability to produce
    any screening data raises ScreeningProviderError.
    """
    company_name = application_data.get("company_name", "")
    country = application_data.get("country", "")

    report = {
        "screened_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "company_screening": {},
        "director_screenings": [],
        "ubo_screenings": [],
        "ip_geolocation": {},
        "overall_flags": [],
        "total_hits": 0,
        "degraded_sources": [],
    }

    # Map jurisdiction
    jurisdiction = None
    if country:
        jur_map = {"mauritius": "mu", "united kingdom": "gb", "uk": "gb", "france": "fr",
                   "singapore": "sg", "india": "in", "hong kong": "hk", "usa": "us",
                   "united states": "us", "south africa": "za", "germany": "de"}
        jurisdiction = jur_map.get(country.lower())

    # ── Parallel API calls ──
    with ThreadPoolExecutor(max_workers=8) as executor:
        # Submit all screening tasks concurrently
        company_future = executor.submit(lookup_opencorporates, company_name, jurisdiction)
        company_sanctions_future = executor.submit(screen_sumsub_aml, company_name, entity_type="Company")

        director_futures = []
        # W2-1: Track screened persons to avoid duplicate AML screening
        screened_persons = set()  # dedup_key → True
        for d in directors:
            d_name = d.get("full_name", "")
            if d_name:
                dedup_key = " ".join(d_name.lower().split()) + "|" + (d.get("date_of_birth") or "").strip()
                screened_persons.add(dedup_key)
                f = executor.submit(screen_sumsub_aml, d_name, birth_date=d.get("date_of_birth"), nationality=d.get("nationality"), entity_type="Person")
                director_futures.append((d, f))

        ubo_futures = []
        for u in ubos:
            u_name = u.get("full_name", "")
            if u_name:
                dedup_key = " ".join(u_name.lower().split()) + "|" + (u.get("date_of_birth") or "").strip()
                if dedup_key in screened_persons:
                    # Same person already screened as director — skip duplicate AML call
                    continue
                f = executor.submit(screen_sumsub_aml, u_name, birth_date=u.get("date_of_birth"), nationality=u.get("nationality"), entity_type="Person")
                ubo_futures.append((u, f))

        ip_future = executor.submit(geolocate_ip, client_ip) if client_ip else None

        kyc_futures = []
        # W2-1: Deduplicate persons across director and UBO roles to avoid duplicate Sumsub applicants.
        # Use normalised name + DOB as the dedup key for conservative matching.
        all_persons = [(d, "director") for d in directors] + [(u, "ubo") for u in ubos]
        seen_person_keys = {}  # dedup_key → (person, ptype, ext_id, future)
        for person, ptype in all_persons:
            p_name = person.get("full_name", "")
            if not p_name:
                continue
            # Build a conservative dedup key: normalised lowercase name + DOB
            dedup_name = " ".join(p_name.lower().split())
            dedup_dob = (person.get("date_of_birth") or "").strip()
            dedup_key = f"{dedup_name}|{dedup_dob}"
            if dedup_key in seen_person_keys:
                # Same person already queued — reuse the existing Sumsub applicant
                existing = seen_person_keys[dedup_key]
                kyc_futures.append((person, ptype, p_name, existing[3]))
                continue
            # Use a role-agnostic ext_id based on name + DOB hash to avoid cross-person collisions
            hash_input = p_name + "|" + dedup_dob
            ext_id = person.get("email", "") or f"person_{hashlib.md5(hash_input.encode()).hexdigest()[:12]}"
            parts = p_name.strip().split(" ", 1)
            first = parts[0] if parts else ""
            last = parts[1] if len(parts) > 1 else ""
            f = executor.submit(sumsub_create_applicant,
                external_user_id=ext_id, first_name=first, last_name=last,
                country=person.get("nationality", ""))
            seen_person_keys[dedup_key] = (person, ptype, p_name, f)
            kyc_futures.append((person, ptype, p_name, f))

        # ── Collect results with per-source error handling ──

        # Company registry lookup (non-critical — degrade gracefully)
        company_result, company_err = _safe_future_result(
            company_future, timeout=30, source_label="opencorporates", company_name=company_name)
        if company_err:
            report["degraded_sources"].append("opencorporates")
            report["overall_flags"].append(f"Company registry lookup unavailable: {company_err[:100]}")
            report["company_screening"] = {"found": False, "source": "unavailable", "degraded": True}
        else:
            report["company_screening"] = company_result
            if not company_result.get("found"):
                report["overall_flags"].append(f"Company '{company_name}' not found in corporate registry")

        # Company sanctions (non-critical — degrade gracefully)
        company_sanctions, sanctions_err = _safe_future_result(
            company_sanctions_future, timeout=30, source_label="sumsub_company_sanctions", company_name=company_name)
        if sanctions_err:
            report["degraded_sources"].append("sumsub_company_sanctions")
            report["overall_flags"].append(f"Company sanctions screening unavailable: {sanctions_err[:100]}")
            report["company_screening"]["sanctions"] = {"matched": False, "results": [], "source": "unavailable", "degraded": True}
        else:
            report["company_screening"]["sanctions"] = company_sanctions
            if company_sanctions.get("matched"):
                report["overall_flags"].append(f"Company '{company_name}' has sanctions/watchlist matches")
                report["total_hits"] += len(company_sanctions.get("results", []))

        for d, f in director_futures:
            d_name = d.get("full_name", "")
            screening, d_err = _safe_future_result(
                f, timeout=30, source_label=f"sumsub_director_{d_name}", company_name=company_name)
            if d_err:
                report["degraded_sources"].append(f"director_screening:{d_name}")
                screening = {"matched": False, "results": [], "source": "unavailable", "degraded": True}
                report["overall_flags"].append(f"Director '{d_name}' screening unavailable: {d_err[:100]}")
            result = {
                "person_name": d_name, "person_type": "director",
                "nationality": d.get("nationality", ""), "declared_pep": d.get("is_pep", "No"),
                "screening": screening,
            }
            if screening.get("matched"):
                report["overall_flags"].append(f"Director '{d_name}' has sanctions/PEP matches")
                report["total_hits"] += len(screening.get("results", []))
                for hit in screening.get("results", []):
                    if hit.get("is_pep") and d.get("is_pep", "No") != "Yes":
                        result["undeclared_pep"] = True
                        report["overall_flags"].append(f"Director '{d_name}' may be undeclared PEP")
            report["director_screenings"].append(result)

        for u, f in ubo_futures:
            u_name = u.get("full_name", "")
            screening, u_err = _safe_future_result(
                f, timeout=30, source_label=f"sumsub_ubo_{u_name}", company_name=company_name)
            if u_err:
                report["degraded_sources"].append(f"ubo_screening:{u_name}")
                screening = {"matched": False, "results": [], "source": "unavailable", "degraded": True}
                report["overall_flags"].append(f"UBO '{u_name}' screening unavailable: {u_err[:100]}")
            result = {
                "person_name": u_name, "person_type": "ubo",
                "nationality": u.get("nationality", ""), "ownership_pct": u.get("ownership_pct", 0),
                "declared_pep": u.get("is_pep", "No"), "screening": screening,
            }
            if screening.get("matched"):
                report["overall_flags"].append(f"UBO '{u_name}' has sanctions/PEP matches")
                report["total_hits"] += len(screening.get("results", []))
                for hit in screening.get("results", []):
                    if hit.get("is_pep") and u.get("is_pep", "No") != "Yes":
                        result["undeclared_pep"] = True
                        report["overall_flags"].append(f"UBO '{u_name}' may be undeclared PEP")
            report["ubo_screenings"].append(result)

        if ip_future:
            ip_result, ip_err = _safe_future_result(
                ip_future, timeout=30, source_label="ip_geolocation", company_name=company_name)
            if ip_err:
                report["degraded_sources"].append("ip_geolocation")
                report["ip_geolocation"] = {"source": "unavailable", "degraded": True}
            else:
                report["ip_geolocation"] = ip_result
                ip_geo = report["ip_geolocation"]
                if ip_geo.get("risk_level") in ("HIGH", "VERY_HIGH"):
                    report["overall_flags"].append(f"Client IP geolocated to high-risk jurisdiction: {ip_geo.get('country')}")
                if ip_geo.get("is_vpn"):
                    report["overall_flags"].append("Client IP detected as VPN")
                if ip_geo.get("is_proxy"):
                    report["overall_flags"].append("Client IP detected as proxy")
                if ip_geo.get("is_tor"):
                    report["overall_flags"].append("Client IP detected as Tor exit node")

        report["kyc_applicants"] = []
        for person, ptype, p_name, f in kyc_futures:
            applicant, kyc_err = _safe_future_result(
                f, timeout=30, source_label=f"sumsub_kyc_{p_name}", company_name=company_name)
            if kyc_err:
                report["degraded_sources"].append(f"kyc:{p_name}")
                applicant = {"source": "unavailable", "degraded": True}
            applicant["person_name"] = p_name
            applicant["person_type"] = ptype
            report["kyc_applicants"].append(applicant)
            if applicant.get("review_answer") == "RED":
                report["overall_flags"].append(f"Sumsub KYC FAILED for {ptype} '{p_name}'")
                report["total_hits"] += 1

    return report
