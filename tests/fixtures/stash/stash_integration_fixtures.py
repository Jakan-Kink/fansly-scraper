"""Common fixtures for StashProcessing integration tests - REAL OBJECTS.

These fixtures use REAL database objects and REAL Stash API connections:
- ✅ Real PostgreSQL database with UUID isolation per test
- ✅ Real FanslyConfig (not mocked)
- ✅ Real Database instances
- ✅ Real Account, Media, Post, Message objects from FactoryBoy
- ✅ Real StashContext connecting to Docker Stash (localhost:9999)
- ✅ No MockConfig, no MockDatabase, no fake attributes
- ✅ Stash API calls hit real Docker instance (or can be mocked per test)

Philosophy:
- Mock ONLY external services when necessary (Stash API can be mocked OR real)
- Use REAL database objects everywhere
- Use factories for test data creation
"""

import time
from contextlib import contextmanager
from dataclasses import dataclass
from unittest.mock import patch

import httpx
import pytest
import pytest_asyncio
import respx
from faker import Faker
from stash_graphql_client.types import StashID, Studio

from fileio.fnmanip import extract_media_id
from metadata import AccountMedia
from stash.processing import StashProcessing
from tests.fixtures.metadata.metadata_factories import (
    AccountMediaBundleFactory,
    AccountMediaFactory,
    MediaFactory,
)


# ============================================================================
# Helper Functions
# ============================================================================


def _clear_stash_client_caches(client):
    """Clear all LRU caches from StashClient to prevent test pollution.

    The StashClient may use @async_lru_cache decorators on find_* methods.
    These caches persist between sequential tests, causing stale data issues.
    This function clears all known caches if they exist.

    Note: In stash-graphql-client v0.5.0+, caching behavior may have changed.
    We safely check for cache_clear() before calling it.
    """
    # List of method names that might have caches
    cache_methods = [
        "find_performer",
        "find_performers",
        "find_studio",
        "find_studios",
        "find_scene",
        "find_scenes",
        "find_image",
        "find_images",
        "find_gallery",
        "find_galleries",
        "find_tag",
        "find_tags",
        "find_marker",
        "find_markers",
    ]

    for method_name in cache_methods:
        if hasattr(client, method_name):
            method = getattr(client, method_name)
            # Only clear cache if the method has cache_clear (i.e., is decorated with @lru_cache)
            if hasattr(method, "cache_clear"):
                method.cache_clear()


def _get_id(obj):
    """Get ID from either a dict or an object with id attribute.

    Handles both Strawberry objects and plain dicts for Pydantic transition.
    """
    return obj["id"] if isinstance(obj, dict) else obj.id


def _get_path(obj, default=None):
    """Get path from Pydantic model attributes.

    For Stash Image objects, extracts path from visual_files[0].path.
    For Stash Scene objects, extracts path from files[0].path.
    """
    # Try direct path first
    if hasattr(obj, "path") and obj.path:
        return obj.path

    # Try visual_files (images)
    if hasattr(obj, "visual_files") and obj.visual_files and len(obj.visual_files) > 0:
        return obj.visual_files[0].path

    # Try files (scenes)
    if hasattr(obj, "files") and obj.files and len(obj.files) > 0:
        return obj.files[0].path

    return default


# ============================================================================
# Configuration Fixtures
# ============================================================================


@pytest.fixture
def test_state(tmp_path):
    """Fixture for test download state using DownloadStateFactory.

    This creates a REAL DownloadState object with all required attributes.
    """
    from tests.fixtures.download import DownloadStateFactory

    # Create real paths
    base_path = tmp_path / "downloads"
    download_path = base_path / "test_user"
    download_path.mkdir(parents=True, exist_ok=True)

    return DownloadStateFactory(
        creator_id="12345",
        creator_name="test_user",
        base_path=base_path,
        download_path=download_path,
    )


# ============================================================================
# Stash Object Fixtures (Stash API objects - use mocks for external API)
# ============================================================================
# NOTE: Metadata fixtures (Account, Media, Post, Message, etc.) have been moved
# to tests/fixtures/metadata_fixtures.py for better separation of concerns.


