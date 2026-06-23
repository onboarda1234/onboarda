#!/usr/bin/env node
"use strict";

const crypto = require("crypto");
const fs = require("fs");
const http = require("http");
const path = require("path");
const { execFileSync } = require("child_process");

const REPO = path.resolve(__dirname, "../../../../../..");
const BASE = "https://staging.regmind.co";
const REGION = "af-south-1";
const CLUSTER = "regmind-staging";
const SERVICE = "regmind-backend";
const CONTAINER = "regmind-backend";
const OUT_DIR = path.resolve(__dirname, "..");
const SCREENSHOT_DIR = path.join(OUT_DIR, "screenshots");
const MERGE_SHA_UNDER_TEST = execFileSync("git", ["rev-parse", "HEAD"], { cwd: REPO, encoding: "utf8" }).trim();

fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });

function loadPlaywright() {
  try {
    return require("playwright");
  } catch (_) {
    return require("playwright-core");
  }
}

function chromePath() {
  const candidates = [
    process.env.CHROME_PATH,
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
  ].filter(Boolean);
  for (const candidate of candidates) {
    if (fs.existsSync(candidate)) return candidate;
  }
  return undefined;
}

function sh(cmd, args, opts = {}) {
  return execFileSync(cmd, args, {
    cwd: opts.cwd || REPO,
    encoding: "utf8",
    stdio: ["ignore", "pipe", "pipe"],
    timeout: opts.timeout || 240000,
    maxBuffer: 20 * 1024 * 1024,
  });
}

function getSecret() {
  return JSON.parse(sh("aws", [
    "secretsmanager",
    "get-secret-value",
    "--secret-id",
    "regmind/staging",
    "--region",
    REGION,
    "--query",
    "SecretString",
    "--output",
    "text",
  ]));
}

