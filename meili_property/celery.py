import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "meili_property.settings")

app = Celery("meili_property")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()


@app.task(bind=True, ignore_result=False, name="meili_property.ping")
def ping(self):
    """Smoke-test task — verifies broker + worker + result backend loop."""
    return "pong"