@pytest.fixture
def mock_permissions():
    """Fixture for mock content permissions.

    This is just a dict, not a database object, so we keep it as-is.
    """
    return {
        "permissionFlags": [
            {
                "id": "perm_123",
                "type": 0,
                "flags": 2,
                "price": 0,
                "metadata": "",
                "validAfter": None,
                "validBefore": None,
                "verificationFlags": 2,
                "verificationMetadata": "{}",
            }
        ],
        "accountPermissionFlags": {
            "flags": 6,
            "metadata": '{"4":"{\\"subscriptionTierId\\":\\"tier_123\\"}"}',
        },
    }


# REMOVED: integration_mock_performer, integration_mock_studio, integration_mock_scene
# REMOVED: mock_gallery, mock_image
# These are duplicate MagicMock fixtures.
# ✅ Use real factories from stash_type_factories.py instead:
#    - PerformerFactory / mock_performer
#    - StudioFactory / mock_studio
#    - SceneFactory / mock_scene
#    - GalleryFactory / mock_gallery
#    - ImageFactory / mock_image


# ============================================================================
# StashProcessing Fixtures
# ============================================================================


@pytest_asyncio.fixture
async def real_stash_processor(config, test_database_sync, test_state, stash_context):
    """Fixture for StashProcessing with REAL database and REAL Docker Stash.

    This is for TRUE integration tests that hit the real Stash instance.
    All HTTP calls go to the actual Docker Stash server (localhost:9999).

    Args:
        config: Real FanslyConfig with UUID-isolated database
        test_database_sync: Real Database instance
        test_state: Real TestState (download state)
        stash_context: Real StashContext connected to Docker (localhost:9999)

    Yields:
        StashProcessing: Fully functional processor hitting real Docker Stash
    """
    # Set up config with real database and real stash
    config._database = test_database_sync
    config._stash = stash_context

    # Initialize the client (will make REAL HTTP calls)
    await stash_context.get_client()

    # Disable prints for testing
    with (
        patch("textio.textio.print_info"),
        patch("textio.textio.print_warning"),
        patch("textio.textio.print_error"),
    ):
        processor = StashProcessing.from_config(config, test_state)
        yield processor
        # Cleanup: Clear LRU caches to prevent pollution in sequential test execution
        # Only clear if client is still initialized (some tests call cleanup() which closes client)
        if processor.context._client is not None:
            _clear_stash_client_caches(processor.context.client)


@pytest_asyncio.fixture
async def respx_stash_processor(config, test_database_sync, test_state, stash_context):
    """Fixture for StashProcessing with respx HTTP mocking enabled.

    This is for unit tests that want to mock HTTP responses to Stash.
    Use @respx.mock decorator and add routes in your test to override defaults.

    Args:
        config: Real FanslyConfig with UUID-isolated database
        test_database_sync: Real Database instance
        test_state: Real TestState (download state)
        stash_context: Real StashContext but using a respx wrapper (localhost:9999)

    Yields:
        StashProcessing: Processor with mocked HTTP responses

    Example:
        @pytest.mark.asyncio
        async def test_something(respx_stash_processor):
            # Add specific mock route for your test
            respx.post("http://localhost:9999/graphql").mock(
                side_effect=[
                    httpx.Response(200, json={"data": {"findScenes": {...}}}),       # Call 1
                    httpx.Response(200, json={"data": {"findPerformers": {...}}}),   # Call 2
                    httpx.Response(200, json={"data": {"findStudios": {...}}}),      # Call 3
                    httpx.Response(200, json={"data": {"findStudios": {...}}}),      # Call 4
                    httpx.Response(200, json={"data": {"sceneUpdate": {...}}}),      # Call 5
                ]
            )
            # Now respx_stash_processor will use your mocked responses
    """
    # Set up config with real database and real stash
    config._database = test_database_sync
    config._stash = stash_context

    # Set up default respx mock for GraphQL endpoint
    # Tests can add more specific mocks as needed
    with respx.mock:
        # Default response for any GraphQL requests
        respx.post("http://localhost:9999/graphql").mock(
            return_value=httpx.Response(200, json={"data": {}})
        )

        # Initialize the client (will use mocked HTTP)
        await stash_context.get_client()

        # Disable prints for testing
        with (
            patch("textio.textio.print_info"),
            patch("textio.textio.print_warning"),
            patch("textio.textio.print_error"),
        ):
            processor = StashProcessing.from_config(config, test_state)
            yield processor
            # Cleanup: Clear LRU caches to prevent pollution in sequential test execution
            # All find_* methods use @async_lru_cache which persists between tests
            _clear_stash_client_caches(processor.context.client)
            # Reset respx to prevent route pollution
            respx.reset()


