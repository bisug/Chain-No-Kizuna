from aiogram import types

from chainnokizuna.core.resources import GlobalState

def inline_keyboard_from_button(button: types.InlineKeyboardButton) -> types.InlineKeyboardMarkup:
    return types.InlineKeyboardMarkup(inline_keyboard=[[button]])


def get_add_to_group_keyboard() -> types.InlineKeyboardMarkup:
    username = GlobalState.bot_user.username if GlobalState.bot_user else "ChainNoKizunaBot"
    return inline_keyboard_from_button(
        types.InlineKeyboardButton(text="Add to group", url=f"https://t.me/{username}?startgroup=_")
    )

def get_add_vp_to_group_keyboard() -> types.InlineKeyboardMarkup:
    username = GlobalState.vp_user.username if GlobalState.vp_user else "KizunaAssistantBot"
    return inline_keyboard_from_button(
        types.InlineKeyboardButton(text="Add Kizuna Assistant to group", url=f"https://t.me/{username}?startgroup=_")
    )
