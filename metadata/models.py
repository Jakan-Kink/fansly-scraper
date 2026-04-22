"""Pydantic models for Fansly metadata entities.

Pure Pydantic BaseModel subclasses — no SQLAlchemy ORM.
- Identity map via @model_validator(mode="wrap")
- Bidirectional relationship sync via __setattr__ override
- Dirty tracking via snapshot comparison
- Auto-coercion of API data (str IDs, int timestamps, extra="ignore")
- Field aliases for API name mismatches (metadata→meta_info, fypFlags→fypFlag)
- Nested relationship enrichment (parent context injection)
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from enum import Enum
from typing import Annotated, Any, ClassVar, Self
from urllib.parse import urlparse

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ModelWrapValidatorHandler,
    PrivateAttr,
    ValidationInfo,
    model_validator,
)


# ── Snowflake ID type ───────────────────────────────────────────────────

_SNOWFLAKE_MIN = 10**15  # Fansly IDs are ~18 digits
_SNOWFLAKE_MAX = 2**63 - 1  # Signed 64-bit ceiling


def _validate_snowflake(v: Any) -> int:
    """Coerce str→int and validate Snowflake ID range.

    Rejects non-numeric strings (e.g., "media1") at validation time
    instead of letting them silently propagate through the system.
    """
    try:
        v = int(v)
    except (TypeError, ValueError):
        raise ValueError(
            f"Invalid Snowflake ID: {v!r} — must be a numeric value"
        ) from None
    if v < _SNOWFLAKE_MIN or v > _SNOWFLAKE_MAX:
        raise ValueError(
            f"Snowflake ID out of range: {v} "
            f"(must be {_SNOWFLAKE_MIN} ≤ id ≤ {_SNOWFLAKE_MAX})"
        )
    return v


SnowflakeId = Annotated[int, BeforeValidator(_validate_snowflake)]


# ── Module-level store accessor ──────────────────────────────────────────


def get_store() -> Any:
    """Get the global EntityStore singleton.

    Returns the store set on FanslyObject._store. Raises RuntimeError if
    not initialized. Use this instead of passing 'store' through every function.
    """
    store = FanslyObject._store
    if store is None:
        raise RuntimeError(
            "EntityStore not initialized — call FanslyObject._store = store first"
        )
    return store


# ── RelationshipMetadata ─────────────────────────────────────────────────

# Sentinel for fields resolved by __init_subclass__ from the dict key name.
_DEFERRED = object()


class RelationshipMetadata:
    """Metadata describing a relationship between entity types.

    Query Strategies (how the store loads this relationship):
        - 'direct_field': FK column on this model's table
        - 'assoc_table': M2M via association table
        - 'reverse_fk': Other model has FK pointing to us

    Attributes:
        target_field: DB column or virtual field (e.g., 'tag_ids', 'content_id')
        is_list: True for M2M/one-to-many, False for FK/many-to-one
        transform: Optional transform for DB serialization (e.g., obj → obj.id)
        query_field: Field name when reading. Auto-derived from target_field if not set.
        inverse_type: Type of related entity (string to avoid circular imports)
        inverse_query_field: Field name on inverse type for bidirectional sync
        query_strategy: How to query this relationship
        assoc_table: Association table name for M2M relationships
        fk_column: FK column name for direct FK relationships
        auto_sync: DB maintains referential integrity via FK constraints
        notes: Additional implementation notes
    """

    def __init__(
        self,
        target_field: str,
        is_list: bool,
        transform: Callable[[Any], Any] | None = None,
        *,
        query_field: str | None = None,
        inverse_type: str | type | None = None,
        inverse_query_field: str | None = None,
        query_strategy: str = "direct_field",
        assoc_table: str | None = None,
        fk_column: str | None = None,
        ordered: bool = False,
        auto_sync: bool = True,
        notes: str = "",
    ) -> None:
        self.target_field = target_field
        self.is_list = is_list
        self.transform = transform

        # Auto-derive query_field from target_field if not provided
        # Same logic as StashObject: gallery_ids → galleries, studio_id → studio
        if query_field is None:
            if target_field.endswith("_ids"):
                query_field = target_field[:-4] + "s"
            elif target_field.endswith("_id"):
                query_field = target_field[:-3]
            else:
                query_field = target_field
        self.query_field = query_field

        self.inverse_type = inverse_type
        self.inverse_query_field = inverse_query_field
        self.query_strategy = query_strategy
        self.assoc_table = assoc_table
        self.fk_column = fk_column
        self.ordered = ordered
        self.auto_sync = auto_sync
        self.notes = notes

    def __repr__(self) -> str:
        return (
            f"RelationshipMetadata("
            f"target_field={self.target_field!r}, "
            f"query_field={self.query_field!r}, "
            f"strategy={self.query_strategy!r})"
        )


# ── ActiveRecord-Style Relationship Helpers ──────────────────────────────
#
# Replace verbose RelationshipMetadata(...) with concise helpers:
#   "preview": belongs_to("Media")
#   "posts":   habtm("Post", assoc_table="post_hashtags", ...)
#   "walls":   has_many("Wall", fk_column="accountId")
#
# Fields marked _DEFERRED are resolved by FanslyObject.__init_subclass__
# from the dict key name (e.g., "preview" → target_field="previewId").


_IRREGULAR_PLURALS: dict[str, str] = {"stories": "story"}


def _singularize(name: str) -> str:
    """Naive singularization for relationship field names.

    Handles regular English plurals and a small irregular table.
    Only needs to cover the field names actually used in FDNG models.
    """
    if name in _IRREGULAR_PLURALS:
        return _IRREGULAR_PLURALS[name]
    if name.endswith("s") and not name.endswith("ss"):
        return name[:-1]
    return name


def _camel_to_snake(name: str) -> str:
    """Convert camelCase to snake_case.

    timelineStats → timeline_stats
    mediaStoryState → media_story_state
    accountMediaBundles → account_media_bundles
    """
    result: list[str] = []
    for i, ch in enumerate(name):
        if ch.isupper() and i > 0:
            result.append("_")
        result.append(ch.lower())
    return "".join(result)


def belongs_to(
    inverse_type: str,
    *,
    fk_column: str | None = None,
    target_field: str | None = None,
    inverse_query_field: str | None = None,
    notes: str = "",
) -> RelationshipMetadata:
    """FK on this model's table → single related object.

    Strategy: direct_field, is_list=False.

    Auto-derives (via __init_subclass__):
        - query_field: from dict key
        - target_field: key + "Id" (camelCase)
        - fk_column: same as target_field (if not provided)

    Examples::

        "preview": belongs_to("Media")
            → target_field="previewId", fk_column="previewId"
        "account": belongs_to("Account", fk_column="accountId")
            → target_field="accountId", fk_column="accountId"
    """
    return RelationshipMetadata(
        target_field=target_field or _DEFERRED,
        is_list=False,
        query_field=_DEFERRED,
        inverse_type=inverse_type,
        inverse_query_field=inverse_query_field,
        query_strategy="direct_field",
        fk_column=fk_column or _DEFERRED,
        notes=notes,
    )


def has_one_through(
    inverse_type: str,
    *,
    assoc_table: str,
    target_field: str | None = None,
    notes: str = "",
) -> RelationshipMetadata:
    """Scalar via junction table — 1:1 junction.

    Strategy: assoc_table, is_list=False.

    Auto-derives (via __init_subclass__):
        - query_field: from dict key
        - target_field: key + "_media_id" (snake_case)

    Examples::

        "avatar": has_one_through("Media", assoc_table="account_avatar")
            → target_field="avatar_media_id"
        "banner": has_one_through("Media", assoc_table="account_banner")
            → target_field="banner_media_id"
    """
    return RelationshipMetadata(
        target_field=target_field or _DEFERRED,
        is_list=False,
        query_field=_DEFERRED,
        inverse_type=inverse_type,
        query_strategy="assoc_table",
        assoc_table=assoc_table,
        notes=notes,
    )


def habtm(
    inverse_type: str,
    *,
    assoc_table: str,
    fk_column: str | None = None,
    ordered: bool = False,
    inverse_query_field: str | None = None,
    target_field: str | None = None,
    notes: str = "",
) -> RelationshipMetadata:
    """Many-to-many via junction table.

    Strategy: assoc_table, is_list=True.

    Auto-derives (via __init_subclass__):
        - query_field: from dict key
        - target_field: singularize(key) + "_ids"

    Examples::

        "posts": habtm("Post", assoc_table="post_hashtags",
                        inverse_query_field="hashtags")
            → target_field="post_ids"
        "locations": habtm("MediaLocation", assoc_table="media_locations",
                           fk_column="mediaId")
            → target_field="location_ids"
    """
    return RelationshipMetadata(
        target_field=target_field or _DEFERRED,
        is_list=True,
        query_field=_DEFERRED,
        inverse_type=inverse_type,
        inverse_query_field=inverse_query_field,
        query_strategy="assoc_table",
        assoc_table=assoc_table,
        fk_column=fk_column,
        ordered=ordered,
        notes=notes,
    )


def has_many(
    inverse_type: str,
    *,
    fk_column: str,
    inverse_query_field: str | None = None,
    target_field: str | None = None,
    notes: str = "",
) -> RelationshipMetadata:
    """Reverse FK — other model has FK pointing here (list).

    Strategy: reverse_fk, is_list=True.

    Auto-derives (via __init_subclass__):
        - query_field: from dict key
        - target_field: singularize(key) + "_ids"

    Examples::

        "attachments": has_many("Attachment", fk_column="postId")
            → target_field="attachment_ids"
        "walls": has_many("Wall", fk_column="accountId")
            → target_field="wall_ids"
    """
    return RelationshipMetadata(
        target_field=target_field or _DEFERRED,
        is_list=True,
        query_field=_DEFERRED,
        inverse_type=inverse_type,
        inverse_query_field=inverse_query_field,
        query_strategy="reverse_fk",
        fk_column=fk_column,
        notes=notes,
    )


def has_one(
    inverse_type: str,
    *,
    fk_column: str,
    target_field: str | None = None,
    notes: str = "",
) -> RelationshipMetadata:
    """Reverse FK — other model has FK pointing here (scalar).

    Strategy: reverse_fk, is_list=False.

    Auto-derives (via __init_subclass__):
        - query_field: from dict key
        - target_field: camel_to_snake(key) + "_id"

    Examples::

        "timelineStats": has_one("TimelineStats", fk_column="accountId")
            → target_field="timeline_stats_id"
        "mediaStoryState": has_one("MediaStoryState", fk_column="accountId")
            → target_field="media_story_state_id"
    """
    return RelationshipMetadata(
        target_field=target_field or _DEFERRED,
        is_list=False,
        query_field=_DEFERRED,
        inverse_type=inverse_type,
        query_strategy="reverse_fk",
        fk_column=fk_column,
        notes=notes,
    )


# ── ContentType Enum ─────────────────────────────────────────────────────


class ContentType(Enum):
    """Content types for attachments.

    Attributes:
        ACCOUNT_MEDIA: Individual media item (type=1)
        ACCOUNT_MEDIA_BUNDLE: Collection of media items (type=2)
        AGGREGATED_POSTS: Array of aggregated post dictionaries (type=8)
        TIP_GOALS: Tip goal content (type=7100)
        STORY: Story content (type=32001)
        POLL: Poll content (type=42001)

    Note:
        The following contentType values are intentionally NOT included in this enum
        and will be skipped during processing (monetary transactions, not media):
        - TIP (type=7): Simple tip/payment attachment
        - TIP_GOALS (type=7100): Also skipped explicitly in post processing

    """

    ACCOUNT_MEDIA = 1
    ACCOUNT_MEDIA_BUNDLE = 2
    AGGREGATED_POSTS = 8
    TIP_GOALS = 7100
    STORY = 32001
    POLL = 42001


# ── Timestamp / utility helpers ──────────────────────────────────────────


def _parse_timestamp(v: Any) -> Any:
    """Parse timestamp from int/float/string to datetime. Shared by validators."""
    if v is None or isinstance(v, datetime):
        return v
    if isinstance(v, (int, float)):
        if v > 1e10:
            v = v / 1000
        return datetime.fromtimestamp(v, UTC)
    if isinstance(v, str):
        return datetime.fromisoformat(v.replace("Z", "+00:00"))
    return v


# ── FanslyRecord — simple records without identity map ───────────────────


class FanslyRecord(BaseModel):
    """Base for composite-PK tables that don't need identity map or dirty tracking."""

    __table_name__: ClassVar[str] = ""

    model_config = ConfigDict(arbitrary_types_allowed=True, extra="ignore")


