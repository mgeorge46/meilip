from django.urls import path

from . import views

app_name = "accounting"

urlpatterns = [
    path("accounts/", views.AccountListView.as_view(), name="account-list"),
    path("accounts/<int:pk>/", views.AccountDetailView.as_view(), name="account-detail"),
    path("ledger/", views.GeneralLedgerView.as_view(), name="general-ledger"),
    path("journals/new/", views.journal_entry_create, name="journal-create"),
    path("journals/<int:pk>/", views.journal_entry_detail, name="journal-detail"),
    path("journals/<int:pk>/post/", views.journal_entry_post, name="journal-post"),
    path("reports/commission/", views.commission_income_report, name="commission-report"),
]
