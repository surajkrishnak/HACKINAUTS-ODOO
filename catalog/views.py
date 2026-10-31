import datetime
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse_lazy
from django.views import View
from django.views.generic import CreateView, DetailView, ListView, UpdateView

from accounts.permissions import RoleRequiredMixin
from catalog.forms import (
    DiscountForm,
    PlanForm,
    ProductForm,
    ProductVariantFormSet,
    TaxForm,
)
from catalog.models import Discount, Plan, Product, Tax
from catalog.services import (
    close_plan_early,
    pause_plan,
    renew_plan,
    resume_plan,
)


# ==============================================================================
# TAX VIEWS (Admin only)
# ==============================================================================

class TaxListView(RoleRequiredMixin, ListView):
    model = Tax
    template_name = "catalog/tax_list.html"
    context_object_name = "taxes"
    paginate_by = 20
    allowed_roles = ("admin",)

    def get_queryset(self):
        qs = Tax.objects.all().order_by("name")
        q = self.request.GET.get("q", "").strip()
        status_filter = self.request.GET.get("status", "").strip()

        if q:
            qs = qs.filter(name__icontains=q)
        if status_filter == "active":
            qs = qs.filter(is_active=True)
        elif status_filter == "inactive":
            qs = qs.filter(is_active=False)

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = self.request.GET.get("q", "")
        ctx["status_filter"] = self.request.GET.get("status", "")
        return ctx


class TaxCreateView(RoleRequiredMixin, CreateView):
    model = Tax
    form_class = TaxForm
    template_name = "catalog/tax_form.html"
    success_url = reverse_lazy("catalog:tax_list")
    allowed_roles = ("admin",)

    def form_valid(self, form):
        messages.success(self.request, f"Tax rule '{form.instance.name}' created successfully.")
        return super().form_valid(form)


class TaxUpdateView(RoleRequiredMixin, UpdateView):
    model = Tax
    form_class = TaxForm
    template_name = "catalog/tax_form.html"
    success_url = reverse_lazy("catalog:tax_list")
    allowed_roles = ("admin",)

    def form_valid(self, form):
        messages.success(self.request, f"Tax rule '{form.instance.name}' updated successfully.")
        return super().form_valid(form)


class TaxToggleActiveView(RoleRequiredMixin, View):
    """Soft delete / deactivate or reactivate tax rule."""
    allowed_roles = ("admin",)

    def post(self, request, pk):
        tax = get_object_or_404(Tax, pk=pk)
        tax.is_active = not tax.is_active
        tax.save(update_fields=["is_active"])
        state = "activated" if tax.is_active else "deactivated"
        messages.success(request, f"Tax '{tax.name}' was {state}.")
        return redirect("catalog:tax_list")


# ==============================================================================
# PRODUCT VIEWS (Admin full access; Staff read-only)
# ==============================================================================

class ProductListView(RoleRequiredMixin, ListView):
    model = Product
    template_name = "catalog/product_list.html"
    context_object_name = "products"
    paginate_by = 20
    allowed_roles = ("admin", "staff")

    def get_queryset(self):
        qs = Product.objects.select_related("tax").prefetch_related("variants").order_by("name")
        q = self.request.GET.get("q", "").strip()
        p_type = self.request.GET.get("type", "").strip()

        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(notes__icontains=q))
        if p_type:
            qs = qs.filter(product_type=p_type)

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = self.request.GET.get("q", "")
        ctx["type_filter"] = self.request.GET.get("type", "")
        ctx["types"] = Product.ProductType.choices
        return ctx


class ProductDetailView(RoleRequiredMixin, DetailView):
    model = Product
    template_name = "catalog/product_detail.html"
    context_object_name = "product"
    allowed_roles = ("admin", "staff")

    def get_queryset(self):
        return Product.objects.select_related("tax").prefetch_related("variants")


class ProductCreateView(RoleRequiredMixin, CreateView):
    model = Product
    form_class = ProductForm
    template_name = "catalog/product_form.html"
    success_url = reverse_lazy("catalog:product_list")
    allowed_roles = ("admin",)

    def form_valid(self, form):
        if getattr(form, "cost_exceeds_sales", False):
            messages.warning(
                self.request,
                f"Warning: Cost price (₹{form.cleaned_data['cost_price']}) exceeds sales price (₹{form.cleaned_data['sales_price']}).",
            )
        messages.success(self.request, f"Product '{form.instance.name}' created. You can now add product variants.")
        response = super().form_valid(form)
        return redirect("catalog:product_edit", pk=self.object.pk)


class ProductUpdateView(RoleRequiredMixin, UpdateView):
    model = Product
    form_class = ProductForm
    template_name = "catalog/product_form.html"
    allowed_roles = ("admin",)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        if self.request.POST:
            ctx["variant_formset"] = ProductVariantFormSet(self.request.POST, instance=self.object)
        else:
            ctx["variant_formset"] = ProductVariantFormSet(instance=self.object)
        return ctx

    def form_valid(self, form):
        ctx = self.get_context_data()
        variant_formset = ctx["variant_formset"]

        if variant_formset.is_valid():
            with transaction.atomic():
                self.object = form.save()
                variant_formset.instance = self.object
                variant_formset.save()

            if getattr(form, "cost_exceeds_sales", False):
                messages.warning(
                    self.request,
                    f"Warning: Cost price (₹{form.cleaned_data['cost_price']}) is higher than sales price (₹{form.cleaned_data['sales_price']}).",
                )
            messages.success(self.request, f"Product '{self.object.name}' and variants saved successfully.")
            return redirect("catalog:product_list")
        else:
            return self.form_invalid(form)


