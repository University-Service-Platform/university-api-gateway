"""
Access-token verification with the Identity Service's public keys (JWKS).

The gateway only checks that a token is genuine and current (RS256 signature, exp, iss, aud,
sub). What a user may do is still decided by each service, which also verifies the token.
"""
import asyncio
import json
import logging
import time
from typing import Any, Dict, Optional

import httpx
import jwt
from jwt.algorithms import RSAAlgorithm

from app.errors import GatewayError

logger = logging.getLogger("gateway.auth")

# Don't hammer the Identity Service when tokens carry an unknown key id
MIN_REFRESH_INTERVAL_SECONDS = 10


def _unauthorized(code: str, message: str) -> GatewayError:
    return GatewayError(401, code, message, headers={"WWW-Authenticate": "Bearer"})


class JwksVerifier:
    def __init__(self, jwks_url: Optional[str], issuer: str, audience: str, cache_seconds: int,
                 client: httpx.AsyncClient):
        self.jwks_url = jwks_url
        self.issuer = issuer
        self.audience = audience
        self.cache_seconds = cache_seconds
        self.client = client
        self._keys: Dict[str, Any] = {}
        self._fetched_at = float("-inf")    # last successful fetch
        self._attempted_at = float("-inf")  # last fetch attempt, successful or not
        self._lock = asyncio.Lock()

    async def verify(self, authorization: Optional[str]) -> Dict[str, Any]:
        if not authorization:
            raise _unauthorized("UNAUTHORIZED", "Authentication credentials were not provided.")
        scheme, _, token = authorization.partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise _unauthorized("UNAUTHORIZED", "Authentication credentials were not provided.")
        token = token.strip()

        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise _unauthorized("INVALID_TOKEN", "Authentication token is invalid or expired.")
        if header.get("alg") != "RS256":
            raise _unauthorized("INVALID_TOKEN", "Authentication token is invalid or expired.")

        key = await self._key(header.get("kid"))
        if key is None:
            raise _unauthorized("INVALID_TOKEN", "Authentication token is invalid or expired.")
        try:
            return jwt.decode(
                token, key, algorithms=["RS256"], audience=self.audience, issuer=self.issuer,
                options={"require": ["exp", "sub", "iss", "aud"]},
            )
        except jwt.PyJWTError:
            raise _unauthorized("INVALID_TOKEN", "Authentication token is invalid or expired.")

    def _may_refresh(self) -> bool:
        return not self._keys or time.monotonic() - self._attempted_at >= MIN_REFRESH_INTERVAL_SECONDS

    async def _key(self, kid: Optional[str]):
        stale = time.monotonic() - self._fetched_at > self.cache_seconds
        if (stale or kid not in self._keys) and self._may_refresh():
            async with self._lock:
                if self._may_refresh():
                    await self._refresh()
        if kid is None and len(self._keys) == 1:
            return next(iter(self._keys.values()))
        return self._keys.get(kid)

    async def _refresh(self) -> None:
        self._attempted_at = time.monotonic()
        if not self.jwks_url:
            raise GatewayError(503, "AUTH_UNAVAILABLE",
                               "Token verification is not configured (IDENTITY_SERVICE_URL is not set).")
        try:
            response = await self.client.get(self.jwks_url)
            response.raise_for_status()
            keys = {}
            for jwk in response.json()["keys"]:
                if jwk.get("kty") == "RSA":
                    keys[jwk.get("kid")] = RSAAlgorithm.from_jwk(json.dumps(jwk))
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            logger.warning("Could not fetch JWKS from %s: %s", self.jwks_url, exc)
            if self._keys:
                return  # keep verifying with the keys we already have
            raise GatewayError(503, "AUTH_UNAVAILABLE",
                               "Tokens cannot be verified right now because the Identity Service is "
                               "unavailable. Please retry later.")
        self._keys = keys
        self._fetched_at = time.monotonic()
