import asyncio
import aiofiles
import logging
import random
from string import ascii_lowercase
from typing import Optional

import orjson
from dawg import CompletionDAWG

from config import WORDLIST_SOURCE, WORD_POOL_FILES
from chainnokizuna.core.resources import get_db, get_session

logger = logging.getLogger(__name__)

# Named word pools the game modes draw from. "general" is the full source list
# and is the one used to validate what a player types, so a curated pool can
# never make a legitimate word "not in my list of words".
DEFAULT_POOL = "general"


class Words:
    """
    Manages the bot's word dictionary using a Directed Acyclic Word Graph (DAWG)
    for high-performance prefix lookups and existence checks.
    """

    dawg: CompletionDAWG = CompletionDAWG()
    count: int = 0
    # Extra pools loaded from bundled data files, keyed by pool name. These are
    # deliberately smaller and curated; see load_pools().
    pools: dict[str, CompletionDAWG] = {}

    @staticmethod
    async def update() -> None:
        """
        Refreshes the word list by fetching from a remote text source and the MongoDB database.
        Rebuilds the DAWG in a separate executor thread to avoid blocking the event loop.
        """
        logger.info("Retrieving words")

        async def get_words_from_source() -> list[str]:
            session = get_session()
            try:
                async with session.get(WORDLIST_SOURCE) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        # Cache the words
                        try:
                            import os
                            os.makedirs("chainnokizuna/data", exist_ok=True)
                            async with aiofiles.open("chainnokizuna/data/words.txt", "w", encoding="utf-8") as f:
                                await f.write(text)
                        except Exception as e:
                            logger.error(f"Failed to write cache: {e}")
                        return text.splitlines()
                    else:
                        logger.warning(f"Failed to fetch words from source: {resp.status}")
            except Exception as e:
                logger.error(f"Error fetching words from source: {e}")
            
            # Fallback to local cache
            try:
                import os
                if os.path.exists("chainnokizuna/data/words.txt"):
                    async with aiofiles.open("chainnokizuna/data/words.txt", "r", encoding="utf-8") as f:
                        logger.info("Loading words from local cache.")
                        content = await f.read()
                        return content.splitlines()
                else:
                    logger.error("No local wordlist cache found.")
                    return []
            except FileNotFoundError:
                logger.error("No local wordlist cache found.")
                return []


        async def get_words_from_db() -> list[str]:
            db = get_db()
            cursor = db.wordlist.find({"accepted": True}, {"word": 1, "_id": 0})
            # Stream rather than to_list(length=None): the accepted wordlist is
            # the largest collection the bot holds, and materialising it all
            # only to copy it again in set() roughly tripled peak memory.
            words: list[str] = []
            async for row in cursor:
                words.append(row["word"])
            return words

        source_task = asyncio.create_task(get_words_from_source())
        db_task = asyncio.create_task(get_words_from_db())

        source_words = await source_task
        db_words = await db_task
        if not source_words:
            logger.warning("Word source unavailable. Using only DB words.")
        wordlist = list(set(source_words + db_words))

        logger.info("Processing words")

        def build_dawg(words_list: list[str]) -> CompletionDAWG:
            filtered = [w.lower() for w in words_list if w.isalpha()]
            return CompletionDAWG(filtered)

        loop = asyncio.get_running_loop()
        Words.dawg = await loop.run_in_executor(None, build_dawg, wordlist)

        Words.count = len(Words.dawg.keys())
        if not Words.count:
            # An empty DAWG makes get_random_word() return None, which crashes every
            # game mode that picks a starting word. Fail loudly instead of silently.
            raise ValueError("Word list is empty (source and database both unavailable).")

        logger.info(f"DAWG updated with {Words.count} words")

        await load_pools()


async def load_pools() -> None:
    """Builds the curated pools that ship alongside the full word list.

    The full source list is exhaustive but contains 3-letter strings like "qwl"
    and "mpu" that no player would guess, and 126k words longer than the game
    ever accepts. The bot picks its own starting words and plays as the VP, so
    it can emit an unplayable prompt or stall on a letter with almost no
    candidates. Curated pools give it sane choices without narrowing what a
    player is allowed to type, which still validates against the full list.

    Pools are advisory for word choice only. A missing or malformed file is
    logged and skipped rather than raised: the general list alone is enough to
    run every game mode.
    """
    loop = asyncio.get_running_loop()

    def build(words: list[str]) -> CompletionDAWG:
        return CompletionDAWG(words)

    for name, path in WORD_POOL_FILES.items():
        try:
            async with aiofiles.open(path, "rb") as f:
                words = orjson.loads(await f.read())
        except FileNotFoundError:
            logger.error(f"Word pool {name!r} missing at {path}; skipping.")
            continue
        except Exception as e:
            logger.error(f"Failed to load word pool {name!r} from {path}: {e}")
            continue

        cleaned = [w.lower() for w in words if isinstance(w, str) and w.isalpha()]
        if not cleaned:
            logger.error(f"Word pool {name!r} is empty after filtering; skipping.")
            continue

        Words.pools[name] = await loop.run_in_executor(None, build, cleaned)
        logger.info(f"Word pool {name!r}: {len(Words.pools[name].keys()):,} words")