# ==============================================================================
# PLAN VIEWS (Admin full access; Staff read-only)
# ==============================================================================

class PlanListView(RoleRequiredMixin, ListView):
    model = Plan
    template_name = "catalog/plan_list.html"
    context_object_name = "plans"
    paginate_by = 20
    allowed_roles = ("admin", "staff")

    def get_queryset(self):
        qs = Plan.objects.all().order_by("name")
        q = self.request.GET.get("q", "").strip()
        period = self.request.GET.get("period", "").strip()

        if q:
            qs = qs.filter(name__icontains=q)
        if period:
            qs = qs.filter(billing_period=period)

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = self.request.GET.get("q", "")
        ctx["period_filter"] = self.request.GET.get("period", "")
        ctx["periods"] = Plan.BillingPeriod.choices
        return ctx


class PlanDetailView(RoleRequiredMixin, DetailView):
    model = Plan
    template_name = "catalog/plan_detail.html"
    context_object_name = "plan"
    allowed_roles = ("admin", "staff")


class PlanCreateView(RoleRequiredMixin, CreateView):
    model = Plan
    form_class = PlanForm
    template_name = "catalog/plan_form.html"
    success_url = reverse_lazy("catalog:plan_list")
    allowed_roles = ("admin",)

    def form_valid(self, form):
        messages.success(self.request, f"Plan '{form.instance.name}' created successfully.")
        return super().form_valid(form)


class PlanUpdateView(RoleRequiredMixin, UpdateView):
    model = Plan
    form_class = PlanForm
    template_name = "catalog/plan_form.html"
    success_url = reverse_lazy("catalog:plan_list")
    allowed_roles = ("admin",)

    def form_valid(self, form):
        messages.success(self.request, f"Plan '{form.instance.name}' updated successfully.")
        return super().form_valid(form)


class PlanActionView(RoleRequiredMixin, View):
    """Executes lifecycle actions: close, pause, resume, renew."""
    allowed_roles = ("admin",)

    def post(self, request, pk, action):
        plan = get_object_or_404(Plan, pk=pk)

        try:
            if action == "close":
                close_plan_early(plan)
                messages.success(request, f"Plan '{plan.name}' has been closed early.")
            elif action == "pause":
                pause_plan(plan)
                messages.warning(request, f"Plan '{plan.name}' is now paused.")
            elif action == "resume":
                resume_plan(plan)
                messages.success(request, f"Plan '{plan.name}' has been resumed.")
            elif action == "renew":
                new_date_str = request.POST.get("new_available_to")
                if not new_date_str:
                    messages.error(request, "Please provide a valid new expiration date.")
                else:
                    new_date = datetime.date.fromisoformat(new_date_str)
                    renew_plan(plan, new_date)
                    messages.success(request, f"Plan '{plan.name}' renewed through {new_date}.")
            else:
                messages.error(request, f"Unrecognized action '{action}'.")
        except (ValidationError, ValueError) as e:
            msg = e.message if hasattr(e, "message") else str(e)
            messages.error(request, f"Action failed: {msg}")

        return redirect("catalog:plan_detail", pk=plan.pk)


# ==============================================================================
# DISCOUNT VIEWS (Admin only)
# ==============================================================================

class DiscountListView(RoleRequiredMixin, ListView):
    model = Discount
    template_name = "catalog/discount_list.html"
    context_object_name = "discounts"
    paginate_by = 20
    allowed_roles = ("admin",)

    def get_queryset(self):
        qs = Discount.objects.select_related("product").order_by("-id")
        q = self.request.GET.get("q", "").strip()
        d_type = self.request.GET.get("type", "").strip()
        applies = self.request.GET.get("applies", "").strip()

        if q:
            qs = qs.filter(name__icontains=q)
        if d_type:
            qs = qs.filter(discount_type=d_type)
        if applies:
            qs = qs.filter(applies_to=applies)

        return qs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["q"] = self.request.GET.get("q", "")
        ctx["type_filter"] = self.request.GET.get("type", "")
        ctx["applies_filter"] = self.request.GET.get("applies", "")
        return ctx


class DiscountCreateView(RoleRequiredMixin, CreateView):
    model = Discount
    form_class = DiscountForm
    template_name = "catalog/discount_form.html"
    success_url = reverse_lazy("catalog:discount_list")
    allowed_roles = ("admin",)

    def form_valid(self, form):
        messages.success(self.request, f"Discount '{form.instance.name}' created successfully.")
        return super().form_valid(form)


class DiscountUpdateView(RoleRequiredMixin, UpdateView):
    model = Discount
    form_class = DiscountForm
    template_name = "catalog/discount_form.html"
    success_url = reverse_lazy("catalog:discount_list")
    allowed_roles = ("admin",)

    def form_valid(self, form):
        messages.success(self.request, f"Discount '{form.instance.name}' updated successfully.")
        return super().form_valid(form)
