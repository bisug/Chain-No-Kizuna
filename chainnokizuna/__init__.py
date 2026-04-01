"""
Core initialization for the Chain No Kizuna bot.
Sets up logging, configures the dispatcher with handlers, and defines bot-level lifecycle events.
"""
import logging
import asyncio

from aiogram import Dispatcher

from chainnokizuna.core.resources import init_resources, close_resources
from chainnokizuna.utils.telegram import send_admin_group
from chainnokizuna.services.words import Words

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
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


async def sync_task_loop():
    """Periodic task loop to sync Redis with MongoDB (every 24 hours)."""
    while True:
        # 1-hour initial delay for safety, then 24 hours
        await asyncio.sleep(3600)

        from chainnokizuna.services.leader import LeaderElection
        if await LeaderElection.is_leader():
            try:
                from chainnokizuna.db.sync import sync_logic
                logger.info("Starting periodic database sync (Redis <-> MongoDB)...")
                await sync_logic()
            except Exception as e:
                logger.error(f"Periodic sync failed: {e}")

        await asyncio.sleep(24 * 3600 - 3600)


@dp.startup()
async def startup():
    """Bot initialization hook: starts resources and background workers."""
    await init_resources()

    # Ensure word list is loaded BEFORE bot starts accepting messages
    try:
        await Words.update()
    except Exception as e:
        logger.error(f"Initial word dictionary update failed: {e}")

    asyncio.create_task(background_task_loop())
    asyncio.create_task(sync_task_loop())
    try:
        bot_name = GlobalState.bot_user.full_name if GlobalState.bot_user else "Bot"
        await send_admin_group(f"{bot_name} starting.")
    except Exception as e:
        logger.error(f"Startup notification failed: {e}")


@dp.shutdown()
async def shutdown():
    """Bot shutdown hook: gracefully closes all shared resources."""
    await close_resources()
    try:
        bot_name = GlobalState.bot_user.full_name if GlobalState.bot_user else "Bot"
        await send_admin_group(f"{bot_name} stopping.")
    except Exception as e:
        logger.error(f"Shutdown notification failed: {e}")
