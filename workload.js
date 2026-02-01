import http from "k6/http";
import { check, sleep } from "k6";

/**
 * Env vars set by measure_rapl.py:
 *   BASE_URL  e.g., http://localhost:9966
 *   ENDPOINT  e.g., /api/owners
 *   RATE      e.g., 10
 */

const BASE_URL = __ENV.BASE_URL || "http://localhost:9966";
const ENDPOINT = __ENV.ENDPOINT || "/api/owners";
// const RATE = Number(__ENV.RATE || "10");
// const DURATION = __ENV.K6_DURATION || "180s";

// export const options = {
//   scenarios: {
//     constant_rate: {
//       executor: "constant-arrival-rate",
//       rate: RATE,
//       timeUnit: "1s",
//       duration: DURATION,
//       preAllocatedVUs: 50,
//       maxVUs: 200,
//     },
//   },
// };

export default function () {
  const url = `${BASE_URL}${ENDPOINT}`;
  const res = http.get(url, { headers: { Accept: "application/json" } });

  check(res, {
    "status is 200": (r) => r.status === 200,
  });

  // Small sleep prevents VU tight loops from causing weird client-side effects
  sleep(0.001);
}
