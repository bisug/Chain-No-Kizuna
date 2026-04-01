"""
Main entry point for the Word Chain bot.
Handles initialization, leader election (multiple instance support), and polling.
"""
import asyncio
import logging
import random
import time
import uuid

from aiogram.exceptions import TelegramConflictError

from chainnokizuna import dp
from chainnokizuna.services.leader import LeaderElection
from chainnokizuna.core.resources import init_resources, close_resources, bot, vp_bot, GlobalState
from chainnokizuna.db.redis import load_all_games
from config import GameState

random.seed(time.time())
logger = logging.getLogger(__name__)


async def main() -> None:
    """
    Orchestrates bot startup:
    1. Initializes shared resources (DB, Redis, HTTP session).
    2. Enters a leader election loop to ensure only one master instance polls Telegram.
    3. Restores active games from persistence on takeover.
    """
    await init_resources()

    active_bots = [bot]
    if vp_bot:
        active_bots.append(vp_bot)

    instance_id = str(uuid.uuid4())
    leader_election = LeaderElection(bot_id=instance_id)

    logger.info(f"Starting bot instance {instance_id}")

    try:
        while True:
            if await leader_election.acquire():
                logger.info(f"Instance {instance_id} is now LEADER. Starting polling...")
                game_tasks = []

                try:
                    await asyncio.gather(*(b.delete_webhook(drop_pending_updates=True) for b in active_bots))

                    # --- State restoration ---
                    try:
                        restored_games = await load_all_games()
                        GlobalState.games.clear()
                        for game in restored_games:
                            if game.state == GameState.RUNNING:
                                GlobalState.games[game.group_id] = game
                                game_tasks.append(asyncio.create_task(game.resume_loop()))

                        if restored_games:
                            logger.info(f"Restored {len(restored_games)} active game(s).")
                    except Exception as e:
                        logger.error(f"State restoration failed: {e}")

                    await dp.start_polling(*active_bots, drop_pending_updates=True)
                    logger.info("Polling finished. Exiting...")
                    break

                except TelegramConflictError:
                    logger.warning("Conflict: terminated by another instance. Retrying...")
                except Exception as e:
                    logger.error(f"Polling crashed: {e}")
                finally:
                    logger.info("Releasing leadership...")
                    for task in game_tasks:
                        task.cancel()
                    if game_tasks:
                        await asyncio.gather(*game_tasks, return_exceptions=True)

                    GlobalState.games.clear()
                    await leader_election.release()
            else:
                logger.debug(f"Standby - {instance_id}")
                await asyncio.sleep(5)

    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot interrupted.")
    finally:
        await leader_election.release()
        await close_resources()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
