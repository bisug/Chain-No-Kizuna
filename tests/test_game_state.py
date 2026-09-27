"""Regression tests for game state persistence and the nullable from_user fix."""
import asyncio
import datetime
import pathlib
import re
import unittest
from unittest import mock

from aiogram import types, html

from config import GameState
from chainnokizuna.core.resources import GlobalState
from chainnokizuna.db.redis import _get_game_class
from chainnokizuna.models import GAME_MODES, ClassicGame
from chainnokizuna.models.player import Player
from tests.helpers import make_message, make_user, run


class TestSerializationRoundTrip(unittest.TestCase):
    """Every mode must survive to_dict/from_dict, which is the Redis restore path."""

    def setUp(self):
        self.player = Player(make_user(user_id=42, name="Ada"))
        self.player.word_count, self.player.letter_count = 7, 45
        self.player.longest_word, self.player.score = "orchestra", 3

    def _round_trip(self, mode):

        game = mode(-100777)
        game.players = [self.player]
        game.players_in_game = [self.player]
        game.state = 1
        game.start_time = datetime.datetime.now(datetime.timezone.utc)
        game.current_word = "apple"
        game.turns = 5
        game.used_words = {"apple", "pear"}
        data = game.to_dict()
        return data, mode.from_dict(data)

    def test_every_mode_round_trips(self):
        for mode in GAME_MODES:
            with self.subTest(mode=mode.__name__):
                data, back = self._round_trip(mode)
                self.assertEqual(type(back), mode)
                self.assertEqual(back.group_id, -100777)
                self.assertEqual(back.current_word, "apple")
                self.assertEqual(back.used_words, {"apple", "pear"})
                self.assertEqual(back.turns, 5)

    def test_players_survive_the_round_trip(self):
        for mode in GAME_MODES:
            with self.subTest(mode=mode.__name__):
                _, back = self._round_trip(mode)
                self.assertEqual(len(back.players), 1)
                self.assertEqual(back.players[0].user_id, 42)
                self.assertEqual(back.players[0].word_count, 7)
                self.assertEqual(back.players[0].longest_word, "orchestra")

    def test_turn_order_is_preserved(self):
        for mode in GAME_MODES:
            with self.subTest(mode=mode.__name__):
                _, back = self._round_trip(mode)
                self.assertEqual([p.user_id for p in back.players_in_game], [42])

    def test_every_serialised_type_resolves_to_a_class(self):
        for mode in GAME_MODES:
            with self.subTest(mode=mode.__name__):
                data, _ = self._round_trip(mode)
                self.assertIsNotNone(_get_game_class(data["type"]), data["type"])

    def test_every_declared_slot_is_populated(self):
        """from_dict builds the object with object.__new__, skipping __init__.

        Any slot it forgets to assign stays *unset* (not None), because
        __slots__ has no class-level default, and the first read raises
        AttributeError. That crashed a restored game the moment its first
        turn completed, in running_phase_tick and in answer_handler.
        """
        for mode in GAME_MODES:
            with self.subTest(mode=mode.__name__):
                _, back = self._round_trip(mode)
                for klass in reversed(type(back).__mro__):
                    for slot in getattr(klass, "__slots__", ()):
                        if slot.startswith("__"):
                            continue
                        with self.subTest(mode=mode.__name__, slot=f"{klass.__name__}.{slot}"):
                            # getattr, not hasattr: a default would also satisfy hasattr
                            getattr(back, slot)

    def test_allow_any_player_answer_defaults_false_for_restored_games(self):
        """Guess the Word re-declares it as True, so only the base default is checked."""
        _, back = self._round_trip(ClassicGame)
        self.assertFalse(back.allow_any_player_answer)

    def test_allow_any_player_answer_is_not_serialised(self):
        """Only Guess the Word sets it True, and it re-applies that in from_dict."""
        data, _ = self._round_trip(ClassicGame)
        self.assertNotIn("allow_any_player_answer", data)


