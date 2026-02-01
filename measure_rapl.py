#!/usr/bin/env python3
"""
measure_rapl.py
Measure host energy using Intel RAPL while driving a workload (k6) against an API.

Outputs one CSV row per run:
variant,run_id,endpoint,rate_rps,warmup_s,duration_s,energy_j,requests,energy_per_request_j,p95_latency_ms,avg_latency_ms,notes

Requirements (on Ubuntu 22.04):
- Python 3.10+
- k6 installed
- RAPL visible at /sys/class/powercap/intel-rapl:0/energy_uj
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Tuple


DEFAULT_RAPL_PATH = "/sys/class/powercap/intel-rapl:0/energy_uj"


@dataclass
class K6Summary:
    requests: Optional[float]
    p95_latency_ms: Optional[float]
    avg_latency_ms: Optional[float]


def read_rapl_energy_uj(rapl_path: str) -> int:
    """Read RAPL energy counter in microjoules (uj)."""
    try:
        with open(rapl_path, "r", encoding="utf-8") as f:
            return int(f.read().strip())
    except FileNotFoundError as e:
        raise RuntimeError(
            f"RAPL path not found: {rapl_path}. "
            f"Expected Intel RAPL at {DEFAULT_RAPL_PATH}. "
            f"Are you on bare-metal Linux with intel_rapl enabled?"
        ) from e
    except PermissionError as e:
        raise RuntimeError(
            f"Permission denied reading {rapl_path}. Try running with sudo, "
            f"or adjust permissions for /sys/class/powercap."
        ) from e


def run_cmd(cmd: list[str], env: Optional[dict[str, str]] = None) -> Tuple[int, str, str]:
    """Run a command, returning (returncode, stdout, stderr)."""
    proc = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    return proc.returncode, proc.stdout, proc.stderr


# def parse_k6_summary(summary_json: dict) -> K6Summary:
#     """
#     Parse k6 --summary-export JSON.
#     We look for:
#       - metrics.http_reqs.values.count
#       - metrics.http_req_duration.values["p(95)"]
#       - metrics.http_req_duration.values.avg
#     """
#     metrics = summary_json.get("metrics", {})
#     http_reqs = metrics.get("http_reqs", {}).get("values", {})
#     http_req_duration = metrics.get("http_req_duration", {}).get("values", {})

#     requests = http_reqs.get("count")
#     p95 = http_req_duration.get("p(95)")
#     avg = http_req_duration.get("avg")

#     # k6 reports durations in milliseconds
#     return K6Summary(
#         requests=float(requests) if requests is not None else None,
#         p95_latency_ms=float(p95) if p95 is not None else None,
#         avg_latency_ms=float(avg) if avg is not None else None,
#     )

def parse_k6_summary(summary_json: dict) -> K6Summary:
    """
    Parse k6 --summary-export JSON (flat metrics format).
    Example:
      metrics.http_req_duration.p(95)
      metrics.http_req_duration.avg
      metrics.http_reqs.count
    """
    metrics = summary_json.get("metrics") or {}

    dur = metrics.get("http_req_duration") or {}
    reqs = metrics.get("http_reqs") or {}

    # durations are in ms
    p95 = dur.get("p(95)")
    avg = dur.get("avg")

    # requests count is typically here
    requests = reqs.get("count")

    return K6Summary(
        requests=float(requests) if requests is not None else None,
        p95_latency_ms=float(p95) if p95 is not None else None,
        avg_latency_ms=float(avg) if avg is not None else None,
    )



