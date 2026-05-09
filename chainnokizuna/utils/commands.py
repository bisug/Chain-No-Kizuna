from aiogram import Bot, types
from aiogram.types import BotCommand

async def set_bot_commands(bot: Bot):
    commands = [
        BotCommand(command="startclassic", description="Start a classic word chain game"),
        BotCommand(command="starthard", description="Start a hard mode game"),
        BotCommand(command="startchaos", description="Start a chaos mode game (random turns)"),
        BotCommand(command="startcfl", description="Start a chosen first letter game"),
        BotCommand(command="startrfl", description="Start a random first letter game"),
        BotCommand(command="startbl", description="Start a banned letters game"),
        BotCommand(command="startrl", description="Start a required letter game"),
        BotCommand(command="startelim", description="Start an elimination game"),
        BotCommand(command="startmelim", description="Start a mixed elimination game"),
        BotCommand(command="new", description="Start a Guess the Word game"),
        BotCommand(command="join", description="Join an active game"),
        BotCommand(command="flee", description="Leave a game during joining phase"),
        BotCommand(command="stats", description="View your statistics"),
        BotCommand(command="groupstats", description="View statistics for the current group"),
        BotCommand(command="leaderboard", description="View global leaderboard"),
        BotCommand(command="help", description="Get help and information"),
        BotCommand(command="gameinfo", description="View detailed game modes info"),
        BotCommand(command="troubleshoot", description="Fix common bot issues"),
    ]

    await bot.set_my_commands(commands, scope=types.BotCommandScopeAllGroupChats())
    await bot.set_my_commands(commands, scope=types.BotCommandScopeAllPrivateChats())
