# FOLLOW-UP AUDIT 2 — PR-KYC-EDD-REQUIREMENTS-1A
- Audited commit: `062343719ecde4bf048faadab6259ef6082e0728`
- Auditor mode: report-only; no product code changed

## GAP 1 — SCREENING GATES ARE INDEPENDENTLY ENFORCED

### 1. Screening second-review gate exists outside `enhanced_requirements.py`

**`/home/runner/work/onboarda/onboarda/arie-backend/security_hardening.py:285-317`**
```python
_SCREENING_SECOND_REVIEW_BLOCK_CODE = "screening_second_review_pending"
_SCREENING_SECOND_REVIEW_ALLOWED_ROLES = {"admin", "sco"}
_SCREENING_SECOND_REVIEW_PENDING_STATUSES = {
    "pending_second_review",
    "second_review_pending",
    "second_review_required",
    "pending_four_eyes",
    "four_eyes_pending",
}

def _screening_review_requires_second_review(review: Mapping[str, Any]) -> bool:
    status_tokens = {
        _normalise_screening_review_token(review.get(key))
        for key in (
            "review_four_eyes_status",
            "four_eyes_status",
            "status",
            "review_status",
            "screening_review_status",
            "workflow_status",
        )
        if review.get(key) not in (None, "")
    }
    return bool(
        _truthy_screening_review_flag(review.get("requires_four_eyes"))
        or _truthy_screening_review_flag(review.get("second_review_required"))
        or status_tokens.intersection(_SCREENING_SECOND_REVIEW_PENDING_STATUSES)
    )
```

**`/home/runner/work/onboarda/onboarda/arie-backend/security_hardening.py:356-375`**
```python
def _screening_second_review_block_reason(
    review: Mapping[str, Any],
    reviewer_roles: Mapping[str, str],
) -> Optional[str]:
    if not _screening_review_requires_second_review(review):
        return None

    second_reviewer_id = _screening_second_reviewer_id(review)
    first_reviewer_id = _screening_first_reviewer_id(review)
    if not second_reviewer_id:
        return "second review pending"

    if first_reviewer_id and first_reviewer_id == second_reviewer_id:
        return "second reviewer must be different from first reviewer"

    second_reviewer_role = reviewer_roles.get(second_reviewer_id)
    if second_reviewer_role not in _SCREENING_SECOND_REVIEW_ALLOWED_ROLES:
        return "SCO/admin second reviewer required"

    return None
```

### 2. Final approval is fail-closed while second review is unresolved

**`/home/runner/work/onboarda/onboarda/arie-backend/security_hardening.py:378-452`**
```python
def screening_second_review_pending_summary(
    db,
    app: Mapping[str, Any],
    screening_reviews: Optional[List[Dict]] = None,
) -> Dict[str, Any]:
    """Return fail-closed approval blockers for unresolved screening four-eyes reviews."""
    ...
    for review in reviews:
        ...
        reason = _screening_second_review_block_reason(review, reviewer_roles)
        if not reason:
            continue
        ...
        blocker = _approval_gate_blocker(
            f"{_SCREENING_SECOND_REVIEW_BLOCK_CODE}:{review_id or subject_type + ':' + subject_name}",
            "Screening",
            "Screening second review pending",
            (
                f"{subject_type.title()} screening for {subject_name} requires SCO/admin "
                f"second review before final approval. Reason: {reason}."
            ),
            ...
        )
        ...
    return {
        "blocked": bool(blockers),
        "code": _SCREENING_SECOND_REVIEW_BLOCK_CODE if blockers else None,
        "message": format_screening_second_review_pending_message({"blockers": blockers}) if blockers else None,
        "pending_review_ids": pending_ids,
        "blockers": blockers,
    }
```

**`/home/runner/work/onboarda/onboarda/arie-backend/security_hardening.py:455-469`**
```python
def format_screening_second_review_pending_message(summary: Mapping[str, Any]) -> str:
    ...
    return (
        "screening_second_review_pending: Screening second review pending. "
        "SCO/admin review is required before final approval."
        + suffix
    )
```

**`/home/runner/work/onboarda/onboarda/arie-backend/security_hardening.py:785-794`**
```python
second_review_summary = screening_second_review_pending_summary(
    db,
    app,
    screening_reviews,
)
if second_review_summary.get("blocked"):
    return (
        False,
        format_screening_second_review_pending_message(second_review_summary),
    )
```

**`/home/runner/work/onboarda/onboarda/arie-backend/server.py:25426-25439`**
```python
can_approve, gate_error = ApprovalGateValidator.validate_approval(app, db)
if not can_approve:
    reason = f"Approval blocked: {gate_error}"
    second_review_summary = _screening_second_review_summary_if_blocked(db, app)
    if second_review_summary:
        reason = "Approval blocked: screening_second_review_pending"
        _audit_approval_blocked_screening_second_review(
            self,
            db,
            app,
            user,
            second_review_summary,
            "application_decision",
        )
```

### 3. Material screening dispositions are forced onto EDD workflow outside `enhanced_requirements.py`

**`/home/runner/work/onboarda/onboarda/arie-backend/server.py:17596-17631`**
```python
"""Keep application.status/onboarding_lane aligned with screening review truth.

Policy:
* unresolved raw/material screening concerns route to EDD;
* needs_more_information is explicitly treated as unresolved and EDD-routed;
* a completed false_positive_cleared review may exit edd_required only when
  recomputed lane/risk no longer require EDD.
"""
...
if code in _SCREENING_WORKFLOW_EDD_DISPOSITIONS:
    target_lane = "EDD"
    if not terminal:
        target_status = "edd_required"
    reason = f"screening_disposition_{code}_requires_edd_workflow"
```

**`/home/runner/work/onboarda/onboarda/arie-backend/rule_engine.py:1537-1587`**
```python
def _screening_disposition_floor_signal(db, app):
    """Return the current screening-disposition floor signal for recompute.

    Screening review rows are not part of the base prescreening score input.
    This helper bridges that state so formal dispositions that create/preserve
    EDD or unresolved match blocking cannot persist a final LOW classification.
    """
    ...
    for review in reviews:
        code = str(_row_get(review, "disposition_code") or "").strip().lower()
        if code in _SCREENING_DISPOSITION_FLOOR_CODES:
            if code == "needs_more_information":
                return {
                    "code": code,
                    "minimum_level": "MEDIUM",
                    "reason_code": "screening_needs_more_information_floor",
                    "reason_text": (
                        "Screening disposition floor: needs_more_information keeps the match unresolved "
                        "and routes the case to EDD until formally resolved"
                    ),
                    "sets_edd_lane": True,
                }
            return {
                "code": code,
                "minimum_level": "HIGH",
                "reason_code": "material_screening_disposition_floor",
                "reason_text": (
                    "Screening disposition floor: "
                    + code
                    + " creates or preserves material screening/EDD controls and requires at least HIGH final risk"
                ),
                "sets_edd_lane": code in _SCREENING_DISPOSITION_EDD_CODES,
            }

    if _screening_report_has_raw_completed_match(app, reviews=reviews):
        cleared_reviews = [r for r in reviews if _screening_review_is_complete_clearance(r)]
        if not cleared_reviews:
            return {
                "code": "raw_completed_match",
                "minimum_level": "HIGH",
                "reason_code": "material_screening_disposition_floor",
                "reason_text": (
                    "Screening disposition floor: unresolved raw completed_match remains a material "
                    "screening concern requiring at least HIGH final risk until formally cleared"
                ),
                "sets_edd_lane": True,
            }
```

### 4. Final approval is fail-closed for screening-driven EDD routing

**`/home/runner/work/onboarda/onboarda/arie-backend/security_hardening.py:1004-1050`**
```python
_routing = _md_meta.get('edd_routing') or {}
if _routing.get('route') == 'edd':
    _app_status = (app.get('status') or "").lower()
    ...
    if _routine_onboarding_enhanced_only:
        pass
    else:
        _completion = _approval_edd_completion_status(db, app_id, _routing)
        if _approval_edd_completion_satisfied(_completion):
            ...
        else:
            return (
                False,
                _format_approval_edd_completion_block_reason(
                    _routing,
                    _app_status,
                    _completion,
                )
            )
```

**`/home/runner/work/onboarda/onboarda/arie-backend/security_hardening.py:509-521`**
```python
return (
    "EDD routing policy " + str(routing.get("policy_version", ""))
    + " requires completed EDD evidence before final approval "
    + "(triggers: " + triggers + "). "
    + "Application status is '" + app_status + "'. "
    + "EDD completion is not satisfied: " + str(reason) + "."
    + missing_text
)
```

### 5. Tests + run output

**Test file evidence: unresolved second review blocks approval**

**`/home/runner/work/onboarda/onboarda/arie-backend/tests/test_approval_gate.py:872-882`**
```python
def test_screening_second_review_pending_blocks_approval(db):
    from security_hardening import ApprovalGateValidator

    app = _insert_application_and_memo(db, prescreening_data=_completed_match_prescreening())
    _insert_four_eyes_screening_review(db, app["id"], app_ref=app["ref"])

    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is False
    assert "screening_second_review_pending" in message
    assert "SCO/admin review is required" in message
```

**Pytest output**
```text
JWT_SECRET not set for testing — using generated fallback. Set JWT_SECRET env var.
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.0.2, pluggy-1.6.0 -- /usr/bin/python
cachedir: .pytest_cache
rootdir: /home/runner/work/onboarda/onboarda/arie-backend
configfile: pytest.ini
plugins: anyio-4.14.0, cov-7.1.0, asyncio-1.3.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 1 item

tests/test_approval_gate.py::test_screening_second_review_pending_blocks_approval PASSED [100%]

============================== 1 passed in 0.94s ===============================
```

**Decision API test: final decision request is rejected**

