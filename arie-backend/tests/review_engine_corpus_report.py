"""Real-case validation harness for the review aggregation engine.

Not collected by pytest — the filename has no ``test_`` prefix deliberately.
This produces an *analysis artifact*, not a pass/fail assertion: it runs the four
merged probes and the aggregation engine over the 41-scenario canonical pilot
dataset and prints what an officer would actually see, plus the noise-reduction
measurement the commercial gate turns on.

Run it with::

    cd arie-backend
    python -m pytest tests/review_engine_corpus_report.py::report -s -q \
        -o python_files=review_engine_corpus_report.py -o python_functions=report
    python -m pytest tests/review_engine_corpus_report.py::production_path -s -q \
        -o python_files=review_engine_corpus_report.py \
        -o python_functions=production_path

``production_path`` seeds ``random`` for the same reason the probe harness does:
without provider credentials, screening falls back to a simulated provider that
draws an 8% hit rate, so the evidence — and therefore the counts — would differ
between runs.

Every subject is addressed by its pseudonymous bundle key. No direct identifier
is emitted, and nothing here says a customer is compliant or non-compliant.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from collections import Counter, defaultdict

import environment
import risk_controlled_values
from fixtures.pilot_canonical_seeder import seed_pilot_canonical_dataset
from supervisor_foundation import assemble_application_bundle, run_review
from supervisor_foundation.aggregation import aggregate_review
from supervisor_foundation.policy import unconfigured_policy
from supervisor_foundation.probes import PROBES

AS_OF = "2026-08-01T00:00:00Z"

#: Cases printed in full. Chosen for shape, not for flattery: one approved
#: high-risk case, one mid-review case, one that is mostly unevaluable.
DETAIL_REFS = ("RM-PILOT-024", "RM-PILOT-030", "RM-PILOT-002")


def _enable_tier0_contract(monkeypatch):
    flag = risk_controlled_values.ACTIVATION_FLAG
    monkeypatch.setenv(flag, "true")
    for manager in (environment.flags, risk_controlled_values.flags):
        # ``setitem`` rather than a bare write: the cache mutation is reverted at
        # teardown too, so running this module inside a wider session cannot
        # leave the flag enabled for everything after it.
        monkeypatch.setitem(manager._cache, flag, True)


def _print_case(ref: str, review, output) -> None:
    completeness = output["review_completeness"]
    assessment = output["overall_assessment"]

    print(f"\n{'=' * 78}\n{ref}\n{'=' * 78}")
    print(f"overall status      : {assessment['overall_status']}")
    print(
        "completeness        : "
        f"{len(completeness['probes_completed'])}/{completeness['probes_expected']} probes "
        f"completed · {completeness['finding_count']} findings "
        f"({completeness['critical_count']}C/{completeness['high_count']}H/"
        f"{completeness['medium_count']}M/{completeness['low_count']}L) · "
        f"{completeness['unevaluable_count']} unevaluable · "
        f"{completeness['evidence_reference_count']} evidence refs"
    )
    print(
        "findings -> concerns: "
        f"{len(review['findings'])} findings -> {len(output['grouped_findings'])} groups "
        f"-> {len(output['material_concerns'])} listed concerns"
    )

    print("\n  material concerns")
    for concern in output["material_concerns"]:
        print(f"    [{concern['severity']:<8}] ({concern['affected_count']}×) {concern['claim']}")
    if not output["material_concerns"]:
        print("    (none at critical or high)")

    print("\n  critical findings")
    for entry in output["critical_findings"]:
        print(f"    ({entry['affected_count']}×) {entry['claim']}")
    if not output["critical_findings"]:
        print("    (none)")

    print("\n  contradictions")
    for entry in output["contradictions"]:
        print(f"    {entry['contradiction_type']}")
    if not output["contradictions"]:
        print("    (none)")

    print("\n  required actions")
    for action in output["required_actions"]:
        owners = ",".join(action["owner_roles"])
        print(f"    [{action['priority']:<8}/{owners}] {action['action']}")
    if not output["required_actions"]:
        print("    (none)")

    print("\n  regulator questions")
    for challenge in output["potential_regulatory_challenges"]:
        print(f"    {challenge['question']}")
    if not output["potential_regulatory_challenges"]:
        print("    (none)")

    print("\n  checks that could not run")
    for entry in output["unavailable_checks"]:
        print(f"    {entry['check_id']} ({entry['availability_status']})")
    if not output["unavailable_checks"]:
        print("    (none)")

    print("\n  limitations")
    for entry in output["limitations"]:
        print(f"    [{entry['kind']}] {entry['limitation_id']}")


def _summarise(rows, connection, *, label: str) -> None:
    statuses = Counter()
    totals = Counter()
    grouped_by_check = Counter()
    per_case = []
    lost = []

    for row in rows:
        bundle = assemble_application_bundle(connection, row["id"], as_of=AS_OF)
        review = run_review(bundle, policy=unconfigured_policy(), probes=PROBES)
        output = aggregate_review(review, bundle=bundle)

        # The preservation check, run on every case rather than asserted once.
        grouped_ids = {
            fid for group in output["grouped_findings"] for fid in group["finding_ids"]
        }
        emitted_ids = {item["finding_id"] for item in review["findings"]}
        if grouped_ids != emitted_ids:
            lost.append((row["ref"], sorted(emitted_ids - grouped_ids)))

        statuses[output["overall_assessment"]["overall_status"]] += 1
        totals["findings"] += len(review["findings"])
        totals["groups"] += len(output["grouped_findings"])
        totals["listed_concerns"] += len(output["material_concerns"])
        totals["actions"] += len(output["required_actions"])
        totals["questions"] += len(output["potential_regulatory_challenges"])
        totals["unavailable"] += len(output["unavailable_checks"])
        for group in output["grouped_findings"]:
            grouped_by_check[group["check_id"]] += group["affected_count"]

        per_case.append((row["ref"], review, output))

        if row["ref"] in DETAIL_REFS:
            _print_case(row["ref"], review, output)

    print(f"\n{'=' * 78}\n{label} — per case\n{'=' * 78}")
    print(
        f"{'ref':<16}{'find':>5}{'grp':>5}{'mat':>5}{'act':>5}{'q':>4}{'unav':>6}"
        f"  {'overall status':<20}next action"
    )
    for ref, review, output in per_case:
        actions = output["required_actions"]
        top = actions[0]["action"] if actions else "(none)"
        print(
            f"{ref:<16}{len(review['findings']):>5}{len(output['grouped_findings']):>5}"
            f"{len(output['material_concerns']):>5}{len(actions):>5}"
            f"{len(output['potential_regulatory_challenges']):>4}"
            f"{len(output['unavailable_checks']):>6}  "
            f"{output['overall_assessment']['overall_status']:<20}{top[:58]}"
        )

    print(f"\n{'=' * 78}\n{label} — corpus summary\n{'=' * 78}")
    print(f"cases                    : {len(per_case)}")
    print(f"overall status spread    : {dict(sorted(statuses.items()))}")
    print(f"raw findings             : {totals['findings']}")
    print(f"grouped concerns         : {totals['groups']}")
    print(f"listed material concerns : {totals['listed_concerns']}")
    print(f"required actions         : {totals['actions']}")
    print(f"regulator questions      : {totals['questions']}")
    print(f"unavailable checks       : {totals['unavailable']}")

    if totals["findings"]:
        # Two measures, because they answer two different questions and quoting
        # only the flattering one would be a sales number rather than a result.
        grouping = 1 - (totals["groups"] / totals["findings"])
        first_read = 1 - (totals["listed_concerns"] / totals["findings"])
        print(f"\ngrouping compression     : {grouping:.1%} fewer items in the detail view")
        print(f"first-read compression   : {first_read:.1%} fewer items before the detail view")
        print(
            "  grouping compression is raw findings -> grouped concerns.\n"
            "  first-read compression is raw findings -> the material-concern\n"
            "  summary an officer reads before opening anything. Neither removes\n"
            "  a finding: all of them remain under grouped_findings."
        )

    print("\nfindings absorbed per check (top 10):")
    for check_id, count in grouped_by_check.most_common(10):
        print(f"  {check_id:<34} {count}")

    print(f"\nfindings lost by grouping: {len(lost)} cases")
    for ref, missing in lost:
        print(f"  !! {ref}: {missing}")

    print(json.dumps({"cases": len(per_case), "lost": len(lost)}))


def report(temp_db, monkeypatch, capsys=None):
    """Stage 1 — stored fixture evidence across all 41 canonical cases."""
    _enable_tier0_contract(monkeypatch)
    seed_pilot_canonical_dataset(dry_run=False)

    with contextlib.closing(sqlite3.connect(temp_db)) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            "SELECT id, ref FROM applications WHERE ref LIKE 'RM-PILOT-%' ORDER BY ref"
        ).fetchall()
        _summarise(rows, connection, label="STAGE 1 — stored fixture evidence")


def production_path(temp_db, monkeypatch, sample=8):
    """Stage 2 — evidence regenerated through the real memo and screening paths."""
    import random

    from memo_handler import build_compliance_memo
    from screening import run_full_screening

    # Same reason as the probe harness: the simulated-provider fallback draws
    # from ``random``, so an unseeded run reports a different corpus each time.
    random.seed(20260801)

    _enable_tier0_contract(monkeypatch)
    seed_pilot_canonical_dataset(dry_run=False)

    connection = sqlite3.connect(temp_db)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT * FROM applications WHERE ref LIKE 'RM-PILOT-%' ORDER BY ref LIMIT ?",
        (sample,),
    ).fetchall()

    for row in rows:
        app = dict(row)
        directors = [
            dict(item)
            for item in connection.execute(
                "SELECT * FROM directors WHERE application_id = ?", (app["id"],)
            ).fetchall()
        ]
        ubos = [
            dict(item)
            for item in connection.execute(
                "SELECT * FROM ubos WHERE application_id = ?", (app["id"],)
            ).fetchall()
        ]
        documents = [
            dict(item)
            for item in connection.execute(
                "SELECT * FROM documents WHERE application_id = ?", (app["id"],)
            ).fetchall()
        ]

        prescreening = json.loads(app.get("prescreening_data") or "{}")
        prescreening["screening_report"] = run_full_screening(app, directors, ubos)
        connection.execute(
            "UPDATE applications SET prescreening_data = ? WHERE id = ?",
            (json.dumps(prescreening, default=str), app["id"]),
        )
        memo, _rules, _supervisor, _validation = build_compliance_memo(
            app, directors, ubos, documents
        )
        connection.execute(
            "UPDATE compliance_memos SET memo_data = ? WHERE application_id = ?",
            (json.dumps(memo, default=str), app["id"]),
        )
        connection.commit()

    refreshed = connection.execute(
        "SELECT id, ref FROM applications WHERE ref LIKE 'RM-PILOT-%' ORDER BY ref LIMIT ?",
        (sample,),
    ).fetchall()
    _summarise(refreshed, connection, label="STAGE 2 — production-path evidence")
    connection.close()
