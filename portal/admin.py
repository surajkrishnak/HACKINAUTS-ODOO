from django.contrib import admin
from .models import DiscountRequest


@admin.register(DiscountRequest)
class DiscountRequestAdmin(admin.ModelAdmin):
    list_display = ("id", "customer", "subscription", "seats", "status", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("customer__email", "subscription__number", "message")
