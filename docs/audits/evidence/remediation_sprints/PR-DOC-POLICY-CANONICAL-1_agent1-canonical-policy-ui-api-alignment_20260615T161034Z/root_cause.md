# Root Cause

The root cause was architectural drift between three representations of Agent 1:

1. Backend executable checks: document verification matrix and seed checks.
2. Upload surfaces: portal and back-office allowlists with legacy aliases.
3. Settings/UI registry: lifecycle/evidence-family rows added to make non-onboarding evidence visible.

Because these were not joined by a canonical document policy object, the UI could show broad lifecycle evidence coverage without proving that each row was runtime executable. Workflow usage was also being confused with document policy definition: the same document type could appear in multiple lifecycle sections, making it harder to tell whether checks were canonical or duplicated.

The fix is a backend-backed canonical registry with one policy per document type, plus a separate workflow usage mapping. Document checks live on the document policy. Workflows decide when evidence is required, what it blocks, and what re-screening, risk recalculation, or memo staleness marker is needed.

