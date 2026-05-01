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

# --- Bot Tokens & Identity ---
# Main Telegram Bot Token from @BotFather
TOKEN: str = os.getenv("TOKEN", "8533283359:AAFQ5Ofkys-jsU6kR9blaXIXLPfUdGizeVg")
# Virtual Player Bot Token (Optional) from @BotFather
VP_TOKEN: Optional[str] = os.getenv("VP_TOKEN", "8515282600:AAF9yflYJxswNEYhG58mVf8iU5Y6jD81EXA")

# --- Database & Cache ---
# MongoDB connection URI (e.g. from MongoDB Atlas)
MONGO_URI: str = os.getenv("MONGO_URI") or os.getenv("MONGODB_URI") or "mongodb+srv://sumiloo:gurasnani@cluster0.nb0umdm.mongodb.net/?retryWrites=true&w=majority"
# Database name for the bot
DB_NAME: str = os.getenv("DB_NAME", "WordChainDB")
# Redis/Valkey connection URL (e.g. from Upstash or redis.io)
REDIS_URL: str = os.getenv("REDIS_URL", "rediss://default:gQAAAAAAAbZ_AAIgcDEzODBlNmFmNDA0NzE0ODY2OTFkZjBkMzU3OTY3YTQ5Yg@fair-marmot-112255.upstash.io:6379")

# --- Administrative Configuration ---
# Your numeric Telegram user ID from @userinfobot
OWNER_ID: int = int(os.getenv("OWNER_ID", "7804972365"))
# ID of the group where bot logs and reports are sent
ADMIN_GROUP_ID: int = int(os.getenv("ADMIN_GROUP_ID", "-1003893856082"))
# ID of your community's official game group
OFFICIAL_GROUP_ID: int = int(os.getenv("OFFICIAL_GROUP_ID", "-1003893856082"))
# ID of the channel for word addition announcements
WORD_ADDITION_CHANNEL_ID: int = int(os.getenv("WORD_ADDITION_CHANNEL_ID", "-1003893856082"))

# --- Permissions & Access ---
# Comma-separated or JSON list of VIP user IDs
VIP: list[int] = get_list("VIP", "")
# Comma-separated or JSON list of VIP group IDs
VIP_GROUP: list[int] = get_list("VIP_GROUP", "-1003893856082")

SUPPORT_GROUP = get_str("SUPPORT_GROUP", "SuMelodyVibes")
UPDATE_CHANNEL = get_str("UPDATE_CHANNEL", "SuMelodyVibes")

WORDLIST_SOURCE = "https://raw.githubusercontent.com/dwyl/english-words/master/words.txt"


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
    INCREASED_MAX_PLAYERS = 300
    MIN_TURN_SECONDS = 20
    MAX_TURN_SECONDS = 40
    TURN_SECONDS_REDUCTION_PER_LIMIT_CHANGE = 5
    MIN_WORD_LENGTH_LIMIT = 3
    MAX_WORD_LENGTH_LIMIT = 10
    WORD_LENGTH_LIMIT_INCREASE_PER_LIMIT_CHANGE = 1
    TURNS_BETWEEN_LIMITS_CHANGE = 5
    JOINING_PHASE_WARNINGS = (15, 30, 60)

    ELIM_JOINING_PHASE_SECONDS = 90
    ELIM_MIN_PLAYERS = 5
    ELIM_MAX_PLAYERS = 30
    ELIM_INCREASED_MAX_PLAYERS = 50
    ELIM_TURN_SECONDS = 30
    ELIM_MAX_TURN_SCORE = 20
