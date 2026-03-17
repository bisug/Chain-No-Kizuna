import asyncio
import time

from aiogram import Router, types
from aiogram.enums import ParseMode
from aiogram.filters import Command, CommandObject

from chainnokizuna.filters import IsOwner
from chainnokizuna.core.resources import get_db, bot
from chainnokizuna.utils.telegram import awaitable_to_coroutine, send_admin_group
from chainnokizuna.services.words import check_word_existence, is_word, Words
from config import WORD_ADDITION_CHANNEL_ID

router = Router(name=__name__)


@router.message(Command("exist", "exists"))
async def cmd_exists(message: types.Message) -> None:
    """Checks if a specific word is present in the bot's DAWG dictionary."""
    word = message.text.partition(" ")[2].lower()
    if not word or not is_word(word):  # No proper argument given
        rmsg = message.reply_to_message
        if not rmsg or not rmsg.text or not is_word(rmsg.text.lower()):
            await message.reply(
                "Function: Check if a word is in my dictionary. "
                "Use /reqaddword if you want to request addition of new words.\n"
                "Usage: <code>/exists word</code>"
            )
            return
        word = rmsg.text.lower()

    await message.reply(
        f"<i>{word.capitalize()}</i> is <b>{'' if check_word_existence(word) else 'not '}in</b> my dictionary.",
        parse_mode=ParseMode.HTML
    )


@router.message(Command("reqaddword", "reqaddwords"))
async def cmd_reqaddword(message: types.Message, command: CommandObject) -> None:
    """Processes user requests to add new words to the bot's dictionary."""
    if message.forward_from:
        return

    args = command.args
    if args and (words_to_add := [w for w in set(args.lower().split()) if is_word(w)]):
        pass  # ok
    else:
        await message.reply(
            "Function: Request new words. Check @SuMelodyVibes for word list updates.\n"
            "Before requesting a new word, please check that:\n"
            "• It appears in a credible English dictionary "
            "(\u2714\ufe0f Merriam-Webster \u274c Urban Dictionary)\n"
            "• It is not a <a href='https://simple.wikipedia.org/wiki/Proper_noun'>proper noun</a> "
            "(\u274c names)\n"
            "  (existing proper nouns in the word list and nationalities are exempt)\n"
            "Invalid words will delay the processing of submissions.\n"
            "Usage: <code>/reqaddword word1 word2 ...</code>"
        )
        return

    existing = []
    rejected = []
    rejected_with_reason = []
    for w in words_to_add[:]:  # Iterate through a copy so removal of elements is possible
        if check_word_existence(w):
            existing.append(f"<i>{w.capitalize()}</i>")
            words_to_add.remove(w)

    db = get_db()
    cursor = db.wordlist.find({"accepted": False})
    async for row in cursor:
        word, reason = row["word"], row.get("reason")
        if word not in words_to_add:
            continue
        words_to_add.remove(word)
        word = f"<i>{word.capitalize()}</i>"
        if reason:
            rejected_with_reason.append((word, reason))
        else:
            rejected.append(word)

    text = ""
    if words_to_add:
        text += f"Submitted {', '.join([f'<i>{w.capitalize()}</i>' for w in words_to_add])} for approval.\n"

        name = message.from_user.full_name

        asyncio.create_task(
            send_admin_group(
                message.from_user.mention_html(
                    name=name
                )
                + " is requesting the addition of "
                + ", ".join([f"<i>{w.capitalize()}</i>" for w in words_to_add])
                + " to the word list. #reqaddword",
                parse_mode=ParseMode.HTML
            )
        )
    if existing:
        text += f"{', '.join(existing)} {'is' if len(existing) == 1 else 'are'} already in the word list.\n"
    if rejected:
        text += f"{', '.join(rejected)} {'was' if len(rejected) == 1 else 'were'} rejected.\n"
    for word, reason in rejected_with_reason:
        text += f"{word} was rejected. Reason: {reason}.\n"
    await message.reply(text, parse_mode=ParseMode.HTML)


@router.message(IsOwner(), Command("addword", "addwords"))
async def cmd_addwords(message: types.Message, command: CommandObject) -> None:
    """Allows the bot owner to immediately add and approve new words."""
    args = command.args
    if args and (words_to_add := [w for w in set(args.lower().split()) if is_word(w)]):
        pass  # ok
    else:
        await message.reply("where words")
        return

    existing = []
    rejected = []
    rejected_with_reason = []
    for w in words_to_add[:]:  # Cannot iterate while deleting
        if check_word_existence(w):
            existing.append(f"<i>{w.capitalize()}</i>")
            words_to_add.remove(w)

    db = get_db()
    cursor = db.wordlist.find({"accepted": False})
    async for row in cursor:
        word, reason = row["word"], row.get("reason")
        if word not in words_to_add:
            continue
        words_to_add.remove(word)
        word = f"<i>{word.capitalize()}</i>"
        if reason:
            rejected_with_reason.append((word, reason))
        else:
            rejected.append(word)

    text = ""
    if words_to_add:
        from pymongo import UpdateOne
        operations = [
            UpdateOne({"word": w}, {"$set": {"accepted": True, "reason": None}}, upsert=True)
            for w in words_to_add
        ]
        await db.wordlist.bulk_write(operations)
        text += f"Added {', '.join([f'<i>{w.capitalize()}</i>' for w in words_to_add])} to the word list.\n"
    if existing:
        text += f"{', '.join(existing)} {'is' if len(existing) == 1 else 'are'} already in the word list.\n"
    if rejected:
        text += f"{', '.join(rejected)} {'was' if len(rejected) == 1 else 'were'} rejected.\n"
    for word, reason in rejected_with_reason:
        text += f"{word} was rejected. Reason: {reason}.\n"
    msg = await message.reply(text, parse_mode=ParseMode.HTML)

    if not words_to_add:
        return

    t = time.time()
    await Words.update()
    asyncio.create_task(
        awaitable_to_coroutine(msg.edit_text(
            msg.text + f"\n\nWord list updated. Time taken: <code>{time.time() - t:.3f}s</code>",
            parse_mode=ParseMode.HTML
        ))
    )
    asyncio.create_task(
        bot.send_message(
            WORD_ADDITION_CHANNEL_ID,
            f"Added {', '.join([f'<i>{w.capitalize()}</i>' for w in words_to_add])} to the word list.",
            disable_notification=True,
            parse_mode=ParseMode.HTML
        )
    )


@router.message(IsOwner(), Command("rejword"))
async def cmd_rejword(message: types.Message, command: CommandObject) -> None:
    """Allows the bot owner to reject a word request with an optional reason."""
    args = command.args
    if not args:
        return

    word, _, reason = args.partition(" ")
    if not word:
        return
    word = word.lower()
    reason = reason.strip()

    db = get_db()
    r = await db.wordlist.find_one({"word": word})
    if r is None:
        await db.wordlist.update_one(
            {"word": word},
            {"$set": {"accepted": False, "reason": reason or None}},
            upsert=True
        )

    word = word.capitalize()
    if r is None:
        await message.reply(f"<i>{word}</i> rejected.", parse_mode=ParseMode.HTML)
    elif r.get("accepted"):
        await message.reply(f"<i>{word}</i> was accepted.", parse_mode=ParseMode.HTML)
    elif not r.get("reason"):
        await message.reply(f"<i>{word}</i> was already rejected.", parse_mode=ParseMode.HTML)
    else:
        await message.reply(f"<i>{word}</i> was already rejected. Reason: <code>{html.quote(r['reason'])}</code>.", parse_mode=ParseMode.HTML)
