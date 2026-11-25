"""Regression tests for reconnecting after a lost polling session."""

import unittest
from unittest import mock

from aiogram.exceptions import TelegramConflictError

import chainnokizuna.__main__ as entry
from chainnokizuna.core import resources
from tests.helpers import run


class _StopLoop(Exception):
    """Raised from the second acquire() to end the infinite leader loop."""


def _conflict():
    return TelegramConflictError("deleteWebhook", "conflict")


def _leader(second_acquire=None):
    leader = mock.MagicMock()
    leader.acquire = mock.AsyncMock(side_effect=[True, second_acquire or _StopLoop()])
    leader.release = mock.AsyncMock()
    return leader


class TestCloseResourcesClearsGlobals(unittest.TestCase):
    def test_close_clears_mongo_and_redis_globals(self):
        fake_mongo = mock.MagicMock()
        fake_vk = mock.MagicMock()
        fake_vk.aclose = mock.AsyncMock()
        fake_bot = mock.MagicMock()
        fake_bot.session.close = mock.AsyncMock()
        with (
            mock.patch.object(resources, "bot", fake_bot),
            mock.patch.object(resources, "vp_bot", None),
            mock.patch.object(resources, "mongo_client", fake_mongo),
            mock.patch.object(resources, "vk", fake_vk),
        ):
            run(resources.close_resources())
            self.assertIsNone(resources.mongo_client)
            self.assertIsNone(resources.vk)
            with self.assertRaises(RuntimeError):
                resources.get_db()
            with self.assertRaises(RuntimeError):
                resources.get_vk()


class TestPollingRetryReconnects(unittest.TestCase):
    def test_conflict_retry_sees_cleared_globals_then_reinitializes(self):
        seen_at_init = []

        async def tracked_init():
            # Records the real global state at call time. A live init_resources()
            # returns early when mongo_client is still set, so a stale client here
            # means the retry would reuse a closed connection.
            seen_at_init.append(resources.mongo_client)
            resources.mongo_client = mock.MagicMock()

        fake_bot = mock.MagicMock()
        fake_bot.delete_webhook = mock.AsyncMock()
        fake_bot.session.close = mock.AsyncMock()

        with (
            mock.patch.object(entry, "init_resources", new=tracked_init),
            # close_resources is deliberately real: globals must actually clear.
            mock.patch.object(resources, "bot", fake_bot),
            mock.patch.object(resources, "vp_bot", None),
            mock.patch.object(resources, "mongo_client", None),
            mock.patch.object(resources, "vk", None),
            mock.patch.object(entry, "bot", fake_bot),
            mock.patch.object(entry, "vp_bot", None),
            mock.patch.object(entry, "LeaderElection", return_value=_leader()),
            mock.patch.object(
                entry, "load_all_games", new=mock.AsyncMock(return_value=[])
            ),
            mock.patch.object(
                entry.dp, "start_polling", new=mock.AsyncMock(side_effect=_conflict())
            ),
            self.assertRaises(_StopLoop),
        ):
            run(entry.main())

        self.assertGreaterEqual(len(seen_at_init), 2)
        self.assertIsNone(seen_at_init[-1])


class TestGenericFailureBackoff(unittest.TestCase):
    def test_generic_poll_failure_pauses_before_retry(self):
        fake_bot = mock.MagicMock()
        fake_bot.delete_webhook = mock.AsyncMock()
        sleep = mock.AsyncMock()

        with (
            mock.patch.object(entry, "init_resources", new=mock.AsyncMock()),
            mock.patch.object(entry, "close_resources", new=mock.AsyncMock()),
            mock.patch.object(entry, "bot", fake_bot),
            mock.patch.object(entry, "vp_bot", None),
            mock.patch.object(entry, "LeaderElection", return_value=_leader()),
            mock.patch.object(
                entry, "load_all_games", new=mock.AsyncMock(return_value=[])
            ),
            mock.patch.object(
                entry.dp,
                "start_polling",
                new=mock.AsyncMock(side_effect=RuntimeError("boom")),
            ),
            mock.patch.object(entry.asyncio, "sleep", new=sleep),
            self.assertRaises(_StopLoop),
        ):
            run(entry.main())

        sleep.assert_awaited_once_with(5)


if __name__ == "__main__":
    unittest.main()
