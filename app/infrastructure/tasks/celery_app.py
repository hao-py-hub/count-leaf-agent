"""Celery application factory kept outside API and Agent domain modules."""

from celery import Celery

from app.config import CELERY_RESULT_BACKEND, CELERY_TASK_BROKER_URL


celery_app = Celery(
    "phenotype_agent",
    broker=CELERY_TASK_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
    include=["app.infrastructure.tasks.phenotype_tasks"],
)
celery_app.conf.update(
    task_track_started=True,
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=True,
)