**`/home/runner/work/onboarda/onboarda/arie-backend/tests/test_api.py:4326-4358`**
```python
def test_pending_screening_second_review_blocks_decision_with_structured_audit(self, api_server):
    ...
    resp = http_requests.post(
        f"{api_server}/api/applications/{app_id}/decision",
        json={"decision": "approve", ...},
        headers={"Authorization": f"******"},
        timeout=3,
    )

    assert resp.status_code == 400
    body = resp.json()
    assert body["code"] == "screening_second_review_pending"
    assert body["blockers"][0]["title"] == "Screening second review pending"
    assert body["blockers"][0]["required_reviewer_role"] == "SCO/admin"
    assert body["blockers"][0]["screening_review_id"]
```

**Pytest output**
```text
JWT_SECRET not set for testing — using generated fallback. Set JWT_SECRET env var.
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.0.2, pluggy-1.6.0 -- /usr/bin/python
cachedir: .pytest_cache
rootdir: /home/runner/work/onboarda/onboarda/arie-backend
configfile: pytest.ini
plugins: anyio-4.14.0, cov-7.1.0, asyncio-1.3.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 1 item

tests/test_api.py::TestGovernanceAttemptAudit::test_pending_screening_second_review_blocks_decision_with_structured_audit PASSED [100%]

============================== 1 passed in 2.44s ===============================
```

**Material-screening disposition tests: route to `edd_required` / `EDD`**

**`/home/runner/work/onboarda/onboarda/arie-backend/tests/test_api.py:6049-6130`**
```python
@pytest.mark.parametrize(
    "disposition_code",
    ["true_match", "material_concern", "needs_more_information"],
)
def test_blocking_screening_dispositions_normalize_to_edd_status_and_lane(...):
    ...
    assert body["workflow_normalization"]["new_status"] == "edd_required"
    assert body["workflow_normalization"]["new_lane"] == "EDD"
    ...
    assert app["status"] == "edd_required"
    assert app["onboarding_lane"] == "EDD"
```

**Pytest output**
```text
JWT_SECRET not set for testing — using generated fallback. Set JWT_SECRET env var.
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.0.2, pluggy-1.6.0 -- /usr/bin/python
cachedir: .pytest_cache
rootdir: /home/runner/work/onboarda/onboarda/arie-backend
configfile: pytest.ini
plugins: anyio-4.14.0, cov-7.1.0, asyncio-1.3.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 3 items

tests/test_api.py::TestGovernanceAttemptAudit::test_blocking_screening_dispositions_normalize_to_edd_status_and_lane[true_match] PASSED [ 33%]
tests/test_api.py::TestGovernanceAttemptAudit::test_blocking_screening_dispositions_normalize_to_edd_status_and_lane[material_concern] PASSED [ 66%]
tests/test_api.py::TestGovernanceAttemptAudit::test_blocking_screening_dispositions_normalize_to_edd_status_and_lane[needs_more_information] PASSED [100%]

=============================== warnings summary ===============================
tests/test_api.py::TestGovernanceAttemptAudit::test_blocking_screening_dispositions_normalize_to_edd_status_and_lane[true_match]
tests/test_api.py::TestGovernanceAttemptAudit::test_blocking_screening_dispositions_normalize_to_edd_status_and_lane[material_concern]
tests/test_api.py::TestGovernanceAttemptAudit::test_blocking_screening_dispositions_normalize_to_edd_status_and_lane[needs_more_information]
  /home/runner/work/onboarda/onboarda/arie-backend/edd_completion.py:385: DeprecationWarning: datetime.datetime.utcnow() is deprecated and scheduled for removal in a future version. Use timezone-aware objects to represent datetimes in UTC: datetime.datetime.now(datetime.UTC).
    "checked_at": datetime.utcnow().isoformat() + "Z",

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
======================== 3 passed, 3 warnings in 2.56s =========================
```

**Approval-gate test for screening-driven EDD route**

**`/home/runner/work/onboarda/onboarda/arie-backend/tests/test_approval_gate.py:579-600`**
```python
def test_final_approval_blocks_explicit_edd_route_when_edd_case_missing(db):
    ...
    triggers = ["edd_flag:screening_escalated_to_edd"]
    memo_data = _memo_with_edd_route(*triggers)
    memo_data["metadata"]["edd_routing"]["source"] = "screening_update"
    ...
    can_approve, message = ApprovalGateValidator.validate_approval(app, db)

    assert can_approve is False
    assert "EDD completion is not satisfied" in message
    assert "no_approved_edd_case" in message
```

**Pytest output**
```text
JWT_SECRET not set for testing — using generated fallback. Set JWT_SECRET env var.
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.0.2, pluggy-1.6.0 -- /usr/bin/python
cachedir: .pytest_cache
rootdir: /home/runner/work/onboarda/onboarda/arie-backend
configfile: pytest.ini
plugins: anyio-4.14.0, cov-7.1.0, asyncio-1.3.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 1 item

tests/test_approval_gate.py::test_final_approval_blocks_explicit_edd_route_when_edd_case_missing PASSED [100%]

=============================== warnings summary ===============================
tests/test_approval_gate.py::test_final_approval_blocks_explicit_edd_route_when_edd_case_missing
  /home/runner/work/onboarda/onboarda/arie-backend/edd_completion.py:385: DeprecationWarning: datetime.datetime.utcnow() is deprecated and scheduled for removal in a future version. Use timezone-aware objects to represent datetimes in UTC: datetime.datetime.now(datetime.UTC).
    "checked_at": datetime.utcnow().isoformat() + "Z",

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
========================= 1 passed, 1 warning in 0.94s =========================
```

### 6. `screening_gate_safety_exception.md`

**Find output**
```text
$ cd /home/runner/work/onboarda/onboarda && find . -name 'screening_gate_safety_exception.md' -print
(no output)
```

**Result:** file not present.

**GAP 1 RESULT:** PROVEN from current code/test artifacts above. No `REMOVED CONTROL — NO INDEPENDENT ENFORCEMENT` flag triggered on this commit.

---

## GAP 2 — PORTAL NEGATIVE CASE (LOW-risk, non-PEP)

### 1. Direct LOW-risk portal negative-case test

**Search output for empty portal requirement assertions in `tests/test_application_enhanced_requirements.py`**
```text
body["total"] == 0: NOT FOUND
body["requirements"] == []: NOT FOUND
len(body["requirements"]) == 0: NOT FOUND
```

**Closest existing negative-case tests are not the requested LOW-risk portal test:**

**`/home/runner/work/onboarda/onboarda/arie-backend/tests/test_application_enhanced_requirements.py:1514-1521`**
```python
low_app = _insert_application(conn, risk_level="MEDIUM")
conn.execute(
    "INSERT INTO directors (application_id, full_name, is_pep) VALUES (?,?,?)",
    (low_app, "Medium Risk Director", "No"),
)
conn.commit()
_generate(conn, low_app)
assert _count_app_reqs(conn, low_app, "standard_kyc_section_b") == 0
```

**`/home/runner/work/onboarda/onboarda/arie-backend/tests/test_application_enhanced_requirements.py:1574-1586`**
```python
low_app_id = _insert_application(conn, risk_level="MEDIUM")
conn.execute(
    "INSERT INTO directors (id, application_id, person_key, full_name, is_pep) VALUES (?,?,?,?,?)",
    ("dir_kyc_low", low_app_id, "dir-low", "Medium Director", "No"),
)
conn.commit()
low_app = conn.execute("SELECT * FROM applications WHERE id=?", (low_app_id,)).fetchone()
low_expected = _kyc_required_document_expectations(conn, low_app)
assert not [
    item for item in low_expected
    if item.get("doc_type") in {"bankref", "source_wealth"}
    and item.get("person_id") == "dir-low"
]
```

**Pytest output for closest existing tests**
```text
JWT_SECRET not set for testing — using generated fallback. Set JWT_SECRET env var.
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.0.2, pluggy-1.6.0 -- /usr/bin/python
cachedir: .pytest_cache
rootdir: /home/runner/work/onboarda/onboarda/arie-backend
configfile: pytest.ini
plugins: anyio-4.14.0, cov-7.1.0, asyncio-1.3.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 1 item

tests/test_application_enhanced_requirements.py::test_standard_kyc_section_b_bankref_and_source_wealth_conditions PASSED [100%]

============================== 1 passed in 1.14s ===============================
```

```text
JWT_SECRET not set for testing — using generated fallback. Set JWT_SECRET env var.
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.0.2, pluggy-1.6.0 -- /usr/bin/python
cachedir: .pytest_cache
rootdir: /home/runner/work/onboarda/onboarda/arie-backend
configfile: pytest.ini
plugins: anyio-4.14.0, cov-7.1.0, asyncio-1.3.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 1 item

tests/test_application_enhanced_requirements.py::test_standard_kyc_required_document_expectations_include_v5_section_b PASSED [100%]

============================== 1 passed in 2.18s ===============================
```

**GAP 2.1 RESULT:** COVERAGE FAIL — no direct LOW-risk, non-PEP portal/UI negative-case test found.

### 2. Conditional generation code

**`/home/runner/work/onboarda/onboarda/arie-backend/enhanced_requirements.py:957-972`**
```python
def _section_b_person_document_rules(db, app):
    """Return v5 Section B person-level requirements for new generation only."""
    high_or_very_high = _application_high_or_very_high(app)
    rules = []
    for idx, subject in enumerate(_section_b_subjects_for_person_requirements(db, app)):
        subject_type = subject.get("type")
        is_director_or_ubo = subject_type in {"director", "ubo"}
        is_pep = bool(subject.get("is_pep"))
        bankref_required = is_director_or_ubo and (high_or_very_high or is_pep)
        source_wealth_required = is_director_or_ubo and (high_or_very_high or is_pep)
        reason = "high_or_very_high_risk" if high_or_very_high else "pep_person"
        if bankref_required:
            rules.append(_section_b_person_document_rule("bankref", subject, idx, reason=reason))
        if source_wealth_required:
            rules.append(_section_b_person_document_rule("source_wealth", subject, idx, reason=reason))
    return rules
```

