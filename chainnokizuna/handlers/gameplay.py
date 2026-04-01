import asyncio
import re
from typing import Type

from aiogram.filters import Command, CommandObject

from chainnokizuna.core.resources import GlobalState, vp_bot
from config import GameState, VIP, VIP_GROUP, UPDATE_CHANNEL
from chainnokizuna.utils.decorators import send_groups_only_message
from chainnokizuna.filters import HasGameInstance, IsAdmin, IsOwner
from chainnokizuna.models import ClassicGame, EliminationGame, GAME_MODES, MixedEliminationGame, GuessTheWordGame
from aiogram import Router, types, Bot
from aiogram.enums import ParseMode

gameplay_router = Router(name=__name__)


@send_groups_only_message
async def start_game(message: types.Message, game_type: Type[ClassicGame]) -> None:
    """
    Initializes and starts a new game of the specified type in a group.
    Ensures group-specific constraints (slow mode, maintenance) are met.
    """
    group_id = message.chat.id
    if group_id in GlobalState.games:
        # There is already a game running in the group
        await GlobalState.games[group_id].join(message)
        return

    if GlobalState.maint_mode:
        # Only stop people from starting games, not joining
        await message.reply(
            "<b>🛠 Maintenance mode is on.</b> Games are temporarily disabled.\n"
            "This is likely due to a pending bot update.",
            parse_mode=ParseMode.HTML
        )
        return

    chat = await message.bot.get_chat(message.chat.id)
    if chat.slow_mode_delay:
        await message.reply(
            "<b>⚠️ Slow mode is enabled</b> in this group, so the bot cannot function properly.\n"
            "If you are a group admin, please disable slow mode to start games.",
            parse_mode=ParseMode.HTML
        )
        return

    if (
        game_type is MixedEliminationGame and message.chat.id not in VIP_GROUP
        and message.from_user.id not in VIP
    ):
        await message.reply(
            "<b>🚫 This game mode is a restricted reward.</b>\n"
            f"You can try this game mode at the <a href='https://t.me/{UPDATE_CHANNEL}'>official group</a>.",
            parse_mode=ParseMode.HTML
        )
        return

    async with GlobalState.games_lock:  # Avoid duplicate game creation
        if group_id in GlobalState.games:
            asyncio.create_task(GlobalState.games[group_id].join(message))
        else:
            game = game_type(message.chat.id)
            GlobalState.games[group_id] = game
            asyncio.create_task(game.main_loop(message))


@gameplay_router.message(Command(re.compile(r"^(start[a-z]+)$")))
async def cmd_startgame(message: types.Message, command: CommandObject) -> None:
    """Dynamic handler for all registered /start<mode> commands."""
    command_name = command.command.lower()
    if command_name == "startgame":
        await start_game(message, ClassicGame)
        return

    for mode in GAME_MODES:
        if mode.command == command_name:
            await start_game(message, mode)
            return

    await message.reply("Unknown game mode. Use <code>/gameinfo</code> for available modes.", parse_mode=ParseMode.HTML)


@gameplay_router.message(Command("new"))
async def cmd_new(message: types.Message) -> None:
    await start_game(message, GuessTheWordGame)


@gameplay_router.message(Command("end"), IsAdmin(), HasGameInstance())
async def cmd_end(message: types.Message) -> None:
    await cmd_killgame(message, CommandObject(command="end", args=str(message.chat.id)))


@gameplay_router.message(Command("join"))
@send_groups_only_message
async def cmd_join(message: types.Message) -> None:
    group_id = message.chat.id
    if group_id in GlobalState.games:
        await GlobalState.games[group_id].join(message)


@gameplay_router.message(Command("forcejoin"), IsOwner(), HasGameInstance())
async def cmd_forcejoin(message: types.Message) -> None:
    group_id = message.chat.id
    rmsg = message.reply_to_message
    if rmsg and rmsg.from_user.is_bot:  # Virtual Player only
        if vp_bot and rmsg.from_user.id == vp_bot.id:
            await cmd_addvp(message)
        return
    await GlobalState.games[group_id].forcejoin(message)


