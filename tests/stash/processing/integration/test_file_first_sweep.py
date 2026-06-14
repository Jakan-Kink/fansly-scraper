import pytest
from stash_graphql_client.types import BaseFile, ImageFile, VideoFile

from tests.fixtures.stash.stash_api_fixtures import dump_graphql_calls
from tests.fixtures.stash.stash_integration_fixtures import capture_graphql_calls


@pytest.mark.integration
@pytest.mark.asyncio
async def test_sweep_yields_typed_files(real_stash_processor, stash_cleanup_tracker):
    client = real_stash_processor.context.client
    async with stash_cleanup_tracker(client):
        try:
            with capture_graphql_calls(client) as calls:
                files = [f async for f in real_stash_processor._sweep_creator_files()]
        finally:
            dump_graphql_calls(calls, "sweep_yields_typed_files")
    if not files:
        pytest.skip("Docker Stash library is empty under base_path")
    assert all(isinstance(f, BaseFile) for f in files)
    # polymorphic: at least one concrete subtype, never a bare BaseFile
    assert any(isinstance(f, (VideoFile, ImageFile)) for f in files)