**LOW-risk non-PEP evaluation from the code above**

- `high_or_very_high = False`
- `is_pep = False`
- `bankref_required = is_director_or_ubo and (False or False) = False`
- `source_wealth_required = is_director_or_ubo and (False or False) = False`
- Returned value: `rules` remains `[]`

### 3. Server-side portal/API filter

**`/home/runner/work/onboarda/onboarda/arie-backend/server.py:31229-31239`**
```python
requirements = list_portal_application_enhanced_requirements(
    db,
    app["id"],
    exclude_linked_periodic_review=exclude_periodic_review,
)
self.success({
    "application_id": app["id"],
    "application_ref": app["ref"],
    "requirements": requirements,
    "total": len(requirements),
})
```

**`/home/runner/work/onboarda/onboarda/arie-backend/enhanced_requirements.py:3559-3592`**
```python
def list_portal_application_enhanced_requirements(db, application_id, *, exclude_linked_periodic_review=False):
    """List only client-visible requested enhanced requirements for the portal."""
    ...
    rows = db.execute(
        f"""
        SELECT aer.*, err.active AS source_rule_active
        FROM application_enhanced_requirements aer
        LEFT JOIN enhanced_requirement_rules err ON err.id = aer.source_rule_id
        WHERE aer.application_id = ?
          AND aer.active = 1
          AND aer.audience IN ('client', 'both')
          AND aer.requirement_type NOT IN ('review_task', 'internal_control')
          AND aer.status IN ({placeholders})
          {periodic_review_filter}
        ORDER BY aer.requested_at DESC, aer.updated_at DESC, aer.requirement_label, aer.id
        """,
        (application_id, *APPLICATION_REQUIREMENT_PORTAL_VISIBLE_STATUSES),
    ).fetchall()

    requirements = []
    for row in rows:
        ...
        safe = serialize_portal_application_requirement(db, row)
        if safe:
            requirements.append(safe)
    return requirements
```

**Evidence available / missing**

- Generation code above returns no `bankref_*` / `source_wealth_*` rows for LOW-risk non-PEP.
- Portal API returns whatever `list_portal_application_enhanced_requirements()` finds in `application_enhanced_requirements`.
- No explicit LOW-risk portal API assertion (`total == 0` / `requirements == []`) was found in `tests/test_application_enhanced_requirements.py`.

**GAP 2.3 RESULT:** SERVER-SIDE LOGIC PRESENT; LOW-risk portal API test coverage NOT FOUND.

---

## GAP 3 — REMOVED-KEY COUNT RECONCILIATION

### 1. Exact count in `REMOVED_ACTIVE_ENHANCED_REQUIREMENT_KEYS`

**`/home/runner/work/onboarda/onboarda/arie-backend/enhanced_requirements.py:207-243`** contains **35** keys, not 22.

1. enhanced_business_activity_explanation
2. company_bank_statements_6m
3. material_ubo_sow_evidence
4. pep_role_position
5. pep_jurisdiction
6. pep_sow_evidence
7. pep_bank_reference
8. pep_linked_sof_evidence
9. mandatory_senior_review
10. ongoing_monitoring_flag
11. licence_or_registration_evidence
12. transaction_flow_explanation
13. jurisdictions_served
14. wallet_exchange_counterparty_exposure
15. crypto_source_of_funds_evidence
16. crypto_enhanced_monitoring_flag
17. crypto_regulatory_status_assessment
18. ownership_structure_chart
19. ownership_chain_documents
20. enhanced_ubo_evidence
21. control_rationale
22. operating_country_target_market_explanation
23. jurisdiction_licensing_regulatory_evidence
24. enhanced_screening_review
25. high_volume_bank_statements
26. screening_disposition
27. false_positive_rationale
28. adverse_media_pep_sanctions_assessment
29. material_screening_senior_review
30. client_clarification_screening
31. manual_edd_pack
32. money_services_pack
33. regulated_financial_services_pack
34. cross_border_pack
35. high_risk_product_pack

### 2. The 3 moved keys

Moved keys (still listed inside the 35 removed keys):

1. `material_ubo_sow_evidence`
2. `pep_sow_evidence`
3. `pep_bank_reference`

**Read-only / alias evidence**

**`/home/runner/work/onboarda/onboarda/arie-backend/enhanced_requirements.py:168-183`**
```python
LEGACY_ENHANCED_REQUIREMENT_DOCUMENT_POLICY_ALIASES = {
    # Historical/read-only compatibility for generated records created before
    # KYC/EDD matrix v5. These keys are not active defaults for new generation.
    "company_bank_statements_6m": "bank_statements",
    "material_ubo_sow_evidence": "source_wealth",
    "pep_sow_evidence": "source_wealth",
    "pep_bank_reference": "bankref",
    ...
}
```

**No new EDD rows with old moved keys**

**`/home/runner/work/onboarda/onboarda/arie-backend/tests/test_application_enhanced_requirements.py:1426-1449`**
```python
rows = conn.execute(
    """
    SELECT requirement_key, requirement_label, subject_scope, trigger_context
    FROM application_enhanced_requirements
    WHERE application_id=? AND (requirement_key LIKE 'source_wealth_%' OR requirement_key LIKE 'bankref_%')
    ORDER BY requirement_key
    """,
    (app_id,),
).fetchall()
assert len(rows) == 4
...
assert conn.execute(
    """
    SELECT COUNT(*) AS c
    FROM application_enhanced_requirements
    WHERE application_id=? AND requirement_key IN ('pep_sow_evidence', 'pep_bank_reference')
    """,
    (app_id,),
).fetchone()["c"] == 0
```

**`/home/runner/work/onboarda/onboarda/arie-backend/tests/test_application_enhanced_requirements.py:1608-1664`**
```python
def test_v5_removed_edd_keys_do_not_generate_for_new_applications(enhanced_app_db):
    ...
    removed = {
        ...
        "material_ubo_sow_evidence",
        ...
        "pep_sow_evidence",
        "pep_bank_reference",
        ...
    }
    assert not removed.intersection(keys)
```

**Pytest output**
```text
JWT_SECRET not set for testing — using generated fallback. Set JWT_SECRET env var.
============================= test session starts ==============================
platform linux -- Python 3.12.3, pytest-9.0.2, pluggy-1.6.0 -- /usr/bin/python
cachedir: .pytest_cache
rootdir: /home/runner/work/onboarda/onboarda/arie-backend
configfile: pytest.ini
plugins: anyio-4.14.0, cov-7.1.0, asyncio-1.3.0
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 1 item

tests/test_application_enhanced_requirements.py::test_v5_removed_edd_keys_do_not_generate_for_new_applications PASSED [100%]

============================== 1 passed in 1.13s ===============================
```

### 3. Grep results across codebase + portal

**Key findings from the grep summary**

- `screening_disposition`: active references remain in `rule_engine.py` and `server.py` for independent screening enforcement (`/home/runner/work/onboarda/onboarda/arie-backend/rule_engine.py:1537-1724`, `/home/runner/work/onboarda/onboarda/arie-backend/server.py:17528-17631`).
- `material_ubo_sow_evidence`, `pep_sow_evidence`, `pep_bank_reference`: active references remain in portal/backoffice compatibility/classification code:
  - `/home/runner/work/onboarda/onboarda/arie-backoffice.html:8827-8832`
  - `/home/runner/work/onboarda/onboarda/arie-backoffice.html:26115`
  - `/home/runner/work/onboarda/onboarda/arie-backoffice.html:26262`
  - `/home/runner/work/onboarda/onboarda/arie-backend/document_policy_registry.py:316`
- `pep_role_position`, `pep_jurisdiction`, `pep_sow_evidence`, `pep_linked_sof_evidence`: active portal fallback / classification references remain:
  - `/home/runner/work/onboarda/onboarda/arie-backend/enhanced_requirements.py:3342-3367`
  - `/home/runner/work/onboarda/onboarda/arie-backoffice.html:10577-10578`
- `mandatory_senior_review`, `ongoing_monitoring_flag`, `control_rationale`, `operating_country_target_market_explanation` still have active code references outside docs/tests (`/home/runner/work/onboarda/onboarda/arie-backend/enhanced_requirements.py:116-136`, `1065-1073`; `/home/runner/work/onboarda/onboarda/arie-backoffice.html:10577`).

**Conclusion on zero-active-reference claim**

**NOT CONFIRMED.** The grep summary below shows non-doc/non-test references still exist for multiple removed/moved keys. Some are explicitly legacy-marked (`LEGACY_ENHANCED_REQUIREMENT_DOCUMENT_POLICY_ALIASES`), but some portal/backoffice compatibility references are still active code paths without an inline `legacy` marker (for example `arie-backoffice.html:8829-8831`, `26115`, `26262`, and `_PORTAL_SAFE_COPY_BY_REQUIREMENT_KEY` at `enhanced_requirements.py:3342-3367`).

### Appendix A — Exact grep summary captured from the current commit

