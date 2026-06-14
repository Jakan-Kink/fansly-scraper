"""Reusable helpers for StashProcessing file-first / adjudication tests.

These were previously duplicated (and silently diverged) across
``test_stash_id_fast_path``, ``test_image_adjudication``, ``test_scene_adjudication``
and ``test_run_file_first``. They live here so every test file shares one
definition.
"""

import json
import re

import httpx

from pathio import get_stash_path
from tests.fixtures.stash.stash_type_factories import PerformerFactory, StudioFactory


def stash_creator_root(processor) -> str:
    """The Stash-visible creator root the sweep/helpers anchor to."""
    return get_stash_path(processor.state.base_path, processor.config).rstrip("/")


def seed_processor_caches(processor, mock_account):
    """Pre-seed account/performer caches so a stamp fires zero incidental GraphQL.

    The performer's ``scenes``/``images`` are seeded EMPTY so that wiring it onto
    a stamped scene/image (the bidirectional inverse sync) stays in-memory rather
    than issuing a PopulateRelationship query for the missing reverse field.
    Returns the studio to pass explicitly.
    """
    processor._account = mock_account
    processor._performer = PerformerFactory(
        id="123", name=mock_account.username, scenes=[], images=[]
    )
    return StudioFactory(id="200", name=f"{mock_account.username} (Fansly)")


def graphql_op_fired(calls, op: str) -> bool:
    """Whether any captured GraphQL call invoked the given operation name.

    Matches the op as a field invocation (``op(``) rather than a bare substring,
    so an op name that is a substring of another (e.g. ``studioCreate`` inside
    ``studioCreateMany``) cannot false-positive.
    """
    pattern = re.compile(rf"\b{re.escape(op)}\s*\(")
    for call in calls:
        try:
            query = json.loads(call.request.content)["query"]
        except (ValueError, KeyError):
            continue
        if pattern.search(query):
            return True
    return False


def find_files_response(*file_dicts) -> httpx.Response:
    """A ``findFiles`` page carrying the given BaseFile dicts."""
    return httpx.Response(
        200,
        json={
            "data": {"findFiles": {"count": len(file_dicts), "files": list(file_dicts)}}
        },
    )
