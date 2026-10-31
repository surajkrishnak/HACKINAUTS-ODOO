from django.urls import path
from portal import views

app_name = "portal"

urlpatterns = [
    # Customer Subscriptions
    path("subscriptions/", views.CustomerSubscriptionListView.as_view(), name="subscription_list"),
    path("subscriptions/<int:pk>/", views.CustomerSubscriptionDetailView.as_view(), name="subscription_detail"),
    path("subscriptions/<int:pk>/action/", views.CustomerSubscriptionActionView.as_view(), name="subscription_action"),

    # Customer Invoices
    path("invoices/", views.CustomerInvoiceListView.as_view(), name="invoice_list"),
    path("invoices/<int:pk>/", views.CustomerInvoiceDetailView.as_view(), name="invoice_detail"),

    # Customer Discount Requests
    path("discount-request/", views.CustomerDiscountRequestView.as_view(), name="discount_request"),

    # Admin Management for Discount Requests
    path("admin/discount-requests/", views.AdminDiscountRequestListView.as_view(), name="admin_discount_requests"),
    path("admin/discount-requests/<int:pk>/action/", views.AdminDiscountRequestActionView.as_view(), name="admin_discount_request_action"),

    # Dashboard shortcut
    path("", views.CustomerSubscriptionListView.as_view(), name="dashboard"),
]
