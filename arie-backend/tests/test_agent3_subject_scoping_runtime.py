"""Agent 3 narrative must never show one subject's findings on another's card.

Founder-reported on ARF-2026-100436: Belinda WRIGLEY (Director, 0 hits, Clear)
had her card render the PEP / watchlist / adverse-media findings of THREE other
directors — "Priority match — review first: the PEP match thomas roberts
(triage 65) — PEP class 1-2; exact name match" — under her own name and Clear
badge, directly above her honest "No provider matches for this subject" line.

Cause: agent3NarrativeEntriesForSubject fell back to the full app-wide entry
list when no entry matched the subject ("so nothing is hidden"), and the panel
suppressed its "scoped to X" label in exactly that case. Because screening is
the business of matching a subject against OTHER PEOPLE'S names, an officer
reasonably reads "the PEP match thomas roberts — exact name match" on Belinda's
card as HER matching a PEP record. Misattribution to a named individual is
worse than omission.

These tests EXECUTE the real functions in Node (the static-pin suites cannot
detect a fallback that leaves the asserted strings in place).
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BACKOFFICE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "arie-backoffice.html",
)


def _extract(html, name):
    start = html.index("function " + name)
    end = html.index("\nfunction ", start + 1)
    return html[start:end]


def _run_node(script):
    assert shutil.which("node"), "Node.js is required for runtime tests"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as handle:
        handle.write(script)
        script_path = handle.name
    try:
        result = subprocess.run(
            ["node", script_path],
            cwd=os.path.dirname(BACKOFFICE_PATH),
            text=True,
            capture_output=True,
            check=False,
        )
    finally:
        try:
            os.unlink(script_path)
        except OSError:
            pass
    assert result.returncode == 0, result.stderr or result.stdout
    return json.loads(result.stdout)


def _harness(expr):
    with open(BACKOFFICE_PATH, encoding="utf-8") as handle:
        html = handle.read()
    # Minimal browser shims + the real functions under test and their helpers.
    script = "var window = {};\n"
    script += "function escapeHtml(s){ return String(s == null ? '' : s); }\n"
    for name in (
        "screeningSubjectJoinName",
        "screeningSubjectTokenKey",
        "screeningSubjectNamesMatch",
        "agent3NarrativeEntriesForSubject",
        "agent3TriageNarrativeHtml",
    ):
        script += _extract(html, name) + "\n"
    script += "\nconsole.log(JSON.stringify(" + expr + "));\n"
    return _run_node(script)


# The founder-reported shape: three other directors' findings, none for Belinda.
NARRATIVE = {
    "entries": [
        {"kind": "hit", "subject_name": "Miles Thomas ROBERTS", "matched_name": "thomas roberts",
         "score": 65, "band": "moderate", "categories": ["pep"],
         "reasons": ["PEP class 1-2; exact name match"]},
        {"kind": "hit", "subject_name": "Krzysztof BIALKOWSKI", "matched_name": "bialkowski krzysztof",
         "score": 53, "band": "moderate", "categories": ["watchlist"],
         "reasons": ["watchlist entry; exact name match"]},
        {"kind": "hit", "subject_name": "Alastair Martin James BROWN", "matched_name": "alasdair james brown",
         "score": 47, "band": "moderate", "categories": ["adverse_media", "watchlist"],
         "reasons": ["watchlist entry; multiple risk categories"]},
    ],
    "weak_tail_count": 5,
    "weak_threshold": 40,
    "headline": "3 subjects carry priority matches.",
}


def test_zero_hit_subject_gets_no_entries():
    out = _harness(
        "agent3NarrativeEntriesForSubject(" + json.dumps(NARRATIVE) + ", 'Belinda WRIGLEY')"
    )
    assert out == [], f"entries misattributed to a zero-hit subject: {out}"


def test_zero_hit_subject_narrative_names_no_other_subject():
    out = _harness(
        "agent3TriageNarrativeHtml(" + json.dumps(NARRATIVE) + ", 'Belinda WRIGLEY')"
    )
    html = str(out)
    for other in ("thomas roberts", "ROBERTS", "BIALKOWSKI", "BROWN",
                  "alasdair james brown", "PEP class", "watchlist entry"):
        assert other not in html, (
            f"another subject's finding ({other!r}) rendered on a zero-hit card: {html}"
        )
    # And the app-wide weak-tail / headline footnotes must not leak in either.
    assert "weak threshold" not in html
    assert "priority matches" not in html


def test_matching_subject_still_gets_exactly_their_entries():
    out = _harness(
        "agent3NarrativeEntriesForSubject(" + json.dumps(NARRATIVE) + ", 'Miles Thomas ROBERTS')"
    )
    assert len(out) == 1
    assert out[0]["matched_name"] == "thomas roberts"


def test_provider_profile_name_still_joins_to_the_party_name():
    # Stored entries can carry the PROVIDER profile name while the application
    # party holds the full legal name — the token-key matcher joins them. The
    # strict filter must not break that join (which would silently empty a
    # subject WITH findings).
    narrative = {"entries": [
        {"kind": "hit", "subject_name": "thomas roberts", "matched_name": "thomas roberts",
         "score": 65, "categories": ["pep"], "reasons": []},
    ]}
    out = _harness(
        "agent3NarrativeEntriesForSubject(" + json.dumps(narrative) + ", 'thomas roberts')"
    )
    assert len(out) == 1


def test_unscoped_mode_unchanged_for_app_wide_contexts():
    # No subjectName -> full list with subject-name prefixes; the app-wide
    # footnotes still render. (No current caller uses this mode, but the
    # contract is kept for the app-level surface.)
    out = _harness(
        "agent3NarrativeEntriesForSubject(" + json.dumps(NARRATIVE) + ", '')"
    )
    assert len(out) == 3
    html = str(_harness(
        "agent3TriageNarrativeHtml(" + json.dumps(NARRATIVE) + ", '')"
    ))
    assert "Miles Thomas ROBERTS" in html
    assert "weak threshold" in html


def test_headline_never_substitutes_on_a_scoped_card():
    # headline is an APP-WIDE summary; on a scoped card with no entries it
    # must not appear (the panel renders its own per-subject empty state).
    out = _harness(
        "agent3TriageNarrativeHtml(" + json.dumps(NARRATIVE) + ", 'Belinda WRIGLEY')"
    )
    assert out == "" or "priority matches" not in str(out)
