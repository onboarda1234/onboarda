"""Real-case validation harness for the Phase 0B-2 probe set.

Not collected by pytest — the filename has no ``test_`` prefix deliberately.
This produces an *analysis artifact*, not a pass/fail assertion: it seeds the
41-scenario canonical pilot dataset through the real seeder, runs the four
probes over every application, and prints a report used for the commercial
gate (SHIP / REVISE / DROP per probe).

Run it with::

    python -m pytest tests/probe_corpus_report.py::report -s -q \
        -o python_files=probe_corpus_report.py -o python_functions=report

The findings it prints describe fixture data, not real customers. Every subject
is addressed by its pseudonymous bundle key; no direct identifier is emitted.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict

import environment
import risk_controlled_values
from fixtures.pilot_canonical_seeder import seed_pilot_canonical_dataset
from supervisor_foundation import assemble_application_bundle, run_review
from supervisor_foundation.policy import unconfigured_policy
from supervisor_foundation.probes import PROBES

AS_OF = "2026-08-01T00:00:00Z"


def _enable_tier0_contract(monkeypatch):
    flag = risk_controlled_values.ACTIVATION_FLAG
    monkeypatch.setenv(flag, "true")
    for manager in (environment.flags, risk_controlled_values.flags):
        manager._cache[flag] = True


def report(temp_db, monkeypatch, capsys=None):
    _enable_tier0_contract(monkeypatch)
    seed_pilot_canonical_dataset(dry_run=False)

    connection = sqlite3.connect(temp_db)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT id, ref, status, risk_level, final_risk_level FROM applications "
        "WHERE ref LIKE 'RM-PILOT-%' ORDER BY ref"
    ).fetchall()

    by_probe = defaultdict(Counter)
    by_probe_severity = defaultdict(Counter)
    examples = defaultdict(list)
    per_case = []

    for row in rows:
        bundle = assemble_application_bundle(connection, row["id"], as_of=AS_OF)
        review = run_review(bundle, policy=unconfigured_policy(), probes=PROBES)
        statuses = {}
        for finding in review["findings"]:
            probe_id = finding["probe_id"]
            by_probe[probe_id][finding["status"]] += 1
            by_probe_severity[probe_id][finding["severity"]] += 1
            statuses.setdefault(probe_id, []).append(finding["status"])
            if finding["status"] == "hit" and len(examples[probe_id]) < 4:
                examples[probe_id].append(
                    {
                        "ref": row["ref"],
                        "severity": finding["severity"],
                        "claim": finding["claim"],
                        "officer_question": finding["officer_question"],
                    }
                )
        per_case.append(
            {
                "ref": row["ref"],
                "status": row["status"],
                "risk": row["final_risk_level"] or row["risk_level"],
                "findings": len(review["findings"]),
                "hits": sum(
                    1 for f in review["findings"] if f["status"] == "hit"
                ),
                "unevaluable": review["review_completeness"]["unevaluable_count"],
                "review_hash": review["review_hash"][:12],
            }
        )

    connection.close()

    print("\n" + "=" * 78)
    print(f"PHASE 0B-2 REAL-CASE VALIDATION — {len(rows)} canonical pilot cases")
    print(f"as_of = {AS_OF}")
    print("=" * 78)

    print("\n-- Per-probe outcome distribution --")
    for probe_id in sorted(by_probe):
        print(f"\n{probe_id}")
        print(f"  status:   {dict(sorted(by_probe[probe_id].items()))}")
        print(f"  severity: {dict(sorted(by_probe_severity[probe_id].items()))}")

    print("\n-- Sample hits (fixture data; subjects are pseudonymous keys) --")
    for probe_id in sorted(examples):
        print(f"\n{probe_id}")
        for item in examples[probe_id]:
            print(f"  [{item['severity']}] {item['ref']}")
            print(f"    claim: {item['claim']}")
            print(f"    ask:   {item['officer_question']}")

    print("\n-- Per-case summary --")
    print(f"{'ref':<16}{'status':<22}{'risk':<12}{'n':<4}{'hits':<6}{'uneval':<8}hash")
    for case in per_case:
        print(
            f"{case['ref']:<16}{case['status']:<22}{str(case['risk']):<12}"
            f"{case['findings']:<4}{case['hits']:<6}{case['unevaluable']:<8}"
            f"{case['review_hash']}"
        )

    totals = Counter()
    for case in per_case:
        totals["findings"] += case["findings"]
        totals["hits"] += case["hits"]
        totals["unevaluable"] += case["unevaluable"]
    print(f"\ntotals: {dict(totals)}")
    print(json.dumps({"cases": len(per_case)}))


# ── Stage 2: production-path evidence ────────────────────────────────
# The canonical pilot seeder writes memos and screening reports directly rather
# than through ``memo_handler`` and ``screening``, so stage 1 gives P-03 and
# P-04 nothing to read. This stage regenerates both through the real production
# functions for a sample of cases, which is the only way to know whether those
# two probes work against production-shaped evidence or merely against fixtures.


def production_path(temp_db, monkeypatch, sample=8):
    import random

    from memo_handler import build_compliance_memo
    from screening import run_full_screening

    # With no provider credentials the screening path falls back to a simulated
    # provider that draws an 8% hit rate from ``random`` (``screening.py``).
    # Unseeded, the *evidence* differs between runs and the reported P-04 count
    # drifts — 16 or 17 hits over the same eight cases, observed. The probes are
    # deterministic given a bundle; it is the fixture that moves. Seeding pins
    # the evidence so the figures quoted in the validation document are
    # reproducible. Any seed would do; this one is arbitrary and fixed.
    random.seed(20260801)

    _enable_tier0_contract(monkeypatch)
    seed_pilot_canonical_dataset(dry_run=False)

    connection = sqlite3.connect(temp_db)
    connection.row_factory = sqlite3.Row
    rows = connection.execute(
        "SELECT * FROM applications WHERE ref LIKE 'RM-PILOT-%' ORDER BY ref LIMIT ?",
        (sample,),
    ).fetchall()

    print("\n" + "=" * 78)
    print(f"STAGE 2 — production-path evidence, {len(rows)} cases")
    print("=" * 78)

    by_probe = defaultdict(Counter)
    samples = defaultdict(list)

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

        # Real screening. With no provider credentials configured this yields
        # simulated-fallback records — which is itself the state P-04 check 1
        # exists to report, so the run is informative either way.
        prescreening = json.loads(app.get("prescreening_data") or "{}")
        report = run_full_screening(app, directors, ubos)
        prescreening["screening_report"] = report
        connection.execute(
            "UPDATE applications SET prescreening_data = ? WHERE id = ?",
            (json.dumps(prescreening, default=str), app["id"]),
        )
        connection.commit()
        app["prescreening_data"] = json.dumps(prescreening, default=str)

        # Real memo, which is what persists agent5_input_contract.
        memo, _rules, _supervisor, _validation = build_compliance_memo(
            app, directors, ubos, documents
        )
        updated = connection.execute(
            "UPDATE compliance_memos SET memo_data = ? WHERE application_id = ?",
            (json.dumps(memo, default=str), app["id"]),
        ).rowcount
        connection.commit()
        if not updated:
            # Without this the harness would report P-03 as unavailable and the
            # output would read as a probe result rather than a seeding gap.
            print(f"  !! {row['ref']}: no compliance_memos row to update — "
                  "P-03 output for this case reflects setup, not the probe")

        bundle = assemble_application_bundle(connection, app["id"], as_of=AS_OF)
        review = run_review(bundle, policy=unconfigured_policy(), probes=PROBES)
        for finding in review["findings"]:
            by_probe[finding["probe_id"]][finding["status"]] += 1
            if len(samples[finding["probe_id"]]) < 3:
                samples[finding["probe_id"]].append(
                    f"[{finding['severity']}/{finding['status']}] "
                    f"{row['ref']}: {finding['claim']}"
                )

    connection.close()

    print("\n-- Per-probe outcome distribution (production-path evidence) --")
    for probe_id in sorted(by_probe):
        print(f"  {probe_id}: {dict(sorted(by_probe[probe_id].items()))}")

    print("\n-- Samples --")
    for probe_id in sorted(samples):
        print(f"\n{probe_id}")
        for line in samples[probe_id]:
            print(f"  {line}")
