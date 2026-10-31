from decimal import Decimal
from django import forms
from django.core.exceptions import ValidationError
from django.forms import inlineformset_factory

from catalog.models import Discount, Plan, Product, ProductVariant, Tax


class TaxForm(forms.ModelForm):
    class Meta:
        model = Tax
        fields = ["name", "rate_percent", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. GST 18%"}),
            "rate_percent": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0", "max": "100"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def clean_rate_percent(self):
        rate = self.cleaned_data.get("rate_percent")
        if rate is not None:
            if rate < Decimal("0.00") or rate > Decimal("100.00"):
                raise ValidationError("Tax rate percentage must be between 0.00% and 100.00%.")
        return rate


class ProductForm(forms.ModelForm):
    class Meta:
        model = Product
        fields = ["name", "product_type", "sales_price", "cost_price", "tax", "notes", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Enterprise Cloud Suite"}),
            "product_type": forms.Select(attrs={"class": "form-select"}),
            "sales_price": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
            "cost_price": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
            "tax": forms.Select(attrs={"class": "form-select"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Product description or internal notes"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def clean_sales_price(self):
        price = self.cleaned_data.get("sales_price")
        if price is not None and price < Decimal("0.00"):
            raise ValidationError("Sales price cannot be negative.")
        return price

    def clean_cost_price(self):
        cost = self.cleaned_data.get("cost_price")
        if cost is not None and cost < Decimal("0.00"):
            raise ValidationError("Cost price cannot be negative.")
        return cost

    def clean(self):
        cleaned_data = super().clean()
        sales_price = cleaned_data.get("sales_price")
        cost_price = cleaned_data.get("cost_price")

        # Note: rule states "warn (not block) if cost price is above sales price".
        # We flag a warning attribute for the view to show a warning flash message.
        if sales_price is not None and cost_price is not None:
            if cost_price > sales_price:
                self.cost_exceeds_sales = True
        return cleaned_data


class ProductVariantForm(forms.ModelForm):
    class Meta:
        model = ProductVariant
        fields = ["attribute", "value", "extra_price"]
        widgets = {
            "attribute": forms.TextInput(attrs={"class": "form-control form-control-sm", "placeholder": "e.g. Brand, Tier"}),
            "value": forms.TextInput(attrs={"class": "form-control form-control-sm", "placeholder": "e.g. Odoo, Enterprise"}),
            "extra_price": forms.NumberInput(attrs={"class": "form-control form-control-sm", "step": "0.01", "min": "0"}),
        }

    def clean_extra_price(self):
        extra = self.cleaned_data.get("extra_price")
        if extra is not None and extra < Decimal("0.00"):
            raise ValidationError("Extra price cannot be negative.")
        return extra


ProductVariantFormSet = inlineformset_factory(
    Product,
    ProductVariant,
    form=ProductVariantForm,
    fields=["attribute", "value", "extra_price"],
    extra=1,
    can_delete=True,
)


class PlanForm(forms.ModelForm):
    class Meta:
        model = Plan
        fields = [
            "name",
            "billing_period",
            "price",
            "min_quantity",
            "available_from",
            "available_to",
            "auto_close",
            "closable",
            "pausable",
            "renewable",
            "is_active",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. Growth Monthly"}),
            "billing_period": forms.Select(attrs={"class": "form-select"}),
            "price": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
            "min_quantity": forms.NumberInput(attrs={"class": "form-control", "min": "1"}),
            "available_from": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "available_to": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "auto_close": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "closable": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "pausable": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "renewable": forms.CheckboxInput(attrs={"class": "form-check-input"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
        help_texts = {
            "auto_close": "Automatically deactivate this plan when available_to has passed.",
            "closable": "Allow staff or customers to close active subscriptions early.",
            "pausable": "Allow subscriptions on this plan to be paused and resumed.",
            "renewable": "Allow extending the plan validity beyond available_to.",
        }

    def clean_price(self):
        price = self.cleaned_data.get("price")
        if price is not None and price < Decimal("0.00"):
            raise ValidationError("Plan price cannot be negative.")
        return price

    def clean_min_quantity(self):
        qty = self.cleaned_data.get("min_quantity")
        if qty is not None and qty < 1:
            raise ValidationError("Minimum quantity must be at least 1.")
        return qty

    def clean(self):
        cleaned_data = super().clean()
        start = cleaned_data.get("available_from")
        end = cleaned_data.get("available_to")

        if start and end and end < start:
            self.add_error("available_to", "Available To date must be after Available From date.")
        return cleaned_data


class DiscountForm(forms.ModelForm):
    class Meta:
        model = Discount
        fields = [
            "name",
            "discount_type",
            "value",
            "min_purchase",
            "min_quantity",
            "start_date",
            "end_date",
            "usage_limit",
            "applies_to",
            "product",
            "is_active",
        ]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "e.g. SUMMER25"}),
            "discount_type": forms.Select(attrs={"class": "form-select", "id": "id_discount_type"}),
            "value": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0.01"}),
            "min_purchase": forms.NumberInput(attrs={"class": "form-control", "step": "0.01", "min": "0"}),
            "min_quantity": forms.NumberInput(attrs={"class": "form-control", "min": "1"}),
            "start_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "end_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "usage_limit": forms.NumberInput(attrs={"class": "form-control", "min": "1"}),
            "applies_to": forms.Select(attrs={"class": "form-select", "id": "id_applies_to"}),
            "product": forms.Select(attrs={"class": "form-select", "id": "id_product"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def clean_value(self):
        val = self.cleaned_data.get("value")
        d_type = self.cleaned_data.get("discount_type")

        if val is not None:
            if val <= Decimal("0.00"):
                raise ValidationError("Discount value must be greater than 0.")
            if d_type == Discount.DiscountType.PERCENT and val > Decimal("100.00"):
                raise ValidationError("Percentage discount cannot exceed 100.00%.")
        return val

    def clean_usage_limit(self):
        limit = self.cleaned_data.get("usage_limit")
        if limit is not None and limit < 1:
            raise ValidationError("Usage limit must be at least 1.")
        return limit

    def clean(self):
        cleaned_data = super().clean()
        start = cleaned_data.get("start_date")
        end = cleaned_data.get("end_date")
        applies_to = cleaned_data.get("applies_to")
        product = cleaned_data.get("product")
        d_type = cleaned_data.get("discount_type")
        val = cleaned_data.get("value")

        if start and end and end < start:
            self.add_error("end_date", "End date must be after start date.")

        if applies_to == Discount.AppliesTo.PRODUCT and not product:
            self.add_error("product", "A product must be selected when applies_to is 'Product'.")

        if d_type == Discount.DiscountType.PERCENT and val and val > Decimal("100.00"):
            self.add_error("value", "Percentage discount cannot exceed 100.00%.")

        return cleaned_data
