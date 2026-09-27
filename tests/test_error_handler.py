"""Regression tests for error_handler tolerating an unavailable admin group.

Before the fix, send_admin_group() returning None was dereferenced, so the
handler raised AttributeError before it could mark the game KILLGAME, and the
original error was masked.
"""
import unittest
from unittest import mock

from aiogram import types

import chainnokizuna.handlers.errors as errors
import chainnokizuna.db.redis as redis_mod
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


class TestMigrateChatReKeysPersistedState(unittest.TestCase):
    """Telegram issues a new id when a group is upgraded to a supergroup.

    migrate_chat moved the in-memory game but left the saved copy under the
    old id, so a restart restored the game against a chat the bot can no
    longer reach while the new chat looked like it had no game at all.
    """

    OLD_ID = -1001111111
    NEW_ID = -2002222222

    def setUp(self):
        GlobalState.games.clear()

    def tearDown(self):
        GlobalState.games.clear()

    def _game(self, group_id):
        from chainnokizuna.models.player import Player
        from tests.helpers import make_user

        game = ClassicGame(group_id)
        game.state = 1
        game.players = [Player(make_user(user_id=5, name="Ada"))]
        return game

    def test_move_is_applied_to_persistence_as_well_as_memory(self):
        game = self._game(self.OLD_ID)
        GlobalState.games[self.OLD_ID] = game

        moved = mock.AsyncMock()
        saved = mock.AsyncMock()
        db = mock.MagicMock()
        db.games.update_many = mock.AsyncMock()

        async def scenario():
            # migrate_chat imports these inside the function to avoid a cycle
            # through models.game, so patch the defining module, not errors.
            with mock.patch.object(redis_mod, "move_saved_game", new=moved), \
                 mock.patch.object(redis_mod, "save_game", new=saved), \
                 mock.patch.object(errors, "get_db", return_value=db), \
                 mock.patch.object(errors, "send_admin_group", new=mock.AsyncMock()):
                await errors.migrate_chat(self.OLD_ID, self.NEW_ID)

        run(scenario())

        self.assertNotIn(self.OLD_ID, GlobalState.games)
        self.assertIs(GlobalState.games[self.NEW_ID], game)
        self.assertEqual(game.group_id, self.NEW_ID)

        # The persisted copy must follow, or a restart resurrects the old id.
        moved.assert_awaited_once_with(self.OLD_ID, self.NEW_ID)
        saved.assert_awaited_once_with(game)
        db.games.update_many.assert_awaited_once()

    def test_no_game_means_no_persistence_work(self):
        moved = mock.AsyncMock()
        db = mock.MagicMock()
        db.games.update_many = mock.AsyncMock()

        async def scenario():
            with mock.patch.object(redis_mod, "move_saved_game", new=moved), \
                 mock.patch.object(redis_mod, "save_game", new=mock.AsyncMock()), \
                 mock.patch.object(errors, "get_db", return_value=db), \
                 mock.patch.object(errors, "send_admin_group", new=mock.AsyncMock()):
                await errors.migrate_chat(self.OLD_ID, self.NEW_ID)

        run(scenario())
        moved.assert_not_awaited()
        # Historical records are still re-pointed.
        db.games.update_many.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
