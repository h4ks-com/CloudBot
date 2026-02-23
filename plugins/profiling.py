import asyncio
import gc
import os
import signal
import sys
import threading
import time
import traceback
import tracemalloc
from collections import Counter
from datetime import datetime

import objgraph
import psutil
import pympler
import pympler.muppy
import pympler.summary
import pympler.tracker

from cloudbot import hook
from cloudbot.util import web

tr = pympler.tracker.SummaryTracker()

# Start tracemalloc on module load
if not tracemalloc.is_tracing():
    tracemalloc.start(10)  # 10 frames of traceback

# Memory monitoring state
monitor_task = None
monitor_active = False
monitor_snapshots = []
MAX_SNAPSHOTS = 20  # Keep last 20 snapshots


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
    objgraph.show_most_common_types(limit=20)
    return "Printed to console"


@hook.command("objgrowth", autohelp=False, permissions=["botcontrol"])
def show_growth():
    """- Print object growth data to the console"""
    objgraph.show_growth(limit=10)
    return "Printed to console"


@hook.command("pymsummary", autohelp=False, permissions=["botcontrol"])
def pympler_summary():
    """- Print object summary data to the console"""
    all_objects = pympler.muppy.get_objects()
    summ = pympler.summary.summarize(all_objects)
    pympler.summary.print_(summ)
    return "Printed to console"


@hook.command("pymdiff", autohelp=False, permissions=["botcontrol"])
def pympler_diff():
    """- Print object diff data to the console"""
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

    return web.paste("\n".join(lines), ext="txt")


@hook.command("memtracemalloc", autohelp=False, permissions=["botcontrol"])
def memory_tracemalloc():
    """- Show memory allocations using tracemalloc (includes C extensions)"""
    lines = []
    lines.append("TRACEMALLOC MEMORY SNAPSHOT")
    lines.append("=" * 80)
    lines.append("")

    if not tracemalloc.is_tracing():
        tracemalloc.start(10)
        lines.append("Started tracemalloc, run this command again in a few minutes...")
        return web.paste("\n".join(lines), ext="txt")

    snapshot = tracemalloc.take_snapshot()
    top_stats = snapshot.statistics("lineno")

    lines.append("TOP 50 MEMORY ALLOCATIONS BY FILE:LINE")
    lines.append("-" * 80)

    for i, stat in enumerate(top_stats[:50], 1):
        lines.append(
            f"\n#{i} - {stat.size / 1024 / 1024:.2f} MB, {stat.count:,} blocks"
        )
        lines.append(f"  {stat.traceback.format()[0]}")

    lines.append("")
    lines.append("=" * 80)

    # Get current and peak memory
    current, peak = tracemalloc.get_traced_memory()
    lines.append(f"Current traced: {current / 1024 / 1024:.2f} MB")
    lines.append(f"Peak traced: {peak / 1024 / 1024:.2f} MB")

    return web.paste("\n".join(lines), ext="txt")


@hook.command("memrefs", autohelp=False, permissions=["botcontrol"])
def memory_refs(bot):
    """- Find what's referencing large objects"""
    lines = []
    lines.append("MEMORY REFERENCE ANALYSIS")
    lines.append("=" * 80)
    lines.append("")

    gc.collect()

    # Check bot.memory
    lines.append(f"bot.memory keys: {list(bot.memory.keys())}")
    lines.append(f"bot.memory size: {sys.getsizeof(bot.memory) / 1024:.2f} KB")

    for key, value in bot.memory.items():
        try:
            size = sys.getsizeof(value)
            lines.append(f"  {key}: {size / 1024:.2f} KB ({type(value).__name__})")

            # If it's hook_stats, show top hooks
            if key == "hook_stats" and isinstance(value, dict):
                lines.append(f"    Hook stats entries: {len(value)}")
                # Sort by call count
                sorted_stats = sorted(
                    value.items(), key=lambda x: x[1].get("calls", 0), reverse=True
                )
                lines.append("    Top 20 most called hooks:")
                for hook_name, stats in sorted_stats[:20]:
                    calls = stats.get("calls", 0)
                    lines.append(f"      {hook_name}: {calls:,} calls")
        except Exception as e:
            lines.append(f"  {key}: error - {e}")

    lines.append("")

    # Check for conn.memory on all connections
    lines.append("CONNECTION MEMORY:")
    lines.append("-" * 80)

    for conn_name, conn in bot.connections.items():
        lines.append(f"\nConnection: {conn_name}")
        if hasattr(conn, "memory"):
            lines.append(f"  conn.memory keys: {list(conn.memory.keys())[:10]}")
            lines.append(
                f"  conn.memory size: {sys.getsizeof(conn.memory) / 1024:.2f} KB"
            )

            for key, value in conn.memory.items():
                try:
                    size = sys.getsizeof(value)
                    if size > 10000:
                        lines.append(
                            f"    {key}: {size / 1024:.2f} KB ({type(value).__name__})"
                        )
                except:
                    pass

        if hasattr(conn, "history"):
            lines.append(f"  conn.history channels: {list(conn.history.keys())}")
            total_history_size = sum(sys.getsizeof(h) for h in conn.history.values())
            lines.append(
                f"  conn.history total size: {total_history_size / 1024:.2f} KB"
            )

    return web.paste("\n".join(lines), ext="txt")


