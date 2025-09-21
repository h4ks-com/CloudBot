import asyncio
import logging
import os
import signal
import sys
import time
from pathlib import Path

from uvicorn import Config, Server

from cloudbot.bot import CloudBot
from cloudbot.util import async_util
from cloudbot.webhooks.app import app, get_port, is_enabled, setup_app


async def async_main():
    # store the original working directory, for use when restarting
    original_wd = Path().resolve()

    # Logging optimizations, doing it here because we only want to change this if we're the main file
    logging._srcfile = None
    logging.logThreads = False
    logging.logProcesses = False

    logger = logging.getLogger("cloudbot")
    logger.info("Starting CloudBot.")

    # create the bot
    run_path = os.environ.get("CLOUDBOT_RUN_PATH")
    if run_path:
        _bot = CloudBot(config_dir=Path(run_path))
    else:
        _bot = CloudBot()

    # setup webhooks app
    setup_app()

    # whether we are killed while restarting
    stopped_while_restarting = False

    # store the original SIGINT handler
    original_sigint = signal.getsignal(signal.SIGINT)

    # define closure for signal handling
    # The handler is called with two arguments: the signal number and the current stack frame
    # These parameters should NOT be removed
    # noinspection PyUnusedLocal
    def exit_gracefully(signum, frame):
        nonlocal stopped_while_restarting
        if not _bot:
            # we are currently in the process of restarting
            stopped_while_restarting = True
        else:
            async_util.run_coroutine_threadsafe(
                _bot.stop(f"Killed (Received SIGINT {signum})"),
                _bot.loop,
            )

        logger.warning("Bot received Signal Interrupt (%s)", signum)

        # restore the original handler so if they do it again it triggers
        signal.signal(signal.SIGINT, original_sigint)

    signal.signal(signal.SIGINT, exit_gracefully)

    # start the web server if enabled
    server_task = None
    if is_enabled():
        port = get_port()
        config = Config(app=app, host="0.0.0.0", port=port)
        server = Server(config)
        server_task = server.serve()
    else:
        # Create a dummy task that never completes
        server_task = asyncio.create_task(asyncio.sleep(float("inf")))

    # start the bot and web server concurrently
    # CloudBot.run() will return True if it should restart, False otherwise
    restart, _ = await asyncio.gather(_bot.run(), server_task)

    # the bot has stopped, do we want to restart?
    if restart:
        # remove reference to cloudbot, so exit_gracefully won't try to stop it
        _bot = None
        # sleep one second for timeouts
        time.sleep(1)
        if stopped_while_restarting:
            logger.info("Received stop signal, no longer restarting")
        else:
            # actually restart
            os.chdir(str(original_wd))
            args = sys.argv
            logger.info("Restarting Bot")
            logger.debug("Restart arguments: %s", args)
            for f in [sys.stdout, sys.stderr]:
                f.flush()

            # close logging, and exit the program.
            logger.debug("Stopping logging engine")
            logging.shutdown()
            os.execv(sys.executable, [sys.executable] + args)  # nosec

    # close logging, and exit the program.
    logger.debug("Stopping logging engine")
    logging.shutdown()


def main():
    asyncio.run(async_main())


if __name__ == "__main__":
    main()
