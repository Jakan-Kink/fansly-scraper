"""GraphQL utilities for Stash interface.

This module provides utilities for loading and managing GraphQL fragments and queries.
"""

from pathlib import Path
from typing import Dict


def load_fragments(schema_dir: str | Path = None) -> dict[str, str]:
    """Load GraphQL fragments from the fragments.graphql file.

    Args:
        schema_dir: Optional path to schema directory. If None, uses default location.

    Returns:
        Dictionary mapping fragment names to their definitions.
    """
    if schema_dir is None:
        schema_dir = Path(__file__).parent / "schema"
    elif isinstance(schema_dir, str):
        schema_dir = Path(schema_dir)

    fragments_file = schema_dir / "fragments.graphql"
    if not fragments_file.exists():
        raise FileNotFoundError(f"Fragments file not found at {fragments_file}")

    # Read the entire file content - we'll let GQLWrapper parse it
    with fragments_file.open() as f:
        return f.read()


def build_query(
    query: str, *fragment_names: str, fragments: dict[str, str] | None = None
) -> str:
    """Build a complete GraphQL query with the specified fragments.

    Args:
        query: The main GraphQL query
        *fragment_names: Names of fragments to include
        fragments: Optional pre-loaded fragments dictionary. If None, loads fragments.

    Returns:
        Complete GraphQL query with all required fragments.

    Example:
        >>> query = '''
        ... query FindScene($id: ID!) {
        ...   findScene(id: $id) {
        ...     ...SceneFields
        ...   }
        ... }
        ... '''
        >>> complete_query = build_query(query, "SceneFields")
    """
    if fragments is None:
        fragments = load_fragments()

    used_fragments = set()
    fragment_definitions = []

    def add_fragment_with_deps(name: str) -> None:
        """Recursively add a fragment and its dependencies."""
        if name in used_fragments:
            return

        if name not in fragments:
            raise ValueError(f"Fragment {name} not found")

        fragment_def = fragments[name]
        # Find nested fragment references
        for line in fragment_def.split("\n"):
            if "..." in line and not line.strip().startswith("#"):
                nested = line.strip().replace("...", "").strip()
                if nested != name:  # Avoid infinite recursion
                    add_fragment_with_deps(nested)

        fragment_definitions.append(fragment_def)
        used_fragments.add(name)

    # Add all requested fragments and their dependencies
    for name in fragment_names:
        add_fragment_with_deps(name)

    # Combine query with fragments
    return "\n".join([query, *fragment_definitions])


# Example queries using the fragments
FIND_SCENE_QUERY = """
query FindScene($id: ID!) {
  findScene(id: $id) {
    ...SceneFields
  }
}
"""

FIND_PERFORMER_QUERY = """
query FindPerformer($id: ID!) {
  findPerformer(id: $id) {
    ...PerformerFields
  }
}
"""

FIND_STUDIO_QUERY = """
query FindStudio($id: ID!) {
  findStudio(id: $id) {
    ...StudioFields
  }
}
"""

FIND_TAG_QUERY = """
query FindTag($id: ID!) {
  findTag(id: $id) {
    ...TagFields
  }
}
"""
