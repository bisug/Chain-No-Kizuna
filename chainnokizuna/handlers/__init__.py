from chainnokizuna.handlers import gameplay, info, misc, stats, wordlist

routers = [
    info.router,
    misc.router,
    stats.router,
    wordlist.router,
    gameplay.gameplay_router
]

__all__ = (
    "gameplay",
    "info",
    "misc",
    "stats",
    "wordlist",
    "routers"
)
