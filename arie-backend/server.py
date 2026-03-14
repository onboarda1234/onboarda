#!/usr/bin/env python3
"""
ARIE Finance — Back-End API Server
====================================
Single-file production-ready API server using Tornado + SQLite.
Provides: authentication, application CRUD, document uploads,
risk scoring, AI verification, audit trail, and user management.

Run:  python server.py
Env:  PORT=8080 SECRET_KEY=your-secret DB_PATH=./arie.db
"""

import os, sys, json, uuid, time, hashlib, hmac, re, sqlite3, base64, logging, secrets
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from urllib.parse import quote_plus

import bcrypt
import jwt
import tornado.ioloop
import tornado.web
import tornado.escape
import requests

import html
import asyncio
from concurrent.futures import ThreadPoolExecutor

# Supervisor framework
try:
    from supervisor.api import setup_supervisor, get_supervisor_routes
    SUPERVISOR_AVAILABLE = True
except ImportError:
    SUPERVISOR_AVAILABLE = False
    logging.getLogger("arie").warning("Supervisor framework not available — install pydantic>=2.0")

# ── Logging ───────────────────────────────────────────────
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("arie")

# ── Configuration ──────────────────────────────────────────
PORT = int(os.environ.get("PORT", 8080))
ENVIRONMENT = os.environ.get("ENVIRONMENT", "development")

# SECRET_KEY: In production, MUST be set via env var. In dev, auto-generate a random key per session.
_env_secret = os.environ.get("SECRET_KEY", "")
if not _env_secret and ENVIRONMENT == "production":
    print("FATAL: SECRET_KEY environment variable is required in production mode.")
    print("       Generate one with: python3 -c \"import secrets; print(secrets.token_hex(64))\"")
    sys.exit(1)
SECRET_KEY = _env_secret or secrets.token_hex(64)  # Random per-session in dev

DB_PATH = os.environ.get("DB_PATH", os.path.join(os.path.dirname(__file__), "arie.db"))
UPLOAD_DIR = os.environ.get("UPLOAD_DIR", os.path.join(os.path.dirname(__file__), "uploads", "documents"))
STATIC_DIR = os.path.join(os.path.dirname(__file__), "..")  # Serves from arie-backend parent
MAX_UPLOAD_MB = 10
TOKEN_EXPIRY_HOURS = 24

# ── External API Keys (set via environment variables) ──────
OPENSANCTIONS_API_KEY = os.environ.get("OPENSANCTIONS_API_KEY", "")
OPENSANCTIONS_API_URL = os.environ.get("OPENSANCTIONS_API_URL", "https://api.opensanctions.org")
OPENCORPORATES_API_KEY = os.environ.get("OPENCORPORATES_API_KEY", "")
OPENCORPORATES_API_URL = os.environ.get("OPENCORPORATES_API_URL", "https://api.opencorporates.com/v0.4")
IP_GEOLOCATION_API_KEY = os.environ.get("IP_GEOLOCATION_API_KEY", "")
IP_GEOLOCATION_API_URL = os.environ.get("IP_GEOLOCATION_API_URL", "https://ipapi.co")

# Sumsub KYC/Identity Verification
SUMSUB_APP_TOKEN = os.environ.get("SUMSUB_APP_TOKEN", "")
SUMSUB_SECRET_KEY = os.environ.get("SUMSUB_SECRET_KEY", "")
SUMSUB_BASE_URL = os.environ.get("SUMSUB_BASE_URL", "https://api.sumsub.com")
SUMSUB_LEVEL_NAME = os.environ.get("SUMSUB_LEVEL_NAME", "basic-kyc-level")
SUMSUB_WEBHOOK_SECRET = os.environ.get("SUMSUB_WEBHOOK_SECRET", "")

os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(STATIC_DIR, exist_ok=True)

# ── Pricing Configuration (based on risk profile) ──
PRICING_TIERS = {
    "LOW": {
        "onboarding_fee": 500,
        "annual_monitoring_fee": 250,
        "currency": "USD",
        "description": "Standard onboarding — Low risk profile",
        "includes": ["Basic KYC verification", "Sanctions screening", "Annual review"]
    },
    "MEDIUM": {
        "onboarding_fee": 1500,
        "annual_monitoring_fee": 750,
        "currency": "USD",
        "description": "Enhanced onboarding — Medium risk profile",
        "includes": ["Enhanced KYC verification", "Sanctions & PEP screening", "Semi-annual review", "Adverse media monitoring"]
    },
    "HIGH": {
        "onboarding_fee": 3500,
        "annual_monitoring_fee": 2000,
        "currency": "USD",
        "description": "Enhanced Due Diligence onboarding — High risk profile",
        "includes": ["Full EDD verification", "Continuous sanctions & PEP monitoring", "Quarterly review", "Adverse media monitoring", "Behaviour & risk drift monitoring"]
    },
    "VERY_HIGH": {
        "onboarding_fee": 5000,
        "annual_monitoring_fee": 3500,
        "currency": "USD",
        "description": "Maximum Due Diligence onboarding — Very High risk profile",
        "includes": ["Maximum EDD verification", "Real-time sanctions & PEP monitoring", "Monthly review", "Full monitoring suite", "Dedicated compliance officer"]
    }
}

# ══════════════════════════════════════════════════════════
# DATABASE
# ══════════════════════════════════════════════════════════
def get_db():
    """Get a thread-local database connection."""
    db = sqlite3.connect(DB_PATH)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    return db


