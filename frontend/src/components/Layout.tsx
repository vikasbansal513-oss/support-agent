import { NavLink, Outlet } from "react-router-dom";
import { api } from "../api/client";
import type { AgentStatus } from "../api/types";
import { useAuth } from "../auth/AuthContext";
import { t } from "../i18n";
import { Icon, IconSprite } from "./Icons";
import { useApi } from "./useApi";

const ICON: Record<string, string> = {
  "/": "home",
  "/conversations": "chat",
  "/handoffs": "arrows",
  "/review": "review",
  "/approvals": "check-square",
  "/onboarding": "settings",
  "/settings/integrations": "link",
  "/settings/policy": "file",
  "/settings/autonomy": "bot",
  "/settings/voice": "phone",
  "/settings/notifications": "bell",
  "/privacy": "shield",
  "/settings/security": "shield",
  "/admin": "home",
  "/admin/tenants": "users",
  "/admin/ops": "settings",
  "/admin/audit-log": "file",
  "/admin/alerts": "bell",
  "/admin/control-tower": "review",
  "/admin/billing": "file",
  "/admin/security": "shield",
};

function initials(name: string | undefined): string {
  if (!name) return "?";
  const parts = name.trim().split(/\s+/);
  return (parts[0]?.[0] ?? "").concat(parts.length > 1 ? parts[parts.length - 1]?.[0] ?? "" : "").toUpperCase() || "?";
}

export function Layout() {
  const { user, realm, logout } = useAuth();
  const owner = user?.role === "owner";
  const [status] = useApi(
    () => (realm === "admin" ? Promise.resolve(null) : api.get<AgentStatus>("/agent/status")),
    [realm],
  );
  const links: [string, string][] =
    realm === "admin"
      ? [
          ["/admin", t.navOverview],
          ["/admin/tenants", t.navTenants],
          ["/admin/ops", t.navOps],
          ["/admin/audit-log", t.navAuditLog],
          ["/admin/alerts", t.navAlerts],
          ["/admin/control-tower", t.navControlTower],
          ["/admin/billing", t.navBilling],
          ["/admin/security", t.navSecurity],
        ]
      : [
          ["/", t.navDashboard],
          ["/conversations", t.navConversations],
          ["/handoffs", t.navHandoffs],
          ["/review", t.navReview],
          ["/approvals", t.navApprovals],
          ...(owner
            ? ([
                ["/onboarding", t.navOnboarding],
                ["/settings/integrations", t.navIntegrations],
                ["/settings/policy", t.navPolicy],
                ["/settings/autonomy", t.navAutonomy],
                ["/settings/voice", t.navVoice],
                ["/settings/notifications", t.navNotifications],
                ["/privacy", t.navPrivacy],
              ] as [string, string][])
            : []),
          ["/settings/security", t.navSecurity],
        ];

  const agentPaused = status.state === "data" && status.data?.agent_paused === true;

  return (
    <div className="layout">
      <IconSprite />
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">
            <Icon name="bot" />
          </span>
          {t.appName}
        </div>
        <div className="top-spacer" />
        {status.state === "data" && status.data && (
          <>
            <div className="agent-live">
              <span className="live-dot" style={agentPaused ? { background: "#e3464d", boxShadow: "0 0 0 3px #ffeded" } : undefined} />
              {agentPaused ? t.paused : t.agentOnline}
            </div>
            <div className="top-divider" />
          </>
        )}
        <div className="notification" title={t.notificationsLabel}>
          <Icon name="bell" />
        </div>
        <div className="top-divider" />
        <div className="account">
          <span className="account-avatar">{initials(user?.name)}</span>
          <span className="account-name">{user?.name ?? user?.email ?? ""}</span>
          <button type="button" className="secondary" onClick={() => void logout()}>
            {t.logout}
          </button>
        </div>
      </header>
      <div className="app-shell">
        <aside className="sidebar">
          <nav className="nav-list" aria-label="main">
            {links.map(([to, label]) => (
              <NavLink key={to} to={to} end={to === "/" || to === "/admin"} className={({ isActive }) => (isActive ? "nav-item active" : "nav-item")}>
                <Icon name={ICON[to] ?? "home"} />
                <span>{label}</span>
              </NavLink>
            ))}
          </nav>
          <div className="sidebar-foot">
            <div className="system-card">
              <b>
                <span className="live-dot" />
                {t.systemOnline}
              </b>
              <p>{t.servicesRunning}</p>
            </div>
          </div>
        </aside>
        <main>
          <Outlet />
        </main>
      </div>
      <footer className="footer">
        <NavLink to="/privacy-notice">{t.privacyNoticeTitle}</NavLink> <NavLink to="/terms">{t.termsTitle}</NavLink>
      </footer>
    </div>
  );
}
