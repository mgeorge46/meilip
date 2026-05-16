from django.urls import path

from . import views

app_name = "accounting"

urlpatterns = [
    path("accounts/", views.AccountListView.as_view(), name="account-list"),
    path("accounts/new/", views.AccountCreateView.as_view(), name="account-create"),
    path("accounts/<int:pk>/", views.AccountDetailView.as_view(), name="account-detail"),
    path("accounts/<int:pk>/edit/", views.AccountUpdateView.as_view(), name="account-update"),
    path("ledger/", views.GeneralLedgerView.as_view(), name="general-ledger"),
    path("journals/new/", views.journal_entry_create, name="journal-create"),
    path("journals/<int:pk>/", views.journal_entry_detail, name="journal-detail"),
    path("journals/<int:pk>/post/", views.journal_entry_post, name="journal-post"),
    path("reports/commission/", views.commission_income_report, name="commission-report"),
    path("reports/trial-balance/", views.trial_balance, name="trial-balance"),

    # Bank accounts
    path("bank-accounts/", views.BankAccountListView.as_view(), name="bankaccount-list"),
    path("bank-accounts/new/", views.BankAccountCreateView.as_view(), name="bankaccount-create"),
    path("bank-accounts/<int:pk>/", views.BankAccountDetailView.as_view(), name="bankaccount-detail"),
    path("bank-accounts/<int:pk>/edit/", views.BankAccountUpdateView.as_view(), name="bankaccount-update"),
    path("bank-accounts/<int:pk>/delete/", views.BankAccountDeleteView.as_view(), name="bankaccount-delete"),

    # Internal transfers
    path("transfers/", views.InternalTransferListView.as_view(), name="transfer-list"),
    path("transfers/new/", views.InternalTransferCreateView.as_view(), name="transfer-create"),
    path("transfers/<int:pk>/", views.InternalTransferDetailView.as_view(), name="transfer-detail"),
    path("transfers/<int:pk>/approve/", views.internal_transfer_approve, name="transfer-approve"),
    path("transfers/<int:pk>/reject/", views.internal_transfer_reject, name="transfer-reject"),
]
