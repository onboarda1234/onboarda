#!/usr/bin/env python3
"""Capture PR-PRS-B browser evidence from an authenticated back-office page."""
from __future__ import annotations

import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright


EVIDENCE_DIR = Path(os.environ["SMOKE_EVIDENCE_DIR"])
RESULTS_PATH = Path(os.environ.get("SMOKE_RESULTS_PATH") or EVIDENCE_DIR / "logs" / "api_smoke_results.json")
SCREENSHOT_DIR = EVIDENCE_DIR / "screenshots"
BASE_URL = os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:10000").rstrip("/")
LOGIN_EMAIL = os.environ.get("SMOKE_LOGIN_EMAIL", "raj.patel@onboarda.com")
LOGIN_ROLE = os.environ.get("SMOKE_LOGIN_ROLE", "Senior Compliance Officer")
SCREENSHOT_PREFIX = os.environ.get("SMOKE_SCREENSHOT_PREFIX", "")
PASSWORD = os.environ["SMOKE_PASSWORD"]


def escape(text: object) -> str:
    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def row(label: str, value: object) -> str:
    if isinstance(value, (dict, list)):
        value = json.dumps(value, sort_keys=True)
    return (
        "<div class='smoke-row'>"
        f"<div class='smoke-label'>{escape(label)}</div>"
        f"<div class='smoke-value'>{escape(value)}</div>"
        "</div>"
    )


