from django.conf import settings


def currency_context(request):
    """
    Exposes CURRENCY_SYMBOL and CURRENCY_CODE globally to all Django templates.
    """
    return {
        "CURRENCY_SYMBOL": getattr(settings, "CURRENCY_SYMBOL", "₹"),
        "CURRENCY_CODE": getattr(settings, "CURRENCY_CODE", "INR"),
    }
