// k6 load test for the write-confirm path: POST /approvals/{action_id}/approve - an owner
// confirming a pending Shopify/WooCommerce write (cancel/refund/etc, see
// app/integrations/shopify_write.py). This is the one path in the app that, on success,
// calls a REAL third-party write API (Shopify/WooCommerce) - never run this against
// production approvals that correspond to real customer orders. It needs a real, currently
// pending action_id, which this script deliberately does NOT create or discover on its
// own (that would mean either writing fake orders into a real tenant's data, or silently
// approving whatever real pending action happens to exist - both unacceptable for a load
// test). You seed ACTION_IDS yourself with action ids from a disposable/staging tenant.
//
// WRITE-ONLY, NOT WIRED TO RUN ANYWHERE, AND THE RISKIEST of the three scripts in this
// folder - see loadtest/README.md before running this against anything but a throwaway
// test tenant with fake orders you're fine being cancelled/refunded for real.
//
// Usage:
//   BASE_URL=https://staging-xyz.a.run.app LOGIN_EMAIL=owner@example.com \
//     LOGIN_PASSWORD='...' ACTION_IDS=uuid1,uuid2,uuid3 k6 run loadtest/approval_write.js
//
// Each ACTION_IDS entry is consumed at most once across the whole run (tracked in
// __ENV via a shared array index) - approving the same action twice is a real state
// transition (first call wins, second is a conflict/no-op depending on writes.approve()'s
// own idempotency), not something repeat-use would usefully load-test.

import http from "k6/http";
import { check, sleep } from "k6";
import { SharedArray } from "k6/data";

const BASE_URL = __ENV.BASE_URL;
const LOGIN_EMAIL = __ENV.LOGIN_EMAIL;
const LOGIN_PASSWORD = __ENV.LOGIN_PASSWORD;
const ACTION_IDS_RAW = __ENV.ACTION_IDS;
if (!BASE_URL || !LOGIN_EMAIL || !LOGIN_PASSWORD || !ACTION_IDS_RAW) {
  throw new Error(
    "Set BASE_URL, LOGIN_EMAIL, LOGIN_PASSWORD and ACTION_IDS env vars (see the comment at the top of this file)."
  );
}

const actionIds = new SharedArray("action_ids", function () {
  return ACTION_IDS_RAW.split(",").map((s) => s.trim()).filter(Boolean);
});

export const options = {
  // Deliberately NOT a ramp/stress profile like the other two scripts: this hits a real
  // external write API per call and consumes one action id per call, so it runs each
  // seeded action id exactly once, sequentially, with 1 VU - a correctness/latency check
  // of the write-confirm path, not a concurrency stress test. Stress-testing concurrent
  // approvals would need a disposable tenant with hundreds of throwaway pending actions
  // seeded ahead of time, which is beyond what this script does on its own.
  scenarios: {
    one_pass_through_seeded_actions: {
      executor: "shared-iterations",
      vus: 1,
      iterations: actionIds.length,
      maxDuration: "10m",
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.05"], // looser than the read scripts: a real third-party
    // API call (Shopify/WooCommerce) is on the critical path here, and its latency is
    // not this app's own code to optimize - see Section 1 of the DevOps audit.
  },
};

export function setup() {
  const res = http.post(
    `${BASE_URL}/auth/login`,
    JSON.stringify({ email: LOGIN_EMAIL, password: LOGIN_PASSWORD }),
    { headers: { "Content-Type": "application/json" } }
  );
  if (res.status !== 200) {
    throw new Error(`Login failed with status ${res.status}: ${res.body}`);
  }
  const token = JSON.parse(res.body).access_token;
  // require_owner (app/auth/deps.py) gates this endpoint - fail fast with a clear
  // message instead of a confusing 403 part-way through the run.
  if (JSON.parse(res.body).user.role !== "owner") {
    throw new Error(
      `LOGIN_EMAIL must be an owner-role user (POST /approvals/{id}/approve requires require_owner); got role '${JSON.parse(res.body).user.role}'.`
    );
  }
  return { token };
}

export default function (data) {
  const actionId = actionIds[(__ITER) % actionIds.length];
  const res = http.post(
    `${BASE_URL}/approvals/${actionId}/approve`,
    null,
    { headers: { Authorization: `Bearer ${data.token}` } }
  );
  check(res, {
    "approve returned 200 or a clean conflict (409/404), not a 5xx": (r) =>
      r.status === 200 || r.status === 404 || r.status === 409,
  });
  sleep(1);
}
