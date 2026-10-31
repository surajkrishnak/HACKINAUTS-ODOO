from decimal import Decimal
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from accounts.permissions import RoleRequiredMixin
from core.utils import next_number
from subscriptions.forms import (
    QuotationTemplateForm,
    QuotationTemplateLineFormSet,
    SubscriptionCreateForm,
    SubscriptionFromTemplateForm,
    SubscriptionLineFormSet,
)
from subscriptions.models import (
    QuotationTemplate,
    StatusHistory,
    Subscription,
    SubscriptionLine,
)
from subscriptions.services import (
    create_subscription_from_template,
    pause_subscription,
    resume_subscription,
    transition,
)


# ==============================================================================
# QUOTATION TEMPLATE VIEWS (Admin manages, Staff can use)
# ==============================================================================

class QuotationTemplateListView(RoleRequiredMixin, ListView):
    model = QuotationTemplate
    template_name = "subscriptions/template_list.html"
    context_object_name = "templates"
    paginate_by = 20
    allowed_roles = ("admin", "staff")

    def get_queryset(self):
        return (
            QuotationTemplate.objects.select_related("plan")
            .prefetch_related("lines__product")
            .order_by("name")
        )


class QuotationTemplateCreateView(RoleRequiredMixin, CreateView):
    model = QuotationTemplate
    form_class = QuotationTemplateForm
    template_name = "subscriptions/template_form.html"
    allowed_roles = ("admin",)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if self.request.POST:
            ctx["lines_formset"] = QuotationTemplateLineFormSet(self.request.POST)
        else:
            ctx["lines_formset"] = QuotationTemplateLineFormSet()
        return ctx

    def form_valid(self, form):
        ctx = self.get_context_data()
        lines_formset = ctx["lines_formset"]

        if lines_formset.is_valid():
            with transaction.atomic():
                self.object = form.save()
                lines_formset.instance = self.object
                lines_formset.save()

            messages.success(self.request, f"Quotation template '{self.object.name}' created successfully.")
            return redirect("subscriptions:template_list")
        else:
            return self.form_invalid(form)


class QuotationTemplateUpdateView(RoleRequiredMixin, UpdateView):
    model = QuotationTemplate
    form_class = QuotationTemplateForm
    template_name = "subscriptions/template_form.html"
    allowed_roles = ("admin",)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if self.request.POST:
            ctx["lines_formset"] = QuotationTemplateLineFormSet(self.request.POST, instance=self.object)
        else:
            ctx["lines_formset"] = QuotationTemplateLineFormSet(instance=self.object)
        return ctx

    def form_valid(self, form):
        ctx = self.get_context_data()
        lines_formset = ctx["lines_formset"]

        if lines_formset.is_valid():
            with transaction.atomic():
                self.object = form.save()
                lines_formset.instance = self.object
                lines_formset.save()

            messages.success(self.request, f"Quotation template '{self.object.name}' updated successfully.")
            return redirect("subscriptions:template_list")
        else:
            return self.form_invalid(form)


# ==============================================================================
# SUBSCRIPTION VIEWS (Admin and Staff can create and manage)
# ==============================================================================

class SubscriptionListView(RoleRequiredMixin, ListView):
    model = Subscription
    template_name = "subscriptions/subscription_list.html"
    context_object_name = "subscriptions"
    paginate_by = 20
    allowed_roles = ("admin", "staff")

    def get_queryset(self):
        # select_related avoids N+1 queries regardless of page size
        qs = (
            Subscription.objects.select_related("customer", "plan")
            .prefetch_related("lines__product")
            .order_by("-created_at")
        )
        q = self.request.GET.get("q", "").strip()
        status_filter = self.request.GET.get("status", "").strip()
        plan_filter = self.request.GET.get("plan", "").strip()

        if q:
            qs = qs.filter(
                Q(number__icontains=q)
                | Q(customer__email__icontains=q)
                | Q(customer__first_name__icontains=q)
                | Q(customer__last_name__icontains=q)
            )
        if status_filter:
            qs = qs.filter(status=status_filter)
        if plan_filter:
            qs = qs.filter(plan_id=plan_filter)

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = self.request.GET.get("q", "")
        ctx["status_filter"] = self.request.GET.get("status", "")
        ctx["plan_filter"] = self.request.GET.get("plan", "")
        ctx["statuses"] = Subscription.Status.choices
        from catalog.models import Plan
        ctx["plans"] = Plan.objects.filter(is_active=True).order_by("name")
        return ctx


class SubscriptionDetailView(RoleRequiredMixin, DetailView):
    model = Subscription
    template_name = "subscriptions/subscription_detail.html"
    context_object_name = "subscription"
    allowed_roles = ("admin", "staff")

    def get_queryset(self):
        return (
            Subscription.objects.select_related("customer", "plan", "template", "created_by")
            .prefetch_related(
                "lines__product__tax",
                "lines__variant",
                "status_history__changed_by",
            )
        )

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        sub = self.object

        # Determine stepper index (0: Draft, 1: Quotation, 2: Confirmed, 3: Active, 4: Closed)
        stepper_map = {
            Subscription.Status.DRAFT: 0,
            Subscription.Status.QUOTATION: 1,
            Subscription.Status.CONFIRMED: 2,
            Subscription.Status.ACTIVE: 3,
            Subscription.Status.CLOSED: 4,
        }
        ctx["step_index"] = stepper_map.get(sub.status, 0)

        # Allow line edits only while draft or quotation
        ctx["can_edit_lines"] = sub.status in (Subscription.Status.DRAFT, Subscription.Status.QUOTATION)

        # Invoices will be populated in Step 5
        ctx["invoices"] = sub.invoices.all() if hasattr(sub, "invoices") else []
        return ctx


