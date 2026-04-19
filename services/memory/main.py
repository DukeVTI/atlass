"""
Atlas Memory Service — Layer 1 Stub
-------------------------------------
ChromaDB vector store and Postgres interfaces for Atlas.
Full implementation (user profile, semantic notes, vector retrieval) comes in Layer 7.
This stub starts cleanly so docker-compose Layer 1 health checks pass.
"""

import logging
import os
import time

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("atlas.memory")


def main() -> None:
    logger.info("Atlas Memory service starting — Layer 7 implementation pending.")
    logger.info("ChromaDB vector store and Postgres interfaces will be activated in Layer 7.")
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
