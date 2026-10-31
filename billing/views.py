import datetime
from decimal import Decimal
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import F, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views import View
from django.views.generic import DetailView, ListView

from accounts.permissions import RoleRequiredMixin
from billing.forms import PaymentForm
from billing.models import Invoice, Payment
from billing.services import (
    cancel_invoice,
    confirm_invoice,
    generate_invoice,
    record_payment,
    send_invoice_email,
)
from subscriptions.models import Subscription


class InvoiceListView(RoleRequiredMixin, ListView):
    """
    List of invoices for Staff and Admin with search, status filters,
    date range filters, and overdue flag.
    """

    allowed_roles = ["admin", "staff"]
    model = Invoice
    template_name = "billing/invoice_list.html"
    context_object_name = "invoices"
    paginate_by = 20

    def get_queryset(self):
        qs = (
            Invoice.objects.select_related("customer", "subscription", "subscription__plan")
            .prefetch_related("payments")
            .order_by("-invoice_date", "-id")
        )

        # Status filter
        status = self.request.GET.get("status")
        if status in Invoice.Status.values:
            qs = qs.filter(status=status)

        # Overdue filter
        if self.request.GET.get("overdue") == "1":
            today = timezone.localdate()
            qs = qs.filter(
                status=Invoice.Status.CONFIRMED,
                due_date__lt=today,
                amount_paid__lt=F("total"),
            )

        # Date range filters
        date_from = self.request.GET.get("date_from")
        date_to = self.request.GET.get("date_to")
        if date_from:
            try:
                qs = qs.filter(invoice_date__gte=datetime.date.fromisoformat(date_from))
            except ValueError:
                pass
        if date_to:
            try:
                qs = qs.filter(invoice_date__lte=datetime.date.fromisoformat(date_to))
            except ValueError:
                pass

        # Text search (customer email, customer name, invoice number)
        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(number__icontains=q)
                | Q(customer__email__icontains=q)
                | Q(customer__first_name__icontains=q)
                | Q(customer__last_name__icontains=q)
            )

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["status_choices"] = Invoice.Status.choices
        ctx["selected_status"] = self.request.GET.get("status", "")
        ctx["is_overdue_filter"] = self.request.GET.get("overdue") == "1"
        ctx["date_from"] = self.request.GET.get("date_from", "")
        ctx["date_to"] = self.request.GET.get("date_to", "")
        ctx["search_query"] = self.request.GET.get("q", "")

        # Summary KPIs
        today = timezone.localdate()
        ctx["overdue_count"] = Invoice.objects.filter(
            status=Invoice.Status.CONFIRMED,
            due_date__lt=today,
            amount_paid__lt=F("total"),
        ).count()
        ctx["total_outstanding"] = (
            Invoice.objects.filter(status=Invoice.Status.CONFIRMED)
            .aggregate(
                unpaid=Sum(F("total") - F("amount_paid"))
            )["unpaid"]
            or Decimal("0.00")
        )
        return ctx


class InvoiceDetailView(DetailView):
    """
    Detailed view of an invoice showing line items, taxes, discounts,
    payments, and payment recording form.
    Accessible by Admin, Staff, or the Customer who owns this invoice.
    """

    model = Invoice
    template_name = "billing/invoice_detail.html"
    context_object_name = "invoice"

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        user = self.request.user
        if not user.is_authenticated:
            raise PermissionDenied("Authentication required.")
        # If user is a customer, verify ownership
        if user.is_customer and obj.customer_id != user.id:
            raise PermissionDenied("You can only view your own invoices.")
        return obj

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        invoice = self.object
        ctx["lines"] = invoice.lines.all()
        ctx["payments"] = invoice.payments.select_related("recorded_by").all()
        ctx["discount_apps"] = invoice.discount_applications.select_related("discount").all()

        # Provide payment form if payable and user is staff/admin
        if invoice.can_pay and (self.request.user.is_staff_member or self.request.user.is_admin):
            ctx["payment_form"] = PaymentForm(invoice=invoice)
        else:
            ctx["payment_form"] = None

        return ctx


