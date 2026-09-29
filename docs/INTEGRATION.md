# Connecting to the API Gateway: what each team needs to do

**Audience:** Groups 5, 6, 7 and 8, and the shared frontend team.
**Gateway owner:** Group 5 (Dayaleeswaran, dayaleeswaran@gmail.com).

This page lists what each team must change so that its service or frontend works behind the gateway. It is based on each repository's `main` branch and open feature branches as of 30 September 2026.

## How the platform fits together

- **One address for the browser.** Every frontend calls the gateway only: `https://<gateway>/api/v1/...`. No frontend calls a service's own URL.
- **One login.** Users log in at the Identity Service (`POST /api/v1/auth/login` through the gateway). Every other request carries `Authorization: Bearer <access_token>`.
- **Checked twice.** The gateway rejects requests with missing, forged or expired tokens before they reach any service. It forwards the `Authorization` header unchanged, and **every service must still verify the token itself**: on Render's free plan each service has a public URL that can be called without going through the gateway.
- **Token facts.** RS256, verified with `https://university-identity-service.onrender.com/.well-known/jwks.json`. Claims: `iss=university-identity-service`, `aud=university-services-platform`, `sub` (user ID), `roles` (list), `university_id`, `account_type`, 60-minute lifetime.
- **Tracing.** The gateway sends `X-Request-ID` to every service and returns it to the browser. Log it, and forward it when you call another service.

## Route table

| The frontend calls | Service | The service receives |
|---|---|---|
| `/api/v1/auth/**`, `/users/**`, `/roles/**`, `/audit-logs/**`, `/validation/users/{id}`, `/validation/users/{id}/eligibility` | Identity (G5) | same path |
| `/api/v1/validation/users/{id}/affiliation`, `.../responsibilities`, `/faculties/**`, `/departments/**`, `/service-units/**`, `/affiliations/**`, `/responsibilities/**`, `/validation/{faculties,departments,service-units}/**` | Directory (G5) | same path |
| `/api/v1/resources/**`, `/facilities/**`, `/availability-rules/**` | Facility (G6) | `/api/resources/**` etc. |
| `/api/v1/reservations/**` | Reservation (G6) | same path |
| `/api/v1/service-requests/**` | Service request (G7) | `/api/service-requests/**` |
| `/api/v1/work-orders/**` | Work order (G7) | `/api/work-orders/**` |
| `/api/v1/events/**`, `/registrations/**`, `/announcements/**`, `/notifications/**`, `/feedback/**`, `/engagement/**` | Group 8 | same path |

The unversioned paths the Group 6 and 7 frontends use today (`/api/resources`, `/api/facilities`, `/api/reservations`, `/api/service-requests`, `/api/work-orders`) are accepted as well. The live table is at `GET https://<gateway>/gateway/routes`.

---

## Group 5: Identity and Directory

**Identity: nothing to do.** It is live at `https://university-identity-service.onrender.com` and already serves every path above.

**Directory: deploy it.** It already serves `/api/v1/...` and verifies Identity tokens. Once it is on Render:
- set `IDENTITY_SERVICE_BASE_URL=https://university-identity-service.onrender.com` on the Directory;
- set `DIRECTORY_SERVICE_BASE_URL` on the Identity Service and `DIRECTORY_SERVICE_URL` on the gateway to its URL.

---

## Group 6: Facilities and Reservations

### facility-resource-service
1. **Add token checking (required).** `SecurityConfig` permits `/api/**` for everyone, so its public Render URL lets anyone create or delete facilities without going through the gateway. Add the same resource-server setup the reservation service uses:
   ```properties
   spring.security.oauth2.resourceserver.jwt.jwk-set-uri=${IDENTITY_JWKS_URI:https://university-identity-service.onrender.com/.well-known/jwks.json}
   ```
   Then require authentication for `/api/**`. Keep `/v3/api-docs/**` public: the gateway's `/health/services` uses it, because the service has no health endpoint (or add `spring-boot-starter-actuator` and permit `/actuator/health`).
2. **Service-to-service calls.** When the reservation service calls this service, it must forward the user's `Authorization` header, or those calls will get 401 once step 1 is done.
3. **Database.** H2 in memory loses all data on every restart and sleep on Render. Use Postgres for anything that must survive a demo.

### university-reservation-service
1. **Merge `feature/USMG6-group5-identity-integration`.** It verifies issuer and audience and enables the Group 5 integration.
2. **Set the JWKS URI in every shared environment:**
   `SPRING_SECURITY_OAUTH2_RESOURCESERVER_JWT_JWK_SET_URI=https://university-identity-service.onrender.com/.well-known/jwks.json`.
   Without it (and in the `dev` profile on that branch) the fallback decoder accepts **any** string as a token, so do not run the `dev` profile on Render.
3. **Fix the Dockerfile.** The runtime image `openjdk:17-jre-slim` does not exist on Docker Hub (the `openjdk` images are deprecated), so the image cannot be built, locally or on Render. Use `eclipse-temurin:17-jre` instead.
4. **Health endpoint (optional).** There is no actuator; the gateway uses `/v3/api-docs`, which is already public.

