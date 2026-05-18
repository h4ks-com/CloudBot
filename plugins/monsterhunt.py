import json
import operator
import random
from collections import defaultdict
from time import time

from sqlalchemy import (
    Column,
    Integer,
    PrimaryKeyConstraint,
    String,
    Table,
    desc,
)
from sqlalchemy.sql import select

from cloudbot import hook
from cloudbot.util import database, web

duck = [
    " ε=ε=ε=ε=ε=┌(；　・＿・)┘ ",
    " ε=ε=ε=ε=ε=ε=┌(๑ʘ∀ʘ)┘ ",
    " ===≡≡≡｡ﾟ┌(ﾟ´Д`ﾟ)┘ﾟ｡ ",
    " ・・・・・・・ᕕ(╯°□°)ᕗ ",
]
duck_tail = [
    "[¬º-°]¬",
    "(▼皿▼)",
    "←~∋(｡Ψ▼ｰ▼)∈",
    "∋━━o(｀∀´oメ）～→",
    "(˼●̙̂ ̟ ̟̎ ̟ ̘●̂˻)",
    "(;´༎ຶД༎ຶ`)",
    "(((༼•̫͡•༽)))",
    "( ͡° ͜◯ ͡°)",
]
duck_noise = [
    "RUN!!!!!",
    "AHHHHHHHHHHH",
    "FFFFFUUUUUUU!",
    "THE CLOWNS ARE COMING!!!!!",
]

table = Table(
    "monster_hunt",
    database.metadata,
    Column("network", String),
    Column("name", String),
    Column("shot", Integer),
    Column("befriend", Integer),
    Column("chan", String),
    PrimaryKeyConstraint("name", "chan", "network"),
)

optout = Table(
    "monster_nohunt",
    database.metadata,
    Column("network", String),
    Column("chan", String),
    PrimaryKeyConstraint("chan", "network"),
)


class ChannelState:
    """Per-channel monster hunt state.

    Each channel needs heterogeneous state (ints, floats, list of masks),
    so a dataclass-like class is used instead of a defaultdict of mixed values.
    """

    def __init__(self) -> None:
        self.game_on: int = 0
        self.duck_status: int = 0
        self.next_duck_time: int = 0
        self.no_duck_kick: int = 0
        self.messages: int = 0
        self.masks: list[str] = []
        self.duck_time: float = 0.0
        self.shoot_time: float = 0.0


MSG_DELAY = 10
MASK_REQ = 3
scripters: dict[str, float] = defaultdict(float)
game_status: dict[str, dict[str, ChannelState]] = defaultdict(
    lambda: defaultdict(ChannelState)
)
opt_out: list[str] = []


def _get_state(conn_name: str, chan: str) -> ChannelState:
    return game_status[conn_name][chan]


# @hook.on_start()
def load_optout(db):
    """load a list of channels duckhunt should be off in. Right now I am being lazy and not
    differentiating between networks this should be cleaned up later."""
    opt_out.clear()
    chans = db.execute(select(optout.c.chan))
    if chans:
        for row in chans:
            chan = row["chan"]
            opt_out.append(chan)


# @hook.event([EventType.message, EventType.action], singlethread=True)
def incrementMsgCounter(event, conn):
    """Increment the number of messages said in an active game channel. Also keep track of the unique masks that are speaking."""
    if event.chan in opt_out:
        return
    state = _get_state(conn.name, event.chan)
    if state.game_on == 1 and state.duck_status == 0:
        state.messages += 1
        if event.host not in state.masks:
            state.masks.append(event.host)


# @hook.command("starthunt", autohelp=False)
def start_hunt(chan, message, conn):
    """This command starts a spooky MONSTER hunt in your channel, to stop the hunt use .stophunt"""
    if chan in opt_out:
        return None
    elif not chan.startswith("#"):
        return "No hunting by yourself, that isn't safe."
    state = _get_state(conn.name, chan)
    if state.game_on:
        return f"There is already a hunt running in {chan}."
    else:
        state.game_on = 1
    set_ducktime(chan, conn)
    message(
        "Monsters have been spotted chasing people. Save the person and kill the monster with .bang, or try and befriend the monster and let it kill the person using .befriend. For more information on this creepy event see https://redd.it/56sqlx",
        chan,
    )
    return None


