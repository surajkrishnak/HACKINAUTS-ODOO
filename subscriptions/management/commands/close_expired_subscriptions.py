"""
Management command to close active subscriptions that are past their expiry date
and whose plan has auto_close=True. Uses transition() and records in StatusHistory.
"""
from django.core.management.base import BaseCommand
from django.utils import timezone
from subscriptions.models import Subscription
from subscriptions.services import transition


class Command(BaseCommand):
    help = "Closes active subscriptions past expiry_date when plan.auto_close=True."

    def handle(self, *args, **options):
        today = timezone.localdate()
        expired_subs = Subscription.objects.filter(
            status=Subscription.Status.ACTIVE,
            plan__auto_close=True,
            expiry_date__lt=today,
        ).select_related("plan", "customer")

        count = 0
        for sub in expired_subs:
            try:
                transition(sub, Subscription.Status.CLOSED, user=None)
                count += 1
                self.stdout.write(f"Auto-closed expired subscription {sub.number} ({sub.customer.email}).")
            except Exception as e:
                self.stdout.write(self.style.ERROR(f"Failed to auto-close {sub.number}: {e}"))

        self.stdout.write(
            self.style.SUCCESS(f"Successfully processed auto-close for {count} subscription(s).")
        )
