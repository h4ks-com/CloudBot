import json
import math
import random
import re
import time
from datetime import datetime
from time import time

import sqlalchemy
from cachetools import TTLCache
from sqlalchemy import (
    Column,
    Integer,
    PrimaryKeyConstraint,
    String,
    Table,
    select,
)
from sqlalchemy.sql.base import Executable

from cloudbot import hook
from cloudbot.event import EventType
from cloudbot.util import database, web
from plugins.core.chan_track import get_users


def check_user_authenticated(conn, nick):
    """
    Check if a user is authenticated with services.
    Returns None if authenticated, error message if not.
    """
    if not nick or not conn:
        return "🚫 Unable to verify user authentication status. 🚫"

    try:
        user = get_users(conn).getuser(nick)
        if not user.account:
            return "🚫 This command requires you to be authenticated with services (e.g., NickServ). Please identify and try again. 🚫"
    except:
        return "🚫 Unable to verify your authentication status. 🚫"

    return None  # User is authenticated


# Regular expressions for bean commands
bean_add_re = re.compile(r"^\+(\d+)\s+beans?\s+to\s+(\S+)(?:\s+.*)?$", re.IGNORECASE)
bean_admin_add_re = re.compile(r"^\+\+(\d+)\s+beans?\s+to\s+(\S+)(?:\s+.*)?$", re.IGNORECASE)

# Database table for storing bean balances
beans_table = Table(
    "beans",
    database.metadata,
    Column("nick", String),
    Column("beans", Integer),
    PrimaryKeyConstraint("nick"),
)

# Database table for storing trivia questions
trivia_table = Table(
    "trivia",
    database.metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("timestamp", sqlalchemy.DateTime),
    Column("creator", String),
    Column("question", String),
    Column("answer", String),
    Column("prize", Integer),
)

# Database table for storing trivia bets
trivia_bets_table = Table(
    "trivia_bets",
    database.metadata,
    Column("creator", String),
    Column("trivia_id", Integer),
    Column("bet_amount", Integer),
    Column("winner", String),
    Column("timestamp", sqlalchemy.DateTime),
    PrimaryKeyConstraint("creator", "trivia_id"),
)


def get_beans(nick: str, db) -> int:
    """Get the current bean count for a user."""
    nick = nick.lower()
    beans = db.execute(select([beans_table.c.beans]).where(beans_table.c.nick == nick)).fetchone()

    if beans:
        return beans["beans"]

    return 0


def set_beans(nick: str, amount: int, db) -> None:
    """Set the bean count for a user."""
    nick = nick.lower()
    clause = beans_table.c.nick == nick
    beans = db.execute(select([beans_table.c.beans]).where(clause)).fetchone()
    query: Executable

    if beans:
        query = beans_table.update().values(beans=amount).where(clause)
    else:
        query = beans_table.insert().values(nick=nick, beans=amount)

    db.execute(query)
    db.commit()


def transfer_beans(from_nick: str, to_nick: str, amount: int, db) -> bool:
    """Transfer beans from one user to another."""
    from_nick = from_nick.lower()
    to_nick = to_nick.lower()

    # Get current bean counts
    from_beans = get_beans(from_nick, db)
    to_beans = get_beans(to_nick, db)

    # Check if sender has enough beans
    if from_beans < amount:
        return False

    # Update bean counts
    set_beans(from_nick, from_beans - amount, db)
    set_beans(to_nick, to_beans + amount, db)

    return True


def add_beans(nick: str, amount: int, db) -> None:
    """Add beans to a user (admin function)."""
    nick = nick.lower()
    current_beans = get_beans(nick, db)
    set_beans(nick, current_beans + amount, db)


@hook.command("beans", autohelp=False)
def beans_cmd(text: str, nick: str, db) -> str:
    """[user] - Check how many beans you or another user has."""
    if text:
        target = text.strip()
    else:
        target = nick

    beans = get_beans(target, db)
    return f"🌟 {target} has 🫘 {beans:,} beans! 🌟"


