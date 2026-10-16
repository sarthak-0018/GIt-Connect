"""GitHub evidence collection, deterministic analysis, and constrained guide generation."""
import asyncio
import hashlib
import copy
import json
import logging
import re
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlparse

import httpx
from pydantic import BaseModel, ConfigDict

from .analyzer import analyze_profile, file_priority, is_sensitive_path, repository_fingerprint, select_important_paths
from .config import settings
from .errors import APIError

SECRET_LINE = re.compile(r"(?im)^(\s*(?:[A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|PASSWD|API_KEY|PRIVATE_KEY|CLIENT_SECRET)[A-Z0-9_]*|AWS_ACCESS_KEY_ID|AWS_SECRET_ACCESS_KEY)\s*[:=]\s*).+$")
SECRET_VALUE = re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9_]{20,}|github_pat_[A-Za-z0-9_]{20,}|AKIA[0-9A-Z]{16})\b")
URL_CREDENTIALS = re.compile(r"(://[^:/@\s]+:)[^@/\s]+(@)")
ANALYZER_VERSION = "4"
logger = logging.getLogger("contribcompass.analyzer")
_local_analysis_locks: dict[str, asyncio.Lock] = {}
_local_analysis_results: dict[str, tuple[float, dict]] = {}
_analysis_semaphore = asyncio.Semaphore(max(settings.max_concurrent_analyses, 1))
_github_diagnostics = {
    "github_authenticated": None,
    "github_rate_limit_limit": None,
    "github_rate_limit_remaining": None,
    "github_rate_limit_reset": None,
    "github_retry_after": None,
    "requests_made": 0,
    "cache_hits": 0,
    "cache_misses": 0,
    "repository_commit_sha": None,
    "analysis_duration_seconds": None,
}


def _mark_cache_hit(value: dict, status: str, requests_made: int = 0) -> dict:
    result = copy.deepcopy(value)
    analysis = result.setdefault("analysis", {})
    analysis.update({"cache_hit": True, "cache_status": status, "requests_made": requests_made})
    analysis["cache_hits"] = _github_diagnostics["cache_hits"] + 1
    analysis["cache_misses"] = _github_diagnostics["cache_misses"]
    _github_diagnostics["cache_hits"] += 1
    return result


async def _record_github_response(status_code: int, headers) -> None:
    limit = headers.get("x-ratelimit-limit")
    remaining = headers.get("x-ratelimit-remaining")
    reset = headers.get("x-ratelimit-reset")
    retry_after = headers.get("retry-after")
    if limit and limit.isdigit():
        _github_diagnostics["github_rate_limit_limit"] = int(limit)
    if remaining and remaining.isdigit():
        _github_diagnostics["github_rate_limit_remaining"] = int(remaining)
    if reset and reset.isdigit():
        _github_diagnostics["github_rate_limit_reset"] = int(reset)
    if retry_after and retry_after.isdigit():
        _github_diagnostics["github_retry_after"] = int(retry_after)
    if status_code == 401:
        _github_diagnostics["github_authenticated"] = False
    elif status_code < 400 and settings.github_token:
        _github_diagnostics["github_authenticated"] = True
    elif status_code < 400 and _github_diagnostics["github_authenticated"] is None:
        _github_diagnostics["github_authenticated"] = False
    state = {key: value for key, value in _github_diagnostics.items() if key.startswith("github_")}
    await _write_redis_cache("contribcompass:github-rate-state", state, 3600)


async def github_diagnostics() -> dict:
    """Verify that a configured token is accepted without returning credential data."""
    checked = {"github_authenticated": False, "github_rate_limit_limit": None, "github_rate_limit_remaining": None, "github_rate_limit_reset": None, "github_retry_after": None, "diagnostic_request_count": 1}
    try:
        async with httpx.AsyncClient(base_url=settings.github_api_url, headers=_github_headers(), timeout=settings.github_timeout_seconds, follow_redirects=False) as client:
            response = await client.get("/rate_limit")
        await _record_github_response(response.status_code, response.headers)
        if response.status_code < 400:
            core = response.json().get("resources", {}).get("core", {})
            checked.update({
                "github_authenticated": bool(settings.github_token),
                "github_rate_limit_limit": core.get("limit"),
                "github_rate_limit_remaining": core.get("remaining"),
                "github_rate_limit_reset": core.get("reset"),
            })
    except httpx.TimeoutException:
        checked["diagnostic_error"] = "GitHub diagnostic request timed out."
    except httpx.HTTPError:
        checked["diagnostic_error"] = "GitHub diagnostic request failed."
    except (ValueError, KeyError, TypeError):
        checked["diagnostic_error"] = "GitHub returned an unreadable rate-limit response."
    for key in ("github_rate_limit_limit", "github_rate_limit_remaining", "github_rate_limit_reset", "github_retry_after"):
        if checked.get(key) is None:
            checked[key] = _github_diagnostics.get(key)
    _github_diagnostics.update({k: v for k, v in checked.items() if k != "diagnostic_request_count"})
    redis_available = False
    redis_client = None
    try:
        redis_client = await _redis_client()
        if redis_client:
            redis_available = bool(await redis_client.ping())
    except Exception:
        redis_available = False
    finally:
        if redis_client:
            await redis_client.aclose()
    return {**_github_diagnostics, "redis_configured": bool(settings.redis_url), "redis_cache_enabled": redis_available, "diagnostic_request_count": 1}


class GitHubRateLimitError(PermissionError):
    def __init__(self, message: str, retry_after: int | None = None, reset_at: int | None = None, secondary: bool = False):
        super().__init__(message)
        self.retry_after = retry_after
        self.reset_at = reset_at
        self.secondary = secondary


