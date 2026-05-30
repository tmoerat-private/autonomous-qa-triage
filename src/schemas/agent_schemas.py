from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class AgentRunItem(BaseModel):
    """Fields from AgentRun returned in list and detail responses."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    test_failure_id: uuid.UUID
    agent_name: str
    status: str
    input_summary: str | None
    output_summary: str | None
    duration_ms: int | None
    tokens_used: int | None
    started_at: datetime
    completed_at: datetime | None
    created_at: datetime
    updated_at: datetime


class PaginatedAgentRunsResponse(BaseModel):
    """Paginated envelope for agent run list results."""

    items: list[AgentRunItem]
    total: int
    limit: int
    offset: int


class DashboardSummaryResponse(BaseModel):
    """Aggregate counts for the dashboard summary panel."""

    period: str
    by_status: dict[str, int]
    by_category: dict[str, int]
    total: int


class TopFailingTest(BaseModel):
    """A single entry in the top-failing-tests chart."""

    test_name: str
    count: int


class DailyTrend(BaseModel):
    """A single date bucket in the daily-trends chart."""

    date: str
    count: int
