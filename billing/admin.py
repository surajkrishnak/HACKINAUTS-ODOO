from django.contrib import admin
from .models import DiscountApplication, Invoice, InvoiceLine, Payment


class InvoiceLineInline(admin.TabularInline):
    model = InvoiceLine
    extra = 0
    fields = ("description", "quantity", "unit_price", "tax_percent", "amount")


class PaymentInline(admin.TabularInline):
    model = Payment
    extra = 0
    fields = ("method", "amount", "payment_date", "recorded_by")


class DiscountApplicationInline(admin.TabularInline):
    model = DiscountApplication
    extra = 0
    readonly_fields = ("discount", "amount", "applied_at")
    can_delete = False


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = (
        "number",
        "subscription",
        "customer",
        "status",
        "invoice_date",
        "due_date",
        "total",
        "amount_paid",
        "balance",
    )
    list_filter = ("status", "invoice_date", "due_date")
    search_fields = ("number", "customer__email", "subscription__number")
    inlines = [InvoiceLineInline, PaymentInline, DiscountApplicationInline]


@admin.register(InvoiceLine)
class InvoiceLineAdmin(admin.ModelAdmin):
    list_display = ("invoice", "description", "quantity", "unit_price", "tax_percent", "amount")
    search_fields = ("invoice__number", "description")


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ("invoice", "method", "amount", "payment_date", "recorded_by", "created_at")
    list_filter = ("method", "payment_date")
    search_fields = ("invoice__number", "note", "recorded_by__email")


@admin.register(DiscountApplication)
class DiscountApplicationAdmin(admin.ModelAdmin):
    list_display = ("discount", "invoice", "amount", "applied_at")
    search_fields = ("discount__name", "invoice__number")
    list_filter = ("applied_at",)
