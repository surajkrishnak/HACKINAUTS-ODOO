from django.contrib import admin
from .models import (
    QuotationTemplate,
    QuotationTemplateLine,
    StatusHistory,
    Subscription,
    SubscriptionLine,
)


class QuotationTemplateLineInline(admin.TabularInline):
    model = QuotationTemplateLine
    extra = 1
    fields = ("product", "variant", "quantity")


@admin.register(QuotationTemplate)
class QuotationTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "plan", "validity_days", "lines_count", "is_active")
    list_filter = ("is_active", "plan")
    search_fields = ("name",)
    inlines = [QuotationTemplateLineInline]

    def lines_count(self, obj):
        return obj.lines.count()
    lines_count.short_description = "Lines"


class SubscriptionLineInline(admin.TabularInline):
    model = SubscriptionLine
    extra = 0
    fields = ("product", "variant", "quantity", "unit_price", "tax_percent", "amount")
    readonly_fields = ("amount",)


class StatusHistoryInline(admin.TabularInline):
    model = StatusHistory
    extra = 0
    readonly_fields = ("from_status", "to_status", "changed_by", "changed_at")
    can_delete = False


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = (
        "number",
        "customer",
        "plan",
        "status",
        "start_date",
        "expiry_date",
        "next_billing_date",
        "is_paused",
        "created_at",
    )
    list_filter = ("status", "plan", "payment_terms", "is_paused", "start_date")
    search_fields = ("number", "customer__email", "customer__first_name", "customer__last_name")
    inlines = [SubscriptionLineInline, StatusHistoryInline]


@admin.register(SubscriptionLine)
class SubscriptionLineAdmin(admin.ModelAdmin):
    list_display = ("subscription", "product", "variant", "quantity", "unit_price", "tax_percent", "amount")
    search_fields = ("subscription__number", "product__name")


@admin.register(StatusHistory)
class StatusHistoryAdmin(admin.ModelAdmin):
    list_display = ("subscription", "from_status", "to_status", "changed_by", "changed_at")
    search_fields = ("subscription__number", "changed_by__email")
    list_filter = ("to_status", "from_status")
