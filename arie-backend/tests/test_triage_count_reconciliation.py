"""SRP-3 Phase F (F4) — triage summary counts must reconcile with the hit list.

Founder-reported defect (Sir Mick Davis, ARF-2026-100432): the four summary
tiles read "15 Sanctions & watchlist · 1 PEP · 14 Adverse media · 28 Weak
name-only" for a subject that had **zero** material sanctions or watchlist
exposure. ``buckets`` counted every hit in its category while ``weak_count``
re-counted the below-threshold ones, so the same 28 weak hits appeared inside
the 15 and the 14 as well — the tiles summed to 58 for 30 distinct hits, and a
red alarm tile overstated a subject whose only material matches were 1 PEP and
1 adverse-media hit.

The hit list already partitions correctly (every below-threshold hit is pulled
out of its section and rendered in the weak tail), so the fix expresses that
same partition in the counts:

    sum(section_buckets) + weak_tail_count == total

Sanctions are exempt from the tail — a sanctions hit is material regardless of
how the name-match scored and must never be buried behind a "weak name-only"
disclosure.
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server

ROOT = Path(__file__).resolve().parents[2]
HTML = (ROOT / "arie-backoffice.html").read_text(encoding="utf-8")

THRESHOLD = server._TRIAGE_WEAK_THRESHOLD


def _row(items):
    return {"screening_evidence": {"items": items}}


def _hit(category, score):
    return {"category": category, "matched_name": "mick davis", "triage_score": score}


def _mick_davis_row():
    """The founder-reported shape: 1 material PEP + 1 material adverse media,
    15 weak watchlist, 13 weak adverse media = 30 distinct hits."""
    items = [_hit("pep", 65), _hit("adverse_media", 58)]
    items += [_hit("watchlist", 20) for _ in range(15)]
    items += [_hit("adverse_media", 18) for _ in range(13)]
    return _row(items)


# --- the reported defect -----------------------------------------------------

def test_mick_davis_tiles_reconcile_with_the_list():
    triage = server._screening_queue_row_triage(_mick_davis_row())
    assert triage["total"] == 30
    # What the four tiles now render.
    section = triage["section_buckets"]
    assert section["sanctions"] == 0
    assert section["watchlist"] == 0          # all 15 were below threshold
    assert section["pep"] == 1
    assert section["adverse_media"] == 1      # 1 material, 13 tailed
    assert triage["weak_tail_count"] == 28
    # Tile 1 (sanctions + watchlist) + PEP + adverse + weak == every hit, once.
    tiles = section["sanctions"] + section["watchlist"] + section["pep"] \
        + section["adverse_media"] + triage["weak_tail_count"]
    assert tiles == triage["total"] == 30


def test_legacy_overlapping_counts_are_preserved_for_backcompat():
    # The old fields keep their original semantics so stored/cached payloads
    # and any existing consumer are unaffected — the fix is additive.
    triage = server._screening_queue_row_triage(_mick_davis_row())
    assert triage["buckets"]["watchlist"] == 15
    assert triage["buckets"]["adverse_media"] == 14
    assert triage["weak_count"] == 28


# --- the invariant -----------------------------------------------------------

def test_partition_invariant_holds_across_shapes():
    shapes = [
        [],
        [_hit("sanctions", 95)],
        [_hit("watchlist", 5), _hit("pep", 5), _hit("adverse_media", 5), _hit("other", 5)],
        [_hit("pep", 99), _hit("pep", 1), _hit("adverse_media", THRESHOLD)],
        [_hit("other", 12) for _ in range(40)],
    ]
    for items in shapes:
        triage = server._screening_queue_row_triage(_row(items))
        assert sum(triage["section_buckets"].values()) + triage["weak_tail_count"] \
            == triage["total"] == len(items), items


def test_score_exactly_at_threshold_is_material_not_weak():
    triage = server._screening_queue_row_triage(_row([_hit("adverse_media", THRESHOLD)]))
    assert triage["section_buckets"]["adverse_media"] == 1
    assert triage["weak_tail_count"] == 0


def test_weak_buckets_report_the_below_threshold_remainder_per_category():
    triage = server._screening_queue_row_triage(_mick_davis_row())
    assert triage["weak_buckets"]["watchlist"] == 15
    assert triage["weak_buckets"]["adverse_media"] == 13
    assert triage["weak_buckets"]["pep"] == 0


# --- sanctions are never tailed ---------------------------------------------

def test_weak_sanctions_hit_stays_in_section_and_is_not_tailed():
    # A sanctions hit is material at any triage score. It must remain counted
    # in its section (visible in the red tile) and must NOT be swept into the
    # weak tail where an officer could miss it.
    triage = server._screening_queue_row_triage(_row([_hit("sanctions", 3)]))
    assert triage["section_buckets"]["sanctions"] == 1
    assert triage["weak_tail_count"] == 0
    # Still disclosed honestly as below threshold.
    assert triage["weak_buckets"]["sanctions"] == 1
    assert triage["weak_count"] == 1


# --- unscored hits are never assumed weak ------------------------------------

def test_unscored_hit_stays_in_section():
    triage = server._screening_queue_row_triage(
        _row([{"category": "adverse_media", "matched_name": "x"}])
    )
    assert triage["unscored_count"] == 1
    assert triage["section_buckets"]["adverse_media"] == 1
    assert triage["weak_tail_count"] == 0


# --- frontend wiring ---------------------------------------------------------

def _fn(name):
    start = HTML.index("function " + name)
    end = HTML.index("\nfunction ", start + 1)
    return HTML[start:end]


def test_tiles_read_the_reconciled_split():
    fn = _fn("screeningTriageStrip")
    assert "triage.section_buckets" in fn
    assert "triage.weak_tail_count" in fn
    # Category tiles must not read the legacy overlapping buckets directly.
    assert "tile(sectionBuckets.pep" in fn
    assert "tile(sectionBuckets.adverse_media" in fn
    assert "tile(weakTailCount" in fn
    # Below-threshold remainder is disclosed, never silently dropped.
    assert "below threshold" in fn
    # Graceful fallback for older cached payloads.
    assert "triage.section_buckets || buckets" in fn


def test_list_partition_exempts_sanctions_from_the_weak_tail():
    fn = _fn("screeningTriageRankedHitSections")
    assert "bucketKey !== 'sanctions'" in fn
    assert "grouped[bucketKey].push(entry)" in fn


def test_section_headers_count_what_they_render():
    fn = _fn("screeningTriageRankedHitSections")
    assert "sectionBuckets[meta.key]" in fn
    assert "' below threshold'" in fn
    # A section whose hits are all weak still renders, with the honest note.
    assert "!serverCount && !bucketWeak && !entries.length" in fn
    assert "if (bucketWeak > 0) {" in fn