class TestForcefleeNullableFromUser(unittest.TestCase):
    """reply_to_message.from_user is None for channel posts and anonymous admins."""

    GROUP_ID = -100999

    def setUp(self):
        # ClassicGame uses __slots__, so send_message has to be patched on the class.
        from chainnokizuna.models.game.classic import ClassicGame

        patcher = mock.patch.object(ClassicGame, "send_message", new=mock.AsyncMock())
        patcher.start()
        self.addCleanup(patcher.stop)

    def _game(self):
        game = GAME_MODES[0](self.GROUP_ID)
        game.state = GameState.JOINING
        game.players = [Player(make_user(user_id=5, name="Alpha"))]
        return game

    @staticmethod
    def _channel_post():
        from aiogram import types

        return types.Message(
            message_id=2,
            date=datetime.datetime.now(),
            chat=types.Chat(id=-1009999, type="supergroup"),
            from_user=None,
            sender_chat=types.Chat(id=-1009999, type="channel"),
            text="forwarded from a channel",
        )

    def test_channel_post_reply_does_not_crash(self):
        game = self._game()
        command = make_message(text="/forceflee", reply_to_message=self._channel_post())
        run(game.forceflee(command))  # must not raise AttributeError
        self.assertEqual(len(game.players), 1, "nobody should be removed")

    def test_real_user_reply_still_removes_the_player(self):
        game = self._game()
        target = make_message(text="hi", user=make_user(user_id=5, name="Alpha"))
        command = make_message(text="/forceflee", reply_to_message=target)
        run(game.forceflee(command))
        self.assertEqual(len(game.players), 0, "the replied-to user should be removed")

    def test_ignored_when_game_is_not_joining(self):
        game = self._game()
        game.state = GameState.RUNNING
        target = make_message(text="hi", user=make_user(user_id=5, name="Alpha"))
        command = make_message(text="/forceflee", reply_to_message=target)
        run(game.forceflee(command))
        self.assertEqual(len(game.players), 1)

    def test_ignored_when_there_is_no_replied_to_message(self):
        game = self._game()
        run(game.forceflee(make_message(text="/forceflee")))
        self.assertEqual(len(game.players), 1)


class TestStaleTimerScanIsIdempotent(unittest.TestCase):
    """join() and error_handler both request this scan; it must not pile up.

    Without a guard, N triggers meant N concurrent 5-second scanners, each
    repeating the teardown and the admin-group notification.
    """

    GROUP_ID = -100555

    def setUp(self):
        self.game = ClassicGame(self.GROUP_ID)
        self.game.state = GameState.JOINING
        self.game.time_left = -99999
        GlobalState.games[self.GROUP_ID] = self.game

    def tearDown(self):
        GlobalState.games.clear()

    def test_repeated_requests_start_one_scan(self):
        async def scenario():
            started = []

            async def fake_scan(self):
                started.append(1)

            with mock.patch.object(ClassicGame, "scan_for_stale_timer", new=fake_scan):
                for _ in range(25):
                    self.game.request_stale_scan()
                await asyncio.sleep(0)
            return len(started)

        self.assertEqual(run(scenario()), 1)

    def test_a_new_scan_is_allowed_once_the_previous_one_finishes(self):
        async def scenario():
            started = []

            async def fake_scan(self):
                started.append(1)

            with mock.patch.object(ClassicGame, "scan_for_stale_timer", new=fake_scan):
                self.game.request_stale_scan()
                await asyncio.sleep(0.01)  # let it complete
                self.game.request_stale_scan()
                await asyncio.sleep(0)
            return len(started)

        self.assertEqual(run(scenario()), 2)

    def test_repeated_joins_start_one_scan(self):
        async def scenario():
            started = []

            async def fake_scan(self):
                started.append(1)

            with mock.patch.object(ClassicGame, "scan_for_stale_timer", new=fake_scan):
                for i in range(10):
                    await self.game.join(
                        make_message(text="/join", chat_id=self.GROUP_ID,
                                     user=make_user(user_id=100 + i))
                    )
                await asyncio.sleep(0.01)
            return len(started)

        self.assertEqual(run(scenario()), 1)
        self.assertEqual(len(self.game.players), 0, "a negative timer blocks joining")


