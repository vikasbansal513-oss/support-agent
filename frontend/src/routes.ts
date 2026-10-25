export type Access = "public" | "user" | "owner" | "admin";

export interface RouteDef {
  path: string;
  access: Access;
  screen: string;
}

export const ROUTES: RouteDef[] = [
  { path: "/login", access: "public", screen: "Login" },
  { path: "/signup", access: "public", screen: "Signup" },
  { path: "/verify-email", access: "public", screen: "VerifyEmail" },
  { path: "/forgot-password", access: "public", screen: "ForgotPassword" },
  { path: "/reset-password", access: "public", screen: "ResetPassword" },
  { path: "/pending", access: "public", screen: "Pending" },
  { path: "/", access: "user", screen: "Dashboard" },
  { path: "/onboarding", access: "owner", screen: "Onboarding" },
  { path: "/conversations", access: "user", screen: "Conversations" },
  { path: "/conversations/:id", access: "user", screen: "ConversationView" },
  { path: "/handoffs", access: "user", screen: "Handoffs" },
  { path: "/review", access: "user", screen: "Review" },
  { path: "/approvals", access: "user", screen: "Approvals" },
  { path: "/settings/autonomy", access: "owner", screen: "AutonomySettings" },
  { path: "/settings/voice", access: "owner", screen: "VoiceSettings" },
  { path: "/settings/integrations", access: "owner", screen: "IntegrationsSettings" },
  { path: "/settings/policy", access: "owner", screen: "PolicySettings" },
  { path: "/settings/notifications", access: "owner", screen: "NotificationsSettings" },
  { path: "/settings/security", access: "user", screen: "Security" },
  { path: "/privacy", access: "owner", screen: "PrivacyRequests" },
  { path: "/privacy-notice", access: "public", screen: "PrivacyNotice" },
  { path: "/terms", access: "public", screen: "Terms" },
  { path: "/admin/login", access: "public", screen: "AdminLogin" },
  { path: "/admin/tenants", access: "admin", screen: "AdminTenants" },
  { path: "/admin/ops", access: "admin", screen: "AdminOps" },
];
