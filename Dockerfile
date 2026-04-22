# syntax=docker/dockerfile:1.7
# Meili Property — production image.
# Single image reused by web, celery_worker, celery_beat, flower (different
# commands in docker-compose.prod.yml). Keeps build cache shared.

FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DJANGO_ENV=prod \
    DJANGO_SETTINGS_MODULE=meili_property.settings.prod

# System deps — libpq for psycopg, weasyprint has native deps, gettext for i18n
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    libpango-1.0-0 \
    libpangoft2-1.0-0 \
    libcairo2 \
    libffi-dev \
    libjpeg-dev \
    zlib1g-dev \
    gettext \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first for cache efficiency
COPY requirements.txt ./
RUN pip install -r requirements.txt

# Non-root runtime user
RUN useradd --create-home --shell /bin/bash meili

COPY . .

# Collect static at build time so the image ships with prod assets.
# SECRET_KEY / DATABASE_URL are only needed at runtime for app logic;
# collectstatic just needs them to import settings, so a dummy works.
RUN SECRET_KEY=build DATABASE_URL=postgres://build:build@localhost/build \
    ALLOWED_HOSTS=build DJANGO_ENV=prod \
    python manage.py collectstatic --noinput

RUN chown -R meili:meili /app
USER meili

EXPOSE 8000

# Default is the web process; docker-compose.prod.yml overrides for workers.
CMD ["gunicorn", "meili_property.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--timeout", "60", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
