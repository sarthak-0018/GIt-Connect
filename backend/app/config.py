from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


_cwd_env = Path.cwd() / ".env"
_workspace_env = Path(__file__).resolve().parents[2] / ".env"
_env_file = _cwd_env if _cwd_env.exists() else _workspace_env


class Settings(BaseSettings):
    app_name: str = "ContribCompass"
    github_token: str | None = None
    github_api_url: str = "https://api.github.com"
    github_timeout_seconds: float = 15
    llm_provider: str = "ollama"
    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "qwen3:latest"
    ollama_thinking: bool = False
    ollama_num_predict: int = 128
    llm_temperature: float = 0.6
    llm_top_p: float = 0.95
    llm_top_k: int = 20
    vllm_base_url: str = "http://vllm:8000/v1"
    vllm_model: str = "Qwen/Qwen3-4B-Instruct-2507"
    llm_timeout_seconds: float = 20
    max_issues: int = 30
    max_important_files: int = 24
    max_file_size: int = 200000
    max_readme_size: int = 100000
    max_context_size: int = 8000
    max_tree_entries: int = 50000
    max_github_requests_per_analysis: int = 32
    max_analysis_time: int = 120
    analyze_rate_limit_per_ip: int = 10
    analyze_rate_limit_window: int = 3600
    max_concurrent_analyses: int = 4
    analysis_cache_fresh_seconds: int = 60
    analysis_cache_ttl_seconds: int = 600
    redis_url: str | None = None
    database_url: str | None = None
    model_config = SettingsConfigDict(env_file=str(_env_file), extra="ignore")


settings = Settings()
