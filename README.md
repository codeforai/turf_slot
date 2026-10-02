# TurfSlot API

[![CI](https://github.com/codeforai/turf_slot/actions/workflows/ci.yml/badge.svg)](https://github.com/codeforai/turf_slot/actions/workflows/ci.yml)

Backend for a sports turf booking platform: customers find and book turfs, owners confirm payments and manage bookings, admins moderate. Built with **FastAPI, PostgreSQL, SQLAlchemy 2 and Alembic**, packaged with Docker and deployed to Render through a CI pipeline.

**Live API docs:** `https://turfslot-api.onrender.com/docs` *(The first request after idling takes ~1 minute to wake up)*

## Highlights

- **Double-booking is impossible, not just unlikely.** A PostgreSQL exclusion constraint rejects overlapping active bookings on the same turf, so two simultaneous requests can't both win. A test fires five concurrent requests at one slot and asserts exactly one succeeds.
- **Manual payment flow with a state machine.** Bookings hold a slot as `pending`; the owner or an admin confirms payment (cash, UPI, bank transfer, card) to make it `confirmed`. Unpaid holds expire automatically. The database itself refuses a `confirmed` booking that isn't paid.
- **Built to be operated:** liveness and readiness probes (readiness checks the database and that migrations are applied), Prometheus metrics, structured JSON logs with request IDs, and safe migrations on every deploy (advisory lock, so several instances can start at once).
- **CI/CD:** GitHub Actions runs lint, the test suite against a real PostgreSQL (including a migration downgrade/upgrade cycle), builds the Docker image and smoke-tests the running container. Render only deploys commits whose checks pass.
- **Security basics done properly:** scrypt password hashing, short-lived JWTs that are revoked on password change or "log out everywhere", role-based access (customer / owner / admin), no account enumeration on login or password reset, expiring reset codes with an attempt limit, validated image uploads, and a non-root container.

## Architecture

```mermaid
flowchart LR
    client[Web / mobile client] -->|HTTPS + JWT| api
    subgraph render[Render]
        api[FastAPI app<br/>Uvicorn, Docker]
        db[(PostgreSQL)]
    end
    api --> db
    api -->|images| cdn[Cloudinary]
    api -->|emails| smtp[SMTP]
    prom[Prometheus / Grafana] -. scrape /metrics .-> api
    gh[GitHub Actions<br/>lint, tests, image build, smoke test] -->|checks pass| render
```

### Booking lifecycle

```mermaid
stateDiagram-v2
    [*] --> pending: customer books (slot held)
    pending --> confirmed: owner/admin confirms payment
    pending --> expired: hold runs out unpaid
    pending --> cancelled: customer, owner or admin
    confirmed --> cancelled: refund becomes pending
    confirmed --> completed: owner marks done after the slot ends
```

## Reliability and operations

| Concern | How it's handled |
|---|---|
| Race conditions on bookings | `EXCLUDE USING gist (turf_id WITH =, tstzrange(start_at, end_at) WITH &&) WHERE status IN ('pending','confirmed')`; the API turns a violation into `409 Conflict`. Staff actions on a booking take a row lock (`SELECT ... FOR UPDATE`). |
| Health checks | `GET /health` (liveness, no dependencies) and `GET /health/ready` (database reachable and schema at the latest migration, else `503`). Docker `HEALTHCHECK` included. |
| Metrics | `GET /metrics`: request count and latency histogram by route template and status, in-flight requests, plus business counters (bookings created, slot conflicts, payments confirmed, holds expired, cancellations). |
| Logging | One JSON line per request with method, route, status, duration and request ID. The `X-Request-ID` header is accepted or generated and returned. Unhandled errors are logged with the request ID; clients get a generic 500 with that ID and no internals. |
| Migrations | Alembic runs on container start, guarded by a PostgreSQL advisory lock. Tests apply upgrade, downgrade and upgrade again so the rollback path is verified. |
| Database hygiene | Connection pool with pre-ping (survives database restarts), statement timeout, all timestamps stored as UTC `timestamptz`, CHECK constraints on every enum-like column. |
| Configuration | Environment variables only (12-factor). The app refuses to start in production with a weak `SECRET_KEY` or incomplete storage/email settings. |
| Slow dependencies | Emails are sent in background tasks after the response, and failures are logged without failing the request. |

## Monitoring

The service is watched with **Prometheus and Grafana** (Grafana Cloud scraping the token-protected `/metrics` endpoint). The dashboard, alert rules and a load-test script live in [`monitoring/`](monitoring/README.md), and CI validates them on every push.

- **Dashboard:** request rate by route, 5xx error rate, p50/p95/p99 latency, booking activity, blocked double-booking attempts, memory and CPU.
- **Alerts:** service down for 3 minutes, more than 1% of requests failing for 5 minutes, p95 latency above 1 second for 5 minutes, and a spike in slot conflicts.
- **Load test:** a GitHub Actions workflow (*Actions > Load test > Run workflow*) sends realistic traffic, including deliberate double-booking attempts, and fails if any request returns a 5xx.

<!-- Add a screenshot once the dashboard has data: ![Grafana dashboard](monitoring/dashboard.png) -->

## API overview

Interactive docs are at `/docs`. Log in with **POST /auth/login** (email as `username`) or the *Authorize* button.

| Area | Endpoints |
|---|---|
| Auth | `POST /auth/register`, `POST /auth/login`, `POST /auth/logout-all`, `POST /auth/password-reset/request`, `POST /auth/password-reset/confirm` |
| Profile | `GET/PATCH /users/me`, `POST /users/me/password`, `PUT /users/me/avatar` |
| Turfs (public) | `GET /turfs` (text, sport, price, amenities, distance within a radius, sorting, pagination), `GET /turfs/{id}`, `GET /turfs/slug/{slug}`, `GET /turfs/{id}/availability`, `GET /amenities` |
| Bookings | `POST /bookings`, `GET /bookings/me`, `GET /bookings/{id}`, `POST /bookings/{id}/cancel`, `GET /bookings/{id}/receipt` (PDF with QR code) |
| Payments (owner/admin) | `POST /bookings/{id}/confirm-payment`, `POST /bookings/{id}/mark-refunded`, `POST /bookings/{id}/complete` |
| Reviews | `GET/POST /turfs/{id}/reviews` (only after playing there), `PATCH/DELETE /reviews/{id}` |
| Favourites, notifications | `GET/PUT/DELETE /favourites/...`, `GET /notifications`, `GET /notifications/unread-count`, `POST /notifications/{id}/read`, `POST /notifications/read-all` |
| Owner | `GET /owner/dashboard`, CRUD `/owner/turfs`, images, `GET /owner/bookings`, `GET /owner/payments`, `POST /owner/notifications` (message your customers) |
| Admin | `GET /admin/dashboard`, users (block/unblock), turf verification queue, amenities, all bookings, `POST /admin/maintenance/expire-holds` |
| Ops | `GET /health`, `GET /health/ready`, `GET /metrics` |

## Run it locally

With Docker:

```bash
docker compose up --build
docker compose exec api python -m app.cli seed-demo   # demo turfs and accounts
open http://localhost:8000/docs
```

Demo logins (password `Demo@12345`): `player@turfslot.demo`, `owner@turfslot.demo`, `admin@turfslot.demo`.

Without Docker (Python 3.11+ and a PostgreSQL 14+ database):

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
cp .env.example .env            # edit DATABASE_URL
alembic upgrade head
python -m app.cli seed-demo
uvicorn app.main:app --reload
```

Create a real admin with `python -m app.cli create-admin --email you@example.com --phone 9876543210`.

## Tests

The suite runs against a real PostgreSQL, because the guarantees that matter (exclusion constraint, row locks, CHECK constraints) can't be faked with SQLite. Point `TEST_DATABASE_URL` at a disposable database whose name contains `test` (docker compose creates `turfslot_test` for you):

```bash
export TEST_DATABASE_URL=postgresql+psycopg://turfslot:turfslot@localhost:5432/turfslot_test
python -m pytest
ruff check . && ruff format --check .
```

Coverage includes slot maths, auth and token revocation, password reset, role checks, search and geo-distance, availability, every booking transition, concurrent double-booking, hold expiry, receipts, reviews, notifications, health probes and metrics.

## Deploy to Render

1. Push this repo to GitHub. The CI workflow runs on every push.
2. In Render: **New > Blueprint**, pick the repo. `render.yaml` creates the web service (Docker) and a PostgreSQL database, generates `SECRET_KEY` and wires `DATABASE_URL`.
3. Optional: set `STORAGE_BACKEND=cloudinary` and `CLOUDINARY_URL` so uploaded images survive redeploys, and `EMAIL_BACKEND=smtp` with `SMTP_*` to send real emails.
4. Seed data from the Render shell: `python -m app.cli seed-demo`.

Notes on the free tier: the service sleeps when idle, local disk is wiped on deploy, and free databases are time-limited. Check Render's current limits.

## Configuration

All settings are environment variables; see [`.env.example`](.env.example). Key ones:

| Variable | Default | Purpose |
|---|---|---|
| `DATABASE_URL` | local Postgres | `postgres://`, `postgresql://` and `postgresql+psycopg://` are all accepted |
| `SECRET_KEY` | dev value | JWT signing key; must be 32+ random characters in production |
| `ENVIRONMENT` | `development` | `production` turns on strict config checks |
| `TIMEZONE` | `Asia/Kolkata` | Opening hours and booking times are in this zone |
| `BOOKING_HOLD_MINUTES` | `120` | How long an unpaid booking holds its slot (`0` = until cancelled) |
| `CANCELLATION_CUTOFF_MINUTES` | `60` | Customers can't cancel closer to the start than this |
| `MAX_PENDING_BOOKINGS_PER_USER` | `3` | Stops one customer from holding many slots unpaid |
| `STORAGE_BACKEND` | `local` | `local` or `cloudinary` |
| `EMAIL_BACKEND` | `console` | `console` (log only), `smtp`, or `memory` (tests) |
| `METRICS_TOKEN` | empty | If set, `/metrics` requires `Authorization: Bearer <token>` |
| `WEB_CONCURRENCY` | `1` | Uvicorn workers. With more than one, use Prometheus multiprocess mode for accurate metrics |

## Project layout

```
app/
  main.py            app factory, routers, error handling
  config.py          settings from environment variables
  db.py, models.py   SQLAlchemy engine and ORM models
  enums.py           shared enumerations
  security.py        password hashing, JWT, reset codes
  observability.py   JSON logs, request IDs, Prometheus metrics middleware
  deps.py            auth and role dependencies, pagination
  routers/           auth, users, turfs, bookings, owner, admin, health, ...
  services/          booking state machine, slot maths, search, receipts, email, storage
  cli.py             create-admin, seed-demo, expire-holds
migrations/          Alembic (schema written as explicit SQL)
tests/               pytest suite (real PostgreSQL)
monitoring/          Grafana dashboard, alert rules, load test
Dockerfile, docker-compose.yml, render.yaml, .github/workflows/ci.yml
```

## Possible next steps

- Online payments through a gateway (Razorpay or Stripe) with webhook verification, replacing manual confirmation.
- Rate limiting on login and booking endpoints (e.g. Redis-backed).
- A scheduled job calling `expire-holds`, and alert notifications routed to chat (Slack or Discord).
- Refresh tokens and email verification on sign-up.
