"""Attachment protocols and processing.

Attachment construction is now handled by model validators on Post and Message
(Post._prepare_post_data and Message._prepare_message_data filter contentType,
_process_nested_cache_lookups resolves nested dicts with FK injection).
"""

from __future__ import annotations

from typing import Protocol

from .models import Attachment


class HasAttachments(Protocol):
    """Protocol for models that can have attachments."""

    id: int
    attachments: list[Attachment]
