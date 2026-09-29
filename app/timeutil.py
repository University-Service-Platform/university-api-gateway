from datetime import datetime, timezone


def utc_timestamp() -> str:
    """ISO-8601 UTC with milliseconds, e.g. 2026-09-30T10:15:30.123Z (same format as the services)."""
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
