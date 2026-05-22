#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");

const REQUIRED_ENV = [
  "STAGING_PORTAL_EMAIL",
  "STAGING_PORTAL_PASSWORD",
  "STAGING_UPLOAD_FILE",
];

function help() {
  return [
    "Portal async verification browser smoke.",
    "",
    "Required environment variables:",
    "  STAGING_PORTAL_EMAIL       Approved staging client email.",
    "  STAGING_PORTAL_PASSWORD    Approved staging client password.",
    "  STAGING_UPLOAD_FILE        Local PDF/image file to upload through the real portal UI.",
    "",
    "Optional environment variables:",
    "  STAGING_BASE_URL                 Defaults to https://staging.regmind.co.",
    "  STAGING_PORTAL_APP_REF           Application ref/id to open; otherwise the first app is used.",
    "  STAGING_PORTAL_UPLOAD_SELECTOR   Defaults to #view-onboarding input[type=file].",
    "  STAGING_PORTAL_SMOKE_OUT_DIR     Defaults to /tmp/regmind-portal-async-verification-smoke.",
    "  CHROME_PATH                      Chrome/Chromium executable path.",
    "  PLAYWRIGHT_NODE_MODULES          Directory containing playwright-core.",
    "  HEADLESS                         Defaults to true; set false for headed debugging.",
    "",
    "Example:",
    "  STAGING_PORTAL_EMAIL=... STAGING_PORTAL_PASSWORD=... STAGING_UPLOAD_FILE=/tmp/test.pdf node arie-backend/scripts/qa/portal_async_verification_smoke.js",
  ].join("\n");
}

if (process.argv.includes("--help") || process.argv.includes("-h")) {
  console.log(help());
  process.exit(0);
}

function loadPlaywright() {
  try {
    return require("playwright-core");
  } catch (err) {
    const modulesDir = process.env.PLAYWRIGHT_NODE_MODULES;
    if (modulesDir) {
      try {
        return require(path.join(modulesDir, "playwright-core"));
      } catch (innerErr) {
        throw new Error(
          "Unable to load playwright-core from PLAYWRIGHT_NODE_MODULES. " +
            "Install it outside the repo or set PLAYWRIGHT_NODE_MODULES to a directory containing playwright-core."
        );
      }
    }
    throw new Error(
      "playwright-core is required for the browser smoke. " +
        "Install it outside the repo, or set PLAYWRIGHT_NODE_MODULES to an existing node_modules directory."
    );
  }
}

const missing = REQUIRED_ENV.filter((name) => !process.env[name]);
if (missing.length) {
  console.error("Missing required environment variables: " + missing.join(", "));
  console.error("Credential values must be supplied via environment variables and are never written to the report.");
  process.exit(2);
}

const uploadFile = process.env.STAGING_UPLOAD_FILE;
if (!fs.existsSync(uploadFile)) {
  console.error("STAGING_UPLOAD_FILE does not exist: " + uploadFile);
  process.exit(2);
}

const { chromium } = loadPlaywright();

const baseUrl = (process.env.STAGING_BASE_URL || "https://staging.regmind.co").replace(/\/+$/, "");
const email = process.env.STAGING_PORTAL_EMAIL;
const password = process.env.STAGING_PORTAL_PASSWORD;
const appRef = process.env.STAGING_PORTAL_APP_REF || "";
const uploadSelector = process.env.STAGING_PORTAL_UPLOAD_SELECTOR || "#view-onboarding input[type=file]";
const outDir = process.env.STAGING_PORTAL_SMOKE_OUT_DIR || "/tmp/regmind-portal-async-verification-smoke";
const reportFile = path.join(outDir, "report.json");
const headless = String(process.env.HEADLESS || "true").toLowerCase() !== "false";

const report = {
  script: "portal_async_verification_smoke",
  baseUrl,
  startedAt: new Date().toISOString(),
  authenticatedLogin: "ui-form",
  credentialHandling: "STAGING_PORTAL_EMAIL/STAGING_PORTAL_PASSWORD environment variables only; values omitted",
  tokenInjectionUsed: false,
  authBypassUsed: false,
  applicationRef: appRef || null,
  uploadSelector,
  screenshots: [],
  checks: {},
  observations: {},
  consoleErrors: [],
  pageErrors: [],
  failedRequests: [],
  badResponses: [],
};

