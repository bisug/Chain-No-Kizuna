"""Regression tests for GuessTheWordGame.handle_answer holding its lock.

Before the fix the state_lock block covered only the first half of the method.
The guess counter check, the answered flag, GlobalState.pop, remove_game and
save_game all ran unlocked, so a concurrent correct guess could end the game and
delete its Redis key, after which the losing player's save_game would resurrect
the finished game.
"""
import ast
import datetime
import pathlib
import unittest
from unittest import mock

from config import GameState
from chainnokizuna.models.game import guess_the_word as gtw_mod
from chainnokizuna.models.game.classic import ClassicGame
from chainnokizuna.models.game.guess_the_word import GuessTheWordGame
from chainnokizuna.models.player import Player
from tests.helpers import make_message, make_user, run

SOURCE = pathlib.Path(gtw_mod.__file__)


def _handle_answer_ast():
    tree = ast.parse(SOURCE.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "handle_answer":
            return node
    raise AssertionError("handle_answer not found")


class TestLockCoversWholeMethod(unittest.TestCase):
    """Structural: every statement in handle_answer must sit inside the lock."""

    def test_lock_is_the_only_top_level_block(self):
        fn = _handle_answer_ast()
        async_withs = [s for s in fn.body if isinstance(s, ast.AsyncWith)]
        self.assertEqual(len(async_withs), 1, "handle_answer should hold exactly one lock")
        lock = async_withs[0]
        self.assertIsInstance(lock.items[0].context_expr, ast.Attribute)
        self.assertEqual(lock.items[0].context_expr.attr, "state_lock")

    def test_lock_extends_to_the_last_statement(self):
        fn = _handle_answer_ast()
        lock = next(s for s in fn.body if isinstance(s, ast.AsyncWith))
        trailing = [s for s in fn.body if not isinstance(s, ast.AsyncWith) and s.lineno > lock.end_lineno]
        self.assertEqual(trailing, [], "no state mutation may run outside state_lock")

    def test_save_game_call_is_inside_the_lock(self):
        fn = _handle_answer_ast()
        lock = next(s for s in fn.body if isinstance(s, ast.AsyncWith))
        inside = "\n".join(
            line for line in SOURCE.read_text().splitlines()[lock.lineno - 1 : lock.end_lineno]
        )
        self.assertIn("save_game", inside)
        self.assertIn("remove_game", inside)


class TestDictionaryReloadFailureIsLogged(unittest.TestCase):
    """The lazy reload used to swallow the error, making a resumed game unplayable."""

    def _game(self):
        game = GuessTheWordGame(-1004242)
        game.state = 1
        game.target_word = "zebra"
        game.dictionary = []          # as left by from_dict after a restart
        game.max_guesses = 30
        game.guess_count = 0
        game.guess_history = []
        game.accepting_answers = True
        game.players = []
        game.players_in_game = []
        return game

    def test_reload_failure_is_logged_not_swallowed(self):
        game = self._game()
        message = make_message(text="apple")

        async def invoke():
            from aiogram import types

            # The guess is rejected once the dictionary stays empty, so the reply has
            # to be stubbed; the point of the test is the logged failure, not the reply.
            with mock.patch("aiofiles.open", side_effect=OSError("missing data file")), \
                 mock.patch.object(types.Message, "reply", new=mock.AsyncMock()):
                with self.assertLogs(gtw_mod.logger, level="ERROR") as captured:
                    await game.handle_answer(message)
                return captured.output

        output = run(invoke())
        self.assertTrue(
            any("dictionary" in line.lower() for line in output),
            f"expected a logged dictionary failure, got {output}",
        )


class TestUpdateDbParticipantSchema(unittest.TestCase):
    """Both modes must write the same participants shape.

    /groupstats and /stats aggregate on these fields, and the Guess the Word
    variant used to add a "name" field holding rendered HTML markup that
    nothing read.
    """

    def _participants(self, cls):
        import chainnokizuna.core.resources as resources_mod
        import chainnokizuna.models.game.classic as classic_mod

        captured = {}

        class _Coll:
            async def insert_one(self, doc):
                captured["game"] = doc

            async def bulk_write(self, ops):
                captured["ops"] = ops

        class _DB:
            games = _Coll()
            players = _Coll()

        game = cls(-1005551)
        player = Player(make_user(user_id=5, name="Ada"))
        player.word_count, player.letter_count, player.longest_word = 3, 12, "zebra"
        game.players = [player]
        game.players_in_game = [player]
        game.state = GameState.RUNNING
        game.start_time = datetime.datetime.now(datetime.timezone.utc)

        async def scenario():
            # classic.py imports get_db at module level, guess_the_word.py
            # imports it inside update_db, so both targets need patching.
            with mock.patch.object(classic_mod, "get_db", return_value=_DB()), \
                 mock.patch.object(resources_mod, "get_db", return_value=_DB()):
                await game.update_db()

        run(scenario())
        return captured["game"]["participants"][0]

    def test_both_modes_write_identical_participant_keys(self):
        classic = self._participants(ClassicGame)
        guess = self._participants(GuessTheWordGame)
        self.assertEqual(sorted(classic), sorted(guess))
        self.assertEqual(sorted(guess), [
            "full_name", "letter_count", "longest_word", "user_id", "username",
            "won", "word_count",
        ])

    def test_participant_document_contains_no_markup(self):
        """Storing rendered HTML invites injection if it is ever reused."""
        for cls in (ClassicGame, GuessTheWordGame):
            with self.subTest(mode=cls.__name__):
                rendered = str(self._participants(cls))
                self.assertNotIn("<", rendered)
                self.assertNotIn(">", rendered)


if __name__ == "__main__":
    unittest.main()