class SubscriptionFromTemplateView(RoleRequiredMixin, View):
    """Wizard to generate a new subscription in DRAFT status from a QuotationTemplate."""
    allowed_roles = ("admin", "staff")

    def get(self, request):
        form = SubscriptionFromTemplateForm()
        return render(request, "subscriptions/subscription_from_template.html", {"form": form})

    def post(self, request):
        form = SubscriptionFromTemplateForm(request.POST)
        if form.is_valid():
            template = form.cleaned_data["template"]
            customer = form.cleaned_data["customer"]
            start_date = form.cleaned_data["start_date"]

            sub = create_subscription_from_template(
                template=template,
                customer=customer,
                start_date=start_date,
                user=request.user,
            )
            messages.success(
                request,
                f"Quotation {sub.number} generated from template '{template.name}' with "
                f"{sub.lines.count()} line item(s).",
            )
            return redirect("subscriptions:subscription_detail", pk=sub.pk)

        return render(request, "subscriptions/subscription_from_template.html", {"form": form})


class SubscriptionCreateScratchView(RoleRequiredMixin, View):
    """Creates a subscription from scratch with custom line items."""
    allowed_roles = ("admin", "staff")

    def get(self, request):
        form = SubscriptionCreateForm()
        lines_formset = SubscriptionLineFormSet()
        return render(request, "subscriptions/subscription_form_scratch.html", {
            "form": form,
            "lines_formset": lines_formset,
        })

    def post(self, request):
        form = SubscriptionCreateForm(request.POST)
        lines_formset = SubscriptionLineFormSet(request.POST)

        if form.is_valid() and lines_formset.is_valid():
            with transaction.atomic():
                sub = form.save(commit=False)
                sub.number = next_number("SUB", Subscription)
                sub.status = Subscription.Status.DRAFT
                sub.created_by = request.user
                sub.save()

                lines_formset.instance = sub
                lines_formset.save()

                StatusHistory.objects.create(
                    subscription=sub,
                    from_status="none",
                    to_status=Subscription.Status.DRAFT,
                    changed_by=request.user,
                )

            messages.success(request, f"Subscription {sub.number} created successfully.")
            return redirect("subscriptions:subscription_detail", pk=sub.pk)

        return render(request, "subscriptions/subscription_form_scratch.html", {
            "form": form,
            "lines_formset": lines_formset,
        })


class SubscriptionEditLinesView(RoleRequiredMixin, View):
    """Allows editing line items and terms only while subscription is Draft or Quotation."""
    allowed_roles = ("admin", "staff")

    def get(self, request, pk):
        sub = get_object_or_404(Subscription, pk=pk)
        if sub.status not in (Subscription.Status.DRAFT, Subscription.Status.QUOTATION):
            messages.error(request, f"Lines cannot be modified on a {sub.get_status_display()} subscription.")
            return redirect("subscriptions:subscription_detail", pk=sub.pk)

        lines_formset = SubscriptionLineFormSet(instance=sub)
        return render(request, "subscriptions/subscription_edit_lines.html", {
            "subscription": sub,
            "lines_formset": lines_formset,
        })

    def post(self, request, pk):
        sub = get_object_or_404(Subscription, pk=pk)
        if sub.status not in (Subscription.Status.DRAFT, Subscription.Status.QUOTATION):
            raise PermissionDenied("Lines can only be edited while status is Draft or Quotation.")

        lines_formset = SubscriptionLineFormSet(request.POST, instance=sub)
        if lines_formset.is_valid():
            lines_formset.save()
            messages.success(request, f"Line items for {sub.number} updated successfully.")
            return redirect("subscriptions:subscription_detail", pk=sub.pk)

        return render(request, "subscriptions/subscription_edit_lines.html", {
            "subscription": sub,
            "lines_formset": lines_formset,
        })


class SubscriptionTransitionView(RoleRequiredMixin, View):
    """Handles lifecycle actions via transition() service."""
    allowed_roles = ("admin", "staff")

    def post(self, request, pk, action):
        sub = get_object_or_404(Subscription, pk=pk)

        try:
            if action == "send_quotation":
                transition(sub, Subscription.Status.QUOTATION, user=request.user)
                messages.success(request, f"Quotation sent! Status is now {sub.get_status_display()}.")

            elif action == "confirm":
                transition(sub, Subscription.Status.CONFIRMED, user=request.user)
                messages.success(request, f"Quotation confirmed! Status is now {sub.get_status_display()}.")

            elif action == "activate":
                transition(sub, Subscription.Status.ACTIVE, user=request.user)
                messages.success(
                    request,
                    f"Subscription {sub.number} activated! Next billing date scheduled for {sub.next_billing_date}.",
                )

            elif action == "close":
                transition(sub, Subscription.Status.CLOSED, user=request.user)
                messages.warning(request, f"Subscription {sub.number} has been closed.")

            elif action == "cancel":
                transition(sub, Subscription.Status.CLOSED, user=request.user)
                messages.warning(request, f"Quotation {sub.number} has been cancelled.")

            elif action == "pause":
                pause_subscription(sub, user=request.user)
                messages.warning(request, f"Subscription {sub.number} is now paused.")

            elif action == "resume":
                resume_subscription(sub, user=request.user)
                messages.success(request, f"Subscription {sub.number} resumed.")

            else:
                messages.error(request, f"Unknown transition action '{action}'.")

        except ValidationError as e:
            msg = e.message if hasattr(e, "message") else str(e)
            messages.error(request, f"Transition failed: {msg}")

        return redirect("subscriptions:subscription_detail", pk=sub.pk)
