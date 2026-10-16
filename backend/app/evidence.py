"""Structured source records and conservative claim/evidence validation."""
from __future__ import annotations

import re
from pathlib import PurePosixPath
from urllib.parse import urlparse

from .analyzer import file_priority

_TOKEN = re.compile(r"[a-z][a-z0-9+#.-]{2,}", re.I)
_STOP = {"the", "and", "for", "with", "from", "this", "that", "are", "was", "were", "has", "have", "your", "into", "using", "use", "not", "can", "will", "all", "project", "repository", "evidence", "found", "file", "files", "code", "based", "provides", "includes"}
_PATH = re.compile(r"(?<![\w.-])(?:[\w.@+-]+/)*[\w.@+-]+\.(?:md|rst|txt|py|pyi|js|jsx|ts|tsx|json|toml|yaml|yml|lock|go|rs|java|kt|rb|php|sh|cfg|ini|gradle|xml)(?![\w.-])", re.I)
_CANONICAL_FILE = re.compile(r"\b(?:README(?:\.md|\.rst|\.txt)?|CONTRIBUTING(?:\.md|\.rst)?|DEVELOPMENT\.md|INSTALL\.md|Dockerfile|Makefile|compose\.ya?ml|docker-compose\.ya?ml)\b", re.I)
_ISSUE = re.compile(r"(?<!\w)#(\d+)\b")
_URL = re.compile(r"https?://[^\s)\]}>]+", re.I)
_COMMAND = re.compile(r"(?<!\w)(?:npm|pnpm|yarn|bun|python(?:\s+-m)?|pytest|uv|poetry|pip|cargo|go|mvn|gradle|docker|make|just|composer|bundle)\s+[^\s,;]+(?:\s+[^\s,;]+)?", re.I)


def relevance_score(path: str) -> float:
    """Normalize deterministic path priority into a stable [0, 1] evidence grade."""
    return round(min(max(file_priority(path), 0) / 1200, 1), 4)


