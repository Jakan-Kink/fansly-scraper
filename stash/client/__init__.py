"""Stash client module."""

from typing import Any

import strawberry

from .base import StashClientBase
from .mixins.gallery import GalleryClientMixin
from .mixins.image import ImageClientMixin
from .mixins.marker import MarkerClientMixin
from .mixins.not_implemented import NotImplementedClientMixin
from .mixins.performer import PerformerClientMixin
from .mixins.scene import SceneClientMixin
from .mixins.studio import StudioClientMixin
from .mixins.subscription import SubscriptionClientMixin
from .mixins.tag import TagClientMixin


@strawberry.type
class StashClient(
    StashClientBase,  # Base class first to provide execute()
    NotImplementedClientMixin,
    GalleryClientMixin,
    ImageClientMixin,
    MarkerClientMixin,
    PerformerClientMixin,
    SceneClientMixin,
    StudioClientMixin,
    SubscriptionClientMixin,
    TagClientMixin,
):
    """Full Stash client combining all functionality."""

    def __init__(
        self,
        conn: dict[str, Any] | None = None,
        verify_ssl: bool = True,
    ) -> None:
        """Initialize client.

        Args:
            conn: Connection details dictionary with:
                - Scheme: Protocol (default: "http")
                - Host: Hostname (default: "localhost")
                - Port: Port number (default: 9999)
                - ApiKey: Optional API key
                - Logger: Optional logger instance
            verify_ssl: Whether to verify SSL certificates
        """
        # Set initial state
        self._initialized = False
        self._init_args = (conn, verify_ssl)

        # Set up logging early
        from ..logging import client_logger

        self.log = conn.get("Logger", client_logger) if conn else client_logger

        # Set up URL components
        conn = conn or {}
        scheme = conn.get("Scheme", "http")
        host = conn.get("Host", "localhost")
        if host == "0.0.0.0":  # nosec B104 - Converting all-interfaces to localhost
            host = "127.0.0.1"
        port = conn.get("Port", 9999)
        self.url = f"{scheme}://{host}:{port}/graphql"

        # Set up HTTP client
        headers = {}
        if api_key := conn.get("ApiKey"):
            self.log.debug("Using API key authentication")
            headers["ApiKey"] = api_key

        # Debug MRO in logs only
        self.log.debug("Method Resolution Order:")
        for c in self.__class__.__mro__:
            self.log.debug(f"  {c.__module__}.{c.__name__}")
            if hasattr(c, "execute"):
                self.log.debug("    Has execute() method")

        # Initialize base class
        super().__init__(conn=conn, verify_ssl=verify_ssl)

        # Initialize all mixins
        NotImplementedClientMixin.__init__(self)
        GalleryClientMixin.__init__(self)
        ImageClientMixin.__init__(self)
        MarkerClientMixin.__init__(self)
        PerformerClientMixin.__init__(self)
        SceneClientMixin.__init__(self)
        StudioClientMixin.__init__(self)
        SubscriptionClientMixin.__init__(self)
        TagClientMixin.__init__(self)
