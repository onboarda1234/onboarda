"""Fixture-tagged audit writer (Path A: direct INSERT into audit_log).

The original draft package imported a non-existent top-level
``log_audit`` symbol. There is no top-level ``audit.py`` module in
this repo; the only canonical writer is ``BaseHandler.log_audit``
(arie-backend/base_handler.py:285), which is bound to a Tornado
request handler and is not callable from a CLI seeder.

This module therefore writes directly to the ``audit_log`` table
using the canonical column shape used by every other audit insert
in the codebase:

    user_id, user_name, user_role,
    action, target, detail,
    ip_address,
    before_state, after_state

Every action is automatically prefixed with ``fixture.`` so seed
rows can be filtered out of normal audit dashboards and clearly
identified as seed-origin in any compliance review. The synthetic
fixture user is recorded as ``fixture_seed`` / role ``system``.

The writer holds NO transaction itself: it uses the same
``DBConnection`` the seeder is using, so the audit row participates
in the same transaction as the seeded business row. In dry-run
mode the audit rows are rolled back together with the seeded data.
"""

import json
from typing import Any, Optional

FIXTURE_USER_ID = "fixture_seed"
FIXTURE_USER_NAME = "fixture_seed"
FIXTURE_USER_ROLE = "system"
FIXTURE_IP = "127.0.0.1"


def _safe_json(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, default=str, sort_keys=True)
    except Exception:
        return json.dumps({"unserializable": str(value)})


def make_fixture_audit_writer(db):
    """Return a callable that writes a ``fixture.*`` row to ``audit_log``.

    Signature::

        writer(action, target, detail,
               before_state=None, after_state=None)

    The writer participates in the caller's transaction (no commit).
    """

    def writer(
        action: str,
        target: str,
        detail: str,
        before_state: Optional[Any] = None,
        after_state: Optional[Any] = None,
    ):
        tagged_action = action if action.startswith("fixture.") else f"fixture.{action}"
        db.execute(
            "INSERT INTO audit_log "
            "(user_id, user_name, user_role, action, target, detail, "
            "ip_address, before_state, after_state) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (
                FIXTURE_USER_ID,
                FIXTURE_USER_NAME,
                FIXTURE_USER_ROLE,
                tagged_action,
                target,
                detail,
                FIXTURE_IP,
                _safe_json(before_state),
                _safe_json(after_state),
            ),
        )

    return writer
