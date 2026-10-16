"""Deterministic extraction of repository facts from selected GitHub files and tree paths."""
from __future__ import annotations

import json
import re
import tomllib
from pathlib import PurePosixPath


MANIFESTS = {
    "package.json", "pyproject.toml", "requirements.txt", "requirements-dev.txt", "pipfile",
    "cargo.toml", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts", "gemfile",
    "composer.json", "mix.exs", "pubspec.yaml",
}
LOCKS = {"pnpm-lock.yaml", "yarn.lock", "package-lock.json", "npm-shrinkwrap.json", "poetry.lock", "pipfile.lock", "cargo.lock", "gemfile.lock", "composer.lock", "go.sum"}
CANONICAL = {"readme", "readme.md", "readme.rst", "readme.txt", "contributing", "contributing.md", "contributing.rst", "package.json", *MANIFESTS, *LOCKS, "dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml", "makefile", ".env.example"}
CONFIG_NAMES = {"tsconfig.json", "vite.config.ts", "vite.config.js", "next.config.js", "next.config.ts", "webpack.config.js", "pytest.ini", "tox.ini", "setup.cfg", "ruff.toml", ".ruff.toml", "jest.config.js", "vitest.config.ts", "playwright.config.ts", "cargo.toml", "go.work", "justfile", "meson.build"}
SKIP_DIRS = {"node_modules", "vendor", "dist", "build", "coverage", ".next", "target", "__pycache__", ".venv", "venv", "site-packages", "generated"}
IMPORTANT_DIRS = {"src", "app", "lib", "packages", "components", "services", "api", "backend", "frontend", "tests", "test", "__tests__", "docs", "documentation", "examples", "scripts", "config", "cmd", "internal", "crates"}
FRAMEWORKS = {
    "react": "React", "next": "Next.js", "vue": "Vue", "svelte": "Svelte", "angular": "Angular",
    "@angular/core": "Angular", "express": "Express", "fastify": "Fastify", "nestjs": "NestJS", "vite": "Vite",
    "@nestjs/core": "NestJS", "fastapi": "FastAPI", "django": "Django", "flask": "Flask",
    "pytest": "pytest", "spring-boot-starter": "Spring Boot", "org.springframework.boot": "Spring Boot",
    "axum": "Axum", "actix-web": "Actix Web", "github.com/gin-gonic/gin": "Gin", "github.com/labstack/echo": "Echo",
}


def is_sensitive_path(path: str) -> bool:
    parts = PurePosixPath(path).parts
    for part in parts:
        low = part.lower()
        if low in {".env", ".env.local", ".env.production", ".env.development"}:
            return True
        if low.startswith(".env.") and low not in {".env.example", ".env.sample", ".env.template"}:
            return True
        if low.endswith((".pem", ".key", ".p12", ".pfx")) or low in {"credentials", "credentials.json", "secrets", "secret.json", "id_rsa", "id_ed25519"} or "credential" in low or "secret" in low:
            return True
    return False


def file_priority(path: str) -> int:
    """High score means fetch first; independent of GitHub tree ordering."""
    normalized = path.lower()
    name = PurePosixPath(normalized).name
    if is_sensitive_path(path):
        return -1
    if name.startswith("readme"):
        return 1200 if "/" not in normalized else 1080
    if name.startswith("contributing"):
        return 1180 if "/" not in normalized else 1070
    if name in MANIFESTS:
        return 1000
    if name in LOCKS:
        return 900
    if name in {"dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml", "makefile", ".env.example", "justfile"}:
        return 850
    if normalized.startswith(".github/workflows/"):
        return 820
    if name in CONFIG_NAMES:
        return 780
    if any(part in {"docs", "documentation"} for part in PurePosixPath(normalized).parts) and name.endswith((".md", ".rst", ".txt")):
        return 720
    if re.search(r"(^|/)(tests?|__tests__)/", normalized) or name in {"pytest.ini", "tox.ini", "vitest.config.ts", "jest.config.js"}:
        return 700
    if name in {"main.py", "main.go", "main.rs", "main.java", "app.py", "server.py", "index.ts", "index.tsx", "main.ts", "main.tsx", "src/lib.rs"}:
        return 650
    if any(part in IMPORTANT_DIRS for part in PurePosixPath(normalized).parts) and name.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".java", ".kt", ".rb", ".php")):
        return 300
    return 0