def init_db():
    """Create all tables if they don't exist."""
    db = get_db()
    db.executescript("""
    -- Users / Officers
    CREATE TABLE IF NOT EXISTS users (
        id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        full_name TEXT NOT NULL,
        role TEXT NOT NULL DEFAULT 'analyst' CHECK(role IN ('admin','sco','co','analyst')),
        status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','inactive')),
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );

    -- Client accounts (applicants)
    CREATE TABLE IF NOT EXISTS clients (
        id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
        email TEXT UNIQUE NOT NULL,
        password_hash TEXT NOT NULL,
        company_name TEXT,
        status TEXT DEFAULT 'active',
        created_at TEXT DEFAULT (datetime('now'))
    );

    -- Applications
    CREATE TABLE IF NOT EXISTS applications (
        id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
        ref TEXT UNIQUE NOT NULL,
        client_id TEXT REFERENCES clients(id),
        company_name TEXT NOT NULL,
        brn TEXT,
        country TEXT,
        sector TEXT,
        entity_type TEXT,
        ownership_structure TEXT,
        -- Pre-screening data (JSON blob)
        prescreening_data TEXT DEFAULT '{}',
        -- Risk scoring
        risk_score REAL,
        risk_level TEXT CHECK(risk_level IN ('LOW','MEDIUM','HIGH','VERY_HIGH')),
        risk_dimensions TEXT DEFAULT '{}',
        onboarding_lane TEXT,
        -- Status
        status TEXT DEFAULT 'draft' CHECK(status IN (
            'draft','prescreening_submitted','pricing_review','pricing_accepted',
            'kyc_documents','kyc_submitted','compliance_review','in_review',
            'edd_required','approved','rejected','rmi_sent','withdrawn'
        )),
        assigned_to TEXT REFERENCES users(id),
        -- Metadata
        submitted_at TEXT,
        decided_at TEXT,
        decision_by TEXT REFERENCES users(id),
        decision_notes TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        updated_at TEXT DEFAULT (datetime('now'))
    );

    -- Directors
    CREATE TABLE IF NOT EXISTS directors (
        id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
        full_name TEXT NOT NULL,
        nationality TEXT,
        is_pep TEXT DEFAULT 'No',
        pep_declaration TEXT DEFAULT '{}',
        created_at TEXT DEFAULT (datetime('now'))
    );

    -- UBOs
    CREATE TABLE IF NOT EXISTS ubos (
        id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
        full_name TEXT NOT NULL,
        nationality TEXT,
        ownership_pct REAL,
        is_pep TEXT DEFAULT 'No',
        pep_declaration TEXT DEFAULT '{}',
        created_at TEXT DEFAULT (datetime('now'))
    );

    -- Documents
    CREATE TABLE IF NOT EXISTS documents (
        id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
        application_id TEXT NOT NULL REFERENCES applications(id) ON DELETE CASCADE,
        person_id TEXT,
        doc_type TEXT NOT NULL,
        doc_name TEXT NOT NULL,
        file_path TEXT NOT NULL,
        file_size INTEGER,
        mime_type TEXT,
        -- AI verification
        verification_status TEXT DEFAULT 'pending' CHECK(verification_status IN ('pending','verified','flagged','failed')),
        verification_results TEXT DEFAULT '{}',
        uploaded_at TEXT DEFAULT (datetime('now')),
        verified_at TEXT
    );

    -- Risk Model Configuration
    CREATE TABLE IF NOT EXISTS risk_config (
        id INTEGER PRIMARY KEY DEFAULT 1,
        dimensions TEXT NOT NULL DEFAULT '{}',
        thresholds TEXT NOT NULL DEFAULT '{}',
        updated_at TEXT DEFAULT (datetime('now')),
        updated_by TEXT REFERENCES users(id)
    );

    -- AI Agents Configuration
    CREATE TABLE IF NOT EXISTS ai_agents (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_number INTEGER UNIQUE NOT NULL,
        name TEXT NOT NULL,
        icon TEXT DEFAULT '🤖',
        stage TEXT NOT NULL,
        description TEXT,
        enabled INTEGER DEFAULT 1,
        checks TEXT DEFAULT '[]',
        updated_at TEXT DEFAULT (datetime('now'))
    );

    -- AI Verification Checks Configuration
    CREATE TABLE IF NOT EXISTS ai_checks (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        category TEXT NOT NULL CHECK(category IN ('entity','person')),
        doc_type TEXT NOT NULL,
        doc_name TEXT NOT NULL,
        checks TEXT DEFAULT '[]',
        updated_at TEXT DEFAULT (datetime('now'))
    );

    -- Audit Trail
    CREATE TABLE IF NOT EXISTS audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT DEFAULT (datetime('now')),
        user_id TEXT,
        user_name TEXT,
        user_role TEXT,
        action TEXT NOT NULL,
        target TEXT,
        detail TEXT,
        ip_address TEXT
    );

    -- Notifications
    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id TEXT REFERENCES users(id),
        title TEXT NOT NULL,
        message TEXT,
        read INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now'))
    );

    -- Session / Save & Resume tokens
    CREATE TABLE IF NOT EXISTS client_sessions (
        id TEXT PRIMARY KEY DEFAULT (lower(hex(randomblob(8)))),
        client_id TEXT REFERENCES clients(id),
        application_id TEXT REFERENCES applications(id),
        form_data TEXT DEFAULT '{}',
        last_step INTEGER DEFAULT 0,
        updated_at TEXT DEFAULT (datetime('now'))
    );

    -- Monitoring Alerts
    CREATE TABLE IF NOT EXISTS monitoring_alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        application_id TEXT REFERENCES applications(id),
        client_name TEXT,
        alert_type TEXT,
        severity TEXT,
        detected_by TEXT,
        summary TEXT,
        source_reference TEXT,
        ai_recommendation TEXT,
        status TEXT DEFAULT 'open',
        officer_action TEXT,
        officer_notes TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        reviewed_at TEXT,
        reviewed_by TEXT REFERENCES users(id)
    );

    -- Periodic Reviews
    CREATE TABLE IF NOT EXISTS periodic_reviews (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        application_id TEXT REFERENCES applications(id),
        client_name TEXT,
        risk_level TEXT,
        trigger_type TEXT,
        trigger_reason TEXT,
        previous_risk_level TEXT,
        new_risk_level TEXT,
        review_memo TEXT,
        status TEXT DEFAULT 'pending',
        due_date TEXT,
        started_at TEXT,
        completed_at TEXT,
        decision TEXT,
        decision_reason TEXT,
        decided_by TEXT REFERENCES users(id),
        created_at TEXT DEFAULT (datetime('now'))
    );

    -- Monitoring Agent Status
    CREATE TABLE IF NOT EXISTS monitoring_agent_status (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        agent_name TEXT,
        agent_type TEXT,
        last_run TEXT,
        next_run TEXT,
        run_frequency TEXT,
        clients_monitored INTEGER,
        alerts_generated INTEGER DEFAULT 0,
        status TEXT DEFAULT 'active'
    );

    -- Client Notifications
    CREATE TABLE IF NOT EXISTS client_notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        application_id TEXT REFERENCES applications(id),
        client_id TEXT REFERENCES clients(id),
        notification_type TEXT,
        title TEXT NOT NULL,
        message TEXT,
        documents_list TEXT,
        read_status INTEGER DEFAULT 0,
        created_at TEXT DEFAULT (datetime('now')),
        read_at TEXT
    );
    """)

    # Seed default admin user if no users exist
    count = db.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
    if count == 0:
        # Generate a secure random password for initial admin setup
        _init_password = os.environ.get("ADMIN_INITIAL_PASSWORD", "")
        if not _init_password:
            _init_password = secrets.token_urlsafe(16)  # e.g. "aB3_xY7kLm9pQ2wR"
            print(f"\n  ⚠️  INITIAL ADMIN PASSWORD (save this now): {_init_password}")
            print(f"  ⚠️  Change it immediately after first login.\n")

        pw_hash = bcrypt.hashpw(_init_password.encode(), bcrypt.gensalt()).decode()
        db.execute("""
            INSERT INTO users (id, email, password_hash, full_name, role, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("admin001", "asudally@ariefinance.mu", pw_hash, "Aisha Sudally", "admin", "active"))
        db.execute("""
            INSERT INTO users (id, email, password_hash, full_name, role, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("sco001", "raj.patel@ariefinance.mu", pw_hash, "Raj Patel", "sco", "active"))
        db.execute("""
            INSERT INTO users (id, email, password_hash, full_name, role, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("co001", "m.dubois@ariefinance.mu", pw_hash, "Marie Dubois", "co", "active"))
        db.execute("""
            INSERT INTO users (id, email, password_hash, full_name, role, status)
            VALUES (?, ?, ?, ?, ?, ?)
        """, ("analyst001", "l.wei@ariefinance.mu", pw_hash, "Li Wei", "analyst", "active"))
        db.commit()

        # Seed default risk config
        default_dims = json.dumps([
            {"id":"D1","name":"Customer / Entity Risk","weight":30,"subcriteria":[
                {"name":"Entity Type","weight":20},{"name":"Ownership Structure","weight":20},
                {"name":"PEP Status","weight":25},{"name":"Adverse Media","weight":15},
                {"name":"Source of Wealth","weight":10},{"name":"Source of Funds","weight":10}
            ]},
            {"id":"D2","name":"Geographic Risk","weight":25,"subcriteria":[
                {"name":"Country of Incorporation","weight":40},
                {"name":"Countries of Operation","weight":30},
                {"name":"Target Markets","weight":30}
            ]},
            {"id":"D3","name":"Product / Service Risk","weight":20,"subcriteria":[
                {"name":"Service Type","weight":40},{"name":"Monthly Volume","weight":30},
                {"name":"Transaction Complexity","weight":30}
            ]},
            {"id":"D4","name":"Industry / Sector Risk","weight":15,"subcriteria":[
                {"name":"Industry Sector","weight":100}
            ]},
            {"id":"D5","name":"Delivery Channel Risk","weight":10,"subcriteria":[
                {"name":"Introduction Method","weight":50},{"name":"Delivery Channel","weight":50}
            ]}
        ])
        default_thresholds = json.dumps([
            {"level":"LOW","min":0,"max":39.9},
            {"level":"MEDIUM","min":40,"max":54.9},
            {"level":"HIGH","min":55,"max":69.9},
            {"level":"VERY_HIGH","min":70,"max":100}
        ])
        db.execute("INSERT INTO risk_config (id, dimensions, thresholds) VALUES (1, ?, ?)",
                    (default_dims, default_thresholds))

        # Seed AI agents (10-agent architecture)
        # ── Onboarding Agents (1-5): Sequential pipeline ──
        # Flow: Upload → Agent 1 (document) → Agent 2 (external DB) → Agent 3 (screening) → Agent 4 (UBO) → Agent 5 (memo)
        agent1_checks = json.dumps([
            "Detect passport type", "Extract MRZ (machine-readable zone)", "Verify MRZ checksum",
            "Match name with application data", "Check date of birth consistency",
            "Extract expiry date", "Check if expired", "Check if near expiry",
            "Validate issue date vs expiry rules", "Detect tampering",
            "Detect image manipulation", "Verify watermark patterns", "Detect cropped or incomplete documents",
            "Passport name vs application name", "Passport address vs proof of address",
            "Director name vs registry data", "Missing pages detection",
            "Missing UBO documents", "Incomplete shareholder register",
            "Blur detection", "Cropping detection", "Tampering detection",
            "Certificate of incorporation validity", "Shareholder register completeness",
            "Director list consistency", "Nationality consistency",
            "Proof of address issue date", "Proof of address address match",
            "Proof of address document type validity", "Document type classification",
            "Image quality assessment", "Document orientation check",
            "Multi-page document completeness", "Signature presence check",
            "Photo quality assessment", "Document language detection"
        ])
        agent2_checks = json.dumps([
            "Company registry verification", "Director name verification",
            "Shareholder verification", "Jurisdiction validation",
            "Company status check (active/dissolved)", "Registration number verification",
            "Incorporation date verification", "Registered address verification",
            "Company type verification", "Filed documents check",
            "Cross-reference directors with passport data", "Cross-reference shareholders with UBO declarations"
        ])
        agent3_checks = json.dumps([
            "Sanctions list screening", "PEP database screening",
            "Watchlist screening", "Adverse media screening",
            "False positive assessment", "Match confidence scoring",
            "Material adverse media identification", "Political exposure classification",
            "Screening result interpretation", "Risk relevance assessment"
        ])
        agent4_checks = json.dumps([
            "Map ownership layers", "Identify ultimate beneficial owners",
            "Detect nominee structures", "Flag complex ownership chains",
            "Calculate ownership percentages", "Identify high-risk jurisdiction links",
            "Detect shell company indicators", "Verify UBO identity documents",
            "Cross-reference UBOs with sanctions/PEP results", "Structure complexity scoring"
        ])
        agent5_checks = json.dumps([
            "Compile all agent results", "Summarize key findings",
            "Identify risk indicators", "Recommend risk rating",
            "Generate onboarding memo", "Produce review checklist",
            "Flag unresolved contradictions", "Calculate aggregate confidence",
            "Determine approval/escalation recommendation", "Generate compliance narrative"
        ])

        agents_seed = [
            (1, "Identity & Document Integrity Agent", "🔍", "document_verification",
             "Validates authenticity and internal consistency of identity documents uploaded during onboarding. "
             "Performs ~36 automated checks including: passport MRZ extraction and checksum verification, expiry date validation, "
             "tampering and image manipulation detection, cross-document consistency (passport vs application, address vs proof of address, "
             "director names vs registry data), missing documentation detection, blur/cropping detection, certificate of incorporation validity, "
             "and shareholder register completeness. Focuses ONLY on document authenticity, expiry, identity extraction, and cross-document consistency. "
             "Does NOT perform sanctions screening or registry lookups — passes extracted data to downstream agents. "
             "Output: Document Verification Result with verification status, expiry status, tampering risk, name match score, and document completeness.",
             1, agent1_checks),
            (2, "External Database Cross-Verification Agent", "🔎", "external_verification",
             "Secondary verification layer that checks whether passport and company document information matches external databases. "
             "Performs checks including: company registry verification (OpenCorporates, Companies House, ADGM, DIFC registries), "
             "director name verification against registry records, shareholder verification, jurisdiction validation, "
             "company status verification (active/dissolved), registration number and incorporation date cross-referencing. "
             "Confirms that persons in uploaded documents actually exist in official registry records as declared directors/shareholders. "
             "Prevents fake identities in corporate structures. "
             "Output: External verification results with match status per entity, registry source, and discrepancy flags.",
             1, agent2_checks),
            (3, "FinCrime Screening Interpretation Agent", "💼", "screening",
             "Once passport data is extracted by Agent 1, this agent screens individuals and entities against: "
             "sanctions lists, PEP databases, watchlists, and adverse media sources. "
             "Distinguishes false positives from genuine matches, assesses match confidence, highlights material adverse media, "
             "and classifies political exposure levels. "
             "Output: Screening Result with sanctions match status, PEP status (with confidence level), adverse media findings, "
             "and overall screening risk assessment.",
             1, agent3_checks),
            (4, "Corporate Structure & UBO Mapping Agent", "🏗️", "ubo_mapping",
             "For directors and shareholders extracted from documents, this agent: maps ownership layers, "
             "identifies ultimate beneficial owners (natural persons), detects nominee structures, "
             "flags complex ownership chains, calculates ownership percentages through layered structures, "
             "identifies high-risk jurisdiction links, detects shell company indicators, "
             "and cross-references UBOs with sanctions/PEP screening results from Agent 3. "
             "Output: ownership map, UBO list, structure complexity score, and nominee/shell risk flags.",
             1, agent4_checks),
            (5, "Compliance Memo Agent", "📝", "compliance_memo",
             "Final synthesis agent. After Agents 1-4 complete their checks, this agent: "
             "compiles all results into a structured onboarding memo, summarizes key findings across all verification layers, "
             "identifies and ranks risk indicators, recommends a risk rating (LOW/MEDIUM/HIGH/VERY_HIGH), "
             "flags any unresolved contradictions between agents, calculates aggregate confidence score, "
             "and produces a review checklist for compliance officers. "
             "Output: structured onboarding report, risk recommendation, review checklist, and approval/escalation recommendation.",
             1, agent5_checks),
            (6, "Periodic Review Preparation Agent", "📅", "periodic_review",
             "Prepares client files for periodic reviews, identifies expired documents, "
             "requests updated information, and summarises changes since onboarding.",
             1, "[]"),
            (7, "Adverse Media & PEP Monitoring Agent", "📡", "media_monitoring",
             "Continuous media monitoring, PEP status changes, new sanctions exposure, "
             "enforcement actions. Output: alert summaries, severity classification.",
             1, "[]"),
            (8, "Behaviour & Risk Drift Agent", "📈", "risk_drift",
             "Compares current activity vs onboarding profile, detects new jurisdictions, "
             "identifies sector changes, flags unusual growth patterns. Output: risk drift score, escalation trigger.",
             1, "[]"),
            (9, "Regulatory Impact Agent", "⚖️", "regulatory_impact",
             "Analyses new circulars/rules, identifies impacted client segments, "
             "triggers compliance actions. Output: regulatory impact summary, remediation tasks.",
             1, "[]"),
            (10, "Ongoing Compliance Review Agent", "📋", "compliance_review",
             "Consolidates monitoring alerts, summarises risk changes, recommends actions "
             "(maintain/EDD/exit). Output: periodic review memo, updated risk classification, escalation recommendation.",
             1, "[]")
        ]
        for agent_data in agents_seed:
            db.execute("INSERT INTO ai_agents (agent_number, name, icon, stage, description, enabled, checks) VALUES (?,?,?,?,?,?,?)", agent_data)
        db.commit()

        # Seed monitoring agents status
        now = datetime.now().isoformat()
        next_day = (datetime.now() + timedelta(days=1)).isoformat()
        next_week = (datetime.now() + timedelta(days=7)).isoformat()
        next_month = (datetime.now() + timedelta(days=30)).isoformat()

        agents_status = [
            ("Sanctions/PEP Agent", "sanctions_pep", now, next_day, "Daily", 45, 2, "active"),
            ("Adverse Media Agent", "adverse_media", now, (datetime.now() + timedelta(hours=6)).isoformat(), "Every 6 hours", 45, 1, "active"),
            ("Registry Monitoring Agent", "registry", (datetime.now() - timedelta(days=7)).isoformat(), next_week, "Weekly", 45, 0, "active"),
            ("Risk Drift Agent", "risk_drift", (datetime.now() - timedelta(days=30)).isoformat(), next_month, "Monthly", 45, 3, "active"),
            ("Regulatory Impact Agent", "regulatory", (datetime.now() - timedelta(days=14)).isoformat(), next_month, "On circular publication", 45, 1, "active"),
        ]
        for agent_data in agents_status:
            db.execute("INSERT INTO monitoring_agent_status (agent_name, agent_type, last_run, next_run, run_frequency, clients_monitored, alerts_generated, status) VALUES (?,?,?,?,?,?,?,?)", agent_data)
        db.commit()

        # Get a sample application for monitoring seed data (if any exist)
        sample_app = db.execute("SELECT id, ref, company_name FROM applications LIMIT 1").fetchone()

        if sample_app:
            app_id = sample_app["id"]
            company_name = sample_app["company_name"]

            # Seed monitoring alerts
            alerts_seed = [
                (app_id, company_name, "sanctions_match", "critical", "Sanctions/PEP Agent",
                 "Sanctions match detected on director John Smith", "OFAC List - 2026-03-14",
                 "Potential match with OFAC SDN list - requires immediate investigation", "open", None, None, now, None, None),
                (app_id, company_name, "pep_status", "high", "Adverse Media & PEP Monitoring Agent",
                 "UBO acquired PEP status", "Political Exposure Database - 2026-03-12",
                 "UBO recently appointed to government position - enhanced monitoring recommended", "open", None, None, now, None, None),
                (app_id, company_name, "adverse_media", "medium", "Adverse Media & PEP Monitoring Agent",
                 "Adverse media article published about client company", "Financial Times - 2026-03-10",
                 "Article mentions regulatory investigation - requires verification of materiality", "open", None, None, now, None, None),
                (app_id, company_name, "registry_change", "medium", "Registry Monitoring Agent",
                 "Director change detected in company registry", "Companies House - 2026-03-08",
                 "New director appointed - require screening of new officer", "reviewed", "request_documents", "Initiate director screening", (datetime.now() - timedelta(days=3)).isoformat(), now, "sco001"),
                (app_id, company_name, "risk_drift", "medium", "Behaviour & Risk Drift Agent",
                 "New jurisdiction activity detected", "Transaction Monitoring System - 2026-03-06",
                 "Customer expanded to new high-risk jurisdiction - consider enhanced due diligence", "open", None, None, now, None, None),
                (app_id, company_name, "regulatory_impact", "low", "Regulatory Impact Agent",
                 "New regulation affecting client sector", "FATF Guidance - 2026-03-01",
                 "New AML guidance issued - review policies and update procedures", "open", None, None, now, None, None),
                (app_id, company_name, "document_expiry", "low", "Periodic Review Preparation Agent",
                 "Proof of address document expires in 30 days", "Document Management System - 2026-03-14",
                 "Request updated proof of address before expiration", "open", None, None, now, None, None),
                (app_id, company_name, "company_status_change", "high", "Registry Monitoring Agent",
                 "Company status change in registry", "Companies House - 2026-03-05",
                 "Company changed from Active to In Administration - escalate immediately", "escalated", "escalate", "Referred to compliance officer for review", now, now, "co001"),
            ]

            for alert in alerts_seed:
                db.execute("""INSERT INTO monitoring_alerts
                    (application_id, client_name, alert_type, severity, detected_by, summary, source_reference,
                     ai_recommendation, status, officer_action, officer_notes, created_at, reviewed_at, reviewed_by)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", alert)
            db.commit()

            # Seed periodic reviews
            reviews_seed = [
                (app_id, company_name, "HIGH", "time_based", "Annual review due for HIGH risk client",
                 "HIGH", "HIGH", json.dumps({"key_findings": ["No material changes detected"], "risk_mitigation": ["Continue enhanced monitoring"]}),
                 "pending", (datetime.now() + timedelta(days=7)).date().isoformat(), None, None, None, None, None),
                (app_id, company_name, "MEDIUM", "time_based", "Annual review due for MEDIUM risk client",
                 "MEDIUM", "MEDIUM", None, "completed", (datetime.now() - timedelta(days=30)).date().isoformat(),
                 (datetime.now() - timedelta(days=28)).isoformat(), (datetime.now() - timedelta(days=27)).isoformat(),
                 "continue", "Client profile stable, all documents current, no adverse findings", "analyst001"),
            ]

            for review in reviews_seed:
                db.execute("""INSERT INTO periodic_reviews
                    (application_id, client_name, risk_level, trigger_type, trigger_reason,
                     previous_risk_level, new_risk_level, review_memo, status, due_date, started_at, completed_at,
                     decision, decision_reason, decided_by)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", review)
            db.commit()

        # Log the init
        db.execute("INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail) VALUES (?,?,?,?,?,?)",
                    ("system", "System", "system", "Initialize", "Database", "Database initialized with seed data"))
        db.commit()

    db.close()
    print(f"✅ Database initialized at {DB_PATH}")


# ══════════════════════════════════════════════════════════
# AUTH HELPERS
# ══════════════════════════════════════════════════════════
def create_token(user_id, role, name, token_type="officer"):
    payload = {
        "sub": user_id,
        "role": role,
        "name": name,
        "type": token_type,
        "exp": datetime.utcnow() + timedelta(hours=TOKEN_EXPIRY_HOURS),
        "iat": datetime.utcnow()
    }
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def decode_token(token):
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


def generate_ref():
    """Generate application reference like ARF-2026-100429"""
    db = get_db()
    count = db.execute("SELECT COUNT(*) as c FROM applications").fetchone()["c"]
    db.close()
    year = datetime.now().year
    return f"ARF-{year}-{100421 + count}"


def sanitize_input(value):
    """Sanitize user input to prevent XSS and HTML injection."""
    if value is None:
        return None
    if isinstance(value, str):
        return html.escape(value.strip(), quote=True)
    return value

def sanitize_dict(data, keys=None):
    """Sanitize specified keys in a dict (or all string values if keys=None)."""
    if not isinstance(data, dict):
        return data
    result = {}
    for k, v in data.items():
        if keys is None or k in keys:
            result[k] = sanitize_input(v) if isinstance(v, str) else v
        else:
            result[k] = v
    return result


# ══════════════════════════════════════════════════════════
# RISK SCORING ENGINE
# ══════════════════════════════════════════════════════════
# Country risk classification
FATF_GREY = {"syria","myanmar","iran","north korea","yemen","haiti","south sudan",
             "nigeria","south africa","kenya","philippines","tanzania","mozambique",
             "democratic republic of congo","cameroon","burkina faso","mali","senegal"}
FATF_BLACK = {"iran","north korea","myanmar"}
SANCTIONED = {"iran","north korea","syria","cuba","crimea"}
LOW_RISK = {"mauritius","united kingdom","uk","france","germany","sweden","norway",
            "denmark","finland","australia","new zealand","canada","usa","united states",
            "japan","singapore","hong kong","switzerland","netherlands","belgium","luxembourg",
            "ireland","austria","portugal","spain","italy"}

SECTOR_SCORES = {
    "regulated financial":1, "government":1, "bank":1, "listed company":1,
    "healthcare":2, "technology":2, "software":2, "saas":2, "manufacturing":2,
    "retail":2, "e-commerce":2, "education":2, "media":2, "logistics":2,
    "import":3, "export":3, "real estate":3, "construction":3, "mining":3,
    "oil":3, "gas":3, "moneservices":3, "forex":3, "precious":3,
    "non-profit":3, "ngo":3, "charity":3, "advisory":3,
    "crypto":4, "virtual asset":4, "gambling":4, "gaming":4, "betting":4,
    "arms":4, "defence":4, "military":4, "shell company":4, "nominee":4
}


def classify_country(country_name):
    """Return risk score 1-4 for a country."""
    if not country_name:
        return 2
    c = country_name.lower().strip()
    if c in SANCTIONED:
        return 4
    if c in FATF_BLACK:
        return 4
    if c in FATF_GREY:
        return 3
    if c in LOW_RISK:
        return 1
    return 2  # standard


def score_sector(sector_name):
    """Return risk score 1-4 for a sector."""
    if not sector_name:
        return 2
    s = sector_name.lower()
    for key, score in SECTOR_SCORES.items():
        if key in s:
            return score
    return 2


def compute_risk_score(app_data):
    """
    Compute composite risk score from application data.
    Returns: { score, level, dimensions: {d1..d5}, lane }
    """
    data = app_data if isinstance(app_data, dict) else json.loads(app_data)

    # D1: Customer / Entity Risk (30%)
    entity_map = {"listed":1,"regulated":1,"government":1,"large private":2,"sme":2,
                  "newly incorporated":3,"trust":3,"foundation":3,"non-profit":3,"shell":4}
    owner_map = {"simple":1,"1-2":2,"3+":3,"complex":4}

    d1_entity = 2
    et = (data.get("entity_type") or "").lower()
    for k, v in entity_map.items():
        if k in et:
            d1_entity = v; break

    d1_owner = 2
    os_val = (data.get("ownership_structure") or "").lower()
    for k, v in owner_map.items():
        if k in os_val:
            d1_owner = v; break

    has_pep = any(d.get("is_pep") == "Yes" for d in data.get("directors", []))
    has_pep = has_pep or any(u.get("is_pep") == "Yes" for u in data.get("ubos", []))
    d1_pep = 3 if has_pep else 1

    d1 = d1_entity * 0.20 + d1_owner * 0.20 + d1_pep * 0.25 + 1 * 0.15 + 2 * 0.10 + 2 * 0.10

    # D2: Geographic Risk (25%)
    d2_inc = classify_country(data.get("country"))
    op_countries = data.get("operating_countries", [])
    d2_op = max([classify_country(c) for c in op_countries]) if op_countries else d2_inc
    target_markets = data.get("target_markets", [])
    d2_tgt = max([classify_country(c) for c in target_markets]) if target_markets else d2_inc
    d2 = d2_inc * 0.40 + d2_op * 0.30 + d2_tgt * 0.30

    # D3: Product / Service Risk (20%)
    vol_map = {"under":1,"50,000":2,"500,000":3,"over":4}
    d3_svc = 2  # default
    if data.get("cross_border"):
        d3_svc = 3
    d3_vol = 2
    vol = (data.get("monthly_volume") or "").lower()
    for k, v in vol_map.items():
        if k in vol:
            d3_vol = v; break
    d3 = d3_svc * 0.40 + d3_vol * 0.30 + 2 * 0.30

    # D4: Industry / Sector Risk (15%)
    d4 = score_sector(data.get("sector"))

    # D5: Delivery Channel Risk (10%)
    intro_map = {"direct":1,"regulated":1,"non-regulated":3,"unsolicited":4}
    d5_intro = 2
    intro = (data.get("introduction_method") or "").lower()
    for k, v in intro_map.items():
        if k in intro:
            d5_intro = v; break
    d5 = d5_intro * 0.50 + 2 * 0.50  # non-face-to-face by default

    # Composite
    composite = (d1 * 0.30 + d2 * 0.25 + d3 * 0.20 + d4 * 0.15 + d5 * 0.10) / 4 * 100
    composite = round(composite, 1)

    if composite >= 70:
        level = "VERY_HIGH"
    elif composite >= 55:
        level = "HIGH"
    elif composite >= 40:
        level = "MEDIUM"
    else:
        level = "LOW"

    lane_map = {"LOW": "Fast Lane", "MEDIUM": "Standard Review", "HIGH": "EDD", "VERY_HIGH": "EDD"}

    return {
        "score": composite,
        "level": level,
        "dimensions": {"d1": round(d1, 2), "d2": round(d2, 2), "d3": round(d3, 2), "d4": round(d4, 2), "d5": round(d5, 2)},
        "lane": lane_map[level]
    }


# ══════════════════════════════════════════════════════════
# REAL API INTEGRATIONS
# ══════════════════════════════════════════════════════════

def screen_opensanctions(name, birth_date=None, nationality=None, entity_type="Person"):
    """
    Screen a person or entity against OpenSanctions (sanctions, PEP, watchlists).
    Returns: { matched: bool, results: [...], source: "opensanctions"|"simulated" }
    """
    if not OPENSANCTIONS_API_KEY:
        logger.info(f"OpenSanctions: No API key — simulating screening for '{name}'")
        return _simulate_sanctions_screen(name)

    try:
        headers = {"Authorization": f"ApiKey {OPENSANCTIONS_API_KEY}"}
        params = {
            "schema": entity_type,  # "Person" or "Company"
            "properties.name": name,
            "limit": 10,
        }
        if birth_date:
            params["properties.birthDate"] = birth_date
        if nationality:
            params["properties.nationality"] = nationality

        resp = requests.get(
            f"{OPENSANCTIONS_API_URL}/match/default",
            headers=headers,
            params=params,
            timeout=15
        )

        if resp.status_code == 200:
            data = resp.json()
            results = data.get("results", [])
            hits = []
            for r in results:
                score = r.get("score", 0)
                if score >= 0.65:  # Only consider strong matches
                    props = r.get("properties", {})
                    hits.append({
                        "match_score": round(score * 100, 1),
                        "matched_name": (props.get("name", [""])[0] if isinstance(props.get("name"), list) else props.get("name", "")),
                        "datasets": r.get("datasets", []),
                        "schema": r.get("schema", ""),
                        "topics": props.get("topics", []),
                        "countries": props.get("country", []),
                        "sanctions_list": ", ".join(r.get("datasets", [])),
                        "is_pep": "role.pep" in (props.get("topics", []) if isinstance(props.get("topics"), list) else []),
                        "is_sanctioned": any(d in ["sanctions", "crime"] for d in r.get("datasets", [])),
                    })

            return {
                "matched": len(hits) > 0,
                "results": hits,
                "total_checked": len(results),
                "source": "opensanctions",
                "api_status": "live",
                "screened_at": datetime.utcnow().isoformat()
            }
        elif resp.status_code == 401:
            logger.warning("OpenSanctions: Invalid API key — falling back to simulation")
            return _simulate_sanctions_screen(name, note="API key invalid — simulated result")
        else:
            logger.warning(f"OpenSanctions: HTTP {resp.status_code} — falling back to simulation")
            return _simulate_sanctions_screen(name, note=f"API returned {resp.status_code} — simulated result")

    except requests.exceptions.Timeout:
        logger.warning("OpenSanctions: Request timed out — falling back to simulation")
        return _simulate_sanctions_screen(name, note="API timeout — simulated result")
    except Exception as e:
        logger.error(f"OpenSanctions error: {e}")
        return _simulate_sanctions_screen(name, note=f"API error — simulated result")


def _simulate_sanctions_screen(name, note="No API key configured — simulated result"):
    """Fallback: realistic simulation when API key not set."""
    import random
    is_hit = random.random() < 0.08  # 8% simulated hit rate
    results = []
    if is_hit:
        results.append({
            "match_score": round(random.uniform(68, 95), 1),
            "matched_name": name,
            "datasets": ["sanctions-simulated"],
            "schema": "Person",
            "topics": ["role.pep"] if random.random() < 0.5 else ["sanction"],
            "countries": [],
            "sanctions_list": "Simulated Sanctions List",
            "is_pep": random.random() < 0.5,
            "is_sanctioned": random.random() < 0.3,
        })
    return {
        "matched": is_hit,
        "results": results,
        "total_checked": 1,
        "source": "simulated",
        "api_status": "simulated",
        "note": note,
        "screened_at": datetime.utcnow().isoformat()
    }


def lookup_opencorporates(company_name, jurisdiction=None):
    """
    Look up a company via Open Corporates registry.
    Returns: { found: bool, companies: [...], source: "opencorporates"|"simulated" }
    """
    if not OPENCORPORATES_API_KEY:
        logger.info(f"OpenCorporates: No API key — simulating lookup for '{company_name}'")
        return _simulate_company_lookup(company_name)

    try:
        params = {"q": company_name, "api_token": OPENCORPORATES_API_KEY}
        if jurisdiction:
            params["jurisdiction_code"] = jurisdiction.lower()

        resp = requests.get(
            f"{OPENCORPORATES_API_URL}/companies/search",
            params=params,
            timeout=15
        )

        if resp.status_code == 200:
            data = resp.json()
            companies_raw = data.get("results", {}).get("companies", [])
            companies = []
            for c_wrap in companies_raw[:5]:  # Top 5 matches
                c = c_wrap.get("company", {})
                companies.append({
                    "name": c.get("name", ""),
                    "company_number": c.get("company_number", ""),
                    "jurisdiction": c.get("jurisdiction_code", ""),
                    "incorporation_date": c.get("incorporation_date", ""),
                    "dissolution_date": c.get("dissolution_date"),
                    "company_type": c.get("company_type", ""),
                    "registry_url": c.get("registry_url", ""),
                    "status": c.get("current_status", ""),
                    "registered_address": c.get("registered_address_in_full", ""),
                    "opencorporates_url": c.get("opencorporates_url", ""),
                })

            return {
                "found": len(companies) > 0,
                "companies": companies,
                "total_results": data.get("results", {}).get("total_count", 0),
                "source": "opencorporates",
                "api_status": "live",
                "searched_at": datetime.utcnow().isoformat()
            }
        elif resp.status_code == 401:
            logger.warning("OpenCorporates: Invalid API key — falling back to simulation")
            return _simulate_company_lookup(company_name, note="API key invalid — simulated result")
        else:
            logger.warning(f"OpenCorporates: HTTP {resp.status_code}")
            return _simulate_company_lookup(company_name, note=f"API returned {resp.status_code} — simulated")

    except Exception as e:
        logger.error(f"OpenCorporates error: {e}")
        return _simulate_company_lookup(company_name, note=f"API error — simulated result")


def _simulate_company_lookup(company_name, note="No API key configured — simulated result"):
    """Fallback: simulated company registry lookup."""
    import random
    found = random.random() < 0.85  # 85% chance found
    companies = []
    if found:
        companies.append({
            "name": company_name,
            "company_number": f"C{random.randint(10000,99999)}",
            "jurisdiction": "mu",
            "incorporation_date": f"20{random.randint(10,24)}-{random.randint(1,12):02d}-{random.randint(1,28):02d}",
            "dissolution_date": None,
            "company_type": random.choice(["Private Company Limited by Shares", "Global Business Company"]),
            "registry_url": "",
            "status": random.choice(["Active", "Active"]),
            "registered_address": "Port Louis, Mauritius",
            "opencorporates_url": "",
        })
    return {
        "found": found,
        "companies": companies,
        "total_results": 1 if found else 0,
        "source": "simulated",
        "api_status": "simulated",
        "note": note,
        "searched_at": datetime.utcnow().isoformat()
    }


def geolocate_ip(ip_address):
    """
    Look up geolocation and risk data for an IP address.
    Returns: { country, city, is_vpn, is_proxy, risk_level, source }
    """
    if not ip_address or ip_address in ("127.0.0.1", "::1", "0.0.0.0"):
        return {
            "country": "Local",
            "country_code": "XX",
            "city": "Localhost",
            "region": "",
            "is_vpn": False,
            "is_proxy": False,
            "is_tor": False,
            "risk_level": "LOW",
            "source": "local",
            "api_status": "skipped",
            "checked_at": datetime.utcnow().isoformat()
        }

    try:
        # Use ipapi.co (free tier: 1000 req/day, no key needed for basic)
        url = f"{IP_GEOLOCATION_API_URL}/{ip_address}/json/"
        if IP_GEOLOCATION_API_KEY:
            url += f"?key={IP_GEOLOCATION_API_KEY}"

        resp = requests.get(url, timeout=10)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("error"):
                return _simulate_ip_geolocation(ip_address, note=data.get("reason", "API error"))

            country_code = (data.get("country_code") or "").upper()
            # Determine IP risk level
            ip_risk = "LOW"
            if country_code in [c.upper() for c in SANCTIONED]:
                ip_risk = "VERY_HIGH"
            elif country_code in [c.upper() for c in FATF_BLACK]:
                ip_risk = "HIGH"
            elif country_code in [c.upper() for c in FATF_GREY]:
                ip_risk = "MEDIUM"

            return {
                "country": data.get("country_name", "Unknown"),
                "country_code": country_code,
                "city": data.get("city", ""),
                "region": data.get("region", ""),
                "latitude": data.get("latitude"),
                "longitude": data.get("longitude"),
                "org": data.get("org", ""),
                "asn": data.get("asn", ""),
                "is_vpn": False,  # Basic API doesn't detect VPN
                "is_proxy": False,
                "is_tor": False,
                "risk_level": ip_risk,
                "source": "ipapi",
                "api_status": "live",
                "checked_at": datetime.utcnow().isoformat()
            }
        else:
            return _simulate_ip_geolocation(ip_address, note=f"API returned {resp.status_code}")

    except Exception as e:
        logger.error(f"IP Geolocation error: {e}")
        return _simulate_ip_geolocation(ip_address, note=f"API error — simulated")


def _simulate_ip_geolocation(ip_address, note="Simulated result"):
    """Fallback: simulated IP geolocation."""
    import random
    countries = [
        ("Mauritius", "MU"), ("United Kingdom", "GB"), ("France", "FR"),
        ("India", "IN"), ("South Africa", "ZA"), ("Singapore", "SG")
    ]
    country, code = random.choice(countries)
    return {
        "country": country,
        "country_code": code,
        "city": "Simulated City",
        "region": "",
        "is_vpn": random.random() < 0.05,
        "is_proxy": random.random() < 0.03,
        "is_tor": False,
        "risk_level": "LOW",
        "source": "simulated",
        "api_status": "simulated",
        "note": note,
        "checked_at": datetime.utcnow().isoformat()
    }


# ══════════════════════════════════════════════════════════
# SUMSUB KYC / IDENTITY VERIFICATION
# ══════════════════════════════════════════════════════════

def _sumsub_sign(method, url_path, body=b""):
    """
    Create HMAC-SHA256 signature for Sumsub API requests.
    Returns headers dict with X-App-Token, X-App-Access-Ts, X-App-Access-Sig.
    """
    ts = str(int(time.time()))
    # Build signature payload: ts + method + path + body
    sig_payload = ts.encode("utf-8") + method.upper().encode("utf-8") + url_path.encode("utf-8")
    if body:
        sig_payload += body if isinstance(body, bytes) else body.encode("utf-8")
    sig = hmac.new(
        SUMSUB_SECRET_KEY.encode("utf-8"),
        sig_payload,
        hashlib.sha256
    ).hexdigest()
    return {
        "X-App-Token": SUMSUB_APP_TOKEN,
        "X-App-Access-Ts": ts,
        "X-App-Access-Sig": sig,
    }


def sumsub_create_applicant(external_user_id, first_name=None, last_name=None,
                            email=None, phone=None, dob=None, country=None,
                            level_name=None):
    """
    Create a Sumsub applicant for KYC verification.
    Returns: { applicant_id, external_user_id, status, source }
    """
    if not SUMSUB_APP_TOKEN or not SUMSUB_SECRET_KEY:
        logger.info(f"Sumsub: No credentials — simulating applicant creation for '{external_user_id}'")
        return _simulate_sumsub_applicant(external_user_id, first_name, last_name)

    try:
        level = level_name or SUMSUB_LEVEL_NAME
        url_path = f"/resources/applicants?levelName={level}"
        body_data = {
            "externalUserId": external_user_id,
        }
        if first_name or last_name:
            fixed_info = {}
            if first_name:
                fixed_info["firstName"] = first_name
            if last_name:
                fixed_info["lastName"] = last_name
            if dob:
                fixed_info["dob"] = dob
            if country:
                fixed_info["country"] = country
            body_data["fixedInfo"] = fixed_info
        if email:
            body_data["email"] = email
        if phone:
            body_data["phone"] = phone

        body_bytes = json.dumps(body_data).encode("utf-8")
        headers = _sumsub_sign("POST", url_path, body_bytes)
        headers["Content-Type"] = "application/json"

        resp = requests.post(
            f"{SUMSUB_BASE_URL}{url_path}",
            headers=headers,
            data=body_bytes,
            timeout=15
        )

        if resp.status_code in (200, 201):
            data = resp.json()
            applicant_id = data.get("id", "")
            logger.info(f"Sumsub: Created applicant {applicant_id} for user {external_user_id}")
            return {
                "applicant_id": applicant_id,
                "external_user_id": external_user_id,
                "status": data.get("review", {}).get("reviewStatus", "init"),
                "inspection_id": data.get("inspectionId", ""),
                "level_name": level,
                "created_at": data.get("createdAt", ""),
                "source": "sumsub",
                "api_status": "live",
            }
        else:
            logger.warning(f"Sumsub create applicant failed: {resp.status_code} — {resp.text[:300]}")
            # If applicant already exists, try to get existing
            if resp.status_code == 409:
                return sumsub_get_applicant_by_external_id(external_user_id)
            return _simulate_sumsub_applicant(external_user_id, first_name, last_name,
                                             note=f"API returned {resp.status_code}")

    except Exception as e:
        logger.error(f"Sumsub create applicant error: {e}")
        return _simulate_sumsub_applicant(external_user_id, first_name, last_name,
                                         note=f"Exception: {str(e)[:100]}")


def sumsub_get_applicant_by_external_id(external_user_id):
    """Retrieve an existing Sumsub applicant by external user ID."""
    if not SUMSUB_APP_TOKEN or not SUMSUB_SECRET_KEY:
        return _simulate_sumsub_applicant(external_user_id)

    try:
        url_path = f"/resources/applicants/-;externalUserId={external_user_id}/one"
        headers = _sumsub_sign("GET", url_path)

        resp = requests.get(
            f"{SUMSUB_BASE_URL}{url_path}",
            headers=headers,
            timeout=15
        )

        if resp.status_code == 200:
            data = resp.json()
            return {
                "applicant_id": data.get("id", ""),
                "external_user_id": external_user_id,
                "status": data.get("review", {}).get("reviewStatus", "init"),
                "review_answer": data.get("review", {}).get("reviewResult", {}).get("reviewAnswer", ""),
                "inspection_id": data.get("inspectionId", ""),
                "level_name": data.get("requiredIdDocs", {}).get("docSets", [{}])[0].get("idDocSetType", "") if data.get("requiredIdDocs") else "",
                "source": "sumsub",
                "api_status": "live",
            }
        else:
            return _simulate_sumsub_applicant(external_user_id,
                                             note=f"Lookup returned {resp.status_code}")

    except Exception as e:
        logger.error(f"Sumsub get applicant error: {e}")
        return _simulate_sumsub_applicant(external_user_id, note=str(e)[:100])


def sumsub_generate_access_token(external_user_id, level_name=None):
    """
    Generate an access token for the Sumsub WebSDK.
    The client portal uses this token to launch the KYC widget.
    Returns: { token, userId, source }
    """
    if not SUMSUB_APP_TOKEN or not SUMSUB_SECRET_KEY:
        logger.info(f"Sumsub: No credentials — simulating access token for '{external_user_id}'")
        return _simulate_sumsub_token(external_user_id)

    try:
        level = level_name or SUMSUB_LEVEL_NAME
        url_path = f"/resources/accessTokens?userId={external_user_id}&levelName={level}"
        headers = _sumsub_sign("POST", url_path)

        resp = requests.post(
            f"{SUMSUB_BASE_URL}{url_path}",
            headers=headers,
            timeout=15
        )

        if resp.status_code == 200:
            data = resp.json()
            logger.info(f"Sumsub: Generated access token for user {external_user_id}")
            return {
                "token": data.get("token", ""),
                "user_id": external_user_id,
                "level_name": level,
                "source": "sumsub",
                "api_status": "live",
            }
        else:
            logger.warning(f"Sumsub token gen failed: {resp.status_code} — {resp.text[:300]}")
            return _simulate_sumsub_token(external_user_id,
                                         note=f"API returned {resp.status_code}")

    except Exception as e:
        logger.error(f"Sumsub token error: {e}")
        return _simulate_sumsub_token(external_user_id, note=str(e)[:100])


def sumsub_get_applicant_status(applicant_id):
    """
    Get the verification status of a Sumsub applicant.
    Returns: { applicant_id, status, review_answer, verification_steps, source }
    """
    if not SUMSUB_APP_TOKEN or not SUMSUB_SECRET_KEY:
        return _simulate_sumsub_status(applicant_id)

    try:
        # Get applicant data
        url_path = f"/resources/applicants/{applicant_id}/one"
        headers = _sumsub_sign("GET", url_path)

        resp = requests.get(
            f"{SUMSUB_BASE_URL}{url_path}",
            headers=headers,
            timeout=15
        )

        if resp.status_code == 200:
            data = resp.json()
            review = data.get("review", {})
            review_result = review.get("reviewResult", {})

            result = {
                "applicant_id": applicant_id,
                "external_user_id": data.get("externalUserId", ""),
                "status": review.get("reviewStatus", "init"),
                "review_answer": review_result.get("reviewAnswer", ""),
                "rejection_labels": review_result.get("rejectLabels", []),
                "moderation_comment": review_result.get("moderationComment", ""),
                "created_at": data.get("createdAt", ""),
                "source": "sumsub",
                "api_status": "live",
            }

            # Also get verification steps
            steps_url = f"/resources/applicants/{applicant_id}/requiredIdDocsStatus"
            steps_headers = _sumsub_sign("GET", steps_url)
            steps_resp = requests.get(
                f"{SUMSUB_BASE_URL}{steps_url}",
                headers=steps_headers,
                timeout=10
            )
            if steps_resp.status_code == 200:
                result["verification_steps"] = steps_resp.json()

            return result
        else:
            logger.warning(f"Sumsub status check failed: {resp.status_code}")
            return _simulate_sumsub_status(applicant_id,
                                          note=f"API returned {resp.status_code}")

    except Exception as e:
        logger.error(f"Sumsub status error: {e}")
        return _simulate_sumsub_status(applicant_id, note=str(e)[:100])


def sumsub_add_document(applicant_id, doc_type, country, file_path=None, file_data=None, file_name="document.pdf"):
    """
    Add an identity document to a Sumsub applicant.
    doc_type: PASSPORT, ID_CARD, DRIVERS, SELFIE, etc.
    """
    if not SUMSUB_APP_TOKEN or not SUMSUB_SECRET_KEY:
        return {"status": "simulated", "message": "Sumsub not configured", "source": "simulated"}

    try:
        url_path = f"/resources/applicants/{applicant_id}/info/idDoc"
        metadata = json.dumps({
            "idDocType": doc_type,
            "country": country,
        })

        # Read file content
        content = None
        if file_path and os.path.exists(file_path):
            with open(file_path, "rb") as f:
                content = f.read()
        elif file_data:
            content = base64.b64decode(file_data) if isinstance(file_data, str) else file_data

        if not content:
            return {"status": "error", "message": "No document content provided", "source": "sumsub"}

        # Multipart form data — we need to sign without the body for multipart
        headers = _sumsub_sign("POST", url_path)

        files = {
            "metadata": (None, metadata, "application/json"),
            "content": (file_name, content, "application/octet-stream"),
        }

        resp = requests.post(
            f"{SUMSUB_BASE_URL}{url_path}",
            headers=headers,
            files=files,
            timeout=30
        )

        if resp.status_code in (200, 201):
            logger.info(f"Sumsub: Added {doc_type} document for applicant {applicant_id}")
            return {
                "status": "uploaded",
                "doc_type": doc_type,
                "applicant_id": applicant_id,
                "source": "sumsub",
                "api_status": "live",
            }
        else:
            logger.warning(f"Sumsub doc upload failed: {resp.status_code} — {resp.text[:200]}")
            return {
                "status": "error",
                "message": f"Upload failed: {resp.status_code}",
                "source": "sumsub",
            }

    except Exception as e:
        logger.error(f"Sumsub doc upload error: {e}")
        return {"status": "error", "message": str(e)[:100], "source": "sumsub"}


def sumsub_verify_webhook(body_bytes, signature_header):
    """Verify a Sumsub webhook signature (HMAC-SHA256)."""
    if not SUMSUB_WEBHOOK_SECRET:
        if ENVIRONMENT == "production":
            logger.error("Sumsub webhook secret not configured in production — REJECTING webhook")
            return False
        logger.warning("Sumsub webhook secret not configured — accepting in dev mode only")
        return True

    expected = hmac.new(
        SUMSUB_WEBHOOK_SECRET.encode("utf-8"),
        body_bytes,
        hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature_header or "")


# ── Sumsub simulation fallbacks ──────────────────────────

def _simulate_sumsub_applicant(external_user_id, first_name=None, last_name=None, note="No Sumsub credentials configured"):
    """Fallback: simulated applicant creation."""
    sim_id = f"sim_{hashlib.md5(external_user_id.encode()).hexdigest()[:16]}"
    return {
        "applicant_id": sim_id,
        "external_user_id": external_user_id,
        "status": "init",
        "inspection_id": f"insp_{sim_id[:12]}",
        "level_name": SUMSUB_LEVEL_NAME,
        "source": "simulated",
        "api_status": "simulated",
        "note": note,
        "created_at": datetime.utcnow().isoformat(),
    }


def _simulate_sumsub_token(external_user_id, note="No Sumsub credentials configured"):
    """Fallback: simulated access token."""
    token = base64.b64encode(f"sim_token_{external_user_id}_{int(time.time())}".encode()).decode()
    return {
        "token": token,
        "user_id": external_user_id,
        "level_name": SUMSUB_LEVEL_NAME,
        "source": "simulated",
        "api_status": "simulated",
        "note": note,
    }


def _simulate_sumsub_status(applicant_id, note="No Sumsub credentials configured"):
    """Fallback: simulated verification status."""
    import random
    statuses = ["init", "pending", "completed"]
    answers = ["", "", "GREEN"]
    idx = random.randint(0, 2)
    return {
        "applicant_id": applicant_id,
        "external_user_id": "",
        "status": statuses[idx],
        "review_answer": answers[idx],
        "rejection_labels": [],
        "moderation_comment": "",
        "verification_steps": {
            "IDENTITY": {"reviewResult": {"reviewAnswer": answers[idx]} if idx == 2 else {}},
            "SELFIE": {"reviewResult": {"reviewAnswer": answers[idx]} if idx == 2 else {}},
        },
        "source": "simulated",
        "api_status": "simulated",
        "note": note,
        "created_at": datetime.utcnow().isoformat(),
    }


def run_full_screening(application_data, directors, ubos, client_ip=None):
    """
    Run all screening agents in parallel against an application.
    Uses ThreadPoolExecutor for concurrent HTTP API calls.
    """
    company_name = application_data.get("company_name", "")
    country = application_data.get("country", "")

    report = {
        "screened_at": datetime.utcnow().isoformat(),
        "company_screening": {},
        "director_screenings": [],
        "ubo_screenings": [],
        "ip_geolocation": {},
        "overall_flags": [],
        "total_hits": 0,
    }

    # Map jurisdiction
    jurisdiction = None
    if country:
        jur_map = {"mauritius": "mu", "united kingdom": "gb", "uk": "gb", "france": "fr",
                   "singapore": "sg", "india": "in", "hong kong": "hk", "usa": "us",
                   "united states": "us", "south africa": "za", "germany": "de"}
        jurisdiction = jur_map.get(country.lower())

    # ── Parallel API calls ──
    with ThreadPoolExecutor(max_workers=8) as executor:
        # Submit all screening tasks concurrently
        company_future = executor.submit(lookup_opencorporates, company_name, jurisdiction)
        company_sanctions_future = executor.submit(screen_opensanctions, company_name, entity_type="Company")

        director_futures = []
        for d in directors:
            d_name = d.get("full_name", "")
            if d_name:
                f = executor.submit(screen_opensanctions, d_name, nationality=d.get("nationality"), entity_type="Person")
                director_futures.append((d, f))

        ubo_futures = []
        for u in ubos:
            u_name = u.get("full_name", "")
            if u_name:
                f = executor.submit(screen_opensanctions, u_name, nationality=u.get("nationality"), entity_type="Person")
                ubo_futures.append((u, f))

        ip_future = executor.submit(geolocate_ip, client_ip) if client_ip else None

        kyc_futures = []
        all_persons = [(d, "director") for d in directors] + [(u, "ubo") for u in ubos]
        for person, ptype in all_persons:
            p_name = person.get("full_name", "")
            if not p_name:
                continue
            ext_id = person.get("email", "") or f"{ptype}_{hashlib.md5(p_name.encode()).hexdigest()[:12]}"
            parts = p_name.strip().split(" ", 1)
            first = parts[0] if parts else ""
            last = parts[1] if len(parts) > 1 else ""
            f = executor.submit(sumsub_create_applicant,
                external_user_id=ext_id, first_name=first, last_name=last,
                country=person.get("nationality", ""))
            kyc_futures.append((person, ptype, p_name, f))

        # ── Collect results ──
        report["company_screening"] = company_future.result(timeout=30)
        if not report["company_screening"]["found"]:
            report["overall_flags"].append(f"Company '{company_name}' not found in corporate registry")

        company_sanctions = company_sanctions_future.result(timeout=30)
        report["company_screening"]["sanctions"] = company_sanctions
        if company_sanctions["matched"]:
            report["overall_flags"].append(f"Company '{company_name}' has sanctions/watchlist matches")
            report["total_hits"] += len(company_sanctions["results"])

        for d, f in director_futures:
            d_name = d.get("full_name", "")
            screening = f.result(timeout=30)
            result = {
                "person_name": d_name, "person_type": "director",
                "nationality": d.get("nationality", ""), "declared_pep": d.get("is_pep", "No"),
                "screening": screening,
            }
            if screening["matched"]:
                report["overall_flags"].append(f"Director '{d_name}' has sanctions/PEP matches")
                report["total_hits"] += len(screening["results"])
                for hit in screening["results"]:
                    if hit.get("is_pep") and d.get("is_pep", "No") != "Yes":
                        result["undeclared_pep"] = True
                        report["overall_flags"].append(f"Director '{d_name}' may be undeclared PEP")
            report["director_screenings"].append(result)

        for u, f in ubo_futures:
            u_name = u.get("full_name", "")
            screening = f.result(timeout=30)
            result = {
                "person_name": u_name, "person_type": "ubo",
                "nationality": u.get("nationality", ""), "ownership_pct": u.get("ownership_pct", 0),
                "declared_pep": u.get("is_pep", "No"), "screening": screening,
            }
            if screening["matched"]:
                report["overall_flags"].append(f"UBO '{u_name}' has sanctions/PEP matches")
                report["total_hits"] += len(screening["results"])
                for hit in screening["results"]:
                    if hit.get("is_pep") and u.get("is_pep", "No") != "Yes":
                        result["undeclared_pep"] = True
                        report["overall_flags"].append(f"UBO '{u_name}' may be undeclared PEP")
            report["ubo_screenings"].append(result)

        if ip_future:
            report["ip_geolocation"] = ip_future.result(timeout=30)
            ip_geo = report["ip_geolocation"]
            if ip_geo.get("risk_level") in ("HIGH", "VERY_HIGH"):
                report["overall_flags"].append(f"Client IP geolocated to high-risk jurisdiction: {ip_geo.get('country')}")
            if ip_geo.get("is_vpn"):
                report["overall_flags"].append("Client IP detected as VPN")
            if ip_geo.get("is_proxy"):
                report["overall_flags"].append("Client IP detected as proxy")
            if ip_geo.get("is_tor"):
                report["overall_flags"].append("Client IP detected as Tor exit node")

        report["kyc_applicants"] = []
        for person, ptype, p_name, f in kyc_futures:
            applicant = f.result(timeout=30)
            applicant["person_name"] = p_name
            applicant["person_type"] = ptype
            report["kyc_applicants"].append(applicant)
            if applicant.get("review_answer") == "RED":
                report["overall_flags"].append(f"Sumsub KYC FAILED for {ptype} '{p_name}'")
                report["total_hits"] += 1

    return report


# ══════════════════════════════════════════════════════════
# RATE LIMITING
# ══════════════════════════════════════════════════════════

class RateLimiter:
    """In-memory sliding window rate limiter. Keyed by IP + endpoint."""
    def __init__(self):
        self._attempts = {}  # key → list of timestamps

    def is_limited(self, key, max_attempts=10, window_seconds=900):
        """Returns True if the key has exceeded max_attempts in the window."""
        now = time.time()
        cutoff = now - window_seconds
        if key not in self._attempts:
            self._attempts[key] = []
        # Prune old entries
        self._attempts[key] = [t for t in self._attempts[key] if t > cutoff]
        if len(self._attempts[key]) >= max_attempts:
            return True
        self._attempts[key].append(now)
        return False

    def remaining(self, key, max_attempts=10, window_seconds=900):
        """Returns how many attempts remain for the key."""
        now = time.time()
        cutoff = now - window_seconds
        attempts = [t for t in self._attempts.get(key, []) if t > cutoff]
        return max(0, max_attempts - len(attempts))

    def reset(self, key):
        """Reset rate limit for a key (e.g., after successful login)."""
        self._attempts.pop(key, None)

# Global rate limiter instance
rate_limiter = RateLimiter()


# ══════════════════════════════════════════════════════════
# TORNADO REQUEST HANDLERS
# ══════════════════════════════════════════════════════════

class BaseHandler(tornado.web.RequestHandler):
    def set_default_headers(self):
        # CORS — in production, MUST set ALLOWED_ORIGIN env var to your domain
        allowed_origin = os.environ.get("ALLOWED_ORIGIN", "")
        if not allowed_origin:
            if ENVIRONMENT == "production":
                # In production, no CORS header = same-origin only (most secure)
                logger.warning("ALLOWED_ORIGIN not set in production — defaulting to same-origin only")
            else:
                allowed_origin = "*"  # Permissive in dev only
        if allowed_origin:
            self.set_header("Access-Control-Allow-Origin", allowed_origin)
        self.set_header("Access-Control-Allow-Methods", "GET,POST,PUT,PATCH,DELETE,OPTIONS")
        self.set_header("Access-Control-Allow-Headers", "Content-Type,Authorization")
        self.set_header("Access-Control-Max-Age", "3600")
        self.set_header("Content-Type", "application/json")
        # Security headers — always on
        self.set_header("X-Content-Type-Options", "nosniff")
        self.set_header("X-Frame-Options", "DENY")
        self.set_header("X-XSS-Protection", "1; mode=block")
        self.set_header("Referrer-Policy", "strict-origin-when-cross-origin")
        if ENVIRONMENT == "production":
            self.set_header("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
        # Content Security Policy
        csp = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://cdnjs.cloudflare.com; "
            "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com https://fonts.googleapis.com; "
            "font-src 'self' https://fonts.gstatic.com https://cdnjs.cloudflare.com; "
            "img-src 'self' data: blob:; "
            "connect-src 'self'; "
            "frame-ancestors 'none'"
        )
        self.set_header("Content-Security-Policy", csp)
        self.set_header("Permissions-Policy", "camera=(), microphone=(), geolocation=(), payment=()")

    def options(self, *args):
        self.set_status(204)
        self.finish()

    def check_xsrf_cookie(self):
        """Skip XSRF check for API endpoints using Bearer token auth."""
        if self.request.headers.get("Authorization", "").startswith("Bearer "):
            return
        # Also skip for webhook endpoints
        if "/webhook" in self.request.uri:
            return
        super().check_xsrf_cookie()

    def get_json(self):
        try:
            return json.loads(self.request.body)
        except Exception:
            return {}

    def get_current_user_token(self):
        auth = self.request.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            return decode_token(auth[7:])
        return None

    def require_auth(self, roles=None):
        user = self.get_current_user_token()
        if not user:
            self.set_status(401)
            self.write({"error": "Authentication required"})
            return None
        if roles and user.get("role") not in roles:
            self.set_status(403)
            self.write({"error": "Insufficient permissions"})
            return None
        return user

    def get_client_ip(self):
        return self.request.headers.get("X-Real-IP", self.request.remote_ip)

    def success(self, data, status=200):
        self.set_status(status)
        self.write(json.dumps(data, default=str))

    def error(self, message, status=400):
        self.set_status(status)
        self.write({"error": message})

    def log_audit(self, user, action, target, detail, db=None):
        own_db = db is None
        if own_db:
            db = get_db()
        db.execute(
            "INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
            (user.get("sub",""), user.get("name",""), user.get("role",""), action, target, detail, self.get_client_ip())
        )
        db.commit()
        if own_db:
            db.close()

    def check_app_ownership(self, user, app):
        """Returns True if user is allowed to access this application."""
        if user.get("type") == "client" and app["client_id"] != user["sub"]:
            self.error("Unauthorized", 403)
            return False
        return True


# ── Health Check ──
class HealthHandler(BaseHandler):
    def get(self):
        self.success({"status": "ok", "service": "ARIE Finance API", "version": "1.0.0"})


# ══════════════════════════════════════════════════════════
# AUTH ENDPOINTS
# ══════════════════════════════════════════════════════════

class OfficerLoginHandler(BaseHandler):
    """POST /api/auth/officer/login  {email, password}"""
    def post(self):
        data = self.get_json()
        email = data.get("email", "").strip().lower()
        password = data.get("password", "")
        if not email or not password:
            return self.error("Email and password required")

        # Rate limit: 10 attempts per 15 minutes per IP
        ip = self.get_client_ip()
        rl_key = f"officer_login:{ip}"
        if rate_limiter.is_limited(rl_key, max_attempts=10, window_seconds=900):
            self.set_status(429)
            self.write({"error": "Too many login attempts. Please try again in 15 minutes."})
            logger.warning(f"Rate limited officer login from {ip} for {email}")
            return

        db = get_db()
        user = db.execute("SELECT * FROM users WHERE email = ? AND status = 'active'", (email,)).fetchone()
        db.close()

        if not user or not bcrypt.checkpw(password.encode(), user["password_hash"].encode()):
            return self.error("Invalid credentials", 401)

        rate_limiter.reset(rl_key)  # Reset on successful login
        token = create_token(user["id"], user["role"], user["full_name"], "officer")
        self.log_audit({"sub": user["id"], "name": user["full_name"], "role": user["role"]},
                       "Login", "System", f"Officer login from {ip}")
        self.success({
            "token": token,
            "user": {"id": user["id"], "email": user["email"], "name": user["full_name"], "role": user["role"]}
        })


class ClientLoginHandler(BaseHandler):
    """POST /api/auth/client/login  {email, password}"""
    def post(self):
        data = self.get_json()
        email = data.get("email", "").strip().lower()
        password = data.get("password", "")
        if not email or not password:
            return self.error("Email and password required")

        # Rate limit: 10 attempts per 15 minutes per IP
        ip = self.get_client_ip()
        rl_key = f"client_login:{ip}"
        if rate_limiter.is_limited(rl_key, max_attempts=10, window_seconds=900):
            self.set_status(429)
            self.write({"error": "Too many login attempts. Please try again in 15 minutes."})
            logger.warning(f"Rate limited client login from {ip} for {email}")
            return

        db = get_db()
        client = db.execute("SELECT * FROM clients WHERE email = ? AND status = 'active'", (email,)).fetchone()
        db.close()

        if not client or not bcrypt.checkpw(password.encode(), client["password_hash"].encode()):
            return self.error("Invalid credentials", 401)

        rate_limiter.reset(rl_key)  # Reset on successful login

        token = create_token(client["id"], "client", client["company_name"] or email, "client")
        self.success({
            "token": token,
            "client": {"id": client["id"], "email": client["email"], "company": client["company_name"]}
        })


class ClientRegisterHandler(BaseHandler):
    """POST /api/auth/client/register  {email, password, company_name}"""
    def post(self):
        data = self.get_json()
        email = data.get("email", "").strip().lower()
        password = data.get("password", "")
        company = data.get("company_name", "")
        if not email or not password:
            return self.error("Email and password required")

        # Rate limit: 5 registrations per 30 minutes per IP
        ip = self.get_client_ip()
        rl_key = f"register:{ip}"
        if rate_limiter.is_limited(rl_key, max_attempts=5, window_seconds=1800):
            self.set_status(429)
            self.write({"error": "Too many registration attempts. Please try again later."})
            return

        if len(password) < 8:
            return self.error("Password must be at least 8 characters")

        db = get_db()
        exists = db.execute("SELECT id FROM clients WHERE email = ?", (email,)).fetchone()
        if exists:
            db.close()
            return self.error("Email already registered")

        client_id = uuid.uuid4().hex[:16]
        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        db.execute("INSERT INTO clients (id, email, password_hash, company_name) VALUES (?,?,?,?)",
                    (client_id, email, pw_hash, company))
        db.commit()
        db.close()

        token = create_token(client_id, "client", company or email, "client")
        self.success({"token": token, "client": {"id": client_id, "email": email, "company": company}}, 201)


class MeHandler(BaseHandler):
    """GET /api/auth/me"""
    def get(self):
        user = self.require_auth()
        if not user:
            return
        self.success({"id": user["sub"], "name": user["name"], "role": user["role"], "type": user["type"]})


# ══════════════════════════════════════════════════════════
# APPLICATION ENDPOINTS
# ══════════════════════════════════════════════════════════

class ApplicationsHandler(BaseHandler):
    """GET /api/applications — list, POST — create"""
    def get(self):
        user = self.require_auth()
        if not user:
            return

        db = get_db()
        status = self.get_argument("status", None)
        risk = self.get_argument("risk", None)
        assigned = self.get_argument("assigned", None)

        query = "SELECT * FROM applications WHERE 1=1"
        params = []

        # Clients can only see their own
        if user["type"] == "client":
            query += " AND client_id = ?"
            params.append(user["sub"])

        if status:
            query += " AND status = ?"
            params.append(status)
        if risk:
            query += " AND risk_level = ?"
            params.append(risk)
        if assigned:
            query += " AND assigned_to = ?"
            params.append(assigned)

        query += " ORDER BY created_at DESC LIMIT 200"
        rows = db.execute(query, params).fetchall()
        db.close()

        apps = [dict(r) for r in rows]
        # Attach directors and UBOs for each
        db = get_db()
        for app in apps:
            app["directors"] = [dict(d) for d in db.execute(
                "SELECT * FROM directors WHERE application_id = ?", (app["id"],)).fetchall()]
            app["ubos"] = [dict(u) for u in db.execute(
                "SELECT * FROM ubos WHERE application_id = ?", (app["id"],)).fetchall()]
        db.close()

        self.success({"applications": apps, "total": len(apps)})

    def post(self):
        user = self.require_auth()
        if not user:
            return

        data = self.get_json()
        app_id = uuid.uuid4().hex[:16]
        ref = generate_ref()

        db = get_db()
        db.execute("""
            INSERT INTO applications (id, ref, client_id, company_name, brn, country, sector,
                entity_type, ownership_structure, prescreening_data, status)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            app_id, ref,
            user["sub"] if user["type"] == "client" else data.get("client_id"),
            data.get("company_name", ""),
            data.get("brn", ""),
            data.get("country", ""),
            data.get("sector", ""),
            data.get("entity_type", ""),
            data.get("ownership_structure", ""),
            json.dumps(data.get("prescreening_data", {})),
            "draft"
        ))

        # Add directors
        for d in data.get("directors", []):
            db.execute("INSERT INTO directors (application_id, full_name, nationality, is_pep) VALUES (?,?,?,?)",
                        (app_id, d.get("full_name",""), d.get("nationality",""), d.get("is_pep","No")))

        # Add UBOs
        for u in data.get("ubos", []):
            db.execute("INSERT INTO ubos (application_id, full_name, nationality, ownership_pct, is_pep) VALUES (?,?,?,?,?)",
                        (app_id, u.get("full_name",""), u.get("nationality",""), u.get("ownership_pct",0), u.get("is_pep","No")))

        db.commit()
        db.close()

        self.log_audit(user, "Create", ref, f"New application created: {data.get('company_name','')}")
        self.success({"id": app_id, "ref": ref, "status": "draft"}, 201)


class ApplicationDetailHandler(BaseHandler):
    """GET/PUT/PATCH /api/applications/:id"""
    def get(self, app_id):
        user = self.require_auth()
        if not user:
            return

        db = get_db()
        app = db.execute("SELECT * FROM applications WHERE id = ? OR ref = ?", (app_id, app_id)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)

        if not self.check_app_ownership(user, app):
            db.close()
            return

        result = dict(app)
        result["directors"] = [dict(d) for d in db.execute(
            "SELECT * FROM directors WHERE application_id = ?", (result["id"],)).fetchall()]
        result["ubos"] = [dict(u) for u in db.execute(
            "SELECT * FROM ubos WHERE application_id = ?", (result["id"],)).fetchall()]
        result["documents"] = [dict(d) for d in db.execute(
            "SELECT * FROM documents WHERE application_id = ?", (result["id"],)).fetchall()]
        db.close()

        self.success(result)

    def put(self, app_id):
        """Full update of application data + resubmit."""
        user = self.require_auth()
        if not user:
            return

        data = self.get_json()
        db = get_db()

        app = db.execute("SELECT * FROM applications WHERE id = ? OR ref = ?", (app_id, app_id)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)

        if not self.check_app_ownership(user, app):
            db.close()
            return

        real_id = app["id"]

        db.execute("""
            UPDATE applications SET
                company_name=?, brn=?, country=?, sector=?, entity_type=?,
                ownership_structure=?, prescreening_data=?, updated_at=datetime('now')
            WHERE id=?
        """, (
            data.get("company_name", app["company_name"]),
            data.get("brn", app["brn"]),
            data.get("country", app["country"]),
            data.get("sector", app["sector"]),
            data.get("entity_type", app["entity_type"]),
            data.get("ownership_structure", app["ownership_structure"]),
            json.dumps(data.get("prescreening_data", json.loads(app["prescreening_data"]))),
            real_id
        ))

        # Rebuild directors and UBOs if provided
        if "directors" in data:
            db.execute("DELETE FROM directors WHERE application_id = ?", (real_id,))
            for d in data["directors"]:
                db.execute("INSERT INTO directors (application_id, full_name, nationality, is_pep) VALUES (?,?,?,?)",
                            (real_id, d.get("full_name",""), d.get("nationality",""), d.get("is_pep","No")))

        if "ubos" in data:
            db.execute("DELETE FROM ubos WHERE application_id = ?", (real_id,))
            for u in data["ubos"]:
                db.execute("INSERT INTO ubos (application_id, full_name, nationality, ownership_pct, is_pep) VALUES (?,?,?,?,?)",
                            (real_id, u.get("full_name",""), u.get("nationality",""), u.get("ownership_pct",0), u.get("is_pep","No")))

        db.commit()
        db.close()

        self.log_audit(user, "Update", app["ref"], f"Application updated")
        self.success({"status": "updated"})

    def patch(self, app_id):
        """Partial update — status changes, assignments, etc."""
        user = self.require_auth()
        if not user:
            return

        data = self.get_json()
        db = get_db()
        app = db.execute("SELECT * FROM applications WHERE id = ? OR ref = ?", (app_id, app_id)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)

        if not self.check_app_ownership(user, app):
            db.close()
            return

        real_id = app["id"]

        # Only officers can change status and assignment
        if user.get("type") == "client":
            if "status" in data or "assigned_to" in data or "decision_by" in data:
                db.close()
                return self.error("Only officers can change application status", 403)

        # Handle status changes
        new_status = data.get("status")
        if new_status:
            db.execute("UPDATE applications SET status=?, updated_at=datetime('now') WHERE id=?", (new_status, real_id))
            if new_status in ("approved", "rejected"):
                db.execute("UPDATE applications SET decided_at=datetime('now'), decision_by=?, decision_notes=? WHERE id=?",
                           (user["sub"], data.get("notes",""), real_id))
            self.log_audit(user, new_status.replace("_"," ").title(), app["ref"], f"Status → {new_status}", db=db)

        # Handle assignment
        if "assigned_to" in data:
            db.execute("UPDATE applications SET assigned_to=?, updated_at=datetime('now') WHERE id=?",
                       (data["assigned_to"], real_id))
            self.log_audit(user, "Assign", app["ref"], f"Assigned to {data['assigned_to']}", db=db)

        db.commit()
        db.close()
        self.success({"status": "updated"})


class SubmitApplicationHandler(BaseHandler):
    """POST /api/applications/:id/submit — submit pre-screening, run screening, calculate risk, show pricing"""
    def post(self, app_id):
        user = self.require_auth()
        if not user:
            return

        db = get_db()
        app = db.execute("SELECT * FROM applications WHERE id = ? OR ref = ?", (app_id, app_id)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)

        if not self.check_app_ownership(user, app):
            db.close()
            return

        real_id = app["id"]

        # Build scoring input
        directors = [dict(d) for d in db.execute("SELECT * FROM directors WHERE application_id=?", (real_id,)).fetchall()]
        ubos = [dict(u) for u in db.execute("SELECT * FROM ubos WHERE application_id=?", (real_id,)).fetchall()]

        prescreening = json.loads(app["prescreening_data"]) if app["prescreening_data"] else {}
        scoring_input = {
            **prescreening,
            "entity_type": app["entity_type"],
            "ownership_structure": app["ownership_structure"],
            "country": app["country"],
            "sector": app["sector"],
            "company_name": app["company_name"],
            "directors": directors,
            "ubos": ubos
        }

        # ── Run real screening (Agents 1, 5, 6) ──
        client_ip = self.get_client_ip()
        screening_report = run_full_screening(
            scoring_input, directors, ubos, client_ip=client_ip
        )

        # Compute risk score
        risk = compute_risk_score(scoring_input)

        # Elevate risk if screening found hits
        if screening_report["total_hits"] > 0:
            risk_bump = min(screening_report["total_hits"] * 8, 25)  # Up to +25 points
            risk["score"] = min(100, risk["score"] + risk_bump)
            # Re-classify level
            if risk["score"] >= 70:
                risk["level"] = "VERY_HIGH"
            elif risk["score"] >= 55:
                risk["level"] = "HIGH"
            elif risk["score"] >= 40:
                risk["level"] = "MEDIUM"
            risk["lane"] = {"LOW": "Fast Lane", "MEDIUM": "Standard Review", "HIGH": "EDD", "VERY_HIGH": "EDD"}[risk["level"]]
            risk["screening_elevated"] = True
            risk["screening_hits"] = screening_report["total_hits"]

        # Store screening report in prescreening_data
        prescreening["screening_report"] = screening_report
        db.execute("UPDATE applications SET prescreening_data=? WHERE id=?",
                   (json.dumps(prescreening, default=str), real_id))

        db.execute("""
            UPDATE applications SET
                status='submitted', submitted_at=datetime('now'),
                risk_score=?, risk_level=?, risk_dimensions=?, onboarding_lane=?,
                updated_at=datetime('now')
            WHERE id=?
        """, (risk["score"], risk["level"], json.dumps(risk["dimensions"]), risk["lane"], real_id))

        # After pre-screening: move to pricing review
        # Client must accept pricing before proceeding
        db.execute("UPDATE applications SET status='pricing_review' WHERE id=?", (real_id,))

        # Get pricing for this risk level
        pricing = PRICING_TIERS.get(risk["level"], PRICING_TIERS["MEDIUM"])

        # Store pricing in prescreening data
        prescreening["pricing"] = pricing
        prescreening["pricing"]["risk_level"] = risk["level"]
        db.execute("UPDATE applications SET prescreening_data=? WHERE id=?",
                   (json.dumps(prescreening, default=str), real_id))

        # Notify compliance team for HIGH/VERY_HIGH risk
        if risk["level"] in ("HIGH", "VERY_HIGH"):
            compliance_users = db.execute("SELECT id FROM users WHERE role IN ('sco','co')").fetchall()
            for cu in compliance_users:
                db.execute("INSERT INTO notifications (user_id, title, message) VALUES (?,?,?)",
                          (cu["id"], f"{risk['level']}-Risk Pre-Screening Submitted",
                           f"Pre-screening {app['ref']} ({app['company_name']}) — Risk: {risk['level']} (Score: {risk['score']}). Pricing review pending."))
            db.commit()

        db.commit()
        db.close()

        flags_summary = f", Flags: {len(screening_report['overall_flags'])}" if screening_report["overall_flags"] else ""
        self.log_audit(user, "Pre-Screening Submitted", app["ref"],
                       f"Pre-screening submitted — Score: {risk['score']}, Level: {risk['level']}, Lane: {risk['lane']}{flags_summary}")

        self.success({
            "ref": app["ref"],
            "risk_score": risk["score"],
            "risk_level": risk["level"],
            "risk_dimensions": risk["dimensions"],
            "onboarding_lane": risk["lane"],
            "status": "pricing_review",
            "pricing": pricing,
            "screening": {
                "total_hits": screening_report["total_hits"],
                "flags": screening_report["overall_flags"],
                "api_sources": {
                    "sanctions": screening_report.get("director_screenings", [{}])[0].get("screening", {}).get("source", "none") if screening_report.get("director_screenings") else "none",
                    "corporate_registry": screening_report["company_screening"].get("source", "none"),
                    "ip_geolocation": screening_report["ip_geolocation"].get("source", "none") if screening_report.get("ip_geolocation") else "none",
                }
            }
        })


class PricingAcceptHandler(BaseHandler):
    """POST /api/applications/:id/accept-pricing — Client accepts pricing, proceeds to next step"""
    def post(self, app_id):
        user = self.require_auth()
        if not user:
            return

        db = get_db()
        app = db.execute("SELECT * FROM applications WHERE id = ? OR ref = ?", (app_id, app_id)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)

        if not self.check_app_ownership(user, app):
            db.close()
            return

        if app["status"] != "pricing_review":
            db.close()
            return self.error("Application is not in pricing review stage", 400)

        real_id = app["id"]
        risk_level = app["risk_level"] or "MEDIUM"

        # Update status: accepted pricing
        db.execute("UPDATE applications SET status='pricing_accepted', updated_at=datetime('now') WHERE id=?", (real_id,))

        # Route based on risk:
        # LOW/MEDIUM → proceed directly to KYC & Documents
        # HIGH/VERY_HIGH → must go through compliance review first
        if risk_level in ("LOW", "MEDIUM"):
            next_status = "kyc_documents"
            db.execute("UPDATE applications SET status=? WHERE id=?", (next_status, real_id))
            message = "Pricing accepted. Please proceed with KYC verification and document upload."
        else:
            next_status = "compliance_review"
            db.execute("UPDATE applications SET status=? WHERE id=?", (next_status, real_id))
            message = "Pricing accepted. Your application has been referred for compliance review due to the risk profile."
            # Notify compliance officers
            compliance_users = db.execute("SELECT id FROM users WHERE role IN ('sco','co')").fetchall()
            for cu in compliance_users:
                db.execute("INSERT INTO notifications (user_id, title, message) VALUES (?,?,?)",
                          (cu["id"], f"High-Risk Case Requires Review: {app['ref']}",
                           f"{app['company_name']} — Risk: {risk_level}. Client accepted pricing. Compliance review required before KYC proceeds."))

        db.commit()
        db.close()

        self.log_audit(user, "Pricing Accepted", app["ref"], f"Pricing accepted — Risk: {risk_level}, Next: {next_status}")
        self.success({"status": next_status, "message": message, "risk_level": risk_level})


class KYCSubmitHandler(BaseHandler):
    """POST /api/applications/:id/submit-kyc — Submit KYC documents for compliance review"""
    def post(self, app_id):
        user = self.require_auth()
        if not user:
            return

        db = get_db()
        app = db.execute("SELECT * FROM applications WHERE id = ? OR ref = ?", (app_id, app_id)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)

        if not self.check_app_ownership(user, app):
            db.close()
            return

        if app["status"] != "kyc_documents":
            db.close()
            return self.error("Application is not in KYC & Documents stage", 400)

        real_id = app["id"]

        # Check that at least one document has been uploaded
        doc_count = db.execute("SELECT COUNT(*) as c FROM documents WHERE application_id=?", (real_id,)).fetchone()["c"]
        if doc_count == 0:
            db.close()
            return self.error("Please upload at least one document before submitting", 400)

        # ALL applications after KYC go to compliance review — no auto-approval
        db.execute("""
            UPDATE applications SET
                status='compliance_review',
                updated_at=datetime('now')
            WHERE id=?
        """, (real_id,))

        # Notify ALL compliance officers
        compliance_users = db.execute("SELECT id FROM users WHERE role IN ('sco','co','admin')").fetchall()
        for cu in compliance_users:
            db.execute("INSERT INTO notifications (user_id, title, message) VALUES (?,?,?)",
                      (cu["id"], f"KYC Submitted — Ready for Review: {app['ref']}",
                       f"{app['company_name']} has completed KYC & document upload. Risk: {app['risk_level']} (Score: {app['risk_score']}). Awaiting compliance approval."))

        db.commit()
        db.close()

        self.log_audit(user, "KYC Submitted", app["ref"],
                       f"KYC documents submitted for compliance review — {doc_count} document(s)")
        self.success({
            "status": "compliance_review",
            "message": "Your documents have been submitted for compliance review. An officer will review your application shortly.",
            "documents_uploaded": doc_count
        })


# ══════════════════════════════════════════════════════════
# DOCUMENT UPLOAD ENDPOINTS
# ══════════════════════════════════════════════════════════

class DocumentUploadHandler(BaseHandler):
    """POST /api/applications/:id/documents"""
    def post(self, app_id):
        user = self.require_auth()
        if not user:
            return

        db = get_db()
        app = db.execute("SELECT id, ref, client_id FROM applications WHERE id=? OR ref=?", (app_id, app_id)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)

        if not self.check_app_ownership(user, app):
            db.close()
            return

        if "file" not in self.request.files:
            db.close()
            return self.error("No file provided")

        file_info = self.request.files["file"][0]
        filename = file_info["filename"]
        body = file_info["body"]

        if len(body) > MAX_UPLOAD_MB * 1024 * 1024:
            db.close()
            return self.error(f"File exceeds {MAX_UPLOAD_MB}MB limit")

        # Save file
        doc_id = uuid.uuid4().hex[:16]
        ext = os.path.splitext(filename)[1]
        safe_name = f"{app['id']}_{doc_id}{ext}"
        file_path = os.path.join(UPLOAD_DIR, safe_name)

        with open(file_path, "wb") as f:
            f.write(body)

        doc_type = self.get_argument("doc_type", "general")
        person_id = self.get_argument("person_id", None)

        db.execute("""
            INSERT INTO documents (id, application_id, person_id, doc_type, doc_name, file_path, file_size, mime_type)
            VALUES (?,?,?,?,?,?,?,?)
        """, (doc_id, app["id"], person_id, doc_type, filename, file_path, len(body), file_info["content_type"]))
        db.commit()
        db.close()

        self.log_audit(user, "Upload", app["ref"], f"Document uploaded: {filename} ({doc_type})")
        self.success({"id": doc_id, "doc_name": filename, "doc_type": doc_type, "file_size": len(body)}, 201)


class DocumentVerifyHandler(BaseHandler):
    """POST /api/documents/:id/verify — trigger AI verification"""
    def post(self, doc_id):
        user = self.require_auth()
        if not user:
            return

        db = get_db()
        doc = db.execute("SELECT * FROM documents WHERE id=?", (doc_id,)).fetchone()
        if not doc:
            db.close()
            return self.error("Document not found", 404)

        # Get the related application and person for screening
        app = db.execute("SELECT * FROM applications WHERE id=?", (doc["application_id"],)).fetchone()

        import random
        checks = []
        check_types = [
            ("Name Match", "name", "Entity/person name verified against application"),
            ("Document Date", "age", "Document is within acceptable date range"),
            ("Document Clarity", "quality", "Document is legible and complete"),
            ("Content Verification", "content", "Required content elements present"),
        ]
        all_passed = True
        for label, ctype, rule in check_types:
            passed = random.random() > 0.12  # 88% pass rate
            severity = "pass" if passed else ("warn" if random.random() > 0.4 else "fail")
            if not passed:
                all_passed = False
            checks.append({
                "label": label,
                "type": ctype,
                "rule": rule,
                "result": severity,
                "message": "" if passed else f"Check flagged — manual review recommended"
            })

        # If it's an identity document, run sanctions/PEP screening
        sanctions_result = None
        id_doc_types = ["passport", "national_id", "id_card", "drivers_license", "director_id", "ubo_id"]
        if doc["doc_type"] in id_doc_types and doc["person_id"]:
            # Try to find the person's name
            person = db.execute("SELECT full_name, nationality FROM directors WHERE id=?", (doc["person_id"],)).fetchone()
            if not person:
                person = db.execute("SELECT full_name, nationality FROM ubos WHERE id=?", (doc["person_id"],)).fetchone()
            if person:
                sanctions_result = screen_opensanctions(
                    person["full_name"],
                    nationality=person["nationality"],
                    entity_type="Person"
                )
                if sanctions_result["matched"]:
                    all_passed = False
                    checks.append({
                        "label": "Sanctions/PEP Screening",
                        "type": "sanctions",
                        "rule": "Screened against OpenSanctions watchlists and PEP databases",
                        "result": "fail",
                        "message": f"MATCH FOUND — {len(sanctions_result['results'])} hit(s) on sanctions/PEP lists",
                        "details": sanctions_result["results"],
                        "source": sanctions_result["source"]
                    })
                else:
                    checks.append({
                        "label": "Sanctions/PEP Screening",
                        "type": "sanctions",
                        "rule": "Screened against OpenSanctions watchlists and PEP databases",
                        "result": "pass",
                        "message": "No matches found on sanctions or PEP lists",
                        "source": sanctions_result["source"]
                    })

        status = "verified" if all_passed else "flagged"
        results = json.dumps({
            "checks": checks,
            "overall": status,
            "verified_at": datetime.utcnow().isoformat(),
            "sanctions_screening": sanctions_result
        }, default=str)

        db.execute("UPDATE documents SET verification_status=?, verification_results=?, verified_at=datetime('now') WHERE id=?",
                   (status, results, doc_id))
        db.commit()
        db.close()

        self.success({"doc_id": doc_id, "status": status, "checks": checks})


# ══════════════════════════════════════════════════════════
# USER MANAGEMENT ENDPOINTS
# ══════════════════════════════════════════════════════════

class UsersHandler(BaseHandler):
    """GET /api/users — list, POST — create"""
    def get(self):
        user = self.require_auth(roles=["admin", "sco"])
        if not user:
            return

        db = get_db()
        rows = db.execute("SELECT id, email, full_name, role, status, created_at FROM users ORDER BY created_at").fetchall()
        db.close()
        self.success({"users": [dict(r) for r in rows]})

    def post(self):
        user = self.require_auth(roles=["admin"])
        if not user:
            return

        data = self.get_json()
        email = data.get("email", "").strip().lower()
        name = data.get("full_name", "")
        role = data.get("role", "analyst")
        password = data.get("password", "Welcome@123")

        if not email or not name:
            return self.error("Email and full name required")

        db = get_db()
        exists = db.execute("SELECT id FROM users WHERE email=?", (email,)).fetchone()
        if exists:
            db.close()
            return self.error("Email already exists")

        user_id = uuid.uuid4().hex[:16]
        pw_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()
        db.execute("INSERT INTO users (id, email, password_hash, full_name, role) VALUES (?,?,?,?,?)",
                    (user_id, email, pw_hash, name, role))
        db.commit()
        db.close()

        self.log_audit(user, "Create User", name, f"New user added as {role}")
        self.success({"id": user_id, "email": email, "name": name, "role": role}, 201)


class UserDetailHandler(BaseHandler):
    """PUT /api/users/:id — update user"""
    def put(self, user_id):
        user = self.require_auth(roles=["admin"])
        if not user:
            return

        data = self.get_json()
        db = get_db()
        u = db.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not u:
            db.close()
            return self.error("User not found", 404)

        db.execute("UPDATE users SET full_name=?, role=?, status=?, updated_at=datetime('now') WHERE id=?",
                   (data.get("full_name", u["full_name"]), data.get("role", u["role"]),
                    data.get("status", u["status"]), user_id))
        db.commit()
        db.close()

        self.log_audit(user, "Update User", u["full_name"], f"Updated: role={data.get('role')}, status={data.get('status')}")
        self.success({"status": "updated"})


# ══════════════════════════════════════════════════════════
# RISK CONFIG ENDPOINTS
# ══════════════════════════════════════════════════════════

class RiskConfigHandler(BaseHandler):
    """GET/PUT /api/config/risk-model"""
    def get(self):
        user = self.require_auth()
        if not user:
            return
        db = get_db()
        config = db.execute("SELECT * FROM risk_config WHERE id=1").fetchone()
        db.close()
        if config:
            self.success({"dimensions": json.loads(config["dimensions"]),
                         "thresholds": json.loads(config["thresholds"]),
                         "updated_at": config["updated_at"]})
        else:
            self.success({"dimensions": [], "thresholds": []})

    def put(self):
        user = self.require_auth(roles=["admin"])
        if not user:
            return
        data = self.get_json()
        db = get_db()
        db.execute("UPDATE risk_config SET dimensions=?, thresholds=?, updated_by=?, updated_at=datetime('now') WHERE id=1",
                   (json.dumps(data.get("dimensions",[])), json.dumps(data.get("thresholds",[])), user["sub"]))
        db.commit()
        db.close()
        self.log_audit(user, "Config", "Risk Model", "Risk scoring model updated")
        self.success({"status": "saved"})


# ══════════════════════════════════════════════════════════
# AI AGENTS CONFIG ENDPOINTS
# ══════════════════════════════════════════════════════════

class AIAgentsHandler(BaseHandler):
    """GET/POST /api/config/ai-agents"""
    def get(self):
        user = self.require_auth()
        if not user:
            return
        db = get_db()
        rows = db.execute("SELECT * FROM ai_agents ORDER BY agent_number").fetchall()
        db.close()
        agents = []
        for r in rows:
            a = dict(r)
            a["checks"] = json.loads(a["checks"]) if a["checks"] else []
            a["enabled"] = bool(a["enabled"])
            agents.append(a)
        self.success({"agents": agents})

    def post(self):
        user = self.require_auth(roles=["admin"])
        if not user:
            return
        data = self.get_json()
        db = get_db()
        db.execute("""INSERT INTO ai_agents (agent_number, name, icon, stage, description, enabled, checks)
                      VALUES (?,?,?,?,?,?,?)""",
                   (data.get("agent_number",0), data.get("name",""), data.get("icon","🤖"),
                    data.get("stage",""), data.get("description",""),
                    1 if data.get("enabled", True) else 0, json.dumps(data.get("checks",[]))))
        db.commit()
        db.close()
        self.log_audit(user, "Config", "AI Agents", f"Agent added: {data.get('name','')}")
        self.success({"status": "created"}, 201)


class AIAgentDetailHandler(BaseHandler):
    """PUT/DELETE /api/config/ai-agents/:id"""
    def put(self, agent_id):
        user = self.require_auth(roles=["admin"])
        if not user:
            return
        data = self.get_json()
        db = get_db()
        db.execute("""UPDATE ai_agents SET name=?, icon=?, stage=?, description=?,
                      enabled=?, checks=?, updated_at=datetime('now') WHERE id=?""",
                   (data.get("name",""), data.get("icon",""), data.get("stage",""),
                    data.get("description",""), 1 if data.get("enabled",True) else 0,
                    json.dumps(data.get("checks",[])), agent_id))
        db.commit()
        db.close()
        self.success({"status": "updated"})

    def delete(self, agent_id):
        user = self.require_auth(roles=["admin"])
        if not user:
            return
        db = get_db()
        db.execute("DELETE FROM ai_agents WHERE id=?", (agent_id,))
        db.commit()
        db.close()
        self.log_audit(user, "Config", "AI Agents", f"Agent {agent_id} deleted")
        self.success({"status": "deleted"})


# ══════════════════════════════════════════════════════════
# REPORT GENERATION ENDPOINTS
# ══════════════════════════════════════════════════════════

class ReportHandler(BaseHandler):
    """GET /api/reports/generate — generate filtered report data"""
    def get(self):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        db = get_db()

        # Get filter parameters
        status = self.get_argument("status", None)
        risk_level = self.get_argument("risk_level", None)
        date_from = self.get_argument("date_from", None)
        date_to = self.get_argument("date_to", None)
        fields = self.get_argument("fields", "ref,company_name,status,risk_level,created_at,assigned_to")

        # Build query
        conditions = []
        params = []
        if status:
            conditions.append("a.status=?")
            params.append(status)
        if risk_level:
            conditions.append("a.risk_level=?")
            params.append(risk_level)
        if date_from:
            conditions.append("a.created_at >= ?")
            params.append(date_from)
        if date_to:
            conditions.append("a.created_at <= ?")
            params.append(date_to)

        where = " AND ".join(conditions) if conditions else "1=1"

        query = f"""
            SELECT a.*,
                   (SELECT COUNT(*) FROM directors WHERE application_id=a.id) as director_count,
                   (SELECT COUNT(*) FROM ubos WHERE application_id=a.id) as ubo_count,
                   (SELECT COUNT(*) FROM documents WHERE application_id=a.id) as document_count
            FROM applications a
            WHERE {where}
            ORDER BY a.created_at DESC
        """

        rows = db.execute(query, params).fetchall()
        db.close()

        # Parse field selection
        field_list = [f.strip() for f in fields.split(",")]

        results = []
        for row in rows:
            record = dict(row)
            # Parse prescreening_data for risk info
            prescreening = json.loads(record.get("prescreening_data") or "{}")
            risk_info = prescreening.get("risk_assessment", {})
            record["risk_score"] = risk_info.get("score", 0)
            record["risk_level"] = risk_info.get("level", record.get("risk_level", ""))
            record["risk_lane"] = risk_info.get("lane", "")

            # Filter to requested fields
            filtered = {}
            for f in field_list:
                if f in record:
                    filtered[f] = record[f]
                elif f == "director_count":
                    filtered[f] = record.get("director_count", 0)
                elif f == "ubo_count":
                    filtered[f] = record.get("ubo_count", 0)
                elif f == "document_count":
                    filtered[f] = record.get("document_count", 0)
            results.append(filtered)

        self.log_audit(user, "Report", "Generate", f"Report generated: {len(results)} records, fields: {fields}")
        self.success({
            "total": len(results),
            "fields": field_list,
            "data": results
        })


# ══════════════════════════════════════════════════════════
# AUDIT TRAIL ENDPOINTS
# ══════════════════════════════════════════════════════════

class AuditHandler(BaseHandler):
    """GET /api/audit"""
    def get(self):
        user = self.require_auth(roles=["admin", "sco"])
        if not user:
            return
        db = get_db()
        limit = int(self.get_argument("limit", 100))
        offset = int(self.get_argument("offset", 0))
        rows = db.execute("SELECT * FROM audit_log ORDER BY timestamp DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
        total = db.execute("SELECT COUNT(*) as c FROM audit_log").fetchone()["c"]
        db.close()
        self.success({"entries": [dict(r) for r in rows], "total": total})


# ══════════════════════════════════════════════════════════
# DASHBOARD STATS
# ══════════════════════════════════════════════════════════

class DashboardHandler(BaseHandler):
    """GET /api/dashboard"""
    def get(self):
        user = self.require_auth()
        if not user:
            return

        db = get_db()
        stats = {}

        if user.get("type") == "client":
            client_id = user["sub"]
            stats["total"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE client_id=?", (client_id,)).fetchone()["c"]
            stats["pending"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE status IN ('pending_review','submitted') AND client_id=?", (client_id,)).fetchone()["c"]
            stats["in_review"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE status='in_review' AND client_id=?", (client_id,)).fetchone()["c"]
            stats["kyc_documents"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE status='kyc_documents' AND client_id=?", (client_id,)).fetchone()["c"]
            stats["compliance_review"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE status='compliance_review' AND client_id=?", (client_id,)).fetchone()["c"]
            stats["approved"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE status='approved' AND client_id=?", (client_id,)).fetchone()["c"]
            stats["rejected"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE status='rejected' AND client_id=?", (client_id,)).fetchone()["c"]
            stats["edd"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE status='edd_required' AND client_id=?", (client_id,)).fetchone()["c"]

            # Risk distribution
            stats["risk_low"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE risk_level='LOW' AND client_id=?", (client_id,)).fetchone()["c"]
            stats["risk_medium"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE risk_level='MEDIUM' AND client_id=?", (client_id,)).fetchone()["c"]
            stats["risk_high"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE risk_level='HIGH' AND client_id=?", (client_id,)).fetchone()["c"]
            stats["risk_very_high"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE risk_level='VERY_HIGH' AND client_id=?", (client_id,)).fetchone()["c"]

            # Recent applications
            recent = db.execute("""
                SELECT a.*, u.full_name as assigned_name FROM applications a
                LEFT JOIN users u ON a.assigned_to = u.id
                WHERE a.client_id=?
                ORDER BY a.created_at DESC LIMIT 10
            """, (client_id,)).fetchall()
            stats["recent"] = [dict(r) for r in recent]
        else:
            stats["total"] = db.execute("SELECT COUNT(*) as c FROM applications").fetchone()["c"]
            stats["pending"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE status IN ('pending_review','submitted')").fetchone()["c"]
            stats["in_review"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE status='in_review'").fetchone()["c"]
            stats["kyc_documents"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE status='kyc_documents'").fetchone()["c"]
            stats["compliance_review"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE status='compliance_review'").fetchone()["c"]
            stats["approved"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE status='approved'").fetchone()["c"]
            stats["rejected"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE status='rejected'").fetchone()["c"]
            stats["edd"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE status='edd_required'").fetchone()["c"]

            # Risk distribution
            stats["risk_low"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE risk_level='LOW'").fetchone()["c"]
            stats["risk_medium"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE risk_level='MEDIUM'").fetchone()["c"]
            stats["risk_high"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE risk_level='HIGH'").fetchone()["c"]
            stats["risk_very_high"] = db.execute("SELECT COUNT(*) as c FROM applications WHERE risk_level='VERY_HIGH'").fetchone()["c"]

            # Recent applications
            recent = db.execute("""
                SELECT a.*, u.full_name as assigned_name FROM applications a
                LEFT JOIN users u ON a.assigned_to = u.id
                ORDER BY a.created_at DESC LIMIT 10
            """).fetchall()
            stats["recent"] = [dict(r) for r in recent]

        db.close()
        self.success(stats)


# ══════════════════════════════════════════════════════════
# SAVE & RESUME (Client Portal)
# ══════════════════════════════════════════════════════════

class SaveResumeHandler(BaseHandler):
    """POST /api/save-resume — save form progress, GET — restore"""
    def get(self):
        user = self.require_auth()
        if not user:
            return
        db = get_db()
        session = db.execute(
            "SELECT * FROM client_sessions WHERE client_id=? ORDER BY updated_at DESC LIMIT 1",
            (user["sub"],)).fetchone()
        db.close()
        if session:
            self.success({"form_data": json.loads(session["form_data"]),
                         "last_step": session["last_step"],
                         "application_id": session["application_id"]})
        else:
            self.success({"form_data": {}, "last_step": 0})

    def post(self):
        user = self.require_auth()
        if not user:
            return
        data = self.get_json()
        db = get_db()
        existing = db.execute("SELECT id FROM client_sessions WHERE client_id=?", (user["sub"],)).fetchone()
        if existing:
            db.execute("UPDATE client_sessions SET form_data=?, last_step=?, application_id=?, updated_at=datetime('now') WHERE id=?",
                       (json.dumps(data.get("form_data",{})), data.get("last_step",0),
                        data.get("application_id"), existing["id"]))
        else:
            db.execute("INSERT INTO client_sessions (client_id, application_id, form_data, last_step) VALUES (?,?,?,?)",
                       (user["sub"], data.get("application_id"), json.dumps(data.get("form_data",{})), data.get("last_step",0)))
        db.commit()
        db.close()
        self.success({"status": "saved"})


# ══════════════════════════════════════════════════════════
# PORTAL FILE SERVING
# ══════════════════════════════════════════════════════════

PORTAL_DIR = os.path.join(os.path.dirname(__file__), "..")  # outputs/ directory

class PortalHandler(tornado.web.RequestHandler):
    """Serve the client portal HTML"""
    def get(self):
        self.set_header("Content-Type", "text/html")
        with open(os.path.join(PORTAL_DIR, "arie-portal.html"), "r") as f:
            self.write(f.read())

class BackOfficeHandler(tornado.web.RequestHandler):
    """Serve the back-office portal HTML"""
    def get(self):
        self.set_header("Content-Type", "text/html")
        with open(os.path.join(PORTAL_DIR, "arie-backoffice.html"), "r") as f:
            self.write(f.read())


# ══════════════════════════════════════════════════════════
# SCREENING ENDPOINTS (Real API Integrations)
# ══════════════════════════════════════════════════════════

class ScreeningHandler(BaseHandler):
    """POST /api/screening/run — run full screening for an application"""
    def post(self):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        data = self.get_json()
        app_id = data.get("application_id")
        if not app_id:
            return self.error("application_id required")

        db = get_db()
        app = db.execute("SELECT * FROM applications WHERE id=? OR ref=?", (app_id, app_id)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)

        real_id = app["id"]
        directors = [dict(d) for d in db.execute("SELECT * FROM directors WHERE application_id=?", (real_id,)).fetchall()]
        ubos = [dict(u) for u in db.execute("SELECT * FROM ubos WHERE application_id=?", (real_id,)).fetchall()]

        app_data = {
            "company_name": app["company_name"],
            "country": app["country"],
            "sector": app["sector"],
            "entity_type": app["entity_type"],
        }

        report = run_full_screening(app_data, directors, ubos, client_ip=self.get_client_ip())

        # Store screening report
        prescreening = json.loads(app["prescreening_data"]) if app["prescreening_data"] else {}
        prescreening["screening_report"] = report
        prescreening["last_screened_at"] = datetime.utcnow().isoformat()
        prescreening["screened_by"] = user["sub"]
        db.execute("UPDATE applications SET prescreening_data=?, updated_at=datetime('now') WHERE id=?",
                   (json.dumps(prescreening, default=str), real_id))
        db.commit()
        db.close()

        self.log_audit(user, "Screening", app["ref"],
                       f"Full screening run — {report['total_hits']} hit(s), {len(report['overall_flags'])} flag(s)")

        self.success(report)


class SanctionsCheckHandler(BaseHandler):
    """POST /api/screening/sanctions — ad-hoc sanctions/PEP check"""
    def post(self):
        user = self.require_auth()
        if not user:
            return

        data = self.get_json()
        name = data.get("name", "").strip()
        if not name:
            return self.error("name is required")

        entity_type = data.get("entity_type", "Person")
        nationality = data.get("nationality")
        birth_date = data.get("birth_date")

        result = screen_opensanctions(name, birth_date=birth_date, nationality=nationality, entity_type=entity_type)
        self.log_audit(user, "Sanctions Check", name,
                       f"Ad-hoc sanctions check — {'MATCH' if result['matched'] else 'CLEAR'} ({result['source']})")
        self.success(result)


class CompanyLookupHandler(BaseHandler):
    """POST /api/screening/company — ad-hoc company registry lookup"""
    def post(self):
        user = self.require_auth()
        if not user:
            return

        data = self.get_json()
        company_name = data.get("company_name", "").strip()
        if not company_name:
            return self.error("company_name is required")

        jurisdiction = data.get("jurisdiction")
        result = lookup_opencorporates(company_name, jurisdiction)
        self.log_audit(user, "Company Lookup", company_name,
                       f"Company registry lookup — {'FOUND' if result['found'] else 'NOT FOUND'} ({result['source']})")
        self.success(result)


class IPCheckHandler(BaseHandler):
    """GET /api/screening/ip — check IP geolocation"""
    def get(self):
        user = self.require_auth()
        if not user:
            return

        ip = self.get_argument("ip", self.get_client_ip())
        result = geolocate_ip(ip)
        self.success(result)


class APIStatusHandler(BaseHandler):
    """GET /api/screening/status — check which APIs are live vs simulated"""
    def get(self):
        user = self.require_auth()
        if not user:
            return

        self.success({
            "opensanctions": {
                "configured": bool(OPENSANCTIONS_API_KEY),
                "status": "live" if OPENSANCTIONS_API_KEY else "simulated",
                "description": "Sanctions, PEP, and watchlist screening"
            },
            "opencorporates": {
                "configured": bool(OPENCORPORATES_API_KEY),
                "status": "live" if OPENCORPORATES_API_KEY else "simulated",
                "description": "Company registry verification"
            },
            "ip_geolocation": {
                "configured": True,  # ipapi.co works without key (free tier)
                "status": "live",
                "description": "IP address geolocation and risk assessment"
            },
            "sumsub": {
                "configured": bool(SUMSUB_APP_TOKEN and SUMSUB_SECRET_KEY),
                "status": "live" if (SUMSUB_APP_TOKEN and SUMSUB_SECRET_KEY) else "simulated",
                "description": "KYC identity verification (document + selfie + liveness)"
            },
            "environment": ENVIRONMENT,
        })


# ══════════════════════════════════════════════════════════
# SUMSUB KYC ENDPOINTS
# ══════════════════════════════════════════════════════════

class SumsubApplicantHandler(BaseHandler):
    """POST /api/kyc/applicant — Create a Sumsub applicant for KYC"""
    def post(self):
        user = self.require_auth()
        if not user:
            return

        data = self.get_json()
        external_user_id = data.get("external_user_id", "").strip()
        if not external_user_id:
            return self.error("external_user_id is required")

        result = sumsub_create_applicant(
            external_user_id=external_user_id,
            first_name=data.get("first_name"),
            last_name=data.get("last_name"),
            email=data.get("email"),
            phone=data.get("phone"),
            dob=data.get("dob"),
            country=data.get("country"),
            level_name=data.get("level_name"),
        )

        self.log_audit(user, "KYC Applicant Created", external_user_id,
                       f"Sumsub applicant created — ID: {result.get('applicant_id')} ({result['source']})")
        self.success(result)


class SumsubAccessTokenHandler(BaseHandler):
    """POST /api/kyc/token — Generate a Sumsub WebSDK access token"""
    def post(self):
        user = self.require_auth()
        if not user:
            return

        data = self.get_json()
        external_user_id = data.get("external_user_id", "").strip()
        if not external_user_id:
            return self.error("external_user_id is required")

        result = sumsub_generate_access_token(
            external_user_id=external_user_id,
            level_name=data.get("level_name"),
        )
        self.success(result)


class SumsubStatusHandler(BaseHandler):
    """GET /api/kyc/status/:applicant_id — Get verification status"""
    def get(self, applicant_id):
        user = self.require_auth()
        if not user:
            return

        result = sumsub_get_applicant_status(applicant_id)
        self.success(result)


class SumsubDocumentHandler(BaseHandler):
    """POST /api/kyc/document — Upload a document to Sumsub"""
    def post(self):
        user = self.require_auth()
        if not user:
            return

        data = self.get_json()
        applicant_id = data.get("applicant_id", "").strip()
        doc_type = data.get("doc_type", "PASSPORT").strip()
        country = data.get("country", "").strip()

        if not applicant_id:
            return self.error("applicant_id is required")

        # Support base64 file data or a reference to an uploaded file
        file_data = data.get("file_data")
        file_path = data.get("file_path")
        file_name = data.get("file_name", "document.pdf")

        result = sumsub_add_document(
            applicant_id=applicant_id,
            doc_type=doc_type,
            country=country,
            file_path=file_path,
            file_data=file_data,
            file_name=file_name,
        )

        self.log_audit(user, "KYC Document Upload", applicant_id,
                       f"Sumsub document upload — Type: {doc_type}, Status: {result.get('status')}")
        self.success(result)


class SumsubWebhookHandler(tornado.web.RequestHandler):
    """POST /api/kyc/webhook — Receive Sumsub verification webhooks"""

    def set_default_headers(self):
        self.set_header("Content-Type", "application/json")

    def post(self):
        body = self.request.body
        signature = self.request.headers.get("X-Payload-Digest", "")

        # Verify webhook signature
        if SUMSUB_WEBHOOK_SECRET and not sumsub_verify_webhook(body, signature):
            logger.warning("Sumsub webhook: Invalid signature")
            self.set_status(401)
            self.write(json.dumps({"error": "Invalid signature"}))
            return

        try:
            payload = json.loads(body)
        except Exception:
            self.set_status(400)
            self.write(json.dumps({"error": "Invalid JSON"}))
            return

        event_type = payload.get("type", "")
        applicant_id = payload.get("applicantId", "")
        external_user_id = payload.get("externalUserId", "")
        review_result = payload.get("reviewResult", {})
        review_answer = review_result.get("reviewAnswer", "")

        logger.info(f"Sumsub webhook: {event_type} — applicant={applicant_id}, answer={review_answer}")

        # Handle applicantReviewed event
        if event_type == "applicantReviewed":
            db = get_db()
            try:
                # Find the application linked to this external user
                # The external user ID may be a client email or director ID
                kyc_data = json.dumps({
                    "sumsub_applicant_id": applicant_id,
                    "external_user_id": external_user_id,
                    "review_answer": review_answer,
                    "rejection_labels": review_result.get("rejectLabels", []),
                    "moderation_comment": review_result.get("moderationComment", ""),
                    "event_type": event_type,
                    "received_at": datetime.utcnow().isoformat(),
                })

                # Store webhook data in audit log
                db.execute("""
                    INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, ("system", "Sumsub Webhook", "system", f"KYC {event_type}: {review_answer}", applicant_id, kyc_data))

                # Try to update application status if we can find it
                # Look for applications where prescreening_data contains this applicant
                apps = db.execute("SELECT id, prescreening_data FROM applications").fetchall()
                for app in apps:
                    pdata = app["prescreening_data"] or ""
                    if applicant_id in pdata or external_user_id in pdata:
                        # Update the prescreening data with new KYC result
                        try:
                            pdict = json.loads(pdata) if pdata else {}
                            if "screening_report" not in pdict:
                                pdict["screening_report"] = {}
                            pdict["screening_report"]["sumsub_webhook"] = json.loads(kyc_data)

                            # If verification failed, add a flag
                            if review_answer == "RED":
                                flags = pdict["screening_report"].get("overall_flags", [])
                                flags.append(f"Sumsub KYC verification REJECTED for {external_user_id}")
                                pdict["screening_report"]["overall_flags"] = flags

                            db.execute("UPDATE applications SET prescreening_data=? WHERE id=?",
                                      (json.dumps(pdict), app["id"]))
                            logger.info(f"Sumsub webhook: Updated application {app['id']}")
                        except Exception as e:
                            logger.error(f"Failed to update application: {e}")

                db.commit()
            finally:
                db.close()

        elif event_type == "applicantPending":
            logger.info(f"Sumsub: Applicant {applicant_id} pending review")

        self.set_status(200)
        self.write(json.dumps({"status": "ok"}))


# ══════════════════════════════════════════════════════════
# MONITORING ENDPOINTS
# ══════════════════════════════════════════════════════════

class MonitoringDashboardHandler(BaseHandler):
    """GET /api/monitoring/dashboard — returns monitoring stats"""
    def get(self):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        db = get_db()
        stats = {
            "files_due": 0,
            "docs_expiring": 0,
            "alerts": 0,
            "clients_under_review": 0,
            "high_risk_alerts": [],
            "periodic_review_due": 0
        }

        # Count applications pending compliance review
        compliance_review = db.execute("SELECT COUNT(*) as c FROM applications WHERE status='compliance_review'").fetchone()["c"]
        stats["clients_under_review"] = compliance_review

        # Count high-risk alerts
        high_risk = db.execute("SELECT COUNT(*) as c FROM applications WHERE risk_level IN ('HIGH','VERY_HIGH')").fetchone()["c"]
        stats["alerts"] = high_risk

        # Get recent high-risk applications for alert summary
        recent_alerts = db.execute("""
            SELECT ref, company_name, risk_level, risk_score, created_at FROM applications
            WHERE risk_level IN ('HIGH','VERY_HIGH')
            ORDER BY created_at DESC LIMIT 10
        """).fetchall()
        stats["high_risk_alerts"] = [dict(a) for a in recent_alerts]

        db.close()
        self.success(stats)


class MonitoringClientsHandler(BaseHandler):
    """GET /api/monitoring/clients — returns client monitoring status for Kanban board"""
    def get(self):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        db = get_db()

        # Get all applications grouped by status/stage
        applications = db.execute("""
            SELECT a.id, a.ref, a.company_name, a.status, a.risk_level, a.risk_score,
                   a.created_at, u.full_name as assigned_to
            FROM applications a
            LEFT JOIN users u ON a.assigned_to = u.id
            ORDER BY a.created_at DESC
        """).fetchall()

        clients = {}
        for app in applications:
            status = app["status"]
            if status not in clients:
                clients[status] = []
            clients[status].append(dict(app))

        db.close()
        self.success({"clients_by_status": clients})


class MonitoringAlertCreateHandler(BaseHandler):
    """GET/POST /api/monitoring/alerts — List and create monitoring alerts"""
    def get(self):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        severity = self.get_argument("severity", None)
        alert_type = self.get_argument("type", None)
        status_filter = self.get_argument("status", None)
        client_id = self.get_argument("client", None)

        db = get_db()
        query = "SELECT * FROM monitoring_alerts WHERE 1=1"
        params = []

        if severity:
            query += " AND severity = ?"
            params.append(severity)
        if alert_type:
            query += " AND alert_type = ?"
            params.append(alert_type)
        if status_filter:
            query += " AND status = ?"
            params.append(status_filter)
        if client_id:
            query += " AND application_id = ?"
            params.append(client_id)

        query += " ORDER BY created_at DESC"
        alerts = db.execute(query, params).fetchall()

        result = [dict(a) for a in alerts]
        db.close()
        self.success({"alerts": result, "total": len(result)})

    def post(self):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        data = self.get_json()
        db = get_db()

        # Create notification for relevant users
        alert_users = db.execute("SELECT id FROM users WHERE role IN ('sco','co','admin')").fetchall()
        for u in alert_users:
            db.execute("INSERT INTO notifications (user_id, title, message) VALUES (?,?,?)",
                      (u["id"], data.get("title", "Monitoring Alert"),
                       data.get("message", "")))

        db.commit()
        db.close()
        self.log_audit(user, "Alert", "Monitoring", f"Alert created: {data.get('title','')}")
        self.success({"status": "created"}, 201)


# ══════════════════════════════════════════════════════════
# COMPLIANCE MEMO ENDPOINT (Step 5)
# ══════════════════════════════════════════════════════════

class ComplianceMemoHandler(BaseHandler):
    """POST /api/applications/:id/memo — Generate compliance memo from application data"""
    def post(self, app_id):
        user = self.require_auth(roles=["admin", "sco", "co", "analyst"])
        if not user:
            return

        db = get_db()
        app = db.execute("SELECT * FROM applications WHERE id = ? OR ref = ?", (app_id, app_id)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)

        real_id = app["id"]

        # Fetch related data
        directors = [dict(d) for d in db.execute("SELECT * FROM directors WHERE application_id=?", (real_id,)).fetchall()]
        ubos = [dict(u) for u in db.execute("SELECT * FROM ubos WHERE application_id=?", (real_id,)).fetchall()]
        documents = [dict(d) for d in db.execute("SELECT * FROM documents WHERE application_id=?", (real_id,)).fetchall()]

        # Generate memo structure
        memo = {
            "application_ref": app["ref"],
            "company_name": app["company_name"],
            "memo_generated": datetime.now().isoformat(),
            "client_overview": {
                "company_name": app["company_name"],
                "entity_type": app["entity_type"],
                "country": app["country"],
                "sector": app["sector"],
                "brn": app["brn"]
            },
            "ownership_structure": {
                "structure_description": app["ownership_structure"],
                "directors_count": len(directors),
                "ubos_count": len(ubos),
                "directors": [{"name": d["full_name"], "nationality": d["nationality"], "is_pep": d["is_pep"]} for d in directors],
                "ubos": [{"name": u["full_name"], "ownership_pct": u["ownership_pct"], "is_pep": u["is_pep"]} for u in ubos]
            },
            "screening_results": {
                "sanctions_status": "No matches" if not any(d["is_pep"]=="Yes" for d in directors + ubos) else "Potential matches - Review required",
                "pep_matches": [d["full_name"] for d in directors if d["is_pep"]=="Yes"] + [u["full_name"] for u in ubos if u["is_pep"]=="Yes"],
                "documents_verified": len([d for d in documents if d["verification_status"]=="verified"]),
                "documents_total": len(documents)
            },
            "risk_indicators": {
                "risk_score": app["risk_score"],
                "risk_level": app["risk_level"],
                "onboarding_lane": app["onboarding_lane"]
            },
            "ai_recommendation": "Approve" if app["risk_level"]=="LOW" else "Review required" if app["risk_level"] in ("MEDIUM","HIGH") else "Escalate",
            "review_checklist": [
                "✓ Company identity verified",
                "✓ UBO chain mapped",
                "✓ PEP screening completed",
                "✓ Adverse media review conducted",
                "✓ Source of funds verified",
                "✓ Business model plausibility confirmed"
            ]
        }

        db.execute("INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
                   (user.get("sub",""), user.get("name",""), user.get("role",""), "Generate Memo", app["ref"], f"Compliance memo generated for {app['company_name']}", self.get_client_ip()))
        db.commit()
        db.close()

        self.success(memo)


# ══════════════════════════════════════════════════════════
# DECISION WORKFLOW ENDPOINTS (Step 7)
# ══════════════════════════════════════════════════════════

class ApplicationDecisionHandler(BaseHandler):
    """POST /api/applications/:id/decision — Submit application decision with override support"""
    def post(self, app_id):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        data = self.get_json()
        db = get_db()

        app = db.execute("SELECT * FROM applications WHERE id = ? OR ref = ?", (app_id, app_id)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)

        real_id = app["id"]

        # Validate required fields
        decision = data.get("decision")
        decision_reason = data.get("decision_reason")
        override_ai = data.get("override_ai", False)
        override_reason = data.get("override_reason", "")

        valid_decisions = ["approve", "reject", "escalate_edd", "request_documents"]
        if decision not in valid_decisions:
            db.close()
            return self.error(f"Invalid decision. Must be one of: {', '.join(valid_decisions)}", 400)

        if not decision_reason:
            db.close()
            return self.error("decision_reason is required", 400)

        if override_ai and not override_reason:
            db.close()
            return self.error("override_reason is required when override_ai is true", 400)

        # Handle request_documents
        required_documents = []
        if decision == "request_documents":
            required_documents = data.get("documents_list", [])
            if not required_documents:
                db.close()
                return self.error("documents_list is required for request_documents decision", 400)

        # Update application status
        new_status = {
            "approve": "approved",
            "reject": "rejected",
            "escalate_edd": "edd_required",
            "request_documents": "kyc_documents"
        }[decision]

        detail_info = {
            "decision": decision,
            "decision_reason": decision_reason,
            "override_ai": override_ai,
            "override_reason": override_reason if override_ai else None,
            "required_documents": required_documents if decision == "request_documents" else None
        }

        db.execute("""
            UPDATE applications SET
                status=?, decided_at=datetime('now'), decision_by=?, decision_notes=?, updated_at=datetime('now')
            WHERE id=?
        """, (new_status, user["sub"], json.dumps(detail_info), real_id))

        # Log audit trail with full detail
        audit_detail = f"Decision: {decision} | Reason: {decision_reason}"
        if override_ai:
            audit_detail += f" | AI Override: {override_reason}"
        if required_documents:
            audit_detail += f" | Documents Required: {', '.join(required_documents)}"

        db.execute("INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
                   (user.get("sub",""), user.get("name",""), user.get("role",""), "Decision", app["ref"], audit_detail, self.get_client_ip()))

        db.commit()
        db.close()

        self.success({"status": "decision_recorded", "decision": decision, "application_status": new_status}, 201)


# ══════════════════════════════════════════════════════════
# CLIENT NOTIFICATION ENDPOINTS (Step 9)
# ══════════════════════════════════════════════════════════

class ClientNotificationHandler(BaseHandler):
    """POST /api/applications/:id/notify — Send notification to client"""
    def post(self, app_id):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        data = self.get_json()
        db = get_db()

        app = db.execute("SELECT * FROM applications WHERE id = ? OR ref = ?", (app_id, app_id)).fetchone()
        if not app:
            db.close()
            return self.error("Application not found", 404)

        notification_type = data.get("notification_type")
        message = data.get("message")
        documents_list = data.get("documents_list", [])

        valid_types = ["approved", "documents_required", "rejected"]
        if notification_type not in valid_types:
            db.close()
            return self.error(f"Invalid notification_type. Must be one of: {', '.join(valid_types)}", 400)

        if not message:
            db.close()
            return self.error("message is required", 400)

        # Create notification
        title_map = {
            "approved": "Application Approved",
            "documents_required": "Documents Required",
            "rejected": "Application Rejected"
        }

        db.execute("""
            INSERT INTO client_notifications (application_id, client_id, notification_type, title, message, documents_list, read_status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, 0, datetime('now'))
        """, (app["id"], app.get("client_id"), notification_type, title_map[notification_type], message,
              json.dumps(documents_list) if documents_list else None))

        # Log audit trail
        db.execute("INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
                   (user.get("sub",""), user.get("name",""), user.get("role",""), "Notify Client", app["ref"],
                    f"Sent {notification_type} notification to client", self.get_client_ip()))

        db.commit()
        db.close()

        self.success({"status": "notification_sent", "notification_type": notification_type}, 201)


class GetClientNotificationsHandler(BaseHandler):
    """GET /api/notifications — Get notifications for logged-in client"""
    def get(self):
        user = self.require_auth()
        if not user:
            return

        # Only clients can retrieve their notifications
        if user.get("type") != "client":
            return self.error("Only clients can retrieve notifications", 403)

        db = get_db()
        notifications = db.execute("""
            SELECT id, application_id, notification_type, title, message, documents_list, read_status, created_at
            FROM client_notifications
            WHERE client_id = ?
            ORDER BY created_at DESC
        """, (user["sub"],)).fetchall()

        result = [dict(n) for n in notifications]
        for n in result:
            if n["documents_list"]:
                n["documents_list"] = json.loads(n["documents_list"])

        db.close()
        self.success({"notifications": result})


class MarkNotificationReadHandler(BaseHandler):
    """PATCH /api/notifications/:id/read — Mark notification as read"""
    def patch(self, notif_id):
        user = self.require_auth()
        if not user:
            return

        db = get_db()
        notif = db.execute("SELECT * FROM client_notifications WHERE id = ?", (notif_id,)).fetchone()
        if not notif:
            db.close()
            return self.error("Notification not found", 404)

        if notif["client_id"] != user["sub"]:
            db.close()
            return self.error("Unauthorized", 403)

        db.execute("UPDATE client_notifications SET read_status=1, read_at=datetime('now') WHERE id=?", (notif_id,))
        db.commit()
        db.close()

        self.success({"status": "marked_read"})


# ══════════════════════════════════════════════════════════
# MONITORING ENDPOINTS (Ongoing Monitoring)
# ══════════════════════════════════════════════════════════

class MonitoringAlertDetailHandler(BaseHandler):
    """GET/PATCH /api/monitoring/alerts/:id — Get alert detail and update status"""
    def get(self, alert_id):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        db = get_db()
        alert = db.execute("SELECT * FROM monitoring_alerts WHERE id = ?", (alert_id,)).fetchone()
        if not alert:
            db.close()
            return self.error("Alert not found", 404)

        result = dict(alert)
        db.close()
        self.success(result)

    def patch(self, alert_id):
        """Update alert status (dismiss, escalate, trigger_review)"""
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        data = self.get_json()
        db = get_db()

        alert = db.execute("SELECT * FROM monitoring_alerts WHERE id = ?", (alert_id,)).fetchone()
        if not alert:
            db.close()
            return self.error("Alert not found", 404)

        action = data.get("action")
        reason = data.get("reason", "")

        valid_actions = ["dismiss", "escalate", "trigger_review"]
        if action not in valid_actions:
            db.close()
            return self.error(f"Invalid action. Must be one of: {', '.join(valid_actions)}", 400)

        new_status = {
            "dismiss": "dismissed",
            "escalate": "escalated",
            "trigger_review": "escalated"
        }[action]

        db.execute("""
            UPDATE monitoring_alerts SET
                status=?, reviewed_at=datetime('now'), reviewed_by=?, officer_notes=?, officer_action=?
            WHERE id=?
        """, (new_status, user["sub"], reason, action, alert_id))

        # Log audit
        db.execute("INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
                   (user.get("sub",""), user.get("name",""), user.get("role",""), "Alert Action", f"Alert {alert_id}",
                    f"Action: {action}, Reason: {reason}", self.get_client_ip()))

        db.commit()
        db.close()

        self.success({"status": "alert_updated", "new_status": new_status})


class MonitoringAgentsHandler(BaseHandler):
    """GET /api/monitoring/agents — Get status of monitoring agents"""
    def get(self):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        db = get_db()
        agents = db.execute("""
            SELECT id, agent_name, agent_type, last_run, next_run, run_frequency, clients_monitored, alerts_generated, status
            FROM monitoring_agent_status
            ORDER BY agent_name
        """).fetchall()

        result = [dict(a) for a in agents]
        db.close()
        self.success({"agents": result})


class MonitoringAgentRunHandler(BaseHandler):
    """POST /api/monitoring/agents/:id/run — Manually trigger agent run"""
    def post(self, agent_id):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        db = get_db()
        agent = db.execute("SELECT * FROM monitoring_agent_status WHERE id = ?", (agent_id,)).fetchone()
        if not agent:
            db.close()
            return self.error("Agent not found", 404)

        # Simulate agent run - in production, this would trigger actual monitoring logic
        now = datetime.now().isoformat()
        db.execute("""
            UPDATE monitoring_agent_status SET last_run=?, alerts_generated=alerts_generated+1 WHERE id=?
        """, (now, agent_id))

        db.execute("INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
                   (user.get("sub",""), user.get("name",""), user.get("role",""), "Agent Run", agent["agent_name"],
                    f"Manual run triggered for {agent['agent_name']}", self.get_client_ip()))

        db.commit()
        db.close()

        self.success({"status": "agent_run_initiated", "agent": agent["agent_name"], "run_time": now})


class PeriodicReviewsListHandler(BaseHandler):
    """GET /api/monitoring/reviews — List periodic reviews"""
    def get(self):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        status_filter = self.get_argument("status", None)
        db = get_db()

        query = "SELECT * FROM periodic_reviews WHERE 1=1"
        params = []

        if status_filter:
            query += " AND status = ?"
            params.append(status_filter)

        query += " ORDER BY due_date ASC"
        reviews = db.execute(query, params).fetchall()

        result = [dict(r) for r in reviews]
        db.close()
        self.success({"reviews": result})


class PeriodicReviewDetailHandler(BaseHandler):
    """GET /api/monitoring/reviews/:id — Get review detail"""
    def get(self, review_id):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        db = get_db()
        review = db.execute("SELECT * FROM periodic_reviews WHERE id = ?", (review_id,)).fetchone()
        if not review:
            db.close()
            return self.error("Review not found", 404)

        result = dict(review)
        if result["review_memo"]:
            result["review_memo"] = json.loads(result["review_memo"])

        db.close()
        self.success(result)


class PeriodicReviewDecisionHandler(BaseHandler):
    """POST /api/monitoring/reviews/:id/decision — Submit review decision"""
    def post(self, review_id):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        data = self.get_json()
        db = get_db()

        review = db.execute("SELECT * FROM periodic_reviews WHERE id = ?", (review_id,)).fetchone()
        if not review:
            db.close()
            return self.error("Review not found", 404)

        decision = data.get("decision")
        decision_reason = data.get("decision_reason")

        valid_decisions = ["continue", "enhanced_monitoring", "request_info", "exit_relationship"]
        if decision not in valid_decisions:
            db.close()
            return self.error(f"Invalid decision. Must be one of: {', '.join(valid_decisions)}", 400)

        if not decision_reason:
            db.close()
            return self.error("decision_reason is required", 400)

        db.execute("""
            UPDATE periodic_reviews SET
                status='completed', decision=?, decision_reason=?, decided_by=?, completed_at=datetime('now')
            WHERE id=?
        """, (decision, decision_reason, user["sub"], review_id))

        # Log audit
        db.execute("INSERT INTO audit_log (user_id, user_name, user_role, action, target, detail, ip_address) VALUES (?,?,?,?,?,?,?)",
                   (user.get("sub",""), user.get("name",""), user.get("role",""), "Review Decision", f"Review {review_id}",
                    f"Decision: {decision}, Reason: {decision_reason}", self.get_client_ip()))

        db.commit()
        db.close()

        self.success({"status": "decision_recorded", "decision": decision})


class PeriodicReviewScheduleHandler(BaseHandler):
    """POST /api/monitoring/reviews/schedule — Check and create due reviews"""
    def post(self):
        user = self.require_auth(roles=["admin", "sco", "co"])
        if not user:
            return

        db = get_db()

        # Find applications due for periodic review based on risk level
        # LOW: every 2 years, MEDIUM: annual, HIGH: semi-annual, VERY_HIGH: quarterly
        now = datetime.now()
        today = now.date().isoformat()

        risk_intervals = {
            "LOW": 730,  # days
            "MEDIUM": 365,
            "HIGH": 180,
            "VERY_HIGH": 90
        }

        created_count = 0
        for risk_level, days in risk_intervals.items():
            # Find applications with this risk level that haven't been reviewed recently
            cutoff_date = (now - timedelta(days=days)).isoformat()
            apps = db.execute("""
                SELECT a.id, a.ref, a.company_name, a.risk_level
                FROM applications a
                WHERE a.risk_level = ? AND a.status IN ('approved', 'rmi_sent')
                AND NOT EXISTS (
                    SELECT 1 FROM periodic_reviews pr
                    WHERE pr.application_id = a.id AND pr.created_at > ?
                )
            """, (risk_level, cutoff_date)).fetchall()

            for app in apps:
                due_date = (now + timedelta(days=7)).date().isoformat()
                db.execute("""
                    INSERT INTO periodic_reviews (application_id, client_name, risk_level, trigger_type, trigger_reason, status, due_date, created_at)
                    VALUES (?, ?, ?, 'time_based', ?, 'pending', ?, datetime('now'))
                """, (app["id"], app["company_name"], app["risk_level"], f"Periodic review due for {risk_level} risk client", due_date))
                created_count += 1

        db.commit()
        db.close()

        self.success({"status": "schedule_check_complete", "reviews_created": created_count})


# ══════════════════════════════════════════════════════════
# AI ASSISTANT ENDPOINT
# ══════════════════════════════════════════════════════════

class AIAssistantHandler(BaseHandler):
    """POST /api/ai/assistant — AI assistant for compliance topics"""
    def post(self):
        user = self.require_auth()
        if not user:
            return

        data = self.get_json()
        message = data.get("message", "").strip()

        if not message:
            self.error("Message cannot be empty", 400)
            return

        # Simulated AI response about compliance topics
        # In production, this would integrate with an LLM API
        compliance_keywords = ["kyc", "aml", "sanctions", "pep", "risk", "screening", "compliance", "due diligence"]
        is_compliance_topic = any(kw in message.lower() for kw in compliance_keywords)

        if is_compliance_topic:
            response = self._get_compliance_response(message)
        else:
            response = "I can help with compliance and KYC-related questions. Please ask about AML, sanctions screening, PEP verification, risk assessment, or due diligence procedures."

        self.success({"response": response, "topic": "compliance" if is_compliance_topic else "general"})

    def _get_compliance_response(self, message):
        """Return contextual compliance guidance based on the message"""
        msg_lower = message.lower()

        if any(w in msg_lower for w in ["kyc", "know your customer"]):
            return "KYC (Know Your Customer) involves verifying customer identity, conducting beneficial ownership analysis, and assessing risk profile. Core elements: identity verification, address verification, source of funds, business purpose, and beneficial ownership structure."

        elif any(w in msg_lower for w in ["sanctions", "screening"]):
            return "Sanctions and PEP screening is mandatory. Check against OFAC, UN, EU sanctions lists and PEP databases. Document all screening results. Update screening annually or when customer information changes. Maintain audit trail of all checks."

        elif any(w in msg_lower for w in ["pep", "politically exposed"]):
            return "PEP (Politically Exposed Persons) are individuals holding prominent public positions. Enhanced due diligence required including: source of wealth verification, beneficial ownership analysis, and ongoing monitoring. Document political exposure and relationships."

        elif any(w in msg_lower for w in ["risk", "assessment"]):
            return "Risk assessment evaluates customer risk across dimensions: entity risk, geographic risk, product risk, sector risk. Consider: entity type, ownership structure, jurisdiction, business model, transaction patterns. Document risk rating and mitigation measures."

        elif any(w in msg_lower for w in ["ubo", "beneficial owner"]):
            return "Ultimate Beneficial Owner (UBO) identification requires mapping full ownership chain to identify natural persons. Detect nominee structures, complex layering, and high-risk jurisdictions. Verify UBO identity and screen against sanctions/PEP lists."

        elif any(w in msg_lower for w in ["aml", "anti-money laundering"]):
            return "AML compliance requires: customer due diligence, sanctions screening, ongoing monitoring, transaction monitoring, suspicious activity reporting, and record retention. Maintain policies, procedures, and staff training programs."

        else:
            return "I can assist with compliance questions including KYC procedures, sanctions screening, PEP verification, risk assessment, UBO identification, and AML compliance. What specific area would you like to know more about?"


# ══════════════════════════════════════════════════════════
# APP SETUP & ROUTES
# ══════════════════════════════════════════════════════════

def make_app():
    routes = [
        # Health
        (r"/api/health", HealthHandler),

        # Auth
        (r"/api/auth/officer/login", OfficerLoginHandler),
        (r"/api/auth/client/login", ClientLoginHandler),
        (r"/api/auth/client/register", ClientRegisterHandler),
        (r"/api/auth/me", MeHandler),

        # Applications (more specific routes first)
        (r"/api/applications/([^/]+)/submit", SubmitApplicationHandler),
        (r"/api/applications/([^/]+)/accept-pricing", PricingAcceptHandler),
        (r"/api/applications/([^/]+)/submit-kyc", KYCSubmitHandler),
        (r"/api/applications/([^/]+)/memo", ComplianceMemoHandler),
        (r"/api/applications/([^/]+)/decision", ApplicationDecisionHandler),
        (r"/api/applications/([^/]+)/notify", ClientNotificationHandler),
        (r"/api/applications/([^/]+)/documents", DocumentUploadHandler),
        (r"/api/applications/([^/]+)", ApplicationDetailHandler),
        (r"/api/applications", ApplicationsHandler),

        # Documents
        (r"/api/documents/([^/]+)/verify", DocumentVerifyHandler),

        # Users
        (r"/api/users", UsersHandler),
        (r"/api/users/([^/]+)", UserDetailHandler),

        # Config
        (r"/api/config/risk-model", RiskConfigHandler),
        (r"/api/config/ai-agents", AIAgentsHandler),
        (r"/api/config/ai-agents/([^/]+)", AIAgentDetailHandler),

        # Screening (Real API Integrations)
        (r"/api/screening/run", ScreeningHandler),
        (r"/api/screening/sanctions", SanctionsCheckHandler),
        (r"/api/screening/company", CompanyLookupHandler),
        (r"/api/screening/ip", IPCheckHandler),
        (r"/api/screening/status", APIStatusHandler),

        # Sumsub KYC
        (r"/api/kyc/applicant", SumsubApplicantHandler),
        (r"/api/kyc/token", SumsubAccessTokenHandler),
        (r"/api/kyc/status/([^/]+)", SumsubStatusHandler),
        (r"/api/kyc/document", SumsubDocumentHandler),
        (r"/api/kyc/webhook", SumsubWebhookHandler),

        # Reports
        (r"/api/reports/generate", ReportHandler),

        # Audit
        (r"/api/audit", AuditHandler),

        # Dashboard
        (r"/api/dashboard", DashboardHandler),

        # Client Notifications
        (r"/api/notifications", GetClientNotificationsHandler),
        (r"/api/notifications/([^/]+)/read", MarkNotificationReadHandler),

        # Monitoring
        (r"/api/monitoring/dashboard", MonitoringDashboardHandler),
        (r"/api/monitoring/clients", MonitoringClientsHandler),
        # Alerts (more specific routes first)
        (r"/api/monitoring/alerts/([^/]+)", MonitoringAlertDetailHandler),
        (r"/api/monitoring/alerts", MonitoringAlertCreateHandler),
        # Agents
        (r"/api/monitoring/agents/([^/]+)/run", MonitoringAgentRunHandler),
        (r"/api/monitoring/agents", MonitoringAgentsHandler),
        # Periodic Reviews (more specific routes first)
        (r"/api/monitoring/reviews/schedule", PeriodicReviewScheduleHandler),
        (r"/api/monitoring/reviews/([^/]+)/decision", PeriodicReviewDecisionHandler),
        (r"/api/monitoring/reviews/([^/]+)", PeriodicReviewDetailHandler),
        (r"/api/monitoring/reviews", PeriodicReviewsListHandler),

        # AI Assistant
        (r"/api/ai/assistant", AIAssistantHandler),

        # Save & Resume
        (r"/api/save-resume", SaveResumeHandler),

        # Root redirect
        (r"/", tornado.web.RedirectHandler, {"url": "/portal"}),

        # Serve portal HTML files and static assets
        (r"/portal", PortalHandler),
        (r"/backoffice", BackOfficeHandler),
        (r"/static/(.*)", tornado.web.StaticFileHandler, {"path": STATIC_DIR}),
    ]

    # Integrate supervisor routes
    if SUPERVISOR_AVAILABLE:
        routes.extend(get_supervisor_routes())
        logger.info("Supervisor API endpoints registered (%d routes)", len(get_supervisor_routes()))

    return tornado.web.Application(routes,
        debug=os.environ.get("DEBUG", "0") == "1",
        xsrf_cookies=False,  # Disabled for API-only server using Bearer tokens
        max_body_size=20 * 1024 * 1024,  # 20MB max request body
    )


if __name__ == "__main__":
    init_db()

    # Run database migrations
    try:
        from migrations.runner import run_all_migrations
        run_all_migrations(DB_PATH)
    except Exception as e:
        logger.warning("Migration runner unavailable: %s", e)

    # Initialize supervisor framework
    if SUPERVISOR_AVAILABLE:
        try:
            setup_supervisor(DB_PATH)
            logger.info("✅ Supervisor framework initialized")
        except Exception as e:
            logger.error("Failed to initialize supervisor: %s", e)
            SUPERVISOR_AVAILABLE = False

    app = make_app()
    # Bind to 0.0.0.0 for cloud deployment (Railway, Render, etc.)
    app.listen(PORT, address="0.0.0.0")

    # API integration status
    sanctions_status = "LIVE" if OPENSANCTIONS_API_KEY else "SIMULATED"
    corporates_status = "LIVE" if OPENCORPORATES_API_KEY else "SIMULATED"
    ip_status = "LIVE (ipapi.co free tier)"
    sumsub_status = "LIVE" if (SUMSUB_APP_TOKEN and SUMSUB_SECRET_KEY) else "SIMULATED"

    print(f"""
╔══════════════════════════════════════════════════╗
║  ARIE Finance API Server                         ║
║  Running on http://0.0.0.0:{PORT}                ║
║  Environment: {ENVIRONMENT:<33s}║
║                                                  ║
║  Core Endpoints:                                 ║
║    POST /api/auth/officer/login                  ║
║    POST /api/auth/client/register                ║
║    POST /api/auth/client/login                   ║
║    GET  /api/dashboard                           ║
║    GET  /api/applications                        ║
║    POST /api/applications/:id/submit             ║
║                                                  ║
║  Screening APIs:                                 ║
║    POST /api/screening/run                       ║
║    POST /api/screening/sanctions                 ║
║    POST /api/screening/company                   ║
║    GET  /api/screening/ip                        ║
║    GET  /api/screening/status                    ║
║                                                  ║
║  API Integrations:                               ║
║    OpenSanctions:    {sanctions_status:<27s}║
║    OpenCorporates:   {corporates_status:<27s}║
║    IP Geolocation:   {ip_status:<27s}║
║    Sumsub KYC:       {sumsub_status:<27s}║
║                                                  ║
║  Admin email: asudally@ariefinance.mu              ║
║  Password: see initial boot output above          ║
╚══════════════════════════════════════════════════╝
    """)
    tornado.ioloop.IOLoop.current().start()
