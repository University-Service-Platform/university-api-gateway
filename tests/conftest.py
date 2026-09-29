"""
Test fixtures: a real RSA key pair plays the Identity Service's signing key, and every upstream
service is an in-process fake (httpx.MockTransport) that echoes what it received.
"""
import json
import time
from typing import Callable, Dict, List

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

from app.config import Settings
from app.main import create_app

ISSUER = "university-identity-service"
AUDIENCE = "university-services-platform"
KID = "test-key-1"

PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTHER_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)

SERVICE_URLS = {
    "identity": "http://identity.test",
    "directory": "http://directory.test",
    "facility": "http://facility.test",
    "reservation": "http://reservation.test",
    "service-request": "http://service-request.test",
    "work-order": "http://work-order.test",
    # group8 deliberately not connected
}
HOST_TO_SERVICE = {url.split("//")[1]: name for name, url in SERVICE_URLS.items()}


def jwk_for(private_key, kid: str) -> Dict:
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    return {**jwk, "kid": kid, "alg": "RS256", "use": "sig"}


def make_token(key=PRIVATE_KEY, kid=KID, **claims) -> str:
    now = int(time.time())
    payload = {"sub": "usr-student-001", "university_id": "STU001", "roles": ["STUDENT"],
               "iss": ISSUER, "aud": AUDIENCE, "iat": now, "exp": now + 3600, **claims}
    payload = {k: v for k, v in payload.items() if v is not None}
    return jwt.encode(payload, key, algorithm="RS256", headers={"kid": kid})


def bearer(token: str = None) -> Dict[str, str]:
    return {"Authorization": f"Bearer {token or make_token()}"}


class FakePlatform:
    """All upstream services. Records requests; each service echoes the request as JSON."""

    def __init__(self):
        self.requests: List[httpx.Request] = []
        self.jwks = {"keys": [jwk_for(PRIVATE_KEY, KID)]}
        self.jwks_status = 200
        self.jwks_calls = 0
        self.override: Dict[str, Callable[[httpx.Request], httpx.Response]] = {}

    def handler(self, request: httpx.Request) -> httpx.Response:
        service = HOST_TO_SERVICE.get(request.url.host, request.url.host)
        if service == "identity" and request.url.path == "/.well-known/jwks.json":
            self.jwks_calls += 1
            return httpx.Response(self.jwks_status, json=self.jwks)
        self.requests.append(request)
        if service in self.override:
            return self.override[service](request)
        return httpx.Response(200, json={
            "service": service,
            "method": request.method,
            "path": request.url.raw_path.decode().split("?")[0],
            "query": request.url.query.decode(),
            "headers": dict(request.headers),
            "body": request.content.decode(),
        })

    @property
    def last(self) -> httpx.Request:
        return self.requests[-1]


@pytest.fixture
def platform() -> FakePlatform:
    return FakePlatform()


def build_client(platform: FakePlatform, **settings_overrides) -> TestClient:
    settings = Settings(**{
        "service_urls": dict(SERVICE_URLS),
        "jwks_url": "http://identity.test/.well-known/jwks.json",
        **settings_overrides,
    })
    app = create_app(settings, transport=httpx.MockTransport(platform.handler))
    return TestClient(app)


@pytest.fixture
def gateway(platform):
    with build_client(platform) as client:
        yield client