class InvoicePrintView(DetailView):
    """
    Dedicated clean, printable invoice layout without navigation chrome,
    optimized for print and PDF generation.
    """

    model = Invoice
    template_name = "billing/invoice_print.html"
    context_object_name = "invoice"

    def get_object(self, queryset=None):
        obj = super().get_object(queryset)
        user = self.request.user
        if not user.is_authenticated:
            raise PermissionDenied("Authentication required.")
        if user.is_customer and obj.customer_id != user.id:
            raise PermissionDenied("You can only view your own invoices.")
        return obj

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["lines"] = self.object.lines.all()
        ctx["payments"] = self.object.payments.all()
        return ctx


class InvoiceActionView(RoleRequiredMixin, View):
    """Handles POST actions: confirm, cancel, and send_email."""

    allowed_roles = ["admin", "staff"]

    def post(self, request, pk):
        invoice = get_object_or_404(Invoice, pk=pk)
        action = request.POST.get("action")

        try:
            if action == "confirm":
                confirm_invoice(invoice, user=request.user)
                messages.success(request, f"Invoice {invoice.number} has been confirmed.")
            elif action == "cancel":
                cancel_invoice(invoice, user=request.user)
                messages.warning(request, f"Invoice {invoice.number} has been cancelled and discounts restored.")
            elif action == "send_email":
                send_invoice_email(invoice, request=request)
                messages.success(request, f"Invoice {invoice.number} sent to {invoice.customer.email}.")
            else:
                messages.error(request, f"Invalid action: {action}.")
        except ValidationError as exc:
            messages.error(request, str(exc.message if hasattr(exc, "message") else exc))
        except Exception as exc:
            messages.error(request, f"Error processing action: {exc}")

        return redirect("billing:invoice_detail", pk=invoice.pk)


class RecordPaymentView(RoleRequiredMixin, View):
    """Records a payment against a confirmed invoice."""

    allowed_roles = ["admin", "staff"]

    def post(self, request, pk):
        invoice = get_object_or_404(Invoice, pk=pk)
        form = PaymentForm(request.POST, invoice=invoice)

        if form.is_valid():
            try:
                payment = record_payment(
                    invoice=invoice,
                    method=form.cleaned_data["method"],
                    amount=form.cleaned_data["amount"],
                    payment_date=form.cleaned_data["payment_date"],
                    note=form.cleaned_data.get("note", ""),
                    user=request.user,
                )
                messages.success(
                    request,
                    f"Payment of ₹{payment.amount} recorded successfully ({payment.get_method_display()}). "
                    f"Remaining balance: ₹{invoice.balance}.",
                )
            except ValidationError as exc:
                messages.error(request, str(exc.message if hasattr(exc, "message") else exc))
        else:
            for field, errors in form.errors.items():
                for error in errors:
                    messages.error(request, f"{field.title()}: {error}")

        return redirect("billing:invoice_detail", pk=invoice.pk)


class PaymentListView(RoleRequiredMixin, ListView):
    """Audit log list of all payments with method, customer search, and date filters."""

    allowed_roles = ["admin", "staff"]
    model = Payment
    template_name = "billing/payment_list.html"
    context_object_name = "payments"
    paginate_by = 20

    def get_queryset(self):
        qs = (
            Payment.objects.select_related("invoice", "invoice__customer", "recorded_by")
            .order_by("-payment_date", "-id")
        )

        method = self.request.GET.get("method")
        if method in Payment.Method.values:
            qs = qs.filter(method=method)

        date_from = self.request.GET.get("date_from")
        date_to = self.request.GET.get("date_to")
        if date_from:
            try:
                qs = qs.filter(payment_date__gte=datetime.date.fromisoformat(date_from))
            except ValueError:
                pass
        if date_to:
            try:
                qs = qs.filter(payment_date__lte=datetime.date.fromisoformat(date_to))
            except ValueError:
                pass

        q = self.request.GET.get("q", "").strip()
        if q:
            qs = qs.filter(
                Q(invoice__number__icontains=q)
                | Q(invoice__customer__email__icontains=q)
                | Q(note__icontains=q)
            )

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["method_choices"] = Payment.Method.choices
        ctx["selected_method"] = self.request.GET.get("method", "")
        ctx["date_from"] = self.request.GET.get("date_from", "")
        ctx["date_to"] = self.request.GET.get("date_to", "")
        ctx["search_query"] = self.request.GET.get("q", "")
        ctx["total_payments_sum"] = (
            self.get_queryset().aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
        )
        return ctx


