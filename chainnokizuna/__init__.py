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
    while True:
        try:
            await Words.update()
        except Exception as e:
            logger.error(f"Error in background task loop: {e}")
        await asyncio.sleep(60 * 60)  # Run every hour


@dp.startup()
async def startup():
    """Bot initialization hook: starts resources and background workers."""
    await init_resources()
    asyncio.create_task(background_task_loop())
    try:
        await send_admin_group("Bot starting.")
    except Exception as e:
        logger.error(f"Startup notification failed: {e}")


@dp.shutdown()
async def shutdown():
    """Bot shutdown hook: gracefully closes all shared resources."""
    await close_resources()
    try:
        await send_admin_group("Bot stopping.")
    except Exception as e:
        logger.error(f"Shutdown notification failed: {e}")
