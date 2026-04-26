# Project State

## Phase F.2 (2026-04-25)
**Collections targets + tiered bonuses + entity audit + UX fixes.**

- New models in `core/models.py`:
  - `CollectionsTarget(employee, month, target_amount, notes)` — month is normalised to day 1; unique per employee+month.
  - `CollectionsBonusBracket(label, min_amount, max_amount, rate_percent, is_active)` — admin-configurable tiers; first matching active bracket whose [min, max] range contains the collected amount wins. `max_amount=null` means "and above".
- Service module `core/collections.py`:
  - `compute_employee_month(employee, month, *, house=None, estate=None)` — sums `PaymentAllocation.amount` where `applied_at` ∈ [month, month+1), `is_advance_hold=False`, parent payment is APPROVED/AUTO_APPROVED, and the allocated invoice's `tenant_house.house.collections_person == employee`. Optional house/estate scope.
  - `compute_bonus(amount)` — picks first active bracket containing amount, returns `(bracket, bonus_amount)`.
  - `build_performance_rows(month, employees=None, house=None, estate=None)` — returns dataclass rows with target / collected / attainment_pct / bracket / bonus.
- CRUD pages: `/core/collections/targets/`, `/core/collections/brackets/`. Permissions: ADMIN / SUPER_ADMIN / FINANCE.
- Report: `/core/reports/collections-performance/` — month picker + employee + house + estate filters, summary cards (Total target / collected / bonus payable), per-employee table with attainment progress bar, CSV export, active-schedule reminder.
- Sidebar links added under Reports.
- Seed updates: 3 demo bonus brackets (2% / 3.5% / 5%), monthly targets for collections employees over the last 3 months, demo `House.collections_person` populated.

### UX fixes shipped en route
- **White-on-white status badges** — added CSS for `.badge.status-{draft,issued,partially_paid,paid,overdue,voided,cancelled,pending,approved,auto_approved,rejected,sent_back}` so status text is readable on every list.
- **Approvals queue "Approve does nothing"** — root cause was self-approval block. Seed re-pointed PENDING records to a non-superuser maker (`demo-maker@example.com`), and seed_demo now uses non-super maker by default.
- **Invoice list, Payment list, Expense Claim list** — search bar, status / method / category filters, date filters, page-size selector (50/100/150/200, default 100), CSV export across all three.
- **Invoice detail** — top stat strip (Status / Total / Paid / Outstanding) with colour coding; Payment Allocations table now links to each Payment detail with method + status badges.
- **Detail-page None guards** — `LandlordPayout`, `SupplierPayment`, `ExpenseClaim` detail templates now `{% if obj.maker %}…{% endif %}` instead of `|default:obj.maker.email|default:'—'` (which crashed when maker/checker was null).
- **BankAccount Create/Update** — added missing `get_success_url` (was throwing ImproperlyConfigured on save).
- **Receipt list** — guarded `r.payment.tenant` / `r.refund.tenant` chains for refund-only receipts.

### Entity connectivity audit (run against seeded data)

| # | Check | Count | Status |
|---|---|---:|---|
| 1 | Tenant ↔ Payment FK | — | ✅ OK |
| 2 | Landlord ↔ Payout FK | — | ✅ OK |
| 3 | Supplier ↔ SupplierPayment FK | — | ✅ OK |
| 4 | Employee ↔ ExpenseClaim FK | — | ✅ OK |
| 5 | `get_effective_setting(house, 'collections_person')` | — | ✅ OK (resolver in use) |
| 6 | Receipt orphans (no payment AND no refund) | **0** | ✅ |
| 7 | NotificationDelivery orphans (no tenant AND no landlord) | **0** | ✅ |
| 8 | Active invoices without `source_journal` | **26** | 🟡 By design for seed; real billing flow via `Invoice.transition_to(ISSUED)` posts GL. Live data is unaffected. |
| 9 | PaymentAllocation orphans (no invoice & not advance-hold) | **0** | ✅ |
| 10 | Houses without `collections_person` at any level | **1 / 6** | 🟡 Unassigned-data gap |
| 11 | Houses without `account_manager` at any level | **5 / 6** | 🟡 Significant unassigned-data gap |
| 12 | Island employees (not referenced anywhere as stakeholder) | **0** | ✅ |
| 13 | Suppliers with zero payments | 1 / 4 | FYI |
| 14 | Stale CollectionsTargets (employee no longer collections_person) | 0 | ✅ |

