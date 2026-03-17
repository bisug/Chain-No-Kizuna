import asyncio
import os
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Optional, Tuple

from aiogram import Router, types, html
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject

from chainnokizuna.core.resources import get_db, bot
from chainnokizuna.filters import IsOwner
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


# Simple in-memory cache
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
    skip = (page - 1) * limit
    
    # Get total count for pagination
    total_count = await db.players.count_documents({"guess_word_wins": {"$gt": 0}})
    max_pages = max(1, math.ceil(total_count / limit))
    
    # Clamp page
    page = max(1, min(page, max_pages))
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
