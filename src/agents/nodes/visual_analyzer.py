from __future__ import annotations

import base64
import uuid
from pathlib import Path

import structlog
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from src.agents.prompts.visual_analyzer_prompt import VISUAL_ANALYZER_SYSTEM_PROMPT
from src.agents.state import TriageState
from src.config.settings import get_settings
from src.db.repositories.screenshot_repo import ScreenshotRepository
from src.db.session import get_session_factory

logger = structlog.get_logger(__name__)


class VisualAnalysisResult(BaseModel):
    """Structured output returned by Claude for visual regression analysis."""

    has_regression: bool
    regression_description: str | None = Field(
        default=None,
        description="Description of the regression if has_regression is True, else null",
    )
    affected_components: list[str] = Field(
        default_factory=list,
        description="List of UI component names that appear broken or incorrect",
    )
    confidence: float = Field(..., ge=0.0, le=1.0)
    comparison_note: str = Field(
        ...,
        description="Brief note on what was compared or observed in the screenshot(s)",
    )


async def visual_analyzer_node(state: TriageState) -> dict:
    """Analyse screenshots for visual regressions using Claude's vision capability.

    For every failure ID in state['failure_ids']:
      1. Load all TestScreenshot records from the DB.
      2. Read image bytes from disk (outside the DB session).
      3. Build a multimodal HumanMessage with base64-encoded image blocks.
      4. Invoke Claude with structured output to obtain a VisualAnalysisResult.

    Skip conditions (returns visual_analysis=None without calling Claude):
      - No screenshots are found in the DB for any failure.
      - Every screenshot's file is missing from disk.

    Errors are accumulated and returned non-fatally; the node never raises.
    """
    log = logger.bind(
        node="visual_analyzer",
        pipeline_event_id=state["pipeline_event_id"],
    )
    log.info("visual_analyzer.started")

    errors: list[str] = list(state["errors"])
    session_factory = get_session_factory()

    # --- Phase 1: Load screenshot records from DB ---
    all_screenshots = []
    for failure_id in state["failure_ids"]:
        async with session_factory() as session:
            screenshots = await ScreenshotRepository().get_by_failure_id(
                session, uuid.UUID(failure_id)
            )
            all_screenshots.extend(screenshots)

    if not all_screenshots:
        log.info("visual_analyzer.skipped", reason="no_screenshots")
        return {"visual_analysis": None, "screenshot_ids": []}

    # --- Phase 2: Load image bytes from disk (outside the DB session) ---
    valid_screenshots = []
    valid_bytes = []
    for screenshot in all_screenshots:
        path = Path(screenshot.storage_path)
        if not path.exists():
            log.warning("visual_analyzer.file_missing", path=str(path))
            errors.append(f"visual_analyzer: file missing: {path}")
            continue
        valid_screenshots.append(screenshot)
        valid_bytes.append(path.read_bytes())

    if not valid_screenshots:
        return {"visual_analysis": None, "screenshot_ids": [], "errors": errors}

    # --- Phase 3: Call Claude with structured output ---
    try:
        content_blocks: list[str | dict] = []
        for screenshot, img_bytes in zip(valid_screenshots, valid_bytes, strict=True):
            b64 = base64.standard_b64encode(img_bytes).decode("utf-8")
            content_blocks.append(
                {
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": screenshot.content_type,
                        "data": b64,
                    },
                }
            )
        content_blocks.append(
            {
                "type": "text",
                "text": (
                    f"These are screenshots from a failing test: "
                    f"{', '.join(s.original_filename for s in valid_screenshots)}. "
                    "Analyze them for visual regressions or UI issues."
                ),
            }
        )
        human_message = HumanMessage(content=content_blocks)

        settings = get_settings()
        llm = ChatAnthropic(
            model=settings.default_model,
            api_key=settings.anthropic_api_key,
        )
        structured_llm = llm.with_structured_output(VisualAnalysisResult)

        result: VisualAnalysisResult = await structured_llm.ainvoke(  # type: ignore[assignment]
            [
                SystemMessage(content=VISUAL_ANALYZER_SYSTEM_PROMPT),
                human_message,
            ]
        )

        log.info(
            "visual_analyzer.complete",
            has_regression=result.has_regression,
            confidence=result.confidence,
            screenshot_count=len(valid_screenshots),
        )

        return {
            "visual_analysis": result.model_dump(),
            "screenshot_ids": [str(s.id) for s in valid_screenshots],
            "errors": errors,
        }

    except Exception as exc:
        log.warning("visual_analyzer.error", error=str(exc))
        errors.append(f"visual_analyzer: {exc}")
        return {"visual_analysis": None, "screenshot_ids": [], "errors": errors}