def get_pool(name: Optional[str] = None) -> CompletionDAWG:
    """Returns the DAWG for a named pool, falling back to the full list.

    An unknown or unloaded pool name must not return an empty DAWG: that would
    make get_random_word() yield None and force-skip turns. Falling back keeps
    a game playable if a pool file is missing in a deployment.
    """
    if not name or name == DEFAULT_POOL:
        return Words.dawg
    pool = Words.pools.get(name)
    if pool is None:
        logger.warning(f"Word pool {name!r} not loaded; using the full word list.")
        return Words.dawg
    return pool


def is_word(s: str) -> bool:
    """Checks if a string contains only lowercase ASCII letters."""
    return all(c in ascii_lowercase for c in s)


def check_word_existence(word: str) -> bool:
    """Checks if a word exists in the DAWG dictionary."""
    return word in Words.dawg


def get_random_word(
    min_len: int = 1,
    prefix: Optional[str] = None,
    required_letter: Optional[str] = None,
    banned_letters: Optional[list[str]] = None,
    exclude_words: Optional[set[str]] = None,
    pool: Optional[str] = None,
    max_len: Optional[int] = None
) -> Optional[str]:
    """
    Retrieves a random word from the dictionary matching specific constraints.

    Synchronous and CPU-bound: the unprefixed variant walks the whole DAWG and
    measured ~90ms against a 370k-word dictionary. Call it from
    get_random_word_async() when the caller is a coroutine, so a burst of
    concurrent game starts cannot stall the event loop.

    pool selects a curated word list for the bot's own choices. It does not
    restrict what players may type: check_word_existence() always checks the
    full list, so pointing a mode at a curated pool changes which word the bot
    opens with, never which answers count.
    """
    dawg = get_pool(pool)
    if not dawg:
        return None

    # Use DAWG prefix search if available
    iterator = dawg.iterkeys(prefix) if prefix else dawg.iterkeys()

    # Measured against a 370k-word dictionary: this full scan is ~80-95ms, but
    # it is dominated by iterating the DAWG, not by building the list. Reservoir
    # sampling bounds the memory (0.06MB vs multi-MB) yet costs ~240ms because
    # of the extra randrange per word, so it is a net loss here. Truncating to the
    # first N matches would be fast but biases selection toward dictionary order.
    # The game modes call this with a prefix, where the same scan is ~0.1ms.
    candidates = []

    for w in iterator:
        if len(w) < min_len:
            continue
        if max_len is not None and len(w) > max_len:
            continue
        if required_letter and required_letter not in w:
            continue
        if banned_letters and any(i in w for i in banned_letters):
            continue
        if exclude_words and w in exclude_words:
            continue
        candidates.append(w)

    if candidates:
        return random.choice(candidates)

    # A curated pool is small, so some prefixes legitimately have no match in it
    # (the "common" pool has only a couple of words starting with "x"). The VP
    # picks by last letter, and returning None there makes vp_answer() force-skip
    # the turn, so widen to the full list before giving up. Callers that pass no
    # pool, or the full pool, are unaffected.
    if pool and pool != DEFAULT_POOL:
        return get_random_word(
            min_len=min_len,
            prefix=prefix,
            required_letter=required_letter,
            banned_letters=banned_letters,
            exclude_words=exclude_words,
            max_len=max_len,
        )
    return None


async def get_random_word_async(**kwargs) -> Optional[str]:
    """Coroutine wrapper around get_random_word that keeps the event loop free.

    The DAWG walk is pure-Python in the filter loop, so the GIL still applies,
    but the await point lets the dispatcher interleave other updates between
    word picks instead of serialising every concurrent game start into one
    long stall. The prefixed variant is already ~0.1ms and is run inline to
    avoid pointless thread hand-off.
    """
    if kwargs.get("prefix"):
        return get_random_word(**kwargs)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: get_random_word(**kwargs))

