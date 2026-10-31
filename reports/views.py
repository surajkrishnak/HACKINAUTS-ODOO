import csv
import datetime
import json
from decimal import Decimal
from django.http import HttpResponse
from django.shortcuts import render
from django.views import View

from accounts.permissions import RoleRequiredMixin
from billing.models import Invoice, Payment
from catalog.models import Plan
from reports.queries import get_dashboard_metrics, get_monthly_revenue_last_12_months
from subscriptions.models import Subscription


class FinanceDashboardView(RoleRequiredMixin, View):
    """
    Consolidated executive finance dashboard for Brightloop Software (Admin & Staff).
    Features high-level KPIs, 5-minute cached Chart.js analytics, recent payments,
    and overdue accounts with role-based revenue/profit controls.
    """

    allowed_roles = ["admin", "staff"]
    template_name = "reports/dashboard.html"

    def get(self, request):
        # Read filter parameters
        date_from_str = request.GET.get("date_from", "").strip()
        date_to_str = request.GET.get("date_to", "").strip()
        plan_id_str = request.GET.get("plan", "").strip()
        customer_q = request.GET.get("q", "").strip()

        date_from = None
        date_to = None
        plan_id = None

        if date_from_str:
            try:
                date_from = datetime.date.fromisoformat(date_from_str)
            except ValueError:
                pass
        if date_to_str:
            try:
                date_to = datetime.date.fromisoformat(date_to_str)
            except ValueError:
                pass
        if plan_id_str:
            try:
                plan_id = int(plan_id_str)
            except ValueError:
                pass

        # Fetch computed aggregate metrics
        metrics = get_dashboard_metrics(
            date_from=date_from,
            date_to=date_to,
            plan_id=plan_id,
            customer_q=customer_q,
        )

        # Fetch cached monthly revenue chart data (last 12 months)
        monthly_revenue_chart = get_monthly_revenue_last_12_months()

        # Format Invoice Status Breakdown for Chart.js doughnut
        inv_counts = metrics["invoice_counts"]
        invoice_status_chart = {
            "labels": ["Draft", "Confirmed", "Paid", "Cancelled"],
            "data": [
                inv_counts.get(Invoice.Status.DRAFT, 0),
                inv_counts.get(Invoice.Status.CONFIRMED, 0),
                inv_counts.get(Invoice.Status.PAID, 0),
                inv_counts.get(Invoice.Status.CANCELLED, 0),
            ],
            "colors": ["#6c757d", "#0d6efd", "#198754", "#212529"],
        }

        plans = Plan.objects.filter(is_active=True).order_by("name")

        context = {
            **metrics,
            "revenue_chart_json": json.dumps(monthly_revenue_chart),
            "invoice_chart_json": json.dumps(invoice_status_chart),
            "plans": plans,
            "selected_plan": plan_id,
            "date_from": date_from_str,
            "date_to": date_to_str,
            "customer_q": customer_q,
            "is_admin": request.user.is_admin,
        }

        return render(request, self.template_name, context)


class ExportReportCSVView(RoleRequiredMixin, View):
    """Generates and streams a CSV export of filtered subscriptions, invoices, or payments."""

    allowed_roles = ["admin", "staff"]

    def get(self, request):
        export_type = request.GET.get("type", "invoices")
        today_str = datetime.date.today().isoformat()

        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="brightloop_{export_type}_{today_str}.csv"'

        writer = csv.writer(response)

        if export_type == "payments":
            writer.writerow(["Payment ID", "Invoice Number", "Customer Email", "Method", "Amount (₹)", "Payment Date", "Reference"])
            payments = Payment.objects.select_related("invoice", "invoice__customer").order_by("-payment_date")
            for pmt in payments:
                writer.writerow([
                    pmt.id,
                    pmt.invoice.number,
                    pmt.invoice.customer.email,
                    pmt.get_method_display(),
                    pmt.amount,
                    pmt.payment_date,
                    pmt.note,
                ])

        elif export_type == "subscriptions":
            writer.writerow(["Subscription #", "Customer Email", "Plan", "Status", "Start Date", "Expiry Date", "Next Billing Date", "Total Recurring (₹)"])
            subs = Subscription.objects.select_related("customer", "plan").order_by("-id")
            for sub in subs:
                writer.writerow([
                    sub.number,
                    sub.customer.email,
                    sub.plan.name,
                    sub.get_status_display(),
                    sub.start_date,
                    sub.expiry_date or "",
                    sub.next_billing_date or "",
                    sub.total_amount,
                ])

        else:  # Invoices
            writer.writerow(["Invoice #", "Customer Email", "Subscription #", "Invoice Date", "Due Date", "Subtotal (₹)", "Tax (₹)", "Discount (₹)", "Total (₹)", "Amount Paid (₹)", "Balance (₹)", "Status"])
            invoices = Invoice.objects.select_related("customer", "subscription").order_by("-invoice_date")
            for inv in invoices:
                writer.writerow([
                    inv.number,
                    inv.customer.email,
                    inv.subscription.number,
                    inv.invoice_date,
                    inv.due_date,
                    inv.subtotal,
                    inv.tax_total,
                    inv.discount_total,
                    inv.total,
                    inv.amount_paid,
                    inv.balance,
                    inv.get_status_display(),
                ])

        return response
