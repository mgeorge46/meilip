"""Tenant portal URL namespace — mounted at /tenant/."""
from django.urls import path

from . import views

app_name = "tenant"

urlpatterns = [
    path("", views.TenantDashboardView.as_view(), name="dashboard"),
    path("invoices/", views.TenantInvoiceListView.as_view(), name="invoice-list"),
    path("invoices/<int:pk>/", views.TenantInvoiceDetailView.as_view(), name="invoice-detail"),
    path("payments/", views.TenantPaymentListView.as_view(), name="payment-list"),
    path("receipts/", views.TenantReceiptListView.as_view(), name="receipt-list"),
    path("profile/", views.TenantProfileView.as_view(), name="profile"),
]
