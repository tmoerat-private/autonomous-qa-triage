from __future__ import annotations

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Jenkins (notification plugin format)
# ---------------------------------------------------------------------------


class JenkinsSCM(BaseModel):
    model_config = {"extra": "ignore"}

    url: str | None = None
    branch: str | None = None
    commit: str | None = None


class JenkinsBuild(BaseModel):
    model_config = {"extra": "ignore"}

    full_url: str
    number: int
    status: str  # "SUCCESS", "FAILURE", "ABORTED", "UNSTABLE"
    url: str
    scm: JenkinsSCM = Field(default_factory=JenkinsSCM)
    artifacts: dict = Field(default_factory=dict)


class JenkinsWebhookPayload(BaseModel):
    model_config = {"extra": "ignore"}

    name: str  # job name
    url: str  # job url
    build: JenkinsBuild


# ---------------------------------------------------------------------------
# GitHub Actions (workflow_run event)
# ---------------------------------------------------------------------------


class GitHubRepository(BaseModel):
    model_config = {"extra": "ignore"}

    id: int
    full_name: str  # "org/repo"
    html_url: str


class GitHubWorkflowRun(BaseModel):
    model_config = {"extra": "ignore"}

    id: int
    name: str  # workflow name
    head_branch: str | None = None
    head_sha: str | None = None
    status: str  # "completed", "in_progress", "queued"
    conclusion: str | None = None  # "failure", "success", "cancelled", etc.
    html_url: str
    run_number: int
    repository: GitHubRepository


class GitHubActionsWebhookPayload(BaseModel):
    model_config = {"extra": "ignore"}

    action: str  # "completed", "requested", "in_progress"
    workflow_run: GitHubWorkflowRun
    repository: GitHubRepository


# ---------------------------------------------------------------------------
# Normalized internal schema — produced by all webhook handlers
# ---------------------------------------------------------------------------


class NormalizedPipelineEvent(BaseModel):
    """Common internal representation of a CI/CD pipeline event.

    All provider-specific webhook handlers normalize their payloads into this
    schema before the data is persisted or passed to the agent pipeline.
    """

    provider: str  # CIProvider value
    provider_build_id: str  # unique build identifier from the provider
    repository: str | None = None
    branch: str | None = None
    commit_sha: str | None = None
    pipeline_name: str | None = None
    status: str  # PipelineStatus value
    raw_payload: dict  # original webhook payload preserved as-is


# ---------------------------------------------------------------------------
# Parsed test failure — produced by CI log parsers
# ---------------------------------------------------------------------------


class ParsedTestFailure(BaseModel):
    """A single test failure extracted from CI logs or artifacts."""

    test_name: str
    test_suite: str | None = None
    test_file: str | None = None
    error_message: str | None = None
    stack_trace: str | None = None
    duration_ms: int | None = None
