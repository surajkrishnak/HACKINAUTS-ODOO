"""
Business logic services for Billing and Invoicing:
- generate_invoice: atomic period generation, line copying, tax rounding (ROUND_HALF_UP),
  discount row locking, and no-double-billing constraint safety.
- confirm_invoice, cancel_invoice (reversing discount usage), and record_payment.
- send_invoice_email with console backend logging.
"""
import datetime
import decimal
from decimal import Decimal
from typing import Optional
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.db import IntegrityError, transaction
from django.utils import timezone

from billing.models import DiscountApplication, Invoice, InvoiceLine, Payment
from catalog.models import Discount
from catalog.services import get_applicable_discounts
from core.utils import add_period, next_number
from subscriptions.models import Subscription


def generate_invoice(
    subscription: Subscription,
    period_start: Optional[datetime.date] = None,
) -> Optional[Invoice]:
    """
    Generates an invoice for a subscription for the designated billing period.
    Wrapped in transaction.atomic().

    Rules:
    - period_end = add_period(period_start, plan.billing_period) - 1 day
    - Skips and returns None if an active (non-cancelled) invoice already exists
      for (subscription, period_start, period_end)
    - Snapshots lines, computes subtotal and taxes with decimal.ROUND_HALF_UP rounding
    - Locks discounts with select_for_update(), re-verifies usage limits, increments times_used,
      creates DiscountApplication, and adds negative InvoiceLine
    - Total = subtotal + tax_total - discount_total (floored at 0.00)
    - Advances subscription.next_billing_date to period_end + 1 day
    """
    plan = subscription.plan

    if period_start is None:
        period_start = subscription.next_billing_date or subscription.start_date

    period_end = add_period(period_start, plan.billing_period) - datetime.timedelta(days=1)

    # Pre-check for duplicate active invoice
    existing = Invoice.objects.filter(
        subscription=subscription,
        period_start=period_start,
        period_end=period_end,
    ).exclude(status=Invoice.Status.CANCELLED).first()

    if existing:
        return None

    today = timezone.localdate()

    # Calculate due date based on payment terms
    terms = subscription.payment_terms
    if terms == Subscription.PaymentTerms.DAYS_15:
        due_date = today + datetime.timedelta(days=15)
    elif terms == Subscription.PaymentTerms.DAYS_30:
        due_date = today + datetime.timedelta(days=30)
    elif terms == Subscription.PaymentTerms.DAYS_45:
        due_date = today + datetime.timedelta(days=45)
    else:  # immediate
        due_date = today

    try:
        with transaction.atomic():
            inv_number = next_number("INV", Invoice)

            invoice = Invoice.objects.create(
                number=inv_number,
                subscription=subscription,
                customer=subscription.customer,
                status=Invoice.Status.DRAFT,
                invoice_date=today,
                due_date=due_date,
                period_start=period_start,
                period_end=period_end,
                subtotal=Decimal("0.00"),
                tax_total=Decimal("0.00"),
                discount_total=Decimal("0.00"),
                total=Decimal("0.00"),
                amount_paid=Decimal("0.00"),
            )

            # Copy lines from subscription
            sub_lines = list(subscription.lines.select_related("product", "variant"))
            invoice_lines_to_create = []
            subtotal = Decimal("0.00")
            tax_total = Decimal("0.00")

            product_subtotals = {}

            for s_line in sub_lines:
                desc = s_line.product.name
                if s_line.variant:
                    desc = f"{s_line.product.name} [{s_line.variant.attribute}: {s_line.variant.value}]"
                desc = f"{desc} ({subscription.plan.name})"

                qty = s_line.quantity
                unit_price = s_line.unit_price
                tax_rate = s_line.tax_percent
                line_amt = Decimal(qty) * unit_price

                # Tax calculation with decimal.ROUND_HALF_UP rounding
                line_tax = (line_amt * tax_rate / Decimal("100.00")).quantize(
                    Decimal("0.01"),
                    rounding=decimal.ROUND_HALF_UP,
                )

                subtotal += line_amt
                tax_total += line_tax
                p_id = s_line.product_id
                product_subtotals[p_id] = product_subtotals.get(p_id, Decimal("0.00")) + line_amt

                invoice_lines_to_create.append(
                    InvoiceLine(
                        invoice=invoice,
                        description=desc,
                        quantity=qty,
                        unit_price=unit_price,
                        tax_percent=tax_rate,
                        amount=line_amt,
                    )
                )

            # Bulk create product invoice lines
            InvoiceLine.objects.bulk_create(invoice_lines_to_create)

            # Evaluate and apply applicable discounts
            applicable_discounts = get_applicable_discounts(subscription, invoice.invoice_date)
            discount_total = Decimal("0.00")

            for disc in applicable_discounts:
                # Lock discount row with select_for_update()
                locked_disc = Discount.objects.select_for_update().get(pk=disc.pk)

                # Re-verify usage limits and active status
                if not locked_disc.is_active or locked_disc.times_used >= locked_disc.usage_limit:
                    continue

                if locked_disc.applies_to == Discount.AppliesTo.SUBSCRIPTION:
                    if locked_disc.discount_type == Discount.DiscountType.PERCENT:
                        disc_amount = (subtotal * locked_disc.value / Decimal("100.00")).quantize(
                            Decimal("0.01"),
                            rounding=decimal.ROUND_HALF_UP,
                        )
                    else:  # FIXED
                        disc_amount = min(locked_disc.value, subtotal)

                elif locked_disc.applies_to == Discount.AppliesTo.PRODUCT:
                    prod_sub = product_subtotals.get(locked_disc.product_id, Decimal("0.00"))
                    if prod_sub <= Decimal("0.00"):
                        continue
                    if locked_disc.discount_type == Discount.DiscountType.PERCENT:
                        disc_amount = (prod_sub * locked_disc.value / Decimal("100.00")).quantize(
                            Decimal("0.01"),
                            rounding=decimal.ROUND_HALF_UP,
                        )
                    else:  # FIXED
                        disc_amount = min(locked_disc.value, prod_sub)
                else:
                    disc_amount = Decimal("0.00")

                # Never let total go below 0.00
                potential_total = subtotal + tax_total - discount_total
                disc_amount = min(disc_amount, potential_total)

                if disc_amount > Decimal("0.00"):
                    discount_total += disc_amount

                    # Record DiscountApplication audit log
                    DiscountApplication.objects.create(
                        discount=locked_disc,
                        invoice=invoice,
                        amount=disc_amount,
                    )

                    # Increment times_used
                    locked_disc.times_used += 1
                    locked_disc.save(update_fields=["times_used"])

                    # Add negative invoice line
                    InvoiceLine.objects.create(
                        invoice=invoice,
                        description=f"Discount: {locked_disc.name}",
                        quantity=1,
                        unit_price=-disc_amount,
                        tax_percent=Decimal("0.00"),
                        amount=-disc_amount,
                    )

            # Finalize totals
            total = max(Decimal("0.00"), subtotal + tax_total - discount_total)
            invoice.subtotal = subtotal
            invoice.tax_total = tax_total
            invoice.discount_total = discount_total
            invoice.total = total
            invoice.save()

            # Advance subscription next_billing_date to period_end + 1 day
            subscription.next_billing_date = period_end + datetime.timedelta(days=1)
            subscription.save(update_fields=["next_billing_date"])

        return invoice

    except IntegrityError:
        # Caught by UniqueConstraint (subscription, period_start, period_end)
        return None


