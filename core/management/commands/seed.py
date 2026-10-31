"""
Database seed command for Brightloop Software Subscription Management System.

Populates initial seed data:
- 1 Admin user (admin@brightloop.test)
- 1 Staff user (staff@brightloop.test)
- 20 Customer accounts (customer1@brightloop.test ... customer20@brightloop.test)
- 3 Taxes (0%, 5%, 18%)
- 8 Products with variants (includes Brand = Odoo with extra_price 560)
- 4 Plans (Daily, Weekly, Monthly, Yearly)
- 2 Quotation templates
- 2 Discounts
- Optional --bulk N flag to generate N subscriptions with lines, invoices, and payments.
All demo passwords: Password@123
"""
import datetime
import random
from decimal import Decimal
from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from catalog.models import Discount, Plan, Product, ProductVariant, Tax
from subscriptions.models import (
    QuotationTemplate,
    QuotationTemplateLine,
    StatusHistory,
    Subscription,
    SubscriptionLine,
)
from billing.models import Invoice, InvoiceLine, Payment
from core.utils import add_period

User = get_user_model()


class Command(BaseCommand):
    help = "Seed database with demo users, catalog, plans, discounts, templates, and optional bulk subscriptions."

    def add_arguments(self, parser):
        parser.add_argument(
            "--bulk",
            type=int,
            default=0,
            help="Generate N subscriptions with lines, invoices, and payments (spread over the past 12 months).",
        )

    def handle(self, *args, **options):
        bulk_count = options["bulk"]
        self.stdout.write(self.style.NOTICE("--- Starting Brightloop Database Seeding ---"))

        demo_password = "Password@123"

        with transaction.atomic():
            # 1. Admin user
            admin_user, _ = User.objects.get_or_create(
                email="admin@brightloop.test",
                defaults={
                    "first_name": "Admin",
                    "last_name": "Brightloop",
                    "role": User.Role.ADMIN,
                    "is_staff": True,
                    "is_superuser": True,
                },
            )
            admin_user.set_password(demo_password)
            admin_user.save()
            self.stdout.write(f"Created Admin: {admin_user.email}")

            # 2. Staff user
            staff_user, _ = User.objects.get_or_create(
                email="staff@brightloop.test",
                defaults={
                    "first_name": "Staff",
                    "last_name": "Support",
                    "role": User.Role.STAFF,
                    "is_staff": True,
                    "is_superuser": False,
                },
            )
            staff_user.set_password(demo_password)
            staff_user.save()
            self.stdout.write(f"Created Staff: {staff_user.email}")

            # 3. Primary Demo Customer and 20 additional customers
            primary_cust, _ = User.objects.get_or_create(
                email="customer@brightloop.test",
                defaults={
                    "first_name": "Jane",
                    "last_name": "Customer",
                    "role": User.Role.CUSTOMER,
                    "phone": "+91 98765 00000",
                },
            )
            primary_cust.set_password(demo_password)
            primary_cust.save()

            customers = [primary_cust]
            for i in range(1, 21):
                email = f"customer{i}@brightloop.test"
                cust, _ = User.objects.get_or_create(
                    email=email,
                    defaults={
                        "first_name": f"Customer{i}",
                        "last_name": f"AcmeCorp{i}",
                        "role": User.Role.CUSTOMER,
                        "phone": f"+91 98765 {i:05d}",
                    },
                )
                cust.set_password(demo_password)
                cust.save()
                customers.append(cust)
            self.stdout.write(f"Created/Verified {len(customers)} Customer accounts.")

            # 4. 3 Taxes (0, 5, 18 percent)
            tax_0, _ = Tax.objects.get_or_create(
                name="Zero Tax (0%)",
                defaults={"rate_percent": Decimal("0.00"), "is_active": True},
            )
            tax_5, _ = Tax.objects.get_or_create(
                name="GST Reduced (5%)",
                defaults={"rate_percent": Decimal("5.00"), "is_active": True},
            )
            tax_18, _ = Tax.objects.get_or_create(
                name="GST Standard (18%)",
                defaults={"rate_percent": Decimal("18.00"), "is_active": True},
            )
            self.stdout.write("Created/Verified 3 Taxes (0%, 5%, 18%).")

            # 5. 8 Products with variants
            # Product 1: ERP Suite (with Brand = Odoo extra 560)
            p1, _ = Product.objects.get_or_create(
                name="Enterprise ERP Suite",
                defaults={
                    "product_type": Product.ProductType.SOFTWARE,
                    "sales_price": Decimal("1200.00"),
                    "cost_price": Decimal("350.00"),
                    "notes": "Full-featured ERP solution for enterprise resource management.",
                    "tax": tax_18,
                    "is_active": True,
                },
            )
            odoo_variant, _ = ProductVariant.objects.get_or_create(
                product=p1,
                attribute="Brand",
                value="Odoo",
                defaults={"extra_price": Decimal("560.00")},
            )
            ProductVariant.objects.get_or_create(
                product=p1,
                attribute="Brand",
                value="SAP",
                defaults={"extra_price": Decimal("1200.00")},
            )

            # Product 2: CRM Cloud Pro
            p2, _ = Product.objects.get_or_create(
                name="CRM Cloud Pro",
                defaults={
                    "product_type": Product.ProductType.SOFTWARE,
                    "sales_price": Decimal("299.00"),
                    "cost_price": Decimal("60.00"),
                    "notes": "Sales pipeline and customer interaction suite.",
                    "tax": tax_18,
                    "is_active": True,
                },
            )
            ProductVariant.objects.get_or_create(
                product=p2,
                attribute="Edition",
                value="Professional",
                defaults={"extra_price": Decimal("0.00")},
            )
            ProductVariant.objects.get_or_create(
                product=p2,
                attribute="Edition",
                value="Enterprise",
                defaults={"extra_price": Decimal("150.00")},
            )

            # Product 3: Helpdesk Support Portal
            p3, _ = Product.objects.get_or_create(
                name="Helpdesk Support Portal",
                defaults={
                    "product_type": Product.ProductType.SOFTWARE,
                    "sales_price": Decimal("199.00"),
                    "cost_price": Decimal("40.00"),
                    "notes": "Omnichannel ticketing and automated customer service queue.",
                    "tax": tax_18,
                    "is_active": True,
                },
            )

            # Product 4: Implementation Service
            p4, _ = Product.objects.get_or_create(
                name="Dedicated Implementation Service",
                defaults={
                    "product_type": Product.ProductType.SERVICE,
                    "sales_price": Decimal("800.00"),
                    "cost_price": Decimal("300.00"),
                    "notes": "Expert onboarding and data migration consulting.",
                    "tax": tax_18,
                    "is_active": True,
                },
            )

            # Product 5: Technical Account Manager
            p5, _ = Product.objects.get_or_create(
                name="Senior Technical Account Manager",
                defaults={
                    "product_type": Product.ProductType.SERVICE,
                    "sales_price": Decimal("450.00"),
                    "cost_price": Decimal("200.00"),
                    "notes": "Dedicated technical advisor with 1-hour SLA.",
                    "tax": tax_5,
                    "is_active": True,
                },
            )

            # Product 6: API Connector
            p6, _ = Product.objects.get_or_create(
                name="API High-Volume Connector",
                defaults={
                    "product_type": Product.ProductType.ADDON,
                    "sales_price": Decimal("99.00"),
                    "cost_price": Decimal("15.00"),
                    "notes": "High throughput REST and webhook integration endpoints.",
                    "tax": tax_18,
                    "is_active": True,
                },
            )

            # Product 7: Cloud Storage
            p7, _ = Product.objects.get_or_create(
                name="Encrypted Cloud Storage 1TB",
                defaults={
                    "product_type": Product.ProductType.ADDON,
                    "sales_price": Decimal("49.00"),
                    "cost_price": Decimal("10.00"),
                    "notes": "Encrypted automated daily backup storage.",
                    "tax": tax_18,
                    "is_active": True,
                },
            )

            # Product 8: Security Audit
            p8, _ = Product.objects.get_or_create(
                name="Annual Security Audit & Compliance",
                defaults={
                    "product_type": Product.ProductType.SERVICE,
                    "sales_price": Decimal("950.00"),
                    "cost_price": Decimal("400.00"),
                    "notes": "SOC2 and ISO 27001 vulnerability inspection and reporting.",
                    "tax": tax_0,
                    "is_active": True,
                },
            )
            all_products = [p1, p2, p3, p4, p5, p6, p7, p8]
            self.stdout.write("Created/Verified 8 Products with variants (including Brand = Odoo with +560 extra price).")

            # 6. 4 Plans (daily, weekly, monthly, yearly)
            today = timezone.localdate()
            p_from = today - datetime.timedelta(days=365)
            p_to = today + datetime.timedelta(days=365)

            plan_daily, _ = Plan.objects.get_or_create(
                name="Daily Pass",
                defaults={
                    "billing_period": Plan.BillingPeriod.DAILY,
                    "price": Decimal("19.00"),
                    "min_quantity": 1,
                    "available_from": p_from,
                    "available_to": p_to,
                    "auto_close": False,
                    "closable": True,
                    "pausable": True,
                    "renewable": True,
                    "is_active": True,
                },
            )
            plan_weekly, _ = Plan.objects.get_or_create(
                name="Weekly Sprint",
                defaults={
                    "billing_period": Plan.BillingPeriod.WEEKLY,
                    "price": Decimal("99.00"),
                    "min_quantity": 1,
                    "available_from": p_from,
                    "available_to": p_to,
                    "auto_close": False,
                    "closable": True,
                    "pausable": True,
                    "renewable": True,
                    "is_active": True,
                },
            )
            plan_monthly, _ = Plan.objects.get_or_create(
                name="Monthly Growth",
                defaults={
                    "billing_period": Plan.BillingPeriod.MONTHLY,
                    "price": Decimal("349.00"),
                    "min_quantity": 1,
                    "available_from": p_from,
                    "available_to": p_to,
                    "auto_close": False,
                    "closable": True,
                    "pausable": True,
                    "renewable": True,
                    "is_active": True,
                },
            )
            plan_yearly, _ = Plan.objects.get_or_create(
                name="Annual Enterprise",
                defaults={
                    "billing_period": Plan.BillingPeriod.YEARLY,
                    "price": Decimal("3499.00"),
                    "min_quantity": 1,
                    "available_from": p_from,
                    "available_to": p_to,
                    "auto_close": False,
                    "closable": True,
                    "pausable": False,
                    "renewable": True,
                    "is_active": True,
                },
            )
            all_plans = [plan_daily, plan_weekly, plan_monthly, plan_yearly]
            self.stdout.write("Created/Verified 4 Plans (daily, weekly, monthly, yearly).")

            # 7. 2 Quotation Templates
            qt1, _ = QuotationTemplate.objects.get_or_create(
                name="Standard Business Suite",
                defaults={
                    "validity_days": 30,
                    "plan": plan_monthly,
                    "is_active": True,
                },
            )
            if not qt1.lines.exists():
                QuotationTemplateLine.objects.create(template=qt1, product=p2, quantity=1)
                QuotationTemplateLine.objects.create(template=qt1, product=p6, quantity=1)

            qt2, _ = QuotationTemplate.objects.get_or_create(
                name="Enterprise Full Deployment",
                defaults={
                    "validity_days": 45,
                    "plan": plan_yearly,
                    "is_active": True,
                },
            )
            if not qt2.lines.exists():
                QuotationTemplateLine.objects.create(
                    template=qt2,
                    product=p1,
                    variant=odoo_variant,
                    quantity=1,
                )
                QuotationTemplateLine.objects.create(template=qt2, product=p4, quantity=1)
                QuotationTemplateLine.objects.create(template=qt2, product=p7, quantity=2)
            self.stdout.write("Created/Verified 2 Quotation Templates with lines.")

            # 8. 2 Discounts
            Discount.objects.get_or_create(
                name="WELCOME10",
                defaults={
                    "discount_type": Discount.DiscountType.PERCENT,
                    "value": Decimal("10.00"),
                    "start_date": today - datetime.timedelta(days=60),
                    "end_date": today + datetime.timedelta(days=180),
                    "usage_limit": 500,
                    "times_used": 0,
                    "applies_to": Discount.AppliesTo.SUBSCRIPTION,
                    "is_active": True,
                },
            )
            Discount.objects.get_or_create(
                name="LAUNCH50",
                defaults={
                    "discount_type": Discount.DiscountType.FIXED,
                    "value": Decimal("50.00"),
                    "min_purchase": Decimal("200.00"),
                    "start_date": today - datetime.timedelta(days=60),
                    "end_date": today + datetime.timedelta(days=180),
                    "usage_limit": 200,
                    "times_used": 0,
                    "applies_to": Discount.AppliesTo.SUBSCRIPTION,
                    "is_active": True,
                },
            )
            self.stdout.write("Created/Verified 2 Discounts (WELCOME10, LAUNCH50).")

            # 9. Handle --bulk N
            if bulk_count > 0:
                self.stdout.write(f"Generating {bulk_count} bulk subscriptions with lines and invoices...")
                self._generate_bulk_data(bulk_count, customers, all_plans, all_products, admin_user)

        self.stdout.write(self.style.SUCCESS("=== Brightloop Database Seeding Completed Successfully ==="))

    def _generate_bulk_data(self, count, customers, plans, products, admin_user):
        """Generates N realistic subscriptions with lines, and invoices/payments for active ones."""
        today = timezone.localdate()
        statuses = [
            Subscription.Status.ACTIVE,
            Subscription.Status.ACTIVE,
            Subscription.Status.CONFIRMED,
            Subscription.Status.QUOTATION,
            Subscription.Status.DRAFT,
            Subscription.Status.CLOSED,
        ]

        # Determine existing max subscription and invoice IDs
        existing_sub_count = Subscription.objects.count()
        existing_inv_count = Invoice.objects.count()

        sub_objs = []
        sub_lines = []
        inv_objs = []
        inv_lines = []
        payment_objs = []
        history_objs = []

        sub_seq = existing_sub_count + 1
        inv_seq = existing_inv_count + 1

        for i in range(count):
            customer = random.choice(customers)
            plan = random.choice(plans)
            status = random.choice(statuses)

            # Spread start dates across past 365 days
            days_ago = random.randint(10, 360)
            start_date = today - datetime.timedelta(days=days_ago)
            expiry_date = start_date + datetime.timedelta(days=365)
            next_billing_date = add_period(start_date, plan.billing_period) if status == Subscription.Status.ACTIVE else None

            sub_num = f"SUB-{sub_seq:05d}"
            sub_seq += 1

            sub = Subscription(
                number=sub_num,
                customer=customer,
                plan=plan,
                status=status,
                start_date=start_date,
                expiry_date=expiry_date,
                payment_terms=Subscription.PaymentTerms.IMMEDIATE,
                next_billing_date=next_billing_date,
                created_by=admin_user,
            )
            sub_objs.append(sub)

        # Bulk create subscriptions to assign PKs
        Subscription.objects.bulk_create(sub_objs, batch_size=1000)
        # Refetch created subscriptions
        created_subs = list(Subscription.objects.filter(number__gte=f"SUB-{existing_sub_count + 1:05d}"))

        for sub in created_subs:
            history_objs.append(
                StatusHistory(
                    subscription=sub,
                    from_status=Subscription.Status.DRAFT,
                    to_status=sub.status,
                    changed_by=admin_user,
                )
            )

            # Choose 1 to 3 products
            chosen_products = random.sample(products, k=random.randint(1, 3))
            sub_total = Decimal("0.00")
            line_details = []

            for prod in chosen_products:
                qty = random.randint(1, 4)
                unit_price = prod.sales_price
                tax_rate = prod.tax.rate_percent if prod.tax else Decimal("0.00")
                line_amt = Decimal(qty) * unit_price
                sub_total += line_amt

                sub_lines.append(
                    SubscriptionLine(
                        subscription=sub,
                        product=prod,
                        quantity=qty,
                        unit_price=unit_price,
                        tax_percent=tax_rate,
                        amount=line_amt,
                    )
                )
                line_details.append((prod.name, qty, unit_price, tax_rate, line_amt))

            # If active, generate an invoice and payment
            if sub.status == Subscription.Status.ACTIVE:
                inv_num = f"INV-{inv_seq:05d}"
                inv_seq += 1
                period_start = sub.start_date
                period_end = add_period(period_start, sub.plan.billing_period) - datetime.timedelta(days=1)
                inv_date = period_start
                due_date = inv_date + datetime.timedelta(days=15)

                tax_sum = Decimal("0.00")
                for _, qty, u_price, t_rate, l_amt in line_details:
                    tax_sum += (l_amt * t_rate / Decimal("100.00")).quantize(Decimal("0.01"))

                inv_total = sub_total + tax_sum
                is_paid = random.random() < 0.85
                paid_amt = inv_total if is_paid else Decimal("0.00")
                inv_status = Invoice.Status.PAID if is_paid else Invoice.Status.CONFIRMED

                inv = Invoice(
                    number=inv_num,
                    subscription=sub,
                    customer=sub.customer,
                    status=inv_status,
                    invoice_date=inv_date,
                    due_date=due_date,
                    period_start=period_start,
                    period_end=period_end,
                    subtotal=sub_total,
                    tax_total=tax_sum,
                    discount_total=Decimal("0.00"),
                    total=inv_total,
                    amount_paid=paid_amt,
                )
                inv_objs.append((inv, line_details, is_paid))

        # Bulk create subscription lines and status histories
        SubscriptionLine.objects.bulk_create(sub_lines, batch_size=2000)
        StatusHistory.objects.bulk_create(history_objs, batch_size=2000)

        # Bulk create invoices
        invoice_instances = [item[0] for item in inv_objs]
        Invoice.objects.bulk_create(invoice_instances, batch_size=1000)

        # Refetch created invoices
        refetched_invoices = list(Invoice.objects.filter(number__gte=f"INV-{existing_inv_count + 1:05d}"))
        inv_map = {inv.number: inv for inv in refetched_invoices}

        for inv_tuple in inv_objs:
            base_inv = inv_tuple[0]
            line_details = inv_tuple[1]
            is_paid = inv_tuple[2]
            created_inv = inv_map.get(base_inv.number)
            if not created_inv:
                continue

            for p_name, qty, u_price, t_rate, l_amt in line_details:
                inv_lines.append(
                    InvoiceLine(
                        invoice=created_inv,
                        description=f"{p_name} ({created_inv.subscription.plan.name})",
                        quantity=qty,
                        unit_price=u_price,
                        tax_percent=t_rate,
                        amount=l_amt,
                    )
                )

            if is_paid:
                method = random.choice([Payment.Method.CARD, Payment.Method.UPI, Payment.Method.BANK_TRANSFER])
                payment_objs.append(
                    Payment(
                        invoice=created_inv,
                        method=method,
                        amount=created_inv.total,
                        payment_date=created_inv.invoice_date,
                        note="Automatic recurring payment",
                        recorded_by=admin_user,
                    )
                )

        InvoiceLine.objects.bulk_create(inv_lines, batch_size=2000)
        Payment.objects.bulk_create(payment_objs, batch_size=2000)
        self.stdout.write(
            f"Successfully bulk-created {len(created_subs)} subscriptions, "
            f"{len(inv_objs)} invoices, and {len(payment_objs)} payments."
        )
