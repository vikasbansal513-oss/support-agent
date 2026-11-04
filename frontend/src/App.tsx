import { lazy, Suspense, type ComponentType, type ReactElement } from "react";
import { BrowserRouter, Navigate, Route, Routes } from "react-router-dom";
import { AuthProvider, useAuth } from "./auth/AuthContext";
import { Layout } from "./components/Layout";
import { AuthLoadingScreen, PageSkeleton } from "./components/common";
import { ToastProvider } from "./components/Toast";
import type { Access } from "./routes";
import { t } from "./i18n";

// Route-level code splitting: every page below is its own chunk, fetched
// only when a user actually navigates to it, instead of all dashboard pages
// loading upfront on first paint.
function lazyNamed<M, K extends keyof M>(loader: () => Promise<M>, key: K) {
  return lazy(() =>
    loader().then((mod) => ({ default: mod[key] as unknown as ComponentType })),
  );
}

const Login = lazyNamed(() => import("./pages/auth"), "Login");
const Signup = lazyNamed(() => import("./pages/auth"), "Signup");
const VerifyEmail = lazyNamed(() => import("./pages/auth"), "VerifyEmail");
const ForgotPassword = lazyNamed(() => import("./pages/auth"), "ForgotPassword");
const ResetPassword = lazyNamed(() => import("./pages/auth"), "ResetPassword");
const Pending = lazyNamed(() => import("./pages/auth"), "Pending");
const AdminLogin = lazyNamed(() => import("./pages/auth"), "AdminLogin");

const NoticePage = lazyNamed(() => import("./pages/Privacy"), "NoticePage");
const PrivacyRequests = lazyNamed(() => import("./pages/Privacy"), "PrivacyRequests");

const Dashboard = lazyNamed(() => import("./pages/Dashboard"), "Dashboard");
const Conversations = lazyNamed(() => import("./pages/Conversations"), "Conversations");
const ConversationView = lazyNamed(() => import("./pages/Conversations"), "ConversationView");
const Handoffs = lazyNamed(() => import("./pages/Handoffs"), "Handoffs");
const Review = lazyNamed(() => import("./pages/Review"), "Review");
const Approvals = lazyNamed(() => import("./pages/Approvals"), "Approvals");
const VoiceSettings = lazyNamed(() => import("./pages/Voice"), "VoiceSettings");
const AutonomySettings = lazyNamed(() => import("./pages/Autonomy"), "AutonomySettings");
const IntegrationsSettings = lazyNamed(() => import("./pages/Integrations"), "IntegrationsSettings");
const PolicySettings = lazyNamed(() => import("./pages/Policy"), "PolicySettings");
const NotificationsSettings = lazyNamed(() => import("./pages/Notifications"), "NotificationsSettings");
const Security = lazyNamed(() => import("./pages/Security"), "Security");

const AdminOverview = lazyNamed(() => import("./pages/Admin"), "AdminOverview");
const AdminTenants = lazyNamed(() => import("./pages/Admin"), "AdminTenants");
const AdminOps = lazyNamed(() => import("./pages/Admin"), "AdminOps");
const AdminAlerts = lazyNamed(() => import("./pages/AdminAlerts"), "AdminAlerts");
const AdminAuditLog = lazyNamed(() => import("./pages/AdminAuditLog"), "AdminAuditLog");
const AdminControlTower = lazyNamed(() => import("./pages/AdminControlTower"), "AdminControlTower");
const AdminTenantDetail = lazyNamed(() => import("./pages/AdminTenantDetail"), "AdminTenantDetail");
const AdminBilling = lazyNamed(() => import("./pages/Billing"), "AdminBilling");
const AdminBillingSettings = lazyNamed(() => import("./pages/Billing"), "AdminBillingSettings");
const AdminInvoice = lazyNamed(() => import("./pages/Billing"), "AdminInvoice");

export function Guard({ access, children }: { access: Access; children: ReactElement }) {
  const { user, realm, loading } = useAuth();
  if (loading) return <AuthLoadingScreen />;
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
    <Suspense fallback={<PageSkeleton />}>
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
    </Suspense>
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
