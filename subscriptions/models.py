from decimal import Decimal
from django.conf import settings
from django.db import models


class QuotationTemplate(models.Model):
    """Reusable quotation template linked to a plan with pre-configured line items."""

    name = models.CharField(max_length=150)
    validity_days = models.PositiveIntegerField(
        default=30,
        help_text="Number of days from start date before the quotation expires.",
    )
    plan = models.ForeignKey(
        "catalog.Plan",
        on_delete=models.CASCADE,
        related_name="quotation_templates",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Quotation Template"
        verbose_name_plural = "Quotation Templates"

    def __str__(self):
        return f"{self.name} ({self.plan.name})"


class QuotationTemplateLine(models.Model):
    """Line item in a quotation template."""

    template = models.ForeignKey(
        QuotationTemplate,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    product = models.ForeignKey(
        "catalog.Product",
        on_delete=models.CASCADE,
        related_name="quotation_template_lines",
    )
    variant = models.ForeignKey(
        "catalog.ProductVariant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="quotation_template_lines",
    )
    quantity = models.PositiveIntegerField(default=1)

    class Meta:
        ordering = ["id"]
        verbose_name = "Quotation Template Line"
        verbose_name_plural = "Quotation Template Lines"

    def __str__(self):
        v = f" [{self.variant.value}]" if self.variant else ""
        return f"{self.product.name}{v} x {self.quantity}"


class Subscription(models.Model):
    """
    Subscription entity representing a customer contract.
    Status flow: Draft -> Quotation -> Confirmed -> Active -> Closed.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        QUOTATION = "quotation", "Quotation"
        CONFIRMED = "confirmed", "Confirmed"
        ACTIVE = "active", "Active"
        CLOSED = "closed", "Closed"

    class PaymentTerms(models.TextChoices):
        IMMEDIATE = "immediate", "Immediate"
        DAYS_15 = "15_days", "15 Days"
        DAYS_30 = "30_days", "30 Days"
        DAYS_45 = "45_days", "45 Days"

    number = models.CharField(
        max_length=50,
        unique=True,
        db_index=True,
        help_text="Unique auto-generated number like SUB-00001",
    )
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="subscriptions",
        db_index=True,
    )
    plan = models.ForeignKey(
        "catalog.Plan",
        on_delete=models.PROTECT,
        related_name="subscriptions",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    start_date = models.DateField(db_index=True)
    expiry_date = models.DateField(null=True, blank=True, db_index=True)
    payment_terms = models.CharField(
        max_length=20,
        choices=PaymentTerms.choices,
        default=PaymentTerms.IMMEDIATE,
    )
    next_billing_date = models.DateField(null=True, blank=True, db_index=True)
    template = models.ForeignKey(
        QuotationTemplate,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="subscriptions",
    )
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="created_subscriptions",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    is_paused = models.BooleanField(
        default=False,
        help_text="Indicates whether this subscription is currently paused.",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "Subscription"
        verbose_name_plural = "Subscriptions"
        indexes = [
            models.Index(fields=["customer", "status"], name="idx_sub_cust_status"),
            models.Index(fields=["status", "next_billing_date"], name="idx_sub_status_nextbill"),
        ]
        constraints = [
            models.CheckConstraint(
                check=models.Q(expiry_date__isnull=True) | models.Q(expiry_date__gte=models.F("start_date")),
                name="sub_expiry_gte_start_date",
            ),
        ]

    def __str__(self):
        return f"{self.number} - {self.customer.email} ({self.get_status_display()})"

    @property
    def total_amount(self):
        """Calculates total recurring line amount."""
        return sum(line.amount for line in self.lines.all())


class SubscriptionLine(models.Model):
    """
    Individual line item on a subscription with snapshotted pricing and tax.
    """

    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    product = models.ForeignKey(
        "catalog.Product",
        on_delete=models.PROTECT,
        related_name="subscription_lines",
    )
    variant = models.ForeignKey(
        "catalog.ProductVariant",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="subscription_lines",
    )
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Snapshotted unit price (sales_price + variant extra_price) at time of creation.",
    )
    tax_percent = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Snapshotted tax rate percentage at time of creation.",
    )
    amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Computed and stored total for this line (quantity * unit_price).",
    )

    class Meta:
        ordering = ["id"]
        verbose_name = "Subscription Line"
        verbose_name_plural = "Subscription Lines"
        constraints = [
            models.CheckConstraint(
                check=models.Q(quantity__gt=0),
                name="sub_line_qty_positive",
            ),
            models.CheckConstraint(
                check=models.Q(unit_price__gte=0),
                name="sub_line_price_non_negative",
            ),
        ]

    def __str__(self):
        return f"{self.subscription.number} - {self.product.name} x {self.quantity}"

    def save(self, *args, **kwargs):
        # Ensure stored amount is always quantity * unit_price
        self.amount = Decimal(self.quantity) * self.unit_price
        super().save(*args, **kwargs)


class StatusHistory(models.Model):
    """Audit log of subscription lifecycle transitions."""

    subscription = models.ForeignKey(
        Subscription,
        on_delete=models.CASCADE,
        related_name="status_history",
    )
    from_status = models.CharField(max_length=20)
    to_status = models.CharField(max_length=20)
    changed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="status_changes",
    )
    changed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-changed_at"]
        verbose_name = "Status History"
        verbose_name_plural = "Status Histories"

    def __str__(self):
        return f"{self.subscription.number}: {self.from_status} -> {self.to_status} at {self.changed_at}"
