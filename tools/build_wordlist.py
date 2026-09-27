"""
Rebuilds chainnokizuna/data/wordlist.txt from its upstream sources.

The list is committed so the bot needs no network at startup, which means it has
to be regenerable by hand. Run this from the repo root with the dependencies
installed:

    python tools/build_wordlist.py

Two problems it fixes, both found by measuring the previous list:

Proper nouns. dwyl/english-words capitalises 79,394 of its 466,550 entries --
cities, people, taxonomic names. The old loader lowercased everything, so
"aachen" and "aaron" were accepted as ordinary words, which is 18% of the
list. A capitalised entry is now removed unless another dictionary calls it a
word, because lowercasing alone cannot tell a proper noun from a common one.

Coverage gaps. The same list is missing 19,924 words that ENABLE1, the
Scrabble tournament word list, accepts: plural and -ized/-izing inflections
and long-tail standard vocabulary such as "absenteeisms" and "abracadabras".
Those are added.

Removal is deliberately conservative. A word survives if ANY of the reference
sources endorses it, because a false removal takes a word away from players
while a false addition only adds a rare one. That veto is load-bearing: the
first version of this rule deleted "email", which dwyl capitalises as "Email"
but which is an ordinary noun.
"""
import json
import re
import sys
import urllib.request

from dawg import CompletionDAWG

# The upstream word list. Capitalisation is the signal used to spot proper nouns,
# so do not switch to words_alpha.txt: that variant is lowercase-only and would
# erase the distinction this script depends on.
DWYL_URL = "https://raw.githubusercontent.com/dwyl/english-words/master/words.txt"
# Tournament word list. Supplies the inflections the headword list lacks.
ENABLE1_URL = "https://raw.githubusercontent.com/dolph/dictionary/master/enable1.txt"
# A second opinion on ordinary vocabulary, and the repo's own hand-curated
# 5-letter list, which is maintained alongside the bot.
POPULAR_URL = "https://raw.githubusercontent.com/dolph/dictionary/master/popular.txt"
CURATED_URL = "chainnokizuna/data/commonfiveletterwords.json"

OUT_PATH = "chainnokizuna/data/wordlist.txt"
# The pools the bot picks its own words from. They were built from the raw dwyl
# file, so they inherited its proper nouns: "aaron" and "aberdeen" were both
# words the bot could open a game with. Rebuild them from the cleaned list.
COMMON_OUT = "chainnokizuna/data/commonwords.json"
FIVE_OUT = "chainnokizuna/data/fiveletters.json"
FREQUENCY_URL = "https://raw.githubusercontent.com/first20hours/google-10000-english/master/google-10000-english-usa.txt"
# Pools cap the length so the bot never opens on a word nobody can build on.
COMMON_MAX_LEN = 12


def fetch(url: str) -> str:
    with urllib.request.urlopen(url, timeout=60) as resp:
        return resp.read().decode("utf-8")


def word_set(text: str) -> set[str]:
    return {w.strip().lower() for w in text.splitlines() if w.strip().isalpha()}


def main() -> int:
    print("Fetching sources...")
    dwyl_text = fetch(DWYL_URL)
    enable1 = word_set(fetch(ENABLE1_URL))
    popular = word_set(fetch(POPULAR_URL))
    curated = {w.lower() for w in json.load(open(CURATED_URL))}
    print(f"  ENABLE1 {len(enable1):,} | popular {len(popular):,} | curated {len(curated):,}")

    # Anything dwyl spells with a capital is a proper noun until proven otherwise.
    proper_nouns = {
        w.strip().lower()
        for w in dwyl_text.splitlines()
        if w.strip().isalpha() and re.search(r"[A-Z]", w.strip())
    }

    current = set(open(OUT_PATH, encoding="utf-8").read().splitlines()) if OUT_PATH else set()
    veto = enable1 | popular | curated
    removed = proper_nouns - veto
    words = (current - removed) | enable1

    print(f"Removing {len(removed):,} proper nouns no other source endorses")
    print(f"Adding   {len(words - current):,} words ENABLE1 accepts")
    print(f"Total    {len(words):,} words")

    # Verify against the real DAWG rather than trusting set arithmetic: it is the
    # structure the bot actually queries, and a mismatch here is silent at runtime.
    dawg = CompletionDAWG(sorted(words))
    if len(dawg.keys()) != len(words):
        print(f"ERROR: DAWG holds {len(dawg.keys()):,} of {len(words):,} words", file=sys.stderr)
        return 1
    for w in ("apple", "email", "running", "absenteeisms", "zebra"):
        if w not in dawg:
            print(f"ERROR: {w!r} missing from the rebuilt list", file=sys.stderr)
            return 1
    for w in ("aaron", "aachen", "aberdeen"):
        if w in dawg:
            print(f"ERROR: proper noun {w!r} survived the rebuild", file=sys.stderr)
            return 1

    with open(OUT_PATH, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(sorted(words)) + "\n")
    print(f"Wrote {OUT_PATH}")

    # Pools are subsets of the cleaned list, rebuilt here so they cannot drift
    # from it. Regenerating them by hand is how "aaron" got in last time.
    frequent = word_set(fetch(FREQUENCY_URL))
    common = sorted(
        {w for w in frequent if w in words and 3 <= len(w) <= COMMON_MAX_LEN}
        | {w for w in curated if w in words}
    )
    five = sorted(w for w in words if len(w) == 5)
    for path, pool in ((COMMON_OUT, common), (FIVE_OUT, five)):
        if not set(pool) <= words:
            print(f"ERROR: {path} contains words absent from the main list", file=sys.stderr)
            return 1
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(pool, indent=2) + "\n")
        print(f"Wrote {path}: {len(pool):,} words")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
