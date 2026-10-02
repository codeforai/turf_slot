#!/usr/bin/env python3
"""Send a small, realistic mix of traffic to a TurfSlot deployment so the dashboard has data.

It browses turfs, checks availability, makes a few deliberate mistakes (404, 401) and runs
booking cycles: book a slot, try to book the same slot again (the database must refuse with
409), then cancel. Uses only the standard library.

    python monitoring/loadtest.py --base-url https://turfslot-api.onrender.com --duration 120 --rps 3

Needs the demo data (python -m app.cli seed-demo). Only point it at a service you own.
Exit code 1 if the service never became ready or any request returned a 5xx.
"""

import argparse
import json
import os
import random
import statistics
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta

MAX_RPS = 10  # this is a demo-data generator, not a stress test; keep free instances safe
MAX_DURATION = 600
IST = timedelta(hours=5, minutes=30)

results: list[tuple[str, int, float]] = []  # (action, status, seconds)
lock = threading.Lock()


def call(base, action, method, path, *, token=None, body=None, form=None, params=None):
    url = base + path + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")
    headers = {"User-Agent": "turfslot-loadtest"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    elif form is not None:
        data = urllib.parse.urlencode(form).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, method=method, headers=headers)
    started = time.perf_counter()
    payload = None
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            status = response.status
            raw = response.read()
    except urllib.error.HTTPError as exc:
        status = exc.code
        raw = exc.read()
    except (urllib.error.URLError, TimeoutError, ConnectionError):
        status, raw = 0, b""
    elapsed = time.perf_counter() - started
    try:
        payload = json.loads(raw) if raw else None
    except ValueError:
        payload = None
    with lock:
        results.append((action, status, elapsed))
    return status, payload


def wait_until_ready(base, timeout=180):
    """Free instances sleep when idle; the first request can take about a minute."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        status, payload = call(base, "warmup", "GET", "/health/ready")
        if status == 200 and payload and payload.get("status") == "ready":
            return True
        time.sleep(5)
    return False


def booking_cycle(base, token, turf_id):
    """Book, try to double-book (expect 409), then cancel so the slot is free again."""
    day = (datetime.now(UTC) + IST + timedelta(days=random.randint(2, 20))).date().isoformat()
    start = f"{random.randint(7, 20):02d}:{random.choice(['00', '30'])}"
    body = {"turf_id": turf_id, "date": day, "start_time": start, "duration_hours": 1, "payment_method": "upi"}
    status, booking = call(base, "book", "POST", "/bookings", token=token, body=body)
    if status != 201 or not booking:
        return
    call(base, "double-book", "POST", "/bookings", token=token, body=body)
    call(base, "cancel", "POST", f"/bookings/{booking['id']}/cancel", token=token, body={"reason": "load test"})


def one_action(base, token, turfs):
    turf = random.choice(turfs) if turfs else None
    roll = random.random()
    if turf is None or roll < 0.35:
        params = random.choice(
            [{}, {"sport_type": "football"}, {"q": "kochi"}, {"sort": "price_asc"}, {"lat": 10.01, "lng": 76.34}]
        )
        call(base, "search", "GET", "/turfs", params=params)
    elif roll < 0.55:
        call(base, "turf-detail", "GET", f"/turfs/{turf['id']}")
    elif roll < 0.75:
        day = (datetime.now(UTC) + IST + timedelta(days=random.randint(1, 14))).date().isoformat()
        call(base, "availability", "GET", f"/turfs/{turf['id']}/availability", params={"date": day})
    elif roll < 0.80:
        call(base, "not-found", "GET", "/turfs/999999")
    elif roll < 0.85:
        call(base, "no-token", "GET", "/users/me")
    elif token and roll < 0.90:
        call(base, "my-bookings", "GET", "/bookings/me", token=token)
    elif token:
        booking_cycle(base, token, turf["id"])
    else:
        call(base, "search", "GET", "/turfs")


def cleanup(base, token):
    """Cancel anything the test left pending, so the demo account is tidy."""
    status, page = call(
        base, "cleanup", "GET", "/bookings/me", token=token, params={"scope": "upcoming", "page_size": 50}
    )
    for booking in (page or {}).get("items", []) if status == 200 else []:
        if booking.get("status") == "pending":
            call(
                base, "cleanup", "POST", f"/bookings/{booking['id']}/cancel", token=token, body={"reason": "load test"}
            )


def summarize():
    by_action: dict[str, list[float]] = {}
    statuses: Counter = Counter()
    for action, status, elapsed in results:
        by_action.setdefault(action, []).append(elapsed)
        statuses[status] += 1
    print(f"\n{'action':<14}{'count':>7}{'p50 ms':>9}{'p95 ms':>9}")
    for action, times in sorted(by_action.items()):
        times.sort()
        p95 = times[min(len(times) - 1, int(len(times) * 0.95))]
        print(f"{action:<14}{len(times):>7}{statistics.median(times) * 1000:>9.0f}{p95 * 1000:>9.0f}")
    print("\nstatus codes:", ", ".join(f"{code or 'network error'}: {n}" for code, n in sorted(statuses.items())))
    server_errors = sum(n for code, n in statuses.items() if code >= 500)
    network_errors = statuses.get(0, 0)
    print(f"total requests: {len(results)}, server errors (5xx): {server_errors}, network errors: {network_errors}")
    return server_errors


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base-url", default=os.environ.get("BASE_URL", "http://localhost:8000"))
    parser.add_argument("--duration", type=int, default=int(os.environ.get("DURATION", "120")), help="seconds")
    parser.add_argument("--rps", type=float, default=float(os.environ.get("RPS", "3")), help="actions per second")
    parser.add_argument("--email", default=os.environ.get("DEMO_EMAIL", "player@turfslot.demo"))
    parser.add_argument("--password", default=os.environ.get("DEMO_PASSWORD", "Demo@12345"))
    args = parser.parse_args()

    base = args.base_url.rstrip("/")
    if not base.startswith(("http://", "https://")):
        sys.exit("--base-url must start with http:// or https://")
    rps = min(max(args.rps, 0.2), MAX_RPS)
    duration = min(max(args.duration, 5), MAX_DURATION)

    print(f"Target: {base}  duration: {duration}s  rate: {rps:g} actions/s")
    if not wait_until_ready(base):
        print("Service did not become ready in time")
        return 1

    status, tokens = call(
        base, "login", "POST", "/auth/login", form={"username": args.email, "password": args.password}
    )
    token = tokens["access_token"] if status == 200 and tokens else None
    if token is None:
        print("Could not log in with the demo account; continuing with read-only traffic")
    status, page = call(base, "search", "GET", "/turfs")
    turfs = (page or {}).get("items", []) if status == 200 else []
    if not turfs:
        print("No turfs found (run `python -m app.cli seed-demo`); continuing with search traffic only")

    deadline = time.time() + duration
    interval = 1 / rps
    with ThreadPoolExecutor(max_workers=4) as pool:
        next_at = time.time()
        while time.time() < deadline:
            pool.submit(one_action, base, token, turfs)
            next_at += interval
            time.sleep(max(0.0, next_at - time.time()))
    if token:
        cleanup(base, token)

    return 1 if summarize() else 0


if __name__ == "__main__":
    sys.exit(main())