def set_ducktime(chan, conn):
    state = _get_state(conn.name, chan)
    state.next_duck_time = random.randint(int(time()) + 480, int(time()) + 3600)
    state.duck_status = 0
    state.messages = 0
    state.masks = []


# @hook.command("stophunt", autohelp=False)
def stop_hunt(chan, conn):
    """This command stops the Monster hunt in your channel. Scores will be preserved"""
    if chan in opt_out:
        return None
    state = _get_state(conn.name, chan)
    if state.game_on:
        state.game_on = 0
        return "The hunt has been stopped."
    else:
        return f"There is no monster hunt running in {chan}."


# @hook.command("monsterkick")
def no_duck_kick(text, chan, conn, notice):
    """If the bot has OP or half-op in the channel you can specify .monsterkick enable|disable so that people are kicked for shooting or befriending a non-existent monster. Default is off."""
    if chan in opt_out:
        return None
    state = _get_state(conn.name, chan)
    if text.lower() == "enable":
        state.no_duck_kick = 1
        return "users will now be kicked for shooting or befriending non-existent monsters. The bot needs to have appropriate flags to be able to kick users for this to work."
    elif text.lower() == "disable":
        state.no_duck_kick = 0
        return "kicking for non-existent monsters has been disabled."
    else:
        notice(no_duck_kick.__doc__)
        return None


def generate_duck():
    """Try and randomize the monster message so people can't highlight on it/script against it."""
    dtail = random.choice(duck_tail)
    rt = random.randint(1, len(dtail) - 1)
    dtail = dtail[:rt] + " \u200b " + dtail[rt:]
    dbody = random.choice(duck)
    rb = random.randint(1, len(dbody) - 1)
    dbody = dbody[:rb] + "\u200b" + dbody[rb:]
    dnoise = random.choice(duck_noise)
    rn = random.randint(1, len(dnoise) - 1)
    dnoise = dnoise[:rn] + "\u200b" + dnoise[rn:]
    return (dtail, dbody, dnoise)


# @hook.periodic(11, initial_interval=11)
def deploy_duck(bot):
    for network in game_status:
        if network not in bot.connections:
            continue
        conn = bot.connections[network]
        if not conn.ready:
            continue
        for chan in game_status[network]:
            state = game_status[network][chan]
            if (
                state.game_on == 1
                and state.duck_status == 0
                and state.next_duck_time <= time()
                and state.messages >= MSG_DELAY
                and len(state.masks) >= MASK_REQ
            ):
                # deploy a duck to channel
                state.duck_status = 1
                state.duck_time = time()
                dtail, dbody, dnoise = generate_duck()
                conn.message(chan, f"{dtail}{dbody}{dnoise}")
            continue
        continue


def hit_or_miss(deploy, shoot):
    """This function calculates if the befriend or bang will be successful."""
    if shoot - deploy < 1:
        return 0.05
    elif 1 <= shoot - deploy <= 7:
        out = random.uniform(0.60, 0.75)
        return out
    else:
        return 1


def dbadd_entry(nick, chan, db, conn, shoot, friend):
    """Takes care of adding a new row to the database."""
    query = table.insert().values(
        network=conn.name,
        chan=chan.lower(),
        name=nick.lower(),
        shot=shoot,
        befriend=friend,
    )
    db.execute(query)
    db.commit()


def dbupdate(nick, chan, db, conn, shoot, friend):
    """update a db row"""
    if shoot and not friend:
        query = (
            table.update()
            .where(table.c.network == conn.name)
            .where(table.c.chan == chan.lower())
            .where(table.c.name == nick.lower())
            .values(shot=shoot)
        )
        db.execute(query)
        db.commit()
    elif friend and not shoot:
        query = (
            table.update()
            .where(table.c.network == conn.name)
            .where(table.c.chan == chan.lower())
            .where(table.c.name == nick.lower())
            .values(befriend=friend)
        )
        db.execute(query)
        db.commit()
    elif friend and shoot:
        query = (
            table.update()
            .where(table.c.network == conn.name)
            .where(table.c.chan == chan.lower())
            .where(table.c.name == nick.lower())
            .values(befriend=friend)
            .values(shot=shoot)
        )
        db.execute(query)
        db.commit()


