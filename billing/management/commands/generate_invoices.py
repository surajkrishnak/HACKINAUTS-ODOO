import datetime
from decimal import Decimal
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from billing.models import Invoice
from billing.services import generate_invoice
from core.utils import add_period
from subscriptions.models import Subscription


class Command(BaseCommand):
    help = "Generates recurring invoices for active subscriptions whose next billing date has arrived."

    def add_arguments(self, parser):
        parser.add_argument(
            "--date",
            type=str,
            help="Target billing date in YYYY-MM-DD format (defaults to today).",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Simulate invoice generation without committing any changes to the database.",
        )

    def handle(self, *args, **options):
        date_str = options.get("date")
        dry_run = options.get("dry_run", False)

        if date_str:
            try:
                target_date = datetime.date.fromisoformat(date_str)
            except ValueError:
                self.stderr.write(self.style.ERROR(f"Invalid date format: {date_str}. Expected YYYY-MM-DD."))
                return
        else:
            target_date = timezone.localdate()

        mode_str = "[DRY-RUN] " if dry_run else ""
        self.stdout.write(
            self.style.MIGRATE_HEADING(
                f"{mode_str}Starting invoice generation job for target date: {target_date}"
            )
        )

        active_subs = (
            Subscription.objects.filter(
                status=Subscription.Status.ACTIVE,
                is_paused=False,
                next_billing_date__lte=target_date,
            )
            .select_related("customer", "plan")
            .prefetch_related("lines")
            .order_by("id")
        )

        total_due_subs = active_subs.count()
        self.stdout.write(f"Found {total_due_subs} subscription(s) due for billing.")

        created_count = 0
        skipped_count = 0
        failed_count = 0

        for sub in active_subs:
            sub_id = sub.id
            sub_number = sub.number

            try:
                # Wrap each subscription in its own transaction (so one failure doesn't halt the batch)
                with transaction.atomic():
                    # Handle multi-period catch up
                    period_count = 0
                    while sub.next_billing_date and sub.next_billing_date <= target_date:
                        period_count += 1
                        current_period_start = sub.next_billing_date

                        inv = generate_invoice(sub, period_start=current_period_start)
                        if inv:
                            created_count += 1
                            self.stdout.write(
                                self.style.SUCCESS(
                                    f"  + Generated invoice {inv.number} for {sub_number} "
                                    f"({inv.period_start} to {inv.period_end}) - Total: ₹{inv.total}"
                                )
                            )
                            # Refresh next_billing_date from DB
                            sub.refresh_from_db(fields=["next_billing_date"])
                        else:
                            # Invoice already existed or could not be generated for this exact period
                            skipped_count += 1
                            self.stdout.write(
                                self.style.WARNING(
                                    f"  - Skipped period starting {current_period_start} for {sub_number} (already invoiced)."
                                )
                            )
                            # Advance next_billing_date to prevent infinite loop
                            plan = sub.plan
                            sub.next_billing_date = add_period(current_period_start, plan.billing_period)
                            sub.save(update_fields=["next_billing_date"])

                    if dry_run:
                        # Rollback this subscription's transaction
                        transaction.set_rollback(True)

            except Exception as exc:
                failed_count += 1
                self.stderr.write(
                    self.style.ERROR(
                        f"  ! FAILED billing for Subscription {sub_number} (ID: {sub_id}): {exc}"
                    )
                )

        self.stdout.write(
            self.style.SUCCESS(
                f"\n{mode_str}Invoice generation job complete!\n"
                f"  - Invoices Generated: {created_count}\n"
                f"  - Periods Skipped:    {skipped_count}\n"
                f"  - Subscriptions Failed: {failed_count}\n"
            )
        )
