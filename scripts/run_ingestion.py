"""
scripts/run_ingestion.py
~~~~~~~~~~~~~~~~~~~~~~~~
Run multi-source job ingestion pipeline directly and populate DB + ChromaDB.
"""

import asyncio
import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger("run_ingestion")


async def main():
    logger.info("Starting live job ingestion from Greenhouse, Ashby, Lever...")
    from ingestion.dispatcher import dispatch_ingestion

    target_roles = [
        "Software",
        "Engineer",
        "Developer",
        "Data",
        "Frontend",
        "Backend",
        "Full Stack",
        "Machine Learning",
        "DevOps",
        "Cloud",
        "AI",
        "Product",
        "Security",
    ]

    result = await dispatch_ingestion(
        roles=target_roles,
        sources=["greenhouse", "ashby"],
        max_results_per_query=10,
    )

    logger.info("Ingestion completed successfully!")
    logger.info("Summary: %s", result)


if __name__ == "__main__":
    asyncio.run(main())
