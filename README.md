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
- Optional JSON generation with local Ollama or OpenAI-compatible vLLM using open-weight models.
- Deterministic evidence-backed fallback if inference is not available.
- Responsive UI with source links and uncertainty notes.

## Architecture

```text
Browser → React/Vite (Nginx) → FastAPI → GitHub REST API
                                      ├→ Ollama (development)
                                      └→ vLLM (production)
                         Compose also provisions PostgreSQL and Redis
```

## Quick start with Docker

Requirements: Docker Compose v2 and enough memory/disk for the selected model. Inference may use a CPU, but will be slower.

```bash
cp .env.example .env
docker compose up --build -d
docker compose exec ollama ollama pull qwen3:4b
```

Open [http://localhost:8080](http://localhost:8080). Configure `APP_PORT` to change the published port. First model download can take several minutes. The default compose setup uses Ollama and Qwen3 4B; it does not require an AI API key. A GitHub token is optional and helps with GitHub's unauthenticated API rate limits.

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

Open the Vite URL printed in the frontend terminal (normally `http://localhost:5173`). Vite proxies `/api` to the backend on port 8000. If Windows reports `EFTYPE` or `EPERM`, stop the Vite process, close any terminals/editors holding files in `frontend/node_modules`, then run `npm ci` from `frontend` to restore exactly the lockfile dependencies. If the npm cache itself reports access errors, use a project-local cache: `npm ci --cache .npm-cache`. For esbuild errors, use Node.js 22 LTS. Run Ollama natively, pull the model with `ollama pull qwen3:4b`, and set `OLLAMA_BASE_URL` to `http://localhost:11434` for inference.

## Ollama development inference

Install [Ollama](https://ollama.com), then run:

```bash
ollama pull qwen3:4b
ollama serve
```

Set `LLM_PROVIDER=ollama`, `OLLAMA_BASE_URL=http://localhost:11434`, and `OLLAMA_MODEL=qwen3:4b`. Model names and available variants change; check the model's current Ollama library entry and license before deployment. Repository-backed deterministic guidance remains available if inference fails.

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
| `LLM_PROVIDER` | `ollama` or `vllm` | `ollama` |
| `OLLAMA_BASE_URL` | Ollama endpoint | `http://ollama:11434` in Compose |
| `OLLAMA_MODEL` | Ollama model name | `qwen3:4b` |
| `VLLM_BASE_URL` | OpenAI-compatible vLLM endpoint | `http://vllm:8000/v1` in production Compose |
| `VLLM_MODEL` | vLLM model identifier | `Qwen/Qwen3-4B-Instruct-2507` |
| `GITHUB_TIMEOUT_SECONDS` | GitHub request timeout | `15` |
| `LLM_TIMEOUT_SECONDS` | Inference timeout | `90` |
| `MAX_ISSUES` | Maximum issue API page size | `30` |
| `MAX_IMPORTANT_FILES` | Maximum prioritized files fetched for analysis | `24` |
| `MAX_FILE_SIZE` | Maximum bytes retained from an inspected file | `200000` |
| `MAX_README_SIZE` | Maximum bytes retained from a README | `100000` |
| `MAX_CONTEXT_SIZE` | Maximum serialized profile/evidence context sent to inference | `24000` |
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

## Licenses

ContribCompass source is Apache-2.0. Runtime dependency licenses are summarized in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). The referenced model is not bundled; model terms vary by exact model and version. Verify its model card/license before redistribution or commercial use. GitHub is an external service with its own terms.

## Limitations and roadmap

The analyzer chooses files by path and provides evidence cards; model prose is schema-filtered and evidence IDs are checked against fetched content. GitHub returns a recursive tree in one response, so `MAX_TREE_ENTRIES` bounds what is analyzed but not the bytes GitHub sends. PostgreSQL is provisioned but not used for analysis history. Review generated guidance before relying on it.
