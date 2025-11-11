"""Utility functions for the Stash client.

This module provides common utility functions used by various client mixins
and other components of the Stash client.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse


if TYPE_CHECKING:
    from ..types import Performer


def _get_attr(obj: Any, attr: str, default: Any = None) -> Any:
    """Get attribute from dict or object (duck typing helper).

    Handles both dict-like and object-like access patterns.
    This is a temporary workaround for Strawberry not deserializing nested objects.

    Args:
        obj: Dictionary or object to get attribute from
        attr: Attribute name to retrieve
        default: Default value if attribute doesn't exist

    Returns:
        Attribute value or default
    """
    if isinstance(obj, dict):
        return obj.get(attr, default)
    return getattr(obj, attr, default)


def sanitize_model_data(data_dict: dict[str, Any]) -> dict[str, Any]:
    """Remove problematic fields from dict before creating model instances.

    This prevents issues with _dirty_attrs and other internal fields
    that might cause problems with model objects.

    Args:
        data_dict: Dictionary containing model data

    Returns:
        Cleaned dictionary without internal attributes
    """
    if not isinstance(data_dict, dict):
        return data_dict

    # Remove internal attributes that could cause issues
    clean_dict = {
        k: v
        for k, v in data_dict.items()
        if not k.startswith("_") and k != "client_mutation_id"
    }
    return clean_dict


def normalize_url(url: str) -> str:
    """Normalize URL for comparison.

    Removes:
    - Protocol differences (http vs https)
    - www prefix
    - Trailing slashes
    - Query parameters and fragments

    Args:
        url: URL to normalize

    Returns:
        Normalized URL string

    Examples:
        >>> normalize_url("https://www.fansly.com/username/")
        "fansly.com/username"
        >>> normalize_url("http://fansly.com/username?foo=bar")
        "fansly.com/username"
    """
    if not url:
        return ""

    parsed = urlparse(url.lower())
    # Get domain without www
    domain = parsed.netloc.replace("www.", "")
    # Get path without trailing slash
    path = parsed.path.rstrip("/")

    return f"{domain}{path}"


def extract_username_from_url(url: str) -> str | None:
    """Extract username/slug from URL path.

    Args:
        url: URL to extract from

    Returns:
        Username if found, None otherwise

    Examples:
        >>> extract_username_from_url("https://fansly.com/testuser")
        "testuser"
        >>> extract_username_from_url("https://fansly.com/testuser/posts")
        "testuser"
    """
    if not url:
        return None

    parsed = urlparse(url.lower())
    path_parts = [p for p in parsed.path.split("/") if p]

    # Usually the username is the first path component
    if path_parts:
        return path_parts[0]

    return None


def urls_match(url1: str, url2: str) -> bool:
    """Check if two URLs match after normalization.

    Args:
        url1: First URL
        url2: Second URL

    Returns:
        True if URLs match after normalization
    """
    if not url1 or not url2:
        return False

    norm1 = normalize_url(url1)
    norm2 = normalize_url(url2)

    # Exact match
    if norm1 == norm2:
        return True

    # Username match (same username in path)
    username1 = extract_username_from_url(url1)
    username2 = extract_username_from_url(url2)

    if username1 and username2 and username1 == username2:
        # Also check if domains are similar (e.g., both fansly)
        domain1 = urlparse(url1.lower()).netloc.replace("www.", "")
        domain2 = urlparse(url2.lower()).netloc.replace("www.", "")
        # Simple check: same base domain
        base1 = ".".join(domain1.split(".")[-2:]) if "." in domain1 else domain1
        base2 = ".".join(domain2.split(".")[-2:]) if "." in domain2 else domain2
        if base1 == base2:
            return True

    return False


def find_best_performer_match(
    candidates: list[Performer],
    attempted_name: str,
    attempted_urls: list[str] | None = None,
    attempted_aliases: list[str] | None = None,
    attempted_disambiguation: str | None = None,
) -> Performer | None:
    """Find the best matching performer from candidates using weighted scoring.

    Scoring weights:
    - Exact name match (case-insensitive): 100 points
    - Exact alias match: 80 points
    - URL match: 60 points
    - No disambiguation (when not provided): 20 points
    - Matching disambiguation: 40 points

    Args:
        candidates: List of potential performer matches
        attempted_name: The name attempted to create
        attempted_urls: URLs for the performer being created
        attempted_aliases: Aliases for the performer being created
        attempted_disambiguation: Disambiguation for the performer being created

    Returns:
        Best matching performer or None if no good match found (score < 60)
    """
    if not candidates:
        return None

    attempted_urls = attempted_urls or []
    attempted_aliases = attempted_aliases or []

    # Normalize for comparison
    attempted_name_lower = attempted_name.lower()
    attempted_aliases_lower = [a.lower() for a in attempted_aliases]

    scored_candidates = []

    for candidate in candidates:
        score = 0

        # Get attributes safely (handles both dict and object)
        candidate_name = _get_attr(candidate, "name", "")
        candidate_alias_list = _get_attr(candidate, "alias_list", [])
        candidate_urls = _get_attr(candidate, "urls", [])
        candidate_disambiguation = _get_attr(candidate, "disambiguation", None)

        # 1. Exact name match (case-insensitive)
        if candidate_name.lower() == attempted_name_lower:
            score += 100

        # 2. Name in attempted aliases
        if candidate_name.lower() in attempted_aliases_lower:
            score += 80

        # 3. Exact alias match
        if candidate_alias_list:
            candidate_aliases_lower = [a.lower() for a in candidate_alias_list]
            # Check if attempted name is in candidate's aliases
            if attempted_name_lower in candidate_aliases_lower:
                score += 80
            # Check if any attempted aliases match candidate's aliases
            for attempted_alias in attempted_aliases_lower:
                if attempted_alias in candidate_aliases_lower:
                    score += 70

        # 4. URL match
        if attempted_urls and candidate_urls:
            for attempted_url in attempted_urls:
                for candidate_url in candidate_urls:
                    if urls_match(attempted_url, candidate_url):
                        score += 60
                        break  # Only count once per attempted URL

        # 5. Disambiguation handling
        if attempted_disambiguation:
            # If we have a disambiguation, prefer exact match
            if candidate_disambiguation == attempted_disambiguation:
                score += 40
        # If we don't have a disambiguation, prefer candidates without one
        elif not candidate_disambiguation:
            score += 20

        scored_candidates.append((score, candidate))

    # Sort by score descending
    scored_candidates.sort(key=lambda x: x[0], reverse=True)

    # Return highest scoring candidate if score is meaningful (>= 60 points)
    if scored_candidates and scored_candidates[0][0] >= 60:
        return scored_candidates[0][1]

    return None