def _rate_limit_error(status_code: int, headers: dict[str, str]) -> GitHubRateLimitError:
    remaining = headers.get("x-ratelimit-remaining")
    reset = headers.get("x-ratelimit-reset")
    retry_header = headers.get("retry-after")
    retry_after = int(retry_header) if retry_header and retry_header.isdigit() else None
    reset_at = int(reset) if reset and reset.isdigit() else None
    if remaining == "0" and status_code == 403:
        wait = max((reset_at or int(datetime.now(timezone.utc).timestamp()) + 60) - int(datetime.now(timezone.utc).timestamp()), 1)
        auth_hint = " An authenticated GITHUB_TOKEN is not configured." if not settings.github_token else ""
        return GitHubRateLimitError(f"GitHub's API quota is exhausted.{auth_hint} Try again after the reset time.", retry_after=wait, reset_at=reset_at)
    wait = retry_after or 60
    return GitHubRateLimitError("GitHub temporarily limited requests. Try again after the indicated wait.", retry_after=wait, reset_at=reset_at, secondary=True)


def _repo_key(owner: str, repo: str) -> str:
    return f"{owner.lower()}/{repo.lower()}"


def _github_headers() -> dict[str, str]:
    headers = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "ContribCompass"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    return headers


async def _redis_client():
    if not settings.redis_url:
        return None
    from redis.asyncio import Redis
    return Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=0.3, socket_timeout=0.3)


async def _fresh_cached_analysis(owner: str, repo: str) -> dict | None:
    client = None
    try:
        client = await _redis_client()
        if not client:
            return None
        key = "contribcompass:latest:" + _repo_key(owner, repo)
        raw = await client.get(key)
        if not raw:
            return None
        pointer = json.loads(raw)
        if datetime.now(timezone.utc).timestamp() - pointer["stored_at"] > settings.analysis_cache_fresh_seconds:
            return None
        result = await client.get(pointer["result_key"])
        if result:
            return _mark_cache_hit(json.loads(result), "fresh")
    except Exception:
        return None
    finally:
        if client:
            await client.aclose()
    return None


async def _analysis_is_active(owner: str, repo: str) -> bool:
    client = None
    try:
        client = await _redis_client()
        if client:
            return bool(await client.exists("contribcompass:lock:" + _repo_key(owner, repo)))
    except Exception:
        pass
    finally:
        if client:
            await client.aclose()
    lock = _local_analysis_locks.get(_repo_key(owner, repo))
    return bool(lock and lock.locked())


