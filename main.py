"""
Aegixa entry point.
Runs Flask in a daemon thread so Railway sees a bound port,
then starts the Discord bot with asyncio.
"""

import asyncio
import logging
import os
import threading

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger(__name__)


def run_web(app):
    port = int(os.getenv("PORT", 8080))
    log.info("Starting web dashboard on port %d", port)
    # Use threaded=False — Flask handles requests in the same thread,
    # which is fine since all heavy work is delegated to the bot loop.
    app.run(host="0.0.0.0", port=port, threaded=True, use_reloader=False)


async def main():
    from bot import create_bot
    from web.app import create_app

    bot = create_bot()
    app = create_app(bot)

    # Start Flask in a background daemon thread
    web_thread = threading.Thread(target=run_web, args=(app,), daemon=True, name="flask")
    web_thread.start()

    token = os.getenv("DISCORD_TOKEN")
    if not token:
        raise RuntimeError("DISCORD_TOKEN environment variable is not set.")

    async with bot:
        await bot.start(token)


if __name__ == "__main__":
    asyncio.run(main())
