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


def test_person_key_joins_a_divergent_provider_profile_name():
    # THE case the name matcher cannot bridge (its own canonical example):
    # stored entry carries the PROVIDER profile name "thomas roberts", the card
    # is keyed by the party name "Miles Thomas ROBERTS". Reviewer-executed
    # proof showed the token matcher MISSES this pair — the stable person_key
    # is the only reliable join, and it must win.
    narrative = {"entries": [
        {"kind": "hit", "subject_name": "thomas roberts", "person_key": "pk-roberts-1",
         "matched_name": "thomas roberts", "score": 65, "categories": ["pep"], "reasons": []},
    ]}
    out = _harness(
        "agent3NarrativeEntriesForSubject(" + json.dumps(narrative)
        + ", 'Miles Thomas ROBERTS', 'pk-roberts-1')"
    )
    assert len(out) == 1, "person_key must attribute a divergent provider-profile name"


def test_person_key_mismatch_rejects_even_identical_names():
    # Two directors can share a name; the key is authoritative in BOTH
    # directions. A key mismatch must reject the entry even when the display
    # names collide.
    narrative = {"entries": [
        {"kind": "hit", "subject_name": "Thomas Roberts", "person_key": "pk-other-2",
         "matched_name": "thomas roberts", "score": 65, "categories": ["pep"], "reasons": []},
    ]}
    out = _harness(
        "agent3NarrativeEntriesForSubject(" + json.dumps(narrative)
        + ", 'Thomas Roberts', 'pk-roberts-1')"
    )
    assert out == []