@hook.regex(bean_add_re)
def transfer_beans_cmd(match, nick: str, db, notice, event, conn) -> str | None:
    """<+amount beans to user> - Transfer beans to another user."""
    # Check if user is authenticated
    auth_error = check_user_authenticated(conn, nick)
    if auth_error:
        return auth_error

    amount = int(match.group(1))
    target = match.group(2)

    # Prevent negative transfers
    if amount <= 0:
        return "🚫 Amount must be positive! 🚫"

    # Check if target is a valid nick
    if not event.is_nick_valid(target.lower()):
        return "🚫 Invalid user! Please provide a valid IRC nickname. 🚫"

    # Prevent self-transfers
    if nick.lower() == target.lower():
        return "🤔 You can't transfer beans to yourself! 🤔"

    # Attempt the transfer
    success = transfer_beans(nick, target, amount, db)

    if success:
        sender_beans = get_beans(nick, db)
        target_beans = get_beans(target, db)
        return f"🎉 {nick} gave 🫘 {amount} beans to {target}! 🎉 {nick} now has 🫘 {sender_beans} beans, and {target} has 🫘 {target_beans} beans!"
    else:
        return f"😢 You don't have enough beans for that transfer. You have 🫘 {get_beans(nick, db)} beans. 😢"


@hook.regex(bean_admin_add_re)
def admin_add_beans(match, nick: str, db, notice, has_permission, event) -> str | None:
    """<++amount beans to user> - Admin command to create beans and give them to a user."""
    if not any(has_permission(per) for per in ["op", "botcontrol"]):
        notice("🚫 You don't have permission to use this command! 🚫")
        return None

    amount = int(match.group(1))
    target = match.group(2)

    # Check if target is a valid nick
    if not event.is_nick_valid(target.lower()):
        return "🚫 Invalid user! Please provide a valid IRC nickname. 🚫"

    # Prevent negative amounts
    if amount <= 0:
        return "🚫 Amount must be positive! 🚫"

    # Add beans to target
    add_beans(target, amount, db)
    target_beans = get_beans(target, db)

    return (
        f"✨ {nick} created 🫘 {amount} beans and gave them to {target}! ✨ {target} now has 🫘 {target_beans} beans!"
    )


def _generate_top_beans_response(top_n: int, db) -> str:
    """Helper function to generate the top beans response."""
    query = (
        select([beans_table.c.nick, beans_table.c.beans]).order_by(sqlalchemy.desc(beans_table.c.beans)).limit(top_n)
    )
    results = db.execute(query).fetchall()

    if not results:
        return "😢 No one has any beans yet! 😢"

    beans_list = [f"{i+1}. {row['nick']} 🫘 ({row['beans']:,} beans)" for i, row in enumerate(results)]
    return f"🏆 Top {top_n} Bean Holders: " + "\n".join(beans_list)


@hook.command("topbeans", "beanstats", autohelp=False)
def top_beans(text: str, nick: str, chan: str, db, notice, message) -> str | None:
    """[number] - Shows the top N users with the most beans (default is 10)."""
    try:
        top_n = int(text.strip()) if text else 10
    except ValueError:
        return "🚫 Please provide a valid number for the top users to display. 🚫"

    response = _generate_top_beans_response(top_n, db)
    if top_n <= 10:
        return response.replace("\n", " ")

    notice(f"📩 {nick}, check your DM for the top {top_n} bean holders!")
    # Loop 10 by 10 splits to generate chunks
    for i in range(0, len(response.splitlines()), 10):
        chunk = " ".join(response.splitlines()[i : i + 10])
        message(chunk, nick)


def get_total_beans(db) -> int:
    """Get the total number of beans in circulation."""
    query = select([sqlalchemy.func.sum(beans_table.c.beans).label("total_beans")])
    result = db.execute(query).fetchone()
    return result["total_beans"] if result["total_beans"] is not None else 0


@hook.command("totalbeans", autohelp=False)
def total_beans(db) -> str:
    """- Shows the total number of beans in circulation."""
    total_beans = get_total_beans(db)
    return f"🌍 There are 🫘 {total_beans:,} beans in circulation! 🌍"


@hook.command("exportbeans", autohelp=False)
def export_beans(db) -> str:
    """Export all bean balances as JSON file"""
    query = select([beans_table.c.nick, beans_table.c.beans]).order_by(beans_table.c.nick)
    results = db.execute(query).fetchall()

    if not results:
        return "❌ No bean data to export."

    data = [{"nick": row["nick"], "beans": row["beans"]} for row in results]
    json_data = json.dumps(data, indent=2)

    try:
        url = web.paste(json_data, ext="json", raise_on_no_paste=True)
        return f"📊 Bean data exported ({len(results)} users): {url}"
    except web.NoPasteException:
        return "❌ Failed to paste data to service."


