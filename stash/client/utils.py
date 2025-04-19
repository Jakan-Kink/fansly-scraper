"""Utility functions for the Stash client.

This module provides common utility functions used by various client mixins
and other components of the Stash client.
"""

from typing import Any


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