# @hook.command("bang", autohelp=False)
def bang(nick, chan, message, db, conn, notice):
    """when there is a monster chasing someone use this command to shoot it."""
    if chan in opt_out:
        return None
    network = conn.name
    out = ""
    miss = [
        "WHOOSH! You totally missed the monster. Save the poor human!",
        "Your gun jammed!",
        "Better luck next time.",
        "Good thing this is just an IRC game cus your aim is terrible!",
    ]
    state = _get_state(network, chan)
    if not state.game_on:
        return (
            "There is no activehunt right now. Use .starthunt to start a game."
        )
    elif state.duck_status != 1:
        if state.no_duck_kick == 1:
            out = f"KICK {chan} {nick} There is no monster! What are you shooting at?"
            conn.send(out)
            return None
        return "There is no monster. What are you shooting at?"
    else:
        state.shoot_time = time()
        deploy = state.duck_time
        shoot = state.shoot_time
        if nick.lower() in scripters:
            if scripters[nick.lower()] > shoot:
                notice(
                    "You are in a cool down period, you can try again in {} seconds.".format(
                        str(scripters[nick.lower()] - shoot)
                    )
                )
                return None
        chance = hit_or_miss(deploy, shoot)
        if not random.random() <= chance and chance > 0.05:
            out = random.choice(miss) + " You can try again in 7 seconds."
            scripters[nick.lower()] = shoot + 7
            return out
        if chance == 0.05:
            out += "You pulled the trigger in {} seconds, that's mighty fast. Are you sure you aren't a script? Take a 2 hour cool down.".format(
                str(shoot - deploy)
            )
            scripters[nick.lower()] = shoot + 7200
            if not random.random() <= chance:
                return random.choice(miss) + " " + out
            else:
                message(out)
        state.duck_status = 2
        score_row = db.execute(
            select(table.c.shot)
            .where(table.c.network == conn.name)
            .where(table.c.chan == chan.lower())
            .where(table.c.name == nick.lower())
        ).fetchone()
        if score_row:
            score = score_row[0] + 1
            dbupdate(nick, chan, db, conn, score, 0)
        else:
            score = 1
            dbadd_entry(nick, chan, db, conn, score, 0)
        timer = f"{shoot - deploy:.3f}"
        duck = "monster" if score == 1 else "monsters"
        message(
            "{} you shot a monster and saved a human in {} seconds! You have killed {} {} in {}.".format(
                nick, timer, score, duck, chan
            )
        )
        set_ducktime(chan, conn)
        return None
    return None


# @hook.command("befriend", autohelp=False)
def befriend(nick, chan, message, db, conn, notice):
    """when there is a monster on the loose chasing a human use this command to befriend it before someone else shoots it. This will also let it kill the human."""
    if chan in opt_out:
        return None
    network = conn.name
    out = ""
    miss = [
        "The monster didn't want to be friends, you are lucky they didn't eat you too!",
        "The monster just grunted and kept chasing his prey, maybe you should provide an offering of some kind",
        "Maybe your breath smells too sweet or your face looks too nice. Either way the monster rejected you.",
    ]
    state = _get_state(network, chan)
    if not state.game_on:
        return "There is no hunt right now. Use .starthunt to start a game."
    elif state.duck_status != 1:
        if state.no_duck_kick == 1:
            out = f"KICK {chan} {nick} You tried befriending a non-existent monster, that's fucking creepy."
            conn.send(out)
            return None
        return "You tried befriending a non-existent monster, that's fucking creepy."
    else:
        state.shoot_time = time()
        deploy = state.duck_time
        shoot = state.shoot_time
        if nick.lower() in scripters:
            if scripters[nick.lower()] > shoot:
                notice(
                    "You are in a cool down period, you can try again in {} seconds.".format(
                        str(scripters[nick.lower()] - shoot)
                    )
                )
                return None
        chance = hit_or_miss(deploy, shoot)
        if not random.random() <= chance and chance > 0.05:
            out = random.choice(miss) + " You can try again in 7 seconds."
            scripters[nick.lower()] = shoot + 7
            return out
        if chance == 0.05:
            out += "You tried friending that monster in {} seconds, that's mighty fast. Are you sure you aren't a script? Take a 2 hour cool down.".format(
                str(shoot - deploy)
            )
            scripters[nick.lower()] = shoot + 7200
            if not random.random() <= chance:
                return random.choice(miss) + " " + out
            else:
                message(out)

        state.duck_status = 2
        score_row = db.execute(
            select(table.c.befriend)
            .where(table.c.network == conn.name)
            .where(table.c.chan == chan.lower())
            .where(table.c.name == nick.lower())
        ).fetchone()
        if score_row:
            score = score_row[0] + 1
            dbupdate(nick, chan, db, conn, 0, score)
        else:
            score = 1
            dbadd_entry(nick, chan, db, conn, 0, score)
        duck = "monster" if score == 1 else "monsters"
        timer = f"{shoot - deploy:.3f}"
        message(
            "{} you befriended a monster in {} seconds! You have made friends with {} {} in {}.".format(
                nick, timer, score, duck, chan
            )
        )
        set_ducktime(chan, conn)
        return None
    return None