@hook.command("memjson", autohelp=False, permissions=["botcontrol"])
def memory_json():
    """- Find large JSON-related objects in memory"""
    lines = []
    lines.append("JSON MEMORY ANALYSIS")
    lines.append("=" * 80)
    lines.append("")

    gc.collect()
    all_objects = gc.get_objects()

    # Look for large dicts that might be parsed JSON
    large_dicts = []
    large_lists = []
    large_strings = []

    for obj in all_objects:
        try:
            size = sys.getsizeof(obj)
            if size > 100000:  # > 100 KB
                if isinstance(obj, dict):
                    # Try to identify if it looks like JSON
                    sample_keys = list(obj.keys())[:5] if obj else []
                    large_dicts.append((size, len(obj), sample_keys, id(obj)))
                elif isinstance(obj, list):
                    large_lists.append((size, len(obj), id(obj)))
                elif isinstance(obj, str):
                    preview = obj[:200] if len(obj) > 200 else obj
                    large_strings.append((size, len(obj), preview, id(obj)))
        except:
            pass

    # Sort by size
    large_dicts.sort(reverse=True)
    large_lists.sort(reverse=True)
    large_strings.sort(reverse=True)

    lines.append(
        f"Found {len(large_dicts)} large dicts, {len(large_lists)} large lists, {len(large_strings)} large strings"
    )
    lines.append("")

    # Show large dicts (potential JSON objects)
    lines.append("LARGE DICTIONARIES (>100 KB, potential parsed JSON):")
    lines.append("-" * 80)
    for i, (size, length, keys, obj_id) in enumerate(large_dicts[:15], 1):
        lines.append(
            f"#{i} - {size / 1024 / 1024:.2f} MB, {length:,} keys, id={obj_id}"
        )
        lines.append(f"     Sample keys: {keys}")

        # Try to find what references this object
        referrers = gc.get_referrers(obj_id)
        lines.append(f"     Referenced by {len(referrers)} objects")

    lines.append("")
    lines.append("LARGE STRINGS (>100 KB, potential JSON strings):")
    lines.append("-" * 80)
    for i, (size, length, preview, obj_id) in enumerate(large_strings[:10], 1):
        lines.append(
            f"#{i} - {size / 1024 / 1024:.2f} MB, {length:,} chars, id={obj_id}"
        )
        lines.append(f"     Preview: {preview[:150]}...")

    return web.paste("\n".join(lines), ext="txt")


async def take_memory_snapshot(bot):
    """Take a memory snapshot and store it"""
    process = psutil.Process()
    mem_info = process.memory_info()

    snapshot = {
        "timestamp": datetime.now().isoformat(),
        "rss_mb": mem_info.rss / 1024 / 1024,
        "vms_mb": mem_info.vms / 1024 / 1024,
    }

    # Get tracemalloc snapshot if available
    if tracemalloc.is_tracing():
        tm_snapshot = tracemalloc.take_snapshot()
        top_stats = tm_snapshot.statistics("lineno")

        # Store top 10 allocations
        snapshot["top_allocations"] = []
        for stat in top_stats[:10]:
            snapshot["top_allocations"].append(
                {
                    "size_mb": stat.size / 1024 / 1024,
                    "count": stat.count,
                    "location": stat.traceback.format()[0]
                    if stat.traceback
                    else "unknown",
                }
            )

    # Get object counts
    gc.collect()
    all_objects = gc.get_objects()
    type_counts = Counter(type(obj).__name__ for obj in all_objects)
    snapshot["top_types"] = dict(type_counts.most_common(10))

    # Get bot.memory size
    snapshot["bot_memory_keys"] = list(bot.memory.keys())
    snapshot["bot_memory_size_kb"] = sys.getsizeof(bot.memory) / 1024

    # Store snapshot
    monitor_snapshots.append(snapshot)
    if len(monitor_snapshots) > MAX_SNAPSHOTS:
        monitor_snapshots.pop(0)

    return snapshot