def test_legacy_entry_without_person_key_falls_back_to_name_join():
    # Interpretations stored before person_key stamping carry no key; the
    # identical-name fallback must still attribute them.
    narrative = {"entries": [
        {"kind": "hit", "subject_name": "Thomas Roberts",
         "matched_name": "thomas roberts", "score": 65, "categories": ["pep"], "reasons": []},
    ]}
    out = _harness(
        "agent3NarrativeEntriesForSubject(" + json.dumps(narrative)
        + ", 'Thomas Roberts', 'pk-roberts-1')"
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


# --- panel-level behavioural tests (kill the M3/M4 mutation survivors) -------
#
# The first review round proved two mutations survive function-level tests:
# M3 (output.summary fallback reordered ahead of the per-subject empty state)
# and M4 (scope label reverted to entry-dependent). Both reintroduce the
# founder-reported defect class while every string pin stays satisfied. These
# execute the REAL panel renderer.

PANEL_OUTPUT = {
    "generated_at": "2026-08-03T17:08:20",
    "summary": "Stored screening results for ARF-2026-100436 show 8 provider result row(s).",
    "recommended_disposition": "Officer review required",
    "hit_counts": {"total": 8},
    "hit_rows": [{"i": 1}],
    "triage_narrative": NARRATIVE,
}


def _panel(subject_name, person_key="", subject_row=None, output=None):
    with open(BACKOFFICE_PATH, encoding="utf-8") as handle:
        html = handle.read()
    stubs = (
        "var window = {};\n"
        "function escapeHtml(s){ return String(s == null ? '' : s); }\n"
        "var AGENT3_SCREENING_INTERPRETATION_BUSY = false;\n"
        "var FIXTURE_OUTPUT = " + json.dumps(output if output is not None else PANEL_OUTPUT) + ";\n"
        "function getAgent3ScreeningInterpretation(app){ return FIXTURE_OUTPUT; }\n"
        "function agent3ScreeningResultTerminal(o){ return true; }\n"
        "function agent3DisplayRecommendation(v){ return v || ''; }\n"
    )
    script = stubs
    for name in (
        "screeningSubjectJoinName",
        "screeningSubjectTokenKey",
        "screeningSubjectNamesMatch",
        "agent3NarrativeEntriesForSubject",
        "agent3TriageNarrativeHtml",
        "renderAgent3ScreeningInterpretationPanel",
    ):
        script += _extract(html, name) + "\n"
    script += (
        "\nconsole.log(JSON.stringify(renderAgent3ScreeningInterpretationPanel("
        "{id:'a1', ref:'ARF-2026-100436'}, "
        + json.dumps(subject_name) + ", "
        + json.dumps(person_key) + ", "
        + json.dumps(subject_row) + ")));\n"
    )
    return str(_run_node(script))


def test_panel_zero_hit_subject_renders_empty_state_never_the_app_summary():
    html = _panel("Belinda WRIGLEY", "", {"total_hits": 0})
    assert "data-agent3-no-subject-findings" in html
    assert "No Agent 3 findings for Belinda WRIGLEY" in html
    # M3 killer: the application-level summary must not render on her card.
    assert "8 provider result row(s)" not in html
    # M4 killer: the scope label renders even though she has no entries.
    assert "scoped to Belinda WRIGLEY" in html
    # And no other subject's findings.
    assert "ROBERTS" not in html.replace("Belinda WRIGLEY", "")
    assert "thomas roberts" not in html


def test_panel_subject_with_hits_but_unattributed_entries_fails_safe():
    # Legacy interpretation (no person_key on entries) whose stored display
    # name diverges from the party name: the subject HAS hits, so "No
    # findings" would be false — the ranked cards below contradict it.
    output = dict(PANEL_OUTPUT)
    output["triage_narrative"] = {"entries": [
        {"kind": "hit", "subject_name": "thomas roberts",
         "matched_name": "thomas roberts", "score": 65,
         "categories": ["pep"], "reasons": ["PEP class 1-2; exact name match"]},
    ]}
    html = _panel("Miles Thomas ROBERTS", "", {"total_hits": 4}, output)
    assert "data-agent3-unattributed-findings" in html
    assert "could not be attributed to Miles Thomas ROBERTS" in html
    assert "No Agent 3 findings" not in html


def test_panel_person_key_attributes_the_divergent_name_end_to_end():
    output = dict(PANEL_OUTPUT)
    output["triage_narrative"] = {"entries": [
        {"kind": "hit", "subject_name": "thomas roberts", "person_key": "pk-r1",
         "matched_name": "thomas roberts", "score": 65,
         "categories": ["pep"], "reasons": ["PEP class 1-2; exact name match"]},
    ]}
    html = _panel("Miles Thomas ROBERTS", "pk-r1", {"total_hits": 4}, output)
    assert "thomas roberts" in html          # his finding, on his card
    assert "data-agent3-unattributed-findings" not in html
    assert "data-agent3-no-subject-findings" not in html


def test_panel_legacy_interpretation_without_narrative_is_labelled_app_level():
    # Pre-rts interpretations carry no triage_narrative; the stored summary is
    # application-level and must render labelled as such — never bare under a
    # subject's name — and the scope label must still render.
    output = dict(PANEL_OUTPUT)
    output.pop("triage_narrative")
    html = _panel("Belinda WRIGLEY", "", {"total_hits": 0}, output)
    assert "data-agent3-application-level-summary" in html
    assert "Application-level summary" in html
    assert "scoped to Belinda WRIGLEY" in html


def test_panel_caption_declares_app_level_scope_of_refresh():
    html = _panel("Belinda WRIGLEY", "", {"total_hits": 0})
    assert "Interpretation covers all subjects on this application." in html


# --- server side: narrative entries must carry the join key ------------------

def test_server_narrative_entries_carry_person_key_and_subject_type():
    """The whole client-side join rests on the server stamping person_key.

    The normalizer's contract: "downstream joins must use person_key when
    present, never the display name" — yet the narrative previously dropped it,
    leaving the client name-only. Pin the stamping on both payload shapes.
    """
    import server

    hits = [
        {"subject_type": "director", "subject_name": "thomas roberts",
         "person_key": "pk-r1", "matched_name": "thomas roberts",
         "categories": ["pep"], "triage_score": 65,
         "triage_score_reasons": ["PEP class 1-2; exact name match"]},
        {"subject_type": "ubo", "subject_name": "Nadia KOVAC",
         "person_key": "pk-k2", "matched_name": "nadia kovac",
         "categories": ["watchlist"], "triage_score": 53,
         "triage_score_reasons": ["watchlist entry"]},
    ]
    narrative = server._agent3_triage_narrative(hits)
    assert narrative, "narrative should be produced for scored hits"

    entries = narrative.get("entries") or []
    assert entries, "grouped entries expected"
    by_key = {e.get("person_key"): e for e in entries}
    assert "pk-r1" in by_key and "pk-k2" in by_key
    assert by_key["pk-r1"]["subject_type"] == "director"
    assert by_key["pk-k2"]["subject_type"] == "ubo"

    priority = narrative.get("priority_hits") or []
    assert priority, "priority_hits expected"
    assert {p.get("person_key") for p in priority} == {"pk-r1", "pk-k2"}


def test_server_collect_hits_stamps_person_key_from_stored_subject():
    """_agent3_collect_hits reads the stored screening record; the record's
    person_key (persisted at screening time) must flow onto every hit."""
    import server

    report = {
        "director_screenings": [{
            "name": "thomas roberts",           # provider profile name
            "person_key": "pk-r1",
            "screening": {
                "pep_results": [{
                    "name": "thomas roberts",
                    "match_score": 0.9,
                    "triage_score": 65,
                }],
            },
        }],
    }
    hits = server._agent3_collect_hits(report, {"company_name": "Acme"})
    director_hits = [h for h in hits if h.get("subject_type") == "director"]
    assert director_hits, "director hit expected"
    assert all(h.get("person_key") == "pk-r1" for h in director_hits)
