"""Employee accounting views — COA list/detail, general ledger, journal entry
form with line formset, commission income drill-down."""

from datetime import date
from decimal import Decimal

from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Sum, Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.generic import CreateView, DetailView, UpdateView, View

from accounts.permissions import RoleRequiredMixin, role_required
from core.mixins import PaginatedListView

from .forms import BankAccountForm, JournalEntryForm, JournalEntryLineFormSet
from .models import Account, BankAccount, JournalEntry, JournalEntryLine
from .utils import SYS_COMMISSION_INCOME, get_account


FINANCE_ROLES = ("ADMIN", "SUPER_ADMIN", "FINANCE", "ACCOUNT_MANAGER")


# ---------------------------------------------------------------------------
# Chart of Accounts
# ---------------------------------------------------------------------------
class AccountListView(RoleRequiredMixin, PaginatedListView):
    model = Account
    template_name = "accounting/account_list.html"
    context_object_name = "accounts"
    required_roles = FINANCE_ROLES

    def get_queryset(self):
        qs = Account.objects.select_related("account_type", "parent").order_by("code")
        q = self.request.GET.get("q")
        if q:
            qs = qs.filter(Q(code__icontains=q) | Q(name__icontains=q))
        category = self.request.GET.get("category")
        if category:
            qs = qs.filter(account_type__category=category)
        return qs


class AccountDetailView(RoleRequiredMixin, DetailView):
    model = Account
    template_name = "accounting/account_detail.html"
    context_object_name = "account"
    required_roles = FINANCE_ROLES

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        account = self.object
        lines = (
            JournalEntryLine.objects.filter(
                account=account, entry__status=JournalEntry.Status.POSTED
            )
            .select_related("entry")
            .order_by("entry__posted_at", "id")
        )
        ctx["lines"] = lines
        ctx["balance"] = account.balance()
        ctx["children"] = account.children.order_by("code")
        return ctx


# ---------------------------------------------------------------------------
# General Ledger — all posted lines, filterable
# ---------------------------------------------------------------------------
class GeneralLedgerView(RoleRequiredMixin, PaginatedListView):
    model = JournalEntryLine
    template_name = "accounting/general_ledger.html"
    context_object_name = "lines"
    required_roles = FINANCE_ROLES

    def get_queryset(self):
        qs = (
            JournalEntryLine.objects.select_related(
                "entry", "account", "account__account_type"
            )
            .filter(entry__status=JournalEntry.Status.POSTED)
            .order_by("-entry__posted_at", "id")
        )
        account_code = self.request.GET.get("account")
        if account_code:
            qs = qs.filter(account__code=account_code)
        date_from = self.request.GET.get("from")
        date_to = self.request.GET.get("to")
        if date_from:
            qs = qs.filter(entry__entry_date__gte=date_from)
        if date_to:
            qs = qs.filter(entry__entry_date__lte=date_to)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        qs = self.get_queryset()
        totals = qs.aggregate(d=Sum("debit"), c=Sum("credit"))
        ctx["total_debit"] = totals["d"] or Decimal("0")
        ctx["total_credit"] = totals["c"] or Decimal("0")
        ctx["filter_account"] = self.request.GET.get("account", "")
        ctx["filter_from"] = self.request.GET.get("from", "")
        ctx["filter_to"] = self.request.GET.get("to", "")
        return ctx


# ---------------------------------------------------------------------------
# Journal Entry — create (formset) + post
# ---------------------------------------------------------------------------
@role_required(*FINANCE_ROLES)
def journal_entry_create(request):
    if request.method == "POST":
        form = JournalEntryForm(request.POST)
        formset = JournalEntryLineFormSet(request.POST, prefix="lines")
        if form.is_valid() and formset.is_valid():
            with transaction.atomic():
                entry = form.save(commit=False)
                entry.created_by = request.user
                entry.save()
                formset.instance = entry
                formset.save()
                if "post" in request.POST:
                    try:
                        entry.post(user=request.user)
                    except ValidationError as exc:
                        messages.error(request, "; ".join(exc.messages))
                        return redirect(reverse("accounting:journal-detail", args=[entry.pk]))
                    messages.success(request, f"Journal entry {entry.reference} posted.")
                else:
                    messages.info(request, "Draft saved — not yet posted.")
            return redirect(reverse("accounting:journal-detail", args=[entry.pk]))
    else:
        form = JournalEntryForm()
        formset = JournalEntryLineFormSet(prefix="lines")
    return render(
        request,
        "accounting/journal_entry_form.html",
        {"form": form, "formset": formset},
    )


