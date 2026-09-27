"""Regression tests for game state persistence and the nullable from_user fix."""
import asyncio
import datetime
import unittest
from unittest import mock

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


if __name__ == "__main__":
    unittest.main()
