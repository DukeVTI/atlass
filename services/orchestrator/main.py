"""
Atlas Orchestrator Service — Layer 1 Stub
------------------------------------------
The brain of Atlas: LLM routing, tool loop, skill execution.
Full implementation (Claude Haiku 3, butler loop, tool registry) comes in Layer 3.
This stub starts cleanly so docker-compose Layer 1 health checks pass.
"""

import logging
import os
import time

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)
logger = logging.getLogger("atlas.orchestrator")


def main() -> None:
    logger.info("Atlas Orchestrator service starting — Layer 3 implementation pending.")
    logger.info("Claude Haiku 3 routing and butler loop will be activated in Layer 3.")
    while True:
        time.sleep(60)


if __name__ == "__main__":
    main()
