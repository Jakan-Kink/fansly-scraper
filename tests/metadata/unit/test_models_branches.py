"""Branch-coverage tests for metadata/models.py.

Targets the FanslyObject.__setattr__ relationship/FK sync arcs (814->818,
836-838, 832-835, 847->846), the before-validator guards (1119->1127, 1123,
1328->1330, 2079, 2255, 2261), and Attachment.resolve_content's contentType
dispatch (1689, 1691, 1694). The validators are exercised as direct classmethod
calls (the cleanest way to drive a single branch without tripping downstream
field validation on deliberately-malformed input).
"""

from collections.abc import Callable
from typing import Any

import pytest
from pydantic import BaseModel
from stash_graphql_client.types.unset import UNSET

from metadata.models import (
    Account,
    ContentType,
    MonitorState,
    PinnedPost,
    Subscription,
)
from tests.fixtures.metadata.metadata_factories import (
    AttachmentFactory,
    MediaFactory,
    PostFactory,
)
from tests.fixtures.utils.test_isolation import snowflake_id


class TestSetattrRelationshipSync:
    """FanslyObject.__setattr__ Path 1 (relationship) / Path 2 (FK scalar)."""

    def test_belongs_to_set_to_unset_leaves_fk_untouched(self):
        """Branch 814->818: assigning UNSET to a belongs_to skips the FK mutation.

        ``isinstance(UNSET, FanslyObject)`` is False and ``UNSET is None`` is
        False, so neither FK-write fires — control falls straight to the inverse
        sync, leaving the FK column as-is (lazy hydration resolves it later).
        """
        media = MediaFactory.build(accountId=snowflake_id())
        original_fk = media.accountId
        media.account = UNSET
        assert media.accountId == original_fk
        assert media.account is UNSET

    def test_fk_scalar_no_store_marks_relationship_unset(self):
        """Lines 836-838: setting an FK with no store → relationship marked UNSET."""
        media = MediaFactory.build()
        assert media._store is None
        media.accountId = snowflake_id()
        assert media.account is UNSET

    @pytest.mark.asyncio
    async def test_fk_scalar_cache_miss_marks_relationship_unset(self, entity_store):
        """Lines 832-835: FK set with a store but a cache miss → relationship UNSET.

        ``get_from_cache_by_type_name`` returns None for an id absent from the
        identity map, so the relationship is marked UNSET (not None) to protect
        the just-set FK from a later ``to_db_dict`` clobber.
        """
        media = MediaFactory.build()
        object.__setattr__(media, "_store", entity_store)
        media.accountId = snowflake_id()  # never cached
        assert media.account is UNSET

    def test_sync_inverse_skips_non_fanslyobject_list_items(self):
        """Branch 847->846: list items that aren't FanslyObjects are skipped.

        ``Post.hashtags`` is a has_many with an inverse; a list of raw values
        passes the ``is_list`` guard but each item fails ``isinstance(...,
        FanslyObject)``, so the loop advances without inverse-syncing.
        """
        post = PostFactory.build(id=snowflake_id(), accountId=snowflake_id())
        post._sync_inverse_relationship("hashtags", [123, "x"])  # must not raise


def _before_validator(model: type[BaseModel], name: str) -> Callable[..., Any]:
    """Return a model's ``mode="before"`` validator as a plain callable.

    ``@model_validator`` exposes the classmethod as a non-callable descriptor
    proxy on the class; the underlying function lives in
    ``__pydantic_decorators__`` and is invoked directly with the raw input.
    """
    return model.__pydantic_decorators__.model_validators[name].func


class TestModelValidators:
    """``mode="before"`` validator guard branches, invoked via their raw func."""

    def test_pinnedpost_coerce_non_dict_passthrough(self):
        """Branch 1119->1127: non-dict data is returned unchanged."""
        coerce = _before_validator(PinnedPost, "_coerce_fields")
        assert coerce("not-a-dict") == "not-a-dict"

    def test_pinnedpost_coerce_stringified_pos(self):
        """Line 1123: a non-int ``pos`` is coerced to int."""
        coerce = _before_validator(PinnedPost, "_coerce_fields")
        assert coerce({"pos": "5"})["pos"] == 5

    def test_pinnedpost_coerce_none_pos_defaults_zero(self):
        """Line 1125: a None ``pos`` coerces to 0 rather than raising."""
        coerce = _before_validator(PinnedPost, "_coerce_fields")
        assert coerce({"pos": None})["pos"] == 0

    def test_monitorstate_set_id_skips_without_creatorid(self):
        """Branch 1328->1330: a dict lacking creatorId gets no synthesized id."""
        out = _before_validator(MonitorState, "_set_id_from_pk")({"lastRunAt": 1})
        assert "id" not in out

    def test_subscription_stringify_non_dict_passthrough(self):
        """Line 2079: non-dict data is returned unchanged."""
        out = _before_validator(Subscription, "_stringify_id_fields")("nope")
        assert out == "nope"

    def test_account_embedded_subscription_non_dict_passthrough(self):
        """Line 2255: non-dict data is returned unchanged."""
        out = _before_validator(Account, "_coerce_embedded_subscription")(42)
        assert out == 42

    def test_account_embedded_subscription_non_list_subscriptions(self):
        """Branch 2261: a non-list ``subscriptions`` short-circuits the merge."""
        data = {"subscription": {"id": 1}, "subscriptions": "notalist"}
        out = _before_validator(Account, "_coerce_embedded_subscription")(data)
        assert out["subscriptions"] == "notalist"


class TestResolveContent:
    """Attachment.resolve_content dispatch on contentType."""

    @pytest.mark.asyncio
    async def test_resolve_account_media(self):
        """Line 1689: contentType ACCOUNT_MEDIA → resolves via ``.media``."""
        att = AttachmentFactory.build(contentType=ContentType.ACCOUNT_MEDIA)
        assert await att.resolve_content() is None  # no store → property yields None

    @pytest.mark.asyncio
    async def test_resolve_account_media_bundle(self):
        """Line 1691: contentType ACCOUNT_MEDIA_BUNDLE → resolves via ``.bundle``."""
        att = AttachmentFactory.build(contentType=ContentType.ACCOUNT_MEDIA_BUNDLE)
        assert await att.resolve_content() is None

    @pytest.mark.asyncio
    async def test_resolve_other_contenttype_returns_none(self):
        """Line 1694: a non-media/non-post contentType falls through to None."""
        att = AttachmentFactory.build(contentType=ContentType.STORY)
        assert await att.resolve_content() is None