# ── FanslyObject Base ────────────────────────────────────────────────────


class FanslyObject(BaseModel):
    """Base for all Fansly metadata entity models.

    Provides:
    - Identity map via @model_validator(mode="wrap")
    - Dirty tracking via _snapshot / _is_new
    - Bidirectional relationship sync via __setattr__
    - Relationship mutation via _add_to_relationship / _remove_from_relationship
    - Field update helper via update_fields()
    """

    _store: ClassVar[Any] = None  # Set to PostgresEntityStore at runtime

    __table_name__: ClassVar[str] = ""
    __relationships__: ClassVar[dict[str, RelationshipMetadata]] = {}
    __tracked_fields__: ClassVar[set[str]] = set()
    __fk_to_rel__: ClassVar[dict[str, tuple[str, RelationshipMetadata]]] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Resolve _DEFERRED fields in __relationships__ from dict keys.

        When ActiveRecord-style helpers (belongs_to, habtm, has_many, etc.)
        are used, they set query_field/target_field/fk_column to _DEFERRED.
        This hook resolves them from the dict key after class creation.

        Also builds __fk_to_rel__: reverse lookup from FK column names to
        (relationship_field_name, RelationshipMetadata) for FK↔relationship
        sync in __setattr__.
        """
        super().__init_subclass__(**kwargs)

        relationships = cls.__dict__.get("__relationships__")
        if not relationships:
            return

        fk_to_rel: dict[str, tuple[str, RelationshipMetadata]] = {}

        for key, meta in relationships.items():
            if not isinstance(meta, RelationshipMetadata):
                continue

            # ── Resolve query_field (always the dict key) ────────────
            if meta.query_field is _DEFERRED:
                meta.query_field = key

            # ── Resolve target_field (strategy-dependent) ────────────
            if meta.target_field is _DEFERRED:
                if meta.query_strategy == "direct_field" and not meta.is_list:
                    # belongs_to: preview → previewId
                    meta.target_field = f"{key}Id"
                elif meta.query_strategy == "assoc_table" and not meta.is_list:
                    # has_one_through: avatar → avatar_media_id
                    meta.target_field = f"{key}_media_id"
                elif meta.is_list:
                    # habtm / has_many: posts → post_ids
                    singular = _singularize(key)
                    meta.target_field = f"{singular}_ids"
                else:
                    # has_one (reverse_fk, scalar): timelineStats → timeline_stats_id
                    meta.target_field = f"{_camel_to_snake(key)}_id"

            # ── Resolve fk_column for belongs_to ─────────────────────
            if meta.fk_column is _DEFERRED:
                # belongs_to auto-derives fk_column = target_field
                meta.fk_column = meta.target_field

            # ── Build FK→relationship reverse map ────────────────────
            if meta.fk_column and not meta.is_list:
                fk_to_rel[meta.fk_column] = (key, meta)

        if fk_to_rel:
            cls.__fk_to_rel__ = fk_to_rel

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        validate_assignment=True,
        populate_by_name=True,
        extra="ignore",
    )

    _snapshot: dict = PrivateAttr(default_factory=dict)
    _is_new: bool = PrivateAttr(default=False)

    id: SnowflakeId | None = None

    # ── API Data Coercion ────────────────────────────────────────────

    @model_validator(mode="before")
    @classmethod
    def _coerce_api_types(cls, data: Any) -> Any:
        """Auto-coerce API data types before Pydantic validation.

        - Strips unpaired Unicode surrogates from strings (asyncpg can't
          encode them as UTF-8; they come from Fansly API JSON)
        - Int/float timestamps → datetime (all Fansly timestamp fields end in 'At')
        - String IDs → int is handled by Pydantic's lax mode automatically
        - Nested relationship enrichment is handled by _process_nested_cache_lookups
        """
        if not isinstance(data, dict):
            return data
        for k, v in list(data.items()):
            if isinstance(v, str) and v:
                # Strip unpaired surrogates (e.g., \ud835 from truncated
                # mathematical bold chars in Fansly wall/post names).
                # surrogatepass encodes surrogates as UTF-8-like bytes,
                # then 'replace' converts them to U+FFFD on decode.
                try:
                    v.encode("utf-8")
                except UnicodeEncodeError:
                    data[k] = v.encode("utf-8", errors="surrogatepass").decode(
                        "utf-8", errors="replace"
                    )
            elif isinstance(v, (int, float)) and k.endswith("At"):
                data[k] = _parse_timestamp(v)
        return data

    # Fields excluded from DB writes:
    # - Inverse-only relationship fields (no DB column, populated by bidirectional sync)
    # Subclasses extend this for model-specific exclusions.
    _WRITE_EXCLUDED: ClassVar[set[str]] = set()

    # ── Identity Semantics ───────────────────────────────────────────
    # Two objects with the same type + id are the SAME entity, regardless
    # of field values. This is critical for identity map, relationship
    # sync (if self not in current), and set membership.

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, FanslyObject):
            return NotImplemented
        if self.id is None or other.id is None:
            return self is other  # Unsaved objects: identity comparison
        return type(self) is type(other) and self.id == other.id

    def __hash__(self) -> int:
        if self.id is None:
            return id(self)  # Unsaved objects: object identity
        return hash((type(self).__name__, self.id))

    # ── Identity Map ─────────────────────────────────────────────────

    @model_validator(mode="wrap")
    @classmethod
    def _identity_map_validator(
        cls,
        data: Any,
        handler: ModelWrapValidatorHandler[Self],
        info: ValidationInfo,
    ) -> Self:
        """Check cache before construction. Returns cached instance if found."""
        ctx = info.context if info.context else None

        if not isinstance(data, dict):
            # Re-validation from validate_assignment — preserve _is_new
            return handler(data, ctx)

        if not cls._store:
            return handler(data, ctx)

        # Resolve relationship IDs → cached objects BEFORE merge or construction.
        # This must happen first so the merge path doesn't overwrite already-resolved
        # relationship objects with raw ints when a second preload re-validates.
        processed = cls._process_nested_cache_lookups(data)

        # Check cache for existing entity.
        # Models with __pk_column__ (e.g., TimelineStats uses accountId)
        # need lookup by PK field since _set_id_from_pk hasn't run yet.
        entity_id = processed.get("id")
        if entity_id is None:
            pk_col = getattr(cls, "__pk_column__", None)
            if pk_col and pk_col != "id":
                entity_id = processed.get(pk_col)

        if entity_id is not None:
            cached = cls._store.get_from_cache(cls, entity_id)
            if cached is not None:
                # Run full Pydantic validation on the new data (runs all
                # before/field validators — timestamp coercion, alias
                # handling, relationship reconstruction, etc.)
                # Temporarily remove from cache so handler doesn't hit
                # this same branch recursively.
                cls._store.invalidate(cls, cached.id)
                validated = handler(processed, ctx)

                # Merge only fields that were explicitly provided in the
                # input data — skip fields that just got Pydantic defaults.
                # This prevents API data from overwriting DB-only fields
                # (is_downloaded, content_hash, local_filename) with defaults.
                # We rely solely on model_fields_set — no `is not None` guard,
                # because the API may legitimately send falsy/None values.
                for k in cls.__tracked_fields__:
                    if k not in validated.model_fields_set:
                        continue
                    new_val = getattr(validated, k, None)
                    object.__setattr__(cached, k, new_val)

                # Defensive: if cached was previously saved to DB (or
                # loaded from DB), ensure _is_new stays False so the
                # next save() does UPDATE, not INSERT.
                if not cached._is_new:
                    cached._is_new = False

                # Re-cache the original instance (not the temp)
                cls._store.cache_instance(cached)
                return cached

        # Normal Pydantic construction (preserve context for nested validators)
        instance = handler(processed, ctx)

        # Not from cache → needs INSERT on first save().
        # DB-loading paths (preload/get) clear this after model_validate.
        instance._is_new = True

        # Cache the new instance
        if instance.id is not None and cls._store:
            cls._store.cache_instance(instance)

        return instance

    @classmethod
    def _process_nested_cache_lookups(cls, data: dict) -> dict:
        """Resolve relationship IDs/dicts to cached objects.

        For uncached dicts, injects parent context:
        - reverse_fk: injects {fk_column: parent_id} (e.g., mediaId for locations)
        - any: inherits accountId from parent when child is missing it

        Only iterates __relationships__ (not all model_fields). Inverse-only
        fields are populated via _sync_inverse_relationship during __setattr__,
        not during construction.
        """
        if not cls._store:
            return data

        processed = data.copy()
        parent_id = data.get("id")
        parent_account_id = data.get("accountId")

        for field_name, meta in cls.__relationships__.items():
            # Check field name first, then Pydantic alias (e.g., accountMediaIds → accountMedia)
            if field_name in data:
                data_key = field_name
            else:
                field_info = cls.model_fields.get(field_name)
                alias = field_info.alias if field_info and field_info.alias else None
                if alias and alias in data:
                    data_key = alias
                # belongs_to: resolve FK column → relationship field from cache
                elif (
                    meta.fk_column
                    and not meta.is_list
                    and meta.fk_column in data
                    and meta.query_strategy == "direct_field"
                ):
                    fk_val = data[meta.fk_column]
                    if isinstance(fk_val, int) and fk_val:
                        cached = cls._store.get_from_cache_by_type_name(
                            meta.inverse_type, fk_val
                        )
                        if cached:
                            processed[field_name] = cached
                    continue
                else:
                    continue
            value = data[data_key]
            if value is None:
                continue

            if meta.is_list and isinstance(value, list):
                resolved = []
                for item in value:
                    if isinstance(item, int):
                        cached = cls._store.get_from_cache_by_type_name(
                            meta.inverse_type, item
                        )
                        if cached:
                            resolved.append(cached)
                    elif isinstance(item, dict):
                        # Always enrich and let Pydantic validate — don't
                        # shortcut to cached objects here.  The identity map
                        # merge in _identity_map_validator will update the
                        # cached instance with fresh data (e.g. locations
                        # from the API that were lost during preload).
                        enriched = cls._enrich_child_dict(
                            item, meta, parent_id, parent_account_id
                        )
                        resolved.append(enriched)
                    else:
                        resolved.append(item)
                processed[field_name] = resolved
                # Remove alias key so Pydantic doesn't double-validate
                if data_key != field_name:
                    processed.pop(data_key, None)
            elif not meta.is_list and isinstance(value, int):
                cached = cls._store.get_from_cache_by_type_name(
                    meta.inverse_type, value
                )
                if cached:
                    processed[field_name] = cached
                    if data_key != field_name:
                        processed.pop(data_key, None)
            elif not meta.is_list and isinstance(value, dict) and "id" in value:
                cached = cls._store.get_from_cache_by_type_name(
                    meta.inverse_type, value["id"]
                )
                if cached:
                    processed[field_name] = cached
                else:
                    processed[field_name] = cls._enrich_child_dict(
                        value, meta, parent_id, parent_account_id
                    )
                if data_key != field_name:
                    processed.pop(data_key, None)
            elif not meta.is_list and isinstance(value, dict):
                # Dict without "id" (e.g., timelineStats keyed by accountId)
                processed[field_name] = cls._enrich_child_dict(
                    value, meta, parent_id, parent_account_id
                )
                if data_key != field_name:
                    processed.pop(data_key, None)

        return processed

    @staticmethod
    def _enrich_child_dict(
        child: dict,
        meta: RelationshipMetadata,
        parent_id: int | None,
        parent_account_id: int | None,
    ) -> dict:
        """Inject parent context into a nested child dict before construction.

        - reverse_fk: injects {fk_column: parent_id}
        - any: inherits accountId from parent when child lacks it
        """
        enriched = child.copy()
        # Inject FK from parent when fk_column is defined (e.g., mediaId for
        # MediaLocation, regardless of whether the strategy is reverse_fk or
        # assoc_table where the assoc table IS the entity table)
        if meta.fk_column and parent_id:
            enriched.setdefault(meta.fk_column, parent_id)
        # Inherit accountId from parent when missing
        if parent_account_id and "accountId" not in enriched:
            enriched["accountId"] = parent_account_id
        return enriched

    # ── Bidirectional Relationship Sync ───────────────────────────────

    def __setattr__(self, name: str, value: Any) -> None:
        """Auto-sync FK↔relationship and inverse relationships on assignment.

        Two sync paths:
        1. Setting a relationship field (e.g., media.account = acct_obj)
           → syncs FK scalar (media.accountId = acct_obj.id)
           → syncs inverse relationship on related object
        2. Setting an FK scalar (e.g., media.accountId = 123)
           → auto-resolves relationship from cache (media.account = cached Account)
        """
        super().__setattr__(name, value)

        # Path 1: relationship field → sync FK scalar + inverse
        if name in self.__relationships__:
            meta = self.__relationships__[name]
            if meta.fk_column and not meta.is_list:
                # Sync FK: media.account = acct → media.accountId = acct.id
                fk_val = value.id if isinstance(value, FanslyObject) else None
                object.__setattr__(self, meta.fk_column, fk_val)
            self._sync_inverse_relationship(name, value)

        # Path 2: FK scalar → auto-resolve relationship from cache
        elif name in self.__fk_to_rel__:
            rel_name, meta = self.__fk_to_rel__[name]
            if value is not None and self._store:
                cached = self._store.get_from_cache_by_type_name(
                    meta.inverse_type, value
                )
                object.__setattr__(self, rel_name, cached)
            else:
                object.__setattr__(self, rel_name, None)

    def _sync_inverse_relationship(self, field_name: str, new_value: Any) -> None:
        meta = self.__relationships__.get(field_name)
        if not meta or not meta.inverse_query_field or not meta.inverse_type:
            return

        if meta.is_list and isinstance(new_value, list):
            for obj in new_value:
                if isinstance(obj, FanslyObject):
                    self._add_to_inverse(obj, meta.inverse_query_field)
        elif new_value is not None and isinstance(new_value, FanslyObject):
            self._add_to_inverse(new_value, meta.inverse_query_field)

    def _add_to_inverse(self, related_obj: FanslyObject, inverse_field: str) -> None:
        current = getattr(related_obj, inverse_field, None)
        if current is None:
            # Lazily initialize list fields so sync works regardless of load order
            inverse_meta = type(related_obj).__relationships__.get(inverse_field)
            if inverse_meta and inverse_meta.is_list:
                current = []
                object.__setattr__(related_obj, inverse_field, current)
            else:
                return
        if isinstance(current, list):
            if self not in current:
                current.append(self)
                # Update snapshot so inverse-sync additions aren't falsely dirty
                if inverse_field in related_obj._snapshot:
                    related_obj._snapshot[inverse_field] = current.copy()
        else:
            object.__setattr__(related_obj, inverse_field, self)

    # ── Relationship Helpers ─────────────────────────────────────────

    async def _add_to_relationship(
        self, field_name: str, related_obj: FanslyObject
    ) -> None:
        """Add an object to a relationship field."""
        if field_name not in self.__relationships__:
            raise ValueError(f"No relationship metadata for '{field_name}'")
        meta = self.__relationships__[field_name]

        current = getattr(self, field_name)
        if current is None and meta.is_list:
            current = []
            setattr(self, field_name, current)
            # Re-read: Pydantic's validate_assignment copies the list
            current = getattr(self, field_name)

        if meta.is_list:
            if related_obj not in current:
                current.append(related_obj)
                self._sync_inverse_relationship(field_name, current)
        else:
            setattr(self, field_name, related_obj)

    async def _remove_from_relationship(
        self, field_name: str, related_obj: FanslyObject
    ) -> None:
        """Remove an object from a relationship field."""
        if field_name not in self.__relationships__:
            raise ValueError(f"No relationship metadata for '{field_name}'")
        meta = self.__relationships__[field_name]

        current = getattr(self, field_name)
        if current is None:
            return

        if meta.is_list and isinstance(current, list) and related_obj in current:
            current.remove(related_obj)
            if meta.inverse_query_field:
                inverse = getattr(related_obj, meta.inverse_query_field, None)
                if isinstance(inverse, list) and self in inverse:
                    inverse.remove(self)
                elif inverse is self:
                    setattr(related_obj, meta.inverse_query_field, None)
        elif current is related_obj:
            setattr(self, field_name, None)

    # ── Dirty Tracking ───────────────────────────────────────────────

    def model_post_init(self, _context: Any) -> None:
        """Post-construction: snapshot for dirty tracking.

        _is_new is managed by _identity_map_validator (set True for new
        objects) and cleared by store DB-loading paths (preload/get/find).
        """
        self._snapshot = {
            field: self._snapshot_value(getattr(self, field, None))
            for field in self.__tracked_fields__
        }

    @staticmethod
    def _snapshot_value(value: Any) -> Any:
        return value.copy() if isinstance(value, list) else value

    def is_dirty(self) -> bool:
        for field in self.__tracked_fields__:
            if getattr(self, field, None) != self._snapshot.get(field):
                return True
        return False

    def get_changed_fields(self) -> dict[str, Any]:
        return {
            field: getattr(self, field)
            for field in self.__tracked_fields__
            if getattr(self, field) != self._snapshot.get(field)
        }

    def mark_clean(self) -> None:
        self._snapshot = {
            field: self._snapshot_value(getattr(self, field, None))
            for field in self.__tracked_fields__
        }

    def mark_dirty(self) -> None:
        self._snapshot = {}

    # ── Output Serialization ─────────────────────────────────────────

    @staticmethod
    def _get_id(obj: Any) -> int | None:
        """Extract ID from an object or dict. Used by store for associations."""
        if isinstance(obj, dict):
            return obj.get("id")
        return getattr(obj, "id", None)

    def to_db_dict(self, only_dirty: bool = False) -> dict[str, Any]:
        """Serialize scalar fields for DB write.

        Args:
            only_dirty: If True, only include changed fields + id (for UPDATE).
                        If False, include all scalar fields (for INSERT).
        """
        rel_keys = set(self.__relationships__.keys())
        exclude = rel_keys | self._WRITE_EXCLUDED

        if only_dirty:
            changed = self.get_changed_fields()
            data = {k: v for k, v in changed.items() if k not in exclude}
            data["id"] = self.id
        else:
            data = self.model_dump(exclude=exclude)

        # Convert enums for DB storage.
        # ContentType uses a PostgreSQL native ENUM (stores member names),
        # so serialize as .name ("ACCOUNT_MEDIA") not .value (1).
        for k, v in list(data.items()):
            if isinstance(v, ContentType):
                data[k] = v.name
            elif isinstance(v, Enum):
                data[k] = v.value

        return data

    async def save(self) -> None:
        """Save to DB and update cache."""
        if self._store is None:
            raise RuntimeError(
                "Store not initialized — call FanslyObject._store = store first"
            )
        if not self.is_dirty() and not self._is_new:
            return
        await self._store.save(self)
        self.mark_clean()

    # ── Stub Creation ───────────────────────────────────────────────

    @classmethod
    def create_stub(cls, entity_id: int, **context: Any) -> Self:
        """Create a minimal stub instance for FK constraint satisfaction.

        Override in models that may be referenced by junction tables
        before full data is available.  The entity store calls this
        automatically when a record-based junction INSERT would
        violate an FK constraint because the target entity is missing.

        The stub is registered in ``stub_tracker`` for later enrichment
        when the real API data arrives.

        Args:
            entity_id: Primary key for the stub.
            **context: Row data from the junction record (varies by table).

        Raises:
            StubNotImplementedError: Default — subclass must override.
        """
        from errors import StubNotImplementedError

        raise StubNotImplementedError(cls, entity_id, context=context)

    # ── Field Update Helper ──────────────────────────────────────────

    @staticmethod
    def update_fields(
        instance: FanslyObject,
        data: dict[str, Any],
        exclude: set[str] | None = None,
    ) -> bool:
        """Update instance fields only if values have changed.

        Handles timestamp conversion for datetime fields automatically.

        Args:
            instance: Model instance to update
            data: Dictionary of field values to update
            exclude: Optional set of field names to exclude from updates

        Returns:
            True if any fields were updated, False otherwise
        """
        exclude = exclude or set()
        updated = False
        for key, value in data.items():
            if key in exclude:
                continue
            # Auto-convert timestamps
            if isinstance(value, (int, float)):
                field_info = instance.model_fields.get(key)
                if (field_info and field_info.annotation is datetime) or (
                    field_info
                    and hasattr(field_info.annotation, "__args__")
                    and datetime in getattr(field_info.annotation, "__args__", ())
                ):
                    value = _parse_timestamp(value)  # noqa: PLW2901

            current_value = getattr(instance, key, None)
            if current_value != value:
                setattr(instance, key, value)
                updated = True
        return updated

    @staticmethod
    def normalize_cdn_url(url: str | None) -> str | None:
        """Strip query params from CDN URLs to prevent false-positive updates."""
        if not url:
            return url
        try:
            parsed = urlparse(url)
        except Exception:
            return url
        else:
            return f"{parsed.scheme}://{parsed.netloc}{parsed.path}"


# ══════════════════════════════════════════════════════════════════════════
# Entity Models (leaf-first order)
# ══════════════════════════════════════════════════════════════════════════


# ── Composite-PK Records ─────────────────────────────────────────────────


class StubTracker(FanslyRecord):
    """Track records created as stubs awaiting full data enrichment."""

    __table_name__: ClassVar[str] = "stub_tracker"

    table_name: str
    record_id: int
    created_at: datetime
    reason: str | None = None


class PinnedPost(FanslyRecord):
    """Junction record for account pinned posts (pos-ordered)."""

    __table_name__: ClassVar[str] = "pinned_posts"

    postId: SnowflakeId
    accountId: SnowflakeId
    pos: int = 0
    createdAt: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _coerce_timestamp(cls, data: Any) -> Any:
        if isinstance(data, dict) and isinstance(data.get("createdAt"), (int, float)):
            data = {**data, "createdAt": _parse_timestamp(data["createdAt"])}
        return data


class MediaLocation(FanslyRecord):
    """Storage location for media content (CDN URLs).

    `location` stores the normalized URL (no query params) for DB dedup/matching.
    `raw_url` preserves the original CDN URL with auth params for downloading.
    """

    __table_name__: ClassVar[str] = "media_locations"

    mediaId: SnowflakeId
    locationId: int  # CDN location type code (1, 102, 103), not a Snowflake
    location: str
    raw_url: str | None = None  # Transient — not in DB table, preserved for download
    metadata: dict | None = (
        None  # Transient — m3u8 auth params (Policy, Key-Pair-Id, Signature)
    )

    @model_validator(mode="before")
    @classmethod
    def _normalize_location(cls, data: Any) -> Any:
        """Preserve raw URL and normalize location for DB storage."""
        if not isinstance(data, dict) or "location" not in data:
            return data
        raw = data["location"]
        data["raw_url"] = raw
        data["location"] = FanslyObject.normalize_cdn_url(raw) or raw
        return data


# ── Standalone Entities ──────────────────────────────────────────────────


class Hashtag(FanslyObject):
    """Hashtag extracted from post content. Auto-increment ID."""

    __table_name__: ClassVar[str] = "hashtags"
    __tracked_fields__: ClassVar[set[str]] = {"value", "stash_id", "posts"}
    __relationships__: ClassVar[dict[str, RelationshipMetadata]] = {
        "posts": habtm(
            "Post", assoc_table="post_hashtags", inverse_query_field="hashtags"
        ),
    }

    id: int | None = None  # auto-increment, not a Snowflake
    value: str
    stash_id: int | None = None
    posts: list[Post] = []  # type: ignore[name-defined]

    def __repr__(self) -> str:
        return f"<Hashtag {self.id}: {self.value}>"


# ── FK-Dependent Entities ────────────────────────────────────────────────


class Story(FanslyObject):
    """A story post authored by an account."""

    __table_name__: ClassVar[str] = "stories"
    __tracked_fields__: ClassVar[set[str]] = {
        "authorId",
        "title",
        "description",
        "content",
        "createdAt",
        "updatedAt",
        "author",
    }
    __relationships__: ClassVar[dict[str, RelationshipMetadata]] = {
        "author": belongs_to("Account", fk_column="authorId"),
    }

    authorId: SnowflakeId
    title: str | None = None
    description: str | None = None
    content: str
    createdAt: datetime
    updatedAt: datetime | None = None

    # Relationships
    author: Account | None = None  # type: ignore[name-defined]


class TimelineStats(FanslyObject):
    """Statistics about an account's timeline content. PK is accountId."""

    __table_name__: ClassVar[str] = "timeline_stats"
    __pk_column__: ClassVar[str] = "accountId"
    __tracked_fields__: ClassVar[set[str]] = {
        "imageCount",
        "videoCount",
        "bundleCount",
        "bundleImageCount",
        "bundleVideoCount",
        "fetchedAt",
    }
    _WRITE_EXCLUDED: ClassVar[set[str]] = {"id"}

    accountId: SnowflakeId
    imageCount: int | None = 0
    videoCount: int | None = 0
    bundleCount: int | None = 0
    bundleImageCount: int | None = 0
    bundleVideoCount: int | None = 0
    fetchedAt: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _set_id_from_pk(cls, data: Any) -> Any:
        if isinstance(data, dict) and "accountId" in data:
            data["id"] = data["accountId"]
        return data


