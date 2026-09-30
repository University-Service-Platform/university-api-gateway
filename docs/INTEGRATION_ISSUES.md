# Platform Integration Issues

**From:** Group 5 (API Gateway and Identity), Dayaleeswaran, dayaleeswaran@gmail.com
**Date:** 30 September 2026
**Scope:** every repository in `University-Service-Platform`, `main` branches plus open feature branches.

This list collects the problems that stop the services and frontends from working together through the API Gateway, or that are unsafe once the services are hosted on Render.

**Priority:** 🔴 must fix (the platform won't work or is insecure) · 🟡 should fix · 🟢 nice to have · ✅ handled by the gateway, nothing to do

> **The biggest blocker:** none of the frontends send the login token yet, so nothing protected works end to end.
> **The biggest security risks:** the Group 6 facility service has no login check, the reservation service can accept fake tokens, and Group 7's dev-token endpoint is open.

---

## Common facts for every team

- **Log in** through the gateway: `POST /api/v1/auth/login`. Send `Authorization: Bearer <access_token>` on every other request.
- **Tokens:** RS256, verified with the JWKS at `https://university-identity-service.onrender.com/.well-known/jwks.json`. Claims: `iss=university-identity-service`, `aud=university-services-platform`, `sub` (user ID), `roles` (list), 60-minute lifetime.
- **Every service must verify the token itself.** On Render's free plan each service has a public URL, so the gateway's check alone is not enough.
- **Render has no persistent disk on the free plan.** H2, SQLite and in-memory data are lost on every restart and sleep. Use Postgres.

---

## Group 6: `facility-resource-service`

| # | Problem | Fix | Priority |
|---|---|---|---|
| 1 | **No login check.** `SecurityConfig` permits `/api/**` for everyone, so anyone can create or delete facilities through the service's public Render URL. | Verify Identity tokens with JWKS (as the reservation service does): `spring.security.oauth2.resourceserver.jwt.jwk-set-uri=https://university-identity-service.onrender.com/.well-known/jwks.json`, and require authentication for `/api/**`. | 🔴 |
| 2 | **Data is lost on restart.** In-memory H2 database. | Use Postgres. | 🔴 |
| 3 | No health endpoint. | Add `spring-boot-starter-actuator` and permit `/actuator/health`. Until then the gateway uses `/v3/api-docs`, so keep that public. | 🟢 |

## Group 6: `university-reservation-service`

| # | Problem | Fix | Priority |
|---|---|---|---|
| 1 | **The Docker build fails.** The Dockerfile's runtime image `openjdk:17-jre-slim` does not exist on Docker Hub (the `openjdk` images are deprecated), so the service cannot be built or deployed to Render. | Use `eclipse-temurin:17-jre`. | 🔴 |
| 2 | **It accepts fake tokens.** Without the JWKS setting, or in the `dev` profile, the fallback `JwtDecoder` accepts any text as a token and gives the caller `STUDENT` and `RESOURCE_MANAGER`. | Set `SPRING_SECURITY_OAUTH2_RESOURCESERVER_JWT_JWK_SET_URI=https://university-identity-service.onrender.com/.well-known/jwks.json` in every shared environment, and never run the `dev` profile on Render. | 🔴 |
| 3 | **Data is lost on restart.** In-memory H2 database. | Use Postgres. | 🔴 |
| 4 | The Identity-integration work is not merged. | Merge `feature/USMG6-group5-identity-integration` (it checks issuer and audience). | 🟡 |
| 5 | Calls to the facility service don't forward the user's token, so they will fail with 401 once the facility service checks logins (facility #1). | Forward the incoming `Authorization` header. | 🟡 |

## Group 6: frontend (`university-services-group6-frontend`)

| # | Problem | Fix | Priority |
|---|---|---|---|
| 1 | **Never sends the login token**, so every protected call fails with 401. | Add `Authorization: Bearer <token>` to every `fetch`. | 🔴 |
| 2 | Uses `localStorage` demo data while `VITE_API_BASE_URL` is empty. | Set `VITE_API_BASE_URL` to the gateway origin, e.g. `https://<gateway>` (the services add `/api/...` themselves). | 🟡 |
| 3 | Calls `/api/reservations`, but the service serves `/api/v1/reservations`. | The gateway rewrites this path. | ✅ |

---

## Group 7: `service-request-service` and `work-order-service`

| # | Problem | Fix | Priority |
|---|---|---|---|
| 1 | **`main` only accepts Group 7's own tokens,** not Identity logins. | Merge `feature/identity-service-integration` (service-request) and `feature/USM-G7-identity-service-integration` (work-order). The JWKS verification is already written there. Set `IDENTITY_SERVICE_BASE_URL=https://university-identity-service.onrender.com`. | 🔴 |
| 2 | **Anyone can get a token.** The `dev` profile is active by default and enables `/api/dev/token`. | Run a non-dev profile on Render (or set `usm.dev-tools.enabled=false`). The gateway never routes `/api/dev/**`, but the services' own URLs still expose it. | 🔴 |
| 3 | **MySQL is not available on Render.** | Switch to Postgres (Flyway supports it) or use an external MySQL host. | 🔴 |
| 4 | Ports 8081/8082 clash with Group 6 when everything runs on one machine. | Locally, use `SERVER_PORT=8083` (service-request) and `SERVER_PORT=8084` (work-order), and set `SERVICE_REQUEST_BASE_URL=http://localhost:8083` on the work-order service. On Render this doesn't matter. | 🟢 |

## Group 7: frontend (branch `feature/group7-frontend` of the shared frontend)

| # | Problem | Fix | Priority |
|---|---|---|---|
| 1 | Calls `/api/service-requests` and `/api/work-orders` directly, without `VITE_API_BASE_URL`, so a hosted build sends them to its own site instead of the gateway. | Build the URLs from `VITE_API_BASE_URL`, e.g. `apiFetch('/service-requests')` → `https://<gateway>/api/v1/service-requests`. | 🔴 |
| 2 | The Vite proxy sends **all** `/api` calls to port 8081, including Identity and Directory calls. | Proxy `/api` to the gateway: `server: { proxy: { '/api': 'http://localhost:8000' } }`. | 🟡 |

---

## Shared frontend (`university-services-frontend`)

| # | Problem | Fix | Priority |
|---|---|---|---|
| 1 | **Never sends the login token.** `apiFetch` adds no `Authorization` header. | After login, store `data.access_token` and send `Authorization: Bearer <token>` on every request. On a `401`, clear it and go back to the login page. | 🔴 |
| 2 | **Reads the login response wrongly.** It expects `{ user, token }`; Identity returns `{"success": true, "data": {"access_token", "token_type", "expires_in", "user_id", "university_id", "roles"}}`. | Read `data.access_token`, then call `GET /auth/me` for the profile, roles and permissions. | 🔴 |
| 3 | **Four Identity paths don't exist:** | Use: | 🔴 |
| | `GET /users/profile` | `GET /auth/me` (own profile) or `GET /users/{id}` | |
| | `GET /users/validate/{id}` | `GET /validation/users/{id}?required_role=…` | |
| | `POST /roles/assign` | `POST /users/{id}/roles` with `{"role_name": "…"}` | |
| | `PUT /users/account-status/{id}` | `PATCH /users/{id}/status` with `{"status": "ACTIVE"}` or `"INACTIVE"` | |
| 4 | Reads `body.message`, but errors are `{"success": false, "error": {"code", "message"}}`. Successful bodies are `{"success": true, "data": …}`. | Read `body.error.message`, branch on `body.error.code`, and read successful payloads from `body.data`. | 🟡 |
| 5 | Typo in `apiClient.ts`: `if (status === 24)`. | Should be `204`. | 🟢 |
| 6 | Base URL for hosting. | Build with `VITE_API_BASE_URL=https://<gateway>/api/v1`. Group 8 is now deployed: set `VITE_G8_DEMO_MODE=false` in the hosted build, so outages show instead of demo data. | 🟡 |

---

## Group 8

| # | Problem | Fix | Priority |
|---|---|---|---|
| 1 | ~~No backend deployed~~ | **Resolved:** event-service (`https://eventmanagement-uni-service-management.onrender.com`) and communication-feedback-service (`https://notification-and-feedback-uni-service.onrender.com`) are deployed and connected to the gateway. | ✅ |
| 2 | event-service lives in a personal repository (`IsuruDharshana/EventManagement-Uni-Service-Management-System_Backend`). | Move it into the `University-Service-Platform` organisation (Team Lead's decision). | 🟢 |
| 3 | communication-feedback-service answers requests without a token with `403` instead of `401`. | Return `401` for a missing or invalid token, so clients know to log in again. Through the gateway this doesn't matter: the gateway answers `401` first. | 🟢 |
| 4 | communication-feedback-service's `/v3/api-docs` returns `500 INTERNAL_SERVER_ERROR`. | Fix the OpenAPI generation, so other teams can read its contract. | 🟡 |

---

## Group 5: Directory Service

| # | Problem | Fix | Priority |
|---|---|---|---|
| 1 | Not deployed yet. | Deploy to Render (Postgres, `IDENTITY_SERVICE_BASE_URL=https://university-identity-service.onrender.com`). Then set its URL as `DIRECTORY_SERVICE_BASE_URL` on Identity and `DIRECTORY_SERVICE_URL` on the gateway. | 🟡 |

---

## Checklist before a demo

- [ ] Every service verifies Identity tokens itself, with no "accept anything" fallback active.
- [ ] No `dev` profile or dev-token endpoint running on Render.
- [ ] Data that must survive a restart is in Postgres, not H2, SQLite or memory.
- [ ] Every frontend sends `Authorization: Bearer <token>` and uses the gateway as its base URL.
- [ ] The gateway lists every deployed service as connected (`GET https://<gateway>/gateway/routes`).
- [ ] Open `https://<gateway>/health/services` 5 minutes before the demo to wake sleeping services.

More detail for each team, including configuration examples, is in [INTEGRATION.md](INTEGRATION.md).
