import asyncio
import logging
import random
import time
from datetime import datetime, timezone
from typing import Any, Optional

from aiogram import types
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.utils.chat_member import ADMINS, MEMBERS

from config import GameSettings, GameState, OWNER_ID
from chainnokizuna.models.player import Player
from chainnokizuna.core.resources import GlobalState, bot, vp_bot, get_db
from chainnokizuna.utils.keyboards import get_add_vp_to_group_keyboard
from chainnokizuna.utils.telegram import send_admin_group
from chainnokizuna.services.words import check_word_existence, get_random_word

logger = logging.getLogger(__name__)


class ClassicGame:
    """
    Base class for all word chain game modes.
    Implements the core finite state machine: JOINING -> RUNNING -> END.
    """
    name = "classic game"
    command = "startclassic"

    __slots__ = (
        "group_id", "players", "players_in_game", "state", "start_time", "end_time",
        "extended_user_ids", "min_players", "max_players", "time_left", "time_limit",
        "min_letters_limit", "current_word", "longest_word", "longest_word_sender_id",
        "answered", "accepting_answers", "turns", "used_words", "join_lock", "answer_lock",
        "_admin_cache", "allow_any_player_answer"
    )

    def __init__(self, group_id: int) -> None:
        self.group_id = group_id
        self.players: list[Player] = []
        self.players_in_game: list[Player] = []
        self.state = GameState.JOINING
        self.start_time: Optional[datetime] = None
        self.end_time: Optional[datetime] = None
        # Store user ids rather than Player object since players may quit then join to extend again
        self.extended_user_ids: set[int] = set()

        # Game settings
        self.min_players = GameSettings.MIN_PLAYERS
        self.max_players = GameSettings.MAX_PLAYERS
        self.time_left = GameSettings.JOINING_PHASE_SECONDS
        self.time_limit = GameSettings.MAX_TURN_SECONDS
        self.min_letters_limit = GameSettings.MIN_WORD_LENGTH_LIMIT

        # Game attributes
        self.current_word: Optional[str] = None
        self.longest_word = ""
        self.longest_word_sender_id: Optional[int] = None  # TODO: Change to Player object instead of id
        self.answered = False
        self.accepting_answers = False
        self.turns = 0
        self.used_words: set[str] = set()
        self.allow_any_player_answer = False

        self.join_lock = asyncio.Lock()  # Prevent same user / vp joining as multiple players
        self.answer_lock = asyncio.Lock() # Protect against race conditions in turn processing

        self._admin_cache: dict[int, tuple[float, bool]] = {} # user_id -> (timestamp, is_admin)

    def to_dict(self) -> dict:
        """Serialize game state for persistence."""
        return {
            "type": self.__class__.__name__,
            "group_id": self.group_id,
            "state": self.state,
            "start_time": self.start_time.isoformat() if self.start_time else None,
            "time_left": self.time_left,
            "time_limit": self.time_limit,
            "min_letters_limit": self.min_letters_limit,
            "min_players": self.min_players,
            "max_players": self.max_players,
            "current_word": self.current_word,
            "longest_word": self.longest_word,
            "longest_word_sender_id": self.longest_word_sender_id,
            "answered": self.answered,
            "accepting_answers": self.accepting_answers,
            "turns": self.turns,
            "used_words": list(self.used_words),
            "players": [p.to_dict() for p in self.players],
            "players_in_game_ids": [p.user_id for p in self.players_in_game],
            "extended_user_ids": list(self.extended_user_ids),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "ClassicGame":
        """Reconstruct a game from serialized data."""
        game = object.__new__(cls)
        game.group_id = data["group_id"]
        game.state = data["state"]
        game.start_time = datetime.fromisoformat(data["start_time"]) if data.get("start_time") else None
        game.end_time = None
        game.time_left = data.get("time_left", GameSettings.MAX_TURN_SECONDS)
        game.time_limit = data.get("time_limit", GameSettings.MAX_TURN_SECONDS)
        game.min_letters_limit = data.get("min_letters_limit", GameSettings.MIN_WORD_LENGTH_LIMIT)
        game.min_players = data.get("min_players", GameSettings.MIN_PLAYERS)
        game.max_players = data.get("max_players", GameSettings.MAX_PLAYERS)
        game.current_word = data.get("current_word")
        game.longest_word = data.get("longest_word", "")
        game.longest_word_sender_id = data.get("longest_word_sender_id")
        game.answered = data.get("answered", False)
        game.accepting_answers = data.get("accepting_answers", False)
        game.turns = data.get("turns", 0)
        game.used_words = set(data.get("used_words", []))
        game.extended_user_ids = set(data.get("extended_user_ids", []))
        game.join_lock = asyncio.Lock()
        game.answer_lock = asyncio.Lock()
        game._admin_cache = {}

        # Reconstruct players
        from chainnokizuna.models.player import Player
        game.players = [Player.from_dict(p) for p in data.get("players", [])]

        # Reconstruct players_in_game order
        pid_map = {p.user_id: p for p in game.players}
        game.players_in_game = [
            pid_map[uid] for uid in data.get("players_in_game_ids", []) if uid in pid_map
        ]

        return game

    @property
    def min_word_length_enforced(self) -> bool:
        return True

    @property
    def update_current_word_on_answer(self) -> bool:
        return True

    def user_in_game(self, user_id: int) -> bool:
        return any(p.user_id == user_id for p in self.players)

    async def send_message(self, *args: Any, **kwargs: Any) -> types.Message:
        """Sends a message to the group associated with this game."""
        return await bot.send_message(self.group_id, *args, **kwargs)

    async def is_admin(self, user_id: int) -> bool:
        """Checks if a user is an admin in the current group, with local caching."""
        now = time.time()
        if user_id in self._admin_cache:
            ts, is_adm = self._admin_cache[user_id]
            if now - ts < 15: # 15s TTL
                return is_adm

        try:
            user = await bot.get_chat_member(self.group_id, user_id)
            is_adm = isinstance(user, ADMINS)
        except TelegramBadRequest as e:
            if "CHAT_ADMIN_REQUIRED" in str(e):
                is_adm = False
            else:
                raise e

        self._admin_cache[user_id] = (now, is_adm)
        return is_adm

    async def join(self, message: types.Message) -> None:
        """Registers a new player to the game during the JOINING phase."""
        async with self.join_lock:
            if self.state != GameState.JOINING or len(self.players) >= self.max_players:
                return

            # Try to detect game not starting
            if self.time_left < 0:
                asyncio.create_task(self.scan_for_stale_timer())
                return

            # Check if user already joined
            user = message.from_user
            if self.user_in_game(user.id):
                return

            player = await Player.create(user)
            self.players.append(player)

            await self.send_message(
                f"{player.name} joined. There {'is' if len(self.players) == 1 else 'are'} now "
                f"{len(self.players)} player{'' if len(self.players) == 1 else 's'}.",
                parse_mode=ParseMode.HTML
            )

            # Save state after player joins
            from chainnokizuna.db.redis import save_game
            await save_game(self)

            # Start game when max players reached
            if len(self.players) >= self.max_players:
                self.time_left = -99999

    async def forcejoin(self, message: types.Message) -> None:
        async with self.join_lock:
            if self.state == GameState.KILLGAME or len(self.players) >= self.max_players:
                return

            if message.reply_to_message:
                user = message.reply_to_message.from_user
            else:
                user = message.from_user

            # Check if user already joined
            if self.user_in_game(user.id):
                return

            player = await Player.create(user)
            self.players.append(player)
            if self.state == GameState.RUNNING:
                self.players_in_game.append(player)

            await self.send_message(
                f"{player.name} was forced to join. There {'is' if len(self.players) == 1 else 'are'} now "
                f"{len(self.players)} player{'' if len(self.players) == 1 else 's'}.",
                parse_mode=ParseMode.HTML
            )

            # Start game when max players reached
            if len(self.players) >= self.max_players:
                self.time_left = -99999

    async def flee(self, message: types.Message) -> None:
        """Removes the sender from the joining players list."""
        async with self.join_lock:
            if self.state != GameState.JOINING:
                return

            # Find player to remove
            user_id = message.from_user.id
            for i in range(len(self.players)):
                if self.players[i].user_id == user_id:
                    player = self.players.pop(i)
                    break
            else:
                return

            await self.send_message(
                f"{player.name} fled. There {'is' if len(self.players) == 1 else 'are'} now "
                f"{len(self.players)} player{'' if len(self.players) == 1 else 's'}.",
                parse_mode=ParseMode.HTML
            )

    async def forceflee(self, message: types.Message) -> None:
        async with self.join_lock:
            # Player to be fled = Sender of replies message
            if self.state != GameState.JOINING or not message.reply_to_message:
                return

            # Find player to remove
            user_id = message.reply_to_message.from_user.id
            for i in range(len(self.players)):
                if self.players[i].user_id == user_id:
                    player = self.players.pop(i)
                    break
            else:
                return

            await self.send_message(
                f"{player.name} was forced to flee. There {'is' if len(self.players) == 1 else 'are'} now "
                f"{len(self.players)} player{'' if len(self.players) == 1 else 's'}.",
                parse_mode=ParseMode.HTML
            )

    async def addvp(self, message: types.Message) -> None:
        async with self.join_lock:
            if self.state != GameState.JOINING or len(self.players) >= self.max_players:
                return

            # Check if Virtual Player already joined
            if any(p.is_vp for p in self.players):
                return

            # Check if vp adder is player/admin/owner
            if (
                message.from_user.id != OWNER_ID
                and not self.user_in_game(message.from_user.id)
                and not await self.is_admin(message.from_user.id)
            ):
                await self.send_message("Imagine not playing")
                return

            try:
                vp_member = await bot.get_chat_member(self.group_id, vp_bot.id)
                assert isinstance(vp_member, MEMBERS)  # VP must be chat member
            except (TelegramBadRequest, AssertionError):
                vp_name = GlobalState.vp_user.full_name if GlobalState.vp_user else "Kizuna Assistant"
                vp_id = GlobalState.vp_user.id if GlobalState.vp_user else vp_bot.id
                await self.send_message(
                    f"Add <a href='tg://user?id={vp_id}'>{vp_name}</a> here to play as a virtual player.",
                    reply_markup=get_add_vp_to_group_keyboard()
                )
                return

            vp = await Player.vp()
            self.players.append(vp)

            await vp_bot.send_message(self.group_id, f"/join@{GlobalState.bot_user.username}")
            await self.send_message(
                (
                    f"{vp.name} joined. There {'is' if len(self.players) == 1 else 'are'} now "
                    f"{len(self.players)} player{'' if len(self.players) == 1 else 's'}."
                ),
                parse_mode=ParseMode.HTML
            )

            # Start game when max players reached
            if len(self.players) >= self.max_players:
                self.time_left = -99999

    async def remvp(self, message: types.Message) -> None:
        async with self.join_lock:
            if self.state != GameState.JOINING:
                return

            # Check if Virtual Player has joined
            if not any(p.is_vp for p in self.players):
                return

            # Check if vp remover is player/admin
            if (
                message.from_user.id != OWNER_ID
                and not self.user_in_game(message.from_user.id)
                and not await self.is_admin(message.from_user.id)
            ):
                await self.send_message("Imagine not playing")
                return

            for i in range(len(self.players)):
                if self.players[i].is_vp:
                    vp = self.players.pop(i)
                    break
            else:
                return

            await vp_bot.send_message(self.group_id, f"/flee@{GlobalState.bot_user.username}")
            await self.send_message(
                (
                    f"{vp.name} fled. There {'is' if len(self.players) == 1 else 'are'} now "
                    f"{len(self.players)} player{'' if len(self.players) == 1 else 's'}."
                ),
                parse_mode=ParseMode.HTML
            )

    async def extend(self, message: types.Message) -> None:
        if self.state != GameState.JOINING:
            return

        # Check if extender is player/admin/owner
        if (
            message.from_user.id != OWNER_ID
            and not self.user_in_game(message.from_user.id)
            and not await self.is_admin(message.from_user.id)
        ):
            await self.send_message("Imagine not playing")
            return

        # Each player can only extend once and only for 30 seconds except admins
        if await self.is_admin(message.from_user.id):
            arg = message.text.partition(" ")[2]

            # Check if arg is a valid negative integer
            try:
                n = int(arg)
                is_neg = n < 0
                n = abs(n)
            except ValueError:
                n = 30
                is_neg = False
        elif message.from_user.id in self.extended_user_ids:
            await self.send_message("You can only extend once peasant")
            return
        else:
            self.extended_user_ids.add(message.from_user.id)
            n = 30
            is_neg = False

        if is_neg:
            # Reduce joining phase time (admins only)
            if not await self.is_admin(message.from_user.id):
                await self.send_message("Imagine not being admin")
                return

            if n >= self.time_left:
                # Start game immediately
                self.time_left = -99999
            else:
                self.time_left -= n
                await self.send_message(
                    f"The joining phase has been reduced by {n}s.\n"
                    f"You have {self.time_left}s to /join."
                )
        else:
            # Extend joining phase time
            # Max joining phase duration is capped
            added_duration = min(n, GameSettings.MAX_JOINING_PHASE_SECONDS - self.time_left)
            self.time_left += added_duration
            await self.send_message(
                f"The joining phase has been extended by {added_duration}s.\n"
                f"You have {self.time_left}s to /join."
            )

    async def send_turn_message(self) -> None:
        """Sends the announcement for the current player's turn."""
        if not self.players_in_game:
            return

        next_player_text = f" (Next: {self.players_in_game[1].name})" if len(self.players_in_game) > 1 else ""
        await self.send_message(
            (
                f"Turn: {self.players_in_game[0].mention}{next_player_text}\n"
                f"Your word must start with <i>{self.current_word[-1].upper()}</i> and "
                f"include <b>at least {self.min_letters_limit} letters</b>.\n"
                f"You have <b>{self.time_limit}s</b> to answer.\n"
                f"Players remaining: {len(self.players_in_game)}/{len(self.players)}\n"
                f"Total words: {self.turns}"
            ),
            parse_mode=ParseMode.HTML
        )

        # Reset per-turn attributes
        self.answered = False
        self.accepting_answers = True
        self.time_left = self.time_limit

        if self.players_in_game[0].is_vp:
            await self.vp_answer()

    def get_random_valid_answer(self) -> Optional[str]:
        return get_random_word(
            min_len=self.min_letters_limit,
            prefix=self.current_word[-1],
            exclude_words=self.used_words
        )

    async def vp_answer(self) -> None:
        """Asynchronous task for the Virtual Player to generate and send an answer."""
        # Wait before answering to prevent exceeding 20 msg/min message limit
        # Also simulate thinking/input time like human players, wowzers
        delay = random.uniform(2.0, 4.0)
        logger.info(f"VP turn in group {self.group_id}. Thinking for {delay:.2f}s...")
        await asyncio.sleep(delay)

        async with self.answer_lock:
            if self.answered or not self.accepting_answers:
                return

            word = self.get_random_valid_answer()

            if not word:  # No valid words to choose from
                logger.info(f"VP in group {self.group_id} has no valid words.")
                try:
                    await vp_bot.send_message(self.group_id, "/forceskip bey")
                    self.time_left = 0
                except Exception as e:
                    logger.error(f"VP failed to send skip message: {e}")
                return

            logger.info(f"VP in group {self.group_id} answering: {word}")
            try:
                await vp_bot.send_message(self.group_id, word.capitalize())
            except Exception as e:
                logger.error(f"VP failed to send answer: {e}")
                return

            self.post_turn_processing(word)
            await self.send_post_turn_message(word)

    async def additional_answer_checkers(self, word: str, message: types.Message) -> bool:
        # To be overridden by other game modes
        # True/False: valid/invalid answer
        return True

    async def handle_answer(self, message: types.Message) -> None:
        """Processes a potential answer from a player and validates it against game rules."""
        async with self.answer_lock:
            # Re-verify state inside the lock to prevent double-processing
            if self.answered or not self.accepting_answers:
                return

            word = message.text.lower()

            # Check if answer is invalid
            if not word.startswith(self.current_word[-1]):
                await message.reply(
                    f"<i>{word.capitalize()}</i> does not start with <i>{self.current_word[-1].upper()}</i>."
                )
                return

            if self.min_word_length_enforced and len(word) < self.min_letters_limit:
                await message.reply(
                    f"<i>{word.capitalize()}</i> has less than {self.min_letters_limit} letters."
                )
                return
            if word in self.used_words:
                await message.reply(f"<i>{word.capitalize()}</i> has been used.")
                return
            if not check_word_existence(word):
                await message.reply(f"<i>{word.capitalize()}</i> is not in my list of words.")
                return
            if not await self.additional_answer_checkers(word, message):
                return

            self.post_turn_processing(word)
            await self.send_post_turn_message(word)

    def post_turn_processing(self, word: str) -> None:
        """Updates game state, word counts, and persistence after a valid answer."""
        # Update attributes
        self.used_words.add(word)
        self.turns += 1

        if self.update_current_word_on_answer:
            self.current_word = word

        self.players_in_game[0].word_count += 1
        self.players_in_game[0].letter_count += len(word)
        # If both words have the same length, it will (probably) default to the first argument
        self.players_in_game[0].longest_word = max(word, self.players_in_game[0].longest_word, key=len)

        if len(word) > len(self.longest_word):
            self.longest_word = word
            self.longest_word_sender_id = self.players_in_game[0].user_id

        # Set per-turn attributes
        self.answered = True
        self.accepting_answers = False

        # Save state after answer
        from chainnokizuna.db.redis import save_game
        asyncio.create_task(save_game(self))

    async def send_post_turn_message(self, word: str) -> None:
        text = f"<i>{word.capitalize()}</i> is accepted.\n\n"
        # Reduce limits if possible every set number of turns
        if self.turns % GameSettings.TURNS_BETWEEN_LIMITS_CHANGE == 0:
            if self.time_limit > GameSettings.MIN_TURN_SECONDS:
                self.time_limit -= GameSettings.TURN_SECONDS_REDUCTION_PER_LIMIT_CHANGE
                text += (
                    f"Time limit decreased from "
                    f"<b>{self.time_limit + GameSettings.TURN_SECONDS_REDUCTION_PER_LIMIT_CHANGE}s</b> "
                    f"to <b>{self.time_limit}s</b>.\n"
                )
            if self.min_letters_limit < GameSettings.MAX_WORD_LENGTH_LIMIT:
                self.min_letters_limit += GameSettings.WORD_LENGTH_LIMIT_INCREASE_PER_LIMIT_CHANGE
                text += (
                    f"Minimum letters per word increased from "
                    f"<b>{self.min_letters_limit - GameSettings.WORD_LENGTH_LIMIT_INCREASE_PER_LIMIT_CHANGE}</b> "
                    f"to <b>{self.min_letters_limit}</b>.\n"
                )
        await self.send_message(text)

    async def running_initialization(self) -> None:
        # Random starting word
        self.current_word = get_random_word(min_len=self.min_letters_limit)
        self.used_words.add(self.current_word)
        self.start_time = datetime.now(timezone.utc).replace(microsecond=0)

        await self.send_message(
            (
                f"The first word is <i>{self.current_word.capitalize()}</i>.\n\n"
                "Turn order:\n"
                + "\n".join(p.mention for p in self.players_in_game)
            ),
            parse_mode=ParseMode.HTML
        )

    async def running_phase_tick(self) -> bool:
        """
        Executes a single second of the running game phase.
        Handles turn rotation and time-out elimination.
        Returns:
            True if the game has ended, False otherwise.
        """
        # Return values
        # True: Game has ended
        # False: Game is still ongoing
        if self.answered:
            if not self.allow_any_player_answer:
                # Move player who just answered to the end of queue
                self.players_in_game.append(self.players_in_game.pop(0))
            else:
                # In any-player games, we don't rotate, but we reset answered flag
                self.answered = False
        else:
            self.time_left -= 1
            if self.time_left > 0:
                return False

            # Timer ran out
            self.accepting_answers = False
            await self.send_message(
                f"{self.players_in_game[0].mention} ran out of time! They have been eliminated.",
                parse_mode=ParseMode.HTML
            )
            del self.players_in_game[0]

            if len(self.players_in_game) == 1:
                await self.handle_game_end()
                return True

        await self.send_turn_message()
        return False

    async def handle_game_end(self) -> None:
        # Calculate game length
        self.end_time = datetime.now(timezone.utc).replace(microsecond=0)
        td = self.end_time - self.start_time
        game_len_str = f"{int(td.total_seconds()) // 3600:02}{str(td)[-6:]}"

        winner = self.players_in_game[0].mention if self.players_in_game else "No one"
        text = f"{winner} won the game out of {len(self.players)} players!\n"
        text += f"Total words: {self.turns}\n"
        if self.longest_word:
            sender = next((p for p in self.players if p.user_id == self.longest_word_sender_id), None)
            if sender:
                text += f"Longest word: <i>{self.longest_word.capitalize()}</i> from {sender.name}\n"
        text += f"Game length: <code>{game_len_str}</code>"
        await self.send_message(text, parse_mode=ParseMode.HTML)

        GlobalState.games.pop(self.group_id, None)

        # Remove persisted state
        from chainnokizuna.db.redis import remove_game
        await remove_game(self.group_id)

    async def update_db(self) -> None:
        """Asynchronously exports game results and player statistics to MongoDB."""
        db = get_db()

        # Prepare game document
        participants = []
        for player in self.players:
            won = player in self.players_in_game if self.state != GameState.KILLGAME else False
            participants.append({
                "user_id": player.user_id,
                "won": won,
                "word_count": player.word_count,
                "letter_count": player.letter_count,
                "longest_word": player.longest_word,
                "full_name": player._name,
                "username": player._username
            })

        game_doc = {
            "group_id": self.group_id,
            "players_count": len(self.players),
            "game_mode": self.__class__.__name__,
            "winner_id": self.players_in_game[0].user_id if self.players_in_game else None,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "participants": participants
        }

        # Insert game record
        await db.games.insert_one(game_doc)

        if not self.players:
            return

        # Prepare bulk operations for player stats
        from pymongo import UpdateOne
        operations = []

        for p in participants:
            operations.append(
                UpdateOne(
                    {"_id": p["user_id"]},
                    {
                        "$inc": {
                            "game_count": 1,
                            "win_count": 1 if p["won"] else 0,
                            "word_count": p["word_count"],
                            "letter_count": p["letter_count"]
                        },
                        "$set": {
                            "full_name": p["full_name"],
                            "username": p["username"]
                        },
                        "$max": {
                            "longest_word": p["longest_word"]
                        }
                    },
                    upsert=True
                )
            )

        if operations:
            await db.players.bulk_write(operations)

        # --- Redis Dual-Write (Stats Layer) ---
        try:
            from chainnokizuna.db.redis import incr_global_stats
            # Aggregate stats from this game
            total_words = sum(p["word_count"] for p in participants)
            total_letters = sum(p["letter_count"] for p in participants)
            await incr_global_stats(words=total_words, letters=total_letters, games=1)
        except Exception as e:
            logger.error(f"Failed to update Redis stats during update_db: {e}")

    async def scan_for_stale_timer(self) -> None:
        # Check if game timer is stuck
        prev = self.time_left
        for _ in range(5):
            await asyncio.sleep(1)
            if self.state == GameState.KILLGAME or self.group_id not in GlobalState.games:
                return
            if self.time_left != prev:
                return  # Timer not stuck
            prev = self.time_left

        await send_admin_group(f"Prolonged stale/negative timer detected in group <code>{self.group_id}</code>. Game terminated.")
        try:
            await self.send_message("Game timer is malfunctioning. Game terminated.")
        except Exception:
            pass

    async def _cleanup_redis(self) -> None:
        """Removes the game from Redis persistence."""
        from chainnokizuna.db.redis import remove_game
        await remove_game(self.group_id)

    async def _handle_engine_error(self, e: Exception) -> None:
        """Handles unexpected errors in the game engine."""
        logger.error(f"Game engine error in group {self.group_id}: {e}")
        GlobalState.games.pop(self.group_id, None)
        await self._cleanup_redis()
        try:
            await self.send_message(
                f"Game ended due to the following error:\n<code>{e.__class__.__name__}: {e}</code>.\n"
                "My owner will be notified."
            )
        except Exception:
            pass
        raise e

    async def _run_game_engine(self) -> None:
        """The core game engine that drives the timer and phase transitions."""
        negative_timer = 0
        from chainnokizuna.utils.timer import GameTimer
        
        try:
            async for delta in GameTimer():
                if self.state == GameState.JOINING:
                    # JOINING Phase logic
                    if self.time_left > 0:
                        self.time_left -= delta
                        if self.time_left in GameSettings.JOINING_PHASE_WARNINGS:
                            await self.send_message(f"{self.time_left}s left to /join.")
                    elif len(self.players) < self.min_players:
                        await self.send_message("Not enough players. Game terminated.")
                        GlobalState.games.pop(self.group_id, None)
                        await self._cleanup_redis()
                        return
                    else:
                        await self._transition_to_running()
                
                elif self.state == GameState.RUNNING:
                    # RUNNING Phase logic
                    if self.time_left < 0:
                        negative_timer += delta
                    if negative_timer >= 5:
                        raise ValueError("Prolonged negative timer.")

                    for _ in range(delta):
                         if await self.running_phase_tick():  # True: Game ended
                            await self.update_db()
                            return
                
                elif self.state == GameState.KILLGAME:
                    await self.send_message("Game ended forcibly.")
                    GlobalState.games.pop(self.group_id, None)
                    await self._cleanup_redis()
                    return
        except Exception as e:
            await self._handle_engine_error(e)

    async def _transition_to_running(self) -> None:
        """Transitions game from joining to running state."""
        self.state = GameState.RUNNING
        await self.send_message("Game is starting...")
        random.shuffle(self.players)
        self.players_in_game = self.players[:]
        await self.running_initialization()
        await self.send_turn_message()
        from chainnokizuna.db.redis import save_game
        await save_game(self)

    async def main_loop(self, message: types.Message) -> None:
        """
        The main game loop. Handles the game state transitions from JOINING to RUNNING to END.
        Uses a robust timer mechanism to prevent drift.
        """
        try:
            await self.send_message(
                f"A{'n' if self.name[0] in 'aeiou' else ''} {self.name} is starting.\n"
                f"{self.min_players}-{self.max_players} players are needed.\n"
                f"{self.time_left}s to /join."
            )
            await self.join(message)
            await self._run_game_engine()
        except Exception:
            pass

    async def resume_loop(self) -> None:
        """Resume a game that was restored from persistence (skip join phase)."""
        try:
            await self.send_message(
                f"⚡ Bot restarted! Resuming {self.name} with {len(self.players_in_game)} players.\n"
                f"Current word: <i>{self.current_word.capitalize() if self.current_word else 'N/A'}</i>\n"
                f"Total words so far: {self.turns}"
            )

            # Skip the interrupted player's turn — it's unfair to make them answer again
            if len(self.players_in_game) > 1:
                skipped = self.players_in_game[0]
                self.players_in_game.append(self.players_in_game.pop(0))
                await self.send_message(
                    f"{skipped.mention}'s turn was interrupted, skipping to next player.",
                    parse_mode=ParseMode.HTML
                )

            # Reset turn state for a clean resume
            self.answered = False
            self.accepting_answers = True
            self.time_left = self.time_limit
            await self.send_turn_message()

            await self._run_game_engine()
        except Exception:
            pass
            try:
                await self.send_message(
                    f"Game ended due to the following error:\n<code>{e.__class__.__name__}: {e}</code>.\n"
                    "My owner will be notified."
                )
            except Exception:
                pass
            raise
