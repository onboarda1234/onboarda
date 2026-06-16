#!/usr/bin/env node
"use strict";

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const ROOT = path.resolve(__dirname, "..");
const RUNTIME_DIR = path.join(ROOT, "runtime_json");
const SCREENSHOT_DIR = path.join(ROOT, "screenshots");
const BASE_URL = (process.env.STAGING_BASE_URL || "https://staging.regmind.co").replace(/\/+$/, "");
const API_BASE = `${BASE_URL}/api`;
const RUN_ID = process.env.RUN_ID || new Date().toISOString().replace(/[-:.]/g, "").slice(0, 15) + "Z";
const ORIGIN_MAIN_SHA = process.env.ORIGIN_MAIN_SHA || "";
const UPLOAD_DELAY_MS = Number(process.env.UPLOAD_DELAY_MS || 2300);
const SUBMIT_DELAY_MS = Number(process.env.SUBMIT_DELAY_MS || 13000);
const SUBMIT_MAX_ATTEMPTS = Number(process.env.SUBMIT_MAX_ATTEMPTS || 2);
const VERIFY_WAIT_MS = Number(process.env.VERIFY_WAIT_MS || 90000);
const HEADLESS = String(process.env.HEADLESS || "true").toLowerCase() !== "false";

const REQUIRED_ENV = [
  "STAGING_PORTAL_EMAIL",
  "STAGING_PORTAL_PASSWORD",
  "STAGING_BO_EMAIL",
  "STAGING_BO_PASSWORD",
  "ORIGIN_MAIN_SHA",
];

const BASE_DOCS = [
  ["cert_inc", "Certificate of Incorporation"],
  ["memarts", "Memorandum and Articles"],
  ["reg_sh", "Register of Shareholders"],
  ["reg_dir", "Register of Directors"],
  ["fin_stmt", "Financial Statements"],
  ["poa", "Proof of Registered Address"],
  ["board_res", "Board Resolution"],
  ["structure_chart", "Ownership Structure Chart"],
];

const SECTION_LABELS = {
  A: "A - Entity & Corporate Documents",
  B: "B - Directors & UBO Identity Documents",
  C: "C - Enhanced Evidence Documents",
  D: "D - Screening / Adverse Media Evidence",
  E: "E - Memo / Pre-Approval Evidence",
  F: "F - Approval Gate",
  G: "G - Audit / Activity Trail",
};

