# Monitoring

TurfSlot exposes Prometheus metrics at `/metrics`. This folder holds everything needed to watch it:

| File | What it is |
|---|---|
| `grafana-dashboard.json` | Grafana dashboard: traffic, errors, latency, booking activity, slot conflicts, memory and CPU |
| `alert-rules.yml` | Alert rules (Prometheus format): service down, high error rate, slow responses, conflict spike |
| `loadtest.py` | Sends a small mix of realistic traffic so the graphs have data |

CI validates the alert rules and the dashboard file on every push.

## Live service: Grafana Cloud

The deployed service is monitored with Grafana Cloud's free plan, which scrapes `/metrics` over HTTPS.

1. **Protect the endpoint.** In Render, set `METRICS_TOKEN` to a long random value. `/metrics` then returns 401 without `Authorization: Bearer <token>`.
2. **Add the scrape job.** Grafana Cloud > Connections > *Metrics Endpoint*. Name: `turfslot-api`. URL: `https://<your-service>.onrender.com/metrics`. Authentication: bearer token (the `METRICS_TOKEN` value). Test the connection and save.
3. **Import the dashboard.** Dashboards > New > Import > upload `grafana-dashboard.json`. Pick the Prometheus data source of your stack in the *Data source* drop-down at the top.
4. **Create the alerts.** Alerts & IRM > Alert rules > New alert rule, one per rule in `alert-rules.yml` (copy the `expr`, set the threshold and the `for` duration). The default contact point emails the account owner.
5. **Generate traffic.** GitHub > Actions > *Load test* > Run workflow.

### Free-tier note

A scrape every minute keeps a free Render instance awake, and Render's free plan has a monthly pool of instance hours shared by all services. The scrape job is therefore **enabled on demand** (demos, load tests, interviews) and disabled otherwise. Pause the alert rules when the scrape job is off, or `TurfSlotDown` will fire.

## What the metrics mean

| Metric | Type | Meaning |
|---|---|---|
| `http_requests_total{method, route, status}` | counter | Requests handled. `route` is the template (`/bookings/{booking_id}`), so the number of series stays bounded |
| `http_request_duration_seconds` | histogram | Response time, measured until the last byte is sent |
| `http_requests_in_progress` | gauge | Requests being handled right now |
| `turfslot_bookings_created_total` | counter | Bookings created |
| `turfslot_booking_conflicts_total` | counter | Booking attempts refused because the slot was taken (the exclusion constraint doing its job) |
| `turfslot_payments_confirmed_total` | counter | Payments confirmed by owners or admins |
| `turfslot_bookings_cancelled_total{by}` | counter | Cancellations, by customer or staff |
| `turfslot_booking_holds_expired_total` | counter | Unpaid holds released automatically |

Useful queries:

```promql
# requests per second, by route
sum by (route) (rate(http_requests_total[5m]))

# share of requests failing with a 5xx
sum(rate(http_requests_total{status=~"5.."}[5m])) / sum(rate(http_requests_total[5m]))

# 95th percentile response time
histogram_quantile(0.95, sum by (le) (rate(http_request_duration_seconds_bucket[5m])))
```

## Load test

```bash
python monitoring/loadtest.py --base-url https://<your-service>.onrender.com --duration 180 --rps 3
```

It waits for the service to wake up, logs in as the demo player, then browses turfs, checks availability, triggers a few 404s and 401s, and runs booking cycles: book a slot, try to book it again (must get 409), cancel. It cleans up after itself, prints latency per action and exits with 1 if any request returned a 5xx. It is capped at 10 actions per second: it is a traffic generator for demos, not a stress test.
