"""Unit tests for NotImplementedClientMixin."""

import pytest

from stash import StashClient
from stash.client.mixins.not_implemented import NotImplementedClientMixin


@pytest.fixture
def not_implemented_client() -> NotImplementedClientMixin:
    """Create a NotImplementedClientMixin instance for testing."""
    return NotImplementedClientMixin()


@pytest.mark.asyncio
async def test_file_operations(
    not_implemented_client: NotImplementedClientMixin,
) -> None:
    """Test that file operations raise NotImplementedError."""
    with pytest.raises(NotImplementedError, match="File operations not implemented"):
        await not_implemented_client.move_files({})

    with pytest.raises(NotImplementedError, match="File operations not implemented"):
        await not_implemented_client.delete_files([])

    with pytest.raises(NotImplementedError, match="File operations not implemented"):
        await not_implemented_client.file_set_fingerprints({})


@pytest.mark.asyncio
async def test_configuration(not_implemented_client: NotImplementedClientMixin) -> None:
    """Test that configuration operations raise NotImplementedError."""
    with pytest.raises(NotImplementedError, match="Configuration not implemented"):
        await not_implemented_client.configure_general({})

    with pytest.raises(NotImplementedError, match="Configuration not implemented"):
        await not_implemented_client.configure_interface({})

    with pytest.raises(NotImplementedError, match="Configuration not implemented"):
        await not_implemented_client.configure_dlna({})

    with pytest.raises(NotImplementedError, match="Configuration not implemented"):
        await not_implemented_client.configure_scraping({})

    with pytest.raises(NotImplementedError, match="Configuration not implemented"):
        await not_implemented_client.configure_defaults({})

    with pytest.raises(NotImplementedError, match="Configuration not implemented"):
        await not_implemented_client.configure_plugin("plugin_id", {})

    with pytest.raises(NotImplementedError, match="Configuration not implemented"):
        await not_implemented_client.configure_ui({})

    with pytest.raises(NotImplementedError, match="Configuration not implemented"):
        await not_implemented_client.configure_ui_setting("key", "value")

    with pytest.raises(NotImplementedError, match="Configuration not implemented"):
        await not_implemented_client.generate_api_key({})


@pytest.mark.asyncio
async def test_import_export(not_implemented_client: NotImplementedClientMixin) -> None:
    """Test that import/export operations raise NotImplementedError."""
    with pytest.raises(NotImplementedError, match="Import/Export not implemented"):
        await not_implemented_client.export_objects({})

    with pytest.raises(NotImplementedError, match="Import/Export not implemented"):
        await not_implemented_client.import_objects({})

    with pytest.raises(NotImplementedError, match="Import/Export not implemented"):
        await not_implemented_client.metadata_import()

    with pytest.raises(NotImplementedError, match="Import/Export not implemented"):
        await not_implemented_client.metadata_export()

    with pytest.raises(NotImplementedError, match="Import/Export not implemented"):
        await not_implemented_client.metadata_auto_tag({})

    with pytest.raises(NotImplementedError, match="Import/Export not implemented"):
        await not_implemented_client.metadata_identify({})

    with pytest.raises(NotImplementedError, match="Import/Export not implemented"):
        await not_implemented_client.metadata_clean({})

    with pytest.raises(NotImplementedError, match="Import/Export not implemented"):
        await not_implemented_client.metadata_clean_generated({})


@pytest.mark.asyncio
async def test_queries(not_implemented_client: NotImplementedClientMixin) -> None:
    """Test that query operations raise NotImplementedError."""
    with pytest.raises(NotImplementedError, match="Scene hash lookup not implemented"):
        await not_implemented_client.find_scene_by_hash({})

    with pytest.raises(NotImplementedError, match="Scene streams not implemented"):
        await not_implemented_client.scene_streams("123")

    with pytest.raises(NotImplementedError, match="Marker wall not implemented"):
        await not_implemented_client.marker_wall()

    with pytest.raises(NotImplementedError, match="Marker strings not implemented"):
        await not_implemented_client.marker_strings()

    with pytest.raises(NotImplementedError, match="Stats not implemented"):
        await not_implemented_client.stats()

    with pytest.raises(NotImplementedError, match="Logs not implemented"):
        await not_implemented_client.logs()


@pytest.mark.asyncio
async def test_scene_mutations(
    not_implemented_client: NotImplementedClientMixin,
) -> None:
    """Test that scene mutation operations raise NotImplementedError."""
    with pytest.raises(NotImplementedError, match="Scene deletion not implemented"):
        await not_implemented_client.scene_destroy({})

    with pytest.raises(NotImplementedError, match="Scene merging not implemented"):
        await not_implemented_client.scene_merge({})

    with pytest.raises(NotImplementedError, match="Scene deletion not implemented"):
        await not_implemented_client.scenes_destroy([])

    with pytest.raises(NotImplementedError, match="Scene O-count not implemented"):
        await not_implemented_client.scene_add_o("123")

    with pytest.raises(NotImplementedError, match="Scene O-count not implemented"):
        await not_implemented_client.scene_delete_o("123")

    with pytest.raises(NotImplementedError, match="Scene O-count not implemented"):
        await not_implemented_client.scene_reset_o("123")

    with pytest.raises(NotImplementedError, match="Scene activity not implemented"):
        await not_implemented_client.scene_save_activity("123")

    with pytest.raises(NotImplementedError, match="Scene activity not implemented"):
        await not_implemented_client.scene_reset_activity("123")

    with pytest.raises(NotImplementedError, match="Scene play count not implemented"):
        await not_implemented_client.scene_add_play("123")

    with pytest.raises(NotImplementedError, match="Scene play count not implemented"):
        await not_implemented_client.scene_delete_play("123")

    with pytest.raises(NotImplementedError, match="Scene play count not implemented"):
        await not_implemented_client.scene_reset_play_count("123")


