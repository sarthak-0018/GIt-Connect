import asyncio

from app import service
from app.errors import APIError


def test_analysis_cache_wrapper_is_not_shadowed_by_uncached_implementation():
    assert service.analyze_repository.__name__ == "analyze_repository"
    assert service._analyze_repository_uncached.__name__ == "_analyze_repository_uncached"


def test_github_token_is_only_added_to_server_request_headers(monkeypatch):
    monkeypatch.setattr(service.settings, "github_token", "test-secret")
    assert service._github_headers()["Authorization"] == "Bearer test-secret"
    monkeypatch.setattr(service.settings, "github_token", None)
    assert "Authorization" not in service._github_headers()


def test_primary_rate_limit_includes_reset_and_token_guidance(monkeypatch):
    monkeypatch.setattr(service.settings, "github_token", None)
    error = service._rate_limit_error(403, {"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1999999999"})
    assert not error.secondary
    assert error.reset_at == 1999999999
    assert "GITHUB_TOKEN" in str(error)


def test_secondary_rate_limit_respects_retry_after():
    error = service._rate_limit_error(429, {"retry-after": "17"})
    assert error.secondary
    assert error.retry_after == 17


def test_rejected_github_token_clears_stale_quota_diagnostics(monkeypatch):
    async def no_cache(*args, **kwargs):
        return None

    monkeypatch.setattr(service, "_write_redis_cache", no_cache)
    monkeypatch.setitem(service._github_diagnostics, "github_authenticated", True)
    monkeypatch.setitem(service._github_diagnostics, "github_rate_limit_limit", 5000)
    monkeypatch.setitem(service._github_diagnostics, "github_rate_limit_remaining", 4900)
    asyncio.run(service._record_github_response(401, {}))
    assert service._github_diagnostics["github_authenticated"] is False
    assert service._github_diagnostics["github_rate_limit_limit"] is None
    assert service._github_diagnostics["github_rate_limit_remaining"] is None


def test_cached_analysis_is_served_without_new_github_work(monkeypatch):
    async def scenario():
        async def no_redis_cache(owner, repo):
            return None

        async def unexpected_analysis(owner, repo):
            raise AssertionError("a fresh cached analysis should be reused")

        monkeypatch.setattr(service, "_fresh_cached_analysis", no_redis_cache)
        monkeypatch.setattr(service, "_analyze_repository_uncached", unexpected_analysis)
        service._local_analysis_results["test-owner/test-repo"] = (service.time.monotonic(), {"analysis": {"cache_hit": False, "analyzer_version": service.ANALYZER_VERSION}})
        result = await service.analyze_repository("test-owner", "test-repo")
        assert result["analysis"]["cache_hit"] is True
        assert result["analysis"]["cache_status"] == "fresh-local"
        service._local_analysis_results.pop("test-owner/test-repo", None)

    asyncio.run(scenario())


def test_same_repository_concurrent_requests_share_one_analysis(monkeypatch):
    async def scenario():
        calls = 0

        async def no_redis_cache(owner, repo):
            return None

        async def fake_analysis(owner, repo):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.05)
            return {"analysis": {"cache_hit": False}, "repository": {"full_name": f"{owner}/{repo}"}}

        monkeypatch.setattr(service, "_fresh_cached_analysis", no_redis_cache)
        monkeypatch.setattr(service, "_analyze_repository_uncached", fake_analysis)
        service._local_analysis_results.clear()
        service._local_analysis_locks.clear()
        left, right = await asyncio.gather(service.analyze_repository("test-owner", "test-repo"), service.analyze_repository("test-owner", "test-repo"))
        assert calls == 1
        assert left["repository"] == right["repository"]

    asyncio.run(scenario())


def test_failed_inflight_analysis_is_not_repeated_for_waiting_request(monkeypatch):
    async def scenario():
        calls = 0

        async def no_cache(owner, repo):
            return None

        async def failed_analysis(owner, repo):
            nonlocal calls
            calls += 1
            await asyncio.sleep(0.05)
            raise APIError(503, "LLM_UNAVAILABLE", "Model unavailable", retryable=True)

        monkeypatch.setattr(service, "_fresh_cached_analysis", no_cache)
        monkeypatch.setattr(service, "_analyze_repository_uncached", failed_analysis)
        service._local_analysis_results.clear()
        service._local_analysis_locks.clear()
        first = asyncio.create_task(service.analyze_repository("test-owner", "failed-repo"))
        await asyncio.sleep(0)
        second = asyncio.create_task(service.analyze_repository("test-owner", "failed-repo"))
        outcomes = await asyncio.gather(first, second, return_exceptions=True)
        assert calls == 1
        assert isinstance(outcomes[0], APIError)
        assert isinstance(outcomes[1], APIError)

    asyncio.run(scenario())