def build_normalized_evidence(owner: str, repo: str, branch: str, metadata: dict, profile: dict,
                              files: dict[str, str], selected: list[dict], issues: list[dict]) -> list[dict]:
    records: list[dict] = [{
        "id": "M1", "source_type": "repository_metadata", "path": None,
        "line_start": None, "line_end": None,
        "content": "\n".join(filter(None, [metadata.get("description"), metadata.get("language")])),
        "facts": [
            {"claim": f"Repository is {metadata.get('full_name', f'{owner}/{repo}')}.", "confidence": 1.0},
            *([{"claim": f"Primary language is {metadata['language']}.", "confidence": 1.0}] if metadata.get("language") else []),
        ], "relevance_score": 1.0,
        "url": metadata.get("html_url", f"https://github.com/{owner}/{repo}"),
    }]
    profile_facts = []
    if profile.get("frameworks"):
        profile_facts.extend({"claim": f"Detected framework or tool: {name}.", "confidence": 0.95} for name in profile["frameworks"])
    if profile.get("package_manager"):
        profile_facts.append({"claim": f"Package manager is {profile['package_manager']}.", "confidence": 0.95})
    for item in selected:
        path = item.get("path") or item.get("label")
        if not path:
            continue
        content = item.get("content") or files.get(path, "")
        facts = [{"claim": f"File {path} exists in the analyzed commit.", "confidence": 1.0}]
        if path == profile.get("readme_analysis", {}).get("path"):
            purpose = profile.get("readme_analysis", {}).get("purpose")
            if purpose:
                facts.append({"claim": f"README purpose: {purpose}", "confidence": 0.9})
        if PurePosixPath(path).name.lower() in {"package.json", "pyproject.toml", "requirements.txt", "cargo.toml", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts"}:
            facts.extend({"claim": f"Dependency {dep} is declared in {path}.", "confidence": 0.98} for dep in profile.get("dependencies", {}).get(path, [])[:100])
        if PurePosixPath(path).name.lower() == "package.json":
            facts.extend({"claim": f"Script {name} is {command}.", "confidence": 0.98} for name, command in profile.get("scripts", {}).items())
        records.append({
            "id": item.get("id"), "source_type": "file", "path": path,
            "line_start": 1 if content else None, "line_end": len(content.splitlines()) if content else None,
            "content": content, "facts": facts, "relevance_score": relevance_score(path),
            "url": item.get("url") or f"https://github.com/{owner}/{repo}/blob/{branch}/{path}",
        })
    for index, directory in enumerate(profile.get("important_directories", []), start=1):
        path = directory.get("path", "").rstrip("/")
        if path:
            records.append({"id": f"D{index}", "source_type": "directory", "path": path,
                            "line_start": None, "line_end": None, "content": "\n".join(directory.get("sample_files", [])),
                            "facts": [{"claim": f"Directory {path}/ exists.", "confidence": 1.0}],
                            "relevance_score": 0.9, "url": f"https://github.com/{owner}/{repo}/tree/{branch}/{path}"})
    for path, dependencies in profile.get("dependencies", {}).items():
        for dep in dependencies:
            records.append({"id": f"DEP{len(records) + 1}", "source_type": "dependency", "path": path,
                            "line_start": None, "line_end": None, "content": dep,
                            "facts": [{"claim": f"Dependency {dep} is declared in {path}.", "confidence": 0.98}],
                            "relevance_score": 0.95, "url": f"https://github.com/{owner}/{repo}/blob/{branch}/{path}"})
    for issue in issues:
        records.append({"id": f"I{issue['number']}", "source_type": "issue", "path": None,
                        "line_start": None, "line_end": None,
                        "content": f"{issue.get('title', '')}\n{issue.get('body', '')}",
                        "facts": [{"claim": f"Open GitHub issue #{issue['number']}: {issue.get('title', '')}.", "confidence": 1.0}],
                        "relevance_score": round(min(issue.get("score", 0), 100) / 100, 4),
                        "url": issue.get("url")})
    return records


def _tokens(value: str) -> set[str]:
    return {token.lower().rstrip(".+-#") for token in _TOKEN.findall(value)
            if token.lower().rstrip(".+-#") not in _STOP and len(token.rstrip(".+-#")) >= 3}


def validate_claims(claims: list[dict], evidence: list[dict], known_paths: set[str],
                    issues: list[dict], known_commands: set[str], owner: str, repo: str) -> dict:
    by_id = {item.get("id"): item for item in evidence if item.get("id")}
    issue_numbers = {str(item.get("number")) for item in issues}
    directory_paths = {"/".join(path.split("/")[:depth]) + "/" for path in known_paths
                       for depth in range(1, len(path.split("/")))}
    checked = []
    for item in claims:
        claim = str(item.get("claim", "")).strip()
        ids = item.get("evidence_ids") if isinstance(item.get("evidence_ids"), list) else []
        valid_ids = [value for value in ids if value in by_id]
        reason = None
        if not claim or not valid_ids:
            status, reason = "unsupported", "No valid evidence citation was supplied."
        else:
            low = claim.lower()
            path_text = claim.rstrip(" .,!?:;")
            referenced_paths = _PATH.findall(path_text) + _CANONICAL_FILE.findall(path_text)
            bad_path = next((p for p in referenced_paths if p not in known_paths and p.rstrip("/") + "/" not in directory_paths), None)
            bad_issue = next((n for n in _ISSUE.findall(claim) if n not in issue_numbers), None)
            repo_prefix = f"/{owner}/{repo}".lower()
            bad_url = next((u for u in _URL.findall(claim) if (urlparse(u).hostname or "").lower() != "github.com" or not (urlparse(u).path.lower() == repo_prefix or urlparse(u).path.lower().startswith(repo_prefix + "/"))), None)
            def command_key(value: str) -> str:
                return re.sub(r"\s*\([^)]*\)\s*$", "", value.strip()).lower()
            accepted_commands = {command_key(value) for value in known_commands}
            bad_command = next((c for c in _COMMAND.findall(claim) if command_key(c) not in accepted_commands and not any(known.startswith(command_key(c) + " ") for known in accepted_commands)), None)
            if bad_path:
                status, reason = "contradicted", f"Referenced file or directory is absent: {bad_path}"
            elif bad_issue:
                status, reason = "contradicted", f"Referenced issue was not retrieved: #{bad_issue}"
            elif bad_url:
                status, reason = "contradicted", "Referenced URL is outside the analyzed GitHub repository."
            elif bad_command:
                status, reason = "contradicted", f"Command is not present in repository evidence: {bad_command}"
            else:
                claim_tokens = _tokens(claim)
                source = " ".join(
                    str(by_id[eid].get("content", "")) + " " + " ".join(str(f.get("claim", "")) for f in by_id[eid].get("facts", []))
                    for eid in valid_ids
                )
                overlap = len(claim_tokens & _tokens(source)) / max(len(claim_tokens), 1)
                if overlap >= 0.6:
                    status = "supported"
                elif overlap >= 0.25:
                    status, reason = "partially_supported", "Only part of the claim is reflected in its cited evidence."
                else:
                    status, reason = "unsupported", "The cited evidence does not substantiate the claim text."
        checked.append({"claim": claim, "evidence_ids": valid_ids, "status": status, **({"reason": reason} if reason else {})})
    total = len(checked)
    counts = {key: sum(item["status"] == key for item in checked) for key in ("supported", "partially_supported", "unsupported", "contradicted")}
    citation_total = sum(len(item.get("evidence_ids", [])) for item in claims)
    citation_valid = sum(len(item["evidence_ids"]) for item in checked)
    return {"claims": checked, "metrics": {
        "claim_count": total,
        "faithfulness": counts["supported"] / total if total else None,
        "unsupported_claim_rate": counts["unsupported"] / total if total else None,
        "contradicted_claim_rate": counts["contradicted"] / total if total else None,
        "citation_accuracy": citation_valid / citation_total if citation_total else None,
        "supported_claims": counts["supported"], "partially_supported_claims": counts["partially_supported"],
        "unsupported_claims": counts["unsupported"], "contradicted_claims": counts["contradicted"],
    }}


def retrieval_metrics(candidate_scores: dict[str, float], retrieved: list[str], required: set[str], k: int = 10) -> dict:
    ordered = retrieved[:]
    graded = [max(float(candidate_scores.get(item, 0)), 0) for item in ordered]
    total_relevance = len(required)
    dcg = sum((2 ** score - 1) / __import__("math").log2(rank + 2) for rank, score in enumerate(graded[:k]))
    ideal = sorted((max(float(v), 0) for v in candidate_scores.values()), reverse=True)[:k]
    idcg = sum((2 ** score - 1) / __import__("math").log2(rank + 2) for rank, score in enumerate(ideal))
    first_relevant = next((index for index, item in enumerate(ordered, 1) if item in required), None)
    return {"context_precision": sum(item in required for item in ordered) / len(ordered) if ordered else None,
            "context_recall": sum(item in required for item in ordered) / total_relevance if total_relevance else None,
            f"hit_rate_at_{k}": int(any(item in required for item in ordered[:k])),
            "mrr": 1 / first_relevant if first_relevant else 0.0,
            f"ndcg_at_{k}": dcg / idcg if idcg else None}


def ranking_metrics(ranked: list[str], relevant: set[str], k: int = 10) -> dict:
    top_k = ranked[:k]
    hits = [item for item in top_k if item in relevant]
    first = next((index for index, item in enumerate(ranked, 1) if item in relevant), None)
    dcg = sum(1 / __import__("math").log2(index + 2) for index, item in enumerate(top_k) if item in relevant)
    ideal = sum(1 / __import__("math").log2(index + 2) for index in range(min(len(relevant), k)))
    return {f"precision_at_{k}": len(hits) / k if k else None,
            f"recall_at_{k}": len(hits) / len(relevant) if relevant else None,
            "mrr": 1 / first if first else 0.0,
            f"ndcg_at_{k}": dcg / ideal if ideal else None}


def specificity_similarity(left: dict, right: dict) -> float:
    """Jaccard overlap across concrete output dimensions; high values flag generic pairs."""
    fields = ("technology_stack", "setup_steps", "run_instructions", "important_directories", "entry_points", "recommended_issues")
    def values(guide):
        found = set()
        for field in fields:
            for value in guide.get(field, []) or []:
                found.add(str(value.get("path", value.get("number", value))) if isinstance(value, dict) else str(value).lower())
        return found
    a, b = values(left), values(right)
    return len(a & b) / len(a | b) if a | b else 1.0
