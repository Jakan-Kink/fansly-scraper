"""Tests for stash module GraphQL schema integration."""

import inspect
from datetime import datetime
from pathlib import Path
from typing import Any, Protocol, Union, get_args, get_origin, get_type_hints
from unittest import TestCase

from graphql import (
    GraphQLEnumType,
    GraphQLField,
    GraphQLInputObjectType,
    GraphQLList,
    GraphQLNonNull,
    GraphQLObjectType,
    GraphQLSchema,
    GraphQLString,
    build_schema,
)

from stash.base_protocols import (
    StashBaseProtocol,
    StashContentProtocol,
    StashGalleryProtocol,
    StashGroupDescriptionProtocol,
    StashGroupProtocol,
    StashImageProtocol,
    StashPerformerProtocol,
    StashSceneProtocol,
    StashStudioProtocol,
    StashTagProtocol,
)


def load_schema() -> GraphQLSchema:
    """Load the GraphQL schema from schema files."""
    schema_dir = Path("/workspace/stash/schema")
    schema_file = schema_dir / "schema.graphql"
    type_files = schema_dir / "types"

    # Read main schema
    schema_text = schema_file.read_text()

    # Read all type definitions
    for type_file in type_files.glob("*.graphql"):
        schema_text += "\n" + type_file.read_text()

    return build_schema(schema_text)


