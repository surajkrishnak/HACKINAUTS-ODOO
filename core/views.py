from django.shortcuts import redirect, render
from reports.views import FinanceDashboardView


def home_dispatcher(request):
    """
    Intelligent root dispatcher:
    - Anonymous visitors see the marketing landing page.
    - Customers are directed straight to their Customer Portal.
    - Staff and Admins are served the Executive Finance Dashboard.
    """
    if not request.user.is_authenticated:
        return render(request, "base.html")

    if request.user.is_customer:
        return redirect("portal:subscription_list")

    return FinanceDashboardView.as_view()(request)
