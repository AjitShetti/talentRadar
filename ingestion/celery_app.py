import asyncio
from celery import Celery
from config.settings import get_settings

settings = get_settings()

celery_app = Celery(
    "talentRadar_ingestion",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["ingestion.tasks", "agents.tasks"]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        # Daily crawl covering India's major tech hubs.
        # 24 h cadence balances freshness against Tavily API cost;
        # job postings rarely change more than once a day.
        "daily_ats_crawler": {
            "task": "ingestion.tasks.run_crawler",
            "schedule": 86400.0,
            "kwargs": {
                # Core roles for the Indian tech/product job market
                "roles": [
                    "Software Engineer",
                    "Data Scientist",
                    "Product Manager",
                ],
                # Tier-1 Indian tech hubs + remote positions
                "locations": [
                    "Remote",
                    "Bangalore",
                    "Mumbai",
                    "Delhi",
                    "Hyderabad",
                    "Pune",
                    "India",
                ],
                # 10 results × 3 roles × 7 locations = 210 max fetches
                # per run — stays well within Tavily rate limits while
                # providing broad coverage.
                "max_results_per_query": 10,
                # Indian job boards + global aggregators with IN presence
                "include_domains": [
                    "linkedin.com",
                    "naukri.com",
                    "indeed.com",
                    "in.indeed.com",
                ],
            },
        },
    }
)
