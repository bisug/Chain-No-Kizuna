"""Regression tests for error_handler tolerating an unavailable admin group.

Before the fix, send_admin_group() returning None was dereferenced, so the
handler raised AttributeError before it could mark the game KILLGAME, and the
original error was masked.
"""
import unittest
from unittest import mock

from aiogram import types

import chainnokizuna.handlers.errors as errors
from chainnokizuna.core.resources import GlobalState
from chainnokizuna.models.game.classic import ClassicGame
from tests.helpers import make_message, run


class TestErrorHandlerWithoutAdminGroup(unittest.TestCase):
    GROUP_ID = -1009876543210

    def setUp(self):
        GlobalState.games.clear()

    def tearDown(self):
        GlobalState.games.clear()

    def _event(self):
        msg = make_message(text="boom", chat_id=self.GROUP_ID)
        return types.ErrorEvent(
            update=types.Update(update_id=1, message=msg),
            exception=RuntimeError("original failure"),
        )

    def test_kills_game_when_admin_group_disabled(self):
        game = ClassicGame(self.GROUP_ID)
        game.state = 1
        GlobalState.games[self.GROUP_ID] = game

        with mock.patch.object(errors, "send_admin_group", new=mock.AsyncMock(return_value=None)):
            with self.assertRaises(RuntimeError) as ctx:
                run(errors.error_handler(self._event()))

        self.assertEqual(str(ctx.exception), "original failure")
        self.assertEqual(game.state, -1, "game should be marked KILLGAME")
        self.assertNotIn(self.GROUP_ID, GlobalState.games, "game should be removed")

    def test_kills_game_when_admin_group_has_no_permission(self):
        game = ClassicGame(self.GROUP_ID)
        game.state = 1
        GlobalState.games[self.GROUP_ID] = game

        with mock.patch.object(errors, "send_admin_group", new=mock.AsyncMock(return_value=None)):
            with self.assertRaises(RuntimeError):
                run(errors.error_handler(self._event()))
        self.assertEqual(game.state, -1)

    def test_original_error_is_never_masked(self):
        with mock.patch.object(errors, "send_admin_group", new=mock.AsyncMock(return_value=None)):
            with self.assertRaises(RuntimeError) as ctx:
                run(errors.error_handler(self._event()))
        self.assertNotIsInstance(ctx.exception, AttributeError)

    def test_handles_error_when_no_game_is_running(self):
        with mock.patch.object(errors, "send_admin_group", new=mock.AsyncMock(return_value=None)):
            with self.assertRaises(RuntimeError):
                run(errors.error_handler(self._event()))

    def test_uses_admin_message_when_available(self):
        game = ClassicGame(self.GROUP_ID)
        game.state = 1
        GlobalState.games[self.GROUP_ID] = game
        admin_msg = mock.MagicMock()
        admin_msg.reply = mock.AsyncMock()

        with mock.patch.object(errors, "send_admin_group", new=mock.AsyncMock(return_value=admin_msg)):
            with self.assertRaises(RuntimeError):
                run(errors.error_handler(self._event()))

        self.assertEqual(game.state, -1)
        # The follow-up "Killing game" note is scheduled on the admin message.
        admin_msg.reply.assert_called_once()


if __name__ == "__main__":
    unittest.main()
