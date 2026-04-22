"""Development settings — Windows dev box.

Loaded by default when DJANGO_ENV is unset or != "prod".
"""
from .base import *  # noqa: F401,F403
from .base import env

DEBUG = True
ALLOWED_HOSTS = env("ALLOWED_HOSTS", default=["localhost", "127.0.0.1", "*"])

# Loose cookies over plain HTTP in dev
SESSION_COOKIE_SECURE = False
CSRF_COOKIE_SECURE = False

# Manifest storage requires collectstatic; skip in dev for convenience
STORAGES["staticfiles"]["BACKEND"] = (  # noqa: F405
    "django.contrib.staticfiles.storage.StaticFilesStorage"
)

# Console email by default (overridable via EMAIL_BACKEND env)
# -- inherited from base

# Django Debug Toolbar is intentionally NOT installed — keep surface minimal.

INTERNAL_IPS = ["127.0.0.1"]
