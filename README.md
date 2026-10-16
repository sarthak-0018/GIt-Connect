# ContribCompass

> **ContribCompass is an open-source, self-hostable AI assistant that analyzes public GitHub repositories and generates evidence-backed onboarding guides for new contributors. It combines GitHub repository analysis, deterministic issue ranking, and open-weight LLM reasoning to help developers understand unfamiliar open-source projects and identify a practical first contribution.**

**Find your way into any open-source project.**

## Status

The MVP validates public GitHub repository URLs, explicitly prioritizes canonical docs and manifests independent of tree order, builds a deterministic repository profile, ranks real open issues, and renders an onboarding brief. Ollama and vLLM use the same evidence context. Redis caching is optional and keyed by repository, commit, analyzer version, model/provider, and issue update stamps; PostgreSQL is provisioned for future guide history but is not yet used.

## Features

- Evidence-first public repository inspection; no arbitrary URL fetching.
- Bounded Git tree and prioritized file inspection, with sensitive paths excluded.
- Deterministic README/CONTRIBUTING, framework/runtime, script, directory, entry-point, CI, Docker, test, and environment-variable extraction.
- Repository fingerprint, profile, and context-size diagnostics in the `analysis` response.
- Beginner issue ranking from labels, description detail, and scope signals. Pull requests are excluded.
- Open-weight Qwen3 reasoning through local Ollama by default, with local vLLM as an alternative.
- Structured evidence, claim checks, and measurable retrieval/generation evaluation.
- Responsive UI with source links and uncertainty notes.

## Architecture

```text
Browser → React/Vite (Nginx) → FastAPI → GitHub REST API
                                      ├→ Ollama + Qwen3 (default)
                                      └→ vLLM + open-weight models (optional)
                         Compose also provisions PostgreSQL and Redis
```

## Quick start with Docker

Requirements: Docker Compose v2, Ollama installed on the host, and enough memory/disk for Qwen3. CPU inference works but can be slow.

```bash
cp .env.example .env
ollama pull qwen3:latest
ollama serve
docker compose up --build -d
```

