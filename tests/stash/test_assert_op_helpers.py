"""Unit tests for ``assert_op`` and ``assert_op_with_vars`` helpers.

The helpers are exercised indirectly by ~230 call sites across the test
suite, but those tests run against real respx mocks and assert on
production behaviour. This file pins the helpers' own contract — kwarg
syntax, the ``paths=`` escape hatch for ``__``-containing keys, and the
diagnostic messages produced on mismatch — so a regression in the
helpers themselves is caught before it shows up as a confusing failure
in 230 unrelated tests.
"""

from __future__ import annotations

import pytest

from tests.fixtures.stash.stash_api_fixtures import (
    assert_op,
    assert_op_with_vars,
)


def _dict_call(query: str, variables: dict | None = None) -> dict:
    """Build a capture_graphql_calls-shaped dict (the simplest call form)."""
    return {"query": query, "variables": variables or {}}


class TestAssertOp:
    """``assert_op`` is a substring check against the query string."""

    def test_matches_substring(self):
        call = _dict_call("query FindGalleries { findGalleries { count } }")
        assert_op(call, "findGalleries")  # no exception → pass

    def test_case_sensitive(self):
        call = _dict_call("query FindGalleries { findGalleries { count } }")
        with pytest.raises(AssertionError, match="findgalleries"):
            assert_op(call, "findgalleries")

    def test_missing_op_raises_with_query_excerpt(self):
        call = _dict_call("query FindScene { findScene { id } }")
        with pytest.raises(AssertionError, match="FindScene"):
            assert_op(call, "FindStudio")


class TestAssertOpWithVarsKwargForm:
    """The ``__``-separator kwarg form (the 95% case)."""

    def test_top_level_match(self):
        call = _dict_call("query FindImage", variables={"id": "123"})
        assert_op_with_vars(call, "FindImage", id="123")

    def test_nested_path(self):
        call = _dict_call(
            "query FindGalleries",
            variables={
                "gallery_filter": {"code": {"value": "12345", "modifier": "EQUALS"}}
            },
        )
        assert_op_with_vars(
            call,
            "FindGalleries",
            gallery_filter__code__value="12345",
            gallery_filter__code__modifier="EQUALS",
        )

    def test_missing_path_raises_with_path_in_message(self):
        call = _dict_call("query Q", variables={"id": "1"})
        with pytest.raises(AssertionError, match=r"filter\.code\.value"):
            assert_op_with_vars(call, "Q", filter__code__value="x")

    def test_type_mismatch_diagnostic_includes_both_types(self):
        call = _dict_call("query Q", variables={"id": 123})  # int, not str
        with pytest.raises(AssertionError, match=r"\(int\)"):
            assert_op_with_vars(call, "Q", id="123")  # expects str


class TestAssertOpWithVarsPathsEscapeHatch:
    """The ``paths=`` dict escape hatch for ``__``-containing keys.

    GraphQL ``__typename`` is the canonical case — Stash does not currently
    surface it in request variables, but SGC fragments use it heavily in
    response shapes (see ``stash_graphql_client/fragments.py``), so the
    escape hatch must work in case any future input type round-trips it.
    """

    def test_typename_at_top_level(self):
        call = _dict_call(
            "mutation M",
            variables={"__typename": "ImageFile", "id": "456"},
        )
        assert_op_with_vars(
            call,
            "M",
            paths={("__typename",): "ImageFile"},
            id="456",
        )

    def test_typename_nested_inside_input(self):
        call = _dict_call(
            "mutation M",
            variables={"input": {"__typename": "ImageFile", "id": "789"}},
        )
        assert_op_with_vars(
            call,
            "M",
            paths={("input", "__typename"): "ImageFile"},
            input__id="789",
        )

    def test_paths_and_kwargs_combine(self):
        call = _dict_call(
            "mutation M",
            variables={
                "__typename": "ImageFile",
                "input": {"id": "999", "name": "x"},
            },
        )
        assert_op_with_vars(
            call,
            "M",
            paths={("__typename",): "ImageFile"},
            input__id="999",
            input__name="x",
        )

    def test_paths_missing_path_still_diagnoses(self):
        call = _dict_call("mutation M", variables={"input": {"id": "1"}})
        with pytest.raises(AssertionError, match=r"input\.__typename"):
            assert_op_with_vars(call, "M", paths={("input", "__typename"): "ImageFile"})
