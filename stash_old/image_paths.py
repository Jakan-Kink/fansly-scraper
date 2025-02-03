from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class ImagePathsType:
    """Represents paths associated with an image."""

    thumbnail: str | None = None
    preview: str | None = None
    image: str | None = None
