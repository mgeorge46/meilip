## 0. Agent Operating Rules (READ FIRST)

You are building this system in Claude Code on a **Windows** development machine. Follow these rules strictly — they are designed to save credits and produce a working system.

### 0.1 Autonomy
- You have full authority to make implementation decisions, write code, create files, run migrations, install packages, and execute tests **without asking for permission**.
- Do not ask "should I proceed?" or "would you like me to...?" — just do it and report what you did.
- **Only pause and ask before:**
  - Dropping or truncating a database table that has data in it
  - Deleting files outside the project directory
  - Force-pushing to git or rewriting git history
  - Changing the tech stack (Django, PostgreSQL, DRF for the two APIs, custom `accounts` app for auth) — the stack is fixed

### 0.2 Credit Efficiency (Critical)
- **Do not re-read `SPEC.md` every turn.** Read it once per phase and rely on `PROJECT_STATE.md` for progress tracking.
- **Do not write exhaustive tests.** Write focused tests for: financial math (commission, pro-rata, FIFO, tax), permission boundaries, and the maker-checker flow. Skip tests for simple CRUD and template rendering.
- **Do not refactor code outside the current task.** If you spot something to improve, note it in `PROJECT_STATE.md` under "Tech Debt" and move on.
- **Do not over-engineer.** Use Django's built-in tools (ModelForm, generic CBVs, admin) before reaching for custom solutions — but note auth is a deliberate exception: we use a custom `accounts` app (see Section 2). Celery + RabbitMQ is the fixed choice for background tasks and scheduling — do not substitute with anything else (see Section 2).
- **Do not explain code you just wrote** unless asked. Short status updates only: "Created Estate model with override logic. Migration applied. Moving to House model."
- **Batch file edits.** If you're making related changes across 5 files, do them in one go, not one at a time with status updates between.

### 0.3 State Tracking
Maintain a `PROJECT_STATE.md` file at the repo root. Update it at the end of every working session. Format:

```markdown
# Project State

## Current Phase: [e.g., Phase 3]
## Last Completed: [e.g., Estate CRUD views + templates]
## Next Up: [e.g., House CRUD with effective-settings resolver]

## Completed Phases
- [x] Phase 1: Core models, admin, soft delete, currency seed
- [x] Phase 2: Chart of accounts, journal entries, ledger view

## Decisions Log
- Using Django's `django-simple-history` for audit trail (Phase 8)
- Receipt numbers: `RCP-YYYYMM-NNNNN` format
- ...

## Tech Debt / Deferred
- Estate list view pagination not yet using AJAX Select2 (defer to Phase 3 polish)
- ...

## Known Issues
- ...
```

### 0.4 Environment (Windows dev, Linux prod)
- **Python:** 3.12 or 3.13 (Django 6.0 requires 3.12 minimum)
- **Python virtualenv:** `meili` — activate with `workon meili` (virtualenvwrapper-win is installed)
- **The venv is bare.** At the start of Phase 1, you must install all dependencies listed in Section 2.1 before any coding begins. Write `requirements.txt` and `requirements-dev.txt` at the repo root, then `pip install -r requirements.txt -r requirements-dev.txt`.
- **Docker Desktop required on Windows** — RabbitMQ runs in Docker to avoid native Windows install pain and keep dev/prod identical.
- **Assume packages are NOT installed.** Before using any library, verify it's in the venv with `pip show <package>`. If missing, install it and add to requirements.
- **Database:** PostgreSQL, database name `meili_prd01`, user `postgres`, password `heaven2870`, host `localhost`, port `5432`
- **RabbitMQ:** via Docker (see Section 2.4), user `meili`, password `heaven2870_rmq`, vhost `meili`, port `5672`, management UI port `15672`
- **Assume Windows paths** — use `pathlib.Path` in code, not hardcoded `/` or `\`
- **Dev server, Celery worker, Celery beat, Flower, and RabbitMQ are managed by the user (George), not by the agent.** The agent writes the code and configs. The user runs the processes in his own terminals. The agent does NOT run `python manage.py runserver`, `celery worker`, `celery beat`, `celery flower`, or any `docker compose up/down/restart` commands. If a task requires a process to be running (e.g., testing a Celery task), the agent asks the user to confirm the process is running and replies with "ready" before proceeding.
- **User commands for reference** (do not execute these — only inform the user when relevant):
  - Dev server: `python manage.py runserver`
  - Celery worker (Windows dev — solo pool required): `celery -A meili_property worker --pool=solo --loglevel=info`
  - Celery beat: `celery -A meili_property beat --scheduler django_celery_beat.schedulers:DatabaseScheduler --loglevel=info`
  - Flower: `celery -A meili_property flower --port=5555`
  - RabbitMQ: `docker compose -f docker-compose.dev.yml up -d rabbitmq`
- **Commands the agent DOES run:** `pip install`, `python manage.py makemigrations`, `python manage.py migrate`, `python manage.py shell` for ad-hoc queries, `pytest`, `ruff check/format`, `python manage.py createsuperuser`, git commands (commit, branch, push — never force-push).
- **Use `py` or `python` as the interpreter** — not `python3`
- **Production target:** Linux (Ubuntu 24.04 LTS) via Docker Compose. Do not bake Windows-specific deployment assumptions into the code — everything production-related must work on Linux.

### 0.5 Assets Already Provided
- `meil.png` — company logo, in the project root. Use for login page, sidebar header (expanded), and favicon. Generate a compact/initials variant for collapsed sidebar.
- `MARY NANTAYIRO Jan 2026 Report.pdf and Teddy.pdf` — reference for the landlord statement layout. Match this format in Phase 5.

### 0.6 What NOT To Do
- Do not build a Single Page Application. No React, Vue, Alpine-as-framework, or HTMX unless the spec explicitly asks for AJAX.
- Do not build REST API endpoints for anything other than (a) inbound payment webhook, (b) outbound notification calls.
- Do not hard-delete financial records — ever. Soft delete only.
- Do not use emojis in UI text or code comments.
- Do not generate placeholder/lorem ipsum data in production templates. Use real Django messages or leave blank.

---

## 1. Project Overview

Meili Property Solution is a Uganda-based property management company that:
- Manages residential properties on behalf of landlords (rent collection, tenant management, estate ops)
- Owns some properties directly (100% of rent goes to Meili)

Build a **Django monolith + PostgreSQL** web application with REST API **only for** (a) payment processing webhooks and (b) outbound notification aggregator calls. Everything else is server-rendered Django templates.

---

## 2. Tech Stack (Fixed — Do Not Deviate)

- **Python:** 3.12+ (Django 6.0 requires 3.12 minimum — 3.13 recommended)
- **Backend & Frontend:** Django 6.0, server-rendered templates with HTML/CSS/vanilla JS
- **Database:** PostgreSQL (`meili_prd01`)
- **APIs (scope-limited):** Django REST Framework used **only** for:
  1. Inbound payment processing endpoint (banks, mobile money)
  2. Outbound notification aggregator calls (Africa's Talking or similar)
- **Authentication:** **Custom `accounts` app** (do NOT use Django's built-in `auth.User`). Use `AbstractBaseUser` + `PermissionsMixin` + custom `BaseUserManager`. Role-based access via a `Role` model or choices field (Admin, Account Manager, Collections, Sales Rep, Finance, Tenant, Landlord). Configure `AUTH_USER_MODEL = 'accounts.User'` in settings from day one — swapping later is painful and risks data loss.
- **Currencies:** UGX (primary, whole numbers — enforced), USD (secondary, 2 decimals — enforced). Extensible.
- **Background tasks / scheduling (production-grade, Linux-deployed):**
  - **Celery 5.6+** — mature, battle-tested task queue. Used for all async work: invoice generation, score calculation, statement generation and delivery, notifications, refund processing.
  - **RabbitMQ 3.13+** — message broker. Production-grade durability, message acknowledgements, dead-letter queues. Runs in Docker.
  - **`django-celery-results`** — PostgreSQL-backed result backend. Task results stored as rows in `meili_prd01`. No Redis needed. Results queryable via Django ORM for admin visibility and audit trail (see Section 19 — task results contribute to the audit trail).
  - **`django-celery-beat`** — database-backed periodic task scheduler. Schedules editable from Django admin (crucial for the "admin can pause invoice generation" requirement in Section 16.2).
  - **Flower** — web-based Celery monitoring dashboard (http://localhost:5555 in dev, restricted behind auth in prod). Shows task queue health, worker status, failed tasks, throughput. Admin-role access only.
  - **Dev machine (Windows):** run Celery worker with `celery -A meili_property worker --pool=solo --loglevel=info` (solo pool is required on Windows). Run beat with `celery -A meili_property beat --scheduler django_celery_beat.schedulers:DatabaseScheduler`. Run Flower with `celery -A meili_property flower`.
  - **Production (Linux + Docker):** worker runs with default prefork pool. Worker, beat, flower, and RabbitMQ each as separate Docker Compose services. Worker and beat run as systemd units managed by Docker's restart policy. Never run beat in more than one instance — duplicate schedules will fire.
  - **Result retention:** `cleanup_old_task_results` Celery beat task runs weekly, deletes task results older than 180 days except those linked to financial audit records (invoice generation, payment allocation, statement delivery) which are kept for 7 years per URA tax record requirements.
- **Frontend libraries (use these, don't invent alternatives):**
  - Select2 (every `<select>` — required per spec)
  - Google Fonts (Inter or Nunito)
  - No CSS framework by default — write custom CSS with CSS variables. If you need utilities, use Tailwind via CDN for development, but commit a proper build for production.

### 2.1 Complete Open Source Library List (Enterprise-Grade)

Every dependency below must be actively maintained (≤12 months since last release) and have a permissive open-source license (MIT, BSD, Apache 2.0, LGPL). **No proprietary, paid, or closed-source packages anywhere in the stack.**

**Core Django stack:**
- `Django==6.0.4` — web framework
- `psycopg[binary]>=3.2` — PostgreSQL driver (use psycopg 3, not psycopg2 — psycopg2 is in maintenance mode)
- `django-environ` — 12-factor env var management
- `python-decouple` — alternative/fallback for env config (choose one, not both — prefer django-environ)
- `whitenoise` — static file serving in production
- `gunicorn` — WSGI server (prod)
- `waitress` — Windows-compatible WSGI server alternative for prod testing

**Authentication & permissions:**
- `argon2-cffi` — Argon2 password hashing (enterprise-grade, stronger than Django default). Set as `PASSWORD_HASHERS[0]` in settings.
- `django-axes` — brute-force login protection (complements our custom LoginAttempt table with additional layers)
- `django-ratelimit` — rate limiting on login, password reset, API endpoints

**Background tasks & scheduling:**
- `celery==5.6.3` — task queue (latest stable, Python 3.13 compatible)
- `django-celery-results` — PostgreSQL result backend (stores task outcomes as ORM-queryable rows)
- `django-celery-beat` — database-backed periodic scheduler (schedules editable from Django admin)
- `flower` — web dashboard for Celery monitoring
- **Broker:** RabbitMQ (via Docker in both dev and prod — no need to install natively on Windows)
- **No Redis** — not needed; PostgreSQL handles results, RabbitMQ handles broker

**REST API (phase 6 only):**
- `djangorestframework` — REST framework
- `drf-spectacular` — OpenAPI 3.x schema generation (for payment partner documentation)

**HTTP & external services:**
- `httpx` — modern HTTP client for outbound notification aggregator calls (preferred over `requests` — supports async, HTTP/2, better timeouts)

**Forms & UI:**
- `django-crispy-forms` + `crispy-bootstrap5` OR hand-rolled form rendering (choose hand-rolled for full design control — only use crispy if time-constrained)
- `django-widget-tweaks` — template-level form widget customisation

**Audit & history:**
- `django-simple-history` — automatic audit trail on every model. Use for Section 19 requirements.

**File handling:**
- `Pillow` — image handling (profile pictures, logo)
- `reportlab` — PDF generation (receipts, statements, invoices). Open source, BSD license.
- `weasyprint` — HTML-to-PDF (alternative to reportlab for complex layouts matching `MARY NANTAYIRO Jan 2026 Report.pdf or Teddy.pdf`). Windows-friendly.
- `openpyxl` — Excel export for reports (if needed)

**Security:**
- `django-csp` — Content Security Policy (Django 6.0 has built-in support via `ContentSecurityPolicyMiddleware`; use the native one, not the package)
- `bleach` — HTML sanitisation for any user-submitted rich text

**Development & testing:**
- `pytest` + `pytest-django` — test runner (cleaner than Django's default test runner)
- `pytest-cov` — coverage reporting
- `factory-boy` — test data factories
- `faker` — fake data generation for tests and seed data
- `ruff` — linter + formatter (replaces flake8, black, isort, pylint — one tool, fast)
- `django-debug-toolbar` — dev-only SQL/query profiler

**Monitoring (production, deferred to Phase 8):**
- `sentry-sdk[django]` — error tracking (free tier sufficient for current scale)
- `django-prometheus` — metrics export (optional, enable if monitoring infra exists)

### 2.2 What We Explicitly Do NOT Use

- **No `django.tasks` or `django-tasks` package** — Django 6.0's built-in task framework is not production-ready (no production worker in core, execution left to external infrastructure). We use Celery + RabbitMQ instead for guaranteed production stability.
- **No Redis anywhere** — RabbitMQ is the broker, PostgreSQL is the result backend. Redis as a Celery broker is less reliable for financial systems (in-memory first, broker second).
- **No Django Channels / WebSockets** — no real-time requirements in spec
- **No SPA frameworks** (React, Vue, Svelte, HTMX-as-framework) — server-rendered only
- **No ORM alternatives** — Django ORM only
- **No paid services** — all open source
- **No psycopg2** — use psycopg 3
- **No `requests` library** — use httpx

### 2.3 Virtual Environment Setup (Windows, First-Time)

The `meili` virtualenv exists but is bare. Before starting Phase 1, run these commands **once** to install all dependencies into the venv:

```batch
REM Activate the venv
workon meili

