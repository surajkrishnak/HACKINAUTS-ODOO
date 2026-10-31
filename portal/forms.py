from django import forms
from portal.models import DiscountRequest
from subscriptions.models import Subscription


class DiscountRequestForm(forms.ModelForm):
    """Form for customers to submit a bulk seat discount request for one of their subscriptions."""

    seats = forms.IntegerField(
        min_value=1,
        widget=forms.NumberInput(
            attrs={
                "class": "form-control",
                "placeholder": "e.g. 50",
                "id": "id_seats",
            }
        ),
        help_text="Total number of team seats or licenses planned.",
    )
    message = forms.CharField(
        required=False,
        widget=forms.Textarea(
            attrs={
                "rows": 3,
                "class": "form-control",
                "placeholder": "Tell us about your team size, expected expansion, or procurement timeline...",
                "id": "id_message",
            }
        ),
        help_text="Optional message or justification for the pricing team.",
    )

    class Meta:
        model = DiscountRequest
        fields = ["subscription", "seats", "message"]
        widgets = {
            "subscription": forms.Select(attrs={"class": "form-select", "id": "id_subscription"}),
        }

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user:
            # Strictly limit subscription choices to user's active/confirmed subscriptions
            self.fields["subscription"].queryset = (
                Subscription.objects.filter(
                    customer=user,
                    status__in=[Subscription.Status.ACTIVE, Subscription.Status.CONFIRMED],
                )
                .select_related("plan")
                .order_by("-created_at")
            )
            self.fields["subscription"].empty_label = "Select one of your subscriptions..."
