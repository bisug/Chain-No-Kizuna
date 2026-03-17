from chainnokizuna.models.game.banned_letters import BannedLettersGame
from chainnokizuna.models.game.chaos import ChaosGame
from chainnokizuna.models.game.chosen_first_letter import ChosenFirstLetterGame
from chainnokizuna.models.game.classic import ClassicGame
from chainnokizuna.models.game.elimination import EliminationGame
from chainnokizuna.models.game.hard_mode import HardModeGame
from chainnokizuna.models.game.mixed_elimination import MixedEliminationGame
from chainnokizuna.models.game.random_first_letter import RandomFirstLetterGame
from chainnokizuna.models.game.required_letter import RequiredLetterGame
from chainnokizuna.models.game.guess_the_word import GuessTheWordGame

GAME_MODES = [
    ClassicGame,
    HardModeGame,
    ChaosGame,
    ChosenFirstLetterGame,
    RandomFirstLetterGame,
    BannedLettersGame,
    RequiredLetterGame,
    EliminationGame,
    MixedEliminationGame,
    GuessTheWordGame
]

__all__ = (
    "ClassicGame",
    "HardModeGame",
    "ChaosGame",
    "ChosenFirstLetterGame",
    "RandomFirstLetterGame",
    "BannedLettersGame",
    "RequiredLetterGame",
    "EliminationGame",
    "MixedEliminationGame",
    "GuessTheWordGame",
    "GAME_MODES"
)
