// k6 load/stress test for the hottest path in the system: POST /webhooks/whatsapp/{token}.
// This is the one endpoint the audit flagged as needing load numbers most - it's what a
// real customer's WhatsApp message hits, it runs the inline-drain agent pipeline on the
// request itself when INSTANT_DRAIN_ENABLED=true (see app/webhooks/whatsapp.py::_drain_now),
// and it is the only endpoint in this app with no human in the loop slowing down the real
// traffic pattern - Gupshup will retry-storm this URL if your real integration ever falls
// behind, so knowing its breaking point matters more here than anywhere else in the app.
//
// WRITE-ONLY, NOT WIRED TO RUN ANYWHERE: nothing in CI or deploy.yml invokes this script.
// You decide if/when/where to run it - see loadtest/README.md, especially the "running
// this against production WILL create real inbound_events/jobs rows and (if
// INSTANT_DRAIN_ENABLED + a real LLM key) will call your real LLM provider and burn real
// quota" warning there before you point BASE_URL at anything but a disposable/staging env.
//
// Usage:
//   BASE_URL=https://staging-xyz.a.run.app WEBHOOK_TOKEN=<tenant's real webhook token> \
//     k6 run loadtest/webhook_whatsapp.js
//
// Optional env vars (all have sane defaults - see "Scenarios" in README.md):
//   VUS           - concurrent virtual users during the steady-state stage (default 20)
//   DURATION      - steady-state stage length (default "2m")
//   P95_MS        - pass/fail threshold, ms (default 3000 - matches the
//                   api_latency_p95_ms monitoring threshold already used in
//                   core/alerts.py, so a passing run and a quiet alerts table agree)

import http from "k6/http";
import { check, sleep } from "k6";
import { Counter, Trend } from "k6/metrics";

const BASE_URL = __ENV.BASE_URL;
const WEBHOOK_TOKEN = __ENV.WEBHOOK_TOKEN;
if (!BASE_URL || !WEBHOOK_TOKEN) {
  throw new Error(
    "Set BASE_URL and WEBHOOK_TOKEN env vars (see the comment at the top of this file)."
  );
}
const VUS = Number(__ENV.VUS || 20);
const DURATION = __ENV.DURATION || "2m";
const P95_MS = Number(__ENV.P95_MS || 3000);

const duplicateDetected = new Counter("duplicate_detected");
const queuedLatency = new Trend("queued_latency_ms");

export const options = {
  scenarios: {
    ramp_and_hold: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: [
        { duration: "30s", target: VUS }, // ramp-up: catches cold-start/connection-pool issues
        { duration: DURATION, target: VUS }, // steady state: the number that matters
        { duration: "30s", target: 0 }, // ramp-down: confirms the system drains cleanly
      ],
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"], // <1% error rate
    http_req_duration: [`p(95)<${P95_MS}`],
  },
};

function gupshupTextPayload(vuId, iterId) {
  // Shape verified against app/channels/gupshup_parse.py::parse() - a v2 Gupshup inbound
  // text-message callback. A unique payload.id per request is required: the webhook's
  // own idempotency (inbound_events UNIQUE(tenant_id, channel, external_id)) would
  // otherwise silently collapse every load-test request into one real row after the
  // first, making your results measure the dedupe fast-path, not real traffic.
  const msgId = `loadtest-${vuId}-${iterId}-${Date.now()}`;
  const phoneDigits = `9${String(1000000000 + (vuId % 900000000))}`; // fake but E.164-shaped
  return JSON.stringify({
    app: "",
    timestamp: Date.now(),
    type: "message",
    payload: {
      id: msgId,
      source: phoneDigits,
      type: "text",
      payload: { text: "k6 load test message - please ignore" },
      sender: { phone: phoneDigits, name: "k6 load test" },
    },
  });
}

export default function () {
  const body = gupshupTextPayload(__VU, __ITER);
  const res = http.post(`${BASE_URL}/webhooks/whatsapp/${WEBHOOK_TOKEN}`, body, {
    headers: { "Content-Type": "application/json" },
  });

  check(res, {
    "status is 200": (r) => r.status === 200,
  });

  if (res.status === 200) {
    try {
      const parsed = JSON.parse(res.body);
      queuedLatency.add(res.timings.duration);
      if (parsed.status === "duplicate") {
        duplicateDetected.add(1);
      }
    } catch (e) {
      // non-JSON 200 would itself be a correctness bug worth seeing in results, not a
      // reason to abort the run - let the "status is 200" check above carry the signal.
    }
  }

  sleep(1); // ~1 msg/sec per VU - a real customer doesn't message faster than this
}