@pytest.fixture
def fansly_network_studio():
    """Fixture providing the 'Fansly (network)' studio from production.

    This matches the real studio data from production Stash instance.
    Used to mock find_studios("Fansly (network)") calls.
    """
    return Studio(
        id="246",
        name="Fansly (network)",
        urls=["https://fansly.com"],
        parent_studio=None,
        aliases=[],
        tags=[],
        stash_ids=[
            StashID(
                stash_id="f03173b3-1c0e-43bc-ac30-0cc445316c80",
                endpoint="https://fansdb.cc/graphql",
            )
        ],
        details="",
    )


@pytest.fixture
def mock_find_studios(fansly_network_studio):
    """Fixture providing mock functions for find_studios calls.

    Returns a tuple of (mock_find_studios_fn, create_creator_studio_fn):
    - mock_find_studios_fn: Async function to mock find_studios("Fansly (network)")
    - create_creator_studio_fn: Helper to create a creator studio with the network as parent

    This allows tests to mock both the network studio lookup and creator studio creation.

    Example:
        mock_find_studios_fn, create_creator_studio_fn = mock_find_studios
        respx.post("http://localhost:9999/graphql").mock(
            side_effect=mock_find_studios_fn
        )
        creator_studio = create_creator_studio_fn("creator_username", "123")
    """

    async def mock_find_studios_fn(request):
        """Mock function for find_studios GraphQL calls."""
        # Return the Fansly (network) studio for queries matching it
        return httpx.Response(
            200,
            json={
                "data": {
                    "findStudios": {
                        "count": 1,
                        "studios": [
                            {
                                "id": fansly_network_studio.id,
                                "name": fansly_network_studio.name,
                                "url": fansly_network_studio.url,
                            }
                        ],
                    }
                }
            },
        )

    def create_creator_studio(creator_username: str, studio_id: str = "999"):
        """Create a creator studio with Fansly network as parent."""
        return Studio(
            id=studio_id,
            name=f"{creator_username} (Fansly)",
            url=f"https://fansly.com/{creator_username}",
            parent_studio=fansly_network_studio,
        )

    return mock_find_studios_fn, create_creator_studio


# ============================================================================
# GraphQL Call Inspection Fixtures
# ============================================================================


@contextmanager
def capture_graphql_calls(stash_client):
    """Context manager to capture GraphQL calls made to Stash in integration tests.

    This patches stash_client._session.execute to intercept and record all GraphQL
    operations, variables, and raw responses from the gql library while still making
    real calls to Stash. Captures at the gql boundary BEFORE StashClient error handling.

    Args:
        stash_client: The StashClient instance to monitor

    Yields:
        list: List of dicts with 'query', 'variables', 'result', and 'exception' for each call

    Example:
        async with stash_cleanup_tracker(stash_client) as cleanup:
            with capture_graphql_calls(stash_client) as calls:
                # Make real calls to Stash
                studio = await real_stash_processor._find_existing_studio(account)

                # Verify call sequence (permanent assertion)
                assert len(calls) == 3, "Expected 3 GraphQL calls"
                assert "findStudios" in calls[0]["query"]
                assert "findStudios" in calls[1]["query"]
                assert "studioCreate" in calls[2]["query"]

                # Check for errors
                if calls[0]["exception"]:
                    assert "GRAPHQL_VALIDATION_FAILED" in str(calls[0]["exception"])
    """
    calls = []
    original_session_execute = stash_client._session.execute

    async def capture_session_execute(operation, variable_values=None):
        """Capture the call details at the gql boundary.

        Captures raw gql operations and responses before StashClient error handling.
        """
        result = None
        exception = None
        try:
            result = await original_session_execute(
                operation, variable_values=variable_values
            )
        except Exception as e:
            exception = e
            raise
        else:
            return result
        finally:
            # Always log the call with raw gql result or exception
            calls.append(
                {
                    "query": str(operation),  # Convert gql DocumentNode to string
                    "variables": variable_values,
                    "result": dict(result) if result else None,
                    "exception": exception,
                }
            )

    # Patch the _session.execute method at the gql boundary
    with patch.object(
        stash_client._session, "execute", side_effect=capture_session_execute
    ):
        yield calls


