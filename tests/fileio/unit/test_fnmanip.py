"""Unit tests for the fnmanip module."""

import contextlib
from unittest.mock import patch

import pytest
from PIL import Image

from errors.mp4 import InvalidMP4Error
from fileio.fnmanip import (
    extract_media_id,
    get_hash_for_image,
    get_hash_for_other_content,
)


class TestExtractors:
    """Tests for the filename extractor functions."""

    def test_extract_media_id(self):
        """Test extract_media_id with valid and invalid filenames."""
        # Valid filenames with IDs
        assert extract_media_id("2023-01-01_at_12-30_UTC_id_123456.jpg") == 123456
        assert extract_media_id("some_prefix_id_789.mp4") == 789
        assert extract_media_id("path/to/file_id_101112.mp4") == 101112
        assert (
            extract_media_id("file_with_multiple_underscores_id_654321.jpg") == 654321
        )

        # Invalid filenames without IDs
        assert extract_media_id("2023-01-01_at_12-30_UTC.jpg") is None
        assert extract_media_id("no_id_here.mp4") is None
        assert extract_media_id("") is None
        assert extract_media_id("id_.jpg") is None
        assert extract_media_id("id_not_numeric.jpg") is None


class TestImageHash:
    """Tests for the image hashing functions."""

    def test_get_hash_for_image(self, tmp_path):
        """Test get_hash_for_image with a real image file."""
        # Create a real 1x1 pixel PNG image

        image_path = tmp_path / "test_image.png"
        img = Image.new("RGB", (1, 1), color="red")
        img.save(image_path)

        # Only mock imagehash.phash to control the return value
        with patch("fileio.fnmanip.imagehash.phash", return_value="test_hash_value"):
            result = get_hash_for_image(image_path)
            assert result == "test_hash_value"

    @pytest.mark.parametrize(
        ("file_kind", "phash_behavior", "match"),
        [
            ("invalid_bytes", None, "Failed to verify image"),
            ("valid_image", "returns_none", "Hash generation returned None"),
            ("valid_image", "raises", "Failed to hash image"),
            ("missing", None, "Failed to verify image"),
        ],
        ids=[
            "verify_fails_on_corrupted_file",
            "phash_returns_none",
            "phash_raises",
            "file_not_found",
        ],
    )
    def test_get_hash_for_image_error_paths(
        self, tmp_path, file_kind, phash_behavior, match
    ):
        """Error paths of get_hash_for_image raise RuntimeError.

        Corrupted bytes and a missing file both fail real PIL verification
        ("Failed to verify image") with no patching at all. The two phash arms
        use a real image and patch only the ``imagehash.phash`` leaf (per the
        mock taxonomy): returning None hits the "Hash generation returned
        None" guard; raising hits the "Failed to hash image" wrapper.
        """
        if file_kind == "invalid_bytes":
            path = tmp_path / "invalid.jpg"
            path.write_bytes(b"This is not a valid image file")
        elif file_kind == "valid_image":
            path = tmp_path / "test_image.png"
            Image.new("RGB", (1, 1), color="blue").save(path)
        else:  # missing
            path = tmp_path / "does_not_exist.png"

        patch_ctx: contextlib.AbstractContextManager[object]
        if phash_behavior == "returns_none":
            patch_ctx = patch("fileio.fnmanip.imagehash.phash", return_value=None)
        elif phash_behavior == "raises":
            patch_ctx = patch(
                "fileio.fnmanip.imagehash.phash", side_effect=Exception("Hash failed")
            )
        else:  # no patch — real PIL fails before phash is reached
            patch_ctx = contextlib.nullcontext()

        with patch_ctx, pytest.raises(RuntimeError, match=match):
            get_hash_for_image(path)


class TestVideoHash:
    """Tests for the video hashing functions."""

    def test_get_hash_for_other_content_real_pipeline(
        self, valid_mp4_file, invalid_mp4_file, tmp_path
    ):
        """Test get_hash_for_other_content end-to-end through the real hashing pipeline.

        Previously this was four shallow tests that stubbed the internal
        ``hash_mp4file`` collaborator and asserted on the stub's call-args, so
        the real MP4 parse/hash code path never ran. Here we feed real MP4 byte
        streams and let the real ``hash_mp4file`` + ``hashlib`` run, exercising
        all three arms of ``get_hash_for_other_content``:

        - success: a valid MP4 yields a real 32-char hex MD5 digest;
        - ``InvalidMP4Error``: a file missing its ``ftyp`` header re-raises;
        - general error: a non-existent file makes ``hash_mp4file`` raise a
          plain ``RuntimeError`` ("does not exist"), which is wrapped.
        """
        # Success arm: real valid MP4 -> real MD5 over ftyp + mdat boxes.
        result = get_hash_for_other_content(valid_mp4_file)
        assert len(result) == 32
        assert all(c in "0123456789abcdef" for c in result)

        # InvalidMP4Error arm: header missing -> InvalidMP4Error re-raised as-is.
        with pytest.raises(InvalidMP4Error):
            get_hash_for_other_content(invalid_mp4_file)

        # General-error arm: missing file -> hash_mp4file raises a plain
        # RuntimeError, which get_hash_for_other_content wraps.
        missing = tmp_path / "does_not_exist.mp4"
        with pytest.raises(RuntimeError) as excinfo:
            get_hash_for_other_content(missing)
        assert f"Failed to hash file {missing}" in str(excinfo.value)
        assert "does not exist" in str(excinfo.value)

    def test_get_hash_for_other_content_empty_hash(self, valid_mp4_file):
        """Test the defensive ``if not file_hash`` arm with a real MP4.

        ``hash_mp4file`` always returns a non-empty hexdigest in practice, so
        this branch is unreachable through normal data. To exercise it honestly
        without stubbing the internal ``hash_mp4file`` collaborator, only the
        ``hashlib.md5`` leaf factory is patched to return a real digest object
        whose ``hexdigest`` is empty; the real ``hash_mp4file`` + ``get_boxes``
        still parse and hash the real MP4, and ``get_hash_for_other_content``
        raises on the empty result.
        """

        class _EmptyDigest:
            """Real hashlib-shaped algorithm whose hexdigest is empty."""

            def update(self, data: bytes) -> None:
                pass

            def hexdigest(self) -> str:
                return ""

        with (
            patch("fileio.fnmanip.hashlib.md5", return_value=_EmptyDigest()),
            pytest.raises(RuntimeError, match="Failed to generate hash"),
        ):
            get_hash_for_other_content(valid_mp4_file)
