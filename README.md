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

---

## Production deployment (Ubuntu 24.04 via Docker)

### Prerequisites

- Ubuntu 24.04 LTS host with Docker 26+ and `docker compose` plugin
- DNS `A` record pointing to the host
- TLS cert + key on disk at `./tls/fullchain.pem` + `./tls/privkey.pem`
  (use certbot or your org's PKI; renewal is out of scope for this compose file)
- Outbound HTTP(S) allowed to Sentry, Africa's Talking, SMTP

### First deploy

```bash
# on the prod host
git clone <repo> /opt/meili && cd /opt/meili
cp .env.prod.example .env     # then fill in secrets
mkdir -p tls && cp /path/to/{fullchain,privkey}.pem tls/

DJANGO_ENV=prod docker compose -f docker-compose.prod.yml up -d --build

# verify
curl -fsS https://<host>/healthz/
curl -fsS https://<host>/readyz/
```

On first boot, `web` runs migrations automatically (see compose command).

### Admin bootstrap

```bash
docker compose -f docker-compose.prod.yml exec web \
  python manage.py create_initial_superuser \
  --email admin@example.com --phone +256700000000 \
  --first-name Admin --last-name User --password <strong>
```

### Updating

```bash
cd /opt/meili && git pull
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d      # rolling restart
```

### Backups

- **Daily** — systemd timer runs `scripts/backup_db.sh` at 02:00, keeps 14 days
  of dumps under `/var/backups/meili/`.
- **Weekly** — Sunday backup is uploaded to S3 if `S3_BACKUP_BUCKET` is set.
- To restore: `gunzip < dump.sql.gz | psql $DATABASE_URL`.

### Flower / Celery monitoring

- `https://<host>/flower/` — HTTP basic-auth via `FLOWER_BASIC_AUTH`.
- Put Flower behind your VPN or IP allow-list in nginx if the host is
  internet-facing. Default config trusts the basic-auth + upstream-only network.

---

## Operations runbook

### RabbitMQ is down
1. `docker compose -f docker-compose.prod.yml logs rabbitmq | tail -200`
2. Restart: `docker compose -f docker-compose.prod.yml restart rabbitmq`.
3. If the volume is corrupted, stop the service, rename `rabbitmq_data`
   volume, and recreate. Celery tasks queued at the instant of failure are
   LOST — `django-celery-beat` will re-fire scheduled tasks on the next tick.
4. Workers reconnect automatically once the broker is healthy.

### Worker OOM / stuck task
1. `docker compose -f docker-compose.prod.yml restart celery_worker`
2. Inspect Flower for the offending task name. If a specific task leaks
   memory, cap retries in the task decorator and add a soft time limit.
3. For beat-scheduled tasks, pause via Django admin → *Periodic Tasks* → untick
   *Enabled* while you investigate.

### Invoice generation failed for a period
1. Identify the failed periodic task in Flower or in `django_celery_results`.
2. Re-run manually for the affected date:
   ```bash
   docker compose -f docker-compose.prod.yml exec web \
     python manage.py generate_invoices --today YYYY-MM-DD
   ```
   The task is idempotent — it skips tenancies that already have an invoice
   for the period.

### Paystack / mobile-money webhook keeps retrying
1. Check `/api/v1/payments/` in the provider's dashboard — our endpoint returns
   a 2xx only after the idempotency check + FIFO allocation commit.
2. If a duplicate `(api_key, transaction_id)` is observed the endpoint returns
   the original 200 OK payload. That's intentional; ignore.
3. 4xx responses (auth failures, malformed payload) are visible in the provider
   retry log; our Sentry captures 5xx only.

### Sentry flooded
- `SENTRY_TRACES_SAMPLE_RATE` in `.env` (default 0.1) caps perf tracing.
- For error noise, bump the integration filter in `settings/prod.py`.

### Rotating secrets
- `SECRET_KEY`, DB password, broker password: update `.env`, then
  `docker compose -f docker-compose.prod.yml up -d` to apply. Sessions survive
  because `SESSION_ENGINE` is DB-backed; `SECRET_KEY` rotation invalidates
  CSRF tokens until browsers refresh.
