import asyncio
import time
from datetime import datetime, timezone

from aiogram import Router, F, types, html
from aiogram.enums import ChatType, ParseMode
from aiogram.filters import Command, CommandStart
from aiogram.utils.deep_linking import create_start_link

from chainnokizuna.core.resources import GlobalState
from config import GameState, SUPPORT_GROUP, UPDATE_CHANNEL
from chainnokizuna.utils.keyboards import inline_keyboard_from_button
from chainnokizuna.utils.decorators import send_private_only_message
from chainnokizuna.filters import IsOwner
from chainnokizuna.services.words import Words

router = Router(name=__name__)


@router.message(CommandStart(deep_link=True, magic=F.args == "help"), F.chat.type == ChatType.PRIVATE)
@router.message(Command("help"))
async def cmd_help(message: types.Message) -> None:
    if message.chat.id < 0:
        start_link = await create_start_link(message.bot, "help")
        await message.reply(
            "Please use this command in private.",
            reply_markup=inline_keyboard_from_button(
                types.InlineKeyboardButton(text="📨 Get Help", url=start_link)
            )
        )
        return

    bot_user = await message.bot.me()
    await message.reply(
        f"<b>⛓️ {html.quote(bot_user.full_name)}</b>\n\n"
        "Connect words, one letter at a time!\n\n"
        "<b>📚 Information</b>\n"
        "/gameinfo - Detailed game modes\n"
        "/troubleshoot - Fix common issues\n"
        "/stats - View statistics\n"
        "/ping - Check bot status\n\n"
        "<b>🛠️ Utilities</b>\n"
        "/reqaddword - Suggest new words\n"
        "/feedback - Contact developer\n\n"
        "<i>Select an option below for more resources:</i>",
        parse_mode=ParseMode.HTML,
        reply_markup=types.InlineKeyboardMarkup(inline_keyboard=[
            [
                types.InlineKeyboardButton(text="Updates", url=f"https://t.me/{UPDATE_CHANNEL}"),
                types.InlineKeyboardButton(text="Support Chat", url=f"https://t.me/{SUPPORT_GROUP}"),
            ]
        ]),
        disable_web_page_preview=True
    )


@router.message(Command("gameinfo"))
@send_private_only_message
async def cmd_gameinfo(message: types.Message) -> None:
    await message.reply(
        "<b>🎮 Game Modes Information</b>\n\n"
        "<b>/startclassic</b> - Classic game\n"
        "Players take turns to send words starting with the last letter of the previous word.\n\n"
        "<b>Variants:</b>\n"
        "<b>/starthard</b> - Hard mode game\n"
        "<b>/startchaos</b> - Chaos game (random turn order)\n"
        "<b>/startcfl</b> - Chosen first letter game\n"
        "<b>/startrfl</b> - Random first letter game\n"
        "<b>/startbl</b> - Banned letters game\n"
        "<b>/startrl</b> - Required letter game\n\n"
        "<b>/startelim</b> - Elimination game\n"
        "Each player's score is their cumulative word length. "
        "The lowest scoring players are eliminated after each round.\n\n"
        "<b>/startmelim</b> - Mixed elimination game (restricted reward)\n"
        f"Elimination game with different modes. Try at the <a href='https://t.me/{UPDATE_CHANNEL}'>official group</a>.",
        parse_mode=ParseMode.HTML
    )


@router.message(Command("troubleshoot"))
@send_private_only_message
async def cmd_troubleshoot(message: types.Message) -> None:
    await message.reply(
        (
            "These steps assume you have admin privileges. "
            "If you do not, please ask a group admin to check instead.\n\n"
            "<b>If the bot does not respond to <code>/start[mode]</code></b>, check if:\n"
            "1. The bot is absent from / muted in your group "
            "\u27a1\ufe0f Add the bot to your group / Unmute the bot\n"
            "2. Slow mode is enabled \u27a1\ufe0f Disable slow mode\n"
            "3. Someone spammed commands in your group recently "
            "\u27a1\ufe0f The bot is rate limited in your group, wait patiently\n"
            "4. The bot does not respond to <code>/ping</code> "
            f"\u27a1\ufe0f The bot is likely offline, check @{UPDATE_CHANNEL} for status updates\n\n"
            "<b>If the bot cannot be added to your group</b>:\n"
            "1. There can be at most 20 bots in a group. Check if this limit is reached.\n\n"
            f"If you encounter other issues, please contact <a href='https://t.me/{SUPPORT_GROUP}'>Support</a>."
        ),
        parse_mode=ParseMode.HTML,
    )


@router.message(Command("ping"))
async def cmd_ping(message: types.Message) -> None:
    start = time.time()
    msg = await message.reply("Pong!")
    end = time.time()
    
    try:
        import psutil
        cpu = psutil.cpu_percent()
        mem = psutil.virtual_memory().percent
        sys_info = f"\nCreate Time: <code>{end - start:.3f}s</code>\nCPU: <code>{cpu}%</code>\nRAM: <code>{mem}%</code>"
    except ImportError:
        sys_info = f" <code>{end - start:.3f}s</code>"

    await msg.edit_text(f"Pong!{sys_info}", parse_mode=ParseMode.HTML)


@router.message(Command("chatid"))
async def cmd_chatid(message: types.Message) -> None:
    await message.reply(f"<code>{message.chat.id}</code>", parse_mode=ParseMode.HTML)


@router.message(Command("runinfo"))
async def cmd_runinfo(message: types.Message) -> None:
    build_time_str = (
        "{0.day}/{0.month}/{0.year}".format(GlobalState.build_time)
        + " "
        + str(GlobalState.build_time.time())
        + " HKT"
    )
    uptime = datetime.now(timezone.utc).replace(microsecond=0) - GlobalState.build_time
    await message.reply(
        f"Build time: <code>{build_time_str}</code>\n"
        f"Uptime: <code>{uptime.days}.{str(uptime).rsplit(maxsplit=1)[-1]}</code>\n"
        f"Words in dictionary: <code>{Words.count}</code>\n"
        f"Total games: <code>{len(GlobalState.games)}</code>\n"
        f"Running games: <code>{len([g for g in GlobalState.games.values() if g.state == GameState.RUNNING])}</code>\n"
        f"Players: <code>{sum(len(g.players) for g in GlobalState.games.values())}</code>",
        parse_mode=ParseMode.HTML
    )


@router.message(IsOwner(), Command("playinggroups"))
async def cmd_playinggroups(message: types.Message) -> None:
    if not GlobalState.games:
        await message.reply("No groups are playing games.")
        return

    # TODO: return and gather the result instead of doing append
    groups = []

    async def append_group(group_id: int) -> None:
        try:
            group = await message.bot.get_chat(group_id)
        except Exception as e:
            text = f"<code>[{e.__class__.__name__}: {e}]</code>"
        else:
            if group.username:
                text = f"<a href='https://t.me/{group.username}'>{html.quote(group.title)}</a>"
            else:
                text = f"<b>{group.title}</b>"

        if group_id not in GlobalState.games:  # In case the game ended during API calls
            return

        game = GlobalState.games[group_id]
        groups.append(
            text + (
                f" <code>{group_id}</code> "
                f"{len(game.players_in_game)}/{len(game.players)}P "
                f"{game.turns}W {game.time_left}s"
            )
        )

    await asyncio.gather(*[append_group(gid) for gid in GlobalState.games])
    await message.reply("\n".join(groups), parse_mode=ParseMode.HTML)