### Group 6 frontend (`university-services-group6-frontend`)
1. Set `VITE_API_BASE_URL` to the gateway origin, e.g. `https://<gateway>` (no `/api/v1`: the services add `/api/...` themselves). The gateway maps `/api/reservations/**` to the reservation service's `/api/v1/reservations/**`.
2. **Send the token.** No request sends `Authorization` today. Add `Authorization: Bearer <token>` to every `fetch`, using the token from the shared login.
3. With `VITE_API_BASE_URL` empty the app uses its `localStorage` demo data. Keep that for UI work only.

---

## Group 7: Service Requests and Work Orders

### Both services
1. **Merge the Identity integration branches:** `feature/identity-service-integration` (service-request) and `feature/USM-G7-identity-service-integration` (work-order). They verify Identity tokens through the JWKS, check issuer and audience, and use Identity's role names. Nothing else is needed for tokens.
2. **Ports.** Both default to 8081/8082, the same as Group 6. On Render this doesn't matter (one service per URL). Locally, run them with `SERVER_PORT=8083` and `SERVER_PORT=8084`, and set `SERVICE_REQUEST_BASE_URL=http://localhost:8083` on the work-order service.
3. **Turn off the dev token endpoint in shared environments.** The `dev` profile is active by default and enables `/api/dev/token`, which issues tokens to anyone. The gateway never routes `/api/dev/**`, but the services' own Render URLs would still expose it. Run with a non-dev profile (or `usm.dev-tools.enabled=false`) on Render.
4. **Database.** Render has no managed MySQL. Use Postgres (Flyway supports it) or an external MySQL host.
5. Set `IDENTITY_SERVICE_BASE_URL=https://university-identity-service.onrender.com`.

### Group 7 frontend (`feature/group7-frontend` in the shared frontend)
- It calls `/api/service-requests` and `/api/work-orders` directly, without `VITE_API_BASE_URL`, so a deployed frontend would send them to its own address. Build the URL from `VITE_API_BASE_URL` like the other services, e.g. `apiFetch('/service-requests')`, which becomes `https://<gateway>/api/v1/service-requests`.
- Its `vite.config.ts` proxies all of `/api` to port 8081, which also catches every `/api/v1` call for other services. Proxy `/api` to the gateway instead (below).

---

## Group 8: Events, Communication and Feedback

The gateway already routes `/api/v1/events`, `/registrations`, `/announcements`, `/notifications`, `/feedback` and `/engagement`. Until `GROUP8_SERVICE_URL` is set, they answer `404 ROUTE_NOT_FOUND`, which the frontend's demo mode treats as "not deployed" (demo data is shown).

When your service exists, send Group 5 its URL and confirm that it serves those paths under `/api/v1`. If it uses different paths or needs more, tell us and we'll add a route. The service must verify Identity tokens (JWKS above).

---

## Shared frontend team (`university-services-frontend`)

1. **Base URL.** Set `VITE_API_BASE_URL=https://<gateway>/api/v1` when built for Render. In development use a Vite proxy, so no CORS is needed:
   ```ts
   server: { proxy: { '/api': 'http://localhost:8000' } }   // local gateway
   ```
2. **Send the token.** `apiFetch` never adds `Authorization`. After login, store `data.access_token` and add `Authorization: Bearer <token>` to every request. On a `401`, clear it and return to the login page.
3. **Read the login response correctly.** Identity returns `{"success": true, "data": {"access_token", "token_type", "expires_in", "user_id", "university_id", "roles"}}`, not `{ user, token }`. Then call `GET /auth/me` for the profile, roles and permissions.
4. **Read errors correctly.** Errors are `{"success": false, "error": {"code", "message"}}`. `apiFetch` reads `body.message`; use `body.error.message` and branch on `body.error.code`. Successful bodies are `{"success": true, "data": ...}`, so the payload is `response.data.data`.
5. **Fix four Identity paths:**

   | Current default | Use |
   |---|---|
   | `GET /users/profile` | `GET /auth/me` (own profile) or `GET /users/{id}` |
   | `GET /users/validate/{id}` | `GET /validation/users/{id}?required_role=…` |
   | `POST /roles/assign` | `POST /users/{id}/roles` with `{"role_name": "…"}` |
   | `PUT /users/account-status/{id}` | `PATCH /users/{id}/status` with `{"status": "ACTIVE" \| "INACTIVE"}` |

6. Keep `VITE_G8_DEMO_MODE=true` until Group 8 is deployed.

---

## Checklist before a demo

- [ ] Every service verifies Identity tokens itself (JWKS), with no "accept anything" fallback active.
- [ ] No `dev` profile or dev-token endpoint on Render.
- [ ] Data that must survive restarts is in Postgres, not H2 or SQLite.
- [ ] The gateway has every deployed service's URL (`GET /gateway/routes` shows `"connected": true`).
- [ ] Open `https://<gateway>/health/services` 5 minutes before the demo to wake sleeping services.
