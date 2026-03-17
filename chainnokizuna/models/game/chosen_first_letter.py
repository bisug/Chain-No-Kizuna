import random
from datetime import datetime, timezone
from string import ascii_lowercase

from aiogram.enums import ParseMode

from chainnokizuna.models.game.classic import ClassicGame


class ChosenFirstLetterGame(ClassicGame):
    name = "chosen first letter game"
    command = "startcfl"

    @property
    def update_current_word_on_answer(self) -> bool:
        return False

    async def running_initialization(self) -> None:
        # Instead of storing the last used word like in other game modes,
        # self.current_word stores in the chosen first letter which is constant throughout the game
        self.current_word = random.choice(ascii_lowercase)
        self.start_time = datetime.now(timezone.utc).replace(microsecond=0)

        await self.send_message(
            (
                f"The chosen first letter is <i>{self.current_word.upper()}</i>.\n\n"
                "Turn order:\n"
                + "\n".join(p.mention for p in self.players_in_game)
            ),
            parse_mode=ParseMode.HTML
        )
