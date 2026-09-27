import time
from typing import Optional, Tuple

from aiogram import Router, types, html
from aiogram.enums import ParseMode
from aiogram.filters import Command

from chainnokizuna.core.resources import get_db
from chainnokizuna.filters import IsMainBot
from chainnokizuna.utils.decorators import send_groups_only_message
import math

router = Router(name=__name__)
router.message.filter(IsMainBot())

# Leaderboard paging: the total-count scan and deep skips are both linear in
# the number of winning players, so both are bounded rather than left to grow
# with the player base.
_LEADERBOARD_COUNT_TTL = 30  # seconds
_MAX_LEADERBOARD_PAGE = 100
_leaderboard_count_cache: Optional[int] = None
_leaderboard_total_at: float = 0.0


@router.message(Command("stat", "stats", "stalk"))
async def cmd_stats(message: types.Message) -> None:
    rmsg = message.reply_to_message
    origin = rmsg.forward_origin if rmsg else None
    # Bot API 7.0 replaced forward_from with forward_origin; only user origins expose a sender.
    original_sender = origin.sender_user if isinstance(origin, types.MessageOriginUser) else None
    user = original_sender or (rmsg.from_user if rmsg else None) or message.from_user

    name = user.full_name
    mention = user.mention_html(name=name)

    db = get_db()
    res = await db.players.find_one({"_id": user.id})

    if not res:
        await message.reply(
            f"No statistics for {mention}!",
            parse_mode=ParseMode.HTML
        )
        return

    # .get() for both: a document with game_count but no win_count would
    # otherwise raise KeyError past the guard.
    game_count = res.get("game_count", 0)
    win_count = res.get("win_count", 0)
    win_rate = (win_count / game_count * 100) if game_count > 0 else 0

    text = (
        f"📊 Statistics for {mention}:\n"
        f"<b>{game_count}</b> games played\n"
        f"<b>{win_count} ({win_rate:.0f}%)</b> games won\n"
        f"<b>{res.get('guess_word_wins', 0)}</b> Guess the Word wins\n"
        f"<b>{res.get('word_count', 0)}</b> total words played\n"
        f"<b>{res.get('letter_count', 0)}</b> total letters played"
    )
    if res.get("longest_word"):
        text += f"\nLongest word: <b>{res['longest_word'].capitalize()}</b>"
    await message.reply(text, parse_mode=ParseMode.HTML)


@router.message(Command("groupstats"))
@send_groups_only_message
async def cmd_groupstats(message: types.Message) -> None:
    db = get_db()

    # Game count is a plain $count, and player count a distinct on the indexed
    # sub-field, rather than one pipeline that built a set of every game _id and
    # every participant id just to $size them. Those sets grow without bound
    # with the group's history, so a busy group could allocate tens of MB per
    # request to produce four integers.
    game_cursor = db.games.aggregate([
        {"$match": {"group_id": message.chat.id}},
        {"$count": "game_cnt"},
    ])
    game_list = await game_cursor.to_list(length=1)

    if not game_list:
        await message.reply("No games have been played in this group yet.")
        return

    player_cursor = db.games.aggregate([
        {"$match": {"group_id": message.chat.id}},
        {"$unwind": "$participants"},
        {"$group": {
            "_id": None,
            "player_cnt": {"$addToSet": "$participants.user_id"},
            "word_cnt": {"$sum": "$participants.word_count"},
            "letter_cnt": {"$sum": "$participants.letter_count"},
        }},
        {"$project": {
            "player_cnt": {"$size": "$player_cnt"},
            "word_cnt": 1,
            "letter_cnt": 1,
        }},
    ])
    player_list = await player_cursor.to_list(length=1)
    res = player_list[0] if player_list else {}
    game_cnt = game_list[0]["game_cnt"]

    await message.reply(
        (
            f"\U0001f4ca Statistics for <b>{html.quote(message.chat.title)}</b>\n"
            f"<b>{res.get('player_cnt', 0)}</b> players\n"
            f"<b>{game_cnt}</b> games played\n"
            f"<b>{res.get('word_cnt', 0)}</b> total words played\n"
            f"<b>{res.get('letter_cnt', 0)}</b> total letters played"
        ),
        parse_mode=ParseMode.HTML
    )


_global_stats_cache: Optional[Tuple[float, str]] = None  # (timestamp, content)
_GLOBAL_STATS_TTL = 30  # seconds

