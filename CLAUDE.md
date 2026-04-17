# Claude Code Operating Rules — Meili Property

## Environment
- Windows dev machine, Linux production (Ubuntu 24.04 LTS via Docker)
- Python 3.12+ required (Django 6.0)
- Virtualenv: `meili` (activate with `workon meili`) — **venv is bare, dependencies must be installed per Section 2.1 in Phase 1**
- Database: PostgreSQL, `meili_prd01`, user `postgres`, password `heaven2870`, localhost:5432
- RabbitMQ: Docker container, user `meili`, password `heaven2870_rmq`, vhost `meili`, port `5672`, management UI `15672`
- Docker Desktop required on Windows for RabbitMQ
- Python: use `py` or `python`, not `python3`
- Always verify `pip show <package>` before assuming a library is installed

## Background Tasks (CRITICAL — Celery stack)
- Use **Celery 5.6.3 + RabbitMQ + django-celery-beat + django-celery-results + Flower**
- **NO django.tasks, NO django-tasks package, NO Redis** — not production-ready / not needed
- RabbitMQ is the broker (via Docker), PostgreSQL is the result backend (via `django-celery-results`), `django-celery-beat` is the scheduler (DB-backed, admin-editable)
- **Process lifecycle is the user's responsibility, NOT yours.** You write code and config; the user runs the processes.
- Do NOT run: `python manage.py runserver`, `celery worker`, `celery beat`, `celery flower`, `docker compose up/down/restart`.
- If a task requires a process to be running, ask the user to confirm it's running and reply "ready". Do not start it yourself.
- Never run more than one beat instance — duplicate schedules will fire. Document this in the README.

## Autonomy
- Execute without asking permission. No "should I proceed?" questions.
- Only pause for: dropping populated tables, deleting files outside project, force-pushing git, changing tech stack.

## Credit Efficiency
- Don't re-read SPEC.md unless ambiguous — rely on PROJECT_STATE.md.
- Write focused tests only: financial math, permissions, maker-checker. Skip trivial CRUD tests.
- Don't refactor unrelated code — note in PROJECT_STATE.md "Tech Debt" and move on.
- Don't over-explain. Short status updates only.
- Batch related edits. Don't narrate each file.

## Tech Stack (Fixed)
- Django 6.0 monolith, server-rendered templates
- PostgreSQL via psycopg 3 (NOT psycopg2)
- **Custom `accounts` app for auth — do NOT use Django's built-in `auth.User`**. `AUTH_USER_MODEL = 'accounts.User'` set from project init.
- Argon2 password hashing (`argon2-cffi`)
- **Celery + RabbitMQ + django-celery-beat + django-celery-results + Flower** for background work and scheduling — NOT django.tasks, NOT Redis
- DRF ONLY for inbound payment webhook + outbound notification aggregator
- `django-simple-history` for audit trail
- `httpx` for outbound HTTP (NOT `requests`)
- Select2 on every `<select>`
- CSS variables for theming — no hardcoded colours
- Vanilla JS, no frontend framework
- All libraries must be open source (MIT/BSD/Apache/LGPL) and actively maintained
- Production target is Linux — do not bake Windows-only assumptions into production code

## Hard Rules
- **UGX:** use `UGXField` (whole numbers only, decimals rejected at save)
- **USD:** use `USDField` (exactly 2 decimals)
- Never use generic `DecimalField` for money
- Timezone-aware datetimes, store UTC, display Africa/Kampala
- Soft delete only on core entities — never hard delete financial records
- **Invoice lifecycle (Section 16):** Issued/Paid/Voided/Overdue invoices can NEVER be deleted. Only Draft invoices can be deleted, only by Super Admin. To cancel an issued invoice, use Void (creates reversing journal entry, preserves record). To reduce an issued invoice, use Credit Note.
- **Voids, Credit Notes, Refunds always require maker-checker** — no trusted-employee bypass. Self-approval always blocked.
- **Sequential numbering integrity:** INV/CRN/REF/RCP numbers never reused. Voided invoices keep their number. Gaps in sequence are an audit red flag.
- Maker-checker required on manual financial entries (payments, ad-hoc charges) unless employee is Trusted. Voids/Credit Notes/Refunds are never bypassable even for Trusted employees.
- Self-approval blocked in all cases
- House-level settings override estate-level (use `get_effective_setting`)
- Tenants/landlords cannot edit own profiles; only employees can
- Only Admins can edit employee profiles
- **Commission Income is a standalone COA account** — do not merge with Rent Income
- **Two held-advance accounts** (liabilities): `Tenant Advance Payments Held — Managed Properties` (fiduciary) and `Tenant Advance Payments Held — Meili-Owned` (deferred revenue). Routed automatically by `get_advance_holding_account(house)` based on landlord ownership. Never merge. Landlord statements never show held balances. (See Section 20.)
- **Tenant exit with held advance:** strict application order — outstanding invoices → damages/ad-hoc charges → (optional, employee-approved) transfer to tenant's other active tenancies → refund remainder to tenant. Refunds go through maker-checker. (See Section 20.5.)
- Pagination default 50, options 20/50/100/150 — via `PaginatedListView` mixin, session-persistent
- No emojis in UI or code
- **Permission matrix (Section 16.9) enforced server-side** — UI hiding is not sufficient, `@role_required` / `RoleRequiredMixin` on every view and handler

## State Tracking
Update PROJECT_STATE.md at end of every session with:
- Current phase + last completed + next up
- Decisions log
- Tech debt / deferred items
- Known issues