def run_k6(workload_js: str, base_url: str, endpoint: str, rate: int, duration_s: int, out_json_path: str) -> K6Summary:
    """
    Run k6 with constant arrival rate.
    Writes summary JSON to out_json_path and returns parsed summary metrics.
    """
    env = os.environ.copy()
    env["BASE_URL"] = base_url.rstrip("/")
    env["ENDPOINT"] = endpoint
    env["RATE"] = str(rate)
    env["K6_DURATION"] = f"{duration_s}s"

    cmd = [
        "k6",
        "run",
        "--quiet",
        "--summary-export",
        out_json_path,
        "--vus",
        "10",
        "--rps",
        str(rate),
        "--duration",
        f"{duration_s}s",
        workload_js,
    ]

    rc, _stdout, stderr = run_cmd(cmd, env=env)
    if rc != 0:
        raise RuntimeError(f"k6 failed (exit {rc}). stderr:\n{stderr}")

    with open(out_json_path, "r", encoding="utf-8") as f:
        summary = json.load(f)

    return parse_k6_summary(summary)


def ensure_csv_header(csv_path: Path) -> None:
    if not csv_path.exists():
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow([
                "variant",
                "run_id",
                "endpoint",
                "rate_rps",
                "warmup_s",
                "duration_s",
                "energy_j",
                "requests",
                "energy_per_request_j",
                "p95_latency_ms",
                "avg_latency_ms",
                "notes",
            ])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--variant", required=True, help="Variant label, e.g., v0_baseline, v1_d1, v2_d3, v3_d4")
    ap.add_argument("--run-id", type=int, default=1, help="Run number for the variant")
    ap.add_argument("--base-url", required=True, help="Base URL of API, e.g., http://localhost:9966")
    ap.add_argument("--endpoint", default="/api/owners", help="Endpoint path, default /api/owners")
    ap.add_argument("--rate", type=int, default=10, help="Requests per second (constant arrival rate)")
    ap.add_argument("--warmup", type=int, default=30, help="Warmup seconds (not recorded)")
    ap.add_argument("--duration", type=int, default=180, help="Measurement duration seconds")
    ap.add_argument("--workload", default="workload.js", help="Path to k6 workload.js")
    ap.add_argument("--rapl-path", default=DEFAULT_RAPL_PATH, help="RAPL energy counter path")
    ap.add_argument("--out", default="results/results.csv", help="CSV output path")
    ap.add_argument("--notes", default="", help="Optional notes to store with the run")
    args = ap.parse_args()

    csv_path = Path(args.out)
    ensure_csv_header(csv_path)

    workload_js = args.workload
    if not Path(workload_js).exists():
        print(f"ERROR: workload file not found: {workload_js}", file=sys.stderr)
        return 2

    # Warmup (no measurement)
    if args.warmup > 0:
        _ = run_k6(workload_js, args.base_url, args.endpoint, args.rate, args.warmup, out_json_path="results/_warmup_summary.json")

    # Measurement window
    e_start_uj = read_rapl_energy_uj(args.rapl_path)
    t_start = time.time()

    summary = run_k6(workload_js, args.base_url, args.endpoint, args.rate, args.duration, out_json_path="results/_measure_summary.json")

    t_end = time.time()
    e_end_uj = read_rapl_energy_uj(args.rapl_path)

    energy_j = (e_end_uj - e_start_uj) / 1e6
    duration_s = int(round(t_end - t_start))

    requests = summary.requests
    energy_per_req = (energy_j / requests) if (requests and requests > 0) else None

    # Write row
    with csv_path.open("a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow([
            args.variant,
            args.run_id,
            args.endpoint,
            args.rate,
            args.warmup,
            duration_s,
            f"{energy_j:.6f}",
            f"{requests:.0f}" if requests is not None else "",
            f"{energy_per_req:.9f}" if energy_per_req is not None else "",
            f"{summary.p95_latency_ms:.3f}" if summary.p95_latency_ms is not None else "",
            f"{summary.avg_latency_ms:.3f}" if summary.avg_latency_ms is not None else "",
            args.notes,
        ])

    print(f"OK: {args.variant} run {args.run_id}")
    print(f"  energy_j={energy_j:.6f}")
    print(f"  requests={requests if requests is not None else 'N/A'}")
    print(f"  energy_per_request_j={energy_per_req if energy_per_req is not None else 'N/A'}")
    print(f"  p95_latency_ms={summary.p95_latency_ms if summary.p95_latency_ms is not None else 'N/A'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
