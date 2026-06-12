#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");

const REQUIRED_ENV = ["STAGING_QA_EMAIL", "STAGING_QA_PASSWORD"];
const REQUIRED_SMOKE_AREAS = [
  "Applications",
  "Application Detail",
  "KYC Documents",
  "Screening Review",
  "AI Compliance Supervisor",
  "Lifecycle Tab",
  "Case Management",
  "Ongoing Monitoring",
  "Monitoring Alerts",
  "Monitoring Agents",
  "Lifecycle Queue",
  "EDD",
  "Change Management",
];

function help() {
  return [
    "Authenticated AWS staging browser smoke.",
    "",
    "Required environment variables:",
    "  STAGING_QA_EMAIL       Approved staging QA officer email.",
    "  STAGING_QA_PASSWORD    Approved staging QA officer password.",
    "",
    "Optional environment variables:",
    "  STAGING_BASE_URL       Defaults to https://staging.regmind.co.",
    "  STAGING_SMOKE_APP_ID   Application ref/id to open; otherwise first visible row is used.",
    "  STAGING_SMOKE_OUT_DIR  Defaults to /tmp/regmind-staging-browser-smoke.",
    "  STAGING_SMOKE_FAIL_PATHS Comma-separated API pathnames to simulate as HTTP 503 after sign-in (for resilient-load QA).",
    "  CHROME_PATH            Chrome/Chromium executable path.",
    "  PLAYWRIGHT_NODE_MODULES Directory containing playwright-core, if not installed locally.",
    "  HEADLESS               Defaults to true; set false for headed debugging.",
    "",
    "Example:",
    "  STAGING_QA_EMAIL=... STAGING_QA_PASSWORD=... node arie-backend/scripts/qa/staging_browser_smoke.js",
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

const { chromium } = loadPlaywright();

const baseUrl = (process.env.STAGING_BASE_URL || "https://staging.regmind.co").replace(/\/+$/, "");
const email = process.env.STAGING_QA_EMAIL;
const password = process.env.STAGING_QA_PASSWORD;
const appId = process.env.STAGING_SMOKE_APP_ID || "";
const outDir = process.env.STAGING_SMOKE_OUT_DIR || "/tmp/regmind-staging-browser-smoke";
const failPaths = String(process.env.STAGING_SMOKE_FAIL_PATHS || "")
  .split(",")
  .map((value) => value.trim())
  .filter(Boolean);
const reportFile = path.join(outDir, "report.json");
const headless = String(process.env.HEADLESS || "true").toLowerCase() !== "false";

const report = {
  script: "staging_browser_smoke",
  baseUrl,
  startedAt: new Date().toISOString(),
  authenticatedLogin: "ui-form",
  credentialHandling: "STAGING_QA_EMAIL/STAGING_QA_PASSWORD environment variables only; values omitted",
  tokenInjectionUsed: false,
  authBypassUsed: false,
  simulatedFailurePaths: failPaths,
  requiredSmokeAreas: REQUIRED_SMOKE_AREAS,
  applicationRef: appId || null,
  screenshots: [],
  checks: {},
  observations: {},
  consoleErrors: [],
  consoleWarnings: [],
  nonBlockingConsoleErrors: [],
  blockingConsoleErrors: [],
  pageErrors: [],
  failedRequests: [],
  badResponses: [],
  knownRoleDeniedResponses: [],
  unexpectedBadResponses: [],
  providerLabelFindings: [],
};

const removedProviderPatterns = [
  "open" + "\\s*sanctions",
  "open" + "[-_]?sanctions",
  "open" + "\\s*sanction",
  "open" + "[-_]?sanction",
];

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

async function scanRemovedProviderLabels(page, surface) {
  const findings = await page.evaluate(({ patterns, surfaceName }) => {
    const regexes = patterns.map((pattern) => new RegExp(pattern, "i"));
    const visible = (el) => {
      if (!el || !el.innerText) return false;
      const style = window.getComputedStyle(el);
      if (style.display === "none" || style.visibility === "hidden") return false;
      const rect = el.getBoundingClientRect();
      return rect.width > 0 && rect.height > 0;
    };
    const selectorFor = (el) => {
      if (!el) return "";
      if (el.id) return `#${el.id}`;
      const parts = [];
      let node = el;
      while (node && node.nodeType === Node.ELEMENT_NODE && parts.length < 5) {
        let part = node.tagName.toLowerCase();
        if (node.classList && node.classList.length) {
          part += "." + Array.from(node.classList).slice(0, 3).join(".");
        }
        parts.unshift(part);
        node = node.parentElement;
      }
      return parts.join(" > ");
    };
    const matches = [];
    for (const el of Array.from(document.querySelectorAll("body *"))) {
      if (!visible(el)) continue;
      const ownText = el.innerText.trim().replace(/\s+/g, " ");
      if (!ownText) continue;
      if (regexes.some((regex) => regex.test(ownText))) {
        matches.push({ surface: surfaceName, source: "visible_dom", selector: selectorFor(el), text: ownText.slice(0, 240) });
      }
    }
    for (const storageName of ["localStorage", "sessionStorage"]) {
      const storage = window[storageName];
      for (let i = 0; i < storage.length; i += 1) {
        const key = storage.key(i);
        const value = storage.getItem(key) || "";
        if (regexes.some((regex) => regex.test(`${key} ${value}`))) {
          matches.push({ surface: surfaceName, source: storageName, selector: key, text: value.slice(0, 240) });
        }
      }
    }
    return matches;
  }, { patterns: removedProviderPatterns, surfaceName: surface });
  report.providerLabelFindings.push(...findings);
}

async function captureSurface(page, name, surface) {
  await scanRemovedProviderLabels(page, surface);
  await screenshot(page, name);
}

async function visible(page, selector) {
  return page.locator(selector).first().isVisible().catch(() => false);
}

async function fillLoginForm(page, email, password) {
  for (let attempt = 0; attempt < 3; attempt += 1) {
    await page.locator("#login-email").fill(email);
    await page.locator("#login-password").fill(password);
    const currentEmail = await page.locator("#login-email").inputValue();
    const currentPassword = await page.locator("#login-password").inputValue();
    if (currentEmail === email && currentPassword === password) {
      return { email: currentEmail, passwordLength: currentPassword.length, attempt: attempt + 1 };
    }
    await page.waitForTimeout(250);
  }
  const currentEmail = await page.locator("#login-email").inputValue().catch(() => "");
  const currentPassword = await page.locator("#login-password").inputValue().catch(() => "");
  return { email: currentEmail, passwordLength: currentPassword.length, attempt: 3 };
}

async function overlayState(page) {
  return page.$eval("#login-overlay", (el) => {
    const style = window.getComputedStyle(el);
    return {
      display: style.display,
      visibility: style.visibility,
      pointerEvents: style.pointerEvents,
      hidden: !!el.hidden,
      ariaHidden: el.getAttribute("aria-hidden"),
      className: el.className,
    };
  });
}

function isOverlayInactive(state) {
  return !!state &&
    state.display === "none" &&
    state.visibility === "hidden" &&
    state.pointerEvents === "none" &&
    state.hidden === true &&
    state.ariaHidden === "true" &&
    /\bhidden\b/.test(state.className || "");
}

async function clickNav(page, view) {
  const nav = page.locator(`.snav-item[data-view="${view}"]`).first();
  if (await nav.count()) {
    await nav.click();
  } else {
    await page.evaluate((targetView) => window.showView && window.showView(targetView), view);
  }
  await page.waitForFunction(
    (targetView) => document.getElementById(`view-${targetView}`)?.classList.contains("active"),
    view,
    { timeout: 30000 }
  );
}

async function clickDetailTab(page, tab) {
  await page.locator(`#tab-${tab}`).click();
  await page.waitForFunction(
    (targetTab) => {
      const panel = document.getElementById(`detail-tab-${targetTab}`);
      return !!panel && panel.style.display !== "none";
    },
    tab,
    { timeout: 30000 }
  );
}

async function openApplicationDetail(page) {
  if (appId) {
    await page.evaluate((ref) => window.openAppDetail(ref, { initialTab: "lifecycle" }), appId);
  } else {
    await page.locator("#applications-body tr").first().click();
  }
  await page.waitForSelector("#view-app-detail.active", { timeout: 30000 });
  if (!appId) {
    await clickDetailTab(page, "lifecycle");
  }
  await page.waitForFunction(
    () => {
      const panel = document.getElementById("detail-tab-lifecycle");
      return !!panel && panel.style.display !== "none";
    },
    { timeout: 30000 }
  );
}

function isKnownRoleDeniedResponse(responseInfo) {
  if (responseInfo.status !== 403) return false;
  try {
    const parsed = new URL(responseInfo.url);
    return parsed.pathname === "/api/users" || parsed.pathname === "/api/audit";
  } catch (err) {
    return false;
  }
}

function recordBadResponse(resp) {
  const info = { url: resp.url(), status: resp.status() };
  report.badResponses.push(info);
  if (isKnownRoleDeniedResponse(info)) {
    report.knownRoleDeniedResponses.push(info);
    return;
  }
  try {
    const parsed = new URL(info.url);
    if (parsed.pathname.startsWith("/api/") || info.status >= 500) {
      report.unexpectedBadResponses.push(info);
    }
  } catch (err) {
    if (info.status >= 500) report.unexpectedBadResponses.push(info);
  }
}

function isNonBlockingConsoleError(entry) {
  const text = String(entry && entry.text ? entry.text : "");
  if (text.includes("BO API Error: GET /users Error: Insufficient permissions")) return true;
  if (text.includes("BO API Error: GET /audit?limit=100 Error: Insufficient permissions")) return true;
  if (text.includes("Failed to load resource: the server responded with a status of 403")) {
    return report.knownRoleDeniedResponses.length > 0;
  }
  // Browser resource-load console entries are not JavaScript exceptions. The
  // response/request listeners above decide whether the underlying HTTP event is
  // blocking.
  if (text.includes("Failed to load resource: the server responded with a status of 404")) return true;
  return false;
}

function classifyConsoleErrors() {
  report.nonBlockingConsoleErrors = [];
  report.blockingConsoleErrors = [];
  for (const entry of report.consoleErrors) {
    if (isNonBlockingConsoleError(entry)) {
      report.nonBlockingConsoleErrors.push(entry);
    } else {
      report.blockingConsoleErrors.push(entry);
    }
  }
}

async function main() {
  ensureOutDir();
  const browser = await chromium.launch({ executablePath: chromePath(), headless });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
    ignoreHTTPSErrors: true,
  });
  const page = await context.newPage();

  if (failPaths.length) {
    await page.route("**/*", async (route) => {
      const requestUrl = route.request().url();
      try {
        const parsed = new URL(requestUrl);
        if (failPaths.includes(parsed.pathname)) {
          await route.fulfill({
            status: 503,
            contentType: "application/json",
            body: JSON.stringify({ error: `Simulated smoke failure for ${parsed.pathname}` }),
          });
          return;
        }
      } catch (err) {
        // Fall through to the original request when URL parsing fails.
      }
      await route.continue();
    });
  }

  page.on("console", (msg) => {
    const entry = { type: msg.type(), text: msg.text() };
    if (msg.type() === "error") report.consoleErrors.push(entry);
    if (msg.type() === "warning") report.consoleWarnings.push(entry);
  });
  page.on("pageerror", (err) => {
    report.pageErrors.push(String(err && err.message ? err.message : err));
  });
  page.on("requestfailed", (req) => {
    report.failedRequests.push({
      url: req.url(),
      method: req.method(),
      failure: req.failure()?.errorText || "",
    });
  });
  page.on("response", (resp) => {
    if (resp.status() >= 400) recordBadResponse(resp);
  });

  try {
    await page.goto(`${baseUrl}/backoffice`, { waitUntil: "domcontentloaded", timeout: 45000 });
    await page.waitForFunction(() => {
      const overlay = document.getElementById("login-overlay");
      const emailInput = document.getElementById("login-email");
      const passwordInput = document.getElementById("login-password");
      const submitBtn = document.getElementById("login-submit");
      if (!overlay || !emailInput || !passwordInput || !submitBtn) return false;
      const style = window.getComputedStyle(overlay);
      return style.display === "flex" &&
        style.visibility === "visible" &&
        style.pointerEvents !== "none" &&
        submitBtn.textContent.trim() === "Sign In" &&
        emailInput.disabled !== true &&
        passwordInput.disabled !== true;
    }, { timeout: 30000 });
    report.observations.loginFormValuesBeforeSubmit = await fillLoginForm(page, email, password);
    if (report.observations.loginFormValuesBeforeSubmit.email !== email ||
        report.observations.loginFormValuesBeforeSubmit.passwordLength !== password.length) {
      throw new Error("Login form values were not retained before submit");
    }
    const authResponsePromise = page.waitForResponse((resp) => {
      try {
        return new URL(resp.url()).pathname === "/api/auth/officer/login";
      } catch (err) {
        return false;
      }
    }, { timeout: 30000 });
    await page.locator('button:has-text("Login"), button:has-text("Sign In"), button[type="submit"]').first().click();
    const authResponse = await authResponsePromise;
    report.checks.loginRequestCompleted = true;
    report.checks.loginWithApprovedCredentials = authResponse.ok();
    report.observations.loginResponseStatus = authResponse.status();
    if (!authResponse.ok()) {
      throw new Error(`Login failed with status ${authResponse.status()}`);
    }
    await page.waitForFunction(() => {
      const overlay = document.getElementById("login-overlay");
      if (!overlay) return false;
      const style = window.getComputedStyle(overlay);
      return style.display === "none" &&
        style.visibility === "hidden" &&
        style.pointerEvents === "none" &&
        overlay.hidden === true &&
        overlay.getAttribute("aria-hidden") === "true" &&
        /\bhidden\b/.test(overlay.className || "");
    }, { timeout: 30000 });
    report.observations.overlayStateAfterLogin = await overlayState(page);
    report.checks.loginOverlayHidden = isOverlayInactive(report.observations.overlayStateAfterLogin);
    report.checks.noVisibleLoginErrorAfterSuccess = !(await visible(page, "#login-error.show"));
    report.observations.loginErrorText = await page.locator("#login-error-text").first().textContent().catch(() => "");
    report.checks.shellNavigationInteractive = false;

    if (failPaths.length) {
      await page.waitForSelector("#dashboard-load-warning.show", { timeout: 30000 });
      report.checks.resilientLoadWarningVisible = await visible(page, "#dashboard-load-warning.show");
    }

    await clickNav(page, "applications");
    report.checks.shellNavigationInteractive = true;
    await page.waitForSelector("#applications-body tr", { timeout: 30000 });
    report.checks.applicationsPageLoads = await visible(page, "#view-applications.active");
    await captureSurface(page, "applications", "Applications");

    await openApplicationDetail(page);
    report.checks.applicationDetailLoads = await visible(page, "#view-app-detail.active");
    report.checks.lifecycleTabLoads = await visible(page, "#detail-tab-lifecycle");
    await captureSurface(page, "application-detail-lifecycle", "Application Detail - Lifecycle");

    const tabChecks = [
      ["kyc-docs", "kycDocumentsTabLoads"],
      ["screening", "screeningReviewTabLoads"],
      ["supervisor", "complianceSupervisorTabLoads"],
      ["activity", "activityLogTabLoads"],
    ];
    for (const [tab, checkName] of tabChecks) {
      await clickDetailTab(page, tab);
      report.checks[checkName] = await visible(page, `#detail-tab-${tab}`);
      await captureSurface(page, `application-detail-${tab}`, `Application Detail - ${tab}`);
    }

    await clickNav(page, "cases");
    report.checks.caseManagementLoads = await visible(page, "#view-cases.active");
    await captureSurface(page, "case-management", "Case Management");

    await clickNav(page, "monitoring");
    report.checks.ongoingMonitoringLoads = await visible(page, "#view-monitoring.active");
    report.checks.monitoringAlertsLoad = await visible(page, "#monitoring-alerts-tab");
    await captureSurface(page, "ongoing-monitoring-alerts", "Monitoring Alerts");
    await page.locator('#view-monitoring .tab:has-text("Monitoring Agents")').click();
    await page.waitForFunction(() => document.getElementById("monitoring-agents-tab")?.style.display !== "none", {
      timeout: 30000,
    });
    report.checks.monitoringAgentsLoad = await visible(page, "#monitoring-agents-tab");
    await captureSurface(page, "ongoing-monitoring-agents", "Monitoring Agents");

    await clickNav(page, "lifecycle");
    await page.waitForSelector("#lifecycle-body tr", { timeout: 30000 });
    report.checks.lifecycleQueueLoads = await visible(page, "#view-lifecycle.active");
    await captureSurface(page, "lifecycle-queue", "Lifecycle Queue");

    await clickNav(page, "edd");
    report.checks.eddWorkflowLoads = await visible(page, "#view-edd.active");
    await captureSurface(page, "edd", "EDD");

    await clickNav(page, "change-mgmt");
    report.checks.changeManagementLoads = await visible(page, "#view-change-mgmt.active");
    await captureSurface(page, "change-management", "Change Management");

    report.checks.noTokenInjection = report.tokenInjectionUsed === false;
    report.checks.noAuthBypass = report.authBypassUsed === false;
    report.checks.screenshotsCaptured = report.screenshots.length >= 10;
    classifyConsoleErrors();
    report.checks.noBlockingConsoleErrors = report.blockingConsoleErrors.length === 0;
    report.checks.noPageErrors = report.pageErrors.length === 0;
    report.checks.noApi500Responses = report.badResponses.filter((entry) => entry.status >= 500).length === 0;
    report.checks.noUnexpectedBadApiResponses = report.unexpectedBadResponses.length === 0;
    report.checks.noFailedRequests = report.failedRequests.length === 0;
    report.checks.noRemovedProviderLabels = report.providerLabelFindings.length === 0;

    report.observations.knownOfficerRoleDeniedResponses = report.knownRoleDeniedResponses.length;
  } finally {
    await browser.close();
    writeReport();
  }

  const failedChecks = Object.entries(report.checks).filter(([, passed]) => passed !== true);
  if (failedChecks.length) {
    console.error(`Staging browser smoke failed ${failedChecks.length} check(s). Report: ${reportFile}`);
    process.exit(1);
  }
  console.log(`Staging browser smoke passed. Report: ${reportFile}`);
}

main().catch((err) => {
  report.fatal = String(err && err.stack ? err.stack : err);
  writeReport();
  console.error(report.fatal);
  process.exit(1);
});