class MediaStoryState(FanslyObject):
    """State of an account's media stories. PK is accountId."""

    __table_name__: ClassVar[str] = "media_story_states"
    __pk_column__: ClassVar[str] = "accountId"
    __tracked_fields__: ClassVar[set[str]] = {
        "status",
        "storyCount",
        "version",
        "createdAt",
        "updatedAt",
        "hasActiveStories",
    }
    _WRITE_EXCLUDED: ClassVar[set[str]] = {"id"}

    accountId: SnowflakeId
    status: int | None = None
    storyCount: int | None = 0
    version: int | None = None
    createdAt: datetime | None = None
    updatedAt: datetime | None = None
    hasActiveStories: bool | None = False

    @model_validator(mode="before")
    @classmethod
    def _set_id_from_pk(cls, data: Any) -> Any:
        if isinstance(data, dict) and "accountId" in data:
            data["id"] = data["accountId"]
        return data


class Media(FanslyObject):
    """A media item (image, video) with its metadata and variants."""

    __table_name__: ClassVar[str] = "media"
    __tracked_fields__: ClassVar[set[str]] = {
        "accountId",
        "meta_info",
        "location",
        "flags",
        "mimetype",
        "height",
        "width",
        "duration",
        "type",
        "status",
        "createdAt",
        "updatedAt",
        "local_filename",
        "content_hash",
        "is_downloaded",
        "stash_id",
        # Relationships tracked for dirty detection → _sync_associations
        "account",
        "variants",
        "locations",
    }
    __relationships__: ClassVar[dict[str, RelationshipMetadata]] = {
        "account": belongs_to("Account", fk_column="accountId"),
        "variants": habtm("Media", assoc_table="media_variants"),
        "locations": habtm(
            "MediaLocation", assoc_table="media_locations", fk_column="mediaId"
        ),
    }

    accountId: SnowflakeId
    meta_info: str | None = Field(default=None, alias="metadata")
    location: str | None = None
    flags: int | None = None
    mimetype: str | None = None
    height: int | None = None
    width: int | None = None
    duration: float | None = None
    type: int | None = None
    status: int | None = None
    createdAt: datetime | None = None

    @model_validator(mode="before")
    @classmethod
    def _extract_video_dimensions(cls, data: Any) -> Any:
        """Extract width/height/duration from JSON metadata string for videos."""
        if not isinstance(data, dict):
            return data
        raw_meta = data.get("metadata")
        if not raw_meta or not isinstance(raw_meta, str):
            return data
        if not data.get("mimetype", "").startswith("video/"):
            return data
        try:
            import json

            parsed = json.loads(raw_meta)
            if "original" in parsed:
                data.setdefault("width", parsed["original"].get("width"))
                data.setdefault("height", parsed["original"].get("height"))
            if "duration" in parsed:
                data.setdefault("duration", float(parsed["duration"]))
        except (json.JSONDecodeError, ValueError, AttributeError, KeyError):
            pass
        return data

    updatedAt: datetime | None = None
    local_filename: str | None = None
    content_hash: str | None = None
    is_downloaded: bool = False
    stash_id: int | None = None

    # Transient download fields — excluded from DB writes, used by download path
    download_url: str | None = None
    download_id: SnowflakeId | None = None  # Variant ID when downloading a variant
    file_extension: str | None = None
    is_preview: bool = False
    preview_id: SnowflakeId | None = None
    preview_url: str | None = None
    preview_mimetype: str | None = None
    default_normal_id: SnowflakeId | None = None

    # Relationships (managed by _sync_associations on save)
    account: Account | None = None  # type: ignore[name-defined]
    variants: list[Media] = []
    locations: list[MediaLocation] = []

    _WRITE_EXCLUDED: ClassVar[set[str]] = {
        "download_url",
        "download_id",
        "file_extension",
        "is_preview",
        "preview_id",
        "preview_url",
        "preview_mimetype",
        "default_normal_id",
    }

    @property
    def created_at_timestamp(self) -> float:
        """Created time as POSIX timestamp for os.utime and filename formatting."""
        if self.createdAt is None:
            return 0.0
        return self.createdAt.timestamp()

    def get_file_name(self, for_preview: bool = False) -> str:
        """Get filename for download.

        Args:
            for_preview: If True, generate filename for preview content

        Returns:
            Filename with timestamp, id marker, and extension
        """
        id_marker = "preview_id" if for_preview else "id"
        extension = self.file_extension

        if extension is None and self.download_url:
            extension = self.download_url.split("/")[-1].split(".")[-1].split("?")[0]

        ts_str = (
            self.createdAt.strftime("%Y-%m-%d_at_%H-%M_UTC")
            if self.createdAt
            else "unknown"
        )
        media_id = self.download_id or self.id
        return f"{ts_str}_{id_marker}_{media_id}.{extension}"

    def __repr__(self) -> str:
        return f"<Media {self.id}: {self.mimetype}>"