def select_important_paths(paths: list[str], limit: int = 24) -> list[str]:
    eligible = [p for p in paths if file_priority(p) > 0 and not any(part.lower() in SKIP_DIRS for part in PurePosixPath(p).parts)]
    return sorted(set(eligible), key=lambda p: (-file_priority(p), p.lower()))[:limit]


def _parse_json(value: str) -> dict:
    try:
        result = json.loads(value)
        return result if isinstance(result, dict) else {}
    except (ValueError, TypeError):
        return {}


def _parse_toml(value: str) -> dict:
    try:
        return tomllib.loads(value)
    except (tomllib.TOMLDecodeError, TypeError):
        return {}


def analyze_readme(path: str | None, content: str) -> dict:
    if not path or not content.strip():
        return {"status": "README not found", "path": None, "length": 0, "purpose": None, "features": [], "target_users": [], "prerequisites": [], "installation": [], "development_setup": [], "run_commands": [], "build_commands": [], "test_commands": [], "architecture": [], "workflow": [], "contribution": [], "documentation_links": [], "sections": []}
    sections: list[tuple[str, list[str]]] = []
    current = "introduction"
    lines: list[str] = []
    for line in content.splitlines():
        match = re.match(r"^#{1,4}\s+(.+?)\s*#*\s*$", line)
        if match:
            if lines:
                sections.append((current, lines))
            current, lines = match.group(1).strip(), []
        else:
            lines.append(line)
    if lines:
        sections.append((current, lines))
    text_by_heading = {h.lower(): "\n".join(body).strip() for h, body in sections}
    all_text = content

    def matching(terms: tuple[str, ...]) -> list[str]:
        return [f"{heading}: {text[:1800]}" for heading, text in text_by_heading.items() if any(term in heading for term in terms) and text]

    fenced = re.findall(r"```([^\n`]*)\n(.*?)```", content, re.S)
    command_blocks: list[str] = []
    for language, block in fenced:
        code = block.strip()
        if code and (re.search(r"(^|\n)\s*(npm|pnpm|yarn|bun|pip|uv|poetry|python|pytest|cargo|go |mvn|gradle|docker|make|just|cmake|composer|bundle|mix )", code, re.I) or "install" in language.lower()):
            command_blocks.append((language.strip() + "\n" + code).strip()[:1800])
    features = []
    for heading, body in sections:
        if any(x in heading.lower() for x in ("feature", "what it does", "capabilities")):
            features.extend(re.findall(r"^\s*[-*+]\s+(.+)$", "\n".join(body), re.M)[:12])
    purpose = next((line.strip(" #>*") for line in content.splitlines() if line.strip() and not line.startswith(("#", "![", "[!", "<!--"))), None)
    links = list(dict.fromkeys(re.findall(r"\[[^\]]+\]\((https?://[^)]+|[^)#]+\.md(?:#[^)]*)?)\)", content)))[:20]
    return {
        "status": "found", "path": path, "length": len(content), "purpose": purpose[:700] if purpose else None,
        "features": features[:12], "target_users": matching(("audience", "for whom", "who is", "target user")),
        "prerequisites": matching(("prerequisite", "requirements", "dependencies", "before you begin")),
        "installation": matching(("install", "getting started", "quick start", "setup")),
        "development_setup": matching(("develop", "contributing", "local setup", "setup")),
        "run_commands": matching(("usage", "run", "start", "develop", "quick start")),
        "build_commands": matching(("build", "compile", "production")),
        "test_commands": matching(("test", "quality", "check")),
        "architecture": matching(("architecture", "structure", "design", "overview")),
        "workflow": matching(("workflow", "develop", "release")),
        "contribution": matching(("contribut", "pull request", "development")),
        "documentation_links": links, "sections": [h for h, _ in sections],
        "command_blocks": command_blocks[:16], "text_excerpt": all_text[:10000],
    }


