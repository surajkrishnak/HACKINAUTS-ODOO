import datetime
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from catalog.models import Discount, Plan, Product, ProductVariant, Tax
from catalog.services import (
    close_plan_early,
    get_applicable_discounts,
    pause_plan,
    renew_plan,
    resume_plan,
)
from subscriptions.models import Subscription, SubscriptionLine

User = get_user_model()


class CatalogTests(TestCase):
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
            email="cust@brightloop.test",
            password="Password@123",
            role=User.Role.CUSTOMER,
        )

        self.tax_18 = Tax.objects.create(name="GST 18%", rate_percent=Decimal("18.00"))

        self.product = Product.objects.create(
            name="ERP Core",
            product_type=Product.ProductType.SOFTWARE,
            sales_price=Decimal("1000.00"),
            cost_price=Decimal("200.00"),
            tax=self.tax_18,
        )
        self.variant_odoo = ProductVariant.objects.create(
            product=self.product,
            attribute="Brand",
            value="Odoo",
            extra_price=Decimal("560.00"),
        )

        self.plan = Plan.objects.create(
            name="Monthly Standard",
            billing_period=Plan.BillingPeriod.MONTHLY,
            price=Decimal("299.00"),
            min_quantity=1,
            available_from=self.today - datetime.timedelta(days=30),
            available_to=self.today + datetime.timedelta(days=30),
            closable=True,
            pausable=True,
            renewable=True,
            auto_close=False,
        )

        self.non_closable_plan = Plan.objects.create(
            name="Fixed Term Plan",
            billing_period=Plan.BillingPeriod.YEARLY,
            price=Decimal("1200.00"),
            min_quantity=1,
            available_from=self.today - datetime.timedelta(days=30),
            available_to=self.today + datetime.timedelta(days=30),
            closable=False,
            pausable=False,
            renewable=False,
            auto_close=True,
        )

    # 1. Plan available_to before available_from validation
    def test_plan_available_to_before_available_from_rejected(self):
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("catalog:plan_create"),
            {
                "name": "Invalid Date Plan",
                "billing_period": Plan.BillingPeriod.MONTHLY,
                "price": "99.00",
                "min_quantity": 1,
                "available_from": (self.today + datetime.timedelta(days=10)).isoformat(),
                "available_to": self.today.isoformat(),  # Before start date
                "is_active": True,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["form"],
            "available_to",
            "Available To date must be after Available From date.",
        )

    # 2. Closable flag controls early-close action
    def test_closable_flag_controls_early_close(self):
        # Closable plan succeeds
        close_plan_early(self.plan)
        self.plan.refresh_from_db()
        self.assertFalse(self.plan.is_active)

        # Non-closable plan raises ValidationError
        with self.assertRaises(ValidationError):
            close_plan_early(self.non_closable_plan)

    def test_plan_lifecycle_actions_service(self):
        # Pause / Resume
        pause_plan(self.plan)
        self.plan.refresh_from_db()
        self.assertTrue(self.plan.is_paused)

        resume_plan(self.plan)
        self.plan.refresh_from_db()
        self.assertFalse(self.plan.is_paused)

        # Renew
        future_date = self.today + datetime.timedelta(days=90)
        renew_plan(self.plan, future_date)
        self.plan.refresh_from_db()
        self.assertEqual(self.plan.available_to, future_date)
        self.assertTrue(self.plan.is_active)

    # 3. Discount at usage limit shows Exhausted
    def test_discount_at_usage_limit_shows_exhausted(self):
        discount = Discount.objects.create(
            name="LIMITED10",
            discount_type=Discount.DiscountType.PERCENT,
            value=Decimal("10.00"),
            start_date=self.today - datetime.timedelta(days=10),
            end_date=self.today + datetime.timedelta(days=10),
            usage_limit=5,
            times_used=5,  # Reached limit
        )
        self.assertEqual(discount.computed_status, "Exhausted")

        self.client.force_login(self.admin)
        response = self.client.get(reverse("catalog:discount_list"))
        self.assertContains(response, "Exhausted")

    # 4. Staff cannot create a discount (Forbidden 403)
    def test_staff_cannot_create_discount(self):
        self.client.force_login(self.staff)
        response = self.client.get(reverse("catalog:discount_create"))
        self.assertEqual(response.status_code, 403)

        post_resp = self.client.post(
            reverse("catalog:discount_create"),
            {
                "name": "STAFF_DISCOUNT",
                "discount_type": "percent",
                "value": "15.00",
                "start_date": self.today.isoformat(),
                "end_date": (self.today + datetime.timedelta(days=30)).isoformat(),
                "usage_limit": 10,
                "applies_to": "subscription",
            },
        )
        self.assertEqual(post_resp.status_code, 403)

    def test_staff_can_view_products_and_plans_readonly(self):
        self.client.force_login(self.staff)
        # Can view products list and detail
        p_list = self.client.get(reverse("catalog:product_list"))
        self.assertEqual(p_list.status_code, 200)
        p_detail = self.client.get(reverse("catalog:product_detail", kwargs={"pk": self.product.pk}))
        self.assertEqual(p_detail.status_code, 200)

        # Cannot edit products
        p_edit = self.client.get(reverse("catalog:product_edit", kwargs={"pk": self.product.pk}))
        self.assertEqual(p_edit.status_code, 403)

        # Can view plans list and detail
        pl_list = self.client.get(reverse("catalog:plan_list"))
        self.assertEqual(pl_list.status_code, 200)
        pl_detail = self.client.get(reverse("catalog:plan_detail", kwargs={"pk": self.plan.pk}))
        self.assertEqual(pl_detail.status_code, 200)

        # Cannot edit plans
        pl_edit = self.client.get(reverse("catalog:plan_edit", kwargs={"pk": self.plan.pk}))
        self.assertEqual(pl_edit.status_code, 403)

    def test_customer_receives_403_on_catalog(self):
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get(reverse("catalog:product_list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("catalog:plan_list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("catalog:tax_list")).status_code, 403)
        self.assertEqual(self.client.get(reverse("catalog:discount_list")).status_code, 403)

    # 5. Product effective price test
    def test_variant_effective_price(self):
        # sales_price 1000 + extra 560 = 1560
        self.assertEqual(self.variant_odoo.effective_price, Decimal("1560.00"))

    # 6. Management command close_expired_plans
    def test_close_expired_plans_command(self):
        expired_auto_plan = Plan.objects.create(
            name="Past Auto Plan",
            billing_period=Plan.BillingPeriod.DAILY,
            price=Decimal("10.00"),
            available_from=self.today - datetime.timedelta(days=60),
            available_to=self.today - datetime.timedelta(days=5),  # In the past
            auto_close=True,
            is_active=True,
        )
        call_command("close_expired_plans")
        expired_auto_plan.refresh_from_db()
        self.assertFalse(expired_auto_plan.is_active)

    # 7. Discount applicability service
    def test_get_applicable_discounts_service(self):
        # Create a subscription with lines
        sub = Subscription.objects.create(
            number="SUB-TEST01",
            customer=self.customer,
            plan=self.plan,
            status=Subscription.Status.ACTIVE,
            start_date=self.today,
        )
        SubscriptionLine.objects.create(
            subscription=sub,
            product=self.product,
            quantity=2,
            unit_price=self.product.sales_price,
            amount=Decimal("2000.00"),
        )

        d_valid = Discount.objects.create(
            name="VALID_SUB",
            discount_type=Discount.DiscountType.PERCENT,
            value=Decimal("10.00"),
            start_date=self.today - datetime.timedelta(days=5),
            end_date=self.today + datetime.timedelta(days=5),
            usage_limit=10,
            times_used=0,
            min_purchase=Decimal("500.00"),
            applies_to=Discount.AppliesTo.SUBSCRIPTION,
        )

        d_high_min = Discount.objects.create(
            name="TOO_EXPENSIVE",
            discount_type=Discount.DiscountType.FIXED,
            value=Decimal("100.00"),
            start_date=self.today - datetime.timedelta(days=5),
            end_date=self.today + datetime.timedelta(days=5),
            usage_limit=10,
            times_used=0,
            min_purchase=Decimal("50000.00"),  # Higher than 2000.00
            applies_to=Discount.AppliesTo.SUBSCRIPTION,
        )

        applicable = get_applicable_discounts(sub, self.today)
        self.assertIn(d_valid, applicable)
        self.assertNotIn(d_high_min, applicable)