class AccountMedia(FanslyObject):
    """Associates media items with accounts, adding access control."""

    __table_name__: ClassVar[str] = "account_media"
    __tracked_fields__: ClassVar[set[str]] = {
        "accountId",
        "mediaId",
        "previewId",
        "createdAt",
        "deletedAt",
        "deleted",
        "access",
        "stash_id",
        # Relationship fields
        "account",
        "media",
        "preview",
    }
    __relationships__: ClassVar[dict[str, RelationshipMetadata]] = {
        "account": belongs_to("Account", fk_column="accountId"),
        "media": belongs_to("Media", fk_column="mediaId"),
        "preview": belongs_to("Media", fk_column="previewId"),
    }

    accountId: SnowflakeId
    mediaId: SnowflakeId
    previewId: SnowflakeId | None = None
    createdAt: datetime
    deletedAt: datetime | None = None
    deleted: bool = False
    access: bool = False
    stash_id: int | None = None

    # Relationships (auto-resolved from FK scalars via __setattr__ / cache)
    account: Account | None = None  # type: ignore[name-defined]
    media: Media | None = None
    preview: Media | None = None


class AccountMediaBundle(FanslyObject):
    """A collection of media items grouped together by an account."""

    __table_name__: ClassVar[str] = "account_media_bundles"
    __tracked_fields__: ClassVar[set[str]] = {
        "accountId",
        "previewId",
        "createdAt",
        "deletedAt",
        "deleted",
        "access",
        "purchased",
        "whitelisted",
        "stash_id",
        "account",
        "preview",
        "accountMedia",
    }
    __relationships__: ClassVar[dict[str, RelationshipMetadata]] = {
        "account": belongs_to("Account", fk_column="accountId"),
        "preview": belongs_to("Media", fk_column="previewId"),
        "accountMedia": habtm(
            "AccountMedia",
            assoc_table="account_media_bundle_media",
            ordered=True,
            target_field="account_media_ids",
        ),
    }

    @model_validator(mode="before")
    @classmethod
    def _prepare_bundle_data(cls, data: Any) -> Any:
        """Set defaults for fields the API omits on some paths.

        Note: bundleContent/accountMediaIds → accountMedia conversion is NOT
        done here because mode='before' runs INSIDE mode='wrap'. The wrap
        validator's _process_nested_cache_lookups would see the raw data
        before this runs. Conversion is handled in _process_single_bundle.
        """
        if not isinstance(data, dict):
            return data
        data.setdefault("createdAt", datetime.now(UTC))
        return data

    accountId: SnowflakeId
    previewId: SnowflakeId | None = None
    createdAt: datetime
    deletedAt: datetime | None = None
    deleted: bool = False
    access: bool = False
    purchased: bool = False
    whitelisted: bool = False
    stash_id: int | None = None

    # Relationships
    account: Account | None = None  # type: ignore[name-defined]
    preview: Media | None = None
    accountMedia: list[AccountMedia] = Field(default=[], alias="accountMediaIds")


