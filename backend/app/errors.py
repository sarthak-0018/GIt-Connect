from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class APIError(Exception):
    def __init__(self, status_code: int, code: str, message: str, retryable: bool = False, retry_at: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.retryable = retryable
        self.retry_at = retry_at


def rate_limit_api_error(exc: Any, secondary: bool = False) -> APIError:
    reset_at = getattr(exc, "reset_at", None)
    retry_after = getattr(exc, "retry_after", None)
    retry_at = datetime.fromtimestamp(reset_at, timezone.utc).isoformat() if reset_at else datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + retry_after, timezone.utc).isoformat() if retry_after else None
    return APIError(
        429,
        "GITHUB_SECONDARY_RATE_LIMIT" if secondary else "GITHUB_RATE_LIMIT",
        "GitHub API rate limit reached." if not secondary else "GitHub temporarily limited requests. Please try again later.",
        retryable=True,
        retry_at=retry_at,
    )