# ============================================================================
# Message Media Generation Fixture
# ============================================================================


@dataclass
class MessageMediaMetadata:
    """Metadata about generated media for call count assertions.

    Attributes:
        num_images: Number of images created
        num_videos: Number of videos created
        total_media: Total media items (images + videos)
        has_bundle: True if images were bundled (>3 images)
        media_items: List of built Media objects (not committed - test sets accountId)
        account_media_items: List of built AccountMedia objects (not committed)
        bundle: Built AccountMediaBundle if has_bundle, else None (not committed)
        bundle_media_links: List of dicts with bundle linking metadata
    """

    num_images: int
    num_videos: int
    total_media: int
    has_bundle: bool
    media_items: list  # List of Media objects (built, not committed)
    account_media_items: list[AccountMedia]  # Built AccountMedia objects
    bundle: object | None  # AccountMediaBundle or None (built, not committed)
    bundle_media_links: list[
        dict
    ]  # Bundle linking metadata: [{"account_media": obj, "pos": int}]


@dataclass
class MultiObjectMediaMetadata:
    """Wrapper for media distributed across multiple objects (posts/messages).

    Supports list-like access: len(wrapper) and wrapper[index]

    Attributes:
        total_images: Total images across all distributions
        total_videos: Total videos across all distributions
        total_media: Total media items across all distributions
        distributions: List of MessageMediaMetadata for each object

    Example:
        # Generate media for 3 posts
        media_meta = await message_media_generator(spread_over_objs=3)
        print(f"Total: {media_meta.total_images} images")
        print(f"Distributions: {len(media_meta)}")  # 3

        # Access individual distribution
        for i in range(len(media_meta)):
            dist = media_meta[i]  # MessageMediaMetadata for object i
            print(f"Object {i}: {dist.num_images} images")
    """

    total_images: int
    total_videos: int
    total_media: int
    distributions: list[MessageMediaMetadata]

    def __len__(self) -> int:
        """Return number of distributions (spread_over_objs)."""
        return len(self.distributions)

    def __getitem__(self, index: int) -> MessageMediaMetadata:
        """Access individual distribution by index."""
        return self.distributions[index]

    def __setitem__(self, index: int, obj: MessageMediaMetadata) -> None:
        """Set individual distribution by index."""
        self.distributions[index] = obj


