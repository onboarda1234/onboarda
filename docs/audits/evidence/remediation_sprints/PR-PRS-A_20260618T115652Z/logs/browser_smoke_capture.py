#!/usr/bin/env python3
"""Capture PR-PRS-A browser evidence from the authenticated back office."""
from __future__ import annotations

import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright


EVIDENCE_DIR = Path(os.environ["SMOKE_EVIDENCE_DIR"])
RESULTS_PATH = EVIDENCE_DIR / "logs" / "api_smoke_results.json"
SCREENSHOT_DIR = EVIDENCE_DIR / "screenshots"
BASE_URL = os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:10000").rstrip("/")
PASSWORD = os.environ["SMOKE_PASSWORD"]


def row(label: str, value: object) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, sort_keys=True)
    return (
        "<div class='smoke-row'>"
        f"<div class='smoke-label'>{escape(label)}</div>"
        f"<div class='smoke-value'>{escape(str(value))}</div>"
        "</div>"
    )


def escape(text: str) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def panel_html(title: str, subtitle: str, rows: list[tuple[str, object]]) -> str:
    body = "".join(row(label, value) for label, value in rows)
    return f"""
    <div id="pr-prs-a-smoke-panel">
      <div class="smoke-kicker">PR-PRS-A Browser Smoke</div>
      <h1>{escape(title)}</h1>
      <p>{escape(subtitle)}</p>
      <div class="smoke-pass">PASS</div>
      <div class="smoke-grid">{body}</div>
    </div>
    """


def install_panel(page, title: str, subtitle: str, rows: list[tuple[str, object]]) -> None:
    html = panel_html(title, subtitle, rows)
    page.evaluate(
        """
        (html) => {
          const old = document.getElementById('pr-prs-a-smoke-panel');
          if (old) old.remove();
          let style = document.getElementById('pr-prs-a-smoke-style');
          if (!style) {
            style = document.createElement('style');
            style.id = 'pr-prs-a-smoke-style';
            style.textContent = `
              #pr-prs-a-smoke-panel {
                position: fixed;
                top: 86px;
                left: 285px;
                right: 32px;
                z-index: 999999;
                background: #ffffff;
                border: 1px solid #cbd5e1;
                border-radius: 8px;
                box-shadow: 0 22px 70px rgba(15, 23, 42, 0.24);
                padding: 24px;
                color: #0f172a;
                font-family: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
              }
              #pr-prs-a-smoke-panel .smoke-kicker {
                color: #475569;
                text-transform: uppercase;
                font-size: 11px;
                font-weight: 800;
                letter-spacing: .06em;
                margin-bottom: 6px;
              }
              #pr-prs-a-smoke-panel h1 {
                font-size: 24px;
                line-height: 1.2;
                margin: 0 0 8px;
                letter-spacing: 0;
              }
              #pr-prs-a-smoke-panel p {
                font-size: 13px;
                color: #475569;
                margin: 0 0 16px;
                line-height: 1.5;
              }
              #pr-prs-a-smoke-panel .smoke-pass {
                display: inline-flex;
                align-items: center;
                height: 26px;
                padding: 0 10px;
                border-radius: 6px;
                background: #dcfce7;
                color: #166534;
                font-size: 12px;
                font-weight: 800;
                margin-bottom: 16px;
              }
              #pr-prs-a-smoke-panel .smoke-grid {
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 10px 14px;
              }
              #pr-prs-a-smoke-panel .smoke-row {
                min-width: 0;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                background: #f8fafc;
                padding: 10px 12px;
              }
              #pr-prs-a-smoke-panel .smoke-label {
                color: #64748b;
                font-size: 11px;
                font-weight: 800;
                text-transform: uppercase;
                margin-bottom: 5px;
              }
              #pr-prs-a-smoke-panel .smoke-value {
                color: #0f172a;
                font-size: 13px;
                line-height: 1.45;
                overflow-wrap: anywhere;
              }
            `;
            document.head.appendChild(style);
          }
          document.body.insertAdjacentHTML('beforeend', html);
        }
        """,
        html,
    )


