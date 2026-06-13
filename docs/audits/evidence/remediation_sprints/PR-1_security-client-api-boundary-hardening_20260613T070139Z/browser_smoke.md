# PR-1 Browser Smoke Evidence

Browser smoke testing was not completed at branch stage.

This PR changes server-side API authorization and projection behavior, not frontend code. Browser smoke remains mandatory after PR merge and staging deployment because affected workflows are visible through:

- client portal application/status access
- back-office application list/review
- back-office screening queue

Required staging browser checks before closure:

1. Client can log in and load own portal-safe application/status without internal compliance fields.
2. Client cannot see risk/memo/provider/audit/officer-only data.
3. Back-office user can load applications and screening queue.
4. Navigation and console/network errors show no regression from changed API responses.
