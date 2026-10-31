from decimal import Decimal
from django.conf import settings
from django.db import models
from django.utils import timezone


class InvoiceQuerySet(models.QuerySet):
    """Custom queryset for Invoice with filtering helpers."""

    def overdue(self):
        """Returns confirmed invoices where due_date < today and amount_paid < total."""
        today = timezone.localdate()
        return self.filter(
            status=Invoice.Status.CONFIRMED,
            due_date__lt=today,
            amount_paid__lt=models.F("total"),
        )


class Invoice(models.Model):
    """
    Invoice generated from a subscription for a specific billing period.
    UniqueConstraint guarantees no double billing for active/confirmed/paid invoices.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "Draft"
        CONFIRMED = "confirmed", "Confirmed"
        PAID = "paid", "Paid"
        CANCELLED = "cancelled", "Cancelled"

    number = models.CharField(
        max_length=50,
        unique=True,
        db_index=True,
        help_text="Unique auto-generated number like INV-00001",
    )
    subscription = models.ForeignKey(
        "subscriptions.Subscription",
        on_delete=models.PROTECT,
        related_name="invoices",
    )
    customer = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="invoices",
        db_index=True,
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    invoice_date = models.DateField(db_index=True)
    due_date = models.DateField(db_index=True)
    period_start = models.DateField()
    period_end = models.DateField()

    subtotal = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    tax_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    total = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))
    amount_paid = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal("0.00"))

    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    objects = InvoiceQuerySet.as_manager()

    class Meta:
        ordering = ["-invoice_date", "-id"]
        verbose_name = "Invoice"
        verbose_name_plural = "Invoices"
        constraints = [
            models.UniqueConstraint(
                fields=["subscription", "period_start", "period_end"],
                condition=~models.Q(status="cancelled"),
                name="unique_invoice_subscription_period_not_cancelled",
            )
        ]
        indexes = [
            models.Index(fields=["status", "due_date"], name="idx_inv_status_duedate"),
            models.Index(fields=["customer", "status"], name="idx_inv_cust_status"),
            models.Index(fields=["invoice_date"], name="idx_inv_date"),
        ]

    def __str__(self):
        return f"{self.number} - {self.customer.email} (₹{self.total}) [{self.get_status_display()}]"

    @property
    def balance(self):
        """Outstanding unpaid balance."""
        diff = self.total - self.amount_paid
        return max(Decimal("0.00"), diff)

    @property
    def is_overdue(self):
        """True if invoice is confirmed, past due date, and has an unpaid balance."""
        return (
            self.status == self.Status.CONFIRMED
            and self.due_date < timezone.localdate()
            and self.balance > Decimal("0.00")
        )

    @property
    def days_overdue(self):
        """Number of days past due date if overdue."""
        if not self.is_overdue:
            return 0
        return (timezone.localdate() - self.due_date).days

    @property
    def can_cancel(self):
        """Cannot cancel if any payment exists or if already cancelled/paid."""
        if self.payments.exists() or self.status in (self.Status.CANCELLED, self.Status.PAID):
            return False
        return True

    @property
    def can_pay(self):
        """Only confirmed invoices with a positive unpaid balance can receive payments."""
        return self.status == self.Status.CONFIRMED and self.balance > Decimal("0.00")


class InvoiceLine(models.Model):
    """Individual line item or applied discount entry on an invoice."""

    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name="lines",
    )
    description = models.CharField(max_length=255)
    quantity = models.PositiveIntegerField(default=1)
    unit_price = models.DecimalField(max_digits=12, decimal_places=2)
    tax_percent = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal("0.00"),
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)

    class Meta:
        ordering = ["id"]
        verbose_name = "Invoice Line"
        verbose_name_plural = "Invoice Lines"

    def __str__(self):
        return f"{self.invoice.number} - {self.description} (₹{self.amount})"


class Payment(models.Model):
    """Payment recorded against an invoice."""

    class Method(models.TextChoices):
        CASH = "cash", "Cash"
        CARD = "card", "Credit/Debit Card"
        UPI = "upi", "UPI"
        BANK_TRANSFER = "bank_transfer", "Bank Transfer"

    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.PROTECT,
        related_name="payments",
    )
    method = models.CharField(max_length=20, choices=Method.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    payment_date = models.DateField(default=timezone.localdate, db_index=True)
    note = models.TextField(blank=True, default="")
    recorded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="recorded_payments",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-payment_date", "-id"]
        verbose_name = "Payment"
        verbose_name_plural = "Payments"
        indexes = [
            models.Index(fields=["payment_date"], name="idx_pmt_date"),
            models.Index(fields=["invoice", "payment_date"], name="idx_pmt_inv_date"),
        ]

    def __str__(self):
        return f"Payment of ₹{self.amount} for {self.invoice.number} via {self.get_method_display()}"


class DiscountApplication(models.Model):
    """
    Audit log of each discount applied to an invoice.
    Enables traceability, limits checking, and cancellation reversal.
    """

    discount = models.ForeignKey(
        "catalog.Discount",
        on_delete=models.PROTECT,
        related_name="applications",
    )
    invoice = models.ForeignKey(
        Invoice,
        on_delete=models.CASCADE,
        related_name="discount_applications",
    )
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    applied_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-applied_at"]
        verbose_name = "Discount Application"
        verbose_name_plural = "Discount Applications"

    def __str__(self):
        return f"{self.discount.name} applied to {self.invoice.number} (₹{self.amount})"
