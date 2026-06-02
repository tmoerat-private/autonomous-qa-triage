"""Factory for RootCauseAnalysis model instances.

Multiple RootCauseAnalysis rows can exist per TestFailure (no unique constraint).
pipeline_event_id is nullable and optional.

Usage (persisted — test_failure must already be flushed):
    rca = RootCauseAnalysisFactory(
        test_failure_id=failure.id,
        pipeline_event_id=event.id,   # optional
    )
    db_session.add(rca)
    await db_session.flush()
"""
from __future__ import annotations

import uuid

import factory

from src.config.constants import DEFAULT_MODEL, FailureCategory
from src.models.root_cause_analysis import RootCauseAnalysis


class RootCauseAnalysisFactory(factory.Factory):
    class Meta:
        model = RootCauseAnalysis

    id = factory.LazyFunction(uuid.uuid4)
    # test_failure_id must be supplied by the caller when persisting to the DB.
    test_failure_id = factory.LazyFunction(uuid.uuid4)
    # pipeline_event_id is optional (SET NULL on delete) — leave as None by default.
    pipeline_event_id = None
    root_cause_summary = (
        "The checkout total computation silently returns 0.00 when the applied "
        "discount code is expired.  The discount-validation guard clause introduced "
        "in commit a3f9e2b does not fall back to the undiscounted price, causing "
        "downstream assertions to fail."
    )
    root_cause_category = FailureCategory.PRODUCT_BUG
    likely_cause_files = factory.LazyFunction(
        lambda: [
            "src/services/checkout_service.py",
            "src/models/discount.py",
        ]
    )
    investigation_steps = factory.LazyFunction(
        lambda: [
            "Reproduce locally: run pytest tests/unit/services/test_checkout.py -k test_checkout_total",
            "Inspect discount_service.apply_discount() return value when code is expired.",
            "Verify the fallback path in checkout_service.compute_total() handles None discount.",
            "Check git log for recent changes to src/services/checkout_service.py.",
        ]
    )
    model_used = DEFAULT_MODEL
