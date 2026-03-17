from chainnokizuna.handlers import gameplay, info, misc, stats, wordlist

routers = [
    gameplay.gameplay_router,
    info.router,
    misc.router,
    stats.router,
    wordlist.router
]

__all__ = (
    "gameplay",
    "info",
    "misc",
    "stats",
    "wordlist",
    "routers"
)
