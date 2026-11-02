import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { Layout } from "./components/Layout";
import { ToastProvider } from "./components/Toast";
import type { Access } from "./routes";
import { t } from "./i18n";
import { AdminLogin, ForgotPassword, Login, Pending, ResetPassword, Signup, VerifyEmail } from "./pages/auth";
import { AdminOps, AdminOverview, AdminTenants } from "./pages/Admin";
import { AdminAlerts } from "./pages/AdminAlerts";
import { AdminAuditLog } from "./pages/AdminAuditLog";
import { AdminControlTower } from "./pages/AdminControlTower";
import { AdminTenantDetail } from "./pages/AdminTenantDetail";
import { AdminBilling, AdminBillingSettings, AdminInvoice } from "./pages/Billing";
import { Approvals } from "./pages/Approvals";
import { AutonomySettings } from "./pages/Autonomy";
import { VoiceSettings } from "./pages/Voice";
import { ConversationView, Conversations } from "./pages/Conversations";
import { Dashboard } from "./pages/Dashboard";
import { Handoffs } from "./pages/Handoffs";
import { IntegrationsSettings } from "./pages/Integrations";
import { NotificationsSettings } from "./pages/Notifications";
import { PolicySettings } from "./pages/Policy";
import { NoticePage, PrivacyRequests } from "./pages/Privacy";
import { Review } from "./pages/Review";
import { Security } from "./pages/Security";
import type { ReactElement } from "react";

export function Guard({ access, children }: { access: Access; children: ReactElement }) {
  const { user, realm, loading } = useAuth();
  if (loading) return null;
  if (access === "public") return children;
  if (!user) return <Navigate to={access === "admin" ? "/admin/login" : "/login"} replace />;
  if (access === "admin") return realm === "admin" ? children : <Navigate to="/admin/login" replace />;
  if (realm === "admin") return <Navigate to="/admin" replace />;
  if (access === "owner" && user.role !== "owner") return <p className="empty">{t.roleHidden}</p>;
  return children;
}

function guarded(access: Access, element: ReactElement): ReactElement {
  return <Guard access={access}>{element}</Guard>;
}

export function AppRoutes() {
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/signup" element={<Signup />} />
      <Route path="/verify-email" element={<VerifyEmail />} />
      <Route path="/forgot-password" element={<ForgotPassword />} />
      <Route path="/reset-password" element={<ResetPassword />} />
      <Route path="/pending" element={<Pending />} />
      <Route path="/admin/login" element={<AdminLogin />} />
      <Route path="/privacy-notice" element={<NoticePage kind="privacy" />} />
      <Route path="/terms" element={<NoticePage kind="terms" />} />
      <Route element={<Layout />}>
        <Route path="/" element={guarded("user", <Dashboard />)} />
        <Route path="/conversations" element={guarded("user", <Conversations />)} />
        <Route path="/conversations/:id" element={guarded("user", <ConversationView />)} />
        <Route path="/handoffs" element={guarded("user", <Handoffs />)} />
        <Route path="/review" element={guarded("user", <Review />)} />
        <Route path="/approvals" element={guarded("user", <Approvals />)} />
        <Route path="/settings/voice" element={guarded("owner", <VoiceSettings />)} />
        <Route path="/settings/autonomy" element={guarded("owner", <AutonomySettings />)} />
        <Route path="/settings/integrations" element={guarded("owner", <IntegrationsSettings />)} />
        <Route path="/onboarding" element={<Navigate to="/settings/integrations" replace />} />
        <Route path="/settings/policy" element={guarded("owner", <PolicySettings />)} />
        <Route path="/settings/notifications" element={guarded("owner", <NotificationsSettings />)} />
        <Route path="/privacy" element={guarded("owner", <PrivacyRequests />)} />
        <Route path="/settings/security" element={guarded("user", <Security />)} />
        <Route path="/admin" element={guarded("admin", <AdminOverview />)} />
        <Route path="/admin/tenants" element={guarded("admin", <AdminTenants />)} />
        <Route path="/admin/tenants/:id" element={guarded("admin", <AdminTenantDetail />)} />
        <Route path="/admin/ops" element={guarded("admin", <AdminOps />)} />
        <Route path="/admin/audit-log" element={guarded("admin", <AdminAuditLog />)} />
        <Route path="/admin/alerts" element={guarded("admin", <AdminAlerts />)} />
        <Route path="/admin/control-tower" element={guarded("admin", <AdminControlTower />)} />
        <Route path="/admin/billing" element={guarded("admin", <AdminBilling />)} />
        <Route path="/admin/billing/settings" element={guarded("admin", <AdminBillingSettings />)} />
        <Route path="/admin/billing/invoices/:id" element={guarded("admin", <AdminInvoice />)} />
        <Route path="/admin/security" element={guarded("admin", <Security />)} />
      </Route>
      <Route path="*" element={<p className="empty">{t.notFound}</p>} />
    </Routes>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <ToastProvider>
        <AuthProvider>
          <AppRoutes />
        </AuthProvider>
      </ToastProvider>
    </BrowserRouter>
  );
}
