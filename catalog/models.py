from decimal import Decimal
from django.db import models
from django.utils import timezone


class Tax(models.Model):
    """Tax definition with a rate percentage (e.g. 0%, 5%, 18%)."""

    name = models.CharField(max_length=100)
    rate_percent = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Tax rate percentage (e.g. 18.00 for 18%)",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Tax"
        verbose_name_plural = "Taxes"

    def __str__(self):
        return f"{self.name} ({self.rate_percent}%)"


class Product(models.Model):
    """Software, service, or add-on product offered by Brightloop Software."""

    class ProductType(models.TextChoices):
        SERVICE = "service", "Service"
        SOFTWARE = "software", "Software"
        ADDON = "addon", "Addon"

    name = models.CharField(max_length=200)
    product_type = models.CharField(
        max_length=20,
        choices=ProductType.choices,
        default=ProductType.SOFTWARE,
    )
    sales_price = models.DecimalField(max_digits=12, decimal_places=2)
    cost_price = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    notes = models.TextField(blank=True, default="")
    tax = models.ForeignKey(
        Tax,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="products",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Product"
        verbose_name_plural = "Products"

    def __str__(self):
        return f"{self.name} (₹{self.sales_price})"


class ProductVariant(models.Model):
    """
    Variant of a product (e.g., Brand = Odoo adding extra_price 560).
    """

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="variants",
    )
    attribute = models.CharField(max_length=100, help_text="Variant attribute, e.g. Brand, Tier, Edition")
    value = models.CharField(max_length=100, help_text="Variant value, e.g. Odoo, Enterprise")
    extra_price = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
        help_text="Additional price added to the base sales price",
    )

    class Meta:
        ordering = ["product", "attribute", "value"]
        verbose_name = "Product Variant"
        verbose_name_plural = "Product Variants"

    def __str__(self):
        if self.extra_price > 0:
            return f"{self.product.name} [{self.attribute}: {self.value}] (+₹{self.extra_price})"
        return f"{self.product.name} [{self.attribute}: {self.value}]"

    @property
    def effective_price(self):
        return self.product.sales_price + self.extra_price


class Plan(models.Model):
    """
    Subscription billing plan (daily, weekly, monthly, yearly)
    with lifecycle control flags.
    """

    class BillingPeriod(models.TextChoices):
        DAILY = "daily", "Daily"
        WEEKLY = "weekly", "Weekly"
        MONTHLY = "monthly", "Monthly"
        YEARLY = "yearly", "Yearly"

    name = models.CharField(max_length=150)
    billing_period = models.CharField(
        max_length=20,
        choices=BillingPeriod.choices,
        default=BillingPeriod.MONTHLY,
    )
    price = models.DecimalField(max_digits=12, decimal_places=2)
    min_quantity = models.PositiveIntegerField(default=1)
    available_from = models.DateField()
    available_to = models.DateField()

    # Lifecycle flags
    auto_close = models.BooleanField(
        default=False,
        help_text="Automatically close expired subscriptions tied to this plan.",
    )
    closable = models.BooleanField(
        default=False,
        help_text="Allow staff or customers to close an active subscription early.",
    )
    pausable = models.BooleanField(
        default=False,
        help_text="Allow pausing and resuming the subscription.",
    )
    renewable = models.BooleanField(
        default=False,
        help_text="Allow renewing this plan beyond available_to.",
    )
    is_paused = models.BooleanField(
        default=False,
        help_text="Indicates if the plan is temporarily paused.",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]
        verbose_name = "Plan"
        verbose_name_plural = "Plans"

    def __str__(self):
        return f"{self.name} ({self.get_billing_period_display()})"

    @property
    def computed_status(self):
        today = timezone.localdate()
        if not self.is_active:
            return "Inactive"
        if self.is_paused:
            return "Paused"
        if self.available_from > today:
            return "Upcoming"
        if self.available_to < today:
            return "Expired"
        return "Available"


class Discount(models.Model):
    """
    Discount rule (fixed amount or percentage) applied to individual products
    or overall subscriptions.
    """

    class DiscountType(models.TextChoices):
        FIXED = "fixed", "Fixed Amount"
        PERCENT = "percent", "Percentage"

    class AppliesTo(models.TextChoices):
        PRODUCT = "product", "Product"
        SUBSCRIPTION = "subscription", "Subscription"

    name = models.CharField(max_length=150)
    discount_type = models.CharField(max_length=20, choices=DiscountType.choices)
    value = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        help_text="Discount value (₹ fixed or % percentage)",
    )
    min_purchase = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Minimum purchase amount required for this discount",
    )
    min_quantity = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text="Minimum total quantity required for this discount",
    )
    start_date = models.DateField(db_index=True)
    end_date = models.DateField(db_index=True)
    usage_limit = models.PositiveIntegerField(default=100)
    times_used = models.PositiveIntegerField(default=0)
    applies_to = models.CharField(
        max_length=20,
        choices=AppliesTo.choices,
        default=AppliesTo.SUBSCRIPTION,
    )
    product = models.ForeignKey(
        Product,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="discounts",
        help_text="Required when applies_to is 'product'",
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["-id"]
        verbose_name = "Discount"
        verbose_name_plural = "Discounts"

    def __str__(self):
        unit = "%" if self.discount_type == self.DiscountType.PERCENT else "₹"
        return f"{self.name} ({self.value}{unit})"

    @property
    def computed_status(self):
        today = timezone.localdate()
        if not self.is_active:
            return "Inactive"
        if self.times_used >= self.usage_limit:
            return "Exhausted"
        if today < self.start_date:
            return "Scheduled"
        if today > self.end_date:
            return "Expired"
        return "Active"
