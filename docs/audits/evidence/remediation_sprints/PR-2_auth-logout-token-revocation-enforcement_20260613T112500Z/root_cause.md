# Root Cause

## Exact Root Cause

`TokenRevocationList` persisted logout revocations to `revoked_tokens`, but each
running process loaded the table only once via `_db_load_all()`.

After the first load, `_db_loaded` stayed true. On a local cache miss,
`is_revoked(jti)` returned `False` without checking the database again. In a
multi-worker or multi-task runtime, the worker that handled logout had the new
JTI in memory and rejected the token, but another already-running worker could
have a stale cache and accept the same token until restart.

This explains the staging diagnosis:

- The same logged-out token was rejected on some endpoints.
- The same logged-out token was accepted on other authenticated endpoints.
- The inconsistency varied by bearer/cookie and endpoint, consistent with
  requests being served by different workers/tasks with different revocation
  cache state.

## Affected Paths

- `arie-backend/security_hardening.py`
  - `TokenRevocationList._db_load_all`
  - `TokenRevocationList.is_revoked`
  - `TokenRevocationList.get_expiry`
- Authentication callers:
  - `auth.decode_token`
  - `BaseHandler.get_current_user_token`
  - `BaseHandler.require_auth`
  - `LogoutHandler.post`

## Fix Design

Keep current logout semantics: logout revokes the presented active bearer token
and/or `arie_session` cookie token. It does not revoke every session for the
user unless an existing user-level revocation flow, such as password reset,
does so.

The smallest safe fix is to make a cache miss non-authoritative:

1. Keep the in-memory cache for fast hits.
2. Keep persistence in `revoked_tokens`.
3. On a local cache miss, look up the exact active JTI in the database.
4. If found and not expired, add it to the local cache and reject the token.
5. Apply the same persisted lookup to `get_expiry()` so user-level revocation
   entries are not missed by stale worker caches.

The fix does not change JWT format, login issuance, cookie names, RBAC, or
front-end logout behaviour.
