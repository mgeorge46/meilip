# Project State

## Current Phase
Phase 3 — Employee Dashboard: UI Layout, Design System, Entity CRUD (complete)

## Last Completed
Phase 3 — Employee Dashboard (2026-04-17)

## Next Up
Phase 4 — Billing (invoices, schedules, payment allocation, maker-checker)

## Completed Phases
- [x] Phase 1 — Project Setup, Custom Auth & Core Models
- [x] Phase 2 — Chart of Accounts & Accounting
- [x] Phase 3 — Employee Dashboard: UI Layout, Design System, Entity CRUD

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
- 2026-04-17 — `RoleRequiredMixin.dispatch` now defers to `LoginRequiredMixin` before checking role membership, so unauthenticated hits redirect to login rather than returning 403. Role check runs only once the user is authenticated.
- 2026-04-17 — Static storage downgraded to non-manifest (`StaticFilesStorage`) whenever `DEBUG=True` or under `manage.py test` to avoid "missing manifest" failures without requiring a `collectstatic` before every test run. Production still uses `CompressedManifestStaticFilesStorage`.
- 2026-04-17 — `get_effective_setting_with_source(house, field)` added next to `get_effective_setting` so the House detail page can show whether each effective setting is inherited from the estate or overridden at house level. Original single-return helper left intact for tests.
- 2026-04-17 — Tenant/landlord self-edit guard enforced both on `accounts:profile` (view-level guard with message + redirect) and on `core:tenant-update` (403 when the tenant's linked user is the request user and holds no staff role). UI-hiding is not sufficient per CLAUDE.md.
- 2026-04-17 — `TenantHouse` lifecycle transitions (`tenancy-activate` / `tenancy-exit`) only allowed from the permitted prior state. Activating a Prospect stamps today's `move_in_date` if blank and marks the house OCCUPIED. Exiting an Active tenancy stamps today's `move_out_date` if blank and re-vacates the house only when no other Active tenancy remains.

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
