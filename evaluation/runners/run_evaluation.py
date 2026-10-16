"""Run deterministic analysis against commit-pinned GitHub snapshots.

Usage: python -m evaluation.runners.run_evaluation [--with-model] [--model qwen3:latest]
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import hashlib
import html
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from app.analyzer import analyze_profile, file_priority, repository_fingerprint, select_important_paths  # noqa: E402
from app.config import settings  # noqa: E402
from app.evidence import build_normalized_evidence, relevance_score, retrieval_metrics, ranking_metrics, specificity_similarity, validate_claims  # noqa: E402
from app.service import _context_profile, _deterministic_guide, _fit_context, _select_content_for_context, generate_guide, rank_issue  # noqa: E402

DATASETS = ROOT / "evaluation" / "datasets"
REPORTS = ROOT / "evaluation" / "reports"
MAX_FILES = 16


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _expected_facts(golden: dict) -> dict:
    return golden.get("facts", {}) if isinstance(golden.get("facts"), dict) else {}


def compare_facts(profile: dict, expected: dict, paths: set[str]) -> dict:
    actual = {
        "primary_language": profile["repository"].get("primary_language"),
        "has_readme": profile["readme_analysis"]["status"] == "found",
        "has_contributing": profile["contributing_analysis"]["status"] == "found",
        "has_tests": bool(profile["test_structure"] or profile["test_commands"]),
        "has_docker": bool(profile["docker_configuration"]),
        "has_ci": bool(profile["ci_configuration"]),
        "important_files": profile["important_files"],
        "frameworks": profile["frameworks"],
        "package_manager": profile["package_manager"],
        "entrypoints": [item["path"] for item in profile["entry_points"]],
    }
    checks = {}
    for key, expected_value in expected.items():
        if expected_value is None:
            continue
        if key == "important_files":
            truth = set(expected_value)
            observed = set(actual[key])
            checks[key] = {"expected": sorted(truth), "actual": sorted(observed & paths), "score": len(truth & observed) / len(truth) if truth else None}
        elif key in {"frameworks", "entrypoints"}:
            truth = set(expected_value if isinstance(expected_value, list) else [expected_value])
            observed = set(actual[key])
            checks[key] = {"expected": sorted(truth), "actual": sorted(observed), "score": len(truth & observed) / len(truth) if truth else None}
        else:
            checks[key] = {"expected": expected_value, "actual": actual.get(key), "score": float(actual.get(key) == expected_value)}
    scores = [item["score"] for item in checks.values() if item.get("score") is not None]
    return {"actual": actual, "checks": checks, "repository_fact_accuracy": sum(scores) / len(scores) if scores else None, "fact_count": len(scores)}


def _tree_facts(paths: list[str], truncated: bool) -> dict:
    root = [path for path in paths if "/" not in path]
    def present(value):
        return True if value else None if truncated else False
    return {
        "has_readme": present(any(Path(path).name.lower().startswith("readme") for path in root)),
        "has_contributing": present(any(Path(path).name.lower().startswith("contributing") for path in root)),
        "has_tests": present(any(path.lower().startswith(("test/", "tests/", "__tests__/")) or "/tests/" in path.lower() or "/test/" in path.lower() or Path(path).name.lower().startswith("test_") for path in paths)),
        "has_docker": present(any(Path(path).name.lower() in {"dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"} for path in root)),
        "has_ci": present(any(path.startswith(".github/workflows/") for path in paths)),
    }


async def fetch_snapshot(client: httpx.AsyncClient, item: dict) -> tuple[dict, dict, list[str], dict[str, str], bool]:
    slug, sha = item["repository"], item.get("commit_sha")
    if not sha or len(sha) != 40:
        raise ValueError(f"{slug} does not have a pinned commit SHA")
    response = await client.get(f"/repos/{slug}/git/trees/{sha}", params={"recursive": "1"})
    response.raise_for_status()
    tree = response.json()
    paths = [entry["path"] for entry in tree.get("tree", []) if entry.get("type") == "blob"]
    truncated = bool(tree.get("truncated")) or item.get("tree_truncated", False)
    selected = select_important_paths(paths, MAX_FILES)
    files: dict[str, str] = {}
    semaphore = asyncio.Semaphore(4)

    async def fetch_file(path):
        async with semaphore:
            result = await client.get(f"/repos/{slug}/contents/{path}", params={"ref": sha})
            result.raise_for_status()
            payload = result.json()
            if payload.get("encoding") != "base64" or not payload.get("content"):
                return
            import base64
            try:
                files[path] = base64.b64decode(payload["content"], validate=False).decode("utf-8", errors="replace")
            except (ValueError, UnicodeError):
                return

    await asyncio.gather(*(fetch_file(path) for path in selected))
    metadata = {"owner": {"login": slug.split("/")[0]}, "name": slug.split("/")[1], "full_name": slug,
                "html_url": item.get("html_url", f"https://github.com/{slug}"), "default_branch": item.get("default_branch"),
                "description": None, "language": item.get("primary_language")}
    return item, metadata, paths, files, truncated


async def run(args) -> dict:
    repositories = load_json(DATASETS / "repositories.json")
    facts_rows = {row["repository"]: row for row in load_json(DATASETS / "golden_facts.json")}
    issue_rows = {row["repository"]: row for row in load_json(DATASETS / "golden_issues.json")}
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "ContribCompass-Evaluation"}
    if settings.github_token:
        headers["Authorization"] = f"Bearer {settings.github_token}"
    limits = httpx.Limits(max_connections=6, max_keepalive_connections=3)
    timeout = httpx.Timeout(settings.github_timeout_seconds)
    records = []
    model_label = (args.model or (settings.ollama_model if settings.llm_provider == "ollama" else settings.vllm_model)) if args.with_model else None
    async with httpx.AsyncClient(base_url=settings.github_api_url, headers=headers, timeout=timeout, limits=limits, follow_redirects=True) as client:
        gate = asyncio.Semaphore(2)

        async def evaluate(item):
            async with gate:
                started = time.perf_counter()
                try:
                    snapshot, metadata, paths, files, truncated = await fetch_snapshot(client, item)
                    language = {item["primary_language"]: 100} if item.get("primary_language") else {}
                    profile = analyze_profile(metadata, paths, files, language, item["commit_sha"])
                    fact_eval = compare_facts(profile, _expected_facts(facts_rows.get(item["repository"], {})), set(paths))
                    required = set(_expected_facts(facts_rows.get(item["repository"], {})).get("important_files", []))
                    candidate_scores = {path: relevance_score(path) for path in paths if file_priority(path) > 0}
                    selected = _select_content_for_context(profile, files, item["repository"].split("/")[0], item["repository"].split("/")[1], item["default_branch"], settings.max_context_size // 2)
                    retrieved_paths = [record["path"] for record in selected]
                    retrieved_required = required or set(candidate_scores)
                    retrieval = retrieval_metrics(candidate_scores, retrieved_paths, retrieved_required)
                    gold_issues = set(str(x) for x in issue_rows.get(item["repository"], {}).get("relevant_issue_numbers", []))
                    issue_metrics = ranking_metrics([], gold_issues, 10) if gold_issues else {"precision_at_5": None, "recall_at_10": None, "mrr": None, "ndcg_at_10": None}
                    guide = _deterministic_guide(profile, [])
                    generation = {"faithfulness": None, "unsupported_claim_rate": None, "citation_accuracy": None, "command_accuracy": None, "issue_accuracy": None, "hallucination_rate": None, "answer_relevance": None, "answer_correctness": None, "completeness": None, "claim_count": 0}
                    if args.with_model:
                        if args.model and settings.llm_provider == "ollama":
                            settings.ollama_model = args.model
                        if args.model and settings.llm_provider == "vllm":
                            settings.vllm_model = args.model
                        context = {"repository_profile": _context_profile(profile), "ranked_issues": [], "evidence": selected}
                        context_json = _fit_context(context, settings.max_context_size)
                        normalized = build_normalized_evidence(item["repository"].split("/")[0], item["repository"].split("/")[1], item["default_branch"], metadata, profile, files, selected, [])
                        evidence_map = {record["id"]: record.get("content", "") for record in normalized}
                        generated = await generate_guide(context_json, guide, set(paths), evidence_map)
                        claims = validate_claims(generated.get("claim_evidence", []), normalized, set(paths), [], set(profile.get("install_commands", []) + profile.get("build_commands", []) + profile.get("test_commands", [])), item["repository"].split("/")[0], item["repository"].split("/")[1])
                        generation = claims["metrics"]
                        guide = {**guide, **generated}
                    fact_sha = hashlib.sha256(json.dumps(fact_eval["actual"], sort_keys=True, ensure_ascii=False).encode()).hexdigest()
                    return {"repository": item["repository"], "commit_sha": item["commit_sha"], "model": model_label,
                            "model_version": model_label, "latency_seconds": round(time.perf_counter() - started, 3),
                            "tree_truncated": truncated, "fact_accuracy": fact_eval["repository_fact_accuracy"], "fact_count": fact_eval["fact_count"],
                            "fact_checks": fact_eval["checks"], "important_file_recall": retrieval["context_recall"], "retrieval": retrieval,
                            "issues": issue_metrics, "generation": generation, "fact_digest": fact_sha, "guide": guide,
                            "annotation_status": facts_rows.get(item["repository"], {}).get("annotation_status", "missing")}
                except Exception as exc:
                    return {"repository": item["repository"], "commit_sha": item.get("commit_sha"), "error": type(exc).__name__, "message": str(exc)[:300], "latency_seconds": round(time.perf_counter() - started, 3)}

        records = await asyncio.gather(*(evaluate(item) for item in repositories))

    successful = [row for row in records if "error" not in row]
    def mean(key, group=successful):
        values = [row.get(key) for row in group if isinstance(row.get(key), (int, float))]
        return sum(values) / len(values) if values else None
    aggregate = {
        "repository_fact_accuracy": mean("fact_accuracy"), "important_file_recall": mean("important_file_recall"),
        "dependency_accuracy": None, "issue_precision_at_5": None, "issue_recall_at_10": None,
        "context_precision": mean("context_precision", [row["retrieval"] for row in successful if row.get("retrieval")]),
        "context_recall": mean("context_recall", [row["retrieval"] for row in successful if row.get("retrieval")]),
        "hit_rate_at_10": mean("hit_rate_at_10", [row["retrieval"] for row in successful if row.get("retrieval")]),
        "retrieval_mrr": mean("mrr", [row["retrieval"] for row in successful if row.get("retrieval")]),
        "retrieval_ndcg_at_10": mean("ndcg_at_10", [row["retrieval"] for row in successful if row.get("retrieval")]),
        "faithfulness": mean("faithfulness", [row["generation"] for row in successful if row.get("generation")]),
        "answer_relevance": None, "answer_correctness": None, "citation_accuracy": None,
        "command_accuracy": None, "issue_accuracy": None, "hallucination_rate": None,
        "latency_seconds": mean("latency_seconds"), "repository_count": len(records), "successful_repositories": len(successful),
    }
    if args.with_model:
        for key in ("faithfulness", "citation_accuracy", "command_accuracy", "issue_accuracy", "hallucination_rate"):
            aggregate[key] = mean(key, [row["generation"] for row in successful if row.get("generation")])
    pair_scores = []
    specificity_failures = []
    for left_i, left in enumerate(successful):
        for right in successful[left_i + 1:]:
            if left.get("repository", "").split("/")[0] == right.get("repository", "").split("/")[0]:
                continue
            score = specificity_similarity(left["guide"], right["guide"])
            pair_scores.append(score)
            if score >= 0.85 and left["repository"] != right["repository"]:
                specificity_failures.append({"left": left["repository"], "right": right["repository"], "similarity": score, "code": "REPOSITORY_SPECIFICITY_FAILURE"})
    aggregate["repository_specificity"] = 1 - (sum(pair_scores) / len(pair_scores)) if pair_scores else None
    aggregate["specificity_failures"] = len(specificity_failures)
    weighted = {"repository_fact_accuracy": .25, "important_file_recall": .15, "issue_precision_at_5": .10, "faithfulness": .20, "citation_accuracy": .10, "command_accuracy": .10, "answer_relevance": .05, "repository_specificity": .05}
    available = [(name, weight) for name, weight in weighted.items() if isinstance(aggregate.get(name), (int, float))]
    aggregate["weighted_score"] = sum(aggregate[name] * weight for name, weight in available) / sum(weight for _, weight in available) if available else None
    gates = {"faithfulness": aggregate["faithfulness"], "citation_accuracy": aggregate["citation_accuracy"], "unsupported_claim_rate": mean("unsupported_claim_rate", [row["generation"] for row in successful if row.get("generation")]), "repository_fact_accuracy": aggregate["repository_fact_accuracy"]}
    aggregate["hard_gates"] = {"faithfulness_at_least_0_95": gates["faithfulness"] is not None and gates["faithfulness"] >= .95,
                               "citation_accuracy_at_least_0_98": gates["citation_accuracy"] is not None and gates["citation_accuracy"] >= .98,
                               "unsupported_claim_rate_at_most_0_05": gates["unsupported_claim_rate"] is not None and gates["unsupported_claim_rate"] <= .05,
                               "repository_fact_accuracy_at_least_0_95": gates["repository_fact_accuracy"] is not None and gates["repository_fact_accuracy"] >= .95}
    aggregate["production_ready"] = all(aggregate["hard_gates"].values()) and not specificity_failures
    report = {"repository": "ContribCompass benchmark", "commit_sha": None, "model": model_label,
              "model_version": model_label, "evaluation_version": "1.1",
              "generated_at": datetime.now(timezone.utc).isoformat(), "dataset_size": len(repositories),
              "dataset_sha256": hashlib.sha256((DATASETS / "repositories.json").read_bytes()).hexdigest(),
              "run_mode": "pinned-deterministic" if not args.with_model else "pinned-local-model",
              "metrics": aggregate, "repositories": records, "specificity_failures": specificity_failures,
              "limitations": ["Auto-seeded fact labels require human review.", "Issue and golden-answer labels are not annotated yet.", "Generation metrics are null unless --with-model is used and have no answer gold set.", "Token and peak memory metrics are unavailable from the current Ollama adapter."]}
    REPORTS.mkdir(parents=True, exist_ok=True)
    report_path = REPORTS / "current.json"
    previous = load_json(report_path) if report_path.exists() else None
    report["comparison"] = _comparison(previous, aggregate, report["evaluation_version"])
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    _write_csv(REPORTS / "current.csv", records)
    _write_html(REPORTS / "current.html", report)
    _print_report(report)
    return report


def _comparison(previous: dict | None, current: dict, evaluation_version: str) -> dict:
    if not previous:
        return {"baseline": None, "after": current, "delta": None, "note": "No previous report exists; this run establishes the initial baseline."}
    if previous.get("evaluation_version") != evaluation_version:
        return {"baseline_generated_at": previous.get("generated_at"), "before": previous.get("metrics"), "after": current, "delta": None, "note": "Metric or evaluation methodology changed; this report establishes a new comparable baseline."}
    baseline = previous.get("metrics", {})
    keys = set(baseline) & set(current)
    delta = {key: current[key] - baseline[key] for key in keys if isinstance(current[key], (int, float)) and isinstance(baseline[key], (int, float))}
    return {"baseline_generated_at": previous.get("generated_at"), "before": {key: baseline.get(key) for key in delta}, "after": {key: current.get(key) for key in delta}, "delta": delta}


def _write_csv(path: Path, rows: list[dict]) -> None:
    columns = ["repository", "commit_sha", "fact_accuracy", "important_file_recall", "latency_seconds", "tree_truncated", "error"]
    with path.open("w", newline="", encoding="utf-8") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in columns})


def _write_html(path: Path, report: dict) -> None:
    rows = "".join(f"<tr><td>{html.escape(str(r.get('repository')))}</td><td>{html.escape(str(r.get('commit_sha','')))}</td><td>{r.get('fact_accuracy','')}</td><td>{r.get('important_file_recall','')}</td><td>{r.get('latency_seconds','')}</td><td>{html.escape(str(r.get('error','')))}</td></tr>" for r in report["repositories"])
    body = f"<!doctype html><meta charset='utf-8'><title>ContribCompass Evaluation</title><h1>ContribCompass Evaluation</h1><p>{html.escape(report['run_mode'])} · {report['dataset_size']} pinned repositories</p><pre>{html.escape(json.dumps(report['metrics'], indent=2))}</pre><table><thead><tr><th>Repository</th><th>Commit</th><th>Fact accuracy</th><th>Important-file recall</th><th>Latency</th><th>Error</th></tr></thead><tbody>{rows}</tbody></table><h2>Limitations</h2><ul>" + "".join(f"<li>{html.escape(x)}</li>" for x in report["limitations"]) + "</ul>"
    path.write_text(body, encoding="utf-8")


def _print_report(report: dict) -> None:
    metrics = report["metrics"]
    print(f"Mode: {report['run_mode']} · repositories {metrics['successful_repositories']}/{metrics['repository_count']}")
    for name, value in metrics.items():
        if isinstance(value, float):
            print(f"{name:32} {value:.3f}")
    comp = report["comparison"]
    if comp.get("delta"):
        print("\nBefore → After (delta)")
        for key, delta in sorted(comp["delta"].items()):
            print(f"{key:32} {comp['before'][key]} → {comp['after'][key]} ({delta:+.3f})")
    else:
        print("\n" + comp.get("note", "No comparable baseline exists; this run establishes a baseline."))
    print(f"Production ready: {metrics['production_ready']}")
    print(f"Reports: {REPORTS / 'current.json'}, {REPORTS / 'current.csv'}, {REPORTS / 'current.html'}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--with-model", action="store_true", help="Run the configured local open-weight model for each pinned repository.")
    parser.add_argument("--model", help="Override the configured local model for comparison runs.")
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
