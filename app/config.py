"""
Gateway configuration, read from environment variables.

Every upstream service URL is optional: a service without a URL is "not deployed yet" and
its routes answer 404 ROUTE_NOT_FOUND (which the frontend's demo modes treat as
"route not registered").
"""
import os
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, Optional, Tuple

# Upstream service keys -> environment variable holding the service's base URL
SERVICE_URL_VARIABLES: Dict[str, str] = {
    "identity": "IDENTITY_SERVICE_URL",
    "directory": "DIRECTORY_SERVICE_URL",
    "facility": "FACILITY_SERVICE_URL",
    "reservation": "RESERVATION_SERVICE_URL",
    "service-request": "SERVICE_REQUEST_SERVICE_URL",
    "work-order": "WORK_ORDER_SERVICE_URL",
    "event": "EVENT_SERVICE_URL",                   # Group 8 event-service
    "communication": "COMMUNICATION_SERVICE_URL",   # Group 8 communication-feedback-service
}

DEFAULT_JWT_ISSUER = "university-identity-service"
DEFAULT_JWT_AUDIENCE = "university-services-platform"
JWKS_PATH = "/.well-known/jwks.json"


def _env(name: str, default: Optional[str] = None) -> Optional[str]:
    value = os.getenv(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _env_float(name: str, default: float) -> float:
    value = _env(name)
    return float(value) if value is not None else default


def _env_int(name: str, default: int) -> int:
    value = _env(name)
    return int(value) if value is not None else default


def _env_list(name: str) -> Tuple[str, ...]:
    value = _env(name)
    if value is None:
        return ()
    return tuple(item.strip() for item in value.split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    environment: str = "development"
    # service key -> base URL (no trailing slash); missing = not deployed
    service_urls: Dict[str, str] = field(default_factory=dict)
    jwks_url: Optional[str] = None
    jwt_issuer: str = DEFAULT_JWT_ISSUER
    jwt_audience: str = DEFAULT_JWT_AUDIENCE
    jwks_cache_seconds: int = 300
    cors_allowed_origins: Tuple[str, ...] = ()
    # Free hosting (e.g. Render) sleeps idle services; waking one takes about a minute
    upstream_connect_timeout_seconds: float = 10.0
    upstream_read_timeout_seconds: float = 90.0
    max_request_body_bytes: int = 10 * 1024 * 1024
    log_level: str = "INFO"

    @property
    def is_production(self) -> bool:
        return self.environment.lower() == "production"

    def validate(self) -> None:
        for key, url in self.service_urls.items():
            if not url.startswith(("http://", "https://")):
                raise RuntimeError(f"{SERVICE_URL_VARIABLES[key]} must start with http:// or https://.")
        if self.is_production:
            if not self.jwks_url:
                raise RuntimeError("IDENTITY_SERVICE_URL (or JWKS_URL) must be set when ENVIRONMENT=production.")
            if "*" in self.cors_allowed_origins:
                raise RuntimeError("CORS_ALLOWED_ORIGINS must list explicit origins in production, not '*'.")


def load_settings() -> Settings:
    service_urls = {}
    for key, variable in SERVICE_URL_VARIABLES.items():
        url = _env(variable)
        if url:
            service_urls[key] = url.rstrip("/")

    identity_url = service_urls.get("identity")
    settings = Settings(
        environment=_env("ENVIRONMENT", "development"),
        service_urls=service_urls,
        jwks_url=_env("JWKS_URL") or (f"{identity_url}{JWKS_PATH}" if identity_url else None),
        jwt_issuer=_env("JWT_ISSUER", DEFAULT_JWT_ISSUER),
        jwt_audience=_env("JWT_AUDIENCE", DEFAULT_JWT_AUDIENCE),
        jwks_cache_seconds=_env_int("JWKS_CACHE_SECONDS", 300),
        cors_allowed_origins=_env_list("CORS_ALLOWED_ORIGINS"),
        upstream_connect_timeout_seconds=_env_float("UPSTREAM_CONNECT_TIMEOUT_SECONDS", 10.0),
        upstream_read_timeout_seconds=_env_float("UPSTREAM_READ_TIMEOUT_SECONDS", 90.0),
        max_request_body_bytes=_env_int("MAX_REQUEST_BODY_BYTES", 10 * 1024 * 1024),
        log_level=_env("LOG_LEVEL", "INFO"),
    )
    settings.validate()
    return settings


@lru_cache
def get_settings() -> Settings:
    return load_settings()
