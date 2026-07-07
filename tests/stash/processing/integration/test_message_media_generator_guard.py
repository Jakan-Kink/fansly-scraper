"""Guard test for the message_media_generator bounded-retry fix.

The generator's media-count validation loop is unsatisfiable when the buffered
distribution capacity goes negative (``max_*_for_distribution < 0`` from a
transiently-low Docker-Stash count). The fix replaced an unbounded ``while``
(which spun to a 90s pytest-timeout) with a bounded 1000-try loop that
``pytest.skip``s with the observed counts. This test forces that condition by
stubbing the scene count low and asserts the generator skips *quickly* rather
than hanging.
"""

import time
from types import SimpleNamespace

import _pytest.outcomes
import pytest


@pytest.mark.integration
@pytest.mark.asyncio
async def test_generator_skips_fast_when_distribution_unsatisfiable(
    message_media_generator,
    real_stash_processor,
    stash_cleanup_tracker,
    monkeypatch,
):
    """Low scene count → unsatisfiable distribution → fast skip, not a timeout."""
    client = real_stash_processor.context.client

    async def _few_scenes(*args, **kwargs):
        # 5 scenes < reserved_buffer (10) → max_scenes_for_distribution = -5,
        # so validate_media_obj_counts can never return True.
        return SimpleNamespace(count=5, scenes=[])

    # Enforcement requires the tracker on any real_stash_processor test, even
    # though this one creates nothing (it skips before any Stash mutation).
    async with stash_cleanup_tracker(
        client
    ):  # CCH:NO-DUMP  # guard path: skips before any GraphQL mutation
        monkeypatch.setattr(client, "find_scenes", _few_scenes)

        start = time.monotonic()
        with pytest.raises(_pytest.outcomes.Skipped) as skipped:
            await message_media_generator(spread_over_objs=3)
        elapsed = time.monotonic() - start

    # The 1000-try loop is effectively instant; the old unbounded loop hit the
    # 90s pytest-timeout. A few seconds of headroom keeps this robust under load.
    assert elapsed < 10, f"skip took {elapsed:.1f}s — bounded loop not engaged?"
    # The skip reason surfaces the observed counts for diagnosis.
    assert "scenes_available=5" in str(skipped.value)