async def get_global_stats() -> str:
    global _global_stats_cache
    now = time.time()
    
    if _global_stats_cache and (now - _global_stats_cache[0]) < _GLOBAL_STATS_TTL:
        return _global_stats_cache[1]

    db = get_db()

    # Get counts from games and players collections
    group_cnt = len(await db.games.distinct("group_id"))
    game_cnt = await db.games.count_documents({})
    player_cnt = await db.players.count_documents({})
    
    # Sum words and letters from players collection
    pipeline = [
        {"$group": {
            "_id": None,
            "word_cnt": {"$sum": "$word_count"},
            "letter_cnt": {"$sum": "$letter_count"}
        }}
    ]
    agg_res = await db.players.aggregate(pipeline).to_list(length=1)
    word_cnt = agg_res[0]["word_cnt"] if agg_res else 0
    letter_cnt = agg_res[0]["letter_cnt"] if agg_res else 0

    result = (
        "\U0001f4ca Global statistics\n"
        f"<b>{group_cnt}</b> groups\n"
        f"<b>{player_cnt}</b> players\n"
        f"<b>{game_cnt}</b> games played\n"
        f"<b>{word_cnt}</b> total words played\n"
        f"<b>{letter_cnt}</b> total letters played"
    )
    
    _global_stats_cache = (now, result)
    return result


@router.message(Command("globalstats"))
async def cmd_globalstats(message: types.Message) -> None:
    await message.reply(await get_global_stats())


@router.message(Command("topseekers", "leaderboard", "top"))
async def cmd_topseekers(message: types.Message) -> None:
    text, kb = await get_leaderboard_page(1)
    await message.reply(text, reply_markup=kb, parse_mode=ParseMode.HTML)


@router.callback_query(lambda c: c.data and c.data.startswith("topseekers:page:"))
async def topseekers_callback(callback: types.CallbackQuery) -> None:
    page = int(callback.data.split(":")[-1])
    text, kb = await get_leaderboard_page(page)
    
    try:
        await callback.message.edit_text(text, reply_markup=kb, parse_mode=ParseMode.HTML)
    except Exception:
        pass
    await callback.answer()


async def get_leaderboard_page(page: int) -> Tuple[str, types.InlineKeyboardMarkup]:
    db = get_db()
    limit = 10

    # total_count is a full scan of the winning players, and it was re-run on
    # every page click. Cache it briefly; the leaderboard moves slowly and a
    # slightly stale page count is harmless.
    global _leaderboard_count_cache, _leaderboard_total_at
    now = time.time()
    total_count = _leaderboard_count_cache
    if total_count is None or (now - _leaderboard_total_at) > _LEADERBOARD_COUNT_TTL:
        total_count = await db.players.count_documents({"guess_word_wins": {"$gt": 0}})
        _leaderboard_count_cache = total_count
        _leaderboard_total_at = now

    max_pages = max(1, math.ceil(total_count / limit))

    # Clamp page. Bounding the depth keeps a stale deep link from asking
    # Mongo for a large skip, which costs O(skip) even with an index.
    page = max(1, min(page, max_pages, _MAX_LEADERBOARD_PAGE))
    skip = (page - 1) * limit

    cursor = db.players.find({"guess_word_wins": {"$gt": 0}}).sort("guess_word_wins", -1).skip(skip).limit(limit)
    players = await cursor.to_list(length=limit)
    
    text = "🏆 <b>Elite Guess the Word Leaderboard</b>\n\n"
    if not players:
        text += "No seekers found yet. Be the first to win! /new"
    else:
        for i, p in enumerate(players, start=skip + 1):
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"<b>{i}.</b>"
            # Get user info if available, else use fallback
            # Try to get persisted name, fallback to User ID
            name = p.get("full_name") or f"User <code>{p['_id']}</code>"
            text += f"{medal} {name} — <b>{p['guess_word_wins']}</b> wins\n"

    # Navigation buttons
    buttons = []
    if page > 1:
        buttons.append(types.InlineKeyboardButton(text="⬅️ Back", callback_data=f"topseekers:page:{page-1}"))
    
    buttons.append(types.InlineKeyboardButton(text=f"Page {page}/{max_pages}", callback_data="noop"))
    
    if page < max_pages:
        buttons.append(types.InlineKeyboardButton(text="Next ➡️", callback_data=f"topseekers:page:{page+1}"))
    
    kb = types.InlineKeyboardMarkup(inline_keyboard=[buttons])
    return text, kb
