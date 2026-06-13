# Browser Smoke

TBD.
# Browser Smoke

Pending. This branch has not been merged or deployed yet.

Required after merged-main staging deploy:

- Client portal login works.
- Client portal authenticated state is visible before logout.
- Client portal logout visibly logs the user out.
- Authenticated portal route access is denied or redirected after logout.
- No protected client API succeeds after logout except expected login/logout
  flows.
- Back-office login works.
- Back-office authenticated state is visible before logout.
- Back-office logout visibly logs the user out.
- Protected back-office routes are denied or redirected after logout.
- Normal login works again after logout.
- No console/network errors beyond expected 401/403 responses.
