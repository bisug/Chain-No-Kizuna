"""Regression tests for GuessTheWordGame.handle_answer holding its lock.

Before the fix the state_lock block covered only the first half of the method.
The guess counter check, the answered flag, GlobalState.pop, remove_game and
save_game all ran unlocked, so a concurrent correct guess could end the game and
delete its Redis key, after which the losing player's save_game would resurrect
the finished game.
"""
import ast
import pathlib
import unittest
from unittest import mock

from chainnokizuna.models.game import guess_the_word as gtw_mod
from chainnokizuna.models.game.guess_the_word import GuessTheWordGame
from tests.helpers import make_message, run

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


if __name__ == "__main__":
    unittest.main()
