import gc
import os
import signal
import sys
import threading
import traceback
from collections import Counter

import psutil

from cloudbot import hook
from cloudbot.util import web

PYMPLER_ENABLED = False

if PYMPLER_ENABLED:
    try:
        import pympler
        import pympler.muppy
        import pympler.summary
        import pympler.tracker
    except ImportError:
        pympler = None
else:
    pympler = None
try:
    import objgraph
except ImportError:
    objgraph = None


def create_tracker():
    if pympler is None:
        return None

    return pympler.tracker.SummaryTracker()


tr = create_tracker()


def get_name(thread_id):
    current_thread = threading.current_thread()
    if thread_id == current_thread.ident:
        is_current = True
        thread = current_thread
    else:
        is_current = False
        thread = None
        for t in threading.enumerate():
            if t.ident == thread_id:
                thread = t
                break

    if thread is not None:
        if thread.name is not None:
            name = thread.name
        else:
            name = "Unnamed thread"
    else:
        name = "Unknown thread"

    name = f"{name} ({thread_id})"
    if is_current:
        name += " - Current thread"

    return name


def get_thread_dump():
    code = []
    threads = [
        (get_name(thread_id), traceback.extract_stack(stack))
        for thread_id, stack in sys._current_frames().items()
    ]
    for thread_name, stack in threads:
        code.append(f"# {thread_name}")
        for filename, line_num, name, line in stack:
            code.append(f"{filename}:{line_num} - {name}")
            if line:
                code.append(f"    {line.strip()}")
        code.append("")  # new line
    return web.paste("\n".join(code), ext="txt")


@hook.command("threaddump", autohelp=False, permissions=["botcontrol"])
async def threaddump_command():
    """- Return a full thread dump"""
    return get_thread_dump()


@hook.command("objtypes", autohelp=False, permissions=["botcontrol"])
def show_types():
    """- Print object type data to the console"""
    if objgraph is None:
        return "objgraph not installed"
    objgraph.show_most_common_types(limit=20)
    return "Printed to console"


@hook.command("objgrowth", autohelp=False, permissions=["botcontrol"])
def show_growth():
    """- Print object growth data to the console"""
    if objgraph is None:
        return "objgraph not installed"
    objgraph.show_growth(limit=10)
    return "Printed to console"


@hook.command("pymsummary", autohelp=False, permissions=["botcontrol"])
def pympler_summary():
    """- Print object summary data to the console"""
    if pympler is None:
        return "pympler not installed / not enabled"
    all_objects = pympler.muppy.get_objects()
    summ = pympler.summary.summarize(all_objects)
    pympler.summary.print_(summ)
    return "Printed to console"


@hook.command("pymdiff", autohelp=False, permissions=["botcontrol"])
def pympler_diff():
    """- Print object diff data to the console"""
    if pympler is None:
        return "pympler not installed / not enabled"
    tr.print_diff()
    return "Printed to console"


@hook.command("memstats", autohelp=False, permissions=["botcontrol"])
def memory_stats():
    """- Get current memory statistics"""
    process = psutil.Process()
    mem_info = process.memory_info()
    mem_full = process.memory_full_info()

    rss_mb = mem_info.rss / 1024 / 1024
    vms_mb = mem_info.vms / 1024 / 1024
    uss_mb = mem_full.uss / 1024 / 1024

    return (
        f"RSS: {rss_mb:.1f} MB | VMS: {vms_mb:.1f} MB | USS (Unique): {uss_mb:.1f} MB"
    )


@hook.command("memtop", autohelp=False, permissions=["botcontrol"])
def memory_top():
    """- Get top memory-consuming object types"""
    gc.collect()
    all_objects = gc.get_objects()

    type_counts = Counter(type(obj).__name__ for obj in all_objects)

    lines = ["Top 15 object types by count:"]
    for obj_type, count in type_counts.most_common(15):
        lines.append(f"{count:>8,} {obj_type}")

    return web.paste("\n".join(lines), ext="txt")


@hook.command("memlarge", autohelp=False, permissions=["botcontrol"])
def memory_large():
    """- Find largest objects in memory"""
    gc.collect()
    all_objects = gc.get_objects()

    large_objects = []
    for obj in all_objects:
        try:
            size = sys.getsizeof(obj)
            if size > 50000:  # > 50 KB
                obj_type = type(obj).__name__
                # Try to get more info for dicts/lists
                if obj_type == "dict":
                    obj_type = f"dict (len={len(obj)})"
                elif obj_type == "list":
                    obj_type = f"list (len={len(obj)})"
                elif obj_type == "deque":
                    obj_type = f"deque (len={len(obj)})"
                large_objects.append((obj_type, size))
        except:
            pass

    large_objects.sort(key=lambda x: x[1], reverse=True)

    lines = ["Largest objects (>50KB):"]
    for obj_type, size in large_objects[:30]:
        lines.append(f"{size / 1024 / 1024:>8.2f} MB - {obj_type}")

    return web.paste("\n".join(lines), ext="txt")


