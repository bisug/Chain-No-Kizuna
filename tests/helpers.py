"""Shared helpers for the test suite."""

import asyncio
import datetime

from aiogram import types


def make_user(
    user_id: int = 777, name: str = "Tester", is_bot: bool = False
) -> types.User:
    return types.User(
        id=user_id, is_bot=is_bot, first_name=name, username=f"u{user_id}"
    )


def make_bot_full_info(
    user_id: int = 888, name: str = "Virtual Player"
) -> types.BotFullInfo:
    """Bot API 10.5 getMe result; aiogram 3.34 no longer returns a User here."""
    return types.BotFullInfo(
        id=user_id,
        is_bot=True,
        first_name=name,
        username=f"vp{user_id}",
        can_join_groups=False,
        can_read_all_group_messages=False,
        supports_guest_queries=False,
        supports_inline_queries=False,
        needs_inline_query_location=False,
        can_connect_to_business=False,
        has_main_web_app=False,
        has_topics_enabled=False,
        allows_users_to_create_topics=False,
        has_topic_sidebar=False,
        can_manage_bots=False,
        supports_join_request_queries=False,
    )


def make_message(
    text: str = "hello",
    chat_id: int = -1001234567890,
    chat_type: str = "supergroup",
    user: types.User | None = None,
    reply_to_message: types.Message | None = None,
    message_id: int = 1,
    **extra,
) -> types.Message:
    if reply_to_message is not None:
        extra["reply_to_message"] = reply_to_message
    return types.Message(
        message_id=message_id,
        date=datetime.datetime.now(),
        chat=types.Chat(
            id=chat_id,
            type=chat_type,
            title="Test Group" if chat_type != "private" else None,
        ),
        from_user=user if user is not None else make_user(),
        text=text,
        **extra,
    )


class SilentBot:
    """Stands in for a real Bot: records sends, never touches the network."""

    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, **kwargs):
        self.sent.append((chat_id, text, kwargs))
        return make_message(text=str(text), chat_id=chat_id)

    async def delete_message(self, chat_id, message_id):
        self.sent.append(("delete", chat_id, message_id))

    async def get_chat_member(self, chat_id, user_id):
        return types.ChatMember(member=make_user(user_id), status="creator")

    async def get_chat(self, chat_id):
        return types.Chat(id=chat_id, type="supergroup", title="Test Group")


def run(coro):
    """Run a coroutine on a fresh event loop."""
    return asyncio.run(coro)
