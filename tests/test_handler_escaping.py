"""Regression tests for gameplay handlers and HTML-safe Telegram replies."""

import datetime
import re
import unittest
from unittest import mock

from aiogram import html, types

from chainnokizuna.core.resources import GlobalState
from chainnokizuna.handlers import gameplay, info, stats
from chainnokizuna.models import ClassicGame, EliminationGame
from chainnokizuna.models.player import Player
from config import GameState
from tests.helpers import make_message, make_user, run


class _HTMLSafetyMixin:
    """Telegram rejects a whole HTML message on a bare & or an unsupported tag."""

    SUPPORTED_TAGS = {
        "b",
        "i",
        "u",
        "s",
        "a",
        "code",
        "pre",
        "tg-spoiler",
        "tg-emoji",
        "blockquote",
    }

    @classmethod
    def _would_telegram_reject(cls, text):
        for m in re.finditer(r"&(?!(?:[a-zA-Z]+|#x?[0-9a-fA-F]+);)", text):
            return f"bare & at {m.start()}"
        for m in re.finditer(r"<(/?)([a-zA-Z0-9-]+)", text):
            if m.group(2) not in cls.SUPPORTED_TAGS:
                return f"unsupported tag <{m.group(2)}>"
        return None


class TestForceJoinChannelPost(unittest.TestCase):
    """A channel post has no from_user; /forcejoin must not dereference it."""

    GROUP_ID = -1005555

    def setUp(self):
        GlobalState.games.clear()

    def tearDown(self):
        GlobalState.games.clear()

    def test_channel_post_reply_is_noop_and_leaves_game_alive(self):
        game = ClassicGame(self.GROUP_ID)
        game.state = GameState.JOINING
        game.players = [Player(make_user(user_id=5, name="Ada"))]
        GlobalState.games[self.GROUP_ID] = game

        channel = types.Chat(id=-1007777, type="channel", title="Channel")
        channel_reply = types.Message(
            message_id=7,
            date=datetime.datetime.now(),
            chat=channel,
            from_user=None,
            sender_chat=channel,
            text="anonymous",
        )
        message = make_message(
            text="/forcejoin", chat_id=self.GROUP_ID, reply_to_message=channel_reply
        )

        run(gameplay.cmd_forcejoin(message))

        self.assertIn(self.GROUP_ID, GlobalState.games)
        self.assertEqual([p.user_id for p in game.players], [5])


class TestPlayingGroupsEscaping(unittest.TestCase, _HTMLSafetyMixin):
    GROUP_ID = -1006666

    def setUp(self):
        GlobalState.games.clear()

    def tearDown(self):
        GlobalState.games.clear()

    def _message(self, get_chat):
        bot = mock.MagicMock()
        bot.get_chat = get_chat
        return make_message(text="/playinggroups", chat_id=self.GROUP_ID).as_(bot)

    def _reply_text(self, get_chat):
        GlobalState.games[self.GROUP_ID] = ClassicGame(self.GROUP_ID)

        async def scenario():
            with mock.patch.object(
                types.Message, "reply", new=mock.AsyncMock()
            ) as reply:
                await info.cmd_playinggroups(self._message(get_chat))
            return reply.call_args.args[0]

        return run(scenario())

    def test_group_title_is_escaped(self):
        title = "A & B <script>"
        text = self._reply_text(
            mock.AsyncMock(
                side_effect=lambda gid: types.Chat(
                    id=gid, type="supergroup", title=title, username=None
                )
            )
        )
        self.assertIn(html.quote(title), text)
        self.assertIsNone(
            self._would_telegram_reject(text),
            f"/playinggroups reply would be rejected: {self._would_telegram_reject(text)}",
        )

    def test_exception_text_is_escaped(self):
        error_text = "db <script> & down"
        text = self._reply_text(mock.AsyncMock(side_effect=RuntimeError(error_text)))
        self.assertIn(html.quote(error_text), text)
        self.assertIsNone(
            self._would_telegram_reject(text),
            f"/playinggroups reply would be rejected: {self._would_telegram_reject(text)}",
        )


class TestLeaderboardEscaping(unittest.TestCase, _HTMLSafetyMixin):
    def setUp(self):
        stats._leaderboard_count_cache = None
        stats._leaderboard_total_at = 0.0

    def tearDown(self):
        stats._leaderboard_count_cache = None
        stats._leaderboard_total_at = 0.0

    def test_db_full_name_is_escaped_in_emitted_reply(self):
        name = "Eve <script> & Co"
        db = mock.MagicMock()
        db.players.count_documents = mock.AsyncMock(return_value=1)
        cursor = mock.MagicMock()
        cursor.sort.return_value = cursor
        cursor.skip.return_value = cursor
        cursor.limit.return_value = cursor
        cursor.to_list = mock.AsyncMock(
            return_value=[{"_id": 7, "full_name": name, "guess_word_wins": 3}]
        )
        db.players.find.return_value = cursor

        async def scenario():
            with (
                mock.patch.object(stats, "get_db", return_value=db),
                mock.patch.object(
                    types.Message, "reply", new=mock.AsyncMock()
                ) as reply,
            ):
                await stats.cmd_topseekers(make_message(text="/top"))
            return reply.call_args.args[0]

        text = run(scenario())
        self.assertIn(html.quote(name), text)
        self.assertIsNone(
            self._would_telegram_reject(text),
            f"leaderboard reply would be rejected: {self._would_telegram_reject(text)}",
        )


