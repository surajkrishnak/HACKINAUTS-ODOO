from django.conf import settings
from django.db import models


class DiscountRequest(models.Model):
    """
    Bulk seat discount request submitted by a customer from the portal.
    Reviewed and processed by administrators.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        APPROVED = "approved", "Approved"
        REJECTED = "rejected", "Rejected"

    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="discount_requests",
    )
    subscription = models.ForeignKey(
        "subscriptions.Subscription",
        on_delete=models.CASCADE,
        related_name="discount_requests",
    )
    seats = models.PositiveIntegerField(help_text="Number of seats requested for bulk discount.")
    message = models.TextField(blank=True, default="", help_text="Customer message or justification.")
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Discount Request"
        verbose_name_plural = "Discount Requests"

    def __str__(self):
        return f"Discount Request #{self.id} for {self.subscription.number} by {self.customer.email}"