class Wall(FanslyObject):
    """A collection of posts organized separately from the main timeline."""

    __table_name__: ClassVar[str] = "walls"
    __tracked_fields__: ClassVar[set[str]] = {
        "accountId",
        "pos",
        "name",
        "description",
        "createdAt",
        "stash_id",
        "account",
        "posts",
    }
    __relationships__: ClassVar[dict[str, RelationshipMetadata]] = {
        "account": belongs_to("Account", fk_column="accountId"),
        "posts": habtm("Post", assoc_table="wall_posts", inverse_query_field="walls"),
    }

    accountId: SnowflakeId
    pos: int | None = None
    name: str | None = None
    description: str | None = None
    createdAt: datetime | None = None
    stash_id: int | None = None

    # Relationships
    account: Account | None = None  # type: ignore[name-defined]
    posts: list[Post] = []  # type: ignore[name-defined]


class Attachment(FanslyObject):
    """An attachment to a post or message, referencing content by type."""

    __table_name__: ClassVar[str] = "attachments"
    __tracked_fields__: ClassVar[set[str]] = {
        "postId",
        "messageId",
        "contentId",
        "pos",
        "contentType",
        "post",
        "message",
    }
    __relationships__: ClassVar[dict[str, RelationshipMetadata]] = {
        "post": belongs_to("Post", fk_column="postId"),
        "message": belongs_to("Message", fk_column="messageId"),
    }

    @model_validator(mode="before")
    @classmethod
    def _coerce_content_type(cls, data: Any) -> Any:
        """Handle contentType as int (API) or enum name string (DB)."""
        if not isinstance(data, dict):
            return data
        ct = data.get("contentType")
        if isinstance(ct, str) and ct in ContentType.__members__:
            data["contentType"] = ContentType[ct]
        return data

    id: int | None = None  # auto-increment, not a Snowflake (API sends no id)

    postId: SnowflakeId | None = None
    messageId: SnowflakeId | None = None
    contentId: SnowflakeId
    pos: int
    contentType: ContentType

    # Relationships
    post: Post | None = None  # type: ignore[name-defined]
    message: Message | None = None  # type: ignore[name-defined]

    @property
    def is_account_media(self) -> bool:
        return self.contentType == ContentType.ACCOUNT_MEDIA

    @property
    def is_account_media_bundle(self) -> bool:
        return self.contentType == ContentType.ACCOUNT_MEDIA_BUNDLE

    @property
    def is_aggregated_post(self) -> bool:
        return self.contentType == ContentType.AGGREGATED_POSTS

    @property
    def is_story(self) -> bool:
        return self.contentType == ContentType.STORY

    # ── Content resolution (replaces SA lazy relationships) ───────

    @property
    def media(self) -> AccountMedia | None:
        """Resolve to AccountMedia when contentType is ACCOUNT_MEDIA."""
        if not self.is_account_media or not self._store:
            return None
        return self._store.get_from_cache(AccountMedia, self.contentId)

    @property
    def bundle(self) -> AccountMediaBundle | None:
        """Resolve to AccountMediaBundle when contentType is ACCOUNT_MEDIA_BUNDLE."""
        if not self.is_account_media_bundle or not self._store:
            return None
        return self._store.get_from_cache(AccountMediaBundle, self.contentId)

    @property
    def aggregated_post(self) -> Post | None:
        """Resolve to Post when contentType is AGGREGATED_POSTS."""
        if not self.is_aggregated_post or not self._store:
            return None
        return self._store.get_from_cache(Post, self.contentId)

    async def resolve_content(
        self,
    ) -> AccountMedia | AccountMediaBundle | Post | None:
        """Resolve the content based on contentType and contentId.

        Returns the related object from the identity map cache, or None.
        Restored from the pre-Pydantic SQLAlchemy model to keep the
        stash processing code's hasattr() checks working.
        """
        if self.contentType == ContentType.ACCOUNT_MEDIA:
            return self.media
        if self.contentType == ContentType.ACCOUNT_MEDIA_BUNDLE:
            return self.bundle
        if self.contentType == ContentType.AGGREGATED_POSTS:
            return self.aggregated_post
        return None


