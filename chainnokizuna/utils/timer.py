import asyncio

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
            self._start_time = self.loop.time()
            self._ticks_yielded = 0

        await asyncio.sleep(1)

        now = self.loop.time()
        elapsed = now - self._start_time
        target_ticks = int(elapsed)

        delta = target_ticks - self._ticks_yielded

        # Ensure we always yield at least 1 tick to prevent infinite fast loops
        # and ensure game progress, but don't let it drift too far ahead.
        if delta < 1:
            delta = 1

        self._ticks_yielded += delta
        return delta
