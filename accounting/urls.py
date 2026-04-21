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

    # Bank accounts
    path("bank-accounts/", views.BankAccountListView.as_view(), name="bankaccount-list"),
    path("bank-accounts/new/", views.BankAccountCreateView.as_view(), name="bankaccount-create"),
    path("bank-accounts/<int:pk>/", views.BankAccountDetailView.as_view(), name="bankaccount-detail"),
    path("bank-accounts/<int:pk>/edit/", views.BankAccountUpdateView.as_view(), name="bankaccount-update"),
    path("bank-accounts/<int:pk>/delete/", views.BankAccountDeleteView.as_view(), name="bankaccount-delete"),
]