function b64url(value) {
  return Buffer.from(value).toString("base64").replace(/=/g, "").replace(/\+/g, "-").replace(/\//g, "_");
}

function signJwt(payload, secret) {
  const now = Math.floor(Date.now() / 1000);
  const header = b64url(JSON.stringify({ alg: "HS256", typ: "JWT" }));
  const body = b64url(JSON.stringify({
    iss: "arie-finance",
    iat: now,
    nbf: now,
    exp: now + 6 * 60 * 60,
    jti: crypto.randomBytes(16).toString("hex"),
    ...payload,
  }));
  const sig = crypto.createHmac("sha256", secret).update(`${header}.${body}`).digest("base64")
    .replace(/=/g, "").replace(/\+/g, "-").replace(/\//g, "_");
  return `${header}.${body}.${sig}`;
}

function getTaskArn() {
  return sh("aws", [
    "ecs",
    "list-tasks",
    "--cluster",
    CLUSTER,
    "--service-name",
    SERVICE,
    "--region",
    REGION,
    "--query",
    "taskArns[0]",
    "--output",
    "text",
  ]).trim();
}

function parseMarkedJson(output) {
  const match = output.match(/JSON_RESULT_START\s*([\s\S]*?)\s*JSON_RESULT_END/);
  if (!match) throw new Error(`Remote command did not return marked JSON:\n${output.slice(-3000)}`);
  return JSON.parse(match[1]);
}

function runRemotePython(source, argv = []) {
  const b64 = Buffer.from(source, "utf8").toString("base64");
  const quotedArgs = argv.map((value) => `'${String(value).replace(/'/g, "'\\''")}'`).join(" ");
  const command = `/bin/sh -c 'cd /app && echo ${b64} | base64 -d > /tmp/portal_modal_fixture.py && PYTHONPATH=/app python /tmp/portal_modal_fixture.py ${quotedArgs}'`;
  const output = sh("aws", [
    "ecs",
    "execute-command",
    "--cluster",
    CLUSTER,
    "--region",
    REGION,
    "--task",
    getTaskArn(),
    "--container",
    CONTAINER,
    "--interactive",
    "--command",
    command,
  ], { timeout: 300000 });
  return parseMarkedJson(output);
}

const fixtureScript = String.raw`
import json
import sys
from datetime import datetime, timedelta, timezone
from db import get_db

prefix = sys.argv[1]
cmd = sys.argv[2] if len(sys.argv) > 2 else "create"

def row_get(row, key, default=None):
    if row is None:
        return default
    try:
        return row[key]
    except Exception:
        return row.get(key, default) if isinstance(row, dict) else default

def cols(db, table):
    rows = db.execute("SELECT column_name FROM information_schema.columns WHERE table_name = ?", (table,)).fetchall()
    return {row_get(row, "column_name") for row in rows}

def insert_dynamic(db, table, data, returning=None):
    known = cols(db, table)
    keys = [key for key in data if key in known]
    values = [data[key] for key in keys]
    sql = f"INSERT INTO {table} ({','.join(keys)}) VALUES ({','.join(['?'] * len(keys))})"
    if returning:
        row = db.execute(sql + f" RETURNING {returning}", tuple(values)).fetchone()
        return row_get(row, returning)
    db.execute(sql, tuple(values))
    return None

def cleanup(db):
    app_id = prefix + "-app"
    client_id = prefix + "-client"
    review_rows = db.execute("SELECT id FROM periodic_reviews WHERE application_id = ?", (app_id,)).fetchall()
    review_ids = [row_get(row, "id") for row in review_rows]
    for rid in review_ids:
        db.execute("DELETE FROM periodic_review_evidence_links WHERE periodic_review_id = ?", (rid,))
        db.execute("DELETE FROM periodic_review_memos WHERE periodic_review_id = ?", (rid,))
        db.execute("DELETE FROM application_enhanced_requirements WHERE linked_periodic_review_id = ?", (rid,))
    db.execute("DELETE FROM periodic_reviews WHERE application_id = ?", (app_id,))
    db.execute("DELETE FROM client_notifications WHERE application_id = ? OR client_id = ?", (app_id, client_id))
    db.execute("DELETE FROM documents WHERE application_id = ?", (app_id,))
    db.execute("DELETE FROM applications WHERE id = ?", (app_id,))
    db.execute("DELETE FROM clients WHERE id = ?", (client_id,))
    db.commit()

def create(db):
    cleanup(db)
    now = datetime.now(timezone.utc)
    client_id = prefix + "-client"
    app_id = prefix + "-app"
    insert_dynamic(db, "clients", {
        "id": client_id,
        "email": prefix + "@example.invalid",
        "password_hash": "not-used-browser-smoke",
        "company_name": "Portal Modal QA Ltd",
        "status": "active",
        "created_at": now.isoformat(),
    })
    insert_dynamic(db, "applications", {
        "id": app_id,
        "ref": "PRPORTAL-" + prefix[-8:].upper(),
        "client_id": client_id,
        "company_name": "Portal Modal QA Ltd",
        "country": "Mauritius",
        "sector": "Financial services",
        "entity_type": "company",
        "status": "approved",
        "risk_score": 72,
        "risk_level": "HIGH",
        "final_risk_level": "HIGH",
        "final_risk_score": 72,
        "prescreening_data": json.dumps({"screening_report": {"status": "clear", "screened_at": now.isoformat()}}),
        "is_fixture": False,
        "created_at": (now - timedelta(days=365)).isoformat(),
        "updated_at": now.isoformat(),
        "inputs_updated_at": now.isoformat(),
        "first_approved_at": (now - timedelta(days=365)).isoformat(),
        "decided_at": (now - timedelta(days=365)).isoformat(),
        "periodic_review_baseline_status": "last_onboarding_date",
        "periodic_review_baseline_date": (now - timedelta(days=365)).date().isoformat(),
        "periodic_review_baseline_cadence_months": 12,
        "periodic_review_next_review_due": now.date().isoformat(),
    })
    rid = insert_dynamic(db, "periodic_reviews", {
        "application_id": app_id,
        "client_name": "Portal Modal QA Ltd",
        "risk_level": "HIGH",
        "status": "pending",
        "due_date": now.date().isoformat(),
        "next_review_date": now.date().isoformat(),
        "trigger_type": "scheduled",
        "trigger_source": "time_based",
        "trigger_reason": "PR-PRS-PORTAL-ATTESTATION-MODAL-LAYOUT-FIX-1 smoke fixture",
        "review_reason": "PR-PRS-PORTAL-ATTESTATION-MODAL-LAYOUT-FIX-1 smoke fixture",
        "priority": "normal",
        "review_type": "scheduled",
        "policy_version": "smoke",
        "frequency_months": 12,
        "calculation_basis": "smoke",
        "client_attestation_status": "not_started",
        "client_attestation_payload": "{}",
        "client_notification_status": "sent",
        "initial_notification_sent_at": now.isoformat(),
        "notification_channel": "portal",
        "baseline_status": "last_onboarding_date",
        "baseline_date": (now - timedelta(days=365)).date().isoformat(),
        "baseline_cadence_months": 12,
        "baseline_note": "Browser layout smoke fixture",
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }, returning="id")
    db.commit()
    return {"client_id": client_id, "application_id": app_id, "review_id": rid}

db = get_db()
try:
    if cmd == "cleanup":
        cleanup(db)
        result = {"cleaned": True, "prefix": prefix}
    else:
        result = create(db)
    print("JSON_RESULT_START")
    print(json.dumps(result, default=str))
    print("JSON_RESULT_END")
finally:
    db.close()
`;

function startStaticServer() {
  const portalPath = path.join(REPO, "arie-portal.html");
  const html = fs.readFileSync(portalPath);
  const server = http.createServer((req, res) => {
    if (req.url === "/portal" || req.url === "/" || req.url === "/arie-portal.html") {
      res.writeHead(200, { "content-type": "text/html; charset=utf-8" });
      res.end(html);
      return;
    }
    if (req.url === "/favicon.ico") {
      res.writeHead(204);
      res.end();
      return;
    }
    res.writeHead(404, { "content-type": "text/plain" });
    res.end("not found");
  });
  return new Promise((resolve) => {
    server.listen(0, "127.0.0.1", () => resolve({
      server,
      url: `http://127.0.0.1:${server.address().port}`,
    }));
  });
}

async function routeApiToStaging(page, token, networkLog, badResponses) {
  await page.route("**/api/**", async (route) => {
    const req = route.request();
    const localUrl = new URL(req.url());
    const target = `${BASE}${localUrl.pathname}${localUrl.search}`;
    const headers = { ...req.headers(), authorization: `Bearer ${token}` };
    delete headers.host;
    delete headers.origin;
    delete headers.referer;
    delete headers["content-length"];
    const started = Date.now();
    const body = req.method() === "GET" || req.method() === "HEAD" ? undefined : req.postDataBuffer();
    try {
      const resp = await fetch(target, { method: req.method(), headers, body });
      const buffer = Buffer.from(await resp.arrayBuffer());
      const entry = {
        method: req.method(),
        url: target.replace(BASE, ""),
        status: resp.status,
        ms: Date.now() - started,
      };
      networkLog.push(entry);
      if (resp.status >= 400) badResponses.push(entry);
      await route.fulfill({
        status: resp.status,
        headers: {
          "content-type": resp.headers.get("content-type") || "application/json",
          "cache-control": "no-store",
        },
        body: buffer,
      });
    } catch (err) {
      const entry = { method: req.method(), url: target.replace(BASE, ""), status: "route_error", error: String(err) };
      networkLog.push(entry);
      badResponses.push(entry);
      await route.abort();
    }
  });
}

function assertModalGeometry(result) {
  const failures = [];
  if (!result.modalOpen) failures.push("modal is not open");
  if (!result.bodyLocked) failures.push("body is not scroll-locked");
  if (result.card.left < 0) failures.push(`card left clipped (${result.card.left})`);
  if (result.card.right > result.viewport.width) failures.push(`card right clipped (${result.card.right} > ${result.viewport.width})`);
  if (result.card.width > result.viewport.width) failures.push("card wider than viewport");
  if (result.scrollWidth > result.viewport.width + 1) failures.push(`horizontal overflow ${result.scrollWidth} > ${result.viewport.width}`);
  if (result.close.top < 0 || result.close.right > result.viewport.width || result.close.left < 0) failures.push("close button clipped");
  if (!result.titleVisible || !result.questionsVisible || !result.answerControlsVisible) failures.push("title/questions/answer controls not visible");
  if (result.sidebarZ >= result.modalZ) failures.push(`sidebar z-index ${result.sidebarZ} is not below modal z-index ${result.modalZ}`);
  if (!result.modalAtLeftEdge) failures.push("modal does not cover the left viewport edge above the sidebar");
  if (!result.bodyCanScroll) failures.push("modal body did not scroll vertically");
  return failures;
}

(async () => {
  const prefix = "prportalmodal-" + crypto.randomBytes(4).toString("hex");
  const secret = getSecret();
  const jwtSecret = secret.JWT_SECRET || secret.jwt_secret || secret.SECRET_KEY;
  const fixture = runRemotePython(fixtureScript, [prefix, "create"]);
  const token = signJwt({
    sub: fixture.client_id,
    role: "client",
    name: "Portal Modal QA Ltd",
    type: "client",
  }, jwtSecret);
  const { server, url } = await startStaticServer();
  const { chromium } = loadPlaywright();
  const report = {
    ok: false,
    base_url: url,
    staging_api: BASE,
    branch_head: MERGE_SHA_UNDER_TEST,
    fixture,
    screenshots: {},
    checks: {},
    geometry: {},
    console_errors: [],
    console_warnings: [],
    page_errors: [],
    network: [],
    bad_responses: [],
  };
  let browser;
  try {
    browser = await chromium.launch({
      headless: String(process.env.HEADLESS || "true").toLowerCase() !== "false",
      executablePath: chromePath(),
    });
    const context = await browser.newContext();
    const page = await context.newPage();
    page.on("console", (msg) => {
      if (msg.type() === "error") report.console_errors.push(msg.text());
      if (msg.type() === "warning") report.console_warnings.push(msg.text());
    });
    page.on("pageerror", (err) => report.page_errors.push(String(err)));
    page.on("requestfailed", (req) => {
      if (!req.url().includes("fonts.")) {
        report.bad_responses.push({ method: req.method(), url: req.url(), status: "request_failed", failure: req.failure() });
      }
    });
    await routeApiToStaging(page, token, report.network, report.bad_responses);

    const viewports = [
      { name: "1440", width: 1440, height: 900 },
      { name: "1280", width: 1280, height: 800 },
      { name: "1024", width: 1024, height: 768 },
    ];

    for (const vp of viewports) {
      await page.setViewportSize({ width: vp.width, height: vp.height });
      await page.goto(`${url}/portal`, { waitUntil: "domcontentloaded" });
      await page.evaluate(({ tokenValue, client }) => {
        setAuth(tokenValue, client);
        showView("my-apps");
        return loadMyApplications();
      }, {
        tokenValue: token,
        client: { id: fixture.client_id, email: `${prefix}@example.invalid`, company: "Portal Modal QA Ltd" },
      });
      await page.waitForSelector("#periodic-review-tasks-container .periodic-review-task-card", { timeout: 30000 });
      await page.locator("#periodic-review-tasks-container .btn-submit").first().click();
      await page.waitForSelector("#periodic-review-modal.open .periodic-review-question", { timeout: 30000 });
      await page.locator("#periodic-review-modal-body").evaluate((el) => { el.scrollTop = 120; });
      await page.waitForTimeout(100);
      const screenshot = path.join(SCREENSHOT_DIR, `after-${vp.name}.png`);
      await page.screenshot({ path: screenshot, fullPage: false });
      report.screenshots[vp.name] = screenshot;
      const geometry = await page.evaluate(() => {
        const modal = document.getElementById("periodic-review-modal");
        const card = document.querySelector(".periodic-review-modal-card");
        const body = document.getElementById("periodic-review-modal-body");
        const close = document.querySelector(".periodic-review-modal-head .btn-outline");
        const title = document.getElementById("periodic-review-modal-title");
        const firstQuestion = document.querySelector(".periodic-review-question-title");
        const answer = document.querySelector(".periodic-review-answer-pill");
        const sidebar = document.getElementById("global-client-sidebar");
        const rect = (el) => {
          const r = el.getBoundingClientRect();
          return { left: r.left, right: r.right, top: r.top, bottom: r.bottom, width: r.width, height: r.height };
        };
        const bodyBefore = body.scrollTop;
        body.scrollTop = bodyBefore + 160;
        const bodyAfter = body.scrollTop;
        const elementAtLeft = document.elementFromPoint(8, Math.max(80, Math.min(window.innerHeight - 8, rect(card).top + 24)));
        return {
          viewport: { width: window.innerWidth, height: window.innerHeight },
          scrollWidth: document.documentElement.scrollWidth,
          bodyLocked: document.body.classList.contains("periodic-review-modal-open") && getComputedStyle(document.body).overflow === "hidden",
          modalOpen: modal.classList.contains("open"),
          modalZ: Number(getComputedStyle(modal).zIndex) || 0,
          sidebarZ: Number(getComputedStyle(sidebar).zIndex) || 0,
          card: rect(card),
          close: rect(close),
          titleVisible: !!(title && rect(title).width > 0 && title.innerText.includes("Periodic Review Attestation")),
          questionsVisible: !!(firstQuestion && rect(firstQuestion).left >= 0 && rect(firstQuestion).right <= window.innerWidth),
          answerControlsVisible: !!(answer && rect(answer).left >= 0 && rect(answer).right <= window.innerWidth),
          bodyCanScroll: body.scrollHeight > body.clientHeight && bodyAfter > bodyBefore,
          modalAtLeftEdge: !!(elementAtLeft && elementAtLeft.closest("#periodic-review-modal")),
        };
      });
      const failures = assertModalGeometry(geometry);
      report.geometry[vp.name] = { ...geometry, failures };
      report.checks[`viewport_${vp.name}`] = failures.length === 0;
      if (failures.length) {
        throw new Error(`Viewport ${vp.name} failed: ${failures.join("; ")}`);
      }
    }

    await page.locator(".periodic-review-modal-head .btn-outline").click();
    await page.waitForFunction(() => {
      const modal = document.getElementById("periodic-review-modal");
      return modal && !modal.classList.contains("open") && modal.getAttribute("aria-hidden") === "true";
    }, { timeout: 5000 });
    report.checks.close_button = await page.evaluate(() => {
      const sidebar = document.getElementById("global-client-sidebar");
      const dashboard = document.getElementById("view-my-apps");
      return !!sidebar.classList.contains("open") &&
        !!document.body.classList.contains("sidebar-active") &&
        !document.body.classList.contains("periodic-review-modal-open") &&
        !dashboard.classList.contains("hidden");
    });
    report.checks.no_console_errors = report.console_errors.length === 0 && report.page_errors.length === 0;
    report.checks.no_bad_api_responses = report.bad_responses.filter((entry) => !String(entry.url || "").includes("fonts.")).length === 0;
    report.ok = Object.values(report.checks).every(Boolean);
    if (!report.ok) throw new Error("One or more browser checks failed");
  } finally {
    try {
      runRemotePython(fixtureScript, [prefix, "cleanup"]);
      report.cleanup = { cleaned: true, prefix };
    } catch (err) {
      report.cleanup = { cleaned: false, prefix, error: String(err) };
    }
    if (browser) await browser.close();
    await new Promise((resolve) => server.close(resolve));
    fs.writeFileSync(path.join(OUT_DIR, "logs", "browser_smoke.raw.json"), JSON.stringify(report, null, 2));
  }
  console.log(JSON.stringify(report, null, 2));
})().catch((err) => {
  console.error(err);
  process.exit(1);
});
