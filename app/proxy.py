"""Forwarding a matched request to its upstream service and relaying the answer."""
import logging
from typing import Dict
from urllib.parse import unquote

import httpx
from fastapi import Request
from fastapi.responses import Response

from app.errors import GatewayError
from app.routes import Route

logger = logging.getLogger("gateway.proxy")

# Connection-level headers that must not be forwarded (RFC 9110 section 7.6.1), plus headers the
# HTTP client sets itself.
HOP_BY_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailer",
    "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}
# The gateway answers CORS for the whole platform; services' own CORS headers would conflict
_CORS_PREFIX = "access-control-"

# Response encodings the HTTP client decodes itself. Services are only asked for these, because
# hosts such as Render otherwise answer with Brotli, which the client would pass on still compressed.
UPSTREAM_ACCEPT_ENCODING = "gzip, deflate"
_DECODED_ENCODINGS = {"gzip", "deflate", "identity"}


def raw_path(request: Request) -> str:
    """
    The path exactly as the client sent it, still percent-encoded. Routing and forwarding both use
    it, so an ID such as "a%2Fb" stays one path segment. Dot segments (also encoded ones such as
    %2e%2e) are refused, so a path can't climb out of its route (/api/v1/auth/login/../../users).
    """
    raw = request.scope.get("raw_path", b"").decode("latin-1") or request.url.path
    if any(segment in (".", "..") for segment in unquote(raw).split("/")):
        raise GatewayError(400, "BAD_REQUEST", "Path must not contain '.' or '..' segments.")
    return raw


def forward_headers(request: Request, request_id: str) -> Dict[str, str]:
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP_BY_HOP}
    client_host = request.client.host if request.client else None
    prior = request.headers.get("x-forwarded-for")
    if client_host:
        headers["x-forwarded-for"] = f"{prior}, {client_host}" if prior else client_host
    headers["x-forwarded-proto"] = request.headers.get("x-forwarded-proto", request.url.scheme)
    headers["x-forwarded-host"] = request.headers.get("x-forwarded-host", request.headers.get("host", ""))
    headers["x-request-id"] = request_id
    headers["accept-encoding"] = UPSTREAM_ACCEPT_ENCODING
    return headers


def relay_headers(upstream: httpx.Response) -> Dict[str, str]:
    # httpx decodes gzip/deflate, so those bodies are sent as plain bytes without the header.
    # Any other encoding is passed through untouched, header included, for the browser to decode.
    encodings = [e.strip().lower() for e in upstream.headers.get("content-encoding", "").split(",") if e.strip()]
    decoded = all(e in _DECODED_ENCODINGS for e in encodings)
    skip = HOP_BY_HOP | ({"content-encoding"} if decoded else set())
    return {k: v for k, v in upstream.headers.items()
            if k.lower() not in skip and not k.lower().startswith(_CORS_PREFIX)}


async def forward(client: httpx.AsyncClient, request: Request, path: str, route: Route, base_url: str,
                  request_id: str, max_body_bytes: int) -> Response:
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_body_bytes:
        raise GatewayError(413, "PAYLOAD_TOO_LARGE", "Request body is too large.")
    body = await request.body()
    if len(body) > max_body_bytes:
        raise GatewayError(413, "PAYLOAD_TOO_LARGE", "Request body is too large.")

    url = base_url + route.upstream_path(path)
    if request.url.query:
        url += "?" + request.url.query

    try:
        upstream = await client.request(request.method, url, headers=forward_headers(request, request_id),
                                        content=body)
    except httpx.TimeoutException:
        logger.warning("%s %s -> %s timed out (request_id=%s)", request.method, request.url.path,
                       route.service, request_id)
        raise GatewayError(504, "UPSTREAM_TIMEOUT",
                           f"The {route.service} service did not answer in time. Please retry later.")
    except httpx.HTTPError as exc:
        logger.warning("%s %s -> %s unreachable: %s (request_id=%s)", request.method, request.url.path,
                       route.service, exc.__class__.__name__, request_id)
        raise GatewayError(502, "UPSTREAM_UNAVAILABLE",
                           f"The {route.service} service is unavailable. Please retry later.")

    return Response(content=upstream.content, status_code=upstream.status_code,
                    headers=relay_headers(upstream))
