"""Game state persistence via Redis.

Saves active game state to Redis so games can survive bot restarts.
"""

import orjson
import logging
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from chainnokizuna.models.game.classic import ClassicGame

logger = logging.getLogger(__name__)

# Redis key constants
ACTIVE_GAMES_KEY = "active_games"
GAME_KEY_PREFIX = "game:"
GLOBAL_STATS_KEY = "stats:global"
LEADERBOARD_KEY = "leaderboard:guess_the_word"


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
    """Save a game's state to Redis."""
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
            async with redis_client.pipeline(transaction=True) as pipe:
                pipe.set(key, data, ex=86400)
                pipe.sadd(ACTIVE_GAMES_KEY, str(game.group_id))
                await pipe.execute()
            return
        except Exception as e:
            if attempt == 2:
                logger.error(f"Failed to save game state for {game.group_id} after 3 attempts: {e}")
            else:
                await asyncio.sleep(0.1 * (attempt + 1))



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
    """Load all saved game states from Redis and reconstruct game objects."""
    redis_client = _get_redis()
    if redis_client is None:
        return []

    games = []
    try:
        group_ids = await redis_client.smembers(ACTIVE_GAMES_KEY)
        if not group_ids:
            return []

        # Use pipelining to fetch all game states in one round-trip
        async with redis_client.pipeline(transaction=False) as pipe:
            for gid in group_ids:
                pipe.get(f"{GAME_KEY_PREFIX}{gid}")
            raw_states = await pipe.execute()

        for gid, raw in zip(group_ids, raw_states):
            if not raw:
                # Cleanup if key disappeared (unlikely but safe)
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
            logger.info(f"Restored {type_name} for group {gid} ({len(game.players)} players)")

    except Exception as e:
        logger.error(f"Failed to load game states: {e}")

    return games


async def incr_global_stats(words: int = 0, letters: int = 0, games: int = 0) -> None:
    """Increment global statistics counters in Redis."""
    redis_client = _get_redis()
    if redis_client is None:
        return

    try:
        async with redis_client.pipeline(transaction=True) as pipe:
            if words:
                pipe.hincrby(GLOBAL_STATS_KEY, "total_words", words)
            if letters:
                pipe.hincrby(GLOBAL_STATS_KEY, "total_letters", letters)
            if games:
                pipe.hincrby(GLOBAL_STATS_KEY, "total_games", games)
            await pipe.execute()
    except Exception as e:
        logger.error(f"Failed to increment global stats in Redis: {e}")


async def update_leaderboard(user_id: int, wins: int) -> None:
    """Update a player's win count in the Redis sorted set leaderboard."""
    redis_client = _get_redis()
    if redis_client is None:
        return

    try:
        # ZADD: Sets the score (wins) for the member (user_id)
        await redis_client.zadd(LEADERBOARD_KEY, {str(user_id): wins})
    except Exception as e:
        logger.error(f"Failed to update leaderboard in Redis: {e}")


async def get_redis_global_stats() -> Optional[dict]:
    """Retrieve global statistics counters from Redis."""
    redis_client = _get_redis()
    if redis_client is None:
        return None

    try:
        res = await redis_client.hgetall(GLOBAL_STATS_KEY)
        if not res:
            return None
        # Convert values to integers
        return {k: int(v) for k, v in res.items()}
    except Exception as e:
        logger.error(f"Failed to get global stats from Redis: {e}")
        return None


async def get_redis_leaderboard(skip: int, limit: int) -> list[tuple[str, int]]:
    """Retrieve a page of the leaderboard from Redis."""
    redis_client = _get_redis()
    if redis_client is None:
        return []

    try:
        # ZREVRANGE: Returns members in descending order of score
        res = await redis_client.zrevrange(
            LEADERBOARD_KEY, skip, skip + limit - 1, withscores=True
        )
        # Result is a list of (member, score) tuples. We convert score to int.
        return [(str(m), int(s)) for m, s in res]
    except Exception as e:
        logger.error(f"Failed to get leaderboard from Redis: {e}")
        return []


async def get_leaderboard_total_count() -> int:
    """Get the total number of players in the leaderboard."""
    redis_client = _get_redis()
    if redis_client is None:
        return 0
    try:
        return await redis_client.zcard(LEADERBOARD_KEY)
    except Exception:
        return 0