def analyze_contributing(path: str | None, content: str) -> dict:
    if not path or not content.strip():
        return {"status": "CONTRIBUTING not found", "path": None, "length": 0, "setup": [], "workflow": [], "branches": [], "commits": [], "tests": [], "lint_format": [], "pull_requests": [], "review": [], "issue_guidance": [], "sections": []}
    sections: list[tuple[str, str]] = []
    heading, body = "introduction", []
    for line in content.splitlines():
        match = re.match(r"^#{1,4}\s+(.+?)\s*#*\s*$", line)
        if match:
            sections.append((heading, "\n".join(body).strip()))
            heading, body = match.group(1).strip(), []
        else:
            body.append(line)
    sections.append((heading, "\n".join(body).strip()))
    categories = {"setup": ("setup", "install", "environment", "prerequisite"), "workflow": ("workflow", "develop", "development", "process"), "branches": ("branch",), "commits": ("commit",), "tests": ("test",), "lint_format": ("lint", "format", "style", "quality"), "pull_requests": ("pull request", "pr ", "submit", "merge"), "review": ("review", "maintainer"), "issue_guidance": ("issue", "good first", "beginner", "triage")}
    extracted = {key: [f"{h}: {text[:1800]}" for h, text in sections if any(term in h.lower() for term in terms) and text][:8] for key, terms in categories.items()}
    return {"status": "found", "path": path, "length": len(content), **extracted, "sections": [h for h, _ in sections], "text_excerpt": content[:10000]}