class OverdueInvoiceListView(RoleRequiredMixin, ListView):
    """
    Dedicated view for all overdue invoices ordered by oldest due date first.
    """

    allowed_roles = ["admin", "staff"]
    model = Invoice
    template_name = "billing/overdue_list.html"
    context_object_name = "overdue_invoices"
    paginate_by = 20

    def get_queryset(self):
        today = timezone.localdate()
        return (
            Invoice.objects.filter(
                status=Invoice.Status.CONFIRMED,
                due_date__lt=today,
                amount_paid__lt=F("total"),
            )
            .select_related("customer", "subscription", "subscription__plan")
            .order_by("due_date", "id")
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        today = timezone.localdate()
        qs = self.get_queryset()
        ctx["total_overdue_count"] = qs.count()
        ctx["total_overdue_balance"] = (
            qs.aggregate(unpaid=Sum(F("total") - F("amount_paid")))["unpaid"] or Decimal("0.00")
        )
        return ctx


class RunBillingJobView(RoleRequiredMixin, View):
    """Admin / Staff trigger to execute the automated billing run for today on demand."""

    allowed_roles = ["admin", "staff"]

    def post(self, request):
        today = timezone.localdate()
        active_subs = (
            Subscription.objects.filter(
                status=Subscription.Status.ACTIVE,
                is_paused=False,
                next_billing_date__lte=today,
            )
            .select_related("plan")
            .order_by("id")
        )

        created_count = 0
        skipped_count = 0
        failed_count = 0

        for sub in active_subs:
            try:
                with transaction.atomic():
                    while sub.next_billing_date and sub.next_billing_date <= today:
                        start_date = sub.next_billing_date
                        inv = generate_invoice(sub, period_start=start_date)
                        if inv:
                            created_count += 1
                            sub.refresh_from_db(fields=["next_billing_date"])
                        else:
                            skipped_count += 1
                            plan = sub.plan
                            from core.utils import add_period

                            sub.next_billing_date = add_period(start_date, plan.billing_period)
                            sub.save(update_fields=["next_billing_date"])
            except Exception:
                failed_count += 1

        messages.info(
            request,
            f"Billing run complete for {today}: "
            f"{created_count} invoice(s) generated, {skipped_count} skipped, {failed_count} failed.",
        )
        return redirect("billing:invoice_list")


class GenerateSubscriptionInvoiceView(RoleRequiredMixin, View):
    """Manually generates the next invoice for a specific active subscription."""

    allowed_roles = ["admin", "staff"]

    def post(self, request, pk):
        subscription = get_object_or_404(Subscription, pk=pk)

        if subscription.status != Subscription.Status.ACTIVE:
            messages.error(request, "Invoices can only be generated for ACTIVE subscriptions.")
            return redirect("subscriptions:subscription_detail", pk=subscription.pk)

        if subscription.is_paused:
            messages.error(request, "Cannot generate invoice: subscription is currently paused.")
            return redirect("subscriptions:subscription_detail", pk=subscription.pk)

        invoice = generate_invoice(subscription, period_start=subscription.next_billing_date)
        if invoice:
            messages.success(
                request,
                f"Generated invoice {invoice.number} ({invoice.period_start} to {invoice.period_end}) for ₹{invoice.total}.",
            )
            return redirect("billing:invoice_detail", pk=invoice.pk)
        else:
            messages.warning(
                request,
                f"No invoice generated. An active invoice already exists for period starting {subscription.next_billing_date}.",
            )
            return redirect("subscriptions:subscription_detail", pk=subscription.pk)