@hook.command("memcaches", autohelp=False, permissions=["botcontrol"])
def memory_caches(bot):
    """- Inspect plugin cache sizes"""
    lines = ["Plugin cache sizes:"]

    # Check known cache dictionaries
    caches_to_check = [
        ("bot.memory", bot.memory),
        ("conn.memory", None),  # Would need conn object
        ("conn.history", None),  # Would need conn object
    ]

    # Import and check plugin caches
    try:
        from plugins import pollinations, gpt, ollama

        caches_to_check.extend(
            [
                (
                    "pollinations_messages_cache",
                    pollinations.pollinations_messages_cache,
                ),
                ("pollinations user_models", pollinations.user_models),
                ("gpt_messages_cache", gpt.gpt_messages_cache),
                ("ollama_messages_cache", ollama.ollama_messages_cache),
                ("ollama user_models", ollama.user_models),
            ]
        )
    except ImportError:
        pass

    for name, cache in caches_to_check:
        if cache is None:
            continue
        try:
            size = sys.getsizeof(cache)
            count = len(cache) if hasattr(cache, "__len__") else "?"
            lines.append(f"{name}: {count} entries, ~{size / 1024:.1f} KB")
        except:
            lines.append(f"{name}: <error>")

    return web.paste("\n".join(lines), ext="txt")


@hook.command("memtrace", autohelp=False, permissions=["botcontrol"])
def memory_trace(bot):
    """- Complete memory trace with stats, top objects, and large objects"""
    lines = []

    # Header with timestamp
    import datetime

    lines.append(f"Memory Trace - {datetime.datetime.now().isoformat()}")
    lines.append("=" * 80)
    lines.append("")

    # Section 1: Memory Statistics
    lines.append("MEMORY STATISTICS")
    lines.append("-" * 80)
    process = psutil.Process()
    mem_info = process.memory_info()
    mem_full = process.memory_full_info()

    rss_mb = mem_info.rss / 1024 / 1024
    vms_mb = mem_info.vms / 1024 / 1024
    uss_mb = mem_full.uss / 1024 / 1024

    lines.append(f"RSS (Resident Set Size):    {rss_mb:>10.2f} MB")
    lines.append(f"VMS (Virtual Memory Size):  {vms_mb:>10.2f} MB")
    lines.append(f"USS (Unique Set Size):      {uss_mb:>10.2f} MB")
    lines.append("")

    # Section 2: Top Object Types
    lines.append("TOP OBJECT TYPES BY COUNT")
    lines.append("-" * 80)
    gc.collect()
    all_objects = gc.get_objects()
    type_counts = Counter(type(obj).__name__ for obj in all_objects)

    for obj_type, count in type_counts.most_common(20):
        lines.append(f"{count:>10,}  {obj_type}")
    lines.append("")

    # Section 3: Largest Objects
    lines.append("LARGEST OBJECTS (>50 KB)")
    lines.append("-" * 80)
    large_objects = []
    for obj in all_objects:
        try:
            size = sys.getsizeof(obj)
            if size > 50000:  # > 50 KB
                obj_type = type(obj).__name__
                # Try to get more info for dicts/lists
                if obj_type == "dict":
                    obj_type = f"dict(len={len(obj)})"
                elif obj_type == "list":
                    obj_type = f"list(len={len(obj)})"
                elif obj_type == "deque":
                    obj_type = f"deque(len={len(obj)})"
                elif obj_type == "str":
                    preview = repr(obj[:100]) if len(obj) > 100 else repr(obj)
                    obj_type = f"str(len={len(obj)}) {preview}"
                large_objects.append((obj_type, size))
        except:
            pass

    large_objects.sort(key=lambda x: x[1], reverse=True)

    for obj_type, size in large_objects[:40]:
        lines.append(f"{size / 1024 / 1024:>10.2f} MB  {obj_type}")
    lines.append("")

    # Section 4: Plugin Caches
    lines.append("PLUGIN CACHE SIZES")
    lines.append("-" * 80)
    lines.append(f"bot.memory entries: {len(bot.memory)}")

    # Check known plugin caches
    try:
        from plugins import pollinations

        lines.append(
            f"pollinations_messages_cache: {len(pollinations.pollinations_messages_cache)} users"
        )
        lines.append(f"pollinations user_models: {len(pollinations.user_models)} users")
    except ImportError:
        lines.append("pollinations: not loaded")

    try:
        from plugins import gpt

        lines.append(f"gpt_messages_cache: {len(gpt.gpt_messages_cache)} users")
    except ImportError:
        lines.append("gpt: not loaded")

    try:
        from plugins import ollama

        lines.append(
            f"ollama_messages_cache: {len(ollama.ollama_messages_cache)} users"
        )
        lines.append(f"ollama user_models: {len(ollama.user_models)} users")
    except ImportError:
        lines.append("ollama: not loaded")

    lines.append("")
    lines.append("=" * 80)
    lines.append(f"Total objects tracked by gc: {len(all_objects):,}")

    return web.paste("\n".join(lines), ext="txt")


# # Provide an easy way to get a threaddump, by using SIGUSR1 (only on POSIX systems)
if os.name == "posix":
    # The handler is called with two arguments: the signal number and the current stack frame
    # These parameters should NOT be removed
    # noinspection PyUnusedLocal
    def debug(sig, frame):
        print(get_thread_dump())

    signal.signal(signal.SIGUSR1, debug)  # Register handler
