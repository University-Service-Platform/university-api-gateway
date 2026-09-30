"""Requests and responses pass through the gateway intact; failures use the platform envelope."""
import httpx
import pytest
from starlette.requests import Request

from app.errors import GatewayError
from app.proxy import raw_path
from tests.conftest import bearer, build_client

URL = "/api/v1/users"


def test_method_body_and_query_are_forwarded(gateway, platform):
    response = gateway.post(URL + "?page=2&q=a%20b", json={"name": "Test User"}, headers=bearer())
    echoed = response.json()
    assert echoed["method"] == "POST"
    assert echoed["query"] == "page=2&q=a%20b"
    assert echoed["body"] == '{"name":"Test User"}'
    assert echoed["headers"]["content-type"] == "application/json"


def test_encoded_slash_in_an_id_stays_one_segment(gateway, platform):
    assert gateway.get(URL + "/a%2Fb", headers=bearer()).json()["path"] == URL + "/a%2Fb"


def test_request_id_is_generated_and_forwarded(gateway, platform):
    response = gateway.get(URL, headers=bearer())
    request_id = response.headers["x-request-id"]
    assert len(request_id) == 32
    assert response.json()["headers"]["x-request-id"] == request_id


def test_incoming_request_id_is_reused(gateway, platform):
    response = gateway.get(URL, headers={**bearer(), "X-Request-ID": "trace-abc-123"})
    assert response.headers["x-request-id"] == "trace-abc-123"
    assert response.json()["headers"]["x-request-id"] == "trace-abc-123"


def test_unsafe_request_id_is_replaced(gateway, platform):
    response = gateway.get(URL, headers={**bearer(), "X-Request-ID": "bad id\twith spaces"})
    assert response.headers["x-request-id"] != "bad id\twith spaces"


def test_forwarded_headers_are_set(gateway, platform):
    headers = gateway.get(URL, headers={**bearer(), "X-Forwarded-For": "203.0.113.9"}).json()["headers"]
    assert headers["x-forwarded-for"].startswith("203.0.113.9, ")
    assert headers["x-forwarded-proto"] == "http"
    assert headers["x-forwarded-host"] == "testserver"


def test_hop_by_hop_headers_are_not_forwarded(gateway, platform):
    headers = gateway.get(URL, headers={**bearer(), "TE": "trailers", "Upgrade": "websocket"}).json()["headers"]
    assert "te" not in headers and "upgrade" not in headers
    assert headers["host"] == "identity.test"


def test_upstream_status_body_and_headers_are_relayed(gateway, platform):
    platform.override["identity"] = lambda request: httpx.Response(
        409, json={"success": False, "error": {"code": "EMAIL_ALREADY_EXISTS", "message": "taken"}},
        headers={"Deprecation": "true", "Access-Control-Allow-Origin": "*"})
    response = gateway.post(URL, json={}, headers=bearer())
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "EMAIL_ALREADY_EXISTS"
    assert response.headers["deprecation"] == "true"
    # The gateway owns CORS; a service's own CORS headers are dropped
    assert "access-control-allow-origin" not in response.headers


@pytest.mark.parametrize("exception,status,code", [
    (httpx.ConnectError("refused"), 502, "UPSTREAM_UNAVAILABLE"),
    (httpx.ReadTimeout("slow"), 504, "UPSTREAM_TIMEOUT"),
])
def test_upstream_failures(gateway, platform, exception, status, code):
    def fail(request):
        raise exception
    platform.override["facility"] = fail
    response = gateway.get("/api/v1/resources", headers=bearer())
    assert response.status_code == status
    body = response.json()
    assert body["error"]["code"] == code
    assert "facility" in body["error"]["message"]
    assert "refused" not in response.text and "slow" not in response.text
    assert set(body) == {"success", "error", "timestamp"}


@pytest.mark.parametrize("raw", [
    b"/api/v1/auth/login/../../users",
    b"/api/v1/auth/login/%2e%2e/%2E%2E/users",
    b"/api/v1/users/./x",
])
def test_dot_segments_are_refused(raw):
    """A public route must not be usable to reach a protected one (checked on the raw request path)."""
    request = Request({"type": "http", "method": "POST", "path": raw.decode(), "raw_path": raw,
                       "query_string": b"", "headers": []})
    with pytest.raises(GatewayError) as error:
        raw_path(request)
    assert (error.value.status_code, error.value.code) == (400, "BAD_REQUEST")


def test_encoded_dot_segments_are_refused_end_to_end(gateway, platform):
    # Clients don't normalise percent-encoded dots, so this reaches the gateway as sent
    response = gateway.post("/api/v1/auth/login/%2e%2e/%2e%2e/users", json={})
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "BAD_REQUEST"
    assert platform.requests == []


def test_oversized_body_is_413(platform):
    with build_client(platform, max_request_body_bytes=10) as client:
        response = client.post(URL, content=b"x" * 11, headers=bearer())
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"
    assert platform.requests == []


def test_services_are_only_asked_for_encodings_the_gateway_can_decode(gateway, platform):
    """Regression: Render answered Accept-Encoding: br with Brotli, which reached the browser garbled."""
    headers = gateway.get(URL, headers={**bearer(), "Accept-Encoding": "br"}).json()["headers"]
    assert headers["accept-encoding"] == "gzip, deflate"


def test_gzip_responses_are_decoded(gateway, platform):
    import gzip, json
    body = json.dumps({"success": True, "data": {"ok": 1}}).encode()
    platform.override["identity"] = lambda request: httpx.Response(
        200, content=gzip.compress(body), headers={"Content-Encoding": "gzip", "Content-Type": "application/json"})
    response = gateway.get(URL, headers={**bearer(), "Accept-Encoding": "identity"})
    assert response.json() == {"success": True, "data": {"ok": 1}}
    assert "content-encoding" not in response.headers


def test_undecodable_encodings_pass_through_with_their_header(gateway, platform):
    brotli_bytes = bytes([0x8B, 0x05, 0x80]) + b"compressed"
    platform.override["identity"] = lambda request: httpx.Response(
        200, content=brotli_bytes, headers={"Content-Encoding": "br"})
    response = gateway.get(URL, headers={**bearer(), "Accept-Encoding": "identity"})
    # Unchanged bytes plus the header, so the browser (which asked for br) can decode them itself
    assert response.headers["content-encoding"] == "br"
    assert response.content == brotli_bytes