slot_cooldown_cache = TTLCache(maxsize=1000, ttl=3600 * 24 * 2)  # Cache for slot cooldowns


@hook.command("slots", autohelp=False)
def slots(text: str, nick: str, chan: str, reply, db, conn) -> str:
    """[bet] - Play the slot machine! Default bet is 5 beans. Win big or lose it all!"""
    # Check if user is authenticated
    auth_error = check_user_authenticated(conn, nick)
    if auth_error:
        return auth_error

    emojis = ["🍒", "🍋", "🍉", "⭐", "🔔", "🍇", "🍊", "🍓"]

    total_beans = get_total_beans(db)
    bot_beans = get_beans(conn.nick, db)
    bot_market_share = bot_beans / total_beans if total_beans > 0 else 0

    min_bet = 3
    if bot_market_share > 0.3:
        min_bet = 2
    if bot_market_share > 0.5:
        min_bet = 1

    max_prize = 100

    # Cooldown settings
    attempts_per_cooldown = 3
    cooldown_time_base = 15  # seconds
    cooldown_bet_multiplier = 2
    # Is also multiplied by the bet multiplier
    cooldown_time_multiplier = 1.5

    # Determine bet amount
    try:
        bet = int(text.strip()) if text else min_bet
    except ValueError:
        return "Please provide a valid number for your bet."

    if bet < min_bet:
        return f"Minimum bet is {min_bet} beans."

    bet_multiplier = bet / min_bet

    # Check cooldown
    current_time = math.floor(time())
    if nick not in slot_cooldown_cache:
        slot_cooldown_cache[nick] = {
            "remaining_plays": attempts_per_cooldown,
            "cooldown_until": 0,
            "accumulated_bet": min_bet,
        }

    cooldown_entry = slot_cooldown_cache[nick]

    wait_time = cooldown_entry["cooldown_until"] - current_time
    cooldown_msg = f"⏳ You need to wait {wait_time} seconds before playing again. Increase your bet to {cooldown_entry['accumulated_bet']} to play now. �����"
    if wait_time > 0 and bet < cooldown_entry["accumulated_bet"]:
        return cooldown_msg

    # If not in cooldown, reset accumulated bet
    if wait_time <= 0:
        slot_cooldown_cache[nick] = {
            "remaining_plays": cooldown_entry["remaining_plays"],
            "cooldown_until": cooldown_entry["cooldown_until"],
            "accumulated_bet": min_bet,
        }
        cooldown_entry = slot_cooldown_cache[nick]

    # Update cooldown cache
    if cooldown_entry["remaining_plays"] > 0:
        slot_cooldown_cache[nick]["remaining_plays"] -= 1
    else:
        # User increased bet, accept it
        if bet > cooldown_entry["accumulated_bet"] and wait_time > 0:
            slot_cooldown_cache[nick] = {
                "remaining_plays": attempts_per_cooldown - 1,
                "cooldown_until": cooldown_entry["cooldown_until"],
                "accumulated_bet": cooldown_entry["accumulated_bet"],
            }
        # User did not increase bet, apply new cooldown
        else:
            cooldown_time = round(
                cooldown_time_base * cooldown_time_multiplier * cooldown_entry["accumulated_bet"] / min_bet
            )
            slot_cooldown_cache[nick] = {
                "remaining_plays": attempts_per_cooldown,
                "cooldown_until": current_time + cooldown_time,
                "accumulated_bet": cooldown_entry["accumulated_bet"] * cooldown_bet_multiplier,
            }
            return f"⏳ You entered a cooldown! You can play again in {cooldown_time:.2f} seconds. Increase your bet to {slot_cooldown_cache[nick]['accumulated_bet']} beans to play now ⏳"

    cooldown_entry = slot_cooldown_cache[nick]
    # reply(f"{cooldown_entry['accumulated_bet']=}")
    # reply(f"{cooldown_entry['remaining_plays']=}")
    # reply(f"{cooldown_entry['cooldown_until']=}")
    is_cooldown = wait_time > 0

    if is_cooldown:
        bet_multiplier = max(min(bet / min_bet, (bet) / (cooldown_entry["accumulated_bet"])), 1)

    user_beans = get_beans(nick, db)
    if user_beans < bet:
        return f"You don't have enough beans to bet {bet}. You only have {user_beans} beans."

    max_prize = math.ceil(bet_multiplier * max_prize)
    if bot_beans < max_prize:
        return (
            f"The bot doesn't have enough beans to pay out a potential prize of {max_prize:,} beans. Try again later!"
        )

    # Deduct bet from user and add to bot's wallet
    if not transfer_beans(nick, conn.nick, bet, db):
        return f"You don't have enough beans to play! You need at least {bet:,} beans to play the slots."

    # Generate expected and actual slot values
    expected_slots = [random.choice(emojis) for _ in range(3)]
    actual_slots = [random.choice(emojis) for _ in range(3)]
    result = " | ".join(f"{e} {a}" for e, a in zip(expected_slots, actual_slots))

    # Check for win conditions
    matches = sum(e == a for e, a in zip(expected_slots, actual_slots))
    if matches == 3:
        if not transfer_beans(conn.nick, nick, max_prize, db):
            return "The bot doesn't have enough beans to pay out the jackpot. Try again later!"
        return f"{result} JACKPOT! You won {max_prize:,} beans!" + (" ⏳" if is_cooldown else "")
    elif matches == 2:
        prize = math.ceil(bet_multiplier * (max_prize / 2))
        if not transfer_beans(conn.nick, nick, prize, db):
            return "The bot doesn't have enough beans to pay out your prize. Try again later!"
        return f"{result} You won {prize:,} beans!" + (" ⏳" if is_cooldown else "")
    elif matches == 1:
        return f"{result} Almost there! Keep trying! You lost {bet:,} beans."
    else:
        return f"{result} Better luck next time! You lost {bet:,} beans."


