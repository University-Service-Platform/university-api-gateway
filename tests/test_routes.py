"""Every path the frontends call reaches the right service with the path that service serves."""
import pytest

from tests.conftest import bearer, build_client

ROUTING = [
    # (method, path called by a frontend, service, path the service receives)
    # Identity (Group 5)
    ("GET", "/api/v1/auth/me", "identity", "/api/v1/auth/me"),
    ("POST", "/api/v1/auth/change-password", "identity", "/api/v1/auth/change-password"),
    ("GET", "/api/v1/users", "identity", "/api/v1/users"),
    ("PATCH", "/api/v1/users/usr-student-001/status", "identity", "/api/v1/users/usr-student-001/status"),
    ("GET", "/api/v1/roles/TECHNICIAN", "identity", "/api/v1/roles/TECHNICIAN"),
    ("GET", "/api/v1/audit-logs", "identity", "/api/v1/audit-logs"),
    ("GET", "/api/v1/validation/users/STU001", "identity", "/api/v1/validation/users/STU001"),
    ("GET", "/api/v1/validation/users/STU001/eligibility", "identity", "/api/v1/validation/users/STU001/eligibility"),
    # Directory (Group 5): shares /api/v1/validation/users with Identity
    ("GET", "/api/v1/validation/users/STU001/affiliation", "directory", "/api/v1/validation/users/STU001/affiliation"),
    ("GET", "/api/v1/validation/users/usr-1/responsibilities", "directory",
     "/api/v1/validation/users/usr-1/responsibilities"),
    ("GET", "/api/v1/validation/faculties/FSC", "directory", "/api/v1/validation/faculties/FSC"),
    ("GET", "/api/v1/validation/departments/CS", "directory", "/api/v1/validation/departments/CS"),
    ("GET", "/api/v1/validation/service-units/ITHD", "directory", "/api/v1/validation/service-units/ITHD"),
    ("GET", "/api/v1/faculties", "directory", "/api/v1/faculties"),
    ("POST", "/api/v1/departments", "directory", "/api/v1/departments"),
    ("GET", "/api/v1/service-units/unit-ithd-1", "directory", "/api/v1/service-units/unit-ithd-1"),
    ("GET", "/api/v1/affiliations/users/usr-1", "directory", "/api/v1/affiliations/users/usr-1"),
    ("GET", "/api/v1/responsibilities", "directory", "/api/v1/responsibilities"),
    # Group 6 facility-resource-service serves /api/...
    ("GET", "/api/v1/resources/7", "facility", "/api/resources/7"),
    ("GET", "/api/v1/resources/code/LAB-1/validate", "facility", "/api/resources/code/LAB-1/validate"),
    ("GET", "/api/v1/facilities", "facility", "/api/facilities"),
    ("GET", "/api/v1/availability-rules/resource/7", "facility", "/api/availability-rules/resource/7"),
    ("GET", "/api/resources/7", "facility", "/api/resources/7"),               # Group 6 frontend
    ("POST", "/api/facilities", "facility", "/api/facilities"),                # Group 6 frontend
    # Group 6 reservation-service serves /api/v1/reservations
    ("GET", "/api/v1/reservations/my", "reservation", "/api/v1/reservations/my"),
    ("POST", "/api/reservations/5/approve", "reservation", "/api/v1/reservations/5/approve"),  # Group 6 frontend
    ("GET", "/api/reservations", "reservation", "/api/v1/reservations"),
    # Group 7 serves /api/...
    ("PATCH", "/api/v1/service-requests/REQ-1/triage", "service-request", "/api/service-requests/REQ-1/triage"),
    ("POST", "/api/service-requests", "service-request", "/api/service-requests"),   # Group 7 frontend
    ("GET", "/api/v1/work-orders/by-request/9", "work-order", "/api/work-orders/by-request/9"),
    ("PATCH", "/api/work-orders/WO-1/start", "work-order", "/api/work-orders/WO-1/start"),  # Group 7 frontend
]


@pytest.mark.parametrize("method,path,service,upstream_path", ROUTING)
def test_route(gateway, platform, method, path, service, upstream_path):
    response = gateway.request(method, path, headers=bearer())
    assert response.status_code == 200, response.text
    echoed = response.json()
    assert (echoed["service"], echoed["method"], echoed["path"]) == (service, method, upstream_path)


def test_login_is_public_and_reaches_identity(gateway, platform):
    response = gateway.post("/api/v1/auth/login", json={"username": "STU001", "password": "x"})
    assert response.status_code == 200
    assert response.json()["service"] == "identity"
    assert "authorization" not in response.json()["headers"]


def test_only_post_login_is_public(gateway, platform):
    assert gateway.get("/api/v1/auth/login").status_code == 401


def test_jwks_is_public(gateway, platform):
    response = gateway.get("/.well-known/jwks.json")
    assert response.status_code == 200
    assert response.json()["keys"][0]["kty"] == "RSA"


@pytest.mark.parametrize("path", [
    "/api/v1/usersX",                # prefix must end at a "/" boundary
    "/api/v1/unknown",
    "/api/dev/token",                # Group 7 dev-token endpoint is never exposed
    "/h2-console",
    "/actuator/health",
    "/api/v1/validation/users/STU001/affiliation/extra",   # not the Directory pattern ...
])
def test_unrouted_paths_are_404(gateway, platform, path):
    response = gateway.get(path, headers=bearer())
    if path.endswith("/extra"):
        # ... so it falls through to Identity's /api/v1/validation/users rule instead
        assert response.json()["service"] == "identity"
        return
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "ROUTE_NOT_FOUND"
    assert platform.requests == []


def test_service_without_url_is_404_so_frontend_demo_mode_applies(gateway, platform):
    """Group 8 is not deployed; its frontend shows demo data when the route is 'not registered' (404)."""
    response = gateway.get("/api/v1/events", headers=bearer())
    assert response.status_code == 404
    body = response.json()
    assert body["error"]["code"] == "ROUTE_NOT_FOUND"
    assert "group8" in body["error"]["message"]


def test_group8_is_routed_once_connected(platform):
    with build_client(platform, service_urls={"group8": "http://group8.test"}) as client:
        response = client.get("/api/v1/registrations/mine", headers=bearer())
    assert response.status_code == 200
    assert response.json()["path"] == "/api/v1/registrations/mine"