function ensureDirs() {
  fs.mkdirSync(RUNTIME_DIR, { recursive: true });
  fs.mkdirSync(SCREENSHOT_DIR, { recursive: true });
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

function redact(value) {
  if (!value) return "";
  const text = String(value);
  const at = text.indexOf("@");
  if (at <= 1) return "<redacted>";
  return `${text.slice(0, 2)}***${text.slice(at)}`;
}

function md(value) {
  if (value === null || value === undefined || value === "") return "-";
  if (Array.isArray(value)) return value.length ? value.map(md).join(", ") : "-";
  if (typeof value === "object") return "`" + JSON.stringify(value).replace(/`/g, "'") + "`";
  return String(value).replace(/\|/g, "\\|").replace(/\n/g, " ");
}

function compact(value, max = 220) {
  const text = typeof value === "string" ? value : JSON.stringify(value || {});
  return text.length > max ? `${text.slice(0, max)}...` : text;
}

function safeName(value) {
  return String(value || "file").replace(/[^a-zA-Z0-9._-]+/g, "_").slice(0, 180);
}

function nowIso() {
  return new Date().toISOString();
}

function randomBrn(prefix) {
  return `${prefix}-${crypto.randomBytes(3).toString("hex").toUpperCase()}`;
}

function pdfBuffer(title, lines = []) {
  const allLines = [title, ...lines].map((line) => String(line).replace(/[()\\]/g, " "));
  const content = [
    "BT",
    "/F1 16 Tf",
    "72 740 Td",
    `(${allLines[0] || "Synthetic staging document"}) Tj`,
    "/F1 10 Tf",
    ...allLines.slice(1).flatMap((line) => ["0 -18 Td", `(${line}) Tj`]),
    "ET",
  ].join("\n");
  const objects = [
    "1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n",
    "2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n",
    "3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >> endobj\n",
    "4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj\n",
    `5 0 obj << /Length ${Buffer.byteLength(content)} >> stream\n${content}\nendstream endobj\n`,
  ];
  let pdf = "%PDF-1.4\n";
  const offsets = [0];
  for (const obj of objects) {
    offsets.push(Buffer.byteLength(pdf));
    pdf += obj;
  }
  const xref = Buffer.byteLength(pdf);
  pdf += `xref\n0 ${objects.length + 1}\n0000000000 65535 f \n`;
  for (let i = 1; i <= objects.length; i += 1) {
    pdf += `${String(offsets[i]).padStart(10, "0")} 00000 n \n`;
  }
  pdf += `trailer << /Size ${objects.length + 1} /Root 1 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  return Buffer.from(pdf, "utf8");
}

function writeSyntheticPdf(prefix, title, lines = []) {
  const file = path.join(RUNTIME_DIR, `${safeName(prefix)}.pdf`);
  fs.writeFileSync(file, pdfBuffer(title, lines));
  return file;
}

function flattenResponse(data) {
  if (data && typeof data === "object" && Object.prototype.hasOwnProperty.call(data, "data")) {
    return data.data;
  }
  return data;
}

async function apiRaw(token, method, route, body, opts = {}) {
  const url = route.startsWith("http") ? route : `${API_BASE}${route}`;
  const headers = Object.assign({}, opts.headers || {});
  if (token) headers.Authorization = `Bearer ${token}`;
  let payload = body;
  if (body && !(body instanceof FormData) && typeof body === "object") {
    headers["Content-Type"] = "application/json";
    payload = JSON.stringify(body);
  }
  const started = Date.now();
  const response = await fetch(url, { method, headers, body: payload, redirect: "manual" });
  const duration_ms = Date.now() - started;
  const contentType = response.headers.get("content-type") || "";
  let data;
  if (contentType.includes("application/json")) {
    data = await response.json().catch(() => ({}));
  } else {
    data = await response.text().catch(() => "");
  }
  return {
    ok: response.ok,
    status: response.status,
    duration_ms,
    data,
    headers: {
      content_type: contentType,
      location: response.headers.get("location") || "",
      retry_after: response.headers.get("retry-after") || "",
    },
  };
}

async function api(token, method, route, body, opts = {}) {
  const result = await apiRaw(token, method, route, body, opts);
  if (!result.ok && !opts.allowFailure) {
    throw new Error(`${method} ${route} failed ${result.status}: ${compact(result.data, 500)}`);
  }
  return result;
}

async function login(pathname, email, password) {
  const result = await api(null, "POST", pathname, { email, password });
  const data = flattenResponse(result.data);
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
    result.data = flattenResponse(result.data);
    attempts.push({
      attempt,
      ok: result.ok,
      status: result.status,
      duration_ms: result.duration_ms,
      retry_after: result.headers?.retry_after || "",
      data: result.data,
    });
    lastResult = result;
    if (result.ok || attempt >= SUBMIT_MAX_ATTEMPTS) break;

    if (result.status === 429) {
      const retryAfter = Number(result.headers?.retry_after || result.data?.retry_after || 0);
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

function partyKey(prefix, scenarioNumber, index) {
  return `${prefix}${scenarioNumber}_${index}`;
}

function person(first, last, nationality, key, extras = {}) {
  return Object.assign({
    person_key: key,
    first_name: first,
    last_name: last,
    full_name: `${first} ${last}`,
    date_of_birth: extras.date_of_birth || "1980-02-14",
    nationality,
    is_pep: "No",
  }, extras);
}

function pepDeclaration(key) {
  return {
    person_key: key,
    declared_pep: true,
    client_declared_pep: true,
    pep_status: "declared_yes",
    pep_role_type: "foreign_pep",
    role_type: "foreign_pep",
    position_title: "Former Deputy Minister of Digital Services",
    public_function: "Senior public decision-making role in a foreign administration",
    pep_country_jurisdiction: "United Kingdom",
    country_jurisdiction: "United Kingdom",
    relationship_type: "self",
    start_date: "2017-01-01",
    end_date: "2022-12-31",
    current_status: false,
    source_of_wealth_detail: "Business revenue and disclosed investment returns.",
    source_of_funds_detail: "Company bank account funded by commercial revenues.",
    supporting_note_evidence: "Synthetic PEP declaration evidence for staging audit.",
    notes: "E2E-PILOT-READINESS-1 synthetic PEP scenario.",
  };
}

function basePrescreening(company, overrides = {}) {
  const country = overrides.country || "United Kingdom";
  const sector = overrides.sector || "Technology";
  return Object.assign({
    registered_entity_name: company,
    trading_name: company.replace(/\bLtd\b/i, " Trading"),
    incorporation_date: "2021-05-10",
    country_of_incorporation: country,
    jurisdiction_exposure_rationale: "Synthetic staging application for portal-to-back-office validation.",
    registered_address: "1 Compliance Street, London, SW1A 1AA",
    headquarters_address: "1 Compliance Street, London, SW1A 1AA",
    entity_contact_first: "Aisha",
    entity_contact_last: "Audit",
    entity_contact_email: `e2e-${RUN_ID.toLowerCase()}@example.test`,
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
    transaction_complexity: overrides.transaction_complexity || "Low complexity domestic and EEA flows.",
    countries_of_operation: overrides.countries_of_operation || [country],
    business_overview: overrides.business_overview || "Software and operational support services.",
    target_markets: overrides.target_markets || ["United Kingdom", "European Union"],
    account_purposes: ["Operating expenses", "Client receipts"],
    existing_bank_account: "Yes",
    existing_bank_name: "Synthetic Bank plc",
    currencies: ["USD"],
    currency: "USD",
    source_of_wealth_type: overrides.source_of_wealth_type || "business revenue",
    source_of_wealth_detail: overrides.source_of_wealth_detail || "Retained earnings from ordinary trading activity.",
    source_of_funds_initial_type: overrides.source_of_funds_initial_type || "company bank",
    source_of_funds_initial_detail: overrides.source_of_funds_initial_detail || "Company bank operating account.",
    source_of_funds_ongoing_type: overrides.source_of_funds_ongoing_type || "client payments",
    source_of_funds_ongoing_detail: overrides.source_of_funds_ongoing_detail || "Payments under standard service contracts.",
    source_of_funds: overrides.source_of_funds || "Initial: company bank; Ongoing: client payments",
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
    introduction_method: overrides.introduction_method || "Direct",
    referrer_name: overrides.referrer_name || "",
    consent_data_processing: true,
    consent_information_sharing: true,
    consent_data_retention: true,
    consent_ongoing_monitoring: true,
    consent_marketing: false,
    consent_declaration: true,
  }, overrides.prescreening_data || {});
}

function scenarioPayload(scenario) {
  const n = String(scenario.number).padStart(2, "0");
  const company = `E2E ${RUN_ID} S${n} ${scenario.shortName} Ltd`;
  const directorKey = partyKey("dir", n, 1);
  const uboKey = partyKey("ubo", n, 1);
  const directors = [person(`Director${n}`, "One", scenario.directorNationality || "United Kingdom", directorKey)];
  const ubos = [Object.assign(person(`Owner${n}`, "One", scenario.uboNationality || "United Kingdom", uboKey), { ownership_pct: 100 })];
  const intermediaries = [];

  if (scenario.number === 5) {
    directors[0].is_pep = "Yes";
    directors[0].pep_declaration = pepDeclaration(directorKey);
  }
  if (scenario.number === 8) {
    ubos.splice(0, ubos.length,
      Object.assign(person(`Owner${n}`, "Alpha", "United Kingdom", partyKey("ubo", n, 1)), { ownership_pct: 45 }),
      Object.assign(person(`Owner${n}`, "Beta", "Singapore", partyKey("ubo", n, 2)), { ownership_pct: 35 }),
      Object.assign(person(`Owner${n}`, "Gamma", "Canada", partyKey("ubo", n, 3)), { ownership_pct: 20 }),
    );
  }
  if (scenario.number === 9) {
    intermediaries.push({
      person_key: partyKey("int", n, 1),
      entity_name: `E2E ${RUN_ID} S${n} Introducer Pte Ltd`,
      jurisdiction: "Singapore",
      ownership_pct: 0,
    });
  }

  const country = scenario.country || "United Kingdom";
  const sector = scenario.sector || "Technology";
  const ownership = scenario.ownership || (scenario.number === 8 ? "complex 3+ layered ownership with multiple UBOs" : "simple 1-2 direct individual owners");
  const ps = basePrescreening(company, {
    country,
    sector,
    countries_of_operation: scenario.countries_of_operation || [country],
    target_markets: scenario.target_markets || [country],
    transaction_complexity: scenario.transaction_complexity,
    business_overview: scenario.business_overview,
    source_of_wealth_type: scenario.source_of_wealth_type,
    source_of_funds: scenario.source_of_funds,
    introduction_method: scenario.introduction_method,
    referrer_name: scenario.referrer_name,
  });
  ps.directors = directors;
  ps.ubos = ubos;
  ps.intermediaries = intermediaries;
  return {
    company_name: company,
    entity_name: company,
    brn: randomBrn(`E2E${n}`),
    country,
    sector,
    entity_type: scenario.entity_type || "Private Limited Company",
    ownership_structure: ownership,
    prescreening_data: ps,
    directors,
    ubos,
    intermediaries,
  };
}

const SCENARIOS = [
  {
    number: 1,
    name: "Clean low-risk standard company",
    shortName: "Clean Low Risk",
    purpose: "Baseline happy path.",
    expected: "Low/normal risk, no unnecessary EDD, correct portal-slot document mapping, approval blocked only by unresolved screening/memo gates.",
    omitDocs: [],
    expectRisk: "LOW or MEDIUM",
    expectApprovalBlocked: true,
  },
  {
    number: 2,
    name: "Missing required corporate document",
    shortName: "Missing Corporate Doc",
    purpose: "Test document request/blocker logic.",
    expected: "Back office shows missing Register of Directors, approval/pre-approval blocked, no false complete state.",
    omitDocs: ["reg_dir"],
    expectRisk: "LOW or MEDIUM",
    expectApprovalBlocked: true,
  },
  {
    number: 3,
    name: "Expired or stale document",
    shortName: "Expired Stale Doc",
    purpose: "Test freshness rules.",
    expected: "Stale/expired evidence is detected or marked review-required; approval blocked unless accepted.",
    staleDocs: ["poa"],
    expectRisk: "LOW or MEDIUM",
    expectApprovalBlocked: true,
  },
  {
    number: 4,
    name: "Director/UBO person KYC issue",
    shortName: "Person KYC Issue",
    purpose: "Test person document verification.",
    expected: "Person-level section shows missing/problem document; Sumsub IDV separate from Agent 1.",
    omitPersonDocs: [{ person_type: "ubo", person_key: "ubo04_1", doc_type: "poa" }],
    expectRisk: "LOW or MEDIUM",
    expectApprovalBlocked: true,
  },
  {
    number: 5,
    name: "PEP declared",
    shortName: "PEP Declared",
    purpose: "Test PEP declaration routing and enhanced requirement trigger.",
    expected: "Risk increases; EDD/enhanced requirements generated if configured; Agent 1 does not claim sanctions/PEP screening ownership.",
    expectRisk: "Elevated versus clean baseline",
    expectEDD: true,
    expectApprovalBlocked: true,
  },
  {
    number: 6,
    name: "High-risk jurisdiction / country-risk scenario",
    shortName: "Country Risk",
    purpose: "Test jurisdiction risk scoring and EDD routing.",
    expected: "Country-risk setting reflected; no silent low-risk fallback for unknown/current manual-source country risk.",
    country: "Cayman Islands",
    directorNationality: "Cayman Islands",
    uboNationality: "Cayman Islands",
    countries_of_operation: ["Cayman Islands", "United Kingdom"],
    target_markets: ["Cayman Islands", "United Kingdom"],
    expectRisk: "At least MEDIUM if country risk is active",
    expectEDD: true,
    expectApprovalBlocked: true,
  },
  {
    number: 7,
    name: "High-risk business activity / regulated activity",
    shortName: "High Risk Activity",
    purpose: "Test enhanced document requests from activity/risk settings.",
    expected: "Enhanced requirement requests generated for regulated/high-risk activity; approval blocked until resolved.",
    sector: "Cryptocurrency",
    business_overview: "Virtual asset services and digital asset treasury operations for synthetic staging testing.",
    transaction_complexity: "High cross-border virtual asset transaction complexity.",
    source_of_funds: "Initial: shareholder capital injection; Ongoing: crypto platform fees",
    expectRisk: "HIGH or VERY_HIGH",
    expectEDD: true,
    expectApprovalBlocked: true,
  },
  {
    number: 8,
    name: "Complex ownership / multiple UBOs",
    shortName: "Complex Ownership",
    purpose: "Test ownership/risk logic.",
    expected: "Multiple UBOs display without duplication; ownership evidence completeness/inconsistency affects gate.",
    ownership: "complex 3+ layered ownership with trust and multiple UBOs",
    expectRisk: "At least MEDIUM",
    expectApprovalBlocked: true,
  },
  {
    number: 9,
    name: "Intermediary / introducer involved",
    shortName: "Intermediary",
    purpose: "Test intermediary scope.",
    expected: "Back office shows intermediary context and requirements without leakage into client/director/UBO slots.",
    introduction_method: "Introducer",
    referrer_name: "Synthetic Introducer Pte Ltd",
    expectRisk: "At least LOW",
    expectApprovalBlocked: true,
  },
  {
    number: 10,
    name: "Manual acceptance / override path",
    shortName: "Manual Acceptance",
    purpose: "Test officer decision controls.",
    expected: "Accept with reason is enforced and audited; approval gate updates only if policy allows.",
    manualAccept: true,
    staleDocs: ["poa"],
    expectRisk: "LOW or MEDIUM",
    expectApprovalBlocked: true,
  },
];

function expectedUploadsForScenario(record) {
  const payload = record.payload;
  const uploads = [];
  const omitted = new Set(record.scenario.omitDocs || []);
  for (const [doc_type, label] of BASE_DOCS) {
    if (omitted.has(doc_type)) continue;
    uploads.push({ doc_type, label, scope: "entity" });
  }
  for (const personType of ["director", "ubo"]) {
    const people = personType === "director" ? payload.directors : payload.ubos;
    for (const p of people) {
      for (const doc_type of ["passport", "poa"]) {
        const shouldOmit = (record.scenario.omitPersonDocs || []).some((item) => (
          item.person_type === personType &&
          item.person_key === p.person_key &&
          item.doc_type === doc_type
        ));
        if (shouldOmit) continue;
        uploads.push({
          doc_type,
          label: `${doc_type === "passport" ? "Passport" : "Proof of Address"} for ${p.full_name}`,
          person_id: p.person_key,
          person_type: personType,
          scope: personType,
        });
      }
    }
  }
  for (const intermediary of payload.intermediaries || []) {
    for (const [doc_type, label] of [
      ["cert_inc", "Intermediary Certificate of Incorporation"],
      ["reg_dir", "Intermediary Register of Directors"],
      ["reg_sh", "Intermediary Register of Shareholders"],
      ["cert_gs", "Intermediary Certificate of Good Standing"],
      ["fin_stmt", "Intermediary Financial Statements"],
    ]) {
      uploads.push({
        doc_type,
        label: `${label} for ${intermediary.entity_name}`,
        person_id: intermediary.person_key,
        person_type: "intermediary",
        scope: "intermediary",
      });
    }
  }
  return uploads;
}

async function uploadPdf(token, appId, upload, record, source = "portal") {
  const stale = (record.scenario.staleDocs || []).includes(upload.doc_type) && upload.scope === "entity";
  const title = `${record.scenario.name} - ${upload.label}`;
  const lines = [
    `Run: ${RUN_ID}`,
    `Application: ${record.application_ref || record.application_id || ""}`,
    `Expected doc type: ${upload.doc_type}`,
    `Scope: ${upload.scope || "entity"}`,
    stale ? "Document date: 2020-01-01. Valid until: 2020-12-31. Intentionally stale synthetic evidence." : "Current synthetic staging evidence.",
    "Synthetic test document. No real customer data.",
  ];
  const file = writeSyntheticPdf(`${record.scenario.number}_${upload.doc_type}_${upload.person_id || "entity"}_${source}`, title, lines);
  const form = new FormData();
  form.append("file", new Blob([fs.readFileSync(file)], { type: "application/pdf" }), path.basename(file));
  form.append("doc_type", upload.doc_type);
  const params = new URLSearchParams({ doc_type: upload.doc_type });
  if (upload.person_id) params.set("person_id", upload.person_id);
  if (upload.person_type) params.set("person_type", upload.person_type);
  const result = await api(token, "POST", `/applications/${encodeURIComponent(appId)}/documents?${params.toString()}`, form, { allowFailure: true });
  return {
    source,
    request: upload,
    file,
    ok: result.ok,
    status: result.status,
    duration_ms: result.duration_ms,
    response: flattenResponse(result.data),
  };
}

async function uploadEnhancedRequirement(token, appId, requirement, record) {
  const title = `${record.scenario.name} - Enhanced requirement ${requirement.label || requirement.requirement_label || requirement.id}`;
  const file = writeSyntheticPdf(`${record.scenario.number}_enhanced_${requirement.id}`, title, [
    `Run: ${RUN_ID}`,
    `Requirement: ${requirement.label || requirement.requirement_label || requirement.requirement_key || requirement.id}`,
    "Synthetic enhanced evidence for staging portal fulfilment test.",
  ]);
  const form = new FormData();
  form.append("file", new Blob([fs.readFileSync(file)], { type: "application/pdf" }), path.basename(file));
  const result = await api(
    token,
    "POST",
    `/portal/applications/${encodeURIComponent(appId)}/enhanced-requirements/${encodeURIComponent(requirement.id)}/upload`,
    form,
    { allowFailure: true },
  );
  return {
    requirement_id: requirement.id,
    requirement_label: requirement.label || requirement.requirement_label || "",
    file,
    ok: result.ok,
    status: result.status,
    duration_ms: result.duration_ms,
    response: flattenResponse(result.data),
  };
}

async function pollVerification(token, docIds, maxWaitMs) {
  const started = Date.now();
  const terminal = new Set(["verified", "flagged", "failed", "skipped"]);
  let latest = {};
  while (Date.now() - started < maxWaitMs) {
    for (const docId of docIds) {
      const status = await api(token, "GET", `/documents/${encodeURIComponent(docId)}/verification-status`, null, { allowFailure: true });
      latest[docId] = {
        ok: status.ok,
        status: status.status,
        data: flattenResponse(status.data),
      };
    }
    const values = Object.values(latest).map((item) => String((item.data || {}).verification_status || (item.data || {}).status || "").toLowerCase());
    if (values.length && values.every((value) => terminal.has(value))) break;
    await sleep(5000);
  }
  return { waited_ms: Date.now() - started, statuses: latest };
}

function documentSlot(doc) {
  return doc.slot_key || `${doc.person_id ? `person:${doc.person_id}:` : "entity:"}${doc.doc_type || ""}`;
}

function expectedSlots(record) {
  const slots = [];
  for (const [doc_type] of BASE_DOCS) {
    slots.push({ doc_type, slot_key: `entity:${doc_type}`, scope: "entity" });
  }
  for (const personType of ["director", "ubo"]) {
    const people = personType === "director" ? record.payload.directors : record.payload.ubos;
    for (const p of people || []) {
      for (const doc_type of ["passport", "poa"]) {
        slots.push({
          doc_type,
          person_id: p.person_key,
          person_type: personType,
          slot_key: `person:${personType}:${p.person_key}:${doc_type}`,
          scope: personType,
        });
      }
    }
  }
  for (const i of record.payload.intermediaries || []) {
    for (const doc_type of ["cert_inc", "reg_dir", "reg_sh", "cert_gs", "fin_stmt"]) {
      slots.push({
        doc_type,
        person_id: i.person_key,
        person_type: "intermediary",
        slot_key: `person:intermediary:${i.person_key}:${doc_type}`,
        scope: "intermediary",
      });
    }
  }
  return slots;
}

function analyzeDocuments(record, detail) {
  const documents = detail.documents || [];
  const bySlot = new Map(documents.map((doc) => [documentSlot(doc), doc]));
  const expected = expectedSlots(record);
  const missing = expected.filter((slot) => !bySlot.has(slot.slot_key));
  const uploaded = documents.map((doc) => ({
    id: doc.id,
    doc_type: doc.doc_type,
    slot_key: doc.slot_key,
    person_id: doc.person_id,
    verification_status: doc.verification_status,
    verification_state: doc.verification_state,
    review_status: doc.review_status,
    document_reliance_state: doc.document_reliance_state,
    evidence_class: doc.evidence_class,
    download_available: doc.download_available,
  }));
  const unclassified = documents.filter((doc) => String(doc.doc_type || "").toLowerCase() === "unclassified");
  const slotMismatches = documents.filter((doc) => {
    if (!doc.slot_key) return true;
    const normalized = String(doc.doc_type || "").toLowerCase();
    return normalized && !String(doc.slot_key).toLowerCase().includes(normalized) && !String(doc.slot_key).startsWith("enhanced_requirement:");
  });
  return {
    expected,
    expected_count: expected.length,
    uploaded,
    uploaded_count: documents.length,
    missing,
    missing_count: missing.length,
    unclassified_count: unclassified.length,
    slot_mismatch_count: slotMismatches.length,
    slot_mismatches: slotMismatches.map((doc) => ({ id: doc.id, doc_type: doc.doc_type, slot_key: doc.slot_key })),
    reliance_summary: detail.document_reliance_summary || detail.document_evidence_gate || null,
    pilot_evidence_summary: detail.pilot_evidence_summary || null,
  };
}

function analyzeAgent1(detail, verificationPoll) {
  const rows = (detail.documents || []).map((doc) => {
    const results = doc.verification_results || {};
    const checks = Array.isArray(results.checks) ? results.checks : [];
    return {
      id: doc.id,
      doc_type: doc.doc_type,
      slot_key: doc.slot_key,
      expected_document_type: doc.doc_type,
      canonical_policy_used: results.policy_id || results.document_policy?.policy_id || results.policy?.id || results.document_policy?.id || "",
      status: doc.verification_status || doc.verification_state || "",
      checks_persisted_count: checks.length,
      material_issue: checks.find((check) => String(check.result || "").toLowerCase() !== "pass")?.message || "",
      ai_source: results.ai_source || "",
      technical_details_available: Boolean(doc.verification_results),
      default_ui_should_hide_noise: true,
    };
  });
  return {
    documents: rows,
    total: rows.length,
    pending_or_in_progress_seen: Object.values(verificationPoll.statuses || {}).some((item) => {
      const status = String((item.data || {}).verification_status || "").toLowerCase();
      return status === "pending" || status === "in_progress";
    }),
    terminal_count: rows.filter((row) => ["verified", "flagged", "failed", "skipped"].includes(String(row.status).toLowerCase())).length,
  };
}

function analyzeRisk(detail) {
  const dims = detail.risk_dimensions || {};
  const ps = detail.prescreening_data || {};
  return {
    raw_score: detail.risk_score ?? detail.final_risk_score ?? null,
    risk_band: detail.final_risk_level || detail.risk_level || "",
    base_risk_level: detail.base_risk_level || "",
    onboarding_lane: detail.onboarding_lane || "",
    score_drivers: detail.risk_escalations || [],
    dimensions: dims,
    country_jurisdiction_contribution: dims.d2 ?? dims.D2 ?? "",
    business_activity_contribution: dims.d4 ?? dims.D4 ?? "",
    pep_contribution: dims.d1 ?? dims.D1 ?? "",
    ownership_structure_contribution: ps.ownership_structure || detail.ownership_structure || "",
    document_evidence_contribution: (detail.document_reliance_summary || {}).status || (detail.document_evidence_gate || {}).status || "",
    elevation_reason_text: detail.elevation_reason_text || "",
  };
}

function analyzeScreening(detail) {
  const ps = detail.prescreening_data || {};
  const report = ps.screening_report || {};
  return {
    screening_mode: report.screening_mode || detail.screening_mode || "",
    total_hits: report.total_hits ?? "",
    overall_flags: report.overall_flags || [],
    screening_truth_summary: detail.screening_truth_summary || {},
    screening_reviews: detail.screening_reviews || [],
    sumsub_idv_statuses: detail.sumsub_idv_statuses || {},
    idv_gate_summary: detail.idv_gate_summary || {},
  };
}

function analyzeMemoApproval(detail, approvalAttempt, submitKycAttempt) {
  return {
    latest_memo: detail.latest_memo || null,
    latest_memo_data_status: detail.latest_memo_data ? {
      final_status: detail.latest_memo_data.final_status,
      review_status: detail.latest_memo_data.review_status,
      validation_status: detail.latest_memo_data.validation_status,
    } : null,
    memo_preapproval_status: detail.latest_memo ? (detail.latest_memo.final_status || detail.latest_memo.review_status || "generated") : "not_generated",
    pre_approval_decision: detail.pre_approval_decision || "",
    pre_approval_notes_present: Boolean(detail.pre_approval_notes),
    approval_should_be_blocked: true,
    approval_attempt: approvalAttempt,
    kyc_submit_attempt: submitKycAttempt,
    gate_blocker_count: detail.gate_blocker_count ?? (detail.gate_blockers || []).length,
    gate_blockers: detail.gate_blockers || [],
    approval_gate_presentation: detail.approval_gate_presentation || null,
    current_gate_diagnostics: detail.current_gate_diagnostics || null,
  };
}

async function downloadChecks(token, detail) {
  const out = [];
  for (const doc of detail.documents || []) {
    const result = await api(token, "GET", `/documents/${encodeURIComponent(doc.id)}/download?view=inline`, null, { allowFailure: true });
    out.push({
      document_id: doc.id,
      doc_type: doc.doc_type,
      ok: result.ok,
      status: result.status,
      has_presigned_url: Boolean((flattenResponse(result.data) || {}).download_url),
      content_type: result.headers.content_type,
    });
  }
  return out;
}

function scenarioVerdict(summary) {
  if ((summary.defects || []).some((d) => [
    "prescreening_screening_provider_unavailable",
    "submit_rate_limit_blocked_test_continuation",
    "prescreening_submit_failed",
  ].includes(d.code))) {
    return "BLOCKED";
  }
  const p0p1 = summary.defects.filter((d) => d.severity === "P0" || d.severity === "P1");
  if (!summary.portal_created || !summary.backoffice_visible) return "BLOCKED";
  if (p0p1.length) return "FAIL";
  if (summary.defects.length) return "PASS WITH MINOR ISSUES";
  return "PASS";
}

function classifyScenario(record) {
  const defects = [];
  const detail = record.backoffice_detail || {};
  const risk = record.scoring || {};
  const docs = record.document_analysis || {};
  const agent = record.agent1_analysis || {};
  const enhanced = record.enhanced_requirements || {};
  const approvalAttempt = record.approval_attempt || {};
  const submitKycAttempt = record.submit_kyc_attempt || {};
  const downloads = record.download_checks || [];

  const add = (severity, code, message) => defects.push({
    scenario: record.scenario.number,
    severity,
    code,
    message,
  });

  if (!record.create?.ok) add("P0", "portal_create_failed", "Application could not be created through authenticated portal API.");
  if (record.submit_prescreening && !record.submit_prescreening.ok) {
    const status = Number(record.submit_prescreening.status);
    const errorText = compact(record.submit_prescreening.data, 300);
    if (status === 503 && /screening provider temporarily unavailable/i.test(JSON.stringify(record.submit_prescreening.data || {}))) {
      add("P0", "prescreening_screening_provider_unavailable", `Portal prescreening submit returned 503 before pricing/KYC: ${errorText}`);
    } else if (status === 429) {
      add("P2", "submit_rate_limit_blocked_test_continuation", `Portal prescreening submit was rate-limited after earlier failed attempts: ${errorText}`);
    } else {
      add("P0", "prescreening_submit_failed", `Portal prescreening submit failed before pricing/KYC: status ${status}; ${errorText}`);
    }
  }
  if (!record.backoffice_visible) add("P0", "backoffice_not_visible", "Created portal application was not visible to back office.");
  if (record.submit_prescreening && !record.submit_prescreening.ok) {
    return defects;
  }
  if (record.scenario.number === 1 && !["LOW", "MEDIUM"].includes(String(risk.risk_band || "").toUpperCase())) {
    add("P1", "baseline_risk_not_low_or_medium", `Clean baseline risk was ${risk.risk_band || "unknown"}.`);
  }
  if (record.scenario.number === 2 && docs.missing_count === 0) {
    add("P1", "missing_required_document_not_detected", "Omitted corporate document was not shown as missing by the document evidence gate.");
  }
  if (record.scenario.number === 4 && docs.missing_count === 0) {
    add("P1", "person_missing_document_not_detected", "Omitted person-level document was not shown as missing.");
  }
  if (record.scenario.expectEDD && !((enhanced.requirements || []).length || (detail.enhanced_review_summary || {}).active_count || String(risk.onboarding_lane || "").toUpperCase() === "EDD")) {
    add("P1", "expected_enhanced_requirements_missing", "Scenario expected enhanced/EDD routing, but no enhanced requirements or EDD lane were observed.");
  }
  if (record.scenario.number === 6 && String(risk.risk_band || "").toUpperCase() === "LOW") {
    add("P1", "country_risk_silent_low", "Country-risk scenario resolved as LOW; document current rollback/manual-source behavior.");
  }
  if (docs.unclassified_count > 0) {
    add("P1", "portal_upload_unclassified", `${docs.unclassified_count} portal-uploaded document(s) appeared as Unclassified.`);
  }
  if (docs.slot_mismatch_count > 0) {
    add("P1", "portal_slot_mapping_mismatch", `${docs.slot_mismatch_count} document(s) had doc_type/slot_key mismatch.`);
  }
  const failedDownloads = downloads.filter((item) => !item.ok);
  if (failedDownloads.length) {
    add("P1", "document_view_download_unavailable", `${failedDownloads.length} uploaded document(s) could not be viewed/downloaded.`);
  }
  const longUploads = (record.uploads || []).filter((u) => u.ok && u.duration_ms > 10000);
  if (longUploads.length) {
    add("P2", "portal_upload_latency_high", `${longUploads.length} upload(s) took over 10 seconds; async behavior should remain fast.`);
  }
  if (agent.total > 0 && agent.terminal_count === 0) {
    add("P2", "agent1_no_terminal_status", "Agent 1 verification remained non-terminal within the polling window.");
  }
  if (approvalAttempt.ok || [200, 201, 202].includes(Number(approvalAttempt.status))) {
    add("P0", "false_approval_possible", "Approval attempt succeeded despite unresolved synthetic evidence/memo gates.");
  }
  if (submitKycAttempt.ok && record.scenario.number !== 10) {
    add("P1", "kyc_submission_unexpectedly_succeeded", "KYC submission succeeded with synthetic/unverified evidence; verify this is intended for staging workflow-only mode.");
  }
  if (record.scenario.manualAccept) {
    if (!record.manual_acceptance?.without_reason || record.manual_acceptance.without_reason.status !== 400) {
      add("P1", "manual_accept_reason_not_required", "Manual acceptance without reason did not return the expected 400.");
    }
    if (!record.manual_acceptance?.with_reason || !record.manual_acceptance.with_reason.ok) {
      add("P1", "manual_accept_with_reason_failed", "Manual acceptance with reason failed.");
    }
    const auditText = JSON.stringify(record.audit_log || {}).toLowerCase();
    if (!auditText.includes("document accepted with findings") && !auditText.includes("senior manual acceptance")) {
      add("P1", "manual_acceptance_audit_missing", "Audit trail did not include a clear manual acceptance event.");
    }
  }
  const sarText = JSON.stringify(record.backoffice_detail || {}).toLowerCase();
  if (sarText.includes("sar/str active") || sarText.includes("activate sar") || sarText.includes("submit sar")) {
    add("P1", "sar_str_active_surface", "SAR/STR active workflow language appeared in application evidence.");
  }
  return defects;
}

function summarizeScenario(record) {
  const riskBand = String(record.scoring?.risk_band || "").toUpperCase();
  const docs = record.document_analysis || {};
  const enhanced = record.enhanced_requirements || {};
  const screening = record.screening_analysis || {};
  const memo = record.memo_approval_analysis || {};
  const defects = record.defects || [];
  const blockedAtSubmit = record.submit_prescreening && !record.submit_prescreening.ok;
  const summary = {
    Scenario: `S${String(record.scenario.number).padStart(2, "0")} ${record.scenario.name}`,
    "Portal created?": record.create?.ok ? "Yes" : "No",
    "Back office visible?": record.backoffice_visible ? "Yes" : (record.backoffice_visible === false ? "No" : "Not checked"),
    "Risk score OK?": blockedAtSubmit ? "Blocked" : (riskBand ? (defects.some((d) => d.code.includes("risk")) ? "No" : "Review") : "No"),
    "Docs OK?": blockedAtSubmit ? "Not reached" : (defects.some((d) => d.code.includes("document") || d.code.includes("slot") || d.code.includes("unclassified")) ? "No" : "Review"),
    "Agent 1 OK?": blockedAtSubmit ? "Not reached" : (defects.some((d) => d.code.includes("agent1")) ? "No" : "Review"),
    "EDD OK?": blockedAtSubmit ? "Not reached" : (record.scenario.expectEDD ? ((enhanced.requirements || []).length ? "Yes" : "No") : "N/A"),
    "Screening OK?": blockedAtSubmit ? "Blocked" : (screening.screening_mode ? "Review" : "No"),
    "Memo/pre-approval OK?": blockedAtSubmit ? "Not reached" : (memo.pre_approval_decision || memo.memo_preapproval_status ? "Review" : "No"),
    "Approval gate OK?": blockedAtSubmit ? "Not reached" : (record.approval_attempt && !record.approval_attempt.ok ? "Yes" : "No"),
    Defects: defects.length ? defects.map((d) => `${d.severity}:${d.code}`).join("; ") : "-",
  };
  summary.Verdict = scenarioVerdict({
    portal_created: record.create?.ok,
    backoffice_visible: record.backoffice_visible,
    defects,
  });
  return summary;
}

function table(headers, rows) {
  const head = `| ${headers.join(" | ")} |`;
  const sep = `| ${headers.map(() => "---").join(" | ")} |`;
  const body = rows.map((row) => `| ${headers.map((h) => md(row[h])).join(" | ")} |`);
  return [head, sep, ...body].join("\n");
}

function masterTable(summaries) {
  const headers = [
    "Scenario",
    "Portal created?",
    "Back office visible?",
    "Risk score OK?",
    "Docs OK?",
    "Agent 1 OK?",
    "EDD OK?",
    "Screening OK?",
    "Memo/pre-approval OK?",
    "Approval gate OK?",
    "Defects",
    "Verdict",
  ];
  return table(headers, summaries);
}

async function loadPlaywright() {
  try {
    return require("playwright-core");
  } catch (err) {
    const modulesDir = process.env.PLAYWRIGHT_NODE_MODULES || "/tmp/onboarda-pw/node_modules";
    try {
      return require(path.join(modulesDir, "playwright-core"));
    } catch (innerErr) {
      throw new Error("playwright-core is required. Install it outside the repo or set PLAYWRIGHT_NODE_MODULES.");
    }
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
  throw new Error("No Chrome/Chromium executable found. Set CHROME_PATH.");
}

async function loginPortalUI(page) {
  await page.goto(`${BASE_URL}/portal`, { waitUntil: "domcontentloaded" });
  const signIn = page.locator("button[onclick=\"showView('login')\"]").first();
  if (await signIn.isVisible().catch(() => false)) {
    await signIn.click();
  }
  await page.locator("#l-email").fill(process.env.STAGING_PORTAL_EMAIL);
  await page.locator("#l-password").fill(process.env.STAGING_PORTAL_PASSWORD);
  await Promise.all([
    page.waitForResponse((resp) => resp.url().includes("/api/auth/client/login") && resp.status() < 500).catch(() => null),
    page.locator("#login-form button[type=submit], #login-form .btn-submit").first().click(),
  ]);
  await page.waitForSelector("#view-my-apps:not(.hidden)", { timeout: 30000 }).catch(() => null);
}

async function loginBackofficeUI(page) {
  await page.goto(`${BASE_URL}/backoffice`, { waitUntil: "domcontentloaded" });
  await page.locator("#login-email").fill(process.env.STAGING_BO_EMAIL);
  await page.locator("#login-password").fill(process.env.STAGING_BO_PASSWORD);
  await Promise.all([
    page.waitForResponse((resp) => resp.url().includes("/api/auth/officer/login") && resp.status() < 500).catch(() => null),
    page.locator("#login-form button[type=submit], #login-submit, button:has-text('Sign In')").first().click(),
  ]);
  await page.waitForFunction(() => {
    const overlay = document.querySelector("#login-overlay");
    if (!overlay) return true;
    const style = window.getComputedStyle(overlay);
    return style.display === "none" || overlay.hidden || overlay.classList.contains("hidden");
  }, null, { timeout: 30000 }).catch(() => null);
}

async function screenshot(page, name) {
  const file = path.join(SCREENSHOT_DIR, `${safeName(name)}.png`);
  await page.screenshot({ path: file, fullPage: true });
  return file;
}

async function captureUiEvidence(browser, records) {
  const portalPage = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const backofficePage = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const screenshots = {};
  await loginPortalUI(portalPage);
  screenshots.portal_login = await screenshot(portalPage, "portal_logged_in_my_apps");
  await loginBackofficeUI(backofficePage);
  screenshots.backoffice_login = await screenshot(backofficePage, "backoffice_logged_in");

  for (const record of records) {
    const n = `S${String(record.scenario.number).padStart(2, "0")}`;
    try {
      await portalPage.evaluate((ref) => {
        if (typeof resumeApplication === "function") {
          return resumeApplication(ref, "onboarding");
        }
      }, record.application_ref);
      await portalPage.waitForTimeout(3500);
      record.screenshots = record.screenshots || {};
      record.screenshots.portal = await screenshot(portalPage, `${n}_${record.application_ref}_portal`);
    } catch (err) {
      record.screenshots = record.screenshots || {};
      record.screenshots.portal_error = err.message;
    }
    try {
      await backofficePage.evaluate((ref) => {
        if (typeof openAppDetail === "function") {
          return openAppDetail(ref, { initialTab: "documents" });
        }
      }, record.application_ref);
      await backofficePage.waitForTimeout(4500);
      record.screenshots = record.screenshots || {};
      record.screenshots.backoffice_documents = await screenshot(backofficePage, `${n}_${record.application_ref}_backoffice_documents`);
      await backofficePage.evaluate(() => {
        if (typeof switchDetailTab === "function") switchDetailTab("overview");
      }).catch(() => null);
      await backofficePage.waitForTimeout(1000);
      record.screenshots.backoffice_overview = await screenshot(backofficePage, `${n}_${record.application_ref}_backoffice_overview`);
      await backofficePage.evaluate(() => {
        if (typeof switchDetailTab === "function") switchDetailTab("activity");
      }).catch(() => null);
      await backofficePage.waitForTimeout(1000);
      record.screenshots.backoffice_activity = await screenshot(backofficePage, `${n}_${record.application_ref}_backoffice_activity`);
    } catch (err) {
      record.screenshots = record.screenshots || {};
      record.screenshots.backoffice_error = err.message;
    }
  }
  await portalPage.close();
  await backofficePage.close();
  return screenshots;
}

async function createScenario(portalToken, boToken, scenario) {
  const record = {
    scenario,
    run_id: RUN_ID,
    started_at: nowIso(),
    payload: scenarioPayload(scenario),
    portal_creation_method: "Authenticated client portal API endpoint used by portal UI; no backend inserts or database seeding.",
    uploads: [],
  };
  record.upload_plan = expectedUploadsForScenario(record);

  record.create = await api(portalToken, "POST", "/applications", record.payload, { allowFailure: true });
  record.create.data = flattenResponse(record.create.data);
  if (!record.create.ok) return record;
  record.application_id = record.create.data.id;
  record.application_ref = record.create.data.ref;
  writeJson(`S${String(scenario.number).padStart(2, "0")}_${record.application_ref}_created.json`, record.create.data);

  record.detail_after_create = flattenResponse((await api(portalToken, "GET", `/applications/${encodeURIComponent(record.application_ref)}`)).data);
  record.submit_prescreening = await submitPrescreeningWithRetry(portalToken, record.application_id);
  record.detail_after_submit = flattenResponse((await api(portalToken, "GET", `/applications/${encodeURIComponent(record.application_ref)}`, null, { allowFailure: true })).data);
  if (!record.submit_prescreening.ok) {
    await completeBackofficeEvidenceForRecord(boToken, record);
    record.defects = classifyScenario(record);
    record.summary = summarizeScenario(record);
    record.finished_at = nowIso();
    writeJson(`S${String(scenario.number).padStart(2, "0")}_${record.application_ref}_record.json`, record);
    return record;
  }

  record.accept_pricing = await api(portalToken, "POST", `/applications/${encodeURIComponent(record.application_id)}/accept-pricing`, {}, { allowFailure: true });
  record.accept_pricing.data = flattenResponse(record.accept_pricing.data);
  record.detail_after_pricing = flattenResponse((await api(portalToken, "GET", `/applications/${encodeURIComponent(record.application_ref)}`, null, { allowFailure: true })).data);

  const pricingStatus = String(record.detail_after_pricing?.status || record.accept_pricing.data?.status || "").toLowerCase();
  if (["pre_approval_review", "edd_required"].includes(pricingStatus)) {
    record.pre_approval_lock_probe = await uploadPdf(portalToken, record.application_id, {
      doc_type: "cert_inc",
      label: "Pre-approval upload lock probe",
      scope: "entity",
    }, record, "portal_lock_probe");
    await sleep(UPLOAD_DELAY_MS);
    record.pre_approval_decision = await api(boToken, "POST", `/applications/${encodeURIComponent(record.application_id)}/pre-approval-decision`, {
      decision: "PRE_APPROVE",
      notes: `E2E-PILOT-READINESS-1 synthetic staging pre-approval to unlock KYC upload testing for ${record.application_ref}. Not remediation.`,
    }, { allowFailure: true });
    record.pre_approval_decision.data = flattenResponse(record.pre_approval_decision.data);
    record.detail_after_preapproval = flattenResponse((await api(boToken, "GET", `/applications/${encodeURIComponent(record.application_ref)}`, null, { allowFailure: true })).data);
  }

  for (const upload of record.upload_plan) {
    const result = await uploadPdf(portalToken, record.application_id, upload, record);
    record.uploads.push(result);
    await sleep(UPLOAD_DELAY_MS);
  }

  const uploadDocIds = record.uploads.map((u) => u.response?.id).filter(Boolean);
  record.verification_poll = uploadDocIds.length ? await pollVerification(boToken, uploadDocIds, VERIFY_WAIT_MS) : { waited_ms: 0, statuses: {} };

  record.portal_documents_after_upload = await api(portalToken, "GET", `/applications/${encodeURIComponent(record.application_id)}/documents`, null, { allowFailure: true });
  record.portal_documents_after_upload.data = flattenResponse(record.portal_documents_after_upload.data);

  record.portal_enhanced_requirements = await api(portalToken, "GET", `/portal/applications/${encodeURIComponent(record.application_id)}/enhanced-requirements?exclude_periodic_review=1`, null, { allowFailure: true });
  record.portal_enhanced_requirements.data = flattenResponse(record.portal_enhanced_requirements.data);

  if (scenario.number === 7) {
    const req = (record.portal_enhanced_requirements.data?.requirements || []).find((item) => item.requirement_type === "document" && ["required", "additional_information_needed"].includes(item.status));
    if (req) {
      record.portal_enhanced_upload = await uploadEnhancedRequirement(portalToken, record.application_id, req, record);
      await sleep(UPLOAD_DELAY_MS);
    }
  }

  record.submit_kyc_attempt = await api(portalToken, "POST", `/applications/${encodeURIComponent(record.application_id)}/submit-kyc`, {}, { allowFailure: true });
  record.submit_kyc_attempt.data = flattenResponse(record.submit_kyc_attempt.data);

  record.backoffice_list_lookup = await api(boToken, "GET", `/applications?view=list&limit=20&offset=0&q=${encodeURIComponent(record.application_ref)}`, null, { allowFailure: true });
  record.backoffice_list_lookup.data = flattenResponse(record.backoffice_list_lookup.data);
  record.backoffice_visible = Boolean((record.backoffice_list_lookup.data?.applications || []).find((app) => app.ref === record.application_ref || app.id === record.application_id));

  record.backoffice_detail_response = await api(boToken, "GET", `/applications/${encodeURIComponent(record.application_ref)}?include_history=true`, null, { allowFailure: true });
  record.backoffice_detail = flattenResponse(record.backoffice_detail_response.data);
  writeJson(`S${String(scenario.number).padStart(2, "0")}_${record.application_ref}_application_detail.json`, record.backoffice_detail);

  record.enhanced_requirements_response = await api(boToken, "GET", `/applications/${encodeURIComponent(record.application_ref)}/enhanced-requirements`, null, { allowFailure: true });
  record.enhanced_requirements = flattenResponse(record.enhanced_requirements_response.data) || {};
  writeJson(`S${String(scenario.number).padStart(2, "0")}_${record.application_ref}_enhanced_requirements.json`, record.enhanced_requirements);

  if (scenario.manualAccept) {
    const targetDoc = (record.backoffice_detail.documents || []).find((doc) => String(doc.verification_status || "").toLowerCase() !== "verified") || (record.backoffice_detail.documents || [])[0];
    if (targetDoc) {
      record.manual_acceptance = {
        document_id: targetDoc.id,
        without_reason: await api(boToken, "POST", `/documents/${encodeURIComponent(targetDoc.id)}/review`, { status: "accepted", comment: "" }, { allowFailure: true }),
      };
      record.manual_acceptance.without_reason.data = flattenResponse(record.manual_acceptance.without_reason.data);
      record.manual_acceptance.with_reason = await api(boToken, "POST", `/documents/${encodeURIComponent(targetDoc.id)}/review`, {
        status: "accepted",
        comment: `E2E-PILOT-READINESS-1 staging workflow test manual acceptance for synthetic document ${targetDoc.id}.`,
      }, { allowFailure: true });
      record.manual_acceptance.with_reason.data = flattenResponse(record.manual_acceptance.with_reason.data);
      record.backoffice_detail_after_manual_acceptance = flattenResponse((await api(boToken, "GET", `/applications/${encodeURIComponent(record.application_ref)}?include_history=true`, null, { allowFailure: true })).data);
      record.backoffice_detail = record.backoffice_detail_after_manual_acceptance || record.backoffice_detail;
    }
  }

  record.download_checks = await downloadChecks(boToken, record.backoffice_detail);
  record.identity_verifications = await api(boToken, "GET", `/applications/${encodeURIComponent(record.application_ref)}/kyc/identity-verifications`, null, { allowFailure: true });
  record.identity_verifications.data = flattenResponse(record.identity_verifications.data);
  record.memo_validation_status = await api(boToken, "GET", `/applications/${encodeURIComponent(record.application_ref)}/memo/validation`, null, { allowFailure: true });
  record.memo_validation_status.data = flattenResponse(record.memo_validation_status.data);
  record.approval_attempt = await api(boToken, "POST", `/applications/${encodeURIComponent(record.application_ref)}/decision`, {
    decision: "approve",
    decision_reason: `E2E-PILOT-READINESS-1 negative approval gate probe for synthetic staging app ${record.application_ref}.`,
    officer_signoff: { acknowledged: true, scope: "decision", source_context: "ai_advisory" },
  }, { allowFailure: true });
  record.approval_attempt.data = flattenResponse(record.approval_attempt.data);
  record.audit_log_response = await api(boToken, "GET", `/applications/${encodeURIComponent(record.application_ref)}/audit-log?limit=100`, null, { allowFailure: true });
  record.audit_log = flattenResponse(record.audit_log_response.data);
  writeJson(`S${String(scenario.number).padStart(2, "0")}_${record.application_ref}_audit_log.json`, record.audit_log);

  record.scoring = analyzeRisk(record.backoffice_detail);
  record.document_analysis = analyzeDocuments(record, record.backoffice_detail);
  record.agent1_analysis = analyzeAgent1(record.backoffice_detail, record.verification_poll);
  record.screening_analysis = analyzeScreening(record.backoffice_detail);
  record.memo_approval_analysis = analyzeMemoApproval(record.backoffice_detail, record.approval_attempt, record.submit_kyc_attempt);
  record.defects = classifyScenario(record);
  record.summary = summarizeScenario(record);
  record.finished_at = nowIso();
  writeJson(`S${String(scenario.number).padStart(2, "0")}_${record.application_ref}_record.json`, record);
  return record;
}

async function completeBackofficeEvidenceForRecord(boToken, record) {
  if (!record || !record.application_ref) return record;
  record.backoffice_list_lookup = await api(boToken, "GET", `/applications?view=list&limit=20&offset=0&q=${encodeURIComponent(record.application_ref)}`, null, { allowFailure: true });
  record.backoffice_list_lookup.data = flattenResponse(record.backoffice_list_lookup.data);
  record.backoffice_visible = Boolean((record.backoffice_list_lookup.data?.applications || []).find((app) => app.ref === record.application_ref || app.id === record.application_id));
  record.backoffice_detail_response = await api(boToken, "GET", `/applications/${encodeURIComponent(record.application_ref)}?include_history=true`, null, { allowFailure: true });
  record.backoffice_detail = flattenResponse(record.backoffice_detail_response.data) || {};
  record.audit_log_response = await api(boToken, "GET", `/applications/${encodeURIComponent(record.application_ref)}/audit-log?limit=100`, null, { allowFailure: true });
  record.audit_log = flattenResponse(record.audit_log_response.data);
  if (record.backoffice_detail_response.ok) {
    writeJson(`S${String(record.scenario.number).padStart(2, "0")}_${record.application_ref}_application_detail.json`, record.backoffice_detail);
  }
  if (record.audit_log_response.ok) {
    writeJson(`S${String(record.scenario.number).padStart(2, "0")}_${record.application_ref}_audit_log.json`, record.audit_log);
  }
  record.scoring = analyzeRisk(record.backoffice_detail || {});
  record.document_analysis = analyzeDocuments(record, record.backoffice_detail || {});
  record.agent1_analysis = analyzeAgent1(record.backoffice_detail || {}, record.verification_poll || { statuses: {} });
  record.screening_analysis = analyzeScreening(record.backoffice_detail || {});
  record.memo_approval_analysis = analyzeMemoApproval(record.backoffice_detail || {}, record.approval_attempt, record.submit_kyc_attempt);
  return record;
}

function finalPilotVerdict(defects, summaries) {
  if (defects.some((d) => d.severity === "P0")) return "not ready";
  if (defects.some((d) => d.severity === "P1")) return "weak";
  if (summaries.some((s) => s.Verdict === "PASS WITH MINOR ISSUES")) return "acceptable with controls";
  return "strong";
}

function buildReports(context, records) {
  const summaries = records.map((record) => record.summary || summarizeScenario(record));
  const defects = records.flatMap((record) => record.defects || []);
  const verdict = finalPilotVerdict(defects, summaries);
  writeJson("scenario_records.json", records);
  writeJson("scenario_summary_table.json", summaries);
  writeJson("classified_findings.json", defects);
  writeJson("audit_runtime_summary.json", {
    run_id: RUN_ID,
    root: ROOT,
    origin_main_sha: ORIGIN_MAIN_SHA,
    version: context.version,
    provider_mode: context.provider_mode,
    credentials_used_without_secrets: context.credentials_used_without_secrets,
    final_pilot_readiness_verdict: verdict,
    scenario_count: records.length,
    defect_count: defects.length,
    submit_delay_ms: SUBMIT_DELAY_MS,
    submit_max_attempts: SUBMIT_MAX_ATTEMPTS,
  });

  writeText("test_plan.md", [
    "# E2E-PILOT-READINESS-1 Test Plan",
    "",
    `Run ID: ${RUN_ID}`,
    `Evidence folder: ${ROOT}`,
    `Origin/main SHA: ${ORIGIN_MAIN_SHA}`,
    `Staging /api/version SHA: ${context.version.git_sha || context.version.git_sha_short || "-"}`,
    "",
    "Scope: create 10 synthetic applications through authenticated staging portal endpoints used by the portal UI, then inspect the resulting back-office/API state. No production data, no backend database inserts, no SAR/STR activation, no remediation changes.",
    "",
    "Portal creation method: authenticated client portal API calls matching the portal UI payload and upload endpoints. Screenshots were captured from the actual portal and back-office UI after creation.",
    "",
    "Provider mode observed:",
    "```json",
    JSON.stringify(context.provider_mode, null, 2),
    "```",
    "",
    "Credentials used: portal " + context.credentials_used_without_secrets.portal + "; back office " + context.credentials_used_without_secrets.backoffice + ". Secrets omitted.",
  ].join("\n"));

  writeText("scenario_matrix.md", [
    "# Scenario Matrix",
    "",
    table(["Scenario", "Purpose", "Expected"], SCENARIOS.map((s) => ({
      Scenario: `S${String(s.number).padStart(2, "0")} ${s.name}`,
      Purpose: s.purpose,
      Expected: s.expected,
    }))),
    "",
    "## Master Table",
    "",
    masterTable(summaries),
  ].join("\n"));

  writeText("portal_creation_log.md", [
    "# Portal Creation Log",
    "",
    table(["Scenario", "Application", "Reference", "Created", "Submitted", "Submit attempts", "Pricing accepted", "Pre-approval action", "Uploads", "KYC submit"], records.map((r) => ({
      Scenario: `S${String(r.scenario.number).padStart(2, "0")}`,
      Application: r.payload.company_name,
      Reference: r.application_ref || "-",
      Created: r.create?.status,
      Submitted: r.submit_prescreening?.status,
      "Submit attempts": (r.submit_prescreening?.attempts || []).map((a) => `${a.attempt}:${a.status}`).join(", ") || "-",
      "Pricing accepted": r.accept_pricing?.status,
      "Pre-approval action": r.pre_approval_decision ? `${r.pre_approval_decision.status}` : "N/A",
      Uploads: `${(r.uploads || []).filter((u) => u.ok).length}/${(r.uploads || []).length}`,
      "KYC submit": r.submit_kyc_attempt ? `${r.submit_kyc_attempt.status} ${compact(r.submit_kyc_attempt.data, 120)}` : "-",
    }))),
  ].join("\n"));

  writeText("backoffice_review_log.md", [
    "# Back-Office Review Log",
    "",
    table(["Scenario", "Reference", "Visible", "Status", "Risk", "Docs", "Enhanced", "Gate blockers", "Screenshots"], records.map((r) => ({
      Scenario: `S${String(r.scenario.number).padStart(2, "0")}`,
      Reference: r.application_ref || "-",
      Visible: r.backoffice_visible ? "Yes" : "No",
      Status: r.backoffice_detail?.status || "-",
      Risk: `${r.scoring?.risk_band || "-"} (${r.scoring?.raw_score ?? "-"})`,
      Docs: `${r.document_analysis?.uploaded_count ?? 0} uploaded; ${r.document_analysis?.missing_count ?? 0} missing`,
      Enhanced: `${(r.enhanced_requirements?.requirements || []).length} requirement(s)`,
      "Gate blockers": r.memo_approval_analysis?.gate_blocker_count ?? "-",
      Screenshots: Object.values(r.screenshots || {}).filter((v) => typeof v === "string").map((p) => path.relative(ROOT, p)).join(", "),
    }))),
  ].join("\n"));

  writeText("scoring_results.md", [
    "# Scoring Results",
    "",
    table(["Scenario", "Raw score", "Risk band", "Lane", "Drivers", "Country contribution", "Business contribution", "PEP contribution", "Ownership", "Expectation"], records.map((r) => ({
      Scenario: `S${String(r.scenario.number).padStart(2, "0")}`,
      "Raw score": r.scoring?.raw_score,
      "Risk band": r.scoring?.risk_band,
      Lane: r.scoring?.onboarding_lane,
      Drivers: r.scoring?.elevation_reason_text || r.scoring?.score_drivers,
      "Country contribution": r.scoring?.country_jurisdiction_contribution,
      "Business contribution": r.scoring?.business_activity_contribution,
      "PEP contribution": r.scoring?.pep_contribution,
      Ownership: r.scoring?.ownership_structure_contribution,
      Expectation: r.scenario.expectRisk || "-",
    }))),
  ].join("\n"));

  writeText("document_request_results.md", [
    "# Document Request Results",
    "",
    table(["Scenario", "Required expected", "Uploaded", "Missing", "Unclassified", "Slot mismatches", "Enhanced docs"], records.map((r) => ({
      Scenario: `S${String(r.scenario.number).padStart(2, "0")}`,
      "Required expected": r.document_analysis?.expected_count,
      Uploaded: r.document_analysis?.uploaded_count,
      Missing: (r.document_analysis?.missing || []).map((m) => m.slot_key).join(", "),
      Unclassified: r.document_analysis?.unclassified_count,
      "Slot mismatches": r.document_analysis?.slot_mismatch_count,
      "Enhanced docs": (r.enhanced_requirements?.requirements || []).map((req) => `${req.requirement_key || req.label || req.id}:${req.status}`).join(", "),
    }))),
  ].join("\n"));

  writeText("agent1_verification_results.md", [
    "# Agent 1 Verification Results",
    "",
    table(["Scenario", "Documents", "Terminal", "Pending/running observed", "Statuses", "Checks persisted"], records.map((r) => ({
      Scenario: `S${String(r.scenario.number).padStart(2, "0")}`,
      Documents: r.agent1_analysis?.total,
      Terminal: r.agent1_analysis?.terminal_count,
      "Pending/running observed": r.agent1_analysis?.pending_or_in_progress_seen ? "Yes" : "No",
      Statuses: (r.agent1_analysis?.documents || []).map((d) => `${d.doc_type}:${d.status}`).join(", "),
      "Checks persisted": (r.agent1_analysis?.documents || []).map((d) => `${d.doc_type}:${d.checks_persisted_count}`).join(", "),
    }))),
    "",
    "Agent 1 is recorded from persisted document verification state and remains separate from Sumsub IDV surfaces captured in runtime JSON.",
  ].join("\n"));

  writeText("screening_results.md", [
    "# Screening Results",
    "",
    table(["Scenario", "Mode", "Hits", "Truth summary", "Reviews", "IDV gate"], records.map((r) => ({
      Scenario: `S${String(r.scenario.number).padStart(2, "0")}`,
      Mode: r.screening_analysis?.screening_mode,
      Hits: r.screening_analysis?.total_hits,
      "Truth summary": compact(r.screening_analysis?.screening_truth_summary, 180),
      Reviews: (r.screening_analysis?.screening_reviews || []).length,
      "IDV gate": compact(r.screening_analysis?.idv_gate_summary, 180),
    }))),
  ].join("\n"));

  writeText("memo_preapproval_results.md", [
    "# Memo / Pre-Approval Results",
    "",
    table(["Scenario", "Pre-approval", "Memo status", "KYC submit", "Blockers"], records.map((r) => ({
      Scenario: `S${String(r.scenario.number).padStart(2, "0")}`,
      "Pre-approval": r.memo_approval_analysis?.pre_approval_decision || "N/A",
      "Memo status": r.memo_approval_analysis?.memo_preapproval_status,
      "KYC submit": r.submit_kyc_attempt ? `${r.submit_kyc_attempt.status}: ${compact(r.submit_kyc_attempt.data, 160)}` : "-",
      Blockers: compact(r.memo_approval_analysis?.gate_blockers, 240),
    }))),
  ].join("\n"));

  writeText("approval_gate_results.md", [
    "# Approval Gate Results",
    "",
    table(["Scenario", "Should block", "Blocked?", "Status", "Response"], records.map((r) => ({
      Scenario: `S${String(r.scenario.number).padStart(2, "0")}`,
      "Should block": "Yes",
      "Blocked?": r.approval_attempt && !r.approval_attempt.ok ? "Yes" : "No",
      Status: r.approval_attempt?.status,
      Response: compact(r.approval_attempt?.data, 240),
    }))),
  ].join("\n"));

  writeText("audit_activity_results.md", [
    "# Audit / Activity Results",
    "",
    table(["Scenario", "Audit rows/shape", "Manual acceptance audit", "Key actions observed"], records.map((r) => {
      const log = r.audit_log || {};
      const rows = log.audit_log || log.entries || log.items || log.logs || log || [];
      const text = JSON.stringify(log).toLowerCase();
      return {
        Scenario: `S${String(r.scenario.number).padStart(2, "0")}`,
        "Audit rows/shape": Array.isArray(rows) ? rows.length : "object",
        "Manual acceptance audit": r.scenario.manualAccept ? (text.includes("document accepted with findings") || text.includes("senior manual acceptance") ? "Yes" : "No") : "N/A",
        "Key actions observed": ["create", "pricing", "upload", "verification", "approval"].filter((word) => text.includes(word)).join(", "),
      };
    })),
  ].join("\n"));

  writeText("defects_and_gaps.md", [
    "# Defects And Gaps",
    "",
    defects.length ? table(["Severity", "Scenario", "Code", "Message"], defects.map((d) => ({
      Severity: d.severity,
      Scenario: `S${String(d.scenario).padStart(2, "0")}`,
      Code: d.code,
      Message: d.message,
    }))) : "No defects were classified by the automated audit harness.",
  ].join("\n"));

  const nextPrs = defects.some((d) => d.severity === "P0" || d.severity === "P1")
    ? [
      "Open a corrective PR focused only on the first P0/P1 category above, preserving this evidence folder.",
      "Do not close remediation items until the corrective PR has its own evidence and retest.",
    ]
    : [
      "Review P2/P3 observations, if any, for UX/report polish.",
      "Run a controlled follow-up only after deciding whether live provider-mode behavior is acceptable for pilot controls.",
    ];
  writeText("recommendations.md", [
    "# Recommendations",
    "",
    `Final pilot-readiness verdict from this run: **${verdict}**.`,
    "",
    nextPrs.map((item) => `- ${item}`).join("\n"),
    "",
    "Country-risk source note: current staging behavior is documented as observed; no country-risk remediation was attempted in this workstream.",
  ].join("\n"));

  writeText("closure_report.md", [
    "# E2E-PILOT-READINESS-1 Closure Report",
    "",
    `Run ID: ${RUN_ID}`,
    `Evidence folder: ${ROOT}`,
    `Origin/main SHA: ${ORIGIN_MAIN_SHA}`,
    `Staging /api/version: ${context.version.git_sha || context.version.git_sha_short || "-"}`,
    `Provider mode: ${JSON.stringify(context.provider_mode)}`,
    `Credentials used: portal ${context.credentials_used_without_secrets.portal}; back office ${context.credentials_used_without_secrets.backoffice}; secrets omitted.`,
    "",
    "## Summary",
    "",
    masterTable(summaries),
    "",
    "## Defects",
    "",
    defects.length ? defects.map((d) => `- ${d.severity} S${String(d.scenario).padStart(2, "0")} ${d.code}: ${d.message}`).join("\n") : "No classified defects.",
    "",
    "## Screenshots / Runtime JSON",
    "",
    `Screenshots: ${path.join(ROOT, "screenshots")}`,
    `Runtime JSON: ${RUNTIME_DIR}`,
    "",
    "## Final Verdict",
    "",
    verdict,
  ].join("\n"));
}

