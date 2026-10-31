from django.urls import path
from reports import views

app_name = "reports"

urlpatterns = [
    path("", views.FinanceDashboardView.as_view(), name="dashboard"),
    path("export/", views.ExportReportCSVView.as_view(), name="export_csv"),
]