```text
removed_count= 35
KEY enhanced_business_activity_explanation: total=12 active=1 tests=1 docs=10
  arie-backend/enhanced_requirements.py:208:"enhanced_business_activity_explanation",
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:246:| `enhanced_business_activity_explanation` | Enhanced business activity explanation | client | explanation | No |
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:487:**Detail:** `company_bank_reference`, `company_sof_evidence`, `material_ubo_sow_evidence`, `enhanced_business_activity_explanation` all default to `blocking_app
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:528:| Business/transaction rationale required | enhanced_business_activity_explanation + high_volume explanations | ✅ PASS |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_AUDIT_20260618T150120Z.md:179:| `enhanced_business_activity_explanation` | ✓ line 208 |
  docs/audits/evidence/remediation_sprints/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1_20260618T040402Z/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:246:| `enhanced_business_activity_explanation` | Enhanced business activity explanation | client | explanation | No |
  docs/audits/evidence/remediation_sprints/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1_20260618T040402Z/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:487:**Detail:** `company_bank_reference`, `company_sof_evidence`, `material_ubo_sow_evidence`, `enhanced_business_activity_explanation` all default to `blocking_app
  docs/audits/evidence/remediation_sprints/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1_20260618T040402Z/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:528:| Business/transaction rationale required | enhanced_business_activity_explanation + high_volume explanations | ✅ PASS |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:52:| `enhanced_business_activity_explanation` | True | True | Remove from active generation/default seeding for new apps. |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:80:| `enhanced_business_activity_explanation` | True | True | False |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/removed_key_reference_audit.md:6:rg -n "enhanced_business_activity_explanation|company_bank_statements_6m|material_ubo_sow_evidence|pep_role_position|pep_jurisdiction|pep_sow_evidence|pep_bank_
  arie-backend/tests/test_application_enhanced_requirements.py:1637:"enhanced_business_activity_explanation",
KEY company_bank_statements_6m: total=27 active=2 tests=2 docs=23
  arie-backend/enhanced_requirements.py:171:"company_bank_statements_6m": "bank_statements",
  arie-backend/enhanced_requirements.py:209:"company_bank_statements_6m",
  docs/compliance/kyc-edd-matrix-v4.md:73:| Company Bank Statements | Company | bank_statements | If existing bank account = Yes (Section A) | company_bank_statements_6m; high_volume_bank_statements |
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:243:| `company_bank_statements_6m` | 6 months company bank statements | client | document | **INACTIVE by default** |
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:249:- `company_bank_statements_6m` has `"active": False` in defaults — not generated unless admin enables the rule.
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:334:| `company_bank_statements_6m` | `bank_statements` | active_runtime_verified |
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:475:### OBS-1: `company_bank_statements_6m` is INACTIVE by default
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:537:| `company_bank_statements_6m` inactive by default | Must be activated in settings | ⚠️ OBS-1 |
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:556:- Confirm whether `company_bank_statements_6m` should be activated in pilot settings.
  docs/compliance/kyc-edd-matrix-v5.md:69:| Company Bank Statements | Company | bank_statements | If existing bank account = Yes (Section A) | company_bank_statements_6m; high_volume_bank_statements |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_AUDIT_20260618T150120Z.md:180:| `company_bank_statements_6m` | ✓ line 209 |
  docs/audits/evidence/remediation_sprints/PR-DOC-RECON-1_edd-agent1-policy-reconciliation-settings-cleanup_20260616T055543Z/enhanced_requirements_inventory.md:10:| `company_bank_statements_6m` | 6 months company bank statements where available | `bank_statements` | `DOC-EDD-BANK-STATEMENTS-v1` | Active runtime verified |
  ...
KEY material_ubo_sow_evidence: total=30 active=4 tests=1 docs=25
  arie-backoffice.html:8829:key.indexOf('material_ubo_sow_evidence') === 0 ||
  arie-backend/enhanced_requirements.py:172:"material_ubo_sow_evidence": "source_wealth",
  arie-backend/enhanced_requirements.py:210:"material_ubo_sow_evidence",
  arie-backend/enhanced_requirements.py:256:("material_ubo_sow_evidence", "source_wealth"),
  docs/compliance/kyc-edd-matrix-v4.md:25:| B | Source of Wealth evidence | Specific UBO/director (per person) | HIGH / VERY HIGH risk, or UBO/director who is a PEP | source_wealth | Conditional per per
  docs/compliance/kyc-edd-matrix-v4.md:74:| Source of Wealth evidence (per UBO/director) | Person (Section B) | source_wealth | All high/very-high or PEP person | material_ubo_sow_evidence; pep_sow_evid
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:245:| `material_ubo_sow_evidence` | UBO Source of Wealth evidence | client | document | No |
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:336:| `material_ubo_sow_evidence` | `source_wealth` | active_runtime_verified |
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:487:**Detail:** `company_bank_reference`, `company_sof_evidence`, `material_ubo_sow_evidence`, `enhanced_business_activity_explanation` all default to `blocking_app
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:555:- Confirm whether `blocking_approval` defaults for HIGH/VERY_HIGH requirements (`company_bank_reference`, `company_sof_evidence`, `material_ubo_sow_evidence`) s
  docs/compliance/kyc-edd-matrix-v5.md:24:| B | Source of Wealth evidence | Specific UBO/director (per person) | HIGH / VERY HIGH risk, or UBO/director who is a PEP | source_wealth | Conditional per per
  docs/compliance/kyc-edd-matrix-v5.md:70:| Source of Wealth evidence (per UBO/director) | Person (Section B) | source_wealth | All high/very-high or PEP person | material_ubo_sow_evidence; pep_sow_evid
  ...
