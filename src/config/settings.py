from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # App
    app_env: str = "development"
    app_port: int = 8000
    log_level: str = "INFO"
    secret_key: str = "change-me-in-production"

    # Database
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/autonomous_qa"
    redis_url: str = "redis://localhost:6379/0"

    # Vector DB
    qdrant_url: str = "http://localhost:6333"
    qdrant_collection: str = "error_signatures"
    qdrant_outcomes_collection: str = "triage_outcomes"

    # Phase 2 — vector similarity
    qdrant_vector_size: int = 384           # matches all-MiniLM-L6-v2 output dims
    similarity_threshold: float = 0.85     # minimum cosine similarity to count as duplicate

    # Phase 2 — flaky test detection
    flaky_lookback_days: int = 30          # history window for flakiness analysis
    flaky_score_threshold: float = 0.5    # minimum score to classify as flaky
    flaky_min_failure_rate: float = 0.05  # below this = probably a real new failure
    flaky_max_failure_rate: float = 0.75  # above this = probably a persistent bug

    # Phase 3 — autonomous reruns
    enable_auto_rerun: bool = False

    # AI
    anthropic_api_key: str = ""
    default_model: str = "claude-sonnet-4-20250514"
    fallback_model: str = "gpt-4o"

    # Jenkins (optional)
    jenkins_url: str = ""
    jenkins_user: str = ""
    jenkins_token: str = ""
    jenkins_webhook_secret: str = ""

    # GitHub (optional)
    github_app_id: str = ""
    github_private_key_path: str = ""
    github_webhook_secret: str = ""

    # Jira (optional)
    jira_url: str = ""
    jira_email: str = ""
    jira_api_token: str = ""
    jira_project_key: str = ""
    jira_default_assignee: str = ""

    # Slack (optional)
    slack_bot_token: str = ""
    slack_signing_secret: str = ""
    slack_channel_id: str = ""

    # Observability
    otel_exporter_otlp_endpoint: str = "http://localhost:4317"
    otel_service_name: str = "autonomous-qa"


@lru_cache
def get_settings() -> Settings:
    return Settings()