class TestTeardownToleratesConcurrentRemoval(unittest.TestCase):
    """/killgame and error_handler delete the entry while main_loop still ticks.

    The joining-phase teardown used `del`, which raised KeyError in that race;
    every other teardown site already used pop(..., None).
    """

    GROUP_ID = -100556

    def tearDown(self):
        GlobalState.games.clear()

    def test_pop_does_not_raise_when_entry_already_removed(self):
        game = ClassicGame(self.GROUP_ID)
        GlobalState.games[self.GROUP_ID] = game
        GlobalState.games.pop(self.GROUP_ID, None)  # e.g. /killgame won the race
        GlobalState.games.pop(self.GROUP_ID, None)  # what main_loop now does
        self.assertNotIn(self.GROUP_ID, GlobalState.games)


class TestAdminCacheIsBounded(unittest.TestCase):
    """Any group member can reach is_admin() via /extend, so the cache must be capped.

    It is per-game __slots__ state that lives as long as the game does, so an
    uncapped dict grew with the group's membership rather than its admin count.
    """

    GROUP_ID = -100557

    def setUp(self):
        from chainnokizuna.models.game import classic as classic_mod

        self.mod = classic_mod
        self.game = ClassicGame(self.GROUP_ID)
        self.api_calls = []

        async def fake_get_chat_member(chat_id, user_id):
            self.api_calls.append(user_id)
            return types.ChatMember(
                member=make_user(user_id=user_id), status="creator"
            )

        patcher = mock.patch.object(classic_mod, "bot", new=mock.Mock(
            get_chat_member=fake_get_chat_member))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_repeated_lookups_hit_the_cache(self):
        async def scenario():
            await self.game.is_admin(7)
            before = len(self.api_calls)
            for _ in range(50):
                await self.game.is_admin(7)
            return len(self.api_calls) - before

        self.assertEqual(run(scenario()), 0, "cached lookups must not call Telegram")

    def test_cache_never_exceeds_the_cap(self):
        async def scenario():
            for uid in range(1000):
                await self.game.is_admin(uid)
            return len(self.game._admin_cache)

        self.assertEqual(run(scenario()), self.mod._ADMIN_CACHE_MAX)

    def test_eviction_is_oldest_first(self):
        async def scenario():
            for uid in range(self.mod._ADMIN_CACHE_MAX + 10):
                await self.game.is_admin(uid)
            return 0 in self.game._admin_cache, 9 in self.game._admin_cache

        oldest_evicted, tenth_evicted = run(scenario())
        self.assertFalse(oldest_evicted, "oldest entry should be evicted")
        self.assertFalse(tenth_evicted)

    def test_expired_entry_is_refetched(self):
        async def scenario():
            await self.game.is_admin(42)
            entry_at = self.game._admin_cache[42][0]
            before = len(self.api_calls)
            with mock.patch.object(
                self.mod.time, "time", return_value=entry_at + self.mod._ADMIN_CACHE_TTL + 1
            ):
                await self.game.is_admin(42)
            return len(self.api_calls) - before

        self.assertEqual(run(scenario()), 1, "an expired entry must re-query Telegram")


class TestSaveGameDoesNotReAddSetMembership(unittest.TestCase):
    """save_game runs on every turn, so it must not re-issue SADD each time.

    The active-games set is the index load_all_games() reads; membership is
    claimed once via register_active_game() and dropped by remove_game().
    """

    GROUP_ID = -100444

    def setUp(self):
        self._saved, self._registered = [], []

    async def _fake_save(self, game):
        self._saved.append(game.group_id)

    async def _fake_register(self, group_id):
        self._registered.append(group_id)

    def test_per_turn_save_does_not_touch_the_set(self):
        game = ClassicGame(self.GROUP_ID)
        # Register once, as the first save of a game does.
        run(self._fake_register(game.group_id))
        for _ in range(5):
            run(self._fake_save(game))
        self.assertEqual(self._registered, [self.GROUP_ID], "registered exactly once")
        self.assertEqual(len(self._saved), 5, "state still saved every turn")

    def test_registration_precedes_the_turn_saves_in_join(self):
        """join() must claim the slot, or a restart cannot find the game."""
        import inspect

        source = inspect.getsource(ClassicGame.join)
        self.assertIn("register_active_game", source,
                      "join() must register the group in the active-games set")


