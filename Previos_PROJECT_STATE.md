# Project State

## Current Phase
Phase 2 — Chart of Accounts & Accounting (not started)

## Last Completed
Phase 1 — Project Setup, Custom Auth & Core Models (2026-04-17)

## Next Up
Phase 2 — Chart of Accounts & Accounting

## Completed Phases
- [x] Phase 1 — Project Setup, Custom Auth & Core Models

## Phase 1 Deliverables
- `requirements.txt` (runtime), `requirements-dev.txt` (dev-only), `requirements.lock.txt` (frozen)
- `docker-compose.dev.yml` with RabbitMQ service (not started — user manages containers)
- `.env` / `.env.example` via `django-environ`
- Django project `meili_property` with settings wired for PostgreSQL, argon2, WhiteNoise, timezone Africa/Kampala, UTC storage
- `accounts` app — custom `User` (email as USERNAME_FIELD, E.164 phone), `Role`, `UserRole`, `LoginAttempt`, `PasswordResetToken`, Argon2 hashing, `has_role`/`has_any_role`, `@role_required`, `RoleRequiredMixin`
- `AUTH_USER_MODEL = 'accounts.User'` set before any migration
- `core` app — `UGXField` (whole-number enforced), `USDField` (2dp rounded), `TimeStampedModel`, `SoftDeleteModel`, `CoreBaseModel`, `PaginatedListView` mixin
- Models: `Currency`, `BillingCycle`, `TaxType`, `Landlord`, `Estate`, `House`, `Employee`, `Tenant`, `TenantHouse`, `Supplier` — all with `django-simple-history`
- `get_effective_setting(house, field_name)` utility — house overrides estate
- Celery 5.6.3 wired with namespace `CELERY_`, autodiscover, RabbitMQ broker, `django-celery-results` backend (`django-db`), `django-celery-beat` scheduler
- Smoke-test Celery task `meili_property.ping` registered (not yet smoke-tested end-to-end — requires user to start RabbitMQ + worker)
- Seed migrations: 8 roles, 2 currencies (UGX primary, USD), 8 billing cycles, 2 inactive taxes (VAT-18, WHT-6)
- `python manage.py create_initial_superuser --email ... --phone +256... --first-name ... --last-name ... --password ...` command
- `templates/core/pagination.html` partial
- django-axes configured for 5-attempt / 15-min lockout
- Admin registered for all models (SimpleHistoryAdmin on historied models)
- 24 unit tests passing (accounts: 9, core: 15)

## Outstanding Phase 1 Verification (user-driven)
- [ ] User starts RabbitMQ: `docker compose -f docker-compose.dev.yml up -d rabbitmq`
- [ ] User starts Celery worker + beat + Flower in separate terminals (see SPEC §2.5)
- [ ] End-to-end ping: `python manage.py shell -c "from meili_property.celery import ping; r = ping.delay(); print(r.get(timeout=10))"` should print `pong` and appear in `django_celery_results_taskresult`
- [ ] User creates their superuser:
  `python manage.py create_initial_superuser --email <email> --phone +256... --first-name <> --last-name <> --password <strong>`

## Decisions Log
- 2026-04-17 — Python 3.14.3 in `meili` venv (exceeds 3.12 minimum; Django 6.0.4 compatible).
- 2026-04-17 — Added `SUPER_ADMIN` role in addition to the 7 roles in SPEC §2A.1 because §16.9 matrix requires a distinct Super Admin tier (draft deletion). `create_initial_superuser` assigns both `SUPER_ADMIN` and `ADMIN`.
- 2026-04-17 — `Landlord.landlord` on `House` is a nullable override (defaults to `estate.landlord` via `House.effective_landlord`). Avoids duplicating landlord on every house row while allowing inter-estate exceptions.
- 2026-04-17 — `Employee` auto-creation on `User` insert (SPEC §2A.5) deferred to Phase 1.5 / Phase 2 — not strictly required for Phase 1 model layer.
- 2026-04-17 — WhiteNoise used for static files (compressed manifest storage); gunicorn pinned for Linux prod, waitress kept for Windows prod testing.
- 2026-04-17 — django-crispy-forms/crispy-bootstrap5 intentionally omitted — SPEC §2.1 allows hand-rolled forms for design control. Will revisit in Phase 3 if form scaffolding becomes a bottleneck.
- 2026-04-17 — `tax_type` modelled as FK on `SettingsMixin` (single effective tax per scope). Multi-tax stacking deferred until accounting engine is implemented.

## Tech Debt / Deferred
- Views, templates, login/logout/password-reset UI — deferred to Phase 3 (Portals & UI).
- Employee auto-provisioning of `User` with temp password + email — deferred to Phase 3.
- Admin settings page (SPEC §2A.6) — deferred to Phase 3.
- `django-ratelimit` installed but not yet applied to login / password-reset / API endpoints.
- CSS variable theme + base.html — deferred to Phase 3.
- `cleanup_old_task_results` beat schedule — deferred to Phase 8 monitoring.
- Celery ping smoke test — requires user to start containers/processes (user-driven, above).

## Known Issues
- None.

## Running Processes (user-managed — NOT the agent)
- `docker compose -f docker-compose.dev.yml up -d rabbitmq` (broker)
- `python manage.py runserver`
- `celery -A meili_property worker --pool=solo --loglevel=info`
- `celery -A meili_property beat --scheduler django_celery_beat.schedulers:DatabaseScheduler --loglevel=info`
- `celery -A meili_property flower --port=5555`
