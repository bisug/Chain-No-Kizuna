from aiogram import types, Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Filter
from aiogram.utils.chat_member import ADMINS

from config import OWNER_ID
from chainnokizuna.core.resources import GlobalState


class IsOwner(Filter):
    async def __call__(self, message: types.Message) -> bool:
        return message.from_user.id == OWNER_ID


class IsAdmin(Filter):
    async def __call__(self, message: types.Message) -> bool:
        if message.from_user.id == OWNER_ID:
            return True

        try:
            member = await message.bot.get_chat_member(message.chat.id, message.from_user.id)
        except TelegramBadRequest as e:
            if "CHAT_ADMIN_REQUIRED" in str(e):
                return False
            else:
                raise e
        else:
            return isinstance(member, ADMINS)


class HasGameInstance(Filter):
    async def __call__(self, message: types.Message) -> bool:
        return message.chat.id in GlobalState.games


class IsMainBot(Filter):
    async def __call__(self, message: types.Message, bot: Bot) -> bool:
        return GlobalState.bot_user and bot.id == GlobalState.bot_user.id
