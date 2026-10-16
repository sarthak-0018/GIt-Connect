import time

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from .config import settings
from .errors import APIError
from .service import GitHubRateLimitError, _analysis_is_active, _fresh_cached_analysis, _redis_client, analyze_repository, github_diagnostics, parse_github_url

router = APIRouter()
_local_limits: dict[str, list[int]] = {}


async def _check_rate_limit(request: Request) -> int | None:
    ip = request.client.host if request.client else "unknown"
    now = int(time.time())
    window = max(settings.analyze_rate_limit_window, 1)
    key = f"contribcompass:ip-limit:{ip}:{now // window}"
    client = None
    try:
        client = await _redis_client()
        if client:
            count = await client.incr(key)
            if count == 1:
                await client.expire(key, window)
            ttl = await client.ttl(key)
            return max(ttl, 1) if count > settings.analyze_rate_limit_per_ip else None
    except Exception:
        pass
    finally:
        if client:
            await client.aclose()
    entries = [stamp for stamp in _local_limits.get(ip, []) if stamp > now - window]
    if len(entries) >= settings.analyze_rate_limit_per_ip:
        _local_limits[ip] = entries
        return max(entries[0] + window - now, 1)
    entries.append(now)
    _local_limits[ip] = entries
    return None


class AnalyzeRequest(BaseModel):
    url: str = Field(min_length=1, max_length=500)


@router.get("/health")
async def health():
    return {"status": "ok", "service": "contribcompass-api"}


@router.get("/health/github")
async def github_health():
    return await github_diagnostics()


@router.post("/analyze")
async def analyze(payload: AnalyzeRequest, request: Request):
    try:
        owner, repo = parse_github_url(payload.url)
        request.state.repository = f"{owner}/{repo}"
        cached = await _fresh_cached_analysis(owner, repo)
        if cached:
            return cached
        if not await _analysis_is_active(owner, repo):
            retry_after = await _check_rate_limit(request)
            if retry_after:
                raise APIError(429, "APPLICATION_RATE_LIMIT", "Too many repository analyses were requested. Please try again later.", retryable=True, retry_at=(datetime.now(timezone.utc) + timedelta(seconds=retry_after)).isoformat())
        return await analyze_repository(owner, repo)
    except ValueError as exc:
        raise APIError(422, "INVALID_GITHUB_URL", "Enter a valid public GitHub repository URL in the form https://github.com/owner/repository.") from exc
    except LookupError as exc:
        raise APIError(404, "REPOSITORY_NOT_FOUND", "Repository not found. Check that the URL is correct and the repository is public.") from exc
    except GitHubRateLimitError as exc:
        from .errors import rate_limit_api_error
        raise rate_limit_api_error(exc, secondary=exc.secondary) from exc
    except PermissionError as exc:
        raise APIError(502, "GITHUB_UNAVAILABLE", "GitHub could not authorize this repository request.", retryable=True) from exc
    except TimeoutError as exc:
        raise APIError(504, "ANALYSIS_TIMEOUT", "Repository analysis took too long. Please try again.", retryable=True) from exc
    except ConnectionError as exc:
        raise APIError(503, "GITHUB_UNAVAILABLE", "GitHub is temporarily unavailable. Please try again shortly.", retryable=True) from exc
