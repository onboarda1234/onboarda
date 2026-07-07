"""PERIODIC-BASELINE-METHOD-HYGIENE-1 / audit REGMIND-P2-001.

A GET (or other non-POST verb) on the POST-only periodic-review baseline routes
must return a clean, spec-compliant 405 — with an `Allow` header and WITHOUT the
`[ERROR] Unhandled exception` log that Tornado's default raise produces (which
otherwise pollutes the CloudWatch validation window). POST behaviour unchanged.
"""
import json
import logging
import os
import sys
import tempfile
import uuid

from tornado.testing import AsyncHTTPTestCase

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_BASELINE_ROUTES = (
    "/api/monitoring/reviews/rev-xyz/baseline",
    "/api/applications/app-xyz/periodic-review-baseline",
)


class PeriodicBaselineMethodHygieneTest(AsyncHTTPTestCase):
    def get_app(self):
        self._db_path = os.path.join(
            tempfile.gettempdir(),
            f"sw1_method_hygiene_{os.getpid()}_{uuid.uuid4().hex[:8]}.db",
        )
        os.environ["DB_PATH"] = self._db_path
        for mod in ("config", "db", "server"):
            m = sys.modules.get(mod)
            if m is not None and hasattr(m, "DB_PATH"):
                setattr(m, "DB_PATH", self._db_path)
        from db import init_db, seed_initial_data, get_db
        from server import make_app
        init_db()
        db = get_db()
        try:
            seed_initial_data(db)
            db.commit()
        except Exception:
            db.rollback()
        db.close()
        return make_app()

    def tearDown(self):
        super().tearDown()
        try:
            os.unlink(self._db_path)
        except OSError:
            pass

    def test_get_returns_clean_405_with_allow_header(self):
        for route in _BASELINE_ROUTES:
            resp = self.fetch(route, method="GET")
            assert resp.code == 405, f"{route}: {resp.code}"
            assert resp.headers.get("Allow") == "POST, OPTIONS", (
                f"{route}: Allow={resp.headers.get('Allow')!r}")
            body = json.loads(resp.body.decode())
            assert body["status"] == 405
            assert body["allowed_methods"] == ["POST", "OPTIONS"]

    def test_other_non_post_verbs_also_405(self):
        for route in _BASELINE_ROUTES:
            for method, needs_body in (("PUT", True), ("PATCH", True),
                                       ("DELETE", False)):
                kwargs = {"body": b""} if needs_body else {}
                resp = self.fetch(route, method=method, **kwargs)
                assert resp.code == 405, f"{method} {route}: {resp.code}"
                assert resp.headers.get("Allow") == "POST, OPTIONS"

    def test_wrong_verb_does_not_log_an_error(self):
        """The whole point of P2-001: a wrong verb must not emit an ERROR log."""
        records = []

        class _Capture(logging.Handler):
            def emit(self, record):
                records.append(record)

        handler = _Capture(level=logging.ERROR)
        root = logging.getLogger()
        arie = logging.getLogger("arie")
        root.addHandler(handler)
        arie.addHandler(handler)
        try:
            resp = self.fetch(_BASELINE_ROUTES[0], method="GET")
        finally:
            root.removeHandler(handler)
            arie.removeHandler(handler)
        assert resp.code == 405
        offending = [r for r in records if r.levelno >= logging.ERROR]
        assert not offending, (
            "wrong-verb 405 must not log at ERROR level; got: "
            + "; ".join(r.getMessage() for r in offending))

    def test_post_still_reaches_handler_logic(self):
        """POST is unchanged: an unauthenticated POST reaches auth (401/403),
        NOT a 405 — proves the mixin didn't shadow the real handler."""
        for route in _BASELINE_ROUTES:
            resp = self.fetch(route, method="POST", body=json.dumps({}))
            assert resp.code != 405, f"{route}: POST should not be 405"
            assert resp.code in (401, 403), f"{route}: {resp.code}"

    def test_options_preflight_not_shadowed(self):
        """OPTIONS must NOT be turned into a 405 — CORS preflight must survive."""
        for route in _BASELINE_ROUTES:
            resp = self.fetch(route, method="OPTIONS")
            assert resp.code != 405, f"{route}: OPTIONS became 405"


def test_source_guard_post_only_mixin_applied():
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    with open(os.path.join(base, "server.py"), encoding="utf-8") as fh:
        src = fh.read()
    assert "class PostOnlyHandlerMixin" in src
    assert "class PeriodicReviewBaselineHandler(PostOnlyHandlerMixin, BaseHandler)" in src
    assert "class ApplicationPeriodicReviewBaselineHandler(PostOnlyHandlerMixin, BaseHandler)" in src
    # OPTIONS intentionally not overridden by the mixin.
    mixin_block = src.split("class PostOnlyHandlerMixin", 1)[1].split("\nclass ", 1)[0]
    assert "def options(" not in mixin_block
