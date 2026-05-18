const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").replace(/\/$/, "");

const LINKS = [
  { href: "/docs",                         icon: "📄", label: "API Docs",                desc: "Swagger UI — all endpoints" },
  { href: "/dashboard-view",               icon: "📊", label: "Operations Dashboard",     desc: "Damage cases + checkout inspections" },
  { href: "/owner-summary",                icon: "📋", label: "Owner Summary",            desc: "High-level metrics (JSON)" },
  { href: "/damage-cases/pending",         icon: "⏳", label: "Pending Damage Cases",     desc: "Open damage cases (JSON)" },
  { href: "/checkout-inspections/pending", icon: "🏠", label: "Pending Checkouts",        desc: "Open checkout inspections (JSON)" },
  { href: "/db/health",                    icon: "✅", label: "Database Health",           desc: "Supabase connection status" },
];

export default function App() {
  return (
    <div style={{
      minHeight: "100vh",
      background: "#f1f5f9",
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif",
      padding: "24px",
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
          background: "#dcfce7",
          color: "#16a34a",
          fontSize: "12px",
          fontWeight: 600,
          padding: "4px 12px",
          borderRadius: "999px",
          marginBottom: "20px",
          letterSpacing: "0.3px",
        }}>
          <span style={{ fontSize: "8px" }}>●</span> Running
        </div>

        <h1 style={{ fontSize: "26px", fontWeight: 700, color: "#0f172a", margin: "0 0 8px" }}>
          Holiday Homes Ops Bot
        </h1>
        <p style={{ color: "#64748b", fontSize: "14px", lineHeight: 1.6, margin: "0 0 32px" }}>
          WhatsApp task reminders and damage case management for<br />
          <strong style={{ color: "#1e293b" }}>Everluxe Real Estate And Holiday Homes</strong>
        </p>

        <div style={{ display: "flex", flexDirection: "column", gap: "10px" }}>
          {LINKS.map(({ href, icon, label, desc }) => (
            <a
              key={href}
              href={API_BASE + href}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                display: "flex",
                alignItems: "center",
                gap: "14px",
                padding: "14px 16px",
                background: "#f8fafc",
                border: "1px solid #e2e8f0",
                borderRadius: "10px",
                textDecoration: "none",
                color: "#1e293b",
                transition: "all 0.15s",
              }}
              onMouseEnter={e => {
                (e.currentTarget as HTMLAnchorElement).style.background = "#f1f5f9";
                (e.currentTarget as HTMLAnchorElement).style.borderColor = "#cbd5e1";
              }}
              onMouseLeave={e => {
                (e.currentTarget as HTMLAnchorElement).style.background = "#f8fafc";
                (e.currentTarget as HTMLAnchorElement).style.borderColor = "#e2e8f0";
              }}
            >
              <span style={{ fontSize: "20px", width: "28px", textAlign: "center", flexShrink: 0 }}>{icon}</span>
              <div>
                <div style={{ fontWeight: 600, fontSize: "14px" }}>{label}</div>
                <div style={{ fontSize: "12px", color: "#64748b", marginTop: "1px" }}>{desc}</div>
              </div>
            </a>
          ))}
        </div>

        <p style={{
          marginTop: "28px",
          fontSize: "12px",
          color: "#94a3b8",
          textAlign: "center",
        }}>
          Powered by FastAPI + Supabase · <a href={API_BASE + "/docs"} target="_blank" rel="noopener noreferrer" style={{ color: "#94a3b8" }}>API v2.0</a>
        </p>
      </div>
    </div>
  );
}
