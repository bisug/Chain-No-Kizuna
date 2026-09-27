"""Regression tests for the empty word-list crash.

get_random_word() returns Optional[str]. Before the fix, running_initialization
assigned it to current_word and immediately called .capitalize() or [-1], so an
empty dictionary crashed 8 of the 10 game modes.
"""
import unittest
from unittest import mock

from dawg import CompletionDAWG

from chainnokizuna.models import GAME_MODES
from chainnokizuna.models.game.classic import ClassicGame
from chainnokizuna.models.game.chosen_first_letter import ChosenFirstLetterGame
from chainnokizuna.models.game.guess_the_word import GuessTheWordGame
from chainnokizuna.models.player import Player
from chainnokizuna.services import words as words_mod
from chainnokizuna.services.words import Words
from tests.helpers import make_user, run

DICTIONARY_DEPENDENT = [m for m in GAME_MODES if m.requires_word_list]
DICTIONARY_INDEPENDENT = [m for m in GAME_MODES if not m.requires_word_list]


class TestRequiresWordListFlag(unittest.TestCase):
    def test_exactly_the_modes_that_call_get_random_word_are_flagged(self):
        self.assertEqual(
            set(DICTIONARY_DEPENDENT),
            set(GAME_MODES) - {ChosenFirstLetterGame, GuessTheWordGame},
        )

    def test_flag_defaults_to_true_on_the_base_class(self):
        self.assertTrue(ClassicGame.requires_word_list)

    def test_letter_and_guess_modes_opt_out(self):
        self.assertFalse(ChosenFirstLetterGame.requires_word_list)
        self.assertFalse(GuessTheWordGame.requires_word_list)

    def test_every_mode_inherits_a_boolean(self):
        for mode in GAME_MODES:
            self.assertIsInstance(mode.requires_word_list, bool, mode.__name__)


class TestEmptyDictionary(unittest.TestCase):
    def setUp(self):
        self._dawg, self._count = Words.dawg, Words.count

    def tearDown(self):
        Words.dawg, Words.count = self._dawg, self._count

    def test_get_random_word_returns_none_when_dawg_is_empty(self):
        Words.dawg = CompletionDAWG()
        Words.count = 0
        self.assertIsNone(words_mod.get_random_word())
        self.assertIsNone(words_mod.get_random_word(prefix="a"))

    def test_guess_the_word_mode_survives_an_empty_dawg(self):
        Words.dawg = CompletionDAWG()
        Words.count = 0
        player = Player(make_user())
        for mode in DICTIONARY_INDEPENDENT:
            if mode is GuessTheWordGame:
                continue  # needs its own JSON data files, covered elsewhere
            game = mode(-100555)
            game.players = [player] * 5
            game.players_in_game = [player] * 5
            game.state = 1
            with mock.patch.object(type(game), "send_message", new=mock.AsyncMock()):
                run(game.running_initialization())
            self.assertIsNotNone(game.current_word, f"{mode.__name__} needs no word list")


class TestUpdateRefusesEmptyDictionary(unittest.TestCase):
    """Drive Words.update() with stub sources so the empty-dictionary guard is exercised."""

    class _Cursor:
        def __init__(self, rows):
            self._rows = rows

        async def to_list(self, length=None):
            return self._rows

    class _Collection:
        def __init__(self, rows):
            self._rows = rows

        def find(self, *a, **k):
            return TestUpdateRefusesEmptyDictionary._Cursor(self._rows)

    class _DB:
        def __init__(self, rows):
            self.wordlist = TestUpdateRefusesEmptyDictionary._Collection(rows)

    class _Response:
        def __init__(self, text, status):
            self._text, self.status = text, status

        async def text(self):
            return self._text

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    class _Session:
        def __init__(self, text, status=200):
            self._text, self._status = text, status

        def get(self, url):
            return TestUpdateRefusesEmptyDictionary._Response(self._text, self._status)

    def setUp(self):
        self._dawg, self._count = Words.dawg, Words.count

    def tearDown(self):
        Words.dawg, Words.count = self._dawg, self._count

    def _run_update(self, source_text, db_rows, status=200):
        async def invoke():
            session = self._Session(source_text, status)
            with mock.patch.object(words_mod, "get_session", return_value=session), \
                 mock.patch.object(words_mod, "get_db", return_value=self._DB(db_rows)):
                await Words.update()

        return run(invoke())

    def test_empty_dictionary_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            self._run_update("", [])
        self.assertIn("empty", str(ctx.exception).lower())

    def test_source_words_produce_a_populated_dawg(self):
        self._run_update("apple\nbanana\ncherry", [])
        self.assertEqual(Words.count, 3)
        self.assertIn("apple", Words.dawg)
        self.assertIsNotNone(words_mod.get_random_word())

    def test_db_words_are_used_when_the_source_is_unavailable(self):
        # A failed source fetch falls back to DB words; the dictionary must still load.
        self._run_update("", [{"word": "mango"}])
        self.assertEqual(Words.count, 1)
        self.assertIn("mango", Words.dawg)

    def test_non_200_source_still_falls_back_to_db_words(self):
        self._run_update("ignored", [{"word": "pear"}], status=500)
        self.assertEqual(Words.count, 1)
        self.assertIn("pear", Words.dawg)

    def test_non_alpha_words_are_filtered_out(self):
        self._run_update("apple\n123\nbanana", [])
        self.assertEqual(Words.count, 2)


if __name__ == "__main__":
    unittest.main()
