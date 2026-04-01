import logging
import asyncio
from typing import Optional

from chainnokizuna.core.resources import get_vk

logger = logging.getLogger(__name__)

class LeaderElection:
    """
    Implements a simple leader election mechanism using Redis.
    Ensures only one bot instance is active (polling) at a time.
    """

    KEY = "bot:leader"
    TTL = 10  # expire key after 10 seconds
    RENEW_INTERVAL = 5  # renew leadership every 5 seconds

    def __init__(self, bot_id: str):
        self.bot_id = bot_id
        self._is_leader = False
        self._renew_task: Optional[asyncio.Task] = None

    @property
    def is_leader(self) -> bool:
        return self._is_leader

    async def acquire(self) -> bool:
        """Try to become the leader."""
        try:
            redis_client = get_vk()
            # SET NX EX: Set if Not Exists, Expire in TTL seconds
            success = await redis_client.set(self.KEY, self.bot_id, ex=self.TTL, nx=True)
            if success:
                self._is_leader = True
                self._start_renew_loop()
                logger.info(f"Leadership acquired by {self.bot_id}")
                return True
            return False
        except Exception as e:
            logger.error(f"Error acquiring leadership: {e}")
            return False

    async def release(self) -> None:
        """Release leadership."""
        self._is_leader = False
        if self._renew_task:
            self._renew_task.cancel()
            try:
                await self._renew_task
            except asyncio.CancelledError:
                pass
            self._renew_task = None

        try:
            redis_client = get_vk()
            script = """
            if redis.call("get", KEYS[1]) == ARGV[1] then
                return redis.call("del", KEYS[1])
            else
                return 0
            end
            """
            await redis_client.eval(script, 1, self.KEY, self.bot_id)
            logger.info(f"Leadership released by {self.bot_id}")
        except Exception as e:
            logger.error(f"Error releasing leadership: {e}")

    def _start_renew_loop(self):
        if self._renew_task:
            return
        self._renew_task = asyncio.create_task(self._renew_loop())

    async def _renew_loop(self):
        while self._is_leader:
            await asyncio.sleep(self.RENEW_INTERVAL)
            try:
                redis_client = get_vk()
                # Extend validity of the key
                # We use a Lua script or just SET again with XX (exist) to be safer,
                # but for simplicity, we just set it again since we are the leader.
                # Ideally check if value is still us, then extend.
                script = """
                if redis.call("get", KEYS[1]) == ARGV[1] then
                    return redis.call("expire", KEYS[1], ARGV[2])
                else
                    return 0
                end
                """
                result = await redis_client.eval(script, 1, self.KEY, self.bot_id, self.TTL)
                if not result:
                    logger.warning("Lost leadership during renewal!")
                    self._is_leader = False
                    break
            except Exception as e:
                logger.error(f"Error renewing leadership: {e}")
                # If we fail to renew, we might lose leadership eventually.
                # For now, just log.
