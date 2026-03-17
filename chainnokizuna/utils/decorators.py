from functools import wraps
from typing import Any, Callable

from aiogram import types

from chainnokizuna.utils.keyboards import get_add_to_group_keyboard

def send_private_only_message(f: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(f)
    async def inner(message: types.Message, *args: Any, **kwargs: Any) -> None:
        if message.chat.id < 0:
            await message.reply("Please use this command in private.")
            return
        await f(message, *args, **kwargs)

    return inner


def send_groups_only_message(f: Callable[..., Any]) -> Callable[..., Any]:
    @wraps(f)
    async def inner(message: types.Message, *args: Any, **kwargs: Any) -> None:
        if message.chat.id > 0:
            await message.reply(
                "This command can only be used in groups.",
                reply_markup=get_add_to_group_keyboard()
            )
            return
        await f(message, *args, **kwargs)

    return inner