def smart_truncate(content, length=320, suffix="..."):
    if len(content) <= length:
        return content
    else:
        return content[:length].rsplit(" • ", 1)[0] + suffix


@hook.command("monsterfriends", autohelp=False)
def friends(text, chan, conn, db):
    """[{global|average}] - Prints a list of the top monster friends in the channel, if 'global' is specified all channels in the database are included."""
    if chan in opt_out:
        return None
    friends: dict[str, int] = defaultdict(int)
    chancount: dict[str, int] = defaultdict(int)
    if text.lower() == "global" or text.lower() == "average":
        out = "Monster friend scores across the network: "
        scores = db.execute(
            select(table.c.name, table.c.befriend)
            .where(table.c.network == conn.name)
            .order_by(desc(table.c.befriend))
        )
        if scores:
            for row in scores:
                if row[1] == 0:
                    continue
                chancount[row[0]] += 1
                friends[row[0]] += row[1]
            if text.lower() == "average":
                for k, v in friends.items():
                    friends[k] = v // chancount[k]
        else:
            return "it appears no one has friended any monsters yet."
    else:
        out = f"Monster friend scores in {chan}: "
        scores = db.execute(
            select(table.c.name, table.c.befriend)
            .where(table.c.network == conn.name)
            .where(table.c.chan == chan.lower())
            .order_by(desc(table.c.befriend))
        )
        if scores:
            for row in scores:
                if row[1] == 0:
                    continue
                friends[row[0]] += row[1]
        else:
            return "it appears no one has friended any monsters yet."

    topfriends = sorted(
        friends.items(), key=operator.itemgetter(1), reverse=True
    )
    url = web.paste(json.dumps(topfriends, indent=4))
    out += " • ".join(
        [
            "{}: {}".format("\x02" + k[:1] + "\u200b" + k[1:] + "\x02", str(v))
            for k, v in topfriends
        ]
    )
    out = smart_truncate(out)
    out += " " + url
    return out


@hook.command("monsterkillers", autohelp=False)
def killers(text, chan, conn, db):
    """[{global|average}] - Prints a list of the top monster killers in the channel, if 'global' is specified all channels in the database are included."""
    if chan in opt_out:
        return None
    killers: dict[str, int] = defaultdict(int)
    chancount: dict[str, int] = defaultdict(int)
    if text.lower() == "global" or text.lower() == "average":
        out = "Monster killer scores across the network: "
        scores = db.execute(
            select(table.c.name, table.c.shot)
            .where(table.c.network == conn.name)
            .order_by(desc(table.c.shot))
        )
        if scores:
            for row in scores:
                if row[1] == 0:
                    continue
                chancount[row[0]] += 1
                killers[row[0]] += row[1]
            if text.lower() == "average":
                for k, v in killers.items():
                    killers[k] = v // chancount[k]
        else:
            return "it appears no one has killed any monsters yet."
    else:
        out = f"Monster killer scores in {chan}: "
        scores = db.execute(
            select(table.c.name, table.c.shot)
            .where(table.c.network == conn.name)
            .where(table.c.chan == chan.lower())
            .order_by(desc(table.c.shot))
        )
        if scores:
            for row in scores:
                if row[1] == 0:
                    continue
                killers[row[0]] += row[1]
        else:
            return "it appears no on has killed any monsters yet."

    topkillers = sorted(
        killers.items(), key=operator.itemgetter(1), reverse=True
    )
    url = web.paste(json.dumps(topkillers, indent=4))
    out += " • ".join(
        [
            "{}: {}".format("\x02" + k[:1] + "\u200b" + k[1:] + "\x02", str(v))
            for k, v in topkillers
        ]
    )
    out = smart_truncate(out) + " " + url
    return out


