import orjson
import random
import logging
import asyncio
from typing import Any, Optional
from datetime import datetime, timezone

from aiogram import types
from aiogram.enums import ParseMode

from chainnokizuna.models.game.classic import ClassicGame
from chainnokizuna.core.resources import bot, GlobalState
from config import GameState

logger = logging.getLogger(__name__)

class GuessTheWordGame(ClassicGame):
    name = "guess the word"
    command = "startguess"

    __slots__ = ("target_word", "guess_count", "max_guesses", "guess_history", "dictionary", "last_waiting_msg_id")

    def __init__(self, group_id: int) -> None:
        super().__init__(group_id)
        self.target_word: str = ""
        self.guess_count: int = 0
        self.max_guesses: int = 30
        self.guess_history: list[str] = []
        self.dictionary: list[str] = []
        self.allow_any_player_answer = True
        self.time_limit = 60
        self.last_waiting_msg_id: Optional[int] = None

    def to_dict(self) -> dict:
        data = super().to_dict()
        data.update({
            "target_word": self.target_word,
            "guess_count": self.guess_count,
            "max_guesses": self.max_guesses,
            "guess_history": self.guess_history,
            "last_waiting_msg_id": self.last_waiting_msg_id,
        })
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "GuessTheWordGame":
        game = super().from_dict(data)
        game.target_word = data.get("target_word", "")
        game.guess_count = data.get("guess_count", 0)
        game.max_guesses = data.get("max_guesses", 30)
        game.guess_history = data.get("guess_history", [])
        game.dictionary = [] # Will be lazy loaded in initialization if missing
        game.allow_any_player_answer = True
        game.last_waiting_msg_id = data.get("last_waiting_msg_id")
        return game

    async def running_initialization(self) -> None:
        # Load validation dictionary (all words)
        try:
            with open("chainnokizuna/data/all-five.json", "rb") as f:
                valid_data = orjson.loads(f.read())
            self.dictionary = [w.lower() for w in valid_data]
        except Exception as e:
            logger.error(f"Failed to load validation dictionary: {e}")
            raise ValueError("Could not load validation word database.")

        # Load common words for target selection
        try:
            with open("chainnokizuna/data/commonfiveletterwords.json", "rb") as f:
                target_data = orjson.loads(f.read())
            target_dictionary = [w.lower() for w in target_data.keys() if len(w) == 5]
            self.target_word = random.choice(target_dictionary)
        except Exception as e:
            logger.error(f"Failed to load target data: {e}")
            raise ValueError("Could not load target word database.")

        self.start_time = datetime.now(timezone.utc).replace(microsecond=0)
        self.guess_count = 0
        self.guess_history = []
        self.accepting_answers = True
        self.time_left = self.time_limit
        
        await self.send_message(
            "🎮 <b>Guess the Word Game Started!</b>\n\n"
            "I've picked a secret 5-letter word. Guess it to win!\n"
            "<b>Anyone</b> can guess at any time! Just send your 5-letter word.\n\n"
            "🟩 = Correct letter, correct spot\n"
            "🟨 = Correct letter, wrong spot\n"
            "🟥 = Wrong letter\n\n"
            f"You have <b>{self.max_guesses}</b> total guesses. Good luck!",
            parse_mode=ParseMode.HTML
        )

    async def send_turn_message(self) -> None:
        # Override to prevent turn rotation messages, but we still need to keep the game "ticking"
        # We'll show a status update every few guesses or just keep it silent until a guess
        self.answered = False
        self.accepting_answers = True
        self.time_left = self.time_limit

    async def handle_answer(self, message: types.Message) -> None:
        # Ignore bot messages (including our own and VP bot)
        if message.from_user.is_bot:
            return

        guess = message.text.lower()

        # Strict 5-letter validation: silent ignore if not 5 letters
        if len(guess) != 5:
            return

        if not guess.isalpha():
            # Still valid to reply here as it's a "bad" 5-letter word
            await message.reply("Your guess must contain only letters!")
            return

        # Dictionary validation
        if not self.dictionary:
             try:
                with open("chainnokizuna/data/all-five.json", "rb") as f:
                    valid_data = orjson.loads(f.read())
                self.dictionary = [w.lower() for w in valid_data]
             except Exception:
                pass
        
        if guess not in self.dictionary:
            await message.reply(f"<i>{guess.upper()}</i> is not in my 5-letter dictionary!")
            return

        # Delete previous waiting message if it exists
        if self.last_waiting_msg_id:
            try:
                await bot.delete_message(self.group_id, self.last_waiting_msg_id)
                self.last_waiting_msg_id = None
            except Exception:
                pass

        # Dynamic Participation: Add player if they haven't "joined"
        from chainnokizuna.models.player import Player
        player = next((p for p in self.players if p.user_id == message.from_user.id), None)
        if not player:
            player = await Player.create(message.from_user)
            self.players.append(player)

        self.guess_count += 1
        
        # Calculate hints
        result = self._calculate_hints(guess)
        emojis_spaced = " ".join(list(result))
        history_line = f"{emojis_spaced} <b>{guess.upper()}</b>"
        self.guess_history.append(history_line)
        
        if guess == self.target_word:
            self.answered = True
            player = next((p for p in self.players if p.user_id == message.from_user.id), None)
            if player:
                player.word_count += 1
                player.score += 100 # Bonus for winning
            
            # Format history for header
            header = f"<b>5-letter mode</b> · {self.guess_count}/{self.max_guesses}"
            history_display = "\n".join(self.guess_history)

            await message.reply(
                f"🎉 <b>BINGO!</b>\n\n"
                f"{header}\n\n"
                f"{history_display}\n\n"
                f"The word was indeed <b>{self.target_word.upper()}</b>.\n"
                f"Winner: {message.from_user.mention_html()}",
                parse_mode=ParseMode.HTML
            )
            await self.handle_game_end_winner(message.from_user)
            return

        # Not correct
        header = f"<b>5-letter mode</b> · {self.guess_count}/{self.max_guesses}"
        # Show all history lines up to max_guesses
        history_display = "\n".join(self.guess_history)
        
        hint_msg = f"{header}\n\n{history_display}"
        
        if self.guess_count >= self.max_guesses:
            await message.reply(
                f"💀 <b>Game Over!</b>\n\n"
                f"You've used all {self.max_guesses} guesses.\n"
                f"The word was: <b>{self.target_word.upper()}</b>.\n"
                f"Try harder next time! 😉",
                parse_mode=ParseMode.HTML
            )
            self.answered = True # To end the loop
            from chainnokizuna.db.redis import remove_game
            GlobalState.games.pop(self.group_id, None)
            await remove_game(self.group_id)
            return

        await message.reply(hint_msg)
        
        from chainnokizuna.db.redis import save_game
        asyncio.create_task(save_game(self))

    def _calculate_hints(self, guess: str) -> str:
        hints = ["🟥"] * 5
        target_list = list(self.target_word)
        guess_list = list(guess)

        # First pass: Green
        for i in range(5):
            if guess_list[i] == target_list[i]:
                hints[i] = "🟩"
                target_list[i] = None # Mark as used
                guess_list[i] = None

        # Second pass: Yellow
        for i in range(5):
            if guess_list[i] is not None:
                if guess_list[i] in target_list:
                    hints[i] = "🟨"
                    target_list[target_list.index(guess_list[i])] = None # Mark as used

        return "".join(hints)

    async def handle_game_end_winner(self, winner_user: types.User) -> None:
        self.players_in_game = [p for p in self.players if p.user_id == winner_user.id]
        await self.handle_game_end()

    async def running_phase_tick(self) -> bool:
        if self.answered:
            # If game ended via correct guess or max guesses
            if self.guess_count >= self.max_guesses or (self.players_in_game and self.state == GameState.RUNNING):
                return True
            self.answered = False
            return False

        self.time_left -= 1

        if self.time_left <= 0:
            # For this mode, we don't eliminate on time usually, just keep it running
            if self.guess_count == 0:
                # Delete previous message if it exists
                if self.last_waiting_msg_id:
                    try:
                        await bot.delete_message(self.group_id, self.last_waiting_msg_id)
                    except Exception:
                        pass
                
                msg = await self.send_message("The Guess the Word game is still waiting for the first guess! Use /end to cancel if you're stuck.")
                self.last_waiting_msg_id = msg.message_id
            self.time_left = self.time_limit # Reset timer
            
        return False

    async def main_loop(self, message: types.Message) -> None:
        """Skip joining phase and start immediately."""
        negative_timer = 0
        try:
            self.state = GameState.RUNNING
            await self.running_initialization()
            
            # Initial save
            from chainnokizuna.db.redis import save_game
            await save_game(self)

            from chainnokizuna.utils.timer import GameTimer
            async for delta in GameTimer():
                if self.state == GameState.RUNNING:
                    if self.time_left < 0:
                        negative_timer += delta
                    if negative_timer >= 5:
                        raise ValueError("Prolonged negative timer.")

                    for _ in range(delta):
                         if await self.running_phase_tick():
                            await self.update_db()
                            return
                elif self.state == GameState.KILLGAME:
                    await self.send_message("Game ended forcibly.")
                    GlobalState.games.pop(self.group_id, None)
                    from chainnokizuna.db.redis import remove_game
                    await remove_game(self.group_id)
                    return
        except Exception as e:
            logger.error(f"Error in GuessTheWordGame loop: {e}")
            GlobalState.games.pop(self.group_id, None)
            from chainnokizuna.db.redis import remove_game
            await remove_game(self.group_id)
            try:
                await self.send_message(f"Game ended due to error: <code>{e}</code>")
            except: pass
            raise

    async def update_db(self) -> None:
        """Override to track Guess the Word wins specifically."""
        from chainnokizuna.core.resources import get_db
        db = get_db()
        
        # Standard game recording
        participants = [{"user_id": p.user_id, "name": p.name, "word_count": p.word_count, 
                        "letter_count": p.letter_count, "won": p in self.players_in_game,
                        "longest_word": p.longest_word, "full_name": p._name, 
                        "username": p._username} for p in self.players]
        
        game_doc = {
            "group_id": self.group_id,
            "game_mode": self.__class__.__name__,
            "winner_id": self.players_in_game[0].user_id if self.players_in_game else None,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "participants": participants
        }
        await db.games.insert_one(game_doc)

        if not self.players:
            return

        from pymongo import UpdateOne
        operations = []
        for p in participants:
            inc_data = {
                "game_count": 1,
                "win_count": 1 if p["won"] else 0,
                "word_count": p["word_count"],
                "letter_count": p["letter_count"]
            }
            # Special field for this mode
            if p["won"]:
                inc_data["guess_word_wins"] = 1
                
            operations.append(
                UpdateOne(
                    {"_id": p["user_id"]},
                    {
                        "$inc": inc_data,
                        "$set": {
                            "full_name": p["full_name"],
                            "username": p["username"]
                        },
                        "$max": {"longest_word": p["longest_word"]}
                    },
                    upsert=True
                )
            )

        if operations:
            await db.players.bulk_write(operations)

    async def addvp(self, message: types.Message) -> None:
        """Explicitly disable VP for this mode."""
        await message.reply("Virtual Players are not supported in <b>Guess the Word</b> mode.", parse_mode=ParseMode.HTML)

    async def handle_game_end(self) -> None:
        # Calculate game length
        self.end_time = datetime.now(timezone.utc).replace(microsecond=0)
        td = self.end_time - self.start_time
        game_len_str = f"{int(td.total_seconds()) // 3600:02}{str(td)[-6:]}"

        # Load educational data
        meaning = "N/A"
        example = "N/A"
        try:
            with open("chainnokizuna/data/commonfiveletterwords.json", "rb") as f:
                data = orjson.loads(f.read())
            word_data = data.get(self.target_word, {})
            meaning = word_data.get("meaning", "N/A")
            example = word_data.get("example", "N/A")
        except Exception as e:
            logger.error(f"Failed to load educational data: {e}")

        # Construct grid (limited to first 12 guesses to avoid spam)
        grid = "\n".join(self.guess_history[:12])
        if len(self.guess_history) > 12:
            grid += f"\n... ({len(self.guess_history) - 12} more)"

        winner = self.players_in_game[0].mention if self.players_in_game else "No one"
        
        text = f"🏁 <b>Guess the Word Summary!</b>\n\n"
        text += f"Word: <b>{self.target_word.upper()}</b>\n"
        text += f"Winner: {winner}\n"
        text += f"Guesses: {self.guess_count}/{self.max_guesses}\n"
        text += f"Time: <code>{game_len_str}</code>\n\n"
        
        text += f"📊 <b>Guess History:</b>\n{grid}\n\n"
        
        text += f"💡 <b>Educational Reveal:</b>\n"
        text += f"<b>Meaning:</b> <i>{meaning}</i>\n"
        text += f"<b>Example:</b> <i>\"{example}\"</i>"

        from chainnokizuna.core.resources import GlobalState
        await self.send_message(text, parse_mode=ParseMode.HTML)

        GlobalState.games.pop(self.group_id, None)
        from chainnokizuna.db.redis import remove_game
        await remove_game(self.group_id)
