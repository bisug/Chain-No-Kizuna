from typing import Tuple

from aiogram import Router, types, html
from aiogram.enums import ParseMode
from aiogram.filters import Command

from chainnokizuna.core.resources import get_db
from chainnokizuna.utils.decorators import send_groups_only_message
import math

router = Router(name=__name__)


@router.message(Command("stat", "stats", "stalk"))
async def cmd_stats(message: types.Message) -> None:
    rmsg = message.reply_to_message
    user = (rmsg.forward_from or rmsg.from_user) if rmsg else message.from_user

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

    win_rate = (res['win_count'] / res['game_count'] * 100) if res.get('game_count', 0) > 0 else 0

    text = (
        f"📊 Statistics for {mention}:\n"
        f"<b>{res.get('game_count', 0)}</b> games played\n"
        f"<b>{res.get('win_count', 0)} ({win_rate:.0f}%)</b> games won\n"
        f"<b>{res.get('guess_word_wins', 0)}</b> Guess the Word wins\n"
        f"<b>{res['word_count']}</b> total words played\n"
        f"<b>{res['letter_count']}</b> total letters played"
    )
    if res.get("longest_word"):
        text += f"\nLongest word: <b>{res['longest_word'].capitalize()}</b>"
    await message.reply(text, parse_mode=ParseMode.HTML)


@router.message(Command("groupstats"))
@send_groups_only_message
async def cmd_groupstats(message: types.Message) -> None:
    db = get_db()
    pipeline = [
        {"$match": {"group_id": message.chat.id}},
        {"$unwind": "$participants"},
        {"$group": {
            "_id": "$group_id",
            "player_cnt": {"$addToSet": "$participants.user_id"},
            "game_cnt": {"$addToSet": "$_id"},
            "word_cnt": {"$sum": "$participants.word_count"},
            "letter_cnt": {"$sum": "$participants.letter_count"}
        }},
        {"$project": {
            "player_cnt": {"$size": "$player_cnt"},
            "game_cnt": {"$size": "$game_cnt"},
            "word_cnt": 1,
            "letter_cnt": 1
        }}
    ]

    res_cursor = db.games.aggregate(pipeline)
    res_list = await res_cursor.to_list(length=1)

    if not res_list:
        await message.reply("No games have been played in this group yet.")
        return

    res = res_list[0]
    await message.reply(
        (
            f"\U0001f4ca Statistics for <b>{html.quote(message.chat.title)}</b>\n"
            f"<b>{res['player_cnt']}</b> players\n"
            f"<b>{res['game_cnt']}</b> games played\n"
            f"<b>{res['word_cnt']}</b> total words played\n"
            f"<b>{res['letter_cnt']}</b> total letters played"
        ),
        parse_mode=ParseMode.HTML
    )



# Shared formatted stats string would ideally be cached in Redis now.

async def get_global_stats() -> str:
    from chainnokizuna.db.redis import get_redis_global_stats, incr_global_stats

    # 1. Try Redis Tier
    stats = await get_redis_global_stats()

    if not stats:
        # 2. Fallback to MongoDB (Tiered Discovery)
        db = get_db()
        group_cnt = len(await db.games.distinct("group_id"))
        game_cnt = await db.games.count_documents({})
        player_cnt = await db.players.count_documents({})

        pipeline = [{"$group": {"_id": None, "word_cnt": {"$sum": "$word_count"}, "letter_cnt": {"$sum": "$letter_count"}}}]
        agg_res = await db.players.aggregate(pipeline).to_list(length=1)
        word_cnt = agg_res[0]["word_cnt"] if agg_res else 0
        letter_cnt = agg_res[0]["letter_cnt"] if agg_res else 0

        # 3. Synchronize Redis for future callers
        try:
             await incr_global_stats(words=word_cnt, letters=letter_cnt, games=game_cnt)
             # Note: group_cnt and player_cnt aren't currently being updated in incr_global_stats,
             # but we'll include them in the formatted stats anyway.
        except Exception: pass
    else:
        word_cnt = stats.get("total_words", 0)
        letter_cnt = stats.get("total_letters", 0)
        game_cnt = stats.get("total_games", 0)
        # Fallback counts that aren't in Redis hash but cheap to get
        db = get_db()
        group_cnt = len(await db.games.distinct("group_id"))
        player_cnt = await db.players.count_documents({})

    return (
        "\U0001f4ca Global statistics\n"
        f"<b>{group_cnt}</b> groups\n"
        f"<b>{player_cnt}</b> players\n"
        f"<b>{game_cnt}</b> games played\n"
        f"<b>{word_cnt}</b> total words played\n"
        f"<b>{letter_cnt}</b> total letters played"
    )


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
    from chainnokizuna.db.redis import get_redis_leaderboard, get_leaderboard_total_count, update_leaderboard

    limit = 10
    skip = (page - 1) * limit

    # 1. Try Redis for total count and page data
    total_count = await get_leaderboard_total_count()
    redis_players = await get_redis_leaderboard(skip, limit)

    if not redis_players and total_count == 0:
        # 2. Redis is empty, fallback to MongoDB
        db = get_db()
        total_count = await db.players.count_documents({"guess_word_wins": {"$gt": 0}})
        cursor = db.players.find({"guess_word_wins": {"$gt": 0}}).sort("guess_word_wins", -1).skip(skip).limit(limit)
        players_data = await cursor.to_list(length=limit)

        # Populate leaderboard reconstruction
        normalized_players = []
        for p in players_data:
            name = p.get("full_name") or f"User <code>{p['_id']}</code>"
            normalized_players.append((name, p["guess_word_wins"]))
            # Seed Redis for next time
            try: await update_leaderboard(p["id"], p["guess_word_wins"])
            except Exception: pass
    else:
        # 3. Use Redis Data
        normalized_players = []
        for uid, wins in redis_players:
            # We still need the name for the UI, fetch from MongoDB (O(1))
            db = get_db()
            p_info = await db.players.find_one({"_id": int(uid)}, {"full_name": 1})
            name = p_info.get("full_name") if p_info else f"User <code>{uid}</code>"
            normalized_players.append((name, wins))

    max_pages = max(1, math.ceil(total_count / limit))
    page = max(1, min(page, max_pages))

    text = "🏆 <b>Elite Guess the Word Leaderboard</b>\n\n"
    if not normalized_players:
        text += "No seekers found yet. Be the first to win! /new"
    else:
        for i, (name, wins) in enumerate(normalized_players, start=skip + 1):
            medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"<b>{i}.</b>"
            text += f"{medal} {name} — <b>{wins}</b> wins\n"

    # Navigation buttons
    buttons = []
    if page > 1:
        buttons.append(types.InlineKeyboardButton(text="⬅️ Back", callback_data=f"topseekers:page:{page-1}"))

    buttons.append(types.InlineKeyboardButton(text=f"Page {page}/{max_pages}", callback_data="noop"))

    if page < max_pages:
        buttons.append(types.InlineKeyboardButton(text="Next ➡️", callback_data=f"topseekers:page:{page+1}"))

    kb = types.InlineKeyboardMarkup(inline_keyboard=[buttons])
    return text, kb