# @hook.command("monsterforgive", permissions=["op", "ignore"])
def duckforgive(text):
    """Allows people to be removed from the mandatory cooldown period."""
    if text.lower() in scripters and scripters[text.lower()] > time():
        scripters[text.lower()] = 0
        return f"{text} has been removed from the mandatory cooldown period."
    else:
        return "I couldn't find anyone banned from the hunt by that nick"


# @hook.command("hunt_opt_out", permissions=["op", "ignore"], autohelp=False)
def hunt_opt_out(text, chan, db, conn):
    """Running this command without any arguments displays the status of the current channel. hunt_opt_out add #channel will disable all duck and monster hunt commands in the specified channel. hunt_opt_out remove #channel will re-enable the game for the specified channel."""
    if not text:
        if chan in opt_out:
            return f"Monster hunt is disabled in {chan}. To re-enable it run .hunt_opt_out remove #channel"
        else:
            return f"Monster hunt is enabled in {chan}. To disable it run .hunt_opt_out add #channel"
    if text == "list":
        return ", ".join(opt_out)
    if len(text.split(" ")) < 2:
        return "please specify add or remove and a valid channel name"
    command = text.split()[0]
    channel = text.split()[1]
    if not channel.startswith("#"):
        return "Please specify a valid channel."
    if command.lower() == "add":
        if channel in opt_out:
            return f"Monster hunt has already been disabled in {channel}."
        query = optout.insert().values(network=conn.name, chan=channel.lower())
        db.execute(query)
        db.commit()
        load_optout(db)
        return f"The monster hunt has been successfully disabled in {channel}."
    if command.lower() == "remove":
        if channel not in opt_out:
            return f"Monster hunt is already enabled in {channel}."
        delete = optout.delete().where(optout.c.chan == channel.lower())
        db.execute(delete)
        db.commit()
        load_optout(db)
    return None


# @hook.command("monstermerge", permissions=["botcontrol"])
def duck_merge(text, conn, db, message):
    """Moves the monster scores from one nick to another nick. Accepts two nicks as input the first will have their monster scores removed the second will have the first score added. Warning this cannot be undone."""
    oldnick, newnick = text.lower().split()
    if oldnick == newnick:
        return "please specify two different nicks."
    if not oldnick or not newnick:
        return "Please specify two nicks for this command."
    oldnickscore = db.execute(
        select(table.c.name, table.c.chan, table.c.shot, table.c.befriend)
        .where(table.c.network == conn.name)
        .where(table.c.name == oldnick)
    ).fetchall()
    newnickscore = db.execute(
        select(table.c.name, table.c.chan, table.c.shot, table.c.befriend)
        .where(table.c.network == conn.name)
        .where(table.c.name == newnick)
    ).fetchall()
    duckmerge: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    total_kills = 0
    total_friends = 0
    channelkey: dict[str, list[str]] = {"update": [], "insert": []}
    if oldnickscore:
        if newnickscore:
            for row in newnickscore:
                duckmerge[row["chan"]]["shot"] = row["shot"]
                duckmerge[row["chan"]]["befriend"] = row["befriend"]
            for row in oldnickscore:
                if row["chan"] in duckmerge:
                    duckmerge[row["chan"]]["shot"] = (
                        duckmerge[row["chan"]]["shot"] + row["shot"]
                    )
                    duckmerge[row["chan"]]["befriend"] = (
                        duckmerge[row["chan"]]["befriend"] + row["befriend"]
                    )
                    channelkey["update"].append(row["chan"])
                    total_kills += row["shot"]
                    total_friends += row["befriend"]
                else:
                    duckmerge[row["chan"]]["shot"] = row["shot"]
                    duckmerge[row["chan"]]["befriend"] = row["befriend"]
                    channelkey["insert"].append(row["chan"])
                    total_kills += row["shot"]
                    total_friends += row["befriend"]
        else:
            for row in oldnickscore:
                duckmerge[row["chan"]]["shot"] = row["shot"]
                duckmerge[row["chan"]]["befriend"] = row["befriend"]
                channelkey["insert"].append(row["chan"])
        for channel in channelkey["insert"]:
            dbadd_entry(
                newnick,
                channel,
                db,
                conn,
                duckmerge[channel]["shot"],
                duckmerge[channel]["befriend"],
            )
        for channel in channelkey["update"]:
            dbupdate(
                newnick,
                channel,
                db,
                conn,
                duckmerge[channel]["shot"],
                duckmerge[channel]["befriend"],
            )
        query = (
            table.delete()
            .where(table.c.network == conn.name)
            .where(table.c.name == oldnick)
        )
        db.execute(query)
        db.commit()
        message(
            "Migrated {} monster kills and {} monster friends from {} to {}".format(
                total_kills, total_friends, oldnick, newnick
            )
        )
    else:
        return f"There are no monster scores to migrate from {oldnick}"
    return None


