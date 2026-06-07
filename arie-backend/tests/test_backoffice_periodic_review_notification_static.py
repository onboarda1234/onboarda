from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def _html():
    return (ROOT / "arie-backoffice.html").read_text(encoding="utf-8")


def test_periodic_review_queue_has_compact_notification_column():
    html = _html()
    queue_region = html[html.index('<div class="view" id="view-periodic-review-signals">'):]
    queue_region = queue_region[: queue_region.index('<!-- ═══════════════ MONITORING ALERTS')]

    assert "<th>Notifications</th>" in queue_region
    assert "periodicReviewNotificationCell(review)" in html
    assert "clientNotificationStatusLabel" in html
    assert "nextReminderDueAt" in html


def test_periodic_review_workspace_shows_notification_status_card():
    html = _html()

    assert "Notification Status" in html
    assert "Client reminders and officer alert state for this review." in html
    assert "renderPeriodicReviewWorkspaceNotifications(activeReview)" in html


def test_backoffice_notification_ui_does_not_reintroduce_internal_banner():
    html = _html()

    assert "Periodic Reviews owns the review cockpit." not in html
    assert "Lifecycle: 1 active linked item" not in html
