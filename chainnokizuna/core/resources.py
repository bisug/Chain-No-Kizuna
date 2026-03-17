import asyncio
import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional

import aiohttp
import motor.motor_asyncio
import redis.asyncio as redis
from aiogram import Bot, types
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties

from config import TOKEN, VP_TOKEN, MONGO_URI, REDIS_URL, DB_NAME

if TYPE_CHECKING:
    from chainnokizuna.models import ClassicGame


logger = logging.getLogger(__name__)


class GlobalState:
    """
    Holds globally accessible runtime states and bot user identities.
    """
    build_time = datetime.now(timezone.utc).replace(microsecond=0)
    maint_mode = False

    games: dict[int, "ClassicGame"] = {}  # group id -> game instance
    games_lock: asyncio.Lock = asyncio.Lock()

    # Bot identity info
    bot_user: Optional[types.User] = None
    vp_user: Optional[types.User] = None


bot = Bot(
    token=TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML,
        allow_sending_without_reply=True,
        link_preview_is_disabled=True,
    )
)
vp_bot: Optional[Bot] = Bot(
    token=VP_TOKEN,
    default=DefaultBotProperties(
        parse_mode=ParseMode.HTML,
        allow_sending_without_reply=True,
        link_preview_is_disabled=True,
    )
) if VP_TOKEN else None


# Initialized on startup
session: Optional[aiohttp.ClientSession] = None
mongo_client: Optional[motor.motor_asyncio.AsyncIOMotorClient] = None
vk: Optional[redis.Redis] = None


def get_session() -> aiohttp.ClientSession:
    """Returns the global aiohttp ClientSession."""
    if session is None:
        raise RuntimeError("session is not initialized!")
    return session


def get_db() -> motor.motor_asyncio.AsyncIOMotorDatabase:
    """Returns the MongoDB database instance."""
    if mongo_client is None:
        raise RuntimeError("mongo_client is not initialized!")
    return mongo_client[DB_NAME]


def get_vk() -> redis.Redis:
    """Returns the Redis client (ValKey/Redis)."""
    if vk is None:
        raise RuntimeError("redis client is not initialized!")
    return vk


async def init_resources() -> None:
    """
    Initializes global connections to MongoDB, Redis, and HTTP session.
    Also fetches bot identity information from Telegram.
    """
    global session, mongo_client, vk

    if session is not None:
        return

    session = aiohttp.ClientSession()

    # Fetch bot identity
    GlobalState.bot_user = await bot.get_me()
    if vp_bot:
        GlobalState.vp_user = await vp_bot.get_me()
        logger.info(f"Virtual player initialized: @{GlobalState.vp_user.username}")
    
    logger.info(f"Bot initialized: @{GlobalState.bot_user.username}")

    logger.info("Connecting to MongoDB...")
    mongo_client = motor.motor_asyncio.AsyncIOMotorClient(
        MONGO_URI,
        maxPoolSize=50,
        minPoolSize=10,
        retryWrites=True
    )
    # Check connection
    await mongo_client.admin.command('ping')
    await ensure_indexes()

    if REDIS_URL:
        logger.info("Connecting to Redis...")
        vk = redis.from_url(
            REDIS_URL,
            decode_responses=True,
            max_connections=20,
            socket_keepalive=True,
            retry_on_timeout=True
        )

async def ensure_indexes() -> None:
    """Ensure MongoDB indexes exist on startup."""
    db = get_db()
    
    logger.info("Initializing MongoDB indexes...")
    
    # Games collection
    await db.games.create_index([("group_id", 1)])
    await db.games.create_index([("start_time", -1)])
    await db.games.create_index([("participants.user_id", 1)])
    await db.games.create_index([("game_mode", 1)])
    
    # Players collection
    # _id is already indexed (user_id)
    await db.players.create_index([("word_count", -1)])
    await db.players.create_index([("letter_count", -1)])
    
    # Wordlist collection
    await db.wordlist.create_index([("word", 1)], unique=True)
    await db.wordlist.create_index([("accepted", 1)])
    
    logger.info("MongoDB indexes verified.")

async def close_resources() -> None:
    """Gracefully closes all open database and network connections."""
    global session, mongo_client, vk
    if session:
        await session.close()
    if mongo_client:
        mongo_client.close()
    if vk:
        await vk.aclose()