# Trivia bet functions
def add_trivia_bet(creator: str, trivia_id: int, bet_amount: int, winner: str, db) -> bool:
    """Add a bet for a trivia question. Returns True if successful."""
    creator = creator.lower()
    winner = winner.lower()

    # Add or update the bet
    clause = (trivia_bets_table.c.creator == creator) & (trivia_bets_table.c.trivia_id == trivia_id)
    existing_bet = db.execute(select([trivia_bets_table]).where(clause)).fetchone()

    if existing_bet:
        query = (
            trivia_bets_table.update()
            .values(bet_amount=bet_amount, winner=winner, timestamp=datetime.now())
            .where(clause)
        )
    else:
        query = trivia_bets_table.insert().values(
            creator=creator,
            trivia_id=trivia_id,
            bet_amount=bet_amount,
            winner=winner,
            timestamp=datetime.now(),
        )

    db.execute(query)
    db.commit()
    return True


def get_trivia_bets(trivia_id: int, db):
    """Get all bets for a specific trivia."""
    query = select([trivia_bets_table]).where(trivia_bets_table.c.trivia_id == trivia_id)
    return db.execute(query).fetchall()


def get_user_bets(nick: str, db):
    """Get all bets placed by a user."""
    nick = nick.lower()
    query = (
        select([trivia_bets_table])
        .where(trivia_bets_table.c.creator == nick)
        .order_by(sqlalchemy.desc(trivia_bets_table.c.timestamp))
    )
    return db.execute(query).fetchall()


def get_recent_trivia_bets(db):
    """Get the most recent trivia bets, grouped by trivia ID."""
    query = (
        select(
            [
                trivia_bets_table.c.trivia_id,
                sqlalchemy.func.sum(trivia_bets_table.c.bet_amount).label("total_bet_amount"),
                sqlalchemy.func.count().label("bet_count"),
            ]
        )
        .group_by(trivia_bets_table.c.trivia_id)
        .order_by(sqlalchemy.desc(sqlalchemy.func.max(trivia_bets_table.c.timestamp)))
        .limit(3)
    )
    return db.execute(query).fetchall()


def delete_trivia_bets(trivia_id: int, db, conn) -> None:
    """Delete all bets for a specific trivia and refund the betters."""
    bets = get_trivia_bets(trivia_id, db)

    for bet in bets:
        # Refund the bet amount to the creator
        if not transfer_beans(conn.nick, bet["creator"], bet["bet_amount"], db):
            # If we can't refund, log or handle the error
            print(f"Failed to refund {bet['bet_amount']} beans to {bet['creator']}")

    # Delete all bets for this trivia
    query = trivia_bets_table.delete().where(trivia_bets_table.c.trivia_id == trivia_id)
    db.execute(query)
    db.commit()


