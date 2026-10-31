from django.contrib import admin
from django.urls import include, path
from core.views import home_dispatcher

urlpatterns = [
    path("admin/", admin.site.urls),
    path("accounts/", include("accounts.urls")),
    path("catalog/", include("catalog.urls")),
    path("subscriptions/", include("subscriptions.urls")),
    path("billing/", include("billing.urls")),
    path("portal/", include("portal.urls")),
    path("reports/", include("reports.urls")),
    path("", home_dispatcher, name="home"),
]