async def memory_monitor_loop(bot, interval_minutes=30):
    """Background task to monitor memory every N minutes"""
    global monitor_active

    while monitor_active:
        try:
            snapshot = await take_memory_snapshot(bot)

            # Log to console
            print(
                f"[MEMORY MONITOR] RSS: {snapshot['rss_mb']:.1f} MB at {snapshot['timestamp']}"
            )

            # If RSS is high, log top allocations
            if snapshot["rss_mb"] > 500:
                print(f"[MEMORY MONITOR] WARNING: High memory usage detected!")
                if "top_allocations" in snapshot:
                    print("[MEMORY MONITOR] Top allocations:")
                    for i, alloc in enumerate(snapshot["top_allocations"][:3], 1):
                        print(
                            f"  #{i}: {alloc['size_mb']:.2f} MB - {alloc['location']}"
                        )

            # Wait for next interval
            await asyncio.sleep(interval_minutes * 60)

        except asyncio.CancelledError:
            print("[MEMORY MONITOR] Stopped")
            break
        except Exception as e:
            print(f"[MEMORY MONITOR] Error: {e}")
            await asyncio.sleep(60)  # Wait 1 min on error


@hook.command("memmonitor", permissions=["botcontrol"])
async def memory_monitor_command(text, bot):
    """<start|stop|status|report> - Start/stop automatic memory monitoring"""
    global monitor_task, monitor_active

    if not text:
        return "Usage: .memmonitor <start|stop|status|report> [interval_minutes]"

    parts = text.split()
    action = parts[0].lower()

    if action == "start":
        if monitor_active:
            return "Memory monitor is already running"

        interval = 30  # Default 30 minutes
        if len(parts) > 1:
            try:
                interval = int(parts[1])
                if interval < 1:
                    return "Interval must be at least 1 minute"
            except ValueError:
                return "Invalid interval, must be a number"

        monitor_active = True
        monitor_task = asyncio.create_task(memory_monitor_loop(bot, interval))

        # Take initial snapshot
        await take_memory_snapshot(bot)

        return f"Memory monitor started. Will check every {interval} minutes and log to console. Use .memmonitor report to view snapshots."

    elif action == "stop":
        if not monitor_active:
            return "Memory monitor is not running"

        monitor_active = False
        if monitor_task and not monitor_task.done():
            monitor_task.cancel()

        return "Memory monitor stopped"

    elif action == "status":
        if monitor_active:
            return f"Memory monitor is ACTIVE. {len(monitor_snapshots)} snapshots collected."
        else:
            return f"Memory monitor is STOPPED. {len(monitor_snapshots)} snapshots available."

    elif action == "report":
        if not monitor_snapshots:
            return "No snapshots available. Start monitor with .memmonitor start"

        lines = []
        lines.append("MEMORY MONITOR REPORT")
        lines.append("=" * 80)
        lines.append(f"Collected {len(monitor_snapshots)} snapshots")
        lines.append("")

        # Show growth over time
        if len(monitor_snapshots) >= 2:
            first = monitor_snapshots[0]
            last = monitor_snapshots[-1]

            rss_growth = last["rss_mb"] - first["rss_mb"]
            time_first = datetime.fromisoformat(first["timestamp"])
            time_last = datetime.fromisoformat(last["timestamp"])
            duration_hours = (time_last - time_first).total_seconds() / 3600

            lines.append("MEMORY GROWTH ANALYSIS")
            lines.append("-" * 80)
            lines.append(f"First snapshot: {first['timestamp']}")
            lines.append(f"Last snapshot:  {last['timestamp']}")
            lines.append(f"Duration:       {duration_hours:.1f} hours")
            lines.append(f"RSS growth:     {rss_growth:+.1f} MB")
            if duration_hours > 0:
                lines.append(
                    f"Growth rate:    {rss_growth / duration_hours:.2f} MB/hour"
                )
            lines.append("")

        # Show all snapshots
        lines.append("ALL SNAPSHOTS")
        lines.append("-" * 80)
        for i, snap in enumerate(monitor_snapshots, 1):
            lines.append(f"#{i} - {snap['timestamp']}")
            lines.append(f"     RSS: {snap['rss_mb']:.1f} MB")
            lines.append(f"     VMS: {snap['vms_mb']:.1f} MB")
            lines.append(f"     bot.memory: {snap['bot_memory_size_kb']:.1f} KB")

            if "top_allocations" in snap and snap["top_allocations"]:
                lines.append("     Top allocation:")
                top = snap["top_allocations"][0]
                lines.append(f"       {top['size_mb']:.2f} MB - {top['location']}")
            lines.append("")

        # Show last snapshot's top allocations in detail
        if "top_allocations" in monitor_snapshots[-1]:
            lines.append("LAST SNAPSHOT - TOP 10 ALLOCATIONS")
            lines.append("-" * 80)
            for i, alloc in enumerate(monitor_snapshots[-1]["top_allocations"], 1):
                lines.append(
                    f"#{i} - {alloc['size_mb']:.2f} MB, {alloc['count']:,} blocks"
                )
                lines.append(f"     {alloc['location']}")

        return web.paste("\n".join(lines), ext="txt")

    else:
        return "Unknown action. Use: start, stop, status, or report"


