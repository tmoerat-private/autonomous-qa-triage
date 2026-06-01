from __future__ import annotations

from datetime import datetime
from pathlib import Path
from uuid import UUID

import httpx
import structlog
from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from src.api.dependencies import DbSession
from src.config.settings import get_settings
from src.db.repositories.heal_suggestion_repo import HealSuggestionRepository
from src.db.repositories.pipeline_repo import PipelineEventRepository
from src.db.repositories.rerun_repo import RerunRepository
from src.db.repositories.root_cause_repo import RootCauseRepository
from src.db.repositories.screenshot_repo import ScreenshotRepository
from src.schemas.failure_schemas import (
    ClassificationDetail,
    FailureDetailResponse,
    FailureListItem,
    HealSuggestionResponse,
    PaginatedFailuresResponse,
    RerunResponse,
    RetriegeResponse,
    RootCauseResponse,
    ScreenshotResponse,
    TicketDetail,
)
from src.services import failure_service
from src.services.screenshot_service import save_screenshot
from src.workers.tasks import run_triage_pipeline


class SuggestionAcceptance(BaseModel):
    accepted: bool

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

    # Inject classification category/confidence and branch via single batch queries
    cat_map = await failure_service.get_category_map(db, [r.id for r in rows])
    branch_map = await failure_service.get_branch_map(
        db, [r.pipeline_event_id for r in rows]
    )
    items = []
    for row in rows:
        base = FailureListItem.model_validate(row)
        cat_data = cat_map.get(row.id, {})
        items.append(
            base.model_copy(
                update={
                    "category": cat_data.get("category"),
                    "confidence": cat_data.get("confidence"),
                    "branch": branch_map.get(row.pipeline_event_id),
                }
            )
        )

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
        commit_sha=detail.get("commit_sha"),
        repository=detail.get("repository"),
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


@router.post("/{failure_id}/rerun", response_model=RerunResponse)
async def rerun_failure(
    failure_id: UUID,
    db: DbSession,
) -> RerunResponse:
    """Trigger a CI rerun for the build associated with a test failure.

    Dispatches to the correct CI provider client based on the linked pipeline
    event's provider field. Returns immediately with the triggered job ID.
    """
    detail = await failure_service.get_failure_detail(db, failure_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="failure not found")

    failure = detail["failure"]

    event = await PipelineEventRepository().get_by_id(db, failure.pipeline_event_id)
    if event is None:
        raise HTTPException(status_code=404, detail="pipeline event not found")

    settings = get_settings()

    try:
        if event.provider == "jenkins":
            from src.integrations.jenkins.client import JenkinsClient

            async with JenkinsClient(settings) as client:
                result = await client.trigger_rerun(
                    job_name=event.pipeline_name or "unknown",
                    build_number=int(event.provider_build_id or "0"),
                )
            job_id = result.get("job_name")
        elif event.provider == "github_actions":
            from src.integrations.github_actions.client import GitHubActionsClient

            async with GitHubActionsClient(settings) as client:
                result = await client.trigger_rerun(
                    repo_full_name=event.repository or "",
                    run_id=int(event.provider_build_id or "0"),
                )
            job_id = str(result.get("run_id"))
        else:
            raise HTTPException(
                status_code=400,
                detail=f"unsupported provider: {event.provider}",
            )
    except HTTPException:
        raise
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=502, detail=f"CI API unreachable: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"CI API unreachable: {exc}") from exc

    await RerunRepository().create(
        db,
        test_failure_id=failure_id,
        provider=event.provider,
        triggered_job_id=job_id,
        trigger_reason="manual",
        status="triggered",
    )
    await db.commit()

    logger.info(
        "failures.rerun.triggered",
        failure_id=str(failure_id),
        provider=event.provider,
        job_id=job_id,
    )
    return RerunResponse(
        triggered=True,
        provider=event.provider,
        job_id=job_id,
        failure_id=str(failure_id),
    )