KEY pep_role_position: total=14 active=2 tests=2 docs=10
  arie-backend/enhanced_requirements.py:211:"pep_role_position",
  arie-backend/enhanced_requirements.py:3347:"pep_role_position": (
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:257:| `pep_role_position` | PEP role/position | both | No |
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:400:- `pep_role_position` → "Role and public-position information"
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_AUDIT_20260618T150120Z.md:182:| `pep_role_position` | ✓ line 211 |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_AUDIT_20260618T150120Z.md:209:- Safe-copy entries for `pep_role_position`, `pep_jurisdiction`, `pep_sow_evidence`, `pep_linked_sof_evidence` (lines 3347–3362) — same purpose
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_AUDIT_20260618T150120Z.md:456:3. **Legacy `_PORTAL_SAFE_COPY_BY_REQUIREMENT_KEY` retains entries for `pep_role_position`,
  docs/audits/evidence/remediation_sprints/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1_20260618T040402Z/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:257:| `pep_role_position` | PEP role/position | both | No |
  docs/audits/evidence/remediation_sprints/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1_20260618T040402Z/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:400:- `pep_role_position` → "Role and public-position information"
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:70:| `pep_role_position` | True | True | Remove from active generation/default seeding for new apps. |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:82:| `pep_role_position` | True | True | False |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/removed_key_reference_audit.md:6:rg -n "enhanced_business_activity_explanation|company_bank_statements_6m|material_ubo_sow_evidence|pep_role_position|pep_jurisdiction|pep_sow_evidence|pep_bank_
  ...
KEY pep_jurisdiction: total=16 active=4 tests=2 docs=10
  arie-backoffice.html:10578:if (/pep_declaration|pep_jurisdiction|pep_role|pep_position|declared_pep|portal_form|questionnaire|declaration/.test(key)) return 'portal_disclosure';
  arie-backend/enhanced_requirements.py:120:"pep_jurisdiction",
  arie-backend/enhanced_requirements.py:212:"pep_jurisdiction",
  arie-backend/enhanced_requirements.py:3351:"pep_jurisdiction": (
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:258:| `pep_jurisdiction` | PEP jurisdiction | both | No |
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:401:- `pep_jurisdiction` → "Public-position jurisdiction information"
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_AUDIT_20260618T150120Z.md:183:| `pep_jurisdiction` | ✓ line 212 |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_AUDIT_20260618T150120Z.md:209:- Safe-copy entries for `pep_role_position`, `pep_jurisdiction`, `pep_sow_evidence`, `pep_linked_sof_evidence` (lines 3347–3362) — same purpose
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_AUDIT_20260618T150120Z.md:457:`pep_jurisdiction`, `pep_sow_evidence`, `pep_linked_sof_evidence`:** These entries exist
  docs/audits/evidence/remediation_sprints/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1_20260618T040402Z/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:258:| `pep_jurisdiction` | PEP jurisdiction | both | No |
  docs/audits/evidence/remediation_sprints/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1_20260618T040402Z/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:401:- `pep_jurisdiction` → "Public-position jurisdiction information"
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:68:| `pep_jurisdiction` | True | True | Remove from active generation/default seeding for new apps. |
  ...
KEY pep_sow_evidence: total=39 active=5 tests=3 docs=31
  arie-backoffice.html:8830:key.indexOf('pep_sow_evidence') === 0 ||
  arie-backend/enhanced_requirements.py:173:"pep_sow_evidence": "source_wealth",
  arie-backend/enhanced_requirements.py:213:"pep_sow_evidence",
  arie-backend/enhanced_requirements.py:257:("pep_sow_evidence", "source_wealth"),
  arie-backend/enhanced_requirements.py:3355:"pep_sow_evidence": (
  docs/compliance/kyc-edd-matrix-v4.md:25:| B | Source of Wealth evidence | Specific UBO/director (per person) | HIGH / VERY HIGH risk, or UBO/director who is a PEP | source_wealth | Conditional per per
  docs/compliance/kyc-edd-matrix-v4.md:74:| Source of Wealth evidence (per UBO/director) | Person (Section B) | source_wealth | All high/very-high or PEP person | material_ubo_sow_evidence; pep_sow_evid
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:259:| `pep_sow_evidence` | Source of Wealth Evidence — [PEP name] | client | No |
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:266:- `pep_sow_evidence` and `pep_bank_reference` are generated **per identified PEP subject** (per-UBO/director).
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:337:| `pep_sow_evidence` | `source_wealth` | active_runtime_verified |
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:402:- `pep_sow_evidence` → "Source of wealth evidence"
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:526:| PEP explanation and evidence required | pep_declaration_details, pep_sow_evidence, pep_bank_reference | ✅ PASS |
  ...
KEY pep_bank_reference: total=41 active=7 tests=2 docs=32
  arie-backoffice.html:8831:key.indexOf('pep_bank_reference') === 0
  arie-backoffice.html:26115:agent1Policy('edd','DOC-EDD-BANK-REFERENCE-v1','Bank reference',['edd_bank_reference','pep_bank_reference','bankref'],'Required for PEP/high-risk enhanced evide
  arie-backoffice.html:26262:canonicalAgent1Policy('evidence','DOC-EVIDENCE-BANK-REFERENCE-v1','Bank reference',['bankref','bank_reference','pep_bank_reference','edd_bank_reference'],'Activ
  arie-backend/document_policy_registry.py:316:"aliases": ["bank_reference", "pep_bank_reference", "edd_bank_reference"],
  arie-backend/enhanced_requirements.py:174:"pep_bank_reference": "bankref",
  arie-backend/enhanced_requirements.py:214:"pep_bank_reference",
  arie-backend/enhanced_requirements.py:258:("pep_bank_reference", "bankref"),
  docs/compliance/kyc-edd-matrix-v4.md:24:| B | Bank Reference Letter | Each director, UBO, individual intermediary | HIGH / VERY HIGH risk, or director/UBO who is a PEP | bankref | Conditional per pers
  docs/compliance/kyc-edd-matrix-v4.md:75:| Bank Reference Letter (per UBO/director) | Person (Section B) | bankref | High-risk or PEP person | pep_bank_reference; Section B bank ref |
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:260:| `pep_bank_reference` | Bank Reference Letter — [PEP name] | client | **Yes — mandatory=1, blocking_approval=1** |
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:266:- `pep_sow_evidence` and `pep_bank_reference` are generated **per identified PEP subject** (per-UBO/director).
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:267:- `pep_bank_reference`: **blocking_approval=True, mandatory=True** (seeder: `enhanced_requirements.py:1587`). This will block final approval if not accepted or 
  ...
KEY pep_linked_sof_evidence: total=20 active=4 tests=1 docs=15
  arie-backend/enhanced_requirements.py:175:"pep_linked_sof_evidence": "source_funds",
  arie-backend/enhanced_requirements.py:215:"pep_linked_sof_evidence",
  arie-backend/enhanced_requirements.py:259:("pep_linked_sof_evidence", "source_funds"),
  arie-backend/enhanced_requirements.py:3359:"pep_linked_sof_evidence": (
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:261:| `pep_linked_sof_evidence` | Source of Funds evidence (PEP-linked) | client | No |
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:339:| `pep_linked_sof_evidence` | `source_funds` | active_runtime_verified |
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:403:- `pep_linked_sof_evidence` → "Source of funds evidence"
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_AUDIT_20260618T150120Z.md:186:| `pep_linked_sof_evidence` | ✓ line 215 |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_AUDIT_20260618T150120Z.md:209:- Safe-copy entries for `pep_role_position`, `pep_jurisdiction`, `pep_sow_evidence`, `pep_linked_sof_evidence` (lines 3347–3362) — same purpose
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_AUDIT_20260618T150120Z.md:457:`pep_jurisdiction`, `pep_sow_evidence`, `pep_linked_sof_evidence`:** These entries exist
  docs/audits/evidence/remediation_sprints/PR-DOC-RECON-1_edd-agent1-policy-reconciliation-settings-cleanup_20260616T055543Z/enhanced_requirements_inventory.md:15:| `pep_linked_sof_evidence` | Source of Funds evidence where funds are linked to PEP | `source_funds` | `DOC-EDD-SOF-v1` | Active runtime verified | Yes |
  docs/audits/evidence/remediation_sprints/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1_20260618T040402Z/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:261:| `pep_linked_sof_evidence` | Source of Funds evidence (PEP-linked) | client | No |
  ...
KEY mandatory_senior_review: total=5 active=3 tests=1 docs=1
  arie-backoffice.html:10577:if (/mandatory_senior_review|senior_review|ongoing_monitoring_flag|monitoring_flag|supervisor|second_line/.test(key)) return 'internal_control';
  arie-backend/enhanced_requirements.py:131:"mandatory_senior_review",
  arie-backend/enhanced_requirements.py:216:"mandatory_senior_review",
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:60:| `mandatory_senior_review` | True | False | Remove from active generation/default seeding for new apps. |
  arie-backend/tests/test_application_enhanced_requirements.py:1365:assert "mandatory_senior_review" not in by_key
KEY ongoing_monitoring_flag: total=5 active=3 tests=1 docs=1
  arie-backoffice.html:10577:if (/mandatory_senior_review|senior_review|ongoing_monitoring_flag|monitoring_flag|supervisor|second_line/.test(key)) return 'internal_control';
  arie-backend/enhanced_requirements.py:133:"ongoing_monitoring_flag",
  arie-backend/enhanced_requirements.py:217:"ongoing_monitoring_flag",
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:63:| `ongoing_monitoring_flag` | True | False | Remove from active generation/default seeding for new apps. |
  arie-backend/tests/test_application_enhanced_requirements.py:1366:assert "ongoing_monitoring_flag" not in by_key
KEY licence_or_registration_evidence: total=19 active=2 tests=1 docs=16
  arie-backend/enhanced_requirements.py:176:"licence_or_registration_evidence": "licence",
  arie-backend/enhanced_requirements.py:218:"licence_or_registration_evidence",
  docs/compliance/kyc-edd-matrix-v4.md:72:| Regulatory Licence(s) | Company | licence | If licence answer = Yes (Section A) | crypto licence_or_registration_evidence; jurisdiction_licensing_regulatory_e
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:275:| `licence_or_registration_evidence` | Licence/registration evidence | client | Maps to `licence` policy |
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:341:| `licence_or_registration_evidence` | `licence` | active_runtime_verified |
  docs/compliance/kyc-edd-matrix-v5.md:68:| Regulatory Licence(s) | Company | licence | If licence answer = Yes (Section A) | crypto licence_or_registration_evidence; jurisdiction_licensing_regulatory_e
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_AUDIT_20260618T150120Z.md:187:| `licence_or_registration_evidence` | ✓ line 218 |
  docs/audits/evidence/remediation_sprints/PR-DOC-RECON-1_edd-agent1-policy-reconciliation-settings-cleanup_20260616T055543Z/enhanced_requirements_inventory.md:17:| `licence_or_registration_evidence` | Licence/registration evidence or confirmation of unlicensed status | `licence` | `DOC-ENTITY-LICENCE-v1` | Active runtime
  docs/audits/evidence/remediation_sprints/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1_20260618T040402Z/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:275:| `licence_or_registration_evidence` | Licence/registration evidence | client | Maps to `licence` policy |
  docs/audits/evidence/remediation_sprints/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1_20260618T040402Z/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:341:| `licence_or_registration_evidence` | `licence` | active_runtime_verified |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:59:| `licence_or_registration_evidence` | True | True | Remove from active generation/default seeding for new apps. |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:88:| `licence_or_registration_evidence` | True | True | True |
  ...
KEY transaction_flow_explanation: total=4 active=2 tests=1 docs=1
  arie-backend/periodic_review_document_requests.py:169:"requirement_key": "expected_transaction_flow_explanation",
  arie-backend/enhanced_requirements.py:219:"transaction_flow_explanation",
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:73:| `transaction_flow_explanation` | True | False | Remove from active generation/default seeding for new apps. |
  arie-backend/tests/test_periodic_review_document_requests.py:71:"expected_transaction_flow_explanation",
KEY jurisdictions_served: total=2 active=1 tests=0 docs=1
  arie-backend/enhanced_requirements.py:220:"jurisdictions_served",
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:58:| `jurisdictions_served` | True | False | Remove from active generation/default seeding for new apps. |
KEY wallet_exchange_counterparty_exposure: total=2 active=1 tests=0 docs=1
  arie-backend/enhanced_requirements.py:221:"wallet_exchange_counterparty_exposure",
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:74:| `wallet_exchange_counterparty_exposure` | True | False | Remove from active generation/default seeding for new apps. |
KEY crypto_source_of_funds_evidence: total=18 active=2 tests=1 docs=15
  arie-backend/enhanced_requirements.py:177:"crypto_source_of_funds_evidence": "source_funds",
  arie-backend/enhanced_requirements.py:222:"crypto_source_of_funds_evidence",
  docs/compliance/kyc-edd-matrix-v4.md:69:| Company Source of Funds evidence | Company | source_funds | All high/very-high | crypto_source_of_funds_evidence; jurisdiction generic SoF |
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:276:| `crypto_source_of_funds_evidence` | Source of funds evidence (crypto) | client | Maps to `source_funds` |
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:342:| `crypto_source_of_funds_evidence` | `source_funds` | active_runtime_verified |
  docs/compliance/kyc-edd-matrix-v5.md:65:| Company Source of Funds evidence | Company | source_funds | All high/very-high | crypto_source_of_funds_evidence; jurisdiction generic SoF |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_AUDIT_20260618T150120Z.md:188:| `crypto_source_of_funds_evidence` | ✓ line 222 |
  docs/audits/evidence/remediation_sprints/PR-DOC-RECON-1_edd-agent1-policy-reconciliation-settings-cleanup_20260616T055543Z/enhanced_requirements_inventory.md:18:| `crypto_source_of_funds_evidence` | Source of Funds evidence | `source_funds` | `DOC-EDD-SOF-v1` | Active runtime verified | Yes |
  docs/audits/evidence/remediation_sprints/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1_20260618T040402Z/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:276:| `crypto_source_of_funds_evidence` | Source of funds evidence (crypto) | client | Maps to `source_funds` |
  docs/audits/evidence/remediation_sprints/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1_20260618T040402Z/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:342:| `crypto_source_of_funds_evidence` | `source_funds` | active_runtime_verified |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:51:| `crypto_source_of_funds_evidence` | True | True | Remove from active generation/default seeding for new apps. |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:87:| `crypto_source_of_funds_evidence` | True | True | True |
  ...
KEY crypto_enhanced_monitoring_flag: total=7 active=1 tests=1 docs=5
  arie-backend/enhanced_requirements.py:223:"crypto_enhanced_monitoring_flag",
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:278:| `crypto_enhanced_monitoring_flag` | Enhanced monitoring flag | backoffice | Internal only |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_AUDIT_20260618T150120Z.md:189:| `crypto_enhanced_monitoring_flag` | ✓ line 223 |
  docs/audits/evidence/remediation_sprints/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1_20260618T040402Z/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:278:| `crypto_enhanced_monitoring_flag` | Enhanced monitoring flag | backoffice | Internal only |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:89:| `crypto_enhanced_monitoring_flag` | False | False | False |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/removed_key_reference_audit.md:6:rg -n "enhanced_business_activity_explanation|company_bank_statements_6m|material_ubo_sow_evidence|pep_role_position|pep_jurisdiction|pep_sow_evidence|pep_bank_
  arie-backend/tests/test_application_enhanced_requirements.py:1647:"crypto_enhanced_monitoring_flag",
KEY crypto_regulatory_status_assessment: total=7 active=1 tests=1 docs=5
  arie-backend/enhanced_requirements.py:224:"crypto_regulatory_status_assessment",
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:279:| `crypto_regulatory_status_assessment` | Regulatory status assessment | backoffice | Internal only |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_AUDIT_20260618T150120Z.md:190:| `crypto_regulatory_status_assessment` | ✓ line 224 |
  docs/audits/evidence/remediation_sprints/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1_20260618T040402Z/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:279:| `crypto_regulatory_status_assessment` | Regulatory status assessment | backoffice | Internal only |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:90:| `crypto_regulatory_status_assessment` | False | False | False |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/removed_key_reference_audit.md:6:rg -n "enhanced_business_activity_explanation|company_bank_statements_6m|material_ubo_sow_evidence|pep_role_position|pep_jurisdiction|pep_sow_evidence|pep_bank_
  arie-backend/tests/test_application_enhanced_requirements.py:1648:"crypto_regulatory_status_assessment",
KEY ownership_structure_chart: total=22 active=5 tests=0 docs=17
  arie-backend/server.py:23435:"ownership_structure_chart": "structure_chart",
  arie-backend/db.py:7815:"structure chart": "structure_chart", "ownership_structure_chart": "structure_chart",
  arie-backend/document_reliance_gate.py:94:"ownership_structure_chart": "structure_chart",
  arie-backend/enhanced_requirements.py:178:"ownership_structure_chart": "structure_chart",
  arie-backend/enhanced_requirements.py:225:"ownership_structure_chart",
  docs/compliance/kyc-edd-matrix-v4.md:70:| Company structure / ownership chart | Company | structure_chart | All clients (Section A) | crypto ownership_structure_chart; opaque ownership_structure_chart
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:277:| `ownership_structure_chart` | Ownership/control structure | client | Maps to `structure_chart` |
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:285:| `ownership_structure_chart` | Ownership/control structure | client | active_runtime_verified |
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:343:| `ownership_structure_chart` | `structure_chart` | active_runtime_verified |
  docs/compliance/kyc-edd-matrix-v5.md:66:| Company structure / ownership chart | Company | structure_chart | All clients (Section A) | crypto ownership_structure_chart; opaque ownership_structure_chart
  docs/audits/evidence/remediation_sprints/PR-DOC-RECON-1_edd-agent1-policy-reconciliation-settings-cleanup_20260616T055543Z/enhanced_requirements_inventory.md:19:| `ownership_structure_chart` | Ownership structure chart | `structure_chart` | `DOC-ENTITY-OWNERSHIP-CHART-v1` | Active runtime verified | No |
  docs/audits/evidence/remediation_sprints/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1_20260618T040402Z/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:277:| `ownership_structure_chart` | Ownership/control structure | client | Maps to `structure_chart` |
  ...
KEY ownership_chain_documents: total=18 active=2 tests=1 docs=15
  arie-backend/enhanced_requirements.py:181:"ownership_chain_documents": "supporting_document",
  arie-backend/enhanced_requirements.py:226:"ownership_chain_documents",
  docs/compliance/kyc-edd-matrix-v4.md:71:| Ownership chain / enhanced UBO evidence | Company | supporting_document (manual) | All high/very-high | opaque ownership_chain_documents; enhanced_ubo_evidenc
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:286:| `ownership_chain_documents` | Ownership chain supporting docs | client | manual_review_only |
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:348:| `ownership_chain_documents` | `supporting_document` | manual_review_only |
  docs/compliance/kyc-edd-matrix-v5.md:67:| Ownership chain / enhanced UBO evidence | Company | supporting_document (manual) | All high/very-high | opaque ownership_chain_documents; enhanced_ubo_evidenc
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_AUDIT_20260618T150120Z.md:191:| `ownership_chain_documents` | ✓ line 226 |
  docs/audits/evidence/remediation_sprints/PR-DOC-RECON-1_edd-agent1-policy-reconciliation-settings-cleanup_20260616T055543Z/enhanced_requirements_inventory.md:20:| `ownership_chain_documents` | Full ownership-chain documents | `supporting_document` | `DOC-UNKNOWN-UNCLASSIFIED-v1` | Manual review only | Yes |
  docs/audits/evidence/remediation_sprints/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1_20260618T040402Z/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:286:| `ownership_chain_documents` | Ownership chain supporting docs | client | manual_review_only |
  docs/audits/evidence/remediation_sprints/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1_20260618T040402Z/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:348:| `ownership_chain_documents` | `supporting_document` | manual_review_only |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:65:| `ownership_chain_documents` | True | True | Remove from active generation/default seeding for new apps. |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:91:| `ownership_chain_documents` | True | True | True |
  ...
KEY enhanced_ubo_evidence: total=18 active=2 tests=1 docs=15
  arie-backend/enhanced_requirements.py:182:"enhanced_ubo_evidence": "supporting_document",
  arie-backend/enhanced_requirements.py:227:"enhanced_ubo_evidence",
  docs/compliance/kyc-edd-matrix-v4.md:71:| Ownership chain / enhanced UBO evidence | Company | supporting_document (manual) | All high/very-high | opaque ownership_chain_documents; enhanced_ubo_evidenc
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:287:| `enhanced_ubo_evidence` | Enhanced UBO evidence | client | manual_review_only |
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:349:| `enhanced_ubo_evidence` | `supporting_document` | manual_review_only |
  docs/compliance/kyc-edd-matrix-v5.md:67:| Ownership chain / enhanced UBO evidence | Company | supporting_document (manual) | All high/very-high | opaque ownership_chain_documents; enhanced_ubo_evidenc
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_AUDIT_20260618T150120Z.md:192:| `enhanced_ubo_evidence` | ✓ line 227 |
  docs/audits/evidence/remediation_sprints/PR-DOC-RECON-1_edd-agent1-policy-reconciliation-settings-cleanup_20260616T055543Z/enhanced_requirements_inventory.md:21:| `enhanced_ubo_evidence` | Enhanced UBO evidence | `supporting_document` | `DOC-UNKNOWN-UNCLASSIFIED-v1` | Manual review only | Yes |
  docs/audits/evidence/remediation_sprints/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1_20260618T040402Z/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:287:| `enhanced_ubo_evidence` | Enhanced UBO evidence | client | manual_review_only |
  docs/audits/evidence/remediation_sprints/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1_20260618T040402Z/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:349:| `enhanced_ubo_evidence` | `supporting_document` | manual_review_only |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:54:| `enhanced_ubo_evidence` | True | True | Remove from active generation/default seeding for new apps. |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:92:| `enhanced_ubo_evidence` | True | True | True |
  ...
KEY control_rationale: total=4 active=3 tests=0 docs=1
  arie-backend/enhanced_requirements.py:228:"control_rationale",
  arie-backend/enhanced_requirements.py:1070:"control_rationale": {
  arie-backend/enhanced_requirements.py:1071:"field_key": "control_rationale",
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:50:| `control_rationale` | True | False | Remove from active generation/default seeding for new apps. |
KEY operating_country_target_market_explanation: total=4 active=3 tests=0 docs=1
  arie-backend/enhanced_requirements.py:229:"operating_country_target_market_explanation",
  arie-backend/enhanced_requirements.py:1065:"operating_country_target_market_explanation": {
  arie-backend/enhanced_requirements.py:1066:"field_key": "operating_country_target_market_explanation",
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:64:| `operating_country_target_market_explanation` | True | False | Remove from active generation/default seeding for new apps. |
KEY jurisdiction_licensing_regulatory_evidence: total=18 active=2 tests=1 docs=15
  arie-backend/enhanced_requirements.py:179:"jurisdiction_licensing_regulatory_evidence": "licence",
  arie-backend/enhanced_requirements.py:230:"jurisdiction_licensing_regulatory_evidence",
  docs/compliance/kyc-edd-matrix-v4.md:72:| Regulatory Licence(s) | Company | licence | If licence answer = Yes (Section A) | crypto licence_or_registration_evidence; jurisdiction_licensing_regulatory_e
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:296:| `jurisdiction_licensing_regulatory_evidence` | client | `licence` — active_runtime_verified |
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:345:| `jurisdiction_licensing_regulatory_evidence` | `licence` | active_runtime_verified |
  docs/compliance/kyc-edd-matrix-v5.md:68:| Regulatory Licence(s) | Company | licence | If licence answer = Yes (Section A) | crypto licence_or_registration_evidence; jurisdiction_licensing_regulatory_e
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_AUDIT_20260618T150120Z.md:193:| `jurisdiction_licensing_regulatory_evidence` | ✓ line 230 |
  docs/audits/evidence/remediation_sprints/PR-DOC-RECON-1_edd-agent1-policy-reconciliation-settings-cleanup_20260616T055543Z/enhanced_requirements_inventory.md:24:| `jurisdiction_licensing_regulatory_evidence` | Licensing/regulatory evidence where relevant | `licence` | `DOC-ENTITY-LICENCE-v1` | Active runtime verified | 
  docs/audits/evidence/remediation_sprints/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1_20260618T040402Z/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:296:| `jurisdiction_licensing_regulatory_evidence` | client | `licence` — active_runtime_verified |
  docs/audits/evidence/remediation_sprints/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1_20260618T040402Z/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:345:| `jurisdiction_licensing_regulatory_evidence` | `licence` | active_runtime_verified |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:57:| `jurisdiction_licensing_regulatory_evidence` | True | True | Remove from active generation/default seeding for new apps. |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:93:| `jurisdiction_licensing_regulatory_evidence` | True | True | True |
  ...
KEY enhanced_screening_review: total=2 active=1 tests=0 docs=1
  arie-backend/enhanced_requirements.py:231:"enhanced_screening_review",
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:53:| `enhanced_screening_review` | True | False | Remove from active generation/default seeding for new apps. |
KEY high_volume_bank_statements: total=19 active=2 tests=1 docs=16
  arie-backend/enhanced_requirements.py:180:"high_volume_bank_statements": "bank_statements",
  arie-backend/enhanced_requirements.py:232:"high_volume_bank_statements",
  docs/compliance/kyc-edd-matrix-v4.md:73:| Company Bank Statements | Company | bank_statements | If existing bank account = Yes (Section A) | company_bank_statements_6m; high_volume_bank_statements |
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:307:| `high_volume_bank_statements` | client | **INACTIVE by default**; also requires `existing_bank_account=true` |
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:347:| `high_volume_bank_statements` | `bank_statements` | active_runtime_verified |
  docs/compliance/kyc-edd-matrix-v5.md:69:| Company Bank Statements | Company | bank_statements | If existing bank account = Yes (Section A) | company_bank_statements_6m; high_volume_bank_statements |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_AUDIT_20260618T150120Z.md:194:| `high_volume_bank_statements` | ✓ line 232 |
  docs/audits/evidence/remediation_sprints/PR-DOC-RECON-1_edd-agent1-policy-reconciliation-settings-cleanup_20260616T055543Z/enhanced_requirements_inventory.md:27:| `high_volume_bank_statements` | Company bank statements where available | `bank_statements` | `DOC-EDD-BANK-STATEMENTS-v1` | Active runtime verified | No |
  docs/audits/evidence/remediation_sprints/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1_20260618T040402Z/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:307:| `high_volume_bank_statements` | client | **INACTIVE by default**; also requires `existing_bank_account=true` |
  docs/audits/evidence/remediation_sprints/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1_20260618T040402Z/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:347:| `high_volume_bank_statements` | `bank_statements` | active_runtime_verified |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:56:| `high_volume_bank_statements` | False | True | Inactive default; remove from active/default target map or keep only as documented legacy compatibility if need
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:94:| `high_volume_bank_statements` | True | False | True |
  ...
KEY screening_disposition: total=69 active=22 tests=32 docs=15
  arie-backend/rule_engine.py:1537:def _screening_disposition_floor_signal(db, app):
  arie-backend/rule_engine.py:1564:"reason_code": "material_screening_disposition_floor",
  arie-backend/rule_engine.py:1579:"reason_code": "material_screening_disposition_floor",
  arie-backend/rule_engine.py:1605:def _apply_screening_disposition_floor_for_recompute(db, app, risk):
  arie-backend/rule_engine.py:1606:signal = _screening_disposition_floor_signal(db, app)
  arie-backend/rule_engine.py:1711:screening_floor = _apply_screening_disposition_floor_for_recompute(db, app, new_risk)
  arie-backend/rule_engine.py:1724:result["screening_disposition_floor"] = screening_floor
  arie-backend/server.py:4883:"screening_disposition",
  arie-backend/server.py:17528:def _screening_disposition_edd_trigger_flags(canonical_disposition):
  arie-backend/server.py:17539:def _screening_disposition_routing_summary(canonical_disposition):
  arie-backend/server.py:17584:def _normalise_screening_disposition_workflow_state(
  arie-backend/server.py:17631:reason = f"screening_disposition_{code}_requires_edd_workflow"
  ...
KEY false_positive_rationale: total=15 active=1 tests=3 docs=11
  arie-backend/enhanced_requirements.py:234:"false_positive_rationale",
  docs/compliance/kyc-edd-matrix-v4.md:76:| Screening: disposition + senior review | Back-office | Internal (screening engine) | All screened cases — NON-WAIVABLE | screening_disposition; material_scree
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:316:| `false_positive_rationale` | backoffice | Internal review task |
  docs/compliance/kyc-edd-matrix-v5.md:72:| Screening: disposition + senior review | Back-office | Internal (screening engine) | All screened cases — NON-WAIVABLE | screening_disposition; material_scree
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_AUDIT_20260618T150120Z.md:196:| `false_positive_rationale` | ✓ line 234 |
  docs/audits/evidence/remediation_sprints/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1_20260618T040402Z/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:316:| `false_positive_rationale` | backoffice | Internal review task |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:55:| `false_positive_rationale` | True | True | Screening safety exception candidate: remove only after independent screening gates are proven. |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:96:| `false_positive_rationale` | True | True | False |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/screening_gate_independence.md:11:- `false_positive_rationale`
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/parsed_v5_target_matrix.md:488:| Screening: disposition + senior review | Back-office | Internal (screening engine) | All screened cases — NON-WAIVABLE | screening_disposition; material_scree
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/parsed_v5_target_matrix.md:548:"Covers (deleted EDD rows)": "screening_disposition; material_screening_senior_review; false_positive_rationale; adverse_media_pep_sanctions_assessment"
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/removed_key_reference_audit.md:6:rg -n "enhanced_business_activity_explanation|company_bank_statements_6m|material_ubo_sow_evidence|pep_role_position|pep_jurisdiction|pep_sow_evidence|pep_bank_
  ...
KEY adverse_media_pep_sanctions_assessment: total=14 active=1 tests=2 docs=11
  arie-backend/enhanced_requirements.py:235:"adverse_media_pep_sanctions_assessment",
  docs/compliance/kyc-edd-matrix-v4.md:76:| Screening: disposition + senior review | Back-office | Internal (screening engine) | All screened cases — NON-WAIVABLE | screening_disposition; material_scree
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:317:| `adverse_media_pep_sanctions_assessment` | backoffice | Internal review task |
  docs/compliance/kyc-edd-matrix-v5.md:72:| Screening: disposition + senior review | Back-office | Internal (screening engine) | All screened cases — NON-WAIVABLE | screening_disposition; material_scree
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_AUDIT_20260618T150120Z.md:197:| `adverse_media_pep_sanctions_assessment` | ✓ line 235 |
  docs/audits/evidence/remediation_sprints/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1_20260618T040402Z/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:317:| `adverse_media_pep_sanctions_assessment` | backoffice | Internal review task |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:47:| `adverse_media_pep_sanctions_assessment` | True | True | Screening safety exception candidate: remove only after independent screening gates are proven. |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:97:| `adverse_media_pep_sanctions_assessment` | True | True | False |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/screening_gate_independence.md:12:- `adverse_media_pep_sanctions_assessment`
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/parsed_v5_target_matrix.md:488:| Screening: disposition + senior review | Back-office | Internal (screening engine) | All screened cases — NON-WAIVABLE | screening_disposition; material_scree
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/parsed_v5_target_matrix.md:548:"Covers (deleted EDD rows)": "screening_disposition; material_screening_senior_review; false_positive_rationale; adverse_media_pep_sanctions_assessment"
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/removed_key_reference_audit.md:6:rg -n "enhanced_business_activity_explanation|company_bank_statements_6m|material_ubo_sow_evidence|pep_role_position|pep_jurisdiction|pep_sow_evidence|pep_bank_
  ...
KEY material_screening_senior_review: total=16 active=1 tests=2 docs=13
  arie-backend/enhanced_requirements.py:236:"material_screening_senior_review",
  docs/compliance/kyc-edd-matrix-v4.md:76:| Screening: disposition + senior review | Back-office | Internal (screening engine) | All screened cases — NON-WAIVABLE | screening_disposition; material_scree
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:318:| `material_screening_senior_review` | backoffice | Internal; waivable=False |
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:379:- `material_screening_senior_review` (screening_concern, backoffice): `waivable=False`.
  docs/compliance/kyc-edd-matrix-v5.md:72:| Screening: disposition + senior review | Back-office | Internal (screening engine) | All screened cases — NON-WAIVABLE | screening_disposition; material_scree
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_AUDIT_20260618T150120Z.md:198:| `material_screening_senior_review` | ✓ line 236 |
  docs/audits/evidence/remediation_sprints/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1_20260618T040402Z/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:318:| `material_screening_senior_review` | backoffice | Internal; waivable=False |
  docs/audits/evidence/remediation_sprints/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1_20260618T040402Z/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:379:- `material_screening_senior_review` (screening_concern, backoffice): `waivable=False`.
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:61:| `material_screening_senior_review` | True | True | Screening safety exception candidate: remove only after independent screening gates are proven. |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:98:| `material_screening_senior_review` | True | True | False |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/screening_gate_independence.md:10:- `material_screening_senior_review`
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/parsed_v5_target_matrix.md:488:| Screening: disposition + senior review | Back-office | Internal (screening engine) | All screened cases — NON-WAIVABLE | screening_disposition; material_scree
  ...
KEY client_clarification_screening: total=9 active=1 tests=1 docs=7
  arie-backend/enhanced_requirements.py:237:"client_clarification_screening",
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:319:| `client_clarification_screening` | both | Client-facing; mandatory=False; only used when back office determines it is necessary |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_AUDIT_20260618T150120Z.md:199:| `client_clarification_screening` | ✓ line 237 |
  docs/audits/evidence/remediation_sprints/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1_20260618T040402Z/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:319:| `client_clarification_screening` | both | Client-facing; mandatory=False; only used when back office determines it is necessary |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:48:| `client_clarification_screening` | True | True | Remove from active generation/default seeding for new apps. |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:99:| `client_clarification_screening` | True | True | False |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/screening_gate_independence.md:13:- `client_clarification_screening`
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/removed_key_reference_audit.md:6:rg -n "enhanced_business_activity_explanation|company_bank_statements_6m|material_ubo_sow_evidence|pep_role_position|pep_jurisdiction|pep_sow_evidence|pep_bank_
  arie-backend/tests/test_application_enhanced_requirements.py:1657:"client_clarification_screening",
KEY manual_edd_pack: total=5 active=1 tests=1 docs=3
  arie-backend/enhanced_requirements.py:238:"manual_edd_pack",
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_AUDIT_20260618T150120Z.md:200:| `manual_edd_pack` | ✓ line 238 |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:100:| `manual_edd_pack` | False | False | False |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/removed_key_reference_audit.md:6:rg -n "enhanced_business_activity_explanation|company_bank_statements_6m|material_ubo_sow_evidence|pep_role_position|pep_jurisdiction|pep_sow_evidence|pep_bank_
  arie-backend/tests/test_application_enhanced_requirements.py:1658:"manual_edd_pack",
KEY money_services_pack: total=4 active=1 tests=1 docs=2
  arie-backend/enhanced_requirements.py:239:"money_services_pack",
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:101:| `money_services_pack` | False | False | False |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/removed_key_reference_audit.md:6:rg -n "enhanced_business_activity_explanation|company_bank_statements_6m|material_ubo_sow_evidence|pep_role_position|pep_jurisdiction|pep_sow_evidence|pep_bank_
  arie-backend/tests/test_application_enhanced_requirements.py:1659:"money_services_pack",
KEY regulated_financial_services_pack: total=4 active=1 tests=1 docs=2
  arie-backend/enhanced_requirements.py:240:"regulated_financial_services_pack",
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:102:| `regulated_financial_services_pack` | False | False | False |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/removed_key_reference_audit.md:6:rg -n "enhanced_business_activity_explanation|company_bank_statements_6m|material_ubo_sow_evidence|pep_role_position|pep_jurisdiction|pep_sow_evidence|pep_bank_
  arie-backend/tests/test_application_enhanced_requirements.py:1660:"regulated_financial_services_pack",
KEY cross_border_pack: total=4 active=1 tests=1 docs=2
  arie-backend/enhanced_requirements.py:241:"cross_border_pack",
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:103:| `cross_border_pack` | False | False | False |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/removed_key_reference_audit.md:6:rg -n "enhanced_business_activity_explanation|company_bank_statements_6m|material_ubo_sow_evidence|pep_role_position|pep_jurisdiction|pep_sow_evidence|pep_bank_
  arie-backend/tests/test_application_enhanced_requirements.py:1661:"cross_border_pack",
KEY high_risk_product_pack: total=4 active=1 tests=1 docs=2
  arie-backend/enhanced_requirements.py:242:"high_risk_product_pack",
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/current_vs_target_diff.md:104:| `high_risk_product_pack` | False | False | False |
  docs/audits/evidence/remediation_sprints/PR-KYC-EDD-REQUIREMENTS-1A_20260618T111704Z/removed_key_reference_audit.md:6:rg -n "enhanced_business_activity_explanation|company_bank_statements_6m|material_ubo_sow_evidence|pep_role_position|pep_jurisdiction|pep_sow_evidence|pep_bank_
  arie-backend/tests/test_application_enhanced_requirements.py:1662:"high_risk_product_pack",
KEY material_ubo_sow_evidence: total=30 active=4 tests=1 docs=25
  arie-backoffice.html:8829:key.indexOf('material_ubo_sow_evidence') === 0 ||
  arie-backend/enhanced_requirements.py:172:"material_ubo_sow_evidence": "source_wealth",
  arie-backend/enhanced_requirements.py:210:"material_ubo_sow_evidence",
  arie-backend/enhanced_requirements.py:256:("material_ubo_sow_evidence", "source_wealth"),
  docs/compliance/kyc-edd-matrix-v4.md:25:| B | Source of Wealth evidence | Specific UBO/director (per person) | HIGH / VERY HIGH risk, or UBO/director who is a PEP | source_wealth | Conditional per per
  docs/compliance/kyc-edd-matrix-v4.md:74:| Source of Wealth evidence (per UBO/director) | Person (Section B) | source_wealth | All high/very-high or PEP person | material_ubo_sow_evidence; pep_sow_evid
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:245:| `material_ubo_sow_evidence` | UBO Source of Wealth evidence | client | document | No |
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:336:| `material_ubo_sow_evidence` | `source_wealth` | active_runtime_verified |
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:487:**Detail:** `company_bank_reference`, `company_sof_evidence`, `material_ubo_sow_evidence`, `enhanced_business_activity_explanation` all default to `blocking_app
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:555:- Confirm whether `blocking_approval` defaults for HIGH/VERY_HIGH requirements (`company_bank_reference`, `company_sof_evidence`, `material_ubo_sow_evidence`) s
  docs/compliance/kyc-edd-matrix-v5.md:24:| B | Source of Wealth evidence | Specific UBO/director (per person) | HIGH / VERY HIGH risk, or UBO/director who is a PEP | source_wealth | Conditional per per
  docs/compliance/kyc-edd-matrix-v5.md:70:| Source of Wealth evidence (per UBO/director) | Person (Section B) | source_wealth | All high/very-high or PEP person | material_ubo_sow_evidence; pep_sow_evid
  ...
KEY pep_sow_evidence: total=39 active=5 tests=3 docs=31
  arie-backoffice.html:8830:key.indexOf('pep_sow_evidence') === 0 ||
  arie-backend/enhanced_requirements.py:173:"pep_sow_evidence": "source_wealth",
  arie-backend/enhanced_requirements.py:213:"pep_sow_evidence",
  arie-backend/enhanced_requirements.py:257:("pep_sow_evidence", "source_wealth"),
  arie-backend/enhanced_requirements.py:3355:"pep_sow_evidence": (
  docs/compliance/kyc-edd-matrix-v4.md:25:| B | Source of Wealth evidence | Specific UBO/director (per person) | HIGH / VERY HIGH risk, or UBO/director who is a PEP | source_wealth | Conditional per per
  docs/compliance/kyc-edd-matrix-v4.md:74:| Source of Wealth evidence (per UBO/director) | Person (Section B) | source_wealth | All high/very-high or PEP person | material_ubo_sow_evidence; pep_sow_evid
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:259:| `pep_sow_evidence` | Source of Wealth Evidence — [PEP name] | client | No |
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:266:- `pep_sow_evidence` and `pep_bank_reference` are generated **per identified PEP subject** (per-UBO/director).
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:337:| `pep_sow_evidence` | `source_wealth` | active_runtime_verified |
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:402:- `pep_sow_evidence` → "Source of wealth evidence"
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:526:| PEP explanation and evidence required | pep_declaration_details, pep_sow_evidence, pep_bank_reference | ✅ PASS |
  ...
KEY pep_bank_reference: total=41 active=7 tests=2 docs=32
  arie-backoffice.html:8831:key.indexOf('pep_bank_reference') === 0
  arie-backoffice.html:26115:agent1Policy('edd','DOC-EDD-BANK-REFERENCE-v1','Bank reference',['edd_bank_reference','pep_bank_reference','bankref'],'Required for PEP/high-risk enhanced evide
  arie-backoffice.html:26262:canonicalAgent1Policy('evidence','DOC-EVIDENCE-BANK-REFERENCE-v1','Bank reference',['bankref','bank_reference','pep_bank_reference','edd_bank_reference'],'Activ
  arie-backend/document_policy_registry.py:316:"aliases": ["bank_reference", "pep_bank_reference", "edd_bank_reference"],
  arie-backend/enhanced_requirements.py:174:"pep_bank_reference": "bankref",
  arie-backend/enhanced_requirements.py:214:"pep_bank_reference",
  arie-backend/enhanced_requirements.py:258:("pep_bank_reference", "bankref"),
  docs/compliance/kyc-edd-matrix-v4.md:24:| B | Bank Reference Letter | Each director, UBO, individual intermediary | HIGH / VERY HIGH risk, or director/UBO who is a PEP | bankref | Conditional per pers
  docs/compliance/kyc-edd-matrix-v4.md:75:| Bank Reference Letter (per UBO/director) | Person (Section B) | bankref | High-risk or PEP person | pep_bank_reference; Section B bank ref |
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:260:| `pep_bank_reference` | Bank Reference Letter — [PEP name] | client | **Yes — mandatory=1, blocking_approval=1** |
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:266:- `pep_sow_evidence` and `pep_bank_reference` are generated **per identified PEP subject** (per-UBO/director).
  docs/compliance/PORTAL-KYC-EDD-REQUIREMENTS-AUDIT-1.md:267:- `pep_bank_reference`: **blocking_approval=True, mandatory=True** (seeder: `enhanced_requirements.py:1587`). This will block final approval if not accepted or 
  ...
___BEGIN___COMMAND_DONE_MARKER___0
```
