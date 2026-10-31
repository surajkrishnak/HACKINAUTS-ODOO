"""
Management command to deactivate plans that have auto_close=True and available_to in the past.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from catalog.models import Plan


class Command(BaseCommand):
    help = "Deactivates plans with auto_close=True and available_to date in the past."

    def handle(self, *args, **options):
        today = timezone.localdate()
        expired_plans = Plan.objects.filter(
            auto_close=True,
            is_active=True,
            available_to__lt=today,
        )
        count = expired_plans.count()
        if count == 0:
            self.stdout.write(self.style.SUCCESS("No auto-close expired plans found."))
            return

        updated = expired_plans.update(is_active=False)
        self.stdout.write(
            self.style.SUCCESS(f"Successfully deactivated {updated} expired plan(s) with auto_close=True.")
        )
