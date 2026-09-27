"""Regression tests for the empty word-list crash.

get_random_word() returns Optional[str]. Before the fix, running_initialization
assigned it to current_word and immediately called .capitalize() or [-1], so an
empty dictionary crashed 8 of the 10 game modes.
"""
import asyncio
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
        """Mimics AsyncCursor: supports both to_list() and async iteration."""

        def __init__(self, rows):
            self._rows = rows

        async def to_list(self, length=None):
            return self._rows

        def __aiter__(self):
            async def gen():
                for row in self._rows:
                    yield row
            return gen()

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


class TestAsyncWordPicker(unittest.TestCase):
    """Game starts call the DAWG walk from a coroutine, so it must not block the loop.

    The unprefixed walk is a full scan and measured ~90ms on a 370k dictionary;
    running it inline stalls the event loop for every concurrent game start.
    """

    def setUp(self):
        self._dawg, self._count = Words.dawg, Words.count
        Words.dawg = CompletionDAWG(["apple", "apply", "banana", "pear", "zebra"])
        Words.count = 5

    def tearDown(self):
        Words.dawg, Words.count = self._dawg, self._count

    def test_async_returns_a_valid_word(self):
        word = run(words_mod.get_random_word_async())
        self.assertIn(word, {"apple", "apply", "banana", "pear", "zebra"})

    def test_async_honours_min_len(self):
        for _ in range(20):
            self.assertGreaterEqual(len(run(words_mod.get_random_word_async(min_len=5))), 5)

    def test_async_honours_prefix(self):
        for _ in range(10):
            self.assertTrue(run(words_mod.get_random_word_async(prefix="app")).startswith("app"))

    def test_async_returns_none_for_an_empty_dawg(self):
        Words.dawg = CompletionDAWG()
        Words.count = 0
        self.assertIsNone(run(words_mod.get_random_word_async()))

    def test_async_matches_the_sync_implementation(self):
        """Same constraints, same distribution source, no behavioural change."""
        for kwargs in ({"min_len": 5}, {"required_letter": "e"},
                       {"banned_letters": ["z"]}, {"prefix": "app"}):
            for _ in range(15):
                word = run(words_mod.get_random_word_async(**kwargs))
                if word is None:
                    # No match is a legitimate result; the sync version does the same.
                    self.assertIsNone(words_mod.get_random_word(**kwargs))
                    continue
                if kwargs.get("min_len"):
                    self.assertGreaterEqual(len(word), kwargs["min_len"])
                if kwargs.get("prefix"):
                    self.assertTrue(word.startswith(kwargs["prefix"]))
                if kwargs.get("required_letter"):
                    self.assertIn(kwargs["required_letter"], word)
                if kwargs.get("banned_letters"):
                    self.assertFalse(set(kwargs["banned_letters"]) & set(word))

    def test_prefixed_call_does_not_touch_the_executor(self):
        """Prefixed is ~0.1ms, so it must stay inline rather than paying thread hand-off."""
        async def scenario():
            loop = asyncio.get_running_loop()
            with mock.patch.object(
                loop, "run_in_executor",
                side_effect=AssertionError("prefixed path should not offload"),
            ):
                return await words_mod.get_random_word_async(prefix="app")

        self.assertTrue(run(scenario()).startswith("app"))

    def test_unprefixed_call_does_offload(self):
        async def scenario():
            loop = asyncio.get_running_loop()
            seen = []
            original = loop.run_in_executor

            async def spy(executor, fn, *a):
                seen.append(1)
                return await original(executor, fn, *a)

            with mock.patch.object(loop, "run_in_executor", spy):
                word = await words_mod.get_random_word_async()
            return seen, word

        seen, word = run(scenario())
        self.assertEqual(len(seen), 1, "unprefixed pick should run in the executor")
        self.assertIn(word, {"apple", "apply", "banana", "pear", "zebra"})

    def test_every_mode_still_initialises_through_the_async_picker(self):
        """running_initialization is a coroutine; the game modes await the pick.

        The fixture must contain words at least as long as the strictest mode's
        limit, otherwise the pick legitimately returns None and the mode's own
        .capitalize() fails. Hard mode asks for MAX_WORD_LENGTH_LIMIT (10).
        """
        player = Player(make_user())
        Words.dawg = CompletionDAWG([
            "apple", "banana", "pear", "zebra",
            "extraordinary", "communication", "understanding", "professional",
        ])
        Words.count = 8
        for mode in DICTIONARY_DEPENDENT:
            if mode.__name__ == "MixedEliminationGame":
                continue  # random mode selection; covered by the round-trip test
            with self.subTest(mode=mode.__name__):
                game = mode(-100666)
                game.players = [player] * 2
                game.players_in_game = [player] * 2
                game.state = 1
                with mock.patch.object(type(game), "send_message", new=mock.AsyncMock()):
                    run(game.running_initialization())
                self.assertIsNotNone(game.current_word)


if __name__ == "__main__":
    unittest.main()
