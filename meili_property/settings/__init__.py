"""Settings package.

Selection is via DJANGO_SETTINGS_MODULE:
  - meili_property.settings.dev  (Windows dev; DEBUG=True; console email; WhiteNoise)
  - meili_property.settings.prod (Linux prod; DEBUG=False; Sentry; security headers)

Default when DJANGO_SETTINGS_MODULE is unset (e.g. manage.py on dev box):
import from `dev` so existing workflows keep working.
"""
import os

_env = os.environ.get("DJANGO_ENV", "dev").lower()

if _env == "prod":
    from .prod import *  # noqa: F401,F403
else:
    from .dev import *  # noqa: F401,F403
