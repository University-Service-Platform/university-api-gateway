# University Services API Gateway

The single entry point of the University Services Management Platform. The shared frontend calls **one base URL** (`VITE_API_BASE_URL`, e.g. `https://<gateway>/api/v1`), and the gateway:

1. **routes** each request to the service that owns the path, rewriting the path where a service serves a different one;
2. **checks the access token** (RS256, verified with the Identity Service's public keys) before anything protected is forwarded;
3. **forwards** the request unchanged otherwise: method, body, query, `Authorization` header and `X-Request-ID`, so every service can still check the token and permissions itself;
4. answers **CORS** for the whole platform and returns errors in the platform's standard format.

```
Browser (shared frontend, Group 6 / 7 frontends)
        │  https://<gateway>/api/v1/...   Authorization: Bearer <Identity token>
        ▼
┌──────────────────────── API Gateway ────────────────────────┐
│ route table (app/routes.py) · token check (JWKS) · CORS      │
│ X-Request-ID · long timeouts for sleeping free-tier services  │
└───┬────────────┬──────────┬────────────┬──────────┬─────────┘
    ▼            ▼          ▼            ▼          ▼
 Identity    Directory   Facility   Reservation  Service requests / Work orders   Events / Communication
 (Group 5)   (Group 5)   (Group 6)  (Group 6)    (Group 7)
```

## Routes

The gateway accepts `/api/v1/<resource>` for everything. `GET /gateway/routes` lists the live table.

| Frontend calls | Service | Forwarded as |
|---|---|---|
| `POST /api/v1/auth/login`, `POST /api/v1/auth/forgot-password`, `POST /api/v1/auth/reset-password`, `GET /.well-known/jwks.json` | Identity | unchanged (**public**) |
| `/api/v1/auth/**`, `/users/**`, `/roles/**`, `/audit-logs/**` | Identity | unchanged |
| `/api/v1/validation/users/{id}`, `/api/v1/validation/users/{id}/eligibility` | Identity | unchanged |
| `/api/v1/validation/users/{id}/affiliation`, `/api/v1/validation/users/{id}/responsibilities` | **Directory** (matched first) | unchanged |
| `/api/v1/faculties/**`, `/departments/**`, `/service-units/**`, `/affiliations/**`, `/responsibilities/**`, `/api/v1/validation/{faculties,departments,service-units}/**` | Directory | unchanged |
| `/api/v1/resources/**`, `/api/v1/facilities/**`, `/api/v1/availability-rules/**` | Group 6 facility | `/api/resources/**` etc. |
| `/api/v1/reservations/**` | Group 6 reservation | unchanged |
| `/api/v1/service-requests/**` | Group 7 service request | `/api/service-requests/**` |
| `/api/v1/work-orders/**` | Group 7 work order | `/api/work-orders/**` |
| `/api/v1/events/**`, `/api/v1/registrations/**` | Group 8 event-service | unchanged |
| `/api/v1/announcements/**`, `/notifications/**`, `/feedback/**`, `/engagement-dashboard/**` | Group 8 communication-feedback-service | `/api/<resource>/**` |

The Group 6 and Group 7 frontends call unversioned paths, so these are accepted too: `/api/resources/**`, `/api/facilities/**`, `/api/availability-rules/**`, `/api/service-requests/**`, `/api/work-orders/**`, and `/api/reservations/**` (forwarded as `/api/v1/reservations/**`).

Not exposed: Group 7's `/api/dev/**` token endpoint, H2 consoles, actuator endpoints and anything else not in the table (`404 ROUTE_NOT_FOUND`).

## Gateway endpoints

| Endpoint | Purpose |
|---|---|
| `GET /health` | The gateway itself |
| `GET /health/services` | Checks every connected service (it does **not** wake sleeping ones; see below) |
| `GET /gateway/routes` | The routing table and which services are connected |
| `GET /gateway/wake-targets` | The services' public health addresses, which the frontend calls from the browser to wake them |

### Sleeping free-tier services

Free Render services sleep after ~15 idle minutes, and a request from the gateway (inside Render) does
not wake them: Render answers it at once with its own 502 page. A request from outside Render does.
So:

- **The frontend wakes them.** On load it calls every address from `/gateway/wake-targets` from the
  visitor's browser.
- **The gateway waits for them.** When Render answers with its 502/503 page (HTML, so the request never
  reached the service), the gateway sends the request again, pausing 2, 3, 5, 8, 10 and 12 seconds,
  for up to `UPSTREAM_WAKE_WAIT_SECONDS`. Then it answers `503 SERVICE_STARTING`. The services' own
  errors (JSON) and timeouts are never resent.
- **Before a demo**, run **Actions → Keep services awake → Run workflow** and choose how many hours;
  it calls every service every 4 minutes from GitHub. It isn't scheduled, because keeping the
  services awake all month would use up the free plan's instance hours in about ten days.

## Errors added by the gateway

Every error uses the platform envelope `{"success": false, "error": {"code", "message"}, "timestamp"}`. Responses from the services themselves are passed through unchanged.

| Status | `error.code` | When |
|---|---|---|
| 400 | `BAD_REQUEST` | Path contains `.` or `..` segments |
| 401 | `UNAUTHORIZED` | No `Authorization: Bearer` token on a protected route |
| 401 | `INVALID_TOKEN` | Bad signature, expired, wrong issuer or audience, not RS256 |
| 404 | `ROUTE_NOT_FOUND` | No service owns the path, or the owning service is not connected yet (the frontend's demo modes rely on this) |
| 413 | `PAYLOAD_TOO_LARGE` | Body over `MAX_REQUEST_BODY_BYTES` |
| 502 | `UPSTREAM_UNAVAILABLE` | The service could not be reached |
| 503 | `SERVICE_STARTING` | The service was still asleep or starting after `UPSTREAM_WAKE_WAIT_SECONDS` |
| 503 | `AUTH_UNAVAILABLE` | Tokens can't be verified because the Identity Service is unreachable and no keys are cached |
| 504 | `UPSTREAM_TIMEOUT` | The service did not answer within `UPSTREAM_READ_TIMEOUT_SECONDS` |

## Configuration

All settings are environment variables; see [.env.example](.env.example).

| Variable | Default | Purpose |
|---|---|---|
| `IDENTITY_SERVICE_URL` | – | Identity Service base URL; also where the token keys come from |
| `DIRECTORY_SERVICE_URL`, `FACILITY_SERVICE_URL`, `RESERVATION_SERVICE_URL`, `SERVICE_REQUEST_SERVICE_URL`, `WORK_ORDER_SERVICE_URL`, `EVENT_SERVICE_URL`, `COMMUNICATION_SERVICE_URL` | – | Each service's base URL. Empty = not connected (its routes answer 404) |
| `JWKS_URL` | `IDENTITY_SERVICE_URL/.well-known/jwks.json` | Where public keys are fetched |
| `JWT_ISSUER` / `JWT_AUDIENCE` | `university-identity-service` / `university-services-platform` | Required token claims |
| `JWKS_CACHE_SECONDS` | `300` | Key cache lifetime; an unknown key id triggers a refresh (at most every 10 s) |
| `CORS_ALLOWED_ORIGINS` | – | Comma-separated frontend origins; `*` is refused in production |
| `UPSTREAM_CONNECT_TIMEOUT_SECONDS` / `UPSTREAM_READ_TIMEOUT_SECONDS` | `10` / `90` | Long enough for a sleeping Render service to wake |
| `UPSTREAM_WAKE_WAIT_SECONDS` | `40` | How long to keep resending a request while Render reports the service as not running; `0` turns it off. Keep it below the frontend's 60-second request timeout |
| `MAX_REQUEST_BODY_BYTES` | `10485760` | Request body limit |
| `ENVIRONMENT` | `development` | `production` requires token verification to be configured |

## Run locally

```bash
python -m venv .venv
.venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
pip install -r requirements-dev.txt
# set the service URLs (see .env.example), e.g. in PowerShell:
#   $env:IDENTITY_SERVICE_URL="http://localhost:8001"; $env:DIRECTORY_SERVICE_URL="http://localhost:8002"
uvicorn app.main:app --port 8000 --reload
```

Port **8000**: 8001/8002 are Identity/Directory, 8081–8084 the Group 6 and 7 services. (8080 is often taken on Windows, e.g. by XAMPP's Apache.)

**Frontend in development:** point the Vite dev server's proxy at the gateway so the browser sees one origin and no CORS is needed:

```ts
// vite.config.ts
server: { proxy: { '/api': 'http://localhost:8000' } }
```

## Tests

```bash
pytest -q
```

Every upstream service is replaced by an in-process fake, and a real RSA key pair plays the Identity Service's signing key. The tests cover every route in the table, token checks (expired, forged, wrong issuer or audience, HS256, key rotation, JWKS caching, Identity unavailable), forwarding (bodies, queries, headers, request IDs, encoded IDs, path traversal), service failures, health, CORS and configuration.

CI (`.github/workflows/ci.yml`) runs the tests, then builds the Docker image and checks that the container starts, refuses a request without a token and answers 404 for an unconnected service.

## Deploy

See [docs/DEPLOY_RENDER.md](docs/DEPLOY_RENDER.md). What each team has to do to work behind the gateway is in [docs/INTEGRATION.md](docs/INTEGRATION.md); the problems found in each repository, with priorities, are in [docs/INTEGRATION_ISSUES.md](docs/INTEGRATION_ISSUES.md).

## Project layout

```
app/config.py   settings from environment variables
app/routes.py   the routing table
app/auth.py     token verification with the Identity JWKS
app/proxy.py    forwarding and relaying
app/main.py     the application: request IDs, errors, CORS, health, proxy
tests/          automated tests (fake upstreams, real RSA keys)
```
