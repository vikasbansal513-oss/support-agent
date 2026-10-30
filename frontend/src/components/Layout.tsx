import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthContext";
import { t } from "../i18n";

export function Layout() {
  const { user, realm, logout } = useAuth();
  const owner = user?.role === "owner";
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
  return (
    <div className="layout">
      <header className="topbar">
        <strong>{t.appName}</strong>
        <nav aria-label="main">
          {links.map(([to, label]) => (
            <NavLink key={to} to={to} end={to === "/" || to === "/admin"}>
              {label}
            </NavLink>
          ))}
        </nav>
        <button type="button" className="secondary" onClick={() => void logout()}>
          {t.logout}
        </button>
      </header>
      <main>
        <Outlet />
      </main>
      <footer className="footer">
        <NavLink to="/privacy-notice">{t.privacyNoticeTitle}</NavLink> <NavLink to="/terms">{t.termsTitle}</NavLink>
      </footer>
    </div>
  );
}
