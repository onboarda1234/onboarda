#!/usr/bin/env node
/* Capture deterministic release-validation screenshots of the actual memo workspace. */

const fs = require("node:fs");
const http = require("node:http");
const path = require("node:path");
const { chromium } = require("/Users/Aisha/node_modules/playwright");

const backendRoot = path.resolve(__dirname, "../..");
const repoRoot = path.resolve(backendRoot, "..");
const outputDir = path.join(repoRoot, "docs", "validation", "compliance-memo");
const backofficeHtml = fs.readFileSync(path.join(repoRoot, "arie-backoffice.html"));
const chromePath = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";

const draftPdf = fs.readFileSync(path.join(outputDir, "compliance-memo-draft-sample.pdf")).toString("base64");
const finalPdf = fs.readFileSync(path.join(outputDir, "compliance-memo-final-sample.pdf")).toString("base64");

const rationale =
  "I have reviewed the generated memorandum and the underlying evidence. Verified ownership, clear screening results and the documented mitigants support conditional approval, with the recorded activation conditions and monitoring controls remaining proportionate to the residual risk.";

function memo(overrides = {}) {
  return {
    memo_id: "memo-validation-v2",
    memo_version: "v2",
    lifecycle_status: "AWAITING_OFFICER_SIGNOFF",
    review_status: "pending",
    validation_status: "pass",
    memo_generated: "2026-08-01T06:30:00+00:00",
    application_snapshot_timestamp: "2026-08-01T06:28:00+00:00",
    officer_rationale: "",
    sections: {
      compliance_decision: { decision: "APPROVE_WITH_CONDITIONS" },
    },
    metadata: {
      memo_id: "memo-validation-v2",
      memo_version: "v2",
      memo_generated_at: "2026-08-01T06:30:00+00:00",
      application_snapshot_timestamp: "2026-08-01T06:28:00+00:00",
      blocked: false,
    },
    ...overrides,
  };
}

function application(latestMemo, overrides = {}) {
  return {
    id: "release-validation-application",
    ref: "ARF-2026-VAL-001",
    inputsUpdatedAt: "2026-08-01T06:28:00+00:00",
    updated_at: "2026-08-01T06:28:00+00:00",
    latestMemo,
    memoIsStale: false,
    ...overrides,
  };
}

const history = [
  {
    memo_id: "memo-validation-v2",
    memo_version: "v2",
    lifecycle_status: "AWAITING_OFFICER_SIGNOFF",
    recommendation: "APPROVE_WITH_CONDITIONS",
    generated_at: "2026-08-01T06:30:00+00:00",
    reviewed_by: "Aisha Sudally",
    is_current: true,
  },
  {
    memo_id: "memo-validation-v1",
    memo_version: "v1",
    lifecycle_status: "SUPERSEDED",
    recommendation: "APPROVE_WITH_CONDITIONS",
    generated_at: "2026-07-31T18:25:16+00:00",
    approved_by: "Aisha Sudally",
    approved_at: "2026-07-31T18:27:10+00:00",
    is_current: false,
  },
];

async function preparePage(page, backofficeUrl) {
  await page.goto(backofficeUrl, { waitUntil: "domcontentloaded" });
  await page.evaluate(() => {
    const workspace = document.getElementById("compliance-memo-workspace");
    if (!workspace) throw new Error("Compliance Memo workspace not found");
    document.body.replaceChildren(workspace);
    document.body.style.margin = "0";
    document.body.style.padding = "18px";
    document.body.style.background = "#f4f6fa";
    workspace.style.width = "100%";
    workspace.style.maxWidth = "none";
    workspace.style.margin = "0";
    window.currentUserRole = () => "sco";
    window.canApproveMemo = () => true;
    window._memoWorkspaceAppId = null;
    window.boApiCall = async () => ({ data: { versions: [] } });
  });
}

async function installState(page, app, pdfBase64 = null, versions = []) {
  await page.evaluate(
    ({ app, pdfBase64, versions }) => {
      if (window._memoPdfCache && window._memoPdfCache.url) {
        URL.revokeObjectURL(window._memoPdfCache.url);
      }
      window._memoPdfCache = null;
      window._memoWorkspaceAppId = String(app.id || app.ref || "");
      window._currentDetailApp = app;
      window._currentMemoData = app.latestMemo || null;
      window.boApiCall = async () => ({ data: { versions } });

      if (pdfBase64 && app.latestMemo) {
        const bytes = Uint8Array.from(atob(pdfBase64), (char) => char.charCodeAt(0));
        const blob = new Blob([bytes], { type: "application/pdf" });
        const url = URL.createObjectURL(blob);
        window._memoPdfCache = {
          key: memoWorkspacePdfKey(app, app.latestMemo),
          url,
          blob,
          filename: "compliance-memo-validation.pdf",
          sha256: "release-validation",
          memoId: memoWorkspaceMemoId(app.latestMemo),
          memoVersion: memoWorkspaceVersion(app.latestMemo),
          lifecycle: memoWorkspaceLifecycle(app, app.latestMemo),
        };
      }

      const rationaleField = document.getElementById("memo-approval-reason");
      if (rationaleField) rationaleField.dataset.memoId = "";
      const signoff = document.getElementById("memo-officer-signoff");
      if (signoff) signoff.dataset.memoId = "";
      const historyPanel = document.getElementById("memo-version-history-panel");
      if (historyPanel) historyPanel.classList.remove("open");
      renderMemoWorkspace(app);
    },
    { app, pdfBase64, versions },
  );
  if (pdfBase64) await page.waitForTimeout(1800);
}

