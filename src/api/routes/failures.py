from __future__ import annotations

from datetime import datetime
from uuid import UUID

import structlog
from fastapi import APIRouter, HTTPException, Query

from src.api.dependencies import DbSession
from src.schemas.failure_schemas import (
    ClassificationDetail,
    FailureDetailResponse,
    FailureListItem,
    PaginatedFailuresResponse,
    RetriegeResponse,
    TicketDetail,
)
from src.services import failure_service
from src.workers.tasks import run_triage_pipeline

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/failures", tags=["failures"])


@router.get("", response_model=PaginatedFailuresResponse)
async def list_failures(
    db: DbSession,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None),
    category: str | None = Query(default=None),
    repository: str | None = Query(default=None),
    branch: str | None = Query(default=None),
    date_from: datetime | None = Query(default=None),
    date_to: datetime | None = Query(default=None),
) -> PaginatedFailuresResponse:
    """List test failures with optional filtering and pagination."""
    filters = {
        "status": status,
        "category": category,
        "repository": repository,
        "branch": branch,
        "date_from": date_from,
        "date_to": date_to,
    }
    rows, total = await failure_service.get_failures(db, filters, limit, offset)
    items = [FailureListItem.model_validate(row) for row in rows]
    logger.info("failures.list", total=total, limit=limit, offset=offset)
    return PaginatedFailuresResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/{failure_id}", response_model=FailureDetailResponse)
async def get_failure(
    failure_id: UUID,
    db: DbSession,
) -> FailureDetailResponse:
    """Return full detail for a single test failure including classification and ticket."""
    detail = await failure_service.get_failure_detail(db, failure_id)
    if detail is None:
        logger.info("failures.not_found", failure_id=str(failure_id))
        raise HTTPException(status_code=404, detail="failure not found")

    failure = detail["failure"]
    classification = detail["classification"]
    ticket = detail["ticket"]

    return FailureDetailResponse(
        id=failure.id,
        test_name=failure.test_name,
        test_suite=failure.test_suite,
        test_file=failure.test_file,
        error_message=failure.error_message,
        stack_trace=failure.stack_trace,
        status=failure.status,
        duration_ms=failure.duration_ms,
        retry_count=failure.retry_count,
        pipeline_event_id=failure.pipeline_event_id,
        created_at=failure.created_at,
        updated_at=failure.updated_at,
        classification=(
            ClassificationDetail.model_validate(classification)
            if classification is not None
            else None
        ),
        ticket=(
            TicketDetail.model_validate(ticket)
            if ticket is not None
            else None
        ),
        error_signature_hash=detail["error_signature_hash"],
    )


@router.post("/{failure_id}/retriage", status_code=202, response_model=RetriegeResponse)
async def retriage_failure(
    failure_id: UUID,
    db: DbSession,
) -> RetriegeResponse:
    """Enqueue a fresh triage run for an existing test failure.

    Returns 202 immediately; processing happens asynchronously via Celery.
    """
    detail = await failure_service.get_failure_detail(db, failure_id)
    if detail is None:
        logger.info("failures.retriage.not_found", failure_id=str(failure_id))
        raise HTTPException(status_code=404, detail="failure not found")

    failure = detail["failure"]
    run_triage_pipeline.delay(pipeline_event_id=str(failure.pipeline_event_id))

    logger.info(
        "failures.retriage.enqueued",
        failure_id=str(failure_id),
        pipeline_event_id=str(failure.pipeline_event_id),
    )
    return RetriegeResponse(
        message="triage enqueued",
        failure_id=str(failure_id),
    )
