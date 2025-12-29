"""Stash integration module.

This module provides high-level processing logic for interacting with
Stash media server using the stash-graphql-client library.

For Stash types and client, import directly from stash_graphql_client:
    from stash_graphql_client import StashClient, StashContext
    from stash_graphql_client.types import Gallery, Performer, Scene, etc.
"""

# Local modules
from .logging import debug_print, processing_logger
from .processing import StashProcessing


__all__ = [
    "StashProcessing",
    "debug_print",
    "processing_logger",
]
