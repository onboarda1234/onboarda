#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const ROOT = path.resolve(__dirname, "..");
const RUNTIME_DIR = path.join(ROOT, "runtime_json");
const BASE_URL = (process.env.STAGING_BASE_URL || "https://staging.regmind.co").replace(/\/+$/, "");
const API_BASE = `${BASE_URL}/api`;
const RUN_ID = process.env.RUN_ID || new Date().toISOString().replace(/[-:.]/g, "").slice(0, 15) + "Z";
const ORIGIN_MAIN_SHA = process.env.ORIGIN_MAIN_SHA || "";
const SUBMIT_MAX_ATTEMPTS = Number(process.env.SUBMIT_MAX_ATTEMPTS || 2);

const REQUIRED_ENV = [
  "STAGING_PORTAL_EMAIL",
  "STAGING_PORTAL_PASSWORD",
  "STAGING_BO_EMAIL",
  "STAGING_BO_PASSWORD",
  "ORIGIN_MAIN_SHA",
];

function ensureDirs() {
  fs.mkdirSync(RUNTIME_DIR, { recursive: true });
  fs.mkdirSync(path.join(ROOT, "screenshots"), { recursive: true });
}

function nowIso() {
  return new Date().toISOString();
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function writeJson(name, data) {
  const file = path.join(RUNTIME_DIR, name);
  fs.writeFileSync(file, JSON.stringify(data, null, 2));
  return file;
}

function writeText(name, data) {
  const file = path.join(ROOT, name);
  fs.writeFileSync(file, data.endsWith("\n") ? data : `${data}\n`);
  return file;
}

function flattenResponse(data) {
  if (data && typeof data === "object" && Object.prototype.hasOwnProperty.call(data, "data")) {
    return data.data;
  }
  return data;
}

function compact(value, max = 260) {
  const text = typeof value === "string" ? value : JSON.stringify(value || {});
  return text.length > max ? `${text.slice(0, max)}...` : text;
}

function md(value) {
  if (value === null || value === undefined || value === "") return "-";
  if (Array.isArray(value)) return value.length ? value.map(md).join(", ") : "-";
  if (typeof value === "object") return "`" + JSON.stringify(value).replace(/`/g, "'") + "`";
  return String(value).replace(/\|/g, "\\|").replace(/\n/g, " ");
}

function table(headers, rows) {
  const head = `| ${headers.join(" | ")} |`;
  const sep = `| ${headers.map(() => "---").join(" | ")} |`;
  const body = rows.map((row) => `| ${headers.map((h) => md(row[h])).join(" | ")} |`);
  return [head, sep, ...body].join("\n");
}

async function apiRaw(token, method, route, body, opts = {}) {
  const url = route.startsWith("http") ? route : `${API_BASE}${route}`;
  const headers = Object.assign({}, opts.headers || {});
  if (token) headers.Authorization = `Bearer ${token}`;
  let payload = body;
  if (body && typeof body === "object") {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const started = Date.now();
  const response = await fetch(url, { method, headers, body: payload });
  const duration_ms = Date.now() - started;
  const contentType = response.headers.get("content-type") || "";
  const data = contentType.includes("application/json")
    ? await response.json().catch(() => ({}))
    : await response.text().catch(() => "");
  return {
    ok: response.ok,
    status: response.status,
    duration_ms,
    data,
    headers: {
      content_type: contentType,
      retry_after: response.headers.get("retry-after") || "",
    },
  };
}

async function api(token, method, route, body, opts = {}) {
  const result = await apiRaw(token, method, route, body, opts);
  result.data = flattenResponse(result.data);
  if (!result.ok && !opts.allowFailure) {
    throw new Error(`${method} ${route} failed ${result.status}: ${compact(result.data, 600)}`);
  }
  return result;
}

async function login(pathname, email, password) {
  const result = await api(null, "POST", pathname, { email, password });
  const data = result.data;
  if (!data || !data.token) {
    throw new Error(`Login ${pathname} did not return a token`);
  }
  return { token: data.token, user: data.user || data.client || {}, raw: data };
}

async function submitPrescreeningWithRetry(token, appId) {
  const attempts = [];
  let lastResult = null;
  for (let attempt = 1; attempt <= SUBMIT_MAX_ATTEMPTS; attempt += 1) {
    const result = await api(token, "POST", `/applications/${encodeURIComponent(appId)}/submit`, {}, { allowFailure: true });
    attempts.push({
      attempt,
      ok: result.ok,
      status: result.status,
      duration_ms: result.duration_ms,
      retry_after: result.headers.retry_after,
      data: result.data,
    });
    lastResult = result;
    if (result.ok || attempt >= SUBMIT_MAX_ATTEMPTS) break;
    if (result.status === 429) {
      const retryAfter = Number(result.headers.retry_after || result.data?.retry_after || 0);
      await sleep(Math.max(1000, (retryAfter + 2) * 1000));
    } else if (result.status === 503) {
      await sleep(10000);
    } else {
      break;
    }
  }
  lastResult.attempts = attempts;
  return lastResult;
}

function smokePayload() {
  const suffix = crypto.randomBytes(3).toString("hex").toUpperCase();
  const company = `E2E ${RUN_ID} GATE Smoke Ltd`;
  const directorKey = "gate_dir_1";
  const uboKey = "gate_ubo_1";
  const director = {
    person_key: directorKey,
    first_name: "Gate",
    last_name: "Director",
    full_name: "Gate Director",
    date_of_birth: "1980-02-14",
    nationality: "United Kingdom",
    is_pep: "No",
  };
  const ubo = {
    person_key: uboKey,
    first_name: "Gate",
    last_name: "Owner",
    full_name: "Gate Owner",
    date_of_birth: "1981-03-15",
    nationality: "United Kingdom",
    is_pep: "No",
    ownership_pct: 100,
  };
  const prescreeningData = {
    registered_entity_name: company,
    trading_name: `${company} Trading`,
    incorporation_date: "2021-05-10",
    country_of_incorporation: "United Kingdom",
    jurisdiction_exposure_rationale: "Synthetic staging prescreening smoke for E2E-PILOT-READINESS-1.",
    registered_address: "1 Compliance Street, London, SW1A 1AA",
    headquarters_address: "1 Compliance Street, London, SW1A 1AA",
    entity_contact_first: "Gate",
    entity_contact_last: "Smoke",
    entity_contact_email: `e2e-gate-${RUN_ID.toLowerCase()}@example.test`,
    entity_contact_phone_code: "+44",
    entity_contact_mobile: "2070001234",
    website: "https://example.test",
    is_licensed: false,
    has_licence: false,
    regulatory_licences: "",
    licence_number: "",
    licence_authority: "",
    licence_type: "",
    authorised_share_capital: "100000",
    services_required: ["Company administration", "Compliance onboarding"],
    monthly_volume: "100000",
    expected_volume: "100000",
    volume_rationale_vs_business_size: "Expected activity is consistent with synthetic operating profile.",
    transaction_complexity: "Low complexity domestic and EEA flows.",
    countries_of_operation: ["United Kingdom"],
    business_overview: "Software and operational support services.",
    target_markets: ["United Kingdom"],
    account_purposes: ["Operating expenses", "Client receipts"],
    existing_bank_account: "Yes",
    existing_bank_name: "Synthetic Bank plc",
    currencies: ["USD"],
    currency: "USD",
    source_of_wealth_type: "business revenue",
    source_of_wealth_detail: "Retained earnings from ordinary trading activity.",
    source_of_funds_initial_type: "company bank",
    source_of_funds_initial_detail: "Company bank operating account.",
    source_of_funds_ongoing_type: "client payments",
    source_of_funds_ongoing_detail: "Payments under standard service contracts.",
    source_of_funds: "Initial: company bank; Ongoing: client payments",
    estimated_monthly_activity: {
      inflows: { transactions: 12, min_amount_usd: 1000, max_amount_usd: 25000, fx_amount_usd: 0 },
      outflows: { transactions: 8, min_amount_usd: 500, max_amount_usd: 15000, fx_amount_usd: 0 },
    },
    financial_forecast: {
      revenue: { year_1: 500000, year_2: 650000, year_3: 800000 },
      cost_of_sales: { year_1: 180000, year_2: 220000, year_3: 260000 },
      profit: { year_1: 90000, year_2: 130000, year_3: 180000 },
    },
    management_overview: "Managed by one director and one beneficial owner.",
    introduction_method: "Direct",
    referrer_name: "",
    consent_data_processing: true,
    consent_information_sharing: true,
    consent_data_retention: true,
    consent_ongoing_monitoring: true,
    consent_marketing: false,
    consent_declaration: true,
    directors: [director],
    ubos: [ubo],
    intermediaries: [],
  };
  return {
    company_name: company,
    entity_name: company,
    brn: `E2EGATE-${suffix}`,
    country: "United Kingdom",
    sector: "Technology",
    entity_type: "Private Limited Company",
    ownership_structure: "simple 1-2 direct individual owners",
    prescreening_data: prescreeningData,
    directors: [director],
    ubos: [ubo],
    intermediaries: [],
  };
}

function writeBlockedClosure(reason, details) {
  writeText("closure_report.md", [
    "# E2E-PILOT-READINESS-1 Closure Report",
    "",
    reason,
    "",
    details,
  ].join("\n"));
}

function writeVersionReports(version, sourceOk) {
  writeText("source_of_truth_gate.md", [
    "# Source Of Truth Gate",
    "",
    `Latest origin/main SHA: \`${ORIGIN_MAIN_SHA}\``,
    `SHA selected for testing: \`${ORIGIN_MAIN_SHA}\``,
    "",
    `Gate result: ${sourceOk ? "PASS" : "BLOCKED"}`,
  ].join("\n"));

  writeText("deployment_alignment.md", [
    "# Deployment Alignment",
    "",
    table(["Field", "Expected", "Actual", "Result"], [
      { Field: "git_sha", Expected: ORIGIN_MAIN_SHA, Actual: version.git_sha, Result: version.git_sha === ORIGIN_MAIN_SHA ? "PASS" : "FAIL" },
      { Field: "image_tag", Expected: ORIGIN_MAIN_SHA, Actual: version.image_tag, Result: version.image_tag === ORIGIN_MAIN_SHA ? "PASS" : "FAIL" },
      { Field: "environment", Expected: "staging", Actual: version.environment, Result: version.environment === "staging" ? "PASS" : "WARN" },
      { Field: "build_time", Expected: "current deployment", Actual: version.build_time, Result: version.build_time ? "RECORDED" : "MISSING" },
    ]),
  ].join("\n"));
}

function providerGate(providerStatus) {
  const truth = providerStatus.provider_truth || {};
  const ca = providerStatus.complyadvantage || {};
  const sumsub = providerStatus.sumsub || {};
  const opencorporates = providerStatus.opencorporates || {};
  const operatorSandboxConfirmed = process.env.CA_SANDBOX_OPERATOR_CONFIRMED === "1";
  const caRuntimeActive = (
    ca.active === true &&
    ca.configured === true &&
    ca.status === "live" &&
    truth.active_aml_screening_provider_key === "complyadvantage" &&
    truth.requested_screening_provider === "complyadvantage" &&
    truth.screening_abstraction_enabled === true &&
    truth.simulation_fallback_enabled === false &&
    truth.simulation_fallback_mode === "disabled"
  );
  const ok = caRuntimeActive && operatorSandboxConfirmed;
  return {
    ok,
    ca_runtime_active: caRuntimeActive,
    operator_sandbox_confirmed: operatorSandboxConfirmed,
    api_exposes_workspace_identifier: JSON.stringify(providerStatus).toLowerCase().includes("sandbox"),
    active_aml: truth.active_aml_screening_provider || "",
    active_aml_key: truth.active_aml_screening_provider_key || "",
    requested_provider: truth.requested_screening_provider || ca.requested_provider || "",
    ca_status: ca.status || "",
    ca_fallback_mode: ca.fallback_mode || truth.simulation_fallback_mode || "",
    ca_simulation_fallback_enabled: ca.simulation_fallback_enabled ?? truth.simulation_fallback_enabled,
    sumsub_status: sumsub.status || "",
    sumsub_configured: sumsub.configured,
    opencorporates_status: opencorporates.status || truth.registry_kyb_status || "",
    opencorporates_configured: opencorporates.configured,
  };
}

function writeProviderReport(providerStatus, gate) {
  writeText("provider_status.md", [
    "# Provider Status",
    "",
    `Gate result: ${gate.ok ? "PASS" : "BLOCKED"}`,
    "",
    table(["Check", "Value"], [
      { Check: "ComplyAdvantage active AML provider", Value: `${gate.active_aml} (${gate.active_aml_key})` },
      { Check: "ComplyAdvantage runtime active/configured", Value: gate.ca_runtime_active },
      { Check: "ComplyAdvantage status", Value: gate.ca_status },
      { Check: "Fallback/simulation mode", Value: `${gate.ca_fallback_mode}; enabled=${gate.ca_simulation_fallback_enabled}` },
      { Check: "CA Sandbox operator confirmation", Value: gate.operator_sandbox_confirmed ? "Confirmed by operator in workstream instruction" : "Not confirmed" },
      { Check: "Workspace identifier exposed by API", Value: gate.api_exposes_workspace_identifier },
      { Check: "Sumsub IDV", Value: `${gate.sumsub_status}; configured=${gate.sumsub_configured}` },
      { Check: "OpenCorporates / registry enrichment", Value: `${gate.opencorporates_status}; configured=${gate.opencorporates_configured}` },
    ]),
    "",
    "Note: `/api/screening/status` does not expose the ComplyAdvantage workspace identifier or credential hostname. Sandbox mode is therefore recorded from operator confirmation plus runtime evidence that staging uses ComplyAdvantage Mesh with fallback disabled. The prescreening smoke is the runtime check that the configured staging provider path does not fail with the prior Production-workspace 503.",
    "",
    "Raw provider status is stored in `runtime_json/screening_status_gate.json`.",
  ].join("\n"));
}

async function runSmoke(portalToken, boToken, providerBefore) {
  const record = {
    run_id: RUN_ID,
    started_at: nowIso(),
    payload: smokePayload(),
    provider_status_before_submit: providerBefore,
  };
  record.create = await api(portalToken, "POST", "/applications", record.payload, { allowFailure: true });
  if (record.create.ok) {
    record.application_id = record.create.data.id;
    record.application_ref = record.create.data.ref;
    record.detail_after_create = (await api(portalToken, "GET", `/applications/${encodeURIComponent(record.application_ref)}`, null, { allowFailure: true })).data;
    record.submit_prescreening = await submitPrescreeningWithRetry(portalToken, record.application_id);
    record.detail_after_submit = (await api(portalToken, "GET", `/applications/${encodeURIComponent(record.application_ref)}`, null, { allowFailure: true })).data;
    record.backoffice_list_lookup = await api(boToken, "GET", `/applications?view=list&limit=20&offset=0&q=${encodeURIComponent(record.application_ref)}`, null, { allowFailure: true });
    record.backoffice_detail = await api(boToken, "GET", `/applications/${encodeURIComponent(record.application_ref)}?include_history=true`, null, { allowFailure: true });
    record.provider_status_after_submit = (await api(boToken, "GET", "/screening/status", null, { allowFailure: true })).data;
  }
  record.finished_at = nowIso();
  const submitStatus = Number(record.submit_prescreening?.status || 0);
  const rateLimited = (record.submit_prescreening?.attempts || []).some((attempt) => Number(attempt.status) === 429);
  record.ok = Boolean(
    record.create?.ok &&
    record.submit_prescreening?.ok &&
    submitStatus !== 503 &&
    !rateLimited &&
    record.backoffice_detail?.ok
  );
  record.failure_reason = record.ok ? "" : compact({
    create_status: record.create?.status,
    submit_status: record.submit_prescreening?.status,
    submit_attempts: record.submit_prescreening?.attempts,
    backoffice_detail_status: record.backoffice_detail?.status,
  }, 800);
  return record;
}

function writeSmokeReport(record) {
  writeText("prescreening_smoke.md", [
    "# Prescreening Smoke",
    "",
    `Gate result: ${record.ok ? "PASS" : "BLOCKED"}`,
    "",
    table(["Field", "Value"], [
      { Field: "Application", Value: record.payload.company_name },
      { Field: "Reference", Value: record.application_ref || "-" },
      { Field: "Portal create status", Value: record.create?.status },
      { Field: "Prescreening submit status", Value: record.submit_prescreening?.status },
      { Field: "Submit attempts", Value: (record.submit_prescreening?.attempts || []).map((a) => `${a.attempt}:${a.status}`).join(", ") || "-" },
      { Field: "Back-office detail visible", Value: Boolean(record.backoffice_detail?.ok) },
      { Field: "Provider before submit", Value: (record.provider_status_before_submit?.provider_truth || {}).active_aml_screening_provider || "-" },
      { Field: "Provider after submit", Value: (record.provider_status_after_submit?.provider_truth || {}).active_aml_screening_provider || "-" },
    ]),
    "",
    record.ok ? "Prescreening submit passed without 503 or rate-limit failure." : `Failure detail: ${record.failure_reason}`,
  ].join("\n"));
}

async function main() {
  ensureDirs();
  const missing = REQUIRED_ENV.filter((name) => !process.env[name]);
  if (missing.length) {
    throw new Error("BLOCKED - missing runtime input: " + missing.join(", "));
  }

  const portalLogin = await login("/auth/client/login", process.env.STAGING_PORTAL_EMAIL, process.env.STAGING_PORTAL_PASSWORD);
  const boLogin = await login("/auth/officer/login", process.env.STAGING_BO_EMAIL, process.env.STAGING_BO_PASSWORD);

  const version = (await api(boLogin.token, "GET", "/version")).data;
  writeJson("api_version_gate.json", version);
  const sourceOk = version.git_sha === ORIGIN_MAIN_SHA && version.image_tag === ORIGIN_MAIN_SHA;
  writeVersionReports(version, sourceOk);
  if (!sourceOk) {
    writeBlockedClosure("BLOCKED - VERSION MISMATCH", `origin/main: ${ORIGIN_MAIN_SHA}\nstaging git_sha: ${version.git_sha}\nstaging image_tag: ${version.image_tag}`);
    throw new Error(`Version mismatch: origin/main ${ORIGIN_MAIN_SHA}; staging git_sha=${version.git_sha}; image_tag=${version.image_tag}`);
  }

  const providerStatus = (await api(boLogin.token, "GET", "/screening/status")).data;
  writeJson("screening_status_gate.json", providerStatus);
  const pg = providerGate(providerStatus);
  writeJson("provider_gate_result.json", pg);
  writeProviderReport(providerStatus, pg);
  if (!pg.ok) {
    writeBlockedClosure("BLOCKED - SCREENING PROVIDER MODE NOT CONFIRMED", compact(pg, 1000));
    throw new Error(`Provider mode not confirmed: ${compact(pg, 1000)}`);
  }

  const smoke = await runSmoke(portalLogin.token, boLogin.token, providerStatus);
  writeJson("prescreening_smoke_record.json", smoke);
  writeSmokeReport(smoke);
  if (!smoke.ok) {
    writeBlockedClosure("BLOCKED - PRESCREENING SMOKE FAILED", smoke.failure_reason);
    throw new Error(`Prescreening smoke failed: ${smoke.failure_reason}`);
  }

  writeJson("gates_passed.json", {
    run_id: RUN_ID,
    origin_main_sha: ORIGIN_MAIN_SHA,
    version,
    provider_gate: pg,
    prescreening_smoke_ref: smoke.application_ref,
    passed_at: nowIso(),
  });
  console.log(`Gates passed for ${ORIGIN_MAIN_SHA}; smoke ref ${smoke.application_ref}`);
}

main().catch((err) => {
  ensureDirs();
  writeJson("gate_runner_failure.json", {
    error: err.message,
    stack: err.stack,
    time: nowIso(),
  });
  console.error(err.stack || err.message);
  process.exit(1);
});
