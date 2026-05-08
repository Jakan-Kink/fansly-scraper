"""Lightweight story stand-ins for tests that exercise story-helper code paths.

``_mark_stories_viewed`` only reads ``.id`` off each saved-story object,
so tests that just want to assert "the helper iterates and POSTs once
per story" don't need to construct a full ``MediaStory`` Pydantic model
(which has FK constraints into Account etc.). ``FakeStory`` is the
minimal duck-type for those tests — kept here so test files don't
re-invent it.
"""

from dataclasses import dataclass


@dataclass
class FakeStory:
    """Minimal story object exposing only ``.id``."""

    id: int
