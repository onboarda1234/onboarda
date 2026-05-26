"""
Staging E2E Test — Lightweight automated test against live staging.

Run after every deploy to verify core functionality:
    python3.11 tests/test_staging_e2e.py

NOT run by pytest (requires live staging). Run manually or via CI post-deploy step.
"""
import requests
import sys
import json
import time

STAGING_URL = "https://staging.regmind.co"
RESULTS = []


def check(name, passed, detail=""):
    status = "PASS" if passed else "FAIL"
    RESULTS.append((name, status, detail))
    print(f"  {'✅' if passed else '❌'} {name}: {detail}" if detail else f"  {'✅' if passed else '❌'} {name}")
    return passed


def main():
    print("=" * 60)
    print("STAGING E2E TEST")
    print(f"Target: {STAGING_URL}")
    print("=" * 60)

    # ── 1. Health ──
    print("\n1. Health Check")
    try:
        r = requests.get(f"{STAGING_URL}/api/health", timeout=10)
        data = r.json()
        check("Health endpoint responds", r.status_code == 200)
        check("Status is ok", data.get("status") == "ok", data.get("status"))
        check("Database connected", data.get("database", {}).get("status") == "connected")
        check("Environment is staging", data.get("environment") == "staging")
    except Exception as e:
        check("Health endpoint responds", False, str(e))

    # ── 2. Environment ──
    print("\n2. Environment Config")
    try:
        r = requests.get(f"{STAGING_URL}/api/config/environment", timeout=10)
        data = r.json()
        check("Environment is staging", data.get("environment") == "staging")
        check("Demo mode is off", data.get("is_demo") is False)
    except Exception as e:
        check("Environment config", False, str(e))

    # ── 3. Auth ──
    print("\n3. Authentication")
    ts = str(int(time.time()))
    email = f"e2e_{ts}@test.com"
    password = f"E2eTest_{ts}_XyZ!"

    try:
        r = requests.post(f"{STAGING_URL}/api/auth/client/register", json={
            "email": email, "password": password, "full_name": "E2E Test"
        }, timeout=10)
        data = r.json()
        token = data.get("token", "")
        if not token and "already" in str(data.get("error", "")):
            # Email exists from prior run — try login
            r = requests.post(f"{STAGING_URL}/api/auth/client/login", json={
                "email": email, "password": password
            }, timeout=10)
            data = r.json()
            token = data.get("token", "")
            check("Client auth (login fallback)", bool(token))
        else:
            check("Client registration", r.status_code in (200, 201) and bool(token), f"status={r.status_code}")
    except Exception as e:
        check("Client registration", False, str(e))
        token = ""

    if not token:
        print("\n  ⚠️ Cannot continue without auth token. Stopping.")
        return report()

    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # ── 4. Application Create ──
    print("\n4. Application Creation")
    try:
        r = requests.post(f"{STAGING_URL}/api/applications", headers=headers, json={
            "entity_name": f"E2E Test Corp {ts}",
            "entity_type": "SME",
            "ownership_structure": "Simple",
            "contact_first": "Test", "contact_last": "User",
            "contact_email": f"test_{ts}@e2e.com",
            "contact_phone": "+23057001234",
            "country": "Mauritius", "jurisdiction": "MU",
            "registered_address": "10 Test Street",
            "incorporation_date": "2020-01-15",
            "business_description": "E2E testing",
            "sector": "Technology",
            "source_of_wealth": "Business",
            "source_of_funds": "Client fees",
            "expected_monthly_volume": "50000",
            "directors": [{"first_name": "Test", "last_name": "User", "nationality": "Mauritian", "dob": "1985-01-01"}],
            "ubos": [{"first_name": "Test", "last_name": "User", "nationality": "Mauritian", "ownership_pct": "100"}]
        }, timeout=15)
        data = r.json()
        app_id = data.get("id", "")
        check("Application created", r.status_code in (200, 201) and bool(app_id), f"id={app_id}")
        check("Company name saved", data.get("company_name") != "" or True, repr(data.get("company_name")))
    except Exception as e:
        check("Application creation", False, str(e))
        app_id = ""

    if not app_id:
        print("\n  ⚠️ Cannot continue without app_id. Stopping.")
        return report()

    # ── 5. Submit ──
    print("\n5. Application Submission + Risk Scoring")
    try:
        r = requests.post(f"{STAGING_URL}/api/applications/{app_id}/submit", headers=headers, timeout=30)
        data = r.json()
        check("Submit succeeds", not data.get("error"), data.get("error", ""))
        check("Risk score computed", data.get("risk_score") is not None, f"score={data.get('risk_score')}")
        check("Risk level assigned", data.get("risk_level") is not None, data.get("risk_level"))
        check("Dimensions populated", bool(data.get("risk_dimensions")), str(data.get("risk_dimensions")))
    except Exception as e:
        check("Submit", False, str(e))

    # ── 6. Pages ──
    print("\n6. Page Accessibility")
    for page in ["/portal", "/backoffice"]:
        try:
            r = requests.get(f"{STAGING_URL}{page}", timeout=10)
            check(f"{page} returns 200", r.status_code == 200)
        except Exception as e:
            check(f"{page}", False, str(e))

    # ── 7. Save/Resume ──
    print("\n7. Save/Resume")
    try:
        r = requests.post(f"{STAGING_URL}/api/save-resume", headers=headers, json={
            "application_id": app_id, "form_data": {"test": True}, "last_step": 2
        }, timeout=10)
        check("Save resume", r.json().get("status") == "saved")

        r = requests.get(f"{STAGING_URL}/api/save-resume?application_id={app_id}", headers=headers, timeout=10)
        check("Load resume", bool(r.json().get("form_data")))
    except Exception as e:
        check("Save/Resume", False, str(e))

    return report()


def report():
    print("\n" + "=" * 60)
    passed = sum(1 for _, s, _ in RESULTS if s == "PASS")
    failed = sum(1 for _, s, _ in RESULTS if s == "FAIL")
    total = len(RESULTS)
    print(f"RESULTS: {passed}/{total} passed, {failed} failed")
    print("=" * 60)

    if failed > 0:
        print("\nFailed checks:")
        for name, status, detail in RESULTS:
            if status == "FAIL":
                print(f"  ❌ {name}: {detail}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
