import asyncio
import logging
import traceback

from aiogram import types
from aiogram.enums import ParseMode
from aiogram.exceptions import (TelegramMigrateToChat,
                                TelegramRetryAfter)

from chainnokizuna.core.resources import GlobalState, get_db
from config import GameState
from chainnokizuna.utils.telegram import send_admin_group, awaitable_to_coroutine

logger = logging.getLogger(__name__)


async def migrate_chat(old_chat_id: int, new_chat_id: int) -> None:
    if old_chat_id in GlobalState.games:
        game = GlobalState.games.pop(old_chat_id)
        game.group_id = new_chat_id
        GlobalState.games[new_chat_id] = game
        # Re-key the persisted copy too, or a restart would restore the game
        # against the old, unreachable chat id.
        from chainnokizuna.db.redis import move_saved_game, save_game
        await move_saved_game(old_chat_id, new_chat_id)
        # Re-save under the new id so the stored group_id matches as well.
        await save_game(game)
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

    # Always log locally, even when reporting upstream is impossible, so a
    # failure can never be completely invisible.
    logger.error("Unhandled error in %s: %s", type(event.update).__name__, error,
                 exc_info=error)

    message = update.message if update is not None else None
    group_id = message.chat.id if message is not None and message.chat is not None else None

    if group_id is not None and group_id in GlobalState.games:
        GlobalState.games[group_id].request_stale_scan()

    if isinstance(error, TelegramMigrateToChat):
        # Guarded: dereferencing update.message here used to raise
        # AttributeError, which replaced the real error with a misleading one.
        if group_id is None:
            logger.error("MigrateToChat error with no resolvable chat id: %s", error)
        else:
            try:
                await migrate_chat(group_id, error.migrate_to_chat_id)
            except Exception:
                # The migration is a side task; still report the real error.
                logger.exception("Failed to migrate chat %s -> %s", group_id, error.migrate_to_chat_id)
        raise error from None

    where = group_id if group_id is not None else "idk"
    if isinstance(error, TelegramRetryAfter):
        body = f"<code>{error.__class__.__name__} @ {where}</code>:\n<pre>{str(error)}</pre>"
    else:
        body = "<pre>" + "".join(traceback.format_exception(error)) + f"@ {where}</pre>"

    try:
        send_admin_msg = await send_admin_group(body, parse_mode=ParseMode.HTML)
    except Exception:
        # send_admin_group already swallows its own failures; this is the
        # backstop so a reporting bug cannot mask the original error.
        logger.exception("Failed to report error to the admin group")
        send_admin_msg = None

    if message is not None and message.chat is not None:
        asyncio.create_task(
            awaitable_to_coroutine(message.reply(
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

            # pop, not del: the game loop may already have removed it.
            if GlobalState.games.pop(group_id, None) is not None:
                try:
                    await message.reply("Game ended forcibly.")
                except Exception:
                    # Best-effort notice: never let it mask the original error
                    pass

    # Re-raise so the dispatcher still surfaces the failure to its own
    # observer, preserving the original exception rather than a wrapper.
    raise error from None