@hook.command("monsters", autohelp=False)
def ducks_user(text, nick, chan, conn, db, message):
    """<nick> - Prints a users monster stats. If no nick is input it will check the calling username."""
    name = nick.lower()
    if text:
        name = text.split()[0].lower()
    ducks: dict[str, int] = defaultdict(int)
    scores = db.execute(
        select(table.c.name, table.c.chan, table.c.shot, table.c.befriend)
        .where(table.c.network == conn.name)
        .where(table.c.name == name)
    ).fetchall()
    if scores:
        for row in scores:
            if row["chan"].lower() == chan.lower():
                ducks["chankilled"] += row["shot"]
                ducks["chanfriends"] += row["befriend"]
            ducks["killed"] += row["shot"]
            ducks["friend"] += row["befriend"]
            ducks["chans"] += 1
        if ducks["chans"] == 1:
            message(
                "{} has killed {} and befriended {} monsters in {}.".format(
                    name, ducks["chankilled"], ducks["chanfriends"], chan
                )
            )
            return None
        kill_average = ducks["killed"] // ducks["chans"]
        friend_average = ducks["friend"] // ducks["chans"]
        message(
            "\x02{}'s\x02 duck stats: \x02{}\x02 killed and \x02{}\x02 befriended in {}. Across {} channels: \x02{}\x02 killed and \x02{}\x02 befriended. Averaging \x02{}\x02 kills and \x02{}\x02 friends per channel.".format(
                name,
                ducks["chankilled"],
                ducks["chanfriends"],
                chan,
                ducks["chans"],
                ducks["killed"],
                ducks["friend"],
                kill_average,
                friend_average,
            )
        )
        return None
    return f"It appears {name} has not participated in the monster hunt."


@hook.command("monsterstats", autohelp=False)
def duck_stats(chan, conn, db, message):
    """- Prints monster statistics for the entire channel and totals for the network."""
    totals: dict[str, int] = defaultdict(int)
    friendchan: dict[str, int] = defaultdict(int)
    killchan: dict[str, int] = defaultdict(int)
    scores = db.execute(
        select(
            table.c.name, table.c.chan, table.c.shot, table.c.befriend
        ).where(table.c.network == conn.name)
    ).fetchall()
    if scores:
        for row in scores:
            friendchan[row["chan"]] += row["befriend"]
            killchan[row["chan"]] += row["shot"]
            if row["chan"].lower() == chan.lower():
                totals["chankilled"] += row["shot"]
                totals["chanfriends"] += row["befriend"]
            totals["killed"] += row["shot"]
            totals["friend"] += row["befriend"]
        totals["chans"] = (len(friendchan) + len(killchan)) // 2
        killerchan, killscore = sorted(
            killchan.items(), key=operator.itemgetter(1), reverse=True
        )[0]
        friendchan_top, friendscore = sorted(
            friendchan.items(),
            key=operator.itemgetter(1),
            reverse=True,
        )[0]
        message(
            "\x02Monster Stats:\x02 {} killed and {} befriended in \x02{}\x02. Across {} channels \x02{}\x02 monsters have been killed and \x02{}\x02 befriended. \x02Top Channels:\x02 \x02{}\x02 with {} kills and \x02{}\x02 with {} friends".format(
                totals["chankilled"],
                totals["chanfriends"],
                chan,
                totals["chans"],
                totals["killed"],
                totals["friend"],
                killerchan,
                killscore,
                friendchan_top,
                friendscore,
            )
        )
    else:
        return "It looks like there has been no monster activity on this channel or network."
    return None
