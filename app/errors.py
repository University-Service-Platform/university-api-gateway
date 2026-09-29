"""The platform's error envelope, shared with the Identity and Directory services."""
from typing import Dict, Optional

from fastapi.responses import JSONResponse

from app.timeutil import utc_timestamp


class GatewayError(Exception):
    def __init__(self, status_code: int, code: str, message: str, headers: Optional[Dict[str, str]] = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.headers = headers or {}


def error_response(status_code: int, code: str, message: str,
                   headers: Optional[Dict[str, str]] = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"success": False, "error": {"code": code, "message": message}, "timestamp": utc_timestamp()},
        headers=headers,
    )
