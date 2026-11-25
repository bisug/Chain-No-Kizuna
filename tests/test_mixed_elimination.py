"""Regression tests for MixedEliminationGame.handle_answer.

The override skips the answer_lock and the answered/accepting_answers recheck
that ClassicGame.handle_answer performs. The gameplay caller checks those flags
before awaiting, and validation awaits, so two in-flight answers can both pass
used_words and both mutate turn state.
"""

import ast
import asyncio
import pathlib
import unittest
from unittest import mock

from chainnokizuna.models.game import mixed_elimination as mix_mod
from chainnokizuna.models.game.chosen_first_letter import ChosenFirstLetterGame
from chainnokizuna.models.game.mixed_elimination import MixedEliminationGame
from chainnokizuna.models.player import Player
from tests.helpers import make_message, make_user, run

SOURCE = pathlib.Path(mix_mod.__file__)


def _handle_answer_ast():
    tree = ast.parse(SOURCE.read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "handle_answer":
            return node
    raise AssertionError("handle_answer not found")


class TestHandleAnswerLock(unittest.TestCase):
    """Structural: the override must guard the whole body with answer_lock."""

    def test_answer_lock_is_the_only_top_level_block(self):
        fn = _handle_answer_ast()
        async_withs = [s for s in fn.body if isinstance(s, ast.AsyncWith)]
        self.assertEqual(
            len(async_withs), 1, "handle_answer should hold exactly one lock"
        )
        self.assertEqual(async_withs[0].items[0].context_expr.attr, "answer_lock")

    def test_no_state_mutation_outside_the_lock(self):
        fn = _handle_answer_ast()
        lock = next(s for s in fn.body if isinstance(s, ast.AsyncWith))
        trailing = [
            s
            for s in fn.body
            if not isinstance(s, ast.AsyncWith) and s.lineno > lock.end_lineno
        ]
        self.assertEqual(trailing, [], "no state mutation may run outside answer_lock")

    def test_both_flags_are_rechecked_first_inside_the_lock(self):
        fn = _handle_answer_ast()
        lock = next(s for s in fn.body if isinstance(s, ast.AsyncWith))
        first = lock.body[0]
        self.assertIsInstance(first, ast.If, "recheck must be the first statement")
        condition = ast.unparse(first.test)
        self.assertIn("self.answered", condition)
        self.assertIn("self.accepting_answers", condition)


class TestConcurrentAnswersCountOnce(unittest.TestCase):
    """Two answers in flight must produce one turn and one mutation."""

    def test_second_answer_cannot_double_count(self):
        game = MixedEliminationGame(-1007771)
        game.current_word = "apple"
        game.used_words = {"apple"}
        game.turns = 0
        game.answered = False
        game.accepting_answers = True
        game.game_mode = None  # last-letter branch; both words start with 'e'
        player = Player(make_user(user_id=5, name="Ada"))
        game.players = [player]
        game.players_in_game = [player]

        first = make_message(text="elder")
        second = make_message(text="echo")

        async def scenario():
            release = asyncio.Event()
            entered = []

            async def fake_checkers(self, word, message):
                entered.append(word)
                await release.wait()
                return True

            with (
                mock.patch.object(mix_mod, "check_word_existence", return_value=True),
                mock.patch.object(
                    MixedEliminationGame,
                    "additional_answer_checkers",
                    new=fake_checkers,
                ),
                mock.patch.object(
                    MixedEliminationGame,
                    "send_post_turn_message",
                    new=mock.AsyncMock(),
                ),
                mock.patch("chainnokizuna.db.redis.save_game", new=mock.AsyncMock()),
            ):
                tasks = [
                    asyncio.create_task(game.handle_answer(m)) for m in (first, second)
                ]
                for _ in range(3):
                    await asyncio.sleep(0)
                release.set()
                await asyncio.gather(*tasks)
            return game, len(entered)

        game, entered = run(scenario())
        self.assertEqual(game.turns, 1, "only one turn should have been processed")
        self.assertEqual(
            game.used_words, {"apple", "elder"}, "one word should be committed"
        )
        self.assertEqual(entered, 1, "the second answer should be rejected by recheck")


class TestChosenLetterStartingWordIsRequired(unittest.TestCase):
    """get_random_word returns None on an empty pool; abort cleanly, do not store None."""

    def test_none_starting_word_is_a_clean_value_error(self):
        game = MixedEliminationGame(-1007772)
        game.used_words = set()
        game.players_in_game = [Player(make_user(user_id=5, name="Ada"))]

        with (
            mock.patch.object(
                mix_mod.random, "choice", return_value=ChosenFirstLetterGame
            ),
            mock.patch.object(mix_mod, "get_random_word", return_value=None),
            mock.patch.object(
                MixedEliminationGame, "send_message", new=mock.AsyncMock()
            ),
        ):
            with self.assertRaisesRegex(ValueError, "No usable word"):
                run(game.running_initialization())

        self.assertEqual(game.used_words, set(), "None must not be stored as a word")


if __name__ == "__main__":
    unittest.main()
