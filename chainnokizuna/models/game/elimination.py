from datetime import datetime, timezone
from typing import Optional

from aiogram import types
from aiogram.enums import ParseMode

from chainnokizuna.models.game.classic import ClassicGame
from chainnokizuna.models.player import Player
from config import GameSettings, GameState
from chainnokizuna.services.words import get_random_word


class EliminationGame(ClassicGame):
    """
    Game mode where players are eliminated based on their score at the end of each round.
    Scores are based on word lengths.
    """
    name = "elimination game"
    command = "startelim"

    __slots__ = ("round", "turns_until_elimination", "exceeded_score_limit")

    def __init__(self, group_id: int) -> None:
        super().__init__(group_id)

        # Elimination game settings
        self.min_players = GameSettings.ELIM_MIN_PLAYERS
        self.max_players = GameSettings.ELIM_MAX_PLAYERS
        self.time_left = GameSettings.ELIM_JOINING_PHASE_SECONDS
        self.time_limit = GameSettings.ELIM_TURN_SECONDS
        # No minimum letters limit (though a word must contain at least one letter by definition)
        # Since answering words with few letters will eventually lead to elimination
        self.min_letters_limit = 1

        # Elimination game attributes
        self.round = 1
        self.turns_until_elimination = 0
        self.exceeded_score_limit = False  # Remind players that there is a turn score increment ceiling

    def to_dict(self) -> dict:
        data = super().to_dict()
        data.update({
            "round": self.round,
            "turns_until_elimination": self.turns_until_elimination,
            "exceeded_score_limit": self.exceeded_score_limit,
        })
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "EliminationGame":
        game = super().from_dict(data)
        game.round = data.get("round", 1)
        game.turns_until_elimination = data.get("turns_until_elimination", 0)
        game.exceeded_score_limit = data.get("exceeded_score_limit", False)
        return game


    @property
    def min_word_length_enforced(self) -> bool:
        return False

    async def forcejoin(self, message: types.Message) -> None:
        # Joining in the middle of an elimination game puts one at a disadvantage since points are cumulative
        # So forcejoin is only allowed in joining phase
        if self.state == GameState.JOINING:
            await super().forcejoin(message)

    def get_leaderboard(self, show_player: Optional[Player] = None) -> str:
        # Make a copy of players in game
        players = self.players_in_game[:]
        # Sort by letter count descending then user id ascending
        players.sort(key=lambda k: (-k.score, k.user_id))

        # Generate all formatted lines first
        lines = []
        show_idx = -1
        for i, p in enumerate(players):
            prefix = "> " if p is show_player else ""
            lines.append(f"{prefix}{i + 1}. {p.name}: {p.score}")
            if p is show_player:
                show_idx = i

        total = len(lines)
        if total <= 10 or not show_player:
            # If few players or specific player not highlighted, show up to 10 (top 10 ideally)
            # But original logic showed *all* if <= 10.
            # If total > 10 and no show_player, original showed top 10?
            # Original code: "if not show_player: Show every player".
            # Wait, if 100 players and no show_player, it showed 100 lines? That's spammy.
            # I'll stick to showing all if not show_player for backward compatibility,
            # OR cap it at 10 to reduce spam (User suggestion 2 was "Reduce Chat Spam").
            # But the user specifically asked for "refactor leaderboard", not "change behavior".
            # I'll keep behavior close to original but cleaner.
            if total <= 10:
                return "\n".join(lines)
            elif not show_player:
                 return "\n".join(lines) # Original behavior for no show_player was "Show every player"

        # Complex slicing logic made simple
        # If player is in top 5 or bottom 5, show top 5 ... bottom 5
        if show_idx < 5 or show_idx >= total - 5:
            return "\n".join(lines[:5] + ["..."] + lines[-5:])

        # Player is in the middle
        return "\n".join(lines[:5] + ["..."] + [lines[show_idx]] + ["..."] + lines[-5:])

    async def send_turn_message(self) -> None:
        await self.send_message(
            (
                f"Turn: {self.players_in_game[0].mention}"
                # Do not show next player on queue if this is last turn of the round
                # Since they could be eliminated
                + (f" (Next: {self.players_in_game[1].name})\n" if self.turns_until_elimination > 1 else "\n")
                + f"Your word must start with <i>{self.current_word[-1].upper()}</i>.\n"
                  f"You have <b>{self.time_limit}s</b> to answer.\n\n"
                  "Leaderboard:\n" + self.get_leaderboard(show_player=self.players_in_game[0])
            ),
            parse_mode=ParseMode.HTML
        )

        # Reset per-turn attributes
        self.answered = False
        self.accepting_answers = True
        self.time_left = self.time_limit

    def post_turn_processing(self, word: str) -> None:
        super().post_turn_processing(word)
        self.players_in_game[0].score += min(len(word), GameSettings.ELIM_MAX_TURN_SCORE)
        if len(word) > GameSettings.ELIM_MAX_TURN_SCORE:
            self.exceeded_score_limit = True

    async def send_post_turn_message(self, word: str) -> None:
        text = f"<i>{word.capitalize()}</i> is accepted."
        if self.exceeded_score_limit:
            text += f"\nThat is a long word! It will only count for {GameSettings.ELIM_MAX_TURN_SCORE} points."
            self.exceeded_score_limit = False
        await self.send_message(text)
        # No limit reduction

    async def running_initialization(self) -> None:
        # Random starting word
        self.current_word = get_random_word()
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

        await self.handle_round_start()

    async def running_phase_tick(self) -> bool:
        if not self.answered:
            self.time_left -= 1
            if self.time_left > 0:
                return False
            self.accepting_answers = False
            await self.send_message(
                f"{self.players_in_game[0].mention} ran out of time!",
                parse_mode=ParseMode.HTML
            )

        # Regardless of answering in time or running out of time
        # Elimination happens at the end of the round
        # Move player who just answered to the end of queue
        self.players_in_game.append(self.players_in_game.pop(0))
        self.turns_until_elimination -= 1

        # Handle round transition
        if self.turns_until_elimination == 0:
            await self.handle_round_end()

            if len(self.players_in_game) <= 1:
                await self.handle_game_end()
                return True

            await self.handle_round_start()

        await self.send_turn_message()
        return False

    async def handle_round_start(self) -> None:
        self.turns_until_elimination = len(self.players_in_game)

        await self.send_message(
            f"Round {self.round} is starting...\n\nLeaderboard:\n" + self.get_leaderboard(),
            parse_mode=ParseMode.HTML
        )

    async def handle_round_end(self) -> None:
        # Eliminate player(s) with lowest score
        # Hence the possibility of no winners
        min_score = min(p.score for p in self.players_in_game)
        eliminated = [p for p in self.players_in_game if p.score == min_score]

        await self.send_message(
            (
                f"Round {self.round} completed.\n\nLeaderboard:\n"
                + self.get_leaderboard()
                + "\n\n"
                + ", ".join(p.mention for p in eliminated)
                + " "
                + ("is" if len(eliminated) == 1 else "are")
                + f" eliminated for having the lowest score of {min_score}."
            ),
            parse_mode=ParseMode.HTML
        )

        # Update attributes
        self.players_in_game = [p for p in self.players_in_game if p not in eliminated]
        self.round += 1
