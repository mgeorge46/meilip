"""Seed demo data for UI walkthroughs.

Creates (idempotently):
  - 2 landlords, 2 estates (1 per landlord), 4 houses (2 per estate)
  - 2 tenants, 2 tenancies
  - a handful of issued invoices, approved payments (no GL posting)
  - a few notification deliveries with varied channels / statuses

All demo rows are prefixed `Demo` so --reset can clean them up without
touching real data.

Usage:
    py manage.py seed_demo            # create (idempotent)
    py manage.py seed_demo --reset    # delete demo rows, then create
"""

from datetime import timedelta
from decimal import Decimal
import random

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

User = get_user_model()

from accounting.models import Account, BankAccount
from billing.models import (
    ApprovalStatus, ExpenseClaim, Invoice, LandlordPayout, Payment,
    PaymentAllocation, Receipt, SupplierPayment,
)
from billing.sequences import allocate_number
from core.models import (
    BillingCycle, CollectionsBonusBracket, CollectionsTarget, Currency,
    Employee, Estate, House, Landlord, Supplier, Tenant, TenantHouse,
)
from notifications.models import (
    Channel, DeliveryStatus, NotificationDelivery, Template,
)


DEMO_TAG = "Demo"


class Command(BaseCommand):
    help = "Seed demo landlords, estates, houses, tenants, invoices, payments, messages."

    def add_arguments(self, parser):
        parser.add_argument("--reset", action="store_true", help="Delete demo rows first.")

    def handle(self, *args, **opts):
        if opts["reset"]:
            self._reset()

        with transaction.atomic():
            ctx = self._ensure_base()
            landlords = self._create_landlords(ctx)
            estates = self._create_estates(landlords)
            houses = self._create_houses(estates)
            tenants = self._create_tenants()
            tenancies = self._create_tenancies(tenants, houses)
            invoices = self._create_invoices(tenancies)
            payments = self._create_payments(tenants, invoices, ctx["bank"])
            self._create_notifications(tenants, landlords)
            suppliers = self._create_suppliers()
            landlord_payouts = self._create_landlord_payouts(landlords, ctx["bank"])
            supplier_payments = self._create_supplier_payments(suppliers, houses, ctx["bank"])
            self._create_pending_approvals(landlords, suppliers, houses, ctx["bank"])
            self._assign_collections_persons(houses)
            self._create_bonus_brackets()
            self._create_collections_targets()

        pending_n = (
            Payment.objects.filter(approval_status=ApprovalStatus.PENDING).count()
            + LandlordPayout.objects.filter(approval_status=ApprovalStatus.PENDING).count()
            + SupplierPayment.objects.filter(approval_status=ApprovalStatus.PENDING).count()
            + ExpenseClaim.objects.filter(approval_status=ApprovalStatus.PENDING).count()
        )
        self.stdout.write(self.style.SUCCESS(
            f"Seeded: {len(landlords)} landlords · {len(estates)} estates · {len(houses)} houses · "
            f"{len(tenants)} tenants · {len(invoices)} invoices · {len(payments)} payments · "
            f"{len(suppliers)} suppliers · {len(landlord_payouts)} payouts · "
            f"{len(supplier_payments)} supplier payments · {pending_n} pending approvals"
        ))

    # ------------------------------------------------------------------ reset
    def _reset(self):
        self.stdout.write("Deleting existing demo rows...")
        # order matters — child rows before parents
        ExpenseClaim.objects.filter(notes__startswith=DEMO_TAG).delete()
        SupplierPayment.objects.filter(reference_number__startswith=DEMO_TAG).delete()
        LandlordPayout.objects.filter(reference_number__startswith=DEMO_TAG).delete()
        Receipt.objects.filter(payment__reference_number__startswith=DEMO_TAG).delete()
        PaymentAllocation.objects.filter(payment__reference_number__startswith=DEMO_TAG).delete()
        Payment.objects.filter(reference_number__startswith=DEMO_TAG).delete()
        Invoice.objects.filter(notes__startswith=DEMO_TAG).delete()
        NotificationDelivery.objects.filter(body__startswith=DEMO_TAG).delete()
        Supplier.objects.filter(name__startswith=DEMO_TAG).delete()
        TenantHouse.objects.filter(tenant__full_name__startswith=DEMO_TAG).delete()
        Tenant.objects.filter(full_name__startswith=DEMO_TAG).delete()
        House.objects.filter(estate__name__startswith=DEMO_TAG).delete()
        Estate.objects.filter(name__startswith=DEMO_TAG).delete()
        Landlord.objects.filter(full_name__startswith=DEMO_TAG).delete()

    # ------------------------------------------------------------------ base
    def _ensure_base(self):
        """Pull the support rows the demo data depends on."""
        ugx = Currency.objects.filter(code="UGX").first()
        if not ugx:
            ugx = Currency.objects.create(code="UGX", name="Uganda Shilling", symbol="UGX", is_active=True)

        cycle = BillingCycle.objects.filter(unit="MONTH", count=1).first()
        if not cycle:
            cycle = BillingCycle.objects.create(name="Monthly", unit="MONTH", count=1, is_active=True)

        # A bank account is required by Payment. We need the GL account it
        # posts to to be POSTABLE — picking a rollup like 1200 breaks JE
        # posting. Prefer a postable child of 1200; create one if missing.
        bank = BankAccount.objects.filter(kind="BANK").first()
        if not bank:
            ledger = self._ensure_postable_bank_leaf()
            bank = BankAccount.objects.create(
                name=f"{DEMO_TAG} Stanbic Operating",
                kind="BANK",
                bank_name="Stanbic Bank",
                account_number="9030099990",
                currency=ugx,
                ledger_account=ledger,
                is_active=True,
            )
        elif not bank.ledger_account.is_postable:
            # Existing demo bank pointed at a rollup — repoint to a leaf.
            bank.ledger_account = self._ensure_postable_bank_leaf()
            bank.save(update_fields=["ledger_account"])
        return {"ugx": ugx, "cycle": cycle, "bank": bank}

    def _ensure_postable_bank_leaf(self):
        """Return a postable Asset leaf account suitable for a BankAccount."""
        # First-choice: any account with the BANK_OPERATING_DEFAULT system code.
        leaf = Account.objects.filter(
            system_code="BANK_OPERATING_DEFAULT", is_postable=True
        ).first()
        if leaf:
            return leaf
        # Else pick the first postable child of the 1200 parent.
        parent = Account.objects.filter(code="1200").first()
        if parent:
            leaf = parent.children.filter(is_postable=True, is_active=True).first()
            if leaf:
                return leaf
            # Create one under the 1200 parent.
            return Account.objects.create(
                code="1210",
                name="Operating Bank Account",
                account_type=parent.account_type,
                parent=parent,
                is_postable=True,
                is_active=True,
                system_code="BANK_OPERATING_DEFAULT",
            )
        # Fallback: any postable asset.
        leaf = Account.objects.filter(
            is_postable=True, is_active=True, account_type__category="ASSET"
        ).first()
        if not leaf:
            raise RuntimeError(
                "No postable asset account found. Run `py manage.py seed_coa` first."
            )
        return leaf

    # ------------------------------------------------------------ landlords
    def _create_landlords(self, ctx):
        specs = [
            dict(first_name="Demo", last_name="Mukasa", other_names="Joseph",
                 phone="+256700110001", email="jmukasa.demo@example.com",
                 id_number="CM901234001", is_meili_owned=False,
                 bank_name="Stanbic", bank_account_number="9030099111", bank_branch="Kampala Main",
                 notes=f"{DEMO_TAG} — managed landlord."),
            dict(first_name="Demo", last_name="Namata", other_names="Sarah",
                 phone="+256700110002", email="snamata.demo@example.com",
                 id_number="CM901234002", is_meili_owned=True,
                 bank_name="Centenary", bank_account_number="2200101222", bank_branch="Ntinda",
                 notes=f"{DEMO_TAG} — Meili-owned portfolio."),
        ]
        out = []
        for s in specs:
            obj, _ = Landlord.objects.update_or_create(phone=s["phone"], defaults=s)
            out.append(obj)
        return out

    # -------------------------------------------------------------- estates
    def _create_estates(self, landlords):
        specs = [
            (landlords[0], dict(name=f"{DEMO_TAG} Buziga Estate", location="Buziga, Kampala",
                                description=f"{DEMO_TAG} — 2-unit managed estate.")),
            (landlords[1], dict(name=f"{DEMO_TAG} Kyaliwajjala Heights", location="Kyaliwajjala, Wakiso",
                                description=f"{DEMO_TAG} — 2-unit Meili-owned estate.")),
        ]
        out = []
        for landlord, s in specs:
            obj, _ = Estate.objects.update_or_create(
                landlord=landlord, name=s["name"], defaults=s,
            )
            out.append(obj)
        return out

    # --------------------------------------------------------------- houses
    def _create_houses(self, estates):
        out = []
        for est in estates:
            for i, (num, label, rent) in enumerate([
                ("A1", "2-bed apartment", 900_000),
                ("A2", "3-bed apartment", 1_350_000),
            ]):
                obj, _ = House.objects.update_or_create(
                    estate=est, house_number=num,
                    defaults=dict(
                        name=label,
                        periodic_rent=Decimal(rent),
                        occupancy_status=House.Occupancy.VACANT,
                    ),
                )
                out.append(obj)
        return out

    # -------------------------------------------------------------- tenants
    def _create_tenants(self):
        specs = [
            dict(first_name="Demo", last_name="Achieng", other_names="Grace",
                 phone="+256700220001", email="gachieng.demo@example.com",
                 id_number="CF901234101",
                 next_of_kin_name="Peter Achieng", next_of_kin_phone="+256700990101",
                 preferred_notification=Tenant.PreferredNotification.WHATSAPP,
                 preferred_receipt=Tenant.PreferredReceipt.EMAIL),
            dict(first_name="Demo", last_name="Okello", other_names="Brian",
                 phone="+256700220002", email="bokello.demo@example.com",
                 id_number="CM901234102",
                 next_of_kin_name="Jane Okello", next_of_kin_phone="+256700990102",
                 preferred_notification=Tenant.PreferredNotification.SMS,
                 preferred_receipt=Tenant.PreferredReceipt.WHATSAPP),
        ]
        out = []
        for s in specs:
            obj, _ = Tenant.objects.update_or_create(phone=s["phone"], defaults=s)
            out.append(obj)
        return out

    # ----------------------------------------------------------- tenancies
    def _create_tenancies(self, tenants, houses):
        today = timezone.localdate()
        pairs = [
            (tenants[0], houses[0], today - timedelta(days=120)),
            (tenants[1], houses[2], today - timedelta(days=60)),
        ]
        out = []
        for tenant, house, move_in in pairs:
            obj, created = TenantHouse.objects.get_or_create(
                tenant=tenant, house=house,
                defaults=dict(
                    status=TenantHouse.Status.ACTIVE,
                    move_in_date=move_in,
                ),
            )
            if created:
                house.occupancy_status = House.Occupancy.OCCUPIED
                house.save(update_fields=["occupancy_status"])
            out.append(obj)
        return out

    # ----------------------------------------------------------- invoices
    def _create_invoices(self, tenancies):
        """12 monthly ISSUED invoices per tenancy — fills the 12-month
        trend chart on the dashboard."""
        today = timezone.localdate()
        out = []
        for th in tenancies:
            rent = th.house.periodic_rent or Decimal("750000")
            for m_offset in range(12, 0, -1):
                period_from = (today.replace(day=1) - timedelta(days=31 * m_offset)).replace(day=1)
                period_to = (period_from + timedelta(days=31)).replace(day=1) - timedelta(days=1)
                inv, created = Invoice.objects.get_or_create(
                    tenant_house=th, period_from=period_from, period_to=period_to,
                    defaults=dict(
                        issue_date=period_from,
                        due_date=period_from + timedelta(days=7),
                        rent_amount=rent, subtotal=rent, tax_total=Decimal("0"), total=rent,
                        status=Invoice.Status.ISSUED,
                        notes=f"{DEMO_TAG} — seeded invoice.",
                        issued_at=timezone.now() - timedelta(days=30 * m_offset),
                    ),
                )
                if created and not inv.number:
                    inv.number = allocate_number("INV")
                    inv.save(update_fields=["number"])
                out.append(inv)
        return out

    # ----------------------------------------------------------- payments
    def _create_payments(self, tenants, invoices, bank):
        """Approved payments for most seeded invoices (leaves last 2 months
        unpaid on tenant #1 to show outstanding AR in the dashboard).
        Also creates Receipts for each approved payment.
        """
        from billing.models import Receipt
        out = []
        now = timezone.now()
        methods = [Payment.Method.MOBILE_MONEY, Payment.Method.BANK, Payment.Method.CASH]
        for idx, tenant in enumerate(tenants):
            tenant_invs = sorted(
                (i for i in invoices if i.tenant_house.tenant_id == tenant.pk),
                key=lambda i: i.issue_date,
            )
            # Pay the first N-2 invoices (leave last 2 unpaid on tenant 0 only)
            cutoff = len(tenant_invs) - (2 if idx == 0 else 0)
            for k, inv in enumerate(tenant_invs[:cutoff]):
                ref = f"{DEMO_TAG}-{tenant.id_number or tenant.pk}-{k+1:02d}"
                if Payment.objects.filter(reference_number=ref).exists():
                    continue
                # Receive ~a week after issue
                received = timezone.make_aware(
                    timezone.datetime.combine(inv.issue_date, timezone.datetime.min.time())
                ) + timedelta(days=7)
                pay = Payment.objects.create(
                    number=allocate_number("RCP"),
                    tenant=tenant,
                    amount=inv.total,
                    method=methods[(idx + k) % len(methods)],
                    bank_account=bank,
                    reference_number=ref,
                    received_at=received,
                    approval_status=ApprovalStatus.AUTO_APPROVED,
                    approved_at=received,
                )
                PaymentAllocation.objects.create(
                    payment=pay, invoice=inv, amount=inv.total,
                    allocated_at=received, applied_at=received,
                )
                inv.status = Invoice.Status.PAID
                inv.save(update_fields=["status"])
                # Receipt for this payment
                Receipt.objects.get_or_create(
                    payment=pay,
                    defaults=dict(
                        number=allocate_number("RCP"),
                        kind=Receipt.Kind.PAYMENT,
                        amount=pay.amount,
                        issued_at=received,
                    ),
                )
                out.append(pay)

        # A couple of PENDING payments so the Approvals queue isn't empty
        pending_specs = [
            (tenants[0], Decimal("450000"), Payment.Method.MOBILE_MONEY, "Part-payment — mid-month"),
            (tenants[1], Decimal("1350000"), Payment.Method.BANK, "April full rent — awaiting bank confirmation"),
        ]
        # Use a NON-superuser maker so the logged-in admin can approve.
        non_super = User.objects.filter(is_superuser=False, is_active=True).first()
        if not non_super:
            non_super, _ = User.objects.get_or_create(
                email="demo-maker@example.com",
                defaults={"is_active": True, "is_staff": False, "is_superuser": False},
            )
        for tenant, amount, method, note in pending_specs:
            ref = f"{DEMO_TAG}-PEND-{tenant.id_number}"
            if Payment.objects.filter(reference_number=ref).exists():
                continue
            Payment.objects.create(
                number=allocate_number("RCP"),
                tenant=tenant,
                amount=amount,
                method=method,
                bank_account=bank,
                reference_number=ref,
                received_at=now - timedelta(hours=6),
                approval_status=ApprovalStatus.PENDING,
                maker=non_super,
                submitted_at=now - timedelta(hours=6),
            )

        return out

    # ------------------------------------------------------- notifications
    def _create_notifications(self, tenants, landlords):
        samples = [
            (tenants[0], Channel.WHATSAPP, Template.PAYMENT_CONFIRMATION, DeliveryStatus.SENT,
             "Payment received", f"{DEMO_TAG} — Thank you Grace, your rent payment of UGX 900,000 has been received."),
            (tenants[0], Channel.EMAIL, Template.RECEIPT, DeliveryStatus.SENT,
             "Your receipt RCP-000001", f"{DEMO_TAG} — Attached: receipt RCP-000001 for January rent."),
            (tenants[1], Channel.SMS, Template.OVERDUE_REMINDER, DeliveryStatus.FAILED,
             "", f"{DEMO_TAG} — Reminder: rent is overdue. Please settle by Friday."),
            (tenants[1], Channel.WHATSAPP, Template.PAYMENT_CONFIRMATION, DeliveryStatus.QUEUED,
             "Payment received", f"{DEMO_TAG} — Payment of UGX 1,350,000 received, applying to Feb invoice."),
        ]
        for tenant, ch, tpl, st, subject, body in samples:
            recipient = tenant.phone if ch != Channel.EMAIL else tenant.email
            NotificationDelivery.objects.get_or_create(
                tenant=tenant, template=tpl, body=body,
                defaults=dict(
                    channel=ch, subject=subject, recipient=recipient,
                    status=st,
                    provider="Twilio" if ch != Channel.EMAIL else "SendGrid",
                    provider_message_id=f"MSG-{random.randint(100000, 999999)}" if st == DeliveryStatus.SENT else "",
                    sent_at=timezone.now() if st == DeliveryStatus.SENT else None,
                    error_detail="Provider returned 400 — invalid number" if st == DeliveryStatus.FAILED else "",
                ),
            )
        # Landlord statements
        for landlord in landlords:
            NotificationDelivery.objects.get_or_create(
                landlord=landlord, template=Template.STATEMENT,
                body=f"{DEMO_TAG} — Your monthly statement for March is attached.",
                defaults=dict(
                    channel=Channel.EMAIL, subject="March statement",
                    recipient=landlord.email or landlord.phone,
                    status=DeliveryStatus.SENT, provider="SendGrid",
                    provider_message_id=f"MSG-{random.randint(100000, 999999)}",
                    sent_at=timezone.now() - timedelta(days=3),
                ),
            )

    # -------------------------------------------------- pending approvals
    def _create_pending_approvals(self, landlords, suppliers, houses, bank):
        """Seed 1 PENDING landlord payout, 1 PENDING supplier payment,
        2 PENDING expense claims so the Approvals queue is never empty.

        Maker is set to a NON-superuser if one exists — otherwise the
        logged-in admin would hit SelfApprovalBlocked when trying to
        approve their own seeded items.
        """
        now = timezone.now()
        # Prefer a non-superuser as maker; fall back to creating a demo maker.
        maker = User.objects.filter(is_superuser=False, is_active=True).first()
        if not maker:
            maker, _ = User.objects.get_or_create(
                email="demo-maker@example.com",
                defaults={"is_active": True, "is_staff": False, "is_superuser": False},
            )
            if not maker.has_usable_password():
                maker.set_unusable_password()
                maker.save(update_fields=["password"])
        superuser = maker  # used in the existing payment-pending block below

        # Pending landlord payout
        ref_lpo = f"{DEMO_TAG}-LPO-PEND"
        if not LandlordPayout.objects.filter(reference_number=ref_lpo).exists():
            LandlordPayout.objects.create(
                landlord=landlords[0],
                amount=Decimal("825000"),
                method=LandlordPayout.Method.BANK,
                bank_account=bank,
                reference_number=ref_lpo,
                paid_at=now,
                notes=f"{DEMO_TAG} — April statement payout (pending approval)",
                approval_status=ApprovalStatus.PENDING,
                maker=superuser,
                submitted_at=now,
            )

        # Pending supplier payment
        ref_sp = f"{DEMO_TAG}-SPY-PEND"
        if not SupplierPayment.objects.filter(reference_number=ref_sp).exists():
            SupplierPayment.objects.create(
                supplier=suppliers[0],
                amount=Decimal("275000"),
                method=SupplierPayment.Method.BANK,
                bank_account=bank,
                service_description="Emergency roof leak fix — Buziga A2",
                invoice_reference="BF-2026-0418",
                reference_number=ref_sp,
                related_house=houses[1],
                paid_at=now,
                notes=f"{DEMO_TAG} — supplier payment pending approval",
                approval_status=ApprovalStatus.PENDING,
                maker=superuser,
                submitted_at=now,
            )

        # 2 pending expense claims from any available employees
        employees = list(Employee.objects.filter(is_active=True)[:2])
        expense_specs = [
            ("TRANSPORT", "Taxi to Buziga site for emergency plumbing", Decimal("45000")),
            ("COMMS", "Airtime top-up for tenant follow-ups (April)", Decimal("30000")),
        ]
        for emp, (cat, desc, amount) in zip(employees, expense_specs):
            if ExpenseClaim.objects.filter(description=desc).exists():
                continue
            ExpenseClaim.objects.create(
                claimant=emp,
                category=cat,
                description=desc,
                amount=amount,
                incurred_at=timezone.localdate() - timedelta(days=3),
                reimbursement_bank=None,  # finance picks at approval
                notes=f"{DEMO_TAG} — employee expense awaiting approval",
                approval_status=ApprovalStatus.PENDING,
                maker=emp.user or superuser,
                submitted_at=now,
            )

    # ----------------------------------------------------------- suppliers
    def _create_suppliers(self):
        specs = [
            dict(name=f"{DEMO_TAG} BrightFix Plumbing", kind=Supplier.Kind.SERVICES,
                 contact_person="Isaac Kato", phone="+256700330001",
                 email="contact.brightfix@example.com", tax_id="1000123401",
                 bank_name="Stanbic", bank_account_number="9030088111"),
            dict(name=f"{DEMO_TAG} GreenGarden Landscaping", kind=Supplier.Kind.SERVICES,
                 contact_person="Mary Nansubuga", phone="+256700330002",
                 email="hello.greengarden@example.com", tax_id="1000123402",
                 bank_name="Centenary", bank_account_number="2200202333"),
            dict(name=f"{DEMO_TAG} SafeHome Hardware", kind=Supplier.Kind.GOODS,
                 contact_person="Paul Ssemakula", phone="+256700330003",
                 email="orders.safehome@example.com", tax_id="1000123403",
                 bank_name="DFCU", bank_account_number="0105500444"),
        ]
        out = []
        for s in specs:
            obj, _ = Supplier.objects.update_or_create(name=s["name"], defaults=s)
            out.append(obj)
        return out

    # --------------------------------------------------- landlord payouts
    def _create_landlord_payouts(self, landlords, bank):
        out = []
        now = timezone.now()
        specs = [
            # (landlord_idx, amount, days_ago, method, period_offset_months, note)
            (0, Decimal("850000"),  5, LandlordPayout.Method.BANK,         1, "March statement payout"),
            (0, Decimal("820000"), 35, LandlordPayout.Method.BANK,         2, "February statement payout"),
            (1, Decimal("1280000"), 7, LandlordPayout.Method.MOBILE_MONEY, 1, "March — Meili-owned net"),
            (1, Decimal("1260000"),38, LandlordPayout.Method.BANK,         2, "February — Meili-owned net"),
        ]
        today = timezone.localdate()
        for idx, amount, days_ago, method, months_back, note in specs:
            landlord = landlords[idx]
            ref = f"{DEMO_TAG}-LPO-{landlord.id_number}-{months_back:02d}"
            if LandlordPayout.objects.filter(reference_number=ref).exists():
                continue
            p_from = (today.replace(day=1) - timedelta(days=30 * months_back)).replace(day=1)
            p_to = (p_from + timedelta(days=31)).replace(day=1) - timedelta(days=1)
            po = LandlordPayout.objects.create(
                landlord=landlord,
                amount=amount,
                method=method,
                bank_account=bank,
                period_from=p_from,
                period_to=p_to,
                reference_number=ref,
                paid_at=now - timedelta(days=days_ago),
                notes=f"{DEMO_TAG} — {note}",
                approval_status=ApprovalStatus.AUTO_APPROVED,
                approved_at=now - timedelta(days=days_ago),
            )
            out.append(po)
        return out

    # -------------------------------------------------- supplier payments
    def _create_supplier_payments(self, suppliers, houses, bank):
        out = []
        now = timezone.now()
        # (supplier_idx, house_idx, amount, days_ago, service, method, invoice_ref)
        specs = [
            (0, 0, Decimal("180000"),  3,  "Kitchen tap + shower repair — A1",           SupplierPayment.Method.MOBILE_MONEY, "BF-2026-0412"),
            (0, 2, Decimal("240000"), 12,  "Blocked drain clearance — Kyaliwajjala A1",  SupplierPayment.Method.BANK,         "BF-2026-0398"),
            (1, 1, Decimal("320000"), 20,  "March grounds & garden — Buziga A2",         SupplierPayment.Method.BANK,         "GG-M03"),
            (1, 3, Decimal("320000"), 20,  "March grounds & garden — Kyaliwajjala A2",   SupplierPayment.Method.BANK,         "GG-M03"),
            (2, 0, Decimal("95000"),   8,  "Padlocks + keys — A1 gate",                  SupplierPayment.Method.CASH,         "SH-5590"),
            (2, 2, Decimal("145000"), 22,  "Paint & primers — Kyaliwajjala A1 touch-up", SupplierPayment.Method.BANK,         "SH-5512"),
        ]
        for sup_idx, house_idx, amount, days_ago, service, method, inv_ref in specs:
            supplier = suppliers[sup_idx]
            house = houses[house_idx]
            ref = f"{DEMO_TAG}-SPY-{supplier.tax_id}-{inv_ref}"
            if SupplierPayment.objects.filter(reference_number=ref).exists():
                continue
            sp = SupplierPayment.objects.create(
                supplier=supplier,
                amount=amount,
                method=method,
                bank_account=bank,
                service_description=service,
                invoice_reference=inv_ref,
                reference_number=ref,
                related_house=house,
                paid_at=now - timedelta(days=days_ago),
                notes=f"{DEMO_TAG} — seeded supplier payment.",
                approval_status=ApprovalStatus.AUTO_APPROVED,
                approved_at=now - timedelta(days=days_ago),
            )
            out.append(sp)
        return out

    # ----------------------------------------------- collections wiring
    def _assign_collections_persons(self, houses):
        """Set House.collections_person to the first two active employees so
        the collections-performance report has someone to attribute to."""
        emps = list(Employee.objects.filter(is_active=True).order_by("pk")[:2])
        if not emps:
            return
        for i, h in enumerate(houses):
            if h.collections_person_id:
                continue
            h.collections_person = emps[i % len(emps)]
            h.save(update_fields=["collections_person"])

    def _create_bonus_brackets(self):
        specs = [
            ("Tier 1: 100k–2M",       Decimal("100000"),    Decimal("2000000"), Decimal("2.00")),
            ("Tier 2: 2M+ to 5M",     Decimal("2000001"),   Decimal("5000000"), Decimal("3.50")),
            ("Tier 3: 5M and above",  Decimal("5000001"),   None,                Decimal("5.00")),
        ]
        for label, lo, hi, rate in specs:
            CollectionsBonusBracket.objects.update_or_create(
                label=label,
                defaults=dict(min_amount=lo, max_amount=hi, rate_percent=rate, is_active=True),
            )

    def _create_collections_targets(self):
        """Create a target for the current month and the previous 2 months
        for every employee that is now a collections_person on any house."""
        emps = (
            Employee.objects.filter(houses_collected__isnull=False).distinct()
            if hasattr(Employee, "houses_collected") else
            Employee.objects.filter(is_active=True)[:2]
        )
        today = timezone.localdate().replace(day=1)
        for emp in emps:
            for offset in (0, 1, 2):
                month = (today - timedelta(days=offset * 31)).replace(day=1)
                CollectionsTarget.objects.update_or_create(
                    employee=emp, month=month,
                    defaults=dict(
                        target_amount=Decimal("1500000"),
                        notes=f"{DEMO_TAG} — auto-seeded target",
                    ),
                )