async function capture(page, filename) {
  const workspace = page.locator("#compliance-memo-workspace");
  await workspace.screenshot({ path: path.join(outputDir, filename), animations: "disabled" });
}

async function main() {
  fs.mkdirSync(outputDir, { recursive: true });
  const server = http.createServer((request, response) => {
    if (request.url === "/arie-backoffice.html") {
      response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
      response.end(backofficeHtml);
      return;
    }
    if (request.url === "/api/config/environment") {
      response.writeHead(200, { "Content-Type": "application/json" });
      response.end(JSON.stringify({ environment: "release_validation", is_production: false }));
      return;
    }
    response.writeHead(404, { "Content-Type": "text/plain" });
    response.end("Not found");
  });
  await new Promise((resolve, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolve);
  });
  const address = server.address();
  const backofficeUrl = `http://127.0.0.1:${address.port}/arie-backoffice.html`;
  const browser = await chromium.launch({
    headless: false,
    executablePath: chromePath,
    args: ["--disable-features=TranslateUI", "--no-first-run", "--no-default-browser-check"],
  });
  const context = await browser.newContext({ viewport: { width: 1440, height: 1100 }, deviceScaleFactor: 1 });
  const page = await context.newPage();
  const errors = [];
  page.on("pageerror", (error) => errors.push(`pageerror: ${error.message}`));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(`console: ${message.text()}`);
  });

  try {
    await preparePage(page, backofficeUrl);

    await installState(page, application(null));
    await capture(page, "01-not-generated.png");

    const draft = memo({ lifecycle_status: "DRAFT", validation_status: "pending", memo_version: "v1", memo_id: "memo-validation-v1" });
    await installState(page, application(draft), draftPdf, history.slice(1));
    await capture(page, "02-draft-generated.png");
    await capture(page, "03-pdf-preview.png");

    const awaiting = memo({ officer_rationale: rationale });
    await installState(page, application(awaiting), draftPdf, history);
    await capture(page, "04-officer-rationale.png");

    const finalMemo = memo({
      lifecycle_status: "FINAL",
      review_status: "approved",
      approved_at: "2026-08-01T06:35:00+00:00",
      officer_rationale: rationale,
    });
    await installState(page, application(finalMemo), finalPdf, history);
    await capture(page, "05-final-locked.png");

    await installState(page, application(awaiting), draftPdf, history);
    await page.evaluate(() => toggleMemoVersionHistory(true));
    await page.waitForTimeout(150);
    await capture(page, "06-version-history.png");

    const staleMemo = memo({ is_stale: true, stale_reason: "Application evidence changed after generation." });
    await installState(
      page,
      application(staleMemo, {
        memoIsStale: true,
        memoStaleReason: "Application evidence changed after generation.",
        inputsUpdatedAt: "2026-08-01T06:42:00+00:00",
      }),
      null,
      history,
    );
    await page.evaluate(() => toggleMemoVersionHistory(true));
    await page.waitForTimeout(150);
    await capture(page, "07-stale-state.png");

    await installState(page, application(awaiting), draftPdf, history);
    await capture(page, "08-desktop-responsive.png");

    const section = await page.locator("#compliance-memo-workspace").boundingBox();
    const frameVisible = await page.locator("#memo-pdf-preview").isVisible();
    const result = {
      viewport: "1440x1100",
      section,
      frameVisible,
      screenshots: [
        "01-not-generated.png",
        "02-draft-generated.png",
        "03-pdf-preview.png",
        "04-officer-rationale.png",
        "05-final-locked.png",
        "06-version-history.png",
        "07-stale-state.png",
        "08-desktop-responsive.png",
      ],
      pageErrors: errors.filter((value, index, all) => all.indexOf(value) === index),
    };
    process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
  } finally {
    await context.close();
    await browser.close();
    await new Promise((resolve) => server.close(resolve));
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
