import asyncio
import logging
import os
import sys

# Add project root to sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from chainnokizuna.core.resources import init_resources, close_resources
from chainnokizuna.db.sync import sync_logic

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def sync_all():
    """Standalone sync entry point with resource management."""
    logger.info("Initializing resources for sync...")
    await init_resources()
    try:
        await sync_logic()
    finally:
        logger.info("Synchronization Complete.")
        await close_resources()

if __name__ == "__main__":
    asyncio.run(sync_all())
