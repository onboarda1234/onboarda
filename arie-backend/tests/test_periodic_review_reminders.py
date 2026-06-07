from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys


sys.path.insert(0, str(Path(__file__).resolve().parent))

from periodic_review_notifications import process_periodic_review_notification
from periodic_review_projection_service import get_review_projection
from test_periodic_review_notifications import _actor, _create_review, prs6_db


def test_projection_marks_reminder_due_when_next_reminder_has_passed(prs6_db):
    initial = datetime(2026, 5, 20, 9, 0, tzinfo=timezone.utc)
    due_at = initial + timedelta(days=7)
    review = _create_review(
        prs6_db,
        notification_status="sent",
        initial_sent_at=initial.isoformat(),
    )
    prs6_db.execute(
        "UPDATE periodic_reviews SET next_reminder_due_at = ? WHERE id = ?",
        (due_at.isoformat(), review["id"]),
    )
    prs6_db.commit()

    projection = get_review_projection(prs6_db, review["id"])

    assert projection["client_notification_status"] == "reminder_due"
    assert projection["client_notification_status_label"] == "Reminder due"


def test_second_reminder_stuck_state_creates_officer_alert(prs6_db):
    initial = datetime(2026, 6, 1, 9, 0, tzinfo=timezone.utc)
    now = initial + timedelta(days=15)
    review = _create_review(
        prs6_db,
        due_date="2026-06-30",
        notification_status="sent",
        initial_sent_at=initial.isoformat(),
        reminder_count=1,
    )
    result = process_periodic_review_notification(prs6_db, review, now=now, actor=_actor())
    prs6_db.commit()
    stored = prs6_db.execute("SELECT * FROM periodic_reviews WHERE id = ?", (review["id"],)).fetchone()

    assert result["sent_events"] == ["periodic_review_reminder"]
    assert stored["reminder_count"] == 2
    assert stored["officer_alert_status"] == "active"