class PostMention(FanslyObject):
    """A mention in a post, linking to an account by handle.

    Auto-increment ID. The (postId, handle) pair is unique.
    accountId is nullable — populated when we can resolve the handle to an account.
    """

    __table_name__: ClassVar[str] = "post_mentions"
    __tracked_fields__: ClassVar[set[str]] = {
        "postId",
        "accountId",
        "handle",
        "post",
        "account",
    }
    __relationships__: ClassVar[dict[str, RelationshipMetadata]] = {
        "post": belongs_to("Post", fk_column="postId"),
        "account": belongs_to("Account", fk_column="accountId"),
    }

    id: int | None = None  # auto-increment, not a Snowflake
    postId: SnowflakeId
    accountId: SnowflakeId | None = None
    handle: str

    # Relationships
    post: Post | None = None  # type: ignore[name-defined]
    account: Account | None = None  # type: ignore[name-defined]

    def __repr__(self) -> str:
        return f"<PostMention {self.id}: @{self.handle} on post {self.postId}>"


class Post(FanslyObject):
    """A user post with attachments, hashtags, mentions, and wall associations."""

    __table_name__: ClassVar[str] = "posts"
    __tracked_fields__: ClassVar[set[str]] = {
        "accountId",
        "content",
        "fypFlag",
        "inReplyTo",
        "inReplyToRoot",
        "createdAt",
        "expiresAt",
        "stash_id",
        # Relationships
        "account",
        "replyTo",
        "replyToRoot",
        "attachments",
        "hashtags",
        "walls",
        "mentions",
    }
    __relationships__: ClassVar[dict[str, RelationshipMetadata]] = {
        "account": belongs_to("Account", fk_column="accountId"),
        "replyTo": belongs_to("Post", fk_column="inReplyTo"),
        "replyToRoot": belongs_to("Post", fk_column="inReplyToRoot"),
        "attachments": has_many("Attachment", fk_column="postId"),
        "hashtags": habtm(
            "Hashtag", assoc_table="post_hashtags", inverse_query_field="posts"
        ),
        "walls": habtm("Wall", assoc_table="wall_posts", inverse_query_field="posts"),
        "mentions": has_many("PostMention", fk_column="postId"),
    }

    @model_validator(mode="before")
    @classmethod
    def _prepare_post_data(cls, data: Any) -> Any:
        """Filter non-media attachment types before validation.

        accountMentions → mentions alias is handled by _process_nested_cache_lookups
        via Field(alias="accountMentions") on the mentions field.
        """
        if not isinstance(data, dict):
            return data

        # Filter attachments to valid, media-relevant content types
        if "attachments" in data:
            valid_types = {ct.value for ct in ContentType}
            # TIP (7) not in enum. TIP_GOALS (7100) in enum but not media.
            skip_types = {7, 7100}
            data["attachments"] = [
                a
                for a in data["attachments"]
                if isinstance(a, dict)
                and a.get("contentType") in valid_types
                and a.get("contentType") not in skip_types
            ]

        return data

    accountId: SnowflakeId
    content: str | None = ""
    fypFlag: int | None = Field(default=0, alias="fypFlags")
    inReplyTo: SnowflakeId | None = None
    inReplyToRoot: SnowflakeId | None = None
    createdAt: datetime | None = None
    expiresAt: datetime | None = None
    stash_id: int | None = None

    # Relationships
    account: Account | None = None  # type: ignore[name-defined]
    replyTo: Post | None = None
    replyToRoot: Post | None = None
    attachments: list[Attachment] = []
    hashtags: list[Hashtag] = []
    walls: list[Wall] = []
    mentions: list[PostMention] = Field(default=[], alias="accountMentions")

    @classmethod
    def create_stub(cls, entity_id: int, **context: Any) -> Post:
        """Create a minimal Post stub for FK satisfaction.

        Requires ``accountId`` in context (provided by PinnedPost records).
        """
        account_id = context.get("accountId")
        if not account_id:
            from errors import StubNotImplementedError

            raise StubNotImplementedError(cls, entity_id, context=context)
        return cls(id=entity_id, accountId=account_id)

    def __repr__(self) -> str:
        return f"<Post {self.id}>"


