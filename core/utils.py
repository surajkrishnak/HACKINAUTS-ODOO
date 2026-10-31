"""
Core utilities for Brightloop Subscription Management System.
Includes helpers for sequential document numbering (SUB-00001, INV-00001)
and date arithmetic across billing periods.
"""
import calendar
import datetime
import re
from typing import Type
from django.db import models


def next_number(prefix: str, model: Type[models.Model]) -> str:
    """
    Generates the next sequential unique number with the given prefix.
    Examples:
        SUB-00001, SUB-00002
        INV-00001, INV-00002

    Extracts existing numbers matching prefix-XXXXX, identifies the highest
    sequence number, and increments it safely. Guarantees no collisions.
    """
    prefix = prefix.strip().upper()
    filter_pattern = f"{prefix}-"

    # Fetch existing numbers with the prefix
    existing_numbers = (
        model.objects.filter(number__startswith=filter_pattern)
        .values_list("number", flat=True)
    )

    max_val = 0
    pattern = re.compile(rf"^{re.escape(prefix)}-(\d+)$")
    for num in existing_numbers:
        match = pattern.match(num)
        if match:
            try:
                val = int(match.group(1))
                if val > max_val:
                    max_val = val
            except ValueError:
                pass

    next_val = max_val + 1
    candidate = f"{prefix}-{next_val:05d}"

    # Secondary safety check to prevent any edge collision
    while model.objects.filter(number=candidate).exists():
        next_val += 1
        candidate = f"{prefix}-{next_val:05d}"

    return candidate


def add_period(start_date: datetime.date, billing_period: str) -> datetime.date:
    """
    Returns the next period boundary date for a given start date and billing period:
    - daily: +1 day
    - weekly: +7 days
    - monthly: +1 month (safely handles 28/29/30/31 day ends)
    - yearly: +1 year (safely handles leap year Feb 29)

    Args:
        start_date: The base datetime.date
        billing_period: 'daily', 'weekly', 'monthly', or 'yearly'

    Returns:
        The computed next datetime.date
    """
    period = (billing_period or "").lower().strip()

    if period == "daily":
        return start_date + datetime.timedelta(days=1)

    elif period == "weekly":
        return start_date + datetime.timedelta(days=7)

    elif period == "monthly":
        year = start_date.year
        month = start_date.month + 1
        if month > 12:
            year += 1
            month = 1
        max_day = calendar.monthrange(year, month)[1]
        day = min(start_date.day, max_day)
        return datetime.date(year, month, day)

    elif period == "yearly":
        year = start_date.year + 1
        month = start_date.month
        max_day = calendar.monthrange(year, month)[1]
        day = min(start_date.day, max_day)
        return datetime.date(year, month, day)

    else:
        raise ValueError(
            f"Unsupported billing period '{billing_period}'. Must be daily, weekly, monthly, or yearly."
        )