async function main() {
  ensureDirs();
  const missing = REQUIRED_ENV.filter((name) => !process.env[name]);
  if (missing.length) {
    console.error("BLOCKED - STAGING PORTAL CREDENTIALS REQUIRED or missing runtime input: " + missing.join(", "));
    process.exit(2);
  }

  const portalLogin = await login("/auth/client/login", process.env.STAGING_PORTAL_EMAIL, process.env.STAGING_PORTAL_PASSWORD);
  const boLogin = await login("/auth/officer/login", process.env.STAGING_BO_EMAIL, process.env.STAGING_BO_PASSWORD);

  const version = flattenResponse((await api(boLogin.token, "GET", "/version")).data);
  writeJson("api_version.json", version);
  if (ORIGIN_MAIN_SHA && version.git_sha && version.git_sha !== ORIGIN_MAIN_SHA) {
    writeText("closure_report.md", [
      "# E2E-PILOT-READINESS-1 Closure Report",
      "",
      "BLOCKED - staging /api/version does not match origin/main.",
      `origin/main: ${ORIGIN_MAIN_SHA}`,
      `staging: ${version.git_sha}`,
    ].join("\n"));
    throw new Error(`Version mismatch: origin/main ${ORIGIN_MAIN_SHA} staging ${version.git_sha}`);
  }

  const environment = flattenResponse((await api(boLogin.token, "GET", "/config/environment", null, { allowFailure: true })).data);
  const health = flattenResponse((await api(boLogin.token, "GET", "/health", null, { allowFailure: true })).data);
  const readiness = flattenResponse((await api(boLogin.token, "GET", "/readiness", null, { allowFailure: true })).data);
  const documentPolicies = flattenResponse((await api(boLogin.token, "GET", "/config/document-policies", null, { allowFailure: true })).data);
  writeJson("environment.json", environment);
  writeJson("health.json", health);
  writeJson("readiness.json", readiness);
  writeJson("document_policies.json", documentPolicies);

  const providerMode = {
    environment: environment.environment || environment.name || "staging",
    is_demo: environment.is_demo,
    is_production: environment.is_production,
    integrations: health.integrations || health.integration_status || {},
    document_policy_registry: documentPolicies.registry_version || documentPolicies.version || "",
    sar_str_active: documentPolicies.sar_str_active ?? documentPolicies.summary?.sar_str_active ?? null,
    features: environment.features || {},
  };

  const context = {
    version,
    environment,
    health,
    readiness,
    documentPolicies,
    provider_mode: providerMode,
    credentials_used_without_secrets: {
      portal: process.env.STAGING_PORTAL_EMAIL,
      backoffice: process.env.STAGING_BO_EMAIL,
      portal_redacted: redact(process.env.STAGING_PORTAL_EMAIL),
      backoffice_redacted: redact(process.env.STAGING_BO_EMAIL),
    },
  };

  if (process.env.POSTPROCESS_ONLY === "1") {
    const recordsPath = path.join(RUNTIME_DIR, "scenario_records.json");
    const records = JSON.parse(fs.readFileSync(recordsPath, "utf8"));
    for (const record of records) {
      await completeBackofficeEvidenceForRecord(boLogin.token, record);
      record.defects = classifyScenario(record);
      record.summary = summarizeScenario(record);
    }
    buildReports(context, records);
    console.log(`Postprocessed evidence reports: ${ROOT}`);
    return;
  }

  const records = [];
  for (const scenario of SCENARIOS) {
    console.log(`[${new Date().toISOString()}] Starting S${String(scenario.number).padStart(2, "0")} ${scenario.name}`);
    const record = await createScenario(portalLogin.token, boLogin.token, scenario);
    records.push(record);
    writeJson("scenario_records_partial.json", records);
    console.log(`[${new Date().toISOString()}] Finished S${String(scenario.number).padStart(2, "0")} ${record.application_ref || "(no ref)"} defects=${(record.defects || []).length}`);
    if (scenario.number < SCENARIOS.length) {
      await sleep(SUBMIT_DELAY_MS);
    }
  }

  const { chromium } = await loadPlaywright();
  const browser = await chromium.launch({ headless: HEADLESS, executablePath: chromePath() });
  try {
    const baseScreenshots = await captureUiEvidence(browser, records);
    writeJson("ui_screenshots.json", { baseScreenshots, records: records.map((r) => ({ scenario: r.scenario.number, ref: r.application_ref, screenshots: r.screenshots })) });
  } finally {
    await browser.close();
  }

  buildReports(context, records);
  console.log(`Evidence complete: ${ROOT}`);
}

main().catch((err) => {
  ensureDirs();
  writeJson("audit_runner_failure.json", {
    error: err.message,
    stack: err.stack,
    time: nowIso(),
  });
  console.error(err.stack || err.message);
  process.exit(1);
});
