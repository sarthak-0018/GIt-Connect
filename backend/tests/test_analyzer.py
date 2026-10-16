from app.analyzer import analyze_profile, analyze_readme, repository_fingerprint, select_important_paths


def repository(language=None):
    return {"owner": {"login": "sample"}, "name": "project", "full_name": "sample/project", "html_url": "https://github.com/sample/project", "default_branch": "main", "description": None, "language": language}


def test_file_selection_prioritizes_docs_and_is_independent_of_tree_order():
    paths = ["z-last/src/app.ts", "README.md", "vendor/README.md", "package.json", "CONTRIBUTING.md", ".env", ".env.example", "dist/bundle.js"]
    first = select_important_paths(paths, 10)
    second = select_important_paths(list(reversed(paths)), 10)
    assert first == second
    assert first[:3] == ["README.md", "CONTRIBUTING.md", "package.json"]
    assert ".env" not in first
    assert "vendor/README.md" not in first
    assert ".env.example" in first


def test_javascript_profile_extracts_framework_scripts_runtime_and_tests():
    paths = ["README.md", "package.json", "pnpm-lock.yaml", "src/main.tsx", "src/components/App.tsx", "tests/App.test.tsx", ".github/workflows/ci.yml"]
    files = {
        "README.md": "# Project Atlas\nA TypeScript dashboard for visualizing climate data.\n\n## Installation\nRun `pnpm install`.\n\n## Development\nRun `pnpm dev`.\n\n## Tests\nRun `pnpm test`.\n",
        "package.json": '{"packageManager":"pnpm@9.0.0","engines":{"node":">=20"},"dependencies":{"react":"^19","vite":"^6"},"scripts":{"dev":"vite --host","build":"vite build","test":"vitest run"}}',
        ".github/workflows/ci.yml": "name: ci\n",
    }
    profile = analyze_profile(repository("TypeScript"), paths, files, {"TypeScript": 5000}, "sha-js")
    assert profile["package_manager"] == "pnpm"
    assert "Node.js >=20 (package.json engines.node)" in profile["runtime"]
    assert "React" in profile["frameworks"]
    assert profile["scripts"]["dev"] == "vite --host"
    assert profile["readme_analysis"]["purpose"].startswith("A TypeScript dashboard")
    assert "tests/App.test.tsx" in profile["test_structure"]
    assert profile["entry_points"][0]["path"] == "src/main.tsx"


def test_python_profile_extracts_runtime_framework_and_contribution_rules():
    paths = ["pyproject.toml", "requirements.txt", "CONTRIBUTING.md", "app/main.py", "tests/test_api.py", "docs/index.md", "Dockerfile"]
    files = {
        "pyproject.toml": '[project]\nname="weather-api"\nrequires-python=">=3.11"\ndependencies=["fastapi>=0.110", "pytest"]\n',
        "requirements.txt": "fastapi\npytest\n",
        "CONTRIBUTING.md": "# Contributing\n\n## Tests\nRun pytest before opening a PR.\n\n## Branches\nUse a short feature branch.\n",
        "Dockerfile": "FROM python:3.11\n",
    }
    profile = analyze_profile(repository("Python"), paths, files, {"Python": 9000}, "sha-py")
    assert "Python >=3.11 (pyproject.toml)" in profile["runtime"]
    assert "FastAPI" in profile["frameworks"]
    assert profile["contributing_analysis"]["status"] == "found"
    assert profile["contributing_analysis"]["tests"]
    assert profile["docker_configuration"]
    assert "tests/test_api.py" in profile["test_structure"]


def test_rust_profile_is_distinct_and_detects_cargo_tests():
    paths = ["Cargo.toml", "Cargo.lock", "src/main.rs", "src/lib.rs", "tests/cli.rs"]
    files = {"Cargo.toml": '[package]\nname="cli-tool"\nrust-version="1.78"\n[dependencies]\nclap="4"\n'}
    profile = analyze_profile(repository("Rust"), paths, files, {"Rust": 8000}, "sha-rs")
    assert profile["package_manager"] == "cargo"
    assert any(x.startswith("Rust 1.78") for x in profile["runtime"])
    assert profile["test_structure"] == ["tests/cli.rs"]
    assert "cargo test (Cargo.toml)" in profile["test_commands"]


def test_missing_readme_and_contributing_are_explicit():
    profile = analyze_profile(repository("Go"), ["go.mod", "cmd/server/main.go"], {"go.mod": "module example.test/service\ngo 1.23\n"}, {}, "sha-go")
    assert profile["readme_analysis"]["status"] == "README not found"
    assert profile["contributing_analysis"]["status"] == "CONTRIBUTING not found"
    assert profile["package_manager"] == "Go modules"
    assert profile["entry_points"][0]["path"] == "cmd/server/main.go"


def test_fingerprint_changes_across_unrelated_profiles():
    js = analyze_profile(repository("TypeScript"), ["package.json", "src/main.tsx"], {"package.json": '{"dependencies":{"react":"19"}}'}, {"TypeScript": 1000}, "js-sha")
    py = analyze_profile(repository("Python"), ["pyproject.toml", "app/main.py"], {"pyproject.toml": '[project]\nname="api"\nrequires-python=">=3.12"\ndependencies=["fastapi"]'}, {"Python": 1000}, "py-sha")
    assert repository_fingerprint(js, 0) != repository_fingerprint(py, 0)


def test_readme_parser_extracts_features_commands_and_links():
    readme = analyze_readme("README.md", "# Demo\nA useful tool.\n\n## Features\n- Fast search\n- Local mode\n\n## Quick Start\n```sh\nnpm install\nnpm run dev\n```\nSee [docs](docs/guide.md).\n")
    assert readme["status"] == "found"
    assert len(readme["features"]) == 2
    assert readme["command_blocks"]
    assert readme["documentation_links"]