def handle_trivia_win(trivia_id: int, winner_nick: str, db, conn) -> tuple[int, int, list[str]]:
    """
    Handle bets when a trivia is won.
    Returns a tuple with (number of winners, total payout amount, unpaid winners list).
    """
    winner_nick = winner_nick.lower()
    bets = get_trivia_bets(trivia_id, db)

    if not bets:
        return 0, 0, []

    # Calculate total bet pool
    total_bet_amount = sum(bet["bet_amount"] for bet in bets)

    # Find winning bets (those who bet on the correct winner)
    winning_bets = [bet for bet in bets if bet["winner"].lower() == winner_nick]

    if not winning_bets:
        # No winners, all bets are lost
        query = trivia_bets_table.delete().where(trivia_bets_table.c.trivia_id == trivia_id)
        db.execute(query)
        db.commit()
        return 0, total_bet_amount, []

    # Calculate total amount bet by winners
    total_winning_bet_amount = sum(bet["bet_amount"] for bet in winning_bets)
    unpaid_winners = []

    # Distribute winnings proportionally to bet amounts
    for bet in winning_bets:
        # Calculate the proportion of the total pool this winner gets
        proportion = bet["bet_amount"] / total_winning_bet_amount
        payout = math.floor(total_bet_amount * proportion)

        # Add the winnings to the better's account
        if not transfer_beans(conn.nick, bet["creator"], payout, db):
            # If we can't pay, add to unpaid list
            unpaid_winners.append(bet["creator"])

    # Delete all bets for this trivia
    query = trivia_bets_table.delete().where(trivia_bets_table.c.trivia_id == trivia_id)
    db.execute(query)
    db.commit()

    return len(winning_bets), total_bet_amount, unpaid_winners


# Trivia functions
def add_trivia(creator: str, question: str, answer: str, prize: int, db) -> int:
    """Add a new trivia question and return its ID."""
    creator = creator.lower()
    query = trivia_table.insert().values(
        timestamp=datetime.now(),
        creator=creator,
        question=question,
        answer=answer.lower(),
        prize=prize,
    )
    result = db.execute(query)
    db.commit()
    return result.inserted_primary_key[0]


def get_trivia(trivia_id: int, db):
    """Get a trivia question by ID."""
    query = select([trivia_table]).where(trivia_table.c.id == trivia_id)
    return db.execute(query).fetchone()


def get_trivia_by_answer(answer: str, db):
    """Get a trivia question by its answer."""
    answer = answer.lower()
    query = select([trivia_table]).where(trivia_table.c.answer == answer)
    return db.execute(query).fetchone()


def get_latest_user_trivia(creator: str, db):
    """Get the latest trivia question created by a user."""
    creator = creator.lower()
    query = (
        select([trivia_table])
        .where(trivia_table.c.creator == creator)
        .order_by(sqlalchemy.desc(trivia_table.c.timestamp))
        .limit(1)
    )
    return db.execute(query).fetchone()


def get_latest_trivias(limit: int, db):
    """Get the latest trivia questions."""
    query = select([trivia_table]).order_by(sqlalchemy.desc(trivia_table.c.timestamp)).limit(limit)
    return db.execute(query).fetchall()


def get_user_trivias(creator: str, db):
    """Get all trivia questions created by a user."""
    creator = creator.lower()
    query = (
        select([trivia_table])
        .where(trivia_table.c.creator == creator)
        .order_by(sqlalchemy.desc(trivia_table.c.timestamp))
    )
    return db.execute(query).fetchall()


def delete_trivia(trivia_id: int, db, conn) -> bool:
    """Delete a trivia question by ID and refund any bets. Returns True if successful."""
    # First handle any bets on this trivia
    delete_trivia_bets(trivia_id, db, conn)

    query = trivia_table.delete().where(trivia_table.c.id == trivia_id)
    result = db.execute(query)
    db.commit()
    return result.rowcount > 0