def analyze_profile(repository: dict, paths: list[str], files: dict[str, str], languages: dict, commit_sha: str | None) -> dict:
    basename = {PurePosixPath(p).name.lower(): p for p in files}
    readme_path = next((p for p in files if PurePosixPath(p).name.lower() in {"readme.md", "readme", "readme.rst", "readme.txt"}), None)
    contributing_path = next((p for p in files if PurePosixPath(p).name.lower() in {"contributing.md", "contributing", "contributing.rst"}), None)
    readme = analyze_readme(readme_path, files.get(readme_path, "") if readme_path else "")
    contributing = analyze_contributing(contributing_path, files.get(contributing_path, "") if contributing_path else "")
    manifests = {p: files[p] for p in files if PurePosixPath(p).name.lower() in MANIFESTS}
    deps: dict[str, list[str]] = {}
    scripts: dict[str, str] = {}
    runtime: list[str] = []
    packages: set[str] = set()
    package_manager = None
    frameworks: set[str] = set()
    install_commands: list[str] = []
    test_commands: list[str] = []
    build_commands: list[str] = []
    lint_commands: list[str] = []
    for path, raw in manifests.items():
        name = PurePosixPath(path).name.lower()
        if name == "package.json":
            data = _parse_json(raw)
            all_deps = {**data.get("dependencies", {}), **data.get("devDependencies", {})}
            deps[path] = sorted(all_deps)[:120]
            packages.update(all_deps)
            engines = data.get("engines", {})
            if engines.get("node"):
                runtime.append(f"Node.js {engines['node']} (package.json engines.node)")
            pm = data.get("packageManager")
            if pm:
                package_manager = pm.split("@")[0]
            scripts.update({str(k): str(v) for k, v in data.get("scripts", {}).items() if isinstance(v, (str, int, float))})
            install_commands.append(f"{package_manager or 'npm'} install (from {path})")
        elif name == "pyproject.toml":
            data = _parse_toml(raw)
            project = data.get("project", {})
            poetry = data.get("tool", {}).get("poetry", {})
            python_req = project.get("requires-python") or poetry.get("dependencies", {}).get("python")
            if python_req:
                runtime.append(f"Python {python_req} ({path})")
            pydeps = [x.split("[", 1)[0].split("<", 1)[0].split(">", 1)[0].split("=", 1)[0].strip() for x in project.get("dependencies", []) if isinstance(x, str)]
            pydeps.extend(poetry.get("dependencies", {}).keys())
            deps[path] = sorted(set(pydeps))[:120]
            packages.update(x.lower().replace("_", "-") for x in pydeps)
            if poetry:
                package_manager = package_manager or "poetry"
            scripts.update({f"python:{k}": str(v) for k, v in project.get("scripts", {}).items()})
            if "poetry" in files:
                package_manager = package_manager or "poetry"
            install_commands.append("poetry install" if package_manager == "poetry" else "python -m pip install -e .")
        elif name.startswith("requirements") or name == "pipfile":
            raw_deps = re.findall(r"^\s*([A-Za-z0-9_.-]+)", raw, re.M)
            deps[path] = raw_deps[:120]
            packages.update(x.lower().replace("_", "-") for x in raw_deps)
            runtime.append("Python (requirements manifest; version not specified)")
            if name == "pipfile":
                package_manager = package_manager or "pipenv"
                install_commands.append("pipenv install")
            elif "uv.lock" in basename:
                package_manager = package_manager or "uv"
                install_commands.append("uv pip install -r requirements.txt")
            else:
                package_manager = package_manager or "pip"
                install_commands.append("python -m pip install -r " + PurePosixPath(path).name)
        elif name == "cargo.toml":
            data = _parse_toml(raw)
            cargo = data.get("package", {})
            runtime.append("Rust" + (f" {cargo['rust-version']}+" if cargo.get("rust-version") else " (version not specified)"))
            deps[path] = sorted(data.get("dependencies", {}))[:120]
            packages.update(deps[path])
            package_manager = package_manager or "cargo"
            install_commands.append("cargo build (Cargo.toml)")
        elif name == "go.mod":
            module = re.search(r"(?m)^module\s+(\S+)", raw)
            version = re.search(r"(?m)^go\s+(\S+)", raw)
            runtime.append(f"Go {version.group(1)}" if version else "Go (version not specified)")
            deps[path] = re.findall(r"(?m)^\s*([^\s()]+)\s+v[\w.+-]+", raw)[:120]
            package_manager = package_manager or "Go modules"
            install_commands.append("go mod download (go.mod)")
        elif name == "pom.xml":
            version = re.search(r"<maven.compiler.(?:release|source)>([^<]+)", raw)
            runtime.append(f"Java {version.group(1)} (pom.xml)" if version else "Java (version not explicitly found in pom.xml)")
            package_manager = package_manager or "Maven"
            deps[path] = re.findall(r"<artifactId>([^<]+)</artifactId>", raw)[:120]
            install_commands.append("mvn install (pom.xml)")
        elif name in {"build.gradle", "build.gradle.kts"}:
            version = re.search(r"(?:sourceCompatibility|JavaVersion\.VERSION_)(?:\s*=\s*|\s*\.)([\w.]+)", raw)
            runtime.append(f"Java {version.group(1)} (Gradle)" if version else "Java (version not explicitly found in Gradle file)")
            package_manager = package_manager or "Gradle"
            deps[path] = re.findall(r"(?:implementation|api|testImplementation)\s*\(?\s*['\"]([^'\"]+)", raw)[:120]
            install_commands.append("./gradlew build (Gradle manifest)")
        else:
            runtime.append({"gemfile": "Ruby", "composer.json": "PHP"}.get(name, ""))
            package_manager = package_manager or {"gemfile": "Bundler", "composer.json": "Composer"}.get(name)
            install_commands.append({"gemfile": "bundle install", "composer.json": "composer install"}.get(name, ""))
    lock_names = {PurePosixPath(p).name.lower() for p in paths}
    for lock, manager in (("pnpm-lock.yaml", "pnpm"), ("yarn.lock", "yarn"), ("package-lock.json", "npm"), ("npm-shrinkwrap.json", "npm"), ("poetry.lock", "poetry"), ("pipfile.lock", "pipenv"), ("cargo.lock", "cargo"), ("go.sum", "Go modules"), ("gemfile.lock", "Bundler"), ("composer.lock", "Composer")):
        if lock in lock_names:
            package_manager = manager
            break
    if "package.json" in basename:
        install_commands = [f"{package_manager or 'npm'} install (from package.json)"]
    if "test" in scripts:
        test_commands.append("npm run test")
    if "build" in scripts:
        build_commands.append("npm run build")
    if "lint" in scripts:
        lint_commands.append("npm run lint")
    if "check" in scripts:
        lint_commands.append("npm run check")
    if "pyproject.toml" in basename:
        data = _parse_toml(files[basename["pyproject.toml"]])
        project_scripts = data.get("project", {}).get("scripts", {})
        test_config = data.get("tool", {}).get("pytest", {})
        if "pytest" in packages or test_config or any("pytest" in p.lower() for p in paths):
            test_commands.append("pytest (pytest detected in project dependencies or files)")
        tool_scripts = data.get("tool", {}).get("poetry", {}).get("scripts", {})
        scripts.update({f"poetry:{k}": str(v) for k, v in tool_scripts.items()})
    if "cargo.toml" in basename:
        test_commands.append("cargo test (Cargo.toml)")
    if "go.mod" in basename:
        test_commands.append("go test ./... (go.mod)")
    if "pom.xml" in basename:
        test_commands.append("mvn test (pom.xml)")
    if any(PurePosixPath(p).name.lower().startswith("build.gradle") for p in manifests):
        test_commands.append("./gradlew test (Gradle manifest)")
    for package in packages:
        if package in FRAMEWORKS:
            frameworks.add(FRAMEWORKS[package])
        elif package.startswith("spring-boot-starter"):
            frameworks.add("Spring Boot")
    path_set = set(paths)
    dirs: dict[str, list[str]] = {}
    for path in paths:
        parts = PurePosixPath(path).parts
        for index, part in enumerate(parts[:-1]):
            if part.lower() in IMPORTANT_DIRS:
                dirname = "/".join(parts[:index + 1])
                dirs.setdefault(dirname, []).append(path)
        if len(parts) == 1 and parts[0].lower() in IMPORTANT_DIRS:
            dirs.setdefault(parts[0], [])
    key_dirs = [{"path": name + "/", "file_count": len(items), "sample_files": items[:5]} for name, items in sorted(dirs.items(), key=lambda kv: (-len(kv[1]), kv[0].lower()))]
    entry_points: list[dict] = []
    candidates = ["src/main.rs", "src/lib.rs", "src/main.go", "main.go", "cmd/main.go", "src/main.py", "app/main.py", "main.py", "app.py", "server.py", "src/index.tsx", "src/main.tsx", "src/index.ts", "src/main.ts", "src/index.js", "src/main.js", "index.js", "main.js", "src/main/java", "src/main/kotlin"]
    for candidate in candidates:
        exact = [p for p in paths if p.lower() == candidate or (candidate.endswith("java") or candidate.endswith("kotlin")) and p.lower().startswith(candidate + "/")]
        for p in exact[:3]:
            entry_points.append({"path": p, "basis": "conventional entry-point path exists in Git tree"})
    conventional_names = {"main.go", "main.rs", "main.py", "main.tsx", "main.ts", "main.js", "index.tsx", "index.ts", "index.js", "app.py", "server.py"}
    for p in paths:
        if PurePosixPath(p).name.lower() in conventional_names and p not in {x["path"] for x in entry_points}:
            entry_points.append({"path": p, "basis": "conventional entry-point filename exists in Git tree"})
    package_json = _parse_json(files.get(basename.get("package.json", ""), ""))
    for key in ("main", "module", "browser"):
        target = package_json.get(key)
        if isinstance(target, str) and target in path_set and target not in {x["path"] for x in entry_points}:
            entry_points.insert(0, {"path": target, "basis": f"package.json {key} field"})
    for script_name in ("start", "dev", "serve"):
        command = scripts.get(script_name)
        if command:
            for p in paths:
                if PurePosixPath(p).name in command and p not in {x["path"] for x in entry_points}:
                    entry_points.append({"path": p, "basis": f"package script '{script_name}' references file"})
    docker_files = {p: files[p][:4000] for p in files if PurePosixPath(p).name.lower() in {"dockerfile", "docker-compose.yml", "docker-compose.yaml", "compose.yml", "compose.yaml"}}
    ci_files = {p: files[p][:3500] for p in files if p.startswith(".github/workflows/")}
    env_path = next((p for p in files if PurePosixPath(p).name.lower() in {".env.example", ".env.sample", ".env.template"}), None)
    env_vars = re.findall(r"(?m)^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", files.get(env_path, "")) if env_path else []
    tests = [p for p in paths if re.search(r"(^|/)(tests?|__tests__)/|(^|/)(test_|.*_test\.)", p.lower())]
    doc_paths = [p for p in paths if ("docs" in PurePosixPath(p).parts or "documentation" in PurePosixPath(p).parts) and p.lower().endswith((".md", ".rst", ".txt"))]
    manifests_found = sorted(manifests)
    important_files = sorted(files, key=lambda p: (-file_priority(p), p.lower()))
    detected_languages = sorted(languages, key=lambda k: (-languages[k], k)) if languages else sorted({".py":"Python", ".ts":"TypeScript", ".tsx":"TypeScript", ".js":"JavaScript", ".jsx":"JavaScript", ".go":"Go", ".rs":"Rust", ".java":"Java", ".rb":"Ruby", ".php":"PHP"}.get(PurePosixPath(p).suffix.lower()) for p in paths if PurePosixPath(p).suffix.lower() in {".py", ".ts", ".tsx", ".js", ".jsx", ".go", ".rs", ".java", ".rb", ".php"})
    detected_languages = [x for x in detected_languages if x]
    if repository.get("language") and repository["language"] not in detected_languages:
        detected_languages.insert(0, repository["language"])
    return {
        "repository": {"owner": repository.get("owner", {}).get("login"), "name": repository.get("name"), "full_name": repository.get("full_name"), "url": repository.get("html_url"), "default_branch": repository.get("default_branch"), "commit_sha": commit_sha, "description": repository.get("description"), "primary_language": repository.get("language")},
        "detected_languages": detected_languages, "frameworks": sorted(frameworks), "runtime": list(dict.fromkeys(x for x in runtime if x)), "package_manager": package_manager,
        "dependency_manifests": manifests_found, "dependencies": deps, "scripts": scripts, "install_commands": list(dict.fromkeys(x for x in install_commands if x)), "build_commands": list(dict.fromkeys(build_commands)), "test_commands": list(dict.fromkeys(test_commands)), "lint_format_commands": list(dict.fromkeys(lint_commands)),
        "important_files": important_files, "important_directories": key_dirs, "entry_points": entry_points[:20], "environment_variables": {"source": env_path, "names": sorted(set(env_vars))},
        "docker_configuration": docker_files, "ci_configuration": ci_files, "test_structure": tests[:60], "documentation_structure": doc_paths[:60], "readme_analysis": readme, "contributing_analysis": contributing,
    }


def repository_fingerprint(profile: dict, issue_count: int) -> dict:
    repo = profile["repository"]
    fingerprint = {"repository": repo.get("full_name"), "commit_sha": repo.get("commit_sha"), "language": repo.get("primary_language"), "frameworks": profile["frameworks"], "package_manager": profile["package_manager"], "has_readme": profile["readme_analysis"]["status"] == "found", "has_contributing": profile["contributing_analysis"]["status"] == "found", "has_tests": bool(profile["test_structure"] or profile["test_commands"]), "has_docker": bool(profile["docker_configuration"]), "important_files": profile["important_files"], "issue_count": issue_count}
    return fingerprint
