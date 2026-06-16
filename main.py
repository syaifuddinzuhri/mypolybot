import sys
import os
import logging
import uvicorn
from loguru import logger
from src.config import settings
from src.bridge_server import app
from src.bot import init_state


def setup_logging():
    os.environ["TZ"] = "Asia/Jakarta"  # WIB = UTC+7
    try:
        import time; time.tzset()       # apply di Unix/Mac
    except AttributeError:
        pass                            # Windows tidak perlu

    logger.remove()
    logger.add(sys.stdout, level="INFO", colorize=True,
               format="<green>{time:HH:mm:ss}</green> | <level>{level: <7}</level> | {message}",
               enqueue=True)

    # Semua log (termasuk DEBUG) → file
    logger.add("data/polybot.log", level="DEBUG", rotation="10 MB", retention="7 days",
               format="{time:YYYY-MM-DD HH:mm:ss} | {level} | {message}", enqueue=True)

    # Uvicorn access log (endpoint hits) → file terpisah, tidak ke terminal
    access_logger = logging.getLogger("uvicorn.access")
    access_logger.handlers.clear()
    file_handler = logging.FileHandler("data/access.log")
    file_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    access_logger.addHandler(file_handler)
    access_logger.propagate = False


def main():
    setup_logging()
    logger.info(f"[POLYBOT] Starting on {settings.bridge_host}:{settings.bridge_port}")
    logger.info(f"[POLYBOT] Symbols: {settings.symbol_list}")
    logger.info("[POLYBOT] Endpoint hits → data/access.log | Debug lengkap → data/polybot.log")
    init_state()
    uvicorn.run(
        app,
        host=settings.bridge_host,
        port=settings.bridge_port,
        log_level="warning",   # suppress uvicorn startup noise di terminal
        access_log=True,
    )


if __name__ == "__main__":
    main()
