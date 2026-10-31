import logging
import time

logger = logging.getLogger("brightloop.performance")


class TimingMiddleware:
    """
    Measures page generation time, logs duration,
    and attaches an 'X-Response-Time' header to the HTTP response.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start_time = time.perf_counter()
        response = self.get_response(request)
        duration_ms = (time.perf_counter() - start_time) * 1000

        # Attach header
        response["X-Response-Time"] = f"{duration_ms:.2f}ms"

        # Log performance metric
        log_msg = f"{request.method} {request.path} took {duration_ms:.2f}ms [status: {response.status_code}]"
        if duration_ms > 2000:
            logger.warning(f"SLOW PAGE: {log_msg}")
        else:
            logger.info(log_msg)

        return response
