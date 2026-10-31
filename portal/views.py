from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import CreateView, DetailView, ListView

from accounts.permissions import RoleRequiredMixin
from billing.models import Invoice
from portal.forms import DiscountRequestForm
from portal.models import DiscountRequest
from subscriptions.models import Subscription
from subscriptions.services import (
    pause_subscription,
    resume_subscription,
    transition,
)


class CustomerSubscriptionListView(RoleRequiredMixin, ListView):
    """List of subscriptions owned exclusively by the logged-in customer."""

    allowed_roles = ["customer"]
    model = Subscription
    template_name = "portal/subscription_list.html"
    context_object_name = "subscriptions"
    paginate_by = 20

    def get_queryset(self):
        return (
            Subscription.objects.filter(customer=self.request.user)
            .select_related("plan")
            .prefetch_related("lines__product")
            .order_by("-created_at")
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        qs = self.get_queryset()
        ctx["active_count"] = qs.filter(status=Subscription.Status.ACTIVE).count()
        ctx["total_recurring"] = sum(
            sub.total_amount for sub in qs.filter(status=Subscription.Status.ACTIVE)
        )
        return ctx


class CustomerSubscriptionDetailView(RoleRequiredMixin, DetailView):
    """
    Detailed view of customer's own subscription.
    Returns 404 if accessed with an ID not belonging to the logged-in customer.
    """

    allowed_roles = ["customer"]
    model = Subscription
    template_name = "portal/subscription_detail.html"
    context_object_name = "subscription"

    def get_object(self, queryset=None):
        pk = self.kwargs.get(self.pk_url_kwarg)
        # Strictly return 404 if subscription doesn't belong to current customer
        return get_object_or_404(
            Subscription.objects.select_related("plan", "template").prefetch_related(
                "lines__product", "lines__variant"
            ),
            pk=pk,
            customer=self.request.user,
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        sub = self.object
        ctx["lines"] = sub.lines.select_related("product", "variant").all()
        ctx["invoices"] = sub.invoices.filter(customer=self.request.user).order_by("-invoice_date")
        ctx["can_close"] = sub.plan.closable and sub.status == Subscription.Status.ACTIVE
        ctx["can_pause"] = sub.plan.pausable and sub.status == Subscription.Status.ACTIVE and not sub.is_paused
        ctx["can_resume"] = sub.plan.pausable and sub.status == Subscription.Status.ACTIVE and sub.is_paused
        return ctx


class CustomerSubscriptionActionView(RoleRequiredMixin, View):
    """
    Allows a customer to close, pause, or resume their own active subscription
    if permitted by plan rules.
    """

    allowed_roles = ["customer"]

    def post(self, request, pk):
        sub = get_object_or_404(
            Subscription.objects.select_related("plan"),
            pk=pk,
            customer=request.user,
        )
        action = request.POST.get("action")

        try:
            if action == "close":
                if not sub.plan.closable:
                    messages.error(request, f"Plan '{sub.plan.name}' cannot be closed early.")
                elif sub.status != Subscription.Status.ACTIVE:
                    messages.error(request, "Only active subscriptions can be closed.")
                else:
                    transition(sub, Subscription.Status.CLOSED, user=request.user)
                    messages.success(request, f"Subscription {sub.number} has been closed.")

            elif action == "pause":
                if not sub.plan.pausable:
                    messages.error(request, f"Plan '{sub.plan.name}' cannot be paused.")
                else:
                    pause_subscription(sub, user=request.user)
                    messages.warning(request, f"Subscription {sub.number} is now paused.")

            elif action == "resume":
                if not sub.plan.pausable:
                    messages.error(request, f"Plan '{sub.plan.name}' cannot be paused or resumed.")
                else:
                    resume_subscription(sub, user=request.user)
                    messages.success(request, f"Subscription {sub.number} has been resumed.")

            else:
                messages.error(request, f"Unrecognized action: {action}")

        except ValidationError as exc:
            messages.error(request, str(exc.message if hasattr(exc, "message") else exc))
        except Exception as exc:
            messages.error(request, f"Error updating subscription: {exc}")

        return redirect("portal:subscription_detail", pk=sub.pk)


class CustomerInvoiceListView(RoleRequiredMixin, ListView):
    """List of invoices issued to the logged-in customer."""

    allowed_roles = ["customer"]
    model = Invoice
    template_name = "portal/invoice_list.html"
    context_object_name = "invoices"
    paginate_by = 20

    def get_queryset(self):
        return (
            Invoice.objects.filter(customer=self.request.user)
            .select_related("subscription", "subscription__plan")
            .order_by("-invoice_date", "-id")
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        qs = self.get_queryset()
        ctx["total_due"] = sum(inv.balance for inv in qs if inv.balance > 0)
        ctx["unpaid_count"] = sum(1 for inv in qs if inv.balance > 0 and inv.status == Invoice.Status.CONFIRMED)
        return ctx


class CustomerInvoiceDetailView(RoleRequiredMixin, DetailView):
    """
    Detailed invoice view for the customer.
    Returns 404 if accessed with an ID not belonging to the logged-in customer.
    """

    allowed_roles = ["customer"]
    model = Invoice
    template_name = "portal/invoice_detail.html"
    context_object_name = "invoice"

    def get_object(self, queryset=None):
        pk = self.kwargs.get(self.pk_url_kwarg)
        return get_object_or_404(
            Invoice.objects.select_related("subscription", "subscription__plan"),
            pk=pk,
            customer=self.request.user,
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        invoice = self.object
        ctx["lines"] = invoice.lines.all()
        ctx["payments"] = invoice.payments.all()
        return ctx


class CustomerDiscountRequestView(RoleRequiredMixin, View):
    """Allows customers to request bulk seat discounts and view their past requests."""

    allowed_roles = ["customer"]
    template_name = "portal/discount_request_form.html"

    def get(self, request):
        form = DiscountRequestForm(user=request.user)
        requests_history = (
            DiscountRequest.objects.filter(customer=request.user)
            .select_related("subscription", "subscription__plan")
            .order_by("-created_at")
        )
        return render(
            request,
            self.template_name,
            {"form": form, "requests_history": requests_history},
        )

    def post(self, request):
        form = DiscountRequestForm(request.POST, user=request.user)
        if form.is_valid():
            req_obj = form.save(commit=False)
            req_obj.customer = request.user
            req_obj.save()
            messages.success(
                request,
                f"Your request for a bulk discount on {req_obj.subscription.number} ({req_obj.seats} seats) has been submitted. Our team will review it shortly.",
            )
            return redirect("portal:discount_request")

        requests_history = (
            DiscountRequest.objects.filter(customer=request.user)
            .select_related("subscription", "subscription__plan")
            .order_by("-created_at")
        )
        return render(
            request,
            self.template_name,
            {"form": form, "requests_history": requests_history},
        )


class AdminDiscountRequestListView(RoleRequiredMixin, ListView):
    """Admin and Staff view to manage, review, and act on bulk discount requests."""

    allowed_roles = ["admin", "staff"]
    model = DiscountRequest
    template_name = "portal/admin_discount_request_list.html"
    context_object_name = "discount_requests"
    paginate_by = 20

    def get_queryset(self):
        qs = DiscountRequest.objects.select_related(
            "customer", "subscription", "subscription__plan"
        ).order_by("-created_at")
        status = self.request.GET.get("status")
        if status in DiscountRequest.Status.values:
            qs = qs.filter(status=status)
        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["status_choices"] = DiscountRequest.Status.choices
        ctx["selected_status"] = self.request.GET.get("status", "")
        ctx["pending_count"] = DiscountRequest.objects.filter(
            status=DiscountRequest.Status.PENDING
        ).count()
        return ctx


class AdminDiscountRequestActionView(RoleRequiredMixin, View):
    """Approves or rejects a discount request."""

    allowed_roles = ["admin", "staff"]

    def post(self, request, pk):
        disc_req = get_object_or_404(DiscountRequest, pk=pk)
        action = request.POST.get("action")

        if action == "approve":
            disc_req.status = DiscountRequest.Status.APPROVED
            disc_req.save(update_fields=["status"])
            messages.success(
                request,
                f"Request #{disc_req.id} approved! You can now create a custom discount for {disc_req.customer.email}.",
            )
            # Redirect to discount create form pre-filled with customer / subscription info if desired
            return redirect(
                f"{reverse('catalog:discount_create')}?name=Bulk+{disc_req.seats}+Seats+-+{disc_req.subscription.number}"
            )

        elif action == "reject":
            disc_req.status = DiscountRequest.Status.REJECTED
            disc_req.save(update_fields=["status"])
            messages.warning(request, f"Request #{disc_req.id} has been marked as rejected.")
            return redirect("portal:admin_discount_requests")

        else:
            messages.error(request, f"Invalid action: {action}")
            return redirect("portal:admin_discount_requests")
