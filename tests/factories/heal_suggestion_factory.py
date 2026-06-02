"""Factory for HealSuggestion model instances.

Multiple HealSuggestion rows can exist per TestFailure (no unique constraint).

Usage (persisted — test_failure must already be flushed):
    suggestion = HealSuggestionFactory(test_failure_id=failure.id)
    db_session.add(suggestion)
    await db_session.flush()
"""
from __future__ import annotations

import random
import uuid

import factory

from src.config.constants import DEFAULT_MODEL
from src.models.heal_suggestion import HealSuggestion


def _random_confidence() -> float:
    return round(random.uniform(0.60, 0.95), 2)


_FIX_SNIPPET = '''\
# Before
assert result.total == expected_total

# After — recalculate with current tax rate
expected_total = checkout_service.compute_total(items, tax_rate=0.08)
assert result.total == pytest.approx(expected_total, abs=0.01)
'''


class HealSuggestionFactory(factory.Factory):
    class Meta:
        model = HealSuggestion

    id = factory.LazyFunction(uuid.uuid4)
    # test_failure_id must be supplied by the caller when persisting to the DB.
    test_failure_id = factory.LazyFunction(uuid.uuid4)
    suggestion = (
        "The assertion uses a hardcoded expected value that no longer matches the "
        "computed checkout total after the tax-rate change in PR #418. Update the "
        "expected value to use `checkout_service.compute_total()` instead."
    )
    confidence = factory.LazyFunction(_random_confidence)
    affected_file = "src/services/checkout_service.py"
    fix_snippet = _FIX_SNIPPET
    accepted = None
    model_used = DEFAULT_MODEL
    tokens_used = factory.Faker("random_int", min=300, max=1500)