@hook.command("trivia")
def trivia_cmd(text: str, nick: str, db, conn) -> str | list[str]:
    """
    .trivia add <prize_amount> <question> -> <answer> - Add a new trivia question
    .trivia question [id] - Show a trivia question (latest by default)
    .trivia list - Show latest 3 trivia questions
    .trivia user <nick> - Show trivias from a user
    .trivia delete <id> - Delete your trivia question
    .trivia help - Show help information
    """
    if not text:
        return trivia_cmd("help", nick, db, conn)

    parts = text.strip().split(None, 1)
    subcmd = parts[0].lower()

    if subcmd == "help":
        return [
            "🎮 Trivia Commands 🎮",
            "> .trivia add <prize_amount> <question> -> <answer> - Add a new trivia question with prize",
            "> .trivia question [id] - Show a trivia question (your latest by default)",
            "> .trivia list - Show latest 3 trivia questions",
            "> .trivia user <nick> - Show trivias created by a user",
            "> .trivia delete <id> - Delete your trivia question and get refunded",
            "> .trivia help - Show this help information",
            "",
            "Notes:",
            "- The prize is paid in beans from your account to the bot",
            "- Answers must be a single alphanumeric word",
            "- Use -> to separate your question from the answer",
        ]

    if len(parts) < 2 and subcmd not in ["list", "help"]:
        return "❌ Missing arguments. Use '.trivia help' for usage information."

    if subcmd == "add":
        # Check authentication for bean transfers
        auth_error = check_user_authenticated(conn, nick)
        if auth_error:
            return auth_error

        match = re.match(r"(\d+)\s+(.+?)\s+->\s+(\w+)$", parts[1])
        if not match:
            return "❌ Invalid format. Use: .trivia add <prize_amount> <question> -> <answer>"

        prize = int(match.group(1))
        question = match.group(2).strip()
        answer = match.group(3).strip().lower()

        # Check if prize is positive
        if prize <= 0:
            return "❌ Prize must be a positive number of beans."

        # Check if answer is alphanumeric
        if not answer.isalnum():
            return "❌ Answer must contain only letters and numbers."

        # Check if user has enough beans
        user_beans = get_beans(nick, db)
        if user_beans < prize:
            return f"❌ You don't have enough beans. You have {user_beans}, but the prize is {prize}."

        # Transfer beans to the bot
        if not transfer_beans(nick, conn.nick, prize, db):
            return "❌ Failed to transfer beans. Please try again."

        # Add the trivia question
        trivia_id = add_trivia(nick, question, answer, prize, db)

        return f"✅ Trivia question #{trivia_id} added with a prize of 🫘 {prize} beans!"

    elif subcmd == "question":
        if len(parts) == 1:
            # Show latest question by the user
            trivia = get_latest_user_trivia(nick, db)
            if not trivia:
                return "❌ You haven't created any trivia questions yet."
        else:
            try:
                trivia_id = int(parts[1])
                trivia = get_trivia(trivia_id, db)
                if not trivia:
                    return f"❌ Trivia question #{trivia_id} not found."
            except ValueError:
                return "❌ Invalid trivia ID. Please provide a number."

        return [
            f"📝 Trivia #{trivia['id']} (created by {trivia['creator']})",
            f"Question: {trivia['question']}",
            f"Prize: 🫘 {trivia['prize']} beans",
        ]

    elif subcmd == "list":
        trivias = get_latest_trivias(3, db)
        if not trivias:
            return "❌ No trivia questions found."

        result = ["🎯 Latest Trivia Questions 🎯"]
        for t in trivias:
            result.append(f"#{t['id']}: \"{t['question']}\" - Prize: 🫘 {t['prize']} beans (by {t['creator']})")

        return result

    elif subcmd == "user":
        target = parts[1].strip()
        trivias = get_user_trivias(target, db)
        if not trivias:
            return f"❌ No trivia questions found for user {target}."

        result = [f"🧩 Trivia Questions by {target} 🧩"]
        for t in trivias:
            result.append(f"#{t['id']}: \"{t['question']}\" - Prize: 🫘 {t['prize']} beans")

        return result

    elif subcmd == "delete":
        # Check authentication for bean transfers
        auth_error = check_user_authenticated(conn, nick)
        if auth_error:
            return auth_error

        try:
            trivia_id = int(parts[1])
            trivia = get_trivia(trivia_id, db)

            if not trivia:
                return f"❌ Trivia question #{trivia_id} not found."

            if trivia["creator"].lower() != nick.lower():
                return "❌ You can only delete your own trivia questions."

            # Check if the bot can pay back the prize
            bot_beans = get_beans(conn.nick, db)
            if bot_beans < trivia["prize"]:
                return "❌ The bot doesn't have enough beans to refund your prize. Try again later."

            # Transfer beans back to the creator
            if not transfer_beans(conn.nick, nick, trivia["prize"], db):
                return "❌ Failed to refund beans. Please try again later."

            # Delete the trivia question
            if delete_trivia(trivia_id, db, conn):
                return f"✅ Trivia question #{trivia_id} deleted. You've been refunded 🫘 {trivia['prize']} beans."
            else:
                # If delete fails, we need to return the beans to the bot
                transfer_beans(nick, conn.nick, trivia["prize"], db)
                return "❌ Failed to delete trivia question. Please try again."

        except ValueError:
            return "❌ Invalid trivia ID. Please provide a number."

    else:
        return f"❌ Unknown subcommand: {subcmd}. Use '.trivia help' for usage information."