class TestWordlistRejectionLookupIsTargeted(unittest.TestCase):
    """/reqaddword must ask about the requested words, not stream the whole set."""

    def setUp(self):
        import chainnokizuna.handlers.wordlist as wordlist_mod

        self.mod = wordlist_mod
        self.queries = []      # find() filter documents
        self.fetch_lengths = []  # to_list(length=...) values

    class _Cursor:
        def __init__(self, rows, sink, lengths):
            self._rows = rows
            self._sink = sink
            self._lengths = lengths

        async def to_list(self, length=None):
            self._lengths.append(length)
            return self._rows

    class _Collection:
        def __init__(self, rows, sink, lengths):
            self._rows = rows
            self._sink = sink
            self._lengths = lengths

        def find(self, query, *a, **k):
            self._sink.append(query)
            # Honour the $in filter the way Mongo would.
            word_clause = query.get("word", {})
            wanted = set(word_clause.get("$in", [])) if "$in" in word_clause else None
            rows = self._rows if wanted is None else [r for r in self._rows if r["word"] in wanted]
            return TestWordlistRejectionLookupIsTargeted._Cursor(rows, self._sink, self._lengths)

    def _db(self, rows):
        return type("_DB", (), {"wordlist": self._Collection(rows, self.queries, self.fetch_lengths)})()

    def test_query_is_limited_to_the_requested_words(self):
        rows = [{"word": "apple", "accepted": False, "reason": None},
                {"word": "pear", "accepted": False, "reason": "not a word"}]
        with mock.patch.object(self.mod, "check_word_existence", return_value=False):
            words = ["apple", "pear", "zebra"]
            existing, rejected, with_reason = run(self.mod._collect_rejections(self._db(rows), words))

        self.assertEqual(len(self.queries), 1, "exactly one query")
        query = self.queries[0]
        self.assertEqual(query["accepted"], False)
        self.assertEqual(set(query["word"]["$in"]), {"apple", "pear", "zebra"},
                         "must ask only about the requested words, not the whole collection")
        self.assertEqual(existing, [])
        self.assertEqual(rejected, ["<i>Apple</i>"])
        self.assertEqual(with_reason, [("<i>Pear</i>", "not a word")])
        self.assertEqual(words, ["zebra"], "matched words are consumed from the list")

    def test_skips_the_query_when_everything_is_already_in_the_dawg(self):
        words = ["apple", "pear"]
        with mock.patch.object(self.mod, "check_word_existence", return_value=True):
            existing, rejected, with_reason = run(
                self.mod._collect_rejections(self._db([]), words)
            )
        self.assertEqual(self.queries, [], "no query when nothing is left to look up")
        self.assertEqual(len(existing), 2)
        self.assertEqual(words, [])

    def test_bulk_fetch_is_capped_at_the_number_of_words(self):
        rows = [{"word": "apple", "accepted": False, "reason": None}]
        with mock.patch.object(self.mod, "check_word_existence", return_value=False):
            run(self.mod._collect_rejections(self._db(rows), ["apple", "pear"]))
        # to_list length must bound the fetch to the requested words
        self.assertEqual(self.fetch_lengths, [2], "fetch length bounded by request size")