@router.get("/{failure_id}/suggestion", response_model=HealSuggestionResponse)
async def get_failure_suggestion(
    failure_id: UUID,
    db: DbSession,
) -> HealSuggestionResponse:
    """Return the most recent healing suggestion for a test failure."""
    detail = await failure_service.get_failure_detail(db, failure_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="failure not found")

    suggestions = await HealSuggestionRepository().get_by_failure_id(db, failure_id)
    if not suggestions:
        raise HTTPException(
            status_code=404, detail="no suggestion found for this failure"
        )

    logger.info(
        "failures.suggestion.retrieved",
        failure_id=str(failure_id),
        suggestion_id=str(suggestions[0].id),
    )
    return HealSuggestionResponse.model_validate(suggestions[0])


@router.patch("/{failure_id}/suggestion", response_model=HealSuggestionResponse)
async def accept_failure_suggestion(
    failure_id: UUID,
    body: SuggestionAcceptance,
    db: DbSession,
) -> HealSuggestionResponse:
    """Accept or reject the healing suggestion for a test failure."""
    detail = await failure_service.get_failure_detail(db, failure_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="failure not found")

    suggestions = await HealSuggestionRepository().get_by_failure_id(db, failure_id)
    if not suggestions:
        raise HTTPException(
            status_code=404, detail="no suggestion found for this failure"
        )

    suggestion = suggestions[0]
    updated = await HealSuggestionRepository().update_acceptance(
        db, suggestion.id, body.accepted
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="suggestion not found")

    await db.commit()

    logger.info(
        "failures.suggestion.acceptance_updated",
        failure_id=str(failure_id),
        suggestion_id=str(suggestion.id),
        accepted=body.accepted,
    )
    return HealSuggestionResponse.model_validate(updated)


@router.get("/{failure_id}/root-cause", response_model=RootCauseResponse)
async def get_failure_root_cause(
    failure_id: UUID,
    db: DbSession,
) -> RootCauseResponse:
    """Return the most recent root cause analysis for a test failure."""
    detail = await failure_service.get_failure_detail(db, failure_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="failure not found")

    analysis = await RootCauseRepository().get_latest_by_failure_id(db, failure_id)
    if analysis is None:
        raise HTTPException(
            status_code=404, detail="no root cause analysis found for this failure"
        )

    logger.info(
        "failures.root_cause.retrieved",
        failure_id=str(failure_id),
        analysis_id=str(analysis.id),
    )
    return RootCauseResponse.model_validate(analysis)


@router.post("/{failure_id}/screenshots", response_model=ScreenshotResponse, status_code=201)
async def upload_screenshot(
    failure_id: UUID,
    db: DbSession,
    file: UploadFile = File(...),
) -> ScreenshotResponse:
    """Upload a screenshot and associate it with a test failure."""
    detail = await failure_service.get_failure_detail(db, failure_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="failure not found")

    data = await file.read()

    try:
        screenshot = await save_screenshot(
            db,
            failure_id,
            file.filename or "screenshot",
            file.content_type or "image/png",
            data,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await db.commit()

    logger.info("failures.screenshot.uploaded", failure_id=str(failure_id))
    return ScreenshotResponse.model_validate(screenshot)


@router.get("/{failure_id}/screenshots", response_model=list[ScreenshotResponse])
async def list_screenshots(
    failure_id: UUID,
    db: DbSession,
) -> list[ScreenshotResponse]:
    """Return all screenshots associated with a test failure."""
    detail = await failure_service.get_failure_detail(db, failure_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="failure not found")

    screenshots = await ScreenshotRepository().get_by_failure_id(db, failure_id)
    return [ScreenshotResponse.model_validate(s) for s in screenshots]


# Separate router so the file-serving endpoint lives at
# GET /api/v1/screenshots/{screenshot_id}/file  (not nested under /failures).
screenshots_router = APIRouter(prefix="/screenshots", tags=["screenshots"])


@screenshots_router.get("/{screenshot_id}/file")
async def get_screenshot_file(
    screenshot_id: UUID,
    db: DbSession,
) -> FileResponse:
    """Stream a screenshot file by its ID."""
    screenshot = await ScreenshotRepository().get_by_id(db, screenshot_id)
    if screenshot is None:
        raise HTTPException(status_code=404, detail="screenshot not found")

    path = Path(screenshot.storage_path)
    if not path.exists():
        raise HTTPException(status_code=404, detail="screenshot file not found on disk")

    return FileResponse(
        path=str(path),
        media_type=screenshot.content_type,
        filename=screenshot.original_filename,
    )