@hook.regex(re.compile(r"^\s*(\S+)\s*$", re.I))
def track_trivia_answers(match, event, db, conn, chan) -> list[str] | None | str:
    if event.type is EventType.action:
        return
    if not chan.startswith("#"):
        return
    answer = match.group(1).strip()
    if not answer:
        return
    trivia = get_trivia_by_answer(answer, db)
    if not trivia:
        return

    # Transfer beans to the winner
    if not transfer_beans(conn.nick, event.nick, trivia["prize"], db):
        return "❌ The bot doesn't have enough beans to pay out the prize. Try again later!"

    # Handle any bets on this trivia
    winners_count, total_bet_amount, unpaid_winners = handle_trivia_win(trivia["id"], event.nick, db, conn)

    # Delete the trivia question after answering
    delete_trivia(trivia["id"], db, conn)

    result = [
        f"🎉 {event.nick} answered correctly! The answer was '{trivia['answer']}'. "
        f"You won 🫘 {trivia['prize']} beans! 🎉"
    ]

    if winners_count > 0:
        result.append(
            f" Additionally, {winners_count} bettors who bet on {event.nick} split "
            f"a pool of 🫘 {total_bet_amount} beans!"
        )

        if unpaid_winners:
            result.append(
                f" Sorry, couldn't pay {len(unpaid_winners)} winners due to insufficient bot beans: "
                f"{', '.join(unpaid_winners[:3])}"
                + (f" and {len(unpaid_winners) - 3} more" if len(unpaid_winners) > 3 else "")
            )

    return result


