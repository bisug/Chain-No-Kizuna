import asyncio
import aiofiles
import logging
import random
from string import ascii_lowercase
from typing import Optional

from dawg import CompletionDAWG

from config import WORDLIST_SOURCE
from chainnokizuna.core.resources import get_db, get_session

logger = logging.getLogger(__name__)


class Words:
    """
    Manages the bot's word dictionary using a Directed Acyclic Word Graph (DAWG)
    for high-performance prefix lookups and existence checks.
    """
    dawg: CompletionDAWG = CompletionDAWG()
    count: int = 0

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
                async with aiofiles.open("chainnokizuna/data/words.txt", "r", encoding="utf-8") as f:
                    logger.info("Loading words from local cache.")
                    content = await f.read()
                    return content.splitlines()
            except FileNotFoundError:
                logger.error("No local wordlist cache found.")
                return []


        async def get_words_from_db() -> list[str]:
            db = get_db()
            cursor = db.wordlist.find({"accepted": True}, {"word": 1})
            return [row["word"] for row in await cursor.to_list(length=None)]

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

        logger.info("DAWG updated")

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
    exclude_words: Optional[set[str]] = None
) -> Optional[str]:
    """
    Retrieves a random word from the dictionary matching specific constraints.
    """
    if not Words.dawg:
        return None

    # Use DAWG prefix search if available
    iterator = Words.dawg.iterkeys(prefix) if prefix else Words.dawg.iterkeys()
    
    candidates = []
    
    for w in iterator:
        if len(w) < min_len:
            continue
        if required_letter and required_letter not in w:
            continue
        if banned_letters and any(i in w for i in banned_letters):
            continue
        if exclude_words and w in exclude_words:
            continue
        candidates.append(w)
        
    return random.choice(candidates) if candidates else None