class TestHtmlEscapingOfDynamicText(unittest.TestCase):
    """Dynamic text rendered into ParseMode.HTML must be escaped.

    Telegram rejects the whole send when it sees a bare & or an unsupported
    tag, so an unescaped value does not degrade the formatting, it loses the
    message entirely.
    """

    SUPPORTED_TAGS = {"b", "i", "u", "s", "a", "code", "pre",
                      "tg-spoiler", "tg-emoji", "blockquote"}

    @classmethod
    def _would_telegram_reject(cls, text):
        for m in re.finditer(r"&(?!(?:[a-zA-Z]+|#x?[0-9a-fA-F]+);)", text):
            return f"bare & at {m.start()}"
        for m in re.finditer(r"<(/?)([a-zA-Z0-9-]+)", text):
            if m.group(2) not in cls.SUPPORTED_TAGS:
                return f"unsupported tag <{m.group(2)}>"
        return None

    def test_aiogram_quote_leaves_apostrophes_alone(self):
        """aiogram's quote uses escape(quote=False).

        Telegram does not accept &#x27;, so escaping the quote character would
        break every display name like "O'Brien". Guard against that regressing
        to the stdlib default.
        """
        self.assertEqual(html.quote("O'Brien"), "O'Brien")
        self.assertEqual(html.quote("a & b"), "a &amp; b")

    def test_guess_the_word_educational_reveal_is_escaped(self):
        import chainnokizuna.models.game.guess_the_word as gtw
        from aiogram import Bot
        from chainnokizuna.models.game.guess_the_word import GuessTheWordGame

        captured = []

        async def scenario():
            game = GuessTheWordGame(-1004321)
            game.target_word = "mayor"
            game.guess_count = 3
            game.max_guesses = 30
            game.guess_history = ["🟩 🟨 <b>APPLE</b>"]
            game.start_time = datetime.datetime.now(datetime.timezone.utc)
            game.state = 1
            with mock.patch.object(gtw, "bot", new=Bot(token="1:T")), \
                 mock.patch.object(GuessTheWordGame, "send_message",
                                   new=mock.AsyncMock(side_effect=lambda *a, **k: captured.append(a[0]))):
                await game.handle_game_end()

        run(scenario())
        self.assertTrue(captured, "summary message should have been sent")
        text = captured[0]
        self.assertIsNone(self._would_telegram_reject(text),
                          f"summary would be rejected: {self._would_telegram_reject(text)}")

    def test_real_shipped_data_no_longer_breaks_the_summary(self):
        """'mayor' has a bare '&c.' in its meaning and is in the target pool."""
        import json
        import pathlib

        data_file = pathlib.Path("chainnokizuna/data/commonfiveletterwords.json")
        if not data_file.exists():
            self.skipTest("word data file not present")
        data = json.loads(data_file.read_text())
        meaning = data.get("mayor", {}).get("meaning", "")
        self.assertIn("&", meaning, "fixture assumption: mayor's meaning contains a bare &")
        rendered = f"<b>Meaning:</b> <i>{html.quote(meaning)}</i>"
        self.assertIsNone(self._would_telegram_reject(rendered))

    def test_rejection_reason_is_escaped_on_both_display_paths(self):
        import chainnokizuna.handlers.wordlist as wordlist_mod

        source = pathlib.Path(wordlist_mod.__file__).read_text()
        # Both reqaddword and addwords build the same line; neither may
        # interpolate the stored reason unescaped, because it is re-rendered
        # to every later requester.
        unescaped = "Reason: {reason}."
        self.assertNotIn(unescaped, source,
                         "stored reason must be escaped before reaching HTML")
        self.assertIn("Reason: {html.quote(reason)}", source)

    def test_exception_text_is_escaped_before_reaching_html(self):
        import chainnokizuna.handlers.gameplay as gameplay_mod

        source = pathlib.Path(gameplay_mod.__file__).read_text()
        self.assertNotIn("f\"<code>{e.__class__.__name__}: {e}</code>\"", source,
                         "int() echoes its argument, so this carried owner input into HTML")

    def test_guess_validation_rejects_non_ascii_letters(self):
        """str.isalpha() accepts any Unicode letter; is_word() does not."""
        from chainnokizuna.services.words import is_word

        self.assertTrue(is_word("apple"))
        for bad in ("аpple", "àpple", "ap3le", "a p"):
            self.assertFalse(is_word(bad), bad)
        # isalpha() would have accepted these, which is why it was replaced.
        self.assertTrue("аpple".isalpha())


if __name__ == "__main__":
    unittest.main()
