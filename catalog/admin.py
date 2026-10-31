from django.contrib import admin
from .models import Discount, Plan, Product, ProductVariant, Tax


class ProductVariantInline(admin.TabularInline):
    model = ProductVariant
    extra = 1
    fields = ("attribute", "value", "extra_price")


@admin.register(Tax)
class TaxAdmin(admin.ModelAdmin):
    list_display = ("name", "rate_percent", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name",)


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "product_type", "sales_price", "cost_price", "tax", "is_active")
    list_filter = ("product_type", "is_active", "tax")
    search_fields = ("name", "notes")
    inlines = [ProductVariantInline]


@admin.register(ProductVariant)
class ProductVariantAdmin(admin.ModelAdmin):
    list_display = ("product", "attribute", "value", "extra_price")
    list_filter = ("attribute", "product")
    search_fields = ("product__name", "attribute", "value")


@admin.register(Plan)
class PlanAdmin(admin.ModelAdmin):
    list_display = (
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
    )
    list_filter = (
        "billing_period",
        "is_active",
        "auto_close",
        "closable",
        "pausable",
        "renewable",
    )
    search_fields = ("name",)


@admin.register(Discount)
class DiscountAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "discount_type",
        "value",
        "applies_to",
        "times_used",
        "usage_limit",
        "start_date",
        "end_date",
        "is_active",
    )
    list_filter = ("discount_type", "applies_to", "is_active")
    search_fields = ("name", "product__name")
