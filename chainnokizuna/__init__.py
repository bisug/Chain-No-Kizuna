"""
Core initialization for the Chain No Kizuna bot.
Sets up logging, configures the dispatcher with handlers, and defines bot-level lifecycle events.
"""
import logging
import asyncio

from aiogram import Dispatcher

from chainnokizuna.core.resources import init_resources, close_resources, GlobalState, bot
from chainnokizuna.utils.telegram import send_admin_group
from chainnokizuna.services.words import Words
from chainnokizuna.utils.commands import set_bot_commands
from config import LOG_LEVEL

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=LOG_LEVEL
)

logger = logging.getLogger(__name__)


from chainnokizuna.handlers import routers
from chainnokizuna.handlers.errors import error_handler

dp = Dispatcher()
dp.include_routers(*routers)
dp.error.register(error_handler)


async def background_task_loop():
    """Periodic task loop for updating the word dictionary (default: 1 hour)."""
    # First update is now handled by startup()
    while True:
        await asyncio.sleep(60 * 60)  # Sleep first, run periodically
        try:
            await Words.update()
        except Exception as e:
            logger.error(f"Error in background task loop: {e}")


@dp.startup()
async def startup():
    """Bot initialization hook: starts resources and background workers."""
    await init_resources()

    # Command registration is cosmetic: if Telegram refuses it, the bot should
    # still serve messages rather than fail the whole startup hook.
    try:
        await set_bot_commands(bot)
    except Exception as e:
        logger.error(f"Failed to publish the command list: {e}")

    # Ensure word list is loaded BEFORE bot starts accepting messages
    try:
        await Words.update()
    except Exception as e:
        logger.error(f"Initial word dictionary update failed: {e}")
        # Non-fatal: start_game() refuses the dictionary-backed modes while
        # Words.count is 0 rather than crashing the handler.
        await send_admin_group("⚠️ Started with an empty word list; word-chain "
                               "modes are disabled until the next refresh.")

    # Held so shutdown can cancel it; an unreferenced task can be collected.
    _background_task = asyncio.create_task(background_task_loop())

    try:
        bot_name = GlobalState.bot_user.full_name if GlobalState.bot_user else "Bot"
        await send_admin_group(f"{bot_name} starting.")
    except Exception as e:
        logger.error(f"Startup notification failed: {e}")


@dp.shutdown()
async def shutdown():
    """Bot shutdown hook: gracefully closes all shared resources."""
    task = globals().get("_background_task")
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Background task failed during shutdown: {e}")

    # Notify before closing, since send_admin_group needs the bot session.
    try:
        bot_name = GlobalState.bot_user.full_name if GlobalState.bot_user else "Bot"
        await send_admin_group(f"{bot_name} stopping.")
    except Exception as e:
        logger.error(f"Shutdown notification failed: {e}")

    await close_resources()
