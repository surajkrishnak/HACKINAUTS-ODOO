from django.urls import path
from subscriptions import views

app_name = "subscriptions"

urlpatterns = [
    # Quotation Templates
    path("templates/", views.QuotationTemplateListView.as_view(), name="template_list"),
    path("templates/create/", views.QuotationTemplateCreateView.as_view(), name="template_create"),
    path("templates/<int:pk>/edit/", views.QuotationTemplateUpdateView.as_view(), name="template_edit"),

    # Subscription Creation
    path("from-template/", views.SubscriptionFromTemplateView.as_view(), name="subscription_from_template"),
    path("create/", views.SubscriptionCreateScratchView.as_view(), name="subscription_create"),

    # Subscription Management
    path("", views.SubscriptionListView.as_view(), name="subscription_list"),
    path("<int:pk>/", views.SubscriptionDetailView.as_view(), name="subscription_detail"),
    path("<int:pk>/edit-lines/", views.SubscriptionEditLinesView.as_view(), name="subscription_edit_lines"),
    path("<int:pk>/transition/<str:action>/", views.SubscriptionTransitionView.as_view(), name="subscription_transition"),
]
