# Project State

## Current Phase
Phase 5 — Tenant & Landlord Portals + Mini Payroll + UI Overhaul (complete)

## Last Completed
Phase 5 — Portals, payroll fields, Bootstrap 5 UI overhaul (2026-04-21)

## Next Up
Phase 6 — Payments ingress (webhook intake, MTN/Airtel/Bank reconciliation) + Notification adapter (email + WhatsApp) that replaces the stub delivery in `portal.tasks.deliver_landlord_statement`

## Completed Phases
- [x] Phase 1 — Project Setup, Custom Auth & Core Models
- [x] Phase 2 — Chart of Accounts & Accounting
- [x] Phase 3 — Employee Dashboard: UI Layout, Design System, Entity CRUD
- [x] Phase 4 — Billing Engine
- [x] Phase 5 — Tenant & Landlord Portals + Mini Payroll + UI Overhaul

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

## Decisions Log
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
- Trial Balance + Balance Sheet reports (SPEC §14.2) — minimal Commission Report delivered; Trial Balance / P&L / Balance Sheet deferred to Phase 7 reporting pass.
- Bank/mobile-money leaf accounts created on demand when a concrete `BankAccount` is registered — no seed data beyond parents.
- Inline AR / Landlord Payable auto-postings from invoice/payment flows — deferred to Phase 4 (Billing) / Phase 5 (Payments).
- Multi-currency ledger (currently UGX-only maths; USD display only) — deferred.
- Journal Entry void/hard-delete workflow for drafts — deferred (rarely needed, trivial admin action).

## Known Issues
- Notifications bell renders a stub count of 0 — backend model + Celery fan-out deferred to Phase 6.
- Password-reset emails are logged to the Django `messages` framework (dev token inlined) rather than sent via Celery/email. Email dispatch wiring deferred.
- Employee creation form still requires a pre-existing `User`; self-provisioning workflow (SPEC §2A.5) deferred.

## Running Processes (user-managed — NOT the agent)
- `docker compose -f docker-compose.dev.yml up -d rabbitmq` (broker)
- `python manage.py runserver`
- `celery -A meili_property worker --pool=solo --loglevel=info`
- `celery -A meili_property beat --scheduler django_celery_beat.schedulers:DatabaseScheduler --loglevel=info`
- `celery -A meili_property flower --port=5555`
