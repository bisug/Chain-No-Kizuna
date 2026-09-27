"""Test suite for the Chain No Kizuna bot.

Uses only the standard library (unittest) so no new dependency is introduced.

config.py refuses to import without credentials, so the required variables are
stubbed here before any application module is imported. Tests that need a
different value set it explicitly and reloads the module.
"""
import os

os.environ.setdefault("TOKEN", "123456789:TESTTOKENforunitundertest000000")
# Deliberately left unset: VP_TOKEN. Most of the bot is easier to test with the
# virtual player disabled, and several code paths guard on vp_bot being None.
os.environ.pop("VP_TOKEN", None)
os.environ.setdefault("MONGO_URI", "mongodb://127.0.0.1:27017")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("OWNER_ID", "1")