@pytest.mark.asyncio
async def test_scene_marker_mutations(
    not_implemented_client: NotImplementedClientMixin,
) -> None:
    """Test that scene marker mutation operations raise NotImplementedError."""
    with pytest.raises(NotImplementedError, match="Scene markers not implemented"):
        await not_implemented_client.scene_marker_create({})

    with pytest.raises(NotImplementedError, match="Scene markers not implemented"):
        await not_implemented_client.scene_marker_update({})

    with pytest.raises(NotImplementedError, match="Scene markers not implemented"):
        await not_implemented_client.scene_marker_destroy("123")

    with pytest.raises(NotImplementedError, match="Scene markers not implemented"):
        await not_implemented_client.scene_markers_destroy([])

    with pytest.raises(
        NotImplementedError, match="Scene file assignment not implemented"
    ):
        await not_implemented_client.scene_assign_file({})


@pytest.mark.asyncio
async def test_image_mutations(
    not_implemented_client: NotImplementedClientMixin,
) -> None:
    """Test that image mutation operations raise NotImplementedError."""
    with pytest.raises(NotImplementedError, match="Bulk image update not implemented"):
        await not_implemented_client.bulk_image_update({})

    with pytest.raises(NotImplementedError, match="Image deletion not implemented"):
        await not_implemented_client.images_destroy({})

    with pytest.raises(NotImplementedError, match="Image updates not implemented"):
        await not_implemented_client.images_update([])

    with pytest.raises(NotImplementedError, match="Image O-count not implemented"):
        await not_implemented_client.image_increment_o("123")

    with pytest.raises(NotImplementedError, match="Image O-count not implemented"):
        await not_implemented_client.image_decrement_o("123")

    with pytest.raises(NotImplementedError, match="Image O-count not implemented"):
        await not_implemented_client.image_reset_o("123")


@pytest.mark.asyncio
async def test_gallery_mutations(
    not_implemented_client: NotImplementedClientMixin,
) -> None:
    """Test that gallery mutation operations raise NotImplementedError."""
    with pytest.raises(
        NotImplementedError, match="Bulk gallery update not implemented"
    ):
        await not_implemented_client.bulk_gallery_update({})


@pytest.mark.asyncio
async def test_performer_mutations(
    not_implemented_client: NotImplementedClientMixin,
) -> None:
    """Test that performer mutation operations raise NotImplementedError."""
    with pytest.raises(NotImplementedError, match="Performer deletion not implemented"):
        await not_implemented_client.performer_destroy({})

    with pytest.raises(NotImplementedError, match="Performer deletion not implemented"):
        await not_implemented_client.performers_destroy([])

    with pytest.raises(
        NotImplementedError, match="Bulk performer update not implemented"
    ):
        await not_implemented_client.bulk_performer_update({})


@pytest.mark.asyncio
async def test_studio_mutations(
    not_implemented_client: NotImplementedClientMixin,
) -> None:
    """Test that studio mutation operations raise NotImplementedError."""
    with pytest.raises(NotImplementedError, match="Studio deletion not implemented"):
        await not_implemented_client.studio_destroy({})

    with pytest.raises(NotImplementedError, match="Studio deletion not implemented"):
        await not_implemented_client.studios_destroy([])


@pytest.mark.asyncio
async def test_tag_mutations(not_implemented_client: NotImplementedClientMixin) -> None:
    """Test that tag mutation operations raise NotImplementedError."""
    with pytest.raises(NotImplementedError, match="Tag deletion not implemented"):
        await not_implemented_client.tag_destroy({})

    with pytest.raises(NotImplementedError, match="Tag deletion not implemented"):
        await not_implemented_client.tags_destroy([])


@pytest.mark.asyncio
async def test_sql_operations(
    not_implemented_client: NotImplementedClientMixin,
) -> None:
    """Test that SQL operations raise NotImplementedError."""
    with pytest.raises(NotImplementedError, match="SQL queries not implemented"):
        await not_implemented_client.querySQL("SELECT 1")

    with pytest.raises(NotImplementedError, match="SQL execution not implemented"):
        await not_implemented_client.execSQL("INSERT INTO table VALUES (1)")


@pytest.mark.asyncio
async def test_stashbox_operations(
    not_implemented_client: NotImplementedClientMixin,
) -> None:
    """Test that stashbox operations raise NotImplementedError."""
    with pytest.raises(
        NotImplementedError, match="Stash-box validation not implemented"
    ):
        await not_implemented_client.validateStashBoxCredentials({})


@pytest.mark.asyncio
async def test_dlna_operations(
    not_implemented_client: NotImplementedClientMixin,
) -> None:
    """Test that DLNA operations raise NotImplementedError."""
    with pytest.raises(NotImplementedError, match="DLNA not implemented"):
        await not_implemented_client.dlnaStatus()


@pytest.mark.asyncio
async def test_job_operations(
    not_implemented_client: NotImplementedClientMixin,
) -> None:
    """Test that job operations raise NotImplementedError."""
    with pytest.raises(NotImplementedError, match="Job subscription not implemented"):
        await not_implemented_client.jobsSubscribe()


@pytest.mark.asyncio
async def test_version_operations(
    not_implemented_client: NotImplementedClientMixin,
) -> None:
    """Test that version operations raise NotImplementedError."""
    with pytest.raises(NotImplementedError, match="Version info not implemented"):
        await not_implemented_client.version()

    with pytest.raises(
        NotImplementedError, match="Latest version info not implemented"
    ):
        await not_implemented_client.latestversion()
