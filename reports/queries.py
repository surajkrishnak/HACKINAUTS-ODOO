import datetime
from decimal import Decimal
from typing import Any, Dict, List, Optional
from django.core.cache import cache
from django.db.models import Case, Count, DecimalField, F, Q, Sum, Value, When
from django.utils import timezone

from billing.models import Invoice, Payment
from catalog.models import Plan
from subscriptions.models import Subscription, SubscriptionLine


def get_dashboard_metrics(
    date_from: Optional[datetime.date] = None,
    date_to: Optional[datetime.date] = None,
    plan_id: Optional[int] = None,
    customer_q: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Computes high-performance aggregate metrics for the Finance Dashboard
    using Django ORM aggregations (Count, Sum, Case/When).
    """
    today = timezone.localdate()

    # --- 1. Active Subscriptions & MRR ---
    active_subs_qs = Subscription.objects.filter(status=Subscription.Status.ACTIVE)
    if plan_id:
        active_subs_qs = active_subs_qs.filter(plan_id=plan_id)
    if customer_q:
        active_subs_qs = active_subs_qs.filter(
            Q(customer__email__icontains=customer_q)
            | Q(customer__first_name__icontains=customer_q)
            | Q(customer__last_name__icontains=customer_q)
            | Q(number__icontains=customer_q)
        )

    active_subscriptions_count = active_subs_qs.count()

    # MRR normalization:
    # Daily: total * 30
    # Weekly: total * 52 / 12 (~4.333333)
    # Monthly: total * 1
    # Yearly: total / 12
    # We aggregate directly using subscription lines to ensure precise line-level calculations
    active_lines_qs = SubscriptionLine.objects.filter(
        subscription__in=active_subs_qs
    ).select_related("subscription__plan", "product")

    mrr = Decimal("0.00")
    estimated_monthly_cost = Decimal("0.00")

    for line in active_lines_qs:
        period = line.subscription.plan.billing_period
        line_amt = line.amount
        cost_unit = line.product.cost_price or Decimal("0.00")
        line_cost = Decimal(line.quantity) * cost_unit

        if period == Plan.BillingPeriod.DAILY:
            multiplier = Decimal("30.00")
        elif period == Plan.BillingPeriod.WEEKLY:
            multiplier = Decimal("52.00") / Decimal("12.00")
        elif period == Plan.BillingPeriod.MONTHLY:
            multiplier = Decimal("1.00")
        elif period == Plan.BillingPeriod.YEARLY:
            multiplier = Decimal("1.00") / Decimal("12.00")
        else:
            multiplier = Decimal("1.00")

        mrr += (line_amt * multiplier).quantize(Decimal("0.01"))
        estimated_monthly_cost += (line_cost * multiplier).quantize(Decimal("0.01"))

    estimated_profit = mrr - estimated_monthly_cost
    margin_percent = (
        (estimated_profit / mrr * Decimal("100.00")).quantize(Decimal("0.1"))
        if mrr > Decimal("0.00")
        else Decimal("0.0")
    )

    # --- 2. Revenue Collected (Payments) ---
    payments_qs = Payment.objects.all()
    if date_from:
        payments_qs = payments_qs.filter(payment_date__gte=date_from)
    if date_to:
        payments_qs = payments_qs.filter(payment_date__lte=date_to)
    if customer_q:
        payments_qs = payments_qs.filter(
            Q(invoice__customer__email__icontains=customer_q)
            | Q(invoice__number__icontains=customer_q)
        )

    revenue_collected = (
        payments_qs.aggregate(total=Sum("amount"))["total"] or Decimal("0.00")
    )

    # --- 3. Outstanding Unpaid Amount on Confirmed Invoices ---
    inv_qs = Invoice.objects.all()
    if plan_id:
        inv_qs = inv_qs.filter(subscription__plan_id=plan_id)
    if customer_q:
        inv_qs = inv_qs.filter(
            Q(customer__email__icontains=customer_q)
            | Q(number__icontains=customer_q)
        )

    confirmed_invs = inv_qs.filter(status=Invoice.Status.CONFIRMED)
    outstanding_amount = (
        confirmed_invs.aggregate(
            unpaid=Sum(F("total") - F("amount_paid"))
        )["unpaid"]
        or Decimal("0.00")
    )

    # --- 4. Overdue Invoices ---
    overdue_invs = confirmed_invs.filter(due_date__lt=today, amount_paid__lt=F("total"))
    overdue_count = overdue_invs.count()
    overdue_amount = (
        overdue_invs.aggregate(
            unpaid=Sum(F("total") - F("amount_paid"))
        )["unpaid"]
        or Decimal("0.00")
    )

    # --- 5. Invoice Counts by Status ---
    invoice_counts = {
        Invoice.Status.DRAFT: 0,
        Invoice.Status.CONFIRMED: 0,
        Invoice.Status.PAID: 0,
        Invoice.Status.CANCELLED: 0,
    }
    grouped_counts = inv_qs.values("status").annotate(count=Count("id"))
    for item in grouped_counts:
        invoice_counts[item["status"]] = item["count"]

    # --- 6. Recent Payments (latest 10) ---
    recent_payments = list(
        payments_qs.select_related("invoice", "invoice__customer", "recorded_by").order_by(
            "-payment_date", "-id"
        )[:10]
    )

    # --- 7. Top Overdue Invoices (10 rows, oldest first) ---
    top_overdue = list(
        overdue_invs.select_related("customer", "subscription", "subscription__plan").order_by(
            "due_date", "id"
        )[:10]
    )

    return {
        "active_subscriptions_count": active_subscriptions_count,
        "mrr": mrr,
        "revenue_collected": revenue_collected,
        "outstanding_amount": outstanding_amount,
        "overdue_count": overdue_count,
        "overdue_amount": overdue_amount,
        "invoice_counts": invoice_counts,
        "recent_payments": recent_payments,
        "top_overdue": top_overdue,
        # Admin-only fields:
        "estimated_monthly_cost": estimated_monthly_cost,
        "estimated_profit": estimated_profit,
        "margin_percent": margin_percent,
    }


def get_monthly_revenue_last_12_months() -> Dict[str, Any]:
    """
    Returns monthly revenue collections for the past 12 calendar months for Chart.js.
    Cached for 5 minutes (300 seconds) in local-memory cache.
    """
    cache_key = "brightloop_monthly_revenue_last_12_months"
    cached_data = cache.get(cache_key)
    if cached_data is not None:
        return cached_data

    today = timezone.localdate()
    months_labels = []
    revenue_series = []

    # Calculate month bounds for the past 12 months (oldest to newest)
    for i in range(11, -1, -1):
        # Calculate year and month
        y = today.year
        m = today.month - i
        while m <= 0:
            m += 12
            y -= 1

        month_label = datetime.date(y, m, 1).strftime("%b %Y")
        months_labels.append(month_label)

        # Start of month to end of month
        start_date = datetime.date(y, m, 1)
        if m == 12:
            end_date = datetime.date(y + 1, 1, 1) - datetime.timedelta(days=1)
        else:
            end_date = datetime.date(y, m + 1, 1) - datetime.timedelta(days=1)

        monthly_sum = (
            Payment.objects.filter(
                payment_date__gte=start_date, payment_date__lte=end_date
            ).aggregate(total=Sum("amount"))["total"]
            or Decimal("0.00")
        )
        revenue_series.append(float(monthly_sum))

    result = {
        "labels": months_labels,
        "data": revenue_series,
    }
    # Cache for 5 minutes
    cache.set(cache_key, result, 300)
    return result