function ensureOutDir() {
  fs.mkdirSync(outDir, { recursive: true });
}

function writeReport() {
  ensureOutDir();
  report.finishedAt = new Date().toISOString();
  fs.writeFileSync(reportFile, JSON.stringify(report, null, 2));
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
  throw new Error("No Chrome/Chromium executable found. Set CHROME_PATH.");
}

async function screenshot(page, name) {
  const file = path.join(outDir, `${name}.png`);
  await page.screenshot({ path: file, fullPage: true });
  report.screenshots.push(file);
}

async function signIn(page) {
  await page.goto(`${baseUrl}/portal`, { waitUntil: "domcontentloaded" });
  await page.locator("#l-email").fill(email);
  await page.locator("#l-password").fill(password);
  await Promise.all([
    page.waitForResponse((resp) => resp.url().includes("/api/auth/client/login") && resp.status() < 500),
    page.locator("#login-form button[type=submit], #login-form .btn-submit").first().click(),
  ]);
  await page.waitForFunction(() => !!window.AUTH_TOKEN, { timeout: 30000 });
  report.checks.login = true;
}

async function openApplication(page) {
  if (appRef) {
    await page.evaluate((ref) => window.resumeApplication && window.resumeApplication(ref, "onboarding"), appRef);
  } else {
    await page.waitForSelector("#my-apps-tbody tr", { timeout: 30000 });
    await page.locator("#my-apps-tbody tr").first().click();
  }
  await page.waitForFunction(() => !!window.currentApplicationId, { timeout: 30000 });
  await page.evaluate(() => window.showView && window.showView("onboarding"));
  await page.waitForSelector("#view-onboarding:not(.hidden)", { timeout: 30000 });
  report.checks.applicationOpened = true;
}

async function uploadAndWaitForTerminal(page) {
  const input = page.locator(uploadSelector).first();
  await input.setInputFiles(uploadFile);
  await page.waitForSelector(
    '.doc-verify-results[data-verification-state="pending"], .doc-verify-results[data-verification-state="in_progress"]',
    { timeout: 30000 }
  );
  report.checks.pendingRendered = true;
  await screenshot(page, "portal-pending");

  const terminalCard = page.locator(
    '.doc-verify-results[data-verification-state="verified"], ' +
      '.doc-verify-results[data-verification-state="flagged"], ' +
      '.doc-verify-results[data-verification-state="failed"]'
  ).first();
  await terminalCard.waitFor({ timeout: 180000 });
  const state = await terminalCard.getAttribute("data-verification-state");
  const success = await terminalCard.getAttribute("data-verification-success");
  const text = (await terminalCard.innerText()).trim();

  report.observations.terminalState = state;
  report.observations.terminalSuccessAttribute = success;
  report.observations.terminalText = text;
  report.checks.terminalRendered = ["verified", "flagged", "failed"].includes(state || "");
  report.checks.noFalseSuccessForNonVerified = state === "verified" || (success !== "true" && !/stored and verified/i.test(text));
  await screenshot(page, "portal-terminal");

  if (!report.checks.noFalseSuccessForNonVerified) {
    throw new Error("Non-verified terminal state rendered success-style language.");
  }
}

(async () => {
  ensureOutDir();
  const browser = await chromium.launch({
    headless,
    executablePath: chromePath(),
  });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });

  page.on("console", (msg) => {
    if (msg.type() === "error") report.consoleErrors.push({ text: msg.text() });
  });
  page.on("pageerror", (err) => report.pageErrors.push({ message: err.message }));
  page.on("requestfailed", (req) => report.failedRequests.push({ url: req.url(), failure: req.failure()?.errorText || "" }));
  page.on("response", (resp) => {
    if (resp.status() >= 400) report.badResponses.push({ url: resp.url(), status: resp.status() });
  });

  try {
    await signIn(page);
    await openApplication(page);
    await uploadAndWaitForTerminal(page);
    report.result = "pass";
  } catch (err) {
    report.result = "fail";
    report.error = err.message;
    try { await screenshot(page, "failure"); } catch (screenshotErr) {}
    process.exitCode = 1;
  } finally {
    await browser.close();
    writeReport();
    console.log("Portal async verification smoke report: " + reportFile);
  }
})();
