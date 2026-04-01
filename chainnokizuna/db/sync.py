import logging
from chainnokizuna.core.resources import get_db
from chainnokizuna.db.redis import GLOBAL_STATS_KEY, LEADERBOARD_KEY, _get_redis

logger = logging.getLogger(__name__)

async def sync_logic():
    """Core synchronization logic to align Redis stats and leaderboards with MongoDB."""
    db = get_db()
    redis_client = _get_redis()

    if redis_client is None:
        logger.error("Redis not available. Aborting sync.")
        return

    logger.info("Syncing Global Stats from MongoDB...")
    # 1. Aggregate Global Stats
    try:
        game_cnt = await db.games.count_documents({})
        pipeline = [
            {"$group": {
                "_id": None,
                "word_cnt": {"$sum": "$word_count"},
                "letter_cnt": {"$sum": "$letter_count"}
            }}
        ]
        agg_res = await db.players.aggregate(pipeline).to_list(length=1)
        word_cnt = agg_res[0]["word_cnt"] if agg_res else 0
        letter_cnt = agg_res[0]["letter_cnt"] if agg_res else 0

        # Update Redis Hash
        async with redis_client.pipeline(transaction=True) as pipe:
            pipe.hset(GLOBAL_STATS_KEY, "total_games", game_cnt)
            pipe.hset(GLOBAL_STATS_KEY, "total_words", word_cnt)
            pipe.hset(GLOBAL_STATS_KEY, "total_letters", letter_cnt)
            await pipe.execute()

        logger.info(f"Global Stats Synced: {game_cnt} games, {word_cnt} words, {letter_cnt} letters.")
    except Exception as e:
        logger.error(f"Failed to sync global stats: {e}")

    logger.info("Syncing Guess the Word Leaderboard from MongoDB...")
    # 2. Sync Leaderboard
    try:
        cursor = db.players.find({"guess_word_wins": {"$gt": 0}}, {"_id": 1, "guess_word_wins": 1})

        count = 0
        async with redis_client.pipeline(transaction=False) as pipe:
            async for player in cursor:
                pipe.zadd(LEADERBOARD_KEY, {str(player["_id"]): player["guess_word_wins"]})
                count += 1
                if count % 1000 == 0:
                    await pipe.execute()
            await pipe.execute()

        logger.info(f"Leaderboard Synced: {count} players indexed.")
    except Exception as e:
        logger.error(f"Failed to sync leaderboard: {e}")