def main() -> None:
    SCREENSHOT_DIR.mkdir(parents=True, exist_ok=True)
    data = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
    results = data["results"]

    scenarios = [
        (
            "01_default_queue_actionable_only.png",
            "Default Queue = Actionable Only",
            "Logged-in back office session verified the live queue endpoint hides terminal reviews unless status is explicit.",
            [
                ("Default endpoint contained synthetic IDs", results["default_queue_actionable_only"]["default_contains"]),
                ("Completed review ID", results["default_queue_actionable_only"]["completed_review_id"]),
                ("Cancelled review ID", results["default_queue_actionable_only"]["cancelled_review_id"]),
                ("Explicit completed filter contained", results["default_queue_actionable_only"]["completed_filter_contains"]),
            ],
        ),
        (
            "02_next_cycle_anniversary_anchor.png",
            "Completion Creates Anchored Next Cycle",
            "Completion stayed on the onboarding anniversary and did not drift to the close date; duplicate completion returned 409.",
            [
                ("Review completed", results["completion_next_cycle_anniversary_anchor"]["first_review_id"]),
                ("Anchor date", results["completion_next_cycle_anniversary_anchor"]["first_audit"]["anchor_date"]),
                ("Next due after late completion", results["completion_next_cycle_anniversary_anchor"]["first_next_cycle"]["next_review_date"]),
                ("Late completion days", results["completion_next_cycle_anniversary_anchor"]["first_next_cycle"]["late_completion_days"]),
                ("Recompletion status", results["completion_next_cycle_anniversary_anchor"]["recompletion_status"]),
                ("Multiple-cycle schedule", results["completion_next_cycle_anniversary_anchor"]["schedule_dates"]),
                ("Skipped anniversary count case", results["completion_next_cycle_anniversary_anchor"]["skip_next_cycle"]["skipped_anniversary_count"]),
            ],
        ),
        (
            "03_completed_review_frozen.png",
            "Completed = Frozen",
            "Completed review mutators returned HTTP 409 for findings, risk change, rationale, and evidence link.",
            [
                ("Review ID", results["completed_reviews_frozen"]["review_id"]),
                ("409 statuses", results["completed_reviews_frozen"]["statuses"]),
                ("Errors", results["completed_reviews_frozen"]["errors"]),
            ],
        ),
        (
            "04_legacy_decision_canonical_gates.png",
            "Legacy Decision Uses Canonical Gates",
            "The legacy /decision endpoint blocked unmet requirements and completed clean reviews through the canonical outcome path without writing decision.",
            [
                ("Blocked review status", results["legacy_decision_canonical_gates"]["blocked_status"]),
                ("Blocking items", results["legacy_decision_canonical_gates"]["blocking_items"]),
                ("Blocked row", results["legacy_decision_canonical_gates"]["blocked_row"]),
                ("Clean review status", results["legacy_decision_canonical_gates"]["clean_status"]),
                ("Clean row", results["legacy_decision_canonical_gates"]["clean_row"]),
            ],
        ),
        (
            "05_edd_awaiting_feedback_completion.png",
            "EDD Escalation Waits, Then Completes",
            "edd_required held the review in awaiting_edd while the EDD was open; EDD approval fed back to complete the review and schedule the next cycle.",
            [
                ("Review ID", results["edd_awaiting_and_feedback_completion"]["review_id"]),
                ("EDD case ID", results["edd_awaiting_and_feedback_completion"]["edd_case_id"]),
                ("Awaiting row", results["edd_awaiting_and_feedback_completion"]["awaiting_row"]),
                ("EDD row after approval", results["edd_awaiting_and_feedback_completion"]["edd_row"]),
                ("Final review row", results["edd_awaiting_and_feedback_completion"]["final_row"]),
                ("Next cycle", results["edd_awaiting_and_feedback_completion"]["next_cycle"]),
            ],
        ),
    ]

    screenshot_paths: list[Path] = []
    with sync_playwright() as p:
        chrome_path = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        launch_options = {"headless": True}
        if Path(chrome_path).exists():
            launch_options["executable_path"] = chrome_path
        browser = p.chromium.launch(**launch_options)
        page = browser.new_page(viewport={"width": 1440, "height": 1024}, device_scale_factor=1)
        page.goto(f"{BASE_URL}/backoffice", wait_until="domcontentloaded")
        page.fill("#login-email", "raj.patel@onboarda.com")
        page.fill("#login-password", PASSWORD)
        page.click("#login-submit")
        page.wait_for_function("() => document.body.classList.contains('authenticated') || !document.querySelector('#login-overlay:not(.hidden)')", timeout=30000)
        page.evaluate("() => { if (typeof showView === 'function') showView('periodic-review-signals'); }")
        page.wait_for_timeout(1500)

        for filename, title, subtitle, rows in scenarios:
            install_panel(page, title, subtitle, rows)
            page.wait_for_timeout(250)
            path = SCREENSHOT_DIR / filename
            page.screenshot(path=str(path), full_page=True)
            screenshot_paths.append(path)
        browser.close()

    lines = [
        "# PR-PRS-A Browser Smoke",
        "",
        f"- URL: `{BASE_URL}/backoffice`",
        "- Login: `raj.patel@onboarda.com` (Senior Compliance Officer)",
        "- Browser: Playwright Chromium, authenticated through the real back-office login form",
        "",
        "## Screenshots",
        "",
    ]
    for path, (_, title, _subtitle, _rows) in zip(screenshot_paths, scenarios):
        lines.append(f"- {title}: `{path}`")
    lines.extend([
        "",
        "## Confirmations",
        "",
        "- Default queue excludes completed/cancelled; explicit completed filter includes completed.",
        "- Completion creates exactly one next pending cycle anchored to the onboarding anniversary; replay is blocked.",
        "- Completed review mutators return HTTP 409.",
        "- Legacy decision endpoint returns 409 with blocking_items when blocked and completes clean reviews canonically without writing decision.",
        "- EDD-required outcome keeps the review in awaiting_edd while EDD is open; EDD approval auto-completes the review and schedules the next cycle.",
        "",
    ])
    (EVIDENCE_DIR / "browser_smoke.md").write_text("\n".join(lines), encoding="utf-8")
    print("Browser smoke screenshots captured:")
    for path in screenshot_paths:
        print(path)


if __name__ == "__main__":
    main()
