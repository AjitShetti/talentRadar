import asyncio
from celery import Celery
from config.settings import get_settings

settings = get_settings()

celery_app = Celery(
    "talentRadar_ingestion",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["ingestion.tasks"]
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)
