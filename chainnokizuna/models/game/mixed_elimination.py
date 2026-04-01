import random
from datetime import datetime, timezone
from string import ascii_lowercase

from aiogram import types
from aiogram.enums import ParseMode

from chainnokizuna.models.game.banned_letters import BannedLettersGame
from chainnokizuna.models.game.chosen_first_letter import ChosenFirstLetterGame
from chainnokizuna.models.game.classic import ClassicGame
from chainnokizuna.models.game import EliminationGame
from chainnokizuna.models.game.required_letter import RequiredLetterGame
from chainnokizuna.services.words import check_word_existence, get_random_word


class MixedEliminationGame(EliminationGame):
    # Implementing this game mode was a misteak
    # self.current_word does not store the chosen first letter
    # but the whole word during ChosenFirstLetterGame here
    # for easier transition of game modes

    name = "mixed elimination game"
    command = "startmelim"
    game_modes = [
        ClassicGame,
        ChosenFirstLetterGame,
        BannedLettersGame,
        RequiredLetterGame
    ]

    __slots__ = ("game_mode", "banned_letters", "required_letter")

    def __init__(self, group_id):
        super().__init__(group_id)
        self.game_mode = None
        self.banned_letters = []
        self.required_letter = None

    def to_dict(self) -> dict:
        data = super().to_dict()
        data["game_mode_name"] = self.game_mode.__name__ if self.game_mode else None
        data["banned_letters"] = self.banned_letters
        data["required_letter"] = self.required_letter
        return data

    @classmethod
    def from_dict(cls, data: dict) -> "MixedEliminationGame":
        game = super().from_dict(data)
        game.banned_letters = data.get("banned_letters", [])
        game.required_letter = data.get("required_letter")
        # Resolve game_mode class from name
        mode_name = data.get("game_mode_name")
        game.game_mode = None
        if mode_name:
            mode_map = {m.__name__: m for m in cls.game_modes}
            game.game_mode = mode_map.get(mode_name)
        return game

    async def send_turn_message(self) -> None:
        text = f"Turn: {self.players_in_game[0].mention}"
        if self.turns_until_elimination > 1:
            text += f" (Next: {self.players_in_game[1].name})"
        text += "\n"

        if self.game_mode is ChosenFirstLetterGame:
            starting_letter = self.current_word[0]
        else:
            starting_letter = self.current_word[-1]
        text += f"Your word must start with <i>{starting_letter.upper()}</i>"

        if self.game_mode is BannedLettersGame:
            text += f" and <b>exclude</b> <i>{', '.join(c.upper() for c in self.banned_letters)}</i>"
        elif self.game_mode is RequiredLetterGame:
            text += f" and <b>include</b> <i>{self.required_letter.upper()}</i>"
        text += ".\n"

        text += f"You have <b>{self.time_limit}s</b> to answer.\n\n"
        text += "Leaderboard:\n" + self.get_leaderboard(show_player=self.players_in_game[0])
        await self.send_message(text, parse_mode=ParseMode.HTML)

        # Reset per-turn attributes
        self.answered = False
        self.accepting_answers = True
        self.time_left = self.time_limit

    async def additional_answer_checkers(self, word: str, message: types.Message) -> bool:
        if self.game_mode is BannedLettersGame:
            return await BannedLettersGame.additional_answer_checkers(self, word, message)
        elif self.game_mode is RequiredLetterGame:
            return await RequiredLetterGame.additional_answer_checkers(self, word, message)
        return True

    async def handle_answer(self, message: types.Message) -> None:
        word = message.text.lower()

        # Starting letter
        if self.game_mode is ChosenFirstLetterGame:
            if not word.startswith(self.current_word[0]):
                await message.reply(
                    f"<i>{word.capitalize()}</i> does not start with <i>{self.current_word[0].upper()}</i>."
                )
                return
        elif not word.startswith(self.current_word[-1]):
            await message.reply(
                f"<i>{word.capitalize()}</i> does not start with <i>{self.current_word[-1].upper()}</i>."
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
        super().post_turn_processing(word)
        if self.game_mode is RequiredLetterGame:
            RequiredLetterGame.change_required_letter(self)

    async def running_initialization(self) -> None:
        self.start_time = datetime.now(timezone.utc).replace(microsecond=0)
        self.turns_until_elimination = len(self.players_in_game)
        self.game_mode = random.choice(self.game_modes)

        # First round is special since first word has to be set

        # Set starting word and mode-based attributes
        if self.game_mode is BannedLettersGame:
            BannedLettersGame.set_banned_letters(self)
            self.current_word = get_random_word(banned_letters=self.banned_letters)
        elif self.game_mode is ChosenFirstLetterGame:
            # Ensure uniform probability of each letter as the starting letter
            self.current_word = get_random_word(prefix=random.choice(ascii_lowercase))
        else:
            self.current_word = get_random_word()
        if self.game_mode is RequiredLetterGame:
            RequiredLetterGame.change_required_letter(self)
        self.used_words.add(self.current_word)

        await self.send_message(
            (
                f"The first word is <i>{self.current_word.capitalize()}</i>.\n\n"
                "Turn order:\n"
                + "\n".join(p.mention for p in self.players_in_game)
            ),
            parse_mode=ParseMode.HTML
        )

        round_text = f"Round 1 is starting...\nMode: <b>{self.game_mode.name.capitalize()}</b>"
        if self.game_mode is ChosenFirstLetterGame:
            round_text += f"\nThe chosen first letter is <i>{self.current_word[0].upper()}</i>."
        elif self.game_mode is BannedLettersGame:
            round_text += f"\nBanned letters: <i>{', '.join(c.upper() for c in self.banned_letters)}</i>"
        round_text += "\n\nLeaderboard:\n" + self.get_leaderboard()
        await self.send_message(round_text, parse_mode=ParseMode.HTML)

    def set_game_mode(self) -> None:
        # Random game mode without having the same mode twice in a row
        modes = self.game_modes[:]
        if self.game_mode:
            modes.remove(self.game_mode)
        self.game_mode = random.choice(modes)

        # Set mode-based attributes
        if self.game_mode is BannedLettersGame:
            BannedLettersGame.set_banned_letters(self)
        elif self.game_mode is RequiredLetterGame:
            RequiredLetterGame.change_required_letter(self)

    async def handle_round_start(self) -> None:
        self.turns_until_elimination = len(self.players_in_game)
        self.set_game_mode()

        round_text = f"Round {self.round} is starting...\nMode: <b>{self.game_mode.name.capitalize()}</b>"
        if self.game_mode is ChosenFirstLetterGame:
            # The last letter of the current word becomes the chosen first letter
            self.current_word = self.current_word[-1]
            round_text += f"\nThe chosen first letter is <i>{self.current_word.upper()}</i>."
        elif self.game_mode is BannedLettersGame:
            round_text += f"\nBanned letters: <i>{', '.join(c.upper() for c in self.banned_letters)}</i>"
        round_text += "\n\nLeaderboard:\n" + self.get_leaderboard()
        await self.send_message(round_text, parse_mode=ParseMode.HTML)
