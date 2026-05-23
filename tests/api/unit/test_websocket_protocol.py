"""Tests for api.websocket_protocol pure helpers."""

from __future__ import annotations

import pytest

from api.websocket_protocol import (
    NOTIFICATION_TYPES,
    notification_inner_to_service_event,
)


@pytest.mark.parametrize(
    ("inner_type", "expected"),
    [
        # Standard NOTIFICATION_TYPES mappings — each encoded as
        # serviceId*1000 + N; helper round-trips via divmod.
        (1004, (1, 4)),
        (2007, (2, 7)),
        (3002, (3, 2)),
        (5003, (5, 3)),
        (7001, (7, 1)),
        (15006, (15, 6)),
        (15007, (15, 7)),
        (15011, (15, 11)),
        (15016, (15, 16)),
        (32007, (32, 7)),
        (45012, (45, 12)),
        # INTERNAL-service correlations (svc=1000) — the AM/bundle copy
        # signals the Fansly client emits to itself; same encoding rule.
        (1000001, (1000, 1)),
        (1000002, (1000, 2)),
    ],
)
def test_notification_inner_to_service_event_unwraps_known_codes(
    inner_type: int, expected: tuple[int, int]
) -> None:
    assert notification_inner_to_service_event(inner_type) == expected


def test_every_notification_types_entry_unwraps_to_valid_pair() -> None:
    """Every catalogued NOTIFICATION_TYPES code unwraps to a (svc>0, type>=0)
    pair. Guards against stray entries that would slip past the divmod
    convention if someone added a non-encoded code by mistake."""
    for inner_type in NOTIFICATION_TYPES:
        svc, etype = notification_inner_to_service_event(inner_type)
        assert svc > 0, f"inner_type={inner_type} unwrapped to svc=0"
        assert etype >= 0, f"inner_type={inner_type} unwrapped to negative type"