class Message(FanslyObject):
    """A message in a conversation or group."""

    __table_name__: ClassVar[str] = "messages"
    __tracked_fields__: ClassVar[set[str]] = {
        "groupId",
        "senderId",
        "recipientId",
        "content",
        "createdAt",
        "deletedAt",
        "deleted",
        "stash_id",
        "group",
        "sender",
        "recipient",
        "attachments",
    }
    __relationships__: ClassVar[dict[str, RelationshipMetadata]] = {
        "group": belongs_to("Group", fk_column="groupId"),
        "sender": belongs_to("Account", fk_column="senderId"),
        "recipient": belongs_to("Account", fk_column="recipientId"),
        "attachments": has_many("Attachment", fk_column="messageId"),
    }

    @model_validator(mode="before")
    @classmethod
    def _prepare_message_data(cls, data: Any) -> Any:
        """Filter non-media attachment types before validation."""
        if not isinstance(data, dict):
            return data
        if "attachments" in data:
            valid_types = {ct.value for ct in ContentType}
            skip_types = {7, 7100}
            data["attachments"] = [
                a
                for a in data["attachments"]
                if isinstance(a, dict)
                and a.get("contentType") in valid_types
                and a.get("contentType") not in skip_types
            ]
        return data

    groupId: SnowflakeId | None = None
    senderId: SnowflakeId
    recipientId: SnowflakeId | None = None
    content: str
    createdAt: datetime
    deletedAt: datetime | None = None
    deleted: bool = False
    stash_id: int | None = None

    # Relationships
    group: Group | None = None  # type: ignore[name-defined]
    sender: Account | None = None  # type: ignore[name-defined]
    recipient: Account | None = None  # type: ignore[name-defined]
    attachments: list[Attachment] = []

    def __repr__(self) -> str:
        return f"<Message {self.id}>"


