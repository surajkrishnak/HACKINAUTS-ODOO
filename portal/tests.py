import datetime
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from billing.models import Invoice
from catalog.models import Plan, Product, Tax
from portal.models import DiscountRequest
from subscriptions.models import Subscription, SubscriptionLine

User = get_user_model()


class CustomerPortalTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.tax_18 = Tax.objects.create(name="GST 18%", rate_percent=Decimal("18.00"))
        today = timezone.localdate()

        self.plan_closable = Plan.objects.create(
            name="Flexible Plan",
            billing_period=Plan.BillingPeriod.MONTHLY,
            price=Decimal("100.00"),
            available_from=today - datetime.timedelta(days=30),
            available_to=today + datetime.timedelta(days=365),
            is_active=True,
            closable=True,
            pausable=True,
        )
        self.plan_fixed = Plan.objects.create(
            name="Fixed Term Plan",
            billing_period=Plan.BillingPeriod.YEARLY,
            price=Decimal("1200.00"),
            available_from=today - datetime.timedelta(days=30),
            available_to=today + datetime.timedelta(days=365),
            is_active=True,
            closable=False,
            pausable=False,
        )
        self.product = Product.objects.create(
            name="Core Software",
            sales_price=Decimal("100.00"),
            cost_price=Decimal("20.00"),
            tax=self.tax_18,
            is_active=True,
        )

        # Customer A
        self.customer_a = User.objects.create_user(
            email="cust_a@test.com",
            password="CustPassword123!",
            role=User.Role.CUSTOMER,
            first_name="Alice",
            last_name="Customer",
        )
        # Customer B
        self.customer_b = User.objects.create_user(
            email="cust_b@test.com",
            password="CustPassword123!",
            role=User.Role.CUSTOMER,
            first_name="Bob",
            last_name="Customer",
        )
        # Staff & Admin
        self.staff = User.objects.create_user(
            email="staff@test.com",
            password="StaffPassword123!",
            role=User.Role.STAFF,
        )
        self.admin = User.objects.create_superuser(
            email="admin@test.com",
            password="AdminPassword123!",
        )

        # Subscriptions
        self.sub_a = Subscription.objects.create(
            number="SUB-CUST-A",
            customer=self.customer_a,
            plan=self.plan_closable,
            status=Subscription.Status.ACTIVE,
            start_date=today,
            next_billing_date=today + datetime.timedelta(days=30),
        )
        SubscriptionLine.objects.create(
            subscription=self.sub_a,
            product=self.product,
            quantity=1,
            unit_price=Decimal("100.00"),
            tax_percent=Decimal("18.00"),
            amount=Decimal("100.00"),
        )

        self.sub_b = Subscription.objects.create(
            number="SUB-CUST-B",
            customer=self.customer_b,
            plan=self.plan_fixed,
            status=Subscription.Status.ACTIVE,
            start_date=today,
            next_billing_date=today + datetime.timedelta(days=365),
        )

        # Invoices
        self.inv_a = Invoice.objects.create(
            number="INV-CUST-A",
            subscription=self.sub_a,
            customer=self.customer_a,
            status=Invoice.Status.CONFIRMED,
            invoice_date=today,
            due_date=today + datetime.timedelta(days=15),
            period_start=today,
            period_end=today + datetime.timedelta(days=30),
            subtotal=Decimal("100.00"),
            tax_total=Decimal("18.00"),
            total=Decimal("118.00"),
            amount_paid=Decimal("0.00"),
        )
        self.inv_b = Invoice.objects.create(
            number="INV-CUST-B",
            subscription=self.sub_b,
            customer=self.customer_b,
            status=Invoice.Status.CONFIRMED,
            invoice_date=today,
            due_date=today + datetime.timedelta(days=15),
            period_start=today,
            period_end=today + datetime.timedelta(days=365),
            subtotal=Decimal("1200.00"),
            tax_total=Decimal("216.00"),
            total=Decimal("1416.00"),
            amount_paid=Decimal("0.00"),
        )

    def test_customer_isolation_subscription_access(self):
        """Customer A can view their own subscription, but GET on Customer B's returns 404."""
        self.client.force_login(self.customer_a)

        # Own subscription -> 200 OK
        resp_own = self.client.get(reverse("portal:subscription_detail", args=[self.sub_a.pk]))
        self.assertEqual(resp_own.status_code, 200)
        self.assertContains(resp_own, "SUB-CUST-A")

        # Other customer's subscription -> 404 NOT FOUND
        resp_other = self.client.get(reverse("portal:subscription_detail", args=[self.sub_b.pk]))
        self.assertEqual(resp_other.status_code, 404)

    def test_customer_isolation_invoice_access(self):
        """Customer A can view their own invoice, but GET on Customer B's returns 404."""
        self.client.force_login(self.customer_a)

        # Own invoice -> 200 OK
        resp_own = self.client.get(reverse("portal:invoice_detail", args=[self.inv_a.pk]))
        self.assertEqual(resp_own.status_code, 200)
        self.assertContains(resp_own, "INV-CUST-A")

        # Other customer's invoice -> 404 NOT FOUND
        resp_other = self.client.get(reverse("portal:invoice_detail", args=[self.inv_b.pk]))
        self.assertEqual(resp_other.status_code, 404)

    def test_customer_forbidden_from_internal_screens(self):
        """Customer attempting to access admin/staff screens receives 403 Forbidden."""
        self.client.force_login(self.customer_a)

        # Products list -> 403
        resp_products = self.client.get(reverse("catalog:product_list"))
        self.assertEqual(resp_products.status_code, 403)

        # Internal Invoices list -> 403
        resp_invoices = self.client.get(reverse("billing:invoice_list"))
        self.assertEqual(resp_invoices.status_code, 403)

        # Reports dashboard -> 403
        resp_reports = self.client.get(reverse("reports:dashboard"))
        self.assertEqual(resp_reports.status_code, 403)

    def test_customer_lifecycle_actions(self):
        """Customer can pause and close their subscription when the plan permits it."""
        self.client.force_login(self.customer_a)

        # Pause subscription
        resp_pause = self.client.post(
            reverse("portal:subscription_action", args=[self.sub_a.pk]),
            {"action": "pause"},
            follow=True,
        )
        self.assertEqual(resp_pause.status_code, 200)
        self.sub_a.refresh_from_db()
        self.assertTrue(self.sub_a.is_paused)

        # Resume subscription
        resp_resume = self.client.post(
            reverse("portal:subscription_action", args=[self.sub_a.pk]),
            {"action": "resume"},
            follow=True,
        )
        self.assertEqual(resp_resume.status_code, 200)
        self.sub_a.refresh_from_db()
        self.assertFalse(self.sub_a.is_paused)

        # Close subscription (plan is closable)
        resp_close = self.client.post(
            reverse("portal:subscription_action", args=[self.sub_a.pk]),
            {"action": "close"},
            follow=True,
        )
        self.assertEqual(resp_close.status_code, 200)
        self.sub_a.refresh_from_db()
        self.assertEqual(self.sub_a.status, Subscription.Status.CLOSED)

    def test_customer_cannot_close_non_closable_plan(self):
        """Customer B cannot close a subscription whose plan is not closable."""
        self.client.force_login(self.customer_b)

        self.client.post(
            reverse("portal:subscription_action", args=[self.sub_b.pk]),
            {"action": "close"},
            follow=True,
        )
        self.sub_b.refresh_from_db()
        # Remains ACTIVE
        self.assertEqual(self.sub_b.status, Subscription.Status.ACTIVE)

    def test_discount_request_workflow(self):
        """Customer submits discount request, admin approves it."""
        self.client.force_login(self.customer_a)

        # Submit request
        resp_submit = self.client.post(
            reverse("portal:discount_request"),
            {
                "subscription": self.sub_a.pk,
                "seats": 25,
                "message": "Team expanding next quarter.",
            },
            follow=True,
        )
        self.assertEqual(resp_submit.status_code, 200)
        req = DiscountRequest.objects.get(customer=self.customer_a, subscription=self.sub_a)
        self.assertEqual(req.seats, 25)
        self.assertEqual(req.status, DiscountRequest.Status.PENDING)

        # Admin approves request
        self.client.force_login(self.admin)
        resp_action = self.client.post(
            reverse("portal:admin_discount_request_action", args=[req.pk]),
            {"action": "approve"},
        )
        self.assertEqual(resp_action.status_code, 302)
        req.refresh_from_db()
        self.assertEqual(req.status, DiscountRequest.Status.APPROVED)
