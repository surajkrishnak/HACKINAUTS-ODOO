import datetime
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

from billing.models import DiscountApplication, Invoice, InvoiceLine, Payment
from billing.services import (
    cancel_invoice,
    confirm_invoice,
    generate_invoice,
    record_payment,
    send_invoice_email,
)
from catalog.models import Discount, Plan, Product, ProductVariant, Tax
from subscriptions.models import Subscription, SubscriptionLine

User = get_user_model()


class BillingTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser(
            email="admin@test.com",
            password="AdminPassword123!",
            first_name="Admin",
            last_name="User",
        )
        self.customer = User.objects.create_user(
            email="customer@test.com",
            password="CustomerPass123!",
            role=User.Role.CUSTOMER,
            first_name="Jane",
            last_name="Doe",
        )
        self.tax_18 = Tax.objects.create(name="GST 18%", rate_percent=Decimal("18.00"))
        today = timezone.localdate()
        self.plan_monthly = Plan.objects.create(
            name="Monthly Pro",
            billing_period=Plan.BillingPeriod.MONTHLY,
            price=Decimal("100.00"),
            available_from=today - datetime.timedelta(days=365),
            available_to=today + datetime.timedelta(days=365),
            is_active=True,
            closable=True,
            pausable=True,
        )
        self.product = Product.objects.create(
            name="Cloud Storage",
            sales_price=Decimal("50.00"),
            tax=self.tax_18,
            is_active=True,
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            attribute="Capacity",
            value="1TB",
            extra_price=Decimal("25.00"),
        )
        self.subscription = Subscription.objects.create(
            number="SUB-TEST-001",
            customer=self.customer,
            plan=self.plan_monthly,
            status=Subscription.Status.ACTIVE,
            start_date=datetime.date(2026, 1, 1),
            next_billing_date=datetime.date(2026, 1, 1),
            payment_terms=Subscription.PaymentTerms.DAYS_15,
            created_by=self.admin,
        )
        # Add 2 units of variant: Unit price = 50 + 25 = 75.00. Tax = 18%.
        # Line amount = 2 * 75 = 150.00. Tax = 150 * 0.18 = 27.00.
        SubscriptionLine.objects.create(
            subscription=self.subscription,
            product=self.product,
            variant=self.variant,
            quantity=2,
            unit_price=Decimal("75.00"),
            tax_percent=Decimal("18.00"),
            amount=Decimal("150.00"),
        )

    def test_generate_invoice_lines_and_tax_rounding(self):
        """Tests that invoice is generated with proper lines, ROUND_HALF_UP tax, and draft status."""
        invoice = generate_invoice(self.subscription, period_start=datetime.date(2026, 1, 1))
        self.assertIsNotNone(invoice)
        self.assertEqual(invoice.status, Invoice.Status.DRAFT)
        self.assertEqual(invoice.subtotal, Decimal("150.00"))
        self.assertEqual(invoice.tax_total, Decimal("27.00"))
        self.assertEqual(invoice.total, Decimal("177.00"))
        self.assertEqual(invoice.balance, Decimal("177.00"))
        self.assertEqual(invoice.period_start, datetime.date(2026, 1, 1))
        self.assertEqual(invoice.period_end, datetime.date(2026, 1, 31))

        # Check line items
        self.assertEqual(invoice.lines.count(), 1)
        line = invoice.lines.first()
        self.assertEqual(line.quantity, 2)
        self.assertEqual(line.unit_price, Decimal("75.00"))
        self.assertEqual(line.amount, Decimal("150.00"))

        # Verify next_billing_date advanced to 2026-02-01
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.next_billing_date, datetime.date(2026, 2, 1))

    def test_no_double_billing_on_repeated_runs(self):
        """Tests that running generate_invoice twice for the same period returns None and prevents duplicate."""
        inv1 = generate_invoice(self.subscription, period_start=datetime.date(2026, 1, 1))
        self.assertIsNotNone(inv1)

        # Call again for the exact same period
        inv2 = generate_invoice(self.subscription, period_start=datetime.date(2026, 1, 1))
        self.assertIsNone(inv2)

        # Confirm there is only 1 invoice in DB
        self.assertEqual(Invoice.objects.filter(subscription=self.subscription).count(), 1)

    def test_discount_application_row_locking_and_usage_limit(self):
        """Tests discount calculation, negative invoice line, and usage limit enforcement."""
        today = timezone.localdate()
        discount = Discount.objects.create(
            name="Launch 10% Off",
            discount_type=Discount.DiscountType.PERCENT,
            value=Decimal("10.00"),
            applies_to=Discount.AppliesTo.SUBSCRIPTION,
            start_date=today - datetime.timedelta(days=10),
            end_date=today + datetime.timedelta(days=30),
            usage_limit=1,
            times_used=0,
            is_active=True,
        )

        invoice = generate_invoice(self.subscription, period_start=datetime.date(2026, 1, 1))
        self.assertIsNotNone(invoice)

        # Subtotal = 150.00. 10% discount = 15.00. Tax = 27.00.
        # Total = 150 + 27 - 15 = 162.00
        self.assertEqual(invoice.subtotal, Decimal("150.00"))
        self.assertEqual(invoice.discount_total, Decimal("15.00"))
        self.assertEqual(invoice.total, Decimal("162.00"))

        # Check discount application audit and times_used
        discount.refresh_from_db()
        self.assertEqual(discount.times_used, 1)
        self.assertEqual(DiscountApplication.objects.filter(invoice=invoice, discount=discount).count(), 1)

        # Negative invoice line created
        disc_line = invoice.lines.get(amount=Decimal("-15.00"))
        self.assertIn("Launch 10% Off", disc_line.description)

        # Second period generation: usage_limit is 1 and times_used is 1, so discount should NOT be applied
        inv_period2 = generate_invoice(self.subscription, period_start=datetime.date(2026, 2, 1))
        self.assertIsNotNone(inv_period2)
        self.assertEqual(inv_period2.discount_total, Decimal("0.00"))
        self.assertEqual(inv_period2.total, Decimal("177.00"))

    def test_expired_discount_not_applied(self):
        """Tests that a discount with past valid_until date is not applied."""
        today = timezone.localdate()
        Discount.objects.create(
            name="Expired Promo",
            discount_type=Discount.DiscountType.FIXED,
            value=Decimal("20.00"),
            applies_to=Discount.AppliesTo.SUBSCRIPTION,
            start_date=today - datetime.timedelta(days=30),
            end_date=today - datetime.timedelta(days=1),
            usage_limit=10,
            times_used=0,
            is_active=True,
        )

        invoice = generate_invoice(self.subscription, period_start=datetime.date(2026, 1, 1))
        self.assertEqual(invoice.discount_total, Decimal("0.00"))
        self.assertEqual(invoice.total, Decimal("177.00"))

    def test_confirm_invoice(self):
        """Tests transitioning draft invoice to confirmed."""
        invoice = generate_invoice(self.subscription, period_start=datetime.date(2026, 1, 1))
        self.assertEqual(invoice.status, Invoice.Status.DRAFT)

        confirmed = confirm_invoice(invoice, user=self.admin)
        self.assertEqual(confirmed.status, Invoice.Status.CONFIRMED)

        # Confirming again raises ValidationError
        with self.assertRaises(ValidationError):
            confirm_invoice(confirmed, user=self.admin)

    def test_record_payment_partial_and_full(self):
        """Tests partial payments keep status confirmed and full payment marks invoice as paid."""
        invoice = generate_invoice(self.subscription, period_start=datetime.date(2026, 1, 1))
        confirm_invoice(invoice, user=self.admin)
        self.assertEqual(invoice.balance, Decimal("177.00"))

        # Partial payment of $77.00
        p1 = record_payment(
            invoice=invoice,
            method=Payment.Method.CARD,
            amount=Decimal("77.00"),
            user=self.admin,
        )
        self.assertEqual(p1.amount, Decimal("77.00"))
        invoice.refresh_from_db()
        self.assertEqual(invoice.amount_paid, Decimal("77.00"))
        self.assertEqual(invoice.balance, Decimal("100.00"))
        self.assertEqual(invoice.status, Invoice.Status.CONFIRMED)

        # Full payment of remaining $100.00
        p2 = record_payment(
            invoice=invoice,
            method=Payment.Method.BANK_TRANSFER,
            amount=Decimal("100.00"),
            user=self.admin,
        )
        invoice.refresh_from_db()
        self.assertEqual(invoice.amount_paid, Decimal("177.00"))
        self.assertEqual(invoice.balance, Decimal("0.00"))
        self.assertEqual(invoice.status, Invoice.Status.PAID)
        self.assertFalse(invoice.can_pay)

    def test_overpayment_rejected(self):
        """Tests that paying more than balance raises ValidationError."""
        invoice = generate_invoice(self.subscription, period_start=datetime.date(2026, 1, 1))
        confirm_invoice(invoice, user=self.admin)

        with self.assertRaises(ValidationError):
            record_payment(
                invoice=invoice,
                method=Payment.Method.CASH,
                amount=Decimal("200.00"),  # Total is 177.00
                user=self.admin,
            )

    def test_cancel_invoice_restores_discounts(self):
        """Tests that cancelling an invoice restores discount usage."""
        today = timezone.localdate()
        discount = Discount.objects.create(
            name="Promo 20",
            discount_type=Discount.DiscountType.FIXED,
            value=Decimal("20.00"),
            applies_to=Discount.AppliesTo.SUBSCRIPTION,
            start_date=today - datetime.timedelta(days=10),
            end_date=today + datetime.timedelta(days=30),
            usage_limit=5,
            times_used=0,
            is_active=True,
        )
        invoice = generate_invoice(self.subscription, period_start=datetime.date(2026, 1, 1))
        discount.refresh_from_db()
        self.assertEqual(discount.times_used, 1)

        # Cancel the invoice
        cancel_invoice(invoice, user=self.admin)
        invoice.refresh_from_db()
        self.assertEqual(invoice.status, Invoice.Status.CANCELLED)

        # Verify discount restored
        discount.refresh_from_db()
        self.assertEqual(discount.times_used, 0)
        self.assertEqual(invoice.discount_applications.count(), 0)

    def test_cannot_cancel_invoice_with_payments(self):
        """Tests that an invoice with recorded payments cannot be cancelled."""
        invoice = generate_invoice(self.subscription, period_start=datetime.date(2026, 1, 1))
        confirm_invoice(invoice, user=self.admin)
        record_payment(
            invoice=invoice,
            method=Payment.Method.UPI,
            amount=Decimal("50.00"),
            user=self.admin,
        )

        with self.assertRaises(ValidationError):
            cancel_invoice(invoice, user=self.admin)

    def test_overdue_queryset_and_properties(self):
        """Tests is_overdue property and Invoice.objects.overdue() filtering."""
        today = timezone.localdate()
        past_date = today - datetime.timedelta(days=10)

        invoice = generate_invoice(self.subscription, period_start=datetime.date(2026, 1, 1))
        confirm_invoice(invoice, user=self.admin)
        invoice.due_date = past_date
        invoice.save(update_fields=["due_date"])

        self.assertTrue(invoice.is_overdue)
        self.assertEqual(invoice.days_overdue, 10)
        self.assertIn(invoice, Invoice.objects.overdue())

        # Once paid, should not be overdue
        record_payment(
            invoice=invoice,
            method=Payment.Method.CARD,
            amount=invoice.total,
            user=self.admin,
        )
        invoice.refresh_from_db()
        self.assertFalse(invoice.is_overdue)
        self.assertNotIn(invoice, Invoice.objects.overdue())

    def test_send_invoice_email(self):
        """Tests sending email to customer and updating sent_at timestamp."""
        invoice = generate_invoice(self.subscription, period_start=datetime.date(2026, 1, 1))
        confirm_invoice(invoice, user=self.admin)

        self.assertIsNone(invoice.sent_at)
        result = send_invoice_email(invoice)
        self.assertTrue(result)
        invoice.refresh_from_db()
        self.assertIsNotNone(invoice.sent_at)

    def test_generate_invoices_management_command(self):
        """Tests generate_invoices command execution with multi-period catch-up."""
        # Active subscription with next_billing_date 2 months ago
        today = timezone.localdate()
        two_months_ago = today - datetime.timedelta(days=60)
        self.subscription.next_billing_date = two_months_ago
        self.subscription.save(update_fields=["next_billing_date"])

        # Dry run first
        call_command("generate_invoices", "--dry-run")
        self.subscription.refresh_from_db()
        self.assertEqual(self.subscription.next_billing_date, two_months_ago)

        # Real run
        call_command("generate_invoices")
        self.subscription.refresh_from_db()
        # Should have caught up until next_billing_date > today
        self.assertGreater(self.subscription.next_billing_date, today)
        self.assertGreater(self.subscription.invoices.count(), 1)