def confirm_invoice(invoice: Invoice, user=None) -> Invoice:
    """Transitions invoice from DRAFT to CONFIRMED."""
    if invoice.status != Invoice.Status.DRAFT:
        raise ValidationError(f"Cannot confirm invoice {invoice.number} with status '{invoice.get_status_display()}'.")

    invoice.status = Invoice.Status.CONFIRMED
    invoice.save(update_fields=["status"])
    return invoice


def cancel_invoice(invoice: Invoice, user=None) -> Invoice:
    """
    Cancels a DRAFT or CONFIRMED invoice.
    Restores discount usage (decrements times_used and removes DiscountApplication).
    Fails if any payment has been recorded.
    """
    if invoice.payments.exists():
        raise ValidationError(f"Cannot cancel invoice {invoice.number}: payments have already been recorded.")

    if invoice.status in (Invoice.Status.PAID, Invoice.Status.CANCELLED):
        raise ValidationError(f"Cannot cancel an invoice with status '{invoice.get_status_display()}'.")

    with transaction.atomic():
        # Restore discount usage
        for app in invoice.discount_applications.select_related("discount").all():
            disc = Discount.objects.select_for_update().get(pk=app.discount_id)
            if disc.times_used > 0:
                disc.times_used -= 1
                disc.save(update_fields=["times_used"])
            app.delete()

        invoice.status = Invoice.Status.CANCELLED
        invoice.save(update_fields=["status"])

    return invoice


def record_payment(
    invoice: Invoice,
    method: str,
    amount: Decimal,
    payment_date: Optional[datetime.date] = None,
    note: str = "",
    user=None,
) -> Payment:
    """
    Records a payment against a confirmed invoice inside transaction.atomic().
    Updates invoice.amount_paid and automatically marks invoice as PAID when paid in full.
    """
    if invoice.status != Invoice.Status.CONFIRMED:
        raise ValidationError(f"Only confirmed invoices can receive payments (current: '{invoice.get_status_display()}').")

    if amount <= Decimal("0.00"):
        raise ValidationError("Payment amount must be greater than ₹0.00.")

    if amount > invoice.balance:
        raise ValidationError(
            f"Payment amount (₹{amount}) exceeds outstanding balance (₹{invoice.balance})."
        )

    if payment_date is None:
        payment_date = timezone.localdate()

    with transaction.atomic():
        payment = Payment.objects.create(
            invoice=invoice,
            method=method,
            amount=amount,
            payment_date=payment_date,
            note=note,
            recorded_by=user,
        )

        invoice.amount_paid += amount
        if invoice.amount_paid >= invoice.total:
            invoice.status = Invoice.Status.PAID

        invoice.save(update_fields=["amount_paid", "status"])

    return payment


def send_invoice_email(invoice: Invoice, request=None) -> bool:
    """Sends a summary email of the invoice to the customer via the configured email backend."""
    customer = invoice.customer
    subject = f"Brightloop Software - Invoice {invoice.number}"

    lines_summary = "\n".join([f"  - {line.description}: ₹{line.amount}" for line in invoice.lines.all()])

    body = f"""Hello {customer.get_full_name() or customer.email},

Please find your subscription invoice summary below:

Invoice Number: {invoice.number}
Billing Period: {invoice.period_start} to {invoice.period_end}
Invoice Date: {invoice.invoice_date}
Due Date: {invoice.due_date}

Line Items:
{lines_summary}

Subtotal: ₹{invoice.subtotal}
Tax: ₹{invoice.tax_total}
Discounts: -₹{invoice.discount_total}
Total Amount: ₹{invoice.total}
Amount Paid: ₹{invoice.amount_paid}
Balance Due: ₹{invoice.balance}

Thank you for choosing Brightloop Software!

Best regards,
Brightloop Billing Team
billing@brightloop.test
"""
    send_mail(
        subject=subject,
        message=body,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[customer.email],
        fail_silently=False,
    )

    invoice.sent_at = timezone.now()
    invoice.save(update_fields=["sent_at"])
    return True
