import asyncio
import logging
from uuid import uuid4

from aiogram import Router, F, types, html
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import JOIN_TRANSITION, ChatMemberUpdatedFilter, Command, CommandObject, CommandStart

from chainnokizuna.core.resources import GlobalState, get_db
from config import ADMIN_GROUP_ID, OFFICIAL_GROUP_ID, VIP, OWNER_ID, SUPPORT_GROUP, UPDATE_CHANNEL
from chainnokizuna.filters import IsOwner
from chainnokizuna.models import GAME_MODES
from chainnokizuna.utils.keyboards import get_add_to_group_keyboard
from chainnokizuna.utils.telegram import awaitable_to_coroutine
from chainnokizuna.services.words import is_word
from chainnokizuna.services.words import Words

logger = logging.getLogger(__name__)

router = Router(name=__name__)


@router.message(CommandStart(), F.chat.type == ChatType.PRIVATE)
async def cmd_start(message: types.Message) -> None:
    bot_user = await message.bot.me()
    username = bot_user.username

    await message.reply(
        (
            "<b>👋 Hello there!</b>\n\n"
            f"I am <b>{html.quote(bot_user.full_name)}</b>, a robust Word Chain bot for your groups.\n\n"
            "✨ <b>Features:</b>\n"
            "• Classic & Elimination Modes\n"
            "• Hard Mode & Chaos Mode\n"
            "• Global Leaderboards\n"
            "• Custom Word Lists\n\n"
            "<i>Add me to a group to start playing!</i>"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [
                types.InlineKeyboardButton(text="➕ Add me to your group", url=f"https://t.me/{username}?startgroup=_")
            ],
            [
                types.InlineKeyboardButton(text="Updates", url=f"https://t.me/{UPDATE_CHANNEL}"),
                types.InlineKeyboardButton(text="Support", url=f"https://t.me/{SUPPORT_GROUP}"),
            ],
            [
                types.InlineKeyboardButton(text="Owner", url=f"tg://user?id={OWNER_ID}"),
            ]
        ])
    )


@router.message(Command("feedback"))
async def cmd_feedback(message: types.Message, command: CommandObject) -> None:
    if message.forward_from:  # Avoid re-triggering on forward
        return

    args = command.args
    if not args:
        bot_user = await message.bot.me()
        await message.reply(
            "Function: Send feedback to my owner.\n"
            f"Usage: <code>/feedback@{bot_user.username} feedback</code>"
        )
        return

    await asyncio.gather(
        message.forward(ADMIN_GROUP_ID),
        message.reply("Feedback sent successfully.")
    )


@router.message(IsOwner(), Command("maintmode"))
async def cmd_maintmode(message: types.Message) -> None:
    GlobalState.maint_mode = not GlobalState.maint_mode
    await message.reply(
        f"Maintenance mode has been switched {'on' if GlobalState.maint_mode else 'off'}."
    )


@router.message(Command("leave"), IsOwner(), F.chat.type.in_((ChatType.GROUP, ChatType.SUPERGROUP)))
async def cmd_leave(message: types.Message) -> None:
    await message.chat.leave()


@router.message(IsOwner(), Command("mongo", "db"))
async def cmd_mongo(message: types.Message, command: CommandObject) -> None:
    args = command.args
    if not args:
        await message.reply("Usage: <code>/mongo collection.find({...})</code> or similar (limited to find/count)")
        return

    db = get_db()
    try:
        # A very primitive and unsafe way to execute commands for the owner
        # In a real app, this should be MUCH more restricted
        import orjson
        part = args.split(".", 1)
        if len(part) < 2:
            raise ValueError("Invalid format. Use collection.operation(...)")
        
        coll_name, rest = part
        op, _, params = rest.partition("(")
        params = params.rstrip(")")
        
        collection = db[coll_name]
        
        if op == "find":
            query = orjson.loads(params or "{}")
            cursor = collection.find(query)
            res = await cursor.to_list(length=20)
        elif op == "count":
            query = orjson.loads(params or "{}")
            res = await collection.count_documents(query)
        else:
            raise ValueError(f"Unsupported operation: {op}")

        await message.reply(f"<code>{html.quote(str(res))}</code>", parse_mode=ParseMode.HTML)
    except Exception as e:
        await message.reply(f"Error: <code>{html.quote(str(e))}</code>", parse_mode=ParseMode.HTML)


@router.chat_member(ChatMemberUpdatedFilter(JOIN_TRANSITION))
async def new_member(event: types.ChatMemberUpdated) -> None:
    bot = event.bot
    bot_user = await bot.me()
    
    if event.new_chat_member.user.id == bot.id:  # self added to group
        await event.answer(
            f"<b>✅ Thanks for adding {html.quote(bot_user.full_name)}!</b>\n\n"
            "To start a new game, use <code>/startclassic</code>.\n"
            "For a list of all game modes, try <code>/gameinfo</code>.\n\n"
            "<i>Let the chains begin!</i>",
            parse_mode=ParseMode.HTML
        )
    elif event.chat.id == OFFICIAL_GROUP_ID:
        await event.answer(
            f"<b>👋 Welcome to the official {html.quote(bot_user.full_name)} group!</b>\n\n"
            "We play word chain games here properly.\n"
            "Use <code>/startclassic</code> to start a round!",
            parse_mode=ParseMode.HTML
        )


@router.inline_query()
async def inline_handler(inline_query: types.InlineQuery) -> None:
    bot = inline_query.bot
    text = inline_query.query.lower()
    results: list[types.InlineQueryResultUnion] = []

    if not text or inline_query.from_user.id not in VIP:
        username = GlobalState.bot_user.username
        for mode in GAME_MODES:
            command = f"/{mode.command}@{username}"
            results.append(
                types.InlineQueryResultArticle(
                    id=str(uuid4()),
                    title="Start " + mode.name,
                    description=command,
                    input_message_content=types.InputTextMessageContent(message_text=command)
                )
            )
        await inline_query.answer(results, is_personal=not text)
        return

    if not is_word(text):
        await inline_query.answer(
            [
                types.InlineQueryResultArticle(
                    id=str(uuid4()),
                    title="A query can only consist of alphabets",
                    description="Try a different query",
                    input_message_content=types.InputTextMessageContent(message_text=r"¯\\_(ツ)\_/¯")
                )
            ],
            is_personal=True
        )
        return

    for word in Words.dawg.iterkeys(text):
        word = word.capitalize()
        results.append(
            types.InlineQueryResultArticle(
                id=str(uuid4()),
                title=word,
                input_message_content=types.InputTextMessageContent(message_text=word)
            )
        )
        if len(results) == 50:  # Max 50 results
            break

    if not results:  # No results
        results.append(
            types.InlineQueryResultArticle(
                id=str(uuid4()),
                title="No results found",
                description="Try a different query",
                input_message_content=types.InputTextMessageContent(message_text=r"¯\\_(ツ)\_/¯")
            )
        )

    await inline_query.answer(results, is_personal=True)


async def callback_query_handler(callback_query: types.CallbackQuery) -> None:
    await callback_query.answer()