class Conversation(FanslyRecord):
    """Conversation summary from the messages API data[] array.

    Not persisted — shapes the data[] JSON (which uses groupId/account_id)
    into standard field names for Group creation. Only needed during processing.
    """

    groupId: SnowflakeId
    account_id: SnowflakeId  # the user's own account → maps to Group.createdBy
    partnerAccountId: SnowflakeId | None = None
    lastMessageId: SnowflakeId | None = None

    def to_group_dict(self) -> dict:
        """Convert to a Group-compatible dict with standard field names."""
        d: dict[str, Any] = {"id": self.groupId, "createdBy": self.account_id}
        if self.lastMessageId is not None:
            d["lastMessageId"] = self.lastMessageId
        return d


class Group(FanslyObject):
    """A message group with users. Created from aggregationData.groups[].

    data[] conversation summaries use Conversation (FanslyRecord) instead,
    which normalizes groupId/account_id → standard Group field names.
    """

    __table_name__: ClassVar[str] = "groups"
    __tracked_fields__: ClassVar[set[str]] = {
        "createdBy",
        "lastMessageId",
        "creator",
        "lastMessage",
        "users",
        "messages",
    }
    __relationships__: ClassVar[dict[str, RelationshipMetadata]] = {
        "creator": belongs_to("Account", fk_column="createdBy"),
        "lastMessage": belongs_to("Message", fk_column="lastMessageId"),
        "users": habtm("Account", assoc_table="group_users"),
        "messages": has_many("Message", fk_column="groupId"),
    }

    @model_validator(mode="before")
    @classmethod
    def _resolve_last_message(cls, data: Any) -> Any:
        """Extract lastMessageId from nested lastMessage dict.

        When the API sends lastMessage as a nested dict (e.g., {"id": 123}),
        extract the ID into lastMessageId. Don't pop lastMessage — it's now
        a Pydantic field (Message | None) resolved via belongs_to.
        """
        if not isinstance(data, dict):
            return data
        lm = data.get("lastMessage")
        if isinstance(lm, dict) and "id" in lm:
            data.setdefault("lastMessageId", lm["id"])
            # Remove the nested dict so Pydantic doesn't try to validate
            # a raw dict as a Message object — the belongs_to cache
            # resolution will handle it via lastMessageId.
            del data["lastMessage"]
        return data

    createdBy: SnowflakeId
    lastMessageId: SnowflakeId | None = None

    # Relationships
    creator: Account | None = None  # type: ignore[name-defined]
    lastMessage: Message | None = None
    users: list[Account] = []  # type: ignore[name-defined]
    messages: list[Message] = []


class Account(FanslyObject):
    """A Fansly account — the hub entity connecting all content."""

    __table_name__: ClassVar[str] = "accounts"
    __tracked_fields__: ClassVar[set[str]] = {
        "username",
        "displayName",
        "flags",
        "version",
        "createdAt",
        "subscribed",
        "about",
        "location",
        "following",
        "profileAccess",
        "stash_id",
        # Relationships
        "avatar",
        "banner",
        "pinnedPosts",
        "walls",
        "accountMedia",
        "accountMediaBundles",
        "stories",
        "timelineStats",
        "mediaStoryState",
    }
    __relationships__: ClassVar[dict[str, RelationshipMetadata]] = {
        "avatar": has_one_through(
            "Media",
            assoc_table="account_avatar",
            notes="1:1 junction — replace on save.",
        ),
        "banner": has_one_through(
            "Media",
            assoc_table="account_banner",
            notes="1:1 junction — replace on save.",
        ),
        "pinnedPosts": habtm(
            "PinnedPost",
            assoc_table="pinned_posts",
            target_field="pinned_post_ids",
            notes="Record junction — pos/createdAt from API data.",
        ),
        "walls": has_many("Wall", fk_column="accountId"),
        "accountMedia": has_many(
            "AccountMedia",
            fk_column="accountId",
            target_field="account_media_ids",
        ),
        "accountMediaBundles": has_many(
            "AccountMediaBundle",
            fk_column="accountId",
            target_field="bundle_ids",
        ),
        "stories": has_many("Story", fk_column="authorId"),
        "timelineStats": has_one("TimelineStats", fk_column="accountId"),
        "mediaStoryState": has_one("MediaStoryState", fk_column="accountId"),
    }
    # Inverse-only fields — no DB column, no __relationships__ entry
    _WRITE_EXCLUDED: ClassVar[set[str]] = {
        "posts",
        "sent_messages",
        "received_messages",
    }

    username: str
    displayName: str | None = None
    flags: int | None = None
    version: int | None = None
    createdAt: datetime | None = None
    subscribed: bool | None = False
    about: str | None = None
    location: str | None = None
    following: bool | None = False
    profileAccess: bool | None = False
    stash_id: int | None = None

    # Relationships (managed by store)
    avatar: Media | None = None
    banner: Media | None = None
    pinnedPosts: list[PinnedPost] = []
    walls: list[Wall] = []
    accountMedia: list[AccountMedia] = []
    accountMediaBundles: list[AccountMediaBundle] = []
    stories: list[Story] = []

    # Inverse-only relationships (populated by bidirectional sync)
    timelineStats: TimelineStats | None = None
    mediaStoryState: MediaStoryState | None = None
    posts: list[Post] = []
    sent_messages: list[Message] = []
    received_messages: list[Message] = []

    def __repr__(self) -> str:
        return f"<Account {self.id}: {self.username}>"


# ── Type Registry ────────────────────────────────────────────────────────
# Maps type name strings (used in RelationshipMetadata.inverse_type) to classes.

_TYPE_REGISTRY: dict[str, type[FanslyObject]] = {
    cls.__name__: cls
    for cls in [
        Account,
        AccountMedia,
        AccountMediaBundle,
        Attachment,
        Group,
        Hashtag,
        Media,
        MediaStoryState,
        Message,
        Post,
        PostMention,
        Story,
        TimelineStats,
        Wall,
    ]
}


def get_from_cache_by_type_name(
    store: Any, type_name: str, entity_id: int
) -> FanslyObject | None:
    """Lookup cached entity by type name string."""
    model_type = _TYPE_REGISTRY.get(type_name)
    if model_type is None:
        return None
    return store.get_from_cache(model_type, entity_id)


# ── Rebuild models for forward reference resolution ──────────────────────

Hashtag.model_rebuild()
Media.model_rebuild()
AccountMediaBundle.model_rebuild()
Wall.model_rebuild()
Attachment.model_rebuild()
PostMention.model_rebuild()
Post.model_rebuild()
Message.model_rebuild()
Group.model_rebuild()
Account.model_rebuild()
Story.model_rebuild()
TimelineStats.model_rebuild()
MediaStoryState.model_rebuild()
AccountMedia.model_rebuild()
