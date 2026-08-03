#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");

const REQUIRED_ENV = ["STAGING_QA_EMAIL", "STAGING_QA_PASSWORD"];
const PROTECTED_CANONICAL_PROFILE = "protected-canonical";
const ROLE_MATRIX_PROFILE = "role-matrix";
const PROTECTED_SMOKE_AREAS = [
  "Applications",
  "Application Detail",
  "KYC Documents",
  "Screening Review",
  "Screening Queue",
  "Screening Queue Filters and Pagination",
  "Screening Evidence",
  "Four-Eyes Screening Controls",
  "Risk Scoring Model",
  "Authoritative Risk Evidence",
  "RegMind AI Compliance Supervisor",
  "Lifecycle Tab",
  "Case Management",
  "RegMind Monitoring",
  "Monitoring Alerts",
  "Monitoring Pilot Scope",
  "Lifecycle Queue",
  "EDD",
  "Change Management",
];
const ROLE_MATRIX_SMOKE_AREAS = [
  "Applications",
  "Application Detail",
  "KYC Documents",
  "Screening Review",
  "RegMind AI Compliance Supervisor",
  "Lifecycle Tab",
  "Case Management",
  "RegMind Monitoring",
  "Monitoring Alerts",
  "Monitoring Pilot Scope",
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
    "  STAGING_SMOKE_PROFILE  protected-canonical (default) or role-matrix.",
    "  Application specimen   protected-canonical is fixed to RM-PILOT-039.",
    "  STAGING_SMOKE_APP_ID   Required internal ID/ref only for role-matrix.",
    "  STAGING_SMOKE_EXPECTED_ROLE Required only for role-matrix: sco, co, or analyst.",
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
const smokeProfile = String(
  process.env.STAGING_SMOKE_PROFILE || PROTECTED_CANONICAL_PROFILE
).trim().toLowerCase();
if (![PROTECTED_CANONICAL_PROFILE, ROLE_MATRIX_PROFILE].includes(smokeProfile)) {
  console.error(
    `Unsupported STAGING_SMOKE_PROFILE=${smokeProfile}; expected ` +
      `${PROTECTED_CANONICAL_PROFILE} or ${ROLE_MATRIX_PROFILE}.`
  );
  process.exit(2);
}
const roleMatrixProfile = smokeProfile === ROLE_MATRIX_PROFILE;
const expectedRole = roleMatrixProfile
  ? String(process.env.STAGING_SMOKE_EXPECTED_ROLE || "").trim().toLowerCase()
  : "";
const appId = roleMatrixProfile
  ? String(process.env.STAGING_SMOKE_APP_ID || "").trim()
  : "RM-PILOT-039";
if (roleMatrixProfile && !appId) {
  console.error("STAGING_SMOKE_APP_ID is required for the role-matrix profile.");
  process.exit(2);
}
if (roleMatrixProfile && !["sco", "co", "analyst"].includes(expectedRole)) {
  console.error(
    "STAGING_SMOKE_EXPECTED_ROLE must be sco, co, or analyst for the role-matrix profile."
  );
  process.exit(2);
}
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
  smokeProfile,
  expectedRole: expectedRole || null,
  simulatedFailurePaths: failPaths,
  requiredSmokeAreas: roleMatrixProfile
    ? ROLE_MATRIX_SMOKE_AREAS
    : PROTECTED_SMOKE_AREAS,
  requestedApplicationTarget: appId,
  applicationRef: null,
  applicationId: null,
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
  limitations: [],
  applicationStatusTokenFindings: {
    officerStatusSurfaces: [],
    fixturePartyNames: [],
    visibleInternalMachineCodes: [],
    storageMachineCodes: [],
  },
};

const removedProviderPatterns = [
  "open" + "\\s*sanctions",
  "open" + "[-_]?sanctions",
  "open" + "\\s*sanction",
  "open" + "[-_]?sanction",
];

