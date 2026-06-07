from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _html():
    return (ROOT / "arie-portal.html").read_text(encoding="utf-8")


def test_portal_periodic_review_task_can_show_reminder_status():
    html = _html()

    assert "periodicReviewNotificationHint" in html
    assert "Reminder status" in html
    assert "task.notification_summary" in html
    assert "Notification:" in html


def test_portal_notification_center_maps_periodic_review_types():
    html = _html()

    assert "periodic_review_required" in html
    assert "periodic_review_documents_required" in html
    assert "periodic_review_reminder" in html
    assert "periodic_review_overdue" in html


def test_portal_notification_copy_does_not_expose_risk_terms():
    html = _html()
    task_region = html[html.index("function periodicReviewNotificationHint"):]
    task_region = task_region[: task_region.index("function closePeriodicReviewModal")]

    assert "risk rating" not in task_region.lower()
    assert "risk score" not in task_region.lower()
