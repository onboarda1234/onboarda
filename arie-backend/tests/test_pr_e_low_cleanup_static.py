"""PR-E #7 — low-cleanup safe batch (items 3, 4, 5).

3. Missing DB index on fresh installs: idx_screening_hit_dispositions_subject
   existed only in a migration marked init_db-covered, so fresh DBs got the table
   without the index. Added to both base-schema variants (Postgres + SQLite).
4b. Dead-compute removal: a freshness/meta block written to #detail-screening-review
   then immediately clobbered by renderScreeningReviewPanel on the next line.
   (Item 4a — removing the dead `comparisonHtml` config keys — was DROPPED: a frozen
   guard test pins buildScreeningComparisonPanel('entity'); needs founder sign-off.)
5. ORDER BY on the per-hit disposition hydration SELECTs (GET + POST), previously
   nondeterministic.

These guards are revert-sensitive; the DB-backed suites separately prove the
schema (with the new index) executes cleanly on the live backend.
"""
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DB_PY = ROOT / "arie-backend" / "db.py"
SERVER_PY = ROOT / "arie-backend" / "server.py"
BACKOFFICE_HTML = ROOT / "arie-backoffice.html"


# --- item 3 -----------------------------------------------------------------

def test_disposition_subject_index_in_both_base_schema_variants():
    src = DB_PY.read_text(encoding="utf-8")
    idx = src.count(
        "CREATE INDEX IF NOT EXISTS idx_screening_hit_dispositions_subject"
    )
    assert idx == 2, f"expected the index in both (PG+SQLite) base schemas, found {idx}"
    assert "ON screening_hit_dispositions(application_id, subject_type, subject_name)" in src


# --- item 4b ----------------------------------------------------------------
# (item 4a — removing the dead `comparisonHtml` config keys — was DROPPED from
#  this batch: a frozen Screening Queue guard test, test_backoffice_ca_truthflow_
#  static::test_backoffice_screening_review_adds_declared_vs_provider_comparison,
#  pins the presence of buildScreeningComparisonPanel('entity'). Removing it
#  regresses a frozen guard, so it needs founder sign-off — deferred.)

def test_clobbered_screening_meta_block_removed():
    html = BACKOFFICE_HTML.read_text(encoding="utf-8")
    assert "innerHTML = freshnessBanner + screeningMeta" not in html


# --- item 5 -----------------------------------------------------------------

def test_disposition_hydration_selects_are_ordered():
    src = SERVER_PY.read_text(encoding="utf-8")
    # GET hydration query and POST response query both deterministic now.
    assert 'query += " ORDER BY subject_type, subject_name, hit_id"' in src
    assert '"ORDER BY hit_id",' in src
