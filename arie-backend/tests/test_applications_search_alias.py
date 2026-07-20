"""APP-AUD-005: GET /api/applications accepts ?search= as an alias for ?q=.

The back-office list UI sends `q`, but some callers send `search`; previously
`/api/applications` read only `q` and silently ignored `search`. Guard that the
ApplicationsHandler free-text filter reads both (with `q` taking precedence).
"""
import re
from pathlib import Path


def _applications_handler_get_body():
    src = (Path(__file__).resolve().parents[1] / "server.py").read_text(encoding="utf-8")
    start = src.index("class ApplicationsHandler(BaseHandler):")
    # Up to the next top-level class definition.
    nxt = re.search(r"\nclass \w+\(", src[start + 1 :])
    end = start + 1 + (nxt.start() if nxt else len(src))
    return src[start:end]


def test_applications_list_reads_q_and_search_alias():
    body = _applications_handler_get_body()
    # The single free-text filter line must consult both arguments.
    line = next(
        (l for l in body.splitlines() if "query_text" in l and "get_argument" in l),
        "",
    )
    assert 'get_argument("q"' in line, line
    assert 'get_argument("search"' in line, line
    # `q` must be evaluated first so it wins when both are supplied.
    assert line.index('get_argument("q"') < line.index('get_argument("search"'), line
