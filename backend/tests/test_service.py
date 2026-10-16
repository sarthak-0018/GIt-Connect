import pytest

from app.analyzer import analyze_profile
from app.service import _deterministic_guide, parse_github_url, rank_issue


@pytest.mark.parametrize("url", ["https://github.com/owner/repo", "https://github.com/owner/repo/", "https://github.com/owner/repo.git"])
def test_parse_github_url(url):
    assert parse_github_url(url) == ("owner", "repo")


@pytest.mark.parametrize("url", ["https://evil.example/owner/repo", "http://github.com/owner/repo", "https://github.com/owner", "https://github.com/owner/repo/issues", "https://github.com/"])
def test_reject_invalid_url(url):
    with pytest.raises(ValueError):
        parse_github_url(url)


def test_beginner_issue_ranks_above_complex_issue():
    beginner = rank_issue({"number": 1, "title": "Add docs", "body": "x" * 120, "labels": [{"name": "good first issue"}], "comments": 0, "html_url": "https://github.com/a/b/issues/1"})
    complex_issue = rank_issue({"number": 2, "title": "Architecture rewrite", "body": "", "labels": [], "comments": 8, "html_url": "https://github.com/a/b/issues/2"})
    assert beginner["score"] > complex_issue["score"]


def test_no_suitable_beginner_issue_does_not_recommend_generic_issue():
    profile = analyze_profile({"owner": {"login": "a"}, "name": "b", "full_name": "a/b", "html_url": "https://github.com/a/b", "default_branch": "main", "language": "Go", "description": None}, ["go.mod"], {"go.mod": "module example.test/a/b\ngo 1.23\n"}, {}, "sha")
    generic = rank_issue({"number": 2, "title": "Investigate broad architecture rewrite", "body": "", "labels": [], "comments": 8, "html_url": "https://github.com/a/b/issues/2"})
    guide = _deterministic_guide(profile, [generic])
    assert guide["recommended_issues"] == []
    assert guide["first_contribution"]["issue_url"] is None
    assert "No issue was recommended" in guide["first_contribution"]["suggestion"]


def test_issue_recommendation_retains_real_github_identity():
    issue = rank_issue({"number": 42, "title": "Add a docs example", "body": "Document how the command works. " * 8, "labels": [{"name": "documentation"}, {"name": "good first issue"}], "comments": 1, "html_url": "https://github.com/a/b/issues/42"})
    assert issue["number"] == 42
    assert issue["url"] == "https://github.com/a/b/issues/42"
    assert issue["score"] >= 25
