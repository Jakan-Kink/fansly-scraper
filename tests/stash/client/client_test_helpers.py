"""Helper functions for testing Stash client mixins."""

from unittest.mock import AsyncMock, MagicMock, create_autospec

from stash import StashClient
from stash.client_helpers import async_lru_cache


def create_base_mock_client() -> StashClient:
    """Create a base mock StashClient for testing.

    Sets up a mock StashClient with common attributes and methods that
    any mixin would need, but doesn't implement specific mixin functionality.

    Returns:
        StashClient: A basic mock StashClient instance
    """
    # Create a mock StashClient
    client = create_autospec(StashClient, instance=True)

    # Add common logging and cache attributes
    client.log = AsyncMock()
    client.execute = AsyncMock()

    # Return the base client
    return client


def add_cache_attributes(client: StashClient, cache_names: list[str]) -> None:
    """Add cache attributes to a mock client.

    Args:
        client: The mock client to add cache attributes to
        cache_names: List of cache attribute names to add
    """
    for cache_name in cache_names:
        # Create both the cache dict and the LRU cache clear method
        setattr(client, cache_name, {})

        # Create a mock cache_clear method for the mocked async_lru_cache
        cache_clear_mock = MagicMock()
        setattr(client, f"{cache_name}_cache_clear", cache_clear_mock)


def create_async_cached_method(
    client: StashClient, method_name: str, return_type: type
):
    """Create an async cached method for a mock client.

    Args:
        client: The mock client to add the method to
        method_name: Name of the method to create
        return_type: Type to instantiate from the result

    Returns:
        AsyncMock: The created async mock method
    """

    @async_lru_cache(maxsize=3096, exclude_arg_indices=[0])
    async def mock_method(*args, **kwargs):
        # Method name without 'find_' prefix is used for logging in real implementation
        # but not needed in our mock
        result = await client.execute({method_name: None})

        if result and result.get(method_name):
            result_data = result[method_name]

            # Clean the data to prevent _dirty_attrs errors
            if isinstance(result_data, dict):
                clean_data = {
                    k: v
                    for k, v in result_data.items()
                    if not k.startswith("_") and k != "client_mutation_id"
                }
                return return_type(**clean_data)
            elif isinstance(result_data, list):
                # Handle list returns (for methods like find_tags)
                return [
                    return_type(
                        **{
                            k: v
                            for k, v in item.items()
                            if not k.startswith("_") and k != "client_mutation_id"
                        }
                    )
                    for item in result_data
                ]

        # Return None or empty container for not found
        if return_type == list:
            return []
        return None

    # Set the method on the client
    setattr(client, method_name, mock_method)
    return mock_method
