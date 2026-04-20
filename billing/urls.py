from django.urls import path

from . import views

app_name = "billing"

urlpatterns = [
    # Invoices
    path("invoices/", views.InvoiceListView.as_view(), name="invoice-list"),
    path("invoices/new/", views.InvoiceCreateView.as_view(), name="invoice-create"),
    path("invoices/<int:pk>/", views.InvoiceDetailView.as_view(), name="invoice-detail"),
    path("invoices/<int:pk>/delete/", views.InvoiceDeleteView.as_view(), name="invoice-delete"),
    path("tenancies/<int:pk>/pause/", views.InvoicePauseView.as_view(), name="invoice-pause"),

    # Payments
    path("payments/", views.PaymentListView.as_view(), name="payment-list"),
    path("payments/new/", views.PaymentCreateView.as_view(), name="payment-create"),
    path("payments/<int:pk>/", views.PaymentDetailView.as_view(), name="payment-detail"),

    # Ad-hoc charges
    path("adhoc/", views.AdHocChargeListView.as_view(), name="adhoc-list"),
    path("adhoc/new/", views.AdHocChargeCreateView.as_view(), name="adhoc-create"),

    # Voids / Credit Notes / Refunds
    path("voids/new/", views.InvoiceVoidCreateView.as_view(), name="void-create"),
    path("credit-notes/new/", views.CreditNoteCreateView.as_view(), name="credit-note-create"),
    path("refunds/new/", views.RefundCreateView.as_view(), name="refund-create"),

    # Approvals
    path("approvals/", views.ApprovalsQueueView.as_view(), name="approvals"),
    path("approvals/<str:kind>/<int:pk>/<str:action>/",
         views.ApprovalActionView.as_view(), name="approval-action"),

    # Receipts
    path("receipts/<int:pk>/", views.ReceiptDetailView.as_view(), name="receipt-detail"),

    # Reports
    path("reports/advances/", views.AdvancePaymentsReportView.as_view(), name="report-advances"),
    path("tenancies/<int:pk>/statement/", views.TenantStatementView.as_view(), name="tenant-statement"),
    path("landlords/<int:pk>/statement/", views.LandlordStatementView.as_view(), name="landlord-statement"),
]