const applicationStatusTokenPatterns = [
  "submitted_to_compliance",
  "officer_submitted_to_compliance",
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

async function scanApplicationStatusTokens(page, surface) {
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
    const categoryForVisibleText = (text, selector) => {
      if (/officer_submitted_to_compliance/i.test(text)) return "visibleInternalMachineCodes";
      if (/submitted_to_compliance\s+(director|owner|ubo|beneficial owner|fixture)/i.test(text)) return "fixturePartyNames";
      if (/(status|badge|filter-status|detail-badges|applications-body|case-command|decision)/i.test(selector)) {
        return "officerStatusSurfaces";
      }
      return "officerStatusSurfaces";
    };
    const matches = [];
    for (const el of Array.from(document.querySelectorAll("body *"))) {
      if (!visible(el)) continue;
      const ownText = el.innerText.trim().replace(/\s+/g, " ");
      if (!ownText) continue;
      if (regexes.some((regex) => regex.test(ownText))) {
        const selector = selectorFor(el);
        matches.push({
          surface: surfaceName,
          source: "visible_dom",
          category: categoryForVisibleText(ownText, selector),
          selector,
          text: ownText.slice(0, 240),
        });
      }
    }
    for (const storageName of ["localStorage", "sessionStorage"]) {
      const storage = window[storageName];
      for (let i = 0; i < storage.length; i += 1) {
        const key = storage.key(i);
        const value = storage.getItem(key) || "";
        if (regexes.some((regex) => regex.test(`${key} ${value}`))) {
          matches.push({
            surface: surfaceName,
            source: storageName,
            category: "storageMachineCodes",
            selector: key,
            text: value.slice(0, 240),
          });
        }
      }
    }
    return matches;
  }, { patterns: applicationStatusTokenPatterns, surfaceName: surface });
  for (const finding of findings) {
    const category = finding.category || "officerStatusSurfaces";
    if (!report.applicationStatusTokenFindings[category]) {
      report.applicationStatusTokenFindings[category] = [];
    }
    report.applicationStatusTokenFindings[category].push(finding);
  }
}

async function captureSurface(page, name, surface) {
  await scanRemovedProviderLabels(page, surface);
  await scanApplicationStatusTokens(page, surface);
  await screenshot(page, name);
}

async function visible(page, selector) {
  return page.locator(selector).first().isVisible().catch(() => false);
}

async function controlState(page, selector) {
  return page.locator(selector).first().evaluate((el) => {
    const style = window.getComputedStyle(el);
    return {
      present: true,
      visible: style.display !== "none" && style.visibility !== "hidden",
      disabled: !!el.disabled,
      ariaDisabled: el.getAttribute("aria-disabled"),
      actionState: el.getAttribute("data-action-state"),
      disabledReason: el.getAttribute("data-disabled-reason") || el.title || "",
      text: String(el.textContent || "").trim().replace(/\s+/g, " "),
    };
  }).catch(() => ({ present: false }));
}

function controlIsEnabled(state) {
  return !!state &&
    state.present === true &&
    state.visible === true &&
    state.disabled === false &&
    state.ariaDisabled !== "true";
}