class TestAddVpEscaping(unittest.TestCase, _HTMLSafetyMixin):
    GROUP_ID = -1007777

    def setUp(self):
        GlobalState.games.clear()

    def tearDown(self):
        GlobalState.games.clear()

    def test_vp_full_name_is_escaped(self):
        GlobalState.games[self.GROUP_ID] = EliminationGame(self.GROUP_ID)
        name = "Eve <script> & Co"
        vp_user = mock.MagicMock(full_name=name, username="vp")

        async def scenario():
            with (
                mock.patch.object(gameplay, "vp_bot", new=mock.MagicMock()),
                mock.patch.object(GlobalState, "vp_user", new=vp_user),
                mock.patch.object(
                    types.Message, "reply", new=mock.AsyncMock()
                ) as reply,
            ):
                await gameplay.cmd_addvp(
                    make_message(text="/addvp", chat_id=self.GROUP_ID)
                )
            return reply.call_args.args[0]

        text = run(scenario())
        self.assertIn(html.quote(name), text)
        self.assertIsNone(
            self._would_telegram_reject(text),
            f"/addvp reply would be rejected: {self._would_telegram_reject(text)}",
        )


class _FakeClock:
    """Stands in for the stats module's clock so slow queries can be simulated."""

    def __init__(self, now=100.0):
        self.now = now

    def time(self):
        return self.now

    def monotonic(self):
        return self.now


class TestStatsCacheClock(unittest.TestCase):
    """Cache timestamps must follow the DB work, not precede it."""

    def setUp(self):
        stats._global_stats_cache = None
        stats._leaderboard_count_cache = None
        stats._leaderboard_total_at = 0.0

    def tearDown(self):
        stats._global_stats_cache = None
        stats._leaderboard_count_cache = None
        stats._leaderboard_total_at = 0.0

    @staticmethod
    def _global_stats_db(clock):
        db = mock.MagicMock()

        async def count_games(_query):
            clock.now += 60  # the slow query runs after the timestamp is read
            return 4

        db.games.distinct = mock.AsyncMock(return_value=[-1001, -1002])
        db.games.count_documents = mock.AsyncMock(side_effect=count_games)
        db.players.count_documents = mock.AsyncMock(return_value=9)
        aggregate = mock.MagicMock()
        aggregate.to_list = mock.AsyncMock(
            return_value=[{"word_cnt": 10, "letter_cnt": 50}]
        )
        db.players.aggregate = mock.MagicMock(return_value=aggregate)
        return db

    def test_slow_db_call_still_yields_fresh_30s_global_cache(self):
        clock = _FakeClock(100.0)
        db = self._global_stats_db(clock)

        with (
            mock.patch.object(stats, "get_db", return_value=db),
            mock.patch.object(stats, "time", clock),
        ):
            first = run(stats.get_global_stats())
            self.assertEqual(stats._global_stats_cache[0], 160.0)
            clock.now = 189.0  # 29s after the stamp, still within TTL
            second = run(stats.get_global_stats())

        self.assertEqual(second, first)
        self.assertEqual(db.games.count_documents.await_count, 1)

    def test_slow_count_query_still_yields_fresh_30s_leaderboard_cache(self):
        clock = _FakeClock(100.0)
        db = mock.MagicMock()

        async def count_winners(_query):
            clock.now += 60
            return 10

        db.players.count_documents = mock.AsyncMock(side_effect=count_winners)
        cursor = mock.MagicMock()
        cursor.sort.return_value = cursor
        cursor.skip.return_value = cursor
        cursor.limit.return_value = cursor
        cursor.to_list = mock.AsyncMock(return_value=[])
        db.players.find.return_value = cursor

        with (
            mock.patch.object(stats, "get_db", return_value=db),
            mock.patch.object(stats, "time", clock),
        ):
            first = run(stats.get_leaderboard_page(1))
            self.assertEqual(stats._leaderboard_total_at, 160.0)
            clock.now = 189.0  # 29s after the stamp, still within TTL
            second = run(stats.get_leaderboard_page(1))

        self.assertEqual(second[0], first[0])
        self.assertEqual(db.players.count_documents.await_count, 1)


if __name__ == "__main__":
    unittest.main()