REM Upgrade pip and core tooling
python -m pip install --upgrade pip setuptools wheel

REM Create requirements.txt at repo root (the agent will write it during Phase 1)
REM Then install everything:
pip install -r requirements.txt

REM Or install directly in one command for Phase 1 bootstrap:
pip install Django==6.0.4 "psycopg[binary]>=3.2" django-environ whitenoise gunicorn waitress argon2-cffi django-axes django-ratelimit celery==5.6.3 django-celery-results django-celery-beat flower httpx Pillow reportlab weasyprint django-simple-history django-widget-tweaks bleach pytest pytest-django pytest-cov factory-boy faker ruff django-debug-toolbar
```

### 2.4 RabbitMQ Setup

RabbitMQ runs in Docker both in development (Windows, via Docker Desktop) and production (Linux). This avoids the pain of a native Windows RabbitMQ install and keeps dev and prod identical.

**`docker-compose.dev.yml` at repo root (Phase 1 deliverable):**

```yaml
services:
  rabbitmq:
    image: rabbitmq:3.13-management-alpine
    container_name: meili_rabbitmq_dev
    ports:
      - "5672:5672"      # AMQP broker port
      - "15672:15672"    # Management UI (http://localhost:15672)
    environment:
      RABBITMQ_DEFAULT_USER: meili
      RABBITMQ_DEFAULT_PASS: heaven2870_rmq
      RABBITMQ_DEFAULT_VHOST: meili
    volumes:
      - rabbitmq_data:/var/lib/rabbitmq
    healthcheck:
      test: ["CMD", "rabbitmqctl", "status"]
      interval: 30s
      timeout: 10s
      retries: 5

volumes:
  rabbitmq_data:
```

**IMPORTANT — Container lifecycle is the user's responsibility.** Claude Code writes the `docker-compose.dev.yml` file and ensures it is correct, but the user (George) manages the container lifecycle himself — starting, stopping, restarting, inspecting logs. The agent must NOT run `docker compose up`, `docker compose down`, or any container-manipulation command. If a task requires RabbitMQ to be running (e.g., smoke-testing Celery), the agent should confirm with the user that RabbitMQ is running before proceeding, not start it.

When the user wants to start it himself, the command is: `docker compose -f docker-compose.dev.yml up -d rabbitmq`. Management UI at http://localhost:15672 (user: `meili`, password: `heaven2870_rmq`).

### 2.5 Celery Process Management — Dev

**Process lifecycle is the user's responsibility.** The agent writes the Celery app code (`meili_property/celery.py`), settings configuration, tasks, and this documentation, but does NOT start the worker, beat, Flower, or Django dev server. The user runs these himself in separate terminals as needed. If a task requires a Celery process to be running, the agent confirms with the user before proceeding.

On Windows dev, the user runs these in **four separate terminals**, each with `workon meili` activated:

```batch
REM Terminal 1 — Django dev server
python manage.py runserver

REM Terminal 2 — Celery worker (solo pool required on Windows)
celery -A meili_property worker --pool=solo --loglevel=info

REM Terminal 3 — Celery beat (scheduler)
celery -A meili_property beat --scheduler django_celery_beat.schedulers:DatabaseScheduler --loglevel=info

