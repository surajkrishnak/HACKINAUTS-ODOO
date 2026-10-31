"""
Business logic services for Catalog domain:
- Plan lifecycle management (close early, pause, resume, renew)
- Applicable discount evaluation engine for subscriptions
"""
import datetime
from decimal import Decimal
from typing import List, Optional
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from catalog.models import Discount, Plan


def close_plan_early(plan: Plan) -> Plan:
    """
    Closes an active plan early.
    Only permitted if plan.closable is True.

    Raises:
        ValidationError: If plan is not closable.
    """
    if not plan.closable:
        raise ValidationError(f"Plan '{plan.name}' does not allow early closure (closable flag is False).")

    with transaction.atomic():
        plan.is_active = False
        today = timezone.localdate()
        if plan.available_to > today:
            plan.available_to = today
        plan.save(update_fields=["is_active", "available_to"])
    return plan


def pause_plan(plan: Plan) -> Plan:
    """
    Temporarily pauses a plan.
    Only permitted if plan.pausable is True.

    Raises:
        ValidationError: If plan is not pausable.
    """
    if not plan.pausable:
        raise ValidationError(f"Plan '{plan.name}' is not pausable.")

    with transaction.atomic():
        plan.is_paused = True
        plan.save(update_fields=["is_paused"])
    return plan


def resume_plan(plan: Plan) -> Plan:
    """
    Resumes a previously paused plan.
    Only permitted if plan.pausable is True.

    Raises:
        ValidationError: If plan is not pausable.
    """
    if not plan.pausable:
        raise ValidationError(f"Plan '{plan.name}' is not pausable.")

    with transaction.atomic():
        plan.is_paused = False
        plan.save(update_fields=["is_paused"])
    return plan


def renew_plan(plan: Plan, new_available_to: datetime.date) -> Plan:
    """
    Renews an expired or closing plan by extending its available_to date.
    Only permitted if plan.renewable is True and new_available_to is in the future.

    Raises:
        ValidationError: If plan is not renewable or new date is not after current available_to.
    """
    if not plan.renewable:
        raise ValidationError(f"Plan '{plan.name}' is not marked as renewable.")

    if new_available_to <= plan.available_to:
        raise ValidationError(
            f"New expiration date ({new_available_to}) must be after the current available_to date ({plan.available_to})."
        )

    with transaction.atomic():
        plan.available_to = new_available_to
        plan.is_active = True
        plan.is_paused = False
        plan.save(update_fields=["available_to", "is_active", "is_paused"])
    return plan


def get_applicable_discounts(subscription, as_of_date: Optional[datetime.date] = None) -> List[Discount]:
    """
    Determines all valid, currently active discounts applicable to a given subscription
    as of a specific date.

    A discount is applicable if and only if ALL of the following criteria are met:
    1. is_active is True.
    2. as_of_date falls strictly within [start_date, end_date].
    3. times_used is strictly less than usage_limit.
    4. Minimum purchase requirement (min_purchase) is satisfied:
       - For 'subscription' discounts: total subscription amount >= min_purchase.
       - For 'product' discounts: total line amount for that product >= min_purchase.
    5. Minimum quantity requirement (min_quantity) is satisfied:
       - For 'subscription' discounts: total quantity across all lines >= min_quantity.
       - For 'product' discounts: total quantity for that product >= min_quantity.
    6. Product-level restriction (applies_to == 'product'):
       - The subscription must include at least one line item with discount.product.

    Args:
        subscription: subscriptions.models.Subscription instance
        as_of_date: Optional datetime.date to evaluate against (defaults to today)

    Returns:
        List[Discount]: List of eligible Discount model instances.
    """
    if as_of_date is None:
        as_of_date = timezone.localdate()

    # Prefetch subscription lines and products for performance
    lines = list(subscription.lines.select_related("product"))
    total_sub_amount = sum(line.amount for line in lines)
    total_sub_qty = sum(line.quantity for line in lines)

    # Product-level aggregations
    product_totals = {}
    product_qtys = {}
    for line in lines:
        p_id = line.product_id
        product_totals[p_id] = product_totals.get(p_id, Decimal("0.00")) + line.amount
        product_qtys[p_id] = product_qtys.get(p_id, 0) + line.quantity

    # Query active discounts within the date window and under usage limit
    discounts = Discount.objects.filter(
        is_active=True,
        start_date__lte=as_of_date,
        end_date__gte=as_of_date,
        times_used__lt=models_f_usage_limit() if False else 99999999,  # Filtered below in loop
    )

    applicable = []
    for discount in discounts:
        # Check usage limit
        if discount.times_used >= discount.usage_limit:
            continue

        if discount.applies_to == Discount.AppliesTo.SUBSCRIPTION:
            # Check min_purchase
            if discount.min_purchase is not None and total_sub_amount < discount.min_purchase:
                continue
            # Check min_quantity
            if discount.min_quantity is not None and total_sub_qty < discount.min_quantity:
                continue
            applicable.append(discount)

        elif discount.applies_to == Discount.AppliesTo.PRODUCT:
            if not discount.product_id or discount.product_id not in product_totals:
                # Subscription does not contain the required product
                continue
            prod_amount = product_totals[discount.product_id]
            prod_qty = product_qtys[discount.product_id]

            if discount.min_purchase is not None and prod_amount < discount.min_purchase:
                continue
            if discount.min_quantity is not None and prod_qty < discount.min_quantity:
                continue
            applicable.append(discount)

    return applicable


def models_f_usage_limit():
    return 0