@pytest_asyncio.fixture
async def message_media_generator(factory_session, real_stash_processor):
    """Generate realistic message media using Docker Stash data and Faker.

    This fixture returns a factory function that can accept parameters.

    Args:
        factory_session: Database session for creating objects
        real_stash_processor: StashProcessing with real Stash connection

    Returns:
        Factory function that accepts:
            spread_over_objs (int): Number of objects (posts/messages) to distribute media across
                - Default: 1 (single MessageMediaMetadata)
                - When > 1: Returns MultiObjectMediaMetadata with distributions

    Example:
        async def test_single_post(message_media_generator):
            # Generate media for single object (backward compatible)
            media_meta = await message_media_generator()
            # Returns: MessageMediaMetadata

        async def test_multiple_posts(message_media_generator):
            # Generate media for 3 posts
            media_meta = await message_media_generator(spread_over_objs=3)
            # Returns: MultiObjectMediaMetadata with len(media_meta) == 3
            for i in range(len(media_meta)):
                dist = media_meta[i]  # MessageMediaMetadata for post i
    """

    async def _generate_media(
        spread_over_objs: int = 1,
    ) -> MessageMediaMetadata | MultiObjectMediaMetadata:
        """Generate media distributed across specified number of objects.

        Args:
            spread_over_objs: Number of objects to distribute media across

        Returns:
            MessageMediaMetadata (when spread_over_objs==1)
            MultiObjectMediaMetadata (when spread_over_objs>1)
        """
        # Use a truly random seed, independent of pytest-randomly
        # time_ns() provides nanosecond precision for better parallel test isolation
        random_seed = int(time.monotonic_ns()) % (2**32)
        faker = Faker()
        faker.seed_instance(random_seed)
        client = real_stash_processor.context.client

        # STEP 1: Get total counts from Docker Stash (will be cached)
        images_result = await client.find_images()
        scenes_result = await client.find_scenes()

        if images_result.count == 0 and scenes_result.count == 0:
            pytest.skip(
                "Docker Stash has no images or scenes; cannot generate message media."
            )

        total_images_available = images_result.count
        total_scenes_available = scenes_result.count

        # If spread_over_objs > 1, reserve buffer for distribution validation
        reserved_buffer = 10 if spread_over_objs > 1 else 0
        max_images_for_distribution = total_images_available - reserved_buffer
        max_scenes_for_distribution = total_scenes_available - reserved_buffer

        # Helper functions for multi-object distribution
        def build_obj_counts(num_objs: int) -> list[dict[str, int]]:
            """Generate media counts for each object.

            Returns:
                List of dicts with 'num_images' and 'num_videos' for each object
            """
            counts_list = []
            for _i in range(num_objs):
                # Images: 0-30 with 75% chance of at least 1
                num_images = faker.random_int(min=0, max=30)
                if num_images == 0 and faker.random.random() < 0.75:
                    num_images = faker.random_int(min=1, max=30)

                # Videos: Weighted distribution (0=60%, 1=25%, 2=10%, 3=5%)
                video_roll = faker.random.random()
                if video_roll < 0.60:
                    num_videos = 0
                elif video_roll < 0.85:
                    num_videos = 1
                elif video_roll < 0.95:
                    num_videos = 2
                else:
                    num_videos = 3

                counts_list.append({"num_images": num_images, "num_videos": num_videos})

            return counts_list

        def validate_media_obj_counts(counts_list: list[dict[str, int]]) -> bool:
            """Validate that media counts don't exceed available media.

            Args:
                counts_list: List of dicts with 'num_images' and 'num_videos'

            Returns:
                True if valid, False if exceeds limits or any object has 0 total
            """
            total_images_needed = sum(c["num_images"] for c in counts_list)
            total_videos_needed = sum(c["num_videos"] for c in counts_list)

            # Check total doesn't exceed available (minus buffer)
            if total_images_needed > max_images_for_distribution:
                return False
            if total_videos_needed > max_scenes_for_distribution:
                return False

            # Each object must have at least 1 media item
            for counts in counts_list:
                if counts["num_images"] + counts["num_videos"] == 0:
                    return False

            return True

        def allocate_page_indices(counts_list: list[dict[str, int]]) -> list[dict]:
            """Allocate unique page indices for each object.

            Args:
                counts_list: List of {"num_images": int, "num_videos": int}

            Returns:
                List of dicts with num_images, num_videos, image_pages, video_pages
            """
            # Calculate totals
            total_images_needed = sum(c["num_images"] for c in counts_list)
            total_videos_needed = sum(c["num_videos"] for c in counts_list)

            # Sample unique pages from available pool (no overlap)
            all_image_pages = (
                faker.random.sample(
                    range(1, total_images_available + 1), total_images_needed
                )
                if total_images_needed > 0
                else []
            )
            all_video_pages = (
                faker.random.sample(
                    range(1, total_scenes_available + 1), total_videos_needed
                )
                if total_videos_needed > 0
                else []
            )

            # Distribute pages to each object
            result = []
            img_offset = 0
            vid_offset = 0

            for counts in counts_list:
                num_imgs = counts["num_images"]
                num_vids = counts["num_videos"]

                result.append(
                    {
                        "num_images": num_imgs,
                        "num_videos": num_vids,
                        "image_pages": all_image_pages[
                            img_offset : img_offset + num_imgs
                        ],
                        "video_pages": all_video_pages[
                            vid_offset : vid_offset + num_vids
                        ],
                    }
                )

                img_offset += num_imgs
                vid_offset += num_vids

            return result

        # STEP 2: Generate and validate media counts
        valid_obj_counts = False
        while not valid_obj_counts:
            media_obj_counts = build_obj_counts(spread_over_objs)
            valid_obj_counts = validate_media_obj_counts(media_obj_counts)

        # STEP 3: Allocate unique page indices for each object
        obj_data_list = allocate_page_indices(media_obj_counts)

        async def build_media_metadata(obj_data: dict) -> MessageMediaMetadata:
            """Build MessageMediaMetadata for a single object using pre-allocated indices.

            Args:
                obj_data: Dict with 'num_images', 'num_videos', 'image_pages', 'video_pages'

            Returns:
                MessageMediaMetadata with all media objects built
            """
            num_images = obj_data["num_images"]
            num_videos = obj_data["num_videos"]
            image_pages = obj_data["image_pages"]
            video_pages = obj_data["video_pages"]

            # STEP 4: Fetch specific media using unique random pages and build in-memory objects
            media_items = []  # Store built Media objects
            account_media_items = []
            bundle = None
            has_bundle = num_images > 3
            bundle_media_links = []  # Track bundle linking data for tests to apply after commit

            # Handle images
            if has_bundle:
                # Build AccountMediaBundle (not committed - test will set accountId and commit)
                bundle = AccountMediaBundleFactory.build()

                for i, page_num in enumerate(image_pages):
                    # Fetch one specific image by page number
                    image_result = await client.find_images(
                        filter_={"per_page": 1, "page": page_num}
                    )

                    if image_result.count > 0:
                        real_image = image_result.images[0]
                        # Handle both dict (GraphQL) and object responses (Pydantic transition)
                        stash_image_id = _get_id(real_image)
                        image_path = _get_path(
                            real_image, f"/stash/media/image_{stash_image_id}.jpg"
                        )

                        # Extract Fansly Media ID from filename (e.g., _id_626832468225302528)
                        fansly_media_id = extract_media_id(image_path)
                        used_fallback = fansly_media_id is None
                        if used_fallback:
                            # Fallback to using Stash ID if filename doesn't contain _id_ pattern
                            fansly_media_id = int(stash_image_id)

                        # Build Media object (not committed - test will set accountId and commit)
                        # 33% chance to include stash_id (tests both code paths)
                        # EXCEPTION: If fallback was used, MUST include stash_id since
                        # Media.id won't be in file path for path-based lookups
                        media_kwargs = {
                            "id": fansly_media_id,  # Fansly API Media ID from filename
                            "mimetype": "image/jpeg",
                            "type": 1,  # Image
                            "is_downloaded": True,
                            "local_filename": image_path,
                        }
                        if used_fallback or faker.random.random() < 0.33:
                            media_kwargs["stash_id"] = int(stash_image_id)

                        media = MediaFactory.build(**media_kwargs)
                        media_items.append(media)  # Store for test to commit

                        # Build AccountMedia without accountId (test will set it)
                        account_media = AccountMediaFactory.build(
                            mediaId=media.id, accountId=None
                        )

                        # Store bundle link metadata (position in bundle)
                        bundle_media_links.append(
                            {"account_media": account_media, "pos": i}
                        )
                        account_media_items.append(account_media)
            else:
                # Individual AccountMedia for ≤3 images
                for _i, page_num in enumerate(image_pages):
                    image_result = await client.find_images(
                        filter_={"per_page": 1, "page": page_num}
                    )
                    if image_result.count > 0:
                        real_image = image_result.images[0]
                        # Handle both dict (GraphQL) and object responses (Pydantic transition)
                        stash_image_id = _get_id(real_image)
                        image_path = _get_path(
                            real_image, f"/stash/media/image_{stash_image_id}.jpg"
                        )

                        # Extract Fansly Media ID from filename (e.g., _id_626832468225302528)
                        fansly_media_id = extract_media_id(image_path)
                        used_fallback = fansly_media_id is None
                        if used_fallback:
                            # Fallback to using Stash ID if filename doesn't contain _id_ pattern
                            fansly_media_id = int(stash_image_id)

                        # Build Media object (not committed)
                        # 33% chance to include stash_id (tests both code paths)
                        # EXCEPTION: If fallback was used, MUST include stash_id since
                        # Media.id won't be in file path for path-based lookups
                        media_kwargs = {
                            "id": fansly_media_id,  # Fansly API Media ID from filename
                            "mimetype": "image/jpeg",
                            "type": 1,
                            "is_downloaded": True,
                            "local_filename": image_path,
                        }
                        if used_fallback or faker.random.random() < 0.33:
                            media_kwargs["stash_id"] = int(stash_image_id)

                        media = MediaFactory.build(**media_kwargs)
                        media_items.append(media)  # Store for test to commit

                        # Build AccountMedia without accountId (test will set it)
                        account_media = AccountMediaFactory.build(
                            mediaId=media.id, accountId=None
                        )
                        account_media_items.append(account_media)

            # Handle videos
            for _i, page_num in enumerate(video_pages):
                scene_result = await client.find_scenes(
                    filter_={"per_page": 1, "page": page_num}
                )
                if scene_result.count > 0:
                    real_scene = scene_result.scenes[0]
                    # Handle both dict (GraphQL) and object responses (Pydantic transition)
                    scene_id = _get_id(real_scene)

                    # Extract path from video file if available
                    video_path = None
                    files = (
                        real_scene.get("files")
                        if isinstance(real_scene, dict)
                        else getattr(real_scene, "files", None)
                    )
                    if files and len(files) > 0:
                        video_file = files[0]
                        video_path = _get_path(video_file)

                    # Extract Fansly Media ID from filename (e.g., _id_626832468225302528)
                    fansly_media_id = (
                        extract_media_id(video_path) if video_path else None
                    )
                    used_fallback = fansly_media_id is None
                    if used_fallback:
                        # Fallback to using Stash ID if filename doesn't contain _id_ pattern
                        fansly_media_id = int(scene_id)

                    # Build Media object (not committed)
                    # 33% chance to include stash_id (tests both code paths)
                    # EXCEPTION: If fallback was used, MUST include stash_id since
                    # Media.id won't be in file path for path-based lookups
                    media_kwargs = {
                        "id": fansly_media_id,  # Fansly API Media ID from filename
                        "mimetype": "video/mp4",
                        "type": 2,  # Video
                        "is_downloaded": True,
                        "local_filename": video_path
                        or f"/stash/media/video_{scene_id}.mp4",
                    }
                    if used_fallback or faker.random.random() < 0.33:
                        media_kwargs["stash_id"] = int(scene_id)

                    media = MediaFactory.build(**media_kwargs)
                    media_items.append(media)  # Store for test to commit

                    # Build AccountMedia without accountId (test will set it)
                    account_media = AccountMediaFactory.build(
                        mediaId=media.id, accountId=None
                    )
                    account_media_items.append(account_media)

            total_media = num_images + num_videos

            # Clear caches so fixture setup queries don't pollute test call counts
            _clear_stash_client_caches(client)

            return MessageMediaMetadata(
                num_images=num_images,
                num_videos=num_videos,
                total_media=total_media,
                has_bundle=has_bundle,
                media_items=media_items,  # Built Media objects (not committed)
                account_media_items=account_media_items,  # Built AccountMedia objects
                bundle=bundle,  # Built bundle (not committed)
                bundle_media_links=bundle_media_links,  # Bundle linking metadata
            )

        # STEP 4: Build metadata for single or multiple objects
        if spread_over_objs == 1:
            # Single object - return MessageMediaMetadata directly (backward compatible)
            return await build_media_metadata(obj_data_list[0])

        # Multiple objects - build MultiObjectMediaMetadata
        return_value = MultiObjectMediaMetadata(
            total_images=0,
            total_videos=0,
            total_media=0,
            distributions=[None] * spread_over_objs,  # type: ignore
        )

        # Build metadata for each object
        for i, obj_data in enumerate(obj_data_list):
            return_value[i] = await build_media_metadata(obj_data)

        # Calculate totals across all distributions
        return_value.total_images = sum(
            d.num_images for d in return_value.distributions
        )
        return_value.total_videos = sum(
            d.num_videos for d in return_value.distributions
        )
        return_value.total_media = sum(
            d.total_media for d in return_value.distributions
        )

        return return_value

    # Return the factory function
    return _generate_media
