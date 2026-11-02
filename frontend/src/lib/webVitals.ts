import { api } from "../api/client";
import type { PagePerfBeacon } from "../api/types";

/**
 * One beacon per page navigation using the browser's own Performance APIs
 * (Navigation Timing + PerformanceObserver for LCP/INP/CLS) — no new npm
 * dependency. Best-effort and fire-and-forget: a failed POST never surfaces
 * to the user and never blocks rendering.
 */
export function sendPerfBeacon(route: string, apiDurationMs?: number, failedApi?: boolean): void {
  try {
    const nav = performance.getEntriesByType("navigation")[0] as PerformanceNavigationTiming | undefined;
    const beacon: PagePerfBeacon = { route, failed_api: failedApi ?? false };
    if (nav) {
      beacon.ttfb_ms = Math.round(nav.responseStart - nav.startTime);
      beacon.dom_content_loaded_ms = Math.round(nav.domContentLoadedEventEnd - nav.startTime);
      beacon.load_ms = Math.round((nav.loadEventEnd || performance.now()) - nav.startTime);
    } else {
      beacon.load_ms = Math.round(performance.now());
    }
    if (apiDurationMs !== undefined) beacon.api_duration_ms = Math.round(apiDurationMs);

    let settled = false;
    const finish = (lcp?: number, cls?: number, inp?: number) => {
      if (settled) return;
      settled = true;
      if (lcp !== undefined) beacon.lcp_ms = Math.round(lcp);
      if (cls !== undefined) beacon.cls = Math.round(cls * 10_000) / 10_000;
      if (inp !== undefined) beacon.inp_ms = Math.round(inp);
      void api.post("/perf/beacon", beacon).catch(() => undefined);
    };

    let lcpValue: number | undefined;
    let clsValue = 0;
    let inpValue: number | undefined;
    const observers: PerformanceObserver[] = [];
    if (typeof PerformanceObserver !== "undefined") {
      try {
        const lcpObs = new PerformanceObserver((list) => {
          const entries = list.getEntries();
          const last = entries[entries.length - 1];
          if (last) lcpValue = last.startTime;
        });
        lcpObs.observe({ type: "largest-contentful-paint", buffered: true });
        observers.push(lcpObs);
      } catch {
        // metric not supported in this browser
      }
      try {
        const clsObs = new PerformanceObserver((list) => {
          for (const entry of list.getEntries() as (PerformanceEntry & { value: number; hadRecentInput: boolean })[]) {
            if (!entry.hadRecentInput) clsValue += entry.value;
          }
        });
        clsObs.observe({ type: "layout-shift", buffered: true });
        observers.push(clsObs);
      } catch {
        // metric not supported in this browser
      }
      try {
        const inpObs = new PerformanceObserver((list) => {
          for (const entry of list.getEntries() as PerformanceEntry[]) {
            const duration = entry.duration;
            if (inpValue === undefined || duration > inpValue) inpValue = duration;
          }
        });
        inpObs.observe({ type: "event", buffered: true, durationThreshold: 40 } as PerformanceObserverInit);
        observers.push(inpObs);
      } catch {
        // metric not supported in this browser
      }
    }

    // Give observers a window to collect LCP/CLS/INP, then send what we have.
    setTimeout(() => {
      for (const o of observers) {
        try {
          o.disconnect();
        } catch {
          // already disconnected
        }
      }
      finish(lcpValue, clsValue, inpValue);
    }, 4000);
  } catch {
    // Web Vitals collection must never break the app it's measuring.
  }
}
