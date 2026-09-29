"""Tokens are verified with the Identity Service's JWKS before anything is forwarded."""
import time

import jwt
import pytest

from tests.conftest import KID, OTHER_KEY, PRIVATE_KEY, bearer, build_client, jwk_for, make_token

PROTECTED = "/api/v1/auth/me"


def test_valid_token_is_forwarded_unchanged(gateway, platform):
    token = make_token()
    response = gateway.get(PROTECTED, headers=bearer(token))
    assert response.status_code == 200
    assert response.json()["headers"]["authorization"] == f"Bearer {token}"


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer "}, {"Authorization": "Basic dXNlcjpwYXNz"}])
def test_missing_token_is_401_unauthorized(gateway, platform, headers):
    response = gateway.get(PROTECTED, headers=headers)
    assert response.status_code == 401
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "UNAUTHORIZED"
    assert response.headers["www-authenticate"] == "Bearer"
    assert platform.requests == []          # nothing reached the service


now = int(time.time())
BAD_TOKENS = {
    "expired": lambda: make_token(exp=now - 60, iat=now - 3700),
    "wrong issuer": lambda: make_token(iss="someone-else"),
    "wrong audience": lambda: make_token(aud="another-platform"),
    "no subject": lambda: make_token(sub=None),
    "signed with another key": lambda: make_token(key=OTHER_KEY),
    "HS256 with a guessed secret": lambda: jwt.encode(
        {"sub": "usr-admin-001", "roles": ["ADMIN"], "iss": "university-identity-service",
         "aud": "university-services-platform", "exp": now + 3600}, "secret", algorithm="HS256"),
    "not a JWT": lambda: "not.a.jwt",
}


@pytest.mark.parametrize("case", BAD_TOKENS)
def test_invalid_tokens_are_401_invalid_token(gateway, platform, case):
    response = gateway.get(PROTECTED, headers=bearer(BAD_TOKENS[case]()))
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "INVALID_TOKEN"
    assert platform.requests == []


def test_jwks_is_cached(gateway, platform):
    for _ in range(3):
        assert gateway.get(PROTECTED, headers=bearer()).status_code == 200
    assert platform.jwks_calls == 1


def test_rotated_signing_key_is_picked_up(gateway, platform):
    assert gateway.get(PROTECTED, headers=bearer()).status_code == 200
    # Identity switches to a new key; the unknown kid makes the gateway fetch the JWKS again
    platform.jwks = {"keys": [jwk_for(OTHER_KEY, "rotated-key")]}
    import app.auth as auth
    original = auth.MIN_REFRESH_INTERVAL_SECONDS
    auth.MIN_REFRESH_INTERVAL_SECONDS = 0
    try:
        response = gateway.get(PROTECTED, headers=bearer(make_token(key=OTHER_KEY, kid="rotated-key")))
    finally:
        auth.MIN_REFRESH_INTERVAL_SECONDS = original
    assert response.status_code == 200
    assert platform.jwks_calls == 2


def test_unknown_key_ids_do_not_refetch_every_time(gateway, platform):
    assert gateway.get(PROTECTED, headers=bearer()).status_code == 200
    for _ in range(3):
        response = gateway.get(PROTECTED, headers=bearer(make_token(key=OTHER_KEY, kid="unknown")))
        assert response.status_code == 401
    assert platform.jwks_calls == 1   # throttled: at most one refresh per interval


def test_identity_unavailable_before_first_key_fetch_is_503(gateway, platform):
    platform.jwks_status = 503
    response = gateway.get(PROTECTED, headers=bearer())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AUTH_UNAVAILABLE"
    assert platform.requests == []


def test_cached_keys_keep_working_while_identity_is_down(platform):
    with build_client(platform, jwks_cache_seconds=0) as client:
        assert client.get(PROTECTED, headers=bearer()).status_code == 200
        platform.jwks_status = 503
        import app.auth as auth
        original = auth.MIN_REFRESH_INTERVAL_SECONDS
        auth.MIN_REFRESH_INTERVAL_SECONDS = 0
        try:
            assert client.get(PROTECTED, headers=bearer()).status_code == 200
        finally:
            auth.MIN_REFRESH_INTERVAL_SECONDS = original


def test_no_token_verification_configured_is_503(platform):
    with build_client(platform, jwks_url=None) as client:
        response = client.get(PROTECTED, headers=bearer())
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "AUTH_UNAVAILABLE"


def test_preflight_options_needs_no_token(gateway, platform):
    response = gateway.options(PROTECTED)
    assert response.status_code == 200
    assert response.json()["method"] == "OPTIONS"
