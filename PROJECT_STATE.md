# Project State

## Current Phase
Phase 3 — Portals & UI (not started)

## Last Completed
Phase 2 — Chart of Accounts & Accounting (2026-04-18)

## Next Up
Phase 3 — Portals & UI (tenant / landlord / employee portals, base.html, login flow)

## Completed Phases
- [x] Phase 1 — Project Setup, Custom Auth & Core Models
- [x] Phase 2 — Chart of Accounts & Accounting

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

## Tech Debt / Deferred
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
- None.

## Running Processes (user-managed — NOT the agent)
- `docker compose -f docker-compose.dev.yml up -d rabbitmq` (broker)
- `python manage.py runserver`
- `celery -A meili_property worker --pool=solo --loglevel=info`
- `celery -A meili_property beat --scheduler django_celery_beat.schedulers:DatabaseScheduler --loglevel=info`
- `celery -A meili_property flower --port=5555`
