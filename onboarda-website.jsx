import { useState, useEffect, useRef } from "react";

// ─── Intersection Observer Hook ──────────────────────────
function useInView(threshold = 0.15) {
  const [inView, setInView] = useState(false);
  const ref = useRef(null);
  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const obs = new IntersectionObserver(
      ([e]) => { if (e.isIntersecting) { setInView(true); obs.unobserve(el); } },
      { threshold }
    );
    obs.observe(el);
    return () => obs.disconnect();
  }, [threshold]);
  return [ref, inView];
}

function FadeIn({ children, delay = 0, className = "" }) {
  const [ref, inView] = useInView();
  return (
    <div
      ref={ref}
      className={className}
      style={{
        opacity: inView ? 1 : 0,
        transform: inView ? "translateY(0)" : "translateY(28px)",
        transition: `opacity 0.7s cubic-bezier(.16,1,.3,1) ${delay}s, transform 0.7s cubic-bezier(.16,1,.3,1) ${delay}s`,
      }}
    >
      {children}
    </div>
  );
}

// ─── Icons (inline SVG) ──────────────────────────────────
const Icons = {
  shield: (
    <svg width="24" height="24" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.5">
      <path strokeLinecap="round" strokeLinejoin="round" d="M9 12.75L11.25 15 15 9.75m-3-7.036A11.959 11.959 0 013.598 6 11.99 11.99 0 003 9.749c0 5.592 3.824 10.29 9 11.623 5.176-1.332 9-6.03 9-11.622 0-1.31-.21-2.571-.598-3.751h-.152c-3.196 0-6.1-1.248-8.25-3.285z" />
    </svg>
  ),
  brain: (
    <svg width="24" height="24" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.5">
      <path strokeLinecap="round" strokeLinejoin="round" d="M9.813 15.904L9 18.75l-.813-2.846a4.5 4.5 0 00-3.09-3.09L2.25 12l2.846-.813a4.5 4.5 0 003.09-3.09L9 5.25l.813 2.846a4.5 4.5 0 003.09 3.09L15.75 12l-2.846.813a4.5 4.5 0 00-3.09 3.09zM18.259 8.715L18 9.75l-.259-1.035a3.375 3.375 0 00-2.455-2.456L14.25 6l1.036-.259a3.375 3.375 0 002.455-2.456L18 2.25l.259 1.035a3.375 3.375 0 002.455 2.456L21.75 6l-1.036.259a3.375 3.375 0 00-2.455 2.456zM16.894 20.567L16.5 21.75l-.394-1.183a2.25 2.25 0 00-1.423-1.423L13.5 18.75l1.183-.394a2.25 2.25 0 001.423-1.423l.394-1.183.394 1.183a2.25 2.25 0 001.423 1.423l1.183.394-1.183.394a2.25 2.25 0 00-1.423 1.423z" />
    </svg>
  ),
  doc: (
    <svg width="24" height="24" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.5">
      <path strokeLinecap="round" strokeLinejoin="round" d="M19.5 14.25v-2.625a3.375 3.375 0 00-3.375-3.375h-1.5A1.125 1.125 0 0113.5 7.125v-1.5a3.375 3.375 0 00-3.375-3.375H8.25m0 12.75h7.5m-7.5 3H12M10.5 2.25H5.625c-.621 0-1.125.504-1.125 1.125v17.25c0 .621.504 1.125 1.125 1.125h12.75c.621 0 1.125-.504 1.125-1.125V11.25a9 9 0 00-9-9z" />
    </svg>
  ),
  users: (
    <svg width="24" height="24" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.5">
      <path strokeLinecap="round" strokeLinejoin="round" d="M18 18.72a9.094 9.094 0 003.741-.479 3 3 0 00-4.682-2.72m.94 3.198l.001.031c0 .225-.012.447-.037.666A11.944 11.944 0 0112 21c-2.17 0-4.207-.576-5.963-1.584A6.062 6.062 0 016 18.719m12 0a5.971 5.971 0 00-.941-3.197m0 0A5.995 5.995 0 0012 12.75a5.995 5.995 0 00-5.058 2.772m0 0a3 3 0 00-4.681 2.72 8.986 8.986 0 003.74.477m.94-3.197a5.971 5.971 0 00-.94 3.197M15 6.75a3 3 0 11-6 0 3 3 0 016 0zm6 3a2.25 2.25 0 11-4.5 0 2.25 2.25 0 014.5 0zm-13.5 0a2.25 2.25 0 11-4.5 0 2.25 2.25 0 014.5 0z" />
    </svg>
  ),
  search: (
    <svg width="24" height="24" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.5">
      <path strokeLinecap="round" strokeLinejoin="round" d="M21 21l-5.197-5.197m0 0A7.5 7.5 0 105.196 5.196a7.5 7.5 0 0010.607 10.607z" />
    </svg>
  ),
  chart: (
    <svg width="24" height="24" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.5">
      <path strokeLinecap="round" strokeLinejoin="round" d="M3 13.125C3 12.504 3.504 12 4.125 12h2.25c.621 0 1.125.504 1.125 1.125v6.75C7.5 20.496 6.996 21 6.375 21h-2.25A1.125 1.125 0 013 19.875v-6.75zM9.75 8.625c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125v11.25c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V8.625zM16.5 4.125c0-.621.504-1.125 1.125-1.125h2.25C20.496 3 21 3.504 21 4.125v15.75c0 .621-.504 1.125-1.125 1.125h-2.25a1.125 1.125 0 01-1.125-1.125V4.125z" />
    </svg>
  ),
  eye: (
    <svg width="24" height="24" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.5">
      <path strokeLinecap="round" strokeLinejoin="round" d="M2.036 12.322a1.012 1.012 0 010-.639C3.423 7.51 7.36 4.5 12 4.5c4.638 0 8.573 3.007 9.963 7.178.07.207.07.431 0 .639C20.577 16.49 16.64 19.5 12 19.5c-4.638 0-8.573-3.007-9.963-7.178z" />
      <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
    </svg>
  ),
  clock: (
    <svg width="24" height="24" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.5">
      <path strokeLinecap="round" strokeLinejoin="round" d="M12 6v6h4.5m4.5 0a9 9 0 11-18 0 9 9 0 0118 0z" />
    </svg>
  ),
  check: (
    <svg width="24" height="24" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
      <path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" />
    </svg>
  ),
  arrow: (
    <svg width="16" height="16" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2">
      <path strokeLinecap="round" strokeLinejoin="round" d="M13.5 4.5L21 12m0 0l-7.5 7.5M21 12H3" />
    </svg>
  ),
  building: (
    <svg width="24" height="24" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="1.5">
      <path strokeLinecap="round" strokeLinejoin="round" d="M2.25 21h19.5m-18-18v18m10.5-18v18m6-13.5V21M6.75 6.75h.75m-.75 3h.75m-.75 3h.75m3-6h.75m-.75 3h.75m-.75 3h.75M6.75 21v-3.375c0-.621.504-1.125 1.125-1.125h2.25c.621 0 1.125.504 1.125 1.125V21M3 3h12m-.75 4.5H21m-3.75 3h.008v.008h-.008v-.008zm0 3h.008v.008h-.008v-.008zm0 3h.008v.008h-.008v-.008z" />
    </svg>
  ),
};

