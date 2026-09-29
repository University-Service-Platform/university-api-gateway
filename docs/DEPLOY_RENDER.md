# Deploying the API Gateway on Render

The Blueprint ([`render.yaml`](../render.yaml)) creates one free Docker web service, `university-api-gateway`. It has no database and no secrets.

## Steps

1. Merge the code into `main`. The Blueprint deploys `main` and redeploys on every push.
2. Render dashboard → **New** → **Blueprint** → select this repository.
3. Render asks for the values marked `sync: false`. **Leave a service empty if it is not deployed yet**; its routes answer `404 ROUTE_NOT_FOUND` until you add its URL.

   | Variable | Value |
   |---|---|
   | `DIRECTORY_SERVICE_URL` | Directory Service URL, e.g. `https://university-directory-service.onrender.com` |
   | `FACILITY_SERVICE_URL` | Group 6 facility-resource-service URL |
   | `RESERVATION_SERVICE_URL` | Group 6 reservation-service URL |
   | `SERVICE_REQUEST_SERVICE_URL` | Group 7 service-request-service URL |
   | `WORK_ORDER_SERVICE_URL` | Group 7 work-order-service URL |
   | `GROUP8_SERVICE_URL` | Group 8 service URL |
   | `CORS_ALLOWED_ORIGINS` | The shared frontend's URL, e.g. `https://university-services-frontend.onrender.com` (comma-separate several) |

   `IDENTITY_SERVICE_URL` is already set to `https://university-identity-service.onrender.com`.
4. Click **Apply**. When the deploy finishes, check:

   | URL | Expected |
   |---|---|
   | `https://<gateway>/health` | `{"status": "healthy", ...}` |
   | `https://<gateway>/health/services` | `identity: up`; other services `up` or `not_configured` |
   | `https://<gateway>/gateway/routes` | The route table, `connected` per service |
   | `POST https://<gateway>/api/v1/auth/login` | Identity's login response |

## Adding a service later

Open **university-api-gateway → Environment**, set its `..._SERVICE_URL`, and save. Render restarts the gateway with the new route active; no code change is needed.

## Free plan notes

- The gateway sleeps after 15 minutes without traffic and every service behind it sleeps too. Opening `/health/services` wakes them all at once, so open it a few minutes before a demo.
- Requests wait up to 90 s (`UPSTREAM_READ_TIMEOUT_SECONDS`) for a sleeping service, so a cold first request is slow but succeeds.
- All services should be in the same region (`singapore`) to keep requests fast.