REM Terminal 4 — Flower (monitoring)
celery -A meili_property flower --port=5555
```

If RabbitMQ is not running, all three Celery processes will fail with connection refused — the user starts RabbitMQ first via `docker compose -f docker-compose.dev.yml up -d rabbitmq`.

Split `requirements.txt` (runtime) and `requirements-dev.txt` (dev-only: pytest, ruff, factory-boy, faker, django-debug-toolbar). Phase 6 adds `djangorestframework` and `drf-spectacular`. Phase 8 adds `sentry-sdk[django]`.

Pin exact versions in the committed `requirements.txt` once Phase 1 is working. Use `pip freeze > requirements.lock.txt` for reproducible builds.

---

## 2A. Custom `accounts` App (Authentication)

Build this as the **first app after project init** (before `core`). The custom user model must exist before any migrations run, or `AUTH_USER_MODEL` swap will break.

### 2A.1 Models

**`User`** (extends `AbstractBaseUser`, `PermissionsMixin`)
- `email` — unique, required, used as `USERNAME_FIELD`
- `phone` — unique, required (format: international E.164, e.g., `+256...`)
- `first_name`, `last_name`
- `is_active`, `is_staff` (standard)
- `last_login_ip`, `last_login_at`
- `profile_picture` (ImageField, optional — initials fallback in UI)
- `force_password_change` (Boolean, default False — used when admin resets a password)
- `created_at`, `updated_at`
- `REQUIRED_FIELDS = ['phone', 'first_name', 'last_name']`

**`Role`**
- `name` — choices: `ADMIN`, `ACCOUNT_MANAGER`, `COLLECTIONS`, `SALES_REP`, `FINANCE`, `TENANT`, `LANDLORD`
- `description`
- `is_system` (Boolean — system roles cannot be deleted)

**`UserRole`** (M2M through model — one user can hold multiple roles)
- `user` FK, `role` FK
- `assigned_at`, `assigned_by` FK
- `is_active`

**`LoginAttempt`** (security audit)
- `email`, `ip_address`, `user_agent`, `success` (Bool), `timestamp`, `failure_reason`

**`PasswordResetToken`**
- `user` FK, `token` (UUID), `created_at`, `expires_at`, `used_at`

### 2A.2 Manager
Implement `UserManager(BaseUserManager)` with:
- `create_user(email, phone, password, **extra)`
- `create_superuser(email, phone, password, **extra)`
- Email normalisation, password hashing via Django's `set_password`

### 2A.3 Views & Templates
- Login view (email + password, OTP optional for tenants/landlords later)
- Logout view
- Password reset (email-based, token expires in 30 minutes)
- Change password (required when `force_password_change=True`)
- Lockout after 5 failed login attempts within 15 minutes (check `LoginAttempt` table)

### 2A.4 Permission Helpers
Build decorators / mixins:
- `@role_required('ADMIN')` — function-based view decorator
- `RoleRequiredMixin` — CBV mixin
- `has_role(user, role_name)` — utility function
- `has_any_role(user, *role_names)`

Use these throughout the dashboard and portals instead of `@login_required` alone.

### 2A.5 Linking to Business Entities
- `Tenant` has `user` OneToOneField (nullable — tenant may not have portal access yet)
- `Landlord` has `user` OneToOneField (nullable)
- `Employee` has `user` OneToOneField (required — every employee must have login)

When an `Employee` is created, automatically create a linked `User` with the `ACCOUNT_MANAGER` / `COLLECTIONS` / etc. role matching their assignment. Generate a temporary password, email it, set `force_password_change=True`.

### 2A.6 Admin Settings Page (top-right dropdown)
Only visible to `ADMIN` role. Contains:
- User list (all users, filterable by role, active status)
- Role management (assign/revoke roles)
- Reset another user's password
- View login audit log

---

## 3. Core Entities

### 3.1 Tenant
Full bio-data (name, phone, email, ID number, next of kin). Preferred notification method: SMS/WhatsApp/Email. Preferred receipt delivery: WhatsApp/Email/Web Console. Sales rep captured at tenant creation.

**Profile editing rule:** Tenants cannot edit their own profile. Only Meili employees can.

**Tenant-level status (DERIVED from TenantHouse records, never manually set):**
- `Active` — has at least one Active TenantHouse
- `Prospect Only` — all TenantHouse records are Prospect
- `Exited` — all TenantHouse records are Exited

### 3.2 TenantHouse (junction model)
A tenant can have multiple TenantHouse records — one per house rented or being considered. Fields: tenant FK, house FK, move-in date, move-out date, security deposit, initial deposit details, billing start date, sales rep FK, account manager FK, collections person FK.

**Per-house status:** `Prospect`, `Active`, `Exited`.

A tenant can be Active in House A and Prospect in House B simultaneously.

### 3.3 Landlord
Bio-data + bank details for settlement. Owns estates and/or individual houses. Commission arrangement per estate or per house. Has portal login (max 6 months per query).

**Profile editing rule:** Only Meili employees can edit landlord profiles.
**Status:** Active / Inactive.

### 3.4 Estate
A group of houses at one location. Holds default settings inherited by all houses within. Settings include: commission type+amount, billing cycle, billing mode, tax applicability, security deposit policy, initial deposit policy, currency, account manager, collections person, utility billing flags (water, garbage — some estates these are collected separately and paid to providers).

### 3.5 House
Every house **must** belong to an estate (no standalone houses). Fields: house number, name, description, estate FK (required), landlord FK, periodic rent amount, all same configurable settings as estate.

**Occupancy status (DERIVED):**
- `Vacant` — no active TenantHouse
- `Occupied` — has an Active TenantHouse
- `Under Maintenance` — manually set; blocks new tenant attachment

**Critical override rule:** House-level setting ALWAYS overrides estate-level setting. Implement this as `get_effective_setting(house, field_name)` utility.

### 3.6 Meili (Company Entity)
Can itself be the landlord. For Meili-owned properties: 100% of rent is Meili's, no commission split.

### 3.7 Employee
Bio-data, direct manager FK (self-ref), role assignments (one employee can hold multiple roles). `requires_checker` boolean (default True). When False → Trusted → can self-approve financial entries.

**Profile editing rule:** Only Admin-role employees can edit employee profiles.

### 3.8 Supplier
Bio-data + type (Goods/Services/Both). Expenses from suppliers are either charged to the landlord (repairs) or to Meili (office supplies).

---

## 4. Ownership Model

| Scenario | Revenue Split |
|---|---|
| Landlord-owned, Meili-managed | Rent collected − Meili commission = Landlord share |
| Meili-owned | 100% to Meili |

---

## 5. Commission Structure

Configured per estate OR per house (house overrides estate).

### 5.1 Types
- **Fixed Amount:** Fixed UGX/USD per billing period per house.
- **Percentage:** % of actual rent collected.

### 5.2 Scope
- Per house (calculated individually per house)
- Per estate (calculated on total estate collection)

### 5.3 Priority Rule (Critical)
**Meili's commission is always deducted first from whatever is collected.** Landlord bears the shortfall — Meili does not guarantee rent.

**Percentage example:** Rent 2,000,000 UGX, commission 20%, tenant pays 500,000 UGX.
- Meili: 500,000 × 20% = 100,000
- Landlord: 500,000 − 100,000 = 400,000
- When tenant clears 1,500,000 arrears later: Meili takes 1,500,000 × 20% = 300,000, landlord gets 1,200,000.
- **The system must track arrears and current billing as SEPARATE columns on the tenant statement.** Support multiple partial payments per period (tenants who pay periodic rent in more than 2 installments are flagged as bad tenants).

**Fixed example 1:** Rent 250,000, fixed commission 50,000, tenant pays 150,000.
- Meili: 50,000 (full fixed amount)
- Landlord: 100,000

**Fixed example 2:** Rent 250,000, fixed commission 50,000, tenant pays 30,000.
- Meili: 30,000 (all of it)
- Landlord: 0
- Commission shortfall of 20,000 is **recoverable from future arrears payments**. Track this.

---

## 6. Billing Cycles

Configurable per house/estate: Hourly, Daily, Weekly, Monthly, Quarterly, Semi-Annual, Yearly, or custom (e.g., every 2 weeks). Minimum resolution: hourly.

---

## 7. Billing Mode

- **Prepaid:** Pay before the period. E.g., pay end of March for April.
- **Postpaid:** Pay after the period. E.g., sleep through March, pay end of March for March.

---

## 8. Pro-Rata

When a tenant moves in mid-period:

### 8.1 Pro-Rata Billing (default)
```
Daily Rate = Period Rent ÷ Days in Period (round to whole UGX)
Pro-Rata Amount = Daily Rate × Days Occupied (round to whole UGX)
```

Example: Move-in April 8, rent 250,000/mo → 8,333 × 22 = 183,326 UGX.

**Commission on pro-rata:**
- Percentage: apply % to pro-rated amount
- Fixed: convert to % first — `(Fixed Commission ÷ Full Period Rent) × 100` — then apply to pro-rated amount

### 8.2 Next-Cycle Alignment (alternative, configurable)
Skip partial period, start billing from next full cycle date.

**Rounding rule:** UGX always whole numbers. USD up to 2 decimals.

---

## 9. Deposits

### 9.1 Security Deposit (refundable)
- Configurable amount (per house/estate), waivable
- On departure: deduct damages/unpaid bills → refund remainder OR bill landlord for shortfall
- Tracked in dedicated `Security Deposits Held` account
- Landlord statements show any usage with line items

### 9.2 Initial Deposit (advance rent)
- Configurable number of periods (0, 1, 2, 3+), waivable
- System tracks periods covered and exact date regular billing resumes
- Tracked in dedicated `Initial Deposit` account
- Every invoice shows: period (from-to), amount due, balance carried forward

---

## 10. Tax

Not active initially, but must be supported from day one. Types: VAT, Withholding Tax, Custom (extensible). Enabled/disabled per house or estate. House-level overrides estate-level.

---

## 11. Invoicing & Receipts

### 11.1 Invoice (per billing period, per active tenant)
Fields on invoice: unique sequential number, period from-to, estate name, house number, house name, rent amount, itemised taxes, outstanding balance from previous periods, total due, due date.

### 11.2 Receipt
Generated on every payment. Mobile-friendly HTML/PDF. Fields: receipt number, date/time, amount, payment method, transaction ref, remaining balance, period(s) settled.

**Delivery:** Per tenant preference — WhatsApp / Email / Web Console.

**Print support (required):**
- "Print Receipt" button on every receipt page
- Two print layouts via `@media print` CSS:
  - **Thermal/POS:** 58mm or 80mm narrow width, minimal margins, no backgrounds
  - **Standard:** A4/Letter
- Employee selects format, or auto-detect based on configurable default

---

## 12. Payment Processing

### 12.1 FIFO Allocation (CRITICAL)
Every payment — API or manual — allocates to **oldest outstanding invoices first**. Surplus goes to current/future periods per Section 20.

### 12.2 Payment Processing API
`POST /api/v1/payments/` — authenticated via API key (unique per bank/provider).

Payload: `amount`, `payer_reference` (phone or account), `transaction_id`, `timestamp`, `source_name`.

Processing: match to tenant → FIFO allocate → generate receipt → trigger notification → respond with success/failure and receipt number.

Handle: unmatched payer, duplicate `transaction_id`, invalid payload.

### 12.3 Manual Payment Entry
Required fields on the form:
- **Receiving account** (FK to BankAccount — which Meili account received the money) — **mandatory**
- Transaction ID / reference from source
- Transaction date/time (actual, not entry time — entry time captured separately in audit)
- Payment source: Cash / Bank Transfer / Mobile Money / Cheque
- Source details (bank name, mobile money provider, payer's account/phone)
- Amount
- Tenant and house (if not auto-matched)

FIFO runs automatically on approval (or immediately if maker-checker bypassed).

### 12.4 Maker-Checker Workflow
Default: all manual financial entries go through maker → checker.

- **Maker** enters → saved as `Pending Approval`
- **Checker** (different person) reviews → Approves or Rejects
- **Approve:** post transaction, FIFO allocate, create journal entries, generate receipt, send notifications
- **Reject:** mark rejected with reason, maker can resubmit
- **Self-approval is blocked** — maker and checker must be different people
- **Trusted bypass:** `Employee.requires_checker = False` → their entries auto-post in single step
- **Audit trail records entry AND approval** regardless of bypass
- **Dashboard shows Pending Approvals queue** with filter by date/amount/maker, highlighting entries overdue > 24 hours (configurable)

---

## 13. Bank & Payment Accounts

Support multiple accounts: Bank (name, number, branch), Mobile Money (provider, phone/account), Cash. Each links to a Chart of Accounts entry for reconciliation.

---

## 14. Chart of Accounts & Accounting

### 14.1 Double-Entry
Every transaction creates balanced debit and credit entries. JournalEntry cannot be posted unless debits = credits.

### 14.2 Default Chart
- **Assets:** Cash, Bank Accounts, Mobile Money, Accounts Receivable (Tenant Balances), Security Deposits Held
- **Liabilities:** Landlord Payable, Security Deposits Refundable, Tax Payable, **Tenant Advance Payments Held — Managed Properties** (fiduciary — money held on behalf of landlords), **Tenant Advance Payments Held — Meili-Owned** (deferred revenue on Meili's own properties). See Section 20. Do NOT merge these two — they are legally distinct categories.
- **Revenue:**
  - **Rent Income** — for Meili-owned properties only (100% rent recognised as Meili revenue)
  - **Commission Income** — standalone account for managed-property commission earnings. Do NOT combine with Rent Income. This separation is required for clean P&L, tax treatment (service vs rental revenue often differ), and commission dispute audits.
- **Expenses:** Maintenance/Repairs, Office Supplies, Service Costs
- **Equity:** Owner's Equity, Retained Earnings

Must support: adding new accounts, configuring types, generating General Ledger, Journals, Trial Balance, Balance Sheet, and a dedicated **Commission Revenue Report** (Commission Income account drill-down by period/estate/house).

---

## 15. Statements & Reports

### 15.1 Landlord Statement (Monthly)
Columns: Estate, House Number, House Name, Tenant Name, Amount Paid, Balance, Balance Period, Repairs/Maintenance, Security Deposit Usage, Meili Commission, Net Payable to Landlord.

**Do NOT show:** Account manager, collections person, internal Meili data.

**Use `MARY NANTAYIRO Jan 2026 Report.pdf or Teddy.pdf` (in project root) as the visual reference** — match its layout.

### 15.2 Internal Reports (Employees)
- Repairs per house in period
- Estate-level modifications (road work, pumping — attached to estate, not house)
- Collection performance per account manager / collections person
- Tenant acquisition per sales rep
- Occupancy rates per estate / house
- Revenue summary (collected, commission, landlord payable)
- Extractable up to 12 months

### 15.3 Query Limits
| Role | Max Period |
|---|---|
| Tenant | 6 months |
| Landlord | 6 months |
| Employee | 12 months |

### 15.4 Automatic Statement Delivery (NEW)
Landlord statements are auto-generated at end of each billing cycle and delivered via the landlord's preferred channel (Email/WhatsApp). Landlords can also manually view/extract them in the portal.

---

## 16. Invoice Automation (NEW — from additions)

### 16.1 Automatic Invoice Generation
- System auto-generates periodic invoices via a Celery beat scheduled task (`generate_invoices`). Runs every hour to catch all billing cycle resolutions (hourly, daily, weekly, monthly, etc.).
- Every active TenantHouse produces an invoice when its next billing period falls due, subject to its `invoice_generation_status` (Active / Paused / Stopped).
- Schedule is stored in `django-celery-beat`'s database tables, editable from Django admin — no code changes needed to adjust timing.
- Task execution requires the Celery worker and beat processes to be running. In dev, run them in separate terminals per Section 0.4. In production (Linux Docker), they run as Docker Compose services with auto-restart policies.

### 16.2 Pause/Resume Controls
- **Admin can pause invoice generation** per TenantHouse (e.g., tenant temporarily away, house under dispute).
- **Admin can resume** — billing picks up from the next full cycle date after resume.
- **Automatic pause on tenant exit:** when TenantHouse status → Exited, invoice generation for that tenancy stops automatically.
- **Automatic resume on tenant return:** if an exited tenant is re-attached to a house (new TenantHouse record), billing resumes per new move-in date.
- Model: add `invoice_generation_status` to TenantHouse (Active / Paused / Stopped) with paused_by, paused_at, pause_reason.

### 16.3 Invoice Lifecycle & Statuses (CRITICAL)

Invoices follow a strict state machine. Status transitions are one-way (except Draft → Issued, which can go back via delete only while still Draft). This is a core accounting-integrity requirement.

| Status | Meaning | Ledger Posted? | Editable? | Deletable? |
|---|---|---|---|---|
| **Draft** | Created but not issued. Not sent to tenant. Not counted in any report. | No | Yes (any authorised employee) | Yes (by Super Admin only — Section 16.6) |
| **Issued** | Finalised, journal entries posted, visible to tenant, counted in AR. | Yes | No (void or credit-note to correct) | **No — EVER** |
| **Partially Paid** | Payments received but balance > 0. | Yes | No | No |
| **Paid** | Balance = 0. | Yes | No | No |
| **Overdue** | Past due date with balance > 0. Auto-transitioned by beat task. | Yes | No | No |
| **Voided** | Cancelled via void workflow. Reversing journal entries posted. Invoice record preserved with original number. Net P&L impact = 0. | Yes (original + reversal) | No | No |
| **Cancelled** | A void that happened before any payment was applied. Same as Voided accounting-wise, different semantic label for reporting. | Yes (original + reversal) | No | No |

**Sequential numbering rule:** Invoice numbers follow format `INV-{YYYYMM}-{NNNNN}` (e.g., `INV-202604-00042`). Numbers are **never re-used**. Voided invoice numbers remain in the sequence — gaps are a red flag for auditors; voided-but-preserved records are normal.

### 16.4 Manual Invoice Creation

Employees with appropriate permissions can create invoices manually outside the automatic `generate_invoices` cycle. Required for:
- Backdated corrections
- One-off charges (though ad-hoc charges per Section 19 are usually better for non-rent items)
- Manual adjustments when automation has been paused
- Edge-case periods that don't match the standard cycle

**Permissions required:** Admin, Finance, or Account Manager role.

**Form fields:**
- Tenant + House (TenantHouse FK)
- Period from date + Period to date (manually specified — can be in the past for backdating, can overlap previously-issued periods with a warning shown)
- Rent amount (defaults to house's effective rent, editable)
- Tax lines (auto-populated from house's effective tax settings, editable)
- Issue date (defaults to today)
- Due date (defaults based on billing mode; editable)
- Notes / reason (required for backdated invoices — free-text, 10 char min)
- **Save as Draft** or **Issue Immediately** buttons

**Workflow:**
1. Employee fills form → submits as Draft → invoice created with status `Draft`, no journal entries yet.
2. Employee (or a different employee if maker-checker is required for invoice issuance — configurable per estate) reviews the draft.
3. On **Issue**: invoice status → `Issued`, journal entries posted (Debit AR, Credit Rent Income or Landlord Payable), invoice becomes visible to tenant, notifications triggered per tenant preferences.
4. Once Issued, it is locked — only void or credit note can change it.

**Backdated invoices:** the issue_date is today, but the period_from/period_to can be in the past. Journal entries are posted to the period in which the invoice is issued (today), not backdated to the period — this preserves the integrity of locked prior accounting periods. The period_from/to is informational only; it goes on the invoice PDF and into landlord statement allocation.

### 16.5 Void Invoice Workflow

Used to cancel an invoice that was issued in error. The invoice record is preserved with status `Voided`; a reversing journal entry is posted on the void date.

**Permissions required:** Finance Admin or Super Admin role.

**Maker-checker: ALWAYS required** — no trusted-employee bypass for voids. The employee who issued the invoice cannot void it. Different checker must approve.

**Preconditions:**
- Invoice status must be `Issued`, `Partially Paid`, `Overdue`, or `Paid`
- If invoice has any payments applied, the payments must first be unapplied (creates reversing entry, payment becomes orphaned credit on tenant account — which then routes through the held-advance / credit-balance logic in Section 20)
- If invoice is linked to a landlord statement that has already been delivered, a warning is shown but void can still proceed — the next statement will show the reversal clearly

**Form fields:**
- Invoice being voided (read-only link)
- Void reason (required, min 20 chars — common reasons: "Wrong tenant selected", "Duplicate invoice", "Amount entered incorrectly", "Tenant never occupied")
- Void reason category (dropdown: Data Entry Error / Duplicate / Wrong Tenant / Wrong Amount / Tenant Dispute / Other)
- Void date (defaults to today, cannot be earlier than the original issue date)

**Workflow:**
1. Maker submits void request → status `Void Pending Approval`. Invoice itself remains in current status until approved.
2. Checker reviews → Approve or Reject.
3. On Approve:
   - Invoice status → `Voided` (or `Cancelled` if no payments had been applied)
   - Reversing journal entry posted: Debit Rent Income / Landlord Payable, Credit AR (exact mirror of the original, dated today)
   - Commission reversal: Debit Commission Income, Credit Landlord Payable (if commission had been recognised)
   - Tenant notified that invoice INV-xxx has been voided
   - Landlord statement for the current period will show the void as a separate line (not hidden)
4. On Reject: void request deleted, original invoice unchanged, maker notified.

**Voided invoices remain visible** in all list views with a clear `Voided` badge, filterable separately. They are excluded from AR balance calculations but counted in audit reports.

### 16.6 Delete Draft Invoice (Super Admin Only)

Drafts that were created by mistake before being Issued can be hard-deleted. Since no journal entries exist yet for drafts, this is accounting-safe.

**Permissions required:** Super Admin role only. No maker-checker required (there's nothing to reverse — nothing was posted).

**Preconditions:**
- Invoice status MUST be `Draft`
- Any Issued/Paid/Voided invoice — deletion is **blocked at the database level** via a model-level check. Attempting to delete via Django shell, admin, or any other path raises an exception.

**Audit:** even draft deletions are logged in `django-simple-history` — you can see who created and who deleted each draft.

**UI:** "Delete Draft" button only appears on Draft-status invoices, and only for Super Admin users. The button is hidden entirely (not just disabled) for everyone else. Attempting to delete via URL manipulation returns 403.

### 16.7 Credit Notes

A Credit Note partially or fully reverses a previously Issued invoice, creating a credit balance on the tenant's account. Used when you need to reduce an invoice amount without fully voiding it — e.g., "tenant stayed only 20 days of the 30-day period, refund them 10 days worth."

**Permissions required:** Finance Admin or Super Admin role.

**Maker-checker: ALWAYS required** — no trusted-employee bypass for credit notes.

**Credit Note Model:**
- `credit_note_number` — sequential, format `CRN-{YYYYMM}-{NNNNN}`
- `original_invoice` FK (the invoice being credited)
- `amount` (must be ≤ original invoice amount; UGX or USD per original invoice currency)
- `reason` (required, min 20 chars)
- `reason_category` (dropdown: Pro-rata Adjustment / Damage Assessment Reversal / Billing Error / Goodwill / Tenant Dispute Resolution / Other)
- `issue_date` (defaults to today)
- `status` (Draft / Issued / Pending Approval)
- Standard audit fields

**Workflow:**
1. Maker creates credit note → status `Pending Approval`.
2. Checker reviews amount and reason → Approve or Reject.
3. On Approve:
   - Credit note status → `Issued`
   - Journal entry: Debit Rent Income (or Landlord Payable), Credit AR for the credit note amount
   - If the original invoice was fully paid, the credit becomes available balance on the tenant's account (goes into `Tenant Advance Payments Held — [ownership type]`)
   - If the original invoice was partially paid or unpaid, the credit first reduces the outstanding balance on that invoice (may move it from Partially Paid → Paid, or reduce the balance due), with any remainder going to tenant credit
   - Commission reversal applied proportionally (if percentage commission, recalc; if fixed, reverse pro-rated by amount)
   - Tenant notified: "Credit note CRN-xxx issued for X UGX against invoice INV-yyy"
4. On Reject: credit note deleted, maker notified with reason.

**Credit notes are themselves voidable** using the same Void workflow (Section 16.5) if issued in error. A voided credit note reverses the credit, restoring the original invoice to its prior state.

**Display:**
- Credit notes appear on tenant statements as a separate line: `Credit Note CRN-xxx: -250,000 UGX (ref INV-yyy)`
- Visible on landlord statements as a deduction from rent income with clear reason
- Tenant portal shows credit notes in history alongside invoices and receipts

### 16.8 Refunds

A refund returns money to a tenant — cash, bank transfer, or mobile money. Separate from credit notes (credit notes create balance; refunds move cash out).

**Permissions required:** Finance Admin or Super Admin role.

**Maker-checker: ALWAYS required** — no trusted-employee bypass for refunds.

**When refunds are used:**
- Tenant exit with held advance balance remaining after damages/arrears/transfer (see Section 20.5)
- Security deposit refund on clean exit
- Overpayment refund (tenant requests cash instead of keeping credit balance)
- Approved credit note where tenant prefers cash over credit

**Refund Model:**
- `refund_number` — sequential, format `REF-{YYYYMM}-{NNNNN}`
- `tenant` FK, `tenant_house` FK (nullable — if refunding to a tenant who has exited all houses)
- `amount` and currency
- `refund_method` (Cash / Bank Transfer / Mobile Money / Cheque — matches a configured BankAccount for the source of funds)
- `source_account` FK to BankAccount (which Meili account the refund is paid FROM)
- `destination_details` (tenant's bank account or phone number — captured at refund time, not pulled from profile to avoid stale data)
- `reference_number` (transaction ID for bank/MM transfers, voucher number for cash — required)
- `linked_credit_note` FK (nullable — if this refund fulfils a credit note)
- `linked_held_advance_account` FK (nullable — if this refund is from the held-advance account on exit)
- `reason` (required, min 20 chars)
- `status` (Draft / Pending Approval / Approved / Rejected / Paid)
- Standard audit fields

**Workflow:**
1. Maker creates refund → status `Pending Approval`.
2. Checker reviews → Approve or Reject.
3. On Approve:
   - Status → `Approved`
   - Journal entry: Debit source liability account (Tenant Advance Payments Held, Security Deposits Refundable, or Accounts Receivable depending on source), Credit Cash/Bank/Mobile Money
   - Refund receipt generated (separate template from payment receipt — clearly labelled "REFUND RECEIPT") with refund number, amount, method, reason, reference
   - Refund receipt delivered via tenant's preferred channel
   - Status → `Paid` once the financial movement is confirmed (auto if maker confirms in UI; otherwise manually marked by Finance)
4. On Reject: refund deleted, maker notified, no financial movement.

### 16.9 Permission Matrix for Invoice Operations

| Operation | Admin | Super Admin | Finance | Account Manager | Collections | Sales Rep |
|---|---|---|---|---|---|---|
| Create invoice (Draft) | ✓ | ✓ | ✓ | ✓ | | |
| Issue invoice (Draft → Issued) | ✓ | ✓ | ✓ | ✓ | | |
| Delete draft invoice | | ✓ | | | | |
| Void Issued invoice | | ✓ | ✓ | | | |
| Approve void (checker) | | ✓ | ✓ | | | |
| Create credit note | | ✓ | ✓ | | | |
| Approve credit note (checker) | | ✓ | ✓ | | | |
| Create refund | | ✓ | ✓ | | | |
| Approve refund (checker) | | ✓ | ✓ | | | |
| View any invoice | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ |
| Enter payment | ✓ | ✓ | ✓ | ✓ | ✓ | |
| Approve payment (checker) | ✓ | ✓ | ✓ | ✓ | | |

- `Super Admin` is a dedicated role above `Admin` — only 1 or 2 people in the company. Required for destructive actions (draft deletion). All other actions Admin can do.
- Finance role is the financial-operations specialist — can void, credit-note, and refund with maker-checker.
- Account Managers handle routine invoicing but not corrections.
- Collections enters payments and follows up but does not issue or correct invoices.
- Sales Reps have read-only access to their tenants' invoice history.

### 16.10 Data Retention

Under no circumstances are issued invoices, voided invoices, credit notes, refunds, or their journal entries **ever** hard-deleted. This applies even when the tenant is exited, the landlord terminates the relationship, or the house is sold. Soft delete (`is_deleted = True`) is permitted only for drafts; all other records are retained permanently for audit compliance. URA mandates 7 years minimum retention on financial records — Meili retains indefinitely.

---

## 17. Portals & UI Layout

All portals built as Django templates. No SPA.

### 17.1 Tenant Portal
Login (phone/email + password or OTP). View: profile, current house(s), payment history, outstanding balance, credit balance, invoices, receipts. Download/view receipts.

### 17.2 Landlord Portal
Login. View: owned estates/houses, tenant list per house, monthly statements, payment history. Extract statements (max 6 months). Receive auto-delivered statements per preferred channel.

### 17.3 Employee Dashboard
Login. Role-based permissions. Manage all entities. Extract reports (max 12 months).

### 17.4 Main Layout (All Portals — `base.html`)

**Colour scheme:** Professional web palette. Use CSS variables in `:root`:
- `--primary`: Professional blue or teal (real estate feel)
- `--secondary`: Complementary accent
- `--success`: Green (paid, approved, active)
- `--warning`: Amber (pending, partial)
- `--danger`: Red (overdue, rejected, at-risk)
- `--neutral-*`: Grey scale for backgrounds/borders/text

Font: Inter or Nunito from Google Fonts. Clean, modern SaaS aesthetic — not default Bootstrap.

**Left Sidebar:**
- Fixed, collapsible
- Supports **multi-level nesting**: main menu → submenu → sub-submenu (accordion-style expansion)
- When collapsed: icons only, tooltips on hover (only in collapsed mode)
- State persists across sessions via `localStorage`
- Top-left: Meili logo (`meil.png`) when expanded; compact initials/icon variant when collapsed

Menu groups (employee dashboard):
- Property Management → Estates, Houses
- People → Tenants, Landlords, Employees, Suppliers
- Billing → Invoices, Payments, Receipts, Invoice Schedules (pause/resume)
- Accounting → Chart of Accounts, Journal Entries, General Ledger, Trial Balance
- Reports → (all internal reports listed above)
- Admin Settings (Admin role only)

**Top Fixed Header:**
- Centre: **Global search bar** returning results in **four grouped tables**:
  1. Tenants
  2. Houses
  3. Estates
  4. Users (employees, landlords, tenants — differentiated by role column)
  - Search by name, phone, ID number, house number, estate name
  - Each result clickable → detail page
- Top-right (in order):
  - **Notification bell** with unread badge (pending approvals, overdue payments, announcements). Dropdown on click.
  - **Profile avatar** — profile picture if uploaded, otherwise **initials of first+last name on a coloured circle**. Dropdown arrow beside it. Menu: My Profile (view-only), Admin Settings (Admin only), Log Out.

**Footer (fixed at bottom of every page):**
- Left or centre: `Developed by Okumpi Technologies`
- Version: `v1.1.0`
- Copyright: `© {current_year} Meili Property Solution` (dynamic year)

### 17.5 System-Wide Conventions

- **Pagination everywhere — system-wide mixin (not per-view):** default 50 records, user-selectable 20 / 50 / 100 / 150. Implement as:
  - Reusable `PaginatedListView` CBV mixin in `core.mixins` applied to every list view
  - Reusable `pagination.html` template partial included by every list template
  - Page size persists per user in session (stored in `request.session['page_size']`)
  - Query param `?page_size=100` always works as override
  - Combined with search/filter state without losing either
- **Select2 on every `<select>`** — searchable, AJAX for large datasets (tenant/house/landlord dropdowns).
- **No broken links:** every menu item has a page. If not built yet, show a styled "Coming Soon" placeholder — never a raw 404.
- **Custom error pages:** 403, 404, 500 — styled consistently.
- **Profile edit restrictions enforced server-side**, not just UI.

### 17.6 Login Page
Use `meil.png` logo prominently. Clean, centered card layout. Professional, on-brand.

### 17.7 Entity Workflow
1. Create Estate (first step)
2. Create Houses in the estate
3. Register Tenant (Prospect if unattached)
4. Attach Tenant to House → TenantHouse Active → House Occupied → billing starts
5. Tenant pays → invoices / receipts / notifications flow
6. Tenant exits → TenantHouse Exited, House Vacant (or Under Maintenance), records retained
7. Tenant returns → new TenantHouse, history preserved

---

## 18. Notifications

### 18.1 Architecture
System does NOT send SMS/WhatsApp directly. All outbound messages go through an aggregator (e.g., Africa's Talking). **Design a provider-agnostic interface** so the aggregator can be swapped without changing business logic.

### 18.2 Channels
SMS, WhatsApp, Email. Each tenant/landlord selects:
- Notification method: SMS / WhatsApp / Email
- Receipt/statement delivery: WhatsApp / Email / Web Console

---

## 19. Audit Trail

Every create/update/delete logged. Use `django-simple-history` or similar.
- Create: who, when (date + month + year + time + timezone)
- Update: who, what changed (old → new), when
- Delete: who, what, when (soft-delete preferred)
- Financial transactions: who entered, who approved, source

All timestamps include date, month, year, time, timezone.

---

## 20. Advance Payments (Liability Held by Meili)

Separate from the initial deposit at move-in. Tenants (especially new ones) commonly pay 3+ months upfront on a monthly cycle. **This money is held by Meili as a liability on behalf of the tenant/landlord — it is not earned or owed to the landlord until the period it relates to arrives.**

### 20.1 Accounting Treatment (CRITICAL)

**Two held-balance accounts, routed by house ownership type:**
- If the house's landlord is Meili → use `Tenant Advance Payments Held — Meili-Owned` (deferred revenue)
- If the house's landlord is external → use `Tenant Advance Payments Held — Managed Properties` (fiduciary liability)

Routing is automatic based on `House.landlord` — no manual classification by the employee entering the payment. Implement as a utility: `get_advance_holding_account(house)`.

**On receipt of advance payment:**
1. FIFO first clears any outstanding invoices (if any).
2. For any amount beyond current obligations:
   - **Debit:** Cash / Bank / Mobile Money (asset ↑)
   - **Credit:** appropriate `Tenant Advance Payments Held` account (liability ↑) — routed by ownership
3. **Do NOT** create the future invoices yet and **do NOT** recognise commission yet.

**At the start of each future billing period (via `generate_invoices` command):**
1. Generate the invoice normally (Debit AR, Credit Rent Income / Landlord Payable — Rent Income for Meili-owned, Landlord Payable for managed).
2. **Auto-apply** from the same `Tenant Advance Payments Held` account that originally received it:
   - **Debit:** `Tenant Advance Payments Held — [ownership type]` (liability ↓)
   - **Credit:** Accounts Receivable (clearing the new invoice)
3. **Now** run the commission split for that single period only (managed properties only — Meili-owned has no commission split):
   - Debit `Landlord Payable`, Credit `Commission Income`
4. This period's activity now shows on the landlord statement. Earlier-held months do not.

**Leftover < full period:** remains in the same `Tenant Advance Payments Held` account, displayed as a credit balance on tenant profile and portal.

### 20.2 Landlord Statement Visibility (Important)

**Landlord sees:**
- Current period's allocated rent only
- Current period's commission
- Current period's net payable
- NOT the held advance balance for future periods
- NOT a summary of how far ahead the tenant has paid

**Meili (internal employee dashboard) sees everything:**
- Full held balance per tenant, regardless of which of the two accounts holds it
- Which account holds each balance (ownership type visible in employee view only — landlord sees nothing, tenant sees their own credit balance but not the account name)
- Scheduled release dates (when each month will be recognised)
- Total liability per account type — separately:
  - Total owed to landlords (managed — fiduciary)
  - Total deferred revenue (Meili-owned)
- Advance Payments Report: filterable by tenant, house, estate, landlord, **and by ownership type**. Includes a badge/indicator for balances ≥ 2 full periods (intentional advance payers — reliability signal, useful alongside tenant classification tier).

**Why this matters:** The landlord statement must reflect only earned/current revenue — premature recognition would overstate landlord payable and trigger incorrect payouts. Meili cannot pay the landlord money it has not yet earned for them. The split between managed-property and Meili-owned advance accounts ensures Meili can cleanly answer "how much of my tenants' money are you holding?" to any landlord on demand, and cleanly separates fiduciary liability from Meili's own deferred revenue.

### 20.3 Example

Tenant pays 800,000 UGX upfront on monthly 250,000 rent, no outstanding balance, prepaid mode starting April.

**Day of payment (e.g., March 28):**
- Cash +800,000; `Tenant Advance Payments Held` +800,000
- **Landlord statement for March:** nothing new (payment held, not allocated)
- Meili internal view: "Tenant X has 800,000 UGX held, covers April–June + 50,000 credit"

**April 1 (invoice generated):**
- AR +250,000; Rent Income / Landlord Payable +250,000
- `Tenant Advance Payments Held` −250,000; AR −250,000 (auto-applied)
- Commission recognised: Landlord Payable −X; Commission Income +X
- **Landlord April statement:** shows 250,000 paid, commission deducted, net payable to landlord

**May 1, June 1:** same cycle.

**July 1:** only 50,000 credit remains; invoice for 250,000 generated, 50,000 auto-applied, 200,000 remains AR outstanding — **tenant now owes 200,000** and normal collections workflow applies.

### 20.4 Display Rules

- **Tenant profile / portal:** credit balance visible ("You have 50,000 UGX credit available")
- **Invoices:** show credit applied and remaining balance due
- **Flag** tenants with large held balances in employee dashboard for visibility
- **Never expose held balance to landlord** — not in portal, not in statements, not in any notification

### 20.5 Refund Path on Tenant Exit (CRITICAL)

When a tenant is detached from a house (`TenantHouse` status → `Exited`) while still holding a balance in `Tenant Advance Payments Held — [ownership type]`, the system must process the held balance in a strict order. Same order applies regardless of which of the two advance-holding accounts holds the money.

**Order of application (strict, sequential):**

1. **Outstanding invoices on the exiting TenantHouse** (FIFO — oldest first). Clear any unpaid rent, taxes, or partial-payment gaps. Journal: Debit `Tenant Advance Payments Held — [ownership type]`, Credit Accounts Receivable. Commission recognised per period as normal.

2. **Damages and ad-hoc charges** assessed on departure (unpaid utilities, property damage — entered as ad-hoc charges per Section 22). Journal: Debit `Tenant Advance Payments Held — [ownership type]`, Credit Accounts Receivable (for each damage/charge line).

3. **Outstanding invoices on the tenant's OTHER active TenantHouse records** (if any). A tenant may be active in House B while exiting House A — if they have advance balance from House A's payment, it can optionally be applied to House B's arrears. **This requires explicit employee approval (not automatic)** — show the option on the exit workflow screen as "Apply remaining balance to Tenant's other active tenancies?" If the other house has a different ownership type, transfer first across the two held accounts (Debit source account, Credit target account) before applying.

4. **Refund any remainder to the tenant.** Generate a refund transaction:
   - Payment method selected by employee (Cash / Bank Transfer / Mobile Money — must match a configured BankAccount)
   - Refund reference / transaction ID captured
   - Journal: Debit `Tenant Advance Payments Held — [ownership type]`, Credit Cash / Bank / Mobile Money
   - Generate a refund receipt (separate template from payment receipt — clearly labelled "REFUND RECEIPT")
   - Deliver via tenant's preferred notification channel
   - Refund transactions go through maker-checker workflow like any financial entry (trusted-employee bypass still applies)

**If the remainder is zero:** no refund needed, close the tenancy cleanly.

**If the remainder is negative** (damages exceed held advance + security deposit): the shortfall is billed to the landlord per Section 9.1. The tenant owes nothing further from held balance (it's already exhausted), but the outstanding damages remain on record and can be pursued separately.

### 20.6 Required Fields on Exit Workflow

The "Detach Tenant from House" / exit workflow screen must show:
- Held advance balance on the exiting TenantHouse (per account)
- Security deposit balance
- Outstanding invoices
- Ad-hoc charges form (damages, unpaid utilities)
- Clear computation panel showing how the balance is being applied step by step (1 → 2 → 3 → 4 above)
- Final refund amount (or landlord shortfall, if negative)
- Approval submit button (routes through maker-checker)

### 20.7 Example

Tenant X paid 800,000 UGX on 250,000/month managed property. After 1 month, they exit. State at exit:
- `Tenant Advance Payments Held — Managed Properties`: 550,000 UGX (months 2 and 3 of the original 3, minus period already released for month 1)
- Security deposit: 250,000 UGX held
- Outstanding: none
- Damages on exit: 80,000 UGX (broken window)
- Unpaid water bill: 45,000 UGX

Application:
1. Outstanding invoices: 0
2. Damages + utilities: 125,000 → deducted from held advance. Held balance now 425,000.
3. No other active tenancies.
4. Refund tenant: 425,000 UGX via tenant's chosen method.

Security deposit (250,000) is handled separately per Section 9.1 — refunded in full in this case since damages were already covered from advance.

Landlord statement for the exit period: shows month 1 commission only. Does NOT show the 425,000 refund (it was never landlord money to begin with). Shows no change to landlord payable from the held balance that was refunded.

---

## 21. Tenant Classification (Payment Scoring)

Rolling 12-month window (or since move-in if less).

```
Score = (0.50 × On-Time Rate) + (0.25 × Advance Bonus) + (0.15 × Completeness) + (0.10 × Tenure Factor)
```

- **On-Time Rate:** `(on-time payments ÷ total periods) × 100`
- **Advance Bonus:** 2+ periods ahead = 100; 1 period ahead = 50; else 0
- **Completeness:** `(paid ÷ invoiced) × 100`, capped at 100
- **Tenure Factor:** `(months active ÷ 24) × 100`, capped at 100

**Tiers:**
| Range | Tier | Label |
|---|---|---|
| 90–100 | Platinum | Excellent |
| 75–89 | Gold | Good |
| 50–74 | Silver | Fair |
| 0–49 | Red | At Risk |

Recalculated at end of each billing cycle via management command. Internal-only — not visible to tenant or landlord. Per-house score + weighted overall score for multi-house tenants.

---

## 22. Edge Cases

- **Abandonment / unpaid utilities:** ad-hoc charge on house → deduct from deposit → shortfall billed to landlord
- **Damage on departure:** deduct from deposit → shortfall billed to landlord
- **Partial payments:** track running balances accurately
- **Overpayments:** handle as advance (Section 20)
- **Mid-period tenant change:** pro-rata for both departing and incoming

---

## 23. Configurable Settings Matrix

All settable at estate OR house level (house overrides):

| Setting | Options |
|---|---|
| Commission type | Fixed, Percentage |
| Commission scope | Per House, Per Estate |
| Billing cycle | Hourly → Yearly + Custom |
| Billing mode | Prepaid, Postpaid |
| Pro-rata handling | Calculated / Next-cycle align |
| Security deposit | Amount or periods; waivable |
| Initial deposit | Number of periods; waivable |
| Tax enabled | Yes/No |
| Tax types | VAT, Withholding, Custom |
| Billing currency | UGX, USD, extensible |
| Account manager | Employee FK |
| Collections person | Employee FK |
| Utility billing (water, garbage) | Separate collection flag |

---

## 24. Development Guidelines Recap

- **Monolith Django** — no SPA
- **DRF only** for inbound payment API + outbound notification calls
- **Soft deletes only** on core entities
- **Timezone-aware** — store UTC, display Africa/Kampala (EAT, UTC+3)
- **UGX whole, USD 2dp — hard enforcement:**
  - Create a custom model field `UGXField` (inherits `DecimalField`, `max_digits=15, decimal_places=0`) — decimals rejected at save
  - Create `USDField` (`max_digits=12, decimal_places=2`) — enforces exactly 2 decimals
  - Never use generic `DecimalField` for money — always one of these two
  - Form validators must reject UGX decimal input with a clear error message
  - Pro-rata and commission calculations must apply rounding **at each step**, not just at the end
- **Extensible** chart of accounts, tax types, currencies, billing cycles
- **Index** tenant, house, estate, landlord, invoice period, payment date
- **Enforce query period limits** in views (6/6/12 months)
- **Template inheritance** — `base.html` → role layouts → page templates
- **Minimal JS** — vanilla, for form validation, AJAX filters, Select2

---

## 25. Phased Build Plan

Build incrementally. Each phase must be **runnable and testable** before moving on. Do not skip ahead. Update `PROJECT_STATE.md` at the end of each phase.

### Phase 1 — Project Setup, Custom Auth & Core Models
- **BEFORE ANY CODE:** Activate venv (`workon meili`), install all dependencies per Section 2.1. Write `requirements.txt` (runtime) and `requirements-dev.txt` (dev-only) at repo root. Verify with `pip list` that Django 6.0.4, psycopg 3, celery 5.6.3, django-celery-results, django-celery-beat, flower, django-simple-history, argon2-cffi are all installed.
- Write `docker-compose.dev.yml` at repo root (per Section 2.4) with the RabbitMQ service definition — **but do not start the container.** RabbitMQ and all Celery/Django/Flower processes are managed by the user, not the agent. Ask the user to confirm RabbitMQ is running before any Celery smoke test. If the agent needs to test a Celery task end-to-end, it prompts the user: "Please confirm RabbitMQ container is running (`docker ps` should show `meili_rabbitmq_dev`) and the Celery worker is running in a separate terminal, then reply 'ready'."
- Verify Python version is 3.12+ (`python --version`). Django 6.0 will not install on older versions.
- Verify PostgreSQL connection: `psql -U postgres -d meili_prd01 -h localhost` with password `heaven2870` — if it fails, stop and surface the error, don't proceed.
- Django project `meili_property`
- **Create `accounts` app FIRST** — build custom `User`, `Role`, `UserRole`, `LoginAttempt`, `PasswordResetToken` models (Section 2A)
- Set `AUTH_USER_MODEL = 'accounts.User'` in settings **before any other migrations run**
- Create core app `core` AFTER auth is in place
- PostgreSQL connection (psycopg 3), timezone (UTC storage, Africa/Kampala display), static files (WhiteNoise)
- Configure `PASSWORD_HASHERS` with `argon2.Argon2PasswordHasher` first
- Configure Celery in `meili_property/celery.py`: load from Django settings with namespace `CELERY_`, autodiscover tasks, use RabbitMQ broker URL from env, use `django-celery-results` as result backend (`CELERY_RESULT_BACKEND = 'django-db'`), use `django-celery-beat` as scheduler.
- Add `django_celery_results` and `django_celery_beat` to INSTALLED_APPS. Run `migrate` to create their tables in `meili_prd01`.
- Write a smoke-test Celery task (e.g., `ping`) and verify the full loop works: enqueue from Django shell → worker picks up → result saved in `django_celery_results_taskresult` table → Flower shows it.
- Set up `django-simple-history` — add `simple_history` to INSTALLED_APPS, include `HistoricalRecords()` on every abstract base model descendant
- Custom money fields: `UGXField`, `USDField` in `core.fields` — enforce rounding/decimals at the model level
- Abstract base model: `created_at/by`, `updated_at/by`, `is_deleted/deleted_at/by` (all user FKs point to `accounts.User`)
- `PaginatedListView` mixin + `pagination.html` template partial in `core`
- Models: Currency, Landlord, Estate, House, Tenant, TenantHouse, Employee, Supplier, BillingCycle, TaxType
- Link `Tenant.user`, `Landlord.user`, `Employee.user` OneToOne to `accounts.User`
- Implement `get_effective_setting(house, field_name)` utility
- Implement `has_role(user, role_name)` and `role_required` decorator + `RoleRequiredMixin`
- Admin registrations with search/filter/list (use Django admin with custom user)
- Data migration: seed UGX/USD currencies, default roles (Admin/AM/Collections/Sales/Finance/Tenant/Landlord), VAT/Withholding (inactive), billing cycles
- Initial superuser creation command (custom, respects the User model)
- Tests: custom user auth, role assignment, soft delete, override logic, tenant-house M2M, UGX rejects decimals, USD rounds to 2dp
- **Verify:** Admin works with custom user, can log in, role-based access works in shell, entities creatable, override logic testable

### Phase 2 — Chart of Accounts & Accounting
- `accounting` app
- Models: AccountType, Account, JournalEntry, JournalEntryLine, BankAccount
- Seed default chart of accounts (Section 14.2) — note:
  - **Commission Income** is a standalone Revenue account (NOT merged with Rent Income)
  - **Two advance-holding liability accounts** seeded from day one: `Tenant Advance Payments Held — Managed Properties` and `Tenant Advance Payments Held — Meili-Owned`
  - Utility `get_advance_holding_account(house)` returns the correct account based on house ownership (House.landlord == Meili → Meili-Owned; else Managed)
- Double-entry validation (post-time) — posting blocked unless debits == credits
- Admin + employee views: COA list/detail, journal entry form (formsets), general ledger
- Commission Revenue drill-down report (Phase 2 minimum: by period; refine in Phase 7)
- Tests: balance validation, account hierarchy, posting, commission income isolated correctly
- **Verify:** Journal entries balance, ledger accurate, chart includes all required accounts

### Phase 3 — Employee Dashboard: UI Layout, Design System, Entity CRUD
- `dashboard` app
- `_variables.css` with full design tokens
- `base.html` with sidebar (multi-level, collapsible, state-persistent, logo/initials), header (global search 4-table results, bell, profile with initials fallback), footer (Okumpi Technologies, v1.1.0, dynamic year)
- Select2 everywhere; pagination everywhere; "Coming Soon" placeholder; custom 403/404/500
- Auth: custom login/logout/password reset views from `accounts` app (Section 2A). Role-based decorators/mixins applied to every view.
- CRUD for: Landlord, Estate, House (effective settings display), Tenant, TenantHouse workflow, Employee, Supplier, BankAccount
- Profile edit restrictions enforced server-side
- Tests: permission boundaries, derived statuses (tenant/house)
- **Verify:** Full sidebar/header/footer work; sidebar state persists; search returns 4 tables; create Tenant → Prospect → attach → Active → detach → Exited + Vacant

### Phase 4 — Billing Engine (Invoices, Payments, Voids, Credit Notes, Refunds)
- `billing` app
- Models:
  - **Invoice** with status field (`Draft`, `Issued`, `Partially Paid`, `Paid`, `Overdue`, `Voided`, `Cancelled`) — enforce state transitions via a `transition_to(new_status)` method that validates allowed transitions. Block deletion at model level unless `status == 'Draft'` (override `delete()` to raise `ProtectedFinancialRecord` if called on non-draft).
  - **InvoiceTaxLine**
  - **Payment** (with approval_status)
  - **PaymentAllocation**
  - **Receipt** (payment receipt)
  - **RefundReceipt** (separate model or flag on Receipt — distinct template)
  - **AdHocCharge**
  - **InvoiceVoid** (links to original invoice: void_reason, reason_category, void_date, maker, checker, approval timestamps, linked reversing JournalEntry)
  - **CreditNote** (number, original_invoice FK, amount, reason, reason_category, status, maker, checker, linked JournalEntry)
  - **Refund** (number, tenant, tenant_house, amount, method, source_account FK, destination_details, reference_number, linked_credit_note, linked_held_advance_account, reason, status, maker, checker, linked JournalEntry)
- Add `invoice_generation_status` to TenantHouse (Active/Paused/Stopped) + pause controls UI
- Invoice generation logic: cycle-aware, pro-rata, tax-aware, journal entries
- `generate_invoices` as a Celery task (`@shared_task` in `billing/tasks.py`). Schedule via `django-celery-beat` with a PeriodicTask entry (cron: `0 * * * *`, hourly). The schedule is stored in the database so it can be edited from Django admin. Also exposed as a management command `python manage.py generate_invoices` for manual runs/backfill.
- Ensure README documents the four required processes (Django, Celery worker, Celery beat, Flower) plus RabbitMQ Docker container
- **Manual Invoice Creation** (Section 16.4): form with period_from, period_to, tenant, house, rent amount, tax lines, issue date, due date, notes. Backdated period allowed with required reason. Save as Draft or Issue Immediately. Permission-gated (Admin/Finance/Account Manager).
- **Void Invoice workflow** (Section 16.5): maker-checker ALWAYS required, no trusted-bypass, self-approval blocked. Creates reversing journal entry on void date (not backdated). Handles payment unapplication. Commission reversal. Tenant notification.
- **Credit Note workflow** (Section 16.7): maker-checker ALWAYS required. Proportional commission reversal. Credit balance routes to held-advance account. Voidable credit notes supported (reverse the credit).
- **Delete Draft Invoice** (Section 16.6): Super Admin only, only when status == Draft. Model-level block prevents deletion of any non-draft. UI button hidden for non-Super-Admins. URL manipulation returns 403.
- **Refund workflow** (Section 16.8): maker-checker ALWAYS required. Source account selection, destination details capture, reference number required. Refund receipt template distinct from payment receipt. Journal entry routes to correct liability account (held-advance, security deposit refundable, or AR credit).
- FIFO allocation. **Advance payments:** do NOT pre-generate future invoices. Instead, credit the correct `Tenant Advance Payments Held` account (routed by house ownership via `get_advance_holding_account(house)` — Managed Properties or Meili-Owned) and auto-apply when each future period's invoice is generated (see Section 20). Commission recognised only at period allocation, not at payment receipt.
- Commission calculation (fixed + percentage, including shortfall recovery from arrears) — runs per-period, never on held advance balances
- **Landlord statement filter: held advance balances invisible to landlord**; employee dashboard shows both advance-holding accounts separately with totals per account type
- Advance Payments Report (employee only): tenant, house, held balance, **which account holds it**, scheduled release dates, months-covered, badge for balances ≥ 2 full periods
- **Separate arrears vs current billing columns on tenant statement**
- **Overdue auto-transition** as a Celery beat task running daily at 01:00 Africa/Kampala — flips `Issued` or `Partially Paid` invoices past due_date to `Overdue`
- Receipt HTML (mobile) + print CSS (thermal 58/80mm + A4). Refund receipt template clearly labelled "REFUND RECEIPT" (not "RECEIPT").
- Manual payment form with all required fields
- Maker-checker workflow for payments (self-approval blocked, trusted bypass allowed for payments only). Voids/credit notes/refunds NEVER allow trusted bypass.
- Pending Approvals queue with separate tabs/filters for: Payments, Ad-hoc Charges, Invoice Voids, Credit Notes, Refunds. Overdue (> 24h) highlighted.
- Ad-hoc charge entry (landlord/Meili target)
- Sequential numbering: `INV-YYYYMM-NNNNN`, `CRN-YYYYMM-NNNNN`, `REF-YYYYMM-NNNNN`, `RCP-YYYYMM-NNNNN`. Numbers never reused. Voided invoices retain their number (gaps in sequence are an audit red flag; preserved-but-voided is correct).
- Permission matrix (Section 16.9) enforced server-side via `RoleRequiredMixin` on every view and `@role_required` on every form handler. UI-only hiding is not sufficient.
- Tests:
  - FIFO, pro-rata, advance with remainder, commission (both types + shortfall recovery), tax, rounding
  - Maker-checker for payments (approve/reject/self-block/trusted bypass works)
  - **Maker-checker for voids/credit-notes/refunds: trusted bypass BLOCKED, self-approval BLOCKED, different employee required**
  - Void creates reversing journal with balanced debits/credits
  - Credit note amount cannot exceed original invoice amount
  - Draft delete works for Super Admin; blocked by model-level check for Issued invoices even via direct ORM call
  - Refund journal routes to correct source liability account based on context
  - Sequential numbering integrity: voided invoices keep their number, no gaps except from voids
- **Verify:** Full flow works end to end. Invoices can be created manually with backdated periods, voided with reason, credit-noted with partial amounts, refunded to tenants. Draft can be deleted by Super Admin; Issued invoice cannot be deleted by anyone. Invoices can be paused/resumed. Receipts and refund receipts print correctly in both formats. All journal entries balance. Flower shows all Celery tasks executing cleanly.

### Phase 5 — Tenant & Landlord Portals
- `portal` app, namespaces `/tenant/` and `/landlord/`
- Tenant portal: login, dashboard, payment history, invoices, receipts, profile (preferences editable — not bio-data)
- Landlord portal: login, dashboard, house-by-house view, statement extraction (max 6 months), match `MARY NANTAYIRO Jan 2026 Report.pdf or Teddy.pdf` layout
- **Landlord statement excludes held advance balances** — only current/allocated period shown. Enforce at the query level, not just template.
- **Statement generation as a Celery task** (`generate_landlord_statement`) triggered on-demand (when landlord clicks Extract) AND on schedule (`django-celery-beat` PeriodicTask — monthly, 1st of each month at 06:00 Africa/Kampala).
- **Statement auto-delivery as a separate Celery task** (`deliver_landlord_statement`) chained after generation — picks up the landlord's preferred channel (Email / WhatsApp) and dispatches via the notification service (Phase 6 builds this adapter; Phase 5 can stub-send to logs initially and wire properly once Phase 6 lands).
- Cross-tenant/cross-landlord isolation (server-side enforcement)
- Tests: data isolation, query limit enforcement, statement generation task produces correct PDF matching `MARY NANTAYIRO Jan 2026 Report.pdf or Teddy.pdf` layout
- **Verify:** Tenant A cannot see Tenant B. Landlord statement matches PDF reference. Statement generation shows up as a task result in `django_celery_results_taskresult` and in Flower.

### Phase 6 — Payment API & Notification Integration
- Install DRF (only now) + `drf-spectacular`
- `api` app
- `POST /api/v1/payments/`: API key auth, match/allocate/receipt/notify/respond. Auth + rate-limiting via `django-ratelimit` on the endpoint.
- Notification service: abstract interface, Africa's Talking adapter (SMS/WhatsApp), Django email adapter. All outbound calls via `httpx`.
- **All notification dispatch happens via Celery tasks** — endpoint/view enqueues a task, task performs the actual HTTP call to the aggregator. This keeps the payment webhook response time fast (<200ms) and isolates aggregator failures from business logic.
- Task examples: `send_payment_confirmation`, `send_receipt`, `send_overdue_reminder`, `send_statement`. Each with retry policy (`autoretry_for=(httpx.HTTPError,)`, `retry_backoff=True`, `max_retries=5`).
- Templates: payment confirmation, receipt, reminder, statement delivery
- Delivery status tracking: update `NotificationDelivery` model with sent/delivered/failed based on aggregator callbacks and task outcomes
- Wire into: payment allocation (Phase 4 hooks), statement generation (Phase 5 hooks), overdue invoice transitions (Phase 4 beat task)
- Connect Flower to the Django auth system so only Admin role can access the dashboard
- Tests: API flows (valid/duplicate/unmatched/malformed), notification with mocked aggregator (use `httpx` MockTransport), retry behaviour on aggregator failure, task-level error handling
- **Verify:** Test payload → allocation → Celery task enqueued → notification triggered (mocked in tests, real stub in dev). Failed notifications visible in Flower with exception traceback stored in TaskResult.

### Phase 7 — Scoring, Reports, Security Deposits
- TenantScore model + calculation service as a Celery task `calculate_tenant_scores` in `scoring/tasks.py`. Scheduled via `django-celery-beat` PeriodicTask (cron: `0 2 * * *` — daily 02:00 Africa/Kampala). Editable from Django admin. Also exposed as a management command for manual runs/backfill.
- Tier assignment, multi-house weighted score
- Dashboard filter/sort by tier; score breakdown on tenant detail; hidden from tenant/landlord portals
- Security deposit lifecycle (Held / Partially Applied / Fully Applied / Refunded) with departure workflow
- **Exit workflow with held-advance refund path (Section 20.5):** strict-order application (outstanding invoices → damages/ad-hoc charges → optional transfer to other active tenancies → refund remainder). Applies to both held-advance accounts. Refund receipt template (distinct from payment receipt). Refund goes through maker-checker.
- Exit workflow UI shows held balance (per account), security deposit, outstanding invoices, damages entry form, step-by-step computation panel, final refund or landlord shortfall figure
- Deposit movements and held-advance releases create journal entries
- Internal reports: repairs per house, estate-level costs, collection performance, tenant acquisition, occupancy rates, revenue summary, **Advance Payments Report** (filterable by tenant/house/estate/landlord/ownership type, badge ≥ 2 full periods)
- Tests: scoring accuracy, tier assignment, deposit deduction, **exit refund order (outstanding → damages → transfer → refund)**, cross-account transfer when tenant's other tenancy is different ownership type, report correctness
- **Verify:** Scoring correct; deposit flow works; exit refund flow works end-to-end with correct journals on both held-advance accounts; reports accurate

### Phase 8 — Audit, Polish, Production
- Full audit trail via `django-simple-history` on all core models + custom `AuditLog` middleware capturing IP + user-agent for every state change. Financial transactions (invoices, payments, voids, credit notes, refunds) get additional detail: maker, checker, approval timestamps.
- Audit log viewer in employee dashboard (filterable by user, model, action, date range). Accessible to Admin and Super Admin only.
- Edge cases: mid-period tenant change (pro-rata for both departing and incoming), move between houses within same estate, commission change effective-dating, strict UGX/USD rounding enforcement
- UI polish: responsive across mobile/tablet/desktop, print-friendly receipt/statement/refund-receipt templates, form validation (client-side JS + server-side Django), loading indicators for AJAX, Select2 everywhere
- Production Docker Compose (`docker-compose.prod.yml` — Linux target):
  - `postgres` service (PostgreSQL 17, named volume, backup strategy documented)
  - `rabbitmq` service (3.13-management, named volume, healthcheck)
  - `web` service (Django + Gunicorn, reads env from `.env.prod`)
  - `celery_worker` service (same image as web, command `celery -A meili_property worker --loglevel=info` — prefork pool, default concurrency)
  - `celery_beat` service (same image, command `celery -A meili_property beat --scheduler django_celery_beat.schedulers:DatabaseScheduler`)
  - `flower` service (password-protected, exposed only to internal network by default, accessible via reverse proxy for admins)
  - `nginx` service (reverse proxy, TLS termination via Let's Encrypt / certbot, static file serving)
  - All services `restart: unless-stopped`, healthchecks on web / rabbitmq / postgres
- Production settings split: `settings/base.py`, `settings/dev.py`, `settings/prod.py`. Load via `DJANGO_SETTINGS_MODULE` env var.
- Env vars via `django-environ` — no hardcoded secrets. `.env.example` committed, `.env.prod` in deployment vault only.
- Static file strategy: WhiteNoise in dev, nginx in prod (collectstatic during Docker image build).
- `sentry-sdk[django]` wired into prod settings for error tracking. Celery task errors automatically captured.
- Database backup strategy: daily `pg_dump` via cron on the Linux host, retained 30 days, weekly offsite backup to S3-compatible storage.
- README documents:
  - Windows dev setup (venv, Docker Desktop for RabbitMQ, 4-terminal process layout)
  - Linux production setup (Docker Compose, env vars, TLS, backups)
  - How to run migrations in prod
  - How to access Flower in prod (behind auth)
  - Escalation runbook: what to do when RabbitMQ is down, when Celery worker OOMs, when invoice generation fails
- Integration tests: end-to-end flows (tenant created → attached to house → invoice generated by Celery beat → payment received via API → FIFO allocation → receipt generated → notification queued → landlord statement at month end → commission recognised correctly)
- **Verify:** Full prod Docker Compose boots cleanly on a fresh Linux VM. All four processes stable under load. Audit log captures every change. Backups run successfully. Error in a Celery task surfaces in Sentry. Statement PDF matches `MARY NANTAYIRO Jan 2026 Report.pdf or Teddy.pdf` format across real sample data.

---

## 28. Final Note

This spec is intentionally opinionated about what NOT to build. Resist the temptation to add features, endpoints, or frameworks not explicitly called for. Every deviation from "Django monolith + two DRF endpoints" increases credit burn and system complexity. Build what's specified, ship it, iterate from user feedback.