@gameplay_router.message(Command("extend"), HasGameInstance())
async def cmd_extend(message: types.Message) -> None:
    await GlobalState.games[message.chat.id].extend(message)


@gameplay_router.message(Command("forcestart"), IsAdmin(), HasGameInstance())
async def cmd_forcestart(message: types.Message) -> None:
    group_id = message.chat.id
    if GlobalState.games[group_id].state == GameState.JOINING:
        GlobalState.games[group_id].time_left = -99999


@gameplay_router.message(Command("flee"), HasGameInstance())
async def cmd_flee(message: types.Message) -> None:
    await GlobalState.games[message.chat.id].flee(message)


@gameplay_router.message(Command("forceflee"), IsOwner(), HasGameInstance())
async def cmd_forceflee(message: types.Message) -> None:
    await GlobalState.games[message.chat.id].forceflee(message)


@gameplay_router.message(Command("killgame", "killgaym"), IsOwner())
async def cmd_killgame(message: types.Message, command: CommandObject) -> None:
    """Forced termination of any active game by the bot owner."""
    try:
        group_id = int(command.args or message.chat.id)
        assert group_id < 0, "Invalid group ID"
        assert group_id in GlobalState.games, "no game running"
    except (ValueError, AssertionError) as e:
        await message.reply(f"<code>{e.__class__.__name__}: {e}</code>", parse_mode=ParseMode.HTML)
        return

    GlobalState.games[group_id].state = GameState.KILLGAME
    await asyncio.sleep(2)

    # If game is still not terminated
    if group_id in GlobalState.games:
        del GlobalState.games[group_id]
        await message.reply("Game ended forcibly.")


@gameplay_router.message(Command("forceskip"), IsOwner(), HasGameInstance())
async def cmd_forceskip(message: types.Message) -> None:
    group_id = message.chat.id
    if GlobalState.games[group_id].state == GameState.RUNNING and not GlobalState.games[group_id].answered:
        GlobalState.games[group_id].time_left = 0


@gameplay_router.message(Command("addvp"), HasGameInstance())
async def cmd_addvp(message: types.Message) -> None:
    group_id = message.chat.id
    if vp_bot is None:
        await message.reply("Virtual player bot is not enabled (missing token).")
        return
    vp_user = GlobalState.vp_user
    if isinstance(GlobalState.games[group_id], EliminationGame):
        await message.reply(
            f"Sorry, <a href='https://t.me/{vp_user.username}'>{vp_user.full_name}</a> "
            "can't play elimination games."
        )
        return
    await GlobalState.games[group_id].addvp(message)


@gameplay_router.message(Command("remvp"), HasGameInstance())
async def cmd_remvp(message: types.Message) -> None:
    if vp_bot is None:
        await message.reply("Virtual player bot is not enabled (missing token).")
        return
    await GlobalState.games[message.chat.id].remvp(message)




@gameplay_router.message(HasGameInstance())
@gameplay_router.edited_message(HasGameInstance())
async def answer_handler(message: types.Message, bot: Bot) -> None:
    """
    Global message handler for capturing game answers.
    Filters out non-playing bots and validates that the sender is the current turn holder.
    """
    # 1. Prevent double handling: only main bot processes message connection
    if bot.id != GlobalState.bot_user.id:
        return

    # 2. Skip non-text or invalid pattern messages
    if message.text is None or not re.match(r"^[a-zA-Z]{1,100}$", message.text):
        return

    # 3. Handle bot messages
    if message.from_user.is_bot:
        # Only allow the VP bot to participate as a bot
        if not (GlobalState.vp_user and message.from_user.id == GlobalState.vp_user.id):
            return

    game = GlobalState.games[message.chat.id]
    if (
        (game.players_in_game or game.allow_any_player_answer)
        and (game.allow_any_player_answer or (game.players_in_game and message.from_user.id == game.players_in_game[0].user_id))
        and not game.answered
        and game.accepting_answers
    ):
        await game.handle_answer(message)
