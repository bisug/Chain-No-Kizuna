import logging
from typing import Any, Awaitable, Coroutine, Optional, TypeVar

from aiogram import types

from config import ADMIN_GROUP_ID
from chainnokizuna.core.resources import bot

T = TypeVar("T")

async def send_admin_group(*args: Any, **kwargs: Any) -> Optional[types.Message]:
    try:
        if not ADMIN_GROUP_ID:
            return None
        return await bot.send_message(ADMIN_GROUP_ID, *args, **kwargs)
    except Exception as e:
        logger = logging.getLogger(__name__)
        logger.error(f"Failed to send message to admin group: {e}")
        return None


def awaitable_to_coroutine(awaitable: Awaitable[T]) -> Coroutine[Any, Any, T]:
    # Convert awaitable like aiogram TelegramMethod to a coroutine
    # so that it can be used in asyncio.create_task()
    async def _runner() -> T:
        return await awaitable

    return _runner()
