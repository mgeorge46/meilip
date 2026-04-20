# Meili Property Solution

Django 6.0 monolith for Ugandan real-estate management — multi-estate portfolio with
tenant billing, landlord remittance, commission accounting, and double-entry ledger.

See [SPEC.md](SPEC.md) for the product specification and [PROJECT_STATE.md](PROJECT_STATE.md)
for current phase, decisions log, and outstanding items.

---

## Requirements

- Python **3.12+** (tested on 3.14.3)
- PostgreSQL 16+ (`meili_prd01`)
- Docker Desktop (for RabbitMQ container)
- Windows 11 (dev) / Ubuntu 24.04 LTS (prod)

## One-time setup

```bash
# activate virtualenv
workon meili

# install pinned deps (Phase 1 used requirements.lock.txt)
pip install -r requirements.lock.txt

# run migrations
python manage.py migrate

# seed chart of accounts, roles, currencies, tax types, billing cycles
# (handled by the seed migrations — no extra command needed)

# create your initial superuser
python manage.py create_initial_superuser \
    --email you@example.com \
    --phone +256700000000 \
    --first-name Admin \
    --last-name User \
    --password <strong-password>
```

Environment is read from `.env` via `django-environ`. Copy `.env.example` and
fill in DB + RabbitMQ credentials if different from defaults.

---

## Running the stack — FOUR processes + RabbitMQ

Claude Code does **not** start these processes. Run each in its own terminal.

### 1. RabbitMQ (broker) — Docker

```bash
docker compose -f docker-compose.dev.yml up -d rabbitmq
```

Management UI: <http://localhost:15672> — user `meili`, password `heaven2870_rmq`, vhost `meili`.

### 2. Django dev server

```bash
python manage.py runserver
```

### 3. Celery worker

```bash
celery -A meili_property worker --pool=solo --loglevel=info
```

`--pool=solo` is required on Windows (prefork doesn't work). On Linux prod, use
`--concurrency=<N>` with the default prefork pool.

### 4. Celery beat (scheduler)

```bash
celery -A meili_property beat --scheduler django_celery_beat.schedulers:DatabaseScheduler --loglevel=info
```

**Only ever run ONE beat process** — duplicate beats fire every periodic task twice.
The DatabaseScheduler lets admins edit schedules via Django admin without restart.

### 5. Celery Flower (monitoring, optional)

```bash
celery -A meili_property flower --port=5555
```

Dashboard: <http://localhost:5555>

---

## Scheduled tasks (Phase 4)

Wired via `django-celery-beat` (DB-backed, editable in Django admin under
*Periodic Tasks*):

| Task                            | Schedule                | Purpose                                                 |
|---------------------------------|-------------------------|---------------------------------------------------------|
| `billing.generate_invoices`     | Every hour (`0 * * * *`)| Issue invoices for any tenancy whose next period is due |
| `billing.mark_overdue`          | Daily 01:00 Africa/Kampala | Flip ISSUED / PARTIALLY_PAID past due_date → OVERDUE |
| `meili_property.ping`           | On-demand               | Celery smoke test                                       |

Manual trigger (for a specific billing date):

```bash
python manage.py generate_invoices --today 2026-04-21
```

---

## Tests

```bash
python manage.py test
```

Current total: **79 tests** (accounts 9, core 15, accounting 18, dashboard 18, billing 19).
Focus is on financial correctness, permissions, and maker-checker — not trivial CRUD.

---

## Money & currency rules

- **UGX:** `UGXField` — whole numbers only, decimals rejected at save
- **USD:** `USDField` — exactly 2 decimals, rounded on save
- **Never** use generic `DecimalField` for money
- Ledger maths is UGX-only; USD source transactions convert at posting time
- All datetimes stored UTC, displayed Africa/Kampala

## Financial-record protection

- **Invoice lifecycle (SPEC §16):** Issued / Paid / Voided / Overdue invoices can
  never be deleted. Only DRAFT invoices can be deleted, only by Super Admin.
- To cancel an issued invoice → **Void** (reversing journal entry, record preserved)
- To reduce an issued invoice → **Credit Note** (amount-capped)
- **Voids, Credit Notes, Refunds always require maker-checker** — no trusted-employee
  bypass, self-approval always blocked
- Sequential numbers (INV/CRN/REF/RCP) are never reused; voided invoices keep theirs

## Advance-holding (SPEC §20)

- Two distinct COA accounts, routed automatically:
  - `TENANT_ADVANCE_HELD_MANAGED` — fiduciary, never shown on landlord statements
  - `TENANT_ADVANCE_HELD_MEILI` — deferred revenue on Meili-owned estates
- Routing via `accounting.utils.get_advance_holding_account(house)`

---

## Process lifecycle reminder

The agent (Claude Code) writes code and configuration.
**You** run the runserver / worker / beat / flower / docker processes. After any
code change that affects background work, restart the worker and beat.
