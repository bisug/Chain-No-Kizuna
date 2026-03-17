import asyncio
from typing import AsyncIterator

class GameTimer:
    """
    An async iterator that yields the number of seconds (delta) elapsed since the last tick.
    It corrects for drift by using the event loop time.
    """
    def __init__(self) -> None:
        self.loop = None

    def __aiter__(self) -> "GameTimer":
        return self

    async def __anext__(self) -> int:
        if self.loop is None:
            self.loop = asyncio.get_running_loop()
            self._last_tick = self.loop.time()
        
        await asyncio.sleep(1)
        now = self.loop.time()
        delta = int(now - self._last_tick)
        
        if delta < 1:
            delta = 1 # Fallback to prevent infinite fast loop if sleep wakes up slightly early
            
        self._last_tick = now
        return delta
