"""Billing views — list/detail/CRUD for invoices, payments, voids, credit
notes, refunds; plus approvals queue. All guarded by RoleRequiredMixin
(SPEC §16.9)."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import models, transaction
from django.http import HttpResponseRedirect
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse, reverse_lazy
from django.utils import timezone
from django.views.generic import CreateView, DetailView, UpdateView, View

from accounts.permissions import RoleRequiredMixin
from core.mixins import PaginatedListView
from core.models import TenantHouse

from .exceptions import ProtectedFinancialRecord, SelfApprovalBlocked, TrustedBypassBlocked
from .forms import (
    AdHocChargeForm,
    CreditNoteForm,
    InvoicePauseForm,
    InvoiceVoidForm,
    ManualInvoiceForm,
    PaymentForm,
    RefundForm,
    RejectionForm,
)
from .models import (
    AdHocCharge,
    ApprovalStatus,
    CreditNote,
    Invoice,
    InvoiceVoid,
    Payment,
    Receipt,
    Refund,
)
from .services import (
    apply_payment,
    execute_credit_note,
    execute_refund,
    execute_void,
)
from .exit_services import (
    build_settlement_plan,
    compute_exit_settlement,
    execute_exit_settlement,
)
from .models import ExitSettlement


# Role groups per SPEC §16.9
STAFF_ROLES = ("ADMIN", "SUPER_ADMIN", "FINANCE", "ACCOUNT_MANAGER", "COLLECTIONS", "SALES_REP")
FINANCE_ROLES = ("ADMIN", "SUPER_ADMIN", "FINANCE", "ACCOUNT_MANAGER")
APPROVER_ROLES = ("ADMIN", "SUPER_ADMIN", "FINANCE")
SUPER_ADMIN_ONLY = ("SUPER_ADMIN",)


# ---------------------------------------------------------------------------
# Invoice
# ---------------------------------------------------------------------------
class InvoiceListView(RoleRequiredMixin, PaginatedListView):
    required_roles = STAFF_ROLES
    model = Invoice
    template_name = "billing/invoice_list.html"
    context_object_name = "invoices"
    paginate_by = 100
    allowed_page_sizes = [50, 100, 150, 200]

    def get_queryset(self):
        qs = (
            super().get_queryset()
            .select_related(
                "tenant_house", "tenant_house__tenant",
                "tenant_house__house", "tenant_house__house__estate",
            )
            .order_by("-issue_date", "-id")
        )
        status = self.request.GET.get("status")
        if status:
            qs = qs.filter(status=status)
        q = (self.request.GET.get("q") or "").strip()
        if q:
            qs = qs.filter(
                models.Q(number__icontains=q)
                | models.Q(tenant_house__tenant__full_name__icontains=q)
                | models.Q(tenant_house__house__house_number__icontains=q)
                | models.Q(tenant_house__house__estate__name__icontains=q)
            )
        date_from = self.request.GET.get("from")
        date_to = self.request.GET.get("to")
        if date_from:
            qs = qs.filter(issue_date__gte=date_from)
        if date_to:
            qs = qs.filter(issue_date__lte=date_to)
        return qs

    def get(self, request, *args, **kwargs):
        if request.GET.get("export") == "csv":
            from core.utils import export_csv
            cols = [
                ("Number", "number"),
                ("Tenant", "tenant_house.tenant.full_name"),
                ("House", lambda i: str(i.tenant_house.house)),
                ("Estate", "tenant_house.house.estate.name"),
                ("Period from", "period_from"),
                ("Period to", "period_to"),
                ("Issue date", "issue_date"),
                ("Due date", "due_date"),
                ("Status", lambda i: i.get_status_display()),
                ("Total (UGX)", "total"),
                ("Outstanding (UGX)", "outstanding"),
            ]
            return export_csv(self.get_queryset(), cols, "invoices")
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        from decimal import Decimal as _D
        from datetime import date as _date
        from .models import PaymentAllocation

        ctx = super().get_context_data(**kwargs)
        ctx["statuses"] = Invoice.Status.choices
        ctx["q"] = self.request.GET.get("q", "")
        ctx["selected_status"] = self.request.GET.get("status", "")
        ctx["filter_from"] = self.request.GET.get("from", "")
        ctx["filter_to"] = self.request.GET.get("to", "")

        # KPI strip: month-on-month view of billed / collected / carry-forward.
        today = timezone.localdate()
        m_start = today.replace(day=1)
        # Next month start
        if m_start.month == 12:
            m_next = _date(m_start.year + 1, 1, 1)
        else:
            m_next = _date(m_start.year, m_start.month + 1, 1)
        # Prev month
        if m_start.month == 1:
            p_start = _date(m_start.year - 1, 12, 1)
        else:
            p_start = _date(m_start.year, m_start.month - 1, 1)

        billed_this_month = (
            Invoice.objects.filter(
                issue_date__gte=m_start, issue_date__lt=m_next,
                status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID,
                            Invoice.Status.PAID, Invoice.Status.OVERDUE],
            ).aggregate(s=models.Sum("total"))["s"] or _D("0")
        )
        collected_this_month = (
            PaymentAllocation.objects.filter(
                applied_at__gte=m_start, applied_at__lt=m_next,
                is_advance_hold=False,
            ).aggregate(s=models.Sum("amount"))["s"] or _D("0")
        )
        billed_prev_month = (
            Invoice.objects.filter(
                issue_date__gte=p_start, issue_date__lt=m_start,
                status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID,
                            Invoice.Status.PAID, Invoice.Status.OVERDUE],
            ).aggregate(s=models.Sum("total"))["s"] or _D("0")
        )
        collected_prev_month = (
            PaymentAllocation.objects.filter(
                applied_at__gte=p_start, applied_at__lt=m_start,
                is_advance_hold=False,
            ).aggregate(s=models.Sum("amount"))["s"] or _D("0")
        )
        # Carry-forward = total outstanding on invoices issued BEFORE this month.
        carry_forward = _D("0")
        for inv in Invoice.objects.filter(
            issue_date__lt=m_start,
            status__in=[Invoice.Status.ISSUED, Invoice.Status.PARTIALLY_PAID,
                        Invoice.Status.OVERDUE],
        ).only("id", "total"):
            carry_forward += inv.outstanding

        ctx["kpi_month_label"] = m_start.strftime("%B %Y")
        ctx["kpi_prev_label"]  = p_start.strftime("%B %Y")
        ctx["kpi_billed_this_month"] = int(billed_this_month)
        ctx["kpi_collected_this_month"] = int(collected_this_month)
        ctx["kpi_billed_prev_month"] = int(billed_prev_month)
        ctx["kpi_collected_prev_month"] = int(collected_prev_month)
        ctx["kpi_carry_forward"] = int(carry_forward)
        ctx["kpi_outstanding_this_month"] = int(billed_this_month - collected_this_month)
        return ctx


class InvoiceDetailView(RoleRequiredMixin, DetailView):
    required_roles = STAFF_ROLES
    model = Invoice
    template_name = "billing/invoice_detail.html"
    context_object_name = "invoice"


class InvoiceCreateView(RoleRequiredMixin, CreateView):
    required_roles = FINANCE_ROLES
    model = Invoice
    form_class = ManualInvoiceForm
    template_name = "billing/invoice_form.html"

    @transaction.atomic
    def form_valid(self, form):
        from .models import InvoiceLine
        from decimal import Decimal as _D

        invoice = form.save(commit=False)
        invoice.created_by = self.request.user
        invoice.updated_by = self.request.user
        invoice.status = Invoice.Status.DRAFT
        # Roll the rent_amount up into subtotal/total so `_issue_and_post`
        # has a real total to debit. Tax stays 0 by default — the existing
        # tax workflow on issue can populate `tax_total` if a TaxType is
        # bound to the tenancy.
        rent = invoice.rent_amount or _D("0")
        invoice.subtotal = rent
        invoice.tax_total = _D("0")
        invoice.total = rent + invoice.tax_total
        invoice.save()

        # Materialize the rent into an InvoiceLine — `_issue_and_post`
        # iterates lines to compute the credit side of the journal.
        if rent > 0:
            InvoiceLine.objects.create(
                invoice=invoice,
                kind=InvoiceLine.Kind.RENT,
                description=f"Rent {invoice.period_from} → {invoice.period_to}",
                amount=rent,
                period_from=invoice.period_from,
                period_to=invoice.period_to,
                target=InvoiceLine.TARGET_LANDLORD,
                created_by=self.request.user,
                updated_by=self.request.user,
            )
        self.object = invoice
        messages.success(self.request, "Draft invoice saved with a rent line.")
        return HttpResponseRedirect(self.get_success_url())

    def get_success_url(self):
        return reverse("billing:invoice-detail", args=[self.object.pk])


class InvoiceIssueView(RoleRequiredMixin, View):
    """DRAFT → ISSUED. Calls the same `_issue_and_post` helper that the
    automated billing run uses, so the GL journal is posted identically."""
    required_roles = FINANCE_ROLES + ("ACCOUNT_MANAGER",)

    @transaction.atomic
    def post(self, request, pk):
        from .services import _issue_and_post
        from decimal import Decimal as _D
        invoice = get_object_or_404(Invoice, pk=pk)
        if invoice.status != Invoice.Status.DRAFT:
            messages.error(
                request,
                f"Only DRAFT invoices can be issued. This invoice is {invoice.get_status_display()}."
            )
            return redirect("billing:invoice-detail", pk=invoice.pk)
        # Guard against zero-total invoices reaching the GL, where they'd
        # violate the JournalEntryLine "at least one side > 0" constraint.
        if (invoice.total or _D("0")) <= 0 or not invoice.lines.exists():
            messages.error(
                request,
                "Cannot issue an empty invoice — it has no rent line or its total is zero. "
                "Edit the draft and add at least one line first."
            )
            return redirect("billing:invoice-detail", pk=invoice.pk)
        try:
            _issue_and_post(invoice, user=request.user)
        except ValidationError as exc:
            messages.error(request, "; ".join(exc.messages))
            return redirect("billing:invoice-detail", pk=invoice.pk)
        messages.success(
            request,
            f"Invoice {invoice.number} issued and posted to the GL."
        )
        return redirect("billing:invoice-detail", pk=invoice.pk)


class InvoiceMarkPaidView(RoleRequiredMixin, View):
    """Admin override: force an ISSUED / PARTIALLY_PAID / OVERDUE invoice to
    PAID. Use when payment was received outside the system and a Payment row
    will not be created (e.g. cash reconciled offline, historical data).

    Adds an AuditLog-style note in invoice.notes with the override reason.
    Does NOT post a GL journal — the admin is asserting the ledger is already
    reconciled separately.
    """
    required_roles = APPROVER_ROLES   # ADMIN / SUPER_ADMIN / FINANCE

    def post(self, request, pk):
        from django.utils import timezone as _tz
        invoice = get_object_or_404(Invoice, pk=pk)
        reason = (request.POST.get("reason") or "").strip()
        if not reason:
            messages.error(request, "A reason is required to manually mark an invoice paid.")
            return redirect("billing:invoice-detail", pk=invoice.pk)
        if invoice.status in (Invoice.Status.PAID, Invoice.Status.VOIDED, Invoice.Status.CANCELLED):
            messages.error(request, f"Invoice is already {invoice.get_status_display()} — nothing to do.")
            return redirect("billing:invoice-detail", pk=invoice.pk)
        prev_status = invoice.get_status_display()
        invoice.status = Invoice.Status.PAID
        stamp = _tz.localtime().strftime("%Y-%m-%d %H:%M")
        note = (
            f"[MANUAL PAID {stamp} by {request.user.email}] was {prev_status}. Reason: {reason}"
        )
        invoice.notes = (invoice.notes + "\n" if invoice.notes else "") + note
        invoice.updated_by = request.user
        invoice.save(update_fields=["status", "notes", "updated_by", "updated_at"])
        messages.success(
            request,
            f"Invoice {invoice.number or invoice.pk} force-marked as Paid. "
            f"Remember: no GL journal was posted — reconcile the ledger separately if needed."
        )
        return redirect("billing:invoice-detail", pk=invoice.pk)


class InvoiceDeleteView(RoleRequiredMixin, View):
    """Only Super Admin may delete, and only drafts."""
    required_roles = SUPER_ADMIN_ONLY

    def post(self, request, pk):
        obj = get_object_or_404(Invoice, pk=pk)
        try:
            obj.delete()
        except ProtectedFinancialRecord as exc:
            messages.error(request, str(exc))
            return redirect("billing:invoice-detail", pk=pk)
        messages.success(request, "Draft invoice deleted.")
        return redirect("billing:invoice-list")


class InvoicePauseView(RoleRequiredMixin, View):
    required_roles = FINANCE_ROLES

    def post(self, request, pk):
        tenancy = get_object_or_404(TenantHouse, pk=pk)
        form = InvoicePauseForm(request.POST)
        if form.is_valid():
            tenancy.invoice_generation_status = form.cleaned_data["status"]
            tenancy.invoice_generation_note = form.cleaned_data.get("note", "")
            tenancy.updated_by = request.user
            tenancy.save(update_fields=[
                "invoice_generation_status", "invoice_generation_note", "updated_by", "updated_at",
            ])
            messages.success(request, f"Invoice generation set to {tenancy.invoice_generation_status}.")
        else:
            messages.error(request, "Invalid pause/resume request.")
        return redirect("core:tenant-detail", pk=tenancy.tenant_id)


# ---------------------------------------------------------------------------
# Payment
# ---------------------------------------------------------------------------
class PaymentListView(RoleRequiredMixin, PaginatedListView):
    required_roles = STAFF_ROLES
    model = Payment
    template_name = "billing/payment_list.html"
    context_object_name = "payments"
    paginate_by = 100
    allowed_page_sizes = [50, 100, 150, 200]

    def get_queryset(self):
        qs = (
            super().get_queryset()
            .select_related("tenant", "bank_account")
            .order_by("-received_at", "-id")
        )
        q = (self.request.GET.get("q") or "").strip()
        if q:
            qobj = (
                models.Q(number__icontains=q)
                | models.Q(reference_number__icontains=q)
                | models.Q(tenant__full_name__icontains=q)
            )
            try:
                from decimal import Decimal as _D
                qobj |= models.Q(amount=_D(q.replace(",", "")))
            except Exception:
                pass
            qs = qs.filter(qobj)
        method = self.request.GET.get("method")
        if method:
            qs = qs.filter(method=method)
        status = self.request.GET.get("status")
        if status:
            qs = qs.filter(approval_status=status)
        date_from = self.request.GET.get("from")
        date_to = self.request.GET.get("to")
        if date_from:
            qs = qs.filter(received_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(received_at__date__lte=date_to)
        return qs

    def get(self, request, *args, **kwargs):
        if request.GET.get("export") == "csv":
            from core.utils import export_csv
            cols = [
                ("Receipt", "number"),
                ("Tenant", "tenant.full_name"),
                ("Method", lambda p: p.get_method_display()),
                ("Bank", "bank_account.name"),
                ("Reference", "reference_number"),
                ("Received", "received_at"),
                ("Status", lambda p: p.get_approval_status_display()),
                ("Amount (UGX)", "amount"),
            ]
            return export_csv(self.get_queryset(), cols, "payments")
        return super().get(request, *args, **kwargs)


class PaymentDetailView(RoleRequiredMixin, DetailView):
    required_roles = STAFF_ROLES
    model = Payment
    template_name = "billing/payment_detail.html"
    context_object_name = "payment"


class PaymentCreateView(RoleRequiredMixin, CreateView):
    required_roles = FINANCE_ROLES + ("COLLECTIONS",)
    model = Payment
    form_class = PaymentForm
    template_name = "billing/payment_form.html"

    @transaction.atomic
    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.updated_by = self.request.user
        form.instance.maker = self.request.user
        form.instance.submitted_at = timezone.now()
        response = super().form_valid(form)
        payment = self.object
        # Attempt trusted bypass for auto-approval
        try:
            if payment.try_trusted_autoapprove():
                apply_payment(payment, user=self.request.user)
                messages.success(self.request, "Payment auto-approved and applied (trusted employee).")
            else:
                messages.success(self.request, "Payment submitted for approval.")
        except TrustedBypassBlocked:
            messages.success(self.request, "Payment submitted for approval.")
        return response

    def get_success_url(self):
        return reverse("billing:payment-detail", args=[self.object.pk])


# ---------------------------------------------------------------------------
# Ad-hoc charges
# ---------------------------------------------------------------------------
class AdHocChargeListView(RoleRequiredMixin, PaginatedListView):
    required_roles = STAFF_ROLES
    model = AdHocCharge
    template_name = "billing/adhoc_list.html"
    context_object_name = "charges"


class AdHocChargeCreateView(RoleRequiredMixin, CreateView):
    required_roles = FINANCE_ROLES + ("ACCOUNT_MANAGER",)
    model = AdHocCharge
    form_class = AdHocChargeForm
    template_name = "billing/adhoc_form.html"
    success_url = reverse_lazy("billing:adhoc-list")

    @transaction.atomic
    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.updated_by = self.request.user
        form.instance.maker = self.request.user
        form.instance.submitted_at = timezone.now()
        response = super().form_valid(form)
        try:
            if self.object.try_trusted_autoapprove():
                messages.success(self.request, "Ad-hoc charge auto-approved.")
            else:
                messages.success(self.request, "Ad-hoc charge submitted for approval.")
        except TrustedBypassBlocked:
            pass
        return response


# ---------------------------------------------------------------------------
# Voids
# ---------------------------------------------------------------------------
class InvoiceVoidCreateView(RoleRequiredMixin, CreateView):
    required_roles = FINANCE_ROLES
    model = InvoiceVoid
    form_class = InvoiceVoidForm
    template_name = "billing/void_form.html"

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.updated_by = self.request.user
        form.instance.maker = self.request.user
        form.instance.submitted_at = timezone.now()
        messages.success(self.request, "Void submitted for approval (maker-checker always required).")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("billing:approvals")


# ---------------------------------------------------------------------------
# Credit Notes
# ---------------------------------------------------------------------------
class CreditNoteCreateView(RoleRequiredMixin, CreateView):
    required_roles = FINANCE_ROLES
    model = CreditNote
    form_class = CreditNoteForm
    template_name = "billing/credit_note_form.html"

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.updated_by = self.request.user
        form.instance.maker = self.request.user
        form.instance.submitted_at = timezone.now()
        messages.success(self.request, "Credit note submitted for approval.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("billing:approvals")


# ---------------------------------------------------------------------------
# Refunds
# ---------------------------------------------------------------------------
class RefundCreateView(RoleRequiredMixin, CreateView):
    required_roles = FINANCE_ROLES
    model = Refund
    form_class = RefundForm
    template_name = "billing/refund_form.html"

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.updated_by = self.request.user
        form.instance.maker = self.request.user
        form.instance.submitted_at = timezone.now()
        messages.success(self.request, "Refund submitted for approval.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("billing:approvals")


# ---------------------------------------------------------------------------
# Approvals queue + approve/reject actions
# ---------------------------------------------------------------------------
def _approval_models():
    from .models import ExitSettlement, ExpenseClaim, LandlordPayout, SupplierPayment
    return {
        "payment": (Payment, apply_payment),
        "adhoc": (AdHocCharge, None),
        "void": (InvoiceVoid, execute_void),
        "credit": (CreditNote, execute_credit_note),
        "refund": (Refund, execute_refund),
        "payout": (LandlordPayout, None),          # signal posts the journal
        "supplier_payment": (SupplierPayment, None),
        "expense": (ExpenseClaim, None),
        "exit": (ExitSettlement, None),            # execute step is a separate workflow page
    }


APPROVAL_MODELS = _approval_models()


class ApprovalsQueueView(RoleRequiredMixin, PaginatedListView):
    """Tabbed queue with history + filters.

    Query params:
      tab       — payment|adhoc|void|credit|refund|payout|supplier_payment|expense
      status    — pending|approved|rejected|sent_back|all  (default: pending)
      maker_q   — substring search on maker name/email
      from / to — submitted_at range (YYYY-MM-DD)
    """
    required_roles = APPROVER_ROLES + ("ACCOUNT_MANAGER",)
    template_name = "billing/approvals_queue.html"
    context_object_name = "items"

    STATUS_MAP = {
        "pending":   [ApprovalStatus.PENDING],
        "approved":  [ApprovalStatus.APPROVED, ApprovalStatus.AUTO_APPROVED],
        "rejected":  [ApprovalStatus.REJECTED],
        "sent_back": [ApprovalStatus.SENT_BACK],
        "all":       [s for s in ApprovalStatus.values],
    }

    def get(self, request, *args, **kwargs):
        self.tab = request.GET.get("tab", "payment")
        self.status_key = request.GET.get("status", "pending")
        if self.status_key not in self.STATUS_MAP:
            self.status_key = "pending"
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        model_cls, _ = APPROVAL_MODELS.get(self.tab, APPROVAL_MODELS["payment"])
        qs = model_cls.objects.filter(
            approval_status__in=self.STATUS_MAP[self.status_key]
        )
        maker_q = (self.request.GET.get("maker_q") or "").strip()
        if maker_q:
            qs = qs.filter(
                models.Q(maker__first_name__icontains=maker_q)
                | models.Q(maker__last_name__icontains=maker_q)
                | models.Q(maker__email__icontains=maker_q)
            )
        date_from = self.request.GET.get("from")
        date_to = self.request.GET.get("to")
        if date_from:
            qs = qs.filter(submitted_at__date__gte=date_from)
        if date_to:
            qs = qs.filter(submitted_at__date__lte=date_to)
        # Pending sorts oldest-first (first in, first out); history newest-first
        if self.status_key == "pending":
            qs = qs.order_by("submitted_at")
        else:
            qs = qs.order_by("-approved_at", "-submitted_at")
        return qs.select_related("maker", "checker")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["active_tab"] = self.tab
        ctx["active_status"] = self.status_key
        ctx["tabs"] = [
            ("payment", "Payments"), ("adhoc", "Ad-hoc"),
            ("void", "Voids"), ("credit", "Credit Notes"), ("refund", "Refunds"),
            ("payout", "Landlord Payouts"),
            ("supplier_payment", "Supplier Payments"),
            ("expense", "Expense Claims"),
            ("exit", "Exit Settlements"),
        ]
        ctx["status_filters"] = [
            ("pending", "Pending"),
            ("approved", "Approved"),
            ("sent_back", "Sent back"),
            ("rejected", "Rejected"),
            ("all", "All"),
        ]
        now = timezone.now()
        ctx["now"] = now
        from datetime import timedelta
        ctx["overdue_threshold"] = now - timedelta(hours=24)
        ctx["bank_accounts"] = _bank_accounts()
        ctx["filter_maker_q"] = self.request.GET.get("maker_q", "")
        ctx["filter_from"] = self.request.GET.get("from", "")
        ctx["filter_to"] = self.request.GET.get("to", "")
        # Build tab_rows = [(code, label, pending_count), ...] so the template
        # can render badges without needing a custom dict-lookup filter.
        tab_rows = []
        for code, label in ctx["tabs"]:
            mc, _ = APPROVAL_MODELS[code]
            cnt = mc.objects.filter(approval_status=ApprovalStatus.PENDING).count()
            tab_rows.append((code, label, cnt))
        ctx["tab_rows"] = tab_rows
        return ctx


class ApprovalActionView(RoleRequiredMixin, View):
    required_roles = APPROVER_ROLES + ("ACCOUNT_MANAGER",)

    # Kinds where Finance can override/set the debit/credit bank account at
    # approval time. Keyed by kind → (field_name_on_model, required_bool).
    BANK_OVERRIDE_FIELDS = {
        "expense":          ("reimbursement_bank", True),   # required — employee doesn't set it
        "payment":          ("bank_account",       False),  # optional override
        "payout":           ("bank_account",       False),
        "supplier_payment": ("bank_account",       False),
        "refund":           ("bank_account",       False),
    }

    def post(self, request, kind, pk, action):
        if kind not in APPROVAL_MODELS:
            raise PermissionDenied("Unknown approval type")
        model_cls, effect_fn = APPROVAL_MODELS[kind]
        obj = get_object_or_404(model_cls, pk=pk)
        redirect_url = f"{reverse('billing:approvals')}?tab={kind}"
        try:
            if action == "approve":
                # Optional/required bank-account override at approval time
                if kind in self.BANK_OVERRIDE_FIELDS:
                    from accounting.models import BankAccount
                    field_name, required = self.BANK_OVERRIDE_FIELDS[kind]
                    bank_id = request.POST.get("bank_account_id")
                    if bank_id:
                        try:
                            bank = BankAccount.objects.get(pk=bank_id, is_active=True)
                            setattr(obj, field_name, bank)
                            obj.save(update_fields=[field_name])
                        except BankAccount.DoesNotExist:
                            messages.error(request, "Selected bank account is not active.")
                            return redirect(redirect_url)
                    elif required and not getattr(obj, field_name, None):
                        messages.error(request, "Please pick a bank account before approving.")
                        return redirect(redirect_url)
                obj.approve(request.user)
                if effect_fn is not None:
                    effect_fn(obj, user=request.user)
                messages.success(request, f"{kind.title()} approved.")
            elif action == "sendback":
                form = RejectionForm(request.POST)
                if not form.is_valid():
                    messages.error(request, "Reason required when sending back.")
                    return redirect(redirect_url)
                obj.send_back(request.user, form.cleaned_data["reason"])
                messages.info(request, f"{kind.title()} sent back to maker for revision.")
            elif action == "resubmit":
                obj.resubmit(request.user)
                messages.success(request, f"{kind.title()} resubmitted for approval.")
            elif action == "reject":
                form = RejectionForm(request.POST)
                if not form.is_valid():
                    messages.error(request, "Reason required to reject.")
                    return redirect("billing:approvals")
                obj.reject(request.user, form.cleaned_data["reason"])
                messages.info(request, f"{kind.title()} rejected.")
            else:
                raise PermissionDenied("Unknown action")
        except SelfApprovalBlocked as exc:
            messages.error(request, f"Blocked: {exc}")
        except ValidationError as exc:
            messages.error(request, f"{exc}")
        return redirect("billing:approvals")


# ---------------------------------------------------------------------------
# Receipts
# ---------------------------------------------------------------------------
class ReceiptDetailView(RoleRequiredMixin, DetailView):
    required_roles = STAFF_ROLES
    model = Receipt
    template_name = "billing/receipt_detail.html"
    context_object_name = "receipt"

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        if request.GET.get("format") == "pdf":
            from .pdf import receipt_pdf
            return receipt_pdf(self.object)
        return super().get(request, *args, **kwargs)

    def get_template_names(self):
        fmt = self.request.GET.get("fmt", "")
        if self.object.kind == Receipt.Kind.REFUND:
            if fmt == "thermal":
                return ["billing/refund_receipt_thermal.html"]
            if fmt == "print":
                return ["billing/refund_receipt_a4.html"]
            return ["billing/refund_receipt_mobile.html"]
        if fmt == "thermal":
            return ["billing/receipt_thermal.html"]
        if fmt == "print":
            return ["billing/receipt_a4.html"]
        return ["billing/receipt_mobile.html"]


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
class AdvancePaymentsReportView(RoleRequiredMixin, PaginatedListView):
    """Employee-facing report: shows held advances for both accounts with
    routing badges (Managed vs Meili-Owned).

    Filters (GET params):
      - tenant     : Tenant pk
      - house      : House pk
      - estate     : Estate pk
      - landlord   : Landlord pk
      - ownership  : "MANAGED" | "MEILI"

    Each row is tagged ``stale_badge=True`` when the hold has been sitting for
    at least two full billing periods (≥ 60 days) — SPEC §22 Advance Report.
    """
    required_roles = FINANCE_ROLES
    template_name = "billing/report_advances.html"
    context_object_name = "rows"

    def get_queryset(self):
        from .models import PaymentAllocation
        qs = (
            PaymentAllocation.objects.filter(is_advance_hold=True, applied_at__isnull=True)
            .select_related("payment", "payment__tenant")
            .order_by("-allocated_at")
        )
        g = self.request.GET
        if g.get("tenant"):
            qs = qs.filter(payment__tenant_id=g["tenant"])
        if g.get("house"):
            qs = qs.filter(payment__tenant__tenancies__house_id=g["house"]).distinct()
        if g.get("estate"):
            qs = qs.filter(
                payment__tenant__tenancies__house__estate_id=g["estate"]
            ).distinct()
        if g.get("landlord"):
            lid = g["landlord"]
            qs = qs.filter(
                models.Q(payment__tenant__tenancies__house__landlord_id=lid)
                | models.Q(payment__tenant__tenancies__house__estate__landlord_id=lid)
            ).distinct()
        ownership = g.get("ownership")
        if ownership in {"MANAGED", "MEILI"}:
            want_meili = ownership == "MEILI"
            qs = qs.filter(
                models.Q(
                    payment__tenant__tenancies__house__landlord__is_meili_owned=want_meili
                )
                | models.Q(
                    payment__tenant__tenancies__house__estate__landlord__is_meili_owned=want_meili
                )
            ).distinct()
        return qs

    def get_context_data(self, **kwargs):
        from datetime import timedelta
        from core.models import Estate, House, Landlord, Tenant

        ctx = super().get_context_data(**kwargs)
        now = timezone.now()
        two_periods = timedelta(days=60)
        rows = []
        for row in ctx.get(self.context_object_name, []):
            row.stale_badge = (now - row.allocated_at) >= two_periods
            rows.append(row)
        ctx[self.context_object_name] = rows

        g = self.request.GET
        ctx.update({
            "tenants": Tenant.objects.order_by("full_name"),
            "houses": House.objects.select_related("estate").order_by(
                "estate__name", "house_number"
            ),
            "estates": Estate.objects.order_by("name"),
            "landlords": Landlord.objects.order_by("full_name"),
            "active_tenant": g.get("tenant", ""),
            "active_house": g.get("house", ""),
            "active_estate": g.get("estate", ""),
            "active_landlord": g.get("landlord", ""),
            "active_ownership": g.get("ownership", ""),
        })
        return ctx


class TenantStatementView(RoleRequiredMixin, DetailView):
    required_roles = STAFF_ROLES
    model = TenantHouse
    template_name = "billing/tenant_statement.html"
    context_object_name = "tenancy"

    def get_context_data(self, **kwargs):
        from decimal import Decimal
        ctx = super().get_context_data(**kwargs)
        today = timezone.localdate()
        invoices = self.object.invoices.exclude(
            status__in=[Invoice.Status.VOIDED, Invoice.Status.CANCELLED]
        ).order_by("period_from")
        arrears = Decimal("0")
        current = Decimal("0")
        for inv in invoices:
            if inv.due_date < today and inv.outstanding > 0:
                arrears += inv.outstanding
            else:
                current += inv.outstanding
        ctx.update({"invoices": invoices, "arrears": arrears, "current": current})
        return ctx


class LandlordStatementView(RoleRequiredMixin, DetailView):
    """Landlord statement — never shows held-advance balances (fiduciary)."""
    required_roles = FINANCE_ROLES
    template_name = "billing/landlord_statement.html"
    context_object_name = "landlord"

    def get_object(self):
        from core.models import Landlord
        return get_object_or_404(Landlord, pk=self.kwargs["pk"])

    def get(self, request, *args, **kwargs):
        self.object = self.get_object()
        if request.GET.get("format") == "pdf":
            from .pdf import landlord_statement_pdf
            ctx = self.get_context_data(object=self.object)
            return landlord_statement_pdf(self.object, ctx["invoices"], ctx["houses"])
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        from core.models import House
        ctx = super().get_context_data(**kwargs)
        # Houses covered by this landlord — either direct override (House.landlord)
        # or via their estates. Use a single Q-filter, NOT queryset OR across models.
        ctx["houses"] = (
            House.objects.filter(
                models.Q(landlord=self.object)
                | models.Q(estate__landlord=self.object)
            )
            .select_related("estate")
            .distinct()
            .order_by("estate__name", "house_number")
        )
        invoices = Invoice.objects.filter(
            models.Q(tenant_house__house__estate__landlord=self.object)
            | models.Q(tenant_house__house__landlord=self.object)
        ).distinct()
        ctx["invoices"] = invoices.exclude(
            status__in=[Invoice.Status.VOIDED, Invoice.Status.CANCELLED]
        ).order_by("-issue_date")[:100]
        return ctx


# ---------------------------------------------------------------------------
# Exit workflow (SPEC §20.5 — strict-order settlement)
# ---------------------------------------------------------------------------
class ExitWorkflowView(RoleRequiredMixin, View):
    """Single-page exit workflow.

    GET  — render the computed plan (preview).
    POST — create/update the ExitSettlement row in DRAFT state, stamp the
           plan. Execution is a separate submit after maker/checker.
    """
    required_roles = FINANCE_ROLES

    def get(self, request, pk):
        th = get_object_or_404(TenantHouse, pk=pk)
        comp = compute_exit_settlement(th)
        settlement = ExitSettlement.objects.filter(tenant_house=th).first()
        context = {
            "tenancy": th,
            "comp": comp,
            "settlement": settlement,
            "other_active": list(
                th.tenant.tenancies.filter(status=TenantHouse.Status.ACTIVE)
                .exclude(pk=th.pk)
            ),
            "bank_accounts": _bank_accounts(),
        }
        return _render_exit(request, context)

    @transaction.atomic
    def post(self, request, pk):
        th = get_object_or_404(TenantHouse, pk=pk)
        action = request.POST.get("action", "compute")
        damages = _parse_damages(request.POST)
        transfer_ids = [
            int(x) for x in request.POST.getlist("transfer_tenancy_id") if x.isdigit()
        ]
        comp = compute_exit_settlement(th, damages=damages)
        plan = build_settlement_plan(
            comp,
            damages=damages,
            transfer_to_tenancy_ids=transfer_ids,
        )
        settlement, _ = ExitSettlement.objects.update_or_create(
            tenant_house=th,
            defaults={
                "status": ExitSettlement.Status.DRAFT,
                "held_managed_at_start": comp.held_managed,
                "held_meili_at_start": comp.held_meili,
                "deposit_at_start": comp.deposit_balance,
                "outstanding_at_start": comp.outstanding_total,
                "damages_total": comp.damages_total,
                "plan": plan,
                "maker": request.user,
                "approval_status": ApprovalStatus.PENDING,
                "submitted_at": timezone.now(),
                "created_by": request.user,
            },
        )

        if action == "execute":
            # Caller must be an approver (not the maker) — enforced by
            # `ExitSettlement.approve()`. UI requires two-person flow.
            try:
                settlement.approve(request.user)
            except SelfApprovalBlocked:
                messages.error(
                    request,
                    "A checker (different user) must approve before executing."
                )
                return redirect("billing:exit-workflow", pk=th.pk)
            refund_method = request.POST.get("refund_method") or "BANK"
            bank_pk = request.POST.get("refund_bank_account")
            bank = None
            if bank_pk:
                from accounting.models import BankAccount
                bank = BankAccount.objects.filter(pk=bank_pk).first()
            destination = request.POST.get("refund_destination", "")
            reference = request.POST.get("refund_reference", "")
            try:
                execute_exit_settlement(
                    settlement,
                    refund_method=refund_method,
                    refund_bank_account=bank,
                    refund_destination=destination,
                    refund_reference=reference,
                    damages_input=damages,
                    user=request.user,
                )
                messages.success(request, "Exit settlement executed.")
            except ValidationError as exc:
                messages.error(request, f"{exc}")
            return redirect("billing:exit-workflow", pk=th.pk)

        messages.info(request, "Settlement plan saved. Submit a checker for execution.")
        return redirect("billing:exit-workflow", pk=th.pk)


def _parse_damages(post):
    from decimal import Decimal, InvalidOperation
    descs = post.getlist("damage_description")
    amts = post.getlist("damage_amount")
    result = []
    for d, a in zip(descs, amts):
        d = (d or "").strip()
        try:
            amount = Decimal(a)
        except (InvalidOperation, TypeError):
            continue
        if d and amount > 0:
            result.append({"description": d, "amount": amount})
    return result


def _bank_accounts():
    from accounting.models import BankAccount
    return BankAccount.objects.filter(is_active=True).order_by("name")


def _render_exit(request, context):
    from django.shortcuts import render
    return render(request, "billing/exit_workflow.html", context)


# ---------------------------------------------------------------------------
# Landlord Payouts (Phase E) — list + create + detail. Approvals reuse the
# existing ApprovalsQueueView workflow via MakerCheckerMixin.
# ---------------------------------------------------------------------------
class LandlordPayoutListView(RoleRequiredMixin, PaginatedListView):
    required_roles = FINANCE_ROLES
    template_name = "billing/landlord_payout_list.html"
    context_object_name = "payouts"

    def get_queryset(self):
        from .models import LandlordPayout
        qs = (
            LandlordPayout.objects.select_related("landlord", "bank_account")
            .order_by("-paid_at", "-id")
        )
        q = (self.request.GET.get("q") or "").strip()
        if q:
            qobj = (
                models.Q(number__icontains=q)
                | models.Q(reference_number__icontains=q)
                | models.Q(landlord__full_name__icontains=q)
            )
            try:
                from decimal import Decimal as _D
                qobj |= models.Q(amount=_D(q.replace(",", "")))
            except Exception:
                pass
            qs = qs.filter(qobj)
        status = self.request.GET.get("status")
        if status:
            qs = qs.filter(approval_status=status)
        return qs

    def get(self, request, *args, **kwargs):
        if request.GET.get("export") == "csv":
            from core.utils import export_csv
            columns = [
                ("Number", "number"),
                ("Landlord", "landlord.full_name"),
                ("Amount (UGX)", "amount"),
                ("Method", lambda r: r.get_method_display()),
                ("Bank", "bank_account.name"),
                ("Reference", "reference_number"),
                ("Paid at", "paid_at"),
                ("Status", lambda r: r.get_approval_status_display()),
            ]
            return export_csv(self.get_queryset(), columns, "landlord_payouts")
        return super().get(request, *args, **kwargs)


class LandlordPayoutCreateView(RoleRequiredMixin, CreateView):
    required_roles = FINANCE_ROLES
    template_name = "billing/landlord_payout_form.html"

    def get_form_class(self):
        from .forms import LandlordPayoutForm
        return LandlordPayoutForm

    def get_initial(self):
        initial = super().get_initial()
        lid = self.request.GET.get("landlord")
        if lid:
            initial["landlord"] = lid
        return initial

    def form_valid(self, form):
        with transaction.atomic():
            obj = form.save(commit=False)
            obj.maker = self.request.user
            obj.submitted_at = timezone.now()
            obj.created_by = self.request.user
            obj.updated_by = self.request.user
            obj.save()
            try:
                obj.try_trusted_autoapprove()
            except TrustedBypassBlocked:
                pass
        messages.success(self.request, f"Landlord payout {obj.number} recorded.")
        return redirect(reverse("billing:landlord-payout-detail", args=[obj.pk]))


class LandlordPayoutDetailView(RoleRequiredMixin, DetailView):
    required_roles = FINANCE_ROLES
    template_name = "billing/landlord_payout_detail.html"
    context_object_name = "payout"

    def get_queryset(self):
        from .models import LandlordPayout
        return LandlordPayout.objects.select_related(
            "landlord", "bank_account", "maker", "checker"
        )


# ---------------------------------------------------------------------------
# Supplier Payments (Phase E)
# ---------------------------------------------------------------------------
class SupplierPaymentListView(RoleRequiredMixin, PaginatedListView):
    required_roles = FINANCE_ROLES
    template_name = "billing/supplier_payment_list.html"
    context_object_name = "payments"

    def get_queryset(self):
        from .models import SupplierPayment
        qs = (
            SupplierPayment.objects.select_related("supplier", "bank_account", "related_house")
            .order_by("-paid_at", "-id")
        )
        q = (self.request.GET.get("q") or "").strip()
        if q:
            qobj = (
                models.Q(number__icontains=q)
                | models.Q(reference_number__icontains=q)
                | models.Q(invoice_reference__icontains=q)
                | models.Q(supplier__name__icontains=q)
                | models.Q(service_description__icontains=q)
            )
            try:
                from decimal import Decimal as _D
                qobj |= models.Q(amount=_D(q.replace(",", "")))
            except Exception:
                pass
            qs = qs.filter(qobj)
        status = self.request.GET.get("status")
        if status:
            qs = qs.filter(approval_status=status)
        return qs

    def get(self, request, *args, **kwargs):
        if request.GET.get("export") == "csv":
            from core.utils import export_csv
            columns = [
                ("Number", "number"),
                ("Supplier", "supplier.name"),
                ("Service", "service_description"),
                ("Amount (UGX)", "amount"),
                ("Method", lambda r: r.get_method_display()),
                ("Bank", "bank_account.name"),
                ("Related house", lambda r: str(r.related_house) if r.related_house else ""),
                ("Invoice ref", "invoice_reference"),
                ("Reference", "reference_number"),
                ("Paid at", "paid_at"),
                ("Status", lambda r: r.get_approval_status_display()),
            ]
            return export_csv(self.get_queryset(), columns, "supplier_payments")
        return super().get(request, *args, **kwargs)


class SupplierPaymentCreateView(RoleRequiredMixin, CreateView):
    required_roles = FINANCE_ROLES
    template_name = "billing/supplier_payment_form.html"

    def get_form_class(self):
        from .forms import SupplierPaymentForm
        return SupplierPaymentForm

    def get_initial(self):
        initial = super().get_initial()
        sid = self.request.GET.get("supplier")
        if sid:
            initial["supplier"] = sid
        return initial

    def form_valid(self, form):
        with transaction.atomic():
            obj = form.save(commit=False)
            obj.maker = self.request.user
            obj.submitted_at = timezone.now()
            obj.created_by = self.request.user
            obj.updated_by = self.request.user
            obj.save()
            try:
                obj.try_trusted_autoapprove()
            except TrustedBypassBlocked:
                pass
        messages.success(self.request, f"Supplier payment {obj.number} recorded.")
        return redirect(reverse("billing:supplier-payment-detail", args=[obj.pk]))


class SupplierPaymentDetailView(RoleRequiredMixin, DetailView):
    required_roles = FINANCE_ROLES
    template_name = "billing/supplier_payment_detail.html"
    context_object_name = "payment"

    def get_queryset(self):
        from .models import SupplierPayment
        return SupplierPayment.objects.select_related(
            "supplier", "bank_account", "related_house", "maker", "checker"
        )


# ---------------------------------------------------------------------------
# Receipt list (Phase E follow-up)
# ---------------------------------------------------------------------------
class ReceiptListView(RoleRequiredMixin, PaginatedListView):
    required_roles = FINANCE_ROLES + ("COLLECTIONS",)
    template_name = "billing/receipt_list.html"
    context_object_name = "receipts"

    def get_queryset(self):
        from .models import Receipt
        qs = (
            Receipt.objects.select_related(
                "payment", "payment__tenant", "payment__bank_account",
                "refund", "refund__tenant",
            )
            .order_by("-issued_at", "-id")
        )
        q = (self.request.GET.get("q") or "").strip()
        if q:
            qobj = (
                models.Q(number__icontains=q)
                | models.Q(payment__tenant__full_name__icontains=q)
                | models.Q(refund__tenant__full_name__icontains=q)
                | models.Q(payment__number__icontains=q)
                | models.Q(payment__reference_number__icontains=q)
            )
            qs = qs.filter(qobj)
        kind = self.request.GET.get("kind")
        if kind:
            qs = qs.filter(kind=kind)
        return qs

    def get(self, request, *args, **kwargs):
        if request.GET.get("export") == "csv":
            from core.utils import export_csv

            def _tenant(r):
                if r.payment and r.payment.tenant:
                    return r.payment.tenant.full_name
                if r.refund and getattr(r.refund, "tenant", None):
                    return r.refund.tenant.full_name
                return ""

            def _payment_ref(r):
                return r.payment.number if r.payment else ""

            columns = [
                ("Number", "number"),
                ("Kind", lambda r: r.get_kind_display()),
                ("Tenant", _tenant),
                ("Payment", _payment_ref),
                ("Amount (UGX)", "amount"),
                ("Issued at", "issued_at"),
            ]
            return export_csv(self.get_queryset(), columns, "receipts")
        return super().get(request, *args, **kwargs)


# ---------------------------------------------------------------------------
# Invoice Schedule list (Phase E follow-up)
# Read-only view of active tenancies + their invoice-generation status.
# ---------------------------------------------------------------------------
class InvoiceScheduleListView(RoleRequiredMixin, PaginatedListView):
    required_roles = FINANCE_ROLES + ("ACCOUNT_MANAGER",)
    template_name = "billing/invoice_schedule_list.html"
    context_object_name = "schedules"

    def get_queryset(self):
        qs = (
            TenantHouse.objects.select_related("tenant", "house", "house__estate")
            .order_by("house__estate__name", "house__house_number")
        )
        gen_status = self.request.GET.get("gen_status")
        if gen_status:
            qs = qs.filter(invoice_generation_status=gen_status)
        status = self.request.GET.get("status") or "ACTIVE"
        if status:
            qs = qs.filter(status=status)
        q = (self.request.GET.get("q") or "").strip()
        if q:
            qs = qs.filter(
                models.Q(tenant__full_name__icontains=q)
                | models.Q(house__house_number__icontains=q)
                | models.Q(house__name__icontains=q)
                | models.Q(house__estate__name__icontains=q)
            )
        return qs

    def get(self, request, *args, **kwargs):
        if request.GET.get("export") == "csv":
            from core.utils import export_csv
            columns = [
                ("Estate", "house.estate.name"),
                ("House", lambda t: str(t.house)),
                ("Tenant", "tenant.full_name"),
                ("Tenancy status", lambda t: t.get_status_display()),
                ("Invoice generation", lambda t: t.get_invoice_generation_status_display()),
                ("Move-in", "move_in_date"),
                ("Billing start", "billing_start_date"),
                ("Rent (UGX)", lambda t: t.house.periodic_rent),
                ("Note", "invoice_generation_note"),
            ]
            return export_csv(self.get_queryset(), columns, "invoice_schedules")
        return super().get(request, *args, **kwargs)


# ---------------------------------------------------------------------------
# Landlord statements index (picker)
# ---------------------------------------------------------------------------
class LandlordStatementIndexView(RoleRequiredMixin, PaginatedListView):
    required_roles = FINANCE_ROLES + ("ACCOUNT_MANAGER",)
    template_name = "billing/landlord_statement_index.html"
    context_object_name = "landlords"

    def get_queryset(self):
        from core.models import Landlord
        qs = Landlord.objects.order_by("full_name")
        q = (self.request.GET.get("q") or "").strip()
        if q:
            qs = qs.filter(
                models.Q(full_name__icontains=q)
                | models.Q(phone__icontains=q)
                | models.Q(email__icontains=q)
            )
        return qs


# ---------------------------------------------------------------------------
# Expense Claims (Phase E.3) — employees submit, maker-checker approves,
# signal posts Dr <expense account> / Cr <reimbursement bank>.
# ---------------------------------------------------------------------------
STAFF_ALL_ROLES = FINANCE_ROLES + ("ACCOUNT_MANAGER", "COLLECTIONS", "SALES_REP")


class ExpenseClaimListView(RoleRequiredMixin, PaginatedListView):
    required_roles = STAFF_ALL_ROLES
    template_name = "billing/expense_claim_list.html"
    context_object_name = "claims"
    paginate_by = 100
    allowed_page_sizes = [50, 100, 150, 200]

    def get_queryset(self):
        from .models import ExpenseClaim
        qs = (
            ExpenseClaim.objects.select_related("claimant", "reimbursement_bank", "related_house")
            .order_by("-incurred_at", "-id")
        )
        q = (self.request.GET.get("q") or "").strip()
        if q:
            qobj = (
                models.Q(number__icontains=q)
                | models.Q(description__icontains=q)
                | models.Q(claimant__full_name__icontains=q)
            )
            try:
                from decimal import Decimal as _D
                qobj |= models.Q(amount=_D(q.replace(",", "")))
            except Exception:
                pass
            qs = qs.filter(qobj)
        status = self.request.GET.get("status")
        if status:
            qs = qs.filter(approval_status=status)
        cat = self.request.GET.get("category")
        if cat:
            qs = qs.filter(category=cat)
        date_from = self.request.GET.get("from")
        date_to = self.request.GET.get("to")
        if date_from:
            qs = qs.filter(incurred_at__gte=date_from)
        if date_to:
            qs = qs.filter(incurred_at__lte=date_to)
        # Non-admins only see their own claims by default
        from accounts.permissions import has_any_role
        if not has_any_role(self.request.user, *FINANCE_ROLES):
            qs = qs.filter(claimant__user=self.request.user)
        return qs

    def get(self, request, *args, **kwargs):
        if request.GET.get("export") == "csv":
            from core.utils import export_csv
            cols = [
                ("Number", "number"),
                ("Claimant", "claimant.full_name"),
                ("Category", lambda c: c.get_category_display()),
                ("Description", "description"),
                ("Related house", lambda c: str(c.related_house) if c.related_house else ""),
                ("Incurred", "incurred_at"),
                ("Amount (UGX)", "amount"),
                ("Status", lambda c: c.get_approval_status_display()),
            ]
            return export_csv(self.get_queryset(), cols, "expense_claims")
        return super().get(request, *args, **kwargs)

    def get(self, request, *args, **kwargs):
        if request.GET.get("export") == "csv":
            from core.utils import export_csv
            columns = [
                ("Number", "number"),
                ("Claimant", "claimant.full_name"),
                ("Category", lambda c: c.get_category_display()),
                ("Description", "description"),
                ("Related house", lambda c: str(c.related_house) if c.related_house else ""),
                ("Amount (UGX)", "amount"),
                ("Incurred", "incurred_at"),
                ("Status", lambda c: c.get_approval_status_display()),
            ]
            return export_csv(self.get_queryset(), columns, "expense_claims")
        return super().get(request, *args, **kwargs)


class ExpenseClaimCreateView(RoleRequiredMixin, CreateView):
    required_roles = STAFF_ALL_ROLES
    template_name = "billing/expense_claim_form.html"

    def get_form_class(self):
        from .forms import ExpenseClaimForm
        return ExpenseClaimForm

    def get_initial(self):
        initial = super().get_initial()
        # Default claimant to the current user's Employee profile if any.
        try:
            emp = self.request.user.employee_profile
            initial["claimant"] = emp.pk
        except Exception:
            pass
        return initial

    def form_valid(self, form):
        with transaction.atomic():
            obj = form.save(commit=False)
            obj.maker = self.request.user
            obj.submitted_at = timezone.now()
            obj.created_by = self.request.user
            obj.updated_by = self.request.user
            obj.save()
            # Expenses never auto-approve (allow_trusted_bypass=False)
        messages.success(self.request, f"Expense claim {obj.number} submitted for approval.")
        return redirect(reverse("billing:expense-claim-detail", args=[obj.pk]))


class ExpenseClaimDetailView(RoleRequiredMixin, DetailView):
    required_roles = STAFF_ALL_ROLES
    template_name = "billing/expense_claim_detail.html"
    context_object_name = "claim"

    def get_queryset(self):
        from .models import ExpenseClaim
        return ExpenseClaim.objects.select_related(
            "claimant", "related_house", "reimbursement_bank", "maker", "checker",
            "source_journal",
        )


# ---------------------------------------------------------------------------
# Security deposit receipt — records the tenant's deposit as a held liability.
# ---------------------------------------------------------------------------
class RecordSecurityDepositView(RoleRequiredMixin, View):
    """Receive a security deposit from a tenant.

    NOT a Payment-allocate-to-Invoice flow. Books:
        Dr <bank.ledger_account>           (asset ↑)
        Cr 1500 Security Deposits Held     (liability ↑)
    Creates / updates a SecurityDeposit row keyed by TenantHouse and stamps
    the resulting JournalEntry onto `hold_journal`.
    """
    required_roles = FINANCE_ROLES + ("COLLECTIONS",)

    def get(self, request, pk):
        from .forms import SecurityDepositReceiveForm
        from core.models import TenantHouse
        th = get_object_or_404(TenantHouse, pk=pk)
        existing = getattr(th, "security_deposit_record", None)
        form = SecurityDepositReceiveForm(initial={
            "amount": th.security_deposit or 0,
            "received_at": timezone.localdate(),
        })
        from django.shortcuts import render
        return render(request, "billing/security_deposit_receive.html", {
            "tenancy": th,
            "form": form,
            "existing": existing,
        })

    @transaction.atomic
    def post(self, request, pk):
        from .forms import SecurityDepositReceiveForm
        from .models import SecurityDeposit, SecurityDepositMovement
        from accounting.models import Account, JournalEntry, JournalEntryLine
        from accounting.utils import SYS_SECURITY_DEPOSIT_HELD, get_account
        from .sequences import allocate_number
        from core.models import TenantHouse
        from decimal import Decimal as _D

        th = get_object_or_404(TenantHouse, pk=pk)
        form = SecurityDepositReceiveForm(request.POST)
        if not form.is_valid():
            from django.shortcuts import render
            return render(request, "billing/security_deposit_receive.html", {
                "tenancy": th,
                "form": form,
                "existing": getattr(th, "security_deposit_record", None),
            })
        amount = _D(form.cleaned_data["amount"])
        bank = form.cleaned_data["bank_account"]
        received_at = form.cleaned_data["received_at"]
        ref = form.cleaned_data.get("reference") or ""
        notes = form.cleaned_data.get("notes") or ""

        # Post the GL journal: Dr Bank / Cr Security Deposits Held
        held_account = get_account(SYS_SECURITY_DEPOSIT_HELD)
        if not bank.ledger_account_id or not bank.ledger_account.is_postable:
            messages.error(
                request,
                f"Bank account '{bank.name}' has no postable ledger account. "
                f"Fix it in Bank Accounts settings before recording deposits."
            )
            return redirect("core:tenant-detail", pk=th.tenant_id)
        entry = JournalEntry.objects.create(
            reference=allocate_number("JE"),
            entry_date=received_at,
            memo=f"Security deposit received from {th.tenant.full_name} ({th.house})",
            source=JournalEntry.Source.MANUAL,
            created_by=request.user,
        )
        JournalEntryLine.objects.create(
            entry=entry, account=bank.ledger_account,
            debit=amount, credit=_D("0"),
            description=f"Deposit ref {ref}" if ref else "Deposit receipt",
        )
        JournalEntryLine.objects.create(
            entry=entry, account=held_account,
            debit=_D("0"), credit=amount,
            description=f"Held deposit for {th.tenant.full_name}",
        )
        entry.post(user=request.user)

        # Upsert SecurityDeposit row
        deposit, _ = SecurityDeposit.objects.get_or_create(
            tenant_house=th,
            defaults={"amount_held": _D("0"), "hold_journal": entry,
                      "created_by": request.user, "updated_by": request.user},
        )
        deposit.amount_held = (deposit.amount_held or _D("0")) + amount
        deposit.hold_journal = deposit.hold_journal or entry
        deposit.recompute_status()
        deposit.updated_by = request.user
        deposit.save()
        SecurityDepositMovement.objects.create(
            deposit=deposit,
            kind="HOLD" if hasattr(SecurityDepositMovement.Kind, "HOLD") else SecurityDepositMovement.Kind.APPLY_DAMAGE,
            amount=amount,
            note=(f"Receipt {ref}. {notes}".strip()),
        ) if False else None  # movements typically track applies/refunds, not the initial hold

        # Mirror the live amount onto TenantHouse.security_deposit
        th.security_deposit = deposit.amount_held
        th.updated_by = request.user
        th.save(update_fields=["security_deposit", "updated_by", "updated_at"])

        messages.success(
            request,
            f"Security deposit UGX {amount:,} recorded — "
            f"booked to 1500 Security Deposits Held (liability), JE {entry.reference}."
        )
        return redirect("core:tenant-detail", pk=th.tenant_id)
