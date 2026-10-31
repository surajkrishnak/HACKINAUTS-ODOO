"""
Business logic services for Subscriptions domain:
- Lifecycle transitions with transition table and plan rule validation
- Quotation template conversion into subscriptions with pricing & tax snapshots
- Audit logging via StatusHistory inside atomic transactions
"""
import datetime
from decimal import Decimal
from typing import Optional
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from core.utils import next_number
from subscriptions.models import (
    QuotationTemplate,
    StatusHistory,
    Subscription,
    SubscriptionLine,
)

User = get_user_model()

# Valid subscription status transitions mapping
VALID_TRANSITIONS = {
    Subscription.Status.DRAFT: [
        Subscription.Status.QUOTATION,
        Subscription.Status.CLOSED,  # "Cancel"
    ],
    Subscription.Status.QUOTATION: [
        Subscription.Status.CONFIRMED,
        Subscription.Status.CLOSED,  # "Cancel"
    ],
    Subscription.Status.CONFIRMED: [
        Subscription.Status.ACTIVE,
    ],
    Subscription.Status.ACTIVE: [
        Subscription.Status.CLOSED,
    ],
    Subscription.Status.CLOSED: [],
}


def transition(
    subscription: Subscription,
    to_status: str,
    user: Optional[User] = None,
) -> Subscription:
    """
    Executes a valid lifecycle transition for a subscription.

    Transition Table:
    - draft -> quotation ("Send quotation")
    - quotation -> confirmed ("Confirm")
    - confirmed -> active ("Activate"; sets next_billing_date = start_date)
    - active -> closed ("Close"; requires plan.closable if closed early)
    - draft or quotation -> closed ("Cancel")

    Raises:
        ValidationError: For invalid status moves or plan rule violations.
    """
    current_status = subscription.status
    allowed_next_states = VALID_TRANSITIONS.get(current_status, [])

    if to_status not in allowed_next_states:
        raise ValidationError(
            f"Invalid transition: Cannot move subscription {subscription.number} "
            f"from '{subscription.get_status_display()}' to '{to_status}'."
        )

    today = timezone.localdate()

    # Rule: closing an active subscription early requires plan.closable
    if current_status == Subscription.Status.ACTIVE and to_status == Subscription.Status.CLOSED:
        is_early = subscription.expiry_date is None or subscription.expiry_date > today
        if is_early and not subscription.plan.closable:
            raise ValidationError(
                f"Cannot close subscription {subscription.number} early: "
                f"Plan '{subscription.plan.name}' is not marked as closable."
            )

    with transaction.atomic():
        subscription.status = to_status

        # When activating, set next_billing_date = start_date
        if to_status == Subscription.Status.ACTIVE:
            subscription.next_billing_date = subscription.start_date
            subscription.is_paused = False

        subscription.save()

        # Audit log into StatusHistory
        StatusHistory.objects.create(
            subscription=subscription,
            from_status=current_status,
            to_status=to_status,
            changed_by=user,
        )

    return subscription


def create_subscription_from_template(
    template: QuotationTemplate,
    customer: User,
    start_date: datetime.date,
    user: Optional[User] = None,
) -> Subscription:
    """
    Builds a new Subscription in DRAFT status from a QuotationTemplate.
    Copies and snapshots:
    - unit_price = product.sales_price + variant.extra_price (if any)
    - tax_percent = product.tax.rate_percent (0 if none)
    - amount = quantity * unit_price
    - expiry_date = start_date + validity_days
    """
    with transaction.atomic():
        sub_number = next_number("SUB", Subscription)
        expiry_date = start_date + datetime.timedelta(days=template.validity_days)

        subscription = Subscription.objects.create(
            number=sub_number,
            customer=customer,
            plan=template.plan,
            status=Subscription.Status.DRAFT,
            start_date=start_date,
            expiry_date=expiry_date,
            payment_terms=Subscription.PaymentTerms.IMMEDIATE,
            next_billing_date=None,
            template=template,
            created_by=user,
        )

        lines_to_create = []
        for t_line in template.lines.select_related("product", "product__tax", "variant"):
            product = t_line.product
            variant = t_line.variant

            # Compute snapshot unit price
            extra = variant.extra_price if variant else Decimal("0.00")
            unit_price = product.sales_price + extra

            # Compute snapshot tax rate
            tax_rate = product.tax.rate_percent if product.tax else Decimal("0.00")
            line_amount = Decimal(t_line.quantity) * unit_price

            lines_to_create.append(
                SubscriptionLine(
                    subscription=subscription,
                    product=product,
                    variant=variant,
                    quantity=t_line.quantity,
                    unit_price=unit_price,
                    tax_percent=tax_rate,
                    amount=line_amount,
                )
            )

        SubscriptionLine.objects.bulk_create(lines_to_create)

        # Initial StatusHistory
        StatusHistory.objects.create(
            subscription=subscription,
            from_status="none",
            to_status=Subscription.Status.DRAFT,
            changed_by=user,
        )

    return subscription


def pause_subscription(subscription: Subscription, user: Optional[User] = None) -> Subscription:
    """Pauses an active subscription if plan.pausable is True."""
    if not subscription.plan.pausable:
        raise ValidationError(f"Plan '{subscription.plan.name}' does not allow pausing.")
    if subscription.status != Subscription.Status.ACTIVE:
        raise ValidationError("Only active subscriptions can be paused.")

    with transaction.atomic():
        subscription.is_paused = True
        subscription.save(update_fields=["is_paused"])
        StatusHistory.objects.create(
            subscription=subscription,
            from_status="active",
            to_status="paused",
            changed_by=user,
        )
    return subscription


def resume_subscription(subscription: Subscription, user: Optional[User] = None) -> Subscription:
    """Resumes a paused subscription."""
    if not subscription.is_paused:
        raise ValidationError("Subscription is not paused.")

    with transaction.atomic():
        subscription.is_paused = False
        subscription.save(update_fields=["is_paused"])
        StatusHistory.objects.create(
            subscription=subscription,
            from_status="paused",
            to_status="active",
            changed_by=user,
        )
    return subscription
