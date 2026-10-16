from app.evidence import ranking_metrics, retrieval_metrics, specificity_similarity, validate_claims


def evidence():
    return [{"id": "E1", "source_type": "file", "path": "README.md", "content": "FastAPI service. Run pytest to check the service.", "facts": [{"claim": "Framework FastAPI is used.", "confidence": 0.95}]}]


def test_claim_validation_checks_citations_paths_issue_ids_and_urls():
    result = validate_claims([
        {"claim": "FastAPI is the service framework.", "evidence_ids": ["E1"]},
        {"claim": "The app starts in missing.py.", "evidence_ids": ["E1"]},
        {"claim": "See https://github.com/other/project/issues/9.", "evidence_ids": ["E1"]},
        {"claim": "This is factual.", "evidence_ids": ["NOT_REAL"]},
    ], evidence(), {"README.md", "app/main.py"}, [], {"pytest"}, "owner", "repo")
    statuses = [claim["status"] for claim in result["claims"]]
    assert statuses == ["supported", "contradicted", "contradicted", "unsupported"]
    assert result["metrics"]["faithfulness"] == 0.25
    assert result["metrics"]["unsupported_claim_rate"] == 0.25


def test_retrieval_metrics_include_precision_recall_hit_rate_mrr_and_ndcg():
    result = retrieval_metrics({"README.md": 1.0, "src/app.py": 0.5}, ["README.md"], {"README.md", "src/app.py"}, k=2)
    assert result["context_precision"] == 1.0
    assert round(result["context_recall"], 3) == 0.5
    assert result["hit_rate_at_2"] == 1
    assert result["mrr"] == 1.0
    assert 0 < result["ndcg_at_2"] <= 1


def test_issue_ranking_metrics_and_repository_specificity():
    metrics = ranking_metrics(["10", "20", "30"], {"20", "30"}, k=3)
    assert metrics["precision_at_3"] == 2 / 3
    assert metrics["recall_at_3"] == 1.0
    assert specificity_similarity({"technology_stack": ["Python"], "run_instructions": ["pytest"]}, {"technology_stack": ["TypeScript"], "run_instructions": ["npm run dev"]}) == 0.0
    assert specificity_similarity({"technology_stack": ["Python"]}, {"technology_stack": ["Python"]}) == 1.0
