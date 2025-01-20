"""Extended StashInterface with custom fragments support."""

from typing import Any

from stashapi.stashapp import StashInterface as BaseStashInterface

from .graphql_utils import load_fragments


class StashInterface(BaseStashInterface):
    """Extended StashInterface with support for custom fragments."""

    def __init__(
        self,
        conn: dict = None,
        fragments: list[str] = None,
        verify_ssl: bool = True,
    ) -> None:
        """Initialize StashInterface with fragments support.

        Args:
            conn: Connection details dictionary
            fragments: List of fragment strings
            verify_ssl: Whether to verify SSL certificates
        """
        # Initialize parent first
        super().__init__(
            conn=conn or {},
            fragments=fragments or [],
            verify_ssl=verify_ssl,
        )

        # Load and parse our custom fragments after parent init
        fragments_content = load_fragments()
        self.parse_fragments(fragments_content)

    def _build_query_with_fragments(self, query: str, *fragment_names: str) -> str:
        """Build a complete query with fragments.

        Args:
            query: Base query
            *fragment_names: Names of fragments to include

        Returns:
            Complete query with fragments
        """
        if not fragment_names:
            return query

        # Build query with referenced fragments
        result = query
        for name in fragment_names:
            if name in self.fragments:
                result = f"{result}\n{self.fragments[name]}"
        return result

    def callGQL(
        self, query: str, variables: dict = None, *fragment_names: str
    ) -> dict[str, Any]:
        """Call GraphQL with automatic fragment inclusion.

        Args:
            query: GraphQL query string
            variables: Optional variables for the query
            *fragment_names: Names of fragments to include

        Returns:
            GraphQL response data
        """
        if fragment_names:
            query = self._build_query_with_fragments(query, *fragment_names)
        return super().callGQL(query, variables or {})

    def find_scene(
        self, scene: str | int | dict, fragment: str | None = None, create: bool = False
    ) -> dict[str, Any]:
        """Find a scene by ID, name, or dict with full fields.

        Args:
            scene: ID, name, or dict of scene to search for
            fragment: Optional fragment to use instead of SceneFields
            create: Create scene if not found. Defaults to False.

        Returns:
            Scene data with all fields from SceneFields fragment (or specified fragment)
        """
        # Call parent's find_scene first
        result = super().find_scene(scene, fragment=None)
        if result is None:
            return None

        # If no custom fragment specified, use our full fields fragment
        if fragment is None:
            return self.callGQL(
                """
                query FindScene($id: ID!) {
                  findScene(id: $id) {
                    ...SceneFields
                  }
                }
                """,
                {"id": result.get("id", result.get("findScene", {}).get("id", None))},
                "SceneFields",
            )
        return result

    def find_performer(
        self,
        performer: str | int | dict,
        fragment: str | None = None,
        create: bool = False,
    ) -> dict[str, Any]:
        """Find a performer by ID, name, or dict with full fields.

        Args:
            performer: ID, name, or dict of performer to search for
            fragment: Optional fragment to use instead of PerformerFields
            create: Create performer if not found. Defaults to False.

        Returns:
            Performer data with all fields from PerformerFields fragment (or specified fragment)
        """
        # Call parent's find_performer first
        result = super().find_performer(performer, fragment=None, create=create)
        if result is None:
            return None

        # If no custom fragment specified, use our full fields fragment
        if fragment is None:
            return self.callGQL(
                """
                query FindPerformer($id: ID!) {
                  findPerformer(id: $id) {
                    ...PerformerFields
                  }
                }
                """,
                {
                    "id": result.get(
                        "id", result.get("findPerformer", {}).get("id", None)
                    )
                },
                "PerformerFields",
            )
        return result

    def find_studio(
        self,
        studio: str | int | dict,
        fragment: str | None = None,
        create: bool = False,
    ) -> dict[str, Any]:
        """Find a studio by ID, name, or dict with full fields.

        Args:
            studio: ID, name, or dict of studio to search for
            fragment: Optional fragment to use instead of StudioFields
            create: Create studio if not found. Defaults to False.

        Returns:
            Studio data with all fields from StudioFields fragment (or specified fragment)
        """
        # Call parent's find_studio first
        result = super().find_studio(studio, fragment=None, create=create)
        if result is None:
            return None

        # If no custom fragment specified, use our full fields fragment
        if fragment is None:
            return self.callGQL(
                """
                query FindStudio($id: ID!) {
                  findStudio(id: $id) {
                    ...StudioFields
                  }
                }
                """,
                {"id": result.get("id", result.get("findStudio", {}).get("id", None))},
                "StudioFields",
            )
        return result

    def find_tag(
        self, tag: str | int | dict, fragment: str | None = None, create: bool = False
    ) -> dict[str, Any]:
        """Find a tag by ID, name, or dict with full fields.

        Args:
            tag: ID, name, or dict of tag to search for
            fragment: Optional fragment to use instead of TagFields
            create: Create tag if not found. Defaults to False.

        Returns:
            Tag data with all fields from TagFields fragment (or specified fragment)
        """
        # Call parent's find_tag first
        result = super().find_tag(tag, fragment=None, create=create)
        if result is None:
            return None

        # If no custom fragment specified, use our full fields fragment
        if fragment is None:
            return self.callGQL(
                """
                query FindTag($id: ID!) {
                  findTag(id: $id) {
                    ...TagFields
                  }
                }
                """,
                {"id": result.get("id", result.get("findTag", {}).get("id", None))},
                "TagFields",
            )
        return result

    def find_scenes(
        self,
        filter_: dict[str, Any] | None = None,
        scene_filter: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Find scenes with full fields.

        Args:
            filter_: Optional filter parameters
            scene_filter: Optional scene-specific filter

        Returns:
            List of scenes with all fields from SceneFields fragment
        """
        return self.callGQL(
            """
            query FindScenes($filter: FindFilterType, $scene_filter: SceneFilterType) {
              findScenes(filter: $filter, scene_filter: $scene_filter) {
                count
                scenes {
                  ...SceneFields
                }
              }
            }
            """,
            {"filter": filter_, "scene_filter": scene_filter},
            "SceneFields",
        )
