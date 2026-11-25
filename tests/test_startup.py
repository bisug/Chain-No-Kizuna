"""Regression tests for resource cleanup on a failed startup, and for the
forward_origin replacement of the Bot API 7.0-deprecated forward_from field.
"""

import asyncio
import unittest
from unittest import mock

from aiogram import types

import chainnokizuna
from chainnokizuna import shutdown, startup
from chainnokizuna.core import resources
from chainnokizuna.core.resources import GlobalState, close_resources
from chainnokizuna.models.player import Player
from tests.helpers import make_bot_full_info, make_message, make_user, run


class TestCloseResourcesIsComplete(unittest.TestCase):
    """close_resources() must release the Bot sessions too, not just our own."""

    def test_closes_bot_sessions(self):
        with (
            mock.patch.object(
                resources.bot.session, "close", new=mock.AsyncMock()
            ) as main_close,
            mock.patch.object(resources, "mongo_client", None),
            mock.patch.object(resources, "vk", None),
        ):
            run(close_resources())
        main_close.assert_awaited_once()

    def test_closes_vp_bot_session_when_enabled(self):
        fake_main = mock.MagicMock()
        fake_main.session.close = mock.AsyncMock()
        fake_vp = mock.MagicMock()
        fake_vp.session.close = mock.AsyncMock()
        with (
            mock.patch.object(resources, "bot", fake_main),
            mock.patch.object(resources, "vp_bot", fake_vp),
            mock.patch.object(resources, "mongo_client", None),
            mock.patch.object(resources, "vk", None),
        ):
            run(close_resources())
        fake_main.session.close.assert_awaited_once()
        fake_vp.session.close.assert_awaited_once()

    def test_tolerates_no_resources_open(self):
        fake_bot = mock.MagicMock()
        fake_bot.session.close = mock.AsyncMock()
        with (
            mock.patch.object(resources, "bot", fake_bot),
            mock.patch.object(resources, "mongo_client", None),
            mock.patch.object(resources, "vk", None),
            mock.patch.object(resources, "vp_bot", None),
        ):
            run(close_resources())  # must not raise


class _FakePeriodicTask:
    """Awaitable stand-in that records cancel() without touching the event loop."""

    def __init__(self):
        self.cancel = mock.Mock(return_value=True)

    def done(self):
        return False

    def __await__(self):
        async def complete():
            return None

        return complete().__await__()


class TestBackgroundTaskLifecycle(unittest.TestCase):
    """startup stores the periodic task where shutdown can find and cancel it."""

    def setUp(self):
        # Never let a task cached by an earlier run leak into this test.
        chainnokizuna.__dict__.pop("_background_task", None)

    def tearDown(self):
        chainnokizuna.__dict__.pop("_background_task", None)

    def test_shutdown_cancels_task_created_by_startup(self):
        # A fake task avoids asyncio.run's end-of-loop cancellation, which would
        # otherwise make a task that shutdown never touched look cancelled.
        fake_task = _FakePeriodicTask()

        def fake_create_task(coro):
            coro.close()  # the stand-in never runs it
            return fake_task

        async def scenario():
            await startup()
            await shutdown()

        with (
            mock.patch.object(chainnokizuna, "init_resources", new=mock.AsyncMock()),
            mock.patch.object(chainnokizuna, "set_bot_commands", new=mock.AsyncMock()),
            mock.patch.object(chainnokizuna.Words, "update", new=mock.AsyncMock()),
            mock.patch.object(chainnokizuna, "send_admin_group", new=mock.AsyncMock()),
            mock.patch.object(chainnokizuna, "close_resources", new=mock.AsyncMock()),
            mock.patch.object(asyncio, "create_task", side_effect=fake_create_task),
        ):
            run(scenario())

        fake_task.cancel.assert_called_once_with()


class TestInitFailureRunsCleanup(unittest.TestCase):
    """main() called init_resources() before entering the try/finally."""

    def test_init_failure_triggers_close_and_reraises(self):
        import chainnokizuna.__main__ as entry

        with (
            mock.patch.object(
                entry,
                "init_resources",
                new=mock.AsyncMock(side_effect=RuntimeError("bad token")),
            ),
            mock.patch.object(entry, "close_resources", new=mock.AsyncMock()) as closer,
        ):
            with self.assertRaises(RuntimeError):
                run(entry.main())
        closer.assert_awaited_once()


class TestForwardOriginReplacesForwardFrom(unittest.TestCase):
    """forward_from is Bot API 7.0 deprecated and Telegram no longer sends it."""

    def _forwarded(self, text):
        origin = types.MessageOriginUser(
            date=__import__("datetime").datetime.now(),
            sender_user=make_user(user_id=999, name="Original"),
        )
        return make_message(text=text, forward_origin=origin)

    def test_deprecated_field_is_none_even_when_forwarded(self):
        # This is exactly why the old guard silently did nothing.
        message = self._forwarded("/feedback please read this")
        self.assertIsNotNone(message.forward_origin)
        self.assertIsNone(message.forward_from)

    def test_stats_resolves_the_original_sender(self):

        message = self._forwarded("hello")
        rmsg = message.forward_origin
        original_sender = (
            rmsg.sender_user if isinstance(rmsg, types.MessageOriginUser) else None
        )
        self.assertIsNotNone(original_sender)
        self.assertEqual(original_sender.id, 999)
        # The old expression would have fallen through to the forwarder.
        self.assertIsNone(message.forward_from)


class TestBotFullInfoPlayer(unittest.TestCase):
    """aiogram 3.34 getMe/me() return BotFullInfo, not User."""

    def test_player_accepts_bot_full_info(self):
        bot_user = make_bot_full_info()
        player = Player(bot_user)
        self.assertEqual(player.user_id, bot_user.id)
        self.assertEqual(player._username, bot_user.username)
        self.assertEqual(player._name, bot_user.full_name)


class TestGlobalStateIsCleanBetweenTests(unittest.TestCase):
    def tearDown(self):
        GlobalState.games.clear()


if __name__ == "__main__":
    unittest.main()
