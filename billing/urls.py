from django.urls import path

from . import reports, views

app_name = "billing"

urlpatterns = [
    # Invoices
    path("invoices/", views.InvoiceListView.as_view(), name="invoice-list"),
    path("invoices/new/", views.InvoiceCreateView.as_view(), name="invoice-create"),
    path("invoices/<int:pk>/", views.InvoiceDetailView.as_view(), name="invoice-detail"),
    path("invoices/<int:pk>/delete/", views.InvoiceDeleteView.as_view(), name="invoice-delete"),
    path("invoices/<int:pk>/mark-paid/", views.InvoiceMarkPaidView.as_view(), name="invoice-mark-paid"),
    path("invoices/<int:pk>/issue/", views.InvoiceIssueView.as_view(), name="invoice-issue"),
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
    path("receipts/", views.ReceiptListView.as_view(), name="receipt-list"),
    path("receipts/<int:pk>/", views.ReceiptDetailView.as_view(), name="receipt-detail"),

    # Invoice schedules (read-only overview)
    path("invoice-schedules/", views.InvoiceScheduleListView.as_view(), name="invoice-schedule-list"),

    # Landlord statements picker
    path("landlord-statements/", views.LandlordStatementIndexView.as_view(), name="landlord-statement-index"),

    # Exit workflow (SPEC §20.5)
    path("tenancies/<int:pk>/exit/", views.ExitWorkflowView.as_view(), name="exit-workflow"),

    # Reports
    path("reports/advances/", views.AdvancePaymentsReportView.as_view(), name="report-advances"),
    path("reports/repairs/", reports.RepairsPerHouseReport.as_view(), name="report-repairs"),
    path("reports/estate-costs/", reports.EstateCostReport.as_view(), name="report-estate-costs"),
    path("reports/collections/", reports.CollectionPerformanceReport.as_view(), name="report-collections"),
    path("reports/acquisition/", reports.TenantAcquisitionReport.as_view(), name="report-acquisition"),
    path("reports/occupancy/", reports.OccupancyReport.as_view(), name="report-occupancy"),
    path("reports/revenue/", reports.RevenueSummaryReport.as_view(), name="report-revenue"),
    path("tenancies/<int:pk>/statement/", views.TenantStatementView.as_view(), name="tenant-statement"),
    path("landlords/<int:pk>/statement/", views.LandlordStatementView.as_view(), name="landlord-statement"),

    # Landlord payouts (Phase E)
    path("landlord-payouts/", views.LandlordPayoutListView.as_view(), name="landlord-payout-list"),
    path("landlord-payouts/new/", views.LandlordPayoutCreateView.as_view(), name="landlord-payout-create"),
    path("landlord-payouts/<int:pk>/", views.LandlordPayoutDetailView.as_view(), name="landlord-payout-detail"),

    # Supplier payments (Phase E)
    path("supplier-payments/", views.SupplierPaymentListView.as_view(), name="supplier-payment-list"),
    path("supplier-payments/new/", views.SupplierPaymentCreateView.as_view(), name="supplier-payment-create"),
    path("supplier-payments/<int:pk>/", views.SupplierPaymentDetailView.as_view(), name="supplier-payment-detail"),

    # Expense claims (Phase E.3)
    path("expense-claims/", views.ExpenseClaimListView.as_view(), name="expense-claim-list"),
    path("expense-claims/new/", views.ExpenseClaimCreateView.as_view(), name="expense-claim-create"),
    path("expense-claims/<int:pk>/", views.ExpenseClaimDetailView.as_view(), name="expense-claim-detail"),
]