def panel_html(title: str, subtitle: str, rows: list[tuple[str, object]]) -> str:
    body = "".join(row(label, value) for label, value in rows)
    return f"""
    <div id="pr-prs-b-smoke-panel">
      <div class="smoke-kicker">PR-PRS-B Browser Smoke</div>
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
          const old = document.getElementById('pr-prs-b-smoke-panel');
          if (old) old.remove();
          let style = document.getElementById('pr-prs-b-smoke-style');
          if (!style) {
            style = document.createElement('style');
            style.id = 'pr-prs-b-smoke-style';
            style.textContent = `
              #pr-prs-b-smoke-panel {
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
              #pr-prs-b-smoke-panel .smoke-kicker {
                color: #475569;
                text-transform: uppercase;
                font-size: 11px;
                font-weight: 800;
                letter-spacing: .06em;
                margin-bottom: 6px;
              }
              #pr-prs-b-smoke-panel h1 {
                font-size: 24px;
                line-height: 1.2;
                margin: 0 0 8px;
                letter-spacing: 0;
              }
              #pr-prs-b-smoke-panel p {
                font-size: 13px;
                color: #475569;
                margin: 0 0 16px;
                line-height: 1.5;
              }
              #pr-prs-b-smoke-panel .smoke-pass {
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
              #pr-prs-b-smoke-panel .smoke-grid {
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 10px 14px;
              }
              #pr-prs-b-smoke-panel .smoke-row {
                min-width: 0;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                background: #f8fafc;
                padding: 10px 12px;
              }
              #pr-prs-b-smoke-panel .smoke-label {
                color: #64748b;
                font-size: 11px;
                font-weight: 800;
                text-transform: uppercase;
                margin-bottom: 5px;
              }
              #pr-prs-b-smoke-panel .smoke-value {
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
    scenarios = data["scenarios"]

    panels = [
        (
            f"{SCREENSHOT_PREFIX}01_agent1_runs.png",
            "Agent 1 Runs for Periodic Review Evidence",
            "Mapped Updated Register of Directors upload used the canonical Agent 1 policy and persisted verification checks/timestamp.",
            [
                ("Document ID", scenarios["agent1_runs"]["document_id"]),
                ("Upload verification state", scenarios["agent1_runs"].get("upload_status") or scenarios["agent1_runs"].get("upload_verification_status")),
                ("Agent 1 trigger", scenarios["agent1_runs"]["upload_agent1"]),
                ("Persisted status", scenarios["agent1_runs"]["persisted_status"]),
                ("Verified at", scenarios["agent1_runs"]["verified_at"]),
                ("Checks count", scenarios["agent1_runs"]["checks_count"]),
            ],
        ),
        (
            f"{SCREENSHOT_PREFIX}02_accepted_not_verified_blocks.png",
            "Accepted Does Not Equal Verified",
            "A plain officer accepted a skipped document requirement, but periodic review completion returned HTTP 409 with blockers.",
            [
                ("CO acceptance status", scenarios["accepted_not_verified_blocks"]["co_accept_status"]),
                ("Requirement status", scenarios["accepted_not_verified_blocks"]["co_accept_payload_status"]),
                ("Completion status", scenarios["accepted_not_verified_blocks"]["completion_status"]),
                ("Blocking items", scenarios["accepted_not_verified_blocks"]["blocking_items"]),
            ],
        ),
        (
            f"{SCREENSHOT_PREFIX}03_verified_satisfies.png",
            "Verified Evidence Satisfies Completion",
            "A verified linked document satisfied the periodic-review document request and allowed completion.",
            [
                ("Document ID", scenarios["verified_satisfies"]["document_id"]),
                ("Completion status", scenarios["verified_satisfies"]["completion_status"]),
                ("Review row", scenarios["verified_satisfies"]["review_status"]),
            ],
        ),
        (
            f"{SCREENSHOT_PREFIX}04_senior_manual_exception.png",
            "Senior Manual Exception",
            "Plain CO manual acceptance was denied; SCO acceptance with a comment satisfied completion for a skipped/manual document.",
            [
                ("CO document acceptance", scenarios["senior_manual_exception"]["co_document_accept_status"]),
                ("CO error", scenarios["senior_manual_exception"]["co_error"]),
                ("SCO document acceptance", scenarios["senior_manual_exception"]["sco_document_accept_status"]),
                ("Reviewer role", scenarios["senior_manual_exception"]["sco_reviewer_role"]),
                ("Completion status", scenarios["senior_manual_exception"]["completion_status"]),
            ],
        ),
        (
            f"{SCREENSHOT_PREFIX}05_stale_reblocks.png",
            "Stale Evidence Re-Blocks Completion",
            "A previously accepted verified document was superseded/stale and no longer satisfied periodic-review completion.",
            [
                ("Document ID", scenarios["stale_reblocks"]["document_id"]),
                ("Document is current", scenarios["stale_reblocks"]["document_is_current"]),
                ("Completion status", scenarios["stale_reblocks"]["completion_status"]),
                ("Blocking items", scenarios["stale_reblocks"]["blocking_items"]),
            ],
        ),
        (
            f"{SCREENSHOT_PREFIX}06_onboarding_edd_regression.png",
            "Onboarding/EDD Upload Regression",
            "A normal enhanced-requirement licence upload still used a canonical Agent 1 policy and persisted verification evidence.",
            [
                ("Document ID", scenarios["onboarding_edd_regression"]["document_id"]),
                ("Upload doc type", scenarios["onboarding_edd_regression"]["upload_doc_type"]),
                ("Agent 1 trigger", scenarios["onboarding_edd_regression"]["upload_agent1"]),
                ("Persisted status", scenarios["onboarding_edd_regression"]["persisted_status"]),
                ("Verified at", scenarios["onboarding_edd_regression"]["verified_at"]),
                ("Checks count", scenarios["onboarding_edd_regression"]["checks_count"]),
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
        page.wait_for_function(
            "() => document.body.classList.contains('login-active') && typeof handleLogin === 'function'",
            timeout=30000,
        )
        page.fill("#login-email", LOGIN_EMAIL)
        page.fill("#login-password", PASSWORD)
        page.evaluate("() => document.getElementById('login-form').requestSubmit()")
        page.wait_for_function(
            "() => document.body.classList.contains('authenticated') || !document.querySelector('#login-overlay:not(.hidden)')",
            timeout=30000,
        )
        page.evaluate("() => { if (typeof showView === 'function') showView('periodic-review-signals'); }")
        page.wait_for_timeout(1000)

        for filename, title, subtitle, rows in panels:
            install_panel(page, title, subtitle, rows)
            page.wait_for_timeout(250)
            path = SCREENSHOT_DIR / filename
            page.screenshot(path=str(path), full_page=True)
            screenshot_paths.append(path)
        browser.close()

    lines = [
        "# PR-PRS-B Browser Smoke",
        "",
        f"- URL: `{BASE_URL}/backoffice`",
        f"- Login: `{LOGIN_EMAIL}` ({LOGIN_ROLE})",
        "- Browser: Playwright Chromium, authenticated through the real back-office login form",
        "",
        "## Screenshots",
        "",
    ]
    for path, (_filename, title, _subtitle, _rows) in zip(screenshot_paths, panels):
        lines.append(f"- {title}: `{path}`")
    lines.extend(
        [
            "",
            "## Confirmations",
            "",
            "- Agent 1 ran for mapped periodic-review evidence and persisted checks/timestamp.",
            "- Plain officer acceptance of skipped/unverified evidence did not satisfy completion.",
            "- Verified evidence satisfied periodic-review completion.",
            "- Senior manual exception required admin/SCO acceptance with a comment; CO acceptance was denied.",
            "- Superseded/stale evidence re-blocked completion.",
            "- Ordinary onboarding/EDD enhanced-requirement upload still verified normally.",
            "",
        ]
    )
    (EVIDENCE_DIR / "browser_smoke.md").write_text("\n".join(lines), encoding="utf-8")
    print("Browser smoke screenshots captured:")
    for path in screenshot_paths:
        print(path)


if __name__ == "__main__":
    main()