function controlIsDisabled(state, reasonPattern) {
  return !!state &&
    state.present === true &&
    state.visible === true &&
    state.disabled === true &&
    (!reasonPattern || reasonPattern.test(String(state.disabledReason || "")));
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
  const normalizedView = view === "screening-queue" ? "screening" : view;
  const nav = page.locator(`.snav-item[data-view="${view}"]`).first();
  if (await nav.count()) {
    await nav.click();
  } else {
    await page.evaluate((targetView) => window.showView && window.showView(targetView), view);
  }
  await page.waitForFunction(
    (targetView) => document.getElementById(`view-${targetView}`)?.classList.contains("active"),
    normalizedView,
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
    report.observations.authenticatedRole = await page.evaluate(() =>
      String(window.currentUser?.role || window.BO_AUTH_USER?.role || "").toLowerCase()
    );
    report.observations.openedApplication = await page.evaluate(() => {
      const app = window.currentApp || window._currentDetailApp || {};
      return {
        id: app.id || app.application_id || null,
        ref: app.ref || app.application_ref || null,
        status: String(app.statusRaw || app.status_raw || app.status || "").toLowerCase(),
      };
    });
    report.applicationId = report.observations.openedApplication.id;
    report.applicationRef = report.observations.openedApplication.ref;
    report.checks.requestedApplicationOpened =
      report.observations.openedApplication.id === appId ||
      report.observations.openedApplication.ref === appId;
    if (roleMatrixProfile) {
      report.checks.authenticatedRoleMatchesExpected =
        report.observations.authenticatedRole === expectedRole;
    }
    report.checks.applicationDetailLoads = await visible(page, "#view-app-detail.active");
    report.checks.lifecycleTabLoads = await visible(page, "#detail-tab-lifecycle");
    await captureSurface(page, "application-detail-lifecycle", "Application Detail - Lifecycle");

    const tabChecks = [
      ["overview", "overviewTabLoads"],
      ["kyc-docs", "kycDocumentsTabLoads"],
      ["screening", "screeningReviewTabLoads"],
      ["supervisor", "complianceSupervisorTabLoads"],
      ["alerts", "alertsTabLoads"],
      ["activity", "activityLogTabLoads"],
    ];
    for (const [tab, checkName] of tabChecks) {
      await clickDetailTab(page, tab);
      report.checks[checkName] = await visible(page, `#detail-tab-${tab}`);
      if (!roleMatrixProfile && tab === "overview") {
        await page.waitForFunction(() => {
          const app = window.currentApp || window._currentDetailApp || {};
          const memo = window._currentMemoData || {};
          const approveMemo = document.getElementById("btn-approve-memo");
          const appStatus = String(app.statusRaw || app.status_raw || app.status || "").toLowerCase();
          const memoStatus = String(memo.review_status || "").toLowerCase();
          return app.ref === "RM-PILOT-039" &&
            appStatus === "approved" &&
            memoStatus === "approved" &&
            approveMemo?.disabled === true &&
            /already been approved|already approved/i.test(String(approveMemo.title || ""));
        }, { timeout: 30000 });
        report.observations.memoControlState = {
          generate: await controlState(page, "#btn-generate-memo"),
          validate: await controlState(page, "#btn-revalidate-memo"),
          approveMemo: await controlState(page, "#btn-approve-memo"),
        };
        report.observations.applicationDecisionControlState = {
          approve: await controlState(page, "#btn-approve"),
          reject: await controlState(page, "#btn-reject"),
          submitToCompliance: await controlState(page, "#btn-submit-compliance"),
        };
        report.observations.authoritativeRiskDisplay = await page.evaluate(() => {
          const app = window.currentApp || {};
          const evidence = app.riskReportEvidence || app.risk_report_evidence || {};
          return {
            applicationRef: app.ref || null,
            score: app.riskScore ?? app.risk_score ?? null,
            level: app.riskLevel || app.risk_level || null,
            configVersion: app.riskConfigVersion || app.risk_config_version || evidence.config_version || null,
            evidenceAvailable: evidence.available === true,
            evidenceAuthoritative: evidence.authoritative === true,
            evidenceReadOnly: evidence.read_only === true,
          };
        });
        report.observations.expectedCanonicalControlState = await page.evaluate(() => {
          const app = window.currentApp || window._currentDetailApp || {};
          const memo = window._currentMemoData || {};
          return {
            applicationRef: app.ref || null,
            applicationStatus: String(app.statusRaw || app.status_raw || app.status || "").toLowerCase(),
            memoReviewStatus: String(memo.review_status || "").toLowerCase(),
            expected: {
              generateMemo: "enabled",
              validateMemo: "enabled",
              approveMemo: "disabled_already_approved",
              approveApplication: "disabled_terminal",
              rejectApplication: "disabled_terminal",
              submitToCompliance: "disabled_terminal",
            },
          };
        });
        report.checks.memoControlsRetainBackendDerivedState =
          report.observations.expectedCanonicalControlState.applicationRef === "RM-PILOT-039" &&
          report.observations.expectedCanonicalControlState.applicationStatus === "approved" &&
          report.observations.expectedCanonicalControlState.memoReviewStatus === "approved" &&
          controlIsEnabled(report.observations.memoControlState.generate) &&
          controlIsEnabled(report.observations.memoControlState.validate) &&
          controlIsDisabled(
            report.observations.memoControlState.approveMemo,
            /already been approved|already approved/i
          );
        report.checks.applicationDecisionControlsRetainBackendDerivedState =
          controlIsDisabled(
            report.observations.applicationDecisionControlState.approve,
            /already approved|terminal/i
          ) &&
          controlIsDisabled(
            report.observations.applicationDecisionControlState.reject,
            /already approved|terminal/i
          ) &&
          controlIsDisabled(
            report.observations.applicationDecisionControlState.submitToCompliance,
            /already approved|terminal/i
          );
        report.checks.authoritativeRiskEvidenceRenders =
          await visible(page, "#detail-score-breakdown") &&
          report.observations.authoritativeRiskDisplay.evidenceAvailable === true &&
          report.observations.authoritativeRiskDisplay.evidenceAuthoritative === true;
        report.checks.riskExportControlsRender =
          await visible(page, 'button[onclick="downloadRiskCSV()"]') &&
          await visible(page, 'button[onclick="downloadRiskPDF()"]');
      }
      if (!roleMatrixProfile && tab === "kyc-docs") {
        report.observations.kycDocumentSemanticEvidence = await page.locator("#detail-docs-with-verification").evaluate((el) => {
          const app = window.currentApp || window._currentDetailApp || {};
          const docs = Array.isArray(app._documents)
            ? app._documents
            : (Array.isArray(app.documents) ? app.documents : []);
          const rendered = String(el.innerText || "").trim();
          const normalized = rendered.toLowerCase();
          const identities = docs.map((doc) => ({
            id: String(doc.id || doc.document_id || ""),
            name: String(doc.doc_name || ""),
            type: String(doc.doc_type || doc.slot_key || ""),
            reviewStatus: String(doc.review_status || ""),
            verificationStatus: String(doc.verification_state || doc.verification_status || ""),
            isCurrent: doc.is_current !== false,
          }));
          const renderedIdentityCount = identities.filter((doc) =>
            [doc.name, doc.type.replace(/_/g, " "), doc.id]
              .filter((value) => value.length >= 4)
              .some((value) => normalized.includes(value.toLowerCase()))
          ).length;
          return {
            documentCount: identities.length,
            currentDocumentCount: identities.filter((doc) => doc.isCurrent).length,
            renderedIdentityCount,
            reviewStatuses: Array.from(new Set(identities.map((doc) => doc.reviewStatus).filter(Boolean))).sort(),
            verificationStatuses: Array.from(new Set(identities.map((doc) => doc.verificationStatus).filter(Boolean))).sort(),
            hasLoadingOrErrorState: /loading|could not be loaded|load error|service unavailable/i.test(rendered),
            renderedTextLength: rendered.length,
          };
        });
        report.checks.kycDocumentEvidenceRenders =
          report.observations.kycDocumentSemanticEvidence.documentCount > 0 &&
          report.observations.kycDocumentSemanticEvidence.currentDocumentCount > 0 &&
          report.observations.kycDocumentSemanticEvidence.renderedIdentityCount > 0 &&
          report.observations.kycDocumentSemanticEvidence.reviewStatuses.length > 0 &&
          report.observations.kycDocumentSemanticEvidence.verificationStatuses.length > 0 &&
          report.observations.kycDocumentSemanticEvidence.hasLoadingOrErrorState === false;
      }
      if (!roleMatrixProfile && tab === "screening") {
        report.observations.applicationScreeningSemanticEvidence = await page.locator("#detail-screening-review").evaluate((el) => {
          const app = window.currentApp || window._currentDetailApp || {};
          const reviews = app.screeningReviews || app.screening_reviews || [];
          const summary = app.screeningTruthSummary || app.screening_truth_summary || {};
          const rendered = String(el.innerText || "").trim();
          return {
            reviewCount: Array.isArray(reviews) ? reviews.length : 0,
            canonicalState: summary.canonical_state || null,
            defensibleClear: summary.defensible_clear === true,
            providerAvailability: summary.provider_availability || null,
            hasLoadingOrErrorState:
              /loading application detail|evidence could not be loaded|load error|service unavailable/i.test(rendered) ||
              !!el.querySelector("[data-screening-evidence-loading], [data-screening-evidence-error], [data-screening-evidence-outage]"),
            renderedTextLength: rendered.length,
          };
        });
        report.checks.applicationScreeningEvidenceRenders =
          report.observations.applicationScreeningSemanticEvidence.reviewCount > 0 &&
          !!report.observations.applicationScreeningSemanticEvidence.canonicalState &&
          !!report.observations.applicationScreeningSemanticEvidence.providerAvailability &&
          report.observations.applicationScreeningSemanticEvidence.renderedTextLength > 0 &&
          report.observations.applicationScreeningSemanticEvidence.hasLoadingOrErrorState === false;
      }
      await captureSurface(page, `application-detail-${tab}`, `Application Detail - ${tab}`);
    }

    if (!roleMatrixProfile) {
      await clickNav(page, "screening-queue");
      await page.waitForFunction(
      () => !window.SCREENING_QUEUE?.is_loading && document.querySelectorAll("#screening-body tr").length > 0,
      { timeout: 30000 }
    );
    report.checks.screeningQueueLoads = await visible(page, "#view-screening.active");
    report.checks.screeningQueueFiltersRender =
      await visible(page, "#screening-queue-search") &&
      await visible(page, "#screening-filter-status") &&
      await visible(page, "#screening-filter-type") &&
      await visible(page, "#screening-filter-pep");
    report.checks.screeningQueuePaginationRenders =
      await visible(page, "#screening-queue-status") &&
      await visible(page, "#screening-queue-pagination");
    report.checks.fixturesExcludedFromDefaultScreeningQueue = await page.evaluate(() => {
      const toggle = document.getElementById("show-fixture-records-toggle");
      const text = document.getElementById("screening-body")?.innerText || "";
      return toggle?.checked === false && !/ARF-QAFIX-|RM-PILOT-/i.test(text);
    });
    await captureSurface(page, "screening-queue-default", "Screening Queue - Default");

    const fixtureToggle = page.locator("#show-fixture-records-toggle");
    if (!(await fixtureToggle.isVisible())) {
      throw new Error("Approved QA role cannot access the fixture visibility control");
    }
    await fixtureToggle.check();
    await page.waitForFunction(
      () => window.showTestSmokeRecordsEnabled?.() === true && !window.SCREENING_QUEUE?.is_loading,
      { timeout: 30000 }
    );
    await page.locator("#screening-queue-search").fill("ARF-QAFIX-004");
    await page.waitForFunction(
      () => !window.SCREENING_QUEUE?.is_loading &&
        window.SCREENING_QUEUE?.rows?.some((row) => row.application_ref === "ARF-QAFIX-004"),
      { timeout: 30000 }
    );
    report.checks.pendingOrErroredScreenNeverAppearsClear = await page.evaluate(() => {
      const row = (window.SCREENING_QUEUE?.rows || []).find(
        (item) => item.application_ref === "ARF-QAFIX-004"
      );
      return !!row &&
        ["failed", "pending_provider", "stale"].includes(String(row.screening_truth_state || row.screening_state || "")) &&
        row.defensible_clear === false &&
        !/clear/i.test(String(row.status_key || ""));
    });
    report.observations.providerErrorScreeningState = await page.evaluate(() => {
      const row = (window.SCREENING_QUEUE?.rows || []).find(
        (item) => item.application_ref === "ARF-QAFIX-004"
      ) || {};
      return {
        applicationRef: row.application_ref || null,
        screeningState: row.screening_state || null,
        truthState: row.screening_truth_state || null,
        statusKey: row.status_key || null,
        defensibleClear: row.defensible_clear === true,
        reviewActionable: row.review_actionable === true,
      };
    });
    await page.locator('#screening-body tr:has-text("ARF-QAFIX-004") button:has-text("View")').click();
    await page.waitForSelector("#view-app-detail.active #detail-tab-screening", { timeout: 30000 });
    report.checks.screeningReviewOpens = await visible(page, "#detail-screening-review");
    report.checks.erroredScreenHasNoDispositionAction = await page.evaluate(() => {
      const host = document.getElementById("detail-screening-review");
      if (!host) return false;
      return !Array.from(host.querySelectorAll("button")).some((button) =>
        /clear as|confirm true match|escalate|request more information/i.test(button.textContent || "")
      );
    });
    await captureSurface(page, "screening-review-provider-error", "Screening Review - Provider Error");

    await clickNav(page, "screening-queue");
    await page.locator("#screening-queue-search").fill("ARF-QAFIX-006");
    await page.waitForFunction(
      () => !window.SCREENING_QUEUE?.is_loading &&
        window.SCREENING_QUEUE?.rows?.some((row) =>
          row.application_ref === "ARF-QAFIX-006" && Number(row.total_hits || 0) > 0
        ),
      { timeout: 30000 }
    );
    report.observations.fourEyesQueueState = await page.evaluate(() => {
      const row = (window.SCREENING_QUEUE?.rows || []).find(
        (item) => item.application_ref === "ARF-QAFIX-006" && item.subject_type === "entity"
      ) || {};
      return {
        applicationRef: row.application_ref || null,
        totalHits: Number(row.total_hits || 0),
        requiresFourEyes: row.requires_four_eyes === true,
        fourEyesStatus: row.review_four_eyes_status || null,
        reviewActionable: row.review_actionable === true,
      };
    });
    report.checks.populatedScreeningEvidenceRenders =
      report.observations.fourEyesQueueState.totalHits > 0;
    report.checks.fourEyesStateRenders =
      report.observations.fourEyesQueueState.requiresFourEyes === true &&
      report.observations.fourEyesQueueState.fourEyesStatus === "pending_second_review";
    report.observations.fourEyesQueueActionState = await page.locator(
      '#screening-body tr:has-text("ARF-QAFIX-006")'
    ).first().evaluate((row) => {
      const actions = Array.from(row.querySelectorAll("button"))
        .map((button) => ({
          text: String(button.textContent || "").trim().replace(/\s+/g, " "),
          disabled: button.disabled === true,
          ariaDisabled: button.getAttribute("aria-disabled"),
          title: String(button.title || ""),
        }))
        .filter((action) => action.text !== "View");
      return { actions };
    });
    report.checks.fourEyesQueueOffersExactSecondReviewAction =
      report.observations.fourEyesQueueActionState.actions.length === 1 &&
      report.observations.fourEyesQueueActionState.actions[0].text ===
        "Clear as False Positive" &&
      report.observations.fourEyesQueueActionState.actions[0].disabled === false &&
      report.observations.fourEyesQueueActionState.actions[0].ariaDisabled !==
        "true";
    await page.locator('#screening-body tr:has-text("ARF-QAFIX-006")').first().locator('button:has-text("View")').click();
    await page.waitForSelector("#view-app-detail.active #detail-tab-screening", { timeout: 30000 });
    await page.waitForFunction(() => {
      const row = (window.SCREENING_QUEUE?.rows || []).find(
        (item) => item.application_ref === "ARF-QAFIX-006" && item.subject_type === "entity"
      );
      const host = document.getElementById("detail-screening-review");
      return !!row &&
        Array.isArray(row.screening_evidence?.items) &&
        row.screening_evidence.items.length > 0 &&
        !!host &&
        !host.querySelector("[data-screening-evidence-loading]");
    }, { timeout: 30000 });
    report.observations.populatedScreeningSemanticEvidence = await page.evaluate(() => {
      const row = (window.SCREENING_QUEUE?.rows || []).find(
        (item) => item.application_ref === "ARF-QAFIX-006" && item.subject_type === "entity"
      ) || {};
      const host = document.getElementById("detail-screening-review");
      const items = row.screening_evidence?.items || [];
      const rendered = String(host?.innerText || "").trim();
      const detailDispositionActions = host
        ? Array.from(host.querySelectorAll("button"))
          .map((button) => ({
            text: String(button.textContent || "").trim().replace(/\s+/g, " "),
            disabled: button.disabled === true,
            ariaDisabled: button.getAttribute("aria-disabled"),
            title: String(button.title || ""),
          }))
          .filter((action) => [
            "Clear as False Positive",
            "Confirm True Match",
            "Escalate",
            "Request More Information",
          ].includes(action.text))
        : [];
      return {
        applicationRef: row.application_ref || null,
        truthState: row.screening_truth_state || null,
        reviewDisposition: row.review_disposition || null,
        fourEyesStatus: row.review_four_eyes_status || null,
        totalHits: Number(row.total_hits || 0),
        evidenceItemCount: items.length,
        providerReferenceCount: new Set(
          items.flatMap((item) => [
            item.provider_alert_id,
            item.provider_case_id,
            item.provider_profile_id,
            item.provider_risk_id,
          ]).filter(Boolean)
        ).size,
        renderedHitCount: host
          ? host.querySelectorAll("[data-screening-triage-hit]").length
          : 0,
        hasLoadingOrErrorState: !!host && (
          /loading application detail|evidence could not be loaded|load error|service unavailable/i.test(rendered) ||
          !!host.querySelector("[data-screening-evidence-loading], [data-screening-evidence-error], [data-screening-evidence-outage]")
        ),
        detailDispositionActions,
      };
    });
    report.checks.populatedScreeningEvidenceRenders =
      report.observations.populatedScreeningSemanticEvidence.applicationRef ===
        "ARF-QAFIX-006" &&
      report.observations.populatedScreeningSemanticEvidence.totalHits > 0 &&
      report.observations.populatedScreeningSemanticEvidence.evidenceItemCount > 0 &&
      report.observations.populatedScreeningSemanticEvidence.providerReferenceCount > 0 &&
      report.observations.populatedScreeningSemanticEvidence.renderedHitCount > 0 &&
      report.observations.populatedScreeningSemanticEvidence.hasLoadingOrErrorState === false;
    report.checks.screeningDispositionControlsRespectRoleAndState =
      report.observations.populatedScreeningSemanticEvidence.fourEyesStatus ===
        "pending_second_review" &&
      report.observations.populatedScreeningSemanticEvidence
        .detailDispositionActions.length === 1 &&
      report.observations.populatedScreeningSemanticEvidence
        .detailDispositionActions[0].text === "Clear as False Positive" &&
      report.observations.populatedScreeningSemanticEvidence
        .detailDispositionActions[0].disabled === false &&
      report.observations.populatedScreeningSemanticEvidence
        .detailDispositionActions[0].ariaDisabled !== "true";
    await captureSurface(page, "screening-review-four-eyes", "Screening Review - Four Eyes");

    report.observations.riskRuntimeProjection = await page.evaluate(() => {
      const model = window.RUNTIME_RISK_MODEL || {};
      const source = model.runtime_source || {};
      return {
        loaded: Object.keys(model).length > 0,
        readOnly: model.read_only === true,
        activationFlag: source.activation_flag || null,
        activationEnabled: source.activation_enabled === true,
        configVersion: source.config_version || null,
        dimensions: Array.isArray(model.dimensions) ? model.dimensions.map((row) => row.id) : [],
      };
    });
    report.checks.riskModelBackendProjectionLoads =
      report.observations.riskRuntimeProjection.loaded === true &&
      report.observations.riskRuntimeProjection.readOnly === true &&
      JSON.stringify(report.observations.riskRuntimeProjection.dimensions) ===
        JSON.stringify(["D1", "D2", "D3", "D4", "D5"]);
    const authenticatedRole = await page.evaluate(() =>
      String(window.currentUser?.role || window.BO_AUTH_USER?.role || "").toLowerCase()
    );
    if (authenticatedRole === "admin") {
      await clickNav(page, "risk-model");
      report.checks.riskModelPageLoads = await visible(page, "#view-risk-model.active");
      report.checks.riskModelRemainsReadOnly = await page.evaluate(() => {
        const view = document.getElementById("view-risk-model");
        if (!view || !/read-only runtime model reference/i.test(view.innerText || "")) return false;
        return view.querySelectorAll("input, select, textarea, [contenteditable=true]").length === 0;
      });
      report.checks.riskModelUsesBackendProjection = await page.locator("#risk-runtime-source-container").evaluate(
        (el) => !/loading|unavailable/i.test(el.innerText || "") && String(el.innerText || "").trim().length > 0
      );
      await captureSurface(page, "risk-scoring-model", "Risk Scoring Model");
    } else {
      report.checks.riskModelAdminBoundaryEnforced =
        !(await visible(page, '.snav-item[onclick*="risk-model"]'));
      report.observations.riskModelPage = {
        status: "not_run",
        authenticatedRole,
        reason: "The approved staging QA credential is not an administrator; the backend projection and application evidence were validated, but the admin-only page cannot be opened.",
      };
      report.limitations.push(report.observations.riskModelPage.reason);
      }
    }

    await clickNav(page, "cases");
    report.checks.caseManagementLoads = await visible(page, "#view-cases.active");
    await captureSurface(page, "case-management", "Case Management");

    await clickNav(page, "monitoring");
    report.checks.ongoingMonitoringLoads = await visible(page, "#view-monitoring.active");
    report.checks.monitoringAlertsLoad = await visible(page, "#monitoring-alerts-tab");
    await captureSurface(page, "ongoing-monitoring-alerts", "RegMind Monitoring");
    await page.locator('#view-monitoring .tab:has-text("Pilot Scope")').click();
    await page.waitForFunction(() => document.getElementById("monitoring-agents-tab")?.style.display !== "none", {
      timeout: 30000,
    });
    report.checks.monitoringPilotScopeLoad = await visible(page, "#monitoring-agents-tab");
    await captureSurface(page, "ongoing-monitoring-pilot-scope", "Monitoring Pilot Scope");

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
    report.checks.screenshotsCaptured =
      report.screenshots.length >= (roleMatrixProfile ? 10 : 15);
    classifyConsoleErrors();
    report.checks.noBlockingConsoleErrors = report.blockingConsoleErrors.length === 0;
    report.checks.noPageErrors = report.pageErrors.length === 0;
    report.checks.noApi500Responses = report.badResponses.filter((entry) => entry.status >= 500).length === 0;
    report.checks.noUnexpectedBadApiResponses = report.unexpectedBadResponses.length === 0;
    report.checks.noFailedRequests = report.failedRequests.length === 0;
    report.checks.noRemovedProviderLabels = report.providerLabelFindings.length === 0;
    report.checks.noRawApplicationStatusTokenStatusSurfaces =
      report.applicationStatusTokenFindings.officerStatusSurfaces.length === 0;
    report.checks.noRawApplicationStatusTokenFixtureNames =
      report.applicationStatusTokenFindings.fixturePartyNames.length === 0;
    report.checks.noVisibleInternalApplicationStatusReasonCodes =
      report.applicationStatusTokenFindings.visibleInternalMachineCodes.length === 0;

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