async def analyze_repository(owner: str, repo: str) -> dict:
    """Cache-first repository analysis with cross-worker Redis single-flight."""
    cached = await _fresh_cached_analysis(owner, repo)
    if cached:
        logger.info("repository_analysis_cache_hit", extra={"repository": _repo_key(owner, repo), "cache_status": "fresh"})
        return cached
    local_cached = _local_analysis_results.get(_repo_key(owner, repo))
    if local_cached and time.monotonic() - local_cached[0] <= settings.analysis_cache_fresh_seconds:
        return _mark_cache_hit(local_cached[1], "fresh-local")

    lock_key = "contribcompass:lock:" + _repo_key(owner, repo)
    client = None
    lock_token = hashlib.sha256(f"{asyncio.current_task()}:{datetime.now(timezone.utc).timestamp()}".encode()).hexdigest()
    acquired = False
    analysis_started = time.monotonic()
    try:
        try:
            client = await _redis_client()
            if client:
                acquired = bool(await client.set(lock_key, lock_token, nx=True, ex=settings.max_analysis_time + 30))
        except Exception:
            if client:
                await client.aclose()
            client = None
            acquired = False
        if client:
            if not acquired:
                deadline = asyncio.get_running_loop().time() + settings.max_analysis_time
                while asyncio.get_running_loop().time() < deadline:
                    await asyncio.sleep(0.25)
                    cached = await _fresh_cached_analysis(owner, repo)
                    if cached:
                        cached["analysis"]["cache_status"] = "deduplicated"
                        return cached
                    if not await client.exists(lock_key):
                        raise APIError(409, "REPOSITORY_ANALYSIS_IN_PROGRESS", "The other repository analysis did not produce a cached result. Please retry shortly.", retryable=True, retry_at=(datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat())
                if not acquired:
                    raise APIError(409, "REPOSITORY_ANALYSIS_IN_PROGRESS", "Repository analysis is already in progress. Please retry shortly.", retryable=True, retry_at=(datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat())
        else:
            lock = _local_analysis_locks.setdefault(_repo_key(owner, repo), asyncio.Lock())
            waiting_for_existing = lock.locked()
            try:
                await asyncio.wait_for(lock.acquire(), timeout=settings.max_analysis_time)
                if waiting_for_existing:
                    lock.release()
                    cached = await _fresh_cached_analysis(owner, repo)
                    if cached:
                        cached["analysis"]["cache_status"] = "deduplicated"
                        return cached
                    local_cached = _local_analysis_results.get(_repo_key(owner, repo))
                    if local_cached and time.monotonic() - local_cached[0] <= settings.analysis_cache_fresh_seconds:
                        return _mark_cache_hit(local_cached[1], "deduplicated-local")
                    raise APIError(409, "REPOSITORY_ANALYSIS_IN_PROGRESS", "The other repository analysis did not produce a cached result. Please retry shortly.", retryable=True, retry_at=(datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat())
                acquired = True
            except asyncio.TimeoutError as exc:
                raise APIError(409, "REPOSITORY_ANALYSIS_IN_PROGRESS", "Repository analysis is already in progress. Please retry shortly.", retryable=True, retry_at=(datetime.now(timezone.utc) + timedelta(seconds=2)).isoformat()) from exc
        cached = await _fresh_cached_analysis(owner, repo)
        if cached:
            return cached
        local_cached = _local_analysis_results.get(_repo_key(owner, repo))
        if local_cached and time.monotonic() - local_cached[0] <= settings.analysis_cache_fresh_seconds:
            return _mark_cache_hit(local_cached[1], "deduplicated-local")
        async with _analysis_semaphore:
            try:
                async with asyncio.timeout(settings.max_analysis_time):
                    result = await _analyze_repository_uncached(owner, repo)
                    _local_analysis_results[_repo_key(owner, repo)] = (time.monotonic(), copy.deepcopy(result))
                    return result
            finally:
                _github_diagnostics["analysis_duration_seconds"] = round(time.monotonic() - analysis_started, 3)
    except TimeoutError as exc:
        raise TimeoutError("Repository analysis exceeded its time limit. Please try again.") from exc
    finally:
        if client:
            if acquired:
                try:
                    await client.eval("if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('del', KEYS[1]) else return 0 end", 1, lock_key, lock_token)
                except Exception:
                    pass
            await client.aclose()
        elif acquired:
            lock = _local_analysis_locks.get(_repo_key(owner, repo))
            if lock and lock.locked():
                lock.release()


class ClaimEvidence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim: str
    evidence_ids: list[str]


class ModelGuide(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project_summary: str
    technology_stack: list[str]
    prerequisites: list[str]
    setup_steps: list[str]
    run_instructions: list[str]
    build_commands: list[str]
    test_commands: list[str]
    important_directories: list[str]
    entry_points: list[dict]
    where_to_start: str
    contribution_workflow: list[str]
    warnings: list[str]
    evidence: list[ClaimEvidence]


def parse_github_url(value: str) -> tuple[str, str]:
    parsed = urlparse(value.strip())
    if parsed.scheme != "https" or parsed.hostname != "github.com" or parsed.port or parsed.username:
        raise ValueError("Enter a repository URL in the form https://github.com/owner/repository")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) != 2 or any(part in {".", ".."} for part in parts):
        raise ValueError("Enter a repository URL with exactly an owner and repository name")
    owner, repo = parts
    if repo.endswith(".git"):
        repo = repo[:-4]
    if not owner or not repo:
        raise ValueError("Both owner and repository are required")
    return owner, repo


def rank_issue(issue: dict) -> dict:
    safe_body = sanitize_content(issue.get("body") or "", "issue-description.txt")
    labels = [x.get("name", "") for x in issue.get("labels", []) if isinstance(x, dict)]
    normalized = {x.lower().replace("-", " ").replace("_", " ") for x in labels}
    text = f"{issue.get('title', '')} {safe_body}".lower()
    score, reasons = 0, []
    for label, points in (("good first issue", 35), ("help wanted", 25), ("first timers only", 20), ("beginner", 18), ("easy", 12), ("documentation", 15), ("docs", 12)):
        if label in normalized:
            score += points
            reasons.append(f"{label} label")
    if len(safe_body) >= 120:
        score += 12
        reasons.append("description provides useful detail")
    if issue.get("comments", 0) <= 2:
        score += 5
        reasons.append("few existing comments")
    if any(term in text for term in ("rewrite", "migration", "architecture", "refactor entire", "redesign the whole")):
        score -= 25
        reasons.append("may involve broad or complex changes")
    if any(term in text for term in ("blocked by", "depends on #", "waiting on #")):
        score -= 15
        reasons.append("description indicates a dependency on other work")
    return {"number": issue["number"], "title": sanitize_content(issue.get("title", "Untitled issue"), "issue-description.txt")[:500], "body": safe_body[:1200], "url": issue["html_url"], "labels": labels, "score": max(score, 0), "reasons": reasons or ["open issue; suitability is uncertain"], "comments": issue.get("comments", 0)}


def sanitize_content(text: str, path: str) -> str:
    if is_sensitive_path(path):
        return ""
    text = SECRET_LINE.sub(r"\1[REDACTED]", text)
    text = URL_CREDENTIALS.sub(r"\1[REDACTED]\2", text)
    text = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]{12,}", "Bearer [REDACTED_TOKEN]", text)
    return SECRET_VALUE.sub("[REDACTED_TOKEN]", text)


def _select_content_for_context(profile: dict, files: dict[str, str], owner: str, repo: str, branch: str, max_chars: int) -> list[dict]:
    readme_path = profile["readme_analysis"].get("path")
    contributing_path = profile["contributing_analysis"].get("path")
    ranked = sorted(files, key=lambda p: (-file_priority(p), p.lower()))
    if readme_path in ranked:
        ranked.remove(readme_path)
        ranked.insert(0, readme_path)
    if contributing_path in ranked:
        ranked.remove(contributing_path)
        ranked.insert(1 if readme_path else 0, contributing_path)
    items, used = [], 0
    for path in ranked:
        content = sanitize_content(files[path], path)
        if not content.strip():
            continue
        per_file_limit = 9000 if path == readme_path else 7000 if path == contributing_path else 4000 if file_priority(path) >= 800 else 2200
        content = content[:per_file_limit]
        remaining = max_chars - used
        if remaining < 300:
            break
        content = content[:remaining]
        evidence_id = f"E{len(items) + 1}"
        items.append({"id": evidence_id, "path": path, "url": f"https://github.com/{owner}/{repo}/blob/{quote(branch, safe='/')}/{quote(path, safe='/')}", "content": content})
        used += len(content)
    return items


def _context_profile(profile: dict) -> dict:
    """Drop verbose derived excerpts so actual source evidence receives the context budget."""
    compact = copy.deepcopy(profile)
    compact.pop("issues", None)
    compact.pop("ranked_beginner_issues", None)
    readme = compact["readme_analysis"]
    readme.pop("text_excerpt", None)
    for key in ("installation", "development_setup", "prerequisites", "run_commands", "build_commands", "test_commands", "architecture", "workflow", "contribution", "target_users"):
        readme[key] = [str(x)[:450] for x in readme.get(key, [])[:2]]
    readme["command_blocks"] = [str(x)[:600] for x in readme.get("command_blocks", [])[:5]]
    contributing = compact["contributing_analysis"]
    contributing.pop("text_excerpt", None)
    for key in ("setup", "workflow", "branches", "commits", "tests", "lint_format", "pull_requests", "review", "issue_guidance"):
        contributing[key] = [str(x)[:550] for x in contributing.get(key, [])[:2]]
    compact["important_files"] = compact["important_files"][:24]
    compact["test_structure"] = compact["test_structure"][:20]
    compact["documentation_structure"] = compact["documentation_structure"][:20]
    compact["important_directories"] = compact["important_directories"][:24]
    compact["docker_configuration"] = {p: text[:1600] for p, text in list(compact["docker_configuration"].items())[:3]}
    compact["ci_configuration"] = {p: text[:800] for p, text in list(compact["ci_configuration"].items())[:3]}
    compact["dependencies"] = {p: items[:40] for p, items in compact["dependencies"].items()}
    compact["scripts"] = {k: str(v)[:240] for k, v in list(compact["scripts"].items())[:30]}

    def sanitize_tree(value):
        if isinstance(value, dict):
            return {k: sanitize_tree(v) for k, v in value.items()}
        if isinstance(value, list):
            return [sanitize_tree(v) for v in value]
        if isinstance(value, str):
            return sanitize_content(value, "repository-evidence.txt")
        return value
    return sanitize_tree(compact)


def _commands_for_scripts(profile: dict) -> tuple[list[str], list[str], list[str]]:
    scripts = profile["scripts"]
    manager = profile.get("package_manager") or "npm"
    if manager in {"pnpm", "yarn", "bun"}:
        run = lambda name: f"{manager} {name}" if manager == "yarn" else f"{manager} run {name}"
    else:
        run = lambda name: f"npm run {name}"
    run_names = [name for name in ("dev", "start", "serve") if name in scripts]
    run_cmds = [run(name) for name in run_names]
    build_cmds = [run(name) for name in ("build",) if name in scripts]
    test_cmds = [run(name) for name in ("test", "test:unit", "test:ci") if name in scripts]
    if profile["package_manager"] in {"cargo", "Go modules", "Maven", "Gradle"}:
        has_rust_main = any(x["path"].endswith(("main.rs",)) for x in profile["entry_points"])
        go_main = next((x["path"] for x in profile["entry_points"] if x["path"].endswith("main.go")), None)
        native_run = ["cargo run"] if profile["package_manager"] == "cargo" and has_rust_main else [f"go run ./{go_main.rsplit('/', 1)[0]}" if "/" in go_main else "go run ."] if profile["package_manager"] == "Go modules" and go_main else []
        run_cmds = run_cmds or native_run
    return run_cmds, build_cmds, test_cmds


def _fit_context(context: dict, maximum: int) -> str:
    """Trim optional evidence while keeping the serialized context valid JSON and under budget."""
    def encode() -> str:
        return json.dumps(context, ensure_ascii=False, separators=(",", ":"))
    while len(encode()) > maximum and context["evidence"]:
        context["evidence"].pop()
    while len(encode()) > maximum and context["ranked_issues"]:
        context["ranked_issues"].pop()
    profile = context["repository_profile"]
    for group in ("readme_analysis", "contributing_analysis"):
        section = profile.get(group, {})
        for key, values in list(section.items()):
            if isinstance(values, list):
                section[key] = [str(x)[:180] for x in values[:1]]
    profile["important_files"] = profile.get("important_files", [])[:12]
    profile["test_structure"] = profile.get("test_structure", [])[:10]
    profile["documentation_structure"] = profile.get("documentation_structure", [])[:10]
    profile["dependencies"] = {k: v[:20] for k, v in profile.get("dependencies", {}).items()}
    profile["scripts"] = {k: str(v)[:120] for k, v in list(profile.get("scripts", {}).items())[:16]}
    profile["docker_configuration"] = {k: v[:500] for k, v in list(profile.get("docker_configuration", {}).items())[:1]}
    profile["ci_configuration"] = {k: v[:500] for k, v in list(profile.get("ci_configuration", {}).items())[:1]}
    while len(encode()) > maximum and context["evidence"]:
        context["evidence"].pop()
    if len(encode()) > maximum:
        readme, contributing = profile.get("readme_analysis", {}), profile.get("contributing_analysis", {})
        context["repository_profile"] = {
            "repository": profile.get("repository", {}),
            "detected_languages": profile.get("detected_languages", [])[:8],
            "frameworks": profile.get("frameworks", [])[:12],
            "runtime": profile.get("runtime", [])[:12],
            "package_manager": profile.get("package_manager"),
            "dependency_manifests": profile.get("dependency_manifests", [])[:16],
            "scripts": dict(list(profile.get("scripts", {}).items())[:16]),
            "important_files": profile.get("important_files", [])[:16],
            "important_directories": profile.get("important_directories", [])[:12],
            "entry_points": profile.get("entry_points", [])[:12],
            "environment_variables": profile.get("environment_variables", {}),
            "readme_analysis": {"status": readme.get("status"), "path": readme.get("path"), "purpose": str(readme.get("purpose") or "")[:500], "sections": readme.get("sections", [])[:20], "command_blocks": readme.get("command_blocks", [])[:4]},
            "contributing_analysis": {"status": contributing.get("status"), "path": contributing.get("path"), "sections": contributing.get("sections", [])[:20]},
            "test_structure": profile.get("test_structure", [])[:16],
            "documentation_structure": profile.get("documentation_structure", [])[:16],
        }
    if len(encode()) > maximum:
        context["ranked_issues"] = []
        context["evidence"] = []
    return encode()


def _deterministic_guide(profile: dict, ranked_issues: list[dict]) -> dict:
    repo = profile["repository"]
    readme = profile["readme_analysis"]
    contributing = profile["contributing_analysis"]
    stack = list(dict.fromkeys(([repo["primary_language"]] if repo.get("primary_language") else []) + profile["detected_languages"] + profile["runtime"] + profile["frameworks"] + ([profile["package_manager"]] if profile.get("package_manager") else [])))
    purpose = readme.get("purpose") or repo.get("description") or "Not found in repository evidence."
    setup = profile["install_commands"] or [x for x in readme.get("command_blocks", []) if re.search(r"install|setup|clone|quick start", x, re.I)][:5]
    run, build, tests = _commands_for_scripts(profile)
    if not run:
        run = [x for x in readme.get("command_blocks", []) if re.search(r"run|start|serve|dev", x, re.I)][:5]
    if not build:
        build = profile["build_commands"] or [x for x in readme.get("command_blocks", []) if re.search(r"build|compile", x, re.I)][:5]
    if not tests:
        tests = profile["test_commands"] or [x for x in readme.get("command_blocks", []) if re.search(r"test|pytest|cargo test|go test", x, re.I)][:5]
    prereq = profile["runtime"] + readme.get("prerequisites", [])
    prereq = list(dict.fromkeys(prereq)) or ["Not found in repository evidence."]
    important_dirs = [x["path"] for x in profile["important_directories"]]
    entry_points = profile["entry_points"]
    where = (f"Inspect `{entry_points[0]['path']}` ({entry_points[0]['basis']})." if entry_points else "No entry point could be identified from manifest metadata or conventional source paths.")
    contrib = []
    for category in ("setup", "workflow", "branches", "commits", "tests", "lint_format", "pull_requests", "review", "issue_guidance"):
        contrib.extend(contributing.get(category, []))
    if not contrib:
        contrib = ["Not found in repository evidence."]
    suitable = [x for x in ranked_issues if x["score"] >= 25][:5]
    issue_note = "No retrieved open issue met the deterministic beginner-suitability threshold." if not suitable else "Issues are ranked from labels, description detail, comment count, and complexity signals."
    return {
        "project_summary": purpose,
        "technology_stack": stack or ["Not found in repository evidence."],
        "prerequisites": prereq,
        "setup_steps": setup or ["Not found in repository evidence."],
        "run_instructions": run or ["Not found in repository evidence."],
        "build_commands": build or ["Not found in repository evidence."],
        "test_commands": tests or ["Not found in repository evidence."],
        "important_directories": important_dirs or ["Not found in repository evidence."],
        "entry_points": entry_points,
        "where_to_start": where,
        "recommended_issues": suitable,
        "first_contribution": {"suggestion": f"Review issue #{suitable[0]['number']} and follow the repository's contribution instructions." if suitable else "No issue was recommended because none of the retrieved open issues showed strong beginner-friendly signals.", "issue_url": suitable[0]["url"] if suitable else None},
        "contribution_workflow": contrib,
        "warnings": (["README not found."] if readme["status"] != "found" else []) + (["CONTRIBUTING not found."] if contributing["status"] != "found" else []) + ([issue_note] if not suitable else []),
    }


async def _analyze_repository_uncached(owner: str, repo: str) -> dict:
    started_at = asyncio.get_running_loop().time()
    headers = _github_headers()
    encoded_owner, encoded_repo = quote(owner, safe=""), quote(repo, safe="")
    async with httpx.AsyncClient(base_url=settings.github_api_url, headers=headers, timeout=settings.github_timeout_seconds, follow_redirects=False) as client:
        requests_made = 0
        rate_status = {"limit": None, "remaining": None, "reset": None}
        async def get(path, params=None, allow_404=False):
            nonlocal requests_made
            cache_id = hashlib.sha256((path + "?" + json.dumps(params or {}, sort_keys=True)).encode()).hexdigest()
            validator_key = f"contribcompass:github-response:{cache_id}"
            saved = await _read_redis_cache(validator_key)
            request_headers = {}
            if saved and saved.get("etag"):
                request_headers["If-None-Match"] = saved["etag"]
            if saved and saved.get("last_modified"):
                request_headers["If-Modified-Since"] = saved["last_modified"]
            attempt = 0
            while True:
                if requests_made >= settings.max_github_requests_per_analysis:
                    raise APIError(413, "REPOSITORY_TOO_LARGE", "This repository exceeds the current GitHub analysis request budget.")
                requests_made += 1
                _github_diagnostics["requests_made"] = requests_made
                response = await client.get(path, params=params, headers=request_headers)
                await _record_github_response(response.status_code, response.headers)
                retry_signal = response.status_code == 429 or bool(response.headers.get("retry-after")) or "secondary rate limit" in response.text.lower() or "abuse detection" in response.text.lower()
                primary_limit = response.status_code == 403 and response.headers.get("x-ratelimit-remaining") == "0"
                if retry_signal and not primary_limit:
                    rate_error = _rate_limit_error(response.status_code, dict(response.headers))
                    delay = rate_error.retry_after if response.headers.get("retry-after") else 2 ** attempt
                    if attempt < 1 and delay <= 5:
                        await asyncio.sleep(delay)
                        attempt += 1
                        continue
                    raise rate_error
                break
            rate_status["limit"] = response.headers.get("x-ratelimit-limit", rate_status["limit"])
            rate_status["remaining"] = response.headers.get("x-ratelimit-remaining", rate_status["remaining"])
            rate_status["reset"] = response.headers.get("x-ratelimit-reset", rate_status["reset"])
            if response.status_code == 304 and saved:
                return saved.get("body")
            if response.status_code == 404 and allow_404:
                return None
            if response.status_code == 404:
                raise LookupError("Repository or requested GitHub resource was not found")
            if response.status_code in (403, 429):
                if response.headers.get("x-ratelimit-remaining") == "0" or response.status_code == 429 or response.headers.get("retry-after"):
                    raise _rate_limit_error(response.status_code, dict(response.headers))
                raise PermissionError("GitHub denied this request. Try again later.")
            if response.status_code >= 400:
                raise ConnectionError(f"GitHub API returned HTTP {response.status_code}")
            body = response.json()
            await _write_redis_cache(validator_key, {"body": body, "etag": response.headers.get("etag"), "last_modified": response.headers.get("last-modified")}, 1800)
            return body

        try:
            metadata = await get(f"/repos/{encoded_owner}/{encoded_repo}")
            if metadata.get("private"):
                raise LookupError("Only public repositories are supported")
            metadata["description"] = sanitize_content(metadata.get("description") or "", "repository-description.txt") or None
            branch = metadata.get("default_branch", "main")
            ref = quote(branch, safe="")
            commit = await get(f"/repos/{encoded_owner}/{encoded_repo}/commits/{ref}", allow_404=True)
            commit_sha = (commit or {}).get("sha")
            _github_diagnostics["repository_commit_sha"] = commit_sha
            model_name = settings.ollama_model if settings.llm_provider == "ollama" else settings.vllm_model
            cache_identity = f"{metadata.get('full_name')}:{commit_sha}:{ANALYZER_VERSION}:{settings.llm_provider}:{model_name}"
            cache_key = "contribcompass:" + hashlib.sha256(cache_identity.encode()).hexdigest() if commit_sha else None
            cached = await _read_redis_cache(cache_key) if cache_key else None
            if cached and cache_key:
                cached = _mark_cache_hit(cached, "commit-validated", requests_made=requests_made)
                cached["analysis"].update({
                    "github_authenticated": bool(_github_diagnostics["github_authenticated"]),
                    "github_rate_limit_limit": _github_diagnostics["github_rate_limit_limit"],
                    "github_rate_limit_remaining": _github_diagnostics["github_rate_limit_remaining"],
                    "github_rate_limit_reset": _github_diagnostics["github_rate_limit_reset"],
                    "cache_hits": _github_diagnostics["cache_hits"],
                    "repository_commit_sha": commit_sha,
                })
                _github_diagnostics["repository_commit_sha"] = commit_sha
                await _write_redis_cache("contribcompass:latest:" + _repo_key(owner, repo), {"result_key": cache_key, "stored_at": datetime.now(timezone.utc).timestamp()}, max(settings.analysis_cache_fresh_seconds, 1))
                logger.info("repository_analysis_cache_hit", extra={"repository": metadata.get("full_name"), "commit_sha": commit_sha, "analyzer_version": ANALYZER_VERSION, "cache_status": "commit-validated"})
                return cached
            _github_diagnostics["cache_misses"] += 1
            tree_ref = quote(commit_sha, safe="") if commit_sha else ref
            tree_result = await get(f"/repos/{encoded_owner}/{encoded_repo}/git/trees/{tree_ref}", {"recursive": "1"})
            commit_sha = commit_sha or tree_result.get("sha")
            if not cache_key:
                cache_identity = f"{metadata.get('full_name')}:{commit_sha}:{ANALYZER_VERSION}:{settings.llm_provider}:{model_name}"
                cache_key = "contribcompass:" + hashlib.sha256(cache_identity.encode()).hexdigest()
            if tree_result.get("truncated"):
                tree_warning = "GitHub truncated the recursive tree; some nested paths may not have been analyzed."
            else:
                tree_warning = None
            tree = [x for x in tree_result.get("tree", [])[:settings.max_tree_entries] if x.get("type") == "blob" and isinstance(x.get("path"), str) and not is_sensitive_path(x["path"])]
            file_paths = [x["path"] for x in tree]
            selected = select_important_paths(file_paths, min(settings.max_important_files, max(settings.max_github_requests_per_analysis - requests_made, 0)))

            async def fetch_file(path):
                try:
                    data = await get(f"/repos/{encoded_owner}/{encoded_repo}/contents/{quote(path, safe='/')}", {"ref": commit_sha or branch})
                    if not data or data.get("encoding") != "base64":
                        return path, ""
                    import base64
                    encoded = data.get("content", "")
                    if data.get("size", 0) > settings.max_file_size:
                        encoded = encoded[: ((settings.max_file_size + 2) // 3) * 4]
                    raw = base64.b64decode(encoded)[:settings.max_file_size]
                    content = raw.decode("utf-8", "replace")
                    if path.lower().split("/")[-1].startswith("readme"):
                        content = content[:settings.max_readme_size]
                    return path, content
                except (LookupError, ConnectionError):
                    return path, ""
            files = dict(await asyncio.gather(*(fetch_file(path) for path in selected)))
            files = {path: sanitize_content(content, path) for path, content in files.items()}
            issue_result = await get(f"/repos/{encoded_owner}/{encoded_repo}/issues", {"state": "open", "per_page": min(max(settings.max_issues, 1), 100), "sort": "updated", "direction": "desc"})
        except (httpx.TimeoutException, asyncio.TimeoutError) as exc:
            raise APIError(504, "GITHUB_TIMEOUT", "GitHub did not respond in time. Please try again.", retryable=True) from exc
        except httpx.HTTPError as exc:
            raise APIError(503, "GITHUB_UNAVAILABLE", "GitHub is temporarily unavailable. Please try again shortly.", retryable=True) from exc

    metadata["default_branch"] = branch
    profile = analyze_profile(metadata, file_paths, files, {}, commit_sha)
    issues = [rank_issue(item) for item in (issue_result or []) if "pull_request" not in item and item.get("state") == "open"]
    issues.sort(key=lambda x: (-x["score"], -x.get("comments", 0), x["number"]))
    guide = _deterministic_guide(profile, issues)
    evidence = []
    for index, path in enumerate(sorted(files, key=lambda p: (-file_priority(p), p.lower())), start=1):
        if not files[path].strip():
            continue
        evidence.append({"id": f"E{len(evidence) + 1}", "label": path, "url": f"https://github.com/{owner}/{repo}/blob/{quote(branch, safe='/')}/{quote(path, safe='/')}", "excerpt": sanitize_content(files[path], path)[:500]})
    context_profile = _context_profile(profile)
    fingerprint = repository_fingerprint(profile, len(issues))
    profile["issues"] = issues
    profile["ranked_beginner_issues"] = [x for x in issues if x["score"] >= 25]
    profile["fingerprint"] = fingerprint
    context = {
        "repository_profile": context_profile,
        "ranked_issues": [{k: (v[:650] if k == "body" and isinstance(v, str) else v) for k, v in issue.items()} for issue in profile["ranked_beginner_issues"][:10]],
        "evidence": [],
    }
    base_size = len(json.dumps(context, ensure_ascii=False, separators=(",", ":")))
    evidence_budget = max(settings.max_context_size - base_size - 2500, 0)
    context_evidence = _select_content_for_context(profile, files, owner, repo, branch, evidence_budget)
    context["evidence"] = context_evidence
    context_text = _fit_context(context, settings.max_context_size)
    if settings.llm_provider.lower() in {"ollama", "vllm"}:
        generated = await generate_guide(context_text, guide, set(file_paths), {x["id"]: x["content"] for x in context_evidence})
        if generated:
            source_text = " ".join([metadata.get("description") or "", *(files.values())]).lower()
            domain_terms = {x for x in re.findall(r"[a-z][a-z0-9+#.-]{3,}", source_text) if x not in {"this", "that", "with", "from", "have", "your", "will", "into", "when", "then", "also", "using", "project", "readme", "install", "tests"}}
            generated_words = set(re.findall(r"[a-z][a-z0-9+#.-]{3,}", generated.get("project_summary", "").lower()))
            if len(generated_words & domain_terms) >= 2:
                guide["project_summary"] = generated["project_summary"]
            evidence_by_id = {item["id"]: item["content"] for item in context_evidence}
            verified_claims = []
            for claim in generated.get("claim_evidence", []):
                source_text = " ".join(evidence_by_id.get(eid, "") for eid in claim["evidence_ids"]).lower()
                terms = {x for x in re.findall(r"[a-z][a-z0-9+#.-]{3,}", claim["claim"].lower()) if x not in {"this", "that", "with", "from", "have", "your", "will", "into", "when", "then", "also", "using", "project", "found", "evidence"}}
                if terms and len(terms & set(re.findall(r"[a-z][a-z0-9+#.-]{3,}", source_text))) >= min(2, len(terms)):
                    verified_claims.append(claim)
            if verified_claims:
                guide["claim_evidence"] = verified_claims
        else:
            guide["warnings"].append("LLM generation was unavailable or returned invalid structured output; deterministic repository analysis is shown.")
    warnings = list(guide["warnings"])
    if tree_warning:
        warnings.append(tree_warning)
    if len(file_paths) > len(files):
        warnings.append(f"Inspected {len(files)} prioritized files out of {len(file_paths)} tracked files; lower-priority paths were not fetched.")
    guide["warnings"] = list(dict.fromkeys(warnings))
    fingerprint["context_size"] = len(context_text)
    fingerprint["llm_provider"] = settings.llm_provider
    fingerprint["llm_model"] = model_name
    analysis_key = cache_key
    def latest_or_response(key, response_key):
        current = _github_diagnostics.get(key)
        if current is not None:
            return current
        value = rate_status.get(response_key)
        return int(value) if isinstance(value, str) and value.isdigit() else value

    result = {
        "repository": {"owner": metadata["owner"]["login"], "name": metadata["name"], "full_name": metadata["full_name"], "url": metadata["html_url"], "description": metadata.get("description"), "language": metadata.get("language"), "stars": metadata.get("stargazers_count", 0), "forks": metadata.get("forks_count", 0), "default_branch": branch, "license": (metadata.get("license") or {}).get("spdx_id")},
        "guide": guide, "evidence": evidence,
        "analysis": {"tree": _compact_tree(file_paths), "dependencies": profile["dependency_manifests"], "files_inspected": list(files), "profile": profile, "fingerprint": fingerprint, "context_size": len(context_text), "analysis_key": analysis_key, "analyzer_version": ANALYZER_VERSION, "cache_hit": False, "cache_status": "miss", "cache_hits": _github_diagnostics["cache_hits"], "cache_misses": _github_diagnostics["cache_misses"], "requests_made": requests_made, "github_authenticated": bool(_github_diagnostics["github_authenticated"]), "github_rate_limit_limit": latest_or_response("github_rate_limit_limit", "limit"), "github_rate_limit_remaining": latest_or_response("github_rate_limit_remaining", "remaining"), "github_rate_limit_reset": latest_or_response("github_rate_limit_reset", "reset"), "github_retry_after": _github_diagnostics["github_retry_after"], "repository_commit_sha": commit_sha, "duration_seconds": None, "truncated": bool(tree_warning or len(file_paths) > len(files) or len(tree_result.get("tree", [])) > settings.max_tree_entries)},
        "metadata": {"commit_sha": commit_sha, "model": model_name, "generated_at": datetime.now(timezone.utc).isoformat()},
    }
    logger.info("repository_profile_built", extra={"repository": metadata.get("full_name"), "commit_sha": commit_sha, "fingerprint": fingerprint})
    result["analysis"]["duration_seconds"] = round(asyncio.get_running_loop().time() - started_at, 3)
    _github_diagnostics["requests_made"] = requests_made
    _github_diagnostics["repository_commit_sha"] = commit_sha
    _github_diagnostics["analysis_duration_seconds"] = result["analysis"]["duration_seconds"]
    result["analysis"].update({"cache_hits": _github_diagnostics["cache_hits"], "cache_misses": _github_diagnostics["cache_misses"]})
    logger.info("github_analysis_complete", extra={"repository": metadata.get("full_name"), "commit_sha": commit_sha, "github_authenticated": result["analysis"]["github_authenticated"], "requests_made": requests_made, "rate_limit_remaining": result["analysis"]["github_rate_limit_remaining"], "cache_hits": _github_diagnostics["cache_hits"], "cache_misses": _github_diagnostics["cache_misses"], "duration_seconds": result["analysis"]["duration_seconds"]})
    await _write_redis_cache(cache_key, result, settings.analysis_cache_ttl_seconds)
    await _write_redis_cache("contribcompass:latest:" + _repo_key(owner, repo), {"result_key": cache_key, "stored_at": datetime.now(timezone.utc).timestamp()}, max(settings.analysis_cache_fresh_seconds, 1))
    return result


async def _read_redis_cache(key: str) -> dict | None:
    if not settings.redis_url:
        return None
    try:
        from redis.asyncio import Redis
        client = Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=0.3, socket_timeout=0.3)
        try:
            value = await client.get(key)
            return json.loads(value) if value else None
        finally:
            await client.aclose()
    except Exception:
        return None


async def _write_redis_cache(key: str, value: dict, ttl: int = 1800) -> None:
    if not settings.redis_url:
        return
    try:
        from redis.asyncio import Redis
        client = Redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=0.3, socket_timeout=0.3)
        try:
            await client.set(key, json.dumps(value, ensure_ascii=False), ex=max(ttl, 1))
        finally:
            await client.aclose()
    except Exception:
        return


def _compact_tree(paths: list[str]) -> str:
    counts: dict[str, int] = {}
    samples: dict[str, list[str]] = {}
    for path in paths:
        first = path.split("/", 1)[0]
        if "/" in path:
            counts[first] = counts.get(first, 0) + 1
            if len(samples.setdefault(first, [])) < 4:
                samples[first].append(path)
    roots = [p for p in paths if "/" not in p]
    lines = roots[:50]
    for folder in sorted(counts):
        lines.append(f"{folder}/ ({counts[folder]} tracked files)")
        lines.extend(f"  {path}" for path in samples[folder])
    return "\n".join(lines[:180])


async def generate_guide(context_json: str, deterministic_guide: dict, known_paths: set[str], known_evidence: dict[str, str]):
    """Generate prose from real repository context; never delegate issue/file selection to the model."""
    system = """You are analyzing ONE specific GitHub repository. Use ONLY the supplied repository evidence. Never use generic assumptions when repository evidence is available.
Never invent files, directories, dependencies, commands, environment variables, issue numbers, architecture, contribution policies, or frameworks. If information is unavailable, return exactly 'Not found in repository evidence.'
The supplied repository_profile contains deterministic manifest, README, CONTRIBUTING, test, configuration, CI, directory, entry-point, language, and real issue analysis. File evidence has stable IDs such as E1 and source paths. Explain this repository specifically.
Return exactly one JSON object, with every field present and no extra fields: project_summary (string), technology_stack (array of strings), prerequisites (array of strings), setup_steps (array of strings), run_instructions (array of strings), build_commands (array of strings), test_commands (array of strings), important_directories (array of strings), entry_points (array of objects with path and basis), where_to_start (string), contribution_workflow (array of strings), warnings (array of strings), evidence (array of objects with claim and evidence_ids). Keep issue selection deterministic; do not add issue recommendations. Cite evidence IDs for factual claims. Commands must appear in evidence/profile command fields or README command blocks."""
    try:
        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            if settings.llm_provider.lower() == "ollama":
                response = await client.post(settings.ollama_base_url.rstrip("/") + "/api/chat", json={"model": settings.ollama_model, "stream": False, "format": "json", "messages": [{"role": "system", "content": system}, {"role": "user", "content": context_json}]})
                response.raise_for_status()
                text = response.json().get("message", {}).get("content", "")
            else:
                response = await client.post(settings.vllm_base_url.rstrip("/") + "/chat/completions", json={"model": settings.vllm_model, "temperature": 0.1, "response_format": {"type": "json_object"}, "messages": [{"role": "system", "content": system}, {"role": "user", "content": context_json}]})
                response.raise_for_status()
                text = response.json()["choices"][0]["message"]["content"]
        try:
            result = ModelGuide.model_validate_json(text).model_dump()
        except Exception as exc:
            raise APIError(502, "LLM_INVALID_RESPONSE", "The language model returned an invalid analysis response.", retryable=True) from exc
        allowed_list = {"technology_stack", "prerequisites", "setup_steps", "run_instructions", "build_commands", "test_commands", "important_directories", "contribution_workflow"}
        clean = {key: [str(x)[:1200] for x in value[:20] if isinstance(x, str)] for key, value in result.items() if key in allowed_list and isinstance(value, list)}
        if isinstance(result.get("project_summary"), str):
            clean["project_summary"] = result["project_summary"][:2000]
        if isinstance(result.get("where_to_start"), str):
            clean["where_to_start"] = result["where_to_start"][:1000]
        if isinstance(result.get("entry_points"), list):
            clean["entry_points"] = [x for x in result["entry_points"][:20] if isinstance(x, dict) and x.get("path") in known_paths]
        if isinstance(result.get("important_directories"), list):
            dirs = {"/".join(path.split("/")[:i]) + "/" for path in known_paths for i in range(1, len(path.split("/")))}
            clean["important_directories"] = [x for x in clean.get("important_directories", []) if x.rstrip("/") + "/" in dirs]
        verified_claims = []
        for item in result.get("evidence", [])[:50] if isinstance(result.get("evidence"), list) else []:
            if not isinstance(item, dict):
                continue
            ids = [x for x in item.get("evidence_ids", []) if x in known_evidence] if isinstance(item.get("evidence_ids"), list) else []
            if ids and isinstance(item.get("claim"), str):
                verified_claims.append({"claim": item["claim"][:800], "evidence_ids": ids})
        if verified_claims:
            clean["claim_evidence"] = verified_claims
        # Only preserve issue recommendations selected deterministically from actual API results.
        clean["recommended_issues"] = deterministic_guide["recommended_issues"]
        clean["first_contribution"] = deterministic_guide["first_contribution"]
        if "project_summary" not in clean:
            raise APIError(502, "LLM_INVALID_RESPONSE", "The language model returned an incomplete analysis response.", retryable=True)
        return clean
    except APIError:
        raise
    except httpx.TimeoutException as exc:
        raise APIError(504, "LLM_TIMEOUT", "The language model did not respond in time. Please try again.", retryable=True) from exc
    except httpx.HTTPStatusError as exc:
        raise APIError(503, "LLM_UNAVAILABLE", "The language model service is temporarily unavailable.", retryable=True) from exc
    except httpx.HTTPError as exc:
        raise APIError(503, "LLM_UNAVAILABLE", "The language model service could not be reached.", retryable=True) from exc
    except Exception as exc:
        raise APIError(502, "LLM_INVALID_RESPONSE", "The language model returned an invalid analysis response.", retryable=True) from exc
