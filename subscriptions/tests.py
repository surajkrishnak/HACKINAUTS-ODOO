import datetime
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.models import Plan, Product, ProductVariant, Tax
from subscriptions.models import (
    QuotationTemplate,
    QuotationTemplateLine,
    StatusHistory,
    Subscription,
    SubscriptionLine,
)
from subscriptions.services import (
    create_subscription_from_template,
    pause_subscription,
    resume_subscription,
    transition,
)

User = get_user_model()


class SubscriptionTests(TestCase):
    def setUp(self):
        self.today = timezone.localdate()

        self.admin = User.objects.create_superuser(
            email="admin@brightloop.test",
            password="Password@123",
            role=User.Role.ADMIN,
        )
        self.staff = User.objects.create_user(
            email="staff@brightloop.test",
            password="Password@123",
            role=User.Role.STAFF,
            is_staff=True,
        )
        self.customer = User.objects.create_user(
            email="customer@brightloop.test",
            password="Password@123",
            role=User.Role.CUSTOMER,
        )

        self.tax_18 = Tax.objects.create(name="GST 18%", rate_percent=Decimal("18.00"))

        self.product_erp = Product.objects.create(
            name="Enterprise ERP Suite",
            product_type=Product.ProductType.SOFTWARE,
            sales_price=Decimal("1200.00"),
            cost_price=Decimal("300.00"),
            tax=self.tax_18,
        )
        self.variant_odoo = ProductVariant.objects.create(
            product=self.product_erp,
            attribute="Brand",
            value="Odoo",
            extra_price=Decimal("560.00"),
        )

        self.product_addon = Product.objects.create(
            name="API Connector",
            product_type=Product.ProductType.ADDON,
            sales_price=Decimal("99.00"),
            cost_price=Decimal("15.00"),
            tax=self.tax_18,
        )

        self.plan_closable = Plan.objects.create(
            name="Monthly Pro",
            billing_period=Plan.BillingPeriod.MONTHLY,
            price=Decimal("299.00"),
            min_quantity=1,
            available_from=self.today - datetime.timedelta(days=30),
            available_to=self.today + datetime.timedelta(days=365),
            closable=True,
            pausable=True,
            renewable=True,
            auto_close=False,
        )

        self.plan_non_closable = Plan.objects.create(
            name="Strict Annual",
            billing_period=Plan.BillingPeriod.YEARLY,
            price=Decimal("2999.00"),
            min_quantity=1,
            available_from=self.today - datetime.timedelta(days=30),
            available_to=self.today + datetime.timedelta(days=365),
            closable=False,
            pausable=False,
            renewable=True,
            auto_close=True,
        )

        self.template = QuotationTemplate.objects.create(
            name="ERP + Odoo Bundle",
            validity_days=30,
            plan=self.plan_closable,
        )
        QuotationTemplateLine.objects.create(
            template=self.template,
            product=self.product_erp,
            variant=self.variant_odoo,
            quantity=2,
        )
        QuotationTemplateLine.objects.create(
            template=self.template,
            product=self.product_addon,
            variant=None,
            quantity=1,
        )

    # 1. Template creates lines with right snapshot price including Odoo +560 variant
    def test_template_creates_lines_with_snapshot_prices(self):
        sub = create_subscription_from_template(
            template=self.template,
            customer=self.customer,
            start_date=self.today,
            user=self.admin,
        )
        self.assertEqual(sub.status, Subscription.Status.DRAFT)
        self.assertEqual(sub.lines.count(), 2)

        # Line 1: ERP + Odoo variant: 1200 + 560 = 1760; quantity 2; amount 3520
        line1 = sub.lines.get(product=self.product_erp)
        self.assertEqual(line1.unit_price, Decimal("1760.00"))
        self.assertEqual(line1.tax_percent, Decimal("18.00"))
        self.assertEqual(line1.quantity, 2)
        self.assertEqual(line1.amount, Decimal("3520.00"))

        # Line 2: API connector: 99.00; quantity 1; amount 99.00
        line2 = sub.lines.get(product=self.product_addon)
        self.assertEqual(line2.unit_price, Decimal("99.00"))
        self.assertEqual(line2.tax_percent, Decimal("18.00"))
        self.assertEqual(line2.quantity, 1)
        self.assertEqual(line2.amount, Decimal("99.00"))

        # Expiry date should equal start_date + validity_days (30)
        self.assertEqual(sub.expiry_date, self.today + datetime.timedelta(days=30))

    # 2. Invalid transitions are blocked
    def test_invalid_transitions_are_blocked(self):
        sub = create_subscription_from_template(self.template, self.customer, self.today, self.admin)
        # Cannot jump from Draft directly to Active
        with self.assertRaises(ValidationError):
            transition(sub, Subscription.Status.ACTIVE, user=self.admin)

        # Cannot jump from Draft directly to Confirmed
        with self.assertRaises(ValidationError):
            transition(sub, Subscription.Status.CONFIRMED, user=self.admin)

        # Valid: Draft -> Quotation -> Confirmed -> Active
        transition(sub, Subscription.Status.QUOTATION, user=self.admin)
        self.assertEqual(sub.status, Subscription.Status.QUOTATION)

        transition(sub, Subscription.Status.CONFIRMED, user=self.admin)
        self.assertEqual(sub.status, Subscription.Status.CONFIRMED)

        transition(sub, Subscription.Status.ACTIVE, user=self.admin)
        self.assertEqual(sub.status, Subscription.Status.ACTIVE)
        # On activation, next_billing_date is set to start_date
        self.assertEqual(sub.next_billing_date, sub.start_date)

        # Cannot move backwards from Active to Quotation or Draft
        with self.assertRaises(ValidationError):
            transition(sub, Subscription.Status.QUOTATION, user=self.admin)

    # 3. Status History is recorded on every transition
    def test_history_is_recorded(self):
        sub = create_subscription_from_template(self.template, self.customer, self.today, self.admin)
        # 1 history record created during creation
        self.assertEqual(sub.status_history.count(), 1)

        transition(sub, Subscription.Status.QUOTATION, user=self.staff)
        transition(sub, Subscription.Status.CONFIRMED, user=self.admin)

        self.assertEqual(sub.status_history.count(), 3)
        last_hist = sub.status_history.first()  # ordered -changed_at
        self.assertEqual(last_hist.from_status, Subscription.Status.QUOTATION)
        self.assertEqual(last_hist.to_status, Subscription.Status.CONFIRMED)
        self.assertEqual(last_hist.changed_by, self.admin)

    # 4. Subscription numbers never repeat
    def test_subscription_numbers_never_repeat(self):
        sub1 = create_subscription_from_template(self.template, self.customer, self.today, self.admin)
        sub2 = create_subscription_from_template(self.template, self.customer, self.today, self.admin)
        sub3 = create_subscription_from_template(self.template, self.customer, self.today, self.admin)

        numbers = [sub1.number, sub2.number, sub3.number]
        self.assertEqual(len(numbers), len(set(numbers)))
        self.assertTrue(all(num.startswith("SUB-") for num in numbers))

    # 5. Plan closable rule when closing active subscription
    def test_plan_closable_rule_enforced_on_early_close(self):
        # Create subscription with non-closable plan
        sub = Subscription.objects.create(
            number="SUB-STRICT01",
            customer=self.customer,
            plan=self.plan_non_closable,
            status=Subscription.Status.ACTIVE,
            start_date=self.today,
            expiry_date=self.today + datetime.timedelta(days=180),
        )

        # Closing early must raise ValidationError because plan.closable is False
        with self.assertRaises(ValidationError):
            transition(sub, Subscription.Status.CLOSED, user=self.admin)

        # If plan is closable, early close succeeds
        sub_closable = Subscription.objects.create(
            number="SUB-CLOSE01",
            customer=self.customer,
            plan=self.plan_closable,
            status=Subscription.Status.ACTIVE,
            start_date=self.today,
            expiry_date=self.today + datetime.timedelta(days=180),
        )
        transition(sub_closable, Subscription.Status.CLOSED, user=self.admin)
        sub_closable.refresh_from_db()
        self.assertEqual(sub_closable.status, Subscription.Status.CLOSED)

    # 6. Management command close_expired_subscriptions
    def test_close_expired_subscriptions_command(self):
        expired_sub = Subscription.objects.create(
            number="SUB-EXPIRED01",
            customer=self.customer,
            plan=self.plan_non_closable,  # has auto_close=True
            status=Subscription.Status.ACTIVE,
            start_date=self.today - datetime.timedelta(days=400),
            expiry_date=self.today - datetime.timedelta(days=35),  # expired in past
        )
        call_command("close_expired_subscriptions")
        expired_sub.refresh_from_db()
        self.assertEqual(expired_sub.status, Subscription.Status.CLOSED)

    # 7. Customer access is forbidden (403)
    def test_customer_cannot_view_internal_subscriptions(self):
        self.client.force_login(self.customer)
        response = self.client.get(reverse("subscriptions:subscription_list"))
        self.assertEqual(response.status_code, 403)

        sub = create_subscription_from_template(self.template, self.customer, self.today, self.admin)
        detail_resp = self.client.get(reverse("subscriptions:subscription_detail", kwargs={"pk": sub.pk}))
        self.assertEqual(detail_resp.status_code, 403)

    # 8. Staff and Admin can view and manage
    def test_staff_and_admin_can_access(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("subscriptions:subscription_list"))
        self.assertEqual(response.status_code, 200)

        # Wizard
        wizard_resp = self.client.get(reverse("subscriptions:subscription_from_template"))
        self.assertEqual(wizard_resp.status_code, 200)
