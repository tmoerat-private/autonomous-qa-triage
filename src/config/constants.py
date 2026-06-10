from enum import StrEnum


class FailureCategory(StrEnum):
    PRODUCT_BUG = "product_bug"
    FLAKY_TEST = "flaky_test"
    ENV_ISSUE = "env_issue"
    TIMEOUT = "timeout"
    INFRA_ISSUE = "infra_issue"
    CONFIG_ERROR = "config_error"
    DEPENDENCY_FAILURE = "dependency_failure"


class CIProvider(StrEnum):
    JENKINS = "jenkins"
    GITHUB_ACTIONS = "github_actions"


class TicketProvider(StrEnum):
    JIRA = "jira"
    LINEAR = "linear"


class NotificationChannel(StrEnum):
    SLACK = "slack"
    TEAMS = "teams"
    EMAIL = "email"


class PipelineStatus(StrEnum):
    SUCCESS = "success"
    FAILURE = "failure"
    ERROR = "error"
    PENDING = "pending"


class FailureStatus(StrEnum):
    NEW = "new"
    TRIAGING = "triaging"
    TRIAGED = "triaged"
    RESOLVED = "resolved"
    IGNORED = "ignored"


class AgentRunStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class TicketPriority(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


DEFAULT_MODEL = "claude-sonnet-4-20250514"
# chars — real CI runs easily exceed 100K before the pytest FAILED lines appear
MAX_LOG_LENGTH = 500_000
ERROR_SIGNATURE_VERSION = "v1"

# Phase 2 — vector similarity duplicate detection
SIMILARITY_THRESHOLD: float = 0.85
EMBEDDING_MODEL: str = "all-MiniLM-L6-v2"
EMBEDDING_VECTOR_SIZE: int = 384

# Phase 2 — flaky test detection
FLAKINESS_SCORE_THRESHOLD: float = 0.5
FLAKY_LOOKBACK_DAYS: int = 30
FLAKY_MIN_FAILURE_RATE: float = 0.05
FLAKY_MAX_FAILURE_RATE: float = 0.75
FLAKY_MIN_SAMPLE_SIZE: int = 3   # minimum failure count before scoring