**Action items from audit:**
- (#8) **Invoices without source_journal** — the seed creates `Invoice` rows directly with `status=ISSUED` to populate the dashboard chart, bypassing `Invoice.transition_to(ISSUED)` which posts the JE. This is a demo-data shortcut, not a production-flow bug. Future improvement: add a `--with-gl` flag to seed_demo to optionally route invoices through the proper transition.
- (#10, #11) **Unassigned houses** — most seeded houses lack an `account_manager`. Either bulk-assign in seed_demo or add a "Houses missing assignment" report. Tracked as tech debt.

## Current Phase
Phase E.3 (bug fixes + one-tenancy-per-house constraint + ExpenseClaim feature) shipped 2026-04-24. Phase E.2 (sidebar wiring + Receipts / Invoice Schedules / Trial Balance / Landlord Statement index + GL posting for payouts) shipped 2026-04-24. Phase E.1 (Landlord/Supplier payment models + detail tabs) shipped 2026-04-23. **151/151 prior tests still passing** (no regressions). New Phase E functionality not yet covered by unit tests — tracked as tech debt.

## Last Completed
Phase E.3 — bug fixes, house-occupancy constraint, ExpenseClaim (2026-04-24):

### Bugs fixed
- **`LandlordStatementView`** — `TypeError: Cannot combine queries on two different base models`. Line 508 OR-ed a House queryset with an Estate `values_list`; rewrote with a single `Q()` across House (via direct landlord FK or via estate.landlord).
- **`ReceiptListView`** — `FieldError: Invalid field name(s) given in select_related: 'tenant'`. `Receipt` has no direct tenant FK; tenant hangs off `payment.tenant` or `refund.tenant`. Fixed `select_related`, search, CSV, and `receipt_list.html` template.
- **Cosmetic** — `landlord_statement_index.html` referenced `l.houses_count` (non-existent); replaced with `l.houses.count`.

### One active tenancy per house (SPEC constraint)
- Added `UniqueConstraint(fields=['house'], condition=Q(status='ACTIVE', is_deleted=False), name='uniq_active_tenancy_per_house')` on `core.TenantHouse` (migration `core.0008`). DB-verified: trying to activate a 2nd tenant on an occupied house raises `IntegrityError` with the constraint name.
- `TenantHouse.clean()` mirrors the constraint with a readable `ValidationError` so forms surface a human error before the DB does.
- `TenantHouseActivateView.post()` now also pre-checks and returns a friendly `messages.error` + redirect instead of a 500 when the house is already occupied.
- A tenant can still have multiple houses (multiple ACTIVE rows with distinct `house`). Constraint is PARTIAL on status=ACTIVE, so EXITED / PROSPECT rows don't collide.

### ExpenseClaim feature — employees submit expense claims with receipt photos
- **Model** `billing.ExpenseClaim` (EXP-prefix, `MakerCheckerMixin` with `allow_trusted_bypass = False` — expenses always need a checker). Fields: claimant (Employee FK), category (enum: MAINTENANCE_REPAIRS / UTILITIES / TRANSPORT / OFFICE_SUPPLIES / COMMS / LEGAL_PROFESSIONAL / OTHER), description, related_house (optional), amount (UGX), incurred_at, **receipt_photo** (`ImageField`, uploads to `MEDIA_ROOT/expense_receipts/<claimant_id>/<EXP-number>.<ext>`), reimbursement_bank (BankAccount FK), notes, source_journal (nullable). Migration `billing.0005`.
- **Forms** `ExpenseClaimForm` with Bootstrap widgets + `accept="image/*,application/pdf"` on the file input.
- **Views** `ExpenseClaimListView` (search by number / description / claimant / amount + category + status filter + CSV export; **non-finance users only see their own claims**), `ExpenseClaimCreateView` (defaults `claimant` to the requesting user's Employee profile; stores `maker` + submits as PENDING), `ExpenseClaimDetailView` (renders the uploaded photo inline — or a "Open PDF" button for PDF attachments).
- **URLs** `/billing/expense-claims/` list/new/<pk>. Added to sidebar Billing submenu. Added as a tab on the Employee detail page (shows that employee's own claims with status, receipt link, amount).
- **Approvals queue** — `ApprovalsQueueView` now has 8 tabs; added Landlord Payouts, Supplier Payments, **Expense Claims**. The queue uses existing `ApprovalActionView` / `MakerCheckerMixin.approve()` flow. Self-approval blocked by the mixin (maker ≠ checker).
- **GL posting signal** — `billing/signals_gl.py::post_expense_claim_journal` fires on `post_save` when `approval_status in (APPROVED, AUTO_APPROVED)` and `source_journal` is still null:
  - Category → system_code via `ExpenseClaim.CATEGORY_TO_SYSTEM_CODE` mapping; falls back to `MAINTENANCE_REPAIRS` if the category-specific account isn't seeded.
  - Posts: `Dr <expense account>` / `Cr <reimbursement_bank.ledger_account>`.
  - Idempotent.
  - Verified: approved a test claim for 35,000 → JE posted `Dr 5100 M&R / Cr 1100 Cash on Hand`, claim.source_journal_id set.

### Dangling-issue scan (no action needed, documented for completeness)
- `coming-soon` remaining in sidebar/header: only "Admin Settings" — SPEC item not yet built, OK to leave stubbed.
- All `{% url %}` references in sidebar/header resolve to registered URL names.
- `billing/signals_gl.py` only imported in `billing/apps.py::BillingConfig.ready()` — clean wiring.

### Tech debt opened this phase
- Category-specific expense accounts (UTILITIES_EXPENSE, TRANSPORT_EXPENSE, OFFICE_SUPPLIES_EXPENSE, COMMS_EXPENSE, LEGAL_EXPENSE, OTHER_OPERATING_EXPENSE) are NOT yet seeded in the COA. Until they are, every approved ExpenseClaim posts to `5100 Maintenance & Repairs` via the fallback. Follow-up: add a `seed_expense_accounts` data migration.
- No unit tests yet for ExpenseClaim list/create/signal/search — tracked as tech debt.
- Seed demo command does NOT yet create sample expense claims. Low priority.

Phase E.2 — sidebar wiring, missing list pages, GL posting (2026-04-24):

## Last Completed
Phase E.2 — sidebar wiring, missing list pages, GL posting (2026-04-24):
- **Sidebar fix** — Billing menu was sending Invoices / Payments / Receipts / Invoice Schedules / Trial Balance / Landlord Statements to the `coming-soon` stub even though views existed. All six now point to real URLs. Added Landlord Payouts, Supplier Payments, Approvals Queue, and Ad-hoc Charges to the Billing submenu.
- **New list pages**
  - `ReceiptListView` (`/billing/receipts/`) — search by number / tenant / payment ref, kind filter, CSV export.
  - `InvoiceScheduleListView` (`/billing/invoice-schedules/`) — read-only overview of tenancies + `invoice_generation_status` (ACTIVE / PAUSED / STOPPED), search by tenant/house/estate, in-row pause/resume button, CSV export.
  - `LandlordStatementIndexView` (`/billing/landlord-statements/`) — picker; each row deep-links to the existing `billing:landlord-statement` per-landlord page.
  - `trial_balance` (`/accounting/reports/trial-balance/`) — per-account posted debits & credits, net debit/credit balance per row, period filter (from / to), Balanced/Unbalanced badge, CSV export. Ledger math confirmed: 5,220,000 debits = 5,220,000 credits across 11 posted journals with demo data.
- **GL posting for LandlordPayout + SupplierPayment** — `billing/signals_gl.py` wires `post_save` on both models. When a row becomes APPROVED/AUTO_APPROVED and has no `source_journal` yet, the signal creates a balanced JournalEntry and posts it:
  - LandlordPayout → `Dr LANDLORD_PAYABLE (2100) / Cr <bank.ledger_account>`
  - SupplierPayment → `Dr MAINTENANCE_REPAIRS (5100) / Cr <bank.ledger_account>`
  - Idempotent: no-ops if `source_journal` already set. JE reference allocated via existing `NumberSequence`.
  - Wired in `billing/apps.py ready()`.
- **Backfill** — re-saved all existing approved LandlordPayout / SupplierPayment rows to post their journals; 6 new journal entries posted cleanly.

### Bug squashed en route
- `NumberSequence` for `JE-202604-*` was out of sync with actual `JournalEntry.reference` values — caused `IntegrityError: duplicate key` on first signal firing. Fixed with a one-shot sync that walks existing `reference` strings and bumps `next_value` to `max(existing) + 1` per (prefix, year, month). Consider a management command `sync_number_sequences` as follow-up tech debt.

Phase E.1 — UI polish + disbursement models (2026-04-23):

## Last Completed
Phase E — UI polish + disbursement models (2026-04-23):
- **New models** — `billing.LandlordPayout` and `billing.SupplierPayment` (both `MakerCheckerMixin + CoreBaseModel`, UGX amount, bank_account FK, Method choices BANK/MOBILE_MONEY/CHEQUE/CASH/OTHER, reference_number, notes, sequential numbering via `LPO` / `SPY` prefixes, `source_journal` nullable FK for future GL posting). SupplierPayment adds `service_description`, `invoice_reference`, `related_house`. Migration `billing/0004`.
- **Admin** — `LandlordPayoutAdmin`, `SupplierPaymentAdmin` with search + status filter, approval-trail fields read-only.
- **CRUD** — `billing/views.py` adds List/Create/Detail for both. List views support search (number / reference / amount / supplier or landlord name / service) + status filter + `?export=csv`. Auto-approval via `try_trusted_autoapprove()` on create (trusted maker).
- **URLs** — `/billing/landlord-payouts/` and `/billing/supplier-payments/` (list / new / <pk>).
- **Templates** — 6 new templates under `templates/billing/`: `landlord_payout_{list,form,detail}.html`, `supplier_payment_{list,form,detail}.html`; all Bootstrap 5, data-table-wrap pattern, approval-trail card.
- **Detail-page payment tabs**
  - Tenant → added **summary stats row** (active tenancies, total paid, invoices, open invoices) + Payments tab now has search (receipt # / reference / exact amount) and pagination (50/100/150/250) + Messages tab pagination; active tab persists via `?tab=` query param.
  - Landlord → rebuilt with tabs Overview / Estates / Houses / **Payouts (searchable + paginated)** / Messages; "Record payout" button deep-links to `LandlordPayoutCreate?landlord=<id>`.
  - Supplier → rebuilt from a bare KV page into full tabbed detail with **stat summary row** (total paid, payment count, supplier type) + Payments tab (search across number / service / invoice ref / external ref / amount; 50/100/150/250 per page); "Record payment" deep-link.
- **Accounting tables polish (Phase B recap, shipped earlier)** — Chart of Accounts, General Ledger, Commission Income, Bank Accounts all have filter bars, sticky-header data-tables, and `?export=csv`. Journal Entry form has Bootstrap widgets, grouped header/lines cards, live Balanced/Unbalanced badge, and Save-&-post is disabled until debits = credits.
- **Reusable pieces** — `core/utils.export_csv(rows, columns, filename)`, `templates/core/_data_toolbar.html`, `.data-table-wrap` / `.data-toolbar` / `.detail-tabs` CSS classes.
- **Seed command** — `py manage.py seed_demo` (idempotent; `--reset` flag) creates 2 landlords, 2 estates, 4 houses, 2 tenants, tenancies, 6 invoices, 4 payments, 3 suppliers, 4 landlord payouts, 5 supplier payments, 5 notification deliveries. All demo rows are prefixed `Demo` for safe cleanup.

## Explicitly deferred (still not built — tagged for a later phase)
Each one is its own phase of work, not cosmetic:
- **Payroll-run workflow + GL posting** (Employee fields exist; runs deferred — needs SalariesExpense / AllowancesExpense / SalariesPayable / PAYEPayable routing, maker-checker, period concept).
- **Balance Sheet report** (Commission Report + Trial Balance shipped; Balance Sheet still pending).
- **Multi-tax stacking** (single tax_type FK today).
- **USD-billed transactions / full multi-currency ledger** (UGX-only math).
- **AuditLog CRUD / approve / void / refund capture** (auth events auto-captured; CRUD+approval actions still need a signal/decorator pass).
- **Notification bell unread template wiring** (backend + Celery shipped; 5-min template wiring deferred).
- **Password-reset email via Celery `enqueue_notification`** (dev inline path still used).
- **Employee auto-provisioning on User insert** + **employee self-provisioning** (both require User-model changes).
- **Journal Entry void / draft hard-delete** (rare admin action).
- **NotificationDelivery JSON functional index** for idempotency lookups (add once volume warrants).
- **Supplier account category routing** — SupplierPayment currently always posts to `MAINTENANCE_REPAIRS`. Mapping supplier kind / category → different expense accounts (utilities, admin, supplies) is a future refinement.
- **Number sequence resync management command** (`sync_number_sequences`) to prevent the NumberSequence-vs-reference drift issue we hit this phase.

## Tech debt opened this phase
- No unit tests yet for LandlordPayout / SupplierPayment list-search, create+auto-approval, CSV export — should add before production use.
- Tenant/Landlord/Supplier detail views compose tabs in-view (paginated context + search). If a 5th tab lands, extract a `TabPayload` dataclass helper.

---

## Phase 8 (2026-04-22):
- **Interactive KPI dashboard** — `dashboard/services.py` compiles live metrics from the ledger: Outstanding AR, Billed/Collected this month, Collection Rate %, Occupancy %, Active tenants, Overdue count+total; AR ageing buckets (Not yet due / 0-30 / 31-60 / 61-90 / 90+); 12-month billed-vs-collected trend; notification health (7-day success rate). `templates/dashboard/home.html` renders all of it with inline SVG bar charts (pure JS, no framework) and a conic-gradient ring widget; Refresh button fetches `/kpi/` for live updates without a full page reload. CSS in `base.css` adds `.kpi-grid`, `.kpi-card`, `.tone-*`, `.dashboard-grid`, `.ring`, `.chart-legend` etc. — matches existing design system.
- **Roadmap copy** — `dashboard/views.coming_soon` now passes a ROADMAP dict (Invoices, Payments, Receipts, Invoice Schedules, Trial Balance, Landlord Statements, Admin Settings) with descriptions; template renders "This feature is on the roadmap and will be available in an upcoming release." plus the full roadmap list highlighting the current feature.
- **Outbound notifications API** — `POST /api/v1/notifications/` (rate-limited 120/m per API key) accepts `{template, tenant_id|landlord_id|recipient, channel?, context, idempotency_key?}`, resolves channel/recipient from party preference, returns delivery row; `GET /api/v1/notifications/{id}/` polls delivery status (attempt_count, provider_message_id, sent_at, error_detail). Idempotency via `NotificationDelivery.context__idempotency_key`. Swagger docs auto-updated.
- **AuditLog** — `accounts.AuditLog` (actor, action, target_type/id/repr, ip_address, user_agent, path, method, detail JSON, timestamp, indexed; append-only, never-deleted — admin `has_add_permission`/`has_delete_permission` return False). `AuditRequestMiddleware` (thread-local for signal handlers). `signals_audit.py` wires Django's `user_logged_in`/`user_logged_out`/`user_login_failed` → AuditLog. `AuditAction` choices cover login/permission/CRUD/approve/void/refund/payment/export/API. Admin/Super-Admin viewer at `/accounts/audit/` with filters (actor email icontains, action, target type, date range, page size). Django admin exposes read-only grid.
- **Settings split** — `meili_property/settings/` package with `base.py` (shared), `dev.py` (DEBUG=True, loose cookies, StaticFilesStorage), `prod.py` (DEBUG=False, HSTS/SSL-redirect/X-Frame DENY, SMTP email, Sentry wiring via `sentry-sdk[django]` with Django+Celery+Logging integrations, verbose logging config). Selector `__init__.py` reads `DJANGO_ENV` env var (defaults to dev). `django.contrib.humanize` moved into base INSTALLED_APPS.
- **Docker + nginx prod stack** — `Dockerfile` (python:3.12-slim-bookworm, libpq/pango/cairo native deps, non-root `meili` user, build-time collectstatic with dummy env, gunicorn 3-worker default CMD). `docker-compose.prod.yml` with 7 services (postgres:16-alpine, rabbitmq:3.13-management, web, celery_worker, celery_beat **singleton**, flower, nginx:1.27) + healthchecks + named volumes + shared `x-django-env` anchor. `nginx.conf` (TLS, HSTS, 443-only, `/static/`+`/media/` served directly, `/flower/` reverse-proxy, `/` → gunicorn upstream). `/healthz/` + `/readyz/` (DB probe) endpoints added to `meili_property/urls.py`. `.env.prod.example` documents every required prod env var. `scripts/backup_db.sh` — daily pg_dump, 14-day local rotation, weekly S3 sync when `S3_BACKUP_BUCKET` set.
- **README runbook** — Production deployment section (prerequisites, first deploy, admin bootstrap, updating, backups, Flower access) + operations runbook (RabbitMQ down / worker OOM / invoice gen failure / webhook retry storms / Sentry flood / secret rotation).
- **Requirements** — `djangorestframework`, `drf-spectacular`, `sentry-sdk[django]` added to `requirements.txt`.
- **Tests** — **+19 tests, total 151 passing**: 7 outbound-notification API tests (auth, queue, 404, idempotency, validation, raw-recipient, status poll); 3 dashboard tests (home renders, KPI API structure, auth gate); 5 AuditLog tests (middleware captures IP+UA+XFF; login signal writes row; admin can view; non-admin 403; filter by action + actor); 1 roadmap template test; 1 full end-to-end pipeline test (tenant → issued invoice → webhook payment → FIFO allocation → receipt → commission posting balanced (Dr Landlord Payable, Cr Commission Income, 10% of 500k) → notification enqueued).
- **Bug fixed en route** — `dashboard.services.top_arrears` had a `select_related`+`only()` conflict on `TenantHouse.house` that would 500 the dashboard as soon as any outstanding invoice existed; removed the over-eager `.only()`.

## Previous Phase — Phase 6b (2026-04-22):
- Deleted empty stale template folders: `templates/people/`, `templates/accounting_ui/`
- `api` app — `ApiKey` (per bank/provider, hashed+prefix lookup, IP allowlist, issue/revoke, `issue_api_key` management command), `WebhookEvent` (unique by `(api_key, transaction_id)` → DB-level idempotency, full audit), `ApiKeyAuthentication` DRF class, rate-limit `60/m` per key via `django-ratelimit`
- `POST /api/v1/payments/` webhook — validates payload, matches tenant by phone (exact or last-9-digit tail) → id_number → prior reference, creates AUTO_APPROVED `Payment`, runs existing `apply_payment()` (FIFO + advance routing + commission), returns `{status, receipt_number, payment_id, payment_number, applied_amount}`, handles duplicate/unmatched/malformed distinctly (201/202/202/400), logs every attempt
- drf-spectacular `GET /api/v1/schema/` + `GET /api/v1/docs/` (Swagger UI, admin-only link from sidebar)
- `notifications` app — `NotificationDelivery` (SMS/WhatsApp/Email × QUEUED/SENDING/SENT/FAILED/SKIPPED × 5 templates, historied, per-channel provider response + attempt_count), provider registry `get_provider(channel)` (Africa's Talking SMS + WhatsApp via `httpx`, Django email backend, console fallback for dev/test), `enqueue_notification()` helper resolves tenant preference
- Celery tasks (all with `autoretry_for=(httpx.HTTPError,) retry_backoff=True max_retries=5`): `deliver_notification`, `send_payment_confirmation`, `send_receipt`, `send_overdue_reminder`, `send_statement`, plus `sweep_queued_notifications` for broker-outage recovery
- Hooks wired: `apply_payment()` → `send_payment_confirmation.delay(pk)`; `mark_overdue_invoices()` → `send_overdue_reminder.delay(pk)`. All hooks wrapped in try/except so a broker outage never rolls back the ledger
- Employee-only delivery dashboard: `/notifications/` list (filter by status/channel/template) + `/notifications/<pk>/` detail (provider response, error, retry count); sidebar Operations section
- Flower: `FLOWER_BASIC_AUTH` + `FLOWER_ADMIN_REQUIRED_ROLES = ("ADMIN", "SUPER_ADMIN")` — reverse-proxy enforcement documented
- Settings: `INSTALLED_APPS` += `rest_framework`, `drf_spectacular`, `api`, `notifications`; `REST_FRAMEWORK` default auth = `ApiKeyAuthentication`; `NOTIFICATION_PROVIDERS` env-configurable (console default for dev/test); `AT_API_KEY`, `AT_USERNAME`, `AT_SENDER_ID`, `AT_WHATSAPP_CHANNEL` env vars; `EMAIL_BACKEND` defaults to console
- Migrations: `api.0001`, `notifications.0001` (historied on both)
- 18 new tests (total **132 passing**): key-issue hash never exposed; auth (missing/invalid/revoked → 401); IP allowlist; payload validation → 400 + logged event; happy-path 201 with FIFO-applied invoice → PAID; phone-tail matching; unmatched payer → 202; duplicate txn_id idempotency (single Payment row); channel resolution (SMS/Email/WhatsApp); rendered body text; SKIPPED when no recipient; `AfricasTalkingSMSProvider` via `httpx.MockTransport` parses messageId; retry policy: `deliver_notification` raises `httpx.ConnectError` so Celery retries, row stays FAILED; apply_payment hook creates a `PAYMENT_CONFIRMATION` row
- Known benign: Celery broker unreachable during tests surfaces as a stderr kombu log (swallowed by service-layer try/except); zero test impact

## Previous Phase — Phase 7 (2026-04-22):
- `scoring` app — `TenantScore` (historied, 0-100, 5 tiers PLATINUM/GOLD/SILVER/BRONZE/WATCH), weighted multi-house scoring service, Celery task `scoring.calculate_tenant_scores` scheduled daily 02:00 Africa/Kampala via `django-celery-beat`, `calculate_scores` management command, tenant list filter/sort by tier + score breakdown on tenant detail, dedicated score roster view
- Security Deposit lifecycle — `SecurityDeposit` (HELD/PARTIALLY_APPLIED/FULLY_APPLIED/REFUNDED) + `SecurityDepositMovement` (APPLY_INVOICE/APPLY_DAMAGE/REFUND) with balanced double-entry journals
- Tenant Exit Settlement (SPEC §20.5) — `ExitSettlement` (maker-checker, `allow_trusted_bypass=False`) + `exit_services.py` implementing strict-order: outstanding invoices → damages/ad-hoc → optional cross-tenancy transfer → refund remainder. Cross-ownership transfer posts to BOTH held-advance accounts (Dr source, Cr target) so MANAGED vs MEILI-owned separation is preserved. Refund row created PENDING its own maker-checker cycle
- Exit workflow UI at `/billing/tenancies/<pk>/exit/` — shows held-managed/held-meili/deposit/outstanding balances, damages entry, transfer dropdown, refund method/bank/reference, computed plan preview, self-approval warning
- Internal reports suite (`billing/reports.py`) — Repairs per House, Estate Cost Rollup, Collection Performance (12-month rolling), Tenant Acquisition, Occupancy Rates, Revenue Summary, plus enhanced Advance Payments Report with filters (tenant/house/estate/landlord/ownership) and ≥ 2-full-period staleness badge
- Sidebar Reports section rewired to live report URLs + Tenant Credit Scores
- 16 new tests (total **114 passing**, up from 98): tier boundaries, new-tenant neutral, on-time-vs-late, multi-house weighting, bulk scoring, deposit application balanced journal + deposit-balance property, exit strict-order with self-approval block, cross-ownership MANAGED→MEILI transfer, report correctness (revenue/occupancy/collections/repairs/advances)

## Next Up
**Production cutover** — issue real API keys to integrating banks (MoMo / Stanbic / etc.), obtain live AT credentials and flip `NOTIFICATION_PROVIDERS` from `console` to `at`, provision TLS cert (certbot) + DNS, first `docker compose -f docker-compose.prod.yml up -d --build`, load initial SUPER_ADMIN via `create_initial_superuser`, verify `/healthz` + `/readyz`, install the systemd timer for `scripts/backup_db.sh` (02:00 daily), configure S3 bucket for weekly backups, add Sentry DSN + test error path. Optional post-launch: wire `AuditLog.record(...)` calls into mutating views (invoice void/credit note/refund/payment approval) for deeper coverage than simple-history row-level change tracking; currently audit captures auth events automatically.

## Completed Phases
- [x] Phase 1 — Project Setup, Custom Auth & Core Models
- [x] Phase 2 — Chart of Accounts & Accounting
- [x] Phase 3 — Employee Dashboard: UI Layout, Design System, Entity CRUD
- [x] Phase 4 — Billing Engine
- [x] Phase 5 — Tenant & Landlord Portals + Mini Payroll + UI Overhaul
- [~] Phase 6 — (partial) Model realignment + utility billing wired; stat-card UI still pending
- [x] Phase 6b — External API + Notifications (2026-04-22)
- [x] Phase 7 — Scoring, Reports, Security Deposits (2026-04-22)
- [x] Phase 8 — Audit, Dashboards, Production Hardening (2026-04-22)

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

## Phase 2 Deliverables
- `accounting` app — `AccountType`, `Account` (hierarchical, system-coded), `JournalEntry`, `JournalEntryLine`, `BankAccount` — all historied
- Double-entry invariants at post-time: balanced, non-empty, all lines on postable & active accounts
- DB-level integrity on `JournalEntryLine`: `debit≥0 & credit≥0`, not-both-sides, at-least-one-side
- Seeded Chart of Accounts (22 accounts per SPEC §14.2):
  - Assets: Cash on Hand, Bank Accounts (parent), Mobile Money (parent), AR Tenant Balances, Security Deposits Held
  - Liabilities: Landlord Payable, Security Deposits Refundable, Tax Payable, **Tenant Advance Payments Held — Managed Properties**, **Tenant Advance Payments Held — Meili-Owned** (two distinct accounts, never merged)
  - Equity: Owner's Equity, Retained Earnings
  - Revenue: **Rent Income** (Meili-owned only), **Commission Income** (standalone — never merged with Rent Income)
  - Expenses: Maintenance & Repairs, Office Supplies, Service Costs
- `accounting.utils.get_advance_holding_account(house)` — auto-routes by `house.effective_landlord.is_meili_owned`; raises when landlord cannot be resolved
- Stable system codes (`CASH_ON_HAND`, `AR_TENANT_BALANCES`, `COMMISSION_INCOME`, `RENT_INCOME`, `LANDLORD_PAYABLE`, `TENANT_ADVANCE_HELD_MANAGED`, `TENANT_ADVANCE_HELD_MEILI`, etc.) referenced by code — safe renames allowed
- `JournalEntry.post(user)` — balances, stamps `posted_at` / `posted_by`, generates `JE-YYYYMM-NNNNNN` reference
- `JournalEntry.reverse(user, memo)` — posts offsetting entry, marks original `REVERSED`; `balance()` includes both POSTED and REVERSED statuses for ledger integrity
- Employee views (gated to Admin/Super Admin/Finance/Account Manager):
  - COA list (`/accounting/accounts/`) with category filter + search
  - Account detail (`/accounting/accounts/<pk>/`) — balance, children, per-line ledger
  - General Ledger (`/accounting/ledger/`) — filter by account code + date range, running debit/credit totals
  - Journal Entry form (`/accounting/journals/new/`) with inline-formset lines, save draft / save & post
  - Journal Entry detail (`/accounting/journals/<pk>/`) with post button
  - Commission Income Report (`/accounting/reports/commission/`) — drill-down by period, shows recognised net (credits − debits)
- Admin registered for all accounting models (journal entry with line inline)
- 42 unit tests passing (accounts: 9, core: 15, accounting: 18): chart completeness, two-advance-account separation, commission-vs-rent isolation, hierarchy constraints, balanced-posting enforcement, reversal roundtrip, line-side constraints, router dispatch (managed/meili/house-override)

## Outstanding Phase 1 Verification (user-driven)
- [ ] User starts RabbitMQ: `docker compose -f docker-compose.dev.yml up -d rabbitmq`
- [ ] User starts Celery worker + beat + Flower in separate terminals (see SPEC §2.5)
- [ ] End-to-end ping: `python manage.py shell -c "from meili_property.celery import ping; r = ping.delay(); print(r.get(timeout=10))"` should print `pong` and appear in `django_celery_results_taskresult`
- [ ] User creates their superuser:
  `python manage.py create_initial_superuser --email <email> --phone +256... --first-name <> --last-name <> --password <strong>`

## Phase 4 Deliverables
- `billing` app — full billing engine per SPEC §16 / §20
- Models (all historied where they carry money):
  - `NumberSequence` — atomic per-prefix, per-period counter (INV/CRN/REF/RCP pad 5; JE pad 6). Allocated via `select_for_update()` inside `allocate_number(prefix)`
  - `Invoice` — state machine DRAFT→ISSUED→{PARTIALLY_PAID→PAID, OVERDUE→PAID, VOIDED, CANCELLED}; `DELETABLE_STATUSES = {DRAFT, CANCELLED}` enforced on `delete()` (raises `ProtectedFinancialRecord` otherwise — Super Admin only, drafts only)
  - `InvoiceLine`, `InvoiceTaxLine` — per-line tax breakdown
  - `Payment` + `PaymentAllocation` — FIFO tenant allocation with approval_status
  - `Receipt` / `RefundReceipt` — generated on paid / refunded events
  - `AdHocCharge` — standalone charges (damages, utilities, cleaning, etc.)
  - `InvoiceVoid` — reversing journal entry workflow
  - `CreditNote` — amount-capped; proportional commission reversal for managed properties
  - `Refund` — source-account routed (overpayment, held-advance, damage-deposit, etc.)
- `core.TenantHouse.invoice_generation_status` field (ACTIVE/PAUSED/STOPPED) + `invoice_generation_note` for pause/stop reason
- `billing/exceptions.py` — `ProtectedFinancialRecord`, `SelfApprovalBlocked`, `TrustedBypassBlocked`, `InvalidInvoiceTransition`, `CreditNoteExceedsInvoice`, `InvoiceGenerationPaused`
- `billing/services.py` — business logic:
  - `_add_cycle(d, cycle)` / `compute_next_period` / `compute_prorata` — handles HOUR/DAY/WEEK/MONTH/QUARTER/SEMI_ANNUAL/YEAR with month clamping; PRO_RATA vs NEXT_CYCLE first-invoice behaviour
  - `generate_invoice_for_tenancy` — creates, issues, auto-applies any held advance on the same tenancy
  - `_issue_and_post` — Dr AR; Cr LANDLORD_PAYABLE (managed) or RENT_INCOME (Meili-owned); Cr TAX_PAYABLE
  - `apply_payment` — FIFO across outstanding invoices; surplus lands in `get_advance_holding_account(house)` (routed by `landlord.is_meili_owned`); issues RCP receipt
  - `try_apply_advance_to_invoice` — held advance → newly-issued invoice (Dr Held Advance, Cr AR)
  - `recognize_commission_on_allocation` — recognised only when cash is applied; PERCENTAGE via `%`, FIXED via pro-rata over rent total; skipped on Meili-owned; posts Dr LANDLORD_PAYABLE, Cr COMMISSION_INCOME
  - `execute_void` — reverses accrual JE + commission postings; detaches payments back to held-advance
  - `execute_credit_note` — Dr RENT_INCOME / LANDLORD_PAYABLE, Cr AR; proportional commission reversal on managed
  - `execute_refund` — Dr source account, Cr bank/cash ledger; issues REF receipt
  - `mark_overdue_invoices` — daily sweep
- `MakerCheckerMixin` with `allow_trusted_bypass` class flag — True on Payment/AdHocCharge (trusted employees self-post), False on Void/CreditNote/Refund (never bypassable); self-approval always blocked
- `billing/tasks.py` — `@shared_task` `generate_invoices` (hourly) + `mark_overdue` (daily 01:00) wired via `django-celery-beat` periodic tasks
- `python manage.py generate_invoices [--today YYYY-MM-DD]` CLI for manual dry-runs / date override
- Employee views (role-gated server-side via `RoleRequiredMixin` + `@role_required`):
  - Invoice list/detail/create (manual issue — backdate requires reason), delete (Super Admin + draft-only)
  - Payment list/detail/create
  - Ad-hoc charge list/create
  - Void/Credit/Refund create
  - Approvals queue with tabs (payment/adhoc/void/credit/refund) + >24h overdue highlight
  - Receipts in three formats: mobile HTML, A4 print, thermal 58/80mm
  - Reports: Advance Payments (Managed/Meili-owned badges), Tenant Statement (arrears vs current split), Landlord Statement (never shows held advances)
  - Pause/resume tenancy invoice generation
- `billing/urls.py` with `app_name = "billing"` — routes for invoices/payments/adhoc/voids/credit-notes/refunds/approvals/receipts/reports/statements
- Sequential numbering integrity: voided invoices keep their number; no gaps allowed; `CRN-`/`REF-`/`RCP-` follow the same monthly-scoped format
- Admin registrations (read-mostly) for all billing models
- 19 new billing tests (total **79 passing**):
  - Sequential numbering — atomic allocation under concurrent writes, voided keeps number
  - State-machine guard — only draft/cancelled deletable, illegal transitions raise
  - Invoice generation — managed routes to LANDLORD_PAYABLE, paused blocks generation
  - FIFO allocation — oldest first; surplus to managed advance account
  - Commission — 10% recognised on managed, none on Meili-owned
  - Maker-checker — self-approval blocked; trusted bypass allowed on Payment; void/credit/refund never bypass
  - Void workflow — accrual + commission reversed, AR cleared
  - Credit note bounds — amount cap enforced, number format CRN-, commission proportional reversal
  - Refund routing — held-advance source posts balanced journal, REF- number
  - Overdue sweep — ISSUED past due_date → OVERDUE

## Phase 5 Deliverables
- **UI Overhaul (Bootstrap 5.3.3)** — CDN-loaded alongside `bootstrap-icons@1.11.3` + `select2-bootstrap-5-theme@1.3.0` in `templates/base.html`. `django-widget-tweaks` added to INSTALLED_APPS. `static/css/base.css` layer auto-promotes native `<input>`/`<select>`/`<table>` to Bootstrap-styled controls so legacy templates benefit without per-template rewrites. New utility classes: `.form-grid` (2-col paper-form layout), `.form-section`, `.page-header`, `.num`, `table-responsive-wrap`. Sidebar collapse-to-icons restored (logo hidden + toggle centred when `.app-shell.collapsed`).
- **Form paper-form style** — `templates/core/_form.html` rewritten with widget_tweaks: every field rendered as a `.mb-3` cell inside `.form-grid`, textarea spans 2 cols, required-asterisk styling, form-actions footer with spacer + Cancel + Submit.
- **Accounting base subnav** — `templates/accounting/_base.html` now extends `base.html` and exposes `{% block accounting_content %}`; all six accounting children switched from `{% block content %}` so they inherit sidebar/header/CSS and render correctly at `/accounting/accounts/` etc.
- **Account CRUD** — fixed "cannot add chart of account" bug. New `AccountForm` (accounting/forms.py) with `parent` queryset filtered to non-postable active accounts; `AccountCreateView` + `AccountUpdateView` wired at `accounting:account-create` / `accounting:account-update`, rendered via `templates/accounting/account_form.html` (thin wrapper around `core/_form.html`). Gated to ADMIN/SUPER_ADMIN/FINANCE. List page given Bootstrap table + "New account" button + row-level edit icon.
- **Mini Payroll (Employee fields)** — `core.Employee` extended with `job_title`, `employment_type` (FULL_TIME/PART_TIME/CONTRACT/INTERN), `hire_date`, `base_salary`, `allowance_transport`/`allowance_housing`/`allowance_airtime`/`allowance_other`, `paye_monthly`, `nssf_employee`, `nssf_employer`, `other_deduction`, `bank_name`/`bank_account_name`/`bank_account_number`/`bank_branch`, `tin`, `nssf_number`. Three properties: `gross_monthly`, `net_monthly`, `total_employer_cost`. Migration `core/0004_employee_allowance_airtime_and_more.py`. `EmployeeForm` exposes all new fields; `hire_date` uses HTML5 date picker.
- **Payroll Chart of Accounts** — `accounting/migrations/0004_seed_payroll_accounts.py` seeds: **1600** `STAFF_ADVANCES_RECEIVABLE` (asset); **2500** parent *Payroll Payables* → **2510** `SALARIES_PAYABLE`, **2520** `PAYE_PAYABLE`, **2530** `NSSF_PAYABLE`, **2540** `OTHER_PAYROLL_PAYABLE`; **5400** parent *Payroll Expenses* → **5410** `SALARIES_EXPENSE`, **5420** `ALLOWANCES_EXPENSE`, **5430** `NSSF_EMPLOYER_EXPENSE`. System codes exported from `accounting/utils.py`. Two-pass create (accounts then parent wiring) to satisfy parent-FK on migration.
- **Portal app** — new `portal` app mounted at `/tenant/` and `/landlord/`. `TenantPortalMixin` / `LandlordPortalMixin` enforce `request.user.tenant_profile` / `landlord_profile` (raises `PermissionDenied` otherwise). All querysets filter server-side — a tenant cannot enumerate another tenant's invoice by URL guessing, and a landlord cannot see another landlord's houses/statements. Dedicated `templates/portal/_base.html` (public-facing navbar, NO employee sidebar) + `tenant/_base.html` + `landlord/_base.html`.
- **Tenant portal** — Dashboard (tenancies + open balances + recent invoices), Invoice list + detail (line items + allocations), Payment history, Receipts, Profile (bio data read-only; `preferred_notification` + `preferred_receipt` editable only).
- **Landlord portal** — Dashboard (portfolio summary + recent statements), House list (estate-landlord + house-override landlord unioned via `portal.services.models_or`), Statement list, Statement request (6-month window with client + server enforcement), Statement download (FileResponse, PDF inline), Profile (bio + banking read-only; `preferred_statement_channel` + `whatsapp_number` editable only).
- **Landlord Statement PDF** — `portal/services.build_statement_context` + `render_statement_pdf` using ReportLab Platypus. Layout matches reference PDFs (MARY NANTAYIRO Jan 2026 Report, Teddy): MEILI PROPERTY SOLUTIONS title, landlord/report-date/period header, houses table grouped by estate with SPAN estate headers, DEFAULTERS section (arrears from periods before window still outstanding with any in-window payments), Summary + LandLord Payments side-by-side. Commission computed only for managed landlords (`is_meili_owned=False`). **Held-advance accounts are NEVER touched** — the statement reads `Invoice.total` / `Invoice.amount_paid` directly; `TENANT_ADVANCE_HELD_MANAGED` / `_MEILI` are fiduciary per SPEC §20 and absent from every query and render path.
- **Statement persistence** — `LandlordStatement` model (status PENDING/GENERATED/DELIVERED/FAILED, channel EMAIL/WHATSAPP/BOTH/MANUAL_DOWNLOAD, FK to Landlord, unique on (landlord, period_start, period_end), cached totals, FileField stored under `media/landlord_statements/YYYY/MM/`). Admin registration with search/filter/autocomplete. Migration `portal/0001_initial.py`.
- **6-month window cap** — `portal.services.enforce_window` raises `StatementWindowError` if `period_end - period_start > 6 months` or if end < start. Enforced at every entry point: `build_statement_context`, view POST, Celery task.
- **Celery tasks** — `portal.tasks.generate_landlord_statement` (bind=True, retries=3) builds context + renders PDF + persists row + chains `deliver_landlord_statement`. `deliver_landlord_statement` reads `landlord.preferred_statement_channel` and logs to `[stub-email]` / `[stub-whatsapp]` until Phase 6 notification adapter lands, then marks row DELIVERED with channel + notes. Landlords with `NONE` channel get `MANUAL_DOWNLOAD` status.
- **Monthly beat schedule** — `portal.tasks.schedule_monthly_statements` seeded as a `django_celery_beat.PeriodicTask` via `portal/0002_seed_beat_schedule.py` — crontab `0 6 1 * *` in `Africa/Kampala`. Dispatches per-landlord generation jobs for the previous calendar month. Landlords with `preferred_statement_channel=NONE` are skipped.
- **Landlord model additions** — `preferred_statement_channel` (EMAIL/WHATSAPP/BOTH/NONE, default EMAIL) + `whatsapp_number` (E.164 or blank — falls back to `phone`). `LandlordForm` exposes both.
- **Tests (portal)** — `portal/tests.py`: tenant sees only own invoices, tenant cannot open another tenant's invoice detail (404), non-tenant user blocked (403), landlord sees only own houses, non-landlord blocked, 6-month window accepted, 7-month rejected, reversed window rejected, `MAX_STATEMENT_MONTHS == 6`, statement context excludes other landlords' invoices, payroll COA seeded + active + postable. **12 tests passing; 91 tests total across the suite.**
- **URL mounts** — `meili_property/urls.py` → `path("tenant/", include(("portal.tenant_urls", "tenant"), namespace="tenant"))`, `path("landlord/", include(("portal.landlord_urls", "landlord"), namespace="landlord"))`.

## Phase 7 Deliverables (Scoring, Reports, Security Deposits)
- **`scoring` app** — `TenantScore` (OneToOne Tenant, 0-100, tier PLATINUM/GOLD/SILVER/BRONZE/WATCH, JSON `breakdown`, historied, `calculated_at` / `calculated_by` stamps). `scoring/tiers.py` defines the 5-tier threshold table (90/75/60/40/0) + colour map + `tier_for_score(s)`. `scoring/services.py` computes weighted sub-scores: on-time 35, arrears-health 25, overdue penalty 15, tenure bonus 10 (linear to 24 mo cap), consistency 15 (stdev of days-late). Multi-house tenants get `total_billed`-weighted blending with equal-weight fallback; brand-new tenants short-circuit to `NEUTRAL_SCORE_NEW_TENANT = 60`.
- **Scoring Celery task** — `@shared_task scoring.calculate_tenant_scores` in `scoring/tasks.py` running `calculate_scores_for_all()`. Scheduled via `django-celery-beat` PeriodicTask seeded in migration `scoring/0002_seed_beat_schedule.py` with crontab `0 2 * * *` Africa/Kampala (daily 02:00 UGT). Admin-editable. Also exposed as `python manage.py calculate_scores [--tenant PK] [--today YYYY-MM-DD]`.
- **Scoring UI** — `scoring:score-list` roster view (`q` + `tier` filters, STAFF_ROLES gate, tier badge column). `core:tenant-list` gained `tier` + `sort` filters (score_asc/score_desc). `tenant_detail.html` renders a "Credit score" card with tier badge, score, breakdown components, and new-tenant neutral notice. Tier badge CSS classes (`tier-platinum/gold/silver/bronze/watch`) appended to `static/css/base.css`. Score hidden from tenant/landlord portals.
- **Security Deposit lifecycle** — `SecurityDeposit` (OneToOne TenantHouse; `amount_held/applied/refunded` UGXFields; status HELD/PARTIALLY_APPLIED/FULLY_APPLIED/REFUNDED; `hold_journal` FK; `balance` property; `recompute_status()`). `SecurityDepositMovement` (APPLY_INVOICE/APPLY_DAMAGE/REFUND + FK to journal entry). Both historied.
- **Tenant Exit Settlement (SPEC §20.5 strict-order)** — new `ExitSettlement` (OneToOne TenantHouse, maker-checker, `allow_trusted_bypass=False`, DRAFT/EXECUTED/CANCELLED). Captures starting balances + plan JSON + executed-at / executed-by stamps. `billing/exit_services.py` implements:
  - `compute_exit_settlement(th)` — reads held balances for BOTH MANAGED and MEILI accounts, security deposit balance, outstanding invoices
  - `build_settlement_plan(comp, damages, transfer_to_tenancy_ids)` — strict-order plan JSON
  - `execute_exit_settlement(...)` @transaction.atomic — applies damages as AdHocCharges (AUTO_APPROVED) on a one-shot exit Invoice, consumes held-advance via `try_apply_advance_to_invoice`, applies security deposit via `_apply_deposit_to_invoice` (Dr SECURITY_DEPOSIT_HELD / Cr AR_TENANTS), executes cross-tenancy transfers via `_execute_transfer_between_held_accounts` (balanced Dr source-acc / Cr target-acc journal so MANAGED↔MEILI separation holds), creates a Refund row PENDING its own maker-checker cycle, marks the tenancy EXITED
- **Exit workflow view + template** — `billing:exit-workflow` at `/billing/tenancies/<pk>/exit/` gated to FINANCE_ROLES. GET renders the computed plan; POST with `action=compute` saves draft; POST with `action=execute` calls `settlement.approve(user)` (`SelfApprovalBlocked` raised on self-approval) then `execute_exit_settlement(...)`. Template shows starting balances, outstanding invoices table, damages entry (3 empty rows), optional transfer-to-tenancy select, refund method/bank/destination/reference, computed-plan preview, and a self-approval warning.
- **Internal reports suite** (`billing/reports.py`, all View-based, FINANCE_ROLES gated):
  - `RepairsPerHouseReport` — approved AdHocCharges with "repair" in description, grouped by house
  - `EstateCostReport` — MEILI-target AdHocCharges grouped by estate (repairs split from other)
  - `CollectionPerformanceReport` — 12-month rolling billed vs collected with rate%
  - `TenantAcquisitionReport` — 12-month new/activated/exited/net tenancy counts
  - `OccupancyReport` — per-estate + portfolio-wide occupancy snapshot
  - `RevenueSummaryReport` — 12-month rent / utilities / ad-hoc breakdown
- **Advance Payments Report enhanced** — filters (tenant/house/estate/landlord/ownership type) + per-row `stale_badge` set when hold has aged ≥ 60 days (≈ 2 full periods). Template rewritten to expose filter form and age column.
- **URL + sidebar wiring** — `billing/urls.py` gained `/reports/repairs/`, `/reports/estate-costs/`, `/reports/collections/`, `/reports/acquisition/`, `/reports/occupancy/`, `/reports/revenue/`. Sidebar Reports section replaced coming-soon placeholders with live links; Tenant Credit Scores roster added.
- **Tests (16 new, 114 total, up from 98)**:
  - `scoring/tests.py` (6) — tier boundary inclusivity, invalid inputs → WATCH, new-tenant neutral, on-time-vs-late ranking, multi-house weighted blending, bulk calculation harness
  - `billing/tests.py` (10) — SecurityDeposit.balance property, `_apply_deposit_to_invoice` posts balanced Dr SEC_HELD / Cr AR and transitions status, exit strict-order (outstanding first, damages second, refund for remainder, Refund row PENDING), self-approval blocked on exit envelope, cross-ownership transfer lands on BOTH MANAGED and MEILI accounts, revenue report sums issued invoices, occupancy report lists fixture estate, collections report matches billed/collected, repairs report filters to "repair" descriptions only, advances report flags stale holds
- **Migrations** — `scoring/0001_initial.py` + `scoring/0002_seed_beat_schedule.py`; `billing/0003_exitsettlement_historicalexitsettlement_and_more.py` (SecurityDeposit, SecurityDepositMovement, ExitSettlement + historical tables).

## Phase 6a Deliverables (Model Realignment + Utility Billing)
- **Name splits** — `Landlord`, `Tenant`, `Employee` gained `first_name` / `last_name` / `other_names` (with validators). `full_name` remains as a denormalized `CharField(blank=True)` that is auto-composed on `save()` via `core.models.compose_full_name` (order: first, other, last). All 49 existing read-sites keep working untouched. Forms switched to the split fields.
- **NSSF removed** — `Employee.nssf_employee` / `nssf_employer` / `nssf_number` dropped; `net_monthly` and `total_employer_cost` recalculated without NSSF. PAYE/TIN/allowances/banking all retained. COA: 2530 NSSF_PAYABLE and 5430 NSSF_EMPLOYER_EXPENSE deleted. Constants `SYS_NSSF_PAYABLE` / `SYS_NSSF_EMPLOYER_EXPENSE` removed from `accounting.utils`. Migration `accounting/0005_drop_nssf_seed_utility_income.py` drops NSSF accounts and seeds utility-income accounts in one delta.
- **Utility flags on SettingsMixin** — `water_billed_separately`, `garbage_billed_separately`, `security_billed_separately`, `electricity_billed_separately`, `other_bills_billed_separately`, `other_bills_description`. All three-valued booleans (null → inherit), house overrides estate via existing `get_effective_setting(house, field)`. Forms + `SETTINGS_DISPLAY_FIELDS` extended.
- **Utility-income COA** — 4310 `WATER_INCOME`, 4320 `GARBAGE_INCOME`, 4330 `SECURITY_INCOME`, 4340 `ELECTRICITY_INCOME`, 4390 `OTHER_UTILITY_INCOME`, all under parent 4000 Revenue, all postable & active. System codes exported from `accounting.utils`.
- **UtilityKind enum** — `core.models.UtilityKind` (WATER/GARBAGE/SECURITY/ELECTRICITY/OTHER) with companion mapping `UTILITY_FLAG_BY_KIND` (kind → SettingsMixin flag name) and `UTILITY_INCOME_SYSCODE_BY_KIND` (kind → accounting system code).
- **InvoiceLine.Kind.UTILITY + `utility_kind` field** — new line type for separately-billed utilities. `AdHocCharge` also gained `utility_kind` so employees can classify a recurring water/electricity/etc. charge. Generic ad-hocs keep `utility_kind=""`.
- **Invoice generation** — `generate_invoice_for_tenancy` now inspects each pending `AdHocCharge`'s `utility_kind`; when the matching `*_billed_separately` flag resolves True (house > estate), the charge becomes a `UTILITY` line; when False, it falls back to a regular `AD_HOC` line (bundled accounting, current behaviour).
- **`_issue_and_post` routing** — tallies landlord-target revenue into non-utility vs per-kind utility buckets. Meili-owned houses credit the matching utility income account (4310–4390). Managed houses still credit `LANDLORD_PAYABLE` — utilities are pass-through to the landlord, the per-line description preserves the break-out on the landlord statement.
- **Commission untouched** — `_rent_portion_of` already filtered to `RENT` + `PRORATA` kinds, so `UTILITY` and `AD_HOC` lines never attract commission. Verified by test: 10% commission on rent only, even when a 100,000 UGX water line is on the same invoice.
- **Data migration** — `core/0006_backfill_split_names.py` splits existing `full_name` into first/last/other using a simple heuristic (1-word → first only; 2-word → first+last; 3+ → first+last+middle-joined-as-other).
- **Portal tests realigned** — `PayrollChartOfAccountsTests` trimmed of NSSF codes; new `UtilityIncomeChartOfAccountsTests` asserts all five utility income accounts are active+postable.
- **6 new `UtilityBillingTests`** in `billing/tests.py`: Meili-owned water flag routes to water income, house flag overrides estate flag, flag=False degrades to bundled AD_HOC line, mixed utilities break out independently, managed route stays in landlord payable, commission excludes separately-billed utilities. **98 tests passing (was 92).**

## Decisions Log
- 2026-04-22 (Phase 6b) — ApiKey stored as SHA-256 hash + 12-char non-secret prefix. Prefix is unique-indexed so lookup is O(1); raw key is only returned by `ApiKey.issue()` at creation time. Never logged, never persisted. A DB leak cannot be replayed into the webhook.
- 2026-04-22 (Phase 6b) — Idempotency enforced by DB uniqueness on `(api_key, transaction_id)` on `WebhookEvent`, not by application-level lookups. A replay is detected inside the same transaction that would create the second Payment, so double-crediting is impossible by construction. The response body of the first call is replayed verbatim (status changes to "duplicate").
- 2026-04-22 (Phase 6b) — Webhook payments are AUTO_APPROVED (no maker-checker). Rationale: the integrating bank/provider is already an authenticated trusted source (the API key *is* the authentication); routing them through the checker queue would delay receipting for hours. Manual payments (cash, in-person) still require maker-checker per SPEC §16.9. Trusted-bypass flag stays False on Payments for non-API-entered rows.
- 2026-04-22 (Phase 6b) — Notification provider layer is a plain `get_provider(channel)` factory reading from `settings.NOTIFICATION_PROVIDERS`, not a plug-in registry. Channels are fixed (SMS/WhatsApp/Email per SPEC §18). A registry would be over-engineered for 3 channels; the factory swap is enough for dev/test ("console") vs prod ("africastalking") switching.
- 2026-04-22 (Phase 6b) — `NotificationDelivery` row is created *before* Celery dispatch. If the broker is down, the row is stamped QUEUED and `sweep_queued_notifications` (scheduled task) picks it up on the next tick. This preserves a business-level audit trail even when infra flaps — Celery result_backend alone would lose the context (recipient, rendered body) after pruning.
- 2026-04-22 (Phase 6b) — Retry policy uniform across all outbound-HTTP tasks: `autoretry_for=(httpx.HTTPError, ConnectionError, TimeoutError)`, `retry_backoff=True`, `retry_backoff_max=600`, `retry_jitter=True`, `max_retries=5`. Matches SPEC §18 verbatim. Non-HTTPError exceptions (e.g. provider library misuse) are NOT retried — they're a bug, not transient, and retrying would compound the error.
- 2026-04-22 (Phase 6b) — `billing.services.apply_payment` and `mark_overdue_invoices` call `notification_task.delay()` inside a try/except. A broker outage must never roll back a posted ledger entry. The alternative (transaction.on_commit hook) also works but would couple the ledger transaction to the broker — rejected for the same reason.
- 2026-04-22 (Phase 6b) — Payer matching order: exact phone → last-9-digit phone tail → id_number → prior `Payment.reference_number`. Gateway formats vary (`+256700...`, `0700...`, `256700...`) so the tail fallback catches the common variations without requiring the gateway to normalise. id_number and prior-reference are cheap tiebreakers for walk-in payers.
- 2026-04-22 (Phase 6b) — Unmatched payer returns 202 (not 400). The payment *is* valid from the provider's standpoint — it just has no tenant on our side yet. 202 + `status: unmatched` lets the provider stop retrying while the finance team manually resolves via the admin. A 4xx would be interpreted as "bad request, please fix and retry" which is the wrong semantic.
- 2026-04-22 — `full_name` kept as a denormalized read-optimised column auto-composed from first/last/other on save, instead of converting it to a `@property`. Preserves 49 read-sites across templates, queries, and ordering (`class Meta: ordering=["full_name"]`). Trade-off: one extra SQL column; the alternative of converting to a method would have required rewriting every `{{ obj.full_name }}` template and every `order_by("full_name")`. Not worth it for a cosmetic split.
- 2026-04-22 — Utilities on **managed** properties stay in `LANDLORD_PAYABLE` (no break-out to 4310/4320/etc.). Rationale: the utility fee is a pass-through to the landlord; Meili doesn't earn utility income, it just forwards it. The landlord statement still shows the break-out per-line because invoice-line descriptions preserve the utility identity. Utility income accounts are therefore Meili-owned-only in practice.
- 2026-04-22 — Commission left untouched by utilities. `_rent_portion_of` already filters on `Kind in {RENT, PRORATA}` + `target=LANDLORD`, so utility and ad-hoc revenue is automatically excluded from the commission base. No code changed in commission logic — just added test coverage to lock the behaviour.
- 2026-04-22 — NSSF accounts (2530, 5430) dropped rather than soft-disabled. Rationale: no journal entries reference them (payroll posting isn't wired yet), so a clean removal is safe. The migration's reverse op re-seeds them idempotently if ever needed. Tax compliance (PAYE) retained per user directive.
- 2026-04-22 — When a utility `AdHocCharge` is created with a `utility_kind` but the corresponding flag is False, the charge is still **billed** — it just posts as a regular `AD_HOC` line instead of a `UTILITY` line. This preserves the employee's intent (they created a charge, it shouldn't silently vanish) while honouring the setting (not separately billed → bundled with rent). Flag=True is what triggers the utility-income routing.
- 2026-04-17 — Python 3.14.3 in `meili` venv (exceeds 3.12 minimum; Django 6.0.4 compatible).
- 2026-04-17 — Added `SUPER_ADMIN` role in addition to the 7 roles in SPEC §2A.1 because §16.9 matrix requires a distinct Super Admin tier (draft deletion). `create_initial_superuser` assigns both `SUPER_ADMIN` and `ADMIN`.
- 2026-04-17 — `Landlord.landlord` on `House` is a nullable override (defaults to `estate.landlord` via `House.effective_landlord`). Avoids duplicating landlord on every house row while allowing inter-estate exceptions.
- 2026-04-17 — `Employee` auto-creation on `User` insert (SPEC §2A.5) deferred to Phase 1.5 / Phase 2 — not strictly required for Phase 1 model layer.
- 2026-04-17 — WhiteNoise used for static files (compressed manifest storage); gunicorn pinned for Linux prod, waitress kept for Windows prod testing.
- 2026-04-17 — django-crispy-forms/crispy-bootstrap5 intentionally omitted — SPEC §2.1 allows hand-rolled forms for design control. Will revisit in Phase 3 if form scaffolding becomes a bottleneck.
- 2026-04-17 — `tax_type` modelled as FK on `SettingsMixin` (single effective tax per scope). Multi-tax stacking deferred until accounting engine is implemented.
- 2026-04-18 — All ledger amounts stored in UGX (`UGXField`) on `JournalEntryLine`. USD source transactions must convert to UGX at posting time. `Account.currency` is a display hint only; ledger maths is UGX-only for integrity. Will revisit when a USD-billed use case lands.
- 2026-04-18 — Account hierarchy uses `parent` FK with invariant "parent.is_postable = False" enforced via `clean()`. Posting is blocked on non-postable accounts at `post()` time (not just UI). Keeps parent rollups safe from accidental direct posting.
- 2026-04-18 — Seeded parent rollups for Bank Accounts (1200) and Mobile Money (1300) so concrete `BankAccount` rows can post to leaf sub-accounts created per bank. Phase 2 does not create any leaf children — added when a real BankAccount is registered.
- 2026-04-18 — `JournalEntry.reverse()` keeps both original (marked REVERSED) and reversal (POSTED) counted by `Account.balance()`. Reversal is two POSTED states accounting-wise; REVERSED is a display/audit flag only. This avoids "balance goes negative after reversal" artefact.
- 2026-04-18 — Finance views authorised via `role_required('ADMIN','SUPER_ADMIN','FINANCE','ACCOUNT_MANAGER')` per SPEC §16.9. Collections/Sales Rep excluded from journal creation. Read-only views for them deferred to Phase 3.
- 2026-04-18 — Templates are functional-plain for Phase 2 (inline `<style>` in `_base.html`). Proper theming with CSS variables per SPEC §17.4 deferred to Phase 3.
- 2026-04-17 — `RoleRequiredMixin.dispatch` now defers to `LoginRequiredMixin` before checking role membership, so unauthenticated hits redirect to login rather than returning 403. Role check runs only once the user is authenticated.
- 2026-04-17 — Static storage downgraded to non-manifest (`StaticFilesStorage`) whenever `DEBUG=True` or under `manage.py test` to avoid "missing manifest" failures without requiring a `collectstatic` before every test run. Production still uses `CompressedManifestStaticFilesStorage`.
- 2026-04-17 — `get_effective_setting_with_source(house, field)` added next to `get_effective_setting` so the House detail page can show whether each effective setting is inherited from the estate or overridden at house level. Original single-return helper left intact for tests.
- 2026-04-17 — Tenant/landlord self-edit guard enforced both on `accounts:profile` (view-level guard with message + redirect) and on `core:tenant-update` (403 when the tenant's linked user is the request user and holds no staff role). UI-hiding is not sufficient per CLAUDE.md.
- 2026-04-17 — `TenantHouse` lifecycle transitions (`tenancy-activate` / `tenancy-exit`) only allowed from the permitted prior state. Activating a Prospect stamps today's `move_in_date` if blank and marks the house OCCUPIED. Exiting an Active tenancy stamps today's `move_out_date` if blank and re-vacates the house only when no other Active tenancy remains.
- 2026-04-21 — `accounting.JournalEntry.reference` made nullable (`unique=True, null=True, blank=True`). Previously empty-string references on DRAFT entries collided on the unique index. Postgres treats NULLs as distinct, so draft entries now carry `NULL` until `post()` stamps `JE-YYYYMM-NNNNNN`. Migration `accounting/0003_alter_historicaljournalentry_reference_and_more.py`.
- 2026-04-21 — Commission recognised only at **allocation** time, not at invoice issue. Ensures Meili earns commission only on cash collected (matches SPEC §20). Implemented via `recognize_commission_on_allocation(invoice, amount_applied)` called from inside `apply_payment` / `try_apply_advance_to_invoice`.
- 2026-04-21 — Surplus-with-no-active-tenancy raises `ValidationError` rather than silently parking funds. Forces the finance user to resolve routing explicitly (usually a new tenancy record or a refund).
- 2026-04-21 — Voids rewind `PaymentAllocation` rows back to held-advance (Dr AR, Cr Held Advance for the invoice's landlord routing) — not direct refunds. Separates void (accrual reversal) from refund (cash movement), which then requires its own maker-checker cycle.
- 2026-04-21 — `services.py` kept as a single cohesive module — commission/allocation/void/credit/refund split across files only if it grows past ~600 LOC. Premature abstraction is worse than one readable file.
- 2026-04-21 — `generate_invoices` beat schedule is **hourly** (not daily) so short cycles (HOUR/DAY) get picked up in time. `mark_overdue` runs once at 01:00 Africa/Kampala.
- 2026-04-21 — Bootstrap 5 adopted via CDN as the UI foundation over hand-rolling further CSS. Integrated as a *promotion layer* in `base.css` that upgrades native controls and legacy tables, so existing templates benefit without per-template rewrites. `django-widget-tweaks` added rather than crispy-forms: less magic, no extra template pack.
- 2026-04-21 — Portal is a **separate user surface** with its own `_base.html` (no employee sidebar, Bootstrap navbar only). Tenants/landlords see only portal nav items; staff navigate via the existing `app-shell` layout. This keeps role-UI boundaries obvious and prevents tenants from seeing employee menus even accidentally.
- 2026-04-21 — Statement **held-advance exclusion** enforced at the query layer, not the template layer. `build_statement_context` reads `Invoice.total` / `Invoice.amount_paid` only. The two held-advance liability accounts (`TENANT_ADVANCE_HELD_MANAGED`, `TENANT_ADVANCE_HELD_MEILI`) are never referenced in context building or PDF rendering. This makes accidental leakage impossible even if a future template change adds a "total advances" row.
- 2026-04-21 — Statement window capped at **6 months**, enforced in three places (view POST, service `enforce_window`, Celery task via `enforce_window` inside `build_statement_context`). Max-month constant exported from `portal.services.MAX_STATEMENT_MONTHS` so tests and UI hints stay in sync.
- 2026-04-21 — `generate_landlord_statement` + `deliver_landlord_statement` are **two chained tasks**, not one. Generation is idempotent (update_or_create on unique (landlord, period_start, period_end)); delivery is a separate retryable boundary and will be replaced wholesale in Phase 6 with real Email/WhatsApp adapters without touching generation.
- 2026-04-21 — Monthly landlord-statement beat job seeded via **migration** (`portal/0002_seed_beat_schedule.py`) rather than admin-managed. Keeps production parity automatic — no "forgot to enable the cron" incidents after a fresh deploy. The crontab is `django_celery_beat.CrontabSchedule` with `timezone="Africa/Kampala"`, so local DST-free 06:00 is honoured regardless of server TZ.
- 2026-04-21 — Mini-payroll implemented as **Employee model fields** (not a separate Payroll app). The COA for payroll postings is seeded now so future payroll runs have the accounts ready; payroll-run workflow + journal posting deferred to a later phase. Avoids premature abstraction while exposing the bank/tax/NSSF data the finance team needs immediately.
- 2026-04-21 — Landlord `preferred_statement_channel=NONE` opts the landlord out of auto-send but still allows manual-download through the portal. Safer default than disabling generation entirely — landlords can always self-serve.
- 2026-04-22 — Scoring tier thresholds (90 Platinum / 75 Gold / 60 Silver / 40 Bronze / 0 Watch) hard-coded in `scoring/tiers.py`, not a runtime setting. Adjusting tier boundaries is a deliberate analytical decision — code review enforces peer sign-off. New tenants (zero billed invoices) short-circuit to NEUTRAL_SCORE_NEW_TENANT=60 so the roster doesn't penalise fresh tenancies.
- 2026-04-22 — Multi-house scores blended by **total_billed weight**, with equal-weight fallback when nobody has been billed yet. Economically accurate — a tenant with 2M outstanding on one house and a perfect 50k track record on another is dominated by the 2M relationship. Locked in `MultiHouseWeightingTests`.
- 2026-04-22 — Exit settlement envelope (`ExitSettlement`) reuses `MakerCheckerMixin` with `allow_trusted_bypass=False`. Even trusted employees cannot self-execute an exit — the settlement itself needs a checker, AND the refund row it creates goes through its own maker-checker cycle. Two gates on one financial event by design.
- 2026-04-22 (Phase 8) — AuditLog lives **alongside** simple-history, not instead of it. simple-history answers "what changed on this row?"; AuditLog answers "who did what action from where, when?" with IP + user-agent + path. Different questions, different tables. AuditLog is append-only with `has_delete_permission=False` in admin; simple-history rows also never delete. Both are queryable by Admin/Super-Admin.
- 2026-04-22 (Phase 8) — Dashboard charts rendered in **inline SVG via vanilla JS**, not Chart.js / D3 / ApexCharts. Rationale: ~40 lines per chart, zero dependencies, zero CSP headaches, perfect print-friendliness, and matches CLAUDE.md's "Vanilla JS, no frontend framework" rule. The KPI API endpoint returns the same shape the initial server render embeds via `json_script`, so the Refresh button just re-renders on the fly.
- 2026-04-22 (Phase 8) — Outbound notifications API idempotency uses `NotificationDelivery.context__idempotency_key` JSON query rather than a dedicated `idempotency_key` column. JSONField index not needed at our volume (notifications are tail-aligned to payment-confirmation rates). If we outgrow the JSON lookup, add a functional index: `CREATE UNIQUE INDEX ON notifications_notificationdelivery ((context->>'idempotency_key'));` — migration deferred until it matters.
- 2026-04-22 (Phase 8) — Settings package selector keys off `DJANGO_ENV`, not `DJANGO_SETTINGS_MODULE`. The latter would require every dev command to either export the var or pass `--settings=` — breaking the long-standing `manage.py migrate` muscle memory. `DJANGO_ENV=prod` in the Docker compose file is a cleaner opt-in. Dev is the default.
- 2026-04-22 (Phase 8) — Production runs **one** celery_beat replica by explicit design (duplicate beats double-fire every scheduled job). Scaling out workers is fine (`docker compose up -d --scale celery_worker=N`) but beat stays at 1. Documented in README and in a comment on the beat service definition.
- 2026-04-22 — Cross-ownership held-advance transfer (MANAGED ↔ MEILI) posts a **two-legged journal** touching BOTH system accounts (Dr source, Cr target). Same-ownership transfers skip the journal and just rewire the allocation rows. Keeps the fiduciary vs deferred-revenue separation airtight even when a tenant moves funds between tenancies with different landlords.
- 2026-04-22 — Exit workflow exit-damages billed as a **one-shot exit Invoice** with AD_HOC lines rather than ad-hoc allocations, so AR and landlord-payable stay consistent. Held-advance is consumed first via `try_apply_advance_to_invoice`; any remaining balance is absorbed by the security deposit via `_apply_deposit_to_invoice`; only THEN does the refund row get created for the residual.
- 2026-04-22 — Advance Payments Report "≥ 2 full billing periods" badge implemented as a 60-day age threshold on `allocated_at`, rather than a per-tenancy rent-cycle lookup. Simpler, doesn't require a rent schedule query per row; the 60-day rule matches the common Monthly billing cycle and is clearly documented as "two periods ≈ 60 days" in the view docstring.

## Phase 3 Deliverables
- `dashboard` app — home page, global search (4-table grouped: tenants/houses/estates/users), coming-soon placeholder, custom 403/404/500 error pages wired via `handler403`/`handler404`/`handler500` in the project `urls.py`
- Context processors: `accounts.context_processors.user_roles` and `dashboard.context_processors.notifications` registered in `TEMPLATES.OPTIONS.context_processors` so `user_role_names`, `unread_notifications_count`, and `notifications` are available to every template
- Design system (`static/css/_variables.css`) — full token set per SPEC §17.4 (primary/secondary/semantic/neutral palette, spacing 4px grid, radius, typography, shadows, layout vars). No hardcoded colours anywhere in templates or `base.css`
- `static/css/base.css` — grid-based `.app-shell` (sidebar | header/main/footer), collapsible sidebar with collapsed-mode tooltips, submenu accordion, header global-search bar, icon buttons with `badge-dot`, profile avatar with initials fallback, dropdown menus, cards, forms, data tables, badges, pagination, messages alerts, auth shell, error shell, coming-soon
- `static/js/layout.js` — vanilla JS: sidebar collapse persisted in `localStorage` key `meili.sidebar.collapsed`; submenu accordion with open sections persisted as JSON in `meili.sidebar.open`; header dropdowns via `[data-dropdown]` / `[data-dropdown-trigger]` convention; click-outside closes dropdowns
- `templates/base.html` — Inter font, Select2 CDN, jQuery, and a blanket Select2 initialiser applied to `select.select2` and any `<select>` inside a `.select2-auto` container
- `templates/layouts/sidebar.html` — multi-level sidebar: Dashboard · Property (Estates/Houses) · People (Tenants/Landlords/Employees/Suppliers) · Billing (Invoices/Payments/Receipts/Invoice Schedules — Coming Soon) · Accounting (COA/Journal/GL/Trial Balance Coming Soon/Bank Accounts) · Reports (Commission Income + 4 Coming Soon) · Admin (superuser-gated)
- `templates/layouts/header.html` — global search form (posts to `dashboard:search`), notification bell with unread badge, profile avatar with initials fallback and My Profile / Admin Settings / Log Out menu
- `templates/layouts/footer.html` — Okumpi Technologies credit · v1.1.0 · dynamic year
- `accounts` auth — `login/logout/password-reset/password-reset-confirm/password-change/profile` views with forms, CSRF, Argon2 hashing, login/failure attempts logged to `LoginAttempt`, Axes-integrated via settings. Auth-shell templates (`_auth_base.html`, `login.html`, `password_reset_request.html`, `password_reset_confirm.html`). In-app templates (`password_change.html`, `profile.html`)
- `core` CRUD — Landlord, Estate, House, Tenant, TenantHouse, Employee, Supplier: list/detail/create/update/soft-delete via class-based views gated by `RoleRequiredMixin`. House detail page shows full "effective settings" table with per-row `house` / `estate` / `none` source badges via `get_effective_setting_with_source`
- `core:tenancy-create` / `tenancy-activate` / `tenancy-exit` — tenancy lifecycle actions (Prospect → Active → Exited) with automatic house occupancy updates
- `accounting` CRUD — BankAccount list/detail/create/update/delete added next to existing COA/Journal/Ledger/Report views
- Server-side profile edit guards per CLAUDE.md: tenants/landlords cannot edit their own profile (accounts:profile blocks POST with a redirect + message; core:tenant-update 403s when the viewer *is* the linked user and holds no staff role); employee management (`core:employee-*`) restricted to ADMIN / SUPER_ADMIN
- Pagination: all list views use `PaginatedListView` (default 50, options 20/50/100/150, session-persistent via `?page_size=`)
- 18 new tests in `dashboard/tests.py` covering permission boundaries (unauth redirect, authenticated-without-role 403, tenant blocked, collections allowed, admin-only employee list), tenancy lifecycle (activate/exit with house occupancy transitions, derived status transitions), profile guards (tenant blocked, employee allowed, self-edit on Tenant record blocked), coming-soon + search rendering. **60 tests total passing** (accounts 9, core 15, accounting 18, dashboard 18)

## Outstanding Phase 1 Verification (user-driven)
- Views, templates, login/logout/password-reset UI — deferred to Phase 3 (Portals & UI).
- Employee auto-provisioning of `User` with temp password + email — deferred to Phase 3.
- Admin settings page (SPEC §2A.6) — deferred to Phase 3.
- `django-ratelimit` installed but not yet applied to login / password-reset / API endpoints.
- CSS variable theme + base.html — deferred to Phase 3.
- `cleanup_old_task_results` beat schedule — deferred to Phase 8 monitoring.
- Celery ping smoke test — requires user to start containers/processes (user-driven, above).
- Trial Balance + Balance Sheet reports (SPEC §14.2) — minimal Commission Report delivered; Trial Balance / P&L / Balance Sheet deferred to a later reporting pass (Phase 7 delivered the operational reports suite but not the formal financial statements).
- Bank/mobile-money leaf accounts created on demand when a concrete `BankAccount` is registered — no seed data beyond parents.
- Inline AR / Landlord Payable auto-postings from invoice/payment flows — deferred to Phase 4 (Billing) / Phase 5 (Payments).
- Multi-currency ledger (currently UGX-only maths; USD display only) — deferred.
- Journal Entry void/hard-delete workflow for drafts — deferred (rarely needed, trivial admin action).

## Known Issues
- Notifications bell renders a stub count of 0 — backend model + Celery fan-out shipped in 6b; wiring the bell count to `NotificationDelivery.objects.filter(user=request.user, read_at__isnull=True)` is a 5-min follow-up, deferred.
- Password-reset emails are logged to the Django `messages` framework (dev token inlined) rather than sent via Celery/email. In prod the `password_reset` Celery task now exists via notifications.tasks but the view still uses the dev inline path — TODO: swap for `enqueue_notification(template="PASSWORD_RESET", ...)`.
- Employee creation form still requires a pre-existing `User`; self-provisioning workflow (SPEC §2A.5) deferred.
- AuditLog currently captures auth events automatically (login/logout/login_failed). To capture CRUD/approval/void/refund actions, a follow-up pass should call `AuditLog.record(action=..., actor=request.user, target=obj, request=request)` inside the mutating views. Non-blocking for ship — simple-history already covers row-level change tracking.
- Flower is reverse-proxied at `/flower/` via nginx with basic-auth; for internet-facing deployments, add an IP allowlist in nginx or put the whole subdomain behind the org VPN — the current config trusts basic-auth alone.

## Running Processes (user-managed — NOT the agent)
- `docker compose -f docker-compose.dev.yml up -d rabbitmq` (broker)
- `python manage.py runserver`
- `celery -A meili_property worker --pool=solo --loglevel=info`
- `celery -A meili_property beat --scheduler django_celery_beat.schedulers:DatabaseScheduler --loglevel=info`
- `celery -A meili_property flower --port=5555`
