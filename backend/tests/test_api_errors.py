import pytest
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import routes
from app.errors import APIError
from app.main import app
from app.service import GitHubRateLimitError


client = TestClient(app, raise_server_exceptions=False)


def test_rate_limit_uses_reverse_proxy_client_ip():
    request = Request({
        "type": "http", "method": "POST", "path": "/api/analyze",
        "headers": [(b"x-real-ip", b"203.0.113.42")],
        "client": ("172.19.0.5", 12345), "server": ("backend", 8000),
        "scheme": "http", "query_string": b"",
    })
    assert routes._request_ip(request) == "203.0.113.42"


def test_rate_limit_falls_back_to_direct_client_ip():
    request = Request({
        "type": "http", "method": "POST", "path": "/api/analyze",
        "headers": [], "client": ("127.0.0.1", 12345),
        "server": ("backend", 8000), "scheme": "http", "query_string": b"",
    })
    assert routes._request_ip(request) == "127.0.0.1"


def test_invalid_repository_url_returns_consistent_json():
    response = client.post("/api/analyze", json={"url": "not-a-github-url"})
    assert response.status_code == 422
    assert response.headers["content-type"].startswith("application/json")
    assert response.json()["error"]["code"] == "INVALID_GITHUB_URL"
    assert response.json()["error"]["request_id"]


def test_unknown_api_route_returns_error_schema():
    response = client.get("/api/not-a-route")
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


@pytest.mark.parametrize("code", [
    "REPOSITORY_TOO_LARGE", "GITHUB_UNAVAILABLE", "GITHUB_AUTH_INVALID", "GITHUB_TIMEOUT", "ANALYSIS_TIMEOUT",
    "LLM_UNAVAILABLE", "LLM_TIMEOUT", "LLM_INVALID_RESPONSE", "INTERNAL_SERVER_ERROR",
])
def test_api_errors_always_have_consistent_json_schema(monkeypatch, code):
    async def no_cache(*args):
        return None

    async def inactive(*args):
        return False

    async def no_limit(*args):
        return None

    async def raise_error(*args):
        status = 500 if code == "INTERNAL_SERVER_ERROR" else 401 if code == "GITHUB_AUTH_INVALID" else 503
        raise APIError(status, code, "Safe test message", retryable=code != "GITHUB_AUTH_INVALID")

    monkeypatch.setattr(routes, "_fresh_cached_analysis", no_cache)
    monkeypatch.setattr(routes, "_analysis_is_active", inactive)
    monkeypatch.setattr(routes, "_check_rate_limit", no_limit)
    monkeypatch.setattr(routes, "analyze_repository", raise_error)
    response = client.post("/api/analyze", json={"url": "https://github.com/owner/repo"})
    assert response.headers["content-type"].startswith("application/json")
    payload = response.json()["error"]
    assert payload["code"] == code
    assert payload["message"] == "Safe test message"
    assert payload["retryable"] is (code != "GITHUB_AUTH_INVALID")
    assert payload["request_id"] == response.headers["x-request-id"]


@pytest.mark.parametrize("headers, secondary, code", [
    ({"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1999999999"}, False, "GITHUB_RATE_LIMIT"),
    ({"retry-after": "15"}, True, "GITHUB_SECONDARY_RATE_LIMIT"),
])
def test_github_rate_limits_return_retryable_json(monkeypatch, headers, secondary, code):
    async def no_cache(*args):
        return None

    async def inactive(*args):
        return False

    async def no_limit(*args):
        return None

    async def rate_limited(*args):
        raise GitHubRateLimitError("internal GitHub response text", retry_after=15, reset_at=1999999999, secondary=secondary)

    monkeypatch.setattr(routes, "_fresh_cached_analysis", no_cache)
    monkeypatch.setattr(routes, "_analysis_is_active", inactive)
    monkeypatch.setattr(routes, "_check_rate_limit", no_limit)
    monkeypatch.setattr(routes, "analyze_repository", rate_limited)
    response = client.post("/api/analyze", json={"url": "https://github.com/owner/repo"})
    assert response.status_code == 429
    error = response.json()["error"]
    assert error["code"] == code
    assert error["retryable"] is True
    assert error["retry_at"]
    assert "internal GitHub" not in error["message"]


def test_unexpected_exception_logs_full_error_and_returns_generic_json(monkeypatch, caplog):
    async def no_cache(*args):
        return None

    async def inactive(*args):
        return False

    async def no_limit(*args):
        return None

    async def broken(*args):
        raise RuntimeError("diagnostic-only traceback message")

    monkeypatch.setattr(routes, "_fresh_cached_analysis", no_cache)
    monkeypatch.setattr(routes, "_analysis_is_active", inactive)
    monkeypatch.setattr(routes, "_check_rate_limit", no_limit)
    monkeypatch.setattr(routes, "analyze_repository", broken)
    response = client.post("/api/analyze", json={"url": "https://github.com/owner/repo"})
    assert response.status_code == 500
    assert response.json()["error"]["code"] == "INTERNAL_SERVER_ERROR"
    assert "diagnostic-only traceback message" not in response.text
    assert "diagnostic-only traceback message" in caplog.text
