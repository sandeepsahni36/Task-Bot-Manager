import { useState, useEffect } from "react";

const BUILD_API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");
const LS_KEY = "ops_api_base_url";

const LINKS = [
  { href: "/docs",                          icon: "📄", label: "API Docs",                 desc: "Swagger UI — all endpoints" },
  { href: "/dashboard-view",                icon: "📊", label: "Operations Dashboard",      desc: "Damage cases + checkout inspections" },
  { href: "/owner-summary",                 icon: "📋", label: "Owner Summary",             desc: "High-level metrics (JSON)" },
  { href: "/damage-cases/pending",          icon: "⏳", label: "Pending Damage Cases",      desc: "Open damage cases (JSON)" },
  { href: "/checkout-inspections/pending",  icon: "🏠", label: "Pending Checkouts",         desc: "Open checkout inspections (JSON)" },
  { href: "/db/health",                     icon: "✅", label: "Database Health",            desc: "Supabase connection status" },
];

export default function App() {
  const [apiBase, setApiBase] = useState<string>(() =>
    BUILD_API_BASE || localStorage.getItem(LS_KEY) || ""
  );
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(apiBase);

  useEffect(() => {
    if (!BUILD_API_BASE) {
      const stored = localStorage.getItem(LS_KEY) ?? "";
      setApiBase(stored);
      setDraft(stored);
      if (!stored) setEditing(true);
    }
  }, []);

  function save() {
    const trimmed = draft.trim().replace(/\/$/, "");
    localStorage.setItem(LS_KEY, trimmed);
    setApiBase(trimmed);
    setEditing(false);
  }

  const base = apiBase.replace(/\/$/, "");
  const configured = base.length > 0;

  return (
    <div style={{
      minHeight: "100vh",
      background: "#f1f5f9",
      fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
    }}>
      {/* Backend URL config bar */}
      <div style={{
        background: configured ? "#0f172a" : "#7c3aed",
        color: "#fff",
        padding: "10px 20px",
        display: "flex",
        alignItems: "center",
        gap: "12px",
        flexWrap: "wrap",
      }}>
        <span style={{ fontSize: "12px", fontWeight: 600, whiteSpace: "nowrap" }}>
          {configured ? "Backend:" : "⚠️ Backend URL not set"}
        </span>
        {editing ? (
          <>
            <input
              autoFocus
              value={draft}
              onChange={e => setDraft(e.target.value)}
              onKeyDown={e => e.key === "Enter" && save()}
              placeholder="https://your-app.replit.app"
              style={{
                flex: 1,
                minWidth: "260px",
                padding: "5px 10px",
                borderRadius: "6px",
                border: "none",
                fontSize: "13px",
                outline: "none",
              }}
            />
            <button onClick={save} style={{
              background: "#22c55e", color: "#fff", border: "none",
              padding: "5px 16px", borderRadius: "6px", fontWeight: 600,
              cursor: "pointer", fontSize: "13px", whiteSpace: "nowrap",
            }}>Save</button>
            {configured && (
              <button onClick={() => setEditing(false)} style={{
                background: "transparent", color: "#94a3b8", border: "1px solid #475569",
                padding: "5px 12px", borderRadius: "6px", cursor: "pointer", fontSize: "13px",
              }}>Cancel</button>
            )}
          </>
        ) : (
          <>
            <span style={{ fontSize: "12px", color: "#94a3b8", flex: 1, wordBreak: "break-all" }}>
              {base}
            </span>
            <button onClick={() => { setDraft(base); setEditing(true); }} style={{
              background: "transparent", color: "#94a3b8", border: "1px solid #475569",
              padding: "4px 12px", borderRadius: "6px", cursor: "pointer", fontSize: "12px",
              whiteSpace: "nowrap",
            }}>Edit</button>
          </>
        )}
      </div>

      {/* Main card */}
      <div style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        padding: "48px 24px",
      }}>
        <div style={{
          background: "#fff",
          borderRadius: "16px",
          padding: "48px 44px",
          boxShadow: "0 4px 32px rgba(0,0,0,0.08)",
          maxWidth: "540px",
          width: "100%",
        }}>
          <div style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "6px",
            background: configured ? "#dcfce7" : "#fef3c7",
            color: configured ? "#16a34a" : "#92400e",
            fontSize: "12px",
            fontWeight: 600,
            padding: "4px 12px",
            borderRadius: "999px",
            marginBottom: "20px",
            letterSpacing: "0.3px",
          }}>
            <span style={{ fontSize: "8px" }}>●</span>
            {configured ? "Running" : "Backend URL required"}
          </div>

          <h1 style={{ fontSize: "26px", fontWeight: 700, color: "#0f172a", margin: "0 0 8px" }}>
            Holiday Homes Ops Bot
          </h1>
          <p style={{ color: "#64748b", fontSize: "14px", lineHeight: 1.6, margin: "0 0 32px" }}>
            WhatsApp task reminders and damage case management for<br />
            <strong style={{ color: "#1e293b" }}>Everluxe Real Estate And Holiday Homes</strong>
          </p>

          {!configured && (
            <div style={{
              background: "#fef3c7",
              border: "1px solid #fde68a",
              borderRadius: "10px",
              padding: "14px 16px",
              marginBottom: "20px",
              fontSize: "13px",
              color: "#92400e",
              lineHeight: 1.6,
            }}>
              Enter your Replit backend URL in the bar above (e.g.{" "}
              <code style={{ background: "#fef9c3", padding: "1px 4px", borderRadius: "4px" }}>
                https://your-app.replit.app
              </code>
              ) then click <strong>Save</strong> to activate the links below.
            </div>
          )}

          <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
            {LINKS.map(({ href, icon, label, desc }) => (
              <a
                key={href}
                href={configured ? base + href : undefined}
                onClick={!configured ? (e) => { e.preventDefault(); setEditing(true); } : undefined}
                target="_blank"
                rel="noopener noreferrer"
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "14px",
                  padding: "14px 16px",
                  background: configured ? "#f8fafc" : "#f8fafc",
                  border: "1px solid #e2e8f0",
                  borderRadius: "10px",
                  textDecoration: "none",
                  color: configured ? "#1e293b" : "#94a3b8",
                  cursor: configured ? "pointer" : "not-allowed",
                  transition: "all 0.15s",
                  opacity: configured ? 1 : 0.6,
                }}
              >
                <span style={{ fontSize: "20px", width: "28px", textAlign: "center", flexShrink: 0 }}>{icon}</span>
                <div>
                  <div style={{ fontWeight: 600, fontSize: "14px" }}>{label}</div>
                  <div style={{ fontSize: "12px", color: "#64748b", marginTop: "1px" }}>{desc}</div>
                </div>
                {configured && (
                  <span style={{ marginLeft: "auto", fontSize: "11px", color: "#cbd5e1" }}>↗</span>
                )}
              </a>
            ))}
          </div>

          <p style={{
            marginTop: "28px",
            fontSize: "12px",
            color: "#94a3b8",
            textAlign: "center",
          }}>
            Powered by FastAPI + Supabase ·{" "}
            <a
              href={configured ? base + "/docs" : undefined}
              target="_blank"
              rel="noopener noreferrer"
              style={{ color: "#94a3b8" }}
            >
              API v2.0
            </a>
          </p>
        </div>
      </div>
    </div>
  );
}