@role_required(*FINANCE_ROLES)
def journal_entry_detail(request, pk):
    entry = get_object_or_404(JournalEntry, pk=pk)
    debit_total, credit_total = entry.totals()
    return render(
        request,
        "accounting/journal_entry_detail.html",
        {
            "entry": entry,
            "lines": entry.lines.select_related("account"),
            "debit_total": debit_total,
            "credit_total": credit_total,
            "is_balanced": entry.is_balanced(),
        },
    )


@role_required(*FINANCE_ROLES)
def journal_entry_post(request, pk):
    entry = get_object_or_404(JournalEntry, pk=pk)
    if request.method != "POST":
        return redirect(reverse("accounting:journal-detail", args=[entry.pk]))
    try:
        entry.post(user=request.user)
        messages.success(request, f"Journal entry {entry.reference} posted.")
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    return redirect(reverse("accounting:journal-detail", args=[entry.pk]))


# ---------------------------------------------------------------------------
# Commission Income Report — drill-down by period
# ---------------------------------------------------------------------------
@role_required(*FINANCE_ROLES)
def commission_income_report(request):
    account = get_account(SYS_COMMISSION_INCOME)
    today = date.today()
    date_from = request.GET.get("from") or today.replace(day=1).isoformat()
    date_to = request.GET.get("to") or today.isoformat()

    lines = (
        JournalEntryLine.objects.filter(
            account=account, entry__status=JournalEntry.Status.POSTED
        )
        .select_related("entry")
        .filter(entry__entry_date__gte=date_from, entry__entry_date__lte=date_to)
        .order_by("entry__entry_date")
    )
    totals = lines.aggregate(d=Sum("debit"), c=Sum("credit"))
    debits = totals["d"] or Decimal("0")
    credits = totals["c"] or Decimal("0")
    # Revenue = credit-normal; recognised amount = credits - debits (reversals).
    recognised = credits - debits
    return render(
        request,
        "accounting/commission_income_report.html",
        {
            "account": account,
            "lines": lines,
            "date_from": date_from,
            "date_to": date_to,
            "debits": debits,
            "credits": credits,
            "recognised": recognised,
        },
    )


# ---------------------------------------------------------------------------
# BankAccount CRUD
# ---------------------------------------------------------------------------
from django.contrib import messages as _msgs
from django.shortcuts import redirect as _redirect


class BankAccountListView(RoleRequiredMixin, PaginatedListView):
    required_roles = FINANCE_ROLES
    model = BankAccount
    template_name = "accounting/bankaccount_list.html"
    context_object_name = "bankaccounts"

    def get_queryset(self):
        return super().get_queryset().select_related("currency", "ledger_account")


class BankAccountDetailView(RoleRequiredMixin, DetailView):
    required_roles = FINANCE_ROLES
    model = BankAccount
    template_name = "accounting/bankaccount_detail.html"
    context_object_name = "bankaccount"


class BankAccountCreateView(RoleRequiredMixin, CreateView):
    required_roles = ("ADMIN", "SUPER_ADMIN", "FINANCE")
    model = BankAccount
    form_class = BankAccountForm
    template_name = "accounting/bankaccount_form.html"

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        form.instance.updated_by = self.request.user
        _msgs.success(self.request, "Bank account created.")
        return super().form_valid(form)


class BankAccountUpdateView(RoleRequiredMixin, UpdateView):
    required_roles = ("ADMIN", "SUPER_ADMIN", "FINANCE")
    model = BankAccount
    form_class = BankAccountForm
    template_name = "accounting/bankaccount_form.html"

    def form_valid(self, form):
        form.instance.updated_by = self.request.user
        _msgs.success(self.request, "Bank account updated.")
        return super().form_valid(form)


class BankAccountDeleteView(RoleRequiredMixin, View):
    required_roles = ("ADMIN", "SUPER_ADMIN")

    def post(self, request, pk):
        obj = get_object_or_404(BankAccount, pk=pk)
        obj.soft_delete(user=request.user)
        _msgs.success(request, "Bank account deleted.")
        return _redirect("accounting:bankaccount-list")
