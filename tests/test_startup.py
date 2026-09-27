"""Regression tests for resource cleanup on a failed startup, and for the
forward_origin replacement of the Bot API 7.0-deprecated forward_from field.
"""
import unittest
from unittest import mock

from aiogram import types

import chainnokizuna.core.resources as resources
from chainnokizuna.core.resources import GlobalState, close_resources
from tests.helpers import make_message, make_user, run


class TestCloseResourcesIsComplete(unittest.TestCase):
    """close_resources() must release the Bot sessions too, not just our own."""

    def test_closes_bot_sessions(self):
        with mock.patch.object(resources.bot.session, "close", new=mock.AsyncMock()) as main_close, \
             mock.patch.object(resources, "mongo_client", None), \
             mock.patch.object(resources, "vk", None):
            run(close_resources())
        main_close.assert_awaited_once()

    def test_closes_vp_bot_session_when_enabled(self):
        fake_main = mock.MagicMock()
        fake_main.session.close = mock.AsyncMock()
        fake_vp = mock.MagicMock()
        fake_vp.session.close = mock.AsyncMock()
        with mock.patch.object(resources, "bot", fake_main), \
             mock.patch.object(resources, "vp_bot", fake_vp), \
             mock.patch.object(resources, "mongo_client", None), \
             mock.patch.object(resources, "vk", None):
            run(close_resources())
        fake_main.session.close.assert_awaited_once()
        fake_vp.session.close.assert_awaited_once()

    def test_tolerates_no_resources_open(self):
        fake_bot = mock.MagicMock()
        fake_bot.session.close = mock.AsyncMock()
        with mock.patch.object(resources, "bot", fake_bot), \
             mock.patch.object(resources, "mongo_client", None), \
             mock.patch.object(resources, "vk", None), \
             mock.patch.object(resources, "vp_bot", None):
            run(close_resources())  # must not raise


class TestInitFailureRunsCleanup(unittest.TestCase):
    """main() called init_resources() before entering the try/finally."""

    def test_init_failure_triggers_close_and_reraises(self):
        import chainnokizuna.__main__ as entry

        with mock.patch.object(entry, "init_resources", new=mock.AsyncMock(
                side_effect=RuntimeError("bad token"))), \
             mock.patch.object(entry, "close_resources", new=mock.AsyncMock()) as closer:
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


class TestGlobalStateIsCleanBetweenTests(unittest.TestCase):
    def tearDown(self):
        GlobalState.games.clear()


if __name__ == "__main__":
    unittest.main()