@hook.command("memcompare", autohelp=False, permissions=["botcontrol"])
async def memory_compare(bot):
    """- Take two snapshots 5 minutes apart and compare"""
    lines = []
    lines.append("MEMORY COMPARISON (5 minute interval)")
    lines.append("=" * 80)
    lines.append("")

    lines.append("Taking first snapshot...")
    snap1 = await take_memory_snapshot(bot)

    lines.append(f"First snapshot: RSS = {snap1['rss_mb']:.1f} MB")
    lines.append("Waiting 5 minutes...")
    lines.append("")

    # This will block for 5 minutes - notify user
    await asyncio.sleep(300)  # 5 minutes

    lines.append("Taking second snapshot...")
    snap2 = await take_memory_snapshot(bot)

    lines.append(f"Second snapshot: RSS = {snap2['rss_mb']:.1f} MB")
    lines.append("")

    # Calculate differences
    rss_diff = snap2["rss_mb"] - snap1["rss_mb"]
    vms_diff = snap2["vms_mb"] - snap1["vms_mb"]

    lines.append("MEMORY CHANGE")
    lines.append("-" * 80)
    lines.append(f"RSS change: {rss_diff:+.2f} MB")
    lines.append(f"VMS change: {vms_diff:+.2f} MB")
    lines.append(f"Growth rate: {rss_diff * 12:.2f} MB/hour (estimated)")
    lines.append("")

    # Compare object counts
    lines.append("OBJECT COUNT CHANGES")
    lines.append("-" * 80)

    types1 = snap1["top_types"]
    types2 = snap2["top_types"]

    all_types = set(types1.keys()) | set(types2.keys())
    changes = []
    for obj_type in all_types:
        count1 = types1.get(obj_type, 0)
        count2 = types2.get(obj_type, 0)
        diff = count2 - count1
        if diff != 0:
            changes.append((obj_type, count1, count2, diff))

    # Sort by absolute difference
    changes.sort(key=lambda x: abs(x[3]), reverse=True)

    for obj_type, count1, count2, diff in changes[:15]:
        lines.append(f"{obj_type:20s}: {count1:>8,} -> {count2:>8,} ({diff:+,})")

    lines.append("")

    # Compare top allocations
    if "top_allocations" in snap1 and "top_allocations" in snap2:
        lines.append("TOP ALLOCATION CHANGES")
        lines.append("-" * 80)

        # Create a map of location -> size
        alloc1_map = {a["location"]: a["size_mb"] for a in snap1["top_allocations"]}
        alloc2_map = {a["location"]: a["size_mb"] for a in snap2["top_allocations"]}

        all_locations = set(alloc1_map.keys()) | set(alloc2_map.keys())
        alloc_changes = []

        for location in all_locations:
            size1 = alloc1_map.get(location, 0)
            size2 = alloc2_map.get(location, 0)
            diff = size2 - size1
            if abs(diff) > 0.1:  # Only show changes > 0.1 MB
                alloc_changes.append((location, size1, size2, diff))

        alloc_changes.sort(key=lambda x: abs(x[3]), reverse=True)

        for location, size1, size2, diff in alloc_changes[:10]:
            lines.append(f"{location}")
            lines.append(f"  {size1:.2f} MB -> {size2:.2f} MB ({diff:+.2f} MB)")

    return web.paste("\n".join(lines), ext="txt")


# # Provide an easy way to get a threaddump, by using SIGUSR1 (only on POSIX systems)
if os.name == "posix":
    # The handler is called with two arguments: the signal number and the current stack frame
    # These parameters should NOT be removed
    # noinspection PyUnusedLocal
    def debug(sig, frame):
        print(get_thread_dump())

    signal.signal(signal.SIGUSR1, debug)  # Register handler