class TestStashSchema(TestCase):
    """Test cases for stash GraphQL schema integration."""

    @classmethod
    def setUpClass(cls):
        """Set up test class."""
        cls.schema = load_schema()

    def get_type_fields(self, type_name: str) -> dict[str, GraphQLField]:
        """Get fields of a GraphQL type."""
        type_def = self.schema.get_type(type_name)
        if not isinstance(type_def, GraphQLObjectType):
            self.fail(f"{type_name} is not an object type")
        return type_def.fields

    def get_input_type_fields(self, type_name: str) -> dict[str, GraphQLField]:
        """Get fields of a GraphQL input type."""
        type_def = self.schema.get_type(type_name)
        if not isinstance(type_def, GraphQLInputObjectType):
            self.fail(f"{type_name} is not an input object type")
        return type_def.fields

    def get_enum_values(self, type_name: str) -> list[str]:
        """Get values of a GraphQL enum type."""
        type_def = self.schema.get_type(type_name)
        if not isinstance(type_def, GraphQLEnumType):
            self.fail(f"{type_name} is not an enum type")
        return list(type_def.values.keys())

    def get_protocol_fields(self, protocol_class: type[Protocol]) -> dict[str, Any]:
        """Get all fields from a protocol and its bases."""
        fields = {}
        for base in inspect.getmro(protocol_class):
            if hasattr(base, "__annotations__"):
                fields.update(get_type_hints(base))
        return fields

    def assert_field_exists(
        self,
        protocol_class: type[Protocol],
        field_name: str,
        graphql_field: GraphQLField,
        nullable: bool = True,
    ):
        """Assert that a field exists in the protocol and matches its GraphQL type."""
        # Get all fields from protocol and its bases
        hints = self.get_protocol_fields(protocol_class)
        self.assertIn(
            field_name,
            hints,
            f"Protocol {protocol_class.__name__} should have field {field_name}",
        )

        # Check nullability
        field_type = hints[field_name]
        origin = get_origin(field_type)
        args = get_args(field_type)

        # Handle forward references
        if isinstance(field_type, str):
            # Forward reference, assume it's valid
            return
        if origin is list and len(args) == 1 and isinstance(args[0], str):
            # Forward reference in list, assume it's valid
            return

        if origin is Union and type(None) in args:
            self.assertTrue(
                nullable,
                f"Field {field_name} in {protocol_class.__name__} should not be nullable",
            )
        elif not nullable:
            self.assertNotEqual(
                origin,
                Union,
                f"Field {field_name} in {protocol_class.__name__} should be nullable",
            )

        # Check field type matches GraphQL type
        if isinstance(graphql_field.type, GraphQLNonNull):
            field_type = graphql_field.type.of_type
        else:
            field_type = graphql_field.type

        if isinstance(field_type, GraphQLList):
            self.assertEqual(
                origin,
                list,
                f"Field {field_name} in {protocol_class.__name__} should be a list",
            )
        elif isinstance(field_type, GraphQLEnumType):
            # For enums, we expect either the enum type or str
            self.assertTrue(
                str in args if origin is Union else field_type is str,
                f"Field {field_name} in {protocol_class.__name__} should be an enum or str",
            )
        elif isinstance(field_type, GraphQLObjectType):
            # Map GraphQL type names to protocol types
            protocol_map = {
                "Studio": StashStudioProtocol,
                "Performer": StashPerformerProtocol,
                "Tag": StashTagProtocol,
                "Scene": StashSceneProtocol,
                "Image": StashImageProtocol,
                "Gallery": StashGalleryProtocol,
                "Group": StashGroupProtocol,
            }
            expected_protocol = protocol_map.get(field_type.name)
            if expected_protocol:
                # Handle forward references
                if isinstance(field_type, str):
                    return
                if origin is list and len(args) == 1 and isinstance(args[0], str):
                    return
                self.assertTrue(
                    (
                        expected_protocol in args
                        if origin is Union
                        else field_type is expected_protocol
                    ),
                    f"Field {field_name} in {protocol_class.__name__} should implement {expected_protocol.__name__}",
                )

    def test_performer_schema(self):
        """Test that StashPerformerProtocol matches GraphQL schema."""
        fields = self.get_type_fields("Performer")
        required_fields = {
            "id": str,
            "name": str,
            "created_at": datetime,
            "updated_at": datetime,
            "favorite": bool,
            "ignore_auto_tag": bool,
            "disambiguation": str,
            "gender": str,
            "birthdate": str,
            "ethnicity": str,
            "country": str,
            "eye_color": str,
            "height_cm": int,
            "measurements": str,
            "fake_tits": str,
            "penis_length": float,
            "circumcised": str,
            "career_length": str,
            "tattoos": str,
            "piercings": str,
            "image_path": str,
            "o_counter": int,
            "rating100": int,
            "details": str,
            "death_date": str,
            "hair_color": str,
            "weight": int,
            "custom_fields": dict,
        }

        for field_name, expected_type in required_fields.items():
            self.assertIn(
                field_name, fields, f"Performer should have {field_name} field"
            )
            self.assert_field_exists(
                StashPerformerProtocol,
                field_name,
                fields[field_name],
                nullable=(
                    False
                    if field_name in ["id", "name", "favorite", "ignore_auto_tag"]
                    else True
                ),
            )

    def test_studio_schema(self):
        """Test that StashStudioProtocol matches GraphQL schema."""
        fields = self.get_type_fields("Studio")
        required_fields = {
            "id": str,
            "name": str,
            "created_at": datetime,
            "updated_at": datetime,
            "url": str,
            "parent_studio": StashStudioProtocol,
            "child_studios": list,
            "aliases": list,
            "ignore_auto_tag": bool,
            "image_path": str,
            "rating100": int,
            "favorite": bool,
            "details": str,
            "scene_count": int,
            "image_count": int,
            "gallery_count": int,
            "performer_count": int,
            "group_count": int,
            "stash_ids": list,
            "groups": list,
        }

        for field_name, expected_type in required_fields.items():
            self.assertIn(field_name, fields, f"Studio should have {field_name} field")
            self.assert_field_exists(
                StashStudioProtocol,
                field_name,
                fields[field_name],
                nullable=(
                    False
                    if field_name
                    in [
                        "id",
                        "name",
                        "favorite",
                        "ignore_auto_tag",
                        "aliases",
                        "child_studios",
                    ]
                    else True
                ),
            )

    def test_tag_schema(self):
        """Test that StashTagProtocol matches GraphQL schema."""
        fields = self.get_type_fields("Tag")
        required_fields = {
            "id": str,
            "name": str,
            "description": str,
            "aliases": list,
            "ignore_auto_tag": bool,
            "created_at": datetime,
            "updated_at": datetime,
            "favorite": bool,
            "image_path": str,
            "scene_count": int,
            "scene_marker_count": int,
            "image_count": int,
            "gallery_count": int,
            "performer_count": int,
            "studio_count": int,
            "group_count": int,
            "parents": list,
            "children": list,
            "parent_count": int,
            "child_count": int,
        }

        for field_name, expected_type in required_fields.items():
            self.assertIn(field_name, fields, f"Tag should have {field_name} field")
            self.assert_field_exists(
                StashTagProtocol,
                field_name,
                fields[field_name],
                nullable=(
                    False
                    if field_name
                    in ["id", "name", "aliases", "ignore_auto_tag", "favorite"]
                    else True
                ),
            )

    def test_scene_schema(self):
        """Test that StashSceneProtocol matches GraphQL schema."""
        fields = self.get_type_fields("Scene")
        required_fields = {
            "id": str,
            "title": str,
            "code": str,
            "details": str,
            "director": str,
            "urls": list,
            "date": datetime,
            "rating100": int,
            "o_counter": int,
            "organized": bool,
            "interactive": bool,
            "interactive_speed": int,
            "created_at": datetime,
            "updated_at": datetime,
            "studio": StashStudioProtocol,
            "tags": list,
            "performers": list,
            "captions": list,
            "last_played_at": datetime,
            "resume_time": float,
            "play_duration": float,
            "play_count": int,
            "play_history": list,
            "o_history": list,
        }

        for field_name, expected_type in required_fields.items():
            self.assertIn(field_name, fields, f"Scene should have {field_name} field")
            self.assert_field_exists(
                StashSceneProtocol,
                field_name,
                fields[field_name],
                nullable=(
                    False if field_name in ["id", "organized", "interactive"] else True
                ),
            )

    def test_image_schema(self):
        """Test that StashImageProtocol matches GraphQL schema."""
        fields = self.get_type_fields("Image")
        required_fields = {
            "id": str,
            "title": str,
            "code": str,
            "details": str,
            "urls": list,
            "date": datetime,
            "rating100": int,
            "organized": bool,
            "o_counter": int,
            "created_at": datetime,
            "updated_at": datetime,
            "studio": StashStudioProtocol,
            "tags": list,
            "performers": list,
            "photographer": str,
        }

        for field_name, expected_type in required_fields.items():
            self.assertIn(field_name, fields, f"Image should have {field_name} field")
            self.assert_field_exists(
                StashImageProtocol,
                field_name,
                fields[field_name],
                nullable=False if field_name in ["id", "organized"] else True,
            )

    def test_gallery_schema(self):
        """Test that StashGalleryProtocol matches GraphQL schema."""
        fields = self.get_type_fields("Gallery")
        required_fields = {
            "id": str,
            "title": str,
            "code": str,
            "details": str,
            "urls": list,
            "date": datetime,
            "rating100": int,
            "organized": bool,
            "created_at": datetime,
            "updated_at": datetime,
            "studio": StashStudioProtocol,
            "tags": list,
            "performers": list,
            "photographer": str,
            "o_counter": int,
            "image_count": int,
            "scenes": list,
        }

        for field_name, expected_type in required_fields.items():
            self.assertIn(field_name, fields, f"Gallery should have {field_name} field")
            self.assert_field_exists(
                StashGalleryProtocol,
                field_name,
                fields[field_name],
                nullable=(
                    False if field_name in ["id", "image_count", "organized"] else True
                ),
            )

    def test_group_schema(self):
        """Test that StashGroupProtocol matches GraphQL schema."""
        fields = self.get_type_fields("Group")
        required_fields = {
            "id": str,
            "name": str,
            "aliases": str,
            "duration": int,
            "date": str,
            "rating100": int,
            "director": str,
            "synopsis": str,
            "front_image_path": str,
            "back_image_path": str,
            "created_at": datetime,
            "updated_at": datetime,
            "studio": StashStudioProtocol,
            "scenes": list,
            "performers": list,
            "galleries": list,
            "images": list,
        }

        for field_name, expected_type in required_fields.items():
            self.assertIn(field_name, fields, f"Group should have {field_name} field")
            self.assert_field_exists(
                StashGroupProtocol,
                field_name,
                fields[field_name],
                nullable=False if field_name in ["id", "name"] else True,
            )
