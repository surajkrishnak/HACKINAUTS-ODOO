from decimal import Decimal
from django import forms
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.forms import inlineformset_factory
from django.utils import timezone

from catalog.models import Plan, Product, ProductVariant
from subscriptions.models import (
    QuotationTemplate,
    QuotationTemplateLine,
    Subscription,
    SubscriptionLine,
)

User = get_user_model()


class QuotationTemplateForm(forms.ModelForm):
    class Meta:
        model = QuotationTemplate
        fields = ["name", "plan", "validity_days", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Standard Cloud Package"}),
            "plan": forms.Select(attrs={"class": "form-select"}),
            "validity_days": forms.NumberInput(attrs={"class": "form-control", "min": "1"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }


class QuotationTemplateLineForm(forms.ModelForm):
    class Meta:
        model = QuotationTemplateLine
        fields = ["product", "variant", "quantity"]
        widgets = {
            "product": forms.Select(attrs={"class": "form-select form-select-sm"}),
            "variant": forms.Select(attrs={"class": "form-select form-select-sm"}),
            "quantity": forms.NumberInput(attrs={"class": "form-control form-control-sm", "min": "1"}),
        }


QuotationTemplateLineFormSet = inlineformset_factory(
    QuotationTemplate,
    QuotationTemplateLine,
    form=QuotationTemplateLineForm,
    fields=["product", "variant", "quantity"],
    extra=1,
    can_delete=True,
)


class SubscriptionFromTemplateForm(forms.Form):
    """Form to generate a new quotation subscription from an existing template."""

    template = forms.ModelChoiceField(
        queryset=QuotationTemplate.objects.filter(is_active=True).select_related("plan"),
        label="Quotation Template",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    customer = forms.ModelChoiceField(
        queryset=User.objects.filter(role=User.Role.CUSTOMER).order_by("email"),
        label="Customer Account",
        widget=forms.Select(attrs={"class": "form-select"}),
    )
    start_date = forms.DateField(
        label="Subscription Start Date",
        initial=timezone.localdate,
        widget=forms.DateInput(attrs={"class": "form-control", "type": "date"}),
    )


class SubscriptionCreateForm(forms.ModelForm):
    """Form to create a subscription from scratch."""

    class Meta:
        model = Subscription
        fields = ["customer", "plan", "start_date", "expiry_date", "payment_terms"]
        widgets = {
            "customer": forms.Select(attrs={"class": "form-select"}),
            "plan": forms.Select(attrs={"class": "form-select"}),
            "start_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "expiry_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "payment_terms": forms.Select(attrs={"class": "form-select"}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["customer"].queryset = User.objects.filter(role=User.Role.CUSTOMER).order_by("email")
        self.fields["plan"].queryset = Plan.objects.filter(is_active=True).order_by("name")
        if not self.initial.get("start_date"):
            self.initial["start_date"] = timezone.localdate()

    def clean(self):
        cleaned_data = super().clean()
        start = cleaned_data.get("start_date")
        expiry = cleaned_data.get("expiry_date")
        if start and expiry and expiry < start:
            self.add_error("expiry_date", "Expiry date must be after the start date.")
        return cleaned_data


class SubscriptionLineForm(forms.ModelForm):
    class Meta:
        model = SubscriptionLine
        fields = ["product", "variant", "quantity"]
        widgets = {
            "product": forms.Select(attrs={"class": "form-select form-select-sm"}),
            "variant": forms.Select(attrs={"class": "form-select form-select-sm"}),
            "quantity": forms.NumberInput(attrs={"class": "form-control form-control-sm", "min": "1"}),
        }

    def save(self, commit=True):
        instance = super().save(commit=False)
        product = instance.product
        variant = instance.variant

        # Snapshot unit price
        extra = variant.extra_price if variant else Decimal("0.00")
        instance.unit_price = product.sales_price + extra

        # Snapshot tax percentage
        instance.tax_percent = product.tax.rate_percent if product.tax else Decimal("0.00")
        instance.amount = Decimal(instance.quantity) * instance.unit_price

        if commit:
            instance.save()
        return instance


SubscriptionLineFormSet = inlineformset_factory(
    Subscription,
    SubscriptionLine,
    form=SubscriptionLineForm,
    fields=["product", "variant", "quantity"],
    extra=1,
    can_delete=True,
)