// ─── Dashboard Mockup Component ──────────────────────────
function DashboardMockup() {
  const rows = [
    { ref: "ARF-2026-100421", company: "Meridian Holdings", risk: "LOW", score: 22, status: "Approved", color: "#16a34a" },
    { ref: "ARF-2026-100422", company: "NovaPay Ltd", risk: "HIGH", score: 68, status: "Pre-Approval", color: "#d97706" },
    { ref: "ARF-2026-100423", company: "Atlas Ventures", risk: "MEDIUM", score: 41, status: "KYC Review", color: "#2563eb" },
    { ref: "ARF-2026-100424", company: "Zenith Capital", risk: "VERY HIGH", score: 82, status: "Escalated", color: "#dc2626" },
    { ref: "ARF-2026-100425", company: "Pacific Trade Co", risk: "LOW", score: 18, status: "Approved", color: "#16a34a" },
  ];
  const riskColors = { LOW: "#16a34a", MEDIUM: "#2563eb", HIGH: "#d97706", "VERY HIGH": "#dc2626" };

  return (
    <div style={{
      background: "#0c0e14", borderRadius: 16, border: "1px solid rgba(255,255,255,0.08)",
      overflow: "hidden", boxShadow: "0 40px 80px rgba(0,0,0,0.5), 0 0 0 1px rgba(255,255,255,0.05)",
      fontFamily: "'Inter', system-ui, sans-serif", maxWidth: 720, width: "100%",
    }}>
      {/* Title bar */}
      <div style={{ display: "flex", alignItems: "center", gap: 8, padding: "14px 20px", borderBottom: "1px solid rgba(255,255,255,0.06)", background: "rgba(255,255,255,0.02)" }}>
        <div style={{ width: 12, height: 12, borderRadius: "50%", background: "#ff5f57" }} />
        <div style={{ width: 12, height: 12, borderRadius: "50%", background: "#febc2e" }} />
        <div style={{ width: 12, height: 12, borderRadius: "50%", background: "#28c840" }} />
        <span style={{ marginLeft: 12, fontSize: 12, color: "rgba(255,255,255,0.35)", fontWeight: 500 }}>Onboarda — Back Office</span>
      </div>
      {/* Stats row */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: 1, padding: "16px 20px 12px", borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
        {[
          { label: "Active Clients", value: "247", trend: "+12%" },
          { label: "Pending Review", value: "14", trend: "" },
          { label: "Pre-Approval Queue", value: "3", trend: "" },
          { label: "Avg. Risk Score", value: "34.2", trend: "-2.1" },
        ].map((s, i) => (
          <div key={i} style={{ textAlign: i === 0 ? "left" : "center" }}>
            <div style={{ fontSize: 10, color: "rgba(255,255,255,0.4)", fontWeight: 500, textTransform: "uppercase", letterSpacing: 0.5 }}>{s.label}</div>
            <div style={{ fontSize: 22, fontWeight: 700, color: "#fff", marginTop: 2 }}>{s.value}
              {s.trend && <span style={{ fontSize: 11, fontWeight: 500, color: s.trend.startsWith("+") ? "#16a34a" : s.trend.startsWith("-") ? "#dc2626" : "#888", marginLeft: 6 }}>{s.trend}</span>}
            </div>
          </div>
        ))}
      </div>
      {/* Table */}
      <div style={{ padding: "0 0 4px" }}>
        <div style={{ display: "grid", gridTemplateColumns: "130px 1fr 90px 60px 110px", padding: "10px 20px", fontSize: 10, fontWeight: 600, color: "rgba(255,255,255,0.3)", textTransform: "uppercase", letterSpacing: 0.5, borderBottom: "1px solid rgba(255,255,255,0.06)" }}>
          <span>Reference</span><span>Company</span><span>Risk</span><span>Score</span><span>Status</span>
        </div>
        {rows.map((r, i) => (
          <div key={i} style={{
            display: "grid", gridTemplateColumns: "130px 1fr 90px 60px 110px", padding: "11px 20px",
            fontSize: 13, color: "rgba(255,255,255,0.75)", borderBottom: "1px solid rgba(255,255,255,0.04)",
            transition: "background 0.15s", cursor: "pointer",
          }}
            onMouseEnter={e => e.currentTarget.style.background = "rgba(255,255,255,0.03)"}
            onMouseLeave={e => e.currentTarget.style.background = "transparent"}
          >
            <span style={{ fontFamily: "monospace", fontSize: 11, color: "rgba(255,255,255,0.5)" }}>{r.ref}</span>
            <span style={{ fontWeight: 500, color: "#fff" }}>{r.company}</span>
            <span><span style={{ padding: "2px 8px", borderRadius: 10, fontSize: 11, fontWeight: 600, background: `${riskColors[r.risk]}18`, color: riskColors[r.risk] }}>{r.risk}</span></span>
            <span style={{ fontWeight: 700 }}>{r.score}</span>
            <span style={{ color: r.color, fontWeight: 500, fontSize: 12 }}>{r.status}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Main Website Component ──────────────────────────────
export default function OnboardaWebsite() {
  const [scrolled, setScrolled] = useState(false);
  const [mobileMenu, setMobileMenu] = useState(false);

  useEffect(() => {
    const h = () => setScrolled(window.scrollY > 20);
    window.addEventListener("scroll", h);
    return () => window.removeEventListener("scroll", h);
  }, []);

  const features = [
    { icon: Icons.brain, title: "AI Risk Scoring", desc: "Five-dimension weighted risk engine with explainable outputs. Every score has a clear rationale." },
    { icon: Icons.doc, title: "KYC & Document Automation", desc: "Structured document collection with AI verification. Passport, PoA, certificates — validated automatically." },
    { icon: Icons.users, title: "UBO & Structure Mapping", desc: "Multi-layer beneficial ownership analysis. Intermediary shareholders, PEP checks, ownership chains." },
    { icon: Icons.search, title: "Screening & Adverse Media", desc: "Real-time sanctions, PEP, and adverse media screening against global watchlists. OpenSanctions integrated." },
    { icon: Icons.doc, title: "Compliance Memo Generation", desc: "AI-generated compliance memos synthesising all agent findings into a regulator-ready narrative." },
    { icon: Icons.clock, title: "Ongoing Monitoring", desc: "Continuous client monitoring with periodic reviews, risk drift detection, and automated alerting." },
    { icon: Icons.eye, title: "Audit Trail & Explainability", desc: "Every decision logged. Every AI output explained. Full agent decision trail for regulatory inspection." },
  ];

  const problems = [
    { icon: Icons.clock, title: "Onboarding takes weeks", desc: "Manual forms, email chains, and spreadsheet tracking slow everything down." },
    { icon: Icons.doc, title: "Compliance is manual", desc: "Officers spend hours on tasks that should take minutes. Review quality varies." },
    { icon: Icons.chart, title: "Scaling is painful", desc: "Adding clients means adding headcount. The process doesn't scale." },
    { icon: Icons.eye, title: "Audit trails are fragile", desc: "Scattered records across emails, drives, and legacy systems. Regulators aren't impressed." },
  ];

  const workflowSteps = [
    { label: "Client Applies", sub: "Pre-screening data collected", num: "01" },
    { label: "AI Agents Run", sub: "5 agents score risk in parallel", num: "02" },
    { label: "Risk Routing", sub: "Low risk fast-tracked, high risk gated", num: "03" },
    { label: "Compliance Review", sub: "Officer reviews with full AI context", num: "04" },
    { label: "Decision", sub: "Approve, reject, or escalate", num: "05" },
    { label: "Monitoring", sub: "Ongoing surveillance activated", num: "06" },
  ];

  return (
    <div style={{ fontFamily: "'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif", color: "#0f1117", background: "#fff", overflowX: "hidden" }}>
      <style>{`
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap');
        * { margin: 0; padding: 0; box-sizing: border-box; }
        html { scroll-behavior: smooth; }
        ::selection { background: rgba(99,102,241,0.2); }
        .btn-primary {
          display: inline-flex; align-items: center; gap: 8px;
          padding: 14px 32px; border-radius: 12px; font-weight: 600; font-size: 15px;
          background: linear-gradient(135deg, #4f46e5, #6366f1); color: #fff;
          border: none; cursor: pointer; transition: all 0.25s cubic-bezier(.16,1,.3,1);
          box-shadow: 0 4px 14px rgba(79,70,229,0.35), 0 0 0 0 rgba(99,102,241,0);
          text-decoration: none;
        }
        .btn-primary:hover {
          transform: translateY(-2px);
          box-shadow: 0 8px 25px rgba(79,70,229,0.45), 0 0 0 3px rgba(99,102,241,0.15);
        }
        .btn-secondary {
          display: inline-flex; align-items: center; gap: 8px;
          padding: 14px 32px; border-radius: 12px; font-weight: 600; font-size: 15px;
          background: transparent; color: #0f1117; border: 1.5px solid rgba(15,17,23,0.15);
          cursor: pointer; transition: all 0.25s; text-decoration: none;
        }
        .btn-secondary:hover { border-color: #4f46e5; color: #4f46e5; transform: translateY(-2px); }
        .section { padding: 100px 24px; max-width: 1200px; margin: 0 auto; }
        .section-dark { background: #0c0e14; color: #fff; }
        .section-subtle { background: #f8f9fb; }
        .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 48px; align-items: center; }
        .grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 24px; }
        .grid-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; }
        @media (max-width: 900px) {
          .grid-2, .grid-3, .grid-4 { grid-template-columns: 1fr; }
          .hero-grid { flex-direction: column !important; }
          .section { padding: 64px 20px; }
          .hide-mobile { display: none !important; }
        }
        @media (max-width: 600px) {
          .hero-headline { font-size: 36px !important; }
        }
        .feature-card {
          padding: 32px; border-radius: 16px; border: 1px solid rgba(15,17,23,0.07);
          background: #fff; transition: all 0.3s cubic-bezier(.16,1,.3,1);
        }
        .feature-card:hover {
          transform: translateY(-4px); border-color: rgba(79,70,229,0.2);
          box-shadow: 0 20px 40px rgba(0,0,0,0.06);
        }
        .gradient-text {
          background: linear-gradient(135deg, #4f46e5, #8b5cf6, #6366f1);
          -webkit-background-clip: text; -webkit-text-fill-color: transparent;
          background-clip: text;
        }
        .workflow-line { position: absolute; top: 20px; left: 40px; right: 40px; height: 2px; background: linear-gradient(90deg, #4f46e5, #8b5cf6); z-index: 0; }
        .nav-link { color: rgba(15,17,23,0.6); font-size: 14px; font-weight: 500; text-decoration: none; transition: color 0.2s; cursor: pointer; }
        .nav-link:hover { color: #4f46e5; }
      `}</style>

      {/* ═══ STICKY HEADER ═══ */}
      <header style={{
        position: "fixed", top: 0, left: 0, right: 0, zIndex: 100,
        padding: scrolled ? "12px 24px" : "18px 24px",
        background: scrolled ? "rgba(255,255,255,0.92)" : "transparent",
        backdropFilter: scrolled ? "blur(20px) saturate(180%)" : "none",
        borderBottom: scrolled ? "1px solid rgba(15,17,23,0.06)" : "1px solid transparent",
        transition: "all 0.35s cubic-bezier(.16,1,.3,1)",
      }}>
        <div style={{ maxWidth: 1200, margin: "0 auto", display: "flex", alignItems: "center", justifyContent: "space-between" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <div style={{
              width: 34, height: 34, borderRadius: 10,
              background: "linear-gradient(135deg, #4f46e5, #6366f1)",
              display: "flex", alignItems: "center", justifyContent: "center",
              color: "#fff", fontWeight: 800, fontSize: 16,
            }}>O</div>
            <span style={{ fontSize: 18, fontWeight: 700, letterSpacing: -0.5 }}>Onboarda</span>
          </div>
          <nav className="hide-mobile" style={{ display: "flex", gap: 32 }}>
            <a className="nav-link" href="#features">Features</a>
            <a className="nav-link" href="#workflow">Workflow</a>
            <a className="nav-link" href="#management">Management Cos</a>
            <a className="nav-link" href="#platform">Platform</a>
          </nav>
          <div style={{ display: "flex", gap: 12 }}>
            <a className="btn-primary" href="#demo" style={{ padding: "10px 24px", fontSize: 13 }}>Request Demo</a>
          </div>
        </div>
      </header>

      {/* ═══ HERO ═══ */}
      <section style={{
        padding: "160px 24px 100px", minHeight: "100vh",
        background: "radial-gradient(ellipse 80% 60% at 50% -10%, rgba(99,102,241,0.12), transparent 70%), radial-gradient(ellipse 60% 40% at 80% 20%, rgba(139,92,246,0.08), transparent), #fff",
        display: "flex", alignItems: "center",
      }}>
        <div style={{ maxWidth: 1200, margin: "0 auto", width: "100%" }}>
          <div className="hero-grid" style={{ display: "flex", gap: 64, alignItems: "center" }}>
            <div style={{ flex: "1 1 480px" }}>
              <FadeIn>
                <div style={{ display: "inline-flex", alignItems: "center", gap: 8, padding: "6px 14px", borderRadius: 20, background: "rgba(79,70,229,0.08)", marginBottom: 24 }}>
                  <div style={{ width: 6, height: 6, borderRadius: "50%", background: "#4f46e5" }} />
                  <span style={{ fontSize: 13, fontWeight: 600, color: "#4f46e5" }}>RegTech Infrastructure</span>
                </div>
              </FadeIn>
              <FadeIn delay={0.1}>
                <h1 className="hero-headline" style={{ fontSize: 56, fontWeight: 800, lineHeight: 1.08, letterSpacing: -2, marginBottom: 24 }}>
                  Compliance.<br />
                  <span className="gradient-text">Automated.</span><br />
                  Explained.
                </h1>
              </FadeIn>
              <FadeIn delay={0.2}>
                <p style={{ fontSize: 18, lineHeight: 1.7, color: "rgba(15,17,23,0.6)", maxWidth: 480, marginBottom: 36 }}>
                  Onboard clients faster while maintaining full regulatory control. AI-powered onboarding, risk scoring, and compliance workflows — in one platform.
                </p>
              </FadeIn>
              <FadeIn delay={0.3}>
                <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
                  <a className="btn-primary" href="#demo">Request Demo {Icons.arrow}</a>
                  <a className="btn-secondary" href="#platform">View Platform</a>
                </div>
              </FadeIn>
              <FadeIn delay={0.4}>
                <div style={{ display: "flex", gap: 32, marginTop: 48 }}>
                  {[
                    { val: "5", label: "AI Agents" },
                    { val: "< 24h", label: "Avg. Onboarding" },
                    { val: "100%", label: "Audit Coverage" },
                  ].map((s, i) => (
                    <div key={i}>
                      <div style={{ fontSize: 24, fontWeight: 800, color: "#0f1117" }}>{s.val}</div>
                      <div style={{ fontSize: 12, color: "rgba(15,17,23,0.45)", fontWeight: 500, marginTop: 2 }}>{s.label}</div>
                    </div>
                  ))}
                </div>
              </FadeIn>
            </div>
            <FadeIn delay={0.3} className="hide-mobile" style={{ flex: "1 1 500px" }}>
              <DashboardMockup />
            </FadeIn>
          </div>
        </div>
      </section>

      {/* ═══ SOCIAL PROOF BAR ═══ */}
      <div style={{ borderTop: "1px solid rgba(15,17,23,0.06)", borderBottom: "1px solid rgba(15,17,23,0.06)", padding: "24px 24px" }}>
        <div style={{ maxWidth: 1200, margin: "0 auto", display: "flex", alignItems: "center", justifyContent: "center", gap: 48, flexWrap: "wrap" }}>
          {["AML/CFT Compliant", "FATF Aligned", "Risk-Based Approach", "Regulator-Ready", "SOC 2 Architecture"].map((t, i) => (
            <div key={i} style={{ display: "flex", alignItems: "center", gap: 8, color: "rgba(15,17,23,0.35)", fontSize: 13, fontWeight: 500 }}>
              <div style={{ color: "#4f46e5" }}>{Icons.check}</div>
              {t}
            </div>
          ))}
        </div>
      </div>

      {/* ═══ PROBLEM ═══ */}
      <section className="section">
        <FadeIn>
          <div style={{ textAlign: "center", marginBottom: 64 }}>
            <p style={{ fontSize: 13, fontWeight: 600, color: "#4f46e5", textTransform: "uppercase", letterSpacing: 1.5, marginBottom: 12 }}>The Problem</p>
            <h2 style={{ fontSize: 40, fontWeight: 800, letterSpacing: -1.5 }}>Compliance shouldn't be a bottleneck</h2>
            <p style={{ fontSize: 17, color: "rgba(15,17,23,0.55)", marginTop: 16, maxWidth: 560, margin: "16px auto 0" }}>
              Traditional onboarding is slow, manual, and doesn't scale. Every new client creates more risk — not less.
            </p>
          </div>
        </FadeIn>
        <div className="grid-4">
          {problems.map((p, i) => (
            <FadeIn key={i} delay={i * 0.1}>
              <div style={{ padding: 28, borderRadius: 14, border: "1px solid rgba(15,17,23,0.06)", background: "#fff" }}>
                <div style={{ width: 44, height: 44, borderRadius: 12, background: "rgba(220,38,38,0.06)", display: "flex", alignItems: "center", justifyContent: "center", color: "#dc2626", marginBottom: 16 }}>
                  {p.icon}
                </div>
                <h3 style={{ fontSize: 16, fontWeight: 700, marginBottom: 8 }}>{p.title}</h3>
                <p style={{ fontSize: 14, color: "rgba(15,17,23,0.55)", lineHeight: 1.6 }}>{p.desc}</p>
              </div>
            </FadeIn>
          ))}
        </div>
      </section>

      {/* ═══ SOLUTION ═══ */}
      <section className="section-subtle">
        <div className="section">
          <div className="grid-2">
            <FadeIn>
              <div>
                <p style={{ fontSize: 13, fontWeight: 600, color: "#4f46e5", textTransform: "uppercase", letterSpacing: 1.5, marginBottom: 12 }}>The Solution</p>
                <h2 style={{ fontSize: 40, fontWeight: 800, letterSpacing: -1.5, marginBottom: 20 }}>
                  A Compliance<br /><span className="gradient-text">Operating System</span>
                </h2>
                <p style={{ fontSize: 17, color: "rgba(15,17,23,0.55)", lineHeight: 1.75, marginBottom: 32 }}>
                  Onboarda replaces spreadsheets, email chains, and disconnected tools with a single platform that automates the entire compliance lifecycle — from first application to ongoing monitoring.
                </p>
                <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
                  {[
                    "Reduce onboarding from weeks to hours",
                    "Five AI agents working in parallel",
                    "Every decision explainable and auditable",
                    "Human-in-the-loop approvals at every stage",
                  ].map((t, i) => (
                    <div key={i} style={{ display: "flex", gap: 12, alignItems: "flex-start" }}>
                      <div style={{ width: 22, height: 22, borderRadius: 6, background: "rgba(79,70,229,0.1)", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0, marginTop: 2 }}>
                        <svg width="12" height="12" fill="none" viewBox="0 0 24 24" stroke="#4f46e5" strokeWidth="3"><path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" /></svg>
                      </div>
                      <span style={{ fontSize: 15, color: "rgba(15,17,23,0.7)", fontWeight: 500 }}>{t}</span>
                    </div>
                  ))}
                </div>
              </div>
            </FadeIn>
            <FadeIn delay={0.2} className="hide-mobile">
              <div style={{
                background: "#0c0e14", borderRadius: 16, padding: 32,
                border: "1px solid rgba(255,255,255,0.08)",
                boxShadow: "0 30px 60px rgba(0,0,0,0.15)",
              }}>
                <div style={{ fontSize: 12, fontWeight: 600, color: "rgba(255,255,255,0.4)", textTransform: "uppercase", letterSpacing: 1, marginBottom: 20 }}>AI Agent Pipeline</div>
                {[
                  { agent: "Agent 1", task: "Entity & Ownership Analysis", status: "Complete", time: "1.2s" },
                  { agent: "Agent 2", task: "Sanctions & PEP Screening", status: "Complete", time: "2.4s" },
                  { agent: "Agent 3", task: "Adverse Media Scan", status: "Complete", time: "3.1s" },
                  { agent: "Agent 4", task: "Document Verification", status: "Running", time: "..." },
                  { agent: "Agent 5", task: "Compliance Memo Synthesis", status: "Queued", time: "—" },
                ].map((a, i) => (
                  <div key={i} style={{
                    display: "flex", alignItems: "center", justifyContent: "space-between",
                    padding: "12px 16px", borderRadius: 10, marginBottom: 8,
                    background: a.status === "Running" ? "rgba(99,102,241,0.1)" : "rgba(255,255,255,0.03)",
                    border: a.status === "Running" ? "1px solid rgba(99,102,241,0.2)" : "1px solid rgba(255,255,255,0.04)",
                  }}>
                    <div>
                      <div style={{ fontSize: 10, fontWeight: 600, color: "rgba(255,255,255,0.35)", marginBottom: 2 }}>{a.agent}</div>
                      <div style={{ fontSize: 13, color: "#fff", fontWeight: 500 }}>{a.task}</div>
                    </div>
                    <div style={{ textAlign: "right" }}>
                      <div style={{
                        fontSize: 11, fontWeight: 600, padding: "2px 8px", borderRadius: 6,
                        background: a.status === "Complete" ? "rgba(22,163,74,0.15)" : a.status === "Running" ? "rgba(99,102,241,0.15)" : "rgba(255,255,255,0.05)",
                        color: a.status === "Complete" ? "#16a34a" : a.status === "Running" ? "#818cf8" : "rgba(255,255,255,0.3)",
                      }}>{a.status}</div>
                      <div style={{ fontSize: 10, color: "rgba(255,255,255,0.3)", marginTop: 4 }}>{a.time}</div>
                    </div>
                  </div>
                ))}
              </div>
            </FadeIn>
          </div>
        </div>
      </section>

      {/* ═══ FEATURES ═══ */}
      <section className="section" id="features">
        <FadeIn>
          <div style={{ textAlign: "center", marginBottom: 64 }}>
            <p style={{ fontSize: 13, fontWeight: 600, color: "#4f46e5", textTransform: "uppercase", letterSpacing: 1.5, marginBottom: 12 }}>Features</p>
            <h2 style={{ fontSize: 40, fontWeight: 800, letterSpacing: -1.5 }}>Everything you need. Nothing you don't.</h2>
          </div>
        </FadeIn>
        <div className="grid-3" style={{ gap: 20 }}>
          {features.map((f, i) => (
            <FadeIn key={i} delay={i * 0.07}>
              <div className="feature-card">
                <div style={{ width: 44, height: 44, borderRadius: 12, background: "rgba(79,70,229,0.08)", display: "flex", alignItems: "center", justifyContent: "center", color: "#4f46e5", marginBottom: 16 }}>
                  {f.icon}
                </div>
                <h3 style={{ fontSize: 16, fontWeight: 700, marginBottom: 8 }}>{f.title}</h3>
                <p style={{ fontSize: 14, color: "rgba(15,17,23,0.55)", lineHeight: 1.65 }}>{f.desc}</p>
              </div>
            </FadeIn>
          ))}
        </div>
      </section>

      {/* ═══ WORKFLOW ═══ */}
      <section className="section-dark" id="workflow">
        <div className="section">
          <FadeIn>
            <div style={{ textAlign: "center", marginBottom: 64 }}>
              <p style={{ fontSize: 13, fontWeight: 600, color: "#818cf8", textTransform: "uppercase", letterSpacing: 1.5, marginBottom: 12 }}>Workflow</p>
              <h2 style={{ fontSize: 40, fontWeight: 800, letterSpacing: -1.5, color: "#fff" }}>From application to monitoring</h2>
              <p style={{ fontSize: 17, color: "rgba(255,255,255,0.45)", marginTop: 16, maxWidth: 500, margin: "16px auto 0" }}>A structured, auditable workflow with human oversight at every critical decision point.</p>
            </div>
          </FadeIn>
          <div className="grid-3" style={{ gap: 16 }}>
            {workflowSteps.map((s, i) => (
              <FadeIn key={i} delay={i * 0.08}>
                <div style={{
                  padding: 28, borderRadius: 14,
                  background: "rgba(255,255,255,0.04)", border: "1px solid rgba(255,255,255,0.06)",
                  transition: "all 0.3s",
                }}
                  onMouseEnter={e => { e.currentTarget.style.background = "rgba(99,102,241,0.08)"; e.currentTarget.style.borderColor = "rgba(99,102,241,0.2)"; }}
                  onMouseLeave={e => { e.currentTarget.style.background = "rgba(255,255,255,0.04)"; e.currentTarget.style.borderColor = "rgba(255,255,255,0.06)"; }}
                >
                  <div style={{ fontSize: 32, fontWeight: 800, color: "rgba(99,102,241,0.3)", marginBottom: 12, fontFamily: "monospace" }}>{s.num}</div>
                  <h3 style={{ fontSize: 16, fontWeight: 700, color: "#fff", marginBottom: 6 }}>{s.label}</h3>
                  <p style={{ fontSize: 13, color: "rgba(255,255,255,0.45)", lineHeight: 1.6 }}>{s.sub}</p>
                </div>
              </FadeIn>
            ))}
          </div>
          <FadeIn delay={0.5}>
            <div style={{ textAlign: "center", marginTop: 48, padding: "20px 28px", borderRadius: 12, background: "rgba(99,102,241,0.08)", border: "1px solid rgba(99,102,241,0.15)", display: "inline-flex", alignItems: "center", gap: 10, margin: "48px auto 0", width: "fit-content" }}>
              <div style={{ color: "#818cf8" }}>{Icons.shield}</div>
              <span style={{ fontSize: 15, fontWeight: 600, color: "#c7d2fe" }}>Human-in-the-loop. Always.</span>
            </div>
          </FadeIn>
        </div>
      </section>

      {/* ═══ MANAGEMENT COMPANIES ═══ */}
      <section className="section" id="management">
        <div className="grid-2">
          <FadeIn className="hide-mobile">
            <div style={{
              background: "linear-gradient(135deg, #f8f9fb, #f1f3f9)", borderRadius: 16,
              padding: 32, border: "1px solid rgba(15,17,23,0.06)",
            }}>
              <div style={{ fontSize: 12, fontWeight: 600, color: "rgba(15,17,23,0.4)", textTransform: "uppercase", letterSpacing: 1, marginBottom: 20 }}>Portfolio Risk Overview</div>
              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12, marginBottom: 20 }}>
                {[
                  { label: "Total Clients", value: "312", color: "#0f1117" },
                  { label: "High Risk", value: "28", color: "#d97706" },
                  { label: "Pending Review", value: "14", color: "#6366f1" },
                  { label: "Overdue Reviews", value: "3", color: "#dc2626" },
                ].map((s, i) => (
                  <div key={i} style={{ padding: 16, borderRadius: 10, background: "#fff", border: "1px solid rgba(15,17,23,0.06)" }}>
                    <div style={{ fontSize: 10, fontWeight: 500, color: "rgba(15,17,23,0.4)", textTransform: "uppercase", letterSpacing: 0.5 }}>{s.label}</div>
                    <div style={{ fontSize: 28, fontWeight: 800, color: s.color, marginTop: 4 }}>{s.value}</div>
                  </div>
                ))}
              </div>
              <div style={{ display: "flex", gap: 8 }}>
                {["LOW", "MEDIUM", "HIGH", "VERY HIGH"].map((r, i) => {
                  const widths = [45, 30, 18, 7];
                  const colors = ["#16a34a", "#2563eb", "#d97706", "#dc2626"];
                  return <div key={i} style={{ flex: widths[i], height: 8, borderRadius: 4, background: colors[i], opacity: 0.7 }} />;
                })}
              </div>
              <div style={{ display: "flex", justifyContent: "space-between", marginTop: 6 }}>
                {["Low 45%", "Medium 30%", "High 18%", "Very High 7%"].map((l, i) => (
                  <span key={i} style={{ fontSize: 10, color: "rgba(15,17,23,0.4)" }}>{l}</span>
                ))}
              </div>
            </div>
          </FadeIn>
          <FadeIn delay={0.15}>
            <div>
              <p style={{ fontSize: 13, fontWeight: 600, color: "#4f46e5", textTransform: "uppercase", letterSpacing: 1.5, marginBottom: 12 }}>For Management Companies</p>
              <h2 style={{ fontSize: 40, fontWeight: 800, letterSpacing: -1.5, marginBottom: 20 }}>Built for portfolio-scale compliance</h2>
              <p style={{ fontSize: 17, color: "rgba(15,17,23,0.55)", lineHeight: 1.75, marginBottom: 32 }}>
                Manage hundreds of clients across multiple risk profiles. Real-time portfolio risk views, automated periodic reviews, and white-label capability for your brand.
              </p>
              <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
                {[
                  { t: "Multi-client portfolio management", s: "Centralised view across all entities with risk aggregation" },
                  { t: "Automated periodic reviews", s: "Risk-based review cycles — quarterly, semi-annual, or annual" },
                  { t: "White-label ready", s: "Deploy under your brand with configurable workflows" },
                  { t: "Regulatory reporting", s: "Pre-built reports for MFSA, FSC, and other regulators" },
                ].map((item, i) => (
                  <div key={i} style={{ display: "flex", gap: 14, alignItems: "flex-start" }}>
                    <div style={{ width: 24, height: 24, borderRadius: 8, background: "rgba(79,70,229,0.1)", display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0, marginTop: 2 }}>
                      <svg width="12" height="12" fill="none" viewBox="0 0 24 24" stroke="#4f46e5" strokeWidth="3"><path strokeLinecap="round" strokeLinejoin="round" d="M4.5 12.75l6 6 9-13.5" /></svg>
                    </div>
                    <div>
                      <div style={{ fontSize: 15, fontWeight: 600, color: "#0f1117" }}>{item.t}</div>
                      <div style={{ fontSize: 13, color: "rgba(15,17,23,0.45)", marginTop: 2 }}>{item.s}</div>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          </FadeIn>
        </div>
      </section>

      {/* ═══ PLATFORM DEMO ═══ */}
      <section className="section-subtle" id="platform">
        <div className="section">
          <FadeIn>
            <div style={{ textAlign: "center", marginBottom: 64 }}>
              <p style={{ fontSize: 13, fontWeight: 600, color: "#4f46e5", textTransform: "uppercase", letterSpacing: 1.5, marginBottom: 12 }}>Platform</p>
              <h2 style={{ fontSize: 40, fontWeight: 800, letterSpacing: -1.5 }}>See it in action</h2>
            </div>
          </FadeIn>
          <div className="grid-3">
            {[
              { title: "Client Portal", desc: "Clean, guided onboarding experience. Pre-screening, document upload, and real-time status tracking.", tag: "CLIENT-FACING" },
              { title: "Back Office Dashboard", desc: "Full compliance workbench. Application queue, risk scoring, AI agent outputs, and decision tools.", tag: "OFFICER-FACING" },
              { title: "AI Explainability Layer", desc: "Every AI decision broken down. Agent-by-agent reasoning with confidence scores and evidence links.", tag: "COMPLIANCE" },
            ].map((d, i) => (
              <FadeIn key={i} delay={i * 0.1}>
                <div style={{
                  background: "#fff", borderRadius: 16, border: "1px solid rgba(15,17,23,0.06)",
                  overflow: "hidden", transition: "all 0.3s",
                }}
                  onMouseEnter={e => { e.currentTarget.style.transform = "translateY(-4px)"; e.currentTarget.style.boxShadow = "0 20px 40px rgba(0,0,0,0.06)"; }}
                  onMouseLeave={e => { e.currentTarget.style.transform = "translateY(0)"; e.currentTarget.style.boxShadow = "none"; }}
                >
                  <div style={{ height: 160, background: "linear-gradient(135deg, #0c0e14, #1a1d2e)", display: "flex", alignItems: "center", justifyContent: "center" }}>
                    <div style={{ padding: "8px 16px", borderRadius: 8, background: "rgba(255,255,255,0.06)", border: "1px solid rgba(255,255,255,0.08)", color: "rgba(255,255,255,0.5)", fontSize: 12, fontWeight: 600, letterSpacing: 0.5 }}>{d.tag}</div>
                  </div>
                  <div style={{ padding: 24 }}>
                    <h3 style={{ fontSize: 18, fontWeight: 700, marginBottom: 8 }}>{d.title}</h3>
                    <p style={{ fontSize: 14, color: "rgba(15,17,23,0.55)", lineHeight: 1.65 }}>{d.desc}</p>
                  </div>
                </div>
              </FadeIn>
            ))}
          </div>
        </div>
      </section>

      {/* ═══ TRUST ═══ */}
      <section className="section">
        <FadeIn>
          <div style={{ textAlign: "center", marginBottom: 64 }}>
            <p style={{ fontSize: 13, fontWeight: 600, color: "#4f46e5", textTransform: "uppercase", letterSpacing: 1.5, marginBottom: 12 }}>Trust & Compliance</p>
            <h2 style={{ fontSize: 40, fontWeight: 800, letterSpacing: -1.5 }}>Built for regulated institutions</h2>
          </div>
        </FadeIn>
        <div className="grid-3">
          {[
            { icon: Icons.shield, title: "Regulator-ready workflows", desc: "Every workflow designed with regulatory defensibility in mind. MFSA, FSC, FCA-aligned processes out of the box." },
            { icon: Icons.eye, title: "Audit trail by design", desc: "Every action, every decision, every AI output — timestamped, attributed, and immutable. Always inspection-ready." },
            { icon: Icons.brain, title: "No black-box decisions", desc: "Full explainability layer. See exactly why each risk score was assigned, which agents contributed, and what evidence was used." },
          ].map((t, i) => (
            <FadeIn key={i} delay={i * 0.1}>
              <div style={{ padding: 36, borderRadius: 16, background: "linear-gradient(135deg, rgba(79,70,229,0.04), rgba(139,92,246,0.04))", border: "1px solid rgba(79,70,229,0.1)", textAlign: "center" }}>
                <div style={{ width: 56, height: 56, borderRadius: 16, background: "rgba(79,70,229,0.1)", display: "flex", alignItems: "center", justifyContent: "center", color: "#4f46e5", margin: "0 auto 20px" }}>
                  {t.icon}
                </div>
                <h3 style={{ fontSize: 18, fontWeight: 700, marginBottom: 10 }}>{t.title}</h3>
                <p style={{ fontSize: 14, color: "rgba(15,17,23,0.55)", lineHeight: 1.65 }}>{t.desc}</p>
              </div>
            </FadeIn>
          ))}
        </div>
      </section>

      {/* ═══ FINAL CTA ═══ */}
      <section id="demo" style={{ background: "linear-gradient(135deg, #0c0e14, #1a1d2e)", padding: "100px 24px" }}>
        <div style={{ maxWidth: 700, margin: "0 auto", textAlign: "center" }}>
          <FadeIn>
            <h2 style={{ fontSize: 44, fontWeight: 800, letterSpacing: -1.5, color: "#fff", marginBottom: 20 }}>
              Transform your onboarding process
            </h2>
            <p style={{ fontSize: 18, color: "rgba(255,255,255,0.5)", lineHeight: 1.7, marginBottom: 40 }}>
              Join the regulated institutions replacing manual compliance with intelligent automation. See Onboarda in action.
            </p>
            <div style={{ display: "flex", gap: 16, justifyContent: "center", flexWrap: "wrap" }}>
              <a className="btn-primary" href="mailto:hello@onboarda.com" style={{ fontSize: 16, padding: "16px 36px" }}>
                Request Demo {Icons.arrow}
              </a>
              <a className="btn-secondary" href="mailto:hello@onboarda.com" style={{ fontSize: 16, padding: "16px 36px", color: "rgba(255,255,255,0.7)", borderColor: "rgba(255,255,255,0.15)" }}>
                Book a Call
              </a>
            </div>
          </FadeIn>
        </div>
      </section>

      {/* ═══ FOOTER ═══ */}
      <footer style={{ background: "#0c0e14", borderTop: "1px solid rgba(255,255,255,0.06)", padding: "64px 24px 40px" }}>
        <div style={{ maxWidth: 1200, margin: "0 auto" }}>
          <div className="grid-4" style={{ marginBottom: 48 }}>
            <div>
              <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 16 }}>
                <div style={{ width: 30, height: 30, borderRadius: 8, background: "linear-gradient(135deg, #4f46e5, #6366f1)", display: "flex", alignItems: "center", justifyContent: "center", color: "#fff", fontWeight: 800, fontSize: 14 }}>O</div>
                <span style={{ fontSize: 16, fontWeight: 700, color: "#fff" }}>Onboarda</span>
              </div>
              <p style={{ fontSize: 13, color: "rgba(255,255,255,0.35)", lineHeight: 1.7, maxWidth: 250 }}>
                AI-powered compliance onboarding and monitoring for regulated institutions.
              </p>
            </div>
            {[
              { title: "Product", links: ["Features", "Workflow", "Platform", "Security", "Pricing"] },
              { title: "Use Cases", links: ["Payment Providers", "Management Companies", "Corporate Services", "Fund Administrators"] },
              { title: "Company", links: ["About", "Contact", "Blog", "Careers", "Legal"] },
            ].map((col, i) => (
              <div key={i}>
                <div style={{ fontSize: 12, fontWeight: 600, color: "rgba(255,255,255,0.4)", textTransform: "uppercase", letterSpacing: 1, marginBottom: 16 }}>{col.title}</div>
                {col.links.map((l, j) => (
                  <div key={j} style={{ fontSize: 14, color: "rgba(255,255,255,0.35)", marginBottom: 10, cursor: "pointer", transition: "color 0.2s" }}
                    onMouseEnter={e => e.currentTarget.style.color = "rgba(255,255,255,0.7)"}
                    onMouseLeave={e => e.currentTarget.style.color = "rgba(255,255,255,0.35)"}
                  >{l}</div>
                ))}
              </div>
            ))}
          </div>
          <div style={{ borderTop: "1px solid rgba(255,255,255,0.06)", paddingTop: 24, display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
            <span style={{ fontSize: 12, color: "rgba(255,255,255,0.25)" }}>2026 Onboarda. All rights reserved.</span>
            <div style={{ display: "flex", gap: 24 }}>
              {["Privacy Policy", "Terms of Service", "Cookie Policy"].map((l, i) => (
                <span key={i} style={{ fontSize: 12, color: "rgba(255,255,255,0.25)", cursor: "pointer", transition: "color 0.2s" }}
                  onMouseEnter={e => e.currentTarget.style.color = "rgba(255,255,255,0.5)"}
                  onMouseLeave={e => e.currentTarget.style.color = "rgba(255,255,255,0.25)"}
                >{l}</span>
              ))}
            </div>
          </div>
        </div>
      </footer>
    </div>
  );
}