@hook.command("bets", "bet")
def bet_cmd(text: str, nick: str, db, conn, event) -> str | list[str]:
    """
    .bet trivia <trivia_id> place <amount> bean(s) on <winner> - Bet on who will win a trivia
    .bet trivia list - Show recent trivias with bets
    .bet trivia <trivia_id> - Show bets for a specific trivia
    .bet trivia user <user> - Show bets placed by a user
    .bet help - Show help information
    """
    if not text:
        return bet_cmd("help", nick, db, conn, event)

    parts = text.strip().split(None, 6)

    if parts[0].lower() == "help":
        return [
            "🎲 Betting Commands 🎲",
            "> .bet trivia <trivia_id> place <amount> bean(s) on <winner> - Bet on who will win a trivia",
            "> .bet trivia list - Show recent trivias with bets",
            "> .bet trivia <trivia_id> - Show bets for a specific trivia",
            "> .bet trivia user <user> - Show bets placed by a user",
            "> .bet help - Show this help information",
            "",
            "Notes:",
            "- Winner must be a valid IRC nickname",
            "- You can't bet on trivias you created",
            "- Only one bet per trivia is allowed",
            "- If you win, you get a share of the total bet pool proportional to your bet amount",
        ]

    if len(parts) < 2:
        return "❌ Missing arguments. Use '.bet help' for usage information."

    subcmd = parts[0].lower()

    if subcmd != "trivia":
        return f"❌ Unknown subcommand: {subcmd}. Use '.bet help' for usage information."

    # Handle viewing bets
    if len(parts) == 2 and parts[1].lower() == "list":
        # Show recent trivias with bets
        recent_bets = get_recent_trivia_bets(db)
        if not recent_bets:
            return "❌ No active bets found."

        result = ["🎯 Recent Trivias with Bets 🎯"]

        for bet_summary in recent_bets:
            trivia = get_trivia(bet_summary["trivia_id"], db)
            if not trivia:
                continue

            result.append(
                f"Trivia #{bet_summary['trivia_id']}: \"{trivia['question'][:30]}...\" - "
                f"{bet_summary['bet_count']} bets, 🫘 {bet_summary['total_bet_amount']} beans total"
            )

        return result

    # Handle viewing bets for a specific trivia
    if len(parts) == 2 and parts[1].isdigit():
        trivia_id = int(parts[1])
        trivia = get_trivia(trivia_id, db)

        if not trivia:
            return f"❌ Trivia question #{trivia_id} not found."

        bets = get_trivia_bets(trivia_id, db)
        if not bets:
            return f"❌ No bets found for Trivia #{trivia_id}."

        total_bet_amount = sum(bet["bet_amount"] for bet in bets)
        result = [
            f"🎯 Bets for Trivia #{trivia_id} 🎯",
            f"Question: {trivia['question']}",
            f"Total bet amount: 🫘 {total_bet_amount} beans",
            "Recent bets:",
        ]

        # Sort bets by timestamp, most recent first
        sorted_bets = sorted(
            bets,
            key=lambda x: x["timestamp"] if x["timestamp"] else datetime.min,
            reverse=True,
        )

        for bet in sorted_bets[:3]:  # Limit to 3 recent bets
            result.append(f"{bet['creator']} bet 🫘 {bet['bet_amount']} beans on {bet['winner']}")

        if len(sorted_bets) > 3:
            result.append(f"...and {len(sorted_bets) - 3} more bets")

        return result

    # Handle viewing user bets
    if len(parts) >= 3 and parts[1].lower() == "user":
        target_user = parts[2].strip()
        user_bets = get_user_bets(target_user, db)

        if not user_bets:
            return f"❌ No bets found for user {target_user}."

        total_bet_amount = sum(bet["bet_amount"] for bet in user_bets)
        result = [
            f"🎯 Bets placed by {target_user} 🎯",
            f"Total bet amount: 🫘 {total_bet_amount} beans",
            "Recent bets:",
        ]

        for bet in user_bets[:3]:  # Limit to 3 recent bets
            trivia = get_trivia(bet["trivia_id"], db)
            question = trivia["question"] if trivia else "Unknown"
            result.append(
                f"Trivia #{bet['trivia_id']}: {question[:30]}... - "
                f"Bet 🫘 {bet['bet_amount']} beans on {bet['winner']}"
            )

        return result

    # Now handle placing a bet
    if len(parts) < 7:
        return "❌ Missing arguments. Use '.bet help' for usage information."

    # Check authentication for placing bets (involves bean transfers)
    auth_error = check_user_authenticated(conn, nick)
    if auth_error:
        return auth_error

    if (
        parts[2].lower() not in ["place", "add"]
        or parts[4].lower() not in ["bean", "beans"]
        or parts[5].lower() != "on"
    ):
        return "❌ Invalid syntax. Use '.bet trivia <trivia_id> place <amount> bean(s) on <winner>'."

    trivia_id_str = parts[1]
    bet_amount_str = parts[3]
    winner = parts[6]

    try:
        trivia_id = int(trivia_id_str)
        bet_amount = int(bet_amount_str)
    except ValueError:
        return "❌ Trivia ID and bet amount must be numbers."

    if bet_amount <= 0:
        return "❌ Bet amount must be positive."

    # Check if trivia exists
    trivia = get_trivia(trivia_id, db)
    if not trivia:
        return f"❌ Trivia question #{trivia_id} not found."

    # Check if user is trying to set winner to the trivia creator
    if winner.lower() == trivia["creator"].lower():
        return "❌ You can't bet on the creator of the trivia."

    # Check if user already placed a bet on this trivia
    existing_bet = db.execute(
        select([trivia_bets_table]).where(
            (trivia_bets_table.c.creator == nick.lower()) & (trivia_bets_table.c.trivia_id == trivia_id)
        )
    ).fetchone()

    if existing_bet:
        return f"❌ You already bet 🫘 {existing_bet['bet_amount']} beans on {existing_bet['winner']} for this trivia. Only one bet per trivia is allowed."

    # Check if user has enough beans
    user_beans = get_beans(nick, db)
    if user_beans < bet_amount:
        return f"❌ You don't have enough beans. You have 🫘 {user_beans} beans."

    # Deduct beans from user
    if not transfer_beans(nick, conn.nick, bet_amount, db):
        return "❌ Failed to transfer beans. Please try again."

    # Place the bet
    timestamp = datetime.now()
    db.execute(
        trivia_bets_table.insert().values(
            trivia_id=trivia_id,
            creator=nick.lower(),
            winner=winner.lower(),
            bet_amount=bet_amount,
            timestamp=timestamp,
        )
    )
    db.commit()

    return f"✅ You bet 🫘 {bet_amount} beans on {winner} to win trivia #{trivia_id}."
