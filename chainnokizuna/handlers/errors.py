import asyncio
import traceback

from aiogram import types
from aiogram.enums import ParseMode
from aiogram.exceptions import (TelegramMigrateToChat,
                                TelegramRetryAfter)

from chainnokizuna.core.resources import GlobalState, get_db
from config import GameState
from chainnokizuna.utils.telegram import send_admin_group, awaitable_to_coroutine


async def migrate_chat(old_chat_id: int, new_chat_id: int) -> None:
    if old_chat_id in GlobalState.games:
        GlobalState.games[new_chat_id] = GlobalState.games.pop(old_chat_id)
        GlobalState.games[new_chat_id].group_id = new_chat_id
        asyncio.create_task(
            awaitable_to_coroutine(send_admin_group(f"Game moved from {old_chat_id} to {new_chat_id}."))
        )

    db = get_db()
    await db.games.update_many(
        {"group_id": old_chat_id},
        {"$set": {"group_id": new_chat_id}}
    )

    await send_admin_group(f"Group statistics migrated from {old_chat_id} to {new_chat_id}.")


async def error_handler(event: types.ErrorEvent) -> None:
    update = event.update
    error = event.exception

    if update is not None:
        if update.message is not None and update.message.chat is not None:
            group_id = update.message.chat.id
            if group_id in GlobalState.games:
                GlobalState.games[group_id].request_stale_scan()

        if isinstance(error, TelegramMigrateToChat):
            await migrate_chat(update.message.chat.id, error.migrate_to_chat_id)
            return

        send_admin_msg = await send_admin_group(
            (
                f"<code>{error.__class__.__name__} @ "
                f"{group_id if update.message and update.message.chat else 'idk'}</code>:\n"
                f"<pre>{str(error)}</pre>"
            ) if isinstance(error, TelegramRetryAfter) else (
                "<pre>"
                + "".join(traceback.format_exception(error))
                + f"@ {group_id if update.message and update.message.chat else 'idk'}</pre>"
            ),
            parse_mode=ParseMode.HTML
        )
        if update.message and update.message.chat:
            asyncio.create_task(
                awaitable_to_coroutine(update.message.reply(
                    f"Error occurred (<code>{error.__class__.__name__}</code>). My owner has been notified.",
                    parse_mode=ParseMode.HTML
                ))
            )

            if group_id in GlobalState.games:
                # send_admin_group returns None when admin reporting is disabled
                # (ADMIN_GROUP_ID unset) or the send failed. Never dereference it.
                if send_admin_msg is not None:
                    asyncio.create_task(
                        awaitable_to_coroutine(send_admin_msg.reply(f"Killing game in {group_id} consequently."))
                    )
                GlobalState.games[group_id].state = GameState.KILLGAME
                await asyncio.sleep(2)

                if group_id in GlobalState.games:
                    del GlobalState.games[group_id]
                    try:
                        await update.message.reply("Game ended forcibly.")
                    except Exception:
                        # Best-effort notice: never let it mask the original error
                        pass
    else:  # TODO: update is None, what to do?
        pass

    raise error from None
