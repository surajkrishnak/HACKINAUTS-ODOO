from decimal import Decimal
from django import forms
from django.utils import timezone

from billing.models import Payment


class PaymentForm(forms.ModelForm):
    """Form to record a payment against an invoice."""

    amount = forms.DecimalField(
        max_digits=12,
        decimal_places=2,
        min_value=Decimal("0.01"),
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "step": "0.01",
                "placeholder": "0.00",
                "id": "id_payment_amount",
            }
        ),
        help_text="Amount in INR (₹) to record against this invoice.",
    )
    payment_date = forms.DateField(
        initial=timezone.localdate,
        widget=forms.DateInput(
            attrs={
                "type": "date",
                "class": "form-control",
                "id": "id_payment_date",
            }
        ),
    )
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 2,
                "class": "form-control",
                "placeholder": "Optional transaction reference, check #, or note...",
                "id": "id_payment_note",
            }
        ),
    )

    class Meta:
        model = Payment
        fields = ["method", "amount", "payment_date", "note"]
        widgets = {
            "method": forms.Select(attrs={"class": "form-select", "id": "id_payment_method"}),
        }

    def __init__(self, *args, **kwargs):
        self.invoice = kwargs.pop("invoice", None)
        super().__init__(*args, **kwargs)
        if self.invoice:
            # Set default amount to remaining unpaid balance
            self.fields["amount"].initial = self.invoice.balance
            self.fields["amount"].help_text = (
                f"Maximum payable: ₹{self.invoice.balance} (Unpaid balance)"
            )

    def clean_amount(self):
        amt = self.cleaned_data.get("amount")
        if amt is None or amt <= Decimal("0.00"):
            raise forms.ValidationError("Payment amount must be greater than ₹0.00.")

        if self.invoice and amt > self.invoice.balance:
            raise forms.ValidationError(
                f"Payment amount (₹{amt}) exceeds outstanding invoice balance (₹{self.invoice.balance})."
            )
        return amt
