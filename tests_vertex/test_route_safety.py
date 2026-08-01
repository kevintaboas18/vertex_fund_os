"""Route-level invariants for the web layer, from the 67-route audit.

Two things a sweep of every route turned up: verbs that mutate state
behind a GET, and a base URL with a stale path segment that made a whole
integration answer 404 in silence.
"""

from __future__ import annotations

import ast
import pathlib
import re

import pytest

_SRC = pathlib.Path(__file__).resolve().parents[1] / "vertex_api.py"
_TEXT = _SRC.read_text(encoding="utf-8")
_LINES = _TEXT.split("\n")

#: Statements that change server state. A GET carrying one of these is the
#: defect; HTTP defines GET as safe and browsers, prefetchers, link
#: scanners and the back-forward cache may reissue it unprompted.
_MUTATES = re.compile(
    r"(DELETE\s+FROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DROP\s+TABLE"
    r"|\.unlink\(|rmtree|Thread\()", re.I)


def _routes():
    for node in ast.walk(ast.parse(_TEXT)):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for dec in node.decorator_list:
            m = re.search(r"app\.(get|post|put|delete)\(['\"]([^'\"]+)",
                          ast.unparse(dec))
            if m:
                body = "\n".join(_LINES[node.lineno - 1:(node.end_lineno or node.lineno)])
                yield m.group(1), m.group(2), body


def test_no_get_route_changes_server_state():
    """`/api/report-delete` ran `DELETE FROM reports` on a GET, and
    `/api/scheduler/run-now` and `/api/backfill/start` each spawned a
    thread. The auth cookie is SameSite=Strict, which stops the
    cross-site case, but a prefetch or a back-forward replay inside the
    app does not depend on that."""
    offenders = [(verb, path, _MUTATES.search(body).group(1))
                 for verb, path, body in _routes()
                 if verb == "get" and _MUTATES.search(body)]
    assert not offenders, f"GET routes that mutate: {offenders}"


@pytest.mark.parametrize("path", ["/api/report-delete", "/api/scheduler/run-now",
                                  "/api/backfill/start"])
def test_the_three_that_were_converted_stay_post(path):
    verbs = {verb for verb, p, _ in _routes() if p == path}
    assert verbs == {"post"}, f"{path} is {verbs}"


def test_the_delete_route_does_not_hand_its_exception_to_the_browser():
    """Exception text can carry server paths and SQL. It belongs in the
    log."""
    body = next(b for v, p, b in _routes() if p == "/api/report-delete")
    assert "str(e)" not in body
    assert "logger" in body or "logging" in body


def test_the_quantdata_base_has_no_stale_version_segment():
    """`https://api.quantdata.us/v1` answered 404 on every path ("No
    resource found at 'v1/option/flow'"), so options flow, dark pool and
    GEX were dead in silence — 25 requests and ~8s per analyze against
    routes that do not exist. Without the segment they answer 403: they
    exist, the plan does not reach them, which is a different fact."""
    default = re.search(r'QUANTDATA_BASE\s*=\s*os\.environ\.get\(\s*"QUANTDATA_BASE",\s*"([^"]+)"',
                        _TEXT).group(1)
    assert not default.rstrip("/").endswith("/v1"), default
    assert default.startswith("https://")


def test_an_entitlement_refusal_is_not_retried_all_run():
    """A 401/402/403 is a fact about the plan, not a transient failure.
    Retrying it 25 times per request only spends wall time."""
    assert "_QD_SIN_DERECHO" in _TEXT
    body = next(b for b in [_TEXT] if "_quantdata_request" in b)
    assert "_QD_ENTITLEMENT_STATUSES" in body
