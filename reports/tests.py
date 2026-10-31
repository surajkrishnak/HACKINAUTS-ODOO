import datetime
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from billing.models import Invoice, Payment
from catalog.models import Plan, Product, Tax
from reports.queries import get_dashboard_metrics, get_monthly_revenue_last_12_months
from subscriptions.models import Subscription, SubscriptionLine

User = get_user_model()


class ReportsDashboardTests(TestCase):
    def setUp(self):
        self.client = Client()
        cache.clear()
        today = timezone.localdate()

        self.admin = User.objects.create_superuser(
            email="admin@test.com",
            password="AdminPassword123!",
        )
        self.staff = User.objects.create_user(
            email="staff@test.com",
            password="StaffPassword123!",
            role=User.Role.STAFF,
        )
        self.customer = User.objects.create_user(
            email="customer@test.com",
            password="CustPassword123!",
            role=User.Role.CUSTOMER,
        )

        self.tax_18 = Tax.objects.create(name="GST 18%", rate_percent=Decimal("18.00"))

        self.plan_monthly = Plan.objects.create(
            name="Monthly Plan",
            billing_period=Plan.BillingPeriod.MONTHLY,
            price=Decimal("100.00"),
            available_from=today - datetime.timedelta(days=30),
            available_to=today + datetime.timedelta(days=365),
            is_active=True,
        )
        self.plan_yearly = Plan.objects.create(
            name="Yearly Plan",
            billing_period=Plan.BillingPeriod.YEARLY,
            price=Decimal("1200.00"),
            available_from=today - datetime.timedelta(days=30),
            available_to=today + datetime.timedelta(days=365),
            is_active=True,
        )

        self.product = Product.objects.create(
            name="Analytics Suite",
            sales_price=Decimal("100.00"),
            cost_price=Decimal("30.00"),
            tax=self.tax_18,
            is_active=True,
        )

        # 1 Monthly Active Sub: Line amount = 100.00 (MRR += 100.00, Cost += 30.00)
        self.sub_m = Subscription.objects.create(
            number="SUB-M01",
            customer=self.customer,
            plan=self.plan_monthly,
            status=Subscription.Status.ACTIVE,
            start_date=today,
            next_billing_date=today + datetime.timedelta(days=30),
        )
        SubscriptionLine.objects.create(
            subscription=self.sub_m,
            product=self.product,
            quantity=1,
            unit_price=Decimal("100.00"),
            tax_percent=Decimal("18.00"),
            amount=Decimal("100.00"),
        )

        # 1 Yearly Active Sub: Line amount = 1200.00 (MRR += 1200/12 = 100.00, Cost += (30*1)/12 = 2.50)
        self.sub_y = Subscription.objects.create(
            number="SUB-Y01",
            customer=self.customer,
            plan=self.plan_yearly,
            status=Subscription.Status.ACTIVE,
            start_date=today,
            next_billing_date=today + datetime.timedelta(days=365),
        )
        SubscriptionLine.objects.create(
            subscription=self.sub_y,
            product=self.product,
            quantity=1,
            unit_price=Decimal("1200.00"),
            tax_percent=Decimal("18.00"),
            amount=Decimal("1200.00"),
        )

        # Confirmed Overdue Invoice: Total = 200.00, Paid = 50.00, Balance = 150.00
        self.inv_overdue = Invoice.objects.create(
            number="INV-OD01",
            subscription=self.sub_m,
            customer=self.customer,
            status=Invoice.Status.CONFIRMED,
            invoice_date=today - datetime.timedelta(days=40),
            due_date=today - datetime.timedelta(days=10),
            period_start=today - datetime.timedelta(days=40),
            period_end=today - datetime.timedelta(days=10),
            subtotal=Decimal("200.00"),
            tax_total=Decimal("0.00"),
            total=Decimal("200.00"),
            amount_paid=Decimal("50.00"),
        )

        # Payment of $50.00
        self.payment = Payment.objects.create(
            invoice=self.inv_overdue,
            method=Payment.Method.CARD,
            amount=Decimal("50.00"),
            payment_date=today - datetime.timedelta(days=5),
        )

    def test_dashboard_access_control(self):
        """Admin and Staff have access to reports dashboard; customers get 403."""
        # Admin access
        self.client.force_login(self.admin)
        resp_admin = self.client.get(reverse("reports:dashboard"))
        self.assertEqual(resp_admin.status_code, 200)

        # Staff access
        self.client.force_login(self.staff)
        resp_staff = self.client.get(reverse("reports:dashboard"))
        self.assertEqual(resp_staff.status_code, 200)

        # Customer access -> 403 Forbidden
        self.client.force_login(self.customer)
        resp_cust = self.client.get(reverse("reports:dashboard"))
        self.assertEqual(resp_cust.status_code, 403)

    def test_admin_vs_staff_profitability_visibility(self):
        """Admin sees internal profit and cost analysis; staff view hides it."""
        # Admin view
        self.client.force_login(self.admin)
        resp_admin = self.client.get(reverse("reports:dashboard"))
        self.assertContains(resp_admin, "Profitability & Cost Analysis")
        self.assertContains(resp_admin, "Estimated Monthly Cost")

        # Staff view
        self.client.force_login(self.staff)
        resp_staff = self.client.get(reverse("reports:dashboard"))
        self.assertNotContains(resp_staff, "Profitability & Cost Analysis")
        self.assertNotContains(resp_staff, "Estimated Monthly Cost")

    def test_aggregate_metrics_calculation(self):
        """Validates MRR normalization, revenue collected, and overdue aggregates."""
        metrics = get_dashboard_metrics()

        # 2 active subscriptions
        self.assertEqual(metrics["active_subscriptions_count"], 2)

        # MRR = 100.00 (monthly) + 100.00 (yearly 1200/12) = 200.00
        self.assertEqual(metrics["mrr"], Decimal("200.00"))

        # Revenue collected = 50.00
        self.assertEqual(metrics["revenue_collected"], Decimal("50.00"))

        # Outstanding unpaid amount = 150.00
        self.assertEqual(metrics["outstanding_amount"], Decimal("150.00"))

        # Overdue count = 1, overdue amount = 150.00
        self.assertEqual(metrics["overdue_count"], 1)
        self.assertEqual(metrics["overdue_amount"], Decimal("150.00"))

    def test_monthly_revenue_caching(self):
        """Tests that get_monthly_revenue_last_12_months caches result in memory."""
        cache.clear()
        first_call = get_monthly_revenue_last_12_months()
        self.assertIn("labels", first_call)
        self.assertEqual(len(first_call["labels"]), 12)

        # Second call hits cache
        second_call = get_monthly_revenue_last_12_months()
        self.assertEqual(first_call, second_call)

    def test_csv_exports(self):
        """Validates CSV export endpoint for invoices, payments, and subscriptions."""
        self.client.force_login(self.admin)

        for exp_type in ["invoices", "payments", "subscriptions"]:
            resp = self.client.get(reverse("reports:export_csv"), {"type": exp_type})
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp["Content-Type"], "text/csv")
            self.assertIn(f"brightloop_{exp_type}_", resp["Content-Disposition"])

    def test_timing_middleware_header(self):
        """Every response processed by Django must have the X-Response-Time header."""
        resp = self.client.get(reverse("home"))
        self.assertIn("X-Response-Time", resp.headers)
        self.assertTrue(resp.headers["X-Response-Time"].endswith("ms"))
