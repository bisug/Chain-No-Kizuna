"""
Configuration management for the Word Chain bot.
Handles environment variables, game settings, and global constants.
"""
import orjson
import logging
import os
from typing import Optional
from dotenv import load_dotenv

logger = logging.getLogger(__name__)

# Load constants from environment variables
load_dotenv()

def get_list(key: str, default: str = "") -> list[int]:
    """
    Parses a comma-separated string or a JSON list of integers from environment variables.
    """
    val = os.getenv(key, str(default)).strip()
    if not val or val == "[]":
        return []
    try:
        data = orjson.loads(val)
        if isinstance(data, list):
            return [int(i) for i in data]
    except (orjson.JSONDecodeError, ValueError):
        pass
    return [int(i.strip()) for i in val.split(",") if i.strip().isdigit()]

def get_str(key: str, default: str = "") -> str:
    """
    Retrieves a string value from environment variables.
    """
    return os.getenv(key, default).strip()


def get_int(key: str, default: int = 0) -> int:
    """
    Retrieves an integer from environment variables, falling back to `default` when unset or blank.
    """
    val = os.getenv(key, "").strip()
    return int(val) if val else default


def get_required(key: str, *aliases: str) -> str:
    """
    Retrieves a value that must be supplied via the environment.

    Secrets have no in-code default on purpose: a hardcoded fallback turns a
    missing config into a silent connection to whoever committed it.
    """
    for name in (key, *aliases):
        val = os.getenv(name, "").strip()
        if val:
            return val
    names = " / ".join((key, *aliases))
    raise RuntimeError(
        f"Missing required environment variable: {names}. "
        f"See .env.template for the full list."
    )


# --- Bot Tokens & Identity ---
# Main Telegram Bot Token from @BotFather
TOKEN: str = get_required("TOKEN")
# Virtual Player Bot Token (Optional) from @BotFather
VP_TOKEN: Optional[str] = os.getenv("VP_TOKEN") or None

# --- Database & Cache ---
# MongoDB connection URI (e.g. from MongoDB Atlas)
MONGO_URI: str = get_required("MONGO_URI", "MONGODB_URI")
# Database name for the bot
DB_NAME: str = get_str("DB_NAME", "WordChainDB")
# Redis/Valkey connection URL (e.g. from Upstash or redis.io)
REDIS_URL: str = get_required("REDIS_URL")

# --- Administrative Configuration ---
# Your numeric Telegram user ID from @userinfobot
OWNER_ID: int = int(get_required("OWNER_ID"))
# ID of the group where bot logs and reports are sent (0 disables admin reporting)
ADMIN_GROUP_ID: int = get_int("ADMIN_GROUP_ID")
# ID of your community's official game group (0 disables the welcome message)
OFFICIAL_GROUP_ID: int = get_int("OFFICIAL_GROUP_ID")
# ID of the channel for word addition announcements (0 disables announcements)
WORD_ADDITION_CHANNEL_ID: int = get_int("WORD_ADDITION_CHANNEL_ID")

# --- Permissions & Access ---
# Comma-separated or JSON list of VIP user IDs
VIP: list[int] = get_list("VIP", "")
# Comma-separated or JSON list of VIP group IDs
VIP_GROUP: list[int] = get_list("VIP_GROUP", "")

SUPPORT_GROUP = get_str("SUPPORT_GROUP", "SuMelodyVibes")
UPDATE_CHANNEL = get_str("UPDATE_CHANNEL", "SuMelodyVibes")

# --- Logging ---
# Log level for the bot (DEBUG, INFO, WARNING, ERROR, CRITICAL)
LOG_LEVEL: str = get_str("LOG_LEVEL", "INFO").upper()

WORDLIST_SOURCE = "https://raw.githubusercontent.com/dwyl/english-words/master/words.txt"

# Curated pools, used for the words the bot chooses for itself (opening word and
# VP replies) rather than for validating player input. The full list above stays
# the authority on what counts as a real word, so these never narrow the game.
#
# These are .json rather than .txt because .gitignore excludes data/*.txt, which
# is the runtime download cache.
WORD_POOL_FILES = {
    # Everyday words a player is likely to recognise and build on.
    "common": "chainnokizuna/data/commonwords.json",
    # Every 5-letter word, for modes that need a fixed word length.
    "five": "chainnokizuna/data/fiveletters.json",
}


class GameState:
    """Possible states for a Game instance."""
    JOINING = 0
    RUNNING = 1
    KILLGAME = -1


class GameSettings:
    """Static configuration for game mechanics and balance."""
    JOINING_PHASE_SECONDS = 60
    MAX_JOINING_PHASE_SECONDS = 180
    MIN_PLAYERS = 2
    MAX_PLAYERS = 50
    MIN_TURN_SECONDS = 20
    MAX_TURN_SECONDS = 40
    TURN_SECONDS_REDUCTION_PER_LIMIT_CHANGE = 5
    MIN_WORD_LENGTH_LIMIT = 3
    MAX_WORD_LENGTH_LIMIT = 10
    # Upper bound on the word a game opens with. The source list runs to 45
    # letters, which is unplayable as a prompt, so opening words are capped.
    MAX_STARTING_WORD_LENGTH = 12
    WORD_LENGTH_LIMIT_INCREASE_PER_LIMIT_CHANGE = 1
    TURNS_BETWEEN_LIMITS_CHANGE = 5
    JOINING_PHASE_WARNINGS = (15, 30, 60)

    ELIM_JOINING_PHASE_SECONDS = 90
    ELIM_MIN_PLAYERS = 5
    ELIM_MAX_PLAYERS = 30
    ELIM_TURN_SECONDS = 30
    ELIM_MAX_TURN_SCORE = 20
