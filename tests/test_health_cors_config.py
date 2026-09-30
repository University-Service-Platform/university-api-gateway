"""Gateway health, service status, route listing, CORS and configuration."""
import httpx
import pytest

from app.config import Settings, load_settings
from tests.conftest import bearer, build_client

FRONTEND = "https://usm-frontend.onrender.com"


# ---------------------------------------------------------------- health

def test_health(gateway):
    body = gateway.get("/health").json()
    assert body == {"status": "healthy", "service": "api-gateway", "version": "1.0.0"}


def test_services_health_reports_each_service(gateway, platform):
    def down(request):
        raise httpx.ConnectError("refused")
    platform.override["work-order"] = down
    platform.override["reservation"] = lambda request: httpx.Response(200, json={})

    body = gateway.get("/health/services").json()
    services = body["services"]
    assert services["identity"]["status"] == "up"
    assert services["work-order"] == {"status": "down", "error": "ConnectError",
                                      "latency_ms": services["work-order"]["latency_ms"]}
    assert services["event"] == {"status": "not_configured", "env": "EVENT_SERVICE_URL"}
    assert services["communication"] == {"status": "not_configured", "env": "COMMUNICATION_SERVICE_URL"}
    assert body["status"] == "degraded"
    # Each service is asked on its own health path
    paths = {r.url.host.split(".")[0]: r.url.path for r in platform.requests}
    assert paths["identity"] == "/health"
    assert paths["service-request"] == "/actuator/health"
    assert paths["facility"] == "/v3/api-docs"


def test_services_health_is_healthy_when_all_connected_services_are_up(gateway, platform):
    assert gateway.get("/health/services").json()["status"] == "healthy"


def test_route_table_is_listed(gateway):
    routes = gateway.get("/gateway/routes").json()["routes"]
    login = next(r for r in routes if r["path"] == "/api/v1/auth/login/**")
    assert login["public"] is True and login["methods"] == ["POST"]
    facility = next(r for r in routes if r["path"] == "/api/v1/resources/**")
    assert facility["forwarded_as"] == "/api/resources/**"
    events = next(r for r in routes if r["path"] == "/api/v1/events/**")
    assert events["connected"] is False


# ---------------------------------------------------------------- CORS

@pytest.fixture
def cors_gateway(platform):
    with build_client(platform, cors_allowed_origins=(FRONTEND,)) as client:
        yield client


def test_preflight_from_the_frontend(cors_gateway, platform):
    response = cors_gateway.options("/api/v1/auth/me", headers={
        "Origin": FRONTEND, "Access-Control-Request-Method": "GET",
        "Access-Control-Request-Headers": "authorization,x-request-id"})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == FRONTEND
    assert "authorization" in response.headers["access-control-allow-headers"].lower()
    assert "access-control-allow-credentials" not in response.headers
    assert platform.requests == []     # answered by the gateway, never forwarded


def test_other_origins_are_refused(cors_gateway):
    response = cors_gateway.options("/api/v1/auth/me", headers={
        "Origin": "https://evil.example", "Access-Control-Request-Method": "GET"})
    assert response.status_code == 400
    assert "access-control-allow-origin" not in response.headers


def test_errors_carry_cors_headers_so_the_browser_can_read_them(cors_gateway):
    response = cors_gateway.get("/api/v1/auth/me", headers={"Origin": FRONTEND})
    assert response.status_code == 401
    assert response.headers["access-control-allow-origin"] == FRONTEND
    assert "x-request-id" in response.headers["access-control-expose-headers"].lower()


def test_proxied_responses_carry_cors_headers(cors_gateway, platform):
    response = cors_gateway.get("/api/v1/auth/me", headers={**bearer(), "Origin": FRONTEND})
    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == FRONTEND


# ---------------------------------------------------------------- configuration

def test_settings_from_environment(monkeypatch):
    monkeypatch.setenv("IDENTITY_SERVICE_URL", "https://university-identity-service.onrender.com/")
    monkeypatch.setenv("FACILITY_SERVICE_URL", "http://localhost:8081")
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", f"{FRONTEND}, http://localhost:5173")
    settings = load_settings()
    assert settings.service_urls == {"identity": "https://university-identity-service.onrender.com",
                                     "facility": "http://localhost:8081"}
    assert settings.jwks_url == "https://university-identity-service.onrender.com/.well-known/jwks.json"
    assert settings.cors_allowed_origins == (FRONTEND, "http://localhost:5173")
    assert settings.upstream_read_timeout_seconds == 90.0


def test_service_url_must_be_http():
    with pytest.raises(RuntimeError, match="FACILITY_SERVICE_URL"):
        Settings(service_urls={"facility": "localhost:8081"}).validate()


def test_production_requires_token_verification():
    with pytest.raises(RuntimeError, match="IDENTITY_SERVICE_URL"):
        Settings(environment="production").validate()


def test_production_rejects_wildcard_cors():
    with pytest.raises(RuntimeError, match="CORS_ALLOWED_ORIGINS"):
        Settings(environment="production", jwks_url="https://id.example/.well-known/jwks.json",
                 cors_allowed_origins=("*",)).validate()
