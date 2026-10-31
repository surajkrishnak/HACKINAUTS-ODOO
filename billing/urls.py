from django.urls import path
from billing import views

app_name = "billing"

urlpatterns = [
    path("invoices/", views.InvoiceListView.as_view(), name="invoice_list"),
    path("invoices/overdue/", views.OverdueInvoiceListView.as_view(), name="overdue_list"),
    path("invoices/<int:pk>/", views.InvoiceDetailView.as_view(), name="invoice_detail"),
    path("invoices/<int:pk>/print/", views.InvoicePrintView.as_view(), name="invoice_print"),
    path("invoices/<int:pk>/action/", views.InvoiceActionView.as_view(), name="invoice_action"),
    path("invoices/<int:pk>/pay/", views.RecordPaymentView.as_view(), name="record_payment"),
    path("invoices/run-job/", views.RunBillingJobView.as_view(), name="run_job"),
    path("payments/", views.PaymentListView.as_view(), name="payment_list"),
    path(
        "subscriptions/<int:pk>/generate-invoice/",
        views.GenerateSubscriptionInvoiceView.as_view(),
        name="generate_subscription_invoice",
    ),
]
