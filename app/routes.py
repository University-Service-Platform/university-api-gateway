"""
The routing table: which service answers which path, and how the path is rewritten.

The shared frontend calls everything under one base path, /api/v1 (VITE_API_BASE_URL).
Some services serve other paths (Group 6 facility and Group 7 serve /api/...), so the
gateway rewrites /api/v1/<resource> to what each service actually serves. The unversioned
/api/<resource> paths used by the Group 6 and Group 7 frontends are accepted too.

Routes are tried in order and the first match wins, so more specific routes come first.
"""
import re
from dataclasses import dataclass
from typing import FrozenSet, List, Optional, Pattern


@dataclass(frozen=True)
class Route:
    service: str
    prefix: str                               # incoming path prefix, matched at a "/" boundary
    upstream_prefix: Optional[str] = None     # replaces `prefix` in the forwarded path; None keeps it
    pattern: Optional[Pattern[str]] = None    # full-path regex, used instead of the prefix match
    public: bool = False                      # no token needed
    methods: Optional[FrozenSet[str]] = None  # restrict the route (used for public routes)

    def matches(self, path: str, method: str) -> bool:
        if self.methods is not None and method not in self.methods:
            return False
        if self.pattern is not None:
            return self.pattern.fullmatch(path) is not None
        return path == self.prefix or path.startswith(self.prefix + "/")

    def upstream_path(self, path: str) -> str:
        if self.upstream_prefix is None:
            return path
        return self.upstream_prefix + path[len(self.prefix):]


def _same(service: str, *prefixes: str) -> List[Route]:
    """Paths the service serves exactly as the frontend calls them."""
    return [Route(service, prefix) for prefix in prefixes]


def _versioned(service: str, *resources: str) -> List[Route]:
    """/api/v1/<resource> -> /api/<resource>, plus /api/<resource> unchanged."""
    routes = []
    for resource in resources:
        routes.append(Route(service, f"/api/v1/{resource}", upstream_prefix=f"/api/{resource}"))
        routes.append(Route(service, f"/api/{resource}"))
    return routes


ROUTES: List[Route] = [
    # ---- Identity: public endpoints
    Route("identity", "/api/v1/auth/login", public=True, methods=frozenset({"POST"})),
    Route("identity", "/.well-known/jwks.json", public=True, methods=frozenset({"GET", "HEAD"})),

    # ---- Directory: user checks under the path Identity also uses. Must come before Identity's
    # /api/v1/validation/users rule, or these would reach the wrong service.
    Route("directory", "/api/v1/validation/users",
          pattern=re.compile(r"/api/v1/validation/users/[^/]+/(affiliation|responsibilities)")),

    # ---- Identity (Group 5)
    *_same("identity", "/api/v1/auth", "/api/v1/users", "/api/v1/roles", "/api/v1/audit-logs",
           "/api/v1/validation/users"),

    # ---- Directory (Group 5)
    *_same("directory", "/api/v1/validation/faculties", "/api/v1/validation/departments",
           "/api/v1/validation/service-units", "/api/v1/faculties", "/api/v1/departments",
           "/api/v1/service-units", "/api/v1/affiliations", "/api/v1/responsibilities"),

    # ---- Group 6: facility-resource-service serves /api/...
    *_versioned("facility", "resources", "facilities", "availability-rules"),

    # ---- Group 6: reservation-service serves /api/v1/reservations; its frontend calls /api/reservations
    Route("reservation", "/api/v1/reservations"),
    Route("reservation", "/api/reservations", upstream_prefix="/api/v1/reservations"),

    # ---- Group 7: service-request-service and work-order-service serve /api/...
    *_versioned("service-request", "service-requests"),
    *_versioned("work-order", "work-orders"),

    # ---- Group 8: event-service serves /api/v1/...; communication-feedback-service serves /api/...
    # (while a URL is missing its routes return 404, and the frontend shows demo data)
    *_same("event", "/api/v1/events", "/api/v1/registrations"),
    *_versioned("communication", "announcements", "notifications", "feedback", "engagement-dashboard"),
]


def resolve(path: str, method: str, routes: Optional[List[Route]] = None) -> Optional[Route]:
    for route in routes if routes is not None else ROUTES:
        if route.matches(path, method):
            return route
    return None
