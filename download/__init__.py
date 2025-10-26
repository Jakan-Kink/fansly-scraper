"""Download Module

This module provides functionality for downloading content from various sources.
It includes support for different download types and content handling.
"""

from .transaction import in_transaction_or_new
from .types import DownloadType

__all__ = ["DownloadType", "in_transaction_or_new"]