Open [http://localhost:8080](http://localhost:8080). Configure `APP_PORT` to change the published port. Compose connects to host Ollama through `host.docker.internal`; the model is local and no proprietary LLM API key is required. A GitHub token is optional and increases GitHub's API quota.

## Local development

Use Node.js 22 LTS (and Python 3.12+) for the local development steps below. Run the backend and frontend in separate terminals.

### Backend

```bash
cd backend
python -m venv .venv
# Windows PowerShell:
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload --reload-dir app
```

### Frontend

```bash
cd frontend
npm ci
npm run dev
```

Open the Vite URL printed in the frontend terminal (normally `http://localhost:5173`). Vite proxies `/api` to the backend on port 8000. If Windows reports `EFTYPE` or `EPERM`, stop the Vite process, close any terminals/editors holding files in `frontend/node_modules`, then run `npm ci` from `frontend` to restore exactly the lockfile dependencies. If the npm cache itself reports access errors, use a project-local cache: `npm ci --cache .npm-cache`. For esbuild errors, use Node.js 22 LTS. Run Ollama natively, pull the configured model with `ollama pull qwen3:latest`, and set `OLLAMA_BASE_URL` to `http://localhost:11434` for inference.

## Ollama development inference

Install [Ollama](https://ollama.com), then run:

```bash
ollama pull qwen3:latest
ollama serve
```

The default config uses `LLM_PROVIDER=ollama`, `OLLAMA_BASE_URL=http://host.docker.internal:11434` in Compose, and `OLLAMA_MODEL=qwen3:latest`. For a backend running directly on the host, set the base URL to `http://localhost:11434`. Qwen3 thinking is disabled by default for lower latency; `OLLAMA_NUM_PREDICT` bounds generation length, and the model generates only a concise summary while the analyzer builds the factual guide. If local Ollama is slow or unavailable, the API still returns its deterministic repository analysis with a warning. Set `LLM_PROVIDER=none` to intentionally skip model generation.

## Production with vLLM

The production compose file expects a compatible NVIDIA GPU runtime and a model that fits the available VRAM. Set a secure `POSTGRES_PASSWORD`, review `VLLM_MODEL`, then:

```bash
docker compose -f docker-compose.prod.yml up --build -d
```

The default model identifier is `Qwen/Qwen3-4B-Instruct-2507`; verify its current model-card license and vLLM compatibility before use. Larger weights require appropriately larger GPU memory. This configuration does not include a domain-specific TLS proxy; place Caddy or another HTTPS reverse proxy in front of the published frontend before exposing the service publicly. Do not expose the vLLM port or database to the internet.

## API

- `GET /api/health`
- `GET /api/health/github` (safe auth and GitHub rate-limit diagnostics; token is never returned)
- `POST /api/analyze` with `{"url":"https://github.com/owner/repository"}`

The URL parser accepts only HTTPS `github.com` owner/repository paths. Public repositories only. `/docs` exposes the local OpenAPI documentation.

`POST /api/analyze` also returns `analysis.profile`, `analysis.fingerprint`, `analysis.context_size`, `analysis.files_inspected`, and `analysis.cache_hit` for diagnosis. File contents sent to inference are bounded by `MAX_CONTEXT_SIZE`; canonical files are selected by importance, not Git tree order. Issue recommendations remain deterministic and are limited to issues returned by GitHub.

API failures use `{"error":{"code":"...","message":"...","retryable":false,"request_id":"..."}}` and include the request ID in the `X-Request-ID` response header. Unexpected exceptions are logged server-side with their traceback and request ID; stack traces and internal exception text are not returned to clients.

## Configuration

| Variable | Purpose | Default |
|---|---|---|
| `APP_PORT` | Host port for frontend | `8080` |
| `GITHUB_TOKEN` | Optional server-side GitHub token; never sent to browser | unset |
| `LLM_PROVIDER` | `ollama`, `vllm`, or `none` (deterministic only) | `ollama` |
| `OLLAMA_BASE_URL` | Ollama endpoint | `http://host.docker.internal:11434` in Compose |
| `OLLAMA_MODEL` | Ollama model name | `qwen3:latest` |
| `OLLAMA_THINKING` | Enable Qwen3 reasoning mode | `false` |
| `OLLAMA_NUM_PREDICT` | Maximum generated tokens per Ollama response | `128` |
| `LLM_TEMPERATURE` | Model sampling temperature | `0.6` |
| `LLM_TOP_P` | Model nucleus sampling | `0.95` |
| `LLM_TOP_K` | Model top-k sampling | `20` |
| `VLLM_BASE_URL` | OpenAI-compatible vLLM endpoint | `http://vllm:8000/v1` in production Compose |
| `VLLM_MODEL` | vLLM model identifier | `Qwen/Qwen3-4B-Instruct-2507` |
| `GITHUB_TIMEOUT_SECONDS` | GitHub request timeout | `15` |
| `LLM_TIMEOUT_SECONDS` | Inference timeout | `20` |
| `MAX_ISSUES` | Maximum issue API page size | `30` |
| `MAX_IMPORTANT_FILES` | Maximum prioritized files fetched for analysis | `24` |
| `MAX_FILE_SIZE` | Maximum bytes retained from an inspected file | `200000` |
| `MAX_README_SIZE` | Maximum bytes retained from a README | `100000` |
| `MAX_CONTEXT_SIZE` | Maximum serialized profile/evidence context sent to inference | `8000` |
| `MAX_TREE_ENTRIES` | Maximum tree entries considered after GitHub returns the tree | `50000` |
| `MAX_GITHUB_REQUESTS_PER_ANALYSIS` | Upper bound on REST calls made for one uncached analysis | `32` |
| `MAX_ANALYSIS_TIME` | Maximum seconds for one analysis | `120` |
| `ANALYZE_RATE_LIMIT_PER_IP` | Analyze misses allowed per IP per window | `10` |
| `ANALYZE_RATE_LIMIT_WINDOW` | Application rate-limit window in seconds | `3600` |
| `MAX_CONCURRENT_ANALYSES` | Maximum simultaneous analyses per backend worker | `4` |
| `ANALYSIS_CACHE_FRESH_SECONDS` | Cache-first response window before commit refresh | `60` |
| `ANALYSIS_CACHE_TTL_SECONDS` | Commit-keyed analysis and guide retention | `600` |
| `REDIS_URL` | Shared analysis, GitHub response, deduplication, and rate-limit cache | `redis://localhost:6379/0` in local `.env.example`; Compose service URL in containers |
| `POSTGRES_PASSWORD` | Compose database password | development placeholder; replace for deployment |

## GitHub API, privacy, and security

ContribCompass sends repository requests to GitHub's public REST API. Configure a server-side `GITHUB_TOKEN` for hosted deployments; the token is read only by the backend and never returned to the browser. Analysis checks Redis before GitHub, uses a shared in-progress lock, validates cached results against the default-branch commit after a short freshness window, and stores conditional request validators and responses. It fetches repository metadata, one tree, selected high-value files, and a bounded issue page; it never fetches every repository file or comments. Paths resembling env files, credentials, keys, or certificates are excluded. GitHub primary and secondary limit responses include retry guidance; application rate limiting and a concurrency cap protect the service.

Redis backs cache and deduplication when configured. Without Redis, only a process-local concurrency and rate-limit fallback is available and cache coordination is unavailable; use Redis when running multiple workers or serving a hosted instance. Public hosting still consumes infrastructure resources, and GPU inference can be costly.

## Tests

```bash
cd backend
pip install -r requirements.txt
pip install pytest
pytest
```

Frontend production build: `cd frontend && npm install && npm run build`.
Frontend API response tests: `cd frontend && npm test` (Node.js 20+).

## Accuracy evaluation

The pinned benchmark and deterministic analysis runner live in [`evaluation/`](evaluation/README.md). Run it from the repository root with `python -m evaluation.runners.run_evaluation`. Add `--with-model` to evaluate the configured local Ollama or vLLM model. Each run writes JSON, CSV, and HTML reports under `evaluation/reports/` and compares aggregate metrics with the preceding report. The initial 20-repository facts were seeded from pinned GitHub metadata and trees; they are explicitly marked as needing human review. Issue relevance and golden answers are still unannotated, so the report leaves those accuracy metrics unavailable and cannot declare production readiness.

## Licenses

ContribCompass source is Apache-2.0. Runtime dependency licenses are summarized in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). The referenced model is not bundled; model terms vary by exact model and version. Verify its model card/license before redistribution or commercial use. GitHub is an external service with its own terms.

## Limitations and roadmap

The analyzer chooses files by path and provides evidence cards; model prose is schema-filtered and evidence IDs are checked against fetched content. GitHub returns a recursive tree in one response, so `MAX_TREE_ENTRIES` bounds what is analyzed but not the bytes GitHub sends. PostgreSQL is provisioned but not used for analysis history. Review generated guidance before relying on it.
