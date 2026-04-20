"""Billing views — list/detail/CRUD for invoices, payments, voids, credit
notes, refunds; plus approvals queue. All guarded by RoleRequiredMixin
(SPEC §16.9)."""
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
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

    def get_queryset(self):
        qs = super().get_queryset().select_related("tenant_house", "tenant_house__tenant", "tenant_house__house")
        status = self.request.GET.get("status")
        if status:
            qs = qs.filter(status=status)
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(number__icontains=q) | qs.filter(tenant_house__tenant__full_name__icontains=q)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["statuses"] = Invoice.Status.choices
        ctx["q"] = self.request.GET.get("q", "")
        ctx["selected_status"] = self.request.GET.get("status", "")
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

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.updated_by = self.request.user
        form.instance.status = Invoice.Status.DRAFT
        messages.success(self.request, "Draft invoice saved.")
        return super().form_valid(form)

    def get_success_url(self):
        return reverse("billing:invoice-detail", args=[self.object.pk])


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

    def get_queryset(self):
        return super().get_queryset().select_related("tenant", "bank_account").order_by("-received_at")


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
APPROVAL_MODELS = {
    "payment": (Payment, apply_payment),
    "adhoc": (AdHocCharge, None),
    "void": (InvoiceVoid, execute_void),
    "credit": (CreditNote, execute_credit_note),
    "refund": (Refund, execute_refund),
}


class ApprovalsQueueView(RoleRequiredMixin, PaginatedListView):
    """Tabbed queue of pending items. Tabs: Payments/Ad-hoc/Voids/Credits/Refunds."""
    required_roles = APPROVER_ROLES + ("ACCOUNT_MANAGER",)
    template_name = "billing/approvals_queue.html"
    context_object_name = "items"

    def get(self, request, *args, **kwargs):
        self.tab = request.GET.get("tab", "payment")
        return super().get(request, *args, **kwargs)

    def get_queryset(self):
        model_cls, _ = APPROVAL_MODELS.get(self.tab, APPROVAL_MODELS["payment"])
        return model_cls.objects.filter(
            approval_status=ApprovalStatus.PENDING
        ).order_by("submitted_at")

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["active_tab"] = self.tab
        ctx["tabs"] = [
            ("payment", "Payments"), ("adhoc", "Ad-hoc"),
            ("void", "Voids"), ("credit", "Credit Notes"), ("refund", "Refunds"),
        ]
        now = timezone.now()
        ctx["now"] = now
        from datetime import timedelta
        ctx["overdue_threshold"] = now - timedelta(hours=24)
        return ctx


class ApprovalActionView(RoleRequiredMixin, View):
    required_roles = APPROVER_ROLES + ("ACCOUNT_MANAGER",)

    def post(self, request, kind, pk, action):
        if kind not in APPROVAL_MODELS:
            raise PermissionDenied("Unknown approval type")
        model_cls, effect_fn = APPROVAL_MODELS[kind]
        obj = get_object_or_404(model_cls, pk=pk)
        try:
            if action == "approve":
                obj.approve(request.user)
                if effect_fn is not None:
                    effect_fn(obj, user=request.user)
                messages.success(request, f"{kind.title()} approved.")
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
    routing badges (Managed vs Meili-Owned)."""
    required_roles = FINANCE_ROLES
    template_name = "billing/report_advances.html"
    context_object_name = "rows"

    def get_queryset(self):
        from .models import PaymentAllocation
        return (
            PaymentAllocation.objects.filter(is_advance_hold=True, applied_at__isnull=True)
            .select_related("payment", "payment__tenant")
            .order_by("-allocated_at")
        )


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

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        houses = self.object.houses.all() | self.object.estates.values_list("houses", flat=True).all()
        from .models import PaymentAllocation
        invoices = Invoice.objects.filter(
            tenant_house__house__estate__landlord=self.object,
        ) | Invoice.objects.filter(tenant_house__house__landlord=self.object)
        ctx["invoices"] = invoices.exclude(
            status__in=[Invoice.Status.VOIDED, Invoice.Status.CANCELLED]
        ).order_by("-issue_date")[:100]
        return ctx
