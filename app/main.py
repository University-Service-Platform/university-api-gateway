"""
University Services Platform API Gateway.

One entry point for the shared frontend: every call to /api/v1/... is checked (Identity token)
and forwarded to the service that owns the path. See app/routes.py for the routing table.
"""
import asyncio
import logging
import re
import time
import uuid
from contextlib import asynccontextmanager
from typing import Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.auth import JwksVerifier
from app.config import SERVICE_URL_VARIABLES, Settings, get_settings
from app.errors import GatewayError, error_response
from app.proxy import forward, raw_path
from app.routes import ROUTES, resolve
from app.timeutil import utc_timestamp

VERSION = "1.0.0"
PROXY_METHODS = ["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"]

# What each service answers to show it is running (Group 6 services have no health endpoint;
# their public OpenAPI document serves the same purpose).
HEALTH_PATHS = {
    "identity": "/health",
    "directory": "/health",
    "facility": "/v3/api-docs",
    "reservation": "/v3/api-docs",
    "service-request": "/actuator/health",
    "work-order": "/actuator/health",
    "group8": "/health",
}

_REQUEST_ID = re.compile(r"[A-Za-z0-9._-]{1,128}")
logger = logging.getLogger("gateway")


def create_app(settings: Optional[Settings] = None,
               transport: Optional[httpx.AsyncBaseTransport] = None) -> FastAPI:
    """`transport` lets tests replace every upstream service with an in-process fake."""
    settings = settings or get_settings()
    logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        timeout = httpx.Timeout(settings.upstream_read_timeout_seconds,
                                connect=settings.upstream_connect_timeout_seconds)
        async with httpx.AsyncClient(timeout=timeout, transport=transport, follow_redirects=False) as client:
            app.state.client = client
            app.state.verifier = JwksVerifier(settings.jwks_url, settings.jwt_issuer, settings.jwt_audience,
                                              settings.jwks_cache_seconds, client)
            yield

    app = FastAPI(title="University Services API Gateway", version=VERSION, lifespan=lifespan,
                  docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings

    @app.exception_handler(GatewayError)
    async def gateway_error(request: Request, exc: GatewayError):
        return error_response(exc.status_code, exc.code, exc.message, headers=exc.headers)

    @app.middleware("http")
    async def request_id_and_logging(request: Request, call_next):
        incoming = request.headers.get("x-request-id", "")
        request_id = incoming if _REQUEST_ID.fullmatch(incoming) else uuid.uuid4().hex
        request.state.request_id = request_id
        started = time.perf_counter()
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        logger.info("%s %s -> %s (%.1f ms) request_id=%s", request.method, request.url.path,
                    response.status_code, (time.perf_counter() - started) * 1000, request_id)
        return response

    # ---------------------------------------------------------------- gateway endpoints

    @app.get("/", include_in_schema=False)
    async def root():
        return {"service": "api-gateway", "version": VERSION, "health": "/health",
                "services": "/health/services", "routes": "/gateway/routes"}

    @app.get("/health")
    async def health():
        return {"status": "healthy", "service": "api-gateway", "version": VERSION}

    @app.get("/health/services")
    async def services_health(request: Request):
        """Checks every connected service. Also wakes sleeping free-tier services before a demo."""
        client: httpx.AsyncClient = request.app.state.client

        async def check(service: str):
            base = settings.service_urls.get(service)
            if base is None:
                return service, {"status": "not_configured", "env": SERVICE_URL_VARIABLES[service]}
            started = time.perf_counter()
            try:
                response = await client.get(base + HEALTH_PATHS[service])
                up = response.status_code < 500
                result = {"status": "up" if up else "down", "http_status": response.status_code}
            except httpx.HTTPError as exc:
                result = {"status": "down", "error": exc.__class__.__name__}
            result["latency_ms"] = round((time.perf_counter() - started) * 1000)
            return service, result

        results = dict(await asyncio.gather(*(check(s) for s in SERVICE_URL_VARIABLES)))
        configured = [r for r in results.values() if r["status"] != "not_configured"]
        overall = "healthy" if configured and all(r["status"] == "up" for r in configured) else "degraded"
        return {"status": overall, "timestamp": utc_timestamp(), "services": results}

    @app.get("/gateway/routes")
    async def route_table():
        """The routing table, for teams checking where their calls go."""
        return {"routes": [
            {
                "path": route.pattern.pattern if route.pattern else route.prefix + "/**",
                "service": route.service,
                "forwarded_as": (route.upstream_prefix + "/**") if route.upstream_prefix else "unchanged",
                "public": route.public,
                "methods": sorted(route.methods) if route.methods else "any",
                "connected": route.service in settings.service_urls,
            } for route in ROUTES
        ]}

    # ---------------------------------------------------------------- proxy

    @app.api_route("/{full_path:path}", methods=PROXY_METHODS, include_in_schema=False)
    async def proxy(request: Request, full_path: str):
        path = raw_path(request)
        route = resolve(path, request.method)
        if route is None:
            raise GatewayError(404, "ROUTE_NOT_FOUND", f"No service handles {request.method} {request.url.path}.")
        base_url = settings.service_urls.get(route.service)
        if base_url is None:
            raise GatewayError(404, "ROUTE_NOT_FOUND",
                               f"The {route.service} service is not connected to the gateway yet.")
        if not route.public and request.method != "OPTIONS":
            await request.app.state.verifier.verify(request.headers.get("authorization"))
        return await forward(request.app.state.client, request, path, route, base_url,
                             request.state.request_id, settings.max_request_body_bytes)

    # CORS last, so it wraps everything (errors included)
    if settings.cors_allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(settings.cors_allowed_origins),
            allow_credentials=False,  # tokens travel in the Authorization header, not cookies
            allow_methods=PROXY_METHODS,
            allow_headers=["Authorization", "Content-Type", "Accept", "X-Request-ID"],
            expose_headers=["X-Request-ID", "Deprecation", "Link"],
        )
    return app


app = create_app()
