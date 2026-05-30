from __future__ import annotations

from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Query
from sqlalchemy import and_, desc, func, select

from src.api.dependencies import DbSession
from src.models.agent_run import AgentRun
from src.schemas.agent_schemas import AgentRunItem, PaginatedAgentRunsResponse

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/agent-runs", tags=["agent-runs"])


@router.get("", response_model=PaginatedAgentRunsResponse)
async def list_agent_runs(
    db: DbSession,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    test_failure_id: UUID | None = Query(default=None),
    agent_name: str | None = Query(default=None),
) -> PaginatedAgentRunsResponse:
    """List agent runs with optional filters on failure ID and agent name."""
    conditions = []
    if test_failure_id is not None:
        conditions.append(AgentRun.test_failure_id == test_failure_id)
    if agent_name is not None:
        conditions.append(AgentRun.agent_name == agent_name)

    data_stmt = select(AgentRun)
    count_stmt = select(func.count()).select_from(AgentRun)

    if conditions:
        data_stmt = data_stmt.where(and_(*conditions))
        count_stmt = count_stmt.where(and_(*conditions))

    data_stmt = (
        data_stmt.order_by(desc(AgentRun.started_at)).limit(limit).offset(offset)
    )

    data_result = await db.execute(data_stmt)
    rows = list(data_result.scalars().all())

    count_result = await db.execute(count_stmt)
    total: int = count_result.scalar_one()

    items = [AgentRunItem.model_validate(row) for row in rows]

    logger.info(
        "agent_runs.list",
        total=total,
        limit=limit,
        offset=offset,
        test_failure_id=str(test_failure_id) if test_failure_id else None,
        agent_name=agent_name,
    )
    return PaginatedAgentRunsResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/{run_id}", response_model=AgentRunItem)
async def get_agent_run(
    run_id: UUID,
    db: DbSession,
) -> AgentRunItem:
    """Return detail for a single agent run."""
    stmt = select(AgentRun).where(AgentRun.id == run_id)
    result = await db.execute(stmt)
    run = result.scalar_one_or_none()

    if run is None:
        logger.info("agent_runs.not_found", run_id=str(run_id))
        raise HTTPException(status_code=404, detail="agent run not found")

    logger.info("agent_runs.detail", run_id=str(run_id), status=run.status)
    return AgentRunItem.model_validate(run)
