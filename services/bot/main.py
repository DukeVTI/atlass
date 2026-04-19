"""
Atlas Bot Service — Layer 1 Stub
---------------------------------
Telegram gateway service. Full implementation comes in Layer 2.
This stub starts cleanly so docker-compose Layer 1 health checks pass.
"""

import logging
import os
import time

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("atlas.bot")


def main() -> None:
    logger.info("Atlas Bot service starting — Layer 2 implementation pending.")
    logger.info("Telegram gateway will be activated in Layer 2.")
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
