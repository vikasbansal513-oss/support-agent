// k6 load test for the authenticated dashboard read path: POST /auth/login once per VU,
// then repeated GET /dashboard/stats and GET /approvals calls with that session's bearer
// token - representative of a brand owner leaving the Control Tower/dashboard open while
// their team works, which is a real, sustained load pattern distinct from the webhook
// burst load/stress in webhook_whatsapp.js.
//
// WRITE-ONLY, NOT WIRED TO RUN ANYWHERE - see loadtest/README.md. This script only reads
// (no approve/reject calls - see approval_write.js for that), so it is safe to point at a
// real environment's read endpoints, but it still logs in real users VUS times, so prefer
// a dedicated test tenant/user over your own production owner account.
//
// Usage:
//   BASE_URL=https://staging-xyz.a.run.app LOGIN_EMAIL=loadtest@example.com \
//     LOGIN_PASSWORD='...' k6 run loadtest/dashboard_read.js
//
// Optional env vars: VUS (default 10), DURATION (default "2m"), P95_MS (default 1500 -
// authenticated reads should be much faster than the webhook path above, since there's
// no agent pipeline in the critical path, just SQL reads).

import http from "k6/http";
import { check, sleep } from "k6";

const BASE_URL = __ENV.BASE_URL;
const LOGIN_EMAIL = __ENV.LOGIN_EMAIL;
const LOGIN_PASSWORD = __ENV.LOGIN_PASSWORD;
if (!BASE_URL || !LOGIN_EMAIL || !LOGIN_PASSWORD) {
  throw new Error(
    "Set BASE_URL, LOGIN_EMAIL and LOGIN_PASSWORD env vars (see the comment at the top of this file)."
  );
}
const VUS = Number(__ENV.VUS || 10);
const DURATION = __ENV.DURATION || "2m";
const P95_MS = Number(__ENV.P95_MS || 1500);

export const options = {
  scenarios: {
    ramp_and_hold: {
      executor: "ramping-vus",
      startVUs: 0,
      stages: [
        { duration: "20s", target: VUS },
        { duration: DURATION, target: VUS },
        { duration: "20s", target: 0 },
      ],
    },
  },
  thresholds: {
    http_req_failed: ["rate<0.01"],
    "http_req_duration{endpoint:login}": ["p(95)<2000"], // bcrypt/argon hashing cost, allow more
    "http_req_duration{endpoint:dashboard_stats}": [`p(95)<${P95_MS}`],
    "http_req_duration{endpoint:approvals_list}": [`p(95)<${P95_MS}`],
  },
};

export function setup() {
  // One login per k6 run (not per VU/iteration) would understate real load - real
  // traffic is many separate browser sessions, each with its own login. Each VU logs in
  // for itself in its own init below instead; setup() here only sanity-checks the
  // credentials once, fast, before spending the full ramp-up on bad credentials.
  const res = http.post(
    `${BASE_URL}/auth/login`,
    JSON.stringify({ email: LOGIN_EMAIL, password: LOGIN_PASSWORD }),
    { headers: { "Content-Type": "application/json" }, tags: { endpoint: "login" } }
  );
  if (res.status !== 200) {
    throw new Error(
      `Login check failed with status ${res.status} - fix LOGIN_EMAIL/LOGIN_PASSWORD before running the full load test. Body: ${res.body}`
    );
  }
}

function login() {
  const res = http.post(
    `${BASE_URL}/auth/login`,
    JSON.stringify({ email: LOGIN_EMAIL, password: LOGIN_PASSWORD }),
    { headers: { "Content-Type": "application/json" }, tags: { endpoint: "login" } }
  );
  check(res, { "login succeeded": (r) => r.status === 200 });
  if (res.status !== 200) return null;
  return JSON.parse(res.body).access_token;
}

// Module-level (not inside default()) so it persists across iterations within the same
// VU - k6 gives each VU its own isolated JS runtime, so this is per-VU state without
// needing to key it by __VU. A real dashboard session logs in once and then polls
// repeatedly; logging in on every iteration would mostly measure password-hashing cost,
// not the dashboard read path this script targets.
let cachedToken = null;

export default function () {
  if (!cachedToken) {
    cachedToken = login();
  }
  if (!cachedToken) {
    sleep(2);
    return;
  }
  const headers = { Authorization: `Bearer ${cachedToken}` };

  const stats = http.get(`${BASE_URL}/dashboard/stats`, {
    headers,
    tags: { endpoint: "dashboard_stats" },
  });
  check(stats, { "dashboard/stats is 200": (r) => r.status === 200 });

  const approvals = http.get(`${BASE_URL}/approvals`, {
    headers,
    tags: { endpoint: "approvals_list" },
  });
  check(approvals, { "approvals list is 200": (r) => r.status === 200 });

  // access_token_minutes defaults to 15 (app/core/config.py) - a run longer than that
  // will otherwise see every remaining request from this VU fail with 401. Re-login on
  // the next iteration instead of letting the whole VU die.
  if (stats.status === 401 || approvals.status === 401) {
    cachedToken = null;
  }

  sleep(3); // a human refreshing a dashboard, not a tight polling loop
}
