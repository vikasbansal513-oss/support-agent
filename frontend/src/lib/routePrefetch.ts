// Section 2 (Speed & Scaling): route-level code splitting (see App.tsx's
// lazyNamed()) means every page fetches its own JS chunk only once the user
// actually navigates there - clicking a nav link is the first moment the
// browser even starts the request, so there's a network round-trip of "dead
// air" between the click and the page rendering.
//
// This closes that gap for the sidebar nav specifically: the moment the
// user's pointer/focus lands on a nav link (hover on desktop, focus via
// keyboard/tab, touchstart on mobile - all of which reliably precede an
// actual click/tap by tens to hundreds of milliseconds), we kick off the
// matching import() early, so the chunk is usually already in the browser's
// module cache by the time React Router actually mounts the lazy page.
//
// The sidebar itself is always in the viewport once the Layout mounts, so an
// IntersectionObserver-based "prefetch what's visible" pass would simply fire
// for every nav link on first paint - that's not prefetching one page ahead
// of need, it's eagerly loading the entire app's chunks up front, which is
// exactly what route-level splitting exists to avoid. Hover/focus/touch is
// the signal that's actually predictive here.
const loaders: Record<string, () => Promise<unknown>> = {
  "/": () => import("../pages/Dashboard"),
  "/conversations": () => import("../pages/Conversations"),
  "/handoffs": () => import("../pages/Handoffs"),
  "/review": () => import("../pages/Review"),
  "/approvals": () => import("../pages/Approvals"),
  "/settings/integrations": () => import("../pages/Integrations"),
  "/settings/policy": () => import("../pages/Policy"),
  "/settings/autonomy": () => import("../pages/Autonomy"),
  "/settings/voice": () => import("../pages/Voice"),
  "/settings/notifications": () => import("../pages/Notifications"),
  "/privacy": () => import("../pages/Privacy"),
  "/settings/security": () => import("../pages/Security"),
  "/admin": () => import("../pages/Admin"),
  "/admin/tenants": () => import("../pages/Admin"),
  "/admin/ops": () => import("../pages/Admin"),
  "/admin/audit-log": () => import("../pages/AdminAuditLog"),
  "/admin/alerts": () => import("../pages/AdminAlerts"),
  "/admin/control-tower": () => import("../pages/AdminControlTower"),
  "/admin/billing": () => import("../pages/Billing"),
  "/admin/security": () => import("../pages/Security"),
};

const started = new Set<string>();

/** Fire-and-forget: start the chunk fetch for `path`'s page, at most once.
 * Safe to call repeatedly (e.g. on every mouseenter) - a second call for a
 * path already in flight or loaded is a no-op. Network/parse failures are
 * swallowed and the path is unmarked so a later real navigation still tries
 * the normal lazy() load path instead of being stuck "prefetched" on a
 * failure. */
export function prefetchRoute(path: string): void {
  const loader = loaders[path];
  if (!loader || started.has(path)) return;
  started.add(path);
  loader().catch(() => {
    started.delete(path);
  });
}
