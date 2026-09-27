"""Game state persistence via Redis.

Saves active game state to Redis so games can survive bot restarts.
"""

import orjson
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from chainnokizuna.models.game.classic import ClassicGame

logger = logging.getLogger(__name__)

# Redis key constants
ACTIVE_GAMES_KEY = "active_games"
GAME_KEY_PREFIX = "game:"


def _get_redis():
    """Lazy import to avoid circular dependencies."""
    from chainnokizuna.core.resources import vk
    return vk


def _get_game_class(type_name: str):
    """Resolve a game class from its type name string."""
    from chainnokizuna.models.game import (
        ClassicGame, HardModeGame, ChaosGame, ChosenFirstLetterGame,
        RandomFirstLetterGame, BannedLettersGame, RequiredLetterGame,
        EliminationGame, MixedEliminationGame, GuessTheWordGame,
    )
    mapping = {
        "ClassicGame": ClassicGame,
        "HardModeGame": HardModeGame,
        "ChaosGame": ChaosGame,
        "ChosenFirstLetterGame": ChosenFirstLetterGame,
        "RandomFirstLetterGame": RandomFirstLetterGame,
        "BannedLettersGame": BannedLettersGame,
        "RequiredLetterGame": RequiredLetterGame,
        "EliminationGame": EliminationGame,
        "MixedEliminationGame": MixedEliminationGame,
        "GuessTheWordGame": GuessTheWordGame,
    }
    return mapping.get(type_name)


async def save_game(game: "ClassicGame") -> None:
    """Save a game's state to Redis.

    This is the per-turn hot path, so it deliberately does not touch the
    active-games set: SADD is idempotent, and repeating it every turn cost an
    extra command in the round trip for no effect. Membership is claimed once
    by register_active_game() and dropped by remove_game().
    """
    import asyncio

    redis_client = _get_redis()
    if redis_client is None:
        return  # No Redis available, silently skip

    key = f"{GAME_KEY_PREFIX}{game.group_id}"
    data = orjson.dumps(game.to_dict()).decode()

    for attempt in range(3):
        try:
            # Add 24-hour TTL (86400 seconds) as a safety net
            # If the bot crashes and never calls remove_game, the state will expire.
            # A single command needs no pipeline, and no MULTI/EXEC wrapper.
            await redis_client.set(key, data, ex=86400)
            return
        except Exception as e:
            if attempt == 2:
                logger.error(f"Failed to save game state for {game.group_id} after 3 attempts: {e}")
            else:
                await asyncio.sleep(0.1 * (attempt + 1))


async def register_active_game(group_id: int) -> None:
    """Add a group to the active-games set if it is not already a member."""
    redis_client = _get_redis()
    if redis_client is None:
        return
    try:
        await redis_client.sadd(ACTIVE_GAMES_KEY, str(group_id))
    except Exception as e:
        logger.error(f"Failed to register active game {group_id}: {e}")



async def remove_game(group_id: int) -> None:
    """Remove a game's state from Redis when it ends."""
    import asyncio
    
    redis_client = _get_redis()
    if redis_client is None:
        return

    key = f"{GAME_KEY_PREFIX}{group_id}"
    
    for attempt in range(3):
        try:
            await redis_client.delete(key)
            await redis_client.srem(ACTIVE_GAMES_KEY, str(group_id))
            return
        except Exception as e:
            if attempt == 2:
                logger.error(f"Failed to remove game state for {group_id} after 3 attempts: {e}")
            else:
                await asyncio.sleep(0.1 * (attempt + 1))



async def load_all_games() -> list["ClassicGame"]:
    """Load all saved game states from Redis and reconstruct game objects.

    The active-games set is the index of which groups to load, but it can fall
    out of step with the actual keys: a game key that outlives its membership
    would never be restored. So also SCAN for game:* keys and take the union,
    which makes restore self-healing instead of depending on the index being
    exactly right.
    """
    redis_client = _get_redis()
    if redis_client is None:
        return []

    games = []
    try:
        group_ids = set(await redis_client.smembers(ACTIVE_GAMES_KEY))

        # Catch game keys the set does not list. SCAN rather than KEYS so this
        # does not block the server, and it only runs once at startup.
        async for key in redis_client.scan_iter(match=f"{GAME_KEY_PREFIX}*", count=200):
            gid = key[len(GAME_KEY_PREFIX):]
            if gid:
                group_ids.add(gid)

        if not group_ids:
            return []

        # Use pipelining to fetch all game states in one round-trip
        async with redis_client.pipeline(transaction=False) as pipe:
            for gid in group_ids:
                pipe.get(f"{GAME_KEY_PREFIX}{gid}")
            raw_states = await pipe.execute()

        for gid, raw in zip(group_ids, raw_states):
            if not raw:
                # Key is gone (TTL expiry or explicit removal); drop the index
                # entry so the set does not grow without bound.
                await redis_client.srem(ACTIVE_GAMES_KEY, gid)
                continue

            data = orjson.loads(raw)
            type_name = data.get("type", "ClassicGame")
            game_cls = _get_game_class(type_name)
            if game_cls is None:
                logger.warning(f"Unknown game type '{type_name}' for group {gid}, skipping.")
                await redis_client.srem(ACTIVE_GAMES_KEY, gid)
                continue

            game = game_cls.from_dict(data)
            games.append(game)
            # Re-assert membership so the index converges on the real keys.
            await redis_client.sadd(ACTIVE_GAMES_KEY, gid)
            logger.info(f"Restored {type_name} for group {gid} ({len(game.players)} players)")

    except Exception as e:
        logger.error(f"Failed to load game states: {e}")

    return games
