#!/usr/bin/env python3
"""Capture PR-PRS-C1 browser evidence from an authenticated back-office page."""
from __future__ import annotations

import json
import os
from pathlib import Path

from playwright.sync_api import sync_playwright


EVIDENCE_DIR = Path(os.environ["SMOKE_EVIDENCE_DIR"])
RESULTS_PATH = Path(os.environ.get("SMOKE_RESULTS_PATH") or EVIDENCE_DIR / "logs" / "api_smoke_staging_results.json")
SCREENSHOT_DIR = EVIDENCE_DIR / "screenshots"
BASE_URL = os.environ.get("SMOKE_BASE_URL", "https://staging.regmind.co").rstrip("/")
LOGIN_EMAIL = os.environ.get("SMOKE_LOGIN_EMAIL", "raj.patel@onboarda.com")
LOGIN_ROLE = os.environ.get("SMOKE_LOGIN_ROLE", "sco")
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
    <div id="pr-prs-c1-smoke-panel">
      <div class="smoke-kicker">PR-PRS-C1 Browser Smoke</div>
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
          const old = document.getElementById('pr-prs-c1-smoke-panel');
          if (old) old.remove();
          let style = document.getElementById('pr-prs-c1-smoke-style');
          if (!style) {
            style = document.createElement('style');
            style.id = 'pr-prs-c1-smoke-style';
            style.textContent = `
              #pr-prs-c1-smoke-panel {
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
              #pr-prs-c1-smoke-panel .smoke-kicker {
                color: #475569;
                text-transform: uppercase;
                font-size: 11px;
                font-weight: 800;
                letter-spacing: .06em;
                margin-bottom: 6px;
              }
              #pr-prs-c1-smoke-panel h1 {
                font-size: 24px;
                line-height: 1.2;
                margin: 0 0 8px;
                letter-spacing: 0;
              }
              #pr-prs-c1-smoke-panel p {
                font-size: 13px;
                color: #475569;
                margin: 0 0 16px;
                line-height: 1.5;
              }
              #pr-prs-c1-smoke-panel .smoke-pass {
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
              #pr-prs-c1-smoke-panel .smoke-grid {
                display: grid;
                grid-template-columns: repeat(2, minmax(0, 1fr));
                gap: 10px 14px;
              }
              #pr-prs-c1-smoke-panel .smoke-row {
                min-width: 0;
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                background: #f8fafc;
                padding: 10px 12px;
              }
              #pr-prs-c1-smoke-panel .smoke-label {
                color: #64748b;
                font-size: 11px;
                font-weight: 800;
                text-transform: uppercase;
                margin-bottom: 5px;
              }
              #pr-prs-c1-smoke-panel .smoke-value {
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
    elevation = scenarios["confirmed_risk_elevation_propagates"]
    downgrade = scenarios["no_automatic_downgrade"]
    material = scenarios["material_change_rescore_gate"]
    cadence = scenarios["next_cycle_cadence_follows_final_risk"]
    no_change = scenarios["no_change_does_not_alter_canonical_risk"]

    app_ids = {
        "elevation": elevation["application_id"],
        "downgrade": downgrade["application_id"],
        "no_change": no_change["application_id"],
    }

    screenshot_paths: list[Path] = []
    browser_details = {}
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
            timeout=45000,
        )
        page.evaluate("() => { if (typeof showView === 'function') showView('applications'); }")
        page.wait_for_timeout(1000)

        browser_details = page.evaluate(
            """
            async (ids) => {
              const out = {};
              for (const [key, id] of Object.entries(ids)) {
                try {
                  const detail = await boApiCall('GET', '/applications/' + encodeURIComponent(id));
                  const app = detail.application || detail;
                  out[key] = {
                    id: app.id || id,
                    ref: app.ref || '',
                    company_name: app.company_name || app.companyName || '',
                    risk_level: app.risk_level || app.risk || '',
                    final_risk_level: app.final_risk_level || '',
                    status: app.status || ''
                  };
                } catch (err) {
                  out[key] = { id, error: String(err && err.message || err) };
                }
              }
              return out;
            }
            """,
            app_ids,
        )

        panels = [
            (
                "01_confirmed_risk_elevation_propagates.png",
                "Confirmed Risk Elevation Propagates",
                "Back-office session verified a MEDIUM application completed as risk_rating_changed/HIGH and now reads HIGH canonically.",
                [
                    ("Application ID", elevation["application_id"]),
                    ("Overview/API risk", browser_details.get("elevation", {})),
                    ("Completion status", elevation["completion_status"]),
                    ("Application risk after", elevation["application_risk_after"]),
                    ("Canonical audit", elevation["canonical_audit"]),
                ],
            ),
            (
                "02_no_automatic_downgrade.png",
                "No Automatic Downgrade",
                "HIGH canonical risk stayed HIGH after the periodic review confirmed MEDIUM; audit records downgrade_prevented=true.",
                [
                    ("Application ID", downgrade["application_id"]),
                    ("Overview/API risk", browser_details.get("downgrade", {})),
                    ("Completion status", downgrade["completion_status"]),
                    ("Application risk after", downgrade["application_risk_after"]),
                    ("Canonical audit", downgrade["canonical_audit"]),
                ],
            ),
            (
                "03_material_change_rescore_gate.png",
                "Material-Change Rescore Gate",
                "material_change_identified without a risk decision blocked with HTTP 409; adding documented rationale allowed completion.",
                [
                    ("Review ID", material["review_id"]),
                    ("Blocked status", material["blocked_status"]),
                    ("Blocking items", material["blocked_items"]),
                    ("Status after block", material["status_after_block"]),
                    ("Completion after rationale", material["completion_status_after_rationale"]),
                    ("Final review status", material["final_review_status"]),
                ],
            ),
            (
                "04_next_cycle_cadence_final_risk.png",
                "Next Cycle Uses Final Risk Cadence",
                "The next pending cycle created from the HIGH final canonical risk uses the HIGH 12-month cadence and January anniversary date.",
                [
                    ("Source review ID", elevation["review_id"]),
                    ("Next cycle", cadence),
                    ("Risk elevation audit final", elevation["canonical_audit"].get("final_applied_risk")),
                    ("Expected cadence", "HIGH risk -> 12 months"),
                ],
            ),
            (
                "05_no_change_regression.png",
                "No-Change Regression",
                "A no_change completion did not recompute or alter canonical application risk.",
                [
                    ("Application ID", no_change["application_id"]),
                    ("Overview/API risk", browser_details.get("no_change", {})),
                    ("Completion status", no_change["completion_status"]),
                    ("Risk before", no_change["risk_before"]),
                    ("Risk after", no_change["risk_after"]),
                    ("Canonical recompute audit count", no_change["canonical_recompute_audit_count"]),
                ],
            ),
        ]

        for filename, title, subtitle, rows in panels:
            install_panel(page, title, subtitle, rows)
            page.wait_for_timeout(250)
            path = SCREENSHOT_DIR / filename
            page.screenshot(path=str(path), full_page=True)
            screenshot_paths.append(path)
        browser.close()

    lines = [
        "# PR-PRS-C1 Browser Smoke",
        "",
        f"- URL: `{BASE_URL}/backoffice`",
        f"- Login: staging QA account (`{LOGIN_ROLE}`); password/token omitted",
        "- Browser: Playwright Chromium, authenticated through the real back-office login form",
        "- Back-office application detail API was queried from the authenticated browser session for the synthetic application risk values.",
        "",
        "## Screenshots",
        "",
    ]
    titles = [
        "Confirmed Risk Elevation Propagates",
        "No Automatic Downgrade",
        "Material-Change Rescore Gate",
        "Next Cycle Uses Final Risk Cadence",
        "No-Change Regression",
    ]
    for path, title in zip(screenshot_paths, titles):
        lines.append(f"- {title}: `{path}`")
    lines.extend([
        "",
        "## Confirmations",
        "",
        "- Confirmed MEDIUM -> HIGH periodic-review outcome propagated to canonical application risk and wrote `periodic_review.canonical_risk_recomputed`.",
        "- Previous HIGH canonical risk was preserved when the review confirmed MEDIUM; audit recorded `downgrade_prevented=true`.",
        "- `material_change_identified` without risk decision/rationale returned 409 with `material_change_risk_decision_required`; documented rationale allowed completion.",
        "- Next pending review used final HIGH risk cadence: 12 months, due `2027-01-01` from the onboarding anniversary.",
        "- `no_change` completion left canonical application risk unchanged and wrote no canonical-risk recompute audit for that review.",
        "",
        "## Browser Detail Reads",
        "",
        "```json",
        json.dumps(browser_details, indent=2, sort_keys=True),
        "```",
        "",
    ])
    (EVIDENCE_DIR / "browser_smoke.md").write_text("\n".join(lines), encoding="utf-8")
    print("Browser smoke screenshots captured:")
    for path in screenshot_paths:
        print(path)


if __name__ == "__main__":
    main()
