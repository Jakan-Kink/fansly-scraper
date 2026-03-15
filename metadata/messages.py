"""Message and group processing module."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING, Any

from textio import json_output

from .account import process_account_data, process_media_bundles_data
from .models import Account, Conversation, Group, Message, get_store
from .relationship_logger import (
    log_missing_relationship,
    print_missing_relationships_summary,
)


if TYPE_CHECKING:
    from config import FanslyConfig
    from download.core import DownloadState


async def _process_single_message(
    message_data: dict,
) -> Message | None:
    """Process a single message's data and return the message instance."""
    store = get_store()

    required_fields = {"id", "senderId", "createdAt"}
    missing = {f for f in required_fields if f not in message_data}
    if missing:
        for field in missing:
            json_output(
                1, "meta/mess - missing_required_field", {"missing_field": field}
            )
        return None

    message = Message.model_validate(message_data)
    await store.save(message)
    return message


async def process_messages_metadata(
    config: FanslyConfig,
    _state: DownloadState,
    data: dict[str, Any] | list[dict[str, Any]],
) -> None:
    """Process message metadata and store in the database.

    Message._prepare_message_data filters non-media attachment types.
    _process_nested_cache_lookups resolves attachment dicts with FK injection.
    store.save() persists Message + attachments via _sync_associations.
    """

    if isinstance(data, list):
        data = {"messages": data}

    data = copy.deepcopy(data)
    messages = data.get("messages", [])

    # Process media bundles
    await process_media_bundles_data(config, data)

    # Process messages — attachments handled by model_validate + _sync_associations
    for message_data in messages:
        await _process_single_message(message_data)


async def _process_single_group(
    group_data: dict,
    source: str,
) -> Group | None:
    """Process a single group dict with standard field names (id, createdBy).

    data[] items are normalized via Conversation.to_group_dict() before calling;
    aggregationData.groups[] already use standard names.
    """
    store = get_store()

    group_id = group_data.get("id")
    creator_id = group_data.get("createdBy")
    if not group_id or not creator_id:
        for field, value in [("id", group_id), ("createdBy", creator_id)]:
            if not value:
                json_output(
                    1,
                    "meta/mess - missing_required_field",
                    {"groupId": group_id, "missing_field": field},
                )
        return None

    await log_missing_relationship(
        table_name="groups",
        field_name="createdBy",
        missing_id=creator_id,
        referenced_table="accounts",
        context={"groupId": group_id, "source": source},
    )

    if "lastMessageId" in group_data:
        message_exists = await log_missing_relationship(
            table_name="groups",
            field_name="lastMessageId",
            missing_id=group_data["lastMessageId"],
            referenced_table="messages",
            context={"groupId": group_id, "source": source},
        )
        if not message_exists:
            del group_data["lastMessageId"]

    # Strip users — raw API format [{"userId": ...}] can't be validated
    # as list[Account]. Resolve separately after model_validate.
    users_data = group_data.pop("users", [])

    group = Group.model_validate(group_data)
    await store.save(group)

    # Resolve users from identity map (accounts processed first)
    if users_data:
        user_objs = []
        for user in users_data:
            user_id = user.get("userId") if isinstance(user, dict) else user
            if not user_id:
                continue
            user_id = int(user_id) if isinstance(user_id, str) else user_id
            account = store.get_from_cache(Account, user_id)
            if account:
                user_objs.append(account)
        if user_objs:
            group.users = user_objs
            await store.save(group)

    return group


async def process_groups_response(
    config: FanslyConfig,
    state: DownloadState,
    response: dict,
) -> None:
    """Process group messaging response data.

    Two formats in the response:
    - data[]: conversation summaries (groupId, account_id) → Conversation
    - aggregationData.groups[]: full Group objects (id, createdBy, users)

    Accounts are processed first so they're in the identity map
    for group user resolution.
    """
    response = copy.deepcopy(response)

    # Process accounts first — must be in identity map for group user resolution
    aggregation_data = response.get("aggregationData", {})
    for account in aggregation_data.get("accounts", []):
        await process_account_data(config, data=account, state=state)

    # Process conversation summaries from data[] → normalize via Conversation
    for data_group in response.get("data", []):
        conv = Conversation.model_validate(data_group)
        await _process_single_group(conv.to_group_dict(), "data_groups")

    # Process full group objects from aggregation data (already standard names)
    for group_data in aggregation_data.get("groups", []):
        await _process_single_group(group_data, "aggregation_groups")

    print_missing_relationships_summary()